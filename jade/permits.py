"""Execution permits (ADR-05). A permit authorizes exactly one execution of one plan hash.

High and critical permits start PENDING and become ISSUED only when a human runs `jade permit approve`
in an interactive terminal. Nothing a model outputs can create an ISSUED high-risk permit.
"""

from __future__ import annotations

import json
import secrets
from types import SimpleNamespace

from . import ledger, paths, schemas
from .util import at_least, is_human_tty, new_id, ts, ts_in, user

DEFAULT_TTL_S = 1800


class PermitError(ValueError):
    pass


class ApprovalRefused(PermissionError):
    """Approval attempted from something that is not an interactive human terminal."""


def _db(path):
    return (path, False) if isinstance(path, ledger.Ledger) else (ledger.open(path), True)


def _ns(row):
    if row is None:
        return None
    doc = json.loads(row.doc_json)
    doc.update(state=row.state, uses=row.uses, expires_at=row.expires_at, issued_at=row.issued_at)
    return SimpleNamespace(**doc, ttl_s=row.ttl_s, action=row.action, scope=row.scope)


def _create(path, state: str, action: str, plan_hash: str, ttl_s: int, risk: str, decision_id: str | None, scope: str,
            environment: str, principal: str):
    doc = {"schema_version": "1.0.0", "permit_id": new_id("prm"), "decision_id": decision_id or new_id("dec"), "principal": principal,
           "authorized_action": action, "resource_scope": scope or "*", "environment": environment, "plan_hash": plan_hash,
           "preconditions": [{"kind": "policy_version", "fingerprint": paths.policy_version()}], "policy_version": paths.policy_version(),
           "issued_at": ts(), "expires_at": ts_in(ttl_s), "nonce": secrets.token_hex(16), "usage_limit": 1, "uses": 0, "state": state,
           "risk_level": risk}
    schemas.validate("permit", doc)
    db, own = _db(path)
    try:
        with db.tx() as con:
            con.execute("INSERT INTO permits(permit_id, decision_id, state, risk_level, plan_hash, action, scope, uses, ttl_s, doc_json, issued_at, expires_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (doc["permit_id"], doc["decision_id"], state, risk, plan_hash, action, scope, 0, ttl_s,
                         json.dumps(doc, ensure_ascii=False), doc["issued_at"], doc["expires_at"]))
            db.event(doc["permit_id"], "permit_" + state.lower(), next=state, guard=f"risk={risk}", actor=principal, con=con)
        return get(db, doc["permit_id"])
    finally:
        if own:
            db.close()


def issue(path, action: str, plan_hash: str, ttl_s: int = DEFAULT_TTL_S, risk: str = "low", *, decision_id: str | None = None,
          scope: str = "*", environment: str = "local"):
    """Issue directly. Allowed only below `high`; high and critical must go through request + human approval."""
    if at_least(risk, "high"):
        raise PermitError("high and critical permits are never issued directly: use request(), then a human approves from a terminal")
    return _create(path, "ISSUED", action, plan_hash, ttl_s, risk, decision_id, scope, environment, f"agent:{user()}")


def request(path, action: str, plan_hash: str, risk: str = "high", ttl_s: int = DEFAULT_TTL_S, *, decision_id: str | None = None,
            scope: str = "*", environment: str = "local"):
    """Create a PENDING permit, or return the existing unexpired PENDING one for the same plan."""
    db, own = _db(path)
    try:
        row = db.one("SELECT * FROM permits WHERE plan_hash=? AND state='PENDING' AND expires_at>? ORDER BY issued_at DESC", (plan_hash, ts()))
        if row:
            return _ns(row)
        return _create(db, "PENDING", action, plan_hash, ttl_s, risk, decision_id, scope, environment, f"agent:{user()}")
    finally:
        if own:
            db.close()


def get(path, permit_id: str):
    db, own = _db(path)
    try:
        return _ns(db.one("SELECT * FROM permits WHERE permit_id=?", (permit_id,)))
    finally:
        if own:
            db.close()


def approve(path, permit_id: str, stdin_isatty: bool | None = None):
    """PENDING -> ISSUED. Refuses unless stdin is an interactive terminal and no agent harness is in the environment."""
    if not is_human_tty(stdin_isatty):
        raise ApprovalRefused("permit approval needs a human at an interactive terminal; an agent session cannot approve")
    db, own = _db(path)
    try:
        with db.tx() as con:
            row = con.execute("SELECT * FROM permits WHERE permit_id=?", (permit_id,)).fetchone()
            if row is None:
                raise PermitError(f"unknown permit {permit_id}")
            if row["state"] != "PENDING":
                raise PermitError(f"permit {permit_id} is {row['state']}, not PENDING")
            doc = json.loads(row["doc_json"])
            doc.update(state="ISSUED", principal=f"human:{user()}", approval_reference=f"tty:{user()}@{ts()}", issued_at=ts(),
                       expires_at=ts_in(row["ttl_s"]))
            schemas.validate("permit", doc)
            con.execute("UPDATE permits SET state='ISSUED', doc_json=?, issued_at=?, expires_at=? WHERE permit_id=?",
                        (json.dumps(doc, ensure_ascii=False), doc["issued_at"], doc["expires_at"], permit_id))
            db.event(permit_id, "permit_approved", prev="PENDING", next="ISSUED", guard=doc["approval_reference"], actor=doc["principal"], con=con)
        return get(db, permit_id)
    finally:
        if own:
            db.close()


def consume(path, permit_id: str, plan_hash: str) -> bool:
    """Atomically use an ISSUED, unexpired, unused permit for exactly this plan hash. True once, then never again."""
    db, own = _db(path)
    try:
        with db.tx() as con:
            cur = con.execute("UPDATE permits SET state='CONSUMED', uses=1 WHERE permit_id=? AND state='ISSUED' AND uses=0 AND plan_hash=? AND expires_at>?",
                              (permit_id, plan_hash, ts()))
            if cur.rowcount == 1:
                db.event(permit_id, "permit_consumed", prev="ISSUED", next="CONSUMED", guard="plan_hash equal, unexpired, unused", actor="hook", con=con)
                return True
        return False
    finally:
        if own:
            db.close()


def find_usable(path, plan_hash: str):
    db, own = _db(path)
    try:
        return _ns(db.one("SELECT * FROM permits WHERE plan_hash=? AND state='ISSUED' AND uses=0 AND expires_at>? ORDER BY issued_at DESC", (plan_hash, ts())))
    finally:
        if own:
            db.close()


def revoke(path, permit_id: str, reason: str = "revoked") -> bool:
    db, own = _db(path)
    try:
        with db.tx() as con:
            cur = con.execute("UPDATE permits SET state='REVOKED' WHERE permit_id=? AND state IN ('PENDING','ISSUED')", (permit_id,))
            if cur.rowcount:
                db.event(permit_id, "permit_revoked", next="REVOKED", guard=reason, con=con)
            return bool(cur.rowcount)
    finally:
        if own:
            db.close()


def sweep(path) -> int:
    db, own = _db(path)
    try:
        with db.tx() as con:
            return con.execute("UPDATE permits SET state='EXPIRED' WHERE state IN ('PENDING','ISSUED') AND expires_at<=?", (ts(),)).rowcount
    finally:
        if own:
            db.close()


def listing(path, states=("PENDING", "ISSUED")):
    db, own = _db(path)
    try:
        marks = ",".join("?" * len(states))
        return [_ns(r) for r in db.all(f"SELECT * FROM permits WHERE state IN ({marks}) AND expires_at>? ORDER BY issued_at", (*states, ts()))]  # noqa: S608
    finally:
        if own:
            db.close()
