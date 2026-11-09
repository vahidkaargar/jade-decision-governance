"""Operations: champion/challenger pins, drift detection, retention, wiring checks, harness integration."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from . import ledger, paths, policy
from .util import is_human_tty, now, ts

HOOK_MARK = "jade hook "


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
def settings_path() -> Path:
    return Path(os.environ.get("JADE_CLAUDE_SETTINGS") or Path.home() / ".claude" / "settings.json")


def jade_bin() -> str:
    return os.environ.get("JADE_BIN") or shutil.which("jade") or str(Path.home() / ".local" / "bin" / "jade")


def hooks_status() -> dict[str, bool]:
    try:
        hooks = json.loads(settings_path().read_text()).get("hooks", {})
    except (OSError, ValueError):
        hooks = {}
    out = {}
    for event, (_m, sub, _t) in HOOKS.items():
        cmds = [h.get("command", "") for grp in hooks.get(event, []) for h in grp.get("hooks", [])]
        out[event] = any(f"{HOOK_MARK}{sub}" in c for c in cmds)
    return out


def pretool_matcher_gaps() -> list[str] | None:
    """Gated tools the installed PreToolUse matcher does not cover (an install made before the matcher grew). None when the hook is not installed."""
    import re

    try:
        hooks = json.loads(settings_path().read_text()).get("hooks", {})
    except (OSError, ValueError):
        return None
    matchers = [g.get("matcher", "") for g in hooks.get("PreToolUse", []) for h in g.get("hooks", []) if f"{HOOK_MARK}pretool" in h.get("command", "")]
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


def hooks_install(stdin_isatty: bool | None = None) -> Path:
    _require_human("jade hooks install", stdin_isatty)
    p = settings_path()
    data = json.loads(p.read_text()) if p.exists() else {}
    backup = p.with_name(p.name + f".bak-jade-{now():%Y%m%d%H%M%S}")
    if p.exists():
        shutil.copy2(p, backup)
    hooks = data.setdefault("hooks", {})
    for event, (matcher, sub, timeout) in HOOKS.items():
        groups = hooks.setdefault(event, [])
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if HOOK_MARK not in h.get("command", "")]
        groups[:] = [g for g in groups if g.get("hooks")]
        entry = {"hooks": [{"type": "command", "command": f"{jade_bin()} hook {sub}", "timeout": timeout}]}
        if matcher:
            entry["matcher"] = matcher
        groups.append(entry)
    tmp = p.with_name(p.name + ".tmp-jade")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(p)
    return backup


def hooks_uninstall(stdin_isatty: bool | None = None) -> int:
    _require_human("jade hooks uninstall", stdin_isatty)
    p = settings_path()
    data = json.loads(p.read_text())
    removed = 0
    for event in list(data.get("hooks", {})):
        groups = data["hooks"][event]
        for g in groups:
            keep = [h for h in g.get("hooks", []) if HOOK_MARK not in h.get("command", "")]
            removed += len(g.get("hooks", [])) - len(keep)
            g["hooks"] = keep
        data["hooks"][event] = [g for g in groups if g.get("hooks")]
        if not data["hooks"][event]:
            del data["hooks"][event]
    shutil.copy2(p, p.with_name(p.name + f".bak-jade-{now():%Y%m%d%H%M%S}"))
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return removed


def set_gate_mode(mode: str, stdin_isatty: bool | None = None) -> None:
    _require_human("jade gate", stdin_isatty)
    cfg = paths.config()
    cfg["gate_mode"] = mode
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg, indent=1) + "\n")


def claude_md_section() -> str:
    return (paths.docs_dir() / "claude-md-jev-section.md").read_text()


def claude_md_apply(target: Path | None = None, stdin_isatty: bool | None = None) -> Path:
    """Replace the Jev section of the global CLAUDE.md with the shipped text. Human only; keeps a backup."""
    _require_human("jade claude-md apply", stdin_isatty)
    p = target or Path.home() / ".claude" / "CLAUDE.md"
    text = p.read_text()
    start = text.find(JEV_HEADING)
    if start < 0:
        raise ValueError(f"heading not found in {p}: {JEV_HEADING}")
    end = text.find("\n## ", start + len(JEV_HEADING))
    end = len(text) if end < 0 else end + 1
    backup = p.with_name(p.name + f".bak-jade-{now():%Y%m%d%H%M%S}")
    shutil.copy2(p, backup)
    p.write_text(text[:start] + claude_md_section().rstrip("\n") + "\n\n" + text[end:])
    return backup


def launchd_plist(label: str) -> str:
    log = paths.home() / "maintain.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{jade_bin()}</string><string>maintain</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
"""


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
    gaps = pretool_matcher_gaps()
    if gaps is not None:
        rows.append(("pretool matcher", not gaps, "covers every gated tool" if not gaps else f"does not cover {', '.join(gaps)}: re-run `jade hooks install` in a terminal"))
    rows.append(("gate mode", True, paths.gate_mode()))
    rows.append(("host permission prompt", True, _permission_prompt_detail()))
    shim = shutil.which("jev")
    is_shim = bool(shim) and "jade" in (Path(shim).read_text(errors="replace")[:600] if Path(shim).is_file() else "")
    rows.append(("jev shim", is_shim, shim or "jev not on PATH"))
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
