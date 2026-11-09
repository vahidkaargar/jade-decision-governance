"""Labels, splits and metrics (docs/04). Labels come from observed outcomes, user corrections or human
adjudication; heuristic labels are for monitoring only and never fit a threshold."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import statistics
from pathlib import Path

from . import ledger, paths
from .util import is_human_tty, language, now, parse_ts, ts  # noqa: F401 - re-exported

SPLITS = (("dev", 50), ("calibration", 70), ("validation", 85), ("protected", 100))
FIT_SOURCES = ("observed", "user_correction", "adjudicated")
MIN_LABELED = 200
FA_BUDGET = 0.05
ADJUDICATION_PER_WEEK = 10
ALPHA = 0.05   # family-wise level of the Holm correction over the paired metrics (policies/promotion.yaml offline_replay.alpha)


class ProtectedSetAccessDenied(PermissionError):
    """The protected test split is readable only by a human at a terminal (threat T10)."""


def bucket(session: str) -> str:
    h = int(hashlib.sha256(str(session).encode()).hexdigest()[:8], 16) % 100
    return next(name for name, upper in SPLITS if h < upper)


def split(rows: list[dict], key: str = "session") -> dict[str, list[dict]]:
    """Partition by a hash of the session id, so no session appears in two splits."""
    out: dict[str, list[dict]] = {name: [] for name, _ in SPLITS}
    for r in rows:
        out[bucket(r.get(key, ""))].append(r)
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def metrics(preds: list[float], labels: list[int], accept: float = 0.70, reject: float = 0.30, bins: int = 10) -> dict:
    """Decision quality and calibration for one group. `labels` are 1 when the claim really held."""
    n = len(preds)
    if n == 0 or n != len(labels):
        raise ValueError("preds and labels must be non-empty and the same length")
    acc = [(p, y) for p, y in zip(preds, labels, strict=True) if p >= accept]
    rej = [(p, y) for p, y in zip(preds, labels, strict=True) if p <= reject]
    fa, fr = sum(1 for _, y in acc if y == 0), sum(1 for _, y in rej if y == 1)
    eps = 1e-9
    brier = sum((p - y) ** 2 for p, y in zip(preds, labels, strict=True)) / n
    logloss = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in zip(preds, labels, strict=True)) / n
    ece, reliability = 0.0, []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        cell = [(p, y) for p, y in zip(preds, labels, strict=True) if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if cell:
            conf, freq = sum(p for p, _ in cell) / len(cell), sum(y for _, y in cell) / len(cell)
            ece += len(cell) / n * abs(conf - freq)
            reliability.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(cell), "mean_p": round(conf, 3), "observed": round(freq, 3)})
    lo_fa, hi_fa = wilson(fa, len(acc))
    return {"n": n, "accepted": len(acc), "rejected": len(rej), "abstained": n - len(acc) - len(rej),
            "false_accept": fa, "false_reject": fr, "false_accept_rate": fa / len(acc) if acc else 0.0,
            "false_accept_ci95": [round(lo_fa, 4), round(hi_fa, 4)], "false_reject_rate": fr / len(rej) if rej else 0.0,
            "coverage": (len(acc) + len(rej)) / n, "selective_risk": (fa + fr) / (len(acc) + len(rej)) if (acc or rej) else 0.0,
            "brier": brier, "log_loss": logloss, "ece": ece, "reliability": reliability}


def paired_bootstrap(a: list[int], b: list[int], rounds: int = 2000, seed: int = 7) -> dict:
    """95% interval for mean(a) - mean(b) over the same cases (paired resampling)."""
    if len(a) != len(b) or not a:
        raise ValueError("paired samples must be the same non-zero length")
    rng, n, diffs = random.Random(seed), len(a), []  # noqa: S311 - statistics, not security
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        diffs.append(sum(a[i] - b[i] for i in idx) / n)
    diffs.sort()
    # two-sided bootstrap p for "no difference": the share of resampled differences on each side of zero, counting the observed one (so never 0)
    below, above = (sum(1 for d in diffs if d <= 0) + 1) / (rounds + 1), (sum(1 for d in diffs if d >= 0) + 1) / (rounds + 1)
    return {"diff": sum(x - y for x, y in zip(a, b, strict=True)) / n, "ci95": [diffs[int(0.025 * rounds)], diffs[int(0.975 * rounds) - 1]], "n": n,
            "p": min(1.0, 2 * min(below, above))}


def holm_adjust(pvals: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values: a hypothesis is rejected at family-wise level alpha exactly when its adjusted p is at or below alpha
    (the same rule as `improve.holm_rejected`). Without it, the smallest of several metrics compared at once looks significant by chance."""
    m, out, run = len(pvals), {}, 0.0
    for i, (name, p) in enumerate(sorted(pvals.items(), key=lambda kv: kv[1])):
        run = max(run, min(1.0, (m - i) * p))
        out[name] = run
    return {k: out[k] for k in pvals}


def risk_coverage_curve(preds: list[float], labels: list[int], points: int = 10) -> list[dict]:
    """Selective risk against coverage. A row's confidence is max(p, 1-p) and its call is p >= 0.5; the curve keeps the most confident
    `coverage` share of the rows and reports the error rate among them. A model whose confidence means something has a curve that rises
    with coverage; a flat one says abstaining on the least confident rows buys nothing."""
    n = len(preds)
    if n == 0 or n != len(labels):
        raise ValueError("preds and labels must be non-empty and the same length")
    rows = sorted(((max(p, 1 - p), (p >= 0.5) != bool(y)) for p, y in zip(preds, labels, strict=True)), key=lambda r: -r[0])
    out, seen = [], set()
    for i in range(1, points + 1):
        k = max(1, math.ceil(i * n / points))
        if k in seen:
            continue
        seen.add(k)
        conf = rows[k - 1][0]
        # Rows with the same confidence have no order among themselves (an always-abstaining judge gives every row 0.5), so a cut inside such a
        # group counts its share at the group's error rate, not at whichever rows happen to come first in the ledger.
        above, tied = [bad for c, bad in rows if c > conf], [bad for c, bad in rows if c == conf]
        errors = sum(above) + (k - len(above)) * sum(tied) / len(tied)
        out.append({"coverage": round(i / points, 4), "n": k, "min_confidence": round(conf, 4), "selective_risk": errors / k})
    return out


def _ece(pairs: list[tuple[float, int]], bins: int = 10) -> float:
    cells: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in pairs:
        cells[min(bins - 1, int(p * bins))].append((p, y))
    return sum(len(c) / len(pairs) * abs(sum(p for p, _ in c) / len(c) - sum(y for _, y in c) / len(c)) for c in cells if c)


def ece_bootstrap(preds: list[float], labels: list[int], rounds: int = 500, seed: int = 7, bins: int = 10) -> dict:
    """ECE with a percentile bootstrap 95% interval over the rows. ECE is biased upward on a small sample, so the interval can sit above the
    point estimate there: read its width, and compare two models by whether their intervals overlap."""
    n = len(preds)
    if n == 0 or n != len(labels):
        raise ValueError("preds and labels must be non-empty and the same length")
    pairs = list(zip(preds, labels, strict=True))
    rng, vals = random.Random(seed), []  # noqa: S311 - statistics, not security
    for _ in range(rounds):
        vals.append(_ece([pairs[rng.randrange(n)] for _ in range(n)], bins))
    vals.sort()
    return {"ece": _ece(pairs, bins), "ci95": [vals[int(0.025 * rounds)], vals[max(0, int(0.975 * rounds) - 1)]], "n": n, "rounds": rounds}


def _margin(probs) -> float | None:
    if not isinstance(probs, dict) or len(probs) < 2 or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in probs.values()):
        return None
    top = sorted(probs.values(), reverse=True)
    return float(top[0] - top[1])


def choice_metrics(rows: list[dict]) -> dict:
    """Choice questions. Each row is `{probabilities: {option: p}, label}` where `label` is 1 when the top option was the right one. Top-1
    accuracy (with a Wilson interval) and the margin between the two most probable options; accuracy is also split at the median margin,
    because a narrow margin that is no less accurate than a wide one means the probabilities carry no information about being right."""
    kept = [(m, int(bool(r["label"]))) for r in rows if isinstance(r, dict) and "label" in r for m in [_margin(r.get("probabilities"))] if m is not None]
    if not kept:
        return {"n": 0}
    n, hits = len(kept), sum(y for _, y in kept)
    margins = [m for m, _ in kept]
    med = statistics.median(margins)
    narrow, wide = [y for m, y in kept if m <= med], [y for m, y in kept if m > med]
    lo, hi = wilson(hits, n)
    return {"n": n, "top1_accuracy": hits / n, "top1_ci95": [round(lo, 4), round(hi, 4)],
            "margin": {"min": min(margins), "median": med, "mean": sum(margins) / n, "max": max(margins)},
            "by_margin": {"narrow": {"n": len(narrow), "accuracy": sum(narrow) / len(narrow) if narrow else None},
                          "wide": {"n": len(wide), "accuracy": sum(wide) / len(wide) if wide else None}}}


def routing_confusion(pairs: list[tuple[str, str]]) -> dict:
    """Confusion matrix of the router: `(expected, got)` pairs over `ROUTES`. `jev_false_positive_rate` is the share of FAST cases the router
    sent to Jev (a call where FAST sufficed, docs/04 section 6); `jev_false_negative_rate` the share of JEV cases it did not send there."""
    matrix: dict[str, dict[str, int]] = {}
    for want, got in pairs:
        if want not in ROUTES or got not in ROUTES:
            raise ValueError(f"unknown route in ({want!r}, {got!r}); routes are {ROUTES}")
        matrix.setdefault(want, {}).setdefault(got, 0)
        matrix[want][got] += 1
    n = len(pairs)
    fast, jev = sum(matrix.get("FAST", {}).values()), sum(matrix.get("JEV", {}).values())
    return {"n": n, "matrix": matrix, "accuracy": sum(row.get(w, 0) for w, row in matrix.items()) / n if n else None,
            "recall": {w: row.get(w, 0) / sum(row.values()) for w, row in matrix.items()},
            "jev_false_positive_rate": matrix.get("FAST", {}).get("JEV", 0) / fast if fast else None,
            "jev_false_negative_rate": (jev - matrix.get("JEV", {}).get("JEV", 0)) / jev if jev else None}


def routing_confusion_from_cases(cases: list[dict]) -> dict:
    """`routing_confusion` over a routing case set (`evals/routing`): the hand-labelled `should_route` against what `route.preflight` gives on
    the case's inputs. No provider is involved. Cases with a recorded `known_gap` show up off the diagonal, by design."""
    from . import route

    pairs = []
    for c in cases:
        got = route.preflight(claim=c.get("claim", ""), evidence=c.get("evidence", ""), options=c.get("options"), command=c.get("command"), risk=c.get("risk", "low"),
                              about_to_claim_done=c.get("about_to_claim_done", False), deterministic=c.get("deterministic", False)).route
        pairs.append((c["should_route"], got))
    return routing_confusion(pairs)


def load_protected(family: str, root: Path | None = None, stdin_isatty: bool | None = None) -> list[dict]:
    if not is_human_tty(stdin_isatty):
        raise ProtectedSetAccessDenied("the protected split is read only by `jade eval run --protected` from a human terminal")
    f = (root or paths.home() / "evals" / "protected") / f"{family}.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


# ---- labels ----------------------------------------------------------------------------------------
def set_label(db: ledger.Ledger, call_id: str, label: int, source: str, note: str = "") -> None:
    if source not in (*FIT_SOURCES, "heuristic"):
        raise ValueError(f"unknown label source {source}")
    with db.tx() as con:
        con.execute("INSERT INTO labels(call_id, label, source, note, ts) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(call_id) DO UPDATE SET label=excluded.label, source=excluded.source, note=excluded.note, ts=excluded.ts "
                    "WHERE labels.source='heuristic' OR excluded.source<>'heuristic'", (call_id, int(bool(label)), source, note, ts()))


def auto_label(db: ledger.Ledger) -> dict:
    """Join outcomes to the call that gated the decision (source `observed`), and mark answers that were later contradicted in the
    same session (source `heuristic`). The label says whether *that call's own claim* held, so only the call a decision acted on is
    labelled: the primary verify call named last in the decision's `evaluation_refs`, and only when it was accepted (exit 0). A
    companion question, an earlier rejected call on another claim and a call a human overrode stay unlabelled (the adjudication queue)."""
    n = {"observed": 0, "heuristic": 0}
    for d in db.all("SELECT d.decision_id, d.status, d.doc_json FROM decisions d WHERE d.status='FAILED' OR (d.status='SUCCEEDED' AND EXISTS (SELECT 1 FROM outcomes o "
                    "WHERE o.decision_id=d.decision_id AND json_extract(o.doc_json,'$.observation_type')='observed' AND json_extract(o.doc_json,'$.success')=1))"):
        refs = json.loads(d.doc_json).get("evaluation_refs") or []
        gate = db.get_call(refs[-1]) if refs else None
        if gate is None or gate.p is None or gate.primitive != "noul" or gate.task_family != "verify_claim" or gate.exit_code != 0:
            continue
        target = gate.dedup_of or gate.call_id   # a cached repeat labels the call that produced the answer
        if db.one("SELECT 1 AS x FROM labels WHERE call_id=? AND source<>'heuristic'", (target,)):
            continue
        set_label(db, target, 1 if d.status == "SUCCEEDED" else 0, "observed", "outcome join")
        n["observed"] += 1
    for r in db.all("SELECT a.call_id FROM jev_calls a JOIN jev_calls b ON b.session_id=a.session_id AND b.claim_hash=a.claim_hash AND b.rowid>a.rowid "
                    "WHERE a.exit_code=0 AND b.exit_code=4 AND a.primitive='noul' AND " + ledger.live("a") + " AND a.call_id NOT IN (SELECT call_id FROM labels)"):
        set_label(db, r.call_id, 0, "heuristic", "accepted, then rejected with new evidence in the same session")
        n["heuristic"] += 1
    return n


def adjudication_queue(db: ledger.Ledger, limit: int = ADJUDICATION_PER_WEEK) -> list:
    """Unlabeled answers nearest the decision boundary, capped per week so the human is not flooded (threat T14)."""
    done = db.one("SELECT COUNT(*) AS n FROM labels WHERE source='adjudicated' AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-7 days')").n
    room = max(0, limit - done)
    return db.all("SELECT call_id, p, verdict, question_id, substr(COALESCE(question_text,''),1,160) AS q, ts FROM jev_calls "
                  "WHERE p IS NOT NULL AND primitive='noul' AND dedup_of IS NULL AND task_family='verify_claim' AND " + ledger.live() + " "
                  "AND call_id NOT IN (SELECT call_id FROM labels) ORDER BY ABS(p-0.70) LIMIT ?", (room,))


def distinct_claims(rows: list[dict]) -> int:
    """How many independent pieces of evidence the rows hold: one per (claim, state) pair. A repeat of the same question over the same
    state is the same evidence (docs/04 section 3); rows with no such key each count once."""
    return len({(r["claim_hash"], r["state_hash"]) if r.get("claim_hash") and r.get("state_hash") else r.get("call_id", id(r)) for r in rows})


def labeled(db: ledger.Ledger, fit_only: bool = True, include_protected: bool = False) -> list[dict]:
    """Labeled answers, one per independent (claim, state, calibration group). Cached repeats (`dedup_of`) and answers with no model
    string (no calibration group) are not evidence; neither is a second row for a (claim, state) the group already holds."""
    src = "AND l.source<>'heuristic'" if fit_only else ""
    rows = db.all("SELECT c.call_id, c.p, c.task_family, c.question_id, c.question_version, c.resolved_model, c.session_id, c.risk, c.language, c.claim_hash, c.state_hash, "
                  "l.label, l.source FROM jev_calls c JOIN labels l ON l.call_id=c.call_id WHERE c.p IS NOT NULL AND c.primitive='noul' "
                  f"AND {ledger.live('c')} AND c.dedup_of IS NULL AND c.resolved_model IS NOT NULL {src} ORDER BY c.rowid")  # noqa: S608 - constant fragment
    seen, out = set(), []
    for r in rows:
        key = (r.claim_hash, r.state_hash, r.task_family, r.question_version, r.resolved_model) if r.claim_hash else r.call_id
        if key in seen:
            continue
        seen.add(key)
        out.append(vars(r) | {"session": r.session_id or "", "split": bucket(r.session_id or ""), "language": r.language or "unknown"})
    return out if include_protected else [r for r in out if r["split"] != "protected"]


def report(db: ledger.Ledger, include_protected: bool = False) -> dict:
    """Metrics per calibration group at the current default bands, plus what is still missing."""
    from . import thresholds

    groups: dict[str, list[dict]] = {}
    for r in labeled(db, include_protected=include_protected):
        groups.setdefault(thresholds.group_of(r["task_family"], r["question_version"], r["resolved_model"]), []).append(r)
    out = {"generated_at": ts(), "min_labeled_for_adjustment": MIN_LABELED, "groups": {}}
    for g, rows in sorted(groups.items()):
        fam = rows[0]["task_family"]
        accept, reject = thresholds.bands("noul", fam, "low", g)
        m = metrics([r["p"] for r in rows], [r["label"] for r in rows], accept, reject)
        m["thresholds"] = {"accept": accept, "reject": reject}
        preds, labs = [r["p"] for r in rows], [r["label"] for r in rows]
        m["risk_coverage"] = risk_coverage_curve(preds, labs)
        m["ece_ci95"] = [round(x, 4) for x in ece_bootstrap(preds, labs, rounds=max(100, min(500, 2_000_000 // len(rows))))["ci95"]]
        hi_rows = [r for r in rows if r.get("risk") in ("high", "critical")]
        if hi_rows:   # docs/04 section 6: unsafe accept must be 0 at risk >= high, so this slice is read at the (stricter) high band
            hi_accept, hi_reject = thresholds.bands("noul", fam, "high", g)
            m["risk_high"] = metrics([r["p"] for r in hi_rows], [r["label"] for r in hi_rows], hi_accept, hi_reject) | {"thresholds": {"accept": hi_accept, "reject": hi_reject}}
        else:
            m["risk_high"] = None
        m["distinct_claims"] = distinct_claims(rows)
        m["eligible_for_adjustment"] = m["distinct_claims"] >= MIN_LABELED
        out["groups"][g] = m
    out["note"] = "no labeled rows yet" if not groups else ""
    out["choice"] = choice_metrics(choice_rows(db, include_protected))
    out["operations"] = operations(db)
    by_lang: dict[str, list[dict]] = {}
    for rows in groups.values():
        for r in rows:
            by_lang.setdefault(r["language"], []).append(r)
    out["by_language"] = {lang: {k: m[k] for k in ("n", "false_accept_rate", "coverage", "brier")}
                          for lang, rows in sorted(by_lang.items()) for m in [metrics([r["p"] for r in rows], [r["label"] for r in rows])]}
    return out


def choice_rows(db: ledger.Ledger, include_protected: bool = False) -> list[dict]:
    """Labelled choice answers: label 1 means the top option was right. Cached repeats and heuristic labels are left out, as in `labeled`."""
    out = []
    for r in db.all("SELECT c.call_id, c.answers_json, c.session_id, l.label FROM jev_calls c JOIN labels l ON l.call_id=c.call_id WHERE c.primitive='choice' "
                    f"AND c.dedup_of IS NULL AND c.answers_json IS NOT NULL AND l.source<>'heuristic' AND {ledger.live('c')} ORDER BY c.rowid"):  # noqa: S608 - constant fragment
        try:
            probs = json.loads(r.answers_json).get("probabilities")
        except (ValueError, AttributeError):
            continue
        if include_protected or bucket(r.session_id or "") != "protected":
            out.append({"call_id": r.call_id, "probabilities": probs, "label": r.label})
    return out


def _nearest_rank(sorted_vals: list[float], q: float) -> float | None:
    return sorted_vals[max(0, math.ceil(q * len(sorted_vals)) - 1)] if sorted_vals else None


def operations(db: ledger.Ledger) -> dict:
    """What the ledger says about effort: provider calls per decision, spend per verified decision (an observed success) and how long a
    decision waits for a human. Rows a verification run wrote (`source=test`) are left out."""
    per = db.all("SELECT d.decision_id, (SELECT COUNT(*) FROM jev_calls c WHERE c.decision_id=d.decision_id AND c.dedup_of IS NULL AND " + ledger.live("c") + ") AS n "
                 "FROM decisions d WHERE " + ledger.live("d"))
    counts = [r.n for r in per]
    called = [n for n in counts if n]
    cost = db.one("SELECT COALESCE(SUM(c.cost_usd),0) AS usd FROM jev_calls c JOIN decisions d ON d.decision_id=c.decision_id WHERE " + ledger.live("c") + " AND " + ledger.live("d")).usd
    # a decision a gate permit settled (a shell command a human approved) reached SUCCEEDED without any verification: it is not a verified decision
    verified = db.one("SELECT COUNT(*) AS n FROM decisions d WHERE d.status='SUCCEEDED' AND " + ledger.live("d") + " AND EXISTS (SELECT 1 FROM jev_calls c WHERE c.decision_id=d.decision_id "
                      "AND c.dedup_of IS NULL AND " + ledger.live("c") + ")").n
    waits, open_at = [], {}
    for e in db.all("SELECT entity_id, prev_state, next_state, ts FROM events WHERE (next_state='WAITING_HUMAN' OR prev_state='WAITING_HUMAN') AND " + ledger.live() + " ORDER BY id"):
        if e.prev_state == "WAITING_HUMAN" and e.entity_id in open_at:   # any way out of the wait ends it: an answer, a deadline or a cancel
            waits.append((parse_ts(e.ts) - open_at.pop(e.entity_id)).total_seconds())
        if e.next_state == "WAITING_HUMAN":
            open_at[e.entity_id] = parse_ts(e.ts)
    waits.sort()
    return {"calls_per_decision": {"decisions": len(counts), "calls": sum(counts), "mean": sum(counts) / len(counts) if counts else None,
                                   "mean_of_called": sum(called) / len(called) if called else None, "max": max(counts) if counts else None},
            "cost_per_verified_decision": {"verified_decisions": verified, "total_cost_usd": round(cost, 6), "usd": round(cost / verified, 6) if verified else None},
            "time_to_resolution_waiting_human": {"resolved": len(waits), "unresolved": len(open_at), "p50_s": _nearest_rank(waits, 0.5), "p95_s": _nearest_rank(waits, 0.95),
                                                 "max_s": waits[-1] if waits else None,
                                                 "oldest_unresolved_s": max((now() - t).total_seconds() for t in open_at.values()) if open_at else None}}


def propose_threshold(rows: list[dict], budget: float = FA_BUDGET) -> dict | None:
    """Lowest accept threshold whose Wilson 95% upper bound on false accepts meets the budget (ADR-07).
    Returns None below 200 fit-quality labels on distinct (claim, state) pairs: defaults stay."""
    if distinct_claims(rows) < MIN_LABELED:
        return None
    for t in [x / 100 for x in range(50, 96)]:
        acc = [r for r in rows if r["p"] >= t]
        if len(acc) < 30:
            break
        fa = sum(1 for r in acc if r["label"] == 0)
        if wilson(fa, len(acc))[1] <= budget:
            return {"accept": t, "accepted": len(acc), "false_accept": fa, "upper95": round(wilson(fa, len(acc))[1], 4)}
    return None


def conditions(db: ledger.Ledger) -> dict:
    """docs/04 section 8. (1) bare jev: every answer treated as a yes; (3) Jev with thresholds. (2) reasoning-only and
    (4) cascade need DEEP verdict labels that the ledger does not hold yet, so they are reported as not measurable."""
    rows = labeled(db)
    if not rows:
        return {"n": 0, "note": "no labeled rows; conditions cannot be compared yet"}
    from . import thresholds

    accepted3 = [r["p"] >= thresholds.bands("noul", r["task_family"], r.get("risk") or "low")[0] for r in rows]
    n = len(rows)
    c1 = [1 if r["label"] == 0 else 0 for r in rows]   # (1) bare jev takes every answer as a yes, so every false claim is an unsafe accept
    c3 = [1 if (acc and r["label"] == 0) else 0 for acc, r in zip(accepted3, rows, strict=True)]
    safe3 = [1 if acc and r["label"] == 1 else 0 for acc, r in zip(accepted3, rows, strict=True)]
    # Against bare jev, unsafe_accept, safe_accept and abstain can only move one way (bare jev accepts everything), so a judge that is noise or
    # that abstains on everything still "wins" them: they are reported as differences and never tested. The tested family holds comparisons that
    # can go either way: the accept/not-accept call being right (bare jev is right exactly on the true claims), and each accept metric against
    # accepting the same share of rows at random (an uninformative judge, or one that abstains on everything, has a difference of zero there).
    rate = sum(accepted3) / n
    right3 = [1 if acc == (r["label"] == 1) else 0 for acc, r in zip(accepted3, rows, strict=True)]
    right1 = [1 - x for x in c1]
    descriptive = {"unsafe_accept": paired_bootstrap(c3, c1), "safe_accept": paired_bootstrap(safe3, [1 if r["label"] == 1 else 0 for r in rows]),
                   "abstain": paired_bootstrap([0 if acc else 1 for acc in accepted3], [0] * n)}
    per = {"correct_decision": (right3, right1, 1),
           "unsafe_accept_vs_chance": (c3, [rate * x for x in c1], -1),
           "safe_accept_vs_chance": (safe3, [rate * (1 - x) for x in c1], 1)}
    cmp_ = {k: paired_bootstrap(a, b) for k, (a, b, _) in per.items()}
    holm = holm_adjust({k: v["p"] for k, v in cmp_.items()})
    paired = {k: v | {"holm_p": holm[k], "significant_after_holm": holm[k] <= ALPHA,
                      "win": holm[k] <= ALPHA and v["diff"] * per[k][2] > 0} for k, v in cmp_.items()}
    return {"n": n, "unsafe_accept_rate": {"1_bare_jev": sum(c1) / n, "3_jev_thresholds": sum(c3) / n},
            "paired_3_minus_1": descriptive["unsafe_accept"], "paired_descriptive": descriptive, "paired_metrics": paired,
            "multiple_testing": "holm", "alpha": ALPHA,
            "2_reasoning_only": "not measurable: no DEEP verdict labels", "4_cascade": "not measurable: no DEEP verdict labels"}


# ---- hand-written case sets (W-49, W-14) -------------------------------------------------------------
# evals/routing, evals/judgment and evals/calibration hold small hand-written JSONL sets with a fixed schema. They are synthetic: they can
# prove the schema, the split discipline, the offline paths and the language slices; they cannot calibrate a threshold or stand in for the
# labelled ledger rows that W-04, W-27 and W-52 need (docs/04 section 5).
ROUTES = ("FAST", "JEV", "DEEP", "ESCALATE", "NEEDS_EVIDENCE", "NEEDS_REVISION")
CASE_LANGS = ("fa", "en", "mixed")
CASE_SETS = {"routing": ("RT", "route", ("dev", "validation")), "judgment": ("JG", "judge_input", ("dev", "validation")),
             "calibration": ("CL", "judge_input", ("calibration",))}
_TEXT_FIELDS = ("claim", "evidence", "command", "context", "text", "template", "options", "content_parts")
_SAFE_HOST = re.compile(r"(?:^|\.)(?:test|example|invalid|localhost|local)$|^(?:127\.\d+\.\d+\.\d+|0\.0\.0\.0)$|^(?:192\.0\.2|198\.51\.100|203\.0\.113)\.\d+$", re.I)
_URL_HOST = re.compile(r"[a-z][a-z0-9+.-]*://(?:[^/\s@'\"]*@)?(\[[0-9a-f:]+\]|[^/\s:'\"?#]+)", re.I)
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")


def case_parts(c: dict) -> list[str]:
    """The text fields of a case, one string each, with a secret placeholder (`@@`) removed. `ask.run` sends them as separate state fields,
    so a redaction check must look at them one at a time (a claim ending in `pass` and an evidence banner starting `===` are not `pass = ===`)."""
    parts: list[str] = []
    for k in _TEXT_FIELDS:
        v = c.get(k)
        parts += [str(x) for x in v] if isinstance(v, list) else [str(v)] if v else []
    return [p.replace("@@", "") for p in parts]


def case_text(c: dict) -> str:
    """All the text of a case, in field order. What `lang` describes."""
    return "\n".join(case_parts(c))


def case_lang(c: dict) -> str:
    """A case's slice: its explicit `lang` tag, else the script-based tag of its text (`fa`, `en`, `mixed` or `unknown`)."""
    return c.get("lang") or language(case_text(c))


def text_problems(text: str) -> list[str]:
    """What must never be committed in a case file: a secret-shaped string, a URL whose host is not reserved (.test, .example, .invalid,
    localhost, loopback) and an IPv4 address outside the documentation ranges."""
    from . import redact

    out = []
    if redact.redact(text, secrets_only=True)[1] or redact.residual(text):
        out.append("secret-shaped text")
    out += [f"host {m.group(1)!r} is not a reserved name" for m in _URL_HOST.finditer(text) if not _SAFE_HOST.search(m.group(1).strip("[]"))]
    out += [f"host address {m.group(1)} is not in a documentation range" for m in _IPV4.finditer(text) if not _SAFE_HOST.search(m.group(1))]
    return out


def load_cases(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as e:
            raise ValueError(f"{path}: line {n} is not JSON ({e})") from None
    return rows


def _option_keys(options) -> list[str]:
    return [str(o).split("=", 1)[0].strip() for o in options] if isinstance(options, list) else []


def validate_cases(family: str, cases: list[dict]) -> list[str]:
    """Schema and split checks for one set (`routing`, `judgment` or `calibration`). Returns the problems; empty means the set is valid."""
    prefix, kind, splits = CASE_SETS[family]
    problems: list[str] = []
    ids: set[str] = set()
    sess: dict[str, str] = {}
    for n, c in enumerate(cases, 1):
        if not isinstance(c, dict):
            problems.append(f"{family} case {n}: not a JSON object")
            continue
        cid = c.get("id")
        w = f"{family} {cid}"
        if not isinstance(cid, str) or not cid.startswith(prefix):
            problems.append(f"{w}: id must be a string starting with {prefix}")
        elif cid in ids:
            problems.append(f"{w}: duplicate id")
        ids.add(str(cid))
        if c.get("kind") != kind:
            problems.append(f"{w}: kind must be {kind}")
        if not isinstance(c.get("class"), str) or not c.get("class"):
            problems.append(f"{w}: class is required")
        if c.get("lang") not in CASE_LANGS:
            problems.append(f"{w}: lang must be one of {CASE_LANGS}")
        elif c["lang"] != language(case_text(c)):
            problems.append(f"{w}: lang {c['lang']} but the text reads as {language(case_text(c))}")
        s, sp = c.get("session"), c.get("split")
        if not isinstance(s, str) or not s:
            problems.append(f"{w}: session is required")
        else:
            if sp != bucket(s):
                problems.append(f"{w}: split {sp!r} but the session hash gives {bucket(s)!r}")
            if sess.setdefault(s, sp) != sp:
                problems.append(f"{w}: session {s} is in two splits")
            if bucket(s) == "protected" or sp == "protected":
                problems.append(f"{w}: protected-split cases are never committed (T10)")
            elif bucket(s) not in splits:
                problems.append(f"{w}: a {family} case belongs in {'/'.join(splits)}, not {bucket(s)}")
        problems += [f"{w}: {p}" for part in case_parts(c) for p in text_problems(part)]
        problems += _validate_body(family, c, w)
    return problems


def _validate_body(family: str, c: dict, w: str) -> list[str]:
    out = []
    fam, opts = c.get("family"), c.get("options")
    if fam not in ("verify_claim", "strategy_choice"):
        out.append(f"{w}: family must be verify_claim or strategy_choice")
    if opts is not None and (not isinstance(opts, list) or not all(isinstance(o, str) and "=" in o for o in opts)):
        out.append(f"{w}: options must be a list of 'key=description' strings")
    if family == "routing":
        if c.get("should_route") not in ROUTES:
            out.append(f"{w}: should_route must be one of {ROUTES}")
        if c.get("should_call_jev") is not (c.get("should_route") == "JEV"):
            out.append(f"{w}: should_call_jev must be true exactly when should_route is JEV")
        if c.get("risk") not in ("low", "medium", "high", "critical"):
            out.append(f"{w}: risk must be low, medium, high or critical")
        if not isinstance(c.get("claim"), str) or not c["claim"].strip():
            out.append(f"{w}: claim is required")
        if fam == "strategy_choice" and not opts:
            out.append(f"{w}: a strategy_choice routing case needs options")
        gap, cur = c.get("known_gap"), c.get("current_route")
        if gap is not None and (not isinstance(gap, str) or cur not in ROUTES or cur == c.get("should_route")):
            out.append(f"{w}: known_gap needs a reason and a current_route that differs from should_route")
        if gap is None and cur is not None:
            out.append(f"{w}: current_route is only allowed with known_gap")
        return out
    if not isinstance(c.get("why"), str) or not c["why"].strip():
        out.append(f"{w}: why is required (the reason for the label, so an adjudicator can check it)")
    if fam == "strategy_choice":
        if family == "calibration":
            out.append(f"{w}: the calibration set holds verify_claim cases only (a choice has no probability of being true to calibrate)")
        if not isinstance(c.get("context"), str) or not c["context"].strip() or not isinstance(opts, list) or len(opts) < 2:
            out.append(f"{w}: a choice case needs a context and at least two options")
        elif c.get("best") not in _option_keys(opts):
            out.append(f"{w}: best must be one of the option keys")
    else:
        if not isinstance(c.get("truth"), bool):
            out.append(f"{w}: truth must be true or false (the claim is directly supported by the evidence)")
        for k in ("claim", "evidence"):
            if not isinstance(c.get(k), str) or not c[k].strip():
                out.append(f"{w}: {k} is required")
    return out


def cross_check(sets: dict[str, list[dict]]) -> list[str]:
    """Checks across sets: a session belongs to one set (so no session is in two splits), ids are unique across sets, and every language
    slice is non-empty (a calibration slice must also hold both outcomes, or its false-accept rate is undefined)."""
    out, owner, ids = [], {}, {}
    for name, rows in sets.items():
        rows = [c for c in rows if isinstance(c, dict)]   # a line that is not an object is reported by validate_cases
        for c in rows:
            sk, ik = repr(c.get("session")), repr(c.get("id"))   # a list or an object is a valid JSON value and an unhashable one
            if owner.setdefault(sk, name) != name:
                out.append(f"session {c.get('session')} is in two sets ({owner[sk]} and {name})")
            if ids.setdefault(ik, name) != name:
                out.append(f"id {c.get('id')} is in two sets ({ids[ik]} and {name})")
        for lang in CASE_LANGS:
            sl = [c for c in rows if c.get("lang") == lang]
            if not sl:
                out.append(f"{name}: the {lang} slice is empty")
            elif name == "calibration" and {c.get("truth") for c in sl} != {True, False}:
                out.append(f"{name}: the {lang} slice must hold both true and false labels")
    return out


def validate_sets(root: Path) -> list[str]:
    """Validate `evals/routing`, `evals/judgment` and `evals/calibration` under `root`."""
    sets, problems = {}, []
    for name in CASE_SETS:
        f = Path(root) / name / "cases.jsonl"
        if not f.exists():
            problems.append(f"{name}: {f} does not exist")
            continue
        try:
            sets[name] = load_cases(f)
        except ValueError as e:
            problems.append(str(e))
            continue
        problems += validate_cases(name, sets[name])
    return problems + cross_check(sets)
