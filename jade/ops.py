"""Operations: champion/challenger pins, drift detection, retention, wiring checks, harness integration."""

from __future__ import annotations

import contextlib
import json
import os
import plistlib
import re
import shlex
import shutil
import stat
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

from . import ledger, paths, policy
from .util import is_human_tty, now, ts


def pretool_matcher() -> str:
    """Every built-in tool the gate assesses (policy.GATED_BUILTIN) and every MCP tool. Agent/Task are not listed: they need no gate,
    and the tool calls a subagent makes reach the same hook."""
    return "|".join((*policy.GATED_BUILTIN, "mcp__.*"))


HOOKS = {"PreToolUse": (pretool_matcher(), "pretool", 10), "PostToolUse": ("Bash", "posttool", 10),
         "Stop": ("", "stop", 10), "SessionStart": ("", "session-start", 10)}
JEV_HEADING = "## Jev: verify outputs and decide (global, all sessions)"


class HumanOnly(PermissionError):
    """A governance-wiring change attempted outside an interactive human terminal (docs/07 section 3)."""


def _dir(path) -> Path:
    return Path(path) if path else paths.home()


# ---- champion / challenger -----------------------------------------------------------------------
def _pins(path) -> dict:
    try:
        return json.loads((_dir(path) / "pins.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_pins(path, pins: dict) -> None:
    d = _dir(path)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "pins.json.tmp"
    tmp.write_text(json.dumps(pins, indent=1, sort_keys=True))
    tmp.replace(d / "pins.json")


def frozen(path=None) -> bool:
    return (_dir(path) / "FREEZE").exists()


def pin_champion(path, question_id: str, version: int) -> None:
    pins = _pins(path)
    pins.setdefault(question_id, {})["champion"] = int(version)
    _save_pins(path, pins)


CANARY_FRACTION = 0.2   # the shipped value of policies/promotion.yaml canary.traffic_fraction; a test pins the two together


def promotion_policy() -> dict:
    """policies/promotion.yaml (protected). Read at use, so an edit of the file changes what the gate demands."""
    import yaml

    return yaml.safe_load((paths.policy_dir() / "promotion.yaml").read_text()) or {}


def canary_fraction() -> float:
    try:
        return float(promotion_policy()["canary"]["traffic_fraction"])
    except (OSError, KeyError, TypeError, ValueError):
        return CANARY_FRACTION


def set_challenger(path, question_id: str, version: int, fraction: float | None = None) -> None:
    """Serve a challenger to a share of sessions. The share is capped at the policy's canary fraction: a challenger never gets more traffic than
    the canary stage allows (policies/promotion.yaml canary.traffic_fraction)."""
    fraction = canary_fraction() if fraction is None else fraction
    if not 0 < fraction <= canary_fraction():
        raise ValueError(f"fraction must be in (0, {canary_fraction()}] (policies/promotion.yaml canary.traffic_fraction)")
    pins = _pins(path)
    pins.setdefault(question_id, {}).update(challenger=int(version), challenger_fraction=fraction)
    _save_pins(path, pins)


def rollback(path, question_id: str) -> str:
    """Undo the most recent rollout step of one question. A challenger in canary is cleared (the champion keeps serving). With no
    challenger, a promoted version goes back to the champion it replaced (`previous_champion`; None means the registry default).
    Returns what was done: 'challenger_cleared', 'champion_restored' or 'nothing'."""
    pins = _pins(path)
    p = pins.setdefault(question_id, {})
    if p.get("challenger"):
        p["challenger"] = None
        done = "challenger_cleared"
    elif "previous_champion" in p:
        p["champion"] = p.pop("previous_champion")
        done = "champion_restored"
    else:
        p["challenger"] = None
        done = "nothing"
    _save_pins(path, pins)
    return done


def pins_of(path, question_id: str) -> dict:
    return dict(_pins(path).get(question_id, {}))


def promote_challenger(path, question_id: str) -> int | None:
    pins = _pins(path)
    p = pins.setdefault(question_id, {})
    if p.get("challenger"):
        p["previous_champion"], p["champion"], p["challenger"] = p.get("champion"), p["challenger"], None
        _save_pins(path, pins)
    return p.get("champion")


def in_canary(key: str, fraction: float) -> bool:
    """Deterministic assignment: a session always lands on the same side for a given fraction."""
    from .util import sha256

    return int(sha256(key)[:8], 16) % 10000 < fraction * 10000


def active_version(path, question_id: str, key: str | None = None) -> int | None:
    """Challenger for its canary share of sessions while one is set and the kill switch is absent; otherwise the
    champion (None = registry default). Without a key (administrative view) the challenger is reported."""
    p = _pins(path).get(question_id, {})
    if p.get("challenger") and not frozen(path):
        if key is None or in_canary(f"{question_id}:{key}", p.get("challenger_fraction", canary_fraction())):
            return p["challenger"]
    return p.get("champion")


# ---- drift ---------------------------------------------------------------------------------------
def check_drift(path, resolved_model: str, expected: list[str]):
    """None when the resolved model is expected. Otherwise a MODEL_DRIFT finding, recorded once per new string.
    Drift is informational (decision Q6): the new snapshot is a new calibration group at default thresholds."""
    if resolved_model in expected:
        return None
    db = ledger.open(_dir(path))
    try:
        known = json.loads(db.meta_get("known_models", "[]"))
        first = resolved_model not in known
        if first:
            db.meta_set("known_models", json.dumps(known + [resolved_model]))
            db.event("model", "MODEL_DRIFT", prev=",".join(expected), next=resolved_model, guard="resolved model string changed")
            wid = f"RT-{now():%Y%m%d%H%M%S}"
            item = {"id": wid, "title": f"MODEL_DRIFT: provider now resolves to {resolved_model}", "type": "risk", "priority": "P2",
                    "status": "open", "owner": "human", "next_action": "Informational. New calibration group started at default thresholds; "
                    "add the id to accepted_resolved in the registry when reviewed.", "retry_or_revisit_trigger": "on_session_start", "created_at": ts()}
            db.con.execute("INSERT OR REPLACE INTO work_items(id, status, doc_json, updated_at) VALUES(?,?,?,?)", (wid, "open", json.dumps(item), ts()))
        return SimpleNamespace(kind="MODEL_DRIFT", resolved_model=resolved_model, expected=list(expected), first_seen=first)
    finally:
        db.close()


def expected_models(db: ledger.Ledger | None = None) -> list[str]:
    from . import questions

    out = {m for e in questions.entries() for m in e["model_version"]["accepted_resolved"]}
    return sorted(out)


# ---- retention -----------------------------------------------------------------------------------
PURGED = "[purged by retention]"
OPEN_PERMIT = ("PENDING", "ISSUED")


def purge(db: ledger.Ledger, older_than_days: int = 90) -> int:
    """Remove stored project text older than the retention window and keep every metric (decision Q7): the state and question text of Jev
    calls, the objective and command text of decisions, the command a permit carried (never one still open: PENDING or ISSUED and not yet expired) and the detail of
    events. Statuses, hashes, risk levels, timings, costs and probabilities stay. Returns the number of rows cleared."""
    cutoff = ts(now() - timedelta(days=older_than_days))
    n = 0
    with db.tx() as con:
        n += con.execute("UPDATE jev_calls SET state_text=NULL, question_text=NULL WHERE ts<? AND (state_text IS NOT NULL OR question_text IS NOT NULL)",
                         (cutoff,)).rowcount
        for r in con.execute("SELECT decision_id, doc_json FROM decisions WHERE updated_at<? AND json_extract(doc_json,'$.objective') IS NOT ?",
                             (cutoff, PURGED)).fetchall():
            doc = json.loads(r["doc_json"])
            doc["objective"] = PURGED
            doc["candidate_actions"] = [{**act, "description": PURGED} if isinstance(act, dict) else act for act in doc.get("candidate_actions", [])]
            doc["constraints"], doc["assumptions"], doc["evidence_refs"] = [], [], []
            con.execute("UPDATE decisions SET doc_json=? WHERE decision_id=?", (json.dumps(doc, ensure_ascii=False), r["decision_id"]))
            n += 1
        for r in con.execute("SELECT permit_id, scope FROM permits WHERE issued_at<? AND (state NOT IN ('PENDING','ISSUED') OR expires_at<=?) AND scope LIKE '{%'",
                             (cutoff, ts())).fetchall():
            try:
                ctx = json.loads(r["scope"])
            except ValueError:
                continue
            if isinstance(ctx, dict) and "subject" in ctx and ctx["subject"] != PURGED:
                keep = {k: v for k, v in ctx.items() if k in ("tool", "length", "rule", "bound", "session_id", "agent_id", "permission_mode", "hash_cwd")}
                con.execute("UPDATE permits SET scope=? WHERE permit_id=?", (json.dumps({**keep, "subject": PURGED, "purged": True}), r["permit_id"]))
                n += 1
        n += con.execute("UPDATE events SET detail_json=NULL WHERE ts<? AND detail_json IS NOT NULL AND kind<>'purge'", (cutoff,)).rowcount
        if n:
            db.event("ledger", "purge", guard=f"older than {older_than_days} d", detail={"rows": n}, con=con)
    if n:
        db.scrub()   # the cleared text must not stay readable in the file's free pages or the write-ahead log
    return n


# ---- harness wiring (human only) -------------------------------------------------------------------
class WiringError(ValueError):
    """A wiring file could not be read or understood; nothing was written."""


def settings_path() -> Path:
    return Path(os.environ.get("JADE_CLAUDE_SETTINGS") or Path.home() / ".claude" / "settings.json")


def jade_bin() -> str:
    return os.environ.get("JADE_BIN") or shutil.which("jade") or str(Path.home() / ".local" / "bin" / "jade")


def backup_file(path: Path) -> Path:
    """Copy `path` (symlinks followed) next to the real file as `<name>.bak-jade-<timestamp>`. A backup that already exists is never overwritten: a
    numeric suffix is added, so a second run in the same second keeps the first run's copy, which is the original."""
    target = Path(os.path.realpath(path))
    data = target.read_bytes()
    mode = stat.S_IMODE(target.stat().st_mode)
    stamp = f"{now():%Y%m%d%H%M%S}"
    for n in range(1000):
        dest = target.with_name(f"{target.name}.bak-jade-{stamp}" + (f"-{n}" if n else ""))
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.chmod(dest, mode)
        return dest
    raise WiringError(f"could not find a free backup name for {target}")


def atomic_write(path: Path, text: str, default_mode: int = 0o600) -> None:
    """Write `text` to the file `path` names without ever leaving it half written: a temp file in the same directory, flushed, given the old
    file's permission bits (default_mode for a new file), then renamed over the target. A symlink is followed, so the link stays a link and its
    target is the file that changes."""
    target = Path(os.path.realpath(path))
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = default_mode
        target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.jade-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):   # make the rename itself durable; not every filesystem allows it
        dfd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


_HOOK_CMD = re.compile(r"^(?P<bin>.*?)\s+hook\s+(?P<sub>pretool|posttool|stop|session-start)\s*$", re.S)


class HookCommand(NamedTuple):
    binary: str
    sub: str
    shell_safe: bool   # the command line is exactly `<one word> hook <sub>`: a shell runs the same program this parse names


def parse_hook_command(command: str) -> HookCommand | None:
    """The JADE hook a settings command runs, or None for any other command. Structural: the command ends in `hook <sub>` and what stands before
    it is a program called `jade` (or the name of the configured JADE_BIN). `echo jade hook pretool` and `echo see my-jade hook notes` are not JADE's."""
    m = _HOOK_CMD.match(command.strip()) if isinstance(command, str) else None
    if not m:
        return None
    raw = m["bin"].strip()
    try:
        words = shlex.split(raw)
    except ValueError:
        words = []
    binary, safe = (words[0], True) if len(words) == 1 else (raw, False)   # a path with a space written unquoted by an older install
    if os.path.basename(binary) not in {"jade", os.path.basename(jade_bin())}:
        return None
    return HookCommand(binary, m["sub"], safe)


def hook_command(sub: str) -> str:
    return f"{shlex.quote(jade_bin())} hook {sub}"


def _load_settings(p: Path, *, required: bool = False) -> dict:
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        if required:
            raise WiringError(f"{p} does not exist") from None
        return {}
    except OSError as e:
        raise WiringError(f"cannot read {p}: {e}") from e
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as e:
        raise WiringError(f"{p} is not valid JSON ({e}); nothing was changed") from e
    if not isinstance(data, dict):
        raise WiringError(f"{p} does not hold a JSON object; nothing was changed")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict) or any(not isinstance(g, list) or any(not isinstance(x, dict) for x in g) for g in hooks.values()):
        raise WiringError(f"{p}: `hooks` is not a map of event to a list of groups; nothing was changed")
    for groups in hooks.values():
        if any(not isinstance(x.get("hooks", []), list) or any(not isinstance(h, dict) for h in x.get("hooks", [])) for x in groups):
            raise WiringError(f"{p}: a hook group has no valid `hooks` list; nothing was changed")
    return data


def _jade_hooks(data: dict):
    """(event, group, hook, parsed) for every JADE hook entry of a settings object."""
    for event, groups in (data.get("hooks") or {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                parsed = parse_hook_command(h.get("command", ""))
                if parsed:
                    yield event, g, h, parsed


def _read_quiet(p: Path) -> dict:
    try:
        return _load_settings(p)
    except WiringError:
        return {}


def hooks_status() -> dict[str, bool]:
    data = _read_quiet(settings_path())
    found = {(event, parsed.sub) for event, _g, _h, parsed in _jade_hooks(data)}
    return {event: (event, sub) in found for event, (_m, sub, _t) in HOOKS.items()}


def pretool_matcher_gaps() -> list[str] | None:
    """Gated tools the installed PreToolUse matcher does not cover (an install made before the matcher grew). None when the hook is not installed."""
    data = _read_quiet(settings_path())
    matchers = [g.get("matcher", "") for event, g, _h, p in _jade_hooks(data) if event == "PreToolUse" and p.sub == "pretool"]
    if not matchers:
        return None
    gaps = []
    for tool in (*policy.GATED_BUILTIN, "mcp__server__tool"):
        try:
            if not any(re.fullmatch(m, tool) if m else True for m in matchers):
                gaps.append(tool)
        except re.error:
            return [tool]
    return gaps


def _require_human(what: str, stdin_isatty: bool | None = None) -> None:
    if not is_human_tty(stdin_isatty):
        raise HumanOnly(f"{what} changes agent governance wiring; a human must run it in a terminal (docs/07 section 3)")


def _strip_jade_hooks(data: dict) -> int:
    """Remove JADE's own hook entries (and the groups and events that become empty). Every other hook is left as it is."""
    removed = 0
    hooks = data.get("hooks") or {}
    for event in list(hooks):
        kept_groups = []
        for g in hooks[event]:
            keep = [h for h in g.get("hooks", []) if not parse_hook_command(h.get("command", ""))]
            removed += len(g.get("hooks", [])) - len(keep)
            if keep or "hooks" not in g:
                kept_groups.append({**g, "hooks": keep} if "hooks" in g else g)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    return removed


def hooks_install(stdin_isatty: bool | None = None) -> Path | None:
    """Add the four JADE hooks to the Claude settings file, keeping every other setting and hook. Returns the backup path (None when there was no file)."""
    _require_human("jade hooks install", stdin_isatty)
    p = settings_path()
    data = _load_settings(p)
    backup = backup_file(p) if p.exists() else None
    _strip_jade_hooks(data)
    hooks = data.setdefault("hooks", {})
    for event, (matcher, sub, timeout) in HOOKS.items():
        entry = {"hooks": [{"type": "command", "command": hook_command(sub), "timeout": timeout}]}
        if matcher:
            entry["matcher"] = matcher
        hooks.setdefault(event, []).append(entry)
    atomic_write(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return backup


def hooks_uninstall(stdin_isatty: bool | None = None) -> int:
    """Remove only JADE's hook entries. A missing settings file is nothing to remove (0). Returns the number of entries removed."""
    _require_human("jade hooks uninstall", stdin_isatty)
    p = settings_path()
    if not p.exists():
        return 0
    data = _load_settings(p)
    removed = _strip_jade_hooks(data)
    if removed:
        backup_file(p)
        atomic_write(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return removed


def set_gate_mode(mode: str, stdin_isatty: bool | None = None) -> None:
    _require_human("jade gate", stdin_isatty)
    cfg = paths.config()
    cfg["gate_mode"] = mode
    atomic_write(paths.config_dir() / "config.json", json.dumps(cfg, indent=1) + "\n")


def claude_md_section() -> str:
    return (paths.docs_dir() / "claude-md-jev-section.md").read_text()


_ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def markdown_headings(text: str):
    """(line_index, level, title) of every ATX heading that is not inside a fenced code block."""
    fence = None
    for i, line in enumerate(text.splitlines()):
        m = _FENCE.match(line)
        if fence:
            if m and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not line.strip().strip(fence[0]):
                fence = None
            continue
        if m:
            fence = m[1]
            continue
        h = _ATX.match(line)
        if h:
            yield i, len(h[1]), h[2]


def claude_md_apply(target: Path | None = None, stdin_isatty: bool | None = None) -> Path:
    """Replace the Jev section of the global CLAUDE.md with the shipped text. Human only; keeps a backup. The section runs from its heading to
    the next heading of the same or a higher level (a `###` inside it belongs to it); headings inside code fences are not headings."""
    _require_human("jade claude-md apply", stdin_isatty)
    p = target or Path.home() / ".claude" / "CLAUDE.md"
    text = p.read_text(encoding="utf-8")
    title = JEV_HEADING.lstrip("# ").strip()
    heads = list(markdown_headings(text))
    starts = [(i, lvl) for i, lvl, t in heads if t == title and lvl == 2]
    if not starts:
        raise ValueError(f"heading not found in {p}: {JEV_HEADING}")
    if len(starts) > 1:
        raise ValueError(f"heading found {len(starts)} times in {p} (lines {', '.join(str(i + 1) for i, _ in starts)}); remove the duplicate first")
    first, level = starts[0]
    nxt = next((i for i, lvl, _t in heads if i > first and lvl <= level), None)
    lines = text.splitlines(keepends=True)
    start = sum(len(x) for x in lines[:first])
    end = len(text) if nxt is None else sum(len(x) for x in lines[:nxt])
    backup = backup_file(p)
    atomic_write(p, text[:start] + claude_md_section().rstrip("\n") + "\n\n" + text[end:])
    return backup


def launch_agents_dir() -> Path:
    return Path(os.environ.get("JADE_LAUNCH_AGENTS") or Path.home() / "Library" / "LaunchAgents")


def launchd_plist(label: str) -> str:
    """The daily maintenance LaunchAgent. launchd starts agents with PATH=/usr/bin:/bin:/usr/sbin:/sbin and no profile, so PATH (the directory
    that holds `jade` and `jev`, then the usual tool directories) and HOME are written into the job. plistlib escapes every value."""
    here = os.path.dirname(jade_bin())
    path = [d for d in (here, "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin") if d]
    doc = {"Label": label, "ProgramArguments": [jade_bin(), "maintain"],
           "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path)), "HOME": str(Path.home())},
           "StartCalendarInterval": {"Hour": 9, "Minute": 15},
           "StandardOutPath": str(paths.home() / "maintain.log"), "StandardErrorPath": str(paths.home() / "maintain.log"), "ProcessType": "Background"}
    return plistlib.dumps(doc, sort_keys=False).decode()


PROBE_SESSION = "jade-check-probe"


def _probe_event(sub: str) -> dict:
    if sub == "pretool":   # a call SI-09 always blocks: a hook that decides blocks it, a hook that does nothing lets it through
        return {"tool_name": "Bash", "tool_input": {"command": "jade permit approve prm_000000000000"}, "session_id": PROBE_SESSION, "cwd": "/tmp"}
    if sub == "posttool":
        return {"tool_name": "Bash", "tool_input": {"command": "true"}, "tool_response": {}, "session_id": PROBE_SESSION, "cwd": "/tmp"}
    return {"session_id": PROBE_SESSION, "cwd": "/tmp"}


def probe_hook(parsed: HookCommand, timeout_s: float = 15.0) -> str | None:
    """Run the registered hook command once with a synthetic event, in a throwaway JADE_HOME and with the gate forced to enforce. None when it
    behaves; else what is wrong. The PreToolUse probe must come back as a block that names SI-09: silence from the gate is the failure this exists to catch."""
    import subprocess

    with tempfile.TemporaryDirectory(prefix="jade-probe-") as d:
        env = {**os.environ, "JADE_HOME": d, "JADE_GATE_MODE": "enforce"}
        try:
            r = subprocess.run([parsed.binary, "hook", parsed.sub], input=json.dumps(_probe_event(parsed.sub)), capture_output=True, text=True,
                               timeout=timeout_s, env=env, cwd=d, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            return f"{parsed.sub}: could not run {parsed.binary} ({type(e).__name__}: {e})"
    err = (r.stderr or "").strip()
    if "internal error" in err or "Traceback" in err or "time budget" in err:
        return f"{parsed.sub}: the hook failed on a synthetic event ({err.splitlines()[-1][:160]})"
    if parsed.sub == "pretool":
        if r.returncode != 2 or "SI-09" not in err:
            return f"{parsed.sub}: exit {r.returncode} for a command SI-09 must block (no decision made)"
    elif r.returncode != 0:
        return f"{parsed.sub}: exit {r.returncode} on a synthetic event"
    return None


def wiring_problems(probe: bool = True) -> list[str]:
    """Everything that makes the installed hooks dead or inert, empty when they work. Static facts first (binary missing or not executable,
    hooks disabled, a matcher or a command that the host would not run as intended), then one live run of each registered command."""
    problems: list[str] = []
    sp = settings_path()
    for f in (sp, sp.with_name("settings.local.json")):
        if _read_quiet(f).get("disableAllHooks"):
            problems.append(f"disableAllHooks is true in {f}: the host runs no hook at all")
    data = _read_quiet(sp)
    seen: dict[str, HookCommand] = {}
    for event, g, h, parsed in _jade_hooks(data):
        want = HOOKS.get(event)
        if want is None or want[1] != parsed.sub:
            problems.append(f"`hook {parsed.sub}` is registered under {event}, expected {next((e for e, v in HOOKS.items() if v[1] == parsed.sub), '?')}")
            continue
        if h.get("type") != "command":
            problems.append(f"{event}: hook type {h.get('type')!r}, expected 'command'")
        if event != "PreToolUse" and (g.get("matcher") or "") != want[0]:
            problems.append(f"{event}: matcher {g.get('matcher')!r}, expected {want[0]!r}")
        b = parsed.binary
        if not parsed.shell_safe:
            problems.append(f"{event}: command is not `<one word> hook {parsed.sub}` (an unquoted path with a space?): re-run `jade hooks install`")
        elif not (os.access(b, os.X_OK) and os.path.isfile(b) if "/" in b else shutil.which(b)):
            problems.append(f"{event}: hook binary {b} does not exist or is not executable: re-run `jade hooks install` from the current `jade`")
        else:
            seen.setdefault(parsed.sub, parsed)
    if probe:
        problems += [m for parsed in seen.values() if (m := probe_hook(parsed))]
    return problems


PERMISSIVE_MODES = ("bypassPermissions", "auto")


def configured_permission_mode() -> str | None:
    """permissions.defaultMode of the Claude settings files, the most local file first."""
    sp = settings_path()
    for f in (sp.with_name("settings.local.json"), sp):
        perms = _read_quiet(f).get("permissions")
        if isinstance(perms, dict) and isinstance(perms.get("defaultMode"), str):
            return perms["defaultMode"]
    return None


def settings_env_overrides() -> list[paths.Override]:
    """JADE_* variables written into the `env` block of the Claude settings files: the host hands them to every hook it starts."""
    sp = settings_path()
    out: list[paths.Override] = []
    for f in (sp, sp.with_name("settings.local.json")):
        env = _read_quiet(f).get("env")
        if isinstance(env, dict):
            out += paths.gate_overrides({k: str(v) for k, v in env.items() if str(k).startswith("JADE_")}, source=str(f))
    return out


def _gate_mode_row() -> tuple[str, bool, str]:
    effective, raw = paths.gate_mode(), os.environ.get("JADE_GATE_MODE")
    cfg = paths.config().get("gate_mode")
    configured = cfg if cfg in paths.GATE_MODES else "enforce"
    if effective == "off":
        return ("gate mode", False, "off: every JADE hook is inert" + (" (JADE_GATE_MODE in this environment)" if raw else " (`jade gate off`)") + "; `jade gate enforce` restores it")
    if raw and raw != configured:
        return ("gate mode", False, f"JADE_GATE_MODE={raw} in this environment overrides the configured {configured}; unset it or change the config on purpose")
    if effective == "advisory":
        return ("gate mode", True, "advisory: the gate records would_block and never blocks (the soak state of docs/08 2.1)")
    return ("gate mode", True, effective)


def _overrides_row() -> tuple[str, bool, str]:
    mine = paths.gate_overrides()
    ack = {x.strip() for x in os.environ.get("JADE_ACK_OVERRIDES", "").split(",") if x.strip()}
    theirs = settings_env_overrides()
    weak = [o for o in mine if o.weakens and o.var not in ack] + [o for o in theirs if o.weakens]
    shown = ", ".join(f"{o.var}={o.value} ({o.source}{', acknowledged' if o.var in ack and o in mine else ''})" for o in (*mine, *theirs))
    if weak:
        return ("gate overrides", False, "weakening: " + ", ".join(f"{o.var} [{o.why}]" for o in weak) + ". Unset them; `JADE_ACK_OVERRIDES=VAR,...` only silences "
                "this row for a deliberate override in this shell, never one in the Claude settings `env` block")
    return ("gate overrides", True, shown or "none (JADE_GATE_MODE, JADE_POLICY_DIR, JADE_CONFIG, JADE_HOME all default)")


def _find_jev() -> str | None:
    """The `jev` an agent runs: the first on PATH, else the one beside `jade` (launchd and other minimal environments have no ~/.local/bin on PATH)."""
    if found := shutil.which("jev"):
        return found
    beside = Path(jade_bin()).with_name("jev")
    return str(beside) if beside.is_file() and os.access(beside, os.X_OK) else None


def _launchctl_loaded(label: str) -> bool:
    import subprocess

    try:
        return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def maintain_jobs() -> list[tuple[Path, str, list]]:
    """(plist, label, ProgramArguments) of every LaunchAgent that runs `jade maintain`."""
    out = []
    d = launch_agents_dir()
    for f in sorted(d.glob("*.plist")) if d.is_dir() else []:
        try:
            doc = plistlib.loads(f.read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        args = doc.get("ProgramArguments") if isinstance(doc, dict) else None
        if isinstance(args, list) and len(args) >= 2 and args[-1] == "maintain" and os.path.basename(str(args[0])) in {"jade", os.path.basename(jade_bin())}:
            out.append((f, str(doc.get("Label") or f.stem), args))
    return out


def _maintain_row() -> tuple[str, bool, str]:
    jobs = maintain_jobs()
    if not jobs:
        return ("maintain job", True, f"no `jade maintain` LaunchAgent in {launch_agents_dir()} (optional, docs/08 2.4)")
    bad = []
    for f, label, args in jobs:
        if not (os.path.isfile(str(args[0])) and os.access(str(args[0]), os.X_OK)):
            bad.append(f"{label}: {args[0]} is not an executable file")
        elif not _launchctl_loaded(label):
            bad.append(f"{label}: {f.name} exists but launchd has not loaded it (`launchctl bootstrap gui/$(id -u) {f}`)")
    return ("maintain job", not bad, "; ".join(bad) or ", ".join(f"{label} loaded" for _f, label, _a in jobs))


# ---- health --------------------------------------------------------------------------------------
def check(live: bool = True, provider=None) -> list[tuple[str, bool, str]]:
    """Wiring probe. Every row is (name, ok, detail); the CLI exits non-zero when any row fails."""
    from . import adapter, policy, questions

    rows: list[tuple[str, bool, str]] = []
    try:
        db = ledger.open()
        db.meta_set("last_check", ts())
        n = db.one("SELECT COUNT(*) AS n FROM jev_calls").n
        last = db.one("SELECT MAX(ts) AS t FROM jev_calls").t
        rows.append(("ledger writable", True, f"{db.path} ({n} calls, last {last or 'never'})"))
        db.close()
    except Exception as e:  # noqa: BLE001 - a health probe reports every failure
        rows.append(("ledger writable", False, repr(e)))
    try:
        policy.reset_cache()
        r, s = policy.risk(), policy.safety()
        rows.append(("policies load", True, f"{len(r['rules'])} rules, {len(s['invariants'])} invariants, version {paths.policy_version()}, "
                                            f"overlay {'yes' if paths.overlay('risk') else 'no'}"))
    except Exception as e:  # noqa: BLE001
        rows.append(("policies load", False, repr(e)))
    try:
        bad = {e["question_id"]: questions.lint(e) for e in questions.entries() if questions.lint(e)}
        rows.append(("question registry", not bad and bool(questions.entries()), f"{len(questions.entries())} entries" + (f", lint: {bad}" if bad else "")))
    except Exception as e:  # noqa: BLE001
        rows.append(("question registry", False, repr(e)))
    st = hooks_status()
    missing = [k for k, v in st.items() if not v]
    rows.append(("hooks installed", not missing, "all four" if not missing else f"missing {', '.join(missing)}: run `jade hooks install` in a terminal"))
    if not missing:
        problems = wiring_problems()
        rows.append(("hook wiring", not problems, "binary runs, hooks enabled, a synthetic event is decided" if not problems else "; ".join(problems)))
    gaps = pretool_matcher_gaps()
    if gaps is not None:
        rows.append(("pretool matcher", not gaps, "covers every gated tool" if not gaps else f"does not cover {', '.join(gaps)}: re-run `jade hooks install` in a terminal"))
    rows.append(_gate_mode_row())
    rows.append(_overrides_row())
    rows.append(_permission_row())
    rows.append(_maintain_row())
    shim = _find_jev()
    is_shim = bool(shim) and "jade" in (Path(shim).read_text(errors="replace")[:600] if Path(shim).is_file() else "")
    rows.append(("jev shim", is_shim, shim or "jev not on PATH and not beside jade"))
    if live:
        prov = provider or adapter.OpenRouterProvider()
        q = {"type": "noul", "instructions": "Is `claim` directly supported by `evidence`?",
             "criteria": {"true": "The evidence directly shows the claim is correct.", "false": "The evidence is missing, insufficient, or contradicts the claim."}}
        yes = prov.ask({"claim": "The command exited with status 0", "evidence": "$ make test\nall 12 tests passed\nexit status: 0"}, {"q": q})
        no = prov.ask({"claim": "The command exited with status 0", "evidence": "$ make test\n3 tests failed\nexit status: 2"}, {"q": q}) if yes.ok else yes
        if yes.ok and no.ok:
            py, pn = yes.answers["q"]["noul"], no.answers["q"]["noul"]
            ok = py >= 0.70 and pn <= 0.30
            rows.append(("provider canary", ok, f"known-yes P={py:.2f}, known-no P={pn:.2f}, model {yes.resolved_model}, {yes.latency_ms:.0f} ms"))
            drift = check_drift(None, yes.resolved_model, expected_models())
            rows.append(("model drift", True, "none" if drift is None else f"MODEL_DRIFT (informational): {drift.resolved_model}"))
        else:
            bad_r = yes if not yes.ok else no
            rows.append(("provider canary", False, f"{bad_r.error_kind} {bad_r.http_status or ''} {bad_r.detail}"))
    return rows


def _permission_row() -> tuple[str, bool, str]:
    """The host-prompt note, plus the one combination that leaves nothing: a host that never asks (permissions.defaultMode bypassPermissions or auto)
    with a gate that never blocks (advisory or off)."""
    detail = _permission_prompt_detail()
    mode, gate = configured_permission_mode(), paths.gate_mode()
    if mode:
        detail = f"permissions.defaultMode is {mode}. " + detail
    if mode in PERMISSIVE_MODES and gate != "enforce":
        return ("host permission prompt", False, f"permissions.defaultMode is {mode} (the host never asks) and the gate is {gate} (JADE never blocks): nothing stops a call. "
                "Set the gate to enforce or the host to a prompting mode. " + detail)
    return ("host permission prompt", True, detail)


def _permission_prompt_detail() -> str:
    """JADE's gate is the only gate whenever the host shows no permission prompt; say so, and say how often that was the case here."""
    from . import hooks

    text = ("JADE's gate is the only gate when the host runs with permission_mode bypassPermissions or auto, with acceptEdits (file edits), "
            "headless (`claude -p`) or in unattended subagents: the host prompt is not an independent backstop there (docs/05 T18)")
    try:
        db = ledger.open(timeout=2)
        try:
            rows = db.all("SELECT detail_json FROM events WHERE kind IN ('gated','safety_block') AND ts>=?", (ts(now() - timedelta(days=7)),))
        finally:
            db.close()
        seen = no_prompt = 0
        for r in rows:
            d = json.loads(r.detail_json or "{}")
            seen += 1
            no_prompt += bool(hooks.no_host_prompt(d.get("permission_mode"), str(d.get("tool") or "")))
        text += f". Last 7 days: {no_prompt} of {seen} gated call(s) ran with no host prompt" if seen else ". Last 7 days: no gated calls recorded"
    except Exception:  # noqa: BLE001, S110 - the note is informational; the ledger row above reports ledger failures
        pass
    return text


def print_check(rows) -> int:
    for name, ok, detail in rows:
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: {detail}")
    failed = [n for n, ok, _ in rows if not ok]
    if failed:
        print(f"jade check: {len(failed)} failed ({', '.join(failed)})", file=sys.stderr)
    return 1 if failed else 0
