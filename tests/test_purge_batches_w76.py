"""W-76: the retention purge in the daily `jade maintain` job holds the ledger write lock in bounded batches, not for one transaction that grows with the row count.

A hook waits at most `hooks.LEDGER_TIMEOUT_S` (1.5 s) for the lock and then blocks a high-risk call (docs/05 T17). The purge used to clear every old row inside one
`BEGIN IMMEDIATE`; the time it held the lock grew with the rows. Now each batch is its own transaction and the lock is free between two of them. The scrub
(one `VACUUM` per ledger file, then a checkpoint) is covered in tests/test_scrub_once_w76.py.
"""

from __future__ import annotations

import json
import sqlite3

from jade import ledger, ops

MARK = "zq-batch-host-4e1d"
OLD = "2000-01-01T00:00:00Z"
N = 11   # more than three batches of 3


def plant(db, *, permits_without_subject: int = 0) -> None:
    for i in range(N):
        cid = db.begin_call("q.verify_claim.supported_by_evidence", f"{i:02d}" + "0" * 62, f"s{i}", state_text=f"state {MARK} {i}", question_text=f"q {MARK} {i}")
        db.con.execute("UPDATE jev_calls SET ts=? WHERE call_id=?", (OLD, cid))
        doc = {"objective": f"deploy {MARK} {i}", "candidate_actions": [{"description": f"kubectl {MARK} {i}"}], "constraints": [MARK], "assumptions": [MARK],
               "evidence_refs": [MARK]}
        db.con.execute("INSERT INTO decisions(decision_id, status, doc_json, created_at, updated_at, expires_at) VALUES(?,?,?,?,?,?)",
                       (f"dec_{i:012x}", "DONE", json.dumps(doc), OLD, OLD, OLD))
        scope = json.dumps({"tool": "Bash", "subject": f"kubectl {MARK} {i}", "length": 9})
        db.con.execute("INSERT INTO permits(permit_id, decision_id, state, risk_level, plan_hash, scope, ttl_s, doc_json, issued_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (f"prm_{i:012x}", f"dec_{i:012x}", "USED", "high", "p" * 64, scope, 60, "{}", OLD, OLD))
        db.con.execute("INSERT INTO events(entity_id, kind, detail_json, ts) VALUES(?,?,?,?)", (f"dec_{i:012x}", "note", json.dumps({"x": MARK}), OLD))
    for i in range(permits_without_subject):   # a row the purge selects but never changes: it must not make the batch loop run for ever
        db.con.execute("INSERT INTO permits(permit_id, decision_id, state, risk_level, plan_hash, scope, ttl_s, doc_json, issued_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (f"prm_ff{i:010x}", None, "USED", "high", "p" * 64, json.dumps({"tool": "Bash"}), 60, "{}", OLD, OLD))
        db.con.execute("INSERT INTO permits(permit_id, decision_id, state, risk_level, plan_hash, scope, ttl_s, doc_json, issued_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (f"prm_ee{i:010x}", None, "USED", "high", "p" * 64, "{not json", 60, "{}", OLD, OLD))
    db.con.commit()


def dump(db) -> str:
    return "\n".join(db.con.iterdump())


def begins(db) -> list[str]:
    seen: list[str] = []
    db.con.set_trace_callback(lambda s: seen.append(s) if s.strip().upper().startswith("BEGIN") else None)
    return seen


def test_the_purge_runs_in_several_short_transactions(monkeypatch):
    monkeypatch.setattr(ops, "PURGE_BATCH", 3)
    monkeypatch.setattr(ops, "PURGE_PAUSE_S", 0.0)
    db = ledger.open()
    plant(db)
    seen = begins(db)
    out = ops.purge_report(db, 90)
    # 11 rows in each of 4 tables at 3 rows a batch: 4 transactions per table (and one more for the audit event), where one big transaction would be 1 in all
    assert len(seen) >= 16, f"the purge held the write lock in {len(seen)} transaction(s): it must be several bounded ones"
    assert {t: out[t] for t in ("jev_calls", "decisions", "permits", "events")} == dict.fromkeys(("jev_calls", "decisions", "permits", "events"), N)
    assert MARK not in dump(db), "the text of an old row is still in the ledger"
    assert ops.purge(db, 90) == 0   # nothing left: idempotent


def test_the_lock_is_free_between_two_batches(monkeypatch):
    """A second connection with no wait takes the write lock in every gap, which is what a waiting hook needs."""
    monkeypatch.setattr(ops, "PURGE_BATCH", 3)
    db = ledger.open()
    plant(db)
    other = sqlite3.connect(db.path, timeout=0, isolation_level=None)
    gaps: list[bool] = []

    def gap():
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            gaps.append(True)
        except sqlite3.OperationalError:
            gaps.append(False)

    monkeypatch.setattr(ops, "_between_batches", gap, raising=False)
    ops.purge(db, 90)
    other.close()
    assert len(gaps) >= 4 and all(gaps), gaps


def test_a_row_the_purge_selects_but_does_not_change_cannot_loop_for_ever(monkeypatch):
    monkeypatch.setattr(ops, "PURGE_BATCH", 3)
    monkeypatch.setattr(ops, "PURGE_PAUSE_S", 0.0)
    db = ledger.open()
    plant(db, permits_without_subject=7)
    assert ops.purge_report(db, 90)["permits"] == N
    assert db.one("SELECT COUNT(*) AS n FROM permits WHERE scope='{not json'").n == 7   # left as it was


def test_a_dry_run_gives_the_same_counts_as_the_purge_and_changes_nothing(monkeypatch):
    monkeypatch.setattr(ops, "PURGE_BATCH", 3)
    monkeypatch.setattr(ops, "PURGE_PAUSE_S", 0.0)
    db = ledger.open()
    plant(db, permits_without_subject=4)
    before = dump(db)
    seen = begins(db)
    plan = ops.purge_report(db, 90, ask_text=True, dry_run=True)
    assert dump(db) == before, "a dry run must leave the ledger as it was"
    assert len(seen) == 1, "a dry run is one transaction that is rolled back"
    assert ops.purge_report(db, 90, ask_text=True) == plan


def test_a_run_stopped_part_way_with_ctrl_c_still_leaves_one_audit_event_with_the_rows_it_cleared(monkeypatch):
    monkeypatch.setattr(ops, "PURGE_BATCH", 3)
    calls: list[int] = []

    def stop():
        calls.append(1)
        if len(calls) == 3:
            raise KeyboardInterrupt   # a human who stops a long purge with Ctrl-C

    monkeypatch.setattr(ops, "_between_batches", stop, raising=False)
    db = ledger.open()
    plant(db)
    try:
        ops.purge(db, 90)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("the stop must reach the caller")
    rows = db.all("SELECT detail_json FROM events WHERE kind='purge'")
    assert len(rows) == 1
    assert json.loads(rows[0].detail_json)["rows"] >= 3
    ops.purge(db, 90)   # a later run finishes the job
    assert MARK not in dump(db)
