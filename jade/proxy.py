"""Jev decides in the owner's place, inside the hook (docs/12, plan v3, PR-7).

With the `jev` key off (`jade jev off`; the default at level high, V6-28) nothing here runs and nothing goes to a model, shadow included.

At level low or medium a plain high call that neither a terminal permit nor a confirmed answer covers goes to Jev with the owner's written preferences, the
confirmed answers they gave to calls of the same rule that still count, and their denies of that rule. An accept in the level's band lets the call run, through a decision walked to EXECUTING (so SI-07 still holds
an unknown outcome). Everything else is the owner's question as before: an abstain, a reject, an unavailable or late provider, a call that is skipped. A verdict that is
not an accept is never a yes.

Bands come from `policies/escalation.yaml` (family `human_proxy`): the autonomy level sets the risk the question is judged at, so low accepts at 0.70 and medium
at 0.85. Until the deny gate (`deny_gate`) is met Jev is shadow: it is asked and recorded and decides nothing. The gate counts the owner's denies (at least 30 kinds of
call from at least 3 rules, at least 100 completed shadow questions over at least 7 days, no false allow), not answers: a wall of allow clicks says nothing about a
model. The shadow questions count from the first question asked under this version (the `jev_sample_start` event): an older answer of Jev cannot be checked against
the owner's denies. Once the gate is met, Jev decides a plain high call inside its band at level low or medium while the `jev` key is on; `jade jev off` stops it. At
level high it is shadow too and its advice is not shown.

Never sent (always the owner's question):
- a human-only class, a blocked call, a critical call (only level high calls are sent), fetch-and-exec (R-22), egress (R-21, at every level, V6-28) and writes to
  agent instruction files (R-13, R-18), also when the rule sits in one segment of a longer command;
- a merge, a call the gate could not read (any segment), a secret-shaped value, text that redaction changed;
- a program or a write over 4 KiB (the whole content of a write is shown to Jev, never a part), more than one inline program;
- a call the owner denied before (that call, or a call of its kind), and a call Jev has already been asked about twice this hour in this session (a reworded comment is the same call);
- a call with neither a preferences text nor a confirmed answer of its rule.

The provider call runs in a daemon thread that the hook waits for at most `DEADLINE_S` (and never past the hook's own budget); a hung exchange leaves the thread behind,
its row is closed, and the owner is asked. The answer is never taken from the dedup cache: every accept is a fresh sample of this call.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from . import adapter, ask, ledger, memory, paths
from .util import canonical, now, parse_ts, sha256, ts

QID = "q.human_proxy.would_allow"
MIN_VERSION = 2           # the first version whose template reads `past_answers` as confirmed allows and denies without labels (RR11, RR12)
DEADLINE_S = 3.0          # the most one exchange may take; the hook budget (hooks.BUDGET_S) is the other limit
RESERVE_S = 1.5           # what the rest of the hook still needs after Jev answered
MIN_DEADLINE_S = 0.1
JOIN_SLACK_S = 0.5
MAX_TEXT = 4096
MAX_PAST = 8
MAX_ATTEMPTS = 2          # questions to Jev about one call (by hold key) per session and hour: a comment added to the command does not make it a new call
ATTEMPT_WINDOW_S = 3600
NEVER_RULES = frozenset({"R-22", "R-13", "R-18", "R-21"})   # R-13 and R-18: the files an agent reads its instructions from (AGENTS.md, CLAUDE.md, skills): a model judging them is the injection surface. R-21 is egress, at every level (V6-28, H20: a tightening; before it Jev decided egress at level low at 0.85): where a call sends data is the owner's question


def make_provider(deadline_s: float):
    """The provider of one in-hook call: one try, no retry (a rate limit is the owner's question, not a wait)."""
    return adapter.OpenRouterProvider(deadline_s=deadline_s, max_retries=0)


def preferences() -> str:
    """The owner's preferences text: `preferences.md` in the config dir without its comment lines, at most 4 KiB."""
    try:
        text = (paths.config_dir() / "preferences.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    return "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))[:MAX_TEXT]


def past_answers(db, rule_id: str) -> list[dict]:
    """What the owner answered to plain calls of this rule: the confirmed (strong) allows that `memory.lookup` still honours (`memory.honoured_allows`: a newer deny
    that shares a key with every call the allow covers takes it out), newest first, then the owner's denies of the rule, newest first, each list at most MAX_PAST.
    Unconfirmed allow hints and human-only answers are never sent. An allow carries its label, which the owner read in `jade review confirm`. A deny is never
    confirmed, so its label is text the agent wrote (a comment in the command, a host, a path of a call kept off the network): it stays in the ledger, and the deny
    is sent as its rule, the answer and its age in whole days only."""
    allows = memory.honoured_allows(db, "rule_id=? AND COALESCE(ho,0)=0", [rule_id], limit=MAX_PAST)
    denies = db.con.execute(f"SELECT rule_id, ts FROM human_answers WHERE {memory.DENY_SQL} AND rule_id=? AND COALESCE(ho,0)=0 ORDER BY ts DESC, rowid DESC LIMIT ?",  # noqa: S608
                            (rule_id, MAX_PAST)).fetchall()
    return ([{"rule": r["rule_id"], "answer": "allow", "label": (r["label"] or "")[:120]} for r in allows]
            + [{"rule": r["rule_id"], "answer": "deny", "age_days": _age_days(r["ts"])} for r in denies])


def _age_days(stamp: str | None) -> int | None:
    try:
        return max(0, (now() - parse_ts(stamp)).days) if stamp else None
    except (TypeError, ValueError):
        return None


def owner_answers(db) -> int:
    """Distinct plain-high calls the owner answered (human-only answers, retired answers and repeats of one call do not count). It only reports how much the owner has
    answered: since V6-28 it gates nothing, because a wall of allow clicks passed the old gate of 50 and an always-allow model would agree with all of them."""
    return db.con.execute("SELECT COUNT(DISTINCT exact_key) FROM human_answers WHERE COALESCE(ho,0)=0 AND strength<>'revoked' AND " + ledger.LIVE.format(a="")).fetchone()[0]


# The deny gate (V6-28; Q-08, not yet answered by the owner: these are the numbers of the card). It counts what separates a model from a yes-sayer: the owner's denies.
MIN_DENIES = 30          # distinct kinds of call the owner denied
MIN_DENY_RULES = 3       # ... from at least this many rules
MIN_SAMPLES = 100        # completed shadow questions to Jev ...
MIN_SAMPLE_DAYS = 7      # ... that span at least this many days
FALSE_ALLOWS_ALLOWED = 0
WINDOW_EVENT = "jev_sample_start"   # written once, at the first question to Jev under this version: shadow questions before it have no `jev_accepted` link and do not count


def false_allows(db) -> int:
    """Calls where Jev said ACCEPT and the owner then denied that call or its kind, or marked the Jev yes wrong. Shadow or decided, both count: the
    `jev_accepted` event is written at every accept, and `proxy.step` skips a call the owner denied earlier, so a match is a miss that Jev made first. A yes that Jev
    decided before the `jev_accepted` event existed has a `jev_allowed` event with the same keys, so it is read too. One call with both events counts once. The keys
    are kept by retention (`ops.purge`): the count must not fall to zero because 90 days passed."""
    live = ledger.LIVE.format(a="e.")
    return db.con.execute(
        "SELECT COUNT(DISTINCT e.entity_id) FROM events e WHERE e.kind IN ('jev_accepted','jev_allowed') AND " + live + " AND ("   # noqa: S608 - fixed text
        " EXISTS (SELECT 1 FROM asks a WHERE a.status='DENIED' AND a.session_id=json_extract(e.detail_json,'$.session') AND a.hold_key=json_extract(e.detail_json,'$.hold'))"
        " OR EXISTS (SELECT 1 FROM human_answers h WHERE h.answer='deny' AND h.strength IN ('hint','strong') AND COALESCE(h.source,'')<>'test' AND ("
        "   h.exact_key=json_extract(e.detail_json,'$.exact_key') OR (h.class_key IS NOT NULL AND h.class_key=json_extract(e.detail_json,'$.class_key'))))"
        " OR EXISTS (SELECT 1 FROM events d WHERE d.kind='jev_distrust' AND d.entity_id IN (json_extract(e.detail_json,'$.exact_key'), json_extract(e.detail_json,'$.class_key'),"
        "   json_extract(e.detail_json,'$.coarse_key'))))").fetchone()[0]


def deny_gate(db) -> dict:
    """Whether the owner's own record is enough for a model to be allowed to decide (V6-28). Counts, never answers: distinct kinds of call the owner denied, the rules
    those denies come from, the completed shadow questions (asked since `WINDOW_EVENT`) and the days they span, and the false allows. `ok` is what lets Jev decide at level
    low or medium while the `jev` key is on; no second human step follows it (a separate "decide" switch is an open owner choice, LD-08, V6-31). `recall_bound` is the 95% lower bound of Jev's share of the owner's denies
    that it would also have denied when it missed none (0.05 ** (1 / denies): 30 denies bound it at about 90%, 100 at about 97%); None when there is a miss or no deny."""
    live = ledger.LIVE.format(a="")
    denied, rules = db.con.execute(f"SELECT COUNT(DISTINCT COALESCE(class_key, exact_key)), COUNT(DISTINCT rule_id) FROM human_answers WHERE {memory.DENY_SQL} "  # noqa: S608
                                   f"AND COALESCE(ho,0)=0 AND {live}").fetchone()
    samples, first = db.con.execute(f"SELECT COUNT(*), MIN(ts) FROM jev_calls WHERE question_id=? AND state='DONE' AND verdict IS NOT NULL AND dedup_of IS NULL "  # noqa: S608
                                    f"AND error_kind IS NULL AND {live} AND ts>=(SELECT MIN(ts) FROM events WHERE kind=?)", (QID, WINDOW_EVENT)).fetchone()
    try:
        days = (now() - parse_ts(first)).total_seconds() / 86400 if first else 0.0
    except (TypeError, ValueError):
        days = 0.0
    wrong = false_allows(db)
    need = []
    if denied < MIN_DENIES:
        need.append(f"{denied} of {MIN_DENIES} owner denies")
    if rules < MIN_DENY_RULES:
        need.append(f"denies from {rules} of {MIN_DENY_RULES} rules")
    if samples < MIN_SAMPLES:
        need.append(f"{samples} of {MIN_SAMPLES} shadow questions")
    if days < MIN_SAMPLE_DAYS:
        need.append(f"shadow questions span {int(days)} of {MIN_SAMPLE_DAYS} days")
    if wrong > FALSE_ALLOWS_ALLOWED:
        need.append(f"{wrong} false allow(s) (Jev said yes to a call you denied or marked wrong)")
    return {"ok": not need, "denies": denied, "rules": rules, "samples": samples, "days": days, "false_allows": wrong, "missing": need,
            "recall_bound": (0.05 ** (1 / denied)) if denied and not wrong else None}


def coarse_key(a, tool: str, tin: dict, el: str) -> str | None:
    """For a call that has no kind key (a write, an MCP call, an exact-only shell call): the rule and what it touches, so that one changed character does not undo a
    wrong mark. A shell call is the first two words of each gated segment."""
    from . import policy

    if el == "class":
        return None
    if policy.runs_shell(tool, tin):
        return sha256(canonical({"rule": a.rule_id, "heads": sorted({" ".join(k.split()[:2]) for k in a.seg_keys})}))
    return sha256(canonical({"rule": a.rule_id, "tool": tool, "subject": a.subject}))


def distrusted(db, keys: list[str]) -> bool:
    """The owner marked a call of this exact form, kind or target wrong (`jade review mark ... wrong`): Jev never decides it again."""
    keys = [k for k in keys if k]
    if not keys:
        return False
    marks = ",".join("?" * len(keys))
    return db.con.execute(f"SELECT 1 FROM events WHERE kind='jev_distrust' AND entity_id IN ({marks}) AND {ledger.LIVE.format(a='')} LIMIT 1", keys).fetchone() is not None  # noqa: S608


def distrust(db, *keys: str | None) -> None:
    for key in keys:
        if key:
            db.event(key, "jev_distrust", guard="jade review mark wrong", actor="human")


def denied_before(db, hold: str, cls: str | None = None) -> bool:
    """The owner said no to this call or to a call of its kind (same class key; any session, any time): Jev does not get to say yes."""
    if db.con.execute("SELECT 1 FROM asks WHERE status='DENIED' AND (hold_key=? OR (class_key IS NOT NULL AND class_key=?)) LIMIT 1", (hold, cls)).fetchone() is not None:
        return True
    return bool(cls) and db.con.execute(f"SELECT 1 FROM human_answers WHERE {memory.DENY_SQL} AND class_key=? LIMIT 1", (cls,)).fetchone() is not None  # noqa: S608


def _attempt_key(session: str, hold: str) -> str:
    return "jevtry:" + sha256(f"{session}\0{hold}")[:40]


def attempts(db, session: str, hold: str) -> int:
    since = ts(now() - timedelta(seconds=ATTEMPT_WINDOW_S))
    return db.con.execute(f"SELECT COUNT(*) FROM events WHERE kind='jev_asked' AND entity_id=? AND ts>=? AND {ledger.LIVE.format(a='')}", (_attempt_key(session, hold), since)).fetchone()[0]  # noqa: S608


def _written(tool: str, tin: dict) -> bool:
    from . import policy

    return policy.content_bound(tool, tin)


def _content(tool: str, tin: dict) -> str:
    """Everything a write or an MCP call carries, as one text (policy.content_view: the same rendering the permit digest covers), or '' for a shell call."""
    from . import policy

    return policy.content_view(tin) if _written(tool, tin) else ""


def skip_reason(a, tool: str, tin: dict, level: str) -> str | None:
    """Why this call is not sent to Jev (it is the owner's question), or None."""
    from . import policy

    rules = {a.rule_id, *(getattr(a, "seg_rules", None) or [])}
    if a.ho_ids or a.blocked:
        return "human-only or blocked"
    if a.level != "high":
        return "not a plain high call"
    if rules & NEVER_RULES:
        return "fetch-and-exec, egress, or a write to an agent instruction file"
    if a.blind or getattr(a, "unread", None) or rules & {"R-31", "R-32"}:
        return "the gate could not read all of it"
    subject = a.subject or ""
    if any(memory.MERGE.match(k) for k in getattr(a, "seg_keys", []) or []) or policy.gh_api_merge_in(subject):
        return "a merge"
    if len(subject) > MAX_TEXT or len(_content(tool, tin)) > MAX_TEXT:
        return "over 4 KiB"
    if policy.runs_shell(tool, tin) and len(list(memory.INLINE.finditer(subject))) > 1:
        return "more than one inline program"
    return None


def build_state(a, tool: str, tin: dict, prefs: str, past: list[dict], fp: dict) -> dict:
    """What Jev sees. The level and the folder are not part of it. The call is data: `action`, `kind` and `content_excerpt` are never instructions."""
    from . import decide

    state = {"action": decide.scrub(a.subject), "kind": f"{a.rule_id}: {a.rationale}", "preferences": prefs, "past_answers": past}
    if content := _content(tool, tin):
        state["content_excerpt"] = decide.scrub(content)
    if fp:
        state["fingerprints"] = fp
    return state


def _risk(level: str, a) -> str:
    """The risk the question is judged at, which picks the band in `policies/escalation.yaml` (egress never gets here: it is in NEVER_RULES)."""
    return "low" if level == "low" else "medium"


def _deadline(started: float | None) -> float | None:
    from .hooks import BUDGET_S

    left = BUDGET_S - ((time.monotonic() - started) if started else 0.0) - RESERVE_S
    d = min(DEADLINE_S, left)
    return d if d >= MIN_DEADLINE_S else None


def _ask(spec, state: dict, risk: str, session: str, deadline_s: float):
    box: dict = {}

    def work():
        try:
            box["r"] = ask.run([spec], state, risk=risk, session=session, provider=make_provider(deadline_s), use_cache=False)
        except Exception as e:  # noqa: BLE001 - whatever broke, the owner is asked
            box["e"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(deadline_s + JOIN_SLACK_S)
    return box.get("r")


def _close_stranded(db, session: str, since: str) -> None:
    """The exchange outlived the wait: its row must not stay EVALUATING (it would count against the requests in flight for 40 s)."""
    try:
        db.con.execute("UPDATE jev_calls SET state='DONE', error_kind='TIMEOUT', exit_code=5, done_ts=? WHERE question_id=? AND session_id=? AND state='EVALUATING' AND ts>=?",
                       (ts(), QID, session, since))
    except Exception:  # noqa: BLE001, S110 - bookkeeping only
        pass


def _spec(db):
    """The question Jev is asked, or None (the owner is asked as before). A pin to a version the registry does not hold, or to a version older than
    MIN_VERSION (it reads `past_answers` as confirmed answers only, not in the shape `past_answers` builds now), never blocks the call: the ledger notes
    it once per reason (event `jev_question_unusable`)."""
    from . import questions

    try:
        spec = ask.registry_spec(QID)
    except questions.InvalidQuestion as e:
        reason = str(e)
    except Exception as e:   # a pins.json of the wrong shape (not an object, a list champion, a string fraction, nesting too deep): the owner is asked
        reason = f"{QID}: the pins file cannot be read ({type(e).__name__}: {e})"
    else:
        if spec.version >= MIN_VERSION:
            return spec
        reason = f"{QID} version {spec.version} is pinned; Jev decides only with version {MIN_VERSION} or later"
    reason = reason[:300]
    db.event(QID, "jev_question_unusable", guard=reason, actor="hook", key="jevq:" + sha256(reason)[:40])
    return None


def replay_past_answers(past) -> list[dict]:
    """Stored `past_answers` rebuilt in the shape `past_answers` sends now, for a replay of a stored state (`jade improve shadow`): an allow keeps its rule and
    its label (the owner confirmed it) cut at 120, every other answer keeps its rule, its answer and its age only. A deny label stored before version 2 is
    dropped; an entry that is not an object is dropped."""
    out = []
    for p in past if isinstance(past, list) else []:
        if not isinstance(p, dict):
            continue
        if p.get("answer") == "allow":
            out.append({"rule": p.get("rule"), "answer": "allow", "label": str(p.get("label") or "")[:120]})
        else:
            out.append({"rule": p.get("rule"), "answer": "deny", "age_days": p.get("age_days") if type(p.get("age_days")) is int else None})
    return out


def step(db, mode: str, a, tool: str, event: dict, fp: dict, cwd: str, started: float | None = None, audit_held: bool = False):
    """The Jev branch of the gate: a HookResult that lets the call run, or None (the owner is asked as before). `audit_held` (V6-29): the audit of silent runs is why this
    call is asked about (its scope is paused, a limit was reached, no key): Jev must not let it run in the owner's place, which would be a silent run with no audit."""
    from . import askchat, hooks, permits, state_machine
    from .hooks import HookResult

    level = paths.level()
    session = event.get("session_id")
    if mode != "enforce" or level not in ("low", "medium", "high") or not session:
        return None
    if not jev_on(db):
        return None   # V6-28: before any state is built, so nothing goes to a model, shadow included
    gate_ok = deny_gate(db)["ok"] if audit_held and level != "high" else None   # read once: the question to Jev below is itself a shadow sample and could meet the gate while it is answered
    if gate_ok:
        return None   # Jev would decide here: the audit says the owner is asked (until the deny gate is met, or at level high, Jev only records and decides nothing)
    tin = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if skip_reason(a, tool, tin, level) or permits.make_context(tool, a, event, cwd=cwd).get("redacted"):
        return None
    exact, cls, el = memory.keys(a, tool, tin, fp)
    coarse = coarse_key(a, tool, tin, el)
    hold = a.hold_key or a.plan_hash
    prefs, past = preferences(), past_answers(db, a.rule_id)
    if not prefs and not any(p["answer"] == "allow" for p in past):
        return None   # denies alone give Jev nothing to say yes by
    if distrusted(db, [exact, cls, coarse]) or denied_before(db, hold, cls) or attempts(db, session, hold) >= MAX_ATTEMPTS:
        return None
    state = build_state(a, tool, tin, prefs, past, fp)
    spec = _spec(db)
    if spec is None:
        return None
    _, hits, _, leaked = ask.prepare([_spec(db)], dict(state))
    if hits or leaked:
        return None   # redaction changed the text: Jev would judge something other than what runs
    deadline = _deadline(started)
    if deadline is None:
        return None
    db.event("jev", WINDOW_EVENT, guard="first question to Jev", actor="hook", key=WINDOW_EVENT)   # once (the idempotency key): the shadow sample counts from here
    db.event(_attempt_key(session, hold), "jev_asked", guard=f"{a.rule_id} {a.level}", actor="hook")
    began = ts()
    result = _ask(spec, state, _risk(level, a), session, deadline)
    if result is None:
        _close_stranded(db, session, began)
        return None
    item = result.primary
    if item.call_id:   # V6-23: the link from this pair to its call, so a readout never has to guess between the calls of one session (ids only, no text)
        db.event(_attempt_key(session, hold), "jev_answered", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"call_id": item.call_id})
    if result.exit_code != 0 or item.verdict is None or item.verdict.verdict != "ACCEPT" or not item.call_id:
        return None
    db.event(item.call_id, "jev_accepted", guard=f"{a.rule_id} {a.level}", actor="hook",
             detail={"session": session, "hold": hold, "rule": a.rule_id, "exact_key": exact, "class_key": cls, "coarse_key": coarse})   # the link `false_allows` follows to the owner's later deny
    if level == "high" or not (deny_gate(db)["ok"] if gate_ok is None else gate_ok):   # an audit hold decided at the top by the same reading: it stays shadow if it was shadow
        return None   # shadow: asked and recorded, decides nothing
    try:
        with db.tx():
            if hooks._si07_hold(db, a):   # another identical call became a decision while Jev was answering: the owner is asked
                return None
            did = askchat.run_decision(db, a, tool, cwd, session, item.call_id[-12:], "jev")
            db.event(item.call_id, "jev_allowed", guard=f"{a.rule_id} {a.level}", actor="hook",
                     detail={"decision_id": did, "rule": a.rule_id, "label": decide_label(a), "exact_key": exact, "class_key": cls, "coarse_key": coarse,
                             "p": item.verdict.value, "band": item.verdict.accept})
    except state_machine.IllegalTransition:
        return None
    return HookResult(0, f"JADE: Jev judged this {tool} call from your preferences and past answers (rule {a.rule_id}, P={item.verdict.value:.2f}, needs "
                         f"{item.verdict.accept:.2f}) and let it run in your place. `jade review mark {did} wrong` makes JADE ask again for this call, its kind and "
                         f"its target (it cannot be undone, and it also retires your own answers of that kind); `jade review` shows what Jev decided.")


def jev_on(db) -> bool:
    """The `jev` key in force (`paths.jev`: the config key and the newest `jev` event together, failing closed). A ledger that cannot be read reads `off`."""
    try:
        return paths.jev(ledger.last_setting(db, "jev", "jev")) == "on"
    except Exception:  # noqa: BLE001 - `on` cannot be shown
        return False


def decide_label(a) -> str:
    from . import decide

    return str(decide.scrub(a.subject)).replace("\n", " ")[:120]
