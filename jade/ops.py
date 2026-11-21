"""Operations: champion/challenger pins, drift detection, retention, wiring checks, harness integration."""

from __future__ import annotations

import contextlib
import json
import math
import os
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

from . import ledger, memory, paths, policy, signing
from .util import is_human_tty, now, parse_ts, show_safely, ts, user


def pretool_matcher() -> str:
    """Every built-in tool the gate assesses (policy.GATED_BUILTIN) and every MCP tool. Agent/Task are not listed: they need no gate,
    and the tool calls a subagent makes reach the same hook."""
    return "|".join((*policy.GATED_BUILTIN, "AskUserQuestion", "mcp__.*"))


def posttool_matcher() -> str:
    """The calls that can run under a permit (policy.POST_TOOLS and every MCP tool): PostToolUse closes the decision of each (W-40)."""
    return "|".join((*policy.POST_TOOLS, "AskUserQuestion", "mcp__.*"))


# PreToolUse: a timeout above askdialog.DIALOG_WAIT_S + ALARM_MARGIN_S, because the hook waits for a macOS dialog (W-100c); the others answer at once
HOOKS = {"PreToolUse": (pretool_matcher(), "pretool", 130), "PostToolUse": (posttool_matcher(), "posttool", 10),
         "PostToolUseFailure": (posttool_matcher(), "posttool-failure", 10), "Stop": ("", "stop", 10), "SessionStart": ("", "session-start", 10)}
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
def check_drift(path, resolved_model: str, expected: list[str], db: ledger.Ledger | None = None):
    """None when the resolved model is expected. Otherwise a MODEL_DRIFT finding, recorded once per new string.
    Drift is informational (decision Q6): the new snapshot is a new calibration group at default thresholds.
    `db` is the ledger the call itself uses: it is written to and left open. Without it, `path` (None: the JADE home) is opened and closed here (V6-11: a run
    with a ledger of its own must not write to the home)."""
    if resolved_model in expected:
        return None
    own = db is None
    db = db or ledger.open(_dir(path))
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
        if own:
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
            served = questions.default_version(q)
            if v < served:   # an older copy kept beside the served version (a rollback target), not a new version to pin
                title = f"QUESTION_DRIFT: {q} holds version {v} beside the served version {served}"
                action = (f"Informational. Version {served} is the version served; version {v} is kept as the copy to roll back to. "
                          "Nothing to pin (docs/07 section 1).")
            else:
                title = f"QUESTION_DRIFT: {q} now has version {v}"
                action = ("Informational. The new version is its own calibration group at default thresholds; check its template and pin it as champion or "
                          "challenger when reviewed (docs/07 section 1).")
            item = {"id": wid, "title": title, "type": "risk", "priority": "P3", "status": "open", "owner": "human",
                    "next_action": action, "retry_or_revisit_trigger": "on_session_start", "created_at": ts()}
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
# Events whose detail is the record itself, not project text, so retention keeps it (V6-28 review): `jev` is the value the human set (cleared, `jade jev on` read `off`);
# `jev_accepted` and `jev_allowed` hold only hashes, rule ids and session ids (the command text of a `jev_allowed` label is removed above), and they are what links a
# Jev yes to the owner's later deny (cleared, a false allow vanished after 90 days and the deny gate opened by itself).
KEPT_DETAIL = ("jev", "jev_accepted", "jev_allowed")


PURGED_ASK = json.dumps({"purged": True})   # `asks.question_json` is NOT NULL and a reader parses it: a marker, not NULL
PURGE_TABLES = ("jev_calls", "decisions", "permits", "events", "asks", "human_answers")


class _DryRun(Exception):
    """Raised inside the purge transaction of a dry run so that it rolls back."""


def _purge_rows(con, cutoff: str, *, ask_text: bool) -> dict[str, int]:
    """Clear the text, inside the caller's transaction, and count the rows cleared per table. One place, so a dry run and a purge cannot disagree."""
    n = dict.fromkeys(PURGE_TABLES, 0)
    n["jev_calls"] = con.execute("UPDATE jev_calls SET state_text=NULL, question_text=NULL WHERE ts<? AND (state_text IS NOT NULL OR question_text IS NOT NULL)",
                                 (cutoff,)).rowcount
    for r in con.execute("SELECT decision_id, doc_json FROM decisions WHERE updated_at<? AND json_extract(doc_json,'$.objective') IS NOT ?", (cutoff, PURGED)).fetchall():
        doc = json.loads(r["doc_json"])
        doc["objective"] = PURGED
        doc["candidate_actions"] = [{**act, "description": PURGED} if isinstance(act, dict) else act for act in doc.get("candidate_actions", [])]
        doc["constraints"], doc["assumptions"], doc["evidence_refs"] = [], [], []
        con.execute("UPDATE decisions SET doc_json=? WHERE decision_id=?", (json.dumps(doc, ensure_ascii=False), r["decision_id"]))
        n["decisions"] += 1
    for r in con.execute("SELECT permit_id, scope FROM permits WHERE issued_at<? AND (state NOT IN ('PENDING','ISSUED') OR expires_at<=?) AND scope LIKE '{%'",
                         (cutoff, ts())).fetchall():
        try:
            ctx = json.loads(r["scope"])
        except ValueError:
            continue
        if isinstance(ctx, dict) and "subject" in ctx and ctx["subject"] != PURGED:
            keep = {k: v for k, v in ctx.items() if k in ("tool", "length", "rule", "bound", "session_id", "agent_id", "permission_mode", "hash_cwd")}
            con.execute("UPDATE permits SET scope=? WHERE permit_id=?", (json.dumps({**keep, "subject": PURGED, "purged": True}), r["permit_id"]))
            n["permits"] += 1
    n["events"] = con.execute("UPDATE events SET detail_json=json_remove(detail_json,'$.label') WHERE kind='jev_allowed' AND ts<? AND json_extract(detail_json,'$.label') IS NOT NULL",
                              (cutoff,)).rowcount   # the label is command text; the keys beside it are hashes and stay (below)
    n["events"] += con.execute("UPDATE events SET detail_json=NULL WHERE ts<? AND detail_json IS NOT NULL AND kind NOT IN ('purge', ?, ?, ?)", (cutoff, *KEPT_DETAIL)).rowcount
    if ask_text:
        # The chat question (it holds a "Command:" block), the one-line label and the scope labels of an ask that is over: never an ask still open.
        n["asks"] = con.execute(
            "UPDATE asks SET question_json=?, label=CASE WHEN label IS NULL THEN NULL ELSE ? END, scopes_json=CASE WHEN scopes_json IS NULL THEN NULL ELSE '[]' END "
            "WHERE created_ts<? AND NOT (status IN ('PENDING','SHOWN') AND expires_ts>?) "
            "AND (question_json<>? OR (label IS NOT NULL AND label<>?) OR (scopes_json IS NOT NULL AND scopes_json<>'[]'))",
            (PURGED_ASK, PURGED, cutoff, ts(), PURGED_ASK, PURGED)).rowcount
        # The label of an answer is what `jade review confirm` and `jade knows` print. A standing answer keeps it while it decides (30 days after its last use,
        # 180 at most), and so does an allow the human has not confirmed yet (it can be confirmed for 180 days); the keys, the answer and the rule stay for good.
        cap, use = memory.valid_since()
        n["human_answers"] = con.execute(
            "UPDATE human_answers SET label=CASE WHEN label IS NULL THEN NULL ELSE ? END, scope_label=CASE WHEN scope_label IS NULL THEN NULL ELSE ? END "
            "WHERE ts<? AND ((label IS NOT NULL AND label<>?) OR (scope_label IS NOT NULL AND scope_label<>?)) "
            "AND NOT (answer='allow' AND strength='strong' AND COALESCE(source,'')<>'test' AND confirmed_ts>=? AND COALESCE(last_used_ts, confirmed_ts)>=?) "
            "AND NOT (answer='allow' AND strength='hint' AND ts>=?)",
            (PURGED, PURGED, cutoff, PURGED, PURGED, cap, use, cap)).rowcount
    return n


def purge_report(db: ledger.Ledger, older_than_days: int = 90, *, ask_text: bool = False, dry_run: bool = False) -> dict[str, int]:
    """The retention purge, with the rows cleared per table. `dry_run` runs the same statements and rolls them back, so the ledger, its audit event and its file
    are as they were. `ask_text` also clears the chat question and the labels of ended asks and of answers (DX-H2); it is off until the owner says yes, so
    `jade maintain` never turns it on."""
    cutoff = ts(now() - timedelta(days=older_than_days))
    out: dict[str, int] = {}
    try:
        with db.tx() as con:
            out = _purge_rows(con, cutoff, ask_text=ask_text)
            if dry_run:
                raise _DryRun
            if (total := sum(out.values())):
                db.event("ledger", "purge", guard=f"older than {older_than_days} d", detail={"rows": total, "tables": {t: c for t, c in out.items() if c}}, con=con)
    except _DryRun:
        return out
    if sum(out.values()):
        db.scrub()   # the cleared text must not stay readable in the file's free pages or the write-ahead log
    return out


def purge(db: ledger.Ledger, older_than_days: int = 90, *, ask_text: bool = False) -> int:
    """Remove stored project text older than the retention window and keep every metric (decision Q7): the state and question text of Jev
    calls, the objective and command text of decisions, the command a permit carried (never one still open: PENDING or ISSUED and not yet expired) and the detail of
    events (not `KEPT_DETAIL`). Statuses, hashes, risk levels, timings, costs and probabilities stay. With `ask_text` (the owner's yes, DX-H2) the chat question and the labels of ended
    asks and of answers go too. Returns the number of rows cleared."""
    return sum(purge_report(db, older_than_days, ask_text=ask_text).values())


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


_HOOK_CMD = re.compile(r"^(?P<bin>.*?)\s+hook\s+(?P<sub>pretool|posttool-failure|posttool|stop|session-start)\s*$", re.S)


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


HOST_UNREAD = object()   # `host` argument default: read the version with `host_version()`; `jade hooks install` reads it once and passes it on (review of W-140)


def _wants_on_failure(event: str, host=HOST_UNREAD) -> bool:
    """Only the PreToolUse hook fails closed: a crashed or hung gate then blocks the call instead of letting it run (probe cell 9). Older hosts ignore the field.
    `host` is the version `host_version()` returned (None: unknown); left out, it is read here."""
    if event != "PreToolUse":
        return False
    v = host_version() if host is HOST_UNREAD else host
    return v is not None and v >= ON_FAILURE_MIN_HOST


def _entry_current(event: str, g: dict, h: dict, host=HOST_UNREAD) -> bool:
    """Is one installed JADE entry what `hooks install` writes now? A timeout may be longer than ours (an operator's choice), never shorter, and `onFailure`
    is required on PreToolUse when the host honours it. Matchers are not compared here: `pretool_matcher_gaps` (PreToolUse, where a wider matcher is fine)
    and `wiring_problems` (the other events) report them with the exact fix."""
    _matcher, _sub, want_timeout = HOOKS[event]
    timeout = h.get("timeout")
    if not isinstance(timeout, (int, float)) or timeout < want_timeout:
        return False
    return not (_wants_on_failure(event, host) and h.get("onFailure") != "block")


def hooks_status(host=HOST_UNREAD) -> dict[str, bool]:
    data = _read_quiet(settings_path())
    current = {(event, parsed.sub) for event, g, h, parsed in _jade_hooks(data) if event in HOOKS and _entry_current(event, g, h, host)}
    gaps = pretool_matcher_gaps()
    out = {event: (event, sub) in current for event, (_m, sub, _t) in HOOKS.items()}
    if gaps:
        out["PreToolUse"] = False
    return out


PRETOOL_PROBE_TOOLS = (*policy.GATED_BUILTIN, "AskUserQuestion", "mcp__server__tool")   # every tool the PreToolUse matcher must cover; the last stands for every MCP tool


def _matcher_covers(m: str, tool: str) -> bool:
    """An empty matcher covers every tool. A matcher that is not a valid regex covers nothing (review of W-141): JADE cannot tell what the host makes of it,
    so it is never counted as a check."""
    if not m:
        return True
    try:
        return re.fullmatch(m, tool) is not None
    except re.error:
        return False


def pretool_matcher_gaps() -> list[str] | None:
    """Gated tools the installed PreToolUse matcher does not cover (an install made before the matcher grew, or a matcher that is not a valid regex). None
    when the hook is not installed."""
    data = _read_quiet(settings_path())
    matchers = [g.get("matcher", "") for event, g, _h, p in _jade_hooks(data) if event == "PreToolUse" and p.sub == "pretool"]
    if not matchers:
        return None
    return [tool for tool in PRETOOL_PROBE_TOOLS if not any(_matcher_covers(m, tool) for m in matchers)]


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


def hooks_install(stdin_isatty: bool | None = None, host=HOST_UNREAD) -> Path | None:
    """Add every JADE hook of HOOKS to the Claude settings file, keeping every other setting and hook. Returns the backup path (None when there was no file).
    `host` is the Claude Code version when the caller already read it (`host_version()`); left out, it is read here."""
    _require_human("jade hooks install", stdin_isatty)
    p = settings_path()
    data = _load_settings(p)
    backup = backup_file(p) if p.exists() else None
    _strip_jade_hooks(data)
    hooks = data.setdefault("hooks", {})
    for event, (matcher, sub, timeout) in HOOKS.items():
        entry = {"hooks": [{"type": "command", "command": hook_command(sub), "timeout": timeout}]}
        if _wants_on_failure(event, host):
            entry["hooks"][0]["onFailure"] = "block"
        if matcher:
            entry["matcher"] = matcher
        hooks.setdefault(event, []).append(entry)
    atomic_write(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return backup


def hooks_install_with_summary(stdin_isatty: bool | None = None) -> tuple[Path | None, list[tuple[str, bool | None, str]]]:
    """`jade hooks install`: the human check first (a refused call runs nothing), then one `claude --version` for the write and for the summary rows."""
    _require_human("jade hooks install", stdin_isatty)
    host = host_version()
    backup = hooks_install(stdin_isatty, host=host)
    return backup, install_summary_rows(host=host)


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


LEVEL_CHOICES = ("unset", "off", "low", "medium", "high")   # low and medium use what you confirmed (jade/memory.py); Jev joins them in a later release (docs/12)


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
        raise ValueError(f"level must be one of {', '.join(LEVEL_CHOICES)}, not {value!r}")
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


JEV_CHOICES = ("unset", "on", "off")


def jev_effective(db: ledger.Ledger | None = None) -> str:
    """`on` or `off` (V6-28): whether a model may be asked at all. The config key and the newest `jev` ledger event are read together and `paths.jev` fails
    closed; a ledger that cannot be read leaves `on` unprovable, so it reads `off`. Pass the open ledger inside the hook to use its connection."""
    own = db is None
    try:
        if own:
            db = ledger.open(timeout=5)
        recorded = ledger.last_setting(db, "jev", "jev")
    except Exception:  # noqa: BLE001 - an unreadable ledger records nothing a person can be believed by
        recorded = ledger.UNREADABLE_RECORD
    finally:
        if own and db is not None:
            db.close()
    return paths.jev(recorded)


def set_jev(value: str, stdin_isatty: bool | None = None) -> None:
    """`jade jev on|off|unset`: human terminal only (SI-09). The change is journaled as a `jev` event first, then the key is written, so the file always has a human
    record behind it. `off` and `unset` are always allowed. `on` is refused when the config already holds a value that no `jade jev` command recorded (written
    some other way): the human clears it with `unset` first."""
    _require_human("jade jev", stdin_isatty)
    if value not in JEV_CHOICES:
        raise ValueError(f"jev must be one of {', '.join(JEV_CHOICES)}, not {value!r}")
    cfg = paths.config()
    db = ledger.open(timeout=5)
    try:
        if value == "on" and "jev" in cfg and cfg["jev"] != "off" and cfg["jev"] != ledger.last_setting(db, "jev", "jev"):
            raise HumanOnly("the config holds a jev value that no `jade jev` command recorded (it was written some other way): run `jade jev unset`, check "
                            "the config directory, then set it again")
        db.event("jev", "jev", guard=f"jade jev {value}", actor=f"human:{user()}", detail={"jev": None if value == "unset" else value})
    finally:
        db.close()
    if value == "unset":
        cfg.pop("jev", None)
    else:
        cfg["jev"] = value
    atomic_write(paths.config_dir() / "config.json", json.dumps(cfg, indent=1) + "\n")


def autonomy_label(level: str | None, jev: str) -> str:
    """The display name of a `(level, jev)` pair (V6-28). Never stored: a stored name beside the keys could drift, and drift is a quiet way to raise autonomy."""
    if level is None:
        return "legacy"
    if level == "off":
        return "observe"
    return {("high", "off"): "MANUAL", ("medium", "off"): "ASSISTED", ("medium", "on"): "BALANCED", ("low", "on"): "FAST"}.get((level, jev), "custom")


def _jev_row() -> tuple[str, bool | None, str]:
    """`jade check` row `jev`: the value in force and where it comes from. A failure is a file that does not match the human's record; a warning is `on` with no
    signed approval backend (V6-12)."""
    cfg, lv = paths.config(), paths.level()
    try:
        db = ledger.open(timeout=5)
        try:
            recorded = ledger.last_setting(db, "jev", "jev")
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return ("jev", False, "off: the ledger cannot be read, so `on` cannot be shown to be the human's choice")
    eff = paths.jev(recorded)
    if "jev" in cfg:
        raw = cfg["jev"]
        if not isinstance(raw, str) or raw not in paths.JEV_VALUES:
            return ("jev", False, f"off: the config value {raw!r} is not on or off, so it reads off; `jade jev unset` clears it")
        if raw == "on" and recorded != "on":
            return ("jev", False, "off: the config says on, but no `jade jev on` recorded it (written some other way?); run `jade jev unset`, look at the config directory, set it again")
        if recorded is not None and recorded != raw:
            return ("jev", False, f"{eff}: the config says {raw}, but the last `jade jev` event says {recorded!r}; run `jade jev unset`, look at the config directory, set it again")
        where = "the last `jade jev` event, which the config matches" if recorded is not None else "the config (a new install writes off; no `jade jev` event)"
    elif recorded is not None:
        return ("jev", False, f"off: the last `jade jev` event says {recorded!r}, but the key is gone from the config; run `jade jev unset`, look at the config directory, set it again")
    else:
        where = f"the default (no key and no `jade jev` event; on at level low or medium, off at every other level; level {lv or 'unset'})"
    label = autonomy_label(lv, eff)
    base = f"{eff} ({label}) from {where}"
    if eff == "on" and not signing.signed_backend():
        return ("jev", None, base + "; a model may be asked, and there is no signed approval backend (docs/10), so a terminal approval can be forged by a same-user process; "
                             "`jade jev off` stops every model call")
    return ("jev", True, base)


OFF_MEANS = ("off is observe plus floor: most rules only log, but SI-07, SI-08, SI-09 to SI-12 and writes to JADE's own wiring (HO-wiring, HO-checkout-install, "
             "HO-settings-shadow, HO-state-path) block. Use `jade level high` to have JADE ask about high-risk calls.")


def _level_row() -> tuple[str, bool, str]:
    raw, lv = paths.config().get("level"), paths.level()
    if raw is None:
        return ("level", True, "unset: JADE behaves as release 0.4.0 (`jade level high` turns on in-chat approval, docs/12)")
    if lv is None:
        return ("level", False, f"the config level {raw!r} is not off, low, medium or high, so it counts as unset; `jade level unset` clears it")
    if raw != _recorded_level():
        return ("level", False, f"{lv}, but no `jade level` command recorded it (written some other way?); run `jade level unset`, look at the config directory, set it again")
    if lv == "off":   # V6-10: not a failure (the human chose it), but not "ok": most rules only log. V6-16: the floor blocks
        return ("level", None, "off (observe plus floor): most rules only log; SI-07, SI-08 and writes to JADE's own wiring block, as do SI-09 to SI-12 "
                               "(the row `what blocks` lists them)")
    return ("level", True, f"{lv}")


def blocking_summary(mode: str | None = None, level: str | None | bool = False) -> tuple[bool | None, str]:
    """What the PreToolUse hook blocks for a gate mode and level pair (default: the ones in force), as rule ids only, never a command (V6-10).
    True when every safety invariant blocks and every high or critical risk call waits for a human; None (a warning, never a failure) otherwise. It mirrors
    `hooks.pretool`, `hooks._verdict` and `hooks._gate`: gate off returns before any verdict; the governance invariants block once a level is set; level off
    and advisory gate mode make the other invariants and the risk gate log only; at level low and medium `memory.step` (a confirmed answer) and `proxy.step`
    (Jev, plain high calls only, never egress, only with the `jev` key on and once the deny gate is met) may let a call run with no human; at level high Jev is shadow
    only, and with the key off nothing is sent to a model at all. It does not
    look at the hooks: `_what_blocks_row` does."""
    from . import hooks

    mode = paths.gate_mode() if mode is None else mode
    level = paths.level() if level is False else level
    gov = sorted(hooks.GOVERNANCE_INVARIANTS)
    try:
        ids = sorted({str(i["id"]) for i in policy.safety()["invariants"]} | set(gov))
    except Exception:  # noqa: BLE001 - the `policies load` row reports the failure
        ids = None
    if mode == "off":
        return (None, "gate mode off: nothing blocks; every safety invariant" + (f" ({', '.join(ids)})" if ids else "") + " and every risk rule is skipped, not even logged")
    if ids is None:   # pretool goes to _policy_failure: the invariant set is not known
        return (None, "unknown: the safety policy does not load (see the `policies load` row); the hook fails closed on calls that are not clearly harmless")
    soft = [i for i in ids if i not in gov]
    unset = "; SI-12 is active only once a level is set (until then R-13 rates those writes)"   # policy.py: SI-12 checks run only when paths.level() is set
    active = [i for i in ids if i != "SI-12"]
    if mode == "advisory" and level is None:
        return (None, "advisory gate mode, no level set: nothing blocks; every safety invariant (" + ", ".join(active) + ") and every risk rule only log" + unset)
    if mode == "advisory":
        return (None, "advisory gate mode: " + ", ".join(gov) + " block; " + ", ".join(soft) + " and every risk rule only log")
    if level == "off":   # V6-16: observe plus floor; hooks._verdict and hooks.pretool
        floor = sorted(hooks.OFF_FLOOR_INVARIANTS)
        firm = sorted(set(floor) | set(gov))
        return (None, "advisory: " + ", ".join(i for i in soft if i not in floor) + " and every risk rule (R-13 and R-16 too) only log; "
                + ", ".join(firm) + " block, and so does a write to JADE's own wiring (" + ", ".join(sorted(hooks.OFF_FLOOR_CLASSES)) + ") (level off, observe plus floor, docs/12)")
    if level is None:
        return (True, "every active safety invariant (" + ", ".join(active) + ") blocks; a high or critical risk call is held for a human approval (docs/12)" + unset)
    if level in ("low", "medium"):   # hooks._gate: memory.step, then proxy.step, run before the human is asked
        return (None, "every safety invariant (" + ", ".join(ids) + f") blocks; at level {level} a high or critical risk call runs without a human when a "
                      "confirmed answer covers it (`jade knows list`), and a plain high call runs without a human when Jev accepts it inside its band "
                      "(only with the `jev` key on, never for egress, and only once the deny gate is met: `jade review` shows the count); any other high or critical call is "
                      "held for a human (docs/12)")
    return (True, "every safety invariant (" + ", ".join(ids) + ") blocks; a high or critical risk call is held for a human approval (Jev decides nothing at "
                  "this level, docs/12)")


def list_b() -> tuple[dict, dict]:
    """List B of the floor (V6-17): what is advisory by design, for gate mode `off` and gate mode `advisory`, as `{"mode", "firm", "advisory"}` of safety invariant ids.
    The R rules and the HO classes are advisory in both modes and are not listed one by one. `jade check` prints it (`_list_b_row`) and the floor tests assert it."""
    from . import hooks

    every = sorted(str(i["id"]) for i in policy.safety()["invariants"])
    gov = sorted(hooks.GOVERNANCE_INVARIANTS)
    return ({"mode": "off", "firm": [], "advisory": every},
            {"mode": "advisory", "firm": gov, "advisory": [i for i in every if i not in gov]})


def _list_b_row() -> tuple[str, bool, str]:
    """What the owner chose to leave advisory by design (List B of the floor). Never a warning: it says what gate mode `off` and `advisory` mean, whatever is set now."""
    try:
        off, adv = list_b()
    except Exception as e:  # noqa: BLE001 - the `policies load` row reports the failure
        return ("advisory by design", True, f"unknown: the safety policy does not load ({type(e).__name__})")
    return ("advisory by design", True,
            f"gate mode off: nothing blocks ({', '.join(off['advisory'])}, every R rule and every HO class are skipped); "
            f"gate mode advisory: {', '.join(adv['firm'])} block once a level is set, and {', '.join(adv['advisory'])}, every R rule and every HO class only log "
            "(the floor of level `off` is another thing: it needs gate mode enforce)")


NOT_RUN_HERE = " (the hooks were not run here: `jade check` runs each one once to prove it decides)"


def _what_blocks_row(pretool_ok: bool | None = None, wiring: list[str] | None = None, probe: bool = True, host=HOST_UNREAD) -> tuple[str, bool | None, str]:
    """`blocking_summary` for the mode and level in force, unless the PreToolUse hook is missing (then nothing blocks) or the wiring has problems (then it is
    not known). A PreToolUse hook that is installed but out of date still runs (W-141): a matcher gap leaves only the named tools unchecked, and an old timeout
    or failure setting only weakens what a crashed or hung gate does; the row then says that and still says what blocks. `check` passes what it already found
    (the live wiring probe, run whenever a JADE PreToolUse entry is registered and printed as the `hook wiring` row), so the probe is not run twice and
    the row never points to a row that is not printed. With no `wiring`, `probe` says whether to run the live probe (True) or to read the static facts only
    (False, the install summary, W-140): the row then says the hooks were not run here and points to `jade check`, because a hook that crashes when run
    is seen only by the live probe."""
    probed = wiring is not None or probe   # `check` passes the live probe's result; the install summary passes nothing and probe=False
    where = "see the `hook wiring` row" if probed else "`jade check` names them in its `hook wiring` row"
    if pretool_ok is None:
        pretool_ok = hooks_status(host).get("PreToolUse", False)
    if not pretool_ok:
        gaps = pretool_matcher_gaps()
        if gaps is None:
            return ("what blocks", None, "nothing blocks: the PreToolUse hook is not installed; run `jade hooks install` in a terminal")
        if wiring is None:
            wiring = wiring_problems(probe=probe)   # `check` passes the live result it printed as the `hook wiring` row
        covered = [t for t in PRETOOL_PROBE_TOOLS if t not in gaps]
        if not covered:
            return ("what blocks", None, "nothing blocks: the PreToolUse matcher covers no gated tool (or is not a valid regex), so no call gets a check; "
                                         "run `jade hooks install` in a terminal")
        _ok, text = blocking_summary()
        if gaps:
            names = ", ".join("every MCP tool" if g == "mcp__server__tool" else g for g in gaps)
            checked = ", ".join("every MCP tool" if g == "mcp__server__tool" else g for g in covered)
            lead = (f"the PreToolUse matcher does not cover {names}: a call to those tools gets no check at all; run `jade hooks install` in a terminal; "
                    f"every other gated tool ({checked}) is checked as follows: ")
        else:
            lead = ("the PreToolUse hook is out of date (its timeout or failure setting) but still runs, so a crashed or hung gate may let a call run; "
                    "run `jade hooks install` in a terminal; when the gate runs it blocks as follows: ")
        if wiring:
            lead = f"unknown: the hook wiring has problems ({where}), so the hook may not run; " + lead
        return ("what blocks", None, lead + text + ("" if wiring or probed else NOT_RUN_HERE))
    if wiring is None:
        wiring = wiring_problems(probe=probe)
    ok, text = blocking_summary()
    if wiring:
        return ("what blocks", None, f"unknown: the hook wiring has problems ({where}), so the hook may not run; when it runs: " + text)
    return ("what blocks", ok, text + ("" if probed else NOT_RUN_HERE))


def parse_since(value: str) -> float:
    """`90m`, `24h` or `7d` as a number of seconds."""
    m = re.fullmatch(r"(\d+)([mhd])", (value or "").strip())
    if not m:
        raise ValueError(f"invalid window {value!r}: use 90m, 24h or 7d")
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]


def _shown_line(text: str | None) -> str:
    """A stored label on one line, every control, bidi and zero-width character written out (V6-09)."""
    return show_safely(text, one_line=True)


def review(since_s: float | None = None) -> str:
    """What the human answered to JADE's asks (read-only): counts by answer, strength, rule and channel. Confirming answers is a later, human-only step."""
    db = ledger.open(timeout=5)
    try:
        rows = db.human_answers(since_s=since_s, limit=100000)
    finally:
        db.close()
    window = "all time" if since_s is None else f"last {int(since_s // 3600)}h" if since_s >= 3600 else f"last {int(since_s // 60)}m"
    db = ledger.open(timeout=5)
    try:
        extra = review_lines(db, since_s) + friction_review_lines(db, since_s, window) + audit_review_lines(db, since_s)
    finally:
        db.close()
    if not rows:
        return "\n".join([f"no answers recorded ({window})", *extra])

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
    return "\n".join(lines + extra)


def wrong_rate_upper(wrong: int, n: int) -> float:
    """The 95% upper bound of the share of wrong runs among n marked ones: 3/n when none is wrong (the rule of three; 50 clean marks show the error rate is below
    about 1 in 17), the Wilson upper bound otherwise."""
    if n <= 0:
        return 1.0
    if wrong == 0:
        return min(1.0, 3 / n)
    z, p = 1.96, wrong / n
    return min(1.0, (p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n))


def _latest_marks(db, since: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in db.con.execute("SELECT entity_id, detail_json FROM events WHERE kind='review_mark' AND ts>=? AND " + ledger.LIVE.format(a="") + " ORDER BY id", (since,)):
        try:
            out[r["entity_id"]] = json.loads(r["detail_json"])["mark"]
        except (ValueError, KeyError, TypeError):
            continue
    return out


def friction_review_lines(db, since_s: float | None, window: str) -> list[str]:
    """V6-23: the effort side of the review, beside the correctness side: how often JADE asked, how long answers took, how many asks ran out, and what is known about
    whether the answers were right (marked runs, checked labels), or the plain statement that correctness is not measured (d11 checklist item 17)."""
    from . import readouts

    since = readouts.since_seconds(since_s)
    marks = _latest_marks(db, since)
    return readouts.friction_lines(db, since, window, len(marks), sum(1 for m in marks.values() if m == "wrong"), readouts.label_coverage(db, since)["fit_labelled"])


def audit_review_lines(db, since_s: float | None) -> list[str]:
    """V6-29: the audit of calls that ran without asking, with the false-allow rate only when it can be shown honestly (`audit.lines`)."""
    from . import audit

    return audit.lines(db, ts(now() - timedelta(seconds=since_s)) if since_s is not None else "", _shown_line)


def review_lines(db, since_s: float | None) -> list[str]:
    """What the standing answers are doing: how often a question was avoided, what the human marked wrong, and how far the answer count is from the 50 that a later
    step needs. Counts and rule ids only; no command text beyond the label the human already saw in `jade review confirm`."""
    live, since = ledger.LIVE.format(a=""), ts(now() - timedelta(seconds=since_s)) if since_s is not None else ""
    silent = db.con.execute(f"SELECT entity_id, detail_json, ts, kind FROM events WHERE kind IN ('memory_allowed','jev_allowed') AND ts>=? AND {live} ORDER BY id DESC", (since,)).fetchall()   # noqa: S608
    jev = db.con.execute("SELECT latency_ms, cost_usd FROM jev_calls WHERE question_id='q.human_proxy.would_allow' AND ts>=? AND COALESCE(source,'')<>'test' AND state='DONE' "
                         "AND dedup_of IS NULL AND error_kind IS NULL", (since,)).fetchall()
    asked = db.con.execute(f"SELECT COUNT(*) FROM human_answers WHERE COALESCE(ho,0)=0 AND ts>=? AND {live}", (since,)).fetchone()[0]   # noqa: S608
    from . import proxy

    plain_all = proxy.owner_answers(db)
    if not silent and not plain_all and not jev:
        return []
    marks = _latest_marks(db, since)
    decisions = {}
    for r in silent:
        try:
            decisions[json.loads(r["detail_json"])["decision_id"]] = r
        except (ValueError, KeyError, TypeError):
            continue
    marked = {d: m for d, m in marks.items() if d in decisions}
    wrong = sum(1 for m in marked.values() if m == "wrong")
    by_jev = sum(1 for r in silent if r["kind"] == "jev_allowed")
    share = 100 * len(silent) // (len(silent) + asked) if len(silent) + asked else 0
    who = f"calls that ran from standing answers, without asking you: {len(silent)}" if not by_jev else \
        f"calls that ran without asking you: {len(silent)} ({len(silent) - by_jev} from standing answers, {by_jev} by Jev)"
    out = [f"{who}; questions you answered: {asked}; {share}% of the ordinary high-risk calls ran without asking you"]   # V6-23: plain words for "silent share of plain-high calls"
    if marked:
        out.append(f"you marked {len(marked)} of these calls: {len(marked) - wrong} right, {wrong} wrong; we are 95% sure that no more than "
                   f"{100 * wrong_rate_upper(wrong, len(marked)):.0f}% of such calls are wrong")
    elif silent:
        out.append("you have not marked any of these calls yet: `jade review mark DECISION_ID right|wrong` in your terminal (wrong makes JADE ask that call, and its kind, again)")
    gate = proxy.deny_gate(db)
    out.append(f"ordinary high-risk calls you have answered (each call counted once): {plain_all} (a count of answers gates nothing: an always-allow model would agree with all of them)")
    out.append(f"Jev deny gate: {gate['denies']} of {proxy.MIN_DENIES} kinds of call you denied, from {gate['rules']} of {proxy.MIN_DENY_RULES} rules; {gate['samples']} of "
               f"{proxy.MIN_SAMPLES} shadow questions over {int(gate['days'])} of {proxy.MIN_SAMPLE_DAYS} days; {gate['false_allows']} false allow(s) (Jev said yes to a call you denied "
               f"or marked wrong)" + ("; met: with the jev key on at level low or medium, Jev now decides plain high calls inside its band; `jade jev off` stops it" if gate["ok"] else "; not met, so a model deciding for you stays off"))
    if gate["ok"] and gate["recall_bound"] is not None:
        out.append(f"no false allow among {gate['denies']} denies: the most that shows is a deny-recall of about {100 * gate['recall_bound']:.1f}% (95% lower bound, and only for "
                   "denies that Jev was asked about)")
    if jev:
        allowed = sum(1 for r in silent if r["kind"] == "jev_allowed")
        lat = sorted(r["latency_ms"] or 0.0 for r in jev)
        out.append(f"Jev for you: {len(jev)} call(s), {allowed} allowed, {len(jev) - allowed} not decided by Jev (shadow, abstain or reject); cost ${sum(r['cost_usd'] or 0.0 for r in jev):.4f}, median latency {lat[len(lat) // 2]:.0f} ms")
    for r in silent[:5]:
        did = json.loads(r["detail_json"]).get("decision_id") if r["detail_json"] else None
        if r["kind"] == "jev_allowed":
            det = json.loads(r["detail_json"])
            a = {"rule_id": det.get("rule"), "label": det.get("label")}
        else:
            a = db.con.execute("SELECT rule_id, label FROM human_answers WHERE answer_id=?", (r["entity_id"],)).fetchone()
        out.append(f"  {did}  [{a['rule_id'] if a else '?'}]  {_shown_line(a['label'] if a else '')}  {('(marked ' + marks[did] + ')') if did in marks else ''}".rstrip())
    return out


def review_mark(decision_id: str, mark: str, stdin_isatty: bool | None = None) -> str:
    """`jade review mark DECISION_ID right|wrong` (human terminal): a verdict on a call that ran from a standing answer or from your answer in the chat or a dialog.
    `right` is a hint and changes nothing. `wrong` retires the answer behind the call and every answer of the same exact call or kind, so JADE asks next time."""
    from . import memory

    _require_human("jade review mark", stdin_isatty)
    if mark not in ("right", "wrong"):
        raise ValueError("the mark must be right or wrong")
    db = ledger.open(timeout=5)
    try:
        row = db.con.execute("SELECT doc_json FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if row is None:
            raise ValueError(f"no decision {decision_id}")
        ask = (json.loads(row["doc_json"]) or {}).get("ask")
        if not isinstance(ask, dict) or not ask.get("ask_id"):
            raise ValueError("this decision was not answered by an ask or a standing answer, so there is nothing to mark")
        if ask.get("channel") == "memory":
            answers = db.con.execute("SELECT answer_id, exact_key, COALESCE(class_key, family_key) AS class_key, scope_key FROM human_answers WHERE answer_id=?",
                                     (f"ans_{ask['ask_id']}",)).fetchall()   # a project-scoped row keeps the class key of its kind as family_key
        elif ask.get("channel") == "jev":
            ev = db.con.execute("SELECT detail_json FROM events WHERE kind='jev_allowed' AND entity_id=?", (f"call_{ask['ask_id']}",)).fetchone()
            det = json.loads(ev["detail_json"]) if ev and ev["detail_json"] else {}
            answers = [{"answer_id": None, "exact_key": det.get("exact_key"), "class_key": det.get("class_key"), "coarse_key": det.get("coarse_key")}] if det.get("exact_key") else []
        else:
            a = db.con.execute("SELECT hold_key, fingerprints_json FROM asks WHERE ask_id=?", (ask["ask_id"],)).fetchone()
            answers = db.con.execute("SELECT answer_id, exact_key, COALESCE(class_key, family_key) AS class_key, scope_key FROM human_answers WHERE exact_key=? AND answer='allow'",
                                     (memory.exact_key(a["hold_key"], a["fingerprints_json"]),)).fetchall() if a else []
        with db.tx():
            db.event(decision_id, "review_mark", guard=f"jade review mark {mark}", actor=f"human:{user()}", detail={"mark": mark, "answer_ids": [r["answer_id"] for r in answers if r["answer_id"]]})
            if ask.get("channel") == "memory":   # V6-29: a call that ran from a standing answer: evidence for it (right), or its scope is paused (wrong)
                from . import audit

                audit.on_mark(db, decision_id, mark, [r["answer_id"] for r in answers if r["answer_id"]])
            retired = sum(db.retire_answers(r["exact_key"], r["class_key"], r["scope_key"] if "scope_key" in r.keys() else None) for r in answers) if mark == "wrong" else 0
            if mark == "wrong" and ask.get("channel") == "jev":
                from . import proxy

                for r in answers:
                    proxy.distrust(db, r["exact_key"], r["class_key"], r.get("coarse_key"))
    finally:
        db.close()
    return f"marked {decision_id} {mark}" + (f"; {retired} answer(s) retired, JADE asks again" if mark == "wrong" else "")


def review_audit_walk(stdin_isatty: bool | None = None, *, ask=input, say=print) -> int:
    """`jade review mark` with no arguments (human terminal): the calls that ran without asking you and wait for your check, oldest first, up to the weekly count.
    For each: right, wrong or skip. A wrong one retires the answer behind it and pauses its scope. Returns how many were marked."""
    from . import audit

    _require_human("jade review mark", stdin_isatty)
    db = ledger.open(timeout=5)
    try:
        rows = audit.pending(db, audit.settings().weekly_count)
    finally:
        db.close()
    if not rows:
        say("no calls wait for your check")
        return 0
    say(f"{len(rows)} call(s) ran without asking you and wait for your check. Was each one right to run? [r]ight / [w]rong / [s]kip")
    done = 0
    for r in rows:
        say(f"{r['decision_id']}  [{r['rule_id']}]  {audit.describe(r, _shown_line)}")
        verdict = {"r": "right", "right": "right", "w": "wrong", "wrong": "wrong"}.get(ask("  right, wrong or skip? [r/w/S] ").strip().lower())
        if verdict:
            say(review_mark(r["decision_id"], verdict, stdin_isatty=True))
            done += 1
    return done


def _pending_hints(db) -> list:
    return [r for r in db.human_answers(strength="hint", limit=100000) if r["answer"] == "allow"][::-1]


def _confirm_group(r) -> tuple:
    """(rule, class or exact key, is a class, scope key): the group of `jade review confirm` a hint is shown in. A hint with a scope is grouped by its rule and scope
    only: one yes makes the scope standing for every call it covers, so all hints of that scope (one per PR for a merge) are one question."""
    if r["scope_key"]:
        return (r["rule_id"], None, False, r["scope_key"])
    return (r["rule_id"], r["class_key"] or r["exact_key"], bool(r["class_key"]), None)


def _scope_lines(r) -> list[str]:
    """The plain-words lines for a hint that carries a scope: what it would become, what it covers (`scopes.COVERS`, the text the chat showed), how long it lasts,
    how to undo it."""
    from . import memory, scopes

    lines = [f"Would become standing for: {_shown_line(r['scope_label'])} ({memory.USE_DAYS} days after last use)"]
    covers = scopes.COVERS.get(r["scope_kind"])
    if covers:
        lines.append(covers)
    lines.append(f"Confirming makes this proposal standing. It lasts {memory.USE_DAYS} days after its last use, and never more than {memory.CAP_DAYS} days. "
                 "Undo it with jade knows rm ID.")
    return lines


CONFIRM_MAX_GROUPS = 10   # V6-29: groups that become standing in one sitting; the rest wait for the next one (a long list is answered by habit)


def _rule_is_high(rule_id: str) -> bool:
    """High or critical, or unknown: `--all` never takes such a group (V6-29). An unknown rule counts as high, so a missing rule only adds a question."""
    from . import policy

    level = next((r.get("level") for r in policy.risk().get("rules", []) if r.get("id") == rule_id), None)
    return level in (None, "high", "critical")


def review_confirm(stdin_isatty: bool | None = None, *, ask=input, say=print, all_: bool = False) -> int:
    """`jade review confirm`: the human turns the allow answers they gave in the chat or in a dialog into standing answers, in a terminal. Groups of the same kind of
    call are shown with their rule id; each group can be dropped (`--all` takes the groups of a rule below high risk and still asks about every high one, V6-29); at
    most `CONFIRM_MAX_GROUPS` groups become standing in one sitting; a typed `yes` after the list of what becomes standing seals it."""
    from . import memory

    _require_human("jade review confirm", stdin_isatty)
    db = ledger.open(timeout=5)
    try:
        rows = _pending_hints(db)
        if not rows:
            say("no unconfirmed allow answers")
            return 0
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault(_confirm_group(r), []).append(r)
        chosen: list = []
        taken = 0
        for n_seen, ((rule, _, is_class, scope_key), rs) in enumerate(groups.items()):
            if taken >= CONFIRM_MAX_GROUPS:
                say(f"{len(groups) - n_seen} more group(s) wait: run `jade review confirm` again after this sitting")
                break
            if scope_key:
                say(f"{_shown_line(rs[-1]['label']) or '(no text kept)'}  [{rule}]  allowed {len(rs)}x")
                for line in _scope_lines(rs[-1]):
                    say(f"   {line}")
            else:
                kind = "this kind of call" if is_class else "this exact call only"
                say(f"{_shown_line(rs[-1]['label']) or '(no text kept)'}  [{rule}]  allowed {len(rs)}x  ->  would be standing for {kind}")
            if rule in memory.NEVER_RULES:
                say("   (rule is always asked; confirming it changes nothing)")
            if (all_ and not _rule_is_high(rule)) or ask("  stand for this? [y/N] ").strip().lower() in ("y", "yes"):
                chosen += rs
                taken += 1
        if not chosen:
            say("nothing confirmed")
            return 0
        say(f"{len(chosen)} answer(s) in {len({_confirm_group(r) for r in chosen})} group(s) will become standing.")
        say("At level low or medium, a matching call then runs without a question.")
        say(f"It lasts {memory.USE_DAYS} days after its last use, never more than {memory.CAP_DAYS} days.")
        say("Undo one with jade knows rm ID. jade knows list shows each ID.")
        if ask("Type yes to confirm: ").strip() != "yes":
            say("cancelled; nothing changed")
            return 0
        from . import audit

        with db.tx():
            n = db.confirm_answers([r["answer_id"] for r in chosen])
            for sid in {audit.scope_id(r) for r in chosen}:   # a paused scope starts again, at 0 runs, because the human confirmed an answer of it (never a hint)
                audit.resume(db, sid)
        say(f"{n} answer(s) confirmed")
        return n
    finally:
        db.close()


def knows_list() -> str:
    """`jade knows list` (read-only): the standing answers, with when each stops deciding."""
    from . import audit, memory

    db = ledger.open(timeout=5)
    try:
        rows = db.human_answers(strength="strong", limit=100000)
    finally:
        db.close()
    if not rows:
        return "no standing answers (`jade review confirm` makes some from what you allowed in the chat)"
    out = []
    for r in rows:
        if r["answer"] != "allow":
            continue
        last = max(filter(None, [r["confirmed_ts"], r["last_used_ts"]]))
        use_end = (parse_ts(last) + timedelta(days=memory.USE_DAYS)) if last else None
        cap_end = parse_ts(r["confirmed_ts"]) + timedelta(days=memory.CAP_DAYS)
        runs = r["silent_runs"] or 0
        evid = parse_ts(r["evidence_ts"] or r["confirmed_ts"]) + timedelta(days=audit.settings().age_days) if runs else None   # V6-29: a row that has run silently needs evidence
        end = min(x for x in (use_end, cap_end, evid) if x)
        note = "  (near its 180-day end: confirm again to keep it)" if (cap_end - now()) <= timedelta(days=memory.NEAR_CAP_DAYS) else ""
        if runs:
            note += f"  ({runs} silent run(s) since its last mark; it stops after {audit.settings().age_runs} with no mark: jade review mark)"
        scope = f"  scope  {_shown_line(r['scope_label'])}" if r["scope_label"] else ""
        out.append(f"{r['answer_id']}  [{r['rule_id']}]  {'kind' if r['class_key'] else 'exact'}  {_shown_line(r['label'])}{scope}  stops deciding {ts(end)[:10]}{note}")
    return "\n".join(out) or "no standing answers"


def knows_rm(answer_id: str, stdin_isatty: bool | None = None) -> bool:
    _require_human("jade knows rm", stdin_isatty)
    db = ledger.open(timeout=5)
    try:
        with db.tx():
            return db.revoke_answer(answer_id)
    finally:
        db.close()


PREFERENCES_FILE = "preferences.md"


def preferences_path() -> Path:
    return paths.config_dir() / PREFERENCES_FILE


KNOWS_MAX_LINES = 40
KNOWS_LABEL_CHARS = 90
_KNOWS_MODELS = (
    "# Example, an ssh host: For ssh <user@host> (the <name> project): allow anything, except <what must stay a question>. Always ask me before <what must stay a question>.",
    "# Example, code projects: For the <name> projects on GitHub and GitLab: allow reading and writing, like <the commands you use>. "
    "Always ask me before any delete. Never push to the main branch.",
)


def knows_template(rows, since: tuple[str, str] | None = None) -> str:
    """The first text of the preferences file (pure but for the clock): two header lines, two model sentences with placeholders, then one commented line per kind of
    standing answer. A line is printed exactly when `memory.lookup` still honours the answer for some call it covers: `memory.honoured` with the row's
    `memory.covered_keys`, the same test lookup runs (strong, within the lifetime, no newer deny that shares a key every covered call has). So a deny of one PR
    leaves a merge scope line, and a deny of the same kind hides a class, `kind_any` or `kind_project` line.
    A kind is the answer's stored keys, never words of its label: one line per (rule, scope key), else per (rule, class key), else per (rule, exact key), showing the newest call.
    Every line starts with `#`, and there are at most KNOWS_MAX_LINES lines."""
    from . import askplain

    out = ["# What JADE may decide for you. Write plain sentences; a later release reads this file when it decides in your place.",
           "# Lines that start with # are ignored.", "#", *_KNOWS_MODELS, "#"]
    since = since or memory.valid_since()
    denies = memory.deny_index(r for r in rows if r["answer"] == "deny")   # built once: one dict lookup per key and row
    seen: dict[tuple, str] = {}
    for r in rows:   # newest first, so the first row of a group names it
        if r["answer"] != "allow" or not memory.honoured(r, denies, memory.covered_keys(r), since):
            continue
        key = (r["rule_id"], "scope", r["scope_key"]) if r["scope_key"] else (r["rule_id"], "class", r["class_key"]) if r["class_key"] else (r["rule_id"], "exact", r["exact_key"])
        if key in seen:
            continue
        text = _shown_line(r["scope_label"] or r["label"]) or "(a call with no label stored)"
        if len(text) > KNOWS_LABEL_CHARS:
            text = text[:KNOWS_LABEL_CHARS - 3] + "..."
        seen[key] = f"# I am fine with: {text}  [{r['rule_id']}]  (risk: {askplain.risk_line(r['rule_id'])})"
    lines = [x for x in seen.values() if x]
    room = KNOWS_MAX_LINES - len(out)
    if len(lines) > room:
        lines = [*lines[:room - 1], "# (more kinds are not shown here: `jade knows list` shows all)"]
    return "\n".join([*out, *lines])


def knows_rows(db) -> list:
    """What the template reads: the standing allows (live, newest first) and the denies that share a key with one of them. A newer deny hides an older allow;
    the rest of the ledger is not read."""
    since = memory.valid_since()
    allows = db.con.execute(f"SELECT * FROM human_answers WHERE {memory.ALLOW_SQL} AND confirmed_ts>=? ORDER BY ts DESC, rowid DESC LIMIT 100000", (since[0],)).fetchall()  # noqa: S608
    allows = [r for r in allows if memory.standing(r, since)]
    exact = sorted({r["exact_key"] for r in allows})
    cls = sorted({k for r in allows for k in (r["class_key"], r["family_key"]) if k})
    scope = sorted({r["scope_key"] for r in allows if r["scope_key"]})
    return [*allows, *memory._rows_by_keys(db, memory.DENY_SQL, exact, cls, scope)]


def knows_edit(stdin_isatty: bool | None = None, editor: str | None = None) -> Path:
    """`jade knows edit`: open the preferences file in the human's editor. It starts as a template with one commented-out sentence per standing kind of call."""
    _require_human("jade knows edit", stdin_isatty)
    p = preferences_path()
    if not p.exists():
        db = ledger.open(timeout=5)
        try:
            text = knows_template(knows_rows(db))
        finally:
            db.close()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, text + "\n")
    subprocess.run([editor or os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi", str(p)], check=False)
    return p


def pending_row() -> tuple[str, bool, str]:
    try:
        db = ledger.open(timeout=5)
    except Exception:  # noqa: BLE001 - an unreadable ledger is reported by its own row
        return ("answers to confirm", True, "unknown")
    try:
        n = len(_pending_hints(db))
    finally:
        db.close()
    return ("answers to confirm", True, "none" if not n else f"{n} allow answer(s) wait: `jade review confirm` (in a terminal) makes them standing")


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


def maintain_schedule(fmt: str = "cron") -> str:
    """Where launchd does not exist: the line or the units that run `jade maintain` once a day at 09:15 (the time of the LaunchAgent). Printed only: nothing is
    installed, the human does that. `cron` is one crontab line; `systemd` is a user service and its timer, each under a comment that names its path."""
    if fmt not in ("cron", "systemd"):
        raise ValueError(f"unknown format {fmt!r}: cron or systemd")
    jade, log = jade_bin(), paths.home() / "maintain.log"
    if fmt == "cron":
        return f"15 9 * * * {shlex.quote(jade)} maintain >> {shlex.quote(str(log))} 2>&1\n".replace("%", "\\%")   # cron reads a bare % as a newline
    quote = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'   # noqa: E731 - systemd's own quoting; % is a specifier
    return ("# ~/.config/systemd/user/jade-maintain.service\n[Unit]\nDescription=JADE daily maintenance (expiry sweeps, retention, drift canary, miner)\n\n"
            f"[Service]\nType=oneshot\nExecStart={quote(jade)} maintain\n\n"
            "# ~/.config/systemd/user/jade-maintain.timer\n[Unit]\nDescription=Run JADE maintenance daily\n\n"
            "[Timer]\nOnCalendar=*-*-* 09:15:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")


MAINTAIN_STALE_DAYS = 3   # a job that runs daily and has not left its stamp for this long is not running


def _last_maintain() -> str | None:
    """When `jade maintain` last finished, from the stamp it leaves in the ledger; None when it never did or the ledger cannot be read."""
    try:
        db = ledger.open()
        try:
            return db.meta_get("last_maintain_ts")
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - a row of `jade check` must not crash the check
        return None


PROBE_SESSION = "jade-check-probe"


def _probe_event(sub: str) -> dict:
    if sub == "pretool":   # a call SI-09 always blocks: a hook that decides blocks it, a hook that does nothing lets it through
        return {"tool_name": "Bash", "tool_input": {"command": "jade permit approve prm_000000000000"}, "session_id": PROBE_SESSION, "cwd": "/tmp"}
    if sub == "posttool":
        return {"tool_name": "Bash", "tool_input": {"command": "true"}, "tool_response": {}, "session_id": PROBE_SESSION, "cwd": "/tmp"}
    if sub == "posttool-failure":
        return {"tool_name": "Bash", "tool_input": {"command": "true"}, "error": "probe", "is_interrupt": False, "session_id": PROBE_SESSION, "cwd": "/tmp"}
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


def _project_dirs(cwd) -> list[Path]:
    """`cwd` and each ancestor up to the repository root (the nearest directory with `.git`; without one, `cwd` alone)."""
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
    return dirs


def project_settings_files(cwd) -> list[Path]:
    """The project `.claude/settings.json` and `settings.local.json` a session started in `cwd` can read: in `cwd` and each ancestor up to the
    repository root (the nearest directory with `.git`; without one, `cwd` alone). The user's own two files are not listed: wiring_problems owns them."""
    dirs = _project_dirs(cwd)
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


# ---- what a repository controls before the trust dialog (V6-18 e, BS-01, BS-02; display only) ----------------------------
_INSTRUCTION_FILES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude/CLAUDE.md")
_MAX_RULE_FILES = 200       # rules files scanned; more are counted and reported, never dropped silently
_MAX_RULE_ENUM = 5000       # rules files listed at most, so a repository with a million files cannot slow the scan
_MAX_RULE_BYTES = 16 * 1024 * 1024   # all the rules files together
_MAX_RULE_WALK = 20000      # directory entries looked at, so a `.claude/rules` full of folders and no `.md` file cannot hold up the session start


def _rules_files(rules_dir: Path) -> tuple[list[Path], bool]:
    """The `.md` files under `rules_dir`, at most `_MAX_RULE_ENUM`, from a walk that looks at no more than `_MAX_RULE_WALK` entries and follows no link. The flag says the
    list is cut short (by either limit), so the report can say so."""
    found: list[Path] = []
    seen = 0
    try:
        for root, dirs, files in os.walk(rules_dir, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                seen += 1
                if seen > _MAX_RULE_WALK:
                    return sorted(found), True
                if name.endswith(".md"):
                    if len(found) >= _MAX_RULE_ENUM:
                        return sorted(found), True
                    found.append(Path(root, name))
            seen += len(dirs)
            if seen > _MAX_RULE_WALK:
                return sorted(found), True
    except OSError:
        pass
    return sorted(found), False
_PLAIN_NAME = re.compile(r"[\w.-]{1,40}")
_TAG_RANGES = ((0xE0000, 0xE007F), (0xE0100, 0xE01EF))   # tag characters and the variation selectors supplement: text no reader sees
_BIDI_CONTROLS = frozenset({*range(0x202A, 0x202F), *range(0x2066, 0x206A)})   # embeddings, overrides and isolates (Trojan Source)
_ALWAYS_HIDDEN = frozenset({0x200B, *range(0x2060, 0x2065), 0x00AD, 0x180E, 0x034F, 0xFEFF,
                            *range(0xFE00, 0xFE0F),                 # variation selectors 1 to 15 (a text-smuggling channel); VS16 is the emoji selector, read below
                            *range(0x206A, 0x2070),                 # deprecated format characters
                            0x3164, 0x115F, 0x1160, 0xFFA0,         # Hangul fillers: blank, and not a letter
                            0x2800,                                 # braille blank
                            *range(0xFFF9, 0xFFFD),                 # interlinear annotation marks and the object replacement character
                            *range(0x1D173, 0x1D17B)})              # musical format controls
_VS16 = 0xFE0F                                                      # ordinary after an emoji; a run of them carries bits
_JOINERS = frozenset({0x200C, 0x200D})
_NEEDS_A_SCRIPT = frozenset({0x200C, 0x200D, 0x200E, 0x200F, 0x061C})   # ordinary in Persian and Arabic text and in emoji sequences, hidden anywhere else
_JOINER_MIN, _JOINER_RATIO = 8, 6   # a line with this many joiners, and one in every six characters or more, hides bits in them


def _repo_name(k) -> str:
    """A key or server name of a repository file as it may be shown: plain word characters only, else a fixed placeholder (a name is repository text)."""
    return k if isinstance(k, str) and _PLAIN_NAME.fullmatch(k) else "(unusual name)"


def _repo_names(keys, limit: int = 8) -> str:
    shown = sorted({_repo_name(k) for k in keys})
    return ", ".join(shown[:limit]) + (f" and {len(shown) - limit} more" if len(shown) > limit else "")


def _joins_text(c: str | None) -> bool:
    """A neighbour that makes a joiner or a direction mark ordinary: a letter of an Arabic, Hebrew, Syriac, Thaana or Indic script, a symbol or a pictograph."""
    if not c:
        return False
    o = ord(c)
    return 0x0590 <= o <= 0x0DFF or 0xFB1D <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFC or o >= 0x1F000 or unicodedata.category(c) == "So" or c == "️"


def hidden_unicode(text: str) -> tuple[dict[str, int], int]:
    """({class: count}, line of the first hit) of the characters in `text` that a reader does not see but a model reads. Classes: `tag`, `bidi`, `zero-width`.
    A byte order mark at the very start is not counted, and a joiner or direction mark next to Persian, Arabic or emoji text is ordinary writing, unless two
    joiners touch, or a line holds many of them (`_JOINER_MIN`, `_JOINER_RATIO`: bits of a hidden message). Also counted as `zero-width`: variation selectors
    (a run of the emoji selector too), Hangul fillers, the braille blank, the deprecated format characters, the interlinear and object marks, musical format controls."""
    if text.isascii():
        return {}, 0
    counts: dict[str, int] = {}
    first = 0
    line = 1
    line_len = ordinary_joiners = 0   # the joiners of this line that were exempt as ordinary writing; too many of them in a line are bits of a hidden message

    def end_of_line() -> None:
        nonlocal first
        if ordinary_joiners >= _JOINER_MIN and ordinary_joiners * _JOINER_RATIO >= line_len:
            counts["zero-width"] = counts.get("zero-width", 0) + ordinary_joiners
            first = first or line

    for i, c in enumerate(text):
        o = ord(c)
        if c == "\n":
            end_of_line()
            line += 1
            line_len = ordinary_joiners = 0
            continue
        line_len += 1
        if o < 0x80:
            continue
        prev, nxt = (ord(text[i - 1]) if i else 0), (ord(text[i + 1]) if i + 1 < len(text) else 0)
        if any(a <= o <= b for a, b in _TAG_RANGES):
            kind = "tag"
        elif o in _BIDI_CONTROLS:
            kind = "bidi"
        elif o in _ALWAYS_HIDDEN and not (o == 0xFEFF and i == 0):
            kind = "zero-width"
        elif o == _VS16 and (0xFE00 <= prev <= _VS16 or 0xFE00 <= nxt <= _VS16):   # one after an emoji is ordinary; a run is not
            kind = "zero-width"
        elif o in _NEEDS_A_SCRIPT:
            if (o in _JOINERS and (prev in _JOINERS or nxt in _JOINERS)) or not (_joins_text(text[i - 1] if i else None) or _joins_text(text[i + 1] if i + 1 < len(text) else None)):
                kind = "zero-width"
            else:
                kind = None
                ordinary_joiners += o in _JOINERS
        else:
            kind = None
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
            first = first or line
    end_of_line()
    return counts, first


def _bounded_text(f: Path) -> tuple[str | None, str | None]:
    """(text, problem) of a repository file, read as the host would (symlinks followed), at most MAX_SETTINGS_BYTES. (None, None) when it is no regular file."""
    try:
        st = os.stat(f)
        if not stat.S_ISREG(st.st_mode):
            return None, None
        if st.st_size > MAX_SETTINGS_BYTES:
            return None, f"too large ({st.st_size} bytes) to scan"
        with open(f, "rb") as fh:
            return fh.read(MAX_SETTINGS_BYTES + 1).decode("utf-8", "replace"), None
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"cannot be read ({type(e).__name__})"


def _settings_findings(label: str, data: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    hooks_map = data.get("hooks")
    n_hooks, events = 0, []
    for event, groups in (hooks_map.items() if isinstance(hooks_map, dict) else ()):
        n = sum(len(g["hooks"]) for g in (groups if isinstance(groups, list) else ()) if isinstance(g, dict) and isinstance(g.get("hooks"), list))
        if n:
            n_hooks += n
            events.append(event)
    if n_hooks:
        out.append(("hooks", f"{label}: {n_hooks} hook(s) for {_repo_names(events)}"))
    if data.get("enableAllProjectMcpServers"):
        out.append(("mcp", f"{label}: enableAllProjectMcpServers is true (every server in .mcp.json starts without a question)"))
    if isinstance(data.get("enabledMcpjsonServers"), list) and data["enabledMcpjsonServers"]:
        out.append(("mcp", f"{label}: enabledMcpjsonServers names {len(data['enabledMcpjsonServers'])} server(s)"))
    if isinstance(data.get("env"), dict) and data["env"]:
        out.append(("env", f"{label}: env keys {_repo_names(data['env'])}"))
    return out


def repo_config_findings(cwd=None) -> list[tuple[str, str]]:
    """(kind, line) for what the repository of `cwd` (and the directories above it up to the repo root) controls and the host loads before or without a trust
    question: hooks, MCP servers and `env` keys of its Claude settings files and `.mcp.json`, and hidden Unicode in CLAUDE.md, AGENTS.md and the rules files.
    Names, counts and line numbers only: never a value, a command, a URL or a line of the repository, because the SessionStart text goes to the model. Display
    only: a hook cannot stop what loads before the hooks run (docs/05 T19)."""
    if cwd is None:
        try:
            cwd = Path.cwd()
        except OSError:
            return []
    dirs = _project_dirs(cwd)
    if not dirs:
        return []
    root = dirs[-1]

    def label(f: Path, last: str | None = None) -> str:
        """A path as it may be shown: every segment is a plain name (`_repo_name`), else `(unusual name)`, because a directory or file name is repository text.
        `last` replaces the final segment (a rules file is shown by its number, not by its name)."""
        try:
            parts = list(f.relative_to(root).parts)
        except ValueError:
            parts = [f.name]
        return "/".join([_repo_name(p) for p in parts[:-1]] + [last if last is not None else _repo_name(parts[-1])] if parts else [last or "."])

    out: list[tuple[str, str]] = []
    for f in project_settings_files(cwd):
        text, problem = _bounded_text(f)
        if problem:
            out.append(("unreadable", f"{label(f)}: {problem}"))
        if text is None:
            continue
        try:
            data = json.loads(text) if text.strip() else {}
        except ValueError:
            out.append(("unreadable", f"{label(f)}: is not valid JSON, so JADE cannot say what it sets"))
            continue
        if not isinstance(data, dict):
            out.append(("unreadable", f"{label(f)}: does not hold a JSON object, so JADE cannot say what it sets"))
            continue
        out += _settings_findings(label(f), data)
    for d in dirs:
        text, problem = _bounded_text(d / ".mcp.json")
        if problem:
            out.append(("unreadable", f"{label(d / '.mcp.json')}: {problem}"))
        elif text is not None:
            try:
                servers = (json.loads(text) if text.strip() else {}).get("mcpServers")
            except (ValueError, AttributeError):
                out.append(("unreadable", f"{label(d / '.mcp.json')}: is not valid JSON, so JADE cannot say which MCP servers it starts"))
                continue
            if isinstance(servers, dict) and servers:
                out.append(("mcp", f"{label(d / '.mcp.json')}: {len(servers)} MCP server(s): {_repo_names(servers)}"))
    for d in dirs:
        rules_dir = d / ".claude" / "rules"
        rules, cut = _rules_files(rules_dir)
        named =[(d / n, None) for n in _INSTRUCTION_FILES] + [(f, f"(file {k})") for k, f in enumerate(rules[:_MAX_RULE_FILES], 1)]
        if len(rules) > _MAX_RULE_FILES or cut:   # the host loads every rules file, so a file left out is a gap in the report
            out.append(("unreadable", f"{label(rules_dir)}: {max(len(rules) - _MAX_RULE_FILES, 0)}{' or more' if cut else ''} more rules files were "
                                      "not scanned for hidden Unicode"))
        spent = 0
        for f, shown in named:
            if shown and spent >= _MAX_RULE_BYTES:
                out.append(("unreadable", f"{label(rules_dir)}: the scan of the rules files stopped at its total size limit; the rest were not scanned for hidden Unicode"))
                break
            text, problem = _bounded_text(f)
            where = label(rules_dir / "_", shown) if shown else label(f)   # a rules file by its number, whatever directories lie between
            if problem:
                out.append(("unreadable", f"{where}: {problem} for hidden Unicode"))
            if text is None:
                continue
            spent += len(text) if shown else 0
            counts, first = hidden_unicode(text)
            if counts:
                out.append(("unicode", f"{where}: hidden Unicode ({', '.join(f'{n} {k}' for k, n in sorted(counts.items()))}; first at line {first})"))
    return out


def _repo_config_row() -> tuple[str, bool | None, str]:
    """WARN (never FAIL) when the repository controls hooks, MCP servers, env keys or hides Unicode in the instruction files: JADE tells the owner and stops nothing."""
    try:
        scanned = str(Path.cwd())
    except OSError:
        scanned = "(no current directory)"
    try:
        found = repo_config_findings()
    except Exception:  # noqa: BLE001 - a health probe reports every failure
        return ("repo config", None, "the repository config could not be scanned; look at .claude/settings*.json, .mcp.json, CLAUDE.md and AGENTS.md by hand")
    if not found:
        return ("repo config", True, f"no repository-controlled hooks, MCP servers or env keys, and no hidden Unicode in CLAUDE.md, AGENTS.md or the rules, under {scanned} up to its "
                "repo root (run `jade check` from the project you work in to scan that one)")
    lines = [line for _kind, line in found]
    more = f"; and {len(lines) - 8} more" if len(lines) > 8 else ""
    return ("repo config", None, "display only, JADE cannot stop what the host loads before its trust dialog: " + "; ".join(lines[:8]) + more)


def _gate_mode_row() -> tuple[str, bool, str]:
    effective, raw = paths.gate_mode(), os.environ.get("JADE_GATE_MODE")
    cfg = paths.config().get("gate_mode")
    configured = cfg if cfg in paths.GATE_MODES else "enforce"
    if effective == "off":
        return ("gate mode", False, "off" + (" (JADE_GATE_MODE in this environment)" if raw else " (`jade gate off`)") + ": the PreToolUse and Stop hooks do nothing, so no "
                "call is gated; PostToolUse, PostToolUseFailure and SessionStart still record and sweep; `jade gate enforce` restores it")
    if raw and raw != configured:
        return ("gate mode", False, f"JADE_GATE_MODE={raw} in this environment overrides the configured {configured}; unset it or change the config on purpose")
    if effective == "advisory":
        return ("gate mode", None, "advisory: the gate records would_block and blocks only what the row `what blocks` lists (the soak state of docs/08 2.1)")
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
    return ("gate overrides", True, shown or "none (JADE_GATE_MODE, JADE_POLICY_DIR, JADE_CONFIG, JADE_HOME, JADE_APPROVER_ANCHOR, JADE_SESSION_ID all default or unset)")


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
        last = _last_maintain()
        try:
            age = (now() - parse_ts(last)).total_seconds() / 86400 if last else None
        except (ValueError, TypeError):   # a stamp that is not a time counts as no stamp
            last, age = None, None
        if age is not None and age <= MAINTAIN_STALE_DAYS:   # no LaunchAgent, but `jade maintain` ran lately: cron, a systemd timer or another scheduler runs it
            return ("maintain job", True, f"`jade maintain` last ran {last} (no LaunchAgent: another scheduler runs it, JADE cannot tell which)")
        since = f" It last ran {last}." if age is not None else " It has never left a last-run stamp."
        return ("maintain job", None, f"not installed: no `jade maintain` LaunchAgent in {launch_agents_dir()}, so retention, expiry sweeps, the drift canary and the "
                "miner do not run unless something else schedules `jade maintain` (docs/08 2.4)." + since +
                " Off macOS, `jade maintain-schedule` prints a cron line or a systemd timer for you to install")
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


MAIN_REF = "origin/main"


def _git_out(checkout: Path, *args: str) -> str | None:
    """stdout of `git -C checkout <args>` (local only, 5 s), or None when `checkout` is not a git checkout, git fails or is slow. Never raises."""
    if not (Path(checkout) / ".git").exists():
        return None
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")}
    try:
        r = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True, timeout=5, check=False, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def main_commit(checkout: Path) -> str | None:
    """The short (12) commit of origin/main in `checkout`, as of its last fetch, or None. Never raises."""
    out = _git_out(checkout, "rev-parse", "--verify", "--quiet", "--short=12", f"{MAIN_REF}^{{commit}}")
    sha = (out or "").strip()
    return sha if re.fullmatch(r"[0-9a-f]{12}", sha) else None


def commits_behind_main(checkout: Path, commit: str) -> int | None:
    """How many commits origin/main has that `commit` does not (`git rev-list --count commit..origin/main`), or None when either is unknown there."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit or ""):
        return None
    out = _git_out(checkout, "rev-list", "--count", f"{commit}..{MAIN_REF}")
    return int(out.strip()) if out and out.strip().isdigit() else None


def main_version(checkout: Path) -> str | None:
    """The `project.version` of pyproject.toml at origin/main in `checkout`, as of its last fetch (local only: nothing is fetched). None when the ref or the
    file is not there, git fails, or the text is not a version. Never raises."""
    out = _git_out(checkout, "show", f"{MAIN_REF}:pyproject.toml")
    if out is None:
        return None
    try:
        import tomllib

        v = str(tomllib.loads(out)["project"]["version"])
    except (ValueError, KeyError, TypeError):
        return None
    return v if re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,40}", v) else None


def _main_version_row() -> tuple[str, bool | None, str]:
    """V6-10: the running version against origin/main of the named checkout. A warning when older, unknown when there is nothing to compare with; never a failure."""
    import jade

    co = paths.checkout_dir()
    if co is None:
        return ("version vs main", None, f"unknown: jade {jade.__version__} runs; no checkout named (JADE_CHECKOUT or config `checkout`), so it is not compared with {MAIN_REF}")
    theirs = main_version(co)
    if theirs is None:
        return ("version vs main", None, f"unknown: jade {jade.__version__} runs; no readable {MAIN_REF}:pyproject.toml in {co} (run `git fetch` there)")
    a, b = _version_tuple(jade.__version__), _version_tuple(theirs)
    if a is None or b is None:
        return ("version vs main", None, f"unknown: jade {jade.__version__} runs; {MAIN_REF} in {co} has version {theirs}, not comparable")
    when = f"(in {co}, as of its last fetch)"
    if a < b:
        return ("version vs main", None, f"jade {jade.__version__} runs, older than {MAIN_REF} {theirs} {when}: fixes merged since are not running")
    if a > b:
        return ("version vs main", True, f"jade {jade.__version__} runs, newer than {MAIN_REF} {theirs} {when}")
    # the same version string: main can carry merged fixes after a release tag without a version bump, so compare commits (V6-10 F2)
    running, head = paths.code_commit(), main_commit(co)
    if running is None:
        return ("version vs main", None, f"unknown: installed package, commit not known; jade {jade.__version__} runs, the same version string as {MAIN_REF} {when}, "
                                         "which may carry fixes merged after the release")
    if head is None:
        return ("version vs main", None, f"unknown: jade {jade.__version__} runs at commit {running}; the commit of {MAIN_REF} in {co} is not readable")
    if running == head:
        return ("version vs main", True, f"jade {jade.__version__} runs at commit {running}, same as {MAIN_REF} {when}")
    behind = commits_behind_main(co, running)
    if behind:
        return ("version vs main", None, f"jade {jade.__version__} runs at commit {running}: same version string, {behind} commit(s) behind {MAIN_REF} {head} {when}; "
                                         "fixes merged since are not running")
    return ("version vs main", None, f"jade {jade.__version__} runs at commit {running}: same version string, but a different commit than {MAIN_REF} {head} {when}")


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
    rows.append(("hooks installed", not missing, f"all {len(HOOKS)}: {', '.join(HOOKS)}" if not missing else f"missing or out of date: {', '.join(missing)}: run `jade hooks install` in a terminal"))
    gaps = pretool_matcher_gaps()
    problems = None
    if not missing or gaps is not None:   # review of W-141: a registered PreToolUse entry is probed live even when out of date, so `what blocks` never says a dead hook checks
        problems = wiring_problems()
        rows.append(("hook wiring", not problems, "binary runs, hooks enabled, a synthetic event is decided" if not problems else "; ".join(problems)))
    shadow = shadow_problems()
    try:
        scanned = str(Path.cwd())
    except OSError:
        scanned = "(no current directory)"
    rows.append(("settings shadowing", not shadow, f"no managed or plugin settings file, and no project settings file under {scanned} up to its repo root, disables or shadows the hooks "
                 "(run `jade check` from the project you work in to scan that one)" if not shadow else "; ".join(shadow)))
    rows.append(_repo_config_row())
    if gaps is not None:
        rows.append(("pretool matcher", not gaps, "covers every gated tool" if not gaps else f"does not cover {', '.join(gaps)}: re-run `jade hooks install` in a terminal"))
    rows.append(_gate_mode_row())
    rows.append(_level_row())
    rows.append(_jev_row())
    rows.append(_what_blocks_row(st.get("PreToolUse", False), problems))
    rows.append(_list_b_row())
    rows.append(pending_row())
    rows.append(_overrides_row())
    rows.append(_ack_audit_row())
    rows.append(_permission_row())
    rows.append(_host_bypass_row())
    rows.append(_approval_row())
    rows.append(tool_coverage_row())
    rows.append(_maintain_row())
    rows.append(_main_version_row())
    rows += unknown_rows()
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
    if mode in PERMISSIVE_MODES and paths.level() == "off":   # V6-10: a warning, not a failure (blocking at level off is V6-16)
        from . import hooks

        return ("host permission prompt", None, f"permissions.defaultMode is {mode} (the host never asks) and level off (JADE only logs most rules): only "
                + ", ".join(sorted(_governance_ids() | hooks.OFF_FLOOR_INVARIANTS)) + " and a write to JADE's own wiring stop a call. " + detail)
    return ("host permission prompt", True, detail)


def _governance_ids() -> frozenset[str]:
    from . import hooks

    return hooks.GOVERNANCE_INVARIANTS


def _host_bypass_row() -> tuple[str, bool | None, str]:
    """V6-10: whether the host's own permission prompt backs JADE up. "JADE is the only gate" when the host never asks or the ledger shows gated calls with
    no host prompt; unknown when the ledger shows no gated call (an unreadable ledger counts as none). Never a failure."""
    mode = configured_permission_mode()
    seen, no_prompt = no_prompt_gated_calls()
    counts = f"; last 7 days: {no_prompt} of {seen} gated call(s) had no host prompt" if seen else ""
    if mode in PERMISSIVE_MODES:
        return ("host mode bypass", None, f"JADE is the only gate: permissions.defaultMode is {mode}, the host never asks{counts}")
    if not seen:
        return ("host mode bypass", None, "unknown: no gated call recorded in the last 7 days, so whether the host prompts is not known")
    if no_prompt == seen:
        return ("host mode bypass", None, f"JADE is the only gate: {no_prompt} of {seen} gated call(s) in the last 7 days ran with no host prompt")
    if no_prompt:
        return ("host mode bypass", None, f"JADE is the only gate for {no_prompt} of {seen} gated call(s) in the last 7 days (no host prompt)")
    return ("host mode bypass", True, f"the host prompted on all {seen} gated call(s) of the last 7 days")


def unknown_rows() -> list[tuple[str, bool | None, str]]:
    """V6-10: what JADE cannot see from here reads "unknown: owner confirms" (V6-12 adds the owner's confirmation)."""
    return [("pre-push guard", None, "unknown: owner confirms. JADE does not install the git pre-push guard (the structural half of SI-08); "
             "`jade git-hook check <repo>` shows whether one repository runs it"),
            ("remote branch protection", None, "unknown: owner confirms on the git host. JADE cannot see it, and it is the real boundary for a push to the "
             "default branch (SI-08 in the hook is a text scan)")]


def no_prompt_gated_calls(days: int = 7) -> tuple[int, int]:
    """(gated calls seen, of those with no host prompt) in the last `days` days; (0, 0) when the ledger cannot be read. V6-23: counted as `jade ledger stats` counts
    (`readouts.load_counts`): an SI-07 hold is one call, not two."""
    from . import hooks, readouts

    try:
        db = ledger.open(timeout=2)
        try:
            rows = readouts.gated_rows(db, readouts.since_days(days))
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


def _approval_state() -> tuple[bool | None, str]:
    """W-38 / W-89: which backend approves high and critical permits, and whether its trust anchor can be used. Reads no ledger (`jade hooks install` uses it).
    V6-10: the signed backend is only "ok" while the risk gate asks for permits (gate enforce, level not off); a tty backend is never "ok"."""
    state, why = signing.anchor_state()
    if state == "untrusted":
        return (False, f"signed, but the trust anchor is not usable, so no high or critical permit can be used: {why}")
    if state == "ok":
        text = f"signed: high and critical permits need a signature from the approver account (anchor {signing.anchor_path()})"
        mode, level = paths.gate_mode(), paths.level()
        if mode != "enforce" or level == "off":
            return (None, text + f"; idle now: with {'level off' if mode == 'enforce' else f'gate mode {mode}'} no permit is asked for")
        return (True, text)
    if paths.config().get("approval_backend") == "signed":
        return (False, f"config asks for the signed backend but {why}: no high or critical permit can be used until the approver is installed (docs/10)")
    return (None, "tty: a terminal approves high and critical permits, which a same-user process can imitate (docs/05 T19, docs/10)")


def _approval_row() -> tuple[str, bool | None, str]:
    ok, text = _approval_state()
    if text.startswith("tty"):
        seen, no_prompt = no_prompt_gated_calls()
        text += f"; {no_prompt} of {seen} gated call(s) in the last 7 days had no host prompt" if seen else ""
    return ("approval backend", ok, text)


def install_summary_rows(host=HOST_UNREAD) -> list[tuple[str, bool | None, str]]:
    """V6-10 (DX-H4), W-140: what `jade hooks install` leaves in force, as rows (name, ok, detail) with the names and the states `jade check` uses: the effective
    gate mode, what the hook blocks, and the approval backend. `what blocks` is the row `jade check` prints (`_what_blocks_row`) with the static wiring facts
    only: disabled hooks or a missing binary make it "unknown", but no hook is run (review of W-140: the live probe made install cost about 1.6 s of CPU and
    tens of seconds of wall time on a busy machine), so the row says the hooks were not run here and points to `jade check`. It reads the config, the policy,
    the Claude settings and the trust anchor: nothing is written and no default changes. `host` is the Claude Code version `jade hooks install` already read, so
    `claude --version` runs once per install (about 90% of the summary's CPU was a second run of it)."""
    _, gate_ok, gate_text = _gate_mode_row()
    if os.environ.get("JADE_GATE_MODE") and "JADE_GATE_MODE" not in gate_text:
        gate_text += " (JADE_GATE_MODE in this environment)"
    backend_ok, backend_text = _approval_state()
    return [("gate mode", gate_ok, gate_text), _what_blocks_row(probe=False, host=host), ("approval backend", backend_ok, backend_text)]


CHECK_PREFIX = {True: "ok  ", False: "FAIL", None: "WARN"}   # V6-10: WARN is not safe or not known, and never changes the exit code


def check_failed(ok) -> bool:
    """A row fails on any false value except None (WARN)."""
    return ok is not None and not ok


def check_line(name: str, ok, detail: str) -> str:
    """One row as `jade check` prints it; `jade hooks install` prints its summary rows the same way."""
    return f"{CHECK_PREFIX[ok if ok is None else bool(ok)]}  {name}: {detail}"


def install_summary_lines(rows) -> list[str]:
    return [check_line(name, ok, detail) for name, ok, detail in rows]


def print_check(rows) -> int:
    for name, ok, detail in rows:
        print(check_line(name, ok, detail))
    failed = [n for n, ok, _ in rows if check_failed(ok)]
    if failed:
        print(f"jade check: {len(failed)} failed ({', '.join(failed)})", file=sys.stderr)
    return 1 if failed else 0


def print_check_json(rows) -> int:
    """`jade check --json` (V6-23): the same rows and the same exit code as `print_check`. `ok` is true, false (a failure) or null (a warning). `load_last_7_days` is the
    gated-call count that `jade ledger stats` prints too, from one definition (`readouts.load_counts`), with how many of those calls had no host prompt."""
    from . import readouts

    failed = [n for n, ok, _ in rows if check_failed(ok)]
    load = None
    try:
        db = ledger.open(timeout=2)
        try:
            load = readouts.load_counts(db, readouts.since_days(7))
        finally:
            db.close()
        load["no_host_prompt"] = no_prompt_gated_calls()[1]
    except Exception:  # noqa: BLE001 - an unreadable ledger is the ledger row's failure
        load = None
    print(json.dumps({"ok": not failed, "failed": failed, "rows": [{"name": n, "ok": None if ok is None else bool(ok), "detail": d} for n, ok, d in rows],
                      "load_last_7_days": load}, sort_keys=True, separators=(",", ":")))
    return 1 if failed else 0
