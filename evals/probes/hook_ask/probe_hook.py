"""PR-0 probe hook (W-96, docs/12 plan v3): what does Claude Code do with a hook's answer, in each permission mode?

Standard library only. It never touches JADE, the ledger or any secret. Install it ONLY in a scratch project (see README.md); it logs every
hook event it receives to `probe.log.jsonl` next to this file, and answers by the action written in `probe.action` (one word, re-read on
every event, so the action can change without restarting a session):

    log        record the event, answer nothing (default; also when the file is missing)
    ask        PreToolUse: permissionDecision "ask" with a reason text
    allow      PreToolUse: permissionDecision "allow"
    answer     PreToolUse on AskUserQuestion: updatedInput with an `answers` object (first option of each question) + "allow"
    exit1      exit 1 (a hook error, not a block)
    exit2      exit 2 with a reason on stderr (a block)
    sleep      sleep 15 s (past the 10 s hook timeout the owner sets in the scratch settings), then answer nothing
    dialog     show a macOS dialog (osascript) and wait for the click; Allow -> permissionDecision "allow", Deny -> exit 2

Only the keys and shapes the plan needs are recorded for tool_input and tool_response (key names, and the first 200 characters of each
string value), so a log holds no file content.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "probe.log.jsonl"
ACTION_FILE = HERE / "probe.action"


def shape(v, depth=0):
    if isinstance(v, dict):
        return {k: shape(x, depth + 1) for k, x in list(v.items())[:30]} if depth < 4 else "{...}"
    if isinstance(v, list):
        return [shape(x, depth + 1) for x in v[:8]] if depth < 4 else "[...]"
    if isinstance(v, str):
        return v[:200]
    return v


def action() -> str:
    try:
        return ACTION_FILE.read_text().strip().split()[0]
    except (OSError, IndexError):
        return "log"


def record(event: dict, act: str, note: str = "") -> None:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pid": os.getpid(),
        "action": act,
        "note": note,
        "keys": sorted(event),
        "hook_event_name": event.get("hook_event_name"),
        "tool_name": event.get("tool_name"),
        "tool_use_id": event.get("tool_use_id"),
        "permission_mode": event.get("permission_mode"),
        "session_id": event.get("session_id"),
        "agent_id": event.get("agent_id"),
        "agent_type": event.get("agent_type"),
        "tool_input": shape(event.get("tool_input")),
        "tool_response": shape(event.get("tool_response")),
        "error": shape(event.get("error")),
        "is_interrupt": event.get("is_interrupt"),
    }
    with LOG.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def pre_output(decision: str, reason: str, updated: dict | None = None) -> str:
    out = {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}
    if updated is not None:
        out["updatedInput"] = updated
    return json.dumps({"hookSpecificOutput": out})


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    act = action()
    name = event.get("hook_event_name")
    record(event, act)
    if name != "PreToolUse" or act == "log":
        return 0
    tool = str(event.get("tool_name") or "")
    if act == "ask":
        print(pre_output("ask", "PROBE: this is the reason text the owner should see in the box"))
    elif act == "allow":
        print(pre_output("allow", "PROBE: allowed by hook"))
    elif act == "answer" and tool == "AskUserQuestion":
        qs = (event.get("tool_input") or {}).get("questions") or []
        answers = {}
        for q in qs:
            opts = q.get("options") or []
            answers[q.get("question", "")] = (opts[0].get("label") if opts and isinstance(opts[0], dict) else "") or ""
        print(pre_output("allow", "PROBE: answered by hook", {"questions": qs, "answers": answers}))
    elif act == "exit1":
        print("PROBE: hook error", file=sys.stderr)
        return 1
    elif act == "exit2":
        print("PROBE: blocked by hook (exit 2)", file=sys.stderr)
        return 2
    elif act == "sleep":
        time.sleep(15)
    elif act == "dialog":
        shown = (json.dumps(event.get("tool_input"))[:300]).replace('"', "'")
        script = f'display dialog "PROBE: allow {tool}? {shown}" buttons {{"Deny","Allow"}} default button "Deny" giving up after 120'
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=130)
            ok = "button returned:Allow" in r.stdout
        except Exception as e:  # noqa: BLE001
            record(event, act, f"dialog failed: {e}")
            ok = False
        record(event, act, f"dialog answer allow={ok}")
        if ok:
            print(pre_output("allow", "PROBE: allowed by dialog"))
        else:
            print("PROBE: denied by dialog", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
