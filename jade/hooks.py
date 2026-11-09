"""Claude Code hook handlers. Enforcement and reminders only; capture happens at the adapter (ADR-04).

PreToolUse is the side-effect boundary: exit 2 blocks the tool call and the reason goes to the model.
Any internal error fails closed when the command looks high-risk and open otherwise (threat T17).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass

from . import paths, policy
from .util import at_least, now, ts

FALLBACK_HIGH = re.compile(
    r"git push|\bgh (pr|release)\b|\bglab mr\b|(^|[;&| ])(ssh|scp|sftp) |\brsync\b.* \S+:|\brm -rf\b|\bpfctl\b|\bsudo\b|\blaunchctl\b"
    r"|\bkubectl\b|\bterraform\b|\bhelm\b|docker (push|compose)|composio execute|(npm|cargo) publish|ovault secret|security (add|delete)-"
    r"|\.claude/settings|/etc/|LaunchAgents|LaunchDaemons")
BARE_PUSH = re.compile(r"^git(?: -C (\S+))? push\b(.*)$")
BYPASS = re.compile(r"\bcurl\b.*openrouter\.ai/api/alpha/decisions")
REMIND_WINDOW_S = 6 * 3600


@dataclass
class HookResult:
    exit_code: int = 0
    stderr: str = ""
    stdout: str = ""


def _classify(tool: str, tool_input: dict, cwd: str = "") -> policy.Assessment:
    return policy.assess(tool, tool_input, cwd)


def _on_default_branch(cwd: str, command: str) -> bool:
    """A bare `git push` (no refspec) while the current branch is main/master (invariant SI-08)."""
    for seg in policy.segments(command):
        m = BARE_PUSH.match(policy._bare(seg))
        if not m:
            continue
        positional = [w for w in m.group(2).split() if not w.startswith("-")]
        if len(positional) > 1:
            continue
        try:
            out = subprocess.run(["git", "-C", m.group(1) or cwd or ".", "symbolic-ref", "--short", "-q", "HEAD"],
                                 capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return False
        if out.stdout.strip() in ("main", "master"):
            return True
    return False


def pretool(event: dict, permit=None) -> HookResult:
    mode = paths.gate_mode()
    if mode == "off":
        return HookResult()
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    if tool != "Bash" and tool not in policy.WRITE_TOOLS and not policy.is_mcp(tool):
        return HookResult()
    cwd = event.get("cwd") or ""
    subject = str(tool_input.get("command") or tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if policy.is_mcp(tool):
        subject = policy.mcp_command(tool_input) or json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    try:
        a = _classify(tool, tool_input, cwd)
        if (tool == "Bash" or policy.is_mcp(tool)) and "git" in subject and "push" in subject and "SI-08" not in a.blocked and _on_default_branch(cwd, subject):
            a.blocked.append("SI-08")
    except Exception as e:  # noqa: BLE001 - fail closed for anything that looks high-risk, open otherwise
        if FALLBACK_HIGH.search(subject):
            return _verdict(mode, f"JADE gate: internal error ({type(e).__name__}) while classifying a command that looks high-risk. "
                                  "Failing closed. Run `jade check`; a human can bypass with JADE_GATE_MODE=advisory.")
        return HookResult()
    if a.blocked:
        _journal("gate", "safety_block", a, event)
        return _verdict(mode, f"JADE safety invariant {', '.join(a.blocked)} blocks this {tool} call. No permit can authorize it. "
                              f"Rule text: policies/safety.yaml. Do not rephrase the command to get around the invariant; ask the human.")
    if not at_least(a.level, policy.permit_floor()):
        return HookResult()
    return _gate(mode, tool, a, event, permit)


def _verdict(mode: str, message: str) -> HookResult:
    if mode == "advisory":
        return HookResult(0, "[advisory, not blocked] " + message)
    return HookResult(2, message)


def _journal(entity: str, kind: str, a: policy.Assessment, event: dict) -> None:
    try:
        from . import ledger

        db = ledger.open()
        db.event(entity, kind, guard=f"{a.rule_id} {a.level} {','.join(a.blocked)}".strip(), actor="hook",
                 detail={"session": event.get("session_id"), "plan_hash": a.plan_hash, "tool": event.get("tool_name")})
        db.close()
    except Exception:  # noqa: BLE001, S110 - journaling must never change the gate verdict
        pass


def _gate(mode: str, tool: str, a: policy.Assessment, event: dict, permit) -> HookResult:
    from . import decide, ledger, permits

    db = ledger.open()
    try:
        permits.sweep(db)
        p = permit or permits.find_usable(db, a.plan_hash)
        if p is not None and permits.consume(db, p.permit_id, a.plan_hash):
            row = db.one("SELECT decision_id, status FROM decisions WHERE decision_id=?", (p.decision_id,))
            if row and row.status == "WAITING_HUMAN":
                decide.step(db, row.decision_id, "human_accept", guard=f"permit {p.permit_id} approved from a terminal", actor="human")
                decide.step(db, row.decision_id, "authorize", permit_ok=True, guard=f"permit {p.permit_id} issued, unexpired, unused, plan hash equal")
                decide.step(db, row.decision_id, "execute", guard="PreToolUse saw the matching command; permit consumed", actor="hook", ttl_s=3600)
            return HookResult(0, f"JADE: permit {p.permit_id} consumed for this {tool} call.")
        if mode == "advisory":
            db.event("gate", "would_block", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"session": event.get("session_id"), "plan_hash": a.plan_hash})
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
                                                                                 "rule": a.rule_id, "rationale": a.rationale}), p.permit_id))
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

    db = ledger.open()
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

    db = ledger.open()
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

    db = ledger.open()
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


def main(name: str) -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except ValueError:
        event = {}
    try:
        res = HANDLERS[name](event)
    except Exception as e:  # noqa: BLE001 - last line of defence; see module docstring
        subject = json.dumps(event.get("tool_input") or {})
        if name == "pretool" and paths.gate_mode() == "enforce" and FALLBACK_HIGH.search(subject):
            print(f"JADE gate: internal error ({type(e).__name__}: {e}); failing closed for a high-risk-looking call. Run `jade check`.", file=sys.stderr)
            return 2
        print(f"jade hook {name}: internal error ({type(e).__name__}: {e}); not blocking", file=sys.stderr)
        return 0
    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)
    return res.exit_code
