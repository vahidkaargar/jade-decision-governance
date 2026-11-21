"""W-210 (V6-29): the retention purge clears the command text and the folder that `audit_queue` keeps for a call that ran from a standing answer.

The row is sealed, and the seal covers the text and the folder (`audit.SEALED`). So the purge makes a new seal in the same transaction, and only for a row whose old
seal was good: it must never turn an edited row into a good one. A row still waiting for the human's check is never cleared (the walk shows its text).
"""

from __future__ import annotations

import hmac
from datetime import timedelta

from jade import audit, ledger, ops
from jade.util import now, ts

MARK = "zq-private-host-9f3a"
OLD = "2000-01-01T00:00:00Z"
RESOLVED_OLD = "2000-01-02T00:00:00Z"


def _row(db, key, n: int, *, status: str, drawn: str = OLD, resolved: str | None = RESOLVED_OLD, subject: str | None = None, cwd: str | None = None,
         session: str = "s1") -> str:
    """One queue row, sealed as `audit.record` seals it."""
    r = {"audit_id": f"aud_{n:012d}", "decision_id": f"dec_{n:012d}", "answer_id": "ans_1", "scope_id": "scope-1", "rule_id": "R-17", "stage": 1, "sample_prob": 1.0,
         "session_id": session, "subject": f"kubectl delete ns {MARK}" if subject is None else subject, "cwd": f"/Users/x/{MARK}" if cwd is None else cwd,
         "drawn_ts": drawn, "status": status, "resolved_ts": resolved}
    db.con.execute("INSERT INTO audit_queue(audit_id, decision_id, answer_id, scope_id, rule_id, stage, sample_prob, status, session_id, subject, cwd, drawn_ts, "
                   "resolved_ts, seal, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (r["audit_id"], r["decision_id"], r["answer_id"], r["scope_id"], r["rule_id"], r["stage"], r["sample_prob"], status, session, r["subject"], r["cwd"],
                    drawn, resolved, audit._seal(key, r), None))
    return r["decision_id"]


def _get(db, did):
    return db.con.execute("SELECT * FROM audit_queue WHERE decision_id=?", (did,)).fetchone()


def _good(db, key, did) -> bool:
    q = _get(db, did)
    return hmac.compare_digest(q["seal"], audit._seal(key, q))


def _setup():
    db = ledger.open()
    key = audit._key(db, create=True)
    return db, key


def test_audit_queue_is_counted_in_the_purge_tables():
    assert "audit_queue" in ops.PURGE_TABLES


def test_a_resolved_row_older_than_the_window_loses_its_text_and_keeps_a_good_seal():
    db, key = _setup()
    right = _row(db, key, 1, status="right")
    wrong = _row(db, key, 2, status="wrong")
    db.con.commit()
    report = ops.purge_report(db, 90)
    assert report["audit_queue"] == 2
    for did, status in ((right, "right"), (wrong, "wrong")):
        q = _get(db, did)
        assert q["subject"] == ops.PURGED and q["cwd"] == ops.PURGED and q["status"] == status
        assert _good(db, key, did), "the new seal must verify, or the checked row stops counting in the readout"
    assert MARK not in "\n".join(db.con.iterdump()), "the text is still in the ledger"
    s = audit.summary(db)
    assert s["unsealed"] == 0 and s["checked"] == 2 and s["wrong"] == 1


def test_a_pending_row_is_never_cleared_however_old():
    db, key = _setup()
    did = _row(db, key, 1, status="pending", resolved=None)
    db.con.commit()
    assert ops.purge_report(db, 90)["audit_queue"] == 0
    q = _get(db, did)
    assert MARK in q["subject"] and MARK in q["cwd"] and _good(db, key, did)
    assert [r["decision_id"] for r in audit.pending(db, 10)] == [did]


def test_a_resolved_row_inside_the_window_keeps_its_text():
    db, key = _setup()
    recent = ts(now() - timedelta(days=1))
    did = _row(db, key, 1, status="right", drawn=recent, resolved=recent)
    old_drawn_recent_check = _row(db, key, 2, status="right", drawn=OLD, resolved=recent)   # checked yesterday: the window runs from the check
    db.con.commit()
    assert ops.purge_report(db, 90)["audit_queue"] == 0
    for d in (did, old_drawn_recent_check):
        assert MARK in _get(db, d)["subject"] and _good(db, key, d)


def test_a_row_whose_seal_already_fails_is_cleared_but_never_sealed_again():
    db, key = _setup()
    did = _row(db, key, 1, status="right")
    db.con.execute("UPDATE audit_queue SET status='wrong' WHERE decision_id=?", (did,))   # edited after the draw
    db.con.commit()
    before = _get(db, did)["seal"]
    assert not _good(db, key, did)
    assert ops.purge_report(db, 90)["audit_queue"] == 1
    q = _get(db, did)
    assert q["subject"] == ops.PURGED and q["cwd"] == ops.PURGED
    assert q["seal"] == before and not _good(db, key, did), "the purge must not make an edited row look good"
    assert audit.summary(db)["unsealed"] == 1


def test_without_the_seal_key_nothing_is_cleared_and_nothing_is_broken():
    """No key: a row cannot be sealed again, and clearing it would break a good seal for good. The text stays until a key can be read."""
    db, key = _setup()
    did = _row(db, key, 1, status="right")
    db.con.commit()
    (db.path.parent / "permit-seal.key").rename(db.path.parent / "moved.key")
    assert ops.purge_report(db, 90)["audit_queue"] == 0
    assert MARK in _get(db, did)["subject"]
    (db.path.parent / "moved.key").rename(db.path.parent / "permit-seal.key")
    assert ops.purge_report(db, 90)["audit_queue"] == 1
    assert _good(db, key, did)


def test_a_dry_run_counts_the_rows_and_changes_nothing_and_the_purge_is_idempotent():
    db, key = _setup()
    did = _row(db, key, 1, status="right")
    db.con.commit()
    before = "\n".join(db.con.iterdump())
    plan = ops.purge_report(db, 90, dry_run=True)
    assert plan["audit_queue"] == 1 and "\n".join(db.con.iterdump()) == before
    assert ops.purge_report(db, 90) == {**plan}
    assert ops.purge_report(db, 90)["audit_queue"] == 0
    assert _good(db, key, did)


def test_a_row_with_no_text_is_left_alone():
    db, key = _setup()
    did = _row(db, key, 1, status="right", subject="", cwd="")
    old = _row(db, key, 2, status="right", subject=ops.PURGED, cwd=ops.PURGED)
    db.con.commit()
    seals = {d: _get(db, d)["seal"] for d in (did, old)}
    assert ops.purge_report(db, 90)["audit_queue"] == 0
    assert {d: _get(db, d)["seal"] for d in (did, old)} == seals


def test_a_later_mark_on_a_cleared_row_still_works():
    """`jade review mark` seals the row again after it checks the old seal: the cleared row must still pass that check."""
    db, key = _setup()
    did = _row(db, key, 1, status="right")
    db.con.commit()
    ops.purge_report(db, 90)
    out = audit.on_mark(db, did, "wrong", [])
    assert out["sealed"] is True and _get(db, did)["status"] == "wrong" and _good(db, key, did)
