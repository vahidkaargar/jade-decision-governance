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
# An agent drives a decision only through the events below `agent_may`. The rest belong to someone else: a human at a terminal, the hook
# (`execute` needs the matching command and a consumed permit at risk >= high; `context_changed` and `deadline` are raised by the runtime), or the
# outcome record (`observe`, `verified`, `failed`, `unknown`, `reconcile` follow only from `jade decide outcome`, which stores the observation first).
OUTCOME_EVENTS = {"observe", "verified", "failed", "unknown", "reconcile"}
RUNTIME_EVENTS = {"deadline", "context_changed"}
# `deadline` ends any state that is waiting for something that may never come. EXECUTING, VERIFYING and OUTCOME_UNKNOWN are not in it: a call that
# started and never reported is unknown (SI-07), never expired, and an unknown outcome is left only by `reconcile` or a human `cancel`.
EXPIRES = {"RECEIVED", "VALIDATED", "CLASSIFIED", "EVALUATING", "ABSTAINED", "NEEDS_EVIDENCE", "NEEDS_REVISION", "ACCEPTED", "AUTHORIZED", "FAILED",
           "WAITING_HUMAN", "WAITING_DEPENDENCY", "RETRY_SCHEDULED"}
# States in which `cancel` would release the SI-07 hold on a command whose outcome is not known: only a human does that.
HOLDING = {"EXECUTING", "VERIFYING", "OUTCOME_UNKNOWN"}
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
    ("NEEDS_REVISION", "revise"): "VALIDATED", ("NEEDS_REVISION", "escalate"): "WAITING_HUMAN",
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
        if src in EXPIRES:
            return "EXPIRED"
        raise IllegalTransition(f"deadline does not apply to {src}: a call that started and has no outcome is unknown, not expired")
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


def agent_may(event: str, src: str | None = None, risk: str = "low") -> bool:
    """May an agent session (not a human, not the hook) emit this event through the CLI? `src` and `risk` narrow `execute` and `cancel`."""
    if event in HUMAN_EVENTS or event in OUTCOME_EVENTS or event in RUNTIME_EVENTS:
        return False
    if event == "execute":  # below the permit floor nothing but the decision's own bookkeeping stands behind it; above it only the hook, with a permit
        return not at_least(risk, "high")
    if event == "cancel":
        return src not in HOLDING
    return event in {e for _, e in T} | {"cancel"}


def _same(stored: str, caller: str) -> bool:
    pad = lambda x: (x or "").ljust(8, "-")  # noqa: E731 - decisions store the session id padded to the schema minimum
    return pad(stored) == pad(caller)


def _observed_success(con, decision_id: str) -> bool:
    """The latest outcome of the decision is an observed success."""
    r = con.execute("SELECT json_extract(doc_json,'$.observation_type') AS t, json_extract(doc_json,'$.success') AS ok FROM outcomes "
                    "WHERE decision_id=? ORDER BY rowid DESC LIMIT 1", (decision_id,)).fetchone()
    return bool(r and r["t"] == "observed" and r["ok"] == 1)


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
            caller = ev.get("caller_session")
            if caller is not None and ev["event"] not in ({"cancel"} | HUMAN_EVENTS) and not _same(row["session_id"], caller):
                raise IllegalTransition("this decision belongs to another session; a session may only cancel it or answer for the human")
            if ev["event"] == "deadline" and row["expires_at"] > ts():
                raise IllegalTransition("deadline: expires_at has not passed")
            if ev["event"] == "verified" and not _observed_success(con, ev["decision_id"]):
                raise IllegalTransition("SUCCEEDED needs a recorded outcome with observation_type=observed and success=true")
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
            if dst in TERMINAL or ev["event"] == "context_changed":
                from . import permits

                permits.revoke_for_decision(db, con, ev["decision_id"], f"decision {dst} ({ev['event']})")
            return SimpleNamespace(applied=True, status=dst)
    finally:
        if db is not path:
            db.close()
