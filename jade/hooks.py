"""Claude Code hook handlers. Enforcement and reminders only; capture happens at the adapter (ADR-04).

PreToolUse is the side-effect boundary: exit 2 blocks the tool call and the reason goes to the model.
Threat T17: the gate fails closed. When the classifier, the policy files, the ledger, git or the clock fail, a call that is high-risk (or,
when nothing can be classified, anything that is not clearly read-only) is blocked, never waved through. The whole PreToolUse handler runs
under an alarm (BUDGET_S) that is safely below the host's hook timeout, because a host that kills a hook proceeds with the tool call.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from . import paths, policy
from .util import at_least, now, ts

_STARTED: float | None = None   # time.monotonic() when this PreToolUse call began
BUDGET_S = 6.0          # whole PreToolUse handler; the dialog path (askdialog) raises its own alarm for the wait, the installed host timeout (ops.HOOKS) covers it
LEDGER_TIMEOUT_S = 1.5  # one wait for the ledger lock (open waits at most twice: busy timeout, then the schema step)
GIT_TIMEOUT_S = 2.0     # one git call made to resolve a push destination
BYPASS = re.compile(r"\bcurl\b.*openrouter\.ai/api/alpha/decisions")
REMIND_WINDOW_S = 6 * 3600
# W-59: `jade ack` is an agent's claim about what it did, so it is scoped, journaled and rate limited when no human is at the terminal (docs/05 T24).
ACK_RATE_WINDOW_S = 3600
ACK_MAX_PER_WINDOW = 10   # `jade ack` commands per session per window; a session with more non-yes answers than this needs a human, not a louder ack
ACK_ELIGIBLE_EXITS = (3, 4, 5, 6)
ACK_NOTE_MAX = 500
# Host permission modes in which Claude Code shows no prompt: JADE's gate is then the only gate (docs/05 T18). acceptEdits skips the prompt for file edits only.
NO_PROMPT_MODES = ("bypassPermissions", "auto")
NO_PROMPT_EDIT_MODES = ("acceptEdits",)


def no_host_prompt(mode: str | None, tool: str) -> bool:
    """True when the host would not have asked the human about this call, so no second gate stands behind JADE's."""
    return mode in NO_PROMPT_MODES or (mode in NO_PROMPT_EDIT_MODES and tool in policy.WRITE_TOOLS)


class HookBudgetExceeded(BaseException):  # noqa: N818 - BaseException so that no `except Exception` in the gate can swallow the alarm
    """Raised by the PreToolUse alarm; only main() catches it."""


# Cheap, policy-free fail-safe used when nothing else can be trusted (policy does not load, the handler crashed or ran out of time).
# Linear regexes only: it must itself be safe to run on the input that made the classifier hang.
_SAFE_VERB = (r"(?:ls|ll|cat|head|tail|wc|pwd|whoami|id|uname|which|type|stat|du|df|diff|cmp|basename|dirname|realpath|grep|egrep|fgrep|jq|cut|tr|echo|printf"
              r"|true|false|git (?:status|log|diff|show|rev-parse|ls-files|blame))")
_SAFE_CMD = re.compile(rf"{_SAFE_VERB}(?: [\w./:=@%+,~ -]*)?")
_EXEC_FLAG = re.compile(r"--output|--pre\b|--exec|--upload-pack|-exec|--hostname-bin|--pager")
_PROTECTED_PATH = re.compile(
    r"(?i)\.claude|\.config/jade|\.local/share/jade|\.local/bin|/etc/|/library/launch|policies/|(?:^|/)jade/|jade\.sqlite|\.ssh|\.gnupg|\.aws|\.pem\b|id_rsa|id_ed25519"
    r"|vault_key|agents\.md|\.cursor|\.zshrc|\.bashrc|\.zprofile|\.profile|sudoers|crontab|launchagents|launchdaemons")
# Read, Grep and Glob when the policy cannot be consulted: only a call that may touch the permit seal key (SI-10, W-70) is blocked.
# Not a bare `permit` (a repository has app/Models/Permit.php): the key's own name, a glob that could expand to it (`perm*`, `permit-s*`), the state directory.
_SEAL_KEY_TEXT = re.compile(r"(?i)permit[-_.]?seal|seal[-_.]?key|\.local/share/jade|jade_home|(?:^|/)perm[^/]*[*?\[]")
# Harness bookkeeping tools (M-06b in policies/risk.yaml; a test keeps the two patterns equal): their names say `update`, but they only
# change what the app shows, so the degraded failsafe lets them through like the policy does.
_HARNESS_TOOL = re.compile(r"^mcp__ccd_turn__(?:update_plan|wrap_up)$|^mcp__ccd_session__(?:mark_chapter|dismiss_task)$")
_MUTATING_TOOL = re.compile(
    r"^mcp__.+?__(?:set|write|edit|create|update|delete|remove|move|rename|exec|execute|run|shell|apply|merge|deploy|install|uninstall|kill|terminate|patch|publish"
    r"|push|commit|save|add|drop|truncate|restart|stop|start|send|post|put|accept|fork|link|discard|clean|interact|force|mcp-(?:exec|add|remove|config|create|activate)|code-mode)"
    r"|__(?:\w+_)?run_code"
    r"|kubectl_|exec_in_pod|helm_chart|^RemoteTrigger$|__(?:app_type|Type|type|Shortcut|computer_batch|app_batch|teach_batch|app_key|key)$")


_GUI_PREFIXES = ("mcp__computer-use__", "mcp__MacOS-MCP__")   # tools that can press Allow on the screen
_GUI_SHELL = re.compile(r"\b(?:osascript|cliclick|shortcuts\s+run)\b")   # a shell or terminal call that scripts the screen the same way


def _is_gui_call(tool, event: dict) -> bool:
    if not isinstance(tool, str):
        return False
    if tool.startswith(_GUI_PREFIXES):
        return True
    tin = event.get("tool_input")
    try:
        return isinstance(tin, dict) and policy.runs_shell(tool, tin) and bool(_GUI_SHELL.search(policy.tool_subject(tool, tin)))
    except Exception:  # noqa: BLE001 - not classifiable: it is not paused here, and the gate rates it as it always did
        return False


def _gui_pause(event: dict, tool: str):
    """While an ask of this session is open, the session's computer-use tools are refused: an agent that drives the mouse could press Allow itself (docs/12 risks)."""
    if paths.level() not in ("low", "medium", "high") or paths.gate_mode() != "enforce" or not event.get("session_id"):
        return None
    try:
        from . import askchat, ledger

        db = ledger.open(timeout=LEDGER_TIMEOUT_S)
        try:
            if askchat.gui_paused(db, event["session_id"]):
                return HookResult(2, f"JADE gate: {tool} is paused while the human decides a gated call of this session. Do not click or type anything on the screen. "
                                     "Continue with other work; this clears when the human answers or the ask expires.")
        finally:
            db.close()
    except Exception:  # noqa: BLE001, S110 - a ledger failure must not stop an ordinary GUI call; the gated call itself fails closed
        pass
    return None


def failsafe_reason(event: dict) -> str | None:
    """Why a gated call must be blocked when the policy cannot be consulted, or None when it is clearly harmless: a read-only command,
    a file write outside the protected locations, a tool whose name says it only reads."""
    tool = str(event.get("tool_name") or "")
    if not policy.is_gated(tool):
        return None
    tin = event.get("tool_input")
    if not isinstance(tin, dict):
        return "the tool input is malformed"
    if tool in policy.WRITE_TOOLS:
        for p in policy.write_paths(tin) or [""]:
            if _PROTECTED_PATH.search(p) or _PROTECTED_PATH.search(policy.real(os.path.expanduser(p))):
                return f"the write target {p[:120]!r} is a protected location"
        return None
    if tool in policy.READ_TOOLS:
        targets, globs = policy.read_targets(tool, tin)
        for t in (*targets, *globs):
            if _SEAL_KEY_TEXT.search(t) or _SEAL_KEY_TEXT.search(policy.real(os.path.expanduser(t))) or policy.is_seal_key_dir(policy.real(os.path.expanduser(t))):
                return f"{tool} names {t[:120]!r}, which may be the permit seal key"
        return None
    cmd = tin.get("command") if tool in policy.SHELL_TOOLS else next((tin[k] for k in ("command", "cmd", "script") if isinstance(tin.get(k), str)), None)
    if cmd is not None or tool in policy.SHELL_TOOLS:
        text = str(cmd or "").strip()
        if len(text) <= 4096 and _SAFE_CMD.fullmatch(text) and not _EXEC_FLAG.search(text):
            return None
        return "the command is not clearly read-only"
    if _HARNESS_TOOL.search(tool):
        return None
    if _MUTATING_TOOL.search(tool) or _MUTATING_TOOL.search("__".join((tool.rpartition("__")[0], policy.snake(tool.rpartition("__")[2])))):
        return f"{tool} can mutate state"
    return None


@dataclass
class HookResult:
    exit_code: int = 0
    stderr: str = ""
    stdout: str = ""


def _classify(tool: str, tool_input: dict, cwd: str = "") -> policy.Assessment:
    return policy.assess(tool, tool_input, cwd)


DEFAULT_BRANCHES = ("main", "master")
PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def parse_push(args: list[str]) -> tuple[str | None, list[str], bool]:
    """(remote, refspecs, pushes_every_branch) from the arguments after `git push`."""
    pos: list[str] = []
    repo: str | None = None
    every = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos.extend(args[i + 1:])
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name in ("--all", "--mirror", "--branches"):
                every = True
            if name in PUSH_VALUE_OPTS and not eq:
                i += 1
                val = args[i] if i < len(args) else ""
            if name == "--repo":
                repo = val
        elif a.startswith("-") and len(a) > 1:
            if a.find("o", 1) == len(a) - 1:  # `-o` last in a short cluster takes the next word as its value
                i += 1
        else:
            pos.append(a)
        i += 1
    if repo is not None:
        return repo, pos, every
    return (pos[0] if pos else None), pos[1:], every


class GitUnresolved(Exception):
    """git could not answer (it hung, is missing, refused the directory or the repository is unreadable). Not the same as "no such ref":
    the gate treats this as an unresolved push destination and fails closed at high risk, never as "not main"."""


def _git_env() -> dict[str, str]:
    """The environment for the hook's own git call: the agent's GIT_* variables (GIT_DIR, GIT_CONFIG_*, GIT_EXEC_PATH, GIT_SSH_COMMAND, ...) are
    dropped, system and global config are not read, there is no pager and no prompt."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(LC_ALL="C", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def _git(call: policy.shellnorm.GitCall, cwd: str, *args: str) -> str | None:
    """Run `git <args>` for a `git push` the agent wrote, in a directory the agent controls (W-60), and return its output.

    Returns None only when git gave a definite "nothing there": no such ref (`symbolic-ref -q` exits 1 with nothing on stderr: a detached HEAD,
    no recorded remote default) or no repository at all (a missing directory, "not a git repository": there is nothing to push from). Everything
    else raises GitUnresolved: a timeout, a missing git, a repository git refuses (safe.directory, `dubious ownership`) or cannot read (a broken
    config), a crash, empty output. The repository's own config is read by git whatever we do, so the
    options that make git run a program from it are overridden (core.fsmonitor, core.hooksPath; no pager), the system and global config are
    ignored, and the agent's GIT_* environment is dropped. safe.directory is deliberately not widened: an untrusted owner is "unresolved".
    """
    argv = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, "-c", "core.pager=cat"]
    for d in call.dirs:
        argv += ["-C", os.path.expanduser(d)]
    if call.git_dir:
        argv += ["--git-dir", os.path.expanduser(call.git_dir)]
    if call.work_tree:
        argv += ["--work-tree", os.path.expanduser(call.work_tree)]
    argv = [w.replace("\x00", "") for w in [*argv, *args]]  # a shell ignores a NUL byte; subprocess refuses one
    if cwd and not os.path.isdir(cwd):
        return None
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=GIT_TIMEOUT_S, cwd=cwd or None, env=_git_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError) as e:  # TimeoutExpired is a SubprocessError
        raise GitUnresolved(type(e).__name__) from e
    if out.returncode == 1 and not out.stdout.strip() and not out.stderr.strip():
        return None
    if out.returncode == 128 and re.search(r"not a git repository|cannot change to '", out.stderr):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        raise GitUnresolved(f"git exited {out.returncode}")
    return out.stdout.strip()


def _short_ref(ref: str) -> str:
    for prefix in ("refs/heads/", "heads/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def _push_hits_default(call: policy.shellnorm.GitCall, cwd: str) -> bool:
    remote, refs, every = parse_push(call.args)
    if every:
        return True
    current: list[str | None] = []

    def cur() -> str | None:
        if not current:
            current.append(_git(call, cwd, "symbolic-ref", "--short", "-q", "HEAD"))
        return current[0]

    def on_default() -> bool:
        return (cur() or "").lower() in DEFAULT_BRANCHES

    if not refs:
        return on_default() or _config_sends_to_default(call, cwd, remote, cur)
    targets = []
    for ref in refs:
        src, colon, dst = ref.lstrip("+").partition(":")
        targets.append(_short_ref(dst if colon and dst else src))
    for t in targets:
        if t in ("HEAD", "@", ""):
            if on_default():
                return True
        elif any(ch in t for ch in "$*?["):
            if fnmatch.filter(DEFAULT_BRANCHES, t) or on_default():
                return True
        elif t.lower() in DEFAULT_BRANCHES:
            return True
    # a remote whose default branch is not main/master (origin/HEAD -> origin/trunk)
    if remote and re.fullmatch(r"[\w.-]+", remote):
        head = _git(call, cwd, "symbolic-ref", "--short", "-q", f"refs/remotes/{remote}/HEAD")
        default = head.split("/", 1)[1].lower() if head and "/" in head else ""
        if default and any(t.lower() == default or (t in ("HEAD", "@", "") and (cur() or "").lower() == default) for t in targets):
            return True
    return False


def _short_matches_default(dst: str, on_default) -> bool:
    """Does the destination of one refspec (`HEAD`, `refs/heads/main`, a glob) hit main/master, or the current branch while that is main/master?"""
    dst = _short_ref(dst.lstrip("+").partition(":")[2] or dst.lstrip("+"))
    if dst in ("HEAD", "@", ""):
        return on_default()
    if any(ch in dst for ch in "$*?["):
        return bool(fnmatch.filter(DEFAULT_BRANCHES, dst)) or on_default()
    return dst.lower() in DEFAULT_BRANCHES


def _config_sends_to_default(call: policy.shellnorm.GitCall, cwd: str, remote: str | None, cur) -> bool:
    """A `git push` or `git push <remote>` with no refspec goes where the repository's own config says (`remote.<r>.push`, else `push.default`: `upstream`
    follows `branch.<b>.merge`, `matching` pushes every branch both sides have), not where the branch is named. The system and global config are ignored
    (`_git_env`); a repository that sets any of these to reach main is an SI-08 hit. A branch name `git` cannot report (detached HEAD) has no config to follow."""
    branch = cur()
    if not branch:
        return False
    on_default = lambda: branch.lower() in DEFAULT_BRANCHES  # noqa: E731
    name = remote if remote and re.fullmatch(r"[\w.-]+", remote) else (_git(call, cwd, "config", "--get", f"branch.{branch}.remote") or "origin")
    if re.fullmatch(r"[\w.-]+", name):
        pushes = _git(call, cwd, "config", "--get-all", f"remote.{name}.push")
        if pushes:   # `remote.<r>.push` replaces push.default for a bare push
            return any(_short_matches_default(line.strip(), on_default) for line in pushes.splitlines() if line.strip())
    mode = (_git(call, cwd, "config", "--get", "push.default") or "simple").lower()
    if mode == "matching":
        return True
    if mode in ("upstream", "tracking"):
        merge = _git(call, cwd, "config", "--get", f"branch.{branch}.merge")
        return bool(merge) and _short_ref(merge).lower() in DEFAULT_BRANCHES
    return False


_GIT_ENV_ASSIGN = re.compile(r"(?<![\w])GIT_(?:DIR|WORK_TREE|COMMON_DIR)=")
_CD_VARIABLE = re.compile(r"(?:^|\s)(?:cd|pushd)\s+(?:-\S+\s+)*\S*[$`]")
_REPO_CREATORS = ("clone", "init", "worktree")


def _unfindable(call: policy.shellnorm.GitCall) -> bool:
    """The directory or repository the push names is written with a variable or a substitution: git is run on text that is not a path."""
    return any(re.search(r"[$`\x06]", d) for d in (*call.dirs, call.git_dir or "", call.work_tree or ""))


def _on_default_branch(cwd: str, command: str) -> bool:
    """A `git push` whose destination is main/master or the current branch while that is main/master (invariant SI-08).

    The classifier has already removed git global options, escapes and quoting (policy.segments_cwd), so `git -C d push origin HEAD`
    and `git push origin HEAD:refs/heads/main` are seen as what they are, and each push is resolved in the directory it
    runs in (`cd repo && git push` follows the cd). A destination that cannot be resolved (`$BRANCH`) counts as the current branch. A repository that cannot
    be found from here is "nothing to push from" only when the command names a real directory: a directory written with a variable (`git -C "$R" push`,
    `cd "$R" && git push`), `GIT_DIR=` / `GIT_WORK_TREE=` in the command, or a repository the same command creates (`git clone u r && git -C r push`) is unresolved
    (GitUnresolved), never "not main".
    """
    extra, _ = policy.hidden(command, cwd)   # a git alias that expands to a push, a script the command writes and runs (W-31)
    segs = policy.segments_cwd(command + "".join("\n" + e for e in extra), cwd)
    pushes = [(call, dirs) for seg, dirs in segs if not policy.is_read_only(seg) for call in policy.git_calls(seg) if call.sub == "push"]
    if not pushes:
        return False
    steered = bool(_GIT_ENV_ASSIGN.search(command)) or any(_CD_VARIABLE.search(seg) for seg, _ in segs)
    creates = any(c.sub in _REPO_CREATORS for seg, _ in segs if not policy.is_read_only(seg) for c in policy.git_calls(seg))
    for call, dirs in pushes:
        if steered or _unfindable(call):
            raise GitUnresolved("the repository of this push is named by a variable or the environment")
        if creates and any(_git(call, d, "rev-parse", "--git-dir") is None for d in dirs):
            raise GitUnresolved("the repository of this push does not exist yet: the same command creates it")
        if any(_push_hits_default(call, d) for d in dirs):
            return True
    return False


def _detail(event: dict, a: policy.Assessment | None = None, **more) -> dict:
    d = {"session": event.get("session_id"), "tool": event.get("tool_name"), "permission_mode": event.get("permission_mode"),
         "agent_id": event.get("agent_id"), **more}
    if a is not None:
        d["plan_hash"] = a.plan_hash
    return d


def _fail_closed(mode: str, why: str) -> HookResult:
    return _verdict(mode, f"JADE gate: {why} Failing closed. Run `jade check` to see what is wrong; a human can bypass with JADE_GATE_MODE=advisory.")


def _policy_failure(mode: str, event: dict, tool: str, tool_input: dict, cwd: str, exc: Exception) -> HookResult:
    """The call could not be classified (the policy or an overlay does not load, a rule raised). Block it when it is high-risk by the
    shipped rules or not clearly harmless; let a clearly read-only command or an ordinary file write through."""
    why = failsafe_reason(event)
    if why is None:
        try:
            with policy.base_only():
                b = policy.assess(tool, tool_input, cwd)
                if b.blocked or at_least(b.level, policy.permit_floor()):
                    why = f"the shipped rules call it {b.level} risk ({b.rule_id})"
        except Exception:  # noqa: BLE001, S110 - the policy-free check above already said this call looks harmless
            pass
    if why is None:
        return HookResult()
    return _fail_closed(mode, f"the policy could not be loaded or applied ({type(exc).__name__}: {str(exc)[:200]}), and {why}.")


def pretool(event: dict, permit=None) -> HookResult:
    global _STARTED
    _STARTED = time.monotonic()   # Jev in the hook (proxy.step) is given what is left of BUDGET_S
    mode = paths.gate_mode()
    if mode == "off":
        return HookResult()
    tool = event.get("tool_name", "")
    if tool == "AskUserQuestion":
        from . import askchat

        return askchat.rewrite(event)   # a question that carries an ask token of this session is replaced by the hook's own (docs/12); any other passes
    if _is_gui_call(tool, event) and (res := _gui_pause(event, tool)) is not None:
        return res
    if not policy.is_gated(tool):
        return HookResult()
    _note_unnamed_tool(tool)
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or ""
    subject = policy.tool_subject(tool, tool_input)
    try:
        a = _classify(tool, tool_input, cwd)
    except Exception as e:  # noqa: BLE001 - fail closed for anything that is not clearly harmless
        return _policy_failure(mode, event, tool, tool_input, cwd, e)
    try:
        if policy.runs_shell(tool, tool_input) and "SI-08" not in a.blocked and _on_default_branch(cwd, subject):
            a.blocked.append("SI-08")
    except Exception as e:  # noqa: BLE001 - git hung or failed: the push destination is unknown
        if at_least(a.level, policy.permit_floor()):
            return _fail_closed(mode, f"the destination of this {a.level}-risk call could not be resolved ({type(e).__name__}).")
    if a.blocked:
        _journal("gate", "safety_block", a, event)
        return _verdict(mode, f"JADE safety invariant {', '.join(a.blocked)} blocks this {tool} call. No permit can authorize it. "
                              f"Rule text: policies/safety.yaml. Do not rephrase the command to get around the invariant; ask the human.", a.blocked)
    if a.ho_ids and paths.level() in ("low", "medium", "high") and not at_least(a.level, "high"):
        a.level, a.elevated = "high", True   # docs/12: a human-only class (a mod file, the terminal panel ...) is high work once the human has set a level; `level` and `rule_id` stay the rule's
    if not at_least(a.level, policy.permit_floor()):
        if a.rule_id in policy.RECORDED_RULES and a.recorded:
            if a.rule_id in ("R-31", "R-32"):
                _journal_blind(a, event)   # W-31: the label of what the gate could not read, once per session and label
            else:
                _journal("gate", "secret_read", a, event)   # W-55: counted (rule, tool, session), never the path or the command; the read itself is not gated
        return HookResult()
    try:
        return _gate("advisory" if paths.level() == "off" else mode, tool, a, event, permit)   # level off: JADE only watches (docs/12)
    except Exception as e:  # noqa: BLE001 - the ledger is unreadable, locked or full: no permit can be checked, so nothing high-risk runs
        return _fail_closed(mode, f"this {tool} call is {a.level} risk ({a.rule_id}) and the permit ledger failed ({type(e).__name__}: {str(e)[:160]}), "
                                  "so no permit can be checked.")


GOVERNANCE_INVARIANTS = frozenset({"SI-09", "SI-10", "SI-11", "SI-12"})


def _verdict(mode: str, message: str, blocked: list[str] | tuple[str, ...] = ()) -> HookResult:
    """Exit 2, or an advisory exit 0. Once the human has set a level (docs/12) the governance invariants block whatever the gate mode, and any level at all
    keeps them firm; the other invariants are advisory at level `off` ("JADE only watches") and in advisory gate mode."""
    level = paths.level()
    if level is not None and GOVERNANCE_INVARIANTS & set(blocked):
        return HookResult(2, message)
    if mode == "advisory" or (level == "off" and blocked):
        return HookResult(0, "[advisory, not blocked] " + message)
    return HookResult(2, message)


def _journal_blind(a: policy.Assessment, event: dict) -> None:
    """W-31: event `blind_spot` for a command the gate rated R-31 (a script file, `eval "$VAR"`, a relative write after `cd $VAR` ...). The detail carries the label and
    the session, never the command or a path; a label is journaled once per session, so a long session of `python3 x.py` is one row, not one per call."""
    try:
        from . import ledger

        db = ledger.open(timeout=LEDGER_TIMEOUT_S)
        try:
            session = str(event.get("session_id") or "")[:200]
            for label in a.blind:
                db.event("gate", "blind_spot", guard=f"{a.rule_id} {a.level} {label}", actor="hook", key=f"blind_spot:{db.source or ''}:{session}:{label}",
                         detail=_detail(event, a, blind=[label]))
        finally:
            db.close()
    except Exception:  # noqa: BLE001, S110 - journaling must never change the gate verdict
        pass


def _journal(entity: str, kind: str, a: policy.Assessment, event: dict) -> None:
    try:
        from . import ledger

        db = ledger.open(timeout=LEDGER_TIMEOUT_S)
        db.event(entity, kind, guard=f"{a.rule_id} {a.level} {','.join(a.blocked)}".strip(), actor="hook", detail=_detail(event, a))
        db.close()
    except Exception:  # noqa: BLE001, S110 - journaling must never change the gate verdict
        pass


def _note_unnamed_tool(tool: str) -> None:
    """W-36: record the name (never the arguments) of an MCP tool no specific rule names, once per tool, so `jade check` has an inventory to read: a
    medium or low call is otherwise never journaled. The entity is the tool, which the retention purge keeps; the key is per ledger source."""
    try:
        if not policy.is_mcp(tool) or policy.tool_coverage(tool) is None:
            return
        from . import ledger

        db = ledger.open(timeout=LEDGER_TIMEOUT_S)
        try:
            rule = policy._tool_rule(tool, command_decides=False)
            db.event(tool[:200], "tool_unnamed", guard=f"{rule['id']} {rule['level']}", actor="hook", key=f"tool_unnamed:{db.source or ''}:{tool[:200]}",
                     detail={"tool": tool[:200]})
        finally:
            db.close()
    except Exception:  # noqa: BLE001, S110 - the inventory must never change the gate verdict
        pass


def _si07_hold(db, a: policy.Assessment):
    """SI-07: an earlier decision for this exact command whose outcome is unknown, or an irreversible one still executing. Such a command
    is never started again until the outcome is reconciled; no permit is raised or consumed for it. "This exact command" is the command's hold_key
    (its canonical gated segments, so quoting, `&& true`, an env prefix, `sh -c`, a full program path or another cwd do not make it a new command);
    a different argument list is a different command. Returns (decision_id, reason) or None."""
    now_s = ts()
    held = "status IN ('OUTCOME_UNKNOWN','EXECUTING')"   # two lookups, one per key: with `plan_hash=? OR hold_key=?` the planner reads every held decision instead (W-58)
    for r in db.all(f"SELECT decision_id, status, expires_at, doc_json, updated_at FROM decisions WHERE plan_hash=? AND {held} "  # noqa: S608 - constant fragment
                    f"UNION SELECT decision_id, status, expires_at, doc_json, updated_at FROM decisions WHERE hold_key=? AND {held} "
                    "ORDER BY updated_at DESC", (a.plan_hash, a.hold_key or a.plan_hash)):
        if r.status == "OUTCOME_UNKNOWN" or r.expires_at <= now_s:
            return r.decision_id, "has an unknown outcome (an unknown outcome is never success)"
        try:
            irreversible = json.loads(r.doc_json)["risk"]["reversibility"] == "irreversible"
        except (ValueError, KeyError, TypeError):
            irreversible = True
        if irreversible:
            return r.decision_id, "is still executing and is irreversible"
    return None


def _head_commit(call: policy.shellnorm.GitCall, cwd: str) -> str:
    """HEAD commit for a permit fingerprint; `none` for an unborn branch or a directory git cannot read (it records that state, it decides nothing).
    A hung git still raises: the gate fails closed."""
    try:
        return _git(call, cwd, "rev-parse", "HEAD") or "none"
    except GitUnresolved as e:
        if isinstance(e.__cause__, subprocess.TimeoutExpired):
            raise
        return "none"


def fingerprints(subject: str, cwd: str) -> dict[str, str]:
    """Current values of the preconditions a permit records, for a command run in `cwd`. For every `git push` in it: the HEAD commit of the
    repository it runs in, so a permit approved for pushing one state is not used after the branch moved. A git call that hangs raises
    (the gate fails closed)."""
    heads = []
    for seg, dirs in policy.segments_cwd(subject, cwd):
        if policy.is_read_only(seg):
            continue
        for call in policy.git_calls(seg):
            if call.sub == "push":
                heads += [f"{policy.real(os.path.expanduser(d))}@{_head_commit(call, d)}" for d in dirs]
    return {"git_head": ";".join(sorted(set(heads)))} if heads else {}


def _gate(mode: str, tool: str, a: policy.Assessment, event: dict, permit) -> HookResult:
    from . import decide, ledger, permits, state_machine

    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        db.event("gate", "gated", guard=f"{a.rule_id} {a.level}", actor="hook", detail=_detail(event, a))
        decide.sweep(db)  # a decision past its deadline is EXPIRED before a permit is looked at (and its permits are revoked with it)
        permits.sweep(db)
        cwd = event.get("cwd") or ""
        tin = event.get("tool_input") or {}
        fp = fingerprints(a.plan_subject, cwd) if policy.runs_shell(tool, tin) else {}
        if isinstance(tin, dict) and policy.content_bound(tool, tin):   # W-39: the permit is for this content, not for any content sent to the same path or command
            fp = {**fp, permits.CONTENT_KIND: policy.content_sha(tin)}
        who = {"session": event.get("session_id"), "agent": event.get("agent_id")}
        call_id = permits.call_id_of(event)

        def replayed():
            """W-40: this very call (same host call id, plan, content and caller) already spent a permit a moment ago: a second delivery of it is answered the same."""
            again = permits.consumed_by_call(db, call_id, a.plan_hash, level=a.level, fingerprints=fp, **who)
            return None if again is None else HookResult(0, f"JADE: permit {again.permit_id} was already consumed for this same {tool} call; it is not spent again.")

        if (res := replayed()) is not None:
            return res
        hold = _si07_hold(db, a)
        if hold:
            did, reason = hold
            if mode != "advisory":
                db.event("gate", "safety_block", guard=f"{a.rule_id} {a.level} SI-07", actor="hook", detail=_detail(event, a, decision_id=did))
            return _verdict(mode, f"JADE safety invariant SI-07 blocks this {tool} call. An earlier run of this exact command (decision {did}) {reason}"
                                  f"{' and still executing' if 'executing' in reason else ''}. It is not retried automatically and no permit is raised or used for it. "
                                  f"Read the real state, then record it: jade decide outcome {did} --type observed --success true|false --method \"how you checked\". "
                                  "Do not rephrase the command to get around the invariant; ask the human.")
        found = permit or permits.find_usable(db, a.plan_hash, level=a.level, fingerprints=fp, **who)
        refusal = None
        if found is not None:
            try:
                with db.tx():  # the permit and its decision move together: a decision that cannot advance leaves the permit unused
                    refusal = permits.try_consume(db, found.permit_id, a.plan_hash, level=a.level, fingerprints=fp, call_id=call_id, **who)
                    if refusal is None:
                        row = db.one("SELECT decision_id, status FROM decisions WHERE decision_id=?", (found.decision_id,))
                        if row and row.status == "WAITING_HUMAN":
                            decide.step(db, row.decision_id, "human_accept", guard=f"permit {found.permit_id} approved from a terminal", actor="human")
                            decide.step(db, row.decision_id, "authorize", permit_ok=True, guard=f"permit {found.permit_id} issued, unexpired, unused, plan hash equal")
                        if row and row.status in ("WAITING_HUMAN", "AUTHORIZED"):
                            decide.step(db, row.decision_id, "execute", guard="PreToolUse saw the matching command; permit consumed", actor="hook", ttl_s=3600)
            except state_machine.IllegalTransition as e:
                refusal = f"its decision could not advance ({e})"
            if refusal is None:
                return HookResult(0, f"JADE: permit {found.permit_id} consumed for this {tool} call.")
        if (res := replayed()) is not None:   # a parallel delivery of the same call spent the permit between the lookup above and the use
            return res
        from . import askchat, memory, proxy

        if (res := memory.step(db, mode, a, tool, event, fp, cwd)) is not None:   # docs/12: a call the human confirmed earlier runs without a question (levels low and medium)
            return res
        if (res := proxy.step(db, mode, a, tool, event, fp, cwd, _STARTED)) is not None:   # docs/12: Jev decides in the owner's place, inside its band (levels low and medium)
            return res
        if (res := askchat.step(db, mode, a, tool, event, fp, cwd)) is not None:   # docs/12: the human answers in the chat (consumes a grant, or asks)
            return res
        from . import askdialog

        if (res := askdialog.step(db, mode, a, tool, event, fp, cwd)) is not None:   # docs/12: a subagent call is answered in a macOS dialog the hook waits for
            return res
        if mode == "advisory":
            db.event("gate", "would_block", guard=f"{a.rule_id} {a.level}", actor="hook", detail=_detail(event, a))
            return HookResult(0, f"[advisory, not blocked] JADE: {a.level} risk ({a.rule_id}: {a.rationale}) would need a permit.")
        ctx = permits.make_context(tool, a, event, cwd=cwd)
        pending = permits.pending_for(db, a.plan_hash, context=ctx, fingerprints=fp)
        if pending is None:
            with db.tx():
                doc = decide.open_decision(db, f"Execute {tool}: {decide.scrub(a.subject)[:300]}", command=a.subject, tool=tool, cwd=cwd,
                                           session=event.get("session_id"), route="ESCALATE", assessment=a)
                decide.step(db, doc["decision_id"], "route", guard="route=ESCALATE: permit required")
                decide.step(db, doc["decision_id"], "escalate", guard="no issued permit for this plan", ttl_s=permits.DEFAULT_TTL_S)
                pid = permits.request(db, decide.canonical_action(a.rule_id, a.subject), a.plan_hash, risk=a.level, decision_id=doc["decision_id"],
                                      context=ctx, fingerprints=fp, environment=a.environment).permit_id
        else:
            pid = pending.permit_id
        why = f" (Permit {found.permit_id} exists but cannot be used: {refusal}.)" if found is not None and refusal else ""
        if ctx.get("redacted") and permits.keep_raw(db, pid, permits.raw_parts(tool, a, event)):   # W-43: the human allowed it, so the raw call is kept for their review only
            return HookResult(2, f"JADE gate: {a.level} risk ({a.rule_id}: {a.rationale}). This {tool} call needs a human-approved permit, and the {ctx['redacted']} secret-shaped "
                                 f"value(s) in it are not stored in the ledger.{why}\n"
                                 f"Permit {pid} is PENDING. Ask the human to review the whole command in their own terminal:  jade permit show {pid} --raw   and, if it is right, to approve it:  "
                                 f"jade permit approve {pid}\n"
                                 "Then retry the identical command (single use, 30 minutes). You cannot view the raw command or approve it yourself; "
                                 "do not rephrase the command to avoid the gate. Continue with other work meanwhile.")
        if ctx.get("redacted"):
            return HookResult(2, f"JADE gate: {a.level} risk ({a.rule_id}: {a.rationale}). This {tool} call needs a human-approved permit, and the {ctx['redacted']} secret-shaped "
                                 f"value(s) in it are not stored, so a human cannot review the whole command and the permit cannot be approved.{why}\n"
                                 "Put each secret in an environment variable or a file the command reads, remove it from the command line, and retry. "
                                 f"(Permit {pid} stays PENDING and will expire.) Do not rephrase the command to avoid the gate.")
        return HookResult(2, f"JADE gate: {a.level} risk ({a.rule_id}: {a.rationale}). This {tool} call needs a human-approved permit.{why}\n"
                             f"Permit {pid} is PENDING. Ask the human to run in their own terminal:  jade permit approve {pid}\n"
                             "Then retry the identical command (single use, 30 minutes). You cannot approve it yourself; "
                             "do not rephrase the command to avoid the gate. Continue with other work meanwhile.")
    finally:
        db.close()


def _failed(resp) -> bool:
    return isinstance(resp, dict) and bool(resp.get("is_error") or resp.get("isError") or resp.get("interrupted") or resp.get("error"))


def _executing_decision(db, tool: str, tin: dict, event: dict, cwd: str):
    """The decision a PostToolUse event ends, or None. A Bash call is found by its plan hash. Any other gated call (W-40) must also have been permitted
    for this session, and for this content when the permit bound content (W-39): two writes to one path have one plan hash."""
    from . import permits

    if tool == "Bash":
        command = str(tin.get("command") or "")
        row = db.one("SELECT decision_id FROM decisions WHERE plan_hash=? AND status='EXECUTING' ORDER BY updated_at DESC", (policy.plan_hash("Bash", command, cwd),))
        return row.decision_id if row else None
    try:
        a = _classify(tool, tin, cwd)
    except Exception:  # noqa: BLE001 - the call cannot be matched to a decision: it stays EXECUTING and is held as unknown at its deadline (SI-07)
        return None
    sess = event.get("session_id")
    sha = policy.content_sha(tin) if policy.content_bound(tool, tin) else None
    for row in db.all("SELECT decision_id, session_id FROM decisions WHERE plan_hash=? AND status='EXECUTING' ORDER BY updated_at DESC", (a.plan_hash,)):
        if isinstance(sess, str) and row.session_id != sess.ljust(8, "-"):
            continue
        bound = permits.content_of_decision(db, row.decision_id)
        if sha is not None and bound is not None and bound != sha:
            continue
        return row.decision_id
    return None


def posttool(event: dict) -> HookResult:
    """The call returned: record the outcome of the decision it was permitted under. Bash and every other gated tool (Write, Edit, an MCP call ...) are
    closed the same way (W-40); before, a permitted Write stayed EXECUTING until its deadline and was then held as unknown (SI-07). Only the exit status of
    the call is known here, so the outcome is `observed` with indirect attribution, as for Bash."""
    tool = event.get("tool_name")
    if tool == "AskUserQuestion":
        from . import askchat

        return askchat.settle(event)   # the human's answer to an ask of this session, read from the host's tool_response
    return _close_decision(event, not _failed(event.get("tool_response")), "tool call returned (exit status only; the goal is not verified)")


def posttool_failure(event: dict) -> HookResult:
    """PostToolUseFailure (probe cell 10): the host fires it, and not PostToolUse, for a call that failed. The decision it ran under is closed as an observed failure,
    so SI-07 stops holding it as unknown and the next identical call is asked again. An interrupted call may have run in part: its decision is left as it is
    (EXECUTING, then held as unknown at its deadline), which is the cautious answer."""
    if event.get("is_interrupt"):
        return HookResult()
    return _close_decision(event, False, "tool call failed (host event PostToolUseFailure; exit status only, the goal is not verified)")


def _close_decision(event: dict, success: bool, method: str) -> HookResult:
    tool = event.get("tool_name")
    if not isinstance(tool, str) or not (tool in policy.POST_TOOLS or policy.is_mcp(tool)):
        return HookResult()
    tin = event.get("tool_input")
    tin = tin if isinstance(tin, dict) else {}
    cwd = str(event.get("cwd") or "")
    from . import decide, ledger

    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        command = str(tin.get("command") or "")
        if tool == "Bash" and BYPASS.search(command) and "jade" not in command:
            db.event(event.get("session_id") or "session", "BYPASS", guard="raw Decisions API call outside the adapter", actor="hook")
        did = _executing_decision(db, tool, tin, event, cwd)
        if did:
            decide.record_outcome(db, did, "observed", success, method, attribution="indirect")
    finally:
        db.close()
    return HookResult()


def undisposed(db, session: str, only_unreminded: bool = True):
    """Abstain/reject/error calls in this session with no later accepted answer to the same question and no `jade ack`."""
    extra = "AND c.reminded=0" if only_unreminded else ""
    return db.all(
        "SELECT c.call_id, c.verdict, c.p, c.exit_code, c.question_id, c.error_kind, substr(COALESCE(c.question_text,''),1,90) AS q FROM jev_calls c "
        f"WHERE c.session_id=? AND c.state='DONE' AND c.exit_code IN (3,4,5,6) AND c.disposition IS NULL {extra} "  # noqa: S608 - constant fragments
        "AND c.ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?) AND c.task_family IS NOT 'evidence_quality' AND COALESCE(c.primitive,'')<>'fast' "
        "AND NOT EXISTS (SELECT 1 FROM jev_calls d WHERE d.session_id=c.session_id AND d.claim_hash=c.claim_hash AND d.exit_code=0 AND d.rowid>c.rowid) "
        "ORDER BY c.rowid", (session, f"-{REMIND_WINDOW_S} seconds"))


@dataclass
class AckResult:
    count: int = 0
    refused: str | None = None   # reason code; None when the ack was applied (or there was nothing to ack)
    message: str = ""


def acknowledge(db, session: str, call_ids: list[str], *, all_: bool, disposition: str, note: str, human: bool) -> AckResult:
    """Record a disposition for Jev answers that were not a yes (W-59, docs/05 T24).

    A disposition is a claim about what the caller did, and nothing here can check it, so what it may reach is narrow: only answers with exit
    3, 4, 5 or 6 that have no disposition yet, and, for an agent, only this session's. An agent needs a note and gets `ACK_MAX_PER_WINDOW`
    acks per window. A human at a terminal may cross sessions and is not limited. Every applied ack is journaled (event `ACK`: actor, session,
    disposition, note, call ids) and so is every refusal that is not a missing note (`ACK_REFUSED`). An ack touches `jev_calls.disposition` and
    nothing else: no event, label, decision or permit."""
    from . import redact

    note = redact.redact((note or "").strip(), secrets_only=True)[0][:ACK_NOTE_MAX]
    if not note:
        return AckResult(refused="no_note", message="a disposition needs a --note saying what was done; the note is a claim and is journaled with your session")
    actor = "human" if human else "agent"
    with db.tx() as con:
        def refuse(reason: str, message: str) -> AckResult:
            db.event(session, "ACK_REFUSED", next=disposition, guard=message, actor=actor, detail={"reason": reason, "call_ids": list(call_ids)[:20]}, con=con)
            return AckResult(refused=reason, message=message)

        if not human:
            n = db.one("SELECT COUNT(*) AS n FROM events WHERE kind='ACK' AND entity_id=? AND actor='agent' AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?)",
                       (session, f"-{ACK_RATE_WINDOW_S} seconds")).n   # not `live()`: a JADE_SOURCE=test tag must not reset an agent's own counter
            if n >= ACK_MAX_PER_WINDOW:
                return refuse("rate_limit", f"rate limit: {n} acks by this session in the last hour (limit {ACK_MAX_PER_WINDOW}). A human acknowledges the rest at a terminal, or tell the user the answers stand unresolved")
        if all_:
            ids = [r.call_id for r in undisposed(db, session, only_unreminded=False)]
        else:
            ids, bad = [], []
            for cid in dict.fromkeys(call_ids):
                r = db.get_call(cid)
                if r is None:
                    bad.append(f"{cid}: unknown call")
                elif not human and r.session_id != session:
                    bad.append(f"{cid}: belongs to another session; a human acknowledges those at a terminal")
                elif r.state != "DONE" or r.exit_code not in ACK_ELIGIBLE_EXITS:
                    bad.append(f"{cid}: exit {r.exit_code}, not an abstain, reject or error answer, so there is nothing to acknowledge")
                elif r.disposition is not None:
                    bad.append(f"{cid}: already has disposition '{r.disposition}'")
                else:
                    ids.append(cid)
            if bad:
                return refuse("not_ackable", "; ".join(bad))
        if not ids:
            return AckResult()
        con.executemany("UPDATE jev_calls SET disposition=?, disposition_note=? WHERE call_id=? AND disposition IS NULL", [(disposition, note, i) for i in ids])
        db.event(session, "ACK", next=disposition, guard=note, actor=actor, detail={"call_ids": ids, "count": len(ids), "as": disposition}, con=con)
    return AckResult(count=len(ids))


def stop(event: dict) -> HookResult:
    if paths.gate_mode() == "off" or event.get("stop_hook_active"):
        return HookResult()
    session = event.get("session_id")
    if not session:
        return HookResult()
    from . import ledger

    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        from . import askchat

        if paths.gate_mode() == "enforce" and (nudge := askchat.stop_nudge(db, session)) is not None:
            return nudge
        rows = undisposed(db, session)
        if not rows:
            return HookResult()
        with db.tx() as con:
            con.executemany("UPDATE jev_calls SET reminded=1 WHERE call_id=?", [(r.call_id,) for r in rows])
        lines = [f"  {r.call_id}  exit {r.exit_code} {r.verdict or r.error_kind or ''}" + (f" P={r.p:.2f}" if r.p is not None else "") + f"  {r.q}" for r in rows[:6]]
        more = f"\n  ... and {len(rows) - 6} more" if len(rows) > 6 else ""
        return HookResult(2, "JADE: this session has Jev answers that were not a yes and have no disposition:\n" + "\n".join(lines) + more +
                             "\nBefore finishing, do one of: add raw evidence and re-ask; tell the user plainly the claim is unverified; "
                             "or record the disposition:  jade ack --all --as revised|escalated|reported|dropped --note \"...\"\n"
                             "A non-zero exit from jev/jade ask is never a yes. This reminder is shown once per call.")
    finally:
        db.close()


def _override_notice(db, event: dict) -> list[str]:
    """Environment overrides in force for this hook process that weaken the gate (docs/05 T19, G05): a different policy directory, configuration or
    ledger, or a gate mode below enforce. They are shown to the session and journaled once per session, so a weakened gate is never silent."""
    weak = [o for o in paths.gate_overrides() if o.weakens]
    if not weak:
        return []
    names = ", ".join(f"{o.var}={o.value}" for o in weak)
    try:
        db.event("gate", "gate_override", guard=names, actor="hook", key=f"gate_override:{event.get('session_id') or '-'}:{names}",
                 detail={"session": event.get("session_id"), "overrides": [{"var": o.var, "value": o.value, "why": o.why} for o in weak]})
    except Exception:  # noqa: BLE001, S110 - journaling must never change the hook's answer
        pass
    return [f"JADE: gate override in force in the hook environment ({names}); it weakens the gate. `jade check` lists it, unset it unless a human set it on purpose."]


def _shadow_notice(db, event: dict) -> list[str]:
    """Project settings of the live session (the event's cwd and its ancestors up to the repo root) that disable the hooks or shadow JADE's (docs/05
    T19, W-47): shown and journaled once per session as `gate_override`. Never blocks, and a failure here changes nothing."""
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return []
    try:
        from . import ops

        found = ops.shadow_problems(cwd, managed=False, plugins=False)
    except Exception:  # noqa: BLE001 - a scanner failure must never change the hook's answer
        return []
    if not found:
        return []
    digest = hashlib.sha1("\n".join(found).encode("utf-8", "replace")).hexdigest()[:12]   # noqa: S324 - an idempotency key, not security
    try:
        db.event("gate", "gate_override", guard="; ".join(found)[:500], actor="hook", key=f"gate_override:{event.get('session_id') or '-'}:shadow:{digest}",
                 detail={"session": event.get("session_id"), "cwd": cwd, "shadowing": found[:10]})
    except Exception:  # noqa: BLE001, S110 - journaling must never change the hook's answer
        pass
    return [f"JADE: settings of this project disable or shadow the JADE hooks ({'; '.join(found[:3])}); `jade check` lists every one. Remove the setting unless a human set it on purpose."]


def _ready_notice(db, rows: list[dict], shown: set, room: int) -> list[str]:
    """Open items whose dependencies are all done, told once each (W-51): the event key names the item and what it waited on, so a later session does not
    repeat it. An item is journaled only when its line fits in the briefing, so one that was cut is told next time. Never blocks."""
    lines: list[str] = []
    try:
        done = {r.id for r in db.all("SELECT id FROM work_items WHERE status='done'")}
        for w in rows:
            deps = w.get("dependencies") or []
            if len(lines) >= room or w["id"] in shown or w.get("status") != "open" or not deps or not set(deps) <= done:
                continue
            if db.event(w["id"], "work_ready", guard="dependencies done", actor="hook", key=f"work_ready:{w['id']}:{','.join(sorted(deps))}"):
                lines.append(f"JADE work item {w['id']} ({w.get('priority', '')}) is ready: its dependencies ({', '.join(deps)}) are done. {w.get('title', '')}. Next: {w.get('next_action', '')}")
    except Exception:  # noqa: BLE001 - an announcement must never change the hook's answer
        pass
    return lines


def session_start(event: dict) -> HookResult:
    from . import decide, ledger, permits

    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        decide.sweep(db)
        permits.sweep(db)
        out = _override_notice(db, event) + _shadow_notice(db, event)
        pending = permits.listing(db, ("PENDING",))
        pend = [p for p in pending if getattr(p, "source", None) != "test"]
        if pend:
            out.append(f"JADE: {len(pend)} permit(s) awaiting human approval: " + ", ".join(p.permit_id for p in pend[:5]))
        waiting = db.one("SELECT COUNT(*) AS n FROM decisions WHERE status='WAITING_HUMAN' AND " + ledger.live()).n
        if waiting:
            out.append(f"JADE: {waiting} decision(s) in WAITING_HUMAN (`jade decide list --status WAITING_HUMAN`).")
        tagged = db.one("SELECT (SELECT COUNT(*) FROM permits WHERE source='test') + (SELECT COUNT(*) FROM decisions WHERE source='test') AS n").n
        if tagged:   # rows a verification run left under JADE_SOURCE=test: shown apart from real approvals, never as something to approve (W-72)
            open_tagged = len(pending) - len(pend)
            still = f" {open_tagged} of the permits are still PENDING and approvable whatever their tag: look at `jade permit list` before clearing them." if open_tagged else ""
            out.append(f"JADE: {tagged} test-tagged permit/decision row(s) from a verification run; not real approvals.{still} A human clears them with `jade ledger purge --test-rows`.")
        try:   # a registry that cannot be read is informational, never a reason to lose the rest of the briefing (W-51)
            from . import ops

            ops.check_question_drift(db=db)
        except Exception:  # noqa: BLE001, S110
            pass
        today = f"{now():%Y-%m-%d}"
        rows = [json.loads(r.doc_json) for r in db.all("SELECT doc_json FROM work_items WHERE status IN ('open','in_progress','waiting_human')")]
        shown = set()
        for w in rows:
            trig = str(w.get("retry_or_revisit_trigger", ""))
            due = re.search(r"\d{4}-\d{2}-\d{2}", trig)
            if trig == "on_session_start" or (due and due.group(0) <= today):
                out.append(f"JADE work item {w['id']} ({w.get('priority', '')}): {w.get('title', '')}. Next: {w.get('next_action', '')}")
                shown.add(w["id"])
        out.extend(_ready_notice(db, rows, shown, room=8 - len(out)))
        return HookResult(0, "", "\n".join(out[:8]))
    finally:
        db.close()


HANDLERS = {"pretool": pretool, "posttool": posttool, "posttool-failure": posttool_failure, "stop": stop, "session-start": session_start}


def _on_alarm(_signum, _frame):
    raise HookBudgetExceeded


def _arm(seconds: float) -> None:
    try:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    except (ValueError, AttributeError, OSError):  # not the main thread, or no SIGALRM on this platform: the ledger and git timeouts still bound the hook
        pass


def _disarm() -> None:
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
    except (ValueError, AttributeError, OSError):
        pass


def _failsafe_exit(name: str, event: dict, what: str) -> int:
    if name == "pretool" and paths.gate_mode() != "off":
        why = failsafe_reason(event)
        if why:
            res = _fail_closed(paths.gate_mode(), f"{what}, and {why}.")
            print(res.stderr, file=sys.stderr)
            return res.exit_code
    print(f"jade hook {name}: {what}; not blocking", file=sys.stderr)
    return 0


def main(name: str) -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except ValueError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    try:
        if name == "pretool":
            _arm(BUDGET_S)
        try:
            res = HANDLERS[name](event)
        finally:
            _disarm()
    except HookBudgetExceeded:
        return _failsafe_exit(name, event, f"hook time budget ({BUDGET_S:g} s) exceeded")  # the host would kill the hook at its own timeout and proceed
    except Exception as e:  # noqa: BLE001 - last line of defence; see module docstring
        return _failsafe_exit(name, event, f"internal error ({type(e).__name__}: {e})")
    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)
    return res.exit_code
