"""Jev decides in the owner's place, inside the hook (docs/12, plan v3, PR-7).

At level low or medium a plain high call that neither a terminal permit nor a confirmed answer covers goes to Jev with the owner's written preferences and the
confirmed answers they gave to calls of the same rule. An accept in the level's band lets the call run, through a decision walked to EXECUTING (so SI-07 still holds
an unknown outcome). Everything else is the owner's question as before: an abstain, a reject, an unavailable or late provider, a call that is skipped. A verdict that is
not an accept is never a yes.

Bands come from `policies/escalation.yaml` (family `human_proxy`): the autonomy level sets the risk the question is judged at, so low accepts at 0.70 (egress at
0.85) and medium at 0.85. Until the owner has answered `ops.JEV_BAND_ANSWERS` distinct plain-high calls Jev is shadow: it is asked and recorded and decides nothing. At
level high it is shadow too and its advice is not shown.

Never sent (always the owner's question):
- a human-only class, a blocked call, a critical call (only level high calls are sent), fetch-and-exec (R-22) and writes to agent instruction files (R-13, R-18),
  also when the rule sits in one segment of a longer command;
- a merge, a call the gate could not read (any segment), a secret-shaped value, text that redaction changed;
- a program or a write over 4 KiB (the whole content of a write is shown to Jev, never a part), more than one inline program;
- a call the owner denied before, and a call Jev has already been asked about twice this hour in this session (a reworded comment is the same call);
- a call with neither a preferences text nor a confirmed answer of its rule.
Egress (R-21) is decided at level low only.

The provider call runs in a daemon thread that the hook waits for at most `DEADLINE_S` (and never past the hook's own budget); a hung exchange leaves the thread behind,
its row is closed, and the owner is asked. The answer is never taken from the dedup cache: every accept is a fresh sample of this call.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from . import adapter, ask, ledger, memory, paths
from .util import canonical, now, sha256, ts

QID = "q.human_proxy.would_allow"
DEADLINE_S = 3.0          # the most one exchange may take; the hook budget (hooks.BUDGET_S) is the other limit
RESERVE_S = 1.5           # what the rest of the hook still needs after Jev answered
MIN_DEADLINE_S = 0.1
JOIN_SLACK_S = 0.5
MAX_TEXT = 4096
MAX_PAST = 8
MAX_ATTEMPTS = 2          # questions to Jev about one call (by hold key) per session and hour: a comment added to the command does not make it a new call
ATTEMPT_WINDOW_S = 3600
NEVER_RULES = frozenset({"R-22", "R-13", "R-18"})   # R-13 and R-18: the files an agent reads its instructions from (AGENTS.md, CLAUDE.md, skills): a model judging them is the injection surface
EGRESS = "R-21"


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
    """The confirmed (strong) allow answers the owner gave to plain calls of this rule, newest first. Unconfirmed hints and human-only answers are never sent."""
    cap, use = memory._valid_since()
    rows = db.con.execute("SELECT rule_id, label FROM human_answers WHERE strength='strong' AND answer='allow' AND rule_id=? AND COALESCE(ho,0)=0 AND confirmed_ts>=? AND "
                          "COALESCE(last_used_ts, confirmed_ts)>=? AND COALESCE(source,'')<>'test' ORDER BY confirmed_ts DESC, rowid DESC LIMIT ?",
                          (rule_id, cap, use, MAX_PAST)).fetchall()
    return [{"rule": r["rule_id"], "answer": "allow", "label": (r["label"] or "")[:120]} for r in rows]


def owner_answers(db) -> int:
    """Distinct plain-high calls the owner answered (human-only answers, retired answers and repeats of one call do not count): what the shadow band is measured by."""
    return db.con.execute("SELECT COUNT(DISTINCT exact_key) FROM human_answers WHERE COALESCE(ho,0)=0 AND strength<>'revoked' AND " + ledger.LIVE.format(a="")).fetchone()[0]


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


def denied_before(db, hold: str) -> bool:
    """The owner said no to this call (any session, any time): Jev does not get to say yes."""
    return db.con.execute("SELECT 1 FROM asks WHERE status='DENIED' AND hold_key=? LIMIT 1", (hold,)).fetchone() is not None


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
        return "fetch-and-exec, or a write to an agent instruction file"
    if EGRESS in rules and level != "low":
        return "egress outside level low"
    if a.blind or getattr(a, "unread", None) or rules & {"R-31", "R-32"}:
        return "the gate could not read all of it"
    if any(memory._MERGE.match(k) for k in getattr(a, "seg_keys", []) or []):
        return "a merge"
    subject = a.subject or ""
    if len(subject) > MAX_TEXT or len(_content(tool, tin)) > MAX_TEXT:
        return "over 4 KiB"
    if policy.runs_shell(tool, tin) and len(list(memory._INLINE.finditer(subject))) > 1:
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
    """The risk the question is judged at, which picks the band in `policies/escalation.yaml`."""
    if level == "low":
        return "high" if EGRESS in {a.rule_id, *(getattr(a, "seg_rules", None) or [])} else "low"
    return "medium"


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


def step(db, mode: str, a, tool: str, event: dict, fp: dict, cwd: str, started: float | None = None):
    """The Jev branch of the gate: a HookResult that lets the call run, or None (the owner is asked as before)."""
    from . import askchat, hooks, permits, state_machine
    from .hooks import HookResult

    level = paths.level()
    session = event.get("session_id")
    if mode != "enforce" or level not in ("low", "medium", "high") or not session:
        return None
    tin = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if skip_reason(a, tool, tin, level) or permits.make_context(tool, a, event, cwd=cwd).get("redacted"):
        return None
    exact, cls, el = memory.keys(a, tool, tin, fp)
    coarse = coarse_key(a, tool, tin, el)
    hold = a.hold_key or a.plan_hash
    prefs, past = preferences(), past_answers(db, a.rule_id)
    if not prefs and not past:
        return None
    if distrusted(db, [exact, cls, coarse]) or denied_before(db, hold) or attempts(db, session, hold) >= MAX_ATTEMPTS:
        return None
    state = build_state(a, tool, tin, prefs, past, fp)
    spec = ask.registry_spec(QID)
    _, hits, _, leaked = ask.prepare([ask.registry_spec(QID)], dict(state))
    if hits or leaked:
        return None   # redaction changed the text: Jev would judge something other than what runs
    deadline = _deadline(started)
    if deadline is None:
        return None
    db.event(_attempt_key(session, hold), "jev_asked", guard=f"{a.rule_id} {a.level}", actor="hook")
    began = ts()
    result = _ask(spec, state, _risk(level, a), session, deadline)
    if result is None:
        _close_stranded(db, session, began)
        return None
    item = result.primary
    if result.exit_code != 0 or item.verdict is None or item.verdict.verdict != "ACCEPT" or not item.call_id:
        return None
    if level == "high" or owner_answers(db) < _band_answers():
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


def _band_answers() -> int:
    from . import ops

    return ops.JEV_BAND_ANSWERS


def decide_label(a) -> str:
    from . import decide

    return str(decide.scrub(a.subject)).replace("\n", " ")[:120]
