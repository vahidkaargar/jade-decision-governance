"""V6-23 (DI-8): who saw the goal reached decides whether an outcome may label a call.

The state path does not change: an exit-status close still reaches SUCCEEDED and still releases the SI-07 hold. Only the label changes. `exit_only` (the hook's
own close) and `agent_reported` (an agent said so) never label for fit, and neither does an outcome written before the mark existed. `independent` and `human` do.
The mark is written by code, last: neither the method text nor a `goal_check` key in a caller's dict can set it. A label also says what kind of fact it is; the
kinds are never merged and only `claim_true` fits.
"""
import json

import pytest

from jade import adapter, ask, cli, decide, evals, ledger, state_machine
from jade.util import ts
from tests.acceptance.test_posttool_failure_w100d import (  # noqa: F401 - `enforce` is a fixture of that module
    allow_and_run,
    enforce,
    failure,
)


def noul(p):
    return adapter.FixtureProvider(lambda s, q, m, p=p: {k: {"type": "noul", "noul": p} for k in q})


def accepted_decision(db, claim="the deploy worked", session="s"):
    """A decision whose last Jev call accepted a verify claim, walked to EXECUTING: the state a decision is in when an outcome arrives."""
    did = decide.open_decision(db, "Report that the deploy worked")["decision_id"]
    res = ask.run(ask.verify_specs(companion=False), {"claim": claim, "evidence": "42 passed"}, provider=noul(0.9), session=session, db=db, decision_id=did)
    assert res.exit_code == 0
    for ev in ("authorize", "execute"):
        decide.step(db, did, ev)
    return did, res.primary.call_id


def outcome_check(db, did):
    row = db.one("SELECT doc_json FROM outcomes WHERE decision_id=?", (did,))
    return json.loads(row.doc_json)["measurements"].get("goal_check")


def test_an_exit_status_close_never_labels_but_still_reaches_succeeded():
    db = ledger.open()
    did, call = accepted_decision(db)
    decide.record_outcome(db, did, "observed", True, "tool call returned (exit status only; the goal is not verified)", attribution="indirect", goal_check="exit_only")
    assert decide.get(db, did)["status"] == "SUCCEEDED"   # the state path is unchanged ...
    assert "SUCCEEDED" not in state_machine.HOLDING and "FAILED" not in state_machine.HOLDING   # ... so the SI-07 hold is released
    n = evals.auto_label(db)
    assert n["observed"] == 0 and n["not_fit"] == {"exit_only": 1}
    assert db.one("SELECT COUNT(*) AS n FROM labels").n == 0 and evals.labeled(db, include_protected=True) == []


def test_the_hook_close_writes_exit_only():
    allow_and_run()
    from jade import hooks

    assert hooks.posttool_failure(failure()).exit_code == 0
    db = ledger.open(timeout=5)
    try:
        did = db.one("SELECT decision_id FROM decisions").decision_id
        assert outcome_check(db, did) == "exit_only" and decide.get(db, did)["status"] == "FAILED"
    finally:
        db.close()


def test_forged_method_text_stays_agent_reported_and_never_labels():
    db = ledger.open()
    did, call = accepted_decision(db)
    decide.record_outcome(db, did, "observed", True, "ci passed", evidence_refs=["https://ci.example.test/run/1"])
    assert outcome_check(db, did) == "agent_reported"
    assert decide.get(db, did)["status"] == "SUCCEEDED"
    n = evals.auto_label(db)
    assert n["observed"] == 0 and n["not_fit"] == {"agent_reported": 1}


def test_a_goal_check_in_the_callers_measurements_is_overwritten():
    db = ledger.open()
    did, _ = accepted_decision(db)
    out = decide.record_outcome(db, did, "observed", True, "checked", measurements={"goal_check": "independent", "duration_s": 3})
    assert out["measurements"] == {"duration_s": 3, "goal_check": "agent_reported"}
    assert evals.auto_label(db)["observed"] == 0


def test_an_unknown_goal_check_is_refused():
    db = ledger.open()
    did, _ = accepted_decision(db)
    with pytest.raises(ValueError):
        decide.record_outcome(db, did, "observed", True, "checked", goal_check="trust_me")


def test_the_cli_always_records_an_agent_outcome_as_agent_reported(agent_env, capsys):
    db = ledger.open()
    did, _ = accepted_decision(db)
    db.close()
    assert cli.main(["decide", "outcome", did, "--type", "observed", "--success", "true", "--method", "ci passed"]) == 0
    db = ledger.open()
    assert outcome_check(db, did) == "agent_reported" and evals.auto_label(db)["observed"] == 0


@pytest.mark.parametrize("check", ["independent", "human"])
def test_an_independent_or_human_check_labels_the_call(check):
    db = ledger.open()
    did, call = accepted_decision(db)
    decide.record_outcome(db, did, "observed", True, "read the CI status file", goal_check=check)
    assert evals.auto_label(db) == {"observed": 1, "heuristic": 0, "not_fit": {}}
    row = db.one("SELECT label, source, label_type FROM labels WHERE call_id=?", (call,))
    assert (row.label, row.source, row.label_type) == (1, "observed", "claim_true")


def test_a_failed_decision_is_filtered_the_same_way():
    db = ledger.open()
    did, call = accepted_decision(db)
    decide.record_outcome(db, did, "observed", False, "the agent saw it fail")
    assert evals.auto_label(db)["observed"] == 0 and db.one("SELECT COUNT(*) AS n FROM labels").n == 0   # FAILED used to label with no look at the outcome
    did2, call2 = accepted_decision(db, claim="a second claim")
    decide.record_outcome(db, did2, "observed", False, "a person looked", goal_check="human")
    assert evals.auto_label(db)["observed"] == 1
    assert db.one("SELECT label FROM labels WHERE call_id=?", (call2,)).label == 0


def test_an_outcome_written_before_the_mark_existed_is_not_fit_and_is_counted():
    db = ledger.open()
    did, _ = accepted_decision(db)
    db.con.execute("UPDATE decisions SET status='SUCCEEDED' WHERE decision_id=?", (did,))
    db.con.execute("INSERT INTO outcomes(outcome_id, decision_id, doc_json, ts) VALUES('o_old', ?, ?, ?)", (did, json.dumps({"observation_type": "observed", "success": True}), ts()))
    assert evals.auto_label(db) == {"observed": 0, "heuristic": 0, "not_fit": {"unmarked": 1}}


# ---- label kinds ------------------------------------------------------------------------------------------------------------
def answered_call(db, claim="claim", p=0.9, session="s"):
    return ask.run(ask.verify_specs(companion=False), {"claim": claim, "evidence": "e"}, provider=noul(p), session=session, db=db).primary.call_id


def test_a_label_of_another_kind_is_never_merged_and_never_fits():
    db = ledger.open()
    call = answered_call(db)
    evals.set_label(db, call, 1, "adjudicated", "owner would run it", label_type="owner_would_allow", sample_prob=0.25, audit_source="random_audit")
    row = db.one("SELECT label, label_type, sample_prob, audit_source FROM labels WHERE call_id=?", (call,))
    assert (row.label, row.label_type, row.sample_prob, row.audit_source) == (1, "owner_would_allow", 0.25, "random_audit")
    with pytest.raises(evals.LabelTypeConflict):
        evals.set_label(db, call, 1, "adjudicated", label_type="claim_true")
    with pytest.raises(evals.LabelTypeConflict):
        evals.set_label(db, call, 0, "adjudicated", label_type="harm_observed")
    assert evals.labeled(db, include_protected=True) == [] and evals.labeled(db, fit_only=False, include_protected=True) == []
    assert evals.choice_rows(db, include_protected=True) == []
    assert evals.report(db, include_protected=True)["groups"] == {}
    assert db.one("SELECT label FROM labels WHERE call_id=?", (call,)).label == 1   # the refused writes changed nothing


def test_a_row_with_no_type_reads_as_a_claim_label_and_a_second_write_may_replace_it():
    db = ledger.open()
    call = answered_call(db)
    db.con.execute("INSERT INTO labels(call_id, label, source, ts) VALUES(?,?,?,?)", (call, 1, "adjudicated", ts()))   # a row from before V6-23
    assert len(evals.labeled(db, include_protected=True)) == 1
    evals.set_label(db, call, 0, "adjudicated", sample_prob=0.5, audit_source="adjudication")
    row = db.one("SELECT label, label_type, sample_prob FROM labels WHERE call_id=?", (call,))
    assert (row.label, row.label_type, row.sample_prob) == (0, "claim_true", 0.5)
    evals.set_label(db, call, 1, "adjudicated")   # a value left out does not erase the stored one
    assert db.one("SELECT sample_prob, audit_source FROM labels WHERE call_id=?", (call,)).sample_prob == 0.5


@pytest.mark.parametrize("kw", [{"label_type": "vibes"}, {"sample_prob": 0}, {"sample_prob": 1.5}, {"audit_source": "because I said so"}])
def test_bad_label_fields_are_refused(kw):
    db = ledger.open()
    call = answered_call(db)
    with pytest.raises(ValueError):
        evals.set_label(db, call, 1, "adjudicated", **kw)
    assert db.one("SELECT COUNT(*) AS n FROM labels").n == 0


def test_auto_label_leaves_a_call_that_holds_another_kind_alone():
    db = ledger.open()
    did, call = accepted_decision(db)
    evals.set_label(db, call, 1, "adjudicated", label_type="harm_observed")
    decide.record_outcome(db, did, "observed", True, "a person looked", goal_check="human")
    assert evals.auto_label(db)["observed"] == 0   # no exception, no merge
    assert db.one("SELECT label_type, source FROM labels WHERE call_id=?", (call,)).label_type == "harm_observed"
