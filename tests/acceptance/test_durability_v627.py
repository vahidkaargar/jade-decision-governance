"""V6-27 small durability fixes: `work.save` is atomic and locked; the SessionStart briefing says how many decisions are held and which plans had
two permits expire, and on `compact` or `resume` it shows the open ask, permit and hold ids again."""
import contextlib
import json
import multiprocessing
import os
import threading
from pathlib import Path

import pytest

from jade import decide, hooks, ledger, paths, work
from jade.util import ts, ts_in


def item(wid="W-01", title="a test item", **kw):
    return {"schema_version": "1.0.0", "id": wid, "parent": None, "title": title, "type": "followup", "priority": "P2", "status": "open",
            "owner": "agent", "dependencies": [], "impact": "none", "rationale": "a test", "evidence_refs": [], "next_action": "do it",
            "retry_or_revisit_trigger": "manual", "created_at": "2026-11-09", "updated_at": "2026-11-09",
            "history": [{"at": "2026-11-09", "change": "created"}], "phase": 2, **kw}


def _write_one(path: str, n: int) -> None:   # module level: a spawned process must be able to import it
    lock = getattr(work, "locked", contextlib.nullcontext)
    with lock(Path(path)):
        items = work.load(Path(path)) if Path(path).exists() and Path(path).stat().st_size else []
        work.add(items, title=f"item number {n}", impact="i", rationale="r", next_action="n", trigger="manual")
        work.save(Path(path), items)


# ---- work.save ---------------------------------------------------------------------------------------------------------------
def test_parallel_writers_lose_no_item_and_leave_valid_json(tmp_path):
    f = tmp_path / "w.jsonl"
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_write_one, args=(str(f), n)) for n in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert [p.exitcode for p in procs] == [0] * 8
    items = work.load(f)                         # a cut-off line would raise here
    assert sorted(w["title"] for w in items) == sorted(f"item number {n}" for n in range(8))
    assert len({w["id"] for w in items}) == 8


def test_a_failed_replace_leaves_the_old_file_and_no_temp_file(tmp_path, monkeypatch):
    f = tmp_path / "w.jsonl"
    work.save(f, [item("W-01", "first")])
    before = f.read_text()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        work.save(f, [item("W-01", "first"), item("W-02", "second")])
    monkeypatch.undo()
    assert f.read_text() == before
    assert [p.name for p in tmp_path.iterdir() if p.name != f"{f.name}.lock"] == [f.name]


def test_an_invalid_item_changes_nothing(tmp_path):
    f = tmp_path / "w.jsonl"
    work.save(f, [item()])
    before = f.read_text()
    with pytest.raises(Exception):  # noqa: B017, PT011 - the schema's own error type
        work.save(f, [item(), {"id": "nope"}])
    assert f.read_text() == before


def test_save_keeps_the_file_mode(tmp_path):
    f = tmp_path / "w.jsonl"
    work.save(f, [item()])
    f.chmod(0o640)
    work.save(f, [item(), item("W-02", "second")])
    assert f.stat().st_mode & 0o777 == 0o640


def test_the_lock_can_be_taken_again_by_the_same_thread(tmp_path):
    f = tmp_path / "w.jsonl"
    with work.locked(f):
        work.save(f, [item()])                   # `jade work` holds the lock over load and save, and save asks for it too: no deadlock
        with work.locked(f):
            work.save(f, [item(), item("W-02", "second")])
    assert len(work.load(f)) == 2


def test_a_held_lock_makes_save_wait_and_then_fail_cleanly(tmp_path, monkeypatch):
    import fcntl

    f = tmp_path / "w.jsonl"
    work.save(f, [item()])
    monkeypatch.setattr(work, "LOCK_WAIT_S", 0.3)
    fd = os.open(work.lock_path(f), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        box: list = []
        t = threading.Thread(target=lambda: box.append(_try(lambda: work.save(f, [item(), item("W-02", "second")]))))
        t.start()
        t.join(10)
    finally:
        os.close(fd)
    assert isinstance(box[0], TimeoutError)
    assert len(work.load(f)) == 1                # nothing was written


def _try(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return e
    return None


def test_the_cli_takes_the_lock_over_the_whole_read_and_write(tmp_path, monkeypatch, capsys):
    from jade import cli

    f = tmp_path / "w.jsonl"
    f.write_text("")
    seen = []
    real = work.locked

    @contextlib.contextmanager
    def spy(path):
        seen.append(str(path))
        with real(path):
            yield

    monkeypatch.setattr(work, "locked", spy)
    rc = cli.main(["work", "add", "--file", str(f), "--out", str(tmp_path / "B.md"), "--title", "from the cli", "--impact", "i", "--rationale", "r",
                   "--next", "n", "--trigger", "manual"])
    assert rc == 0 and seen and set(seen) == {str(f)}   # the first entry is the command's own, over load and save; save enters it again
    monkeypatch.undo()
    cli.main(["work", "list", "--file", str(f)])
    assert "from the cli" in capsys.readouterr().out


def test_render_never_leaves_a_cut_backlog(tmp_path, monkeypatch):
    f = tmp_path / "w.jsonl"
    work.save(f, [item()])
    b =tmp_path / "B.md"
    b.write_text("# Backlog\n\nhand written head\n")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        work.render(f, b)
    monkeypatch.undo()
    assert b.read_text() == "# Backlog\n\nhand written head\n"


# ---- the briefing ------------------------------------------------------------------------------------------------------------
def start(session="sess-v627", **kw):
    return hooks.session_start({"session_id": session, "cwd": "/tmp/x", **kw}).stdout


def open_dec(command="rm -rf /tmp/never-run-me"):
    db = ledger.open()
    try:
        return decide.open_decision(db, "Execute Bash: a command", command=command)["decision_id"]
    finally:
        db.close()


def set_status(did, status, expires=None):
    db = ledger.open()
    try:
        with db.tx() as con:
            con.execute("UPDATE decisions SET status=?, expires_at=COALESCE(?, expires_at) WHERE decision_id=?", (status, expires, did))
    finally:
        db.close()


def add_permit(pid, plan, state, *, issued="2026-11-18T10:00:00Z", expires=None, decision_id=None, source=None):
    expires = expires or ts_in(-3600)
    db = ledger.open()
    try:
        with db.tx() as con:
            con.execute("INSERT INTO permits(permit_id, decision_id, state, risk_level, plan_hash, action, scope, uses, ttl_s, doc_json, issued_at, expires_at, source) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (pid, decision_id, state, "high", plan, "a", "s", 0, 600, json.dumps({"permit_id": pid, "policy_version": paths.policy_version()}), issued, expires, source))
    finally:
        db.close()


def test_no_held_decision_no_line():
    assert "decisions held" not in start()


def test_the_briefing_counts_held_decisions_and_tells_it_once():
    a, b = open_dec(), open_dec("rm -rf /tmp/never-run-me-2")
    set_status(a, "OUTCOME_UNKNOWN")
    set_status(b, "OUTCOME_UNKNOWN")
    out = start("sess-one00000")
    assert "decisions held: 2" in out
    assert "decisions held" not in start("sess-two00000")        # the same two decisions are not told again
    c = open_dec("rm -rf /tmp/never-run-me-3")
    set_status(c, "OUTCOME_UNKNOWN")
    assert "decisions held: 3" in start("sess-three000")         # a new one makes it news again


def test_an_executing_reversible_decision_is_not_held():
    a = open_dec("ls")
    set_status(a, "EXECUTING", expires=ts_in(600))
    assert "decisions held" not in start()


def test_test_tagged_decisions_are_not_counted():
    a = open_dec()
    set_status(a, "OUTCOME_UNKNOWN")
    db = ledger.open()
    with db.tx() as con:
        con.execute("UPDATE decisions SET source='test' WHERE decision_id=?", (a,))
    db.close()
    assert "decisions held" not in start()


def test_the_held_line_says_what_the_count_covers():
    set_status(open_dec(), "OUTCOME_UNKNOWN")
    line = next(x for x in start().splitlines() if "decisions held" in x)
    assert "all projects" in line and "jade decide outcome" in line   # the ledger keeps no project for a decision


def test_two_expired_permits_of_one_plan_make_a_line_told_once():
    add_permit("p-1", "plan-a", "EXPIRED", issued="2026-11-18T10:00:00Z")
    add_permit("p-2", "plan-a", "EXPIRED", issued="2026-11-18T11:00:00Z")
    add_permit("p-3", "plan-b", "EXPIRED")
    out = start("sess-one00000")
    assert "permits that expired twice: 1" in out
    assert "permits that expired twice" not in start("sess-two00000")


def test_a_new_pair_of_expired_permits_of_the_same_plan_is_news_again():
    add_permit("p-1", "plan-a", "EXPIRED", issued="2026-11-18T10:00:00Z")
    add_permit("p-2", "plan-a", "EXPIRED", issued="2026-11-18T11:00:00Z")
    assert "permits that expired twice: 1" in start("sess-one00000")
    db = ledger.open()
    with db.tx() as con:
        con.execute("UPDATE permits SET expires_at=? WHERE permit_id IN ('p-1','p-2')", (ts_in(-3 * 86400),))   # the first pair is now outside the 24 hour window
    db.close()
    assert "expired twice" not in start("sess-two00000")
    add_permit("p-3", "plan-a", "EXPIRED", issued="2026-11-20T10:00:00Z")
    add_permit("p-4", "plan-a", "EXPIRED", issued="2026-11-20T11:00:00Z")
    assert "permits that expired twice: 1" in start("sess-three000")     # the same command, a new pair: told again
    assert "expired twice" not in start("sess-four0000")                 # but only once for that pair


def test_a_later_use_clears_the_expired_twice_line():
    add_permit("p-1", "plan-a", "EXPIRED", issued="2026-11-18T10:00:00Z")
    add_permit("p-2", "plan-a", "EXPIRED", issued="2026-11-18T11:00:00Z")
    add_permit("p-3", "plan-a", "CONSUMED", issued="2026-11-18T12:00:00Z", expires=ts_in(600))
    assert "expired twice" not in start()


def test_old_expired_permits_do_not_count():
    old = ts_in(-3 * 86400)
    add_permit("p-1", "plan-a", "EXPIRED", expires=old)
    add_permit("p-2", "plan-a", "EXPIRED", expires=old)
    assert "expired twice" not in start()


def test_test_tagged_permits_are_not_counted():
    add_permit("p-1", "plan-a", "EXPIRED", source="test")
    add_permit("p-2", "plan-a", "EXPIRED", source="test")
    assert "expired twice" not in start()


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_on_compact_and_resume_the_held_and_expired_lines_come_back_with_ids(source):
    a = open_dec()
    set_status(a, "OUTCOME_UNKNOWN")
    add_permit("p-1", "plan-a", "EXPIRED")
    add_permit("p-2", "plan-a", "EXPIRED")
    assert "decisions held: 1" in start("sess-one00000", source="startup")
    again = start("sess-one00000", source=source)
    assert "decisions held: 1" in again and a in again               # told once does not apply: the session lost its context
    assert "permits that expired twice: 1" in again
    plain = start("sess-one00000", source="startup")
    assert "decisions held" not in plain and a not in plain


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_on_compact_and_resume_open_ask_and_permit_ids_are_shown_again(source):
    db = ledger.open()
    with db.tx() as con:
        con.execute("INSERT INTO asks(ask_id, session_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, expires_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("ask-open-1", "sess-one00000", "ph", "hk", "[]", "R-01", "high", "Bash", "{}", "PENDING", ts(), ts_in(600)))
        con.execute("INSERT INTO asks(ask_id, session_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, expires_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("ask-other-2", "sess-other000", "ph", "hk", "[]", "R-01", "high", "Bash", "{}", "PENDING", ts(), ts_in(600)))
    db.close()
    assert hooks.pretool({"tool_name": "Bash", "tool_input": {"command": "kubectl delete namespace production"}, "session_id": "sess-two00000", "cwd": "/tmp/x"}).exit_code == 2
    db = ledger.open()
    pid = db.one("SELECT permit_id FROM permits WHERE state='PENDING'").permit_id
    db.close()
    assert "ask-open-1" not in start("sess-one00000", source="startup")
    out = start("sess-one00000", source=source)
    assert "ask-open-1" in out and pid in out
    assert "ask-other-2" not in out                                   # another session's ask is not this agent's to answer


def test_an_unknown_or_missing_source_changes_nothing():
    set_status(open_dec(), "OUTCOME_UNKNOWN")
    assert "decisions held: 1" in start("sess-one00000", source=None)
    assert "decisions held" not in start("sess-two00000", source=42)


def test_the_cap_of_eight_lines_stays():
    for i in range(12):
        db = ledger.open()
        with db.tx() as con:
            con.execute("INSERT OR REPLACE INTO work_items(id, status, doc_json, updated_at) VALUES(?,?,?,?)",
                        (f"W-{i + 10}", "open", json.dumps(item(f"W-{i + 10}", f"t{i}", retry_or_revisit_trigger="on_session_start")), "2026-11-09"))
        db.close()
    set_status(open_dec(), "OUTCOME_UNKNOWN")
    out = start("sess-one00000", source="compact")
    assert len(out.splitlines()) <= 8 and "decisions held: 1" in out    # the held line comes before the work items, so the cap cuts those first


def test_the_cli_waits_for_the_lock_and_then_fails_with_a_message_and_changes_nothing(tmp_path, monkeypatch, capsys):
    import fcntl

    from jade import cli

    f = tmp_path / "w.jsonl"
    work.save(f, [item()])
    before = f.read_text()
    monkeypatch.setattr(work, "LOCK_WAIT_S", 0.2)
    fd = os.open(work.lock_path(f), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        rc = cli.main(["work", "add", "--file", str(f), "--out", str(tmp_path / "B.md"), "--title", "blocked", "--impact", "i", "--rationale", "r",
                       "--next", "n", "--trigger", "manual"])
    finally:
        os.close(fd)
    assert rc == 1 and "holds the lock" in capsys.readouterr().err
    assert f.read_text() == before and not (tmp_path / "B.md").exists()
