# PR-0 host probe (W-96)

Goal: find out, per permission mode, which way of asking the owner works. Plan: `docs/12-jev-decides-phase1.md` (plan v3, PR-0).
About 20 minutes of the owner's time, mostly clicks. Nothing here touches JADE, the ledger, secrets or any real project.

## Set up (once, in Terminal.app)

```bash
mkdir -p ~/jade-probe/.claude && cd ~/jade-probe && git init -q
```

Write `~/jade-probe/.claude/settings.json` (replace `PATH` with the absolute path of `probe_hook.py` in the JADE checkout):

```json
{"hooks": {
  "PreToolUse":  [{"matcher": "Bash|Write|Edit|AskUserQuestion", "hooks": [{"type": "command", "command": "python3 PATH", "timeout": 10}]}],
  "PostToolUse": [{"matcher": "Bash|Write|Edit|AskUserQuestion", "hooks": [{"type": "command", "command": "python3 PATH", "timeout": 10}]}],
  "PostToolUseFailure": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 PATH", "timeout": 10}]}],
  "PermissionRequest": [{"hooks": [{"type": "command", "command": "python3 PATH", "timeout": 10}]}]
}}
```

Open `~/jade-probe` as a new Claude Code session in the desktop app. JADE's own hooks are global, so the gate must be `advisory` while
this runs (Step 0). Log: `probe.log.jsonl` beside `probe_hook.py`. Action: put one word in `probe.action` (see `probe_hook.py`).

## Cells (the agent drives; the owner clicks and reports what appeared)

Run every cell in: auto, bypassPermissions (switch with Shift+Tab or the mode menu), and once in a subagent (ask the agent to start one that
runs the command).

| # | Action | The agent runs | The owner reports |
|---|---|---|---|
| 1 | `ask` | `echo hello` (Bash) | Did a box appear? Is the reason text shown? Click Deny: what does the agent see? |
| 2 | `ask` + 1 again | `echo hello` | Click Allow: does it run? Which events are in the log? |
| 3 | `allow` | `echo hello` | Did it run with no box? |
| 4 | `log` | an `AskUserQuestion` with two options | Was the box shown? Which events are logged? What does `tool_response` hold (answer field)? |
| 5 | `answer` | an `AskUserQuestion` | Was the question hidden or shown? What do PostToolUse `tool_input` / `tool_response` hold? |
| 6 | `ask` | a Bash call, with a second hook answering (add a PermissionRequest hook that prints `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`) | Did the box still appear? |
| 7 | `log` | a 1.5 KiB heredoc call `python3 - <<'EOF' ... EOF`, then AskUserQuestion, then the same call again, 5 times | How many of the 5 retries were byte-identical? |
| 8 | `dialog` | `echo hello`, in the main session, then in a subagent | Did the dialog show? Did the call run after Allow with no retry? |
| 9 | `exit1`, `exit2`, `sleep` | `echo hello` | Did the call run in each case? (Host version: `claude --version`.) Repeat 9 with `"onFailure": "block"` added to the hook entry. |
| 10 | `log` | `false` (fails), then `sleep 30` and interrupt it | Which events fire (PostToolUse, PostToolUseFailure)? Do they carry `tool_use_id`? |
| 11 | `ask` | `rm -rf ./*` in `~/jade-probe/empty` opened as the working directory | Box? Denied by the host regardless? |
| 12 | none | the owner types `jade gate advisory` in the app's Terminal panel; the agent runs `mcp__terminal__run_in_terminal` with the same, then from a script file | Which of the three were accepted as a human terminal? |
| 13 | none | the owner opens `/plugin` | Is `cc-plugin-sec-default` listed? |

## Result

Write one line per mode: `auto: native ask yes/no; chat question yes/no; dialog yes/no; subagent: ...`. A mode where no channel works stops
phase 1 until the owner decides again (plan v3, PR-0 stop rule).
