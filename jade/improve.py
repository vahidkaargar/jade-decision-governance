"""Bounded self-improvement (docs/07). The miner proposes, the evaluator scores, a human (or the narrow
auto-promotion rule) installs. Protected targets cannot even be expressed as a candidate."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from . import ledger, ops, paths, schemas
from .util import is_human_tty, new_id, now, ts

ALLOWED = ("question_template", "state_selection", "routing_heuristic", "calibration_threshold", "batch_strategy", "cache_ttl",
           "skill_reference", "escalation_heuristic", "performance")
AUTO_PROMOTABLE = ("question_template", "cache_ttl")
REQUIRED_RESULTS = ("protected_test_non_inferior", "no_safety_invariant_violation", "latency_within_budget")
SHADOW_IN_HOT_PATH = False


class ProtectedTarget(ValueError):
    pass


class PromotionBlocked(PermissionError):
    pass


def frozen(path=None) -> bool:
    return ops.frozen(path)


def shadow_mode_is_async() -> bool:
    """Shadow evaluation runs from `jade improve shadow` (maintenance job), never inside `jade ask`."""
    return not SHADOW_IN_HOT_PATH


def _db(path):
    return (path, False) if isinstance(path, ledger.Ledger) else (ledger.open(path), True)


def propose(target_component: str, change_diff: str, hypothesis: str, *, baseline_version: str = "current", predicted_benefit: str = "unmeasured",
            risk: str = "low", evaluation_plan: str = "offline replay on dev and calibration splits, paired against the champion",
            dataset_versions: list[str] | None = None, rollback_reference: str | None = None, actor: str = "agent:miner", path=None,
            key: str | None = None):
    if target_component not in ALLOWED:
        raise ProtectedTarget(f"{target_component!r} is not an improvable component; protected targets are listed in policies/safety.yaml")
    doc = {"schema_version": "1.0.0", "candidate_id": new_id("imp"), "baseline_version": baseline_version, "target_component": target_component,
           "change_diff": change_diff, "hypothesis": hypothesis, "predicted_benefit": predicted_benefit, "risk": risk,
           "evaluation_plan": evaluation_plan, "dataset_versions": dataset_versions or ["ledger-dev", "ledger-calibration"], "results": None,
           "promotion_decision": "pending", "rollout_scope": "offline", "rollback_reference": rollback_reference or baseline_version,
           "audit_trail": [{"at": ts(), "actor": actor, "event": "created"}]}
    schemas.validate("improvement", doc)
    db, own = _db(path)
    try:
        if key:
            if db.meta_get(f"improve:{key}"):
                return None
            db.meta_set(f"improve:{key}", doc["candidate_id"])
        db.con.execute("INSERT INTO improvements(candidate_id, decision, doc_json, updated_at) VALUES(?,?,?,?)",
                       (doc["candidate_id"], "pending", json.dumps(doc, ensure_ascii=False), ts()))
    finally:
        if own:
            db.close()
    return SimpleNamespace(**doc)


def get(path, candidate_id: str) -> dict | None:
    db, own = _db(path)
    try:
        row = db.one("SELECT doc_json FROM improvements WHERE candidate_id=?", (candidate_id,))
        return json.loads(row.doc_json) if row else None
    finally:
        if own:
            db.close()


def _save(db, doc: dict, actor: str, event: str) -> None:
    doc["audit_trail"].append({"at": ts(), "actor": actor, "event": event})
    schemas.validate("improvement", doc)
    db.con.execute("UPDATE improvements SET decision=?, doc_json=?, updated_at=? WHERE candidate_id=?",
                   (doc["promotion_decision"], json.dumps(doc, ensure_ascii=False), ts(), doc["candidate_id"]))


def attach_results(path, candidate_id: str, results: dict, actor: str = "runtime:evaluator") -> dict:
    db, own = _db(path)
    try:
        doc = get(db, candidate_id)
        if doc is None:
            raise PromotionBlocked(f"unknown candidate {candidate_id}")
        doc["results"] = {**(doc["results"] or {}), **results}
        _save(db, doc, actor, "results attached: " + ", ".join(sorted(results)))
        return doc
    finally:
        if own:
            db.close()


def promote(path, candidate_id: str, stdin_isatty: bool | None = None) -> dict:
    """Promotion per policies/promotion.yaml. Blocks when frozen, when any required result is missing or false,
    and when the target needs a human and none is at the terminal."""
    db, own = _db(path)
    try:
        home = db.path.parent
        if frozen(home):
            raise PromotionBlocked("kill switch FREEZE is present: challengers disabled, thresholds frozen")
        doc = get(db, candidate_id)
        if doc is None:
            raise PromotionBlocked(f"unknown candidate {candidate_id}")
        res = doc.get("results") or {}
        missing = [k for k in REQUIRED_RESULTS if res.get(k) is not True]
        if missing:
            raise PromotionBlocked(f"missing or failed required results: {', '.join(missing)}")
        human = is_human_tty(stdin_isatty)
        if doc["target_component"] not in AUTO_PROMOTABLE and not human:
            raise PromotionBlocked(f"{doc['target_component']} is promoted only by a human at a terminal")
        if doc["target_component"] in AUTO_PROMOTABLE and not human and not paths.config().get("auto_promotion_opt_in"):
            raise PromotionBlocked("auto-promotion needs the human opt-in `auto_promotion_opt_in` in config.json")
        doc["promotion_decision"], doc["rollout_scope"] = "promoted", "all"
        if doc["target_component"] == "question_template" and res.get("question_id"):
            ops.promote_challenger(home, res["question_id"])
        if doc["target_component"] == "calibration_threshold" and res.get("group") and res.get("accept") is not None:
            cal = home / "calibration"
            cal.mkdir(exist_ok=True)
            cur = json.loads((cal / "current.json").read_text()) if (cal / "current.json").exists() else {}
            cur[res["group"]] = {"accept": res["accept"], **({"reject": res["reject"]} if res.get("reject") is not None else {}), "candidate": candidate_id}
            (cal / "current.json").write_text(json.dumps(cur, indent=1) + "\n")
        _save(db, doc, "human:terminal" if human else "runtime:auto", "promoted")
        return doc
    finally:
        if own:
            db.close()


def rollback(path, candidate_id: str, actor: str = "human:terminal") -> dict:
    db, own = _db(path)
    try:
        home = db.path.parent
        doc = get(db, candidate_id)
        if doc is None:
            raise PromotionBlocked(f"unknown candidate {candidate_id}")
        res = doc.get("results") or {}
        if doc["target_component"] == "question_template" and res.get("question_id"):
            ops.rollback(home, res["question_id"])
        cal = home / "calibration" / "current.json"
        if doc["target_component"] == "calibration_threshold" and cal.exists():
            cur = {g: v for g, v in json.loads(cal.read_text()).items() if v.get("candidate") != candidate_id}
            cal.write_text(json.dumps(cur, indent=1) + "\n")
        doc["promotion_decision"], doc["rollout_scope"] = "rolled_back", "offline"
        _save(db, doc, actor, f"rolled back to {doc['rollback_reference']}")
        return doc
    finally:
        if own:
            db.close()


# ---- miner -----------------------------------------------------------------------------------------
def patterns(db: ledger.Ledger, days: int = 7) -> list[dict]:
    """Ledger patterns worth a candidate or an observation. Counts only; no state text leaves the ledger."""
    since = f"-{days} days"
    w = "ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?)"
    total = db.one(f"SELECT COUNT(*) AS n FROM jev_calls WHERE {w}", (since,)).n  # noqa: S608 - constant fragment
    if total == 0:
        return []
    out = []
    rep = db.one(f"SELECT COUNT(*) AS n FROM jev_calls WHERE dedup_of IS NOT NULL AND {w}", (since,)).n  # noqa: S608
    if rep / total > 0.03 and rep >= 5:
        out.append({"key": "repeat_without_new_evidence", "target": "skill_reference", "instruction": True, "n": rep, "total": total,
                    "hypothesis": f"{rep} of {total} calls repeated a question with identical state; guidance should say what counts as new evidence"})
    und = db.one(f"SELECT COUNT(*) AS n FROM jev_calls WHERE exit_code IN (3,4) AND disposition IS NULL AND reminded=1 AND {w}", (since,)).n  # noqa: S608
    if und >= 5:
        out.append({"key": "non_accept_without_disposition", "target": "skill_reference", "instruction": True, "n": und, "total": total,
                    "hypothesis": f"{und} abstain/reject answers were reminded by the Stop hook and still have no disposition"})
    for r in db.all(f"SELECT error_kind, COUNT(*) AS n FROM jev_calls WHERE error_kind IS NOT NULL AND {w} GROUP BY error_kind", (since,)):  # noqa: S608
        if r.n / total > 0.02 and r.n >= 3:
            out.append({"key": f"provider_error_{r.error_kind}", "target": "performance", "instruction": False, "n": r.n, "total": total,
                        "hypothesis": f"provider error {r.error_kind} on {r.n} of {total} calls exceeds the 2% budget"})
    for r in db.all(f"SELECT question_id, COUNT(*) AS n, SUM(exit_code=3) AS a FROM jev_calls WHERE p IS NOT NULL AND dedup_of IS NULL AND {w} "  # noqa: S608
                    "GROUP BY question_id HAVING n >= 20", (since,)):
        if r.a / r.n > 0.5:
            out.append({"key": f"high_abstention_{r.question_id}", "target": "question_template", "instruction": False, "n": r.a, "total": r.n,
                        "baseline": r.question_id, "hypothesis": f"{r.question_id} abstained on {r.a} of {r.n} answers; the template or the required evidence needs work"})
    byp = db.one("SELECT COUNT(*) AS n FROM events WHERE kind='BYPASS' AND " + w, (since,)).n
    if byp:
        out.append({"key": "adapter_bypass", "target": "skill_reference", "instruction": True, "n": byp, "total": total,
                    "hypothesis": f"{byp} raw Decisions API call(s) bypassed the adapter"})
    return out


def mine(db: ledger.Ledger, days: int = 7, observer_root: Path | None = None) -> list[dict]:
    """Turn patterns into improvement records (once per pattern per ISO week) and, for instruction problems,
    into task-observer observations. Installs nothing."""
    week = f"{now():%G-W%V}"
    made = []
    for p in patterns(db, days):
        c = propose(p["target"], f"(to be drafted) address pattern {p['key']}", p["hypothesis"], baseline_version=p.get("baseline", "current"),
                    predicted_benefit=f"pattern count below budget (now {p['n']}/{p['total']})", path=db, key=f"{week}:{p['key']}")
        if c is None:
            continue
        entry = {"pattern": p["key"], "candidate_id": c.candidate_id, "observation": None}
        if p["instruction"]:
            entry["observation"] = _observe(p, observer_root)
        made.append(entry)
    return made


def _observe(p: dict, root: Path | None) -> str | None:
    """Write an observation through the task-observer helper when it is installed; otherwise skip quietly."""
    root = root or Path.home() / ".claude"
    helper = root / "skills" / "task-observer" / "scripts" / "new-observation.sh"
    if not helper.exists():
        return None
    body = (f"## Issue\nJADE ledger pattern `{p['key']}`: {p['hypothesis']}.\n\n## Evidence\nCounts from `jade improve mine` "
            f"({p['n']} of {p['total']} in the window). No state text included.\n\n## Suggested improvement\nReview the jade skill and the "
            "CLAUDE.md Jev section for this pattern; a matching improvement record exists in the JADE ledger.\n")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
    try:
        out = subprocess.run(["bash", str(helper), "--body", f.name, f"jade-{p['key'].replace('_', '-')[:40]}", str(root)],
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip().splitlines()[-1] if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        Path(f.name).unlink(missing_ok=True)


def shadow(db: ledger.Ledger, candidate_id: str, question_id: str, version: int, provider=None, limit: int = 100, days: int = 7) -> dict:
    """Asynchronous shadow run: replay recent champion questions' stored (redacted) state through a challenger
    question version and compare verdicts. It runs from a maintenance command, writes nothing to the live call
    table, and never sits on the hot path (`shadow_mode_is_async`). Needs state text still inside retention."""
    from . import adapter, questions, thresholds

    if frozen(db.path.parent):
        raise PromotionBlocked("kill switch FREEZE is present: shadow runs are disabled")
    entry = questions.get(question_id, version)
    if entry["primitive"] != "noul":
        raise PromotionBlocked("shadow comparison is implemented for noul questions")
    provider = provider or adapter.OpenRouterProvider()
    rows = db.all("SELECT call_id, state_text, exit_code FROM jev_calls WHERE question_id=? AND state_text IS NOT NULL AND exit_code IN (0,3,4) "
                  "AND dedup_of IS NULL AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?) ORDER BY rowid DESC LIMIT ?", (question_id, f"-{days} days", limit))
    payload = questions.build_noul(question_id)
    n = agree = champ_acc = chall_acc = errors = 0
    cost, lats, disagreements = 0.0, [], []
    for r in rows:
        res = provider.ask(json.loads(r.state_text), {"q": payload})
        if not res.ok:
            errors += 1
            continue
        v = thresholds.judge(res.answers["q"], entry["task_family"], "low", version, res.resolved_model)
        n += 1
        agree += v.exit_code == r.exit_code
        champ_acc += r.exit_code == 0
        chall_acc += v.exit_code == 0
        cost += res.cost_usd or 0.0
        lats.append(res.latency_ms or 0.0)
        if v.exit_code != r.exit_code and len(disagreements) < 20:
            disagreements.append({"call_id": r.call_id, "champion_exit": r.exit_code, "challenger_exit": v.exit_code})
    lats.sort()
    out = {"question_id": question_id, "challenger_version": version, "n": n, "errors": errors, "agreement": round(agree / n, 3) if n else None,
           "champion_accept_rate": round(champ_acc / n, 3) if n else None, "challenger_accept_rate": round(chall_acc / n, 3) if n else None,
           "cost_usd": round(cost, 6), "p95_latency_ms": round(lats[int(0.95 * (len(lats) - 1))]) if lats else None, "disagreements": disagreements,
           "min_live_samples_met": n >= 100, "ran_at": ts()}
    attach_results(db, candidate_id, {"shadow": out, "question_id": question_id})
    return out


def replay_threshold(db: ledger.Ledger, candidate_id: str, group: str, accept: float, reject: float | None = None) -> dict:
    """Offline paired replay of a threshold change on dev + calibration labels (never the protected split)."""
    from . import evals, thresholds

    rows = [r for r in evals.labeled(db) if thresholds.group_of(r["task_family"], r["question_version"], r["resolved_model"]) == group
            and r["split"] in ("dev", "calibration")]
    if not rows:
        return attach_results(db, candidate_id, {"replay": "no labeled rows in dev/calibration for this group", "group": group, "accept": accept})
    cur_a, cur_r = thresholds.bands("noul", rows[0]["task_family"], "low", group)
    preds, labels = [r["p"] for r in rows], [r["label"] for r in rows]
    champ = evals.metrics(preds, labels, cur_a, cur_r)
    chall = evals.metrics(preds, labels, accept, reject if reject is not None else cur_r)
    res = {"group": group, "accept": accept, "reject": reject, "n": len(rows), "champion": {k: champ[k] for k in ("false_accept_rate", "false_accept_ci95", "coverage", "brier")},
           "challenger": {k: chall[k] for k in ("false_accept_rate", "false_accept_ci95", "coverage", "brier")},
           "offline_replay_pass": len(rows) >= 200 and chall["false_accept_ci95"][1] <= champ["false_accept_rate"] + 1e-9
           and chall["coverage"] >= champ["coverage"] - 0.05}
    return attach_results(db, candidate_id, res)
