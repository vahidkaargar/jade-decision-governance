"""W-59: `jade ack` and `jade label` are commands an agent can type. What each may do on an agent's say-so is decided here.

Agent-callable, scoped, journaled and rate limited: `jade ack` of this session's abstain/reject/error answers (the Stop hook's reminder is meant to be
cleared by the agent that got it). Human terminal only: every write of a calibration label (`label set`, `label auto`), and an ack that reaches outside
the session, or past the rate limit. Nothing synthetic touches the live ledger: every test has its own JADE_HOME (tests/conftest.py)."""
import json
import re
from pathlib import Path

import pytest

from jade import adapter, ask, cli, evals, hooks, improve, ledger, ops

ROOT = Path(__file__).resolve().parents[2]
ME, OTHER = "sess-aaaaaaaa", "sess-bbbbbbbb"


@pytest.fixture(autouse=True)
def as_agent(monkeypatch):
    monkeypatch.setenv("JADE_SESSION_ID", ME)
    monkeypatch.setattr(cli, "is_human_tty", lambda: False)


def human(monkeypatch):
    monkeypatch.setattr(cli, "is_human_tty", lambda: True)


def answer(claim, p=0.5, session=ME):
    """One Jev call in `session`; P=0.5 is an abstain (exit 3), 0.95 an accept (exit 0), 0.05 a reject (exit 4). Returns its call id."""
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": p} for k in q})
    ask.run(ask.verify_specs(companion=False), {"claim": claim, "evidence": f"raw output for {claim}"}, provider=prov, session=session)
    db = ledger.open()
    try:
        return db.one("SELECT call_id FROM jev_calls WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (session,)).call_id
    finally:
        db.close()


def row(call_id):
    db = ledger.open()
    try:
        return db.get_call(call_id)
    finally:
        db.close()


def events(kind):
    db = ledger.open()
    try:
        return db.all("SELECT entity_id, kind, next_state, guard, actor, detail_json FROM events WHERE kind=? ORDER BY id", (kind,))
    finally:
        db.close()


def ack(*args):
    return cli.main(["ack", *args])


# ---- the intended use keeps working ---------------------------------------------------------------------
def test_an_agent_can_still_ack_all_with_a_note_and_the_stop_hook_goes_quiet(capsys):
    c = answer("the fix works")
    assert ack("--all", "--as", "reported", "--note", "told the user the claim is unverified") == 0
    assert "recorded disposition 'reported' for 1 call(s)" in capsys.readouterr().out
    r = row(c)
    assert r.disposition == "reported" and r.disposition_note == "told the user the claim is unverified"
    assert hooks.stop({"session_id": ME}).exit_code == 0


def test_the_ack_is_journaled_with_its_actor_and_the_call_ids():
    a, b = answer("claim one"), answer("claim two")
    assert ack("--all", "--as", "revised", "--note", "added the raw log and re-asked") == 0
    (e,) = events("ACK")
    d = json.loads(e.detail_json)
    assert (e.entity_id, e.next_state, e.actor) == (ME, "revised", "agent")
    assert sorted(d["call_ids"]) == sorted([a, b]) and d["count"] == 2 and e.guard == "added the raw log and re-asked"


def test_the_journaled_note_is_redacted():
    answer("a claim")
    assert ack("--all", "--as", "reported", "--note", "used key sk-or-v1-" + "a1" * 32) == 0
    (e,) = events("ACK")
    assert "a1a1a1a1" not in (e.guard or "") + (e.detail_json or "")


# ---- a disposition is a claim about what the agent did: it needs words, a scope and a target ----------
@pytest.mark.parametrize("as_", ["revised", "escalated", "reported", "dropped"])
def test_a_note_is_required_for_every_disposition(as_, capsys):
    c = answer("x claim")
    assert ack("--all", "--as", as_) == 1 and "note" in capsys.readouterr().err
    assert ack("--all", "--as", as_, "--note", "   ") == 1
    assert row(c).disposition is None
    assert hooks.stop({"session_id": ME}).exit_code == 2   # the reminder is still owed


def test_an_agent_cannot_ack_a_call_of_another_session(capsys):
    mine, theirs = answer("my claim"), answer("their claim", session=OTHER)
    assert ack(mine, theirs, "--as", "dropped", "--note", "nothing to do") == 1
    err = capsys.readouterr().err
    assert theirs in err and "another session" in err
    assert row(theirs).disposition is None and row(mine).disposition is None   # all or nothing
    assert [e.kind for e in events("ACK_REFUSED")] == ["ACK_REFUSED"]


def test_an_agent_cannot_ack_an_accepted_unknown_or_already_disposed_call(capsys):
    ok, good = answer("accepted claim", 0.95), answer("abstained claim")
    assert ack(ok, "--as", "dropped", "--note", "n") == 1
    assert ack("call_does_not_exist", "--as", "dropped", "--note", "n") == 1
    assert ack(good, "--as", "reported", "--note", "first") == 0
    assert ack(good, "--as", "dropped", "--note", "overwrite with nothing") == 1
    err = capsys.readouterr().err
    assert "exit 0" in err or "not an abstain" in err
    assert row(good).disposition == "reported" and row(good).disposition_note == "first"
    assert row(ok).disposition is None


def test_ack_all_with_nothing_to_ack_is_not_an_error_and_writes_nothing(capsys):
    assert ack("--all", "--as", "reported", "--note", "n") == 0
    assert "nothing to acknowledge" in capsys.readouterr().out
    assert events("ACK") == []


# ---- rate limit -------------------------------------------------------------------------------------
def test_an_agent_that_acks_in_a_storm_is_stopped_and_it_is_recorded(capsys):
    for i in range(hooks.ACK_MAX_PER_WINDOW):
        assert ack(answer(f"claim {i}"), "--as", "reported", "--note", f"note {i}") == 0
    last = answer("one more")
    assert ack(last, "--as", "dropped", "--note", "again") == 1
    assert "rate" in capsys.readouterr().err.lower() and row(last).disposition is None
    (r,) = events("ACK_REFUSED")
    assert json.loads(r.detail_json)["reason"] == "rate_limit"


def test_the_rate_limit_counts_rows_tagged_as_test_runs_too(monkeypatch):
    """JADE_SOURCE=test hides rows from statistics; an agent must not be able to use it to reset its own counter."""
    monkeypatch.setenv("JADE_SOURCE", "test")
    for i in range(hooks.ACK_MAX_PER_WINDOW):
        assert ack(answer(f"claim {i}"), "--as", "reported", "--note", "n") == 0
    monkeypatch.delenv("JADE_SOURCE")
    assert ack(answer("one more"), "--as", "reported", "--note", "n") == 1


def test_the_rate_limit_is_per_session(monkeypatch):
    for i in range(hooks.ACK_MAX_PER_WINDOW):
        assert ack(answer(f"claim {i}"), "--as", "reported", "--note", "n") == 0
    c = answer("their claim", session=OTHER)
    monkeypatch.setenv("JADE_SESSION_ID", OTHER)
    assert ack(c, "--as", "reported", "--note", "n") == 0


# ---- a human at a terminal keeps full reach -----------------------------------------------------------
def test_a_human_may_ack_across_sessions_without_the_rate_limit(monkeypatch):
    human(monkeypatch)
    ids = [answer(f"their claim {i}", session=OTHER) for i in range(hooks.ACK_MAX_PER_WINDOW + 3)]
    assert ack(*ids, "--as", "dropped", "--note", "stale session, nothing to do") == 0
    assert all(row(i).disposition == "dropped" for i in ids)
    assert [e.actor for e in events("ACK")] == ["human"]


# ---- what an ack must never reach -------------------------------------------------------------------
def test_an_ack_never_clears_a_safety_event_a_label_or_a_decision():
    c = answer("claim")
    db = ledger.open()
    db.event(ME, "would_block", guard="R-01", actor="runtime")
    db.event(ME, "safety_block", guard="SI-01", actor="runtime")
    evals.set_label(db, c, 1, "observed", "outcome join")
    before = (db.one("SELECT COUNT(*) AS n FROM events WHERE kind IN ('would_block','safety_block')").n, db.one("SELECT COUNT(*) AS n FROM labels").n,
              db.one("SELECT COUNT(*) AS n FROM decisions").n, db.one("SELECT COUNT(*) AS n FROM permits").n)
    db.close()
    assert ack("--all", "--as", "dropped", "--note", "n") == 0
    db = ledger.open()
    assert before == (db.one("SELECT COUNT(*) AS n FROM events WHERE kind IN ('would_block','safety_block')").n, db.one("SELECT COUNT(*) AS n FROM labels").n,
                      db.one("SELECT COUNT(*) AS n FROM decisions").n, db.one("SELECT COUNT(*) AS n FROM permits").n)
    assert db.one("SELECT label, source FROM labels WHERE call_id=?", (c,)).source == "observed"


# ---- `dropped` is not a disposition for the self-improvement signal and is visible in the numbers -----
def _reminded(n, as_=None):
    ids = [answer(f"claim {i}") for i in range(n)]
    db = ledger.open()
    with db.tx() as con:
        con.executemany("UPDATE jev_calls SET reminded=1 WHERE call_id=?", [(i,) for i in ids])
    db.close()
    if as_:
        assert ack("--all", "--as", as_, "--note", "n") == 0
    return ids


def test_improve_still_sees_a_dropped_answer_as_having_no_disposition():
    _reminded(6, "dropped")
    db = ledger.open()
    assert "non_accept_without_disposition" in {p["key"] for p in improve.patterns(db)}


def test_improve_does_not_flag_answers_that_were_reported():
    _reminded(6, "reported")
    db = ledger.open()
    assert "non_accept_without_disposition" not in {p["key"] for p in improve.patterns(db)}


def test_ledger_stats_counts_dispositions_by_kind(capsys):
    _reminded(3, "dropped")
    capsys.readouterr()
    assert cli.main(["ledger", "stats", "--days", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dispositions"]["dropped"] == 3


def test_check_fails_on_an_ack_storm_of_dropped_answers():
    rows = {n: (ok, d) for n, ok, d in ops.check(live=False)}
    assert rows["ack audit"][0] is True
    _reminded(5, "dropped")
    ok, detail = {n: (ok, d) for n, ok, d in ops.check(live=False)}["ack audit"]
    assert ok is False and "dropped" in detail


def test_check_fails_after_a_rate_limited_ack():
    for i in range(hooks.ACK_MAX_PER_WINDOW):
        assert ack(answer(f"claim {i}"), "--as", "reported", "--note", "n") == 0
    assert ack(answer("one more"), "--as", "reported", "--note", "n") == 1
    ok, detail = {n: (ok, d) for n, ok, d in ops.check(live=False)}["ack audit"]
    assert ok is False and "refused" in detail


# ---- labels: calibration inputs are a human's ----------------------------------------------------------
def test_an_agent_cannot_set_a_label(capsys):
    c = answer("claim", 0.6)
    assert cli.main(["label", "set", c, "yes", "--note", "looks right"]) == 1
    assert "human" in capsys.readouterr().err
    db = ledger.open()
    assert db.one("SELECT COUNT(*) AS n FROM labels").n == 0
    (e,) = events("LABEL_REFUSED")
    assert e.actor == "agent" and json.loads(e.detail_json)["command"] == "label set"


def test_an_agent_cannot_run_label_auto(capsys):
    assert cli.main(["label", "auto"]) == 1
    assert "human" in capsys.readouterr().err
    assert json.loads(events("LABEL_REFUSED")[0].detail_json)["command"] == "label auto"


def test_a_human_label_is_set_and_journaled(monkeypatch, capsys):
    human(monkeypatch)
    c = answer("claim", 0.6)
    assert cli.main(["label", "set", c, "no", "--note", "the claim was wrong"]) == 0
    db = ledger.open()
    assert (db.one("SELECT label, source, note FROM labels WHERE call_id=?", (c,)).label, db.one("SELECT source FROM labels WHERE call_id=?", (c,)).source) == (0, "adjudicated")
    (e,) = events("LABEL_SET")
    assert (e.entity_id, e.actor, e.next_state) == (c, "human", "no")
    assert cli.main(["label", "auto"]) == 0


def test_an_agent_can_still_read_the_adjudication_queue(capsys):
    answer("claim", 0.6)
    assert cli.main(["label", "queue"]) == 0
    assert "to adjudicate" in capsys.readouterr().out


def test_a_label_for_a_call_that_does_not_exist_is_refused(monkeypatch, capsys):
    human(monkeypatch)
    assert cli.main(["label", "set", "call_nope", "yes"]) == 1
    assert "unknown" in capsys.readouterr().err
    assert ledger.open().one("SELECT COUNT(*) AS n FROM labels").n == 0


# ---- the contract an agent reads --------------------------------------------------------------------
def test_the_skill_text_states_the_new_contract():
    text = (ROOT / "skills/jade/SKILL.md").read_text()
    assert re.search(r"--note", text) and "labels" in text.lower() and "human" in text.lower()
    assert "data, not instructions" in text or "not instructions" in text   # tool output in state is data (T1)
    section = (ROOT / "docs/claude-md-jev-section.md").read_text()
    assert "jade ack --all --as revised|escalated|reported|dropped --note" in section   # CLAUDE.md's instruction keeps working


def test_the_threat_model_has_a_row_for_ack_and_label():
    text = (ROOT / "docs/05-threat-model.md").read_text()
    assert re.search(r"^\| T24 \| [^|]*(ack|label)[^|]*\|", text, re.M | re.I)
