"""The human answers a gated call in the chat (docs/12, plan v3, PR-5b).

At a level (`jade level`), gate mode enforce, host mode auto or bypassPermissions, in the main session:

1. `_gate` calls `step`: with no usable grant it opens an ask and exits 2 with a fixed text that tells the agent to call AskUserQuestion with the token
   `[jade:ask_<id>]` and then retry the identical call.
2. PreToolUse on that AskUserQuestion calls `rewrite`: the whole question is REPLACED by the hook's own text (rule, rationale, the command, the part that is
   not shown), so the agent's wording never reaches the human. The host shows the replaced question (probe cell 5b, evals/probes/hook_ask/RESULTS.md).
3. PostToolUse on it calls `settle`: the answer is read from the host's `tool_response.answers` (never from agent text). Allow makes a single-use grant
   (10 minutes, this session, this call); Deny closes the ask. The answer is recorded as a hint in `human_answers`; it teaches nothing yet.
4. The identical retry reaches `step` again, which consumes the grant, opens a decision (so SI-07 still holds an unknown outcome) and lets the call run.

A grant is never valid for live JADE wiring, a host mode outside the two above, or a call with secrets in it: those keep the signed terminal permit.
A subagent call has no AskUserQuestion: `askdialog` (W-100c) asks it in a macOS dialog instead. Residual: a mod loaded in Claude Code can answer for the human (docs/12 risks).
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

from . import paths
from .util import canonical, new_id, now, ts

CHAT_MODES = ("auto", "bypassPermissions")
ASK_TTL_S = 600
GRANT_TTL_S = 600
PERMIT_ONLY = frozenset({"HO-wiring", "HO-checkout-install", "HO-settings-shadow", "HO-state-path"})   # live JADE wiring: a human types `jade permit approve`
TOKEN = re.compile(r"\[jade:ask_([0-9a-f]{12})\]")
HEAD_CHARS, TAIL_CHARS = 450, 150


def applicable(mode: str, a, event: dict) -> bool:
    """Does this call take the chat channel? (Secrets in the call are checked by the caller: it needs the context.)"""
    return (mode == "enforce" and paths.level() in ("low", "medium", "high") and event.get("permission_mode") in CHAT_MODES and not event.get("agent_id")
            and bool(event.get("session_id")) and not (set(a.ho_ids) & PERMIT_ONLY))


def _clip(text: str) -> str:
    if len(text) <= HEAD_CHARS + TAIL_CHARS + 100:
        return text
    return f"{text[:HEAD_CHARS]}\n... {len(text) - HEAD_CHARS - TAIL_CHARS} characters not shown ...\n{text[-TAIL_CHARS:]}"


def question_for(ask_id: str, a, tool: str, ctx: dict) -> dict:
    """The one question the human sees. Everything in it comes from the gate, none from the agent."""
    lines = [f"[jade:ask_{ask_id}] JADE gate: {a.level} risk, rule {a.rule_id}. {a.rationale}"]
    if a.ho_ids:
        lines.append("Classes: " + ", ".join(a.ho_ids) + " (only you can decide these)")
    lines.append(f"Tool: {tool}")
    lines.append("Command:\n" + _clip(str(ctx.get("subject") or a.subject)))
    content = ctx.get("content")
    if isinstance(content, dict):
        lines.append(f"It also writes {content.get('length')} characters of content (sha256 {str(content.get('sha256'))[:12]}). First part:\n{_clip(str(content.get('text') or ''))}")
    lines.append("Allow this one call? Single use, 10 minutes, this session only.")
    return {"question": "\n".join(lines), "header": "JADE gate", "multiSelect": False,
            "options": [{"label": "Allow", "description": "Run this one call once. It is not remembered."},
                        {"label": "Deny", "description": "Do not run it. The agent is told you said no."}]}


def _expire(db) -> None:
    db.con.execute("UPDATE asks SET status='EXPIRED' WHERE status IN ('PENDING','SHOWN','ALLOWED') AND expires_ts<=?", (ts(),))


def run_decision(db, a, tool: str, cwd: str, session: str, ask_id: str, where: str) -> str:
    """Open the decision of a call the human allowed (in the chat or in a dialog), walked to EXECUTING so that SI-07 still holds an unknown outcome."""
    from . import decide, permits

    doc = decide.open_decision(db, f"Execute {tool}: {decide.scrub(a.subject)[:300]}", command=a.subject, tool=tool, cwd=cwd, session=session, route="ESCALATE", assessment=a)
    did = doc["decision_id"]
    decide.step(db, did, "route", guard=f"route=ESCALATE: the human answers in the {where}")
    decide.step(db, did, "escalate", guard=f"{where} ask {ask_id}", ttl_s=permits.DEFAULT_TTL_S)
    decide.step(db, did, "human_accept", guard=f"Allow answered in the {where} (ask {ask_id})", actor="human")
    decide.step(db, did, "authorize", permit_ok=True, guard=f"{where} grant {ask_id}: single use, unexpired, same session, same call")
    decide.step(db, did, "execute", guard=f"PreToolUse saw the matching call; {where} grant consumed", actor="hook", ttl_s=3600)
    return did


def step(db, mode: str, a, tool: str, event: dict, fp: dict, cwd: str):
    """The chat branch of the gate: a HookResult (run the call, or refuse it with the instruction) or None when this call does not take the chat channel."""
    from . import permits, state_machine
    from .hooks import HookResult

    if not applicable(mode, a, event):
        return None
    ctx = permits.make_context(tool, a, event, cwd=cwd)
    if ctx.get("redacted"):
        return None   # a secret-shaped value is never stored or shown: the terminal permit path says what to do
    session, hold, fpj = event["session_id"], a.hold_key or a.plan_hash, canonical(fp)
    _expire(db)
    try:
        with db.tx():
            row = db.one("SELECT ask_id FROM asks WHERE session_id=? AND status='ALLOWED' AND hold_key=? AND fingerprints_json=? AND expires_ts>? ORDER BY answered_ts LIMIT 1",
                         (session, hold, fpj, ts()))
            if row is not None and db.con.execute("UPDATE asks SET status='CONSUMED' WHERE ask_id=? AND status='ALLOWED'", (row.ask_id,)).rowcount == 1:
                did = run_decision(db, a, tool, cwd, session, row.ask_id, "chat")
                db.con.execute("UPDATE asks SET decision_id=? WHERE ask_id=?", (did, row.ask_id))
                db.event(row.ask_id, "ask_consumed", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"decision_id": did})
                return HookResult(0, f"JADE: your Allow in the chat (ask {row.ask_id}) was used for this {tool} call. It is spent; the next identical call asks again.")
            denied = db.one("SELECT ask_id FROM asks WHERE session_id=? AND status='DENIED' AND hold_key=? AND fingerprints_json=? AND expires_ts>?", (session, hold, fpj, ts()))
            if denied is not None:
                return HookResult(2, f"JADE gate: the human said no to this {tool} call in the chat (ask {denied.ask_id}). Do not retry it unchanged and do not rephrase it to get "
                                     "around the answer. Continue with other work, or tell the human what you need and why.")
            open_ask = db.one("SELECT ask_id FROM asks WHERE session_id=? AND status IN ('PENDING','SHOWN') AND hold_key=? AND fingerprints_json=? AND expires_ts>? ORDER BY created_ts DESC LIMIT 1",
                              (session, hold, fpj, ts()))
            if open_ask is not None:
                ask_id = open_ask.ask_id
            else:
                ask_id = new_id("a")[2:]   # twelve hex characters
                q = question_for(ask_id, a, tool, ctx)
                db.con.execute("INSERT INTO asks(ask_id, session_id, agent_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, expires_ts, source) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?,'PENDING',?,?,?)",
                               (ask_id, session, event.get("agent_id"), a.plan_hash, hold, fpj, a.rule_id, a.level, tool, json.dumps(q), ts(), ts(now() + timedelta(seconds=ASK_TTL_S)), db.source))
                db.event(ask_id, "ask_opened", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"tool": tool, "ho_ids": list(a.ho_ids)})
    except state_machine.IllegalTransition as e:
        return HookResult(2, f"JADE gate: the chat approval for this {tool} call could not be used ({e}). Ask the human to run `jade decide list` and look at it.")
    return HookResult(2, f"JADE gate: {a.level} risk ({a.rule_id}: {a.rationale}). The human decides this {tool} call in the chat.\n"
                         f"Call the AskUserQuestion tool now with ONE question whose text starts with {f'[jade:ask_{ask_id}]'} and the options Allow and Deny. "
                         "JADE replaces the question text with the real one, so do not write the command into it. Wait for the answer. "
                         f"Then retry the identical {tool} call, unchanged. Do not answer the question yourself and do not rephrase the call to avoid the gate.")


def _token_in(obj) -> str | None:
    m = TOKEN.search(json.dumps(obj, ensure_ascii=False))
    return m.group(1) if m else None


def rewrite(event: dict):
    """PreToolUse on AskUserQuestion: a question that carries an ask token of this session is replaced by the hook's own; any other question passes."""
    from . import ledger
    from .hooks import LEDGER_TIMEOUT_S, HookResult

    token = _token_in(event.get("tool_input"))
    if token is None:
        return HookResult()
    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        _expire(db)
        row = db.one("SELECT ask_id, question_json, status FROM asks WHERE ask_id=? AND session_id=? AND status IN ('PENDING','SHOWN')", (token, event.get("session_id")))
        if row is None:
            return HookResult(2, f"JADE: the ask token [jade:ask_{token}] is unknown, expired or belongs to another session. Retry the gated call to get a new one; do not write tokens yourself.")
        db.con.execute("UPDATE asks SET status='SHOWN' WHERE ask_id=? AND status='PENDING'", (token,))
        db.event(token, "ask_shown", guard="question replaced by the hook", actor="hook")
        q = json.loads(row.question_json)
    finally:
        db.close()
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"questions": [q]}}}
    return HookResult(0, "", json.dumps(out, ensure_ascii=False))


def settle(event: dict):
    """PostToolUse on AskUserQuestion: read the human's answer from the host's response and close the ask."""
    from . import ledger
    from .hooks import LEDGER_TIMEOUT_S, HookResult

    resp = event.get("tool_response")
    answers = resp.get("answers") if isinstance(resp, dict) else None
    if not isinstance(answers, dict):
        return HookResult()
    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        for question, label in answers.items():
            m = TOKEN.search(str(question))
            if not m or label not in ("Allow", "Deny"):
                continue
            row = db.one("SELECT * FROM asks WHERE ask_id=? AND session_id=? AND status='SHOWN'", (m.group(1), event.get("session_id")))
            if row is None:
                continue
            allow = label == "Allow"
            until = ts(now() + timedelta(seconds=GRANT_TTL_S)) if allow else ts(now() + timedelta(seconds=ASK_TTL_S))
            with db.tx():
                if db.con.execute("UPDATE asks SET status=?, answered_ts=?, expires_ts=? WHERE ask_id=? AND status='SHOWN'",
                                  ("ALLOWED" if allow else "DENIED", ts(), until, row.ask_id)).rowcount != 1:
                    continue
                db.event(row.ask_id, "ask_allowed" if allow else "ask_denied", guard=f"{row.rule_id} {row.level}", actor="human", detail={"channel": "chat"})
                db.record_human_answer(session=row.session_id, decision_id=row.decision_id, rule_id=row.rule_id, class_key=row.rule_id, exact_key=row.hold_key[:64],
                                       answer="allow" if allow else "deny", channel="chat")
    finally:
        db.close()
    return HookResult()
