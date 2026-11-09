"""Phase 3 acceptance (docs/06): splits, metrics against synthetic truth, protected set, labels, thresholds, budgets."""
import json
import random

import pytest

from jade import adapter, ask, cli, evals, ledger, thresholds


def test_split_no_session_leak():
    splits = evals.split([{"session": f"s{i % 120}", "x": i} for i in range(400)])
    seen = {}
    for name, rows in splits.items():
        for r in rows:
            assert seen.setdefault(r["session"], name) == name
    sizes = {k: len(v) for k, v in splits.items()}
    assert all(sizes[k] > 0 for k in sizes) and sizes["dev"] > sizes["protected"]


def test_split_is_stable_across_runs_and_roughly_50_20_15_15():
    rows = [{"session": f"session-{i}"} for i in range(4000)]
    a, b = evals.split(rows), evals.split(list(reversed(rows)))
    assert {k: sorted(r["session"] for r in v) for k, v in a.items()} == {k: sorted(r["session"] for r in v) for k, v in b.items()}
    share = {k: len(v) / 4000 for k, v in a.items()}
    assert abs(share["dev"] - 0.50) < 0.04 and abs(share["calibration"] - 0.20) < 0.04 and abs(share["protected"] - 0.15) < 0.04


def test_metrics_against_synthetic_truth():
    m = evals.metrics(preds=[0.9, 0.1, 0.9, 0.1], labels=[1, 0, 0, 1], accept=0.7, reject=0.3)
    assert m["false_accept"] == 1 and m["false_reject"] == 1 and 0 <= m["brier"] <= 1
    perfect = evals.metrics([1.0, 0.0, 1.0, 0.0], [1, 0, 1, 0])
    assert perfect["brier"] == 0 and perfect["false_accept"] == 0 and perfect["ece"] == 0 and perfect["coverage"] == 1
    worst = evals.metrics([1.0, 0.0], [0, 1])
    assert worst["brier"] == 1 and worst["false_accept_rate"] == 1


def test_metrics_calibration_on_a_calibrated_synthetic_population():
    rng = random.Random(3)
    preds = [rng.random() for _ in range(5000)]
    labels = [1 if rng.random() < p else 0 for p in preds]
    m = evals.metrics(preds, labels)
    assert m["ece"] < 0.03 and 0.15 < m["brier"] < 0.20
    over = evals.metrics([min(1.0, p + 0.25) for p in preds], labels)
    assert over["ece"] > m["ece"] + 0.1  # an over-confident model is visibly worse calibrated


def test_wilson_interval_known_values():
    lo, hi = evals.wilson(5, 100)
    assert 0.02 < lo < 0.03 and 0.10 < hi < 0.12
    assert evals.wilson(0, 0) == (0.0, 1.0) and evals.wilson(0, 50)[0] == 0.0


def test_paired_bootstrap_detects_a_real_difference_and_a_null():
    a, b = [1] * 40 + [0] * 160, [1] * 100 + [0] * 100
    d = evals.paired_bootstrap(a, b)
    assert d["diff"] < 0 and d["ci95"][1] < 0
    same = evals.paired_bootstrap(a, a)
    assert same["diff"] == 0 and same["ci95"][0] <= 0 <= same["ci95"][1]


def test_protected_set_unreadable_without_tty(tmp_path, agent_env):
    with pytest.raises(evals.ProtectedSetAccessDenied):
        evals.load_protected("verify_claim")
    with pytest.raises(evals.ProtectedSetAccessDenied):
        evals.load_protected("verify_claim", root=tmp_path, stdin_isatty=True)  # tty alone is not enough in an agent session


def test_protected_set_readable_by_a_human(tmp_path, human_env):
    (tmp_path / "verify_claim.jsonl").write_text(json.dumps({"p": 0.9, "label": 1}) + "\n")
    assert evals.load_protected("verify_claim", root=tmp_path, stdin_isatty=True) == [{"p": 0.9, "label": 1}]


def _answered(db, session, p, claim, decision=None):
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": p} for k in q})
    return ask.run(ask.verify_specs(companion=False), {"claim": claim, "evidence": "raw"}, provider=prov, session=session, db=db, decision_id=decision).primary.call_id


def test_label_join_and_heuristic_never_overrides_a_real_label():
    db = ledger.open()
    c = _answered(db, "s1", 0.9, "claim one")
    evals.set_label(db, c, 1, "adjudicated", "human checked")
    evals.set_label(db, c, 0, "heuristic", "later guess")
    row = db.one("SELECT label, source FROM labels WHERE call_id=?", (c,))
    assert (row.label, row.source) == (1, "adjudicated")
    evals.set_label(db, c, 0, "observed", "outcome says no")  # a stronger source may replace it
    assert db.one("SELECT label FROM labels WHERE call_id=?", (c,)).label == 0
    with pytest.raises(ValueError):
        evals.set_label(db, c, 1, "vibes")


def test_auto_label_from_outcomes_and_later_contradiction():
    db = ledger.open()
    from jade import decide

    d = decide.open_decision(db, "Report that the deploy worked", command=None)
    c = _answered(db, "sA", 0.9, "deploy worked", d["decision_id"])
    for ev in ("authorize", "execute"):
        decide.step(db, d["decision_id"], ev)
    decide.record_outcome(db, d["decision_id"], "observed", False, "health check failed")
    first = _answered(db, "sB", 0.9, "it is fixed")
    _answered_reject = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.05} for k in q})
    ask.run(ask.verify_specs(companion=False), {"claim": "it is fixed", "evidence": "new raw output shows failure"}, provider=_answered_reject, session="sB", db=db)
    n = evals.auto_label(db)
    assert n["observed"] == 1 and n["heuristic"] >= 1
    assert db.one("SELECT label, source FROM labels WHERE call_id=?", (c,)).source == "observed"
    assert db.one("SELECT label, source FROM labels WHERE call_id=?", (first,)).source == "heuristic"
    assert all(r["source"] != "heuristic" for r in evals.labeled(db))  # heuristic labels never fit anything


def test_adjudication_queue_is_capped_and_boundary_first():
    db = ledger.open()
    for i, p in enumerate([0.99, 0.71, 0.50, 0.69, 0.10, 0.72, 0.68, 0.73, 0.67, 0.74, 0.66, 0.75, 0.65]):
        _answered(db, f"s{i}", p, f"claim {i}")
    q = evals.adjudication_queue(db)
    assert len(q) == evals.ADJUDICATION_PER_WEEK and abs(q[0].p - 0.70) < 0.02
    for r in q[:4]:
        evals.set_label(db, r.call_id, 1, "adjudicated")
    assert len(evals.adjudication_queue(db)) == evals.ADJUDICATION_PER_WEEK - 4


def test_thresholds_adjust_only_after_200_labels_and_new_group_starts_at_defaults():
    rng = random.Random(1)
    rows = [{"p": rng.choice([0.55, 0.65, 0.75, 0.85, 0.95]), "label": 1} for _ in range(199)]
    assert evals.propose_threshold(rows) is None
    rows = [{"p": p, "label": 1 if p >= 0.6 else 0} for p in (rng.choice([0.4, 0.55, 0.65, 0.75, 0.85, 0.95]) for _ in range(600))]
    prop = evals.propose_threshold(rows)
    assert prop and prop["accept"] <= 0.70 and prop["upper95"] <= evals.FA_BUDGET
    g_old = thresholds.group_of("verify_claim", 1, "typesafe/jev-1.14-20261014")
    g_new = thresholds.group_of("verify_claim", 1, "typesafe/jev-1.15-20270101")
    assert g_old != g_new


def test_promoted_threshold_applies_to_its_own_group_only(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "calibration").mkdir(parents=True)
    g_old = thresholds.group_of("verify_claim", 1, "typesafe/jev-1.14-20261014")
    (home / "calibration" / "current.json").write_text(json.dumps({g_old: {"accept": 0.60}}))
    assert thresholds.judge({"type": "noul", "noul": 0.65}, "verify_claim", "low", 1, "typesafe/jev-1.14-20261014").verdict == "ACCEPT"
    assert thresholds.judge({"type": "noul", "noul": 0.65}, "verify_claim", "low", 1, "typesafe/jev-1.15-20270101").verdict == "ABSTAIN"
    assert thresholds.judge({"type": "noul", "noul": 0.65}, "verify_claim", "high", 1, "typesafe/jev-1.14-20261014").verdict == "ABSTAIN"  # risk only stricter
    (home / "calibration" / "current.json").unlink()  # rollback = delete the file
    assert thresholds.judge({"type": "noul", "noul": 0.65}, "verify_claim", "low", 1, "typesafe/jev-1.14-20261014").verdict == "ABSTAIN"


def test_choice_and_score_judging():
    assert thresholds.judge({"type": "choice", "choice": "a", "probabilities": {"a": 0.8, "b": 0.2}}, "strategy_choice").verdict == "ACCEPT"
    assert thresholds.judge({"type": "choice", "choice": "a", "probabilities": {"a": 0.5, "b": 0.5}}, "strategy_choice").verdict == "ABSTAIN"
    assert thresholds.judge({"type": "score", "score": 0.1}, "evidence_quality").exit_code == 0  # a score never gates


def test_conditions_report_and_eval_run_on_labeled_ledger(capsys):
    db = ledger.open()
    for i in range(30):
        c = _answered(db, f"s{i}", 0.9 if i % 3 else 0.4, f"claim {i}")
        evals.set_label(db, c, 0 if i % 5 == 0 else 1, "adjudicated")
    out = evals.conditions(db)
    assert out["n"] > 0 and out["unsafe_accept_rate"]["3_jev_thresholds"] <= out["unsafe_accept_rate"]["1_bare_jev"]
    assert "not measurable" in out["2_reasoning_only"]
    rep = evals.report(db)
    assert rep["groups"] and next(iter(rep["groups"].values()))["n"] > 0
    assert cli.main(["eval", "run"]) == 0 and cli.main(["eval", "conditions"]) == 0 and cli.main(["eval", "propose"]) == 0
    capsys.readouterr()


def test_ledger_stats_reproduces_the_baseline_quantities(capsys):
    db = ledger.open()
    for i in range(20):
        _answered(db, "s1" if i < 10 else f"s{i}", [0.9, 0.5, 0.2, 0.65][i % 4], f"claim {i % 5}")
    assert cli.main(["ledger", "stats", "--days", "14"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["calls"] == 20 and s["verify"]["n"] > 0 and 0 <= s["verify"]["share_below_0.70"] <= 1 and "p50" in s["latency_ms"]
    assert s["exit_codes"]["accept_0"] + s["exit_codes"]["abstain_3"] + s["exit_codes"]["reject_4"] >= 1 and s["repeat_share"] > 0


def test_purge_removes_state_text_but_keeps_metrics():
    from jade import ops

    db = ledger.open()
    c = _answered(db, "s", 0.9, "old claim")
    db.con.execute("UPDATE jev_calls SET ts='2000-01-01T00:00:00Z' WHERE call_id=?", (c,))
    fresh = _answered(db, "s2", 0.9, "new claim")
    assert ops.purge(db, 90) == 1
    old, new = db.get_call(c), db.get_call(fresh)
    assert old.state_text is None and old.question_text is None and old.p == 0.9 and old.exit_code == 0 and old.state_hash
    assert new.state_text is not None
