"""Small statistics helpers for the decision benchmark (V6-20, docs/14-v6/10 sections 9 and 15). Plain Python, no new dependency.

Each helper is a short, exact formula and is tested against a known table (tests/test_eval_stats_v620.py, gate G6). What `jade/evals.py` already
has is imported, not written again: `wilson` (interval for a share), `holm_adjust` (many metrics at once) and `paired_bootstrap`.

    exact_zero_bound(n)         upper limit of the failure rate after n tries with no failure (the rule of three is slightly loose)
    cohens_kappa(a, b)          agreement of two raters, beyond chance
    rank_interval(n, q)         which sorted values bound the q-quantile, with no assumption about the data
    design_effect(m, icc)       how much calls of one task count as less than independent calls
    effective_n(n, m, icc)      n divided by the design effect
    fixed_sequence(pvals)       tests in a fixed order, strict to loose; stops at the first one that does not pass

Nothing here reads a file, a clock or a random source, so a run repeats byte for byte.
"""
from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jade.evals import holm_adjust, paired_bootstrap, wilson  # noqa: E402,F401 - re-exported: one import for the benchmark code

ALPHA = 0.05


def exact_zero_bound(n: int, alpha: float = ALPHA) -> float:
    """One-sided (1 - alpha) upper limit of a failure rate after `n` independent tries with zero failures: the p with (1 - p) ** n == alpha, that
    is 1 - alpha ** (1 / n). At n=59 it is 4.95%; the rule of three (3 / n) says 5.08%. With n=0 nothing is known, so it is 1.0."""
    if n < 0 or not 0 < alpha < 1:
        raise ValueError("n must not be negative and alpha must be between 0 and 1")
    return 1.0 if n == 0 else 1.0 - alpha ** (1.0 / n)


def cohens_kappa(a: Sequence, b: Sequence) -> dict:
    """Cohen's kappa of two raters over the same items: (po - pe) / (1 - pe), with po the share they agree on and pe the share they would agree on by chance
    from their own label shares. Both are returned, because a percent alone misleads (an always-yes rater agrees 93% of the time with a rater who says
    yes 93% of the time). When both raters used one single label, pe is 1 and kappa is undefined: it is returned as None, never as 1."""
    if len(a) != len(b) or not a:
        raise ValueError("both raters must label the same, non-zero number of items")
    n = len(a)
    labels = sorted({*a, *b}, key=str)
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    pe = sum((sum(1 for x in a if x == k) / n) * (sum(1 for y in b if y == k) / n) for k in labels)
    kappa = None if pe >= 1.0 else (po - pe) / (1.0 - pe)
    return {"n": n, "agreement": po, "chance": pe, "kappa": kappa, "labels": labels}


def confusion(truth: Sequence, got: Sequence, rows: Sequence | None = None, cols: Sequence | None = None) -> dict:
    """Counts of (truth, got) pairs as {truth: {got: count}}, with every row and column named in `rows` and `cols` present (zeros kept), then any other label."""
    if len(truth) != len(got):
        raise ValueError("truth and got must have the same length")
    rows = list(rows or []) + sorted({t for t in truth if t not in (rows or [])}, key=str)
    cols = list(cols or []) + sorted({g for g in got if g not in (cols or [])}, key=str)
    out = {r: dict.fromkeys(cols, 0) for r in rows}
    for t, g in zip(truth, got, strict=True):
        out[t][g] += 1
    return out


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), summed term by term (n is a count of rows, so small)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1)))


def rank_interval(n: int, q: float = 0.5, alpha: float = ALPHA) -> dict:
    """The two ranks (1 = smallest) of a sorted sample of `n` values that bracket the true q-quantile with at least 1 - alpha coverage, whatever the
    distribution. `lower` is the largest rank l with P(Binomial(n, q) < l) <= alpha / 2; `upper` is the smallest rank u with
    P(Binomial(n, q) <= u - 1) >= 1 - alpha / 2. A rank of None means n is too small: the interval would reach past the smallest or largest value, so
    the sample cannot give that side (a p95 of 10 values has no upper rank). `coverage` is the true probability the pair holds."""
    if n < 1 or not 0 < q < 1 or not 0 < alpha < 1:
        raise ValueError("n must be at least 1, q and alpha must be between 0 and 1")
    lower = next((r for r in range(n, 0, -1) if _binom_cdf(r - 1, n, q) <= alpha / 2), None)
    upper = next((r for r in range(1, n + 1) if _binom_cdf(r - 1, n, q) >= 1 - alpha / 2), None)
    cover = None if lower is None or upper is None else _binom_cdf(upper - 1, n, q) - _binom_cdf(lower - 1, n, q)
    return {"n": n, "q": q, "lower": lower, "upper": upper, "coverage": cover}


def quantile_interval(values: Sequence[float], q: float = 0.5, alpha: float = ALPHA) -> dict:
    """`rank_interval` applied to the data: the sample q-quantile (the value at rank ceil(q * n)) and the interval values, or None where the sample is too small."""
    xs = sorted(values)
    if not xs:
        raise ValueError("no values")
    r = rank_interval(len(xs), q, alpha)
    return {"n": len(xs), "q": q, "value": xs[max(1, math.ceil(q * len(xs))) - 1],
            "low": None if r["lower"] is None else xs[r["lower"] - 1], "high": None if r["upper"] is None else xs[r["upper"] - 1], "coverage": r["coverage"]}


def design_effect(m: float, icc: float) -> float:
    """1 + (m - 1) * icc: the factor by which m calls from one task (intra-class correlation icc) shrink the information in the sample."""
    if m < 1 or not 0 <= icc <= 1:
        raise ValueError("m must be at least 1 and icc between 0 and 1")
    return 1.0 + (m - 1.0) * icc


def effective_n(n: int, m: float, icc: float) -> float:
    """n calls, m per task, as that many independent calls: 600 calls at 20 per task act like 207 at icc 0.1 and 90 at icc 0.3."""
    return n / design_effect(m, icc)


def fixed_sequence(pvals: Sequence[tuple[str, float]], alpha: float = ALPHA) -> list[dict]:
    """Fixed-sequence testing: the hypotheses come in the order given (strict to loose, chosen before the run), each is tested at the full alpha, and the first
    one with p > alpha ends the sequence: every later one is `not tested`, not a miss. No correction is needed because the order is fixed and no
    threshold is chosen by looking at the data (a scan for the lowest passing threshold is a selection effect)."""
    out, stopped = [], False
    for name, p in pvals:
        if stopped:
            out.append({"name": name, "p": p, "result": "not tested"})
            continue
        ok = p <= alpha
        out.append({"name": name, "p": p, "result": "reject" if ok else "keep"})
        stopped = not ok
    return out


def known_tables() -> list[tuple[str, bool, str]]:
    """The checks of gate G6 as (name, ok, detail): every helper against a value taken from a published table or worked by hand. The report prints
    these and the test file asserts them, so a changed formula fails in both places."""
    close = lambda a, b, tol=1e-9: abs(a - b) <= tol  # noqa: E731
    kap = cohens_kappa(["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15, ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15)
    r10, r20 = rank_interval(10), rank_interval(20)
    rows = [
        ("exact_zero_bound(59) = 4.95%, below the rule of three 3/59 = 5.08%", close(exact_zero_bound(59), 1 - 0.05 ** (1 / 59)) and round(exact_zero_bound(59), 4) == 0.0495
         and 3 / 59 > exact_zero_bound(59), f"{exact_zero_bound(59):.5f}"),
        ("exact_zero_bound(0) = 1", exact_zero_bound(0) == 1.0, "1.0"),
        ("cohens_kappa of the 50-item two-rater table (20, 5, 10, 15) = 0.4", close(kap["kappa"], 0.4) and close(kap["agreement"], 0.7) and close(kap["chance"], 0.5),
         f"kappa {kap['kappa']:.4f}"),
        ("cohens_kappa of identical single-label raters is undefined", cohens_kappa(["a"] * 5, ["a"] * 5)["kappa"] is None, "None"),
        ("rank_interval(10, median) = ranks 2 and 9", (r10["lower"], r10["upper"]) == (2, 9) and close(r10["coverage"], 1 - 2 * 11 / 1024), f"{r10['lower']},{r10['upper']}"),
        ("rank_interval(20, median) = ranks 6 and 15", (r20["lower"], r20["upper"]) == (6, 15), f"{r20['lower']},{r20['upper']}"),
        ("rank_interval(10, p95) has no upper rank", rank_interval(10, 0.95)["upper"] is None, "None"),
        ("design_effect(20, 0.1) = 2.9 and 600 calls act like 207", close(design_effect(20, 0.1), 2.9) and round(effective_n(600, 20, 0.1)) == 207, "207"),
        ("effective_n(600, 20, 0.3) = 90", round(effective_n(600, 20, 0.3)) == 90, "90"),
        ("wilson(0, 59) upper limit is 6.1%", round(wilson(0, 59)[1], 3) == 0.061, f"{wilson(0, 59)[1]:.4f}"),
        ("fixed_sequence stops at the first miss", [r["result"] for r in fixed_sequence([("a", 0.01), ("b", 0.2), ("c", 0.001)])] == ["reject", "keep", "not tested"], "reject,keep,not tested"),
    ]
    return rows
