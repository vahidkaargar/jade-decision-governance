"""The human answers a gated subagent call in a macOS dialog (docs/12, plan v3, PR-5c).

A subagent has no AskUserQuestion, so the chat channel (`askchat`) cannot reach the human for it. At a level (`jade level`), gate mode enforce, on a Mac with a
screen, `step` shows a dialog from the PreToolUse hook and WAITS for the click: no retry and no new prompt. The text comes from the gate, never from the agent,
and reaches osascript as an argument, never inside the script.

- Allow: this one call runs. A decision is opened and walked to EXECUTING (as for a chat grant), so SI-07 still holds an unknown outcome of an irreversible call.
- Deny: the call is refused, and the same call is not asked again until the window ends.
- No answer (gave up, closed, no screen, osascript missing): `step` returns None and the signed terminal permit applies as before. The same call is not shown
  again for `GAVE_UP_TTL_S`, so a retry loop cannot stack dialogs.

The wait needs time the normal 6 s alarm does not allow: the alarm is raised for the wait and restored after it, and `ops.HOOKS` installs a PreToolUse timeout
above it. Live JADE wiring and a call with secrets in it never take this channel.
"""

from __future__ import annotations

import re
import signal
import subprocess
import sys
from datetime import timedelta

from . import askchat, paths
from .util import canonical, new_id, now, ts

DIALOG_WAIT_S = 120       # how long the dialog stays up before it gives up
ALARM_MARGIN_S = 10       # the hook alarm and the host timeout sit this far above the wait
GAVE_UP_TTL_S = 300       # an unanswered call is not shown again for this long
DENY_TTL_S = askchat.ASK_TTL_S
RESTORE_ALARM_S = 4.0     # what is left of the hook's budget once the dialog is closed
OSASCRIPT = "/usr/bin/osascript"
_SCRIPT = ("on run argv",
           "set theText to item 1 of argv",
           "set waitS to (item 2 of argv) as integer",
           "tell current application to activate",
           'display dialog theText with title "JADE gate" with icon caution buttons {"Deny", "Allow"} default button "Deny" giving up after waitS',
           "end run")
_RESULT = re.compile(r"button returned:(?P<label>[^,\n]*),\s*gave up:(?P<gave>true|false)")


def _alarm_running() -> bool:
    """Is the hook's alarm armed? `hooks.main` arms it; a caller that runs the handler in-process (a test) has none, and one must not be left behind."""
    try:
        return signal.getitimer(signal.ITIMER_REAL)[0] > 0
    except (ValueError, AttributeError, OSError):
        return False


def available() -> bool:
    """A Mac with a logged-in screen. The platform check is all the hook can know cheaply; a missing screen shows up as no answer."""
    return sys.platform == "darwin"


def applicable(mode: str, a, event: dict) -> bool:
    return (mode == "enforce" and paths.level() in ("low", "medium", "high") and bool(event.get("agent_id")) and bool(event.get("session_id"))
            and not (set(a.ho_ids) & askchat.PERMIT_ONLY) and available())


def notify(text: str) -> None:
    """One macOS notification, fire and forget. The text is written by the hook (rule and folder name, never the command) and goes in as an argument."""
    if not available():
        return
    argv = [OSASCRIPT, "-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "JADE gate"', "-e", "end run", text]
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)  # noqa: S603
    except (OSError, ValueError):
        pass


def run_dialog(text: str, wait_s: int) -> str | None:
    """Show the dialog and return the clicked label ('Allow' or 'Deny'), or None when nobody answered."""
    argv = [OSASCRIPT]
    for line in _SCRIPT:
        argv += ["-e", line]
    argv += [text, str(int(wait_s))]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=wait_s + 5, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    m = _RESULT.search(out.stdout or "") if out.returncode == 0 else None
    if not m or m.group("gave") == "true":
        return None
    return m.group("label").strip() or None


def text_for(a, tool: str, ctx: dict) -> str:
    """Everything in the dialog comes from the gate, none from the agent."""
    lines = [f"JADE gate: {a.level} risk, rule {a.rule_id}. {a.rationale}", "A subagent wants to run this call."]
    if a.ho_ids:
        lines.append("Classes: " + ", ".join(a.ho_ids) + " (only you can decide these)")
    lines.append(f"Tool: {tool}")
    lines.append("Command:\n" + askchat._clip(str(ctx.get("subject") or a.subject)))
    content = ctx.get("content")
    if isinstance(content, dict):
        lines.append(f"It also writes {content.get('length')} characters of content (sha256 {str(content.get('sha256'))[:12]}). First part:\n{askchat._clip(str(content.get('text') or ''))}")
    lines.append("Allow this one call? Single use, this session only. Deny, or no answer, blocks it.")
    return "\n".join(lines)


def _insert(db, ask_id, session, event, a, tool, hold, fpj, text, status, ttl_s, answered):
    db.con.execute("INSERT INTO asks(ask_id, session_id, agent_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, answered_ts, "
                   "expires_ts, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ask_id, session, event.get("agent_id"), a.plan_hash, hold, fpj, a.rule_id, a.level, tool, canonical({"question": text, "channel": "dialog"}), status, ts(),
                    ts() if answered else None, ts(now() + timedelta(seconds=ttl_s)), db.source))


def _finish(db, ask_id: str, status: str, ttl_s: int, answered: bool) -> None:
    db.con.execute("UPDATE asks SET status=?, answered_ts=?, expires_ts=? WHERE ask_id=?", (status, ts() if answered else None, ts(now() + timedelta(seconds=ttl_s)), ask_id))


def step(db, mode: str, a, tool: str, event: dict, fp: dict, cwd: str):
    """The dialog branch of the gate for a subagent call: a HookResult (run it, or refuse it) or None when the signed terminal permit applies."""
    from . import hooks, permits, state_machine
    from .hooks import HookResult

    if not applicable(mode, a, event):
        return None
    ctx = permits.make_context(tool, a, event, cwd=cwd)
    if ctx.get("redacted"):
        return None   # a secret-shaped value is never stored or shown: the terminal permit path says what to do
    session, hold, fpj = event["session_id"], a.hold_key or a.plan_hash, canonical(fp)
    askchat._expire(db)
    if (res := askchat.refusal(db, session, hold, fpj, tool, "a dialog")) is not None:
        return res
    if db.one("SELECT ask_id FROM asks WHERE session_id=? AND status='GAVE_UP' AND hold_key=? AND fingerprints_json=? AND expires_ts>?", (session, hold, fpj, ts())) is not None:
        return None
    if (res := askchat.paused(db, session)) is not None:
        return res
    text = text_for(a, tool, ctx)
    ask_id = new_id("a")[2:]
    _insert(db, ask_id, session, event, a, tool, hold, fpj, text, "DIALOG", DIALOG_WAIT_S + ALARM_MARGIN_S, False)   # while it is on screen the session's GUI tools are paused
    armed = _alarm_running()
    if armed:
        hooks._arm(DIALOG_WAIT_S + ALARM_MARGIN_S - 2)   # the dialog waits for the human; the normal 6 s alarm would kill it
    try:
        label = run_dialog(text, DIALOG_WAIT_S)
    finally:
        if armed:
            hooks._arm(RESTORE_ALARM_S)
    if label not in ("Allow", "Deny"):
        _finish(db, ask_id, "GAVE_UP", GAVE_UP_TTL_S, False)
        db.event(ask_id, "ask_gave_up", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"channel": "dialog"})
        return None
    allow = label == "Allow"
    try:
        with db.tx():
            _finish(db, ask_id, "CONSUMED" if allow else "DENIED", askchat.GRANT_TTL_S if allow else DENY_TTL_S, True)
            did = askchat.run_decision(db, a, tool, cwd, session, ask_id, "dialog") if allow else None
            if did:
                db.con.execute("UPDATE asks SET decision_id=? WHERE ask_id=?", (did, ask_id))
            db.event(ask_id, "ask_allowed" if allow else "ask_denied", guard=f"{a.rule_id} {a.level}", actor="human", detail={"channel": "dialog", "decision_id": did})
            db.record_human_answer(session=session, decision_id=did, rule_id=a.rule_id, class_key=a.rule_id, exact_key=hold[:64], answer="allow" if allow else "deny", channel="dialog")
    except state_machine.IllegalTransition as e:
        return HookResult(2, f"JADE gate: the dialog approval for this {tool} call could not be used ({e}). Ask the human to run `jade decide list` and look at it.")
    if allow:
        return HookResult(0, f"JADE: the human allowed this {tool} call in a dialog (ask {ask_id}). It is spent; the next identical call asks again.")
    return HookResult(2, f"JADE gate: the human said no to this {tool} call in a dialog (ask {ask_id}). Do not retry it unchanged and do not rephrase it to get around "
                         "the answer. Continue with other work, or tell the human what you need and why.")
