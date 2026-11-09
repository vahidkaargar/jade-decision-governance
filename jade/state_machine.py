"""Decision state machine (docs/02 section 2). Pure transition function plus a journaled, idempotent `apply`."""

from __future__ import annotations

import json
from types import SimpleNamespace

from . import ledger
from .util import at_least, ts, ts_in

MAX_REVISIONS = 3
TERMINAL = {"SUCCEEDED", "COMPENSATED", "ROLLED_BACK", "REJECTED", "CANCELLED", "EXPIRED"}
WAITING = {"WAITING_HUMAN", "WAITING_DEPENDENCY", "RETRY_SCHEDULED"}
HUMAN_EVENTS = {"human_answer", "human_accept", "human_reject"}
T = {
    ("RECEIVED", "validate"): "VALIDATED",
    ("VALIDATED", "classify"): "CLASSIFIED",
    ("CLASSIFIED", "route"): "EVALUATING",
    ("EVALUATING", "fast_accept"): "ACCEPTED", ("EVALUATING", "fast_reject"): "REJECTED",
    ("EVALUATING", "jev_result_accept"): "ACCEPTED", ("EVALUATING", "jev_result_abstain"): "ABSTAINED",
    ("EVALUATING", "jev_result_reject"): "REJECTED", ("EVALUATING", "jev_error"): "NEEDS_EVIDENCE",
    ("EVALUATING", "deep_accept"): "ACCEPTED", ("EVALUATING", "deep_reject"): "REJECTED", ("EVALUATING", "deep_revise"): "NEEDS_REVISION",
    ("EVALUATING", "escalate"): "WAITING_HUMAN",
    ("ABSTAINED", "add_evidence"): "EVALUATING", ("NEEDS_EVIDENCE", "add_evidence"): "EVALUATING",
    ("ABSTAINED", "escalate"): "WAITING_HUMAN", ("NEEDS_EVIDENCE", "escalate"): "WAITING_HUMAN",
    ("NEEDS_REVISION", "revise"): "VALIDATED",
    ("ACCEPTED", "authorize"): "AUTHORIZED", ("ACCEPTED", "authorize_without_permit_high_risk"): "WAITING_HUMAN",
    ("AUTHORIZED", "execute"): "EXECUTING", ("AUTHORIZED", "context_changed"): "NEEDS_REVISION",
    ("EXECUTING", "observe"): "VERIFYING",
    ("VERIFYING", "verified"): "SUCCEEDED", ("VERIFYING", "failed"): "FAILED", ("VERIFYING", "unknown"): "OUTCOME_UNKNOWN",
    ("FAILED", "compensate"): "COMPENSATED", ("FAILED", "rollback"): "ROLLED_BACK", ("FAILED", "retry"): "RETRY_SCHEDULED",
    ("OUTCOME_UNKNOWN", "reconcile"): "VERIFYING",
    ("RETRY_SCHEDULED", "resume"): "EVALUATING", ("WAITING_DEPENDENCY", "resume"): "EVALUATING",
    ("WAITING_HUMAN", "human_accept"): "ACCEPTED", ("WAITING_HUMAN", "human_reject"): "REJECTED",
}


class IllegalTransition(ValueError):
    pass


def next_state(src: str, event: str, risk: str = "low", *, prev: str | None = None, permit_ok: bool = False,
               revision_count: int = 0) -> str:
    """The only place a decision status may change. Raises IllegalTransition for anything not in docs/02."""
    if src in TERMINAL:
        raise IllegalTransition(f"{src} is terminal")
    if event == "cancel":
        return "CANCELLED"
    if event == "deadline":
        if src in WAITING:
            return "EXPIRED"
        raise IllegalTransition(f"deadline applies only to waiting states, not {src}")
    if event == "human_answer":
        if src != "WAITING_HUMAN" or not prev:
            raise IllegalTransition("human_answer needs WAITING_HUMAN and a previous state")
        return prev
    dst = T.get((src, event))
    if dst is None:
        raise IllegalTransition(f"no transition {src} --{event}-->")
    if event == "authorize" and at_least(risk, "high") and not permit_ok:
        raise IllegalTransition("authorize at risk >= high needs an issued, unexpired, unused permit with the same plan hash")
    if event == "authorize_without_permit_high_risk" and not at_least(risk, "high"):
        raise IllegalTransition("risk below high needs no permit; use authorize")
    if event in ("add_evidence", "revise") and revision_count >= MAX_REVISIONS:
        raise IllegalTransition(f"revision_count reached {MAX_REVISIONS}; escalate instead")
    return dst


def _after_event_insert() -> None:
    """Fault-injection point for the crash-recovery test."""


def apply(path, ev: dict):
    """Apply one event to a stored decision inside a single transaction. Idempotent on `idempotency_key`."""
    db = path if isinstance(path, ledger.Ledger) else ledger.open(path)
    try:
        with db.tx() as con:
            row = con.execute("SELECT * FROM decisions WHERE decision_id=?", (ev["decision_id"],)).fetchone()
            if row is None:
                raise IllegalTransition(f"unknown decision {ev['decision_id']}")
            key = ev.get("idempotency_key")
            if key and con.execute("SELECT 1 FROM events WHERE idempotency_key=?", (key,)).fetchone():
                return SimpleNamespace(applied=False, status=row["status"])
            src = row["status"]
            dst = next_state(src, ev["event"], row["risk_level"] or "low", prev=row["prev_status"],
                             permit_ok=bool(ev.get("permit_ok")), revision_count=row["revision_count"])
            db.event(ev["decision_id"], ev["event"], prev=src, next=dst, guard=ev.get("guard"), actor=ev.get("actor", "runtime"),
                     key=key, detail=ev.get("detail"), con=con)
            _after_event_insert()
            doc = json.loads(row["doc_json"])
            doc["status"] = dst
            doc["timestamps"]["updated_at"] = ts()
            rev = row["revision_count"] + (1 if ev["event"] in ("add_evidence", "revise") else 0)
            doc["revision_count"] = rev
            if ev.get("patch"):
                doc.update(ev["patch"])
            expires = ts_in(ev["ttl_s"]) if ev.get("ttl_s") else row["expires_at"]
            doc["timestamps"]["expires_at"] = expires
            con.execute("UPDATE decisions SET status=?, prev_status=?, revision_count=?, doc_json=?, updated_at=?, expires_at=?, route=? "
                        "WHERE decision_id=?",
                        (dst, src if dst in WAITING else row["prev_status"], rev, json.dumps(doc, ensure_ascii=False), ts(), expires,
                         doc.get("route"), ev["decision_id"]))
            return SimpleNamespace(applied=True, status=dst)
    finally:
        if db is not path:
            db.close()
