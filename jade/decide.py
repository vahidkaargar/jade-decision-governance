"""Decision records: open, classify, link evaluations, record outcomes. Status changes go through state_machine.apply."""

from __future__ import annotations

import json
import re

from . import ledger, paths, policy, redact, schemas, state_machine
from .util import LEVELS, canonical, new_id, session_id, sha256, ts, ts_in, user

DEFAULT_TTL_S = 24 * 3600


def scrub(value):
    """A JSON-like value with every credential-shaped string redacted: what a decision, an outcome or an event may store (docs/05 T4, T15).
    Email addresses stay: this text never leaves the machine."""
    return redact.redact_obj(value, secrets_only=True)[0]


def open_decision(db: ledger.Ledger, objective: str, *, command: str | None = None, tool: str = "Bash", cwd: str = "",
                  actions: list[dict] | None = None, evidence_refs: list[str] | None = None, constraints: list[str] | None = None,
                  assumptions: list[str] | None = None, session: str | None = None, raise_risk_to: str | None = None,
                  route: str = "JEV", ttl_s: int = DEFAULT_TTL_S, assessment: policy.Assessment | None = None) -> dict:
    """Create a decision: RECEIVED -> VALIDATED -> CLASSIFIED, all journaled. The risk level comes from the
    ordered rules; a caller (or an advisory Jev signal) may raise it, never lower it."""
    a = assessment or (policy.assess(tool, {"command": command} if tool == "Bash" else {"file_path": command}, cwd) if command
                       else policy.Assessment(level="low", rule_id="R-99", rationale="No side effect declared"))
    level = a.level
    if raise_risk_to and LEVELS[raise_risk_to] > LEVELS[level]:
        level = raise_risk_to
    objective = scrub((objective or "").strip())[:2000]    # the risk level above was read from the raw command; what is stored is redacted
    evidence_refs = scrub([str(e)[:512] for e in (evidence_refs or [])])
    actions = scrub(actions or [{"name": "proceed", "canonical_action": "shell.exec" if command else "report.done",
                                 "description": scrub(command or objective)[:500], "eligible": True}])
    constraints, assumptions = scrub(list(constraints or [])), scrub(list(assumptions or []))
    doc = {
        "schema_version": "1.0.0", "decision_id": new_id("dec"), "task_id": new_id("task"), "tenant_scope": f"local:{user()}",
        "objective": objective if len(objective) >= 8 else objective.ljust(8, "."), "candidate_actions": actions,
        "constraints": constraints, "assumptions": assumptions, "evidence_refs": evidence_refs,
        "context_fingerprint": sha256(canonical({"evidence": evidence_refs, "plan": a.plan_hash or objective})),
        "context_freshness": {"observed_at": ts(), "ttl_s": 3600},
        "risk": {"level": level, "reversibility": a.reversibility, "environment": a.environment, "blast_radius": a.blast_radius,
                 "data_sensitivity": a.data_sensitivity, "permission_scope": "execute" if command else "read",
                 "rationale": a.rationale or "default", "rule_id": a.rule_id},
        "route": route, "evaluation_refs": [], "policy_version": paths.policy_version(), "status": "CLASSIFIED",
        "timestamps": {"created_at": ts(), "updated_at": ts(), "expires_at": ts_in(ttl_s)},
        "trace_id": "trace-" + new_id("t")[2:], "session_id": (session or session_id()).ljust(8, "-"), "revision_count": 0,
    }
    if a.ho_ids:   # the gate named human-only classes (live wiring, mods, R-17, the terminal panel): the decision says so
        doc["human_only"], doc["ho_ids"] = True, list(a.ho_ids)
    schemas.validate("decision", doc)
    with db.tx() as con:
        con.execute("INSERT INTO decisions(decision_id, status, risk_level, rule_id, route, session_id, plan_hash, hold_key, doc_json, created_at, updated_at, expires_at, source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (doc["decision_id"], "CLASSIFIED", level, a.rule_id, route, doc["session_id"], a.plan_hash or None, a.hold_key or a.plan_hash or None,
                     json.dumps(doc, ensure_ascii=False), ts(), ts(), doc["timestamps"]["expires_at"], db.source))
        db.event(doc["decision_id"], "open", prev=None, next="VALIDATED", guard="schema valid", actor="agent", con=con)
        db.event(doc["decision_id"], "classify", prev="VALIDATED", next="CLASSIFIED", guard=f"{a.rule_id}: {a.rationale}", con=con)
    return doc


def get(db: ledger.Ledger, decision_id: str) -> dict | None:
    row = db.one("SELECT doc_json FROM decisions WHERE decision_id=?", (decision_id,))
    return json.loads(row.doc_json) if row else None


def step(db: ledger.Ledger, decision_id: str, event: str, **kw):
    return state_machine.apply(db, {"decision_id": decision_id, "event": event, **kw})


def link_evaluation(db: ledger.Ledger, decision_id: str, call_id: str, verdict: str, error: bool = False) -> str:
    """Attach a Jev call to a decision and move it: route (if needed) then the matching jev_result_* event."""
    row = db.one("SELECT status FROM decisions WHERE decision_id=?", (decision_id,))
    if row is None:
        raise state_machine.IllegalTransition(f"unknown decision {decision_id}")
    if row.status == "CLASSIFIED":
        step(db, decision_id, "route", guard="route=JEV")
    elif row.status in ("ABSTAINED", "NEEDS_EVIDENCE"):
        step(db, decision_id, "add_evidence", guard=f"new evaluation {call_id}")
    event = "jev_error" if error else {"ACCEPT": "jev_result_accept", "ABSTAIN": "jev_result_abstain", "REJECT": "jev_result_reject"}[verdict]
    doc = get(db, decision_id)
    res = step(db, decision_id, event, guard=f"call {call_id}", patch={"evaluation_refs": doc["evaluation_refs"] + [call_id]})
    return res.status


def record_outcome(db: ledger.Ledger, decision_id: str, observation_type: str, success: bool | None, method: str, *,
                   evidence_refs: list[str] | None = None, measurements: dict | None = None, attribution: str = "direct",
                   caller_session: str | None = None) -> dict:
    """Store an outcome and advance the decision. Only an *observed* success reaches SUCCEEDED;
    an estimate stays in VERIFYING and an unknown result becomes OUTCOME_UNKNOWN (never success). The observe step, the outcome row and the
    closing step are one transaction: a crash leaves the decision where it was, not in VERIFYING with nothing recorded.
    `caller_session` (the CLI passes it) must be the session that opened the decision."""
    if observation_type == "unknown":
        success = None
    elif success is None:
        raise ValueError("observed/estimated outcomes need success true or false")
    out = {"schema_version": "1.0.0", "outcome_id": new_id("out"), "decision_id": decision_id, "execution_id": new_id("exec"),
           "observation_type": observation_type, "verification_method": scrub(method), "measurements": scrub(measurements or {}),
           "attribution_quality": attribution, "observed_at": ts(), "evidence_refs": scrub([str(e)[:512] for e in (evidence_refs or [])]),
           "success": success}
    schemas.validate("outcome", out)
    who = {"caller_session": caller_session} if caller_session is not None else {}
    with db.tx():
        row = db.one("SELECT status FROM decisions WHERE decision_id=?", (decision_id,))
        if row is None:
            raise state_machine.IllegalTransition(f"unknown decision {decision_id}")
        if row.status == "OUTCOME_UNKNOWN":
            step(db, decision_id, "reconcile", guard="external state read", actor="agent", **who)
        elif row.status == "EXECUTING":
            step(db, decision_id, "observe", guard=f"outcome {out['outcome_id']}", actor="agent", **who)
        elif row.status != "VERIFYING":
            raise state_machine.IllegalTransition(f"cannot record an outcome in status {row.status}")
        with db.tx() as con:
            con.execute("INSERT INTO outcomes(outcome_id, decision_id, doc_json, ts) VALUES(?,?,?,?)",
                        (out["outcome_id"], decision_id, json.dumps(out, ensure_ascii=False), ts()))
        if observation_type == "unknown":
            step(db, decision_id, "unknown", guard="observation_type=unknown", **who)
        elif success is False:
            step(db, decision_id, "failed", guard="success=false", **who)
        elif observation_type == "observed":
            step(db, decision_id, "verified", guard="observed and success=true", **who)
    return out


def sweep(db: ledger.Ledger) -> dict:
    """End decisions past their deadline (SessionStart and daily maintenance): a state that waits for something that never came expires; an
    execution or verification with no outcome becomes OUTCOME_UNKNOWN, never success and never merely expired (SI-07)."""
    now_s, n = ts(), {"expired": 0, "unknown": 0}
    swept = sorted({*state_machine.EXPIRES, "EXECUTING", "VERIFYING"})   # a finished decision is never listed: this runs in every gated call, and most rows are finished
    for row in db.all(f"SELECT decision_id, status FROM decisions WHERE status IN ({','.join('?' * len(swept))}) AND expires_at < ?", (*swept, now_s)):  # noqa: S608 - placeholders only
        try:
            with db.tx():
                if row.status in state_machine.EXPIRES:
                    step(db, row.decision_id, "deadline", guard="expires_at passed")
                    n["expired"] += 1
                elif row.status == "EXECUTING":
                    step(db, row.decision_id, "observe", guard="deadline without outcome")
                    step(db, row.decision_id, "unknown", guard="no outcome recorded before deadline")
                    n["unknown"] += 1
                elif row.status == "VERIFYING":
                    step(db, row.decision_id, "unknown", guard="deadline while verifying: no verdict recorded")
                    n["unknown"] += 1
        except state_machine.IllegalTransition:
            continue
    return n


def canonical_action(rule_id: str, subject: str) -> str:
    m = re.match(r"\s*(?:sudo\s+)?([a-z]+)(?:\s+([a-z_]+))?", subject)
    if not m:
        return "shell.exec"
    return m.group(1) + ("." + m.group(2).replace("-", "_") if m.group(2) else "")
