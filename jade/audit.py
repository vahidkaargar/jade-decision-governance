"""V6-29: the audit of calls that ran without a question, and the aging of standing answers by evidence.

Why. A call that runs from a standing answer (`memory.step`) is never labelled by anyone, so nobody can say how often such a call was wrong. A repeat is not evidence:
a call run 100 times unseen is not "proven". Only a call the human looked at afterwards is evidence. This module makes that look happen, and keeps it honest.

Stage 1 (the only stage that runs today): every call that runs from a standing answer is drawn for review with the stored chance `sample_prob` = 1.0, up to
`stage1_cap` (59) runs per scope, at most `weekly_count` per scope and `weekly_budget` in all per week. When a limit is reached the call is asked about, as before:
this is never a loosening. The human looks at the waiting calls in a terminal (`jade review mark`, no arguments) and says right or wrong. One wrong call retires the
answer and pauses the whole scope (`audit_scopes.status` = `suspended`) until the human confirms an answer of that scope again (`jade review confirm`): then the count
restarts at 0. A hint never lifts a pause.
Stage 2 (sampling with a chance below 1, a fixed weekly count) is built as a function (`stage2_prob`) and a `stage` column, and nothing sets the stage: lifting the cap is
the owner's step in V6-30.

The draw. It is made inside the hook, in the same transaction as the `memory_allowed` event, by a keyed hash (HMAC) of the scope and the decision id. The key is derived
from the permit seal key (`permit-seal.key`, mode 600, next to the ledger) with a fixed label, so it is protected by SI-10 as it is, with no new file name. The agent's
own user can read a file of that user, so this raises the bar and does not remove it (docs/05 T19), as for the permit seal. There is no environment variable, config
key or argument that sets the chance, the key or the outcome of a draw. If the key cannot be had, the call is asked about.
Each drawn row is sealed (HMAC over its fields). The readout counts only rows whose seal holds, so a row edited with SQL is never counted as a clean check.

Aging by evidence (`aged_out`, read by `memory.standing`, so lookup, `jade knows` and the proxy agree): a standing answer stops deciding after `age_runs` silent runs
with no mark, or `age_days` days after its last evidence (its confirm, or a `right` mark) once it has run silently. A `right` mark resets the count; it never lengthens
the old 30 and 180 day lifetime, which still apply in full. A row ages only toward asking more.

Config can only tighten: every number below may be lowered in the human's config (`"audit": {...}`) and never raised above the value in this file; anything else
(a string, a bool, a negative number, a number above the ceiling) is ignored. Raising a number is a change of code, which is the owner's decision (D-B9).
"""

from __future__ import annotations

import hmac
import math
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256

from . import paths
from .util import canonical, new_id, now, ts

LABEL = b"jade-audit-draw-v1"   # what the draw key is derived for: the permit seal key itself is never used to draw
STAGES = (1, 2)
P_MIN = 0.02   # the smallest chance a stage 2 draw may use: a scope with a huge use rate still gets looked at
ALPHA = 0.05


@dataclass(frozen=True)
class Settings:
    """The limits. The values below are the owner's defaults (D-B9: the owner picks the weekly count and the budget; these are the starting numbers, and the ceilings)."""
    weekly_count: int = 10     # drawn calls per scope per week (the existing weekly cap on labels, `evals.ADJUDICATION_PER_WEEK`)
    weekly_budget: int = 10    # drawn calls in all scopes per week: the one weekly budget of the owner's time
    stage1_cap: int = 59       # silent runs of a scope in stage 1: 59 clean checks show an error rate under 4.95% (95%, exact)
    age_runs: int = 60         # silent runs with no mark after which a standing answer needs evidence
    age_days: int = 90         # days after its last evidence after which a standing answer that has run silently needs evidence
    min_sessions: int = 10     # checks from fewer sessions are shown as counts, never as a rate


DEFAULTS = Settings()
_TUNABLE = ("weekly_count", "weekly_budget", "stage1_cap", "age_runs", "age_days")


def settings() -> Settings:
    """The limits in force: the defaults, lowered by `"audit"` in the human's config. Read on every call (a small file; `memory.standing` calls it only for a row that has run
    silently), so a changed config is never read stale. A value that is not a whole number from 1 up to the default is ignored."""
    cfg = paths.config().get("audit")
    cfg = cfg if isinstance(cfg, dict) else {}
    out = {}
    for name in _TUNABLE:
        v = cfg.get(name)
        ceiling = getattr(DEFAULTS, name)
        out[name] = v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= ceiling else ceiling
    return Settings(**out)


class Unavailable(RuntimeError):
    """The audit cannot be made now (no key). The call is asked about."""


# ---- the draw -------------------------------------------------------------------------------------------------------------
def _derive(seal_key: bytes) -> bytes:
    return hmac.new(seal_key, LABEL, sha256).digest()


def _key(db, create: bool) -> bytes | None:
    from . import permits

    try:
        seal = permits._seal_key(db, create=create)
    except (permits.PermitError, OSError) as e:   # a key that cannot be read or made: no draw, so the call is asked about
        raise Unavailable(str(e)) from e
    return _derive(seal) if seal is not None else None


def _read_key(db) -> bytes | None:
    """The draw key for a read: never made here. None when it does not exist or cannot be read: then no row can be verified, so none is counted."""
    try:
        return _key(db, create=False)
    except Unavailable:
        return None


def draw_value(key: bytes, scope: str, decision_id: str) -> float:
    """A number in [0, 1) that nobody without the key can predict or choose: the call is drawn when it is below its `sample_prob`."""
    return int.from_bytes(hmac.new(key, f"draw|{scope}|{decision_id}".encode(), sha256).digest()[:8], "big") / 2 ** 64


def _seal(key: bytes, r) -> str:
    fields = [r["audit_id"], r["decision_id"], r["answer_id"], r["scope_id"], int(r["stage"]), repr(float(r["sample_prob"])), r["drawn_ts"]]
    return hmac.new(key, b"seal|" + canonical(fields).encode(), sha256).hexdigest()


def scope_id(row) -> str:
    """The scope of an answer row: its scope key, else its kind key, else the exact call. One scope has one count, one stage and one pause."""
    for name in ("scope_key", "class_key", "family_key", "exact_key"):
        try:
            if row[name]:
                return str(row[name])
        except (KeyError, IndexError):
            continue
    raise ValueError("an answer row with no key")


def stage2_prob(settings_: Settings, weekly_runs: float) -> float:
    """Stage 2: the chance that gives `weekly_count` drawn calls a week for a scope that runs `weekly_runs` calls a week; never above 1, never below `P_MIN`."""
    return max(P_MIN, min(1.0, settings_.weekly_count / max(1.0, weekly_runs)))


@dataclass
class Admission:
    scope: str
    stage: int
    p: float
    key: bytes


def _live() -> str:
    return "COALESCE(source,'')<>'test'"


def _weekly_runs(db, scope: str) -> float:
    since = ts(now() - timedelta(days=14))
    n = db.con.execute("SELECT COUNT(*) FROM events WHERE kind='memory_allowed' AND ts>=? AND json_extract(detail_json,'$.scope_id')=? AND COALESCE(source,'')<>'test'", (since, scope)).fetchone()[0]
    return n / 2


def admit(db, row) -> tuple[Admission | None, str]:
    """May a call that matched this standing answer run without a question? (Admission, "") or (None, why not). Every refusal means: ask, as before."""
    sid, s = scope_id(row), settings()
    sc = db.con.execute("SELECT stage, status, runs FROM audit_scopes WHERE scope_id=?", (sid,)).fetchone()
    if sc is not None and sc["status"] == "suspended":
        return None, "suspended"
    stage = sc["stage"] if sc is not None else 1
    week = ts(now() - timedelta(days=7))
    if stage == 1:
        if (sc["runs"] if sc is not None else 0) >= s.stage1_cap:
            return None, "stage1_cap"
        if db.con.execute(f"SELECT COUNT(*) FROM audit_queue WHERE scope_id=? AND drawn_ts>=? AND {_live()}", (sid, week)).fetchone()[0] >= s.weekly_count:  # noqa: S608
            return None, "weekly_count"
        if db.con.execute(f"SELECT COUNT(*) FROM audit_queue WHERE drawn_ts>=? AND {_live()}", (week,)).fetchone()[0] >= s.weekly_budget:  # noqa: S608
            return None, "weekly_budget"
        p = 1.0
    else:
        p = stage2_prob(s, _weekly_runs(db, sid))
    try:
        key = _key(db, create=True)
    except Unavailable:
        return None, "no_key"
    if key is None:
        return None, "no_key"
    return Admission(sid, stage, p, key), ""


def refused(db, row, why: str) -> None:
    """One event a day per scope and reason, so that `jade review` can say why a call was asked about."""
    day = ts(now())[:10]
    db.event(scope_id(row), "audit_refused", guard=why, actor="hook", key=f"audit_refused:{scope_id(row)}:{why}:{day}", detail={"reason": why})


def record(db, adm: Admission, row, decision_id: str, session: str | None) -> bool:
    """After the call was let through (same transaction): count the run and, when the draw says so, queue it for review with its stored chance. True when drawn."""
    t = ts()
    db.con.execute("INSERT INTO audit_scopes(scope_id, stage, status, runs, since_ts, updated_ts, source) VALUES(?,?, 'active', 1, ?, ?, ?) "
                   "ON CONFLICT(scope_id) DO UPDATE SET runs=runs+1, updated_ts=excluded.updated_ts", (adm.scope, adm.stage, t, t, db.source))
    if draw_value(adm.key, adm.scope, decision_id) >= adm.p:
        return False
    r = {"audit_id": new_id("aud"), "decision_id": decision_id, "answer_id": row["answer_id"], "scope_id": adm.scope, "stage": adm.stage, "sample_prob": adm.p, "drawn_ts": t}
    db.con.execute("INSERT INTO audit_queue(audit_id, decision_id, answer_id, scope_id, rule_id, stage, sample_prob, status, session_id, drawn_ts, seal, source) VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?)",
                   (r["audit_id"], decision_id, r["answer_id"], adm.scope, row["rule_id"], adm.stage, adm.p, session, t, _seal(adm.key, r), db.source))
    return True


# ---- the verdict ----------------------------------------------------------------------------------------------------------
def suspend(db, scope: str, why: str) -> None:
    t = ts()
    db.con.execute("INSERT INTO audit_scopes(scope_id, stage, status, runs, since_ts, suspended_ts, updated_ts, source) VALUES(?,1,'suspended',0,?,?,?,?) "
                   "ON CONFLICT(scope_id) DO UPDATE SET status='suspended', suspended_ts=excluded.suspended_ts, updated_ts=excluded.updated_ts", (scope, t, t, t, db.source))
    db.event(scope, "audit_scope_suspended", guard=why, actor="human", detail={"reason": why})


def resume(db, scope: str) -> bool:
    """The human confirmed an answer of this scope in a terminal (`jade review confirm`): the pause ends and stage 1 starts again at 0. A hint never gets here."""
    cur = db.con.execute("UPDATE audit_scopes SET status='active', stage=1, runs=0, since_ts=?, suspended_ts=NULL, updated_ts=? WHERE scope_id=? AND status='suspended'", (ts(), ts(), scope))
    if cur.rowcount:
        db.event(scope, "audit_scope_resumed", guard="jade review confirm", actor="human")
    return bool(cur.rowcount)


def reset_evidence(db, answer_ids) -> int:
    """A `right` mark on a silent run of a standing answer: its count of runs with no mark starts again, and so does its evidence clock."""
    n = 0
    for aid in answer_ids:
        n += db.con.execute("UPDATE human_answers SET silent_runs=0, evidence_ts=? WHERE answer_id=? AND strength='strong'", (ts(), aid)).rowcount
    return n


def on_mark(db, decision_id: str, mark: str, answer_ids) -> dict:
    """`jade review mark` on a call that ran from a standing answer. `right`: evidence for the answer, and the queued row (if any) is checked. `wrong`: the scope is
    paused. A queued row that is wrong stays wrong (a later `right` does not clear it). The label is written from the stored draw row, never from caller input."""
    from . import evals

    out = {"scopes": []}
    scopes = set()
    for aid in answer_ids:
        r = db.con.execute("SELECT * FROM human_answers WHERE answer_id=?", (aid,)).fetchone()
        if r is not None:
            scopes.add(scope_id(r))
    q = db.con.execute("SELECT * FROM audit_queue WHERE decision_id=?", (decision_id,)).fetchone()
    if q is not None:
        scopes.add(q["scope_id"])
        key = _read_key(db)
        sealed = key is not None and hmac.compare_digest(q["seal"], _seal(key, q))
        if sealed and q["status"] != "wrong":
            db.con.execute("UPDATE audit_queue SET status=?, resolved_ts=? WHERE decision_id=?", (mark, ts(), decision_id))
            evals.set_label(db, decision_id, 1 if mark == "right" else 0, "adjudicated", f"audit of a silent run: {mark}", label_type="owner_would_allow",
                            sample_prob=q["sample_prob"], audit_source="random_audit")
        out["queued"] = True
        out["sealed"] = sealed
    if mark == "right":
        out["reset"] = reset_evidence(db, answer_ids)
    else:
        for sid in sorted(scopes):
            suspend(db, sid, f"a call of this scope was marked wrong ({decision_id})")
            out["scopes"].append(sid)
    return out


# ---- aging ----------------------------------------------------------------------------------------------------------------
def aged_out(r) -> bool:
    """Does this standing answer need evidence before it decides again? True after `age_runs` silent runs with no mark, or when it has run silently and its last evidence
    (its confirm, or a `right` mark) is older than `age_days`. Only ever makes `memory.standing` stricter."""
    runs = r["silent_runs"] if "silent_runs" in r.keys() else None
    if not runs:
        return False
    s = settings()
    if runs >= s.age_runs:
        return True
    evid = (r["evidence_ts"] if "evidence_ts" in r.keys() else None) or (r["confirmed_ts"] if "confirmed_ts" in r.keys() else None)
    return bool(evid) and evid < ts(now() - timedelta(days=s.age_days))


# ---- the numbers ----------------------------------------------------------------------------------------------------------
def upper_bound(k: int, n: int, alpha: float = ALPHA) -> float:
    """The exact one-sided (1 - alpha) upper limit of an error rate that showed k wrong in n checks (Clopper-Pearson). With no wrong: 1 - alpha^(1/n), so 59 clean
    checks give 4.95%. (The rule of three, 3/n, gives 5.08% at 59: it is a little too wide, and not the number of the plan.)"""
    if n <= 0:
        return 1.0
    if k <= 0:
        return 1.0 - alpha ** (1.0 / n)
    if k >= n:
        return 1.0

    def cdf(p: float) -> float:   # the chance of k or fewer wrong in n checks when the rate is p; it falls as p rises, and the limit is where it equals alpha
        lp, lq = math.log(p), math.log1p(-p)
        return sum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq) for i in range(k + 1))

    lo, hi = k / n, 1.0 - 1e-12
    for _ in range(80):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def estimate(rows) -> dict:
    """The false-allow rate of the checked rows, each weighted by 1/sample_prob (a call drawn with a small chance stands for many calls that were not drawn). `n_eff` is
    the effective number of checks (Kish): equal chances give the plain count, unequal ones fewer, so the bound is wider and never narrower than the plain one."""
    w = [1.0 / r["sample_prob"] for r in rows]
    if not w:
        return {"n": 0, "wrong": 0, "rate": None, "upper": None, "n_eff": 0.0}
    sw, sw2 = sum(w), sum(x * x for x in w)
    wrong_w = sum(x for x, r in zip(w, rows, strict=True) if r["status"] == "wrong")
    rate = wrong_w / sw
    n_eff = sw * sw / sw2
    k_eff = rate * n_eff
    return {"n": len(rows), "wrong": sum(1 for r in rows if r["status"] == "wrong"), "rate": rate, "n_eff": n_eff,
            "upper": upper_bound(math.ceil(k_eff - 1e-9), max(1, math.floor(n_eff + 1e-9)))}


def summary(db, since: str = "") -> dict:
    """What the audit shows: counts, and the false-allow rate only when the checks come from enough sessions. Rows whose seal fails are counted apart and never used."""
    rows = db.con.execute(f"SELECT * FROM audit_queue WHERE drawn_ts>=? AND {_live()} ORDER BY drawn_ts, rowid", (since,)).fetchall()  # noqa: S608
    key = _read_key(db) if rows else None
    good = [r for r in rows if key is not None and hmac.compare_digest(r["seal"], _seal(key, r))]
    checked = [r for r in good if r["status"] in ("right", "wrong")]
    sessions = len({r["session_id"] for r in checked if r["session_id"]})
    est = estimate(checked)
    s = settings()
    return {"drawn": len(rows), "unsealed": len(rows) - len(good), "waiting": sum(1 for r in good if r["status"] == "pending"), "checked": len(checked),
            "wrong": est["wrong"], "sessions": sessions, "min_sessions": s.min_sessions, "weighted": any(r["sample_prob"] < 1 for r in checked),
            "rate": est["rate"] if sessions >= s.min_sessions else None, "upper": est["upper"] if sessions >= s.min_sessions else None, "n_eff": est["n_eff"],
            "paused": db.con.execute("SELECT COUNT(*) FROM audit_scopes WHERE status='suspended'").fetchone()[0]}


def pending(db, limit: int):
    """The calls waiting for the human's check, oldest first, with the answer text they saw (a row whose seal fails is not offered)."""
    key = _read_key(db)
    if key is None:
        return []
    out = []
    for r in db.con.execute("SELECT q.*, a.label AS label FROM audit_queue q LEFT JOIN human_answers a ON a.answer_id=q.answer_id WHERE q.status='pending' AND COALESCE(q.source,'')<>'test' ORDER BY q.drawn_ts, q.rowid"):
        if hmac.compare_digest(r["seal"], _seal(key, r)):
            out.append(r)
            if len(out) >= limit:
                break
    return out


def lines(db, since: str, show) -> list[str]:
    """The `jade review` lines for the audit. Nothing when no call was ever drawn and no scope is paused. `show` makes a stored label safe to print on one line."""
    s = summary(db, since)
    if not s["drawn"] and not s["paused"]:
        return []
    cfg = settings()
    out = [f"audit of calls that ran without asking you: {s['drawn']} drawn for your check, {s['checked']} checked ({s['checked'] - s['wrong']} right, {s['wrong']} wrong), "
           f"{s['waiting']} waiting; {s['paused']} scope(s) paused after a wrong call"]
    if s["unsealed"]:
        out.append(f"  {s['unsealed']} audit row(s) do not match their seal (edited after they were drawn): they are not counted")
    if s["rate"] is None:
        out.append(f"  false-allow rate: not shown until the checks come from {s['min_sessions']} sessions (now {s['sessions']}); only the counts above are known")
    else:
        out.append(f"  false-allow rate: {100 * s['rate']:.1f}% of calls like these were wrong; we are 95% sure it is below {100 * s['upper']:.2f}% "
                   f"(from {s['checked']} checks in {s['sessions']} sessions{', each weighted by 1 over its chance of being drawn' if s['weighted'] else ''})")
    out.append(f"  your time: at most {cfg.weekly_count} checks a week for one scope and {cfg.weekly_budget} in all; when the limit is reached the next call is asked about instead")
    for r in pending(db, cfg.weekly_count):
        out.append(f"  waiting  {r['decision_id']}  [{r['rule_id']}]  {show(r['label'] or '')}".rstrip())
    if s["waiting"]:
        out.append("  check them in your terminal: jade review mark")
    return out
