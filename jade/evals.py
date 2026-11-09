"""Labels, splits and metrics (docs/04). Labels come from observed outcomes, user corrections or human
adjudication; heuristic labels are for monitoring only and never fit a threshold."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

from . import ledger, paths
from .util import is_human_tty, language, ts  # noqa: F401 - re-exported

SPLITS = (("dev", 50), ("calibration", 70), ("validation", 85), ("protected", 100))
FIT_SOURCES = ("observed", "user_correction", "adjudicated")
MIN_LABELED = 200
FA_BUDGET = 0.05
ADJUDICATION_PER_WEEK = 10


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
    return {"diff": sum(x - y for x, y in zip(a, b, strict=True)) / n, "ci95": [diffs[int(0.025 * rounds)], diffs[int(0.975 * rounds) - 1]], "n": n}


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
    """Join outcomes to the calls that judged the same decision (source `observed`), and mark answers that were
    later contradicted in the same session (source `heuristic`)."""
    n = {"observed": 0, "heuristic": 0}
    for r in db.all("SELECT c.call_id, d.status FROM jev_calls c JOIN decisions d ON d.decision_id=c.decision_id "
                    "WHERE c.p IS NOT NULL AND c.primitive='noul' AND d.status IN ('SUCCEEDED','FAILED') "
                    "AND c.call_id NOT IN (SELECT call_id FROM labels WHERE source<>'heuristic')"):
        set_label(db, r.call_id, 1 if r.status == "SUCCEEDED" else 0, "observed", "outcome join")
        n["observed"] += 1
    for r in db.all("SELECT a.call_id FROM jev_calls a JOIN jev_calls b ON b.session_id=a.session_id AND b.claim_hash=a.claim_hash AND b.rowid>a.rowid "
                    "WHERE a.exit_code=0 AND b.exit_code=4 AND a.primitive='noul' AND a.call_id NOT IN (SELECT call_id FROM labels)"):
        set_label(db, r.call_id, 0, "heuristic", "accepted, then rejected with new evidence in the same session")
        n["heuristic"] += 1
    return n


def adjudication_queue(db: ledger.Ledger, limit: int = ADJUDICATION_PER_WEEK) -> list:
    """Unlabeled answers nearest the decision boundary, capped per week so the human is not flooded (threat T14)."""
    done = db.one("SELECT COUNT(*) AS n FROM labels WHERE source='adjudicated' AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-7 days')").n
    room = max(0, limit - done)
    return db.all("SELECT call_id, p, verdict, question_id, substr(COALESCE(question_text,''),1,160) AS q, ts FROM jev_calls "
                  "WHERE p IS NOT NULL AND primitive='noul' AND dedup_of IS NULL AND task_family='verify_claim' "
                  "AND call_id NOT IN (SELECT call_id FROM labels) ORDER BY ABS(p-0.70) LIMIT ?", (room,))


def labeled(db: ledger.Ledger, fit_only: bool = True, include_protected: bool = False) -> list[dict]:
    src = "AND l.source<>'heuristic'" if fit_only else ""
    rows = db.all("SELECT c.call_id, c.p, c.task_family, c.question_version, c.resolved_model, c.session_id, c.risk, c.language, l.label, l.source "
                  f"FROM jev_calls c JOIN labels l ON l.call_id=c.call_id WHERE c.p IS NOT NULL AND c.primitive='noul' {src}")  # noqa: S608 - constant fragment
    out = [vars(r) | {"session": r.session_id or "", "split": bucket(r.session_id or ""), "language": r.language or "unknown"} for r in rows]
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
        m["eligible_for_adjustment"] = m["n"] >= MIN_LABELED
        out["groups"][g] = m
    out["note"] = "no labeled rows yet" if not groups else ""
    by_lang: dict[str, list[dict]] = {}
    for rows in groups.values():
        for r in rows:
            by_lang.setdefault(r["language"], []).append(r)
    out["by_language"] = {lang: {k: m[k] for k in ("n", "false_accept_rate", "coverage", "brier")}
                          for lang, rows in sorted(by_lang.items()) for m in [metrics([r["p"] for r in rows], [r["label"] for r in rows])]}
    return out


def propose_threshold(rows: list[dict], budget: float = FA_BUDGET) -> dict | None:
    """Lowest accept threshold whose Wilson 95% upper bound on false accepts meets the budget (ADR-07).
    Returns None below 200 fit-quality labels: defaults stay."""
    if len(rows) < MIN_LABELED:
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

    c1 = [1 if r["label"] == 0 else 0 for r in rows]
    c3 = []
    for r in rows:
        accept, _ = thresholds.bands("noul", r["task_family"], r.get("risk") or "low")
        c3.append(1 if (r["p"] >= accept and r["label"] == 0) else 0)
    return {"n": len(rows), "unsafe_accept_rate": {"1_bare_jev": sum(c1) / len(rows), "3_jev_thresholds": sum(c3) / len(rows)},
            "paired_3_minus_1": paired_bootstrap(c3, c1), "2_reasoning_only": "not measurable: no DEEP verdict labels",
            "4_cascade": "not measurable: no DEEP verdict labels"}
