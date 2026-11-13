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
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

from . import ledger, paths, policy, signing
from .util import is_human_tty, now, ts, user


def pretool_matcher() -> str:
    """Every built-in tool the gate assesses (policy.GATED_BUILTIN) and every MCP tool. Agent/Task are not listed: they need no gate,
    and the tool calls a subagent makes reach the same hook."""
    return "|".join((*policy.GATED_BUILTIN, "AskUserQuestion", "mcp__.*"))


def posttool_matcher() -> str:
    """The calls that can run under a permit (policy.POST_TOOLS and every MCP tool): PostToolUse closes the decision of each (W-40)."""
    return "|".join((*policy.POST_TOOLS, "AskUserQuestion", "mcp__.*"))


# PreToolUse: a timeout above askdialog.DIALOG_WAIT_S + ALARM_MARGIN_S, because the hook waits for a macOS dialog (W-100c); the others answer at once
HOOKS = {"PreToolUse": (pretool_matcher(), "pretool", 130), "PostToolUse": (posttool_matcher(), "posttool", 10),
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


def check_question_drift(path=None, db: ledger.Ledger | None = None) -> list:
    """QUESTION_DRIFT findings: (question_id, version) pairs the registry holds that this ledger has not seen, one event and one informational
    work item each, never twice (W-51). A new question version starts its own calibration group at default thresholds, as a new model snapshot does
    (docs/07 section 1). The first look at a ledger is a baseline: it records what the registry holds and opens nothing. Reads the registry through
    `questions.entries()`, so an unreadable registry raises and the caller decides (the hook and `ask` treat it as informational)."""
    from . import questions
    from .util import sha256

    current = {(e["question_id"], int(e["version"])) for e in questions.entries()}
    own = db is None
    db = db or ledger.open(_dir(path))
    try:
        raw = db.meta_get("known_question_versions")
        known = {(q, v) for q, vs in json.loads(raw).items() for v in vs} if raw else None
        if known is not None and current <= known:
            return []
        merged: dict[str, list[int]] = {}
        for q, v in sorted((known or set()) | current):
            merged.setdefault(q, []).append(v)
        db.meta_set("known_question_versions", json.dumps(merged))
        if known is None:
            return []
        found = []
        for q, v in sorted(current - known):
            if not db.event("question", "QUESTION_DRIFT", next=f"{q}@{v}", guard="question version added to the registry", key=f"question_drift:{q}@{v}"):
                continue
            wid = "RT-QD-" + sha256(f"{q}@{v}")[:8]
            item = {"id": wid, "title": f"QUESTION_DRIFT: {q} now has version {v}", "type": "risk", "priority": "P3", "status": "open", "owner": "human",
                    "next_action": "Informational. The new version is its own calibration group at default thresholds; check its template and pin it as champion or "
                    "challenger when reviewed (docs/07 section 1).", "retry_or_revisit_trigger": "on_session_start", "created_at": ts()}
            db.con.execute("INSERT OR REPLACE INTO work_items(id, status, doc_json, updated_at) VALUES(?,?,?,?)", (wid, "open", json.dumps(item), ts()))
            found.append(SimpleNamespace(kind="QUESTION_DRIFT", question_id=q, version=v, work_item=wid))
        return found
    finally:
        if own:
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


TEST_ID = re.compile(r"(?:prm|dec)_[0-9a-f]{12}")


def purge_test_rows(db: ledger.Ledger, ids=()) -> dict[str, int]:
    """Remove the rows a verification run left in the ledger under `JADE_SOURCE=test` (W-72): Jev calls (and their labels), decisions (and their
    outcomes), permits and events. Returns the number removed per table. `ids` are permit or decision ids (`prm_...`, `dec_...`) written before the tag
    existed: they are tagged first, together with the permits of a named decision, the decision of a named permit (when no other permit uses it) and the events of all of them. Rows
    with no tag are never touched, so a permit nobody named stays. The only record is one `purge` event with counts; no command text is read or kept."""
    ids = [str(i) for i in ids]
    for i in ids:
        if not TEST_ID.fullmatch(i):
            raise ValueError(f"not a permit or decision id: {i!r} (prm_ or dec_ followed by 12 hex characters)")
    out = dict.fromkeys(("jev_calls", "decisions", "permits", "events"), 0)
    with db.tx() as con:
        for i in ids:
            if i.startswith("dec_"):
                con.execute("UPDATE decisions SET source='test' WHERE decision_id=?", (i,))
                con.execute("UPDATE permits SET source='test' WHERE decision_id=?", (i,))
            else:
                con.execute("UPDATE permits SET source='test' WHERE permit_id=?", (i,))
                # the decision goes with its permit only when no other permit still uses it: a permit that stays must not lose its decision
                con.execute("UPDATE decisions SET source='test' WHERE decision_id IN (SELECT decision_id FROM permits WHERE permit_id=?) "
                            "AND NOT EXISTS (SELECT 1 FROM permits p WHERE p.decision_id=decisions.decision_id AND COALESCE(p.source,'')<>'test')", (i,))
        con.execute("UPDATE events SET source='test' WHERE entity_id IN (SELECT decision_id FROM decisions WHERE source='test' "
                    "UNION SELECT permit_id FROM permits WHERE source='test')")
        con.execute("DELETE FROM labels WHERE call_id IN (SELECT call_id FROM jev_calls WHERE source='test')")
        con.execute("DELETE FROM outcomes WHERE decision_id IN (SELECT decision_id FROM decisions WHERE source='test')")
        for table in ("jev_calls", "decisions", "permits", "events"):
            out[table] = con.execute(f"DELETE FROM {table} WHERE source='test'").rowcount  # noqa: S608 - constant table names
        if sum(out.values()):
            db.event("ledger", "purge", guard="test rows", detail={"test_rows": sum(out.values()), **out}, con=con)
    if sum(out.values()):
        db.scrub()
    return out


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
    ours: bool = True  # the program is JADE's (is_jade_program), not another program that is only called `jade`


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
    return HookCommand(binary, m["sub"], safe, is_jade_program(binary))


_MARKERS = (b"jade.cli", b"jade-decision-governance", b"from jade", b"import jade")


def is_jade_program(binary: str) -> bool:
    """Whether a program named `jade` in a hook or LaunchAgent is this package's launcher and not another tool that shares the name.
    The configured `JADE_BIN` and the `jade` that PATH finds are JADE's when they resolve to the same file as the program named; any other file
    is JADE's when it is a launcher that runs `jade.cli` (a uv tool, a venv script, a shim) and is not when it is something else. A path that no
    longer exists cannot be read, so it is JADE's only when it is the configured one, `~/.local/bin/jade`, or inside a `jade-decision-governance`
    directory (the uv tool install); a bare `jade` that PATH cannot resolve is JADE's (a dead install of ours, which uninstall should remove)."""
    path = binary
    if "/" not in binary:
        path = shutil.which(binary) or ""
        if not path:
            return True
    path = os.path.expanduser(path)
    known = {os.path.realpath(p) for p in (os.environ.get("JADE_BIN"), shutil.which("jade") if "/" in binary else None) if p}
    known.add(os.path.realpath(jade_bin()))
    if os.path.realpath(path) in known or path in {jade_bin(), str(Path.home() / ".local" / "bin" / "jade")}:
        return True
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                head = f.read(65536)
        except OSError:
            return False
        return any(mark in head for mark in _MARKERS)
    return "jade-decision-governance" in Path(path).parts


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
                if parsed and parsed.ours:
                    yield event, g, h, parsed


def _read_quiet(p: Path) -> dict:
    try:
        return _load_settings(p)
    except WiringError:
        return {}


ON_FAILURE_MIN_HOST = (2, 1, 295)   # the Claude Code version that honours `"onFailure": "block"` on a hook entry (docs/12; measured on 2.1.314 in evals/probes/hook_ask/RESULTS.md)


def host_version() -> tuple[int, int, int] | None:
    """The installed Claude Code version as (major, minor, patch), or None when `claude --version` cannot be read."""
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", r.stdout or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _wants_on_failure(event: str) -> bool:
    """Only the PreToolUse hook fails closed: a crashed or hung gate then blocks the call instead of letting it run (probe cell 9). Older hosts ignore the field."""
    if event != "PreToolUse":
        return False
    v = host_version()
    return v is not None and v >= ON_FAILURE_MIN_HOST


def _entry_current(event: str, g: dict, h: dict) -> bool:
    """Is one installed JADE entry what `hooks install` writes now? A timeout may be longer than ours (an operator's choice), never shorter, and `onFailure`
    is required on PreToolUse when the host honours it. Matchers are not compared here: `pretool_matcher_gaps` (PreToolUse, where a wider matcher is fine)
    and `wiring_problems` (the other events) report them with the exact fix."""
    _matcher, _sub, want_timeout = HOOKS[event]
    timeout = h.get("timeout")
    if not isinstance(timeout, (int, float)) or timeout < want_timeout:
        return False
    return not (_wants_on_failure(event) and h.get("onFailure") != "block")


def hooks_status() -> dict[str, bool]:
    data = _read_quiet(settings_path())
    current = {(event, parsed.sub) for event, g, h, parsed in _jade_hooks(data) if event in HOOKS and _entry_current(event, g, h)}
    gaps = pretool_matcher_gaps()
    out = {event: (event, sub) in current for event, (_m, sub, _t) in HOOKS.items()}
    if gaps:
        out["PreToolUse"] = False
    return out


def pretool_matcher_gaps() -> list[str] | None:
    """Gated tools the installed PreToolUse matcher does not cover (an install made before the matcher grew). None when the hook is not installed."""
    data = _read_quiet(settings_path())
    matchers = [g.get("matcher", "") for event, g, _h, p in _jade_hooks(data) if event == "PreToolUse" and p.sub == "pretool"]
    if not matchers:
        return None
    gaps = []
    for tool in (*policy.GATED_BUILTIN, "AskUserQuestion", "mcp__server__tool"):
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
            keep = [h for h in g.get("hooks", []) if not ((p := parse_hook_command(h.get("command", ""))) and p.ours)]
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
        if _wants_on_failure(event):
            entry["hooks"][0]["onFailure"] = "block"
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


def set_gate_mode(mode: str, stdin_isatty: bool | None = None, allow_unsigned: bool = False) -> None:
    _require_human("jade gate", stdin_isatty)
    if mode == "enforce" and not signing.signed_backend():   # W-38 / W-89 (maintainer decision 2026-11-13): enforce is not a boundary against a same-user agent without it
        seen, no_prompt = no_prompt_gated_calls()
        if no_prompt and not allow_unsigned:
            raise HumanOnly(f"enforce mode needs the signed approval backend (docs/10): {no_prompt} of {seen} gated call(s) in the last 7 days ran with no host prompt, "
                            "so JADE's gate is the only gate and a terminal approval can be forged by a same-user process. Install the approver (docs/10 section 6, W-90), "
                            "or pass --allow-unsigned to accept that risk; the override is journaled")
        if allow_unsigned:
            db = ledger.open(timeout=5)
            try:
                db.event("gate", "enforce_unsigned_override", guard="enforce without the signed approval backend", actor=f"human:{user()}",
                         detail={"gated_7d": seen, "no_host_prompt_7d": no_prompt})
            finally:
                db.close()
    cfg = paths.config()
    cfg["gate_mode"] = mode
    atomic_write(paths.config_dir() / "config.json", json.dumps(cfg, indent=1) + "\n")


LEVEL_CHOICES = ("unset", "off", "high")   # 0.5.0: low and medium arrive with memory and Jev (docs/12, release 0.6.0)


def _recorded_level() -> str | None:
    """The level the newest `jade level` event recorded (None for `unset`, for no event, and when the ledger cannot be read)."""
    try:
        db = ledger.open(timeout=5)
    except Exception:  # noqa: BLE001 - an unreadable ledger records nothing
        return None
    try:
        row = db.con.execute("SELECT detail_json FROM events WHERE kind='level' ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        db.close()
    try:
        return (json.loads(row[0]) or {}).get("level") if row and row[0] else None
    except (ValueError, AttributeError):
        return None


def set_level(value: str, stdin_isatty: bool | None = None) -> None:
    """`jade level unset|off|high`: human terminal only (like `jade gate`). The change is journaled as a `level` event first; a level found in the config
    that no `level` event recorded was written some other way, so setting a new one is refused until the human clears it with `unset`."""
    _require_human("jade level", stdin_isatty)
    if value not in LEVEL_CHOICES:
        raise ValueError(f"level must be one of {', '.join(LEVEL_CHOICES)} in this release (low and medium come with memory and Jev, docs/12), not {value!r}")
    cfg = paths.config()
    if value != "unset" and "level" in cfg and cfg["level"] != _recorded_level():
        raise HumanOnly("the config holds a level that no `jade level` command recorded (it was written some other way): run `jade level unset`, check "
                        "the config directory, then set the level again")
    db = ledger.open(timeout=5)
    try:
        db.event("level", "level", guard=f"jade level {value}", actor=f"human:{user()}", detail={"level": None if value == "unset" else value})
    finally:
        db.close()
    if value == "unset":
        cfg.pop("level", None)
    else:
        cfg["level"] = value
    atomic_write(paths.config_dir() / "config.json", json.dumps(cfg, indent=1) + "\n")


def _level_row() -> tuple[str, bool, str]:
    raw, lv = paths.config().get("level"), paths.level()
    if raw is None:
        return ("level", True, "unset: JADE behaves as release 0.4.0 (`jade level high` turns on in-chat approval, docs/12)")
    if lv is None:
        return ("level", False, f"the config level {raw!r} is not off, low, medium or high, so it counts as unset; `jade level unset` clears it")
    if raw != _recorded_level():
        return ("level", False, f"{lv}, but no `jade level` command recorded it (written some other way?); run `jade level unset`, look at the config directory, set it again")
    return ("level", True, f"{lv}")


def parse_since(value: str) -> float:
    """`90m`, `24h` or `7d` as a number of seconds."""
    m = re.fullmatch(r"(\d+)([mhd])", (value or "").strip())
    if not m:
        raise ValueError(f"invalid window {value!r}: use 90m, 24h or 7d")
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]


def review(since_s: float | None = None) -> str:
    """What the human answered to JADE's asks (read-only): counts by answer, strength, rule and channel. Confirming answers is a later, human-only step."""
    db = ledger.open(timeout=5)
    try:
        rows = db.human_answers(since_s=since_s, limit=100000)
    finally:
        db.close()
    window = "all time" if since_s is None else f"last {int(since_s // 3600)}h" if since_s >= 3600 else f"last {int(since_s // 60)}m"
    if not rows:
        return f"no answers recorded ({window})"

    def tally(key):
        out: dict[str, list[int]] = {}
        for r in rows:
            c = out.setdefault(r[key], [0, 0])
            c[0 if r["answer"] == "allow" else 1] += 1
        return out

    allow = sum(1 for r in rows if r["answer"] == "allow")
    hints = sum(1 for r in rows if r["strength"] == "hint")
    lines = [f"{len(rows)} answer(s) ({window}): {allow} allow, {len(rows) - allow} deny; {hints} hint, {len(rows) - hints} confirmed"]
    lines.append("by rule: " + ", ".join(f"{k} {a + d} ({a} allow, {d} deny)" for k, (a, d) in sorted(tally("rule_id").items())))
    lines.append("by channel: " + ", ".join(f"{k} {a + d}" for k, (a, d) in sorted(tally("channel").items())))
    return "\n".join(lines)


def claude_md_section() -> str:
    return (paths.docs_dir() / "claude-md-jev-section.md").read_text()


_ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_BREAK = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_CONTAINER = re.compile(r"^ {0,3}(?:[-*+](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$)|>)")


def markdown_headings(text: str):
    """(line_index, level, title) of every heading that is not inside a fenced code block: ATX (`## T`) and setext (`T` over `===` or `---`).
    A setext heading is the paragraph lines above its underline and starts at the first of them; a list item, a block quote and what leans on
    them up to the next blank line is never part of a paragraph that an underline could turn into a heading (`---` there is a thematic break)."""
    fence = None
    para: list[tuple[int, str]] = []   # the open paragraph: (line index, text)
    container = False                   # inside a list item or block quote (until a blank line)
    for i, line in enumerate(text.splitlines()):
        m = _FENCE.match(line)
        if fence:
            if m and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not line.strip().strip(fence[0]):
                fence = None
            continue
        if m:
            fence, para, container = m[1], [], False
            continue
        if not line.strip():
            para, container = [], False
            continue
        if para and (u := _SETEXT.match(line)):
            yield para[0][0], 1 if u[1][0] == "=" else 2, " ".join(t for _, t in para)
            para = []
            continue
        h = _ATX.match(line)
        if h:
            yield i, len(h[1]), h[2]
            para, container = [], False
            continue
        if _BREAK.match(line):
            para, container = [], False
            continue
        if _CONTAINER.match(line):
            para, container = [], True
            continue
        if container or (line.startswith("    ") and not para):   # a continuation of an item, or an indented code block
            continue
        para.append((i, line.strip()))


class SectionNotFound(ValueError):
    """The Jev heading is not in the CLAUDE.md text."""


def claude_md_render(text: str, where: str = "CLAUDE.md") -> str:
    """`text` with its Jev section replaced by the shipped one (pure: reads nothing but the shipped section, writes nothing). The section runs from its
    heading to the next heading of the same or a higher level (a `###` inside it belongs to it); headings inside code fences are not headings.
    SectionNotFound when the heading is absent, ValueError when it appears twice. `where` only names the file in the messages."""
    title = JEV_HEADING.lstrip("# ").strip()
    heads = list(markdown_headings(text))
    starts = [(i, lvl) for i, lvl, t in heads if t == title and lvl == 2]
    if not starts:
        raise SectionNotFound(f"heading not found in {where}: {JEV_HEADING}")
    if len(starts) > 1:
        raise ValueError(f"heading found {len(starts)} times in {where} (lines {', '.join(str(i + 1) for i, _ in starts)}); remove the duplicate first")
    first, level = starts[0]
    nxt = next((i for i, lvl, _t in heads if i > first and lvl <= level), None)
    lines = text.splitlines(keepends=True)
    start = sum(len(x) for x in lines[:first])
    end = len(text) if nxt is None else sum(len(x) for x in lines[:nxt])
    return text[:start] + claude_md_section().rstrip("\n") + "\n\n" + text[end:]


def claude_md_apply(target: Path | None = None, stdin_isatty: bool | None = None) -> Path:
    """Replace the Jev section of the global CLAUDE.md with the shipped text. Human only; keeps a backup (see claude_md_render for the section's extent)."""
    _require_human("jade claude-md apply", stdin_isatty)
    p = target or Path.home() / ".claude" / "CLAUDE.md"
    text = p.read_text(encoding="utf-8")
    new = claude_md_render(text, str(p))
    backup = backup_file(p)
    atomic_write(p, new)
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
            problems.append(f"{event}: matcher {g.get('matcher')!r}, expected {want[0]!r}: re-run `jade hooks install` in a terminal")
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


# ---- settings files other than the user's (W-47) --------------------------------------------------------------------
_MANAGED_SETTINGS = "/Library/Application Support/ClaudeCode/managed-settings.json"
_JADE_HOOK_WORDS = re.compile(r"\bhook\s+(?:pretool|posttool|stop|session-start)\b")
_GATE_ASSIGN = re.compile(r"\bJADE_(?:GATE_MODE|POLICY_DIR|CONFIG|HOME)\s*=")


def managed_settings_path() -> Path:
    """The managed-policy settings file of the standard macOS location (`JADE_MANAGED_SETTINGS` moves it, for tests)."""
    return Path(os.environ.get("JADE_MANAGED_SETTINGS") or _MANAGED_SETTINGS)


def project_settings_files(cwd) -> list[Path]:
    """The project `.claude/settings.json` and `settings.local.json` a session started in `cwd` can read: in `cwd` and each ancestor up to the
    repository root (the nearest directory with `.git`; without one, `cwd` alone). The user's own two files are not listed: wiring_problems owns them."""
    try:
        start = Path(cwd).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return []
    dirs: list[Path] = []
    for d in (start, *start.parents):
        dirs.append(d)
        if (d / ".git").exists():
            break
    else:
        dirs = [start]
    sp = settings_path()
    user = {os.path.realpath(sp), os.path.realpath(sp.with_name("settings.local.json"))}
    return [f for d in dirs for name in ("settings.json", "settings.local.json") if os.path.realpath(f := d / ".claude" / name) not in user]


def _plugin_registry() -> tuple[list[Path], list[str]]:
    """(hook files, problems) of the plugins the user settings do not switch off (`enabledPlugins`), from the host's plugin registry: each plugin's
    `hooks/hooks.json` and the `hooks` its `.claude-plugin/plugin.json` declares inline. A registry that exists but cannot be read or is not a JSON
    object is a problem (JADE cannot tell what the plugins run); a missing one means no plugins."""
    reg_path = settings_path().parent / "plugins" / "installed_plugins.json"
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError) as e:
        return [], [f"{reg_path}: plugin registry cannot be read ({type(e).__name__}); JADE cannot tell whether a plugin disables or shadows the hooks"]
    if not isinstance(reg, dict):
        return [], [f"{reg_path}: plugin registry does not hold a JSON object; JADE cannot tell whether a plugin disables or shadows the hooks"]
    plugins = reg.get("plugins")
    enabled = _read_quiet(settings_path()).get("enabledPlugins")
    out: list[Path] = []
    for name, entries in (plugins.items() if isinstance(plugins, dict) else ()):
        if isinstance(enabled, dict) and enabled.get(name) is False:
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            ip = e.get("installPath") if isinstance(e, dict) else None
            if isinstance(ip, str) and ip:
                out += [Path(ip) / "hooks" / "hooks.json", Path(ip) / ".claude-plugin" / "plugin.json"]
    return out, []


def plugin_hook_files() -> list[Path]:
    """The hook files of each installed plugin that the user settings do not switch off (see _plugin_registry)."""
    return _plugin_registry()[0]


MAX_SETTINGS_BYTES = 1024 * 1024
# Variables in a project `env` block that change which program the hooks run or which files they read; the host hands the block to every hook it starts.
_ENV_MOVERS = re.compile(r"(?:HOME|PATH|PYTHON\w*|XDG_\w+|LD_\w+|DYLD_\w+)")


def _safe_value(v, n: int = 40) -> str:
    """Settings text shown in a report: a short run of path-like characters only, so a file in a cloned repository cannot put prose in front of the model."""
    return re.sub(r"[^\w./:=@+,~-]", "?", str(v))[:n]


def _shadow_scan(f: Path, *, managed: bool = False) -> list[str]:
    """What one settings or plugin hook file that is not the user's does to JADE's hooks, as `<file>: <key>: <effect>` lines; [] when the file is
    absent or harmless. A file that cannot be read or parsed is reported: JADE cannot tell what it disables, and the host may still apply it."""
    label = f"{f} (-> {os.path.realpath(f)})" if f.is_symlink() else str(f)
    try:
        st = os.stat(f)
        if not stat.S_ISREG(st.st_mode):
            return [f"{label}: is not a regular file, so JADE does not read it; it cannot tell whether it disables or shadows the hooks"]
        if st.st_size > MAX_SETTINGS_BYTES:
            return [f"{label}: too large ({st.st_size} bytes) to read; JADE cannot tell whether it disables or shadows the hooks"]
        with open(f, "rb") as fh:
            text = fh.read(MAX_SETTINGS_BYTES + 1).decode("utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as e:
        return [f"{label}: cannot be read ({type(e).__name__}); JADE cannot tell whether it disables or shadows the hooks"]
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as e:
        return [f"{label}: not valid JSON ({e}); JADE cannot tell whether it disables or shadows the hooks"]
    if not isinstance(data, dict):
        return [f"{label}: does not hold a JSON object; JADE cannot tell whether it disables or shadows the hooks"]
    out: list[str] = []
    if data.get("disableAllHooks"):
        out.append(f"{label}: disableAllHooks is true: the host runs no hook at all in a session that reads this file")
    if managed and data.get("allowManagedHooksOnly"):
        out.append(f"{label}: allowManagedHooksOnly is true: the host runs only managed hooks, not JADE's")
    hooks_map = data.get("hooks")
    for event, groups in (hooks_map.items() if isinstance(hooks_map, dict) else ()):
        for i, g in enumerate(groups if isinstance(groups, list) else ()):
            for j, h in enumerate(g.get("hooks") if isinstance(g, dict) and isinstance(g.get("hooks"), list) else ()):
                cmd = h.get("command") if isinstance(h, dict) else None
                if not isinstance(cmd, str):
                    continue
                parsed = parse_hook_command(cmd)
                if parsed and parsed.ours and not _GATE_ASSIGN.search(cmd):
                    continue   # the installed kind of entry, repeated: nothing shadowed
                if not parsed and not (_JADE_HOOK_WORDS.search(cmd) and "jade" in cmd.lower()):
                    continue   # not a JADE hook at all: what a foreign hook decides is not visible from here (docs/05 T19)
                why = ("sets a gate variable for the hook it starts" if _GATE_ASSIGN.search(cmd) else
                       "runs a different program called jade" if parsed else "wraps or redirects a JADE hook command; it is not the installed one")
                out.append(f"{label}: hooks.{event}[{i}].hooks[{j}].command {why}")
    env = data.get("env")
    if isinstance(env, dict):
        out += [f"{label}: env.{o.var}={_safe_value(o.value)}: {o.why}" for o in paths.gate_overrides({k: str(v) for k, v in env.items() if str(k).startswith("JADE_")}, source=str(f)) if o.weakens]
        out += [f"{label}: env.{k}: changes which program the hooks run or which files they read" for k in env if isinstance(k, str) and _ENV_MOVERS.fullmatch(k)]
    return out


def shadow_problems(cwd=None, *, managed: bool = True, plugins: bool = True) -> list[str]:
    """Hooks that a settings file other than the user's switches off or shadows, for a session working in `cwd` (default: this process's directory):
    project settings (see project_settings_files), the managed-policy file and plugin hook files. Empty when none does."""
    if cwd is None:
        try:
            cwd = Path.cwd()
        except OSError:
            cwd = None
    out: list[str] = []
    for f in project_settings_files(cwd) if cwd is not None else []:
        out += _shadow_scan(f)
    if managed:
        m = managed_settings_path()
        out += _shadow_scan(m, managed=True)
        try:
            dropins = sorted(m.with_name("managed-settings.d").glob("*.json"))   # the host merges these over managed-settings.json
        except OSError:
            dropins = []
        for f in dropins:
            out += _shadow_scan(f, managed=True)
    if plugins:
        files, problems = _plugin_registry()
        out += problems
        for f in files:
            out += _shadow_scan(f)
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
    return ("gate overrides", True, shown or "none (JADE_GATE_MODE, JADE_POLICY_DIR, JADE_CONFIG, JADE_HOME, JADE_APPROVER_ANCHOR all default)")


def overlay_problems() -> list[str]:
    """Lint of the overlays the runtime reads (`policies.loader.lint_overlay`); each finding names the file and the rule or list index, never overlay text."""
    from policies import loader

    out: list[str] = []
    for kind in ("risk", "safety"):
        p = paths.overlay(kind)
        if p is not None:
            out += [f"{p}: {f}" for f in loader.lint_overlay(kind, p, base=paths.policy_dir() / f"{kind}.yaml")]
    return out


def unread_overlay_files() -> list[Path]:
    """`*.local.yaml` files in the checkout's policies/local/ that the loader does not read (it reads `<config dir>/local/{risk,safety}.local.yaml`)."""
    def real(p: Path) -> Path:
        try:
            return p.resolve()
        except (OSError, RuntimeError):
            return p

    read = {real(p) for p in (paths.overlay("risk"), paths.overlay("safety")) if p}
    d = paths.checkout_overlay_dir()
    return [f for f in sorted(d.glob("*.local.yaml")) if real(f) not in read] if d.is_dir() else []


def _overlay_rows() -> list[tuple[str, bool, str]]:
    problems = overlay_problems()
    lint = ("overlay lint", not problems, "; ".join(problems) if problems else
            ("risk/safety overlay in " + str(paths.config_dir() / "local") if (paths.overlay("risk") or paths.overlay("safety")) else "no overlay in " + str(paths.config_dir() / "local")))
    unread = unread_overlay_files()
    here = paths.config_dir() / "local"
    files = ("overlay files", not unread,
             "; ".join(f"{f} is not read by JADE (it reads {here / f.name}" + ("" if f.name in ("risk.local.yaml", "safety.local.yaml") else "; no policy has a file of that name") + "): "
                       "an edit there changes nothing and policy_version does not show it" for f in unread)
             if unread else f"no unread *.local.yaml in {paths.checkout_overlay_dir()}")
    return [lint, files]


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
        if isinstance(args, list) and len(args) >= 2 and args[-1] == "maintain" and os.path.basename(str(args[0])) in {"jade", os.path.basename(jade_bin())} and is_jade_program(str(args[0])):
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


def _version_tuple(v: str) -> tuple[int, ...] | None:
    m = re.match(r"\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", v or "")
    return tuple(int(x or 0) for x in m.groups()) if m else None


def _code_row() -> tuple[str, bool, str]:
    """Running version and commit. Fails only when a checkout the human names (JADE_CHECKOUT or config `checkout`) has a newer pyproject version than the
    code that is running: an installed tool is not updated by a merge (W-50, docs/08 1)."""
    import jade

    commit = paths.code_commit()
    detail = f"jade {jade.__version__}" + (f", commit {commit} (running from a checkout)" if commit else ", installed package (no git checkout)")
    co = paths.checkout_dir()
    if co is None:
        return ("code version", True, detail + "; no checkout named (set JADE_CHECKOUT or config `checkout`), so staleness is not checked")
    try:
        import tomllib

        theirs = str(tomllib.loads((co / "pyproject.toml").read_text())["project"]["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return ("code version", True, detail + f"; cannot read a version from {co / 'pyproject.toml'} (informational)")
    a, b = _version_tuple(jade.__version__), _version_tuple(theirs)
    if a is None or b is None:
        return ("code version", True, detail + f"; checkout {co} has version {theirs}, not comparable (informational)")
    if a < b:
        return ("code version", False, detail + f"; the checkout {co} is at {theirs}, newer than what runs: re-install from it with "
                f"`uv tool install --reinstall {shlex.quote(str(co))}`")
    head = paths.git_commit(co)
    note = f"matches checkout {co} ({theirs})" if a == b else f"newer than checkout {co} ({theirs})"
    if a == b and head and commit != head:
        note += f", but its HEAD is {head} (informational: re-install to run it)"
    return ("code version", True, f"{detail}; {note}")


# ---- ledger integrity ------------------------------------------------------------------------------
_ID_SHAPE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MAX_INTEGRITY_LISTED = 10


def integrity_problems(db: ledger.Ledger) -> tuple[int, int, list[str]]:
    """Every permit row and improvement candidate row, validated against its schema (W-57). Returns the number of rows of each kind and one
    line per bad row: the id and the kind of fault, never the stored content (an id that is not id-shaped is shown as `<malformed id>`)."""
    from . import schemas

    counts, bad = [], []
    for label, name, table, col in (("permit", "permit", "permits", "permit_id"), ("improvement", "improvement", "improvements", "candidate_id")):
        rows = db.all(f"SELECT {col} AS id, doc_json{', state' if table == 'permits' else ''} FROM {table} ORDER BY {col}")  # noqa: S608 - constants above
        counts.append(len(rows))
        for r in rows:
            if table == "permits" and r.state in ("REVOKED", "EXPIRED", "CONSUMED"):
                continue   # never usable again: nothing for the human to act on, and the remedy (revoke) leaves the row in the table
            try:
                schemas.load(name, r.doc_json, id_field=col, id_value=r.id)
            except schemas.Unreadable as e:
                bad.append(f"{label} {r.id if isinstance(r.id, str) and _ID_SHAPE.fullmatch(r.id) else '<malformed id>'} ({e})")
    return counts[0], counts[1], bad


def _integrity_row() -> tuple[str, bool, str]:
    try:
        db = ledger.open()
        try:
            n_permits, n_improvements, bad = integrity_problems(db)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 - a health probe reports every failure
        return ("ledger integrity", False, repr(e))
    if not bad:
        return ("ledger integrity", True, f"{n_permits} permit row(s) and {n_improvements} improvement row(s) match their schemas")
    shown = "; ".join(bad[:MAX_INTEGRITY_LISTED]) + (f"; and {len(bad) - MAX_INTEGRITY_LISTED} more" if len(bad) > MAX_INTEGRITY_LISTED else "")
    return ("ledger integrity", False, f"{len(bad)} row(s) fail validation, never usable: {shown}. Revoke a permit with `jade permit revoke <id>`")


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
        from . import route

        problems = route.policy_problems()
        lim = None if problems else route.limits()
        rows.append(("routing policy", not problems, "; ".join(problems) if problems else f"{len(policy.routing()['preflight'])} checks, at most {lim.max_questions} questions "
                     f"per request, {lim.max_parallel} requests in flight, deadline {lim.deadline_s:g} s"))
    except Exception as e:  # noqa: BLE001
        rows.append(("routing policy", False, repr(e)))
    rows.append(_integrity_row())
    rows.append(_code_row())
    rows += _overlay_rows()
    try:
        bad = questions.validate_registry()
        rows.append(("question registry", not bad and bool(questions.entries()), f"{len(questions.entries())} entries" + (f", lint: {bad}" if bad else "")))
    except Exception as e:  # noqa: BLE001
        rows.append(("question registry", False, repr(e)))
    st = hooks_status()
    missing = [k for k, v in st.items() if not v]
    rows.append(("hooks installed", not missing, "all four" if not missing else f"missing or out of date: {', '.join(missing)}: run `jade hooks install` in a terminal"))
    if not missing:
        problems = wiring_problems()
        rows.append(("hook wiring", not problems, "binary runs, hooks enabled, a synthetic event is decided" if not problems else "; ".join(problems)))
    shadow = shadow_problems()
    try:
        scanned = str(Path.cwd())
    except OSError:
        scanned = "(no current directory)"
    rows.append(("settings shadowing", not shadow, f"no managed or plugin settings file, and no project settings file under {scanned} up to its repo root, disables or shadows the hooks "
                 "(run `jade check` from the project you work in to scan that one)" if not shadow else "; ".join(shadow)))
    gaps = pretool_matcher_gaps()
    if gaps is not None:
        rows.append(("pretool matcher", not gaps, "covers every gated tool" if not gaps else f"does not cover {', '.join(gaps)}: re-run `jade hooks install` in a terminal"))
    rows.append(_gate_mode_row())
    rows.append(_level_row())
    rows.append(_overrides_row())
    rows.append(_ack_audit_row())
    rows.append(_permission_row())
    rows.append(_approval_row())
    rows.append(tool_coverage_row())
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


ACK_AUDIT_DAYS = 7
ACK_STORM_MIN = 5   # agent-recorded `dropped` answers in the window before the share is judged


def _ack_audit_row() -> tuple[str, bool, str]:
    """W-59 (docs/05 T24): what agents said about answers that were not a yes. Fails on a storm of `dropped` (more than half of the agent acks, at least
    ACK_STORM_MIN) or on any ack or label attempt refused in the window, because both are the first sign of an agent clearing a reminder it did not act on.
    Rows tagged JADE_SOURCE=test are counted: the tag hides rows from statistics, not from this audit."""
    try:
        db = ledger.open(timeout=2)
        try:
            since = ts(now() - timedelta(days=ACK_AUDIT_DAYS))
            by: dict[str, int] = {}
            for r in db.all("SELECT next_state AS d, detail_json FROM events WHERE kind='ACK' AND actor='agent' AND ts>=?", (since,)):
                by[r.d] = by.get(r.d, 0) + int(json.loads(r.detail_json or "{}").get("count", 0))
            refused = {r.kind + (f" ({r.why})" if r.why else ""): r.n for r in db.all(
                "SELECT kind, json_extract(detail_json,'$.reason') AS why, COUNT(*) AS n FROM events WHERE kind IN ('ACK_REFUSED','LABEL_REFUSED') AND actor='agent' AND ts>=? "
                "GROUP BY kind, why", (since,))}
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 - a health probe reports every failure
        return ("ack audit", False, repr(e))
    total, dropped = sum(by.values()), by.get("dropped", 0)
    storm = dropped >= ACK_STORM_MIN and dropped * 2 > total
    detail = (f"last {ACK_AUDIT_DAYS} days: agents acknowledged {total} answer(s) (dropped {dropped}" + "".join(f", {k} {v}" for k, v in sorted(by.items()) if k != "dropped") + "); "
              + (f"refused attempts: {', '.join(f'{k} x{v}' for k, v in sorted(refused.items()))}" if refused else "no refused attempts"))
    if storm:
        detail += ". More than half were dropped: the agent is clearing the Stop-hook reminder without acting on it (docs/05 T24). Read the ACK events and the answers"
    return ("ack audit", not storm and not refused, detail)


TOOL_LIST_MAX = 12      # names listed per bucket before "+N more"
TOOL_WINDOW_DAYS = 90   # the ledger's retention window; an unnamed tool is recorded once, so it is read without one


def _names(tools) -> str:
    tools = sorted(tools)
    return ", ".join(tools[:TOOL_LIST_MAX]) + (f", +{len(tools) - TOOL_LIST_MAX} more" if len(tools) > TOOL_LIST_MAX else "")


def tool_coverage_row() -> tuple[str, bool, str]:
    """W-36 (docs/05 T18): tools no rule names, from the names the hook has recorded (events gated, would_block, safety_block and tool_unnamed) and the
    built-in lists in jade/policy.py. Informational, never a failure. Names and counts only: an argument is never read here or stored by the hook.
    The ledger holds no name for a call below the gate that no rule names, except the one event per unnamed MCP tool, and none for a built-in tool the
    matcher does not name: the host never sends those to the hook, so `ungated` lists only built-in names an operator-widened matcher let through."""
    from . import policy

    try:
        db = ledger.open(timeout=2)
        try:
            tools: set[str] = set()
            since = ts(now() - timedelta(days=TOOL_WINDOW_DAYS))
            for r in db.all("SELECT entity_id, kind, detail_json FROM events WHERE (kind='tool_unnamed' OR (kind IN ('gated','would_block','safety_block') AND ts>=?)) AND "
                            + ledger.live(), (since,)):
                try:
                    name = r.entity_id if r.kind == "tool_unnamed" else json.loads(r.detail_json or "{}").get("tool")
                except (ValueError, AttributeError):
                    continue
                if isinstance(name, str) and name:
                    tools.add(name)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 - informational: an unreadable ledger is the ledger row's failure, not this one's
        return ("tool coverage", True, f"unavailable ({type(e).__name__})")
    if not tools:
        return ("tool coverage", True, "no tool name recorded yet (the hook records gated tools and, once each, MCP tools no rule names)")
    gaps: dict[str, set[str]] = {}
    for t in tools:
        why = policy.tool_coverage(t)
        if why:
            gaps.setdefault(why, set()).add(t)
    mcp = sum(policy.is_mcp(t) for t in tools)
    parts = [f"{len(tools)} tool name(s) recorded ({len(tools) - mcp} built-in, {mcp} MCP)"]
    for rid, what in (("M-99", "MCP tool(s) no rule names (medium, recorded, not gated)"),
                      ("M-07", "MCP tool(s) only the verb-in-name heuristic covers (high, gated; add a specific rule)"),
                      ("ungated", "built-in tool(s) nothing gates or decides (add to GATED_BUILTIN or UNGATED_BUILTIN)")):
        if rid in gaps:
            parts.append(f"{len(gaps[rid])} {what} [{rid}]: {_names(gaps[rid])}")
    if not gaps:
        parts.append("no unnamed tool")
    return ("tool coverage", True, "; ".join(parts))


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


def no_prompt_gated_calls(days: int = 7) -> tuple[int, int]:
    """(gated calls seen, of those with no host prompt) in the last `days` days; (0, 0) when the ledger cannot be read."""
    from . import hooks

    try:
        db = ledger.open(timeout=2)
        try:
            rows = db.all("SELECT detail_json FROM events WHERE kind IN ('gated','safety_block') AND " + ledger.live() + " AND ts>=?", (ts(now() - timedelta(days=days)),))
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - informational and a pre-flight: an unreadable ledger is reported by its own check row
        return 0, 0
    seen = no_prompt = 0
    for r in rows:
        d = json.loads(r.detail_json or "{}")
        seen += 1
        no_prompt += bool(hooks.no_host_prompt(d.get("permission_mode"), str(d.get("tool") or "")))
    return seen, no_prompt


def _permission_prompt_detail() -> str:
    """JADE's gate is the only gate whenever the host shows no permission prompt; say so, and say how often that was the case here."""
    text = ("JADE's gate is the only gate when the host runs with permission_mode bypassPermissions or auto, with acceptEdits (file edits), "
            "headless (`claude -p`) or in unattended subagents: the host prompt is not an independent backstop there (docs/05 T18)")
    seen, no_prompt = no_prompt_gated_calls()
    text += f". Last 7 days: {no_prompt} of {seen} gated call(s) ran with no host prompt" if seen else ". Last 7 days: no gated calls recorded"
    return text


def _approval_row() -> tuple[str, bool, str]:
    """W-38 / W-89: which backend approves high and critical permits, and whether its trust anchor can be used."""
    state, why = signing.anchor_state()
    if state == "untrusted":
        return ("approval backend", False, f"signed, but the trust anchor is not usable, so no high or critical permit can be used: {why}")
    if state == "ok":
        return ("approval backend", True, f"signed: high and critical permits need a signature from the approver account (anchor {signing.anchor_path()})")
    if paths.config().get("approval_backend") == "signed":
        return ("approval backend", False, f"config asks for the signed backend but {why}: no high or critical permit can be used until the approver is installed (docs/10)")
    seen, no_prompt = no_prompt_gated_calls()
    note = f"; {no_prompt} of {seen} gated call(s) in the last 7 days had no host prompt" if seen else ""
    return ("approval backend", True, f"tty: a terminal approves high and critical permits, which a same-user process can imitate (docs/05 T19, docs/10){note}")


def print_check(rows) -> int:
    for name, ok, detail in rows:
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: {detail}")
    failed = [n for n, ok, _ in rows if not ok]
    if failed:
        print(f"jade check: {len(failed)} failed ({', '.join(failed)})", file=sys.stderr)
    return 1 if failed else 0
