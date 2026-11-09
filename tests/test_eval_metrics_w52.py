"""W-52: the evaluation metrics docs/04 section 7 listed as designed, each checked against synthetic truth, Holm across the paired
metrics, and the protected-split minimum. Offline and synthetic: nothing here reads a live ledger or a provider."""
import contextlib
import io
import json
import random
from datetime import timedelta
from pathlib import Path

import pytest

from jade import adapter, ask, cli, decide, evals, improve, ledger, paths
from jade.util import now, ts
from tests import promo_helpers as ph

ROOT = Path(__file__).resolve().parents[1]


# ---- selective risk versus coverage ----------------------------------------------------------------
def test_risk_coverage_curve_on_known_data():
    # confident and right, then less confident and wrong: the most confident half has no errors, all rows have 2 of 8
    preds = [0.99, 0.98, 0.02, 0.01, 0.6, 0.4, 0.55, 0.45]
    labels = [1, 1, 0, 0, 0, 1, 1, 0]
    curve = evals.risk_coverage_curve(preds, labels, points=4)
    assert [c["coverage"] for c in curve] == [0.25, 0.5, 0.75, 1.0]
    assert curve[1]["selective_risk"] == 0.0 and curve[1]["n"] == 4
    assert curve[2]["selective_risk"] == pytest.approx(2 / 6) and curve[3]["selective_risk"] == pytest.approx(0.25)   # the two 0.6-confidence rows are wrong
    assert all(a["min_confidence"] >= b["min_confidence"] for a, b in zip(curve, curve[1:], strict=False))


def test_risk_coverage_curve_is_flat_zero_for_a_perfect_model_and_rises_for_a_useless_one():
    rng = random.Random(4)
    perfect = evals.risk_coverage_curve([1.0, 0.0] * 50, [1, 0] * 50)
    assert {c["selective_risk"] for c in perfect} == {0.0}
    preds = [rng.random() for _ in range(4000)]
    useless = [rng.randint(0, 1) for _ in preds]
    curve = evals.risk_coverage_curve(preds, useless)
    assert 0.4 < curve[-1]["selective_risk"] < 0.6 and 0.4 < curve[0]["selective_risk"] < 0.6   # confidence says nothing about being right
    informed = [1 if rng.random() < p else 0 for p in preds]
    good = evals.risk_coverage_curve(preds, informed)
    assert good[0]["selective_risk"] < good[-1]["selective_risk"]   # abstaining on the least confident rows lowers the risk


def test_risk_coverage_curve_does_not_depend_on_the_order_of_tied_rows():
    preds = [0.5] * 100
    a = evals.risk_coverage_curve(preds, [0] * 10 + [1] * 90)
    b = evals.risk_coverage_curve(preds, [1] * 90 + [0] * 10)
    assert a == b and all(c["selective_risk"] == pytest.approx(0.1) for c in a)


def test_risk_coverage_curve_rejects_bad_input():
    with pytest.raises(ValueError):
        evals.risk_coverage_curve([], [])
    with pytest.raises(ValueError):
        evals.risk_coverage_curve([0.5], [1, 0])


# ---- bootstrap interval on ECE ---------------------------------------------------------------------
def test_ece_bootstrap_brackets_the_point_estimate_and_is_deterministic():
    rng = random.Random(3)
    preds = [rng.random() for _ in range(600)]
    labels = [1 if rng.random() < p else 0 for p in preds]
    a, b = evals.ece_bootstrap(preds, labels, rounds=200), evals.ece_bootstrap(preds, labels, rounds=200)
    assert a == b
    assert a["ece"] == pytest.approx(evals.metrics(preds, labels)["ece"])
    assert a["n"] == 600 and a["ci95"][0] <= a["ci95"][1]
    assert 0 <= a["ci95"][0] and a["ci95"][1] <= 1


def test_ece_bootstrap_interval_separates_a_calibrated_model_from_an_overconfident_one():
    rng = random.Random(8)
    preds = [rng.random() for _ in range(3000)]
    labels = [1 if rng.random() < p else 0 for p in preds]
    good = evals.ece_bootstrap(preds, labels, rounds=200)
    bad = evals.ece_bootstrap([min(1.0, p + 0.25) for p in preds], labels, rounds=200)
    assert good["ci95"][1] < bad["ci95"][0]


def test_ece_bootstrap_is_wider_on_a_small_sample():
    rng = random.Random(1)
    preds = [rng.random() for _ in range(2000)]
    labels = [1 if rng.random() < p else 0 for p in preds]
    big = evals.ece_bootstrap(preds, labels, rounds=200)
    small = evals.ece_bootstrap(preds[:60], labels[:60], rounds=200)
    assert small["ci95"][1] - small["ci95"][0] > big["ci95"][1] - big["ci95"][0]


# ---- choice questions: top-1 and margin ------------------------------------------------------------
def test_choice_metrics_on_known_data():
    rows = [{"probabilities": {"a": 0.8, "b": 0.15, "c": 0.05}, "label": 1},    # margin 0.65, right
            {"probabilities": {"a": 0.5, "b": 0.45, "c": 0.05}, "label": 0},    # margin 0.05, wrong
            {"probabilities": {"a": 0.2, "b": 0.7, "c": 0.1}, "label": 1},      # margin 0.5, right
            {"probabilities": {"a": 0.4, "b": 0.35, "c": 0.25}, "label": 0}]    # margin 0.05, wrong
    m = evals.choice_metrics(rows)
    assert m["n"] == 4 and m["top1_accuracy"] == 0.5 and m["top1_ci95"][0] < 0.5 < m["top1_ci95"][1]
    assert m["margin"]["min"] == pytest.approx(0.05) and m["margin"]["max"] == pytest.approx(0.65)
    assert m["margin"]["median"] == pytest.approx(0.275)
    # the rows with the larger margin are the ones that were right
    assert m["by_margin"]["wide"]["accuracy"] == 1.0 and m["by_margin"]["narrow"]["accuracy"] == 0.0
    assert m["by_margin"]["wide"]["n"] + m["by_margin"]["narrow"]["n"] == 4


def test_choice_metrics_empty_and_malformed():
    assert evals.choice_metrics([])["n"] == 0
    assert evals.choice_metrics([{"probabilities": {"a": 1.0}, "label": 1}])["n"] == 0   # one option has no margin: not counted
    assert evals.choice_metrics([{"probabilities": "x", "label": 1}, {"label": 1}])["n"] == 0


def test_choice_rows_come_from_labelled_choice_calls():
    db = ledger.open()
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "choice", "choice": "a", "confidence": 0.7, "probabilities": {"a": 0.7, "b": 0.3}} for k in q})
    r = ask.run([ask.adhoc_spec("choice", "which?", options=["a=first", "b=second"])], {"context": "pick one"}, provider=prov, session="sc1", db=db)
    call = r.primary.call_id
    assert evals.choice_rows(db) == []                      # unlabelled: nothing to score
    evals.set_label(db, call, 1, "adjudicated")
    rows = evals.choice_rows(db)
    assert len(rows) == 1 and rows[0]["label"] == 1 and rows[0]["probabilities"] == {"a": 0.7, "b": 0.3}
    evals.set_label(db, call, 0, "observed")
    assert evals.report(db)["choice"]["n"] == 1


# ---- FAST / JEV / DEEP confusion matrix ------------------------------------------------------------
def test_routing_confusion_on_known_pairs():
    pairs = [("FAST", "FAST"), ("FAST", "FAST"), ("FAST", "JEV"), ("JEV", "JEV"), ("JEV", "FAST"), ("DEEP", "JEV"), ("DEEP", "DEEP")]
    m = evals.routing_confusion(pairs)
    assert m["n"] == 7 and m["accuracy"] == pytest.approx(4 / 7)
    assert m["matrix"]["FAST"] == {"FAST": 2, "JEV": 1} and m["matrix"]["JEV"] == {"JEV": 1, "FAST": 1} and m["matrix"]["DEEP"] == {"JEV": 1, "DEEP": 1}
    assert m["recall"]["FAST"] == pytest.approx(2 / 3) and m["recall"]["DEEP"] == 0.5
    assert m["jev_false_positive_rate"] == pytest.approx(1 / 3)    # Jev called where FAST sufficed: 1 of the 3 FAST cases
    assert m["jev_false_negative_rate"] == pytest.approx(1 / 2)    # Jev was needed and the router said FAST: 1 of 2 JEV cases


def test_routing_confusion_rejects_an_unknown_route_and_handles_no_fast_cases():
    with pytest.raises(ValueError):
        evals.routing_confusion([("FAST", "MAYBE")])
    m = evals.routing_confusion([("JEV", "JEV")])
    assert m["jev_false_positive_rate"] is None and m["jev_false_negative_rate"] == 0.0
    assert evals.routing_confusion([])["n"] == 0


def test_routing_confusion_from_the_shipped_set_matches_the_runner_and_counts_the_known_gaps():
    cases = evals.load_cases(ROOT / "evals" / "routing" / "cases.jsonl")
    m = evals.routing_confusion_from_cases(cases)
    assert m["n"] == len(cases) == sum(sum(row.values()) for row in m["matrix"].values())
    gaps = [c for c in cases if c.get("known_gap")]
    off_diagonal = m["n"] - sum(m["matrix"].get(r, {}).get(r, 0) for r in m["matrix"])
    assert off_diagonal == len(gaps)      # the runner passes every case that is not a recorded known gap, so the matrix agrees with it


def test_eval_routing_command_prints_the_matrix():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main(["eval", "routing", "--cases", str(ROOT / "evals" / "routing" / "cases.jsonl")])
    assert rc == 0 and json.loads(out.getvalue())["n"] > 0


# ---- Holm correction -------------------------------------------------------------------------------
def test_holm_adjusted_values_known():
    adj = evals.holm_adjust({"a": 0.01, "b": 0.02, "c": 0.03})
    assert adj == {"a": pytest.approx(0.03), "b": pytest.approx(0.04), "c": pytest.approx(0.04)}
    assert evals.holm_adjust({"x": 0.5})["x"] == 0.5 and evals.holm_adjust({}) == {}
    assert evals.holm_adjust({"a": 0.4, "b": 0.9}) == {"a": pytest.approx(0.8), "b": 0.9}
    assert evals.holm_adjust({"a": 0.9, "b": 0.8}) == {"a": 1.0, "b": 1.0}   # capped at 1


def test_holm_adjust_agrees_with_the_step_down_rejection_set_used_by_the_promotion_gate():
    rng = random.Random(11)
    for _ in range(200):
        p = {f"m{i}": round(rng.random() ** 2, 4) for i in range(rng.randint(1, 6))}
        adj = evals.holm_adjust(p)
        assert {k for k, v in adj.items() if v <= 0.05} == improve.holm_rejected(p, 0.05)
        assert all(adj[k] >= p[k] for k in p)


def test_one_spurious_win_among_many_metrics_does_not_survive_holm():
    p = {f"m{i}": 0.5 for i in range(9)} | {"lucky": 0.04}
    assert evals.holm_adjust(p)["lucky"] > 0.05      # 0.04 alone would pass; among ten tests it does not


def test_paired_bootstrap_reports_a_two_sided_p():
    a, b = [1] * 40 + [0] * 160, [1] * 100 + [0] * 100
    assert evals.paired_bootstrap(a, b)["p"] < 0.01
    assert evals.paired_bootstrap(a, a)["p"] == 1.0
    assert 0 < evals.paired_bootstrap([1, 0] * 30, [0, 1] * 30, rounds=500)["p"] <= 1.0


def test_conditions_compares_several_metrics_and_applies_holm():
    db = ledger.open()
    ph.seed(db, hold=0, val=0)
    res = evals.conditions(db)
    cmp_ = res["paired_3_minus_1"]
    assert cmp_["n"] == res["n"] and "diff" in cmp_ and "ci95" in cmp_                 # the earlier shape stays
    metrics = res["paired_metrics"]
    assert set(metrics) == {"correct_decision", "unsafe_accept_vs_chance", "safe_accept_vs_chance"}
    for m in metrics.values():
        assert {"diff", "ci95", "p", "holm_p", "significant_after_holm", "win"} <= set(m)
        assert m["holm_p"] >= m["p"]
    assert res["multiple_testing"] == "holm" and res["alpha"] == 0.05
    assert set(res["paired_descriptive"]) == {"unsafe_accept", "safe_accept", "abstain"}
    assert res["paired_descriptive"]["unsafe_accept"]["diff"] < 0       # thresholds accept fewer false claims than taking every answer as a yes
    assert metrics["unsafe_accept_vs_chance"]["diff"] < 0 and metrics["unsafe_accept_vs_chance"]["win"] is True


def _conditions_on(monkeypatch, rows):
    monkeypatch.setattr(evals, "labeled", lambda db, **k: rows)
    return evals.conditions(None)


@pytest.mark.parametrize("judge", ["noise", "abstains", "accepts_all", "inverted"])
def test_conditions_never_calls_an_uninformative_or_inverted_judge_a_win(monkeypatch, judge):
    rng = random.Random(1)
    labels = [rng.randint(0, 1) for _ in range(120)]
    p = {"noise": lambda y: rng.random(), "abstains": lambda y: 0.5, "accepts_all": lambda y: 0.99, "inverted": lambda y: 0.01 if y else 0.99}[judge]
    res = _conditions_on(monkeypatch, [{"p": p(y), "label": y, "task_family": "x", "risk": "low"} for y in labels])
    assert not any(m["win"] for m in res["paired_metrics"].values()), res["paired_metrics"]


def test_conditions_calls_an_informative_judge_a_win(monkeypatch):
    rng = random.Random(2)
    labels = [rng.randint(0, 1) for _ in range(120)]
    res = _conditions_on(monkeypatch, [{"p": 0.99 if y else 0.01, "label": y, "task_family": "x", "risk": "low"} for y in labels])
    assert res["paired_metrics"]["unsafe_accept_vs_chance"]["win"] and res["paired_metrics"]["correct_decision"]["win"]


def test_conditions_with_no_rows_still_says_so():
    assert evals.conditions(ledger.open())["n"] == 0


# ---- risk >= high slice, report additions -----------------------------------------------------------
def _answer(db, session, p, claim, label, risk="low"):
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": p} for k in q})
    r = ask.run(ask.verify_specs(companion=False), {"claim": claim, "evidence": "raw output " + claim}, provider=prov, session=session, db=db, risk=risk)
    evals.set_label(db, r.primary.call_id, label, "adjudicated")
    return r.primary.call_id


def test_report_has_the_curve_the_ece_interval_and_the_high_risk_slice():
    db = ledger.open()
    rng = random.Random(2)
    for i in range(80):
        p = rng.choice([0.05, 0.3, 0.6, 0.8, 0.95])
        sess = f"rep{i}"
        if evals.bucket(sess) == "protected":
            continue
        _answer(db, sess, p, f"claim {i}", 1 if rng.random() < p else 0, risk="high" if i % 4 == 0 else "low")
    g = next(iter(evals.report(db)["groups"].values()))
    assert g["risk_coverage"] and {"coverage", "selective_risk", "min_confidence", "n"} <= set(g["risk_coverage"][0])
    assert len(g["ece_ci95"]) == 2 and g["ece_ci95"][0] <= g["ece_ci95"][1]
    hi = g["risk_high"]
    assert hi["n"] > 0 and hi["n"] < g["n"] and hi["thresholds"]["accept"] >= g["thresholds"]["accept"]   # stricter band for high risk, never looser


def test_high_risk_slice_is_absent_when_the_group_has_no_high_risk_rows():
    db = ledger.open()
    _answer(db, "only-low-1", 0.9, "c1", 1)
    g = next(iter(evals.report(db)["groups"].values()))
    assert g["risk_high"] is None


# ---- ledger operations: calls per decision, cost, time to resolution ----------------------------------
def _decision_with_calls(db, n_calls, cost, session):
    d = decide.open_decision(db, "Report that the work is done", command=None)
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    for i in range(n_calls):
        r = ask.run(ask.verify_specs(companion=False), {"claim": f"claim {session} {i}", "evidence": f"raw {session} {i}"}, provider=prov, session=session, db=db,
                    decision_id=d["decision_id"])
        db.con.execute("UPDATE jev_calls SET cost_usd=? WHERE call_id=?", (cost, r.primary.call_id))
    db.con.commit()
    return d["decision_id"]


def test_operations_calls_per_decision_and_cost_per_verified_decision():
    db = ledger.open()
    a = _decision_with_calls(db, 2, 0.01, "opsA")
    b = _decision_with_calls(db, 1, 0.01, "opsB")
    _decision_with_calls(db, 0, 0.0, "opsC")
    for dec in (a, b):
        db.con.execute("UPDATE decisions SET status='SUCCEEDED' WHERE decision_id=?", (dec,))
    db.con.commit()
    ops = evals.operations(db)
    assert ops["calls_per_decision"]["decisions"] == 3 and ops["calls_per_decision"]["calls"] == 3
    assert ops["calls_per_decision"]["mean"] == pytest.approx(1.0) and ops["calls_per_decision"]["mean_of_called"] == pytest.approx(1.5)
    assert ops["calls_per_decision"]["max"] == 2
    c = ops["cost_per_verified_decision"]
    assert c["verified_decisions"] == 2 and c["total_cost_usd"] == pytest.approx(0.03) and c["usd"] == pytest.approx(0.015)


def test_operations_a_gate_only_decision_is_not_a_verified_decision():
    db = ledger.open()
    a = _decision_with_calls(db, 1, 0.01, "opsE")
    gate = [_decision_with_calls(db, 0, 0.0, f"opsG{i}") for i in range(9)]
    for dec in [a, *gate]:
        db.con.execute("UPDATE decisions SET status='SUCCEEDED' WHERE decision_id=?", (dec,))
    db.con.commit()
    c = evals.operations(db)["cost_per_verified_decision"]
    assert c["verified_decisions"] == 1 and c["usd"] == pytest.approx(0.01)


def test_operations_cost_per_verified_decision_is_none_with_no_verified_decision():
    db = ledger.open()
    _decision_with_calls(db, 1, 0.02, "opsD")
    c = evals.operations(db)["cost_per_verified_decision"]
    assert c["verified_decisions"] == 0 and c["usd"] is None and c["total_cost_usd"] == pytest.approx(0.02)


def test_operations_leaves_out_rows_a_verification_run_wrote(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    db = ledger.open()
    _decision_with_calls(db, 2, 0.5, "opsT")
    monkeypatch.delenv("JADE_SOURCE")
    ops = evals.operations(ledger.open())
    assert ops["calls_per_decision"]["decisions"] == 0 and ops["cost_per_verified_decision"]["total_cost_usd"] == 0


def _waiting(db, entity, start_s, end_s):
    base = now()
    db.event(entity, "escalate", prev="EVALUATING", next="WAITING_HUMAN", actor="runtime")
    if end_s is not None:
        db.event(entity, "human_accept", prev="WAITING_HUMAN", next="ACCEPTED", actor="human")
    rows = db.con.execute("SELECT id FROM events WHERE entity_id=? ORDER BY id", (entity,)).fetchall()
    db.con.execute("UPDATE events SET ts=? WHERE id=?", (ts(base - timedelta(seconds=start_s)), rows[0]["id"]))
    if end_s is not None:
        db.con.execute("UPDATE events SET ts=? WHERE id=?", (ts(base - timedelta(seconds=start_s - end_s)), rows[1]["id"]))
    db.con.commit()


def test_operations_time_to_resolution_for_waiting_human():
    db = ledger.open()
    for i, (start, took) in enumerate([(5000, 60), (4000, 120), (3000, 600)]):
        _waiting(db, f"dec_w{i}", start, took)
    _waiting(db, "dec_still", 100, None)
    t = evals.operations(db)["time_to_resolution_waiting_human"]
    assert t["resolved"] == 3 and t["unresolved"] == 1
    assert t["p50_s"] == pytest.approx(120, abs=1) and t["max_s"] == pytest.approx(600, abs=1)
    assert t["oldest_unresolved_s"] == pytest.approx(100, abs=5)


def test_operations_time_to_resolution_empty():
    t = evals.operations(ledger.open())["time_to_resolution_waiting_human"]
    assert t == {"resolved": 0, "unresolved": 0, "p50_s": None, "p95_s": None, "max_s": None, "oldest_unresolved_s": None}


def test_report_carries_operations_and_choice():
    out = evals.report(ledger.open())
    assert {"operations", "choice"} <= set(out)


# ---- protected-split minimum -----------------------------------------------------------------------
def _policy():
    return paths.policy_dir()


def test_protected_minimum_is_the_design_target():
    import yaml

    pol = yaml.safe_load((ROOT / "policies" / "promotion.yaml").read_text())
    assert pol["protected_test"]["min_samples"] == 150


def _protected_res(n):
    side = {"false_accept_rate": 0.0, "brier": 0.01, "coverage": 0.9, "false_accept": 0}
    return {"protected": {"n": n, "champion": dict(side), "challenger": dict(side)}}


def test_protected_verdict_blocks_below_150_and_passes_at_150():
    import yaml

    pol = yaml.safe_load((ROOT / "policies" / "promotion.yaml").read_text())
    ok, why = improve.protected_verdict(_protected_res(149), pol)
    assert not ok and "fewer than 150" in why
    assert improve.protected_verdict(_protected_res(150), pol) == (True, "ok")


def test_docs_state_what_the_code_enforces():
    doc = (ROOT / "docs" / "04-calibration-and-evaluation.md").read_text()
    assert "protected_test.min_samples: 150" in doc
    assert "Designed, not built (W-52)" not in doc


def test_the_protected_check_of_a_template_reads_as_many_rows_as_the_policy_demands():
    """A cap below `protected_test.min_samples` would make a template candidate unpromotable whatever the ledger holds."""
    import inspect

    assert inspect.signature(improve.protected_check).parameters["limit"].default is None
    assert improve._protected_row_limit(None) == 150 and improve._protected_row_limit(40) == 40


@pytest.mark.parametrize("body", ["{bad\n", '{"claim": "x"}\n', '{"claim": "x", "should_route": "NOPE"}\n', "[1]\n"])
def test_eval_routing_on_a_malformed_case_file_is_a_clean_error_not_a_traceback(tmp_path, body):
    f = tmp_path / "c.jsonl"
    f.write_text(body)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = cli.main(["eval", "routing", "--cases", str(f)])
    assert rc == 1 and "not a valid routing case file" in err.getvalue()
