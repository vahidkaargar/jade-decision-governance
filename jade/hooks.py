"""Claude Code hook handlers. Enforcement and reminders only; capture happens at the adapter (ADR-04).

PreToolUse is the side-effect boundary: exit 2 blocks the tool call and the reason goes to the model.
Threat T17: the gate fails closed. When the classifier, the policy files, the ledger, git or the clock fail, a call that is high-risk (or,
when nothing can be classified, anything that is not clearly read-only) is blocked, never waved through. The whole PreToolUse handler runs
under an alarm (BUDGET_S) that is safely below the host's hook timeout, because a host that kills a hook proceeds with the tool call.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass

from . import paths, policy
from .util import at_least, now, ts

BUDGET_S = 6.0          # whole PreToolUse handler; the installed host timeout is 10 s (ops.HOOKS)
LEDGER_TIMEOUT_S = 1.5  # one wait for the ledger lock (open waits at most twice: busy timeout, then the schema step)
GIT_TIMEOUT_S = 2.0     # one git call made to resolve a push destination
BYPASS = re.compile(r"\bcurl\b.*openrouter\.ai/api/alpha/decisions")
REMIND_WINDOW_S = 6 * 3600
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
_MUTATING_TOOL = re.compile(
    r"^mcp__.+?__(?:set|write|edit|create|update|delete|remove|move|rename|exec|execute|run|shell|apply|merge|deploy|install|uninstall|kill|terminate|patch|publish"
    r"|push|commit|save|add|drop|truncate|restart|stop|start|send|post|put|accept|fork|link|discard|clean|interact|force|mcp-(?:exec|add|remove|config|create|activate)|code-mode)"
    r"|kubectl_|exec_in_pod|helm_chart|^RemoteTrigger$|__(?:app_type|Type|type|Shortcut|computer_batch|app_batch|teach_batch|app_key|key)$")


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
            if _PROTECTED_PATH.search(p) or _PROTECTED_PATH.search(os.path.realpath(os.path.expanduser(p))):
                return f"the write target {p[:120]!r} is a protected location"
        return None
    cmd = tin.get("command") if tool in policy.SHELL_TOOLS else next((tin[k] for k in ("command", "cmd", "script") if isinstance(tin.get(k), str)), None)
    if cmd is not None or tool in policy.SHELL_TOOLS:
        text = str(cmd or "").strip()
        if len(text) <= 4096 and _SAFE_CMD.fullmatch(text) and not _EXEC_FLAG.search(text):
            return None
        return "the command is not clearly read-only"
    if _MUTATING_TOOL.search(tool):
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


def _git(call: policy.shellnorm.GitCall, cwd: str, *args: str) -> str | None:
    argv = ["git"]
    for d in call.dirs:
        argv += ["-C", os.path.expanduser(d)]
    if call.git_dir:
        argv += ["--git-dir", os.path.expanduser(call.git_dir)]
    if call.work_tree:
        argv += ["--work-tree", os.path.expanduser(call.work_tree)]
    try:
        out = subprocess.run([*argv, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S, cwd=cwd or None)
    except subprocess.TimeoutExpired:
        raise  # an unresolved destination is never "not main": the gate treats it as a failure (fail closed)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


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
        return on_default()
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


def _on_default_branch(cwd: str, command: str) -> bool:
    """A `git push` whose destination is main/master or the current branch while that is main/master (invariant SI-08).

    The classifier has already removed git global options, escapes and quoting (policy.segments_cwd), so `git -C d push origin HEAD`
    and `git push origin HEAD:refs/heads/main` are seen as what they are, and each push is resolved in the directory it
    runs in (`cd repo && git push` follows the cd). A destination that cannot be resolved (`$BRANCH`) counts as the current branch.
    """
    for seg, dirs in policy.segments_cwd(command, cwd):
        if policy.is_read_only(seg):
            continue
        for call in policy.git_calls(seg):
            if call.sub == "push" and any(_push_hits_default(call, d) for d in dirs):
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
    mode = paths.gate_mode()
    if mode == "off":
        return HookResult()
    tool = event.get("tool_name", "")
    if not policy.is_gated(tool):
        return HookResult()
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
                              f"Rule text: policies/safety.yaml. Do not rephrase the command to get around the invariant; ask the human.")
    if not at_least(a.level, policy.permit_floor()):
        return HookResult()
    try:
        return _gate(mode, tool, a, event, permit)
    except Exception as e:  # noqa: BLE001 - the ledger is unreadable, locked or full: no permit can be checked, so nothing high-risk runs
        return _fail_closed(mode, f"this {tool} call is {a.level} risk ({a.rule_id}) and the permit ledger failed ({type(e).__name__}: {str(e)[:160]}), "
                                  "so no permit can be checked.")


def _verdict(mode: str, message: str) -> HookResult:
    if mode == "advisory":
        return HookResult(0, "[advisory, not blocked] " + message)
    return HookResult(2, message)


def _journal(entity: str, kind: str, a: policy.Assessment, event: dict) -> None:
    try:
        from . import ledger

        db = ledger.open(timeout=LEDGER_TIMEOUT_S)
        db.event(entity, kind, guard=f"{a.rule_id} {a.level} {','.join(a.blocked)}".strip(), actor="hook", detail=_detail(event, a))
        db.close()
    except Exception:  # noqa: BLE001, S110 - journaling must never change the gate verdict
        pass


def _si07_hold(db, a: policy.Assessment):
    """SI-07: an earlier decision for this exact command whose outcome is unknown, or an irreversible one still executing. Such a command
    is never started again until the outcome is reconciled; no permit is raised or consumed for it. Returns (decision_id, reason) or None."""
    now_s = ts()
    for r in db.all("SELECT decision_id, status, expires_at, doc_json FROM decisions WHERE plan_hash=? AND status IN ('OUTCOME_UNKNOWN','EXECUTING') "
                    "ORDER BY updated_at DESC", (a.plan_hash,)):
        if r.status == "OUTCOME_UNKNOWN" or r.expires_at <= now_s:
            return r.decision_id, "has an unknown outcome (an unknown outcome is never success)"
        try:
            irreversible = json.loads(r.doc_json)["risk"]["reversibility"] == "irreversible"
        except (ValueError, KeyError, TypeError):
            irreversible = True
        if irreversible:
            return r.decision_id, "is still executing and is irreversible"
    return None


def _gate(mode: str, tool: str, a: policy.Assessment, event: dict, permit) -> HookResult:
    from . import decide, ledger, permits

    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        db.event("gate", "gated", guard=f"{a.rule_id} {a.level}", actor="hook", detail=_detail(event, a))
        permits.sweep(db)
        hold = _si07_hold(db, a)
        if hold:
            did, reason = hold
            if mode != "advisory":
                db.event("gate", "safety_block", guard=f"{a.rule_id} {a.level} SI-07", actor="hook", detail=_detail(event, a, decision_id=did))
            return _verdict(mode, f"JADE safety invariant SI-07 blocks this {tool} call. An earlier run of this exact command (decision {did}) {reason}"
                                  f"{' and still executing' if 'executing' in reason else ''}. It is not retried automatically and no permit is raised or used for it. "
                                  f"Read the real state, then record it: jade decide outcome {did} --type observed --success true|false --method \"how you checked\". "
                                  "Do not rephrase the command to get around the invariant; ask the human.")
        p = permit or permits.find_usable(db, a.plan_hash)
        if p is not None and permits.consume(db, p.permit_id, a.plan_hash):
            row = db.one("SELECT decision_id, status FROM decisions WHERE decision_id=?", (p.decision_id,))
            if row and row.status == "WAITING_HUMAN":
                decide.step(db, row.decision_id, "human_accept", guard=f"permit {p.permit_id} approved from a terminal", actor="human")
                decide.step(db, row.decision_id, "authorize", permit_ok=True, guard=f"permit {p.permit_id} issued, unexpired, unused, plan hash equal")
                decide.step(db, row.decision_id, "execute", guard="PreToolUse saw the matching command; permit consumed", actor="hook", ttl_s=3600)
            return HookResult(0, f"JADE: permit {p.permit_id} consumed for this {tool} call.")
        if mode == "advisory":
            db.event("gate", "would_block", guard=f"{a.rule_id} {a.level}", actor="hook", detail=_detail(event, a))
            return HookResult(0, f"[advisory, not blocked] JADE: {a.level} risk ({a.rule_id}: {a.rationale}) would need a permit.")
        pending = db.one("SELECT permit_id FROM permits WHERE plan_hash=? AND state='PENDING' AND expires_at>?", (a.plan_hash, ts()))
        if pending is None:
            doc = decide.open_decision(db, f"Execute {tool}: {a.subject[:300]}", command=a.subject, tool=tool, cwd=event.get("cwd") or "",
                                       session=event.get("session_id"), route="ESCALATE", assessment=a)
            decide.step(db, doc["decision_id"], "route", guard="route=ESCALATE: permit required")
            decide.step(db, doc["decision_id"], "escalate", guard="no issued permit for this plan", ttl_s=permits.DEFAULT_TTL_S)
            p = permits.request(db, decide.canonical_action(a.rule_id, a.subject), a.plan_hash, risk=a.level, decision_id=doc["decision_id"],
                                scope=(event.get("cwd") or "*")[:200], environment=a.environment)
            with db.tx() as con:
                con.execute("UPDATE permits SET scope=? WHERE permit_id=?", (json.dumps({"tool": tool, "subject": a.subject[:4000], "cwd": event.get("cwd") or "",
                                                                                 "rule": a.rule_id, "rationale": a.rationale,
                                                                                 "permission_mode": event.get("permission_mode"), "agent_id": event.get("agent_id")}),
                                                                                p.permit_id))
            pid = p.permit_id
        else:
            pid = pending.permit_id
        return HookResult(2, f"JADE gate: {a.level} risk ({a.rule_id}: {a.rationale}). This {tool} call needs a human-approved permit.\n"
                             f"Permit {pid} is PENDING. Ask the human to run in their own terminal:  jade permit approve {pid}\n"
                             "Then retry the identical command (single use, 30 minutes). You cannot approve it yourself; "
                             "do not rephrase the command to avoid the gate. Continue with other work meanwhile.")
    finally:
        db.close()


def posttool(event: dict) -> HookResult:
    if event.get("tool_name") != "Bash":
        return HookResult()
    command = str((event.get("tool_input") or {}).get("command") or "")
    from . import decide, ledger

    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        if BYPASS.search(command) and "jade" not in command:
            db.event(event.get("session_id") or "session", "BYPASS", guard="raw Decisions API call outside the adapter", actor="hook")
        ph = policy.plan_hash("Bash", command, event.get("cwd") or "")
        row = db.one("SELECT decision_id FROM decisions WHERE plan_hash=? AND status='EXECUTING' ORDER BY updated_at DESC", (ph,))
        if row:
            resp = event.get("tool_response")
            failed = isinstance(resp, dict) and bool(resp.get("is_error") or resp.get("interrupted") or resp.get("error"))
            decide.record_outcome(db, row.decision_id, "observed", not failed, "tool call returned (exit status only; the goal is not verified)",
                                  attribution="indirect")
    finally:
        db.close()
    return HookResult()


def undisposed(db, session: str, only_unreminded: bool = True):
    """Abstain/reject/error calls in this session with no later accepted answer to the same question and no `jade ack`."""
    extra = "AND c.reminded=0" if only_unreminded else ""
    return db.all(
        "SELECT c.call_id, c.verdict, c.p, c.exit_code, c.question_id, c.error_kind, substr(COALESCE(c.question_text,''),1,90) AS q FROM jev_calls c "
        f"WHERE c.session_id=? AND c.state='DONE' AND c.exit_code IN (3,4,5,6) AND c.disposition IS NULL {extra} "  # noqa: S608 - constant fragments
        "AND c.ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?) AND c.task_family IS NOT 'evidence_quality' "
        "AND NOT EXISTS (SELECT 1 FROM jev_calls d WHERE d.session_id=c.session_id AND d.claim_hash=c.claim_hash AND d.exit_code=0 AND d.rowid>c.rowid) "
        "ORDER BY c.rowid", (session, f"-{REMIND_WINDOW_S} seconds"))


def stop(event: dict) -> HookResult:
    if paths.gate_mode() == "off" or event.get("stop_hook_active"):
        return HookResult()
    session = event.get("session_id")
    if not session:
        return HookResult()
    from . import ledger

    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
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


def session_start(event: dict) -> HookResult:
    from . import decide, ledger, permits

    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        decide.sweep(db)
        permits.sweep(db)
        out = []
        pend = permits.listing(db, ("PENDING",))
        if pend:
            out.append(f"JADE: {len(pend)} permit(s) awaiting human approval: " + ", ".join(p.permit_id for p in pend[:5]))
        waiting = db.one("SELECT COUNT(*) AS n FROM decisions WHERE status='WAITING_HUMAN'").n
        if waiting:
            out.append(f"JADE: {waiting} decision(s) in WAITING_HUMAN (`jade decide list --status WAITING_HUMAN`).")
        today = f"{now():%Y-%m-%d}"
        for r in db.all("SELECT doc_json FROM work_items WHERE status IN ('open','in_progress','waiting_human')"):
            w = json.loads(r.doc_json)
            trig = str(w.get("retry_or_revisit_trigger", ""))
            due = re.search(r"\d{4}-\d{2}-\d{2}", trig)
            if trig == "on_session_start" or (due and due.group(0) <= today):
                out.append(f"JADE work item {w['id']} ({w.get('priority', '')}): {w.get('title', '')}. Next: {w.get('next_action', '')}")
        return HookResult(0, "", "\n".join(out[:8]))
    finally:
        db.close()


HANDLERS = {"pretool": pretool, "posttool": posttool, "stop": stop, "session-start": session_start}


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
