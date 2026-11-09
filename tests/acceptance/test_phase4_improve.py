"""Phase 4 acceptance (docs/06): protected targets, promotion gate, kill switch, async shadow, miner, rollback."""
import json

import pytest

from jade import adapter, ask, improve, ledger, ops, schemas
from tests.promo_helpers import group_of, seed, threshold_candidate


def test_candidate_cannot_target_protected():
    for target in ("promotion_rule", "safety_invariant", "permit_issuance", "hook_config", "ledger_transition", "policies/safety.yaml"):
        with pytest.raises(improve.ProtectedTarget):
            improve.propose(target_component=target, change_diff="x", hypothesis="y" * 10)
    doc = {"schema_version": "1.0.0", "candidate_id": "imp_000000000001", "baseline_version": "x", "target_component": "safety_invariant", "change_diff": "x",
           "hypothesis": "y" * 10, "predicted_benefit": "z", "risk": "low", "evaluation_plan": "e" * 10, "dataset_versions": ["d"], "results": None,
           "promotion_decision": "pending", "rollout_scope": "offline", "rollback_reference": "r", "audit_trail": []}
    with pytest.raises(Exception, match="safety_invariant"):
        schemas.validate("improvement", doc)  # the schema enum rejects it independently of the code check


def test_promotion_requires_protected_pass(tmp_path, human_env):
    db = ledger.open(tmp_path)
    c = improve.propose(target_component="calibration_threshold", change_diff="x", hypothesis="y" * 10, path=db)
    with pytest.raises(improve.PromotionBlocked, match="protected_test_non_inferior"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)  # nothing was evaluated
    with pytest.raises(improve.PromotionBlocked, match="computed by the evaluator"):
        improve.attach_results(db, c.candidate_id, {"protected_test_non_inferior": True, "no_safety_invariant_violation": True, "latency_within_budget": True})
    seed(db)
    cid = threshold_candidate(db)
    assert improve.promote(db, cid, stdin_isatty=True)["promotion_decision"] == "promoted"


def test_only_two_targets_auto_promote_and_only_with_opt_in(tmp_path, agent_env, monkeypatch):
    db = ledger.open(tmp_path)
    thr = improve.propose("calibration_threshold", "accept 0.7 -> 0.65", "y" * 10, path=db)
    with pytest.raises(improve.PromotionBlocked, match="human"):
        improve.promote(db, thr.candidate_id)
    ttl = improve.propose("cache_ttl", "1800 -> 900", "y" * 10, path=db)
    with pytest.raises(improve.PromotionBlocked, match="opt-in"):
        improve.promote(db, ttl.candidate_id)
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    monkeypatch.setenv("JADE_CONFIG", str(cfg))
    (cfg / "config.json").write_text(json.dumps({"auto_promotion_opt_in": True}))
    with pytest.raises(improve.PromotionBlocked, match="no evaluator"):
        improve.promote(db, ttl.candidate_id)   # allowed to auto-promote in principle, but nothing computes its results: it cannot pass


def test_kill_switch_freezes(tmp_path, human_env):
    db = ledger.open(tmp_path)
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    ops.set_challenger(tmp_path, "q.x", 2)
    assert ops.active_version(tmp_path, "q.x") == 2
    (tmp_path / "FREEZE").write_text("")
    assert improve.frozen(tmp_path) and ops.active_version(tmp_path, "q.x") is None  # challenger disabled while frozen
    with pytest.raises(improve.PromotionBlocked, match="FREEZE"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)


def test_shadow_adds_zero_hot_path_latency():
    assert improve.shadow_mode_is_async()
    import inspect

    assert "improve" not in inspect.getsource(ask.run).replace("from . import adapter, ledger, ops", "")  # ask never calls the improvement plane


def test_threshold_candidate_replay_and_promotion_writes_group_file(tmp_path, human_env):
    db = ledger.open(tmp_path)
    seed(db)
    g = group_of(db)
    c = improve.propose("calibration_threshold", f"{g}: accept 0.70 -> 0.85", "y" * 10, path=db)
    res = improve.replay_threshold(db, c.candidate_id, g, 0.85)["results"]
    assert res["n"] > 0 and "champion" in res and "challenger" in res and res["offline_replay_pass"] is True
    improve.protected_check(db, c.candidate_id, stdin_isatty=True)
    improve.promote(db, c.candidate_id, stdin_isatty=True)
    assert json.loads((tmp_path / "calibration" / "current.json").read_text())[g]["accept"] == 0.85
    improve.rollback(db, c.candidate_id, stdin_isatty=True)
    assert g not in json.loads((tmp_path / "calibration" / "current.json").read_text())


def test_replay_never_reads_the_protected_split():
    db = ledger.open()
    from jade import evals
    for i in range(60):
        prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.8} for k in q})
        r = ask.run(ask.verify_specs(companion=False), {"claim": f"c{i}", "evidence": "raw"}, provider=prov, session=f"sess-{i}", db=db)
        evals.set_label(db, r.primary.call_id, 1, "adjudicated")
    assert all(r["split"] != "protected" for r in evals.labeled(db))
    assert any(r["split"] == "protected" for r in evals.labeled(db, include_protected=True))


def test_miner_turns_ledger_patterns_into_candidates_once_per_week(tmp_path):
    db = ledger.open(tmp_path)
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.5} for k in q})
    for _ in range(8):  # eight repeats of one question with identical state in one session
        ask.run(ask.verify_specs(companion=False), {"claim": "same", "evidence": "same"}, provider=prov, session="s", db=db, use_cache=False)
    made = improve.mine(db, observer_root=tmp_path / "none")
    assert any(m["pattern"] == "repeat_without_new_evidence" for m in made)
    assert improve.mine(db, observer_root=tmp_path / "none") == []  # same ISO week: no duplicates
    row = db.one("SELECT doc_json FROM improvements")
    doc = json.loads(row.doc_json)
    schemas.validate("improvement", doc)
    assert doc["promotion_decision"] == "pending" and doc["target_component"] in improve.ALLOWED


def test_miner_writes_an_observation_through_the_task_observer_helper(tmp_path):
    root = tmp_path / "claude"
    helper = root / "skills" / "task-observer" / "scripts"
    helper.mkdir(parents=True)
    (helper / "new-observation.sh").write_text('#!/bin/bash\necho "obs-$2" > "$4/observed.txt"; echo "$4/observed.txt"\n'.replace('"$4/observed.txt"; echo', '"$4/observed.txt"; echo'))
    db = ledger.open(tmp_path)
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.5} for k in q})
    for _ in range(8):
        ask.run(ask.verify_specs(companion=False), {"claim": "same", "evidence": "same"}, provider=prov, session="s", db=db, use_cache=False)
    made = improve.mine(db, observer_root=root)
    assert any(m["observation"] for m in made) and (root / "observed.txt").exists()


def test_miner_includes_counts_only_never_state_text(tmp_path):
    db = ledger.open(tmp_path)
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.5} for k in q})
    for _ in range(8):
        ask.run(ask.verify_specs(companion=False), {"claim": "PRIVATE-MARKER-XYZ", "evidence": "PRIVATE-MARKER-XYZ"}, provider=prov, session="s", db=db, use_cache=False)
    improve.mine(db, observer_root=tmp_path / "none")
    assert "PRIVATE-MARKER-XYZ" not in "".join(r.doc_json for r in db.all("SELECT doc_json FROM improvements"))


def test_canary_serves_the_challenger_to_its_share_of_sessions_only(tmp_path):
    ops.pin_champion(tmp_path, "q.x", 1)
    ops.set_challenger(tmp_path, "q.x", 2, fraction=0.2)
    served = [ops.active_version(tmp_path, "q.x", key=f"session-{i}") for i in range(4000)]
    share = served.count(2) / len(served)
    assert 0.17 < share < 0.23 and set(served) == {1, 2}
    assert all(ops.active_version(tmp_path, "q.x", key="session-7") == ops.active_version(tmp_path, "q.x", key="session-7") for _ in range(3))  # sticky
    assert ops.active_version(tmp_path, "q.x") == 2  # the administrative view reports the challenger
    for bad in (0, 1.5):
        with pytest.raises(ValueError):
            ops.set_challenger(tmp_path, "q.x", 2, fraction=bad)


def test_shadow_run_compares_a_challenger_without_touching_live_calls(tmp_path, monkeypatch):
    import shutil

    from jade import paths, questions

    reg = tmp_path / "registry"
    shutil.copytree(paths.registry_dir(), reg)
    q = json.loads((reg / "q.verify_claim.supported_by_evidence.json").read_text())
    q["version"] = 2
    q["template"]["instructions"] = "Is `claim` directly supported by `evidence`? A claim whose evidence is a summary is not supported."
    (reg / "q.verify_claim.supported_by_evidence.v2.json").write_text(json.dumps(q))
    monkeypatch.setenv("JADE_REGISTRY_DIR", str(reg))
    questions.reset_cache()
    db = ledger.open(tmp_path / "home")
    prov = adapter.FixtureProvider(lambda s, q_, m: {k: {"type": "noul", "noul": 0.9} for k in q_})
    for i in range(12):
        ask.run(ask.verify_specs(companion=False), {"claim": f"claim {i}", "evidence": f"raw {i}"}, provider=prov, session=f"s{i}", db=db)
    cand = improve.propose("question_template", "add summary clause", "y" * 10, path=db)
    before = db.one("SELECT COUNT(*) AS n FROM jev_calls").n
    challenger = adapter.FixtureProvider(lambda s, q_, m: {k: {"type": "noul", "noul": 0.5 if "claim 3" in json.dumps(s) else 0.9} for k in q_})
    out = improve.shadow(db, cand.candidate_id, "q.verify_claim.supported_by_evidence", 2, provider=challenger, limit=50)
    assert out["n"] == 12 and out["agreement"] == round(11 / 12, 3) and len(out["disagreements"]) == 1 and out["min_live_samples_met"] is False
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls").n == before  # shadow never writes live call rows
    assert improve.get(db, cand.candidate_id)["results"]["shadow"]["n"] == 12
    (tmp_path / "home" / "FREEZE").write_text("")
    with pytest.raises(improve.PromotionBlocked):
        improve.shadow(db, cand.candidate_id, "q.verify_claim.supported_by_evidence", 2, provider=challenger)
