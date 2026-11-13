"""W-100d: `jade ledger stats` says how often the human was asked, how they answered and where, per 100 gated calls and by rule (docs/12, the measure of the plan:
terminal approvals per 100 gated high calls should fall to live JADE wiring only)."""

import json
import uuid
from datetime import timedelta

from jade import cli, ledger
from jade.util import now, ts


def stats(capsys, *extra):
    assert cli.main(["ledger", "stats", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def add_ask(db, rule, status, age_days=0, session="s-1"):
    created = ts(now() - timedelta(days=age_days))
    db.con.execute("INSERT INTO asks(ask_id, session_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, expires_ts) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:12], session, "p", "h", "{}", rule, "high", "Bash", "{}", status, created, created))


def test_an_empty_ledger_has_no_ask_section(capsys):
    db = ledger.open()
    db.close()
    assert "asks" not in stats(capsys)


def test_counts_answers_by_channel_per_100_gated_calls_and_by_rule(capsys):
    db = ledger.open()
    for _ in range(50):
        db.event("gate", "gated", guard="R-11 high", actor="hook")
    for rule, answer, channel in [("R-11", "allow", "chat")] * 10 + [("R-11", "deny", "chat")] * 2 + [("R-13", "allow", "dialog")] * 5:
        db.record_human_answer(session="s-1", decision_id=None, rule_id=rule, class_key=rule, exact_key="k", answer=answer, channel=channel)
    for _ in range(4):
        db.event("prm_x", "permit_approved", guard="ref", actor="human")
    add_ask(db, "R-11", "ALLOWED")
    add_ask(db, "R-11", "DENIED")
    add_ask(db, "R-11", "EXPIRED")
    add_ask(db, "R-13", "CONSUMED")
    add_ask(db, "R-13", "GAVE_UP")
    db.close()
    s = stats(capsys)["asks"]
    assert s["gated_calls"] == 50
    assert s["answers"] == {"chat": {"allow": 10, "deny": 2}, "dialog": {"allow": 5, "deny": 0}}
    assert s["terminal_approvals"] == 4
    assert s["per_100_gated"] == {"chat": 24.0, "dialog": 10.0, "terminal": 8.0}
    assert s["by_rule"]["R-11"] == {"asks": 3, "allowed": 1, "denied": 1, "unanswered": 1}
    assert s["by_rule"]["R-13"] == {"asks": 2, "allowed": 1, "denied": 0, "unanswered": 1}


def test_no_gated_calls_means_no_rate_not_a_division_by_zero(capsys):
    db = ledger.open()
    db.record_human_answer(session="s-1", decision_id=None, rule_id="R-11", class_key="R-11", exact_key="k", answer="allow", channel="chat")
    db.close()
    s = stats(capsys)["asks"]
    assert s["gated_calls"] == 0 and s["per_100_gated"] is None


def test_the_window_is_respected(capsys):
    db = ledger.open()
    add_ask(db, "R-11", "ALLOWED", age_days=40)
    add_ask(db, "R-11", "ALLOWED", age_days=1)
    db.close()
    assert stats(capsys, "--days", "7")["asks"]["by_rule"]["R-11"]["asks"] == 1
    assert stats(capsys, "--days", "60")["asks"]["by_rule"]["R-11"]["asks"] == 2


def test_an_allow_that_expired_unused_counts_as_answered(capsys):
    db = ledger.open()
    add_ask(db, "R-11", "EXPIRED")
    add_ask(db, "R-11", "EXPIRED")
    db.con.execute("UPDATE asks SET answered_ts=created_ts WHERE rowid=(SELECT MIN(rowid) FROM asks)")
    db.close()
    assert stats(capsys)["asks"]["by_rule"]["R-11"] == {"asks": 2, "allowed": 1, "denied": 0, "unanswered": 1}
