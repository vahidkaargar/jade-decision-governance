"""Execution permits (ADR-05). A permit authorizes exactly one execution of one plan hash.

High and critical permits start PENDING and become ISSUED only when a human runs `jade permit approve`
in an interactive terminal. Nothing a model outputs can create an ISSUED high-risk permit through this module.

What a permit is checked against at use (`try_consume`), all in one transaction: the plan hash of the command being run now, the risk level
the classifier gives that command now (a permit never covers a more severe command than the one approved), a human principal and a tty
approval reference whenever that level is high or critical, the policy version and every recorded precondition, the session and agent the
request came from, the decision it belongs to (a cancelled, expired or finished decision authorizes nothing), and a seal: an HMAC over
the permit's fields made when it was issued with a key in the JADE home, so a row edited with SQL afterwards is refused. The key is a file
the same OS user can read: the seal stops a casual or injected row edit and not a process that reads the key (docs/05 T19, W-41).
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import unicodedata
from types import SimpleNamespace

from . import ledger, paths, policy, redact, schemas
from .state_machine import TERMINAL as TERMINAL_DECISION
from .util import at_least, canonical, human_tty_refusal, new_id, parse_ts, ts, ts_in, user

DEFAULT_TTL_S = 1800
MAX_SUBJECT = 20000        # the most command text a permit stores and the approval screen shows; a longer command is never approved
LIVE_DECISION = ("WAITING_HUMAN", "AUTHORIZED")  # decision states in which a permit may be approved or consumed
SEAL_KEY_FILE = "permit-seal.key"


class PermitError(ValueError):
    pass


class ApprovalRefused(PermissionError):
    """Approval attempted from something that is not an interactive human terminal."""


def _db(path):
    return (path, False) if isinstance(path, ledger.Ledger) else (ledger.open(path), True)


def _doc(row, *, validate: bool = True) -> dict | None:
    """The permit document of a row, validated against its schema on every read (W-57), or None when the stored text is unreadable, does not match
    the schema or names another permit. A permit without a document is never valid: every caller treats None as "not usable". `validate=False` only
    parses (callers that give a more specific refusal first, then validate)."""
    try:
        return schemas.load("permit", _col(row, "doc_json"), id_field="permit_id", id_value=_col(row, "permit_id"), validate=validate)
    except schemas.Unreadable:
        return None


def _ns(row, doc: dict | None = None):
    if row is None:
        return None
    doc = doc if doc is not None else _doc(row)
    if doc is None:
        return None
    doc = dict(doc)
    doc.update(state=row.state, uses=row.uses, expires_at=row.expires_at, issued_at=row.issued_at)
    return SimpleNamespace(**doc, ttl_s=row.ttl_s, action=row.action, scope=row.scope)


def _loads(scope) -> dict:
    try:
        ctx = json.loads(scope)
    except (TypeError, ValueError):
        return {}
    return ctx if isinstance(ctx, dict) else {}


def _col(row, name: str):
    return row[name] if isinstance(row, sqlite3.Row) else getattr(row, name)


def _seal_key(db, create: bool = False) -> bytes | None:
    """The permit seal key (32 random bytes, mode 600) next to the ledger. Only issuing and approving create it, never a check: a missing key
    means no permit can be verified, not that a new one is made."""
    f = db.path.parent / SEAL_KEY_FILE
    try:
        key = f.read_bytes()
    except OSError:
        key = None
    if key is not None and len(key) >= 32:
        return key
    if key is not None:   # a key file that is empty or truncated: never a key, and never silently replaced (every permit sealed with the old one would fail)
        if create:
            raise PermitError(f"the permit seal key {f} is {len(key)} bytes, not the 32 or more it must hold (an empty or truncated file); no permit can be sealed or "
                              "approved until a human moves it aside, after which pending permits must be requested again")
        return None
    if not create:
        return None
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")   # written whole under a temp name and linked into place: no process ever sees a half-written key
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    key = secrets.token_bytes(32)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, f)
    except FileExistsError:  # another process created it between the read and now: theirs is the key
        return _seal_key(db, create=True)
    finally:
        tmp.unlink(missing_ok=True)
    return key


def _seal_of(key: bytes, row, doc: dict) -> str:
    """HMAC-SHA256 over everything that decides what the permit authorizes and until when. `state` and `uses` are left out: they change by design
    and are checked on their own (and a consumed permit has a `permit_consumed` event)."""
    body = canonical({"permit_id": _col(row, "permit_id"), "decision_id": _col(row, "decision_id"), "risk": _col(row, "risk_level"),
                      "plan_hash": _col(row, "plan_hash"), "action": _col(row, "action"), "scope": _col(row, "scope"), "ttl_s": _col(row, "ttl_s"),
                      "issued_at": _col(row, "issued_at"), "expires_at": _col(row, "expires_at"),
                      "principal": doc.get("principal"), "approval_reference": doc.get("approval_reference"), "policy_version": doc.get("policy_version"),
                      "preconditions": doc.get("preconditions"), "nonce": doc.get("nonce"), "resource_scope": doc.get("resource_scope"),
                      "environment": doc.get("environment"), "authorized_action": doc.get("authorized_action")})
    return hmac.new(key, body.encode(), "sha256").hexdigest()


def _seal_refusal(db, row, doc: dict) -> str | None:
    key = _seal_key(db)
    if key is None:
        return "no permit seal key exists, so the permit cannot be verified (a permit approved before the seal was added must be approved again)"
    have = doc.get("seal")
    if not isinstance(have, str) or not hmac.compare_digest(have, _seal_of(key, row, doc)):
        return "the permit row does not match its seal (it was edited after it was issued); it is not used"
    return None


def ctx_of(p) -> dict:
    """The review context stored with a permit (what the approval screen shows), or {} when there is none."""
    return _loads(p.scope)


def make_context(tool: str, a: policy.Assessment, event: dict | None = None, *, cwd: str = "", subject: str | None = None) -> dict:
    """What a human needs to decide, plus what the gate needs to recheck. `subject` is the full text shown (never cut silently: a command
    longer than MAX_SUBJECT is flagged `truncated` and cannot be approved); `hash_subject` and `hash_cwd` are exactly what the plan hash covers."""
    shown = a.subject if subject is None else subject
    ev = event or {}
    clean, hits = redact.redact(shown, secrets_only=True)   # a secret is never stored; see `_verify` for what that means for approval
    ctx = {"tool": tool, "subject": clean[:MAX_SUBJECT], "length": len(shown), "truncated": len(shown) > MAX_SUBJECT, "cwd": cwd,
           "hash_cwd": a.plan_cwd, "rule": a.rule_id, "rationale": a.rationale}
    if a.plan_subject != shown:
        hash_clean, n = redact.redact(a.plan_subject, secrets_only=True)
        hits += n
        ctx["hash_subject"] = hash_clean[:MAX_SUBJECT * 2]
        ctx["truncated"] = ctx["truncated"] or len(a.plan_subject) > MAX_SUBJECT * 2
    if hits:
        ctx["redacted"] = hits
    if ev.get("session_id") is not None or ev.get("agent_id") is not None:  # a hook call: bind the permit to the caller
        ctx.update(bound=True, session_id=ev.get("session_id"), agent_id=ev.get("agent_id"), permission_mode=ev.get("permission_mode"))
    return ctx


def _preconditions(fingerprints: dict[str, str] | None) -> list[dict]:
    pre = [{"kind": "policy_version", "fingerprint": paths.policy_version()}]
    pre += [{"kind": k, "fingerprint": str(v)} for k, v in sorted((fingerprints or {}).items())]
    return pre


def _create(path, state: str, action: str, plan_hash: str, ttl_s: int, risk: str, decision_id: str | None, scope: str, ctx: dict | None,
            environment: str, principal: str, fingerprints: dict[str, str] | None = None):
    doc = {"schema_version": "1.0.0", "permit_id": new_id("prm"), "decision_id": decision_id or new_id("dec"), "principal": principal,
           "authorized_action": action, "resource_scope": ((ctx or {}).get("cwd") or scope or "*")[:200] or "*", "environment": environment,
           "plan_hash": plan_hash, "preconditions": _preconditions(fingerprints), "policy_version": paths.policy_version(),
           "issued_at": ts(), "expires_at": ts_in(ttl_s), "nonce": secrets.token_hex(16), "usage_limit": 1, "uses": 0, "state": state,
           "risk_level": risk}
    schemas.validate("permit", doc)
    db, own = _db(path)
    try:
        stored_scope = json.dumps(ctx) if ctx else scope
        if state == "ISSUED":
            row = SimpleNamespace(permit_id=doc["permit_id"], decision_id=doc["decision_id"], risk_level=risk, plan_hash=plan_hash, action=action,
                                  scope=stored_scope, ttl_s=ttl_s, issued_at=doc["issued_at"], expires_at=doc["expires_at"])
            doc["seal"] = _seal_of(_seal_key(db, create=True), row, doc)
        with db.tx() as con:
            con.execute("INSERT INTO permits(permit_id, decision_id, state, risk_level, plan_hash, action, scope, uses, ttl_s, doc_json, issued_at, expires_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (doc["permit_id"], doc["decision_id"], state, risk, plan_hash, action, stored_scope, 0, ttl_s,
                         json.dumps(doc, ensure_ascii=False), doc["issued_at"], doc["expires_at"]))
            db.event(doc["permit_id"], "permit_" + state.lower(), next=state, guard=f"risk={risk}", actor=principal, con=con)
        return get(db, doc["permit_id"])
    finally:
        if own:
            db.close()


def issue(path, action: str, plan_hash: str, ttl_s: int = DEFAULT_TTL_S, risk: str = "low", *, decision_id: str | None = None,
          scope: str = "*", environment: str = "local"):
    """Issue directly. Allowed only below `high`; high and critical must go through request + human approval. The hook enforces the same
    rule when it uses a permit: a permit without a human principal and a tty approval reference never covers a high or critical command, whatever
    risk this row claims."""
    if at_least(risk, "high"):
        raise PermitError("high and critical permits are never issued directly: use request(), then a human approves from a terminal")
    return _create(path, "ISSUED", action, plan_hash, ttl_s, risk, decision_id, scope, None, environment, f"agent:{user()}")


def _fingerprints_of(doc: dict) -> dict[str, str]:
    return {p["kind"]: p["fingerprint"] for p in doc.get("preconditions", []) if p["kind"] != "policy_version"}


def _current(doc: dict, fingerprints: dict[str, str] | None) -> bool:
    """The permit was requested under the policy and the preconditions that hold now."""
    return doc.get("policy_version") == paths.policy_version() and _fingerprints_of(doc) == {k: str(v) for k, v in (fingerprints or {}).items()}


def _same_request(row, doc: dict, ctx: dict, want_ctx: dict | None, fingerprints: dict[str, str] | None) -> bool:
    """An existing PENDING permit may be shown again only when it is still the same request: same preconditions, same policy, same caller."""
    if not _current(doc, fingerprints):
        return False
    return not (want_ctx and want_ctx.get("bound")) or (ctx.get("session_id"), ctx.get("agent_id")) == (want_ctx.get("session_id"), want_ctx.get("agent_id"))


def pending_for(path, plan_hash: str, *, context: dict | None = None, fingerprints: dict[str, str] | None = None):
    """The unexpired PENDING permit for this plan that is still the same request, else None."""
    db, own = _db(path)
    try:
        for row in db.all("SELECT * FROM permits WHERE plan_hash=? AND state='PENDING' AND expires_at>? ORDER BY issued_at DESC", (plan_hash, ts())):
            doc = _doc(row)
            if doc is not None and _same_request(row, doc, ctx_of(_ns(row, doc)), context, fingerprints):
                return _ns(row, doc)
        return None
    finally:
        if own:
            db.close()


def request(path, action: str, plan_hash: str, risk: str = "high", ttl_s: int = DEFAULT_TTL_S, *, decision_id: str | None = None,
            scope: str = "*", environment: str = "local", context: dict | None = None, fingerprints: dict[str, str] | None = None):
    """Create a PENDING permit, or return the existing unexpired PENDING one for the same request. A PENDING permit for the same plan that is
    no longer current (the repository moved, the policy changed) is revoked: the human approves what is true now. A PENDING permit of another
    session or agent is left alone: each caller gets its own, and each id the human is shown stays valid."""
    db, own = _db(path)
    try:
        row = pending_for(db, plan_hash, context=context, fingerprints=fingerprints)
        if row:
            return row
        for stale in db.all("SELECT permit_id, doc_json FROM permits WHERE plan_hash=? AND state='PENDING'", (plan_hash,)):
            doc = _doc(stale)
            if doc is None:   # a record that cannot be read is never shown to the human; a fresh request replaces it
                revoke(db, stale.permit_id, "superseded: the stored record is unreadable or does not match the permit schema")
            elif not _current(doc, fingerprints):  # a request by another session stays: two agents must not keep revoking each other's
                revoke(db, stale.permit_id, "superseded: the policy or a recorded precondition changed since it was requested")
        return _create(db, "PENDING", action, plan_hash, ttl_s, risk, decision_id, scope, context, environment, f"agent:{user()}", fingerprints)
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


# ---- the approval screen -------------------------------------------------------------------------------
def printable(text: str) -> str:
    """The text with every character that could redraw the terminal or hide itself written out as an escape: control characters (ESC, CR,
    backspace), format characters (bidirectional overrides, zero-width), unpaired surrogates, line and paragraph separators and every space
    other than U+0020. A newline stays a newline so that a multi-line command reads as one."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch == "\n" or (ch != "\t" and cat not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") and (cat != "Zs" or ch == " ")):
            out.append(ch)
        else:
            n = ord(ch)
            out.append(f"\\x{n:02x}" if n < 0x100 else f"\\u{n:04x}" if n < 0x10000 else f"\\U{n:08x}")
    return "".join(out)


def verify_scope(p) -> dict:
    """The review context, after checking that it is what the plan hash covers. Raises PermitError when it is missing, was cut, or does not
    hash to the permit's plan hash: the human would be approving something other than what is on the screen."""
    return _verify(ctx_of(p), p.plan_hash)


def _verify(ctx: dict, plan_hash: str) -> dict:
    if "subject" not in ctx or "tool" not in ctx:
        raise PermitError("this permit carries no command to review; ask the agent to request it again")
    if ctx.get("redacted"):
        raise PermitError(f"the command contains {ctx['redacted']} secret-shaped value(s) that JADE does not store, so it cannot be shown in full and is not approved. "
                          "Ask the agent to pass the secret in an environment variable or a file and request the command again without it")
    if ctx.get("truncated"):
        raise PermitError(f"the command is {ctx.get('length', '?')} characters and cannot be shown in full (limit {MAX_SUBJECT}); "
                          "it is not approved. Ask the agent to put it in a script file you can read, then request the script")
    if policy.plan_hash(ctx["tool"], ctx.get("hash_subject", ctx["subject"]), ctx.get("hash_cwd", "")) != plan_hash:
        raise PermitError("the command on file does not match the permit's plan hash (the permit was not created by the gate); it is not approved")
    return ctx


def review_text(p, now_s: str | None = None) -> str:
    """The whole approval screen: every character of the command (escaped), its length, the first 12 characters of the plan hash, the working
    directory, the rule, the age of the request and the decision's state. Raises PermitError instead of showing a partial command."""
    ctx = verify_scope(p)
    age = ""
    try:
        secs = int((parse_ts(now_s or ts()) - parse_ts(p.issued_at)).total_seconds()) if p.state == "PENDING" else 0
        age = f"requested {secs // 60} min {secs % 60} s ago" if secs >= 0 else ""
    except ValueError:
        pass
    body = printable(ctx["subject"]).replace("\n", "\n  | ")
    return (f"Permit   {p.permit_id}   risk {p.risk_level}   environment {p.environment}   {age}\n"
            f"Rule     {printable(str(ctx.get('rule', '?')))}: {printable(str(ctx.get('rationale', '')))}\n"
            f"Tool     {printable(ctx['tool'])}   cwd {printable(str(ctx.get('cwd', '')))}\n"
            f"Plan     sha256 {p.plan_hash[:12]}   {ctx.get('length', len(ctx['subject']))} characters, shown in full below\n"
            f"Command\n  | {body}\n"
            f"Approving allows exactly this call once within {p.ttl_s // 60} minutes.")


# ---- lifecycle ---------------------------------------------------------------------------------------------
def _decision_refusal(con, decision_id: str) -> str | None:
    """Why the decision a permit belongs to cannot take it, or None (no such decision row counts as live: a permit requested from the CLI
    belongs to no decision)."""
    d = con.execute("SELECT status, expires_at FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if d is None:
        return None
    if d["status"] not in LIVE_DECISION:
        return f"its decision is {d['status']}"
    return f"its decision is {d['status']} but past its deadline ({d['expires_at']})" if d["expires_at"] <= ts() else None


def approve(path, permit_id: str, stdin_isatty: bool | None = None):
    """PENDING -> ISSUED. Refuses unless stdin is an interactive terminal outside any agent harness, the request has not expired, the
    decision it belongs to is still waiting, and (when the permit carries a command) the command on file is what the plan hash covers."""
    why = human_tty_refusal(stdin_isatty)
    if why:
        raise ApprovalRefused(f"permit approval needs a human at an interactive terminal; an agent session cannot approve ({why})")
    db, own = _db(path)
    refused = None
    try:
        with db.tx() as con:
            row = con.execute("SELECT * FROM permits WHERE permit_id=?", (permit_id,)).fetchone()
            if row is None:
                raise PermitError(f"unknown permit {permit_id}")
            if row["state"] != "PENDING":
                raise PermitError(f"permit {permit_id} is {row['state']}, not PENDING")
            if row["expires_at"] <= ts():
                con.execute("UPDATE permits SET state='EXPIRED' WHERE permit_id=?", (permit_id,))
                db.event(permit_id, "permit_expired", prev="PENDING", next="EXPIRED", guard="approval attempted after expiry", con=con)
                refused = f"permit {permit_id} expired at {row['expires_at']}; ask the agent to request it again"
            elif (why := _decision_refusal(con, row["decision_id"])):
                con.execute("UPDATE permits SET state='REVOKED' WHERE permit_id=?", (permit_id,))
                db.event(permit_id, "permit_revoked", prev="PENDING", next="REVOKED", guard=f"approval attempted but {why}", con=con)
                refused = f"permit {permit_id} is revoked: {why}"
            else:
                ctx = _loads(row["scope"])
                if "subject" in ctx:  # a permit that carries a command is approved only when that command is what the plan hash covers
                    _verify(ctx, row["plan_hash"])
                doc = _doc(row)
                if doc is None:
                    raise PermitError(f"permit {permit_id} has an unreadable or invalid stored record and cannot be approved; revoke it and ask the agent to request it again")
                doc.update(state="ISSUED", principal=f"human:{user()}", approval_reference=f"tty:{user()}@{ts()}", issued_at=ts(),
                           expires_at=ts_in(row["ttl_s"]))
                doc.pop("seal", None)
                schemas.validate("permit", doc)
                doc["seal"] = _seal_of(_seal_key(db, create=True), SimpleNamespace(**{**dict(row), "issued_at": doc["issued_at"], "expires_at": doc["expires_at"]}), doc)
                con.execute("UPDATE permits SET state='ISSUED', doc_json=?, issued_at=?, expires_at=? WHERE permit_id=?",
                            (json.dumps(doc, ensure_ascii=False), doc["issued_at"], doc["expires_at"], permit_id))
                db.event(permit_id, "permit_approved", prev="PENDING", next="ISSUED", guard=doc["approval_reference"], actor=doc["principal"], con=con)
        if refused:
            raise PermitError(refused)
        return get(db, permit_id)
    finally:
        if own:
            db.close()


def _static_refusal(db, row, doc: dict, level: str) -> str | None:
    """Checks that need nothing but the permit and the level the command has now: its risk is not below the command's, a human approved it
    from a terminal when the command is at or above the human floor, and its seal matches its fields."""
    if not at_least(doc.get("risk_level", "low"), level):
        return f"the permit is for {doc.get('risk_level')} risk and this command is {level}"
    if at_least(level, "high") or at_least(level, policy.permit_floor()) or at_least(level, policy.human_floor()):
        if not str(doc.get("principal", "")).startswith("human:") or not str(doc.get("approval_reference", "")).startswith("tty:"):
            return f"a {level} command needs a permit a human approved from a terminal"
    return _seal_refusal(db, row, doc)


def _caller_refusal(ctx: dict, session: str | None, agent: str | None) -> str | None:
    if ctx.get("bound") and (ctx.get("session_id"), ctx.get("agent_id")) != (session, agent):
        return "the permit was requested by another session or agent"
    return None


def try_consume(path, permit_id: str, plan_hash: str, *, level: str, session: str | None = None, agent: str | None = None,
                fingerprints: dict[str, str] | None = None) -> str | None:
    """Atomically use an ISSUED, unexpired, unused permit for exactly this plan hash. Returns None when it was used (once, then never
    again), else why not. `level` is the risk the classifier gives this command now; `fingerprints` are the current values of the
    preconditions recorded at request time (git HEAD and the like); `session` and `agent` identify the caller."""
    db, own = _db(path)
    try:
        with db.tx() as con:
            row = con.execute("SELECT * FROM permits WHERE permit_id=?", (permit_id,)).fetchone()
            if row is None:
                return "unknown permit"
            if row["state"] != "ISSUED" or row["uses"] != 0:
                return f"the permit is {row['state']}"
            if row["plan_hash"] != plan_hash:
                return "the plan hash differs"
            if row["expires_at"] <= ts():
                return "the permit expired"
            doc = _doc(row, validate=False)
            if doc is None:
                return "the permit record is unreadable or does not match the permit schema"
            if (why := _static_refusal(db, row, doc, level)):
                return why
            if _doc(row) is None:   # the specific refusals above come first; a record that still fails its schema is never used
                return "the permit record is unreadable or does not match the permit schema"
            if con.execute("SELECT 1 FROM events WHERE entity_id=? AND kind='permit_consumed'", (permit_id,)).fetchone():
                return "the permit was already used"
            if doc.get("policy_version") != paths.policy_version():
                con.execute("UPDATE permits SET state='REVOKED' WHERE permit_id=?", (permit_id,))
                db.event(permit_id, "permit_revoked", prev="ISSUED", next="REVOKED", guard="policy_version changed since approval", con=con)
                return "the policy changed since approval, so the permit was revoked"
            want, have = _fingerprints_of(doc), {k: str(v) for k, v in (fingerprints or {}).items()}
            for kind, fp in want.items():
                if have.get(kind) != fp:
                    db.event(permit_id, "permit_precondition_mismatch", guard=kind, con=con)
                    return f"the precondition {kind} changed since approval"
            ctx = _loads(row["scope"])
            if (why := _caller_refusal(ctx, session, agent)):
                return why
            if (why := _decision_refusal(con, row["decision_id"])):
                con.execute("UPDATE permits SET state='REVOKED' WHERE permit_id=?", (permit_id,))
                db.event(permit_id, "permit_revoked", prev="ISSUED", next="REVOKED", guard=why, con=con)
                return f"the permit was revoked: {why}"
            cur = con.execute("UPDATE permits SET state='CONSUMED', uses=1 WHERE permit_id=? AND state='ISSUED' AND uses=0", (permit_id,))
            if cur.rowcount != 1:
                return "the permit was used concurrently"
            db.event(permit_id, "permit_consumed", prev="ISSUED", next="CONSUMED", guard=f"plan_hash equal, {level}, unexpired, unused", actor="hook", con=con)
            return None
    finally:
        if own:
            db.close()


def consume(path, permit_id: str, plan_hash: str, *, level: str, session: str | None = None, agent: str | None = None,
            fingerprints: dict[str, str] | None = None) -> bool:
    return try_consume(path, permit_id, plan_hash, level=level, session=session, agent=agent, fingerprints=fingerprints) is None


def find_usable(path, plan_hash: str, *, level: str, session: str | None = None, agent: str | None = None):
    """The newest ISSUED, unexpired, unused permit for this plan that could cover a command of this risk level and was requested by this session
    and agent. When only another caller's permit qualifies it is returned too, so that using it says why it cannot be used."""
    db, own = _db(path)
    try:
        other = None
        for row in db.all("SELECT * FROM permits WHERE plan_hash=? AND state='ISSUED' AND uses=0 AND expires_at>? ORDER BY issued_at DESC", (plan_hash, ts())):
            doc = _doc(row, validate=False)
            if doc is None or _static_refusal(db, row, doc, level) is not None or _doc(row) is None:
                continue
            if _caller_refusal(_loads(row.scope), session, agent) is None:
                return _ns(row, doc)
            other = other or _ns(row, doc)
        return other
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


def revoke_for_decision(db, con, decision_id: str, reason: str) -> int:
    """Revoke every open permit of a decision (it was cancelled, expired, finished or its context changed). Runs inside the caller's transaction."""
    n = 0
    for r in con.execute("SELECT permit_id, state FROM permits WHERE decision_id=? AND state IN ('PENDING','ISSUED')", (decision_id,)).fetchall():
        con.execute("UPDATE permits SET state='REVOKED' WHERE permit_id=?", (r["permit_id"],))
        db.event(r["permit_id"], "permit_revoked", prev=r["state"], next="REVOKED", guard=reason, con=con)
        n += 1
    return n


def sweep(path) -> int:
    """Expire permits past their time; revoke open permits of a finished decision and every open permit approved or requested under an
    older policy version (a rule that was tightened must not leave older approvals valid)."""
    db, own = _db(path)
    try:
        with db.tx() as con:
            n = con.execute("UPDATE permits SET state='EXPIRED' WHERE state IN ('PENDING','ISSUED') AND expires_at<=?", (ts(),)).rowcount
            current = paths.policy_version()
            for r in con.execute("SELECT permit_id, state, json_valid(doc_json) AS ok, CASE WHEN json_valid(doc_json) THEN json_extract(doc_json,'$.policy_version') END AS pv, "
                                 "(SELECT status FROM decisions d WHERE d.decision_id=permits.decision_id) AS dstatus FROM permits WHERE state IN ('PENDING','ISSUED')").fetchall():
                why = (f"its decision is {r['dstatus']}" if r["dstatus"] in TERMINAL_DECISION else "its stored record is unreadable" if not r["ok"]
                       else "policy_version changed" if r["pv"] != current else None)
                if why:
                    con.execute("UPDATE permits SET state='REVOKED' WHERE permit_id=?", (r["permit_id"],))
                    db.event(r["permit_id"], "permit_revoked", prev=r["state"], next="REVOKED", guard=why, con=con)
                    n += 1
            return n
    finally:
        if own:
            db.close()


def listing(path, states=("PENDING", "ISSUED")):
    db, own = _db(path)
    try:
        marks = ",".join("?" * len(states))
        rows = (_ns(r) for r in db.all(f"SELECT * FROM permits WHERE state IN ({marks}) AND expires_at>? ORDER BY issued_at", (*states, ts())))  # noqa: S608
        return [p for p in rows if p is not None]   # a row whose record cannot be read is not listed; `jade check` names it
    finally:
        if own:
            db.close()
