"""W-103 (docs/12 plan v3, PR-8): the human's verdict on calls that ran without a question, and what `jade review` says about them.

`jade review mark DECISION_ID right|wrong` (human terminal): `right` is a hint and changes nothing; `wrong` retires the answer behind the call and every answer of the same
exact call or kind, so the gate asks again. `jade review` adds the silent share, the marks, the wrong-rate bound and the count of owner-answered plain-high calls.
"""

# ruff: noqa: F811   (the fixtures are imported from test_memory_w101 and then named as test arguments)
import json

import pytest

from jade import hooks, ops
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_memory_w101 import OTHER, SESSION, enforce, medium, real_terminal_check  # noqa: F401  (fixtures)


def silent_decision(cmd=m.CMD, session=OTHER):
    """Ask, allow, confirm, then run the same call in another session from the standing answer. Returns that run's decision id."""
    m.ask_and_allow(cmd)
    m.confirm_all()
    assert hooks.pretool(m.call(cmd, session)).exit_code == 0
    return decision_ids("memory")[-1]


def decision_ids(channel):
    out = []
    for r in m.rows("SELECT decision_id, doc_json FROM decisions ORDER BY created_at, rowid"):
        ask = json.loads(r["doc_json"]).get("ask") or {}
        if ask.get("channel") == channel:
            out.append(r["decision_id"])
    return out


def mark(did, verdict):
    return ops.review_mark(did, verdict, stdin_isatty=True)


def finish(cmd=m.CMD, session=OTHER):
    """The host reports the silent run done, so SI-07 no longer holds the exact command and the next call is judged on its own."""
    hooks.posttool({**m.call(cmd, session), "tool_response": {"stdout": "ok"}})


# ------------------------------------------------------------------ the verdict

def test_a_mark_needs_a_human_terminal(medium, real_terminal_check):
    with pytest.raises(ops.HumanOnly):
        ops.review_mark("dec_x", "wrong", stdin_isatty=False)


def test_wrong_retires_the_standing_answer_and_the_gate_asks_again(medium):
    did = silent_decision()
    finish()
    assert "retired" in mark(did, "wrong")
    assert m.strong() == []
    r = hooks.pretool(m.call(session="sess-w103-3"))
    assert r.exit_code == 2 and "AskUserQuestion" in r.stderr and "SI-07" not in r.stderr


def test_right_changes_nothing(medium):
    did = silent_decision()
    finish()
    assert mark(did, "right") == f"marked {did} right"
    assert len(m.strong()) == 1
    r = hooks.pretool(m.call(session="sess-w103-3"))
    assert r.exit_code == 0 and "confirmed answer" in r.stderr


def test_the_latest_mark_wins(medium):
    did = silent_decision()
    mark(did, "right")
    mark(did, "wrong")
    assert m.strong() == []
    assert "1 wrong" in ops.review(None)


def test_wrong_on_a_call_the_human_allowed_in_the_chat_retires_the_hint_so_it_cannot_be_confirmed(medium):
    m.ask_and_allow()
    assert hooks.pretool(m.call()).exit_code == 0   # the identical retry spends the grant
    did = decision_ids("chat")[-1]
    assert "retired" in mark(did, "wrong")
    assert m.rows("SELECT strength FROM human_answers") == [{"strength": "revoked"}]
    assert m.confirm_all() == 0


def test_wrong_also_retires_the_other_answers_of_the_same_exact_call(medium):
    did = silent_decision()
    m.stored(strength="hint")   # a second, unconfirmed answer to the same call
    mark(did, "wrong")
    assert {r["strength"] for r in m.rows("SELECT strength FROM human_answers")} == {"revoked"}


def test_a_mark_does_not_touch_another_call(medium):
    other_cmd = "git reset --hard HEAD~2"
    m.ask_and_allow(other_cmd)
    m.confirm_all()
    did = silent_decision()
    mark(did, "wrong")
    assert [r["label"] for r in m.strong()] == [other_cmd]


@pytest.mark.parametrize("bad", ["maybe", "", "RIGHT"])
def test_only_right_and_wrong_are_accepted(medium, bad):
    did = silent_decision()
    with pytest.raises(ValueError):
        ops.review_mark(did, bad, stdin_isatty=True)


def test_an_unknown_decision_and_a_decision_with_no_ask_are_refused(medium):
    with pytest.raises(ValueError, match="no decision"):
        mark("dec_nope", "wrong")
    from jade import decide

    d = m.db()
    try:
        did = decide.open_decision(d, "Check something", command="echo hi")["decision_id"]
    finally:
        d.close()
    with pytest.raises(ValueError, match="nothing to mark"):
        mark(did, "wrong")


# ------------------------------------------------------------------ what `jade review` says

def test_the_wrong_rate_bound():
    assert ops.wrong_rate_upper(0, 50) == pytest.approx(0.06)
    assert ops.wrong_rate_upper(0, 0) == 1.0
    assert 0.05 < ops.wrong_rate_upper(1, 50) < 0.15
    assert ops.wrong_rate_upper(5, 10) > ops.wrong_rate_upper(5, 100)
    assert ops.wrong_rate_upper(10, 10) <= 1.0


def test_review_shows_the_silent_share_the_marks_and_the_recent_runs(medium):
    did = silent_decision()
    out = ops.review(None)
    assert "silent runs from standing answers: 1; questions answered: 1; silent share 50%" in out
    assert "none of the silent runs is marked yet" in out and f"{did}  [R-11]  git reset --hard HEAD~1" in out
    mark(did, "right")
    out = ops.review(None)
    assert "marked: 1 silent run(s), 1 right, 0 wrong; wrong runs are below" in out and "(marked right)" in out


def test_review_counts_owner_answered_plain_high_calls_toward_fifty_and_leaves_out_human_only_ones(medium):
    m.ask_and_allow()
    d = m.db()
    try:
        d.record_human_answer(session=SESSION, decision_id=None, rule_id="R-26", class_key=None, exact_key="e" * 64, answer="allow", channel="chat", ho=True)
    finally:
        d.close()
    assert "owner-answered plain-high calls: 1 of 50" in ops.review(None)


def test_review_says_nothing_extra_when_nothing_has_happened():
    assert "silent" not in ops.review(None) and ops.review(None).startswith("no answers recorded")


def test_the_cli_marks_through_the_review_command(medium, monkeypatch, capsys):
    from jade import cli

    did = silent_decision()
    assert cli.main(["review", "mark", did, "wrong"]) in (0, None)
    assert "retired" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["review", "mark", did])
