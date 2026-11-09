"""W-72: tests and agent verification runs must not share state with the live ledger.

(1) The suite itself refuses to touch the real JADE home. (2) A run that has to use the live hook marks its rows with JADE_SOURCE=test: they are
tagged, left out of the statistics and the SessionStart notice, and a human can remove them with `jade ledger purge --test-rows`."""
import json
from pathlib import Path

import pytest

from jade import cli, hooks, ledger, ops, paths, permits

CRITICAL = "kubectl delete namespace production"


def ev(cmd, session="sess-aaaaaaaa"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": "/tmp/x"}


# ---- (1) the suite never reaches the live ledger -------------------------------------------------------
def test_suite_guard_refuses_the_live_home(monkeypatch):
    monkeypatch.delenv("JADE_HOME", raising=False)
    with pytest.raises(pytest.fail.Exception, match="live JADE home"):
        paths.home()
    with pytest.raises(pytest.fail.Exception, match="live JADE home"):
        ledger.open()


def test_suite_guard_refuses_an_explicit_live_path(monkeypatch):
    live = Path.home() / ".local" / "share" / "jade"
    monkeypatch.setenv("JADE_HOME", str(live))
    with pytest.raises(pytest.fail.Exception, match="live JADE home"):
        paths.home()
    with pytest.raises(pytest.fail.Exception, match="live JADE home"):
        ledger.Ledger(live / "jade.sqlite")


def test_suite_guard_allows_a_temp_home(tmp_path):
    db = ledger.open(tmp_path / "other")
    db.close()


# ---- (2) source marker ----------------------------------------------------------------------------------
def test_row_source_only_accepts_test(monkeypatch):
    assert paths.row_source() is None
    monkeypatch.setenv("JADE_SOURCE", "test")
    assert paths.row_source() == "test"
    monkeypatch.setenv("JADE_SOURCE", "prod")
    assert paths.row_source() is None


def test_no_marker_by_default():
    assert hooks.pretool(ev(CRITICAL)).exit_code == 2
    db = ledger.open()
    for table in ("decisions", "permits", "events"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE source IS NOT NULL").n == 0  # noqa: S608


def test_hook_run_under_the_marker_tags_every_row(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    assert hooks.pretool(ev(CRITICAL)).exit_code == 2   # the gate decides exactly as without the marker
    db = ledger.open()
    for table in ("decisions", "permits", "events"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE COALESCE(source,'')<>'test'").n == 0, table  # noqa: S608
        assert db.one(f"SELECT COUNT(*) AS n FROM {table}").n > 0, table  # noqa: S608


def test_marker_does_not_change_the_verdict(monkeypatch):
    plain = hooks.pretool(ev(CRITICAL))
    monkeypatch.setenv("JADE_SOURCE", "test")
    marked = hooks.pretool(ev(CRITICAL, session="sess-bbbbbbbb"))
    assert (plain.exit_code, marked.exit_code) == (2, 2)


def test_jev_call_rows_are_tagged(monkeypatch):
    db = ledger.open()
    a = db.begin_call("q.x", "h1", "s1", primitive="noul")
    monkeypatch.setenv("JADE_SOURCE", "test")
    db = ledger.open()
    b = db.begin_call("q.x", "h2", "s1", primitive="noul")
    assert db.get_call(a).source is None and db.get_call(b).source == "test"


def test_older_ledger_gets_the_column_and_keeps_its_rows(tmp_path):
    db = ledger.open()
    db.begin_call("q.x", "h1", "s1", primitive="noul")
    for index in ("decisions_test", "permits_test"):   # an older ledger has neither these indexes (W-58) nor a user_version, and SQLite cannot drop an indexed column
        db.con.execute(f"DROP INDEX {index}")  # noqa: S608
    db.con.execute("PRAGMA user_version=0")
    for table in ("jev_calls", "decisions", "permits", "events"):
        db.con.execute(f"ALTER TABLE {table} DROP COLUMN source")  # noqa: S608
    db.close()
    db = ledger.open()
    for table in ("jev_calls", "decisions", "permits", "events"):
        assert "source" in {r["name"] for r in db.con.execute(f"PRAGMA table_info({table})")}, table  # noqa: S608
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls WHERE source IS NULL").n == 1


# ---- statistics and the notice ignore tagged rows --------------------------------------------------------
def test_ledger_stats_ignores_tagged_rows(monkeypatch, capsys):
    db = ledger.open()
    db.begin_call("q.x", "h1", "s1", primitive="noul")
    db.event("e1", "would_block")
    monkeypatch.setenv("JADE_SOURCE", "test")
    t = ledger.open()
    t.begin_call("q.x", "h2", "s2", primitive="noul")
    t.begin_call("q.x", "h3", "s2", primitive="noul")
    t.event("e2", "would_block")
    monkeypatch.delenv("JADE_SOURCE")
    capsys.readouterr()
    assert cli.main(["ledger", "stats"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["calls"] == 1
    assert out["events"] == {"would_block": 1}


def test_session_start_separates_test_permits(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    assert hooks.pretool(ev(CRITICAL)).exit_code == 2
    monkeypatch.delenv("JADE_SOURCE")
    out = hooks.session_start({"session_id": "sess-cccccccc", "cwd": "/tmp/x"}).stdout
    assert "awaiting human approval" not in out
    assert "WAITING_HUMAN" not in out
    assert "test-tagged" in out and "jade ledger purge --test-rows" in out
    # a real pending permit is still announced
    assert hooks.pretool(ev(CRITICAL, session="sess-dddddddd")).exit_code == 2
    assert "awaiting human approval" in hooks.session_start({"session_id": "sess-cccccccc", "cwd": "/tmp/x"}).stdout


def test_permit_list_labels_tagged_rows(monkeypatch, capsys):
    monkeypatch.setenv("JADE_SOURCE", "test")
    hooks.pretool(ev(CRITICAL))
    monkeypatch.delenv("JADE_SOURCE")
    capsys.readouterr()
    assert cli.main(["permit", "list"]) == 0
    assert "[test]" in capsys.readouterr().out


# ---- purge --test-rows -------------------------------------------------------------------------------------
def _populate(monkeypatch):
    """One real and one tagged gate run plus a call row each. Returns the tagged permit id and decision id."""
    assert hooks.pretool(ev(CRITICAL, session="sess-realrealreal")).exit_code == 2
    ledger.open().begin_call("q.x", "real", "s1", primitive="noul")
    monkeypatch.setenv("JADE_SOURCE", "test")
    assert hooks.pretool(ev("terraform destroy -auto-approve", session="sess-testtesttest")).exit_code == 2
    t = ledger.open()
    t.begin_call("q.x", "probe", "s2", primitive="noul")
    monkeypatch.delenv("JADE_SOURCE")
    p = t.one("SELECT permit_id, decision_id FROM permits WHERE source='test'")
    return p.permit_id, p.decision_id


def test_purge_test_rows_removes_only_tagged_rows(monkeypatch):
    pid, did = _populate(monkeypatch)
    db = ledger.open()
    real = {t: db.one(f"SELECT COUNT(*) AS n FROM {t} WHERE source IS NULL").n for t in ("decisions", "permits", "jev_calls")}  # noqa: S608
    n = ops.purge_test_rows(db)
    assert n["permits"] == 1 and n["decisions"] == 1 and n["jev_calls"] == 1 and n["events"] >= 3
    for t in ("decisions", "permits", "jev_calls", "events"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {t} WHERE source='test'").n == 0, t  # noqa: S608
    assert {t: db.one(f"SELECT COUNT(*) AS n FROM {t}").n for t in ("decisions", "permits", "jev_calls")} == real  # noqa: S608
    assert permits.get(db, pid) is None
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE entity_id IN (?,?)", (pid, did)).n == 0
    # one audit row: counts only
    row = db.one("SELECT kind, detail_json FROM events WHERE kind='purge' ORDER BY id DESC")
    assert json.loads(row.detail_json)["test_rows"] == sum(n.values())


def test_purge_test_rows_by_id_tags_leaked_rows(monkeypatch):
    # rows that were written before the marker existed carry no tag: the human names them
    assert hooks.pretool(ev(CRITICAL, session="sess-leakleakleak")).exit_code == 2
    assert hooks.pretool(ev("terraform destroy -auto-approve", session="sess-realrealreal")).exit_code == 2
    db = ledger.open()
    leaked = db.one("SELECT permit_id, decision_id FROM permits ORDER BY issued_at, rowid LIMIT 1")
    keep = db.one("SELECT permit_id FROM permits WHERE permit_id<>?", (leaked.permit_id,)).permit_id
    n = ops.purge_test_rows(db, ids=[leaked.permit_id, leaked.decision_id])
    assert n["permits"] == 1 and n["decisions"] == 1
    assert permits.get(db, leaked.permit_id) is None and permits.get(db, keep) is not None


def test_purge_test_rows_rejects_unknown_id_shapes():
    db = ledger.open()
    with pytest.raises(ValueError, match="permit or decision id"):
        ops.purge_test_rows(db, ids=["call_abc"])
    with pytest.raises(ValueError, match="permit or decision id"):
        ops.purge_test_rows(db, ids=["prm_x'; DROP TABLE permits; --"])


def test_purge_test_rows_with_nothing_tagged_is_a_no_op():
    db = ledger.open()
    assert sum(ops.purge_test_rows(db).values()) == 0
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE kind='purge'").n == 0


def test_cli_purge_test_rows_needs_a_human_terminal(monkeypatch, capsys):
    pid, _ = _populate(monkeypatch)
    monkeypatch.setattr(cli, "is_human_tty", lambda: False)
    assert cli.main(["ledger", "purge", "--test-rows"]) == 1
    assert "human" in capsys.readouterr().err
    assert permits.get(ledger.open(), pid) is not None


def test_cli_purge_test_rows_as_human(monkeypatch, capsys):
    pid, _ = _populate(monkeypatch)
    monkeypatch.setattr(cli, "is_human_tty", lambda: True)
    assert cli.main(["ledger", "purge", "--test-rows"]) == 0
    assert "removed" in capsys.readouterr().out
    assert permits.get(ledger.open(), pid) is None


def test_cli_id_needs_test_rows(monkeypatch, capsys):
    monkeypatch.setattr(cli, "is_human_tty", lambda: True)
    assert cli.main(["ledger", "purge", "--id", "prm_abc"]) == 2
    assert "--test-rows" in capsys.readouterr().err


# ---- review round 2 -----------------------------------------------------------------------------------------
def test_a_source_tag_in_the_hook_environment_is_a_weakening_gate_override():
    """The tag hides gated calls from the statistics and the pending-permit notice, so a tag that persists (a profile, launchctl, the settings `env`
    block) is a gate override like the variables that move the gate: listed by `jade check` and shown at SessionStart."""
    got = paths.gate_overrides({"JADE_SOURCE": "test"})
    assert [(o.var, o.value, o.weakens) for o in got] == [("JADE_SOURCE", "test", True)]
    assert paths.gate_overrides({"JADE_SOURCE": "prod"}) == [] and paths.gate_overrides({}) == []


def test_session_start_and_check_report_a_source_tag_that_is_in_force(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    out = hooks.session_start({"session_id": "sess-eeeeeeee", "cwd": "/tmp/x"}).stdout
    assert "gate override in force" in out and "JADE_SOURCE=test" in out
    name, ok, detail = ops._overrides_row()
    assert name == "gate overrides" and ok is False and "JADE_SOURCE" in detail
    monkeypatch.delenv("JADE_SOURCE")
    assert "JADE_SOURCE" not in ops._overrides_row()[2]


def test_the_settings_env_block_cannot_carry_the_source_tag_unseen(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"JADE_SOURCE": "test"}}))
    monkeypatch.setenv("JADE_CLAUDE_SETTINGS", str(settings))
    assert [(o.var, o.weakens) for o in ops.settings_env_overrides()] == [("JADE_SOURCE", True)]
    row = ops._overrides_row()
    assert row[1] is False and "JADE_SOURCE" in row[2]


def test_session_start_says_a_tagged_permit_is_still_pending_and_approvable(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    assert hooks.pretool(ev(CRITICAL)).exit_code == 2
    monkeypatch.delenv("JADE_SOURCE")
    out = hooks.session_start({"session_id": "sess-ffffffff", "cwd": "/tmp/x"}).stdout
    assert "test-tagged" in out and "PENDING" in out and "approvable" in out


def test_a_live_call_is_not_a_repeat_of_a_test_call(monkeypatch):
    """A test row answers nothing for a live one, and `jade ledger purge --test-rows` must not leave a live row pointing at a deleted anchor."""
    monkeypatch.setenv("JADE_SOURCE", "test")
    db = ledger.open()
    a = db.begin_call("q.x", "h1", "sess-1", primitive="noul", claim_hash="c1")
    db.finish_call(a, exit_code=0, p=0.9)
    again = db.begin_call("q.x", "h1", "sess-1", primitive="noul", claim_hash="c1")
    assert db.get_call(again).dedup_of == a   # a test call still repeats a test call
    db.close()
    monkeypatch.delenv("JADE_SOURCE")
    db = ledger.open()
    b = db.begin_call("q.x", "h1", "sess-1", primitive="noul", claim_hash="c1")
    assert db.get_call(b).source is None and db.get_call(b).dedup_of is None
    ops.purge_test_rows(db)
    assert db.get_call(b) is not None and db.get_call(b).dedup_of is None


def test_a_test_call_is_not_a_repeat_of_a_live_call(monkeypatch):
    db = ledger.open()
    a = db.begin_call("q.x", "h1", "sess-1", primitive="noul", claim_hash="c1")
    db.finish_call(a, exit_code=0, p=0.9)
    db.close()
    monkeypatch.setenv("JADE_SOURCE", "test")
    db = ledger.open()
    assert db.get_call(db.begin_call("q.x", "h1", "sess-1", primitive="noul", claim_hash="c1")).dedup_of is None


def test_purging_a_permit_by_id_keeps_the_decision_another_permit_still_uses(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    assert hooks.pretool(ev(CRITICAL)).exit_code == 2
    monkeypatch.delenv("JADE_SOURCE")
    db = ledger.open()
    row = db.one("SELECT permit_id, decision_id FROM permits")
    sibling = permits.request(db, "shell.exec", "ab" * 32, "high", decision_id=row.decision_id)
    sibling_id = getattr(sibling, "permit_id", sibling)
    db.con.execute("UPDATE permits SET source=NULL WHERE permit_id=?", (row.permit_id,))   # as a permit written before the tag existed
    db.con.execute("UPDATE decisions SET source=NULL WHERE decision_id=?", (row.decision_id,))
    db.con.commit()
    out = ops.purge_test_rows(db, ids=[row.permit_id])
    assert out["permits"] == 1 and out["decisions"] == 0
    assert permits.get(db, row.permit_id) is None
    assert permits.get(db, sibling_id) is not None
    assert db.one("SELECT COUNT(*) AS n FROM decisions WHERE decision_id=?", (row.decision_id,)).n == 1
    assert db.one("SELECT decision_id FROM permits WHERE permit_id=?", (sibling_id,)).decision_id == row.decision_id
    # naming the decision as well removes both permits and the decision, as before
    out = ops.purge_test_rows(db, ids=[row.decision_id])
    assert out["decisions"] == 1 and out["permits"] == 1
