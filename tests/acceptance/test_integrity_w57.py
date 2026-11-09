"""W-57: persisted permit rows and improvement candidates are validated on read, and `jade check` reports every bad row by id.

A bad row fails closed: a permit whose document cannot be read or does not match its schema is never valid, and no read path lets the
error escape into the gate. A failing row names the id and never the stored content."""
import json

import pytest

from jade import hooks, improve, ledger, ops, permits

PUSH = "git push origin feat-x"
MARK = "ZZ-SECRET-MARKER-ZZ"   # stored content that no report may repeat
ROW = "ledger integrity"


def call(cmd=PUSH, session="sess-ffffffff"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": "/tmp/x"}


def rows_of():
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}


def issued_permit():
    """An ISSUED permit for PUSH, approved as a human at a terminal."""
    db = ledger.open()
    assert hooks.pretool(call()).exit_code == 2
    p = permits.listing(db, ("PENDING",))[-1]
    permits.approve(db, p.permit_id, stdin_isatty=True)
    return db, p.permit_id


def set_doc(db, permit_id, text):
    db.con.execute("UPDATE permits SET doc_json=? WHERE permit_id=?", (text, permit_id))
    db.con.commit()


def doc_of(db, permit_id):
    return json.loads(db.one("SELECT doc_json FROM permits WHERE permit_id=?", (permit_id,)).doc_json)


def candidate(db):
    return improve.propose("question_template", "diff", "hypothesis", path=db).candidate_id


def set_candidate(db, cid, text):
    db.con.execute("UPDATE improvements SET doc_json=? WHERE candidate_id=?", (text, cid))
    db.con.commit()


# ---- the integrity pass in `jade check` -----------------------------------------------------------------------------------
def test_a_clean_ledger_passes_the_integrity_row(human_env):
    db, _ = issued_permit()
    candidate(db)
    ok, detail = rows_of()[ROW]
    assert ok, detail
    assert "1 permit" in detail and "1 improvement" in detail


def test_an_empty_ledger_passes_the_integrity_row():
    assert rows_of()[ROW][0]


def test_a_permit_row_with_unreadable_json_fails_the_check_by_id(human_env):
    db, pid = issued_permit()
    set_doc(db, pid, '{"authorized_action": "' + MARK)   # truncated
    ok, detail = rows_of()[ROW]
    assert not ok
    assert pid in detail
    assert MARK not in detail
    assert ops.print_check(ops.check(live=False)) == 1


def test_a_permit_row_that_fails_its_schema_fails_the_check_by_id(human_env):
    db, pid = issued_permit()
    doc = doc_of(db, pid)
    doc["usage_limit"] = 99
    doc["authorized_action"] = MARK
    del doc["nonce"]
    set_doc(db, pid, json.dumps(doc))
    ok, detail = rows_of()[ROW]
    assert not ok and pid in detail and MARK not in detail


def test_a_permit_row_whose_document_names_another_permit_fails(human_env):
    db, pid = issued_permit()
    doc = doc_of(db, pid)
    doc["permit_id"] = "prm_" + "0" * 12
    set_doc(db, pid, json.dumps(doc))
    ok, detail = rows_of()[ROW]
    assert not ok and pid in detail


def test_a_non_object_permit_document_fails(human_env):
    db, pid = issued_permit()
    set_doc(db, pid, json.dumps([MARK]))
    ok, detail = rows_of()[ROW]
    assert not ok and pid in detail and MARK not in detail


def test_an_improvement_row_with_unreadable_json_fails_the_check_by_id():
    db = ledger.open()
    cid = candidate(db)
    set_candidate(db, cid, '{"hypothesis": "' + MARK)
    ok, detail = rows_of()[ROW]
    assert not ok and cid in detail and MARK not in detail


def test_an_improvement_row_that_fails_its_schema_fails_the_check_by_id():
    db = ledger.open()
    cid = candidate(db)
    doc = json.loads(db.one("SELECT doc_json FROM improvements WHERE candidate_id=?", (cid,)).doc_json)
    doc["promotion_decision"] = MARK
    set_candidate(db, cid, json.dumps(doc))
    ok, detail = rows_of()[ROW]
    assert not ok and cid in detail and MARK not in detail


def test_many_bad_rows_are_all_counted_and_the_list_is_bounded(human_env):
    db = ledger.open()
    ids = []
    for i in range(30):
        ids.append(f"imp_bad{i:02d}")
        db.con.execute("INSERT INTO improvements(candidate_id, decision, doc_json, updated_at) VALUES(?,?,?,?)", (ids[-1], "pending", "{", "2026-01-01T00:00:00Z"))
    db.con.commit()
    ok, detail = rows_of()[ROW]
    assert not ok and "30" in detail
    assert len(detail) < 1500


# ---- read paths fail closed and never raise into the gate ------------------------------------------------------------------
@pytest.mark.parametrize("text", ['{"authorized_action": "', "[]", "null", "{}"])
def test_a_permit_with_an_unreadable_document_is_never_consumed(human_env, text):
    db, pid = issued_permit()
    set_doc(db, pid, text)
    why = permits.try_consume(db, pid, db.one("SELECT plan_hash FROM permits WHERE permit_id=?", (pid,)).plan_hash, level="high")
    assert isinstance(why, str) and why
    assert db.one("SELECT state, uses FROM permits WHERE permit_id=?", (pid,)).uses == 0


def test_the_gate_blocks_and_does_not_raise_when_the_only_permit_is_corrupt(human_env):
    db, pid = issued_permit()
    set_doc(db, pid, '{"authorized_action": "')
    assert hooks.pretool(call()).exit_code == 2
    assert db.one("SELECT uses FROM permits WHERE permit_id=?", (pid,)).uses == 0


def test_the_gate_blocks_when_the_only_permit_fails_its_schema(human_env):
    db, pid = issued_permit()
    doc = doc_of(db, pid)
    del doc["risk_level"]
    set_doc(db, pid, json.dumps(doc))
    assert hooks.pretool(call()).exit_code == 2
    assert db.one("SELECT uses FROM permits WHERE permit_id=?", (pid,)).uses == 0


def test_find_usable_skips_a_corrupt_permit(human_env):
    db, pid = issued_permit()
    plan = db.one("SELECT plan_hash FROM permits WHERE permit_id=?", (pid,)).plan_hash
    set_doc(db, pid, "{")
    assert permits.find_usable(db, plan, level="high") is None


def test_listing_get_and_pending_for_do_not_raise_on_a_corrupt_row(human_env):
    db = ledger.open()
    assert hooks.pretool(call()).exit_code == 2
    p = permits.listing(db, ("PENDING",))[-1]
    set_doc(db, p.permit_id, "{")
    assert permits.listing(db, ("PENDING",)) == []
    assert permits.get(db, p.permit_id) is None
    assert permits.pending_for(db, p.plan_hash if hasattr(p, "plan_hash") else db.one("SELECT plan_hash FROM permits").plan_hash) is None


def test_the_sweep_survives_and_revokes_a_corrupt_open_permit(human_env):
    db, pid = issued_permit()
    set_doc(db, pid, "{")
    assert permits.sweep(db) >= 1
    assert db.one("SELECT state FROM permits WHERE permit_id=?", (pid,)).state == "REVOKED"


def test_a_fresh_request_replaces_a_corrupt_pending_permit(human_env):
    db = ledger.open()
    assert hooks.pretool(call()).exit_code == 2
    old = permits.listing(db, ("PENDING",))[-1].permit_id
    set_doc(db, old, "{")
    assert hooks.pretool(call()).exit_code == 2   # does not raise, and raises a usable request
    fresh = [p.permit_id for p in permits.listing(db, ("PENDING",))]
    assert fresh and old not in fresh


def test_approving_a_corrupt_pending_permit_is_a_permit_error(human_env):
    db = ledger.open()
    assert hooks.pretool(call()).exit_code == 2
    pid = db.one("SELECT permit_id FROM permits").permit_id
    set_doc(db, pid, "{")
    with pytest.raises(permits.PermitError):
        permits.approve(db, pid, stdin_isatty=True)
    assert db.one("SELECT state FROM permits WHERE permit_id=?", (pid,)).state == "PENDING"


def test_a_candidate_with_an_unreadable_document_blocks_promotion_instead_of_raising():
    db = ledger.open()
    cid = candidate(db)
    set_candidate(db, cid, '{"hypothesis": "')
    with pytest.raises(improve.PromotionBlocked, match=cid):
        improve.get(db, cid)
    with pytest.raises(improve.PromotionBlocked):
        improve.promote(db, cid)


def test_a_candidate_that_fails_its_schema_blocks_promotion():
    db = ledger.open()
    cid = candidate(db)
    doc = json.loads(db.one("SELECT doc_json FROM improvements WHERE candidate_id=?", (cid,)).doc_json)
    doc["promotion_decision"] = "promoted"   # valid enum, but remove a required field too
    del doc["audit_trail"]
    set_candidate(db, cid, json.dumps(doc))
    with pytest.raises(improve.PromotionBlocked):
        improve.promote(db, cid)


def test_the_cli_lists_and_shows_a_corrupt_candidate_without_a_traceback(capsys):
    from jade import cli

    db = ledger.open()
    cid = candidate(db)
    set_candidate(db, cid, "{")
    db.close()
    assert cli.main(["improve", "list"]) == 0
    assert cid in capsys.readouterr().out
    assert cli.main(["improve", "show", cid]) == 1


def test_an_unknown_candidate_is_still_none():
    assert improve.get(ledger.open(), "imp_nope") is None


def test_the_cli_refuses_to_show_or_approve_a_corrupt_permit_but_can_revoke_it(human_env, capsys):
    from jade import cli

    db, pid = issued_permit()
    set_doc(db, pid, "{")
    db.close()
    assert cli.main(["permit", "show", pid]) == 1
    assert "unreadable" in capsys.readouterr().err
    assert cli.main(["permit", "list"]) == 0
    assert pid not in capsys.readouterr().out
    assert cli.main(["permit", "revoke", pid]) == 0
    assert ledger.open().one("SELECT state FROM permits WHERE permit_id=?", (pid,)).state == "REVOKED"
    assert cli.main(["permit", "show", "prm_nope"]) == 1
    assert "unknown permit" in capsys.readouterr().err
