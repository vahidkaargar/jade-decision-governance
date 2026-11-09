"""Bounded self-improvement (docs/07). The miner proposes, the evaluator scores, a human (or the narrow
auto-promotion rule) installs. Protected targets cannot even be expressed as a candidate."""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from . import ledger, ops, paths, schemas
from .util import is_human_tty, new_id, now, parse_ts, ts

ALLOWED = ("question_template", "state_selection", "routing_heuristic", "calibration_threshold", "batch_strategy", "cache_ttl",
           "skill_reference", "escalation_heuristic", "performance")
# The shipped values of policies/promotion.yaml (promote.auto_allowed_for, promote.requires). The gate reads the file at use; a test pins these to it.
AUTO_PROMOTABLE = ("question_template", "cache_ttl")
REQUIRED_RESULTS = ("protected_test_non_inferior", "no_safety_invariant_violation", "latency_within_budget", "audit_trail_complete")
# Results the system computes. A caller cannot attach them: `promote` derives every one from the evidence the evaluators stored.
COMPUTED_RESULTS = (*REQUIRED_RESULTS, "offline_replay_pass", "min_live_samples_met",
                    # ... and the evidence they are derived from, written only by the evaluators (`replay_threshold`, `shadow`, `protected_check`, `start_canary`)
                    "champion", "challenger", "validation", "worse_subgroups", "distinct_claims", "n", "group", "accept", "reject", "question_id",
                    "shadow", "shadow_started_at", "protected", "canary", "promotion")
EVALUATED = ("calibration_threshold", "question_template")   # targets with an evaluator; any other target cannot be promoted by this code
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
        if row is None:
            return None
        try:   # validated on every read (W-57): a hand-edited or migrated row is never promoted, shadowed or rolled back on trust
            return schemas.load("improvement", row.doc_json, id_field="candidate_id", id_value=candidate_id)
        except schemas.Unreadable as e:
            raise PromotionBlocked(f"candidate {candidate_id} has an unreadable or invalid stored record ({e}); `jade check` lists it") from None
    finally:
        if own:
            db.close()


def _save(db, doc: dict, actor: str, event: str) -> None:
    doc["audit_trail"].append({"at": ts(), "actor": actor, "event": event})
    schemas.validate("improvement", doc)
    db.con.execute("UPDATE improvements SET decision=?, doc_json=?, updated_at=? WHERE candidate_id=?",
                   (doc["promotion_decision"], json.dumps(doc, ensure_ascii=False), ts(), doc["candidate_id"]))


def attach_results(path, candidate_id: str, results: dict, actor: str = "runtime:evaluator", *, stage: str | None = None, _computed: bool = False) -> dict:
    """Store evaluator output on a candidate. The booleans the promotion gate needs are not accepted here: they are derived at promotion
    from the stored evidence (`evaluate`), so no caller can assert that a candidate passed."""
    clash = [k for k in results if k in COMPUTED_RESULTS]
    if clash and not _computed:
        raise PromotionBlocked(f"{', '.join(sorted(clash))} are computed by the evaluator, not attached by a caller")
    db, own = _db(path)
    try:
        doc = get(db, candidate_id)
        if doc is None:
            raise PromotionBlocked(f"unknown candidate {candidate_id}")
        if doc["promotion_decision"] in ("promoted", "rolled_back", "rejected"):
            raise PromotionBlocked(f"candidate {candidate_id} is {doc['promotion_decision']}; results are not changed after the decision")
        doc["results"] = {**(doc["results"] or {}), **results}
        _save(db, doc, actor, (f"stage:{stage}: " if stage else "") + "results attached: " + ", ".join(sorted(results)))
        return doc
    finally:
        if own:
            db.close()


def policy() -> dict:
    return ops.promotion_policy()


# ---- evidence -> verdicts (pure; the evaluators use them when they store, `promote` when it decides) ---------------------------
def _m(preds: list[float], labels: list[int], accept: float, reject: float | None) -> dict:
    from . import evals

    m = evals.metrics(preds, labels, accept, reject if reject is not None else -1.0)
    return {k: m[k] for k in ("n", "accepted", "false_accept", "false_accept_rate", "false_accept_ci95", "coverage", "brier")}


def _band_ok(accept, reject) -> bool:
    num = lambda x: isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x)  # noqa: E731
    return num(accept) and 0 < accept <= 1 and (reject is None or (num(reject) and 0 <= reject < accept))


def _binom_tail(k: int, n: int) -> float:
    """P(X >= k) for X ~ Binomial(n, 1/2)."""
    return 1.0 if n == 0 or k <= 0 else sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def holm_rejected(pvals: dict[str, float], alpha: float) -> set[str]:
    """Holm step-down: the hypotheses rejected at family-wise level alpha."""
    out, m = set(), len(pvals)
    for i, (name, p) in enumerate(sorted(pvals.items(), key=lambda kv: kv[1])):
        if p > alpha / (m - i):
            break
        out.add(name)
    return out


def _subgroup(name: str, r: dict) -> bool:
    kind, _, val = name.partition(":")
    if kind == "language":
        return r.get("language") == val
    return kind == "risk" and val.endswith("+") and r.get("risk") in ("high", "critical")


def _worse_than_champion(rows: list[dict], cur_accept: float, new_accept: float, pol: dict) -> list[str]:
    """Subgroups (and the whole set) where the challenger accepts significantly more false claims than the champion: a paired
    one-sided sign test on the false claims each accepts and the other does not, Holm-corrected across the comparisons made."""
    off, pvals = pol.get("offline_replay", {}), {}
    groups = {"all": rows}
    for name in off.get("subgroups", []):
        groups[name] = [r for r in rows if _subgroup(name, r)]
    for name, rs in groups.items():
        if name != "all" and len(rs) < int(off.get("subgroup_min_samples", 30)):
            continue
        bad = [r for r in rs if r["label"] == 0]
        worse = sum(1 for r in bad if new_accept <= r["p"] < cur_accept)
        better = sum(1 for r in bad if cur_accept <= r["p"] < new_accept)
        pvals[name] = _binom_tail(worse, worse + better)
    return sorted(holm_rejected(pvals, float(off.get("alpha", 0.05))))


def _non_inferior(champ: dict, chall: dict, margins: dict) -> bool:
    return (chall["false_accept_rate"] <= champ["false_accept_rate"] + 1e-9 and chall["brier"] <= champ["brier"] + float(margins.get("brier", 0.01))
            and chall["coverage"] >= champ["coverage"] - float(margins.get("abstain_coverage", 0.05)))


def replay_verdict(res: dict, pol: dict) -> tuple[bool, str]:
    """offline_replay pass_if (policies/promotion.yaml), recomputed from the stored paired metrics."""
    off = pol.get("offline_replay", {})
    champ, chall = res.get("champion"), res.get("challenger")
    if not isinstance(champ, dict) or not isinstance(chall, dict) or not res.get("n"):
        return False, "no paired replay was run"
    need = int(off.get("min_samples", 200))
    if res.get("distinct_claims", res["n"]) < need:
        return False, f"{res.get('distinct_claims', res['n'])} distinct labeled claims, fewer than {need}"
    margins = off.get("margins", {})
    if not chall["false_accept_ci95"][1] <= champ["false_accept_rate"] + 1e-9:
        return False, "challenger false-accept upper 95% bound is above the champion's rate"
    if chall["brier"] > champ["brier"] + float(margins.get("brier", 0.01)) or chall["coverage"] < champ["coverage"] - float(margins.get("abstain_coverage", 0.05)):
        return False, "brier or abstain coverage regressed past the margin"
    if res.get("worse_subgroups"):
        return False, f"significantly more false accepts in {', '.join(res['worse_subgroups'])}"
    val = res.get("validation")
    if isinstance(val, dict) and val.get("champion") and val["champion"]["accepted"] >= int(off.get("subgroup_min_samples", 30)) \
            and val["challenger"]["false_accept_rate"] > val["champion"]["false_accept_rate"] + 1e-9:
        return False, "the validation split shows more false accepts than the champion"
    return True, "ok"


def shadow_verdict(res: dict, pol: dict) -> tuple[bool, str]:
    sh, cfg = res.get("shadow"), pol.get("shadow", {})
    if not isinstance(sh, dict) or not sh.get("n"):
        return False, "no shadow run"
    need = int(cfg.get("min_live_samples", 100))
    if sh["n"] < need:
        return False, f"{sh['n']} live samples, fewer than {need}"
    try:
        started = parse_ts(res["shadow_started_at"])
    except (KeyError, ValueError, TypeError):
        return False, "the shadow start time was not recorded"
    if now() - started < timedelta(days=float(cfg.get("duration_days", 7))):
        return False, f"shadow has run less than {cfg.get('duration_days', 7)} days"
    if sh["errors"] / (sh["n"] + sh["errors"]) > float(cfg.get("max_error_share", 0.05)):
        return False, "provider errors in the shadow run exceed the budget"
    return True, "ok"


def protected_verdict(res: dict, pol: dict) -> tuple[bool, str]:
    pr = res.get("protected")
    if not isinstance(pr, dict) or not isinstance(pr.get("champion"), dict) or not isinstance(pr.get("challenger"), dict):
        return False, "the protected split was not evaluated (a human runs `jade improve protected`)"
    need = int(pol.get("protected_test", {}).get("min_samples", 30))
    if pr["n"] < need:
        return False, f"{pr['n']} labeled protected rows, fewer than {need}"
    if not _non_inferior(pr["champion"], pr["challenger"], pol.get("offline_replay", {}).get("margins", {})):
        return False, "the challenger is not non-inferior on the protected split"
    return True, "ok"


def safety_verdict(doc: dict, res: dict) -> tuple[bool, str]:
    """No safety invariant is weakened: a threshold stays a valid band and accepts no more false claims than the champion on the data
    replayed; a template flips no answer the champion rejected into an accept."""
    pr = res.get("protected") or {}
    if doc["target_component"] == "calibration_threshold":
        if not _band_ok(res.get("accept"), res.get("reject")):
            return False, "the band is not valid (0 < accept <= 1, reject < accept)"
        for part in (res, pr):
            if part.get("champion") and part["challenger"]["false_accept"] > part["champion"]["false_accept"]:
                return False, "the challenger accepts more false claims than the champion on replayed data"
        return True, "ok"
    sh = res.get("shadow")
    if not isinstance(sh, dict):
        return False, "no shadow run"
    if sh.get("reject_to_accept", 1) != 0:
        return False, "the challenger accepts claims the champion rejected"
    if pr.get("champion") and pr["challenger"]["false_accept"] > pr["champion"]["false_accept"]:
        return False, "the challenger accepts more false claims than the champion on the protected split"
    return True, "ok"


def latency_verdict(doc: dict, res: dict, pol: dict) -> tuple[bool, str]:
    if SHADOW_IN_HOT_PATH:
        return False, "shadow evaluation runs in the hot path"
    if doc["target_component"] == "calibration_threshold":
        return True, "a threshold makes no provider call"
    sh = res.get("shadow") or {}
    p95, base = sh.get("p95_latency_ms"), sh.get("champion_p95_latency_ms")
    if p95 is None or base is None:
        return False, "no latency was measured for the challenger and the champion"
    if p95 > base * float(pol.get("shadow", {}).get("latency_budget_ratio", 1.25)):
        return False, f"challenger p95 {p95} ms against champion {base} ms"
    return True, "ok"


def audit_verdict(doc: dict, res: dict) -> tuple[bool, str]:
    """The trail records `created`, every pipeline stage the target needs, in pipeline order, with an actor and a time on each entry."""
    trail = doc.get("audit_trail") or []
    if not trail or trail[0].get("event") != "created" or any(not (e.get("at") and e.get("actor") and e.get("event")) for e in trail):
        return False, "the audit trail is empty, does not start with `created` or has an incomplete entry"
    if [e["at"] for e in trail] != sorted(e["at"] for e in trail):
        return False, "audit trail times go backwards"
    order = [x for x in policy().get("pipeline", []) if x != "promote"]
    need = ["offline_replay", "protected"] if doc["target_component"] == "calibration_threshold" else ["shadow", "canary", "protected"]
    seen = [e["event"].split(":")[1] for e in trail if e["event"].startswith("stage:")]
    for st in need:
        if st not in seen:
            return False, f"stage {st} is not in the audit trail"
    idx = [order.index(st) for st in seen if st in order]
    if idx != sorted(idx):
        return False, "pipeline stages are out of order in the audit trail"
    return True, "ok"


def canary_verdict(res: dict, home: Path, db) -> tuple[bool, str]:
    """A template reaches promotion only through a canary, at no more than the policy's traffic share, of the version that was shadowed."""
    can, sh = res.get("canary"), res.get("shadow") or {}
    if not isinstance(can, dict) or can.get("version") != sh.get("challenger_version"):
        return False, "no canary of the shadowed version was started"
    pin = ops.pins_of(home, res.get("question_id", ""))
    if pin.get("challenger") != can["version"]:
        return False, "the pinned challenger is not the canary version"
    if not 0 < float(pin.get("challenger_fraction", 1)) <= ops.canary_fraction():
        return False, "the challenger's traffic share is above the canary fraction"
    since = f"-{int(policy().get('shadow', {}).get('duration_days', 7))} days"
    row = db.one("SELECT COUNT(*) AS n, SUM(error_kind IS NOT NULL) AS e FROM jev_calls WHERE ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?) "
                 "AND COALESCE(primitive,'')<>'fast' AND " + ledger.live(), (since,))
    if row.n and (row.e or 0) / row.n > 0.05:
        return False, "canary rollback trigger: provider error rate above 5%"
    return True, "ok"


def evaluate(db, doc: dict, home: Path) -> dict[str, tuple[bool, str]]:
    """Every result the gate requires, derived from stored evidence: promote.requires of policies/promotion.yaml plus the stage checks."""
    pol, res = policy(), doc.get("results") or {}
    out = {"protected_test_non_inferior": protected_verdict(res, pol), "no_safety_invariant_violation": safety_verdict(doc, res),
           "latency_within_budget": latency_verdict(doc, res, pol), "audit_trail_complete": audit_verdict(doc, res)}
    if doc["target_component"] == "calibration_threshold":
        out["offline_replay_pass"] = replay_verdict(res, pol)
    else:
        out["offline_replay_pass"] = shadow_verdict(res, pol)
        out["canary_ok"] = canary_verdict(res, home, db)
    return out


def promote(path, candidate_id: str, stdin_isatty: bool | None = None) -> dict:
    """Promotion per policies/promotion.yaml. Blocks when frozen, when the candidate is already decided, when the target needs a human and
    none is at the terminal, when the target has no evaluator, and when any required result - recomputed here from the stored evidence,
    never read from a flag - is missing or failed."""
    db, own = _db(path)
    try:
        home = db.path.parent
        if frozen(home):
            raise PromotionBlocked("kill switch FREEZE is present: challengers disabled, thresholds frozen")
        doc = get(db, candidate_id)
        if doc is None:
            raise PromotionBlocked(f"unknown candidate {candidate_id}")
        if doc["promotion_decision"] in ("promoted", "rolled_back", "rejected"):
            raise PromotionBlocked(f"candidate {candidate_id} is already {doc['promotion_decision']}; propose a new candidate")
        pol, target = policy(), doc["target_component"]
        human = is_human_tty(stdin_isatty)
        auto = target in pol.get("promote", {}).get("auto_allowed_for", []) and target not in pol.get("promote", {}).get("human_required_for", [])
        if not auto and not human:
            raise PromotionBlocked(f"{target} is promoted only by a human at a terminal")
        if auto and not human and not paths.config().get("auto_promotion_opt_in"):
            raise PromotionBlocked("auto-promotion needs the human opt-in `auto_promotion_opt_in` in config.json")
        if target not in EVALUATED:
            raise PromotionBlocked(f"no evaluator exists for {target}: its results cannot be computed, so it cannot be promoted by this code")
        verdicts = evaluate(db, doc, home)
        required = [*pol.get("promote", {}).get("requires", REQUIRED_RESULTS), "offline_replay_pass", *(["canary_ok"] if "canary_ok" in verdicts else [])]
        failing = [f"{k} ({verdicts[k][1]})" if k in verdicts else f"{k} (no evaluator computes it)" for k in required if not verdicts.get(k, (False,))[0]]
        if failing:
            raise PromotionBlocked(f"missing or failed required results: {'; '.join(failing)}")
        res = dict(doc.get("results") or {})
        if target == "question_template":
            qid = res["question_id"]
            before = ops.pins_of(home, qid).get("champion")
            ops.promote_challenger(home, qid)
            res["promotion"] = {"question_id": qid, "champion_before": before, "champion_after": ops.pins_of(home, qid).get("champion")}
        else:
            cal = home / "calibration"
            cal.mkdir(exist_ok=True)
            cur = _read_calibration(cal)
            res["promotion"] = {"group": res["group"], "previous_entry": cur.get(res["group"])}
            cur[res["group"]] = {"accept": res["accept"], **({"reject": res["reject"]} if res.get("reject") is not None else {}), "candidate": candidate_id}
            _write_calibration(cal, cur)
        doc["results"] = res
        doc["promotion_decision"], doc["rollout_scope"] = "promoted", "all"
        _save(db, doc, "human:terminal" if human else "runtime:auto", "promoted")
        return doc
    finally:
        if own:
            db.close()


def _read_calibration(cal: Path) -> dict:
    try:
        cur = json.loads((cal / "current.json").read_text())
    except (OSError, ValueError):
        return {}
    return cur if isinstance(cur, dict) else {}


def _write_calibration(cal: Path, cur: dict) -> None:
    tmp = cal / "current.json.tmp"
    tmp.write_text(json.dumps(cur, indent=1) + "\n")
    tmp.replace(cal / "current.json")


def rollback(path, candidate_id: str, actor: str | None = None, stdin_isatty: bool | None = None) -> dict:
    """Undo a candidate. A promoted question template goes back to the exact champion it replaced; a promoted threshold goes back to the
    band the group had before (or to the defaults). The actor recorded is the real one: `human:terminal` only at a human terminal, else
    `runtime:agent`; a caller cannot name itself human. Under FREEZE only a human at a terminal may roll back, a promotion a human made is reverted only by a human, and loosening a promoted
    stricter threshold also needs one (an agent may only make acceptance stricter)."""
    from . import thresholds

    db, own = _db(path)
    try:
        home = db.path.parent
        human = is_human_tty(stdin_isatty)
        who = "human:terminal" if human else ("runtime:agent" if not actor or actor.startswith("human") else actor)
        if frozen(home) and not human:
            raise PromotionBlocked("kill switch FREEZE is present: only a human at a terminal may roll back")
        doc = get(db, candidate_id)
        if doc is None:
            raise PromotionBlocked(f"unknown candidate {candidate_id}")
        if doc["promotion_decision"] == "rolled_back":
            raise PromotionBlocked(f"candidate {candidate_id} is already rolled back")
        res, promoted = dict(doc.get("results") or {}), doc["promotion_decision"] == "promoted"
        note = f"rolled back to {doc['rollback_reference']}"
        if doc["target_component"] == "question_template" and res.get("question_id"):
            qid, pin = res["question_id"], ops.pins_of(home, res["question_id"])
            promo = res.get("promotion")
            if promoted and promo:
                if pin.get("champion") != promo["champion_after"] or "previous_champion" not in pin or pin["previous_champion"] != promo["champion_before"]:
                    raise PromotionBlocked(f"the pins of {qid} no longer match this promotion (champion {pin.get('champion')}, expected {promo['champion_after']}); "
                                           "something else changed them, restore by hand")
                if pin.get("challenger"):
                    raise PromotionBlocked(f"a challenger (version {pin['challenger']}) is serving {qid} after this promotion; roll that candidate back first, "
                                           "so its canary is not cleared as a side effect of restoring the champion")
                promoter = next((e.get("actor", "") for e in reversed(doc.get("audit_trail") or []) if e.get("event") == "promoted"), "")
                if promoter.startswith("human") and not human:
                    raise PromotionBlocked("this promotion was made by a human at a terminal; only a human at a terminal reverts it")
                if ops.rollback(home, qid) != "champion_restored":
                    raise PromotionBlocked(f"the champion of {qid} was not restored")
                note = f"rolled back: champion restored to {promo['champion_before'] if promo['champion_before'] is not None else 'the registry default'}"
            elif promoted:
                raise PromotionBlocked("a promoted template with no recorded promotion cannot be rolled back automatically")
            else:
                canary, challenger = res.get("canary") or {}, pin.get("challenger")
                if challenger and canary.get("version") == challenger:
                    ops.rollback(home, qid)   # this candidate's own canary is serving: clear the challenger
                # a shadow or pending candidate owns no pin: nothing of the live rollout is touched
        if doc["target_component"] == "calibration_threshold" and promoted:
            cal = home / "calibration"
            cur = _read_calibration(cal)
            promo = res.get("promotion") or {}
            mine = {k: v for k, v in cur.items() if isinstance(v, dict) and v.get("candidate") == candidate_id}
            for grp, entry in mine.items():
                prev = promo.get("previous_entry") if grp == promo.get("group") else None
                fallback = prev["accept"] if isinstance(prev, dict) and prev.get("accept") is not None else thresholds.bands("noul", grp.split("/")[0], "low")[0]
                if fallback < entry["accept"] and not human:
                    raise PromotionBlocked(f"rolling back {grp} would loosen accept from {entry['accept']:.2f} to {fallback:.2f}; "
                                           "only a human at a terminal loosens a promoted threshold")
            for grp in mine:
                prev = promo.get("previous_entry") if grp == promo.get("group") else None
                if isinstance(prev, dict):
                    cur[grp] = prev
                else:
                    cur.pop(grp)
            if mine:
                _write_calibration(cal, cur)
        doc["promotion_decision"], doc["rollout_scope"] = "rolled_back", "offline"
        _save(db, doc, who, note)
        return doc
    finally:
        if own:
            db.close()


def start_canary(path, candidate_id: str, stdin_isatty: bool | None = None) -> dict:
    """Canary stage for a shadowed question template: serve the shadowed challenger version to the policy's traffic share. A human at a
    terminal may start it; otherwise it needs the human's standing opt-in (`auto_promotion_opt_in`, policies/promotion.yaml canary)."""
    db, own = _db(path)
    try:
        home = db.path.parent
        if frozen(home):
            raise PromotionBlocked("kill switch FREEZE is present: challengers disabled")
        doc = get(db, candidate_id)
        if doc is None:
            raise PromotionBlocked(f"unknown candidate {candidate_id}")
        pol, res = policy(), dict(doc.get("results") or {})
        if doc["target_component"] not in pol.get("canary", {}).get("scope", []) or doc["target_component"] != "question_template":
            raise PromotionBlocked(f"{doc['target_component']} has no canary stage")
        if doc["promotion_decision"] in ("promoted", "rolled_back", "rejected"):
            raise PromotionBlocked(f"candidate {candidate_id} is already {doc['promotion_decision']}")
        if pol.get("canary", {}).get("requires_human_opt_in") and not (is_human_tty(stdin_isatty) or paths.config().get("auto_promotion_opt_in")):
            raise PromotionBlocked("a canary needs a human at a terminal or the human opt-in `auto_promotion_opt_in` in config.json")
        ok, why = shadow_verdict(res, pol)
        if not ok:
            raise PromotionBlocked(f"shadow stage not passed: {why}")
        qid, version = res["question_id"], int(res["shadow"]["challenger_version"])
        ops.set_challenger(home, qid, version, ops.canary_fraction())
        res["canary"] = {"version": version, "fraction": ops.canary_fraction(), "started_at": ts()}
        doc["results"], doc["promotion_decision"], doc["rollout_scope"] = res, "canary", "canary"
        _save(db, doc, "human:terminal" if is_human_tty(stdin_isatty) else "runtime:auto", "stage:canary: challenger pinned")
        return doc
    finally:
        if own:
            db.close()


# ---- miner -----------------------------------------------------------------------------------------
def patterns(db: ledger.Ledger, days: int = 7) -> list[dict]:
    """Ledger patterns worth a candidate or an observation. Counts only; no state text leaves the ledger."""
    since = f"-{days} days"
    w = "ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?) AND COALESCE(primitive,'')<>'fast' AND " + ledger.live()   # `fast` rows from an older version are not Jev calls; tagged test rows are not live data (W-72)
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
    byp = db.one("SELECT COUNT(*) AS n FROM events WHERE kind='BYPASS' AND " + ledger.live() + " AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?)", (since,)).n
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
    base = sorted(x.latency_ms for x in db.all("SELECT latency_ms FROM jev_calls WHERE question_id=? AND latency_ms>0 AND dedup_of IS NULL "
                                               "AND exit_code IN (0,3,4) AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?)", (question_id, f"-{days} days")))
    payload = questions.build_noul(question_id)
    n = agree = champ_acc = chall_acc = errors = unsafe = 0
    cost, lats, disagreements = 0.0, [], []
    for r in rows:
        res = provider.ask(json.loads(r.state_text), {"q": payload})
        if not res.ok or "q" not in res.answers or adapter.check_answer(res.answers["q"], payload):
            errors += 1
            continue
        v = thresholds.judge(res.answers["q"], entry["task_family"], "low", version, res.resolved_model, primitive="noul")
        n += 1
        agree += v.exit_code == r.exit_code
        champ_acc += r.exit_code == 0
        chall_acc += v.exit_code == 0
        unsafe += r.exit_code == 4 and v.exit_code == 0
        cost += res.cost_usd or 0.0
        lats.append(res.latency_ms or 0.0)
        if v.exit_code != r.exit_code and len(disagreements) < 20:
            disagreements.append({"call_id": r.call_id, "champion_exit": r.exit_code, "challenger_exit": v.exit_code})
    lats.sort()
    pct = lambda xs: round(xs[int(0.95 * (len(xs) - 1))]) if xs else None  # noqa: E731
    out = {"question_id": question_id, "challenger_version": version, "n": n, "errors": errors, "agreement": round(agree / n, 3) if n else None,
           "champion_accept_rate": round(champ_acc / n, 3) if n else None, "challenger_accept_rate": round(chall_acc / n, 3) if n else None,
           "reject_to_accept": unsafe, "cost_usd": round(cost, 6), "p95_latency_ms": pct(lats), "champion_p95_latency_ms": pct(base),
           "disagreements": disagreements, "min_live_samples_met": n >= int(policy().get("shadow", {}).get("min_live_samples", 100)), "ran_at": ts()}
    prior = get(db, candidate_id) or {}
    started = (prior.get("results") or {}).get("shadow_started_at") or ts()
    attach_results(db, candidate_id, {"shadow": out, "question_id": question_id, "shadow_started_at": started}, stage="shadow", _computed=True)
    doc = get(db, candidate_id)
    if doc["promotion_decision"] == "pending":
        doc["promotion_decision"], doc["rollout_scope"] = "shadow", "shadow"
        _save(db, doc, "runtime:evaluator", "moved to shadow")
    return out


def replay_threshold(db: ledger.Ledger, candidate_id: str, group: str, accept: float, reject: float | None = None) -> dict:
    """Offline paired replay of a threshold change on dev + calibration labels (never the protected split), a holdout check on the
    validation split, and the Holm-corrected subgroup comparison. Stores the paired metrics; whether they pass is decided from them
    (`replay_verdict`) when the candidate is promoted."""
    from . import evals, thresholds

    if not _band_ok(accept, reject):
        raise ValueError("a band needs 0 < accept <= 1 and reject < accept")
    allrows = [r for r in evals.labeled(db) if thresholds.group_of(r["task_family"], r["question_version"], r["resolved_model"]) == group]
    rows = [r for r in allrows if r["split"] in ("dev", "calibration")]
    if not rows:
        return attach_results(db, candidate_id, {"replay": "no labeled rows in dev/calibration for this group", "group": group, "accept": accept, "reject": reject},
                              stage="offline_replay")
    cur_a, cur_r = thresholds.bands("noul", rows[0]["task_family"], "low", group)
    new_r = reject if reject is not None else cur_r
    preds, labels = [r["p"] for r in rows], [r["label"] for r in rows]
    champ, chall = _m(preds, labels, cur_a, cur_r), _m(preds, labels, accept, new_r)
    val = [r for r in allrows if r["split"] == "validation"]
    validation = ({"n": len(val), "champion": _m([r["p"] for r in val], [r["label"] for r in val], cur_a, cur_r),
                   "challenger": _m([r["p"] for r in val], [r["label"] for r in val], accept, new_r)} if val else None)
    res = {"group": group, "accept": accept, "reject": reject, "n": len(rows), "distinct_claims": evals.distinct_claims(rows), "champion": champ, "challenger": chall,
           "validation": validation, "worse_subgroups": _worse_than_champion(rows, cur_a, accept, policy()), "replayed_at": ts()}
    res["offline_replay_pass"] = replay_verdict(res, policy())[0]   # informational; the gate recomputes it
    return attach_results(db, candidate_id, res, stage="offline_replay", _computed=True)


def protected_check(db: ledger.Ledger, candidate_id: str, provider=None, stdin_isatty: bool | None = None, limit: int = 100) -> dict:
    """Evaluate a candidate on the protected split. Human terminal only (threat T10): the split is how a candidate is kept honest, so an
    agent session never reads it. A threshold is re-judged on the stored answers; a question template is re-asked of the challenger on the
    stored state of labeled calls. Stores the paired metrics; `promote` decides from them."""
    from . import adapter, evals, questions, thresholds

    if not is_human_tty(stdin_isatty):
        raise evals.ProtectedSetAccessDenied("the protected split is evaluated only by `jade improve protected` from a human terminal")
    doc = get(db, candidate_id)
    if doc is None:
        raise PromotionBlocked(f"unknown candidate {candidate_id}")
    res = doc.get("results") or {}
    if doc["target_component"] == "calibration_threshold":
        if not res.get("group") or not _band_ok(res.get("accept"), res.get("reject")):
            raise PromotionBlocked("run the offline replay of this threshold first")
        rows = [r for r in evals.labeled(db, include_protected=True) if r["split"] == "protected"
                and thresholds.group_of(r["task_family"], r["question_version"], r["resolved_model"]) == res["group"]]
        out = {"n": len(rows), "split": "protected"}
        if rows:
            cur_a, cur_r = thresholds.bands("noul", rows[0]["task_family"], "low", res["group"])
            preds, labels = [r["p"] for r in rows], [r["label"] for r in rows]
            out |= {"champion": _m(preds, labels, cur_a, cur_r), "challenger": _m(preds, labels, res["accept"], res["reject"] if res.get("reject") is not None else cur_r)}
    elif doc["target_component"] == "question_template":
        sh = res.get("shadow")
        if not isinstance(sh, dict):
            raise PromotionBlocked("run the shadow stage of this template first")
        qid, version = sh["question_id"], int(sh["challenger_version"])
        entry, payload = questions.get(qid, version), questions.build_noul(qid)
        provider = provider or adapter.OpenRouterProvider()
        champ_p, chall_p, labels = [], [], []
        for r in [r for r in evals.labeled(db, include_protected=True) if r["split"] == "protected" and r["question_id"] == qid][:limit]:
            row = db.one("SELECT state_text FROM jev_calls WHERE call_id=?", (r["call_id"],))
            if not row or not row.state_text:
                continue
            ans = provider.ask(json.loads(row.state_text), {"q": payload})
            if not ans.ok or "q" not in ans.answers or adapter.check_answer(ans.answers["q"], payload):
                continue
            champ_p.append(r["p"])
            chall_p.append(float(ans.answers["q"]["noul"]))
            labels.append(r["label"])
        out = {"n": len(labels), "split": "protected"}
        if labels:
            accept, reject = thresholds.bands("noul", entry["task_family"], "low", thresholds.group_of(entry["task_family"], version, None))
            out |= {"champion": _m(champ_p, labels, accept, reject), "challenger": _m(chall_p, labels, accept, reject)}
    else:
        raise PromotionBlocked(f"no evaluator exists for {doc['target_component']}")
    out["evaluated_at"] = ts()
    return attach_results(db, candidate_id, {"protected": out}, stage="protected", _computed=True)
