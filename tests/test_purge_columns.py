"""V6-11 (DX-P1, FN-12): the retention purge reaches every column that holds the text of a call, and it can be tried without changing anything.

`ops.purge` once cleared `jev_calls`, `decisions`, `permits` and `events` only. The chat question (`asks.question_json`, with a "Command:" block), the
one-line labels (`asks.label`, `asks.scopes_json`, `human_answers.label`, `human_answers.scope_label`) kept the command text for ever. Clearing them is a
privacy change that the owner approves (DX-H2: clear the label text, keep the hashes and the decision), so it is OFF by default: `ask_text=True` or
`jade ledger purge --ask-text` turns it on, and the daily `jade maintain` job never does. A dry run runs the same code and rolls it back.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from jade import cli, hooks, ledger, memory, ops
from jade.util import now, sha256, ts

MARK = "zq-private-host-9f3a"          # text that must not survive a purge once the window has passed
OLD = "2000-01-01T00:00:00Z"
TABLES = ("jev_calls", "decisions", "permits", "events", "asks", "human_answers")


def dump(db) -> str:
    """The whole stored content of the ledger, as text."""
    return "\n".join(db.con.iterdump())


def _ask(db, ask_id: str, *, created: str, status: str = "ANSWERED", expires: str | None = None, text: str = MARK) -> None:
    q = json.dumps({"question": f"Command:\nkubectl delete ns {text}", "header": "JADE gate"})
    db.con.execute("INSERT INTO asks(ask_id, session_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, "
                   "expires_ts, class_key, label, ho, scopes_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ask_id, "s1", "p" * 64, f"hold-{ask_id}", "{}", "R-17", "high", "Bash", q, status, created, expires or created, None,
                    f"kubectl delete ns {text}", 0, json.dumps([{"kind": "kind_project", "key": "k", "label": f"every kubectl in {text}"}])))


def _answer(db, *, label: str, scope_label: str, answer: str = "allow", strength: str = "hint", ts_: str = OLD, confirmed: str | None = None,
            used: str | None = None) -> str:
    # the keys are hashes of the call, as in production: they carry no text
    aid = db.record_human_answer(session="s1", decision_id=None, rule_id="R-17", class_key=None, exact_key=sha256(f"exact {label} {answer} {strength}"), answer=answer,
                                 channel="chat", label=label, scope={"kind": "kind_project", "key": sha256(f"scope {label}"), "label": scope_label})
    db.con.execute("UPDATE human_answers SET ts=?, strength=?, confirmed_ts=?, last_used_ts=? WHERE answer_id=?", (ts_, strength, confirmed, used, aid))
    return aid


def plant(db) -> dict:
    """One old row with the marker in every table the purge covers, and one fresh row of each kind that must keep its text."""
    old = db.begin_call("q.verify_claim.supported_by_evidence", "h1" + "0" * 62, "s1", state_text=f"state {MARK}", question_text=f"question {MARK}")
    db.con.execute("UPDATE jev_calls SET ts=? WHERE call_id=?", (OLD, old))
    hooks.pretool({"tool_name": "Bash", "tool_input": {"command": f"kubectl delete ns {MARK}"}, "session_id": "sess-cccccccc", "cwd": "/tmp/x"})
    did = db.one("SELECT decision_id FROM decisions").decision_id
    db.con.execute("UPDATE decisions SET created_at=?, updated_at=? WHERE decision_id=?", (OLD, OLD, did))
    db.con.execute("UPDATE permits SET issued_at=?, expires_at=?", (OLD, OLD))
    db.con.execute("UPDATE events SET ts=?", (OLD,))
    _ask(db, "a-old", created=OLD)
    _ask(db, "a-fresh", created=ts(now()), expires=ts(now()), text="fresh-host-1c7e")
    ids = {"old": _answer(db, label=f"kubectl delete ns {MARK}", scope_label=f"every kubectl in {MARK}"),
           "fresh": _answer(db, label="fresh-label-77aa", scope_label="fresh-scope-77aa", ts_=ts(now()))}
    db.con.commit()
    return ids


def counts(db):
    return {t: db.one(f"SELECT COUNT(*) AS n FROM {t}").n for t in TABLES}   # noqa: S608 - constant table names


def test_without_the_switch_the_ask_and_answer_text_is_left_alone():
    """The owner has not said yes to clearing labels: the retention window clears what it always cleared and nothing more."""
    db = ledger.open()
    plant(db)
    ops.purge(db, 90)
    assert db.one("SELECT question_json, label, scopes_json FROM asks WHERE ask_id='a-old'").label.endswith(MARK)
    assert MARK in db.one("SELECT label FROM human_answers WHERE label LIKE ?", (f"%{MARK}",)).label


def test_the_switch_clears_every_column_that_holds_the_text_of_a_call():
    db = ledger.open()
    plant(db)
    n = ops.purge(db, 90, ask_text=True)
    assert n >= 6
    assert MARK not in dump(db), "the text of an old call is still in the ledger"
    row = db.one("SELECT question_json, label, scopes_json, status, plan_hash, hold_key, rule_id, level FROM asks WHERE ask_id='a-old'")
    assert row.status == "ANSWERED" and row.plan_hash and row.hold_key and row.rule_id == "R-17" and row.level == "high"      # the metrics and the keys stay
    assert json.loads(row.question_json) == {"purged": True} and json.loads(row.scopes_json) == []
    ans = db.one("SELECT label, scope_label, exact_key, answer, rule_id FROM human_answers WHERE label NOT LIKE 'fresh%'")
    assert ans.label == ops.PURGED and ans.scope_label == ops.PURGED and ans.exact_key and ans.answer == "allow" and ans.rule_id == "R-17"
    fresh = dump(db)
    assert "fresh-host-1c7e" in fresh and "fresh-label-77aa" in fresh and "fresh-scope-77aa" in fresh     # inside the window: kept
    assert ops.purge(db, 90, ask_text=True) == 0                                                             # nothing left to clear: idempotent


def test_a_question_that_is_still_open_is_not_cleared():
    db = ledger.open()
    _ask(db, "a-open", created=OLD, status="PENDING", expires=ts(now() + timedelta(days=365)))
    ops.purge(db, 90, ask_text=True)
    assert MARK in db.one("SELECT question_json FROM asks WHERE ask_id='a-open'").question_json


def test_a_standing_answer_and_an_unconfirmed_allow_keep_the_label_the_owner_reads_in_review_and_knows():
    """`jade review confirm` and `jade knows` print the label. A row that still decides, or can still be confirmed, keeps it until it lapses."""
    db = ledger.open()
    recent = ts(now())
    standing = _answer(db, label="standing-label-5b2d", scope_label="standing-scope-5b2d", strength="strong", confirmed=recent, used=recent)
    lapsed = _answer(db, label="lapsed-label-5b2d", scope_label="lapsed-scope-5b2d", strength="strong", confirmed=OLD, used=OLD)
    # `waiting` is an allow hint older than the retention window but inside the 180 days an unconfirmed allow can still be confirmed in
    waiting = _answer(db, label="waiting-label-5b2d", scope_label="waiting-scope-5b2d", ts_=ts(now() - timedelta(days=memory.CAP_DAYS - 10)))
    denied = _answer(db, label="denied-label-5b2d", scope_label="denied-scope-5b2d", answer="deny")
    ops.purge(db, 7, ask_text=True)
    label = {r.answer_id: r.label for r in db.all("SELECT answer_id, label FROM human_answers")}
    assert label[standing] == "standing-label-5b2d" and label[waiting] == "waiting-label-5b2d"
    assert label[lapsed] == ops.PURGED and label[denied] == ops.PURGED


def test_an_unconfirmed_allow_older_than_180_days_is_cleared():
    db = ledger.open()
    old = _answer(db, label=f"old-{MARK}", scope_label="s")
    db.con.execute("UPDATE human_answers SET ts=? WHERE answer_id=?", (ts(now() - timedelta(days=memory.CAP_DAYS + 5)), old))
    ops.purge(db, 90, ask_text=True)
    assert db.one("SELECT label FROM human_answers WHERE answer_id=?", (old,)).label == ops.PURGED


# ---- the dry run ------------------------------------------------------------------------------------------------------
def test_a_dry_run_counts_what_the_purge_would_clear_and_changes_nothing():
    db = ledger.open()
    plant(db)
    before = dump(db)
    rows_before = counts(db)
    plan = ops.purge_report(db, 90, ask_text=True, dry_run=True)
    assert dump(db) == before and counts(db) == rows_before, "a dry run must leave the ledger as it was (including the audit event)"
    assert set(plan) == set(TABLES) and all(plan[t] >= 1 for t in TABLES), plan
    real = ops.purge_report(db, 90, ask_text=True)
    assert real == plan, "the dry run and the purge must clear the same rows"


def test_a_dry_run_without_the_switch_reports_no_ask_or_answer_rows():
    db = ledger.open()
    plant(db)
    plan = ops.purge_report(db, 90, dry_run=True)
    assert plan["asks"] == 0 and plan["human_answers"] == 0 and plan["jev_calls"] >= 1


def test_the_command_line_dry_run_prints_the_counts_for_both_modes_and_needs_no_terminal(capsys, monkeypatch):
    monkeypatch.setattr(cli, "is_human_tty", lambda *a, **k: False)
    db = ledger.open()
    plant(db)
    before = dump(db)
    assert cli.main(["ledger", "purge", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "nothing was changed" in out and "--ask-text" in out and "asks" in out and "human_answers" in out
    assert dump(ledger.open()) == before


def test_the_real_purge_with_the_switch_needs_a_human_terminal(capsys, monkeypatch):
    monkeypatch.setattr(cli, "is_human_tty", lambda *a, **k: False)
    db = ledger.open()
    plant(db)
    assert cli.main(["ledger", "purge", "--ask-text"]) == 1
    assert MARK in dump(ledger.open())
    monkeypatch.setattr(cli, "is_human_tty", lambda *a, **k: True)
    assert cli.main(["ledger", "purge", "--ask-text"]) == 0
    assert MARK not in dump(ledger.open())


def test_dry_run_with_test_rows_is_refused():
    assert cli.main(["ledger", "purge", "--dry-run", "--test-rows"]) == 2


def test_the_daily_job_never_turns_the_switch_on(monkeypatch):
    seen = []
    monkeypatch.setattr(ops, "purge", lambda db, days=90, **kw: seen.append(kw) or 0)
    monkeypatch.setattr(ops, "check", lambda live=True, **k: [])
    assert cli.main(["maintain", "--offline"]) == 0
    assert seen == [{}], "`jade maintain` must call ops.purge without ask_text until the owner says yes (DX-H2)"


@pytest.mark.parametrize("window", [1, 90])
def test_a_window_clears_nothing_inside_it(window):
    db = ledger.open()
    _ask(db, "a-new", created=ts(now()), expires=ts(now()), text="new-host-2d4c")
    assert ops.purge(db, window, ask_text=True) == 0
