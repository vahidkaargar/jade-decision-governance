"""`jade setup`: the human-only, idempotent first-run wizard (REL-2, docs/08 section 2).

It walks the steps a person otherwise types one by one: check the prerequisites, start in advisory mode, install the hooks, replace the Jev section of
CLAUDE.md, install the skill, write and load the daily maintenance job (macOS), then run `jade check` and say what is active. Every change goes through
the function that already does it (`ops.set_gate_mode`, `ops.hooks_install`, `ops.claude_md_apply`, `ops.launchd_plist`), so the same guards, backups and
atomic writes apply. Each step is shown first with the exact change and its undo, asks y/N, and is skipped when it is already done, so a second run
changes nothing.

Like `jade hooks install` it changes what governs every agent action, so it needs an interactive human terminal (`--yes` skips the questions, never the
terminal) and SI-09 blocks it in an agent's command. `--status` only reads and may be run by anyone.

The OpenRouter key is only looked for, never read: the Keychain probe asks for the item's attributes (no `-w`, no `-g`), the environment is tested for the
variable's presence, and neither value is printed or stored. Setup never sets the gate to enforce: that is a separate decision (docs/08 section 2.2)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import jade

from . import ledger, ops, paths, util

KEY_NAME = "OPENROUTER_API_KEY"
LABEL = "com.jade.maintain"          # the default of `jade launchd-plist`
MIN_PYTHON = (3, 13)                 # pyproject.toml requires-python
TOTAL = 7
FATAL = ("blocked", "failed", "check_failed")   # outcomes that make the run exit 1


class SetupError(Exception):
    """A step that was confirmed and then could not be carried out (its message says why)."""


class State(NamedTuple):
    kind: str          # done | todo | na (not applicable, not an error) | conflict (refused, needs the human)
    detail: str = ""


@dataclass
class Action:
    key: str
    step: int
    label: str
    state: Callable[[], State]
    change: Callable[[], str]
    undo: Callable[[], str]
    apply: Callable[[], str]
    needs: str | None = None   # key of an action that must be done (now or before) for this one to make sense


@dataclass
class Outcome:
    key: str
    step: int
    title: str
    outcome: str       # ok missing applied already_done declined skipped blocked failed check_ok check_failed
    detail: str = ""


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    rc: int = 0


# ---- small helpers -------------------------------------------------------------------------------------------------------------------
def _platform() -> str:
    return sys.platform


def _tilde(p) -> str:
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


def _clean(text, n: int = 200) -> str:
    return re.sub(r"[^\x20-\x7e]", "?", str(text))[:n]


def skill_source() -> Path:
    """The skill text shipped with this install: `skills/jade/SKILL.md` of a checkout, `jade/_skills/jade/SKILL.md` of a wheel."""
    return paths._data("skills") / "jade" / "SKILL.md"


def _skill_dir() -> Path:
    return Path.home() / ".agents" / "jade"


def _skill_link() -> Path:
    return Path.home() / ".claude" / "skills" / "jade"


def _claude_md_path() -> Path:
    return Path.home() / ".claude" / "CLAUDE.md"


def _plist_path() -> Path:
    return ops.launch_agents_dir() / f"{LABEL}.plist"


def _maintain_job() -> tuple[Path, str] | None:
    """(plist, label) of the first `jade maintain` LaunchAgent that exists, whatever its label."""
    jobs = ops.maintain_jobs()
    return (jobs[0][0], jobs[0][1]) if jobs else None


def _keychain_has_key() -> bool:
    """Whether the Keychain holds the OPENROUTER_API_KEY item. Without -w or -g `security` prints the item's attributes and never the secret, and
    the output is dropped here."""
    if _platform() != "darwin" or not shutil.which("security"):
        return False
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEY_NAME, "-a", os.environ.get("USER") or util.user()],
                           capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _launchctl_bootstrap(plist: Path, label: str) -> tuple[bool, str]:
    """`launchctl bootstrap gui/<uid> <plist>` as an argument list. Returns (ok, why)."""
    try:
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"launchctl could not be run ({type(e).__name__})"
    if r.returncode == 0:
        return True, ""
    err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else str(r.stderr or "")
    return False, f"launchctl bootstrap exited {r.returncode}: {_clean(err.strip())}"


# ---- the state of each action (read-only) --------------------------------------------------------------------------------------------
def _config_problem() -> str | None:
    """Why `config.json` cannot be edited safely, or None. `paths.config()` reads a broken file as {} and `ops.set_gate_mode` would then rewrite it with
    the one key, silently dropping the others: setup refuses instead of overwriting a file it could not read."""
    p = paths.config_dir() / "config.json"
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        return f"{_tilde(p)} cannot be read ({type(e).__name__})"
    try:
        cfg = json.loads(text)
    except ValueError:
        return f"{_tilde(p)} is not valid JSON"
    return None if isinstance(cfg, dict) else f"{_tilde(p)} is not a JSON object"


def _gate_mode_setting() -> str | None:
    cfg = paths.config()
    return cfg.get("gate_mode") if isinstance(cfg, dict) else None


def _gate_state() -> State:
    if problem := _config_problem():
        return State("conflict", f"{problem}; setup never rewrites it (fix or move it yourself, then run `jade setup` again)")
    mode = _gate_mode_setting()
    if mode in ("advisory", "enforce"):
        return State("done", f"gate mode {mode} is configured")
    return State("todo", f"gate mode is {mode}" if mode else "gate mode is not configured (an unset gate runs as enforce)")


def _hooks_state() -> State:
    missing = [e for e, ok in ops.hooks_status().items() if not ok]
    if missing:
        return State("todo", f"not installed: {', '.join(missing)}")
    gaps = ops.pretool_matcher_gaps()
    if gaps:
        return State("todo", f"the PreToolUse matcher is out of date and does not cover {', '.join(gaps)}")
    return State("done", "four JADE hooks installed")


def _claude_md_state() -> State:
    p = _claude_md_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return State("na", f"{_tilde(p)} does not exist; `jade claude-md show` prints the section to add yourself")
    except (OSError, UnicodeDecodeError) as e:
        return State("conflict", f"{_tilde(p)} cannot be read ({type(e).__name__})")
    try:
        new = ops.claude_md_render(text, str(p))
    except ops.SectionNotFound:
        return State("na", f"{_tilde(p)} has no Jev section to replace; `jade claude-md show` prints the section to add yourself")
    except ValueError as e:
        return State("conflict", _clean(e, 300))
    if new.rstrip("\n") == text.rstrip("\n"):
        return State("done", "the Jev section is the shipped text")
    return State("todo", "the Jev section differs from the shipped text")


def _skill_file_state() -> State:
    src, dest = skill_source(), _skill_dir() / "SKILL.md"
    try:
        want = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return State("conflict", f"the skill file is missing from this install (expected {src}); reinstall: `jade upgrade`, or `uv tool install --reinstall` from the checkout")
    if _skill_dir().exists() and not _skill_dir().is_dir():
        return State("conflict", f"{_tilde(_skill_dir())} exists and is not a directory; setup never replaces it")
    if not os.path.lexists(dest):
        return State("todo", f"{_tilde(dest)} does not exist")
    try:
        have = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return State("conflict", f"{_tilde(dest)} exists and cannot be read as the skill (a dangling link, a directory or not text); setup never replaces it")
    if have == want:
        return State("done", f"{_tilde(dest)} is the packaged skill")
    if dest.is_symlink():
        return State("conflict", f"{_tilde(dest)} is a link and differs from the packaged skill; setup never replaces a link (compare with `diff {dest} {src}` and copy it yourself)")
    return State("todo", f"{_tilde(dest)} differs from the packaged skill (an older release, or your edit)")


def _skill_link_state() -> State:
    link, target = _skill_link(), _skill_dir()
    if not os.path.lexists(link):
        return State("todo", f"{_tilde(link)} does not exist")
    if link.is_symlink() and os.path.realpath(link) == os.path.realpath(target):
        return State("done", f"{_tilde(link)} links to {_tilde(target)}")
    what = f"a link to {_tilde(os.path.realpath(link))}" if link.is_symlink() else "a file or directory"
    return State("conflict", f"{_tilde(link)} exists and is {what}, not a link to {_tilde(target)}; setup never replaces it (remove or move it yourself)")


_NOT_MACOS = "launchd is macOS only; run `jade maintain` daily from your own scheduler"


def _plist_state() -> State:
    if _platform() != "darwin":
        return State("na", _NOT_MACOS)
    if job := _maintain_job():
        return State("done", f"{job[1]} is written in {_tilde(job[0].parent)}")
    p = _plist_path()
    if os.path.lexists(p):
        return State("conflict", f"{_tilde(p)} exists but is not a `jade maintain` job; setup never overwrites it")
    return State("todo", f"{_tilde(p)} does not exist")


def _load_state() -> State:
    if _platform() != "darwin":
        return State("na", _NOT_MACOS)
    job = _maintain_job()
    if job and ops._launchctl_loaded(job[1]):
        return State("done", f"{job[1]} is loaded in launchd")
    return State("todo", f"{job[1]} is not loaded in launchd" if job else "no plist to load yet")


# ---- the changes (each reuses the function that already does it) ---------------------------------------------------------------------
def _hooks_change() -> str:
    lines = [f"add four JADE hook entries to {_tilde(ops.settings_path())}, keeping every other setting and hook:"]
    lines += [f"  + {event}{f' [{m}]' if m and len(m) < 40 else ' [file edits, MCP tools, shell]' if m and event == 'PreToolUse' else ''}: {ops.hook_command(sub)}"
              for event, (m, sub, _t) in ops.HOOKS.items()]
    lines.append("a backup settings.json.bak-jade-<time> is written next to the file first; the write is atomic")
    return "\n".join(lines)


def _make_actions(stdin_isatty: bool | None) -> list[Action]:
    uid = os.getuid()

    def gate_apply() -> str:
        ops.set_gate_mode("advisory", stdin_isatty)
        return "gate mode advisory"

    def hooks_apply() -> str:
        backup = ops.hooks_install(stdin_isatty)
        return f"hooks installed in {_tilde(ops.settings_path())} ({f'backup {_tilde(backup)}' if backup else 'no earlier file, no backup'})"

    def md_apply() -> str:
        return f"CLAUDE.md Jev section replaced (backup {_tilde(ops.claude_md_apply(_claude_md_path(), stdin_isatty))})"

    def skill_apply() -> str:
        src, dest = skill_source(), _skill_dir() / "SKILL.md"
        try:
            text = src.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise SetupError(f"the skill file {src} cannot be read ({type(e).__name__})") from None
        backup = ops.backup_file(dest) if os.path.lexists(dest) else None
        ops.atomic_write(dest, text, default_mode=0o644)
        return f"skill written to {_tilde(dest)}" + (f" (backup {_tilde(backup)})" if backup else "")

    def link_apply() -> str:
        link = _skill_link()
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(_skill_dir(), link)
        return f"{_tilde(link)} -> {_tilde(_skill_dir())}"

    def plist_apply() -> str:
        p = _plist_path()
        ops.atomic_write(p, ops.launchd_plist(LABEL), default_mode=0o644)
        return f"plist written to {_tilde(p)}"

    def load_apply() -> str:
        job = _maintain_job()
        if job is None:
            raise SetupError("there is no `jade maintain` plist to load")
        ok, why = _launchctl_bootstrap(job[0], job[1])
        if not ok:
            raise SetupError(why)
        return f"{job[1]} loaded in launchd"

    def load_change() -> str:
        job = _maintain_job()
        return f"run: launchctl bootstrap gui/{uid} {_tilde(job[0] if job else _plist_path())}"

    def load_undo() -> str:
        job = _maintain_job()
        return f"launchctl bootout gui/{uid}/{job[1] if job else LABEL}"

    return [
        Action("gate", 2, "gate mode", _gate_state,
               lambda: f'set "gate_mode": "advisory" in {_tilde(paths.config_dir() / "config.json")} (other keys stay). Advisory records would_block (or safety_block) events and never blocks',
               lambda: "jade gate enforce (or jade gate off), run by you in a terminal", gate_apply),
        Action("hooks", 3, "Claude Code hooks", _hooks_state, _hooks_change,
               lambda: "jade hooks uninstall (removes only JADE's own entries, keeps a backup)", hooks_apply),
        Action("claude-md", 4, "CLAUDE.md Jev section", _claude_md_state,
               lambda: f"replace only the '{ops.JEV_HEADING}' section of {_tilde(_claude_md_path())} with the shipped text (jade claude-md show); "
                       "a backup CLAUDE.md.bak-jade-<time> is written next to it first",
               lambda: "copy the CLAUDE.md.bak-jade-<time> backup back over CLAUDE.md", md_apply),
        Action("skill-file", 5, "skill file", _skill_file_state,
               lambda: f"copy the packaged skill ({_tilde(skill_source())}) to {_tilde(_skill_dir() / 'SKILL.md')} (creates the directory; a different existing file is first copied to SKILL.md.bak-jade-<time> next to it)",
               lambda: f"a new install: delete {_tilde(_skill_dir() / 'SKILL.md')} and the directory if it is empty; a replaced file: copy the SKILL.md.bak-jade-<time> backup back", skill_apply),
        Action("skill-link", 5, "skill link", _skill_link_state,
               lambda: f"create the symlink {_tilde(_skill_link())} -> {_tilde(_skill_dir())} (an existing different path is never replaced)",
               lambda: f"rm {_tilde(_skill_link())} (removes the link, not the skill)", link_apply, needs="skill-file"),
        Action("launchd-plist", 6, "maintenance job plist", _plist_state,
               lambda: f"write {_tilde(_plist_path())} from `jade launchd-plist --label {LABEL}`: runs `jade maintain` daily at 09:15 (expires permits, purges old state, runs the canary and the miner)",
               lambda: f"delete {_tilde(_plist_path())}", plist_apply),
        Action("launchd-load", 6, "maintenance job loaded", _load_state, load_change, load_undo, load_apply, needs="launchd-plist"),
    ]


def state_of(key: str, stdin_isatty: bool | None = None) -> State:
    """The state of one action by key (read-only)."""
    return next(a for a in _make_actions(stdin_isatty) if a.key == key).state()


# ---- prerequisites -------------------------------------------------------------------------------------------------------------------
def prerequisites() -> tuple[list[tuple[str, str, str]], bool]:
    """(rows of tag, name, detail with tag ok | missing | note; whether the OpenRouter key is present). Reads nothing secret."""
    rows = []
    ok = sys.version_info >= MIN_PYTHON
    rows.append(("ok" if ok else "missing", "python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
                 + ("" if ok else f" (needs {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer)")))
    uv = shutil.which("uv")
    rows.append(("ok", "uv", uv) if uv else ("note", "uv", "not on PATH (only `jade upgrade` needs it; https://docs.astral.sh/uv/)"))
    if _keychain_has_key():
        rows.append(("ok", KEY_NAME, "found in the macOS Keychain (value not read)"))
        key = True
    elif os.environ.get(KEY_NAME, "").strip():
        rows.append(("ok", KEY_NAME, "found in the environment (value not read)"))
        key = True
    else:
        rows.append(("missing", KEY_NAME, "not found in the macOS Keychain or the environment"))
        key = False
    return rows, key


_KEY_HELP = (f"Jev calls need an OpenRouter key; the gate and the hooks work without one. Add it yourself, setup never asks for the value:\n"
             f"  macOS Keychain: security add-generic-password -s {KEY_NAME} -a \"$USER\" -w     (prompts for the value; it is not shown or stored in your history)\n"
             f"  or the environment: export {KEY_NAME}=...   in the shell profile that starts Claude Code")


# ---- the journal (ledger meta) -------------------------------------------------------------------------------------------------------
class _Journal:
    """One `setup.<step>` meta record per step (latest outcome, time, version) and `setup.last_run`. A ledger that cannot be written is reported once
    and never stops the setup: the changes themselves are what matters, and `jade check` reports a broken ledger."""

    def __init__(self, say: Callable[[str], None]):
        self.say, self.db, self.failed = say, None, False

    def _ledger(self):
        if self.db is None and not self.failed:
            try:
                self.db = ledger.open()
            except Exception as e:  # noqa: BLE001 - any ledger problem is reported, not raised
                self._fail(e)
        return self.db

    def _fail(self, e: Exception) -> None:
        if not self.failed:
            self.say(f"jade: could not record the setup events in the ledger ({type(e).__name__}); the setup itself is not affected")
        self.failed = True

    def record(self, key: str, outcome: str) -> None:
        db = self._ledger()
        if db is None:
            return
        try:
            db.meta_set(f"setup.{key}", json.dumps({"outcome": outcome, "ts": util.ts(), "version": jade.__version__}))
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def last_run(self, outcomes: dict[str, str]) -> None:
        db = self._ledger()
        if db is None:
            return
        try:
            db.meta_set("setup.last_run", json.dumps({"ts": util.ts(), "version": jade.__version__, "outcomes": outcomes}))
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def close(self) -> None:
        if self.db is not None:
            try:
                self.db.close()
            except Exception:  # noqa: BLE001, S110
                pass


# ---- the wizard ----------------------------------------------------------------------------------------------------------------------
def _block(say: Callable[[str], None], tag: str, text: str) -> None:
    pad = " " * (len(tag) + 3)
    first, *rest = text.split("\n")
    say(f"  {tag}: {first}")
    for line in rest:
        say(f"{pad}{line}")


def _confirm(input_fn: Callable[[str], str], say: Callable[[str], None], yes: bool) -> bool:
    if yes:
        say("  --yes: applying without asking")
        return True
    try:
        answer = input_fn("  Apply this change? [y/N] ")
    except EOFError:
        say("")
        return False
    return answer.strip().lower() in ("y", "yes")


def run(*, yes: bool = False, stdin_isatty: bool | None = None, input_fn: Callable[[str], str] = input, say: Callable[[str], None] = print) -> Report:
    """Walk the steps. Raises ops.HumanOnly before anything is read or written when this is not an interactive human terminal."""
    why = util.human_tty_refusal(stdin_isatty)
    if why:
        raise ops.HumanOnly(f"jade setup changes what governs every agent action; a human must run it in an interactive terminal ({why})")
    rep, journal = Report(), _Journal(say)

    def done(key: str, step: int, title: str, outcome: str, detail: str = "") -> None:
        rep.outcomes.append(Outcome(key, step, title, outcome, detail))
        journal.record(key, outcome)
        if outcome in FATAL:
            rep.rc = 1

    say(f"JADE setup: {TOTAL} steps. Each one shows the exact change and how to undo it, and asks before it changes anything.")
    say("Steps that are already done are skipped, so running it again is safe.\n")
    try:
        say(f"[1/{TOTAL}] Prerequisites")
        rows, key_ok = prerequisites()
        for tag, name, detail in rows:
            say(f"  {tag:<7} {name}: {detail}")
        if not key_ok:
            say("  " + _KEY_HELP.replace("\n", "\n  "))
        done("prereq", 1, "prerequisites", "ok" if key_ok else "missing")

        by_key: dict[str, str] = {}
        for act in _make_actions(stdin_isatty):
            say(f"\n[{act.step}/{TOTAL}] {act.label[0].upper() + act.label[1:]}")
            st = act.state()
            outcome, detail = "", ""
            if st.kind == "done":
                say(f"  already done: {st.detail}")
                outcome = "already_done"
            elif st.kind == "na":
                say(f"  skipped: {st.detail}")
                outcome = "skipped"
            elif st.kind == "conflict":
                say(f"  refused: {st.detail}")
                outcome = "blocked"
            elif act.needs and by_key.get(act.needs) not in ("applied", "already_done"):
                need = next(a.label for a in _make_actions(stdin_isatty) if a.key == act.needs)
                say(f"  skipped: needs the {need} first")
                outcome = "skipped"
            else:
                say(f"  needed: {st.detail}")
                _block(say, "change", act.change())
                _block(say, "undo", act.undo())
                if not _confirm(input_fn, say, yes):
                    say("  declined: nothing changed")
                    outcome = "declined"
                else:
                    try:
                        detail = act.apply()
                        say(f"  done: {detail}")
                        outcome = "applied"
                    except ops.HumanOnly:
                        raise
                    except (SetupError, ops.WiringError, ValueError, OSError) as e:
                        detail = _clean(e, 300)
                        say(f"  failed: {detail}")
                        outcome = "failed"
            by_key[act.key] = outcome
            done(act.key, act.step, act.label, outcome, detail)

        say(f"\n[{TOTAL}/{TOTAL}] Final check (jade check, offline; run `jade check` yourself for the live provider canary)")
        check_rc = ops.print_check(ops.check(live=False))
        done("check", TOTAL, "final check", "check_ok" if check_rc == 0 else "check_failed")

        _summary(say, key_ok)
        journal.last_run({o.key: o.outcome for o in rep.outcomes})
    finally:
        journal.close()
    return rep


def _summary(say: Callable[[str], None], key_ok: bool) -> None:
    say("\nSummary")
    active = [f"{KEY_NAME}: found (value not read)"] if key_ok else []
    inactive = [] if key_ok else [f"{KEY_NAME}: Jev calls fail until you add it (step 1)"]
    for act in _make_actions(None):
        st = act.state()
        (active if st.kind == "done" else inactive).append(f"{act.label}: {st.detail}" + (" [refused, needs you]" if st.kind == "conflict" else ""))
    say("  Active:")
    say("\n".join(f"    + {x}" for x in active) if active else "    nothing")
    say("  Not active:")
    say("\n".join(f"    - {x}" for x in inactive) if inactive else "    nothing")
    mode = _gate_mode_setting()
    if mode == "advisory":
        say("\nAdvisory is the starting state: the gate records what it would have blocked (events.would_block and events.safety_block in `jade ledger stats`) and blocks nothing.")
    elif mode == "enforce":
        say("\nGate mode is enforce, as you had it configured; setup does not change it.")
    else:
        say("\nGate mode is not configured, so the gate runs as enforce; setup did not set advisory.")
    say("Enforce mode is NOT set by setup. Soak for about a week, read `jade ledger stats` and `jade decide list`, then run `jade gate enforce` yourself. "
        "The decision and the criteria are in docs/08-deployment-runbook.md section 2.2 (docs/01-adrs.md, ADR on the gate).")
    say("Hooks are read at session start: open a new Claude Code session. Undo everything: docs/08 section 3. `jade setup --status` shows this list again.")


def status(say: Callable[[str], None] = print) -> int:
    """What is done and what is missing, read-only. Exit 1 when something is missing or refused."""
    rows, key_ok = prerequisites()
    for tag, name, detail in rows:
        say(f"{'done' if tag == 'ok' else tag:<7}  {name}: {detail}")
    bad = False
    for act in _make_actions(None):
        st = act.state()
        tag = {"done": "done   ", "todo": "missing", "na": "n/a    ", "conflict": "REFUSED"}[st.kind]
        bad = bad or st.kind in ("todo", "conflict")
        say(f"{tag}  {act.label}: {st.detail}")
    if not key_ok:
        say(_KEY_HELP)
    return 1 if bad else 0


def cli_setup(a) -> int:
    if a.status:
        return status()
    try:
        return run(yes=a.yes).rc
    except ops.HumanOnly as e:
        print(f"jade: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\njade: interrupted; steps already applied stay, and running `jade setup` again continues where this one stopped", file=sys.stderr)
        return 1
