"""W-207 (V6-29 small items): a call that Jev let run in the owner's place is drawn into the audit like a call that ran from a standing answer, and `jade review` shows
the call that ran (its sealed text and folder), not the label of a standing answer.

The audit only ever adds questions: when a limit is reached, the scope is paused or the key cannot be had, Jev's yes does not run the call and the owner is asked.
"""

# ruff: noqa: F811   (the fixtures are imported from test_jev_proxy_w102 and then named as test arguments)
import json

from jade import audit, hooks, ledger, ops
from jade.util import ts
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_jev_proxy_w102 import KIND, asked, prov, ready  # noqa: F401  (fixtures: Jev on, past the band of owner answers)
from tests.acceptance.test_memory_audit_v629 import queue, scopes, set_audit
from tests.acceptance.test_memory_w101 import OTHER, SESSION, enforce, medium, real_terminal_check  # noqa: F401  (fixtures)
from tests.acceptance.test_review_marks_w103 import decision_ids, finish, mark


def events(kind):
    return m.rows("SELECT * FROM events WHERE kind=? ORDER BY id", (kind,))


def jev_run(session=SESSION):
    r = hooks.pretool(m.call(KIND, session))
    return r, (decision_ids("jev") or [None])[-1]


# ------------------------------------------------------------------ the draw

def test_a_call_that_jev_let_run_is_queued_with_its_text_a_stored_chance_of_one_and_a_valid_seal(ready):
    r, did = jev_run()
    assert r.exit_code == 0 and "Jev" in r.stderr and did
    [q] = queue()
    assert q["decision_id"] == did and q["sample_prob"] == 1.0 and q["stage"] == 1 and q["status"] == "pending" and q["rule_id"] == "R-11"
    assert q["subject"] == KIND and q["cwd"] == "/tmp" and q["session_id"] == SESSION
    d = m.db()
    try:
        key = audit._key(d, create=False)
        assert key is not None and q["seal"] == audit._seal(key, q)
    finally:
        d.close()
    [sc] = scopes().values()
    assert sc["runs"] == 1 and sc["status"] == "active" and sc["scope_id"] == q["scope_id"]
    [ev] = events("jev_allowed")
    assert json.loads(ev["detail_json"])["decision_id"] == did


def test_the_draw_of_a_jev_run_shares_the_weekly_limit_and_a_refused_call_is_asked_about_not_run_by_jev(ready):
    set_audit(weekly_count=1)
    assert jev_run()[0].exit_code == 0
    finish(KIND, SESSION)
    r = hooks.pretool(m.call(KIND, OTHER))
    assert asked(r)
    assert len(events("jev_allowed")) == 1 and len(queue()) == 1
    assert [e["guard"] for e in events("audit_refused")] == ["weekly_count"]
    assert len(decision_ids("jev")) == 1            # the refused call has no Jev decision: the hook did not walk one


def test_without_a_draw_key_a_jev_yes_does_not_run_the_call(ready, monkeypatch):
    def no_key(db, create):
        raise audit.Unavailable("no key")

    monkeypatch.setattr(audit, "_key", no_key)
    r, _ = jev_run()
    assert asked(r)
    assert events("jev_allowed") == [] and queue() == []
    assert [e["guard"] for e in events("audit_refused")] == ["no_key"]


def test_a_paused_scope_stops_jev_from_running_the_next_call_of_that_kind(ready):
    _, did = jev_run()
    finish(KIND, SESSION)
    assert "JADE asks again" in mark(did, "wrong")
    assert next(iter(scopes().values()))["status"] == "suspended"
    r = hooks.pretool(m.call(KIND, OTHER))
    assert asked(r)
    assert len(events("jev_allowed")) == 1


# ------------------------------------------------------------------ the verdict

def test_a_right_mark_on_a_jev_run_checks_the_queued_row_and_stores_the_label(ready):
    _, did = jev_run()
    mark(did, "right")
    [q] = queue()
    assert q["status"] == "right" and q["resolved_ts"]
    [lab] = m.rows("SELECT * FROM labels WHERE call_id=?", (did,))
    assert lab["label"] == 1 and lab["label_type"] == "owner_would_allow" and lab["audit_source"] == "random_audit" and lab["sample_prob"] == 1.0
    assert audit.summary(_db_for_summary(), "")["checked"] == 1
    assert next(iter(scopes().values()))["status"] == "active"


def test_a_wrong_mark_on_a_jev_run_pauses_the_scope_and_counts_one_wrong(ready):
    _, did = jev_run()
    mark(did, "wrong")
    [q] = queue()
    assert q["status"] == "wrong"
    [sc] = scopes().values()
    assert sc["status"] == "suspended" and sc["scope_id"] == q["scope_id"]
    [lab] = m.rows("SELECT * FROM labels WHERE call_id=?", (did,))
    assert lab["label"] == 0 and lab["audit_source"] == "random_audit"


def test_the_walk_offers_a_jev_run_with_its_call_and_folder(ready):
    _, did = jev_run()
    out, answers = [], iter(["r"])
    n = ops.review_audit_walk(stdin_isatty=True, ask=lambda _="": next(answers), say=out.append)
    assert n == 1
    assert any(did in x and KIND in x and "/tmp" in x for x in out), out
    assert queue()[0]["status"] == "right"


def _db_for_summary():
    return ledger.open(timeout=5)


# ------------------------------------------------------------------ `jade review` shows the call, not the label

def seed(*, label="LABEL OF THE STANDING ANSWER", subject="rm -rf build/ -- the call that ran", cwd="/work/here", seal_ok=True, kind="memory_allowed"):
    """A silent run as the ledger holds it: the answer (with its label), the event, and the drawn row (sealed, or edited after it was sealed)."""
    d = ledger.open(timeout=5)
    try:
        with d.tx():
            aid = d.record_human_answer(session=SESSION, decision_id=None, rule_id="R-11", class_key="k1", exact_key="e" * 64, answer="allow", channel="chat", label=label)
            if kind == "memory_allowed":
                d.event(aid, "memory_allowed", detail={"decision_id": "dec_seed", "channel": "memory"})
            else:
                d.event("call_seed", "jev_allowed", detail={"decision_id": "dec_seed", "rule": "R-11", "label": label, "exact_key": "e" * 64})
            key = audit._key(d, create=True)
            r = {"audit_id": "aud_seed", "decision_id": "dec_seed", "answer_id": aid, "scope_id": "k1", "rule_id": "R-11", "stage": 1, "sample_prob": 1.0, "session_id": SESSION,
                 "subject": subject, "cwd": cwd, "drawn_ts": ts(), "status": "pending", "resolved_ts": None}
            d.con.execute("INSERT INTO audit_queue(audit_id, decision_id, answer_id, scope_id, rule_id, stage, sample_prob, status, session_id, subject, cwd, drawn_ts, seal, source) "
                          "VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?,?,?)",
                          (r["audit_id"], r["decision_id"], aid, "k1", "R-11", 1, 1.0, SESSION, subject if seal_ok else subject + " EDITED", cwd, r["drawn_ts"],
                           audit._seal(key, r), d.source))
        return ops.review_lines(d, None)
    finally:
        d.close()


def test_review_shows_the_call_that_ran_and_not_the_label_of_the_standing_answer(medium):
    lines = seed()
    text = "\n".join(lines)
    assert "rm -rf build/ -- the call that ran" in text and "/work/here" in text
    assert "LABEL OF THE STANDING ANSWER" not in text


def test_review_shows_the_call_that_jev_ran_from_the_queue_too(medium):
    text = "\n".join(seed(kind="jev_allowed"))
    assert "rm -rf build/ -- the call that ran" in text and "LABEL OF THE STANDING ANSWER" not in text


def test_review_does_not_show_text_of_a_row_that_fails_its_seal_and_falls_back_to_the_label(medium):
    text = "\n".join(seed(seal_ok=False))
    assert "EDITED" not in text and "LABEL OF THE STANDING ANSWER" in text


def test_review_keeps_the_label_for_a_run_with_no_drawn_row(medium):
    d = ledger.open(timeout=5)
    try:
        with d.tx():
            aid = d.record_human_answer(session=SESSION, decision_id=None, rule_id="R-11", class_key="k1", exact_key="e" * 64, answer="allow", channel="chat", label="OLD LABEL")
            d.event(aid, "memory_allowed", detail={"decision_id": "dec_old", "channel": "memory"})
        text = "\n".join(ops.review_lines(d, None))
    finally:
        d.close()
    assert "OLD LABEL" in text
