"""V6-20, gate G6: the helpers of evals/stats.py agree with known tables (published values or hand-worked ones), and a repeat gives the same value."""
import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _stats():
    spec = importlib.util.spec_from_file_location("stats_v620", ROOT / "evals" / "stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_known_table_of_the_module_holds():
    bad = [name for name, ok, _ in _stats().known_tables() if not ok]
    assert bad == []


@pytest.mark.parametrize(("n", "want"), [(59, 0.0495), (30, 0.0950), (100, 0.0295), (298, 0.0100)])
def test_exact_zero_failure_bound_against_known_values(n, want):
    # (1 - p) ** n = 0.05: n=59 gives 4.95%, n=30 gives 9.5%, n=298 gives 1% (the rule of three says 5.08%, 10%, 1.01%)
    st = _stats()
    assert round(st.exact_zero_bound(n), 4) == want
    assert st.exact_zero_bound(n) < 3 / n   # the rule of three is slightly loose


def test_exact_zero_failure_bound_edges():
    st = _stats()
    assert st.exact_zero_bound(0) == 1.0
    assert st.exact_zero_bound(59, alpha=0.01) > st.exact_zero_bound(59)   # a stricter level needs a wider bound
    with pytest.raises(ValueError):
        st.exact_zero_bound(-1)
    with pytest.raises(ValueError):
        st.exact_zero_bound(10, alpha=1.0)


def test_kappa_of_the_standard_two_by_two_table():
    # 50 items: both yes 20, rater 1 yes and rater 2 no 5, the reverse 10, both no 15: po = 0.7, pe = 0.5, kappa = 0.4
    st = _stats()
    a = ["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    k = st.cohens_kappa(a, b)
    assert (k["n"], round(k["agreement"], 6), round(k["chance"], 6), round(k["kappa"], 6)) == (50, 0.7, 0.5, 0.4)


def test_kappa_perfect_chance_and_undefined():
    st = _stats()
    assert st.cohens_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"])["kappa"] == 1.0
    assert st.cohens_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"])["kappa"] == 0.0   # agrees on half, as chance does
    assert st.cohens_kappa(["a", "b"], ["b", "a"])["kappa"] == -1.0
    assert st.cohens_kappa(["a"] * 4, ["a"] * 4)["kappa"] is None   # one label only: chance agreement is 1, kappa is undefined
    with pytest.raises(ValueError):
        st.cohens_kappa(["a"], ["a", "b"])
    with pytest.raises(ValueError):
        st.cohens_kappa([], [])


def test_percent_agreement_alone_misleads():
    # an always-yes rater against one who says yes 93 times in 100: 93% agree, and kappa is 0
    st = _stats()
    k = st.cohens_kappa(["y"] * 100, ["y"] * 93 + ["n"] * 7)
    assert round(k["agreement"], 2) == 0.93 and k["kappa"] == 0.0


@pytest.mark.parametrize(("n", "q", "lower", "upper"), [(10, 0.5, 2, 9), (20, 0.5, 6, 15), (50, 0.5, 18, 33), (100, 0.5, 40, 61)])
def test_rank_interval_of_the_median_against_the_binomial_table(n, q, lower, upper):
    r = _stats().rank_interval(n, q)
    assert (r["lower"], r["upper"]) == (lower, upper)
    assert r["coverage"] >= 0.95


def test_rank_interval_says_when_the_sample_is_too_small():
    st = _stats()
    assert st.rank_interval(10, 0.95)["upper"] is None and st.rank_interval(10, 0.95)["coverage"] is None   # a p95 of 10 values has no upper rank
    assert st.rank_interval(59, 0.95)["upper"] is None   # P(Binomial(59, .95) <= 58) is about 0.95, below the 0.975 needed
    assert st.rank_interval(200, 0.95)["upper"] is not None
    with pytest.raises(ValueError):
        st.rank_interval(0)


def test_quantile_interval_picks_the_ranked_values():
    st = _stats()
    q = st.quantile_interval(list(range(20, 0, -1)), 0.5)   # unsorted on purpose: 1..20
    assert (q["value"], q["low"], q["high"]) == (10, 6, 15)
    assert st.quantile_interval([5.0, 7.0], 0.5)["low"] is None   # two values bound nothing
    with pytest.raises(ValueError):
        st.quantile_interval([])


def test_design_effect_and_effective_n_against_the_plan_figures():
    st = _stats()
    assert math.isclose(st.design_effect(20, 0.1), 2.9) and math.isclose(st.design_effect(20, 0.3), 6.7)
    assert round(st.effective_n(600, 20, 0.1)) == 207 and round(st.effective_n(600, 20, 0.3)) == 90
    assert st.design_effect(1, 0.9) == 1.0 and st.design_effect(20, 0.0) == 1.0   # one call per task, or no correlation: nothing is lost
    with pytest.raises(ValueError):
        st.design_effect(0.5, 0.1)
    with pytest.raises(ValueError):
        st.design_effect(5, 1.5)


def test_fixed_sequence_stops_at_the_first_miss_and_does_not_test_later_ones():
    st = _stats()
    got = st.fixed_sequence([("strict", 0.01), ("middle", 0.04), ("loose", 0.30), ("looser", 0.001)])
    assert [(r["name"], r["result"]) for r in got] == [("strict", "reject"), ("middle", "reject"), ("loose", "keep"), ("looser", "not tested")]


def test_confusion_keeps_zero_cells_and_the_named_order():
    st = _stats()
    m = st.confusion(["allow", "block", "block"], ["allow", "block", "ask"], rows=["allow", "ask", "block"], cols=["allow", "ask", "block"])
    assert list(m) == ["allow", "ask", "block"]
    assert m["block"] == {"allow": 0, "ask": 1, "block": 1} and m["ask"] == {"allow": 0, "ask": 0, "block": 0}
    with pytest.raises(ValueError):
        st.confusion(["a"], [])


def test_the_jade_evals_helpers_are_the_same_objects():
    import jade.evals as je

    st = _stats()
    assert st.wilson is je.wilson and st.holm_adjust is je.holm_adjust and st.paired_bootstrap is je.paired_bootstrap


def test_a_seeded_helper_repeats_exactly():
    st = _stats()
    a, b = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1], [0, 0, 1, 0, 0, 1, 0, 1, 0, 1]
    assert st.paired_bootstrap(a, b) == st.paired_bootstrap(a, b)
    assert st.rank_interval(37, 0.9) == st.rank_interval(37, 0.9)
