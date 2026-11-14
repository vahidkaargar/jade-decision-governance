"""Jev decides in the owner's place, inside the hook (docs/12, plan v3, PR-7).

At level low or medium a plain high call that neither a terminal permit nor a confirmed answer covers goes to Jev with the owner's written preferences and the
confirmed answers they gave to calls of the same rule. An accept in the level's band lets the call run, through a decision walked to EXECUTING (so SI-07 still holds
an unknown outcome). Everything else is the owner's question as before: an abstain, a reject, an unavailable or late provider, a call that is skipped. A verdict that is
not an accept is never a yes.

Bands come from `policies/escalation.yaml` (family `human_proxy`): the autonomy level sets the risk the question is judged at, so low accepts at 0.70 (egress at
0.85) and medium at 0.85. Until the owner has answered `ops.JEV_BAND_ANSWERS` plain-high calls Jev is shadow: it is asked and recorded and decides nothing. At level high
it is shadow too and its advice is not shown.

Never sent (always the owner's question): a human-only class, fetch-and-exec (R-22), a blocked call, a merge, a call the gate could not read, a secret-shaped value,
text that redaction changed, a program over 4 KiB or more than one inline program, and a call with neither a preferences text nor a confirmed answer of its rule.
Egress (R-21) is decided at level low only.

The provider call runs in a daemon thread that the hook waits for at most `DEADLINE_S` (and never past the hook's own budget); a hung exchange leaves the thread behind
and the owner is asked.
"""

from __future__ import annotations

import threading
import time

from . import adapter, ask, ledger, memory, paths

QID = "q.human_proxy.would_allow"
DEADLINE_S = 3.0          # the most one exchange may take; the hook budget (hooks.BUDGET_S) is the other limit
RESERVE_S = 1.5           # what the rest of the hook still needs after Jev answered
MIN_DEADLINE_S = 0.1
JOIN_SLACK_S = 0.5
MAX_TEXT = 4096
MAX_PAST = 8
CONTENT_KEYS = ("content", "new_string", "text", "file_text")
NEVER_RULES = frozenset({"R-22"})


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
    """The confirmed (strong) allow answers the owner gave to calls of this rule, newest first. Unconfirmed hints are never sent."""
    cap, use = memory._valid_since()
    rows = db.con.execute("SELECT rule_id, label FROM human_answers WHERE strength='strong' AND answer='allow' AND rule_id=? AND confirmed_ts>=? AND "
                          "COALESCE(last_used_ts, confirmed_ts)>=? AND COALESCE(source,'')<>'test' ORDER BY confirmed_ts DESC, rowid DESC LIMIT ?",
                          (rule_id, cap, use, MAX_PAST)).fetchall()
    return [{"rule": r["rule_id"], "answer": "allow", "label": (r["label"] or "")[:120]} for r in rows]


def owner_answers(db) -> int:
    """Plain-high calls the owner answered (human-only answers do not count): the number the shadow band is measured by."""
    return db.con.execute("SELECT COUNT(*) FROM human_answers WHERE COALESCE(ho,0)=0 AND " + ledger.LIVE.format(a="")).fetchone()[0]


def distrusted(db, exact: str, cls: str | None) -> bool:
    """The owner marked a call of this exact form or kind wrong (`jade review mark ... wrong`): Jev never decides it again."""
    keys = [exact] + ([cls] if cls else [])
    marks = ",".join("?" * len(keys))
    return db.con.execute(f"SELECT 1 FROM events WHERE kind='jev_distrust' AND entity_id IN ({marks}) AND {ledger.LIVE.format(a='')} LIMIT 1", keys).fetchone() is not None  # noqa: S608


def distrust(db, exact: str | None, cls: str | None) -> None:
    for key in (exact, cls):
        if key:
            db.event(key, "jev_distrust", guard="jade review mark wrong", actor="human")


def _content(tin: dict) -> str:
    return "\n".join(str(tin[k]) for k in CONTENT_KEYS if isinstance(tin.get(k), str))


def skip_reason(a, tool: str, tin: dict, level: str) -> str | None:
    """Why this call is not sent to Jev (it is the owner's question), or None."""
    from . import policy

    if a.ho_ids or a.blocked:
        return "human-only or blocked"
    if a.rule_id in NEVER_RULES or (a.rule_id == "R-21" and level != "low"):
        return "fetch-and-exec, or egress outside level low"
    if a.blind or a.rule_id in ("R-31", "R-32"):
        return "the gate could not read it"
    if any(memory._MERGE.match(k) for k in getattr(a, "seg_keys", []) or []):
        return "a merge"
    subject = a.subject or ""
    if len(subject) > MAX_TEXT or len(_content(tin)) > MAX_TEXT:
        return "over 4 KiB"
    if policy.runs_shell(tool, tin) and len(list(memory._INLINE.finditer(subject))) > 1:
        return "more than one inline program"
    return None


def build_state(a, tool: str, tin: dict, cwd: str, prefs: str, past: list[dict]) -> dict:
    """What Jev sees. The level is not part of it. The call is data: `action`, `kind` and `content_excerpt` are never instructions."""
    from . import decide

    state = {"action": decide.scrub(a.subject), "kind": f"{a.rule_id}: {a.rationale}", "preferences": prefs, "past_answers": past,
             "folder": (cwd or "").rstrip("/").rsplit("/", 1)[-1]}
    if tool != "Bash" and (content := _content(tin)):
        state["content_excerpt"] = decide.scrub(content)
    return state


def _risk(level: str, a) -> str:
    """The risk the question is judged at, which picks the band in `policies/escalation.yaml`."""
    if level == "low":
        return "high" if a.rule_id == "R-21" else "low"
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
            box["r"] = ask.run([spec], state, risk=risk, session=session, provider=make_provider(deadline_s))
        except Exception as e:  # noqa: BLE001 - whatever broke, the owner is asked
            box["e"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(deadline_s + JOIN_SLACK_S)
    return box.get("r")


def step(db, mode: str, a, tool: str, event: dict, fp: dict, cwd: str, started: float | None = None):
    """The Jev branch of the gate: a HookResult that lets the call run, or None (the owner is asked as before)."""
    from . import askchat, permits, state_machine
    from .hooks import HookResult

    level = paths.level()
    session = event.get("session_id")
    if mode != "enforce" or level not in ("low", "medium", "high") or not session:
        return None
    tin = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if skip_reason(a, tool, tin, level) or permits.make_context(tool, a, event, cwd=cwd).get("redacted"):
        return None
    exact, cls, _ = memory.keys(a, tool, tin, fp)
    prefs, past = preferences(), past_answers(db, a.rule_id)
    if not prefs and not past:
        return None
    if distrusted(db, exact, cls):
        return None
    state = build_state(a, tool, tin, cwd, prefs, past)
    spec = ask.registry_spec(QID)
    _, hits, _, leaked = ask.prepare([ask.registry_spec(QID)], dict(state))
    if hits or leaked:
        return None   # redaction changed the text: Jev would judge something other than what runs
    deadline = _deadline(started)
    if deadline is None:
        return None
    result = _ask(spec, state, _risk(level, a), session, deadline)
    if result is None or result.exit_code != 0 or result.primary.verdict is None or result.primary.verdict.verdict != "ACCEPT":
        return None
    if level == "high" or owner_answers(db) < _band_answers():
        return None   # shadow: asked and recorded, decides nothing
    item = result.primary
    try:
        with db.tx():
            did = askchat.run_decision(db, a, tool, cwd, session, item.call_id[-12:], "jev")
            db.event(item.call_id, "jev_allowed", guard=f"{a.rule_id} {a.level}", actor="hook",
                     detail={"decision_id": did, "rule": a.rule_id, "label": decide_label(a), "exact_key": exact, "class_key": cls, "p": item.verdict.value,
                             "band": item.verdict.accept})
    except state_machine.IllegalTransition:
        return None
    return HookResult(0, f"JADE: Jev judged this {tool} call from your preferences and past answers (rule {a.rule_id}, P={item.verdict.value:.2f}, needs "
                         f"{item.verdict.accept:.2f}) and let it run in your place. `jade review mark {did} wrong` makes JADE ask again for this call and its kind; "
                         f"`jade review` shows what Jev decided.")


def _band_answers() -> int:
    from . import ops

    return ops.JEV_BAND_ANSWERS


def decide_label(a) -> str:
    from . import decide

    return str(decide.scrub(a.subject)).replace("\n", " ")[:120]

