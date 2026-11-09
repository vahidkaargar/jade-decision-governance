"""W-44: every canary rollback trigger of policies/promotion.yaml is evaluated from the ledger rows of the canary, and a target with no
evaluator cannot be promoted. Each test reproduces one trigger; the provider-error trigger is covered by the existing promotion tests."""
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from jade import improve, ledger, ops
from jade.util import new_id, now, ts
from tests.promo_helpers import Q, registry_v2, seed, template_candidate

SHADOW_MODEL = "typesafe/jev-1.14-fixture"   # what the fixture provider resolves to: recorded on the shadow run


def canary(tmp_path, monkeypatch):
    """A template candidate in canary, with every earlier ledger row aged out of the canary window and the window opened a day ago."""
    registry_v2(tmp_path, monkeypatch)
    home = tmp_path / "home"
    db = ledger.open(home)
    seed(db)
    ops.pin_champion(home, Q, 1)
    cid = template_candidate(db, tmp_path)
    db.con.execute("UPDATE jev_calls SET ts=?", (ts(now() - timedelta(days=3)),))
    doc = improve.get(db, cid)
    doc["results"]["canary"]["started_at"] = ts(now() - timedelta(days=1))
    db.con.execute("UPDATE improvements SET doc_json=? WHERE candidate_id=?", (json.dumps(doc), cid))
    return home, db, cid


def live_calls(db, version, true=0, false=0, *, model=SHADOW_MODEL, source="adjudicated", exit_code=0, when=None, dedup=False, test_source=False):
    """Labeled, accepted (exit 0) answers of one question version: `true` held, `false` did not."""
    for label, n in ((1, true), (0, false)):
        for _ in range(n):
            cid = new_id("call")
            db.con.execute("INSERT INTO jev_calls(call_id, question_id, question_version, primitive, task_family, resolved_model, state_hash, state, p, exit_code, "
                           "dedup_of, source, ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (cid, Q, version, "noul", "verify_claim", model, "h" * 64, "DONE", 0.95, exit_code, "x" if dedup else None,
                            "test" if test_source else None, when or ts()))
            db.con.execute("INSERT INTO labels(call_id, label, source, ts) VALUES(?,?,?,?)", (cid, label, source, ts()))


def promote(db, cid):
    return improve.promote(db, cid, stdin_isatty=True)["promotion_decision"]


def test_the_shadow_run_records_the_model_that_answered(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    assert improve.get(db, cid)["results"]["shadow"]["resolved_models"] == [SHADOW_MODEL]


def test_a_canary_with_no_labeled_traffic_has_no_trigger_to_fire(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    assert improve.canary_triggers(db, improve.get(db, cid)["results"]) == []
    assert promote(db, cid) == "promoted"


def test_false_accept_rate_above_the_champion_plus_margin_fires(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    live_calls(db, 1, true=40)
    live_calls(db, 2, true=16, false=4)                       # 0.20 against 0.00 + 0.03
    fired = improve.canary_triggers(db, improve.get(db, cid)["results"])
    assert len(fired) == 1 and "false_accept_rate" in fired[0]
    with pytest.raises(improve.PromotionBlocked, match="false_accept_rate"):
        improve.promote(db, cid, stdin_isatty=True)
    assert improve.get(db, cid)["promotion_decision"] == "canary"        # blocked, not decided


def test_false_accept_rate_within_the_margin_does_not_fire(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    live_calls(db, 1, true=40)
    live_calls(db, 2, true=39, false=1)                       # 0.025 <= 0.00 + 0.03
    assert improve.canary_triggers(db, improve.get(db, cid)["results"]) == []
    assert promote(db, cid) == "promoted"


def test_the_margin_is_measured_from_the_champions_own_false_accept_rate(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    live_calls(db, 1, true=36, false=4)                       # champion 0.10
    live_calls(db, 2, true=35, false=5)                       # 0.125 <= 0.13
    assert improve.canary_triggers(db, improve.get(db, cid)["results"]) == []
    live_calls(db, 2, true=0, false=5)                        # 10 of 45 = 0.22 > 0.13
    assert any("false_accept_rate" in f for f in improve.canary_triggers(db, improve.get(db, cid)["results"]))


def test_rows_that_are_not_canary_evidence_are_ignored(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    live_calls(db, 1, true=40)
    old = ts(now() - timedelta(days=2))
    live_calls(db, 2, false=10, when=old)                     # answered before the canary started
    live_calls(db, 2, false=10, dedup=True)                   # a cached repeat
    live_calls(db, 2, false=10, source="heuristic")           # not an independent label
    live_calls(db, 2, false=10, test_source=True)             # a verification run, not live data
    live_calls(db, 2, false=10, exit_code=3)                  # abstained: nothing was accepted
    live_calls(db, 2, true=20)
    assert improve.canary_triggers(db, improve.get(db, cid)["results"]) == []


def test_without_a_champion_baseline_the_challenger_is_held_to_zero_false_accepts(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)             # no labeled champion traffic in the window
    live_calls(db, 2, true=6, false=4)
    assert any("false_accept_rate" in f for f in improve.canary_triggers(db, improve.get(db, cid)["results"]))


def test_a_resolved_model_other_than_the_shadowed_one_fires(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    live_calls(db, 2, true=5, model="vendor/other-model-9")
    fired = improve.canary_triggers(db, improve.get(db, cid)["results"])
    assert len(fired) == 1 and "resolved_model" in fired[0] and "vendor/other-model-9" in fired[0]
    with pytest.raises(improve.PromotionBlocked, match="resolved_model"):
        improve.promote(db, cid, stdin_isatty=True)


def test_the_shadowed_model_and_champion_traffic_do_not_fire_the_model_trigger(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    live_calls(db, 2, true=5)
    live_calls(db, 1, true=5, model="vendor/champion-only-model")   # the champion's own model is not the challenger's to explain
    assert improve.canary_triggers(db, improve.get(db, cid)["results"]) == []


def test_a_shadow_that_recorded_no_model_cannot_vouch_for_live_traffic(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    doc = improve.get(db, cid)
    del doc["results"]["shadow"]["resolved_models"]            # a shadow run from before the model was recorded
    db.con.execute("UPDATE improvements SET doc_json=? WHERE candidate_id=?", (json.dumps(doc), cid))
    assert improve.canary_triggers(db, improve.get(db, cid)["results"]) == []        # no traffic, nothing to compare
    live_calls(db, 2, true=5)
    assert any("resolved_model" in f for f in improve.canary_triggers(db, improve.get(db, cid)["results"]))


def test_a_canary_with_no_recorded_start_cannot_be_judged(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    doc = improve.get(db, cid)
    del doc["results"]["canary"]["started_at"]
    db.con.execute("UPDATE improvements SET doc_json=? WHERE candidate_id=?", (json.dumps(doc), cid))
    with pytest.raises(improve.PromotionBlocked, match="canary"):
        improve.promote(db, cid, stdin_isatty=True)


def test_the_provider_error_trigger_still_fires(tmp_path, human_env, monkeypatch):
    home, db, cid = canary(tmp_path, monkeypatch)
    for _ in range(40):                                        # the seeded history is 300-odd calls inside the 7-day window
        db.con.execute("INSERT INTO jev_calls(call_id, question_id, question_version, primitive, state_hash, state, error_kind, exit_code, ts) VALUES(?,?,?,?,?,?,?,?,?)",
                       (new_id("call"), Q, 2, "noul", "h" * 64, "DONE", "UNAVAILABLE", 5, ts()))
    with pytest.raises(improve.PromotionBlocked, match="provider error rate"):
        improve.promote(db, cid, stdin_isatty=True)


def test_the_margin_and_the_triggers_are_the_ones_policies_promotion_yaml_states():
    triggers = improve.policy()["canary"]["rollback_triggers"]
    margin = float(re.search(r"false_accept_rate > champion \+ ([0-9.]+)", " ".join(triggers)).group(1))
    assert margin == improve.CANARY_FALSE_ACCEPT_MARGIN
    assert any("provider_error_rate" in t for t in triggers) and any("resolved_model" in t for t in triggers)


# ---- targets with no evaluator cannot be promoted (W-44) --------------------------------------------------------------------------------------
@pytest.mark.parametrize("target", [t for t in improve.ALLOWED if t not in improve.EVALUATED])
def test_a_target_with_no_evaluator_is_blocked_even_for_a_human(tmp_path, human_env, target):
    db = ledger.open(tmp_path / "home")
    c = improve.propose(target, "x", "y" * 10, path=db)
    with pytest.raises(improve.PromotionBlocked, match="no evaluator"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)
    assert improve.get(db, c.candidate_id)["promotion_decision"] == "pending"
    with pytest.raises(improve.PromotionBlocked):
        improve.start_canary(db, c.candidate_id, stdin_isatty=True)       # cache_ttl is in the canary scope of the policy and has no canary code


def test_the_docs_name_the_targets_that_are_evaluated_and_the_ones_that_are_not():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "07-self-improvement-and-work-management.md").read_text()
    assert "`calibration_threshold`" in doc and "`question_template`" in doc
    assert all(f"`{t}`" in doc for t in improve.ALLOWED if t not in improve.EVALUATED)
