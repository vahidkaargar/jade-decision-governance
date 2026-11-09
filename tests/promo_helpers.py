"""Fixtures for the improvement pipeline tests: a ledger of labeled answers that a threshold or template candidate can be evaluated on,
so promotion is exercised through the evaluators and never by attaching a result."""
from __future__ import annotations

import json
import shutil
from datetime import timedelta

from jade import adapter, ask, evals, improve, ledger, paths, questions
from jade.util import now, ts

Q = "q.verify_claim.supported_by_evidence"


def p_of(state: dict) -> float:
    """The probability a fixture answers with: carried in the claim text so every provider replays the same answer."""
    return float(str(state.get("claim", "")).rsplit("|p=", 1)[1]) if "|p=" in str(state.get("claim", "")) else 0.9


def provider(shift: float = 0.0):
    return adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": max(0.0, min(1.0, p_of(s) + shift))} for k in q})


def seed(db: ledger.Ledger, *, hi: int = 120, lo: int = 165, mid: int = 14, hold: int = 160, val: int = 20, prefix: str = "s") -> None:
    """Labeled calls spread over the splits. dev + calibration get `hi` clear accepts (true), `lo` clear rejects (false) and `mid` answers
    at P=0.75, 6 of them false claims: the default accept band (0.70) takes them, a stricter one (0.85) abstains. The protected and
    validation splits get clear accepts and rejects only."""
    pools = {"main": ([(0.95, 1)] * hi + [(0.05, 0)] * lo + [(0.75, 0)] * 6 + [(0.75, 1)] * (mid - 6)),
             "protected": [(0.95, 1), (0.05, 0)] * (hold // 2), "validation": [(0.95, 1), (0.05, 0)] * (val // 2)}
    import random

    random.Random(5).shuffle(pools["main"])
    i = 0
    while any(pools.values()):
        sess = f"{prefix}{i}"
        i += 1
        b = evals.bucket(sess)
        pool = pools["main"] if b in ("dev", "calibration") else pools[b]
        if not pool:
            continue
        p, label = pool.pop()
        r = ask.run(ask.verify_specs(companion=False), {"claim": f"claim {i} |p={p}", "evidence": f"raw output {i}"}, provider=provider(), session=sess, db=db)
        evals.set_label(db, r.primary.call_id, label, "adjudicated")


def group_of(db: ledger.Ledger) -> str:
    return next(iter(evals.report(db)["groups"]))


def backdate_shadow(db: ledger.Ledger, candidate_id: str, days: float = 8) -> None:
    doc = improve.get(db, candidate_id)
    doc["results"]["shadow_started_at"] = ts(now() - timedelta(days=days))
    db.con.execute("UPDATE improvements SET doc_json=? WHERE candidate_id=?", (json.dumps(doc), candidate_id))


def registry_v2(tmp_path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    shutil.copytree(paths.registry_dir(), reg)
    q = json.loads((reg / f"{Q}.json").read_text())
    q["version"] = 2
    q["template"]["instructions"] = "Is `claim` directly supported by `evidence`? A claim whose evidence is a summary is not supported."
    (reg / f"{Q}.v2.json").write_text(json.dumps(q))
    monkeypatch.setenv("JADE_REGISTRY_DIR", str(reg))
    questions.reset_cache()


def threshold_candidate(db: ledger.Ledger, accept: float = 0.85) -> str:
    """A calibration_threshold candidate that has been replayed and (human terminal) checked on the protected split."""
    g = group_of(db)
    c = improve.propose("calibration_threshold", f"{g}: accept 0.70 -> {accept}", "y" * 10, path=db)
    improve.replay_threshold(db, c.candidate_id, g, accept)
    improve.protected_check(db, c.candidate_id, stdin_isatty=True)
    return c.candidate_id


def template_candidate(db: ledger.Ledger, tmp_path) -> str:
    """A question_template candidate that went through shadow (backdated past the 7 days), a human protected check and a canary."""
    c = improve.propose("question_template", "stricter clause", "y" * 10, path=db)
    improve.shadow(db, c.candidate_id, Q, 2, provider=provider(), limit=100)
    improve.protected_check(db, c.candidate_id, provider=provider(), stdin_isatty=True)
    backdate_shadow(db, c.candidate_id)
    improve.start_canary(db, c.candidate_id, stdin_isatty=True)
    return c.candidate_id
