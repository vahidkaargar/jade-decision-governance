"""V6-23: read-only numbers from the ledger, for `jade ledger stats`, `jade check --json` and `jade review`.

They measure the load on the human (how often JADE asked, how long the answer took, how many asks ran out), how much of the evidence is linked and labelled, and how
often Jev's shadow answer agreed with the owner. Nothing here writes a row, changes a decision or reads command text: counts, rule ids and times only. Rows a
`JADE_SOURCE=test` run wrote are left out (`ledger.live`). A window is an ISO time (`since`); "" means all time.
"""

from __future__ import annotations

import math
from datetime import timedelta

from . import ledger
from .util import now, parse_ts, ts

NOT_MEASURED = "not measured"
ANSWERED_ALLOW = ("ALLOWED", "CONSUMED")   # an Allow that expired unused is still an answer: see `_kind`
UNANSWERED_OVER = ("EXPIRED", "GAVE_UP")
STILL_WAITING = ("PENDING", "SHOWN", "DIALOG")
VERIFY_QID = "q.verify_claim.supported_by_evidence"
SHADOW_PAIR_S = 120   # the Jev call of the proxy comes a moment before the ask it did not replace; an event older than this does not belong to the ask


def since_days(days: float) -> str:
    return ts(now() - timedelta(days=days))


def since_seconds(seconds: float | None) -> str:
    return "" if seconds is None else ts(now() - timedelta(seconds=seconds))


def pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _nearest_rank(vals: list[float], q: float) -> float | None:
    return vals[max(0, math.ceil(q * len(vals)) - 1)] if vals else None


# ---- the load counters, reconciled --------------------------------------------------------------------
def load_counts(db, since: str) -> dict:
    """How many calls the gate handled, counted one way for `jade check` and `jade ledger stats` alike (V6-23, U-10).

    The two counters disagreed because each counted a different set of events. `jade check` counted events `gated` and `safety_block`. `jade ledger stats` counted `gated`
    only. A call the gate stopped for SI-07 (an earlier identical run still held) writes `gated` first and `safety_block` after it, so `jade check` counted it twice.
    A call stopped by an invariant before the permit step (SI-08 and others) writes `safety_block` only, so `jade ledger stats` did not count it at all.
    The one definition: `calls` = every `gated` event + every `safety_block` event that is not the second event of an SI-07 hold. The parts are printed beside it."""
    live = ledger.live()
    gated = db.one(f"SELECT COUNT(*) AS n FROM events WHERE kind='gated' AND ts>=? AND {live}", (since,)).n  # noqa: S608 - constant fragment
    hold = db.one(f"SELECT COUNT(*) AS n FROM events WHERE kind='safety_block' AND guard LIKE '% SI-07' AND ts>=? AND {live}", (since,)).n  # noqa: S608
    invariant = db.one(f"SELECT COUNT(*) AS n FROM events WHERE kind='safety_block' AND COALESCE(guard,'') NOT LIKE '% SI-07' AND ts>=? AND {live}", (since,)).n  # noqa: S608
    return {"calls": gated + invariant, "gated_events": gated, "invariant_blocks_without_gated_event": invariant, "si07_blocks_counted_once_in_gated": hold}


def gated_rows(db, since: str) -> list:
    """The detail of every call `load_counts` counts: the `gated` events and the invariant blocks, not the SI-07 repeat."""
    return db.all(f"SELECT kind, detail_json FROM events WHERE kind IN ('gated','safety_block') AND COALESCE(guard,'') NOT LIKE '% SI-07' AND ts>=? AND {ledger.live()}", (since,))  # noqa: S608


# ---- asks: how often, how long, how many ran out --------------------------------------------------------
def _kind(status: str, answered: bool, expires_ts: str, now_s: str) -> str:
    """allowed | denied | expired (never answered, ended) | open (still waiting)."""
    if status in ANSWERED_ALLOW or (status == "EXPIRED" and answered):
        return "allowed"
    if status == "DENIED":
        return "denied"
    if status in UNANSWERED_OVER or (status in STILL_WAITING and expires_ts < now_s):   # the end of an ask is written lazily: a waiting row past its time is over
        return "expired"
    return "open"


def ask_load(db, since: str) -> dict:
    """What JADE asked the human since `since`: per rule and in all, how many were allowed, denied, never answered or still open, the approve rate among the answered,
    and the seconds from the ask to the answer. The ask time is when the hook wrote the ask (the dialog is shown at once; the chat shows it after the agent calls the
    question tool, so a chat answer includes that wait). Times have one-second resolution."""
    now_s = ts()
    rows = db.all(f"SELECT rule_id, status, created_ts, answered_ts, expires_ts FROM asks WHERE created_ts>=? AND {ledger.live()}", (since,))  # noqa: S608 - constant fragment
    by_rule: dict[str, dict] = {}
    waits: dict[str, list[float]] = {}
    total = {"asked": 0, "allowed": 0, "denied": 0, "expired": 0, "open": 0}
    for r in rows:
        kind = _kind(r.status, r.answered_ts is not None, r.expires_ts or "", now_s)
        row = by_rule.setdefault(r.rule_id, {"asked": 0, "allowed": 0, "denied": 0, "expired": 0, "open": 0})
        for d in (row, total):
            d["asked"] += 1
            d[kind] += 1
        if kind in ("allowed", "denied") and r.answered_ts:
            waits.setdefault(r.rule_id, []).append(max(0.0, (parse_ts(r.answered_ts) - parse_ts(r.created_ts)).total_seconds()))
    for rule, row in by_rule.items():
        w = sorted(waits.get(rule, []))
        row["approve_rate"] = pct(row["allowed"], row["allowed"] + row["denied"])
        row["answer_seconds"] = {"n": len(w), "p50": _nearest_rank(w, 0.5), "max": w[-1] if w else None}
    every = sorted(x for w in waits.values() for x in w)
    total["approve_rate"] = pct(total["allowed"], total["allowed"] + total["denied"])
    total["answer_seconds"] = {"n": len(every), "p50": _nearest_rank(every, 0.5), "p95": _nearest_rank(every, 0.95), "max": every[-1] if every else None}
    total["by_rule"] = dict(sorted(by_rule.items()))
    return total


# ---- verify calls: linked and labelled ----------------------------------------------------------------
def verify_links(db, since: str) -> dict:
    """The Jev verify calls (the main question of `jade ask verify`, not its companion) and how many carry a decision. `decisions_without_a_call` is the other side:
    decisions no Jev call was ever attached to (165 of them in the first measurement)."""
    live_c, live_d = ledger.live("c"), ledger.live("d")
    r = db.one(f"SELECT COUNT(*) AS n, COALESCE(SUM(c.decision_id IS NOT NULL),0) AS linked FROM jev_calls c WHERE c.question_id=? AND c.ts>=? AND {live_c}", (VERIFY_QID, since))  # noqa: S608
    bare = db.one(f"SELECT COUNT(*) AS n FROM decisions d WHERE d.created_at>=? AND {live_d} AND NOT EXISTS (SELECT 1 FROM jev_calls c WHERE c.decision_id=d.decision_id)", (since,)).n  # noqa: S608   # `calls_decision` answers each lookup
    return {"verify_calls": r.n, "linked_to_a_decision": r.linked, "linked_share_percent": pct(r.linked, r.n), "decisions_without_a_call": bare}


def label_coverage(db, since: str) -> dict:
    """How much of the accepted-or-answered verify evidence has a label a fit may use (type `claim_true`, a source that is not `heuristic`). The counts by source and by
    label type say where the labels come from; the three label types are counted apart and are never added together."""
    from . import evals

    live_c = ledger.live("c")
    calls = db.one(f"SELECT COUNT(*) AS n FROM jev_calls c WHERE c.question_id=? AND c.primitive='noul' AND c.p IS NOT NULL AND c.dedup_of IS NULL AND c.ts>=? AND {live_c}", (VERIFY_QID, since)).n  # noqa: S608
    fit = db.one(f"SELECT COUNT(*) AS n FROM jev_calls c JOIN labels l ON l.call_id=c.call_id WHERE c.question_id=? AND c.primitive='noul' AND c.p IS NOT NULL AND c.dedup_of IS NULL "  # noqa: S608
                 f"AND c.ts>=? AND l.source<>'heuristic' AND {evals.FIT_TYPE_SQL} AND {live_c}", (VERIFY_QID, since)).n
    heur = db.one(f"SELECT COUNT(*) AS n FROM jev_calls c JOIN labels l ON l.call_id=c.call_id WHERE c.question_id=? AND c.primitive='noul' AND c.p IS NOT NULL AND c.dedup_of IS NULL "  # noqa: S608
                  f"AND c.ts>=? AND l.source='heuristic' AND {live_c}", (VERIFY_QID, since)).n
    by_source = {r.source: r.n for r in db.all(f"SELECT l.source, COUNT(*) AS n FROM labels l JOIN jev_calls c ON c.call_id=l.call_id WHERE c.ts>=? AND {live_c} GROUP BY l.source", (since,))}  # noqa: S608
    by_type = {r.t: r.n for r in db.all(f"SELECT COALESCE(l.label_type,'claim_true') AS t, COUNT(*) AS n FROM labels l JOIN jev_calls c ON c.call_id=l.call_id WHERE c.ts>=? AND {live_c} GROUP BY t", (since,))}  # noqa: S608
    return {"verify_calls": calls, "fit_labelled": fit, "heuristic_only": heur, "unlabelled": max(0, calls - fit - heur), "fit_coverage_percent": pct(fit, calls),
            "labels_by_source": by_source, "labels_by_type": by_type}


# ---- Jev's shadow answer against the owner's ----------------------------------------------------------
def shadow_agreement(db, since: str) -> dict:
    """For each ask the owner answered, the answer Jev's proxy gave to the same call (question `q.human_proxy.would_allow`), when the proxy was asked. The pair is found
    from rows that already exist: the ask (session, hold key), the `jev_asked` event of that pair just before it, and the Jev call of that session between the two.
    Jev 'allows' with exit code 0; abstain and reject (3, 4) count as not allowing; no answer (5, 6) is counted apart.

    owner_denies: asks the owner denied that Jev was also asked about. jev_caught: of those, Jev did not allow. jev_false_allows: Jev allowed what the owner denied.
    Recall is jev_caught over owner_denies, with its 95% lower bound; with no owner deny it is not measured (never 100%). A call Jev let run in the owner's place
    never reached an ask, so it is not in this join: its audit is `jade review mark`."""
    from . import evals, proxy

    pairs = db.all(f"SELECT ask_id, session_id, hold_key, status, created_ts, answered_ts, expires_ts FROM asks WHERE created_ts>=? AND {ledger.live()}", (since,))  # noqa: S608
    now_s = ts()
    out = {"asks_answered": 0, "joined": 0, "owner_denies": 0, "jev_caught": 0, "jev_false_allows": 0, "jev_no_answer_on_denies": 0,
           "owner_allows": 0, "jev_agreed_allows": 0, "jev_did_not_allow_on_allows": 0, "jev_no_answer_on_allows": 0}
    for p in pairs:
        kind = _kind(p.status, p.answered_ts is not None, p.expires_ts or "", now_s)
        if kind not in ("allowed", "denied"):
            continue
        out["asks_answered"] += 1
        tried = db.one("SELECT ts FROM events WHERE kind='jev_asked' AND entity_id=? AND ts<=? ORDER BY id DESC LIMIT 1", (proxy._attempt_key(p.session_id, p.hold_key), p.created_ts))  # noqa: SLF001
        if tried is None or (parse_ts(p.created_ts) - parse_ts(tried.ts)).total_seconds() > SHADOW_PAIR_S:
            continue
        call = db.one(f"SELECT exit_code FROM jev_calls WHERE question_id=? AND session_id=? AND ts>=? AND ts<=? AND state='DONE' AND {ledger.live()} ORDER BY rowid DESC LIMIT 1",  # noqa: S608
                      (proxy.QID, p.session_id, tried.ts, p.created_ts))
        if call is None:
            continue
        out["joined"] += 1
        said = "allow" if call.exit_code == 0 else "no_answer" if call.exit_code in (5, 6) else "not_allow"
        if kind == "denied":
            out["owner_denies"] += 1
            if said == "allow":
                out["jev_false_allows"] += 1
            elif said == "not_allow":
                out["jev_caught"] += 1
            else:
                out["jev_no_answer_on_denies"] += 1
        else:
            out["owner_allows"] += 1
            out["jev_agreed_allows" if said == "allow" else "jev_did_not_allow_on_allows" if said == "not_allow" else "jev_no_answer_on_allows"] += 1
    d = out["owner_denies"]
    out["recall_percent"] = pct(out["jev_caught"], d)
    out["recall_lower_95_percent"] = round(100 * evals.wilson(out["jev_caught"], d)[0], 1) if d else None
    out["recall_note"] = f"recall {NOT_MEASURED}: the owner has denied no call that Jev was also asked about" if not d else ""
    return out


# ---- plain words for `jade review` --------------------------------------------------------------------
def _secs(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:.0f} s" if x < 120 else f"{x / 60:.0f} min"


def friction_lines(db, since: str, window: str, marked: int, wrong: int, fit_labels: int) -> list[str]:
    """What JADE cost the human in `window`, in plain words, and what is known about whether the answers were right. Always says which: friction is not correctness."""
    a = ask_load(db, since)
    lines = []
    if a["asked"]:
        answered = a["allowed"] + a["denied"]
        lines.append(f"Questions JADE asked you ({window}): {a['asked']}. You allowed {a['allowed']}, denied {a['denied']}, and {a['expired']} ran out with no answer"
                     + (f" ({a['open']} still waiting)." if a["open"] else "."))
        if answered:
            lines.append(f"You allowed {a['approve_rate']:g}% of the questions you answered. This does not say whether those answers were right.")
            s = a["answer_seconds"]
            lines.append(f"Time to answer: half of the answers took {_secs(s['p50'])} or less, 19 of 20 took {_secs(s['p95'])} or less, the longest {_secs(s['max'])}.")
        for rule, r in a["by_rule"].items():
            lines.append(f"  rule {rule}: asked {r['asked']}, allowed {r['allowed']}, denied {r['denied']}, no answer {r['expired']}"
                         + (f", median time to answer {_secs(r['answer_seconds']['p50'])}" if r["answer_seconds"]["n"] else ""))
    else:
        lines.append(f"Questions JADE asked you ({window}): none recorded.")
    if marked or fit_labels:
        lines.append(f"Correctness, as far as it is measured: {marked} run(s) you marked ({wrong} wrong) and {fit_labels} Jev answer(s) with a checked label.")
    else:
        lines.append("Correctness is not measured: no run is marked right or wrong, and no Jev answer has a checked label. The numbers above are about effort, not about being right.")
    return lines
