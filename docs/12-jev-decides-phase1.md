# 12 — The human answers in the chat (release 0.5.0); Jev decides (release 0.6.0, planned)

Status: **0.5.0 is implemented (W-96 to W-100d) and not yet released or installed.** Memory and Jev deciding in the human's place (0.6.0) are planned and not part of this release. This page says what the code does today.

## 1. Why

Before 0.5.0 every high call under `enforce` with the signed backend needed a typed code, the sudo password and a helper code in a terminal, then a "retry" message to the agent. On 2026-11-13 the owner gave 18 such approvals in one afternoon and then stopped; 18 more calls expired. The four pains: the same permission asked again, the same obvious question asked again, every answer typed in a terminal, and a chat that stops until a new prompt.

0.5.0 removes the last two. It does not reduce how often the human is asked: it turns each terminal approval into one click, in the chat or in a dialog, and the agent carries on in the same turn. Fewer questions are 0.6.0.

## 2. The level

One value for the whole Mac, set only by a human in a terminal: `jade level high` turns the new behaviour on, `jade level off` makes JADE only watch, `jade level unset` returns to 0.4.0. The value lives in the JADE config file; a bad value counts as unset. `jade check` shows it. Low and medium arrive with 0.6.0.

| Level | What the gate does |
|---|---|
| unset | As 0.4.0: a high call waits for the signed terminal permit. |
| off | JADE only watches. High calls and ordinary safety invariants are advisory. The four governance invariants (SI-09 to SI-12: the permit, the permit key, the state and the config directory) still block. |
| high | A high call is asked, in the chat or a dialog (section 3). The signed permit stays for live JADE wiring and for what no channel reaches. |

A human-only class (section 4) is high work once a level is set, whatever its rule says. Safety invariants are never asked: nobody can approve them.

## 3. How a call is asked

The first channel that fits the call is used.

1. **Chat question.** Main session, host mode `auto` or `bypassPermissions`, gate mode `enforce`.
   - The gated call exits with a fixed text: call AskUserQuestion with a question that starts with the token `[jade:ask_<id>]`, then retry the identical call.
   - The PreToolUse hook on that question **replaces its whole text** with the hook's own: level, rule, rationale, the command (with "N characters not shown" when it is cut), and the size and head of any content the call writes. The agent's wording never reaches you.
   - The PostToolUse hook reads your answer from the host's `tool_response`. **Allow** makes a grant: single use, 10 minutes, this session, this exact call (the same hold key and fingerprints, so quoting and an env prefix do not matter). **Deny** refuses the retry, and the same call is not asked again for 10 minutes. The 3rd Deny of the same call is a plain refusal for good.
   - The retry that uses the grant opens a decision (route ASK, with an `ask` block that records the answer), so SI-07 still holds an unknown outcome of an irreversible command.
2. **macOS dialog.** A subagent has no AskUserQuestion. At a level, on a Mac, the hook shows its own dialog and waits (up to 120 s; the PreToolUse hook timeout is 130 s). Allow runs the call, Deny refuses it, no answer falls back to the signed permit and the same call is not shown again for 5 minutes.
3. **Signed terminal permit.** Live JADE wiring, a call with a secret-shaped value, a host mode that is neither `auto` nor `bypassPermissions` (headless, `dontAsk`, unknown), a non-macOS host for a subagent, and every call when no level is set.

Around the channels:

- Every answer is stored as a `hint` in the `human_answers` table. A hint decides nothing and goes to no model. `jade review` lists them (read-only).
- While an ask of the session is open (the chat question unanswered) or a dialog is on screen (any session), screen-control calls are refused so that an agent cannot press Allow: the `mcp__computer-use__*` and `mcp__MacOS-MCP__*` tools, and a shell or terminal-tool call that runs `osascript`, `cliclick` or `shortcuts run`.
- A new ask posts one macOS notification (rule and folder name, never the command).
- When the agent tries to stop with an ask that never reached the question, the Stop hook points at it once.
- 30 asks of a session that expired unanswered pause asking for that session until the human answers or they age out of the last hour. Gated calls then take the signed permit path (a permit id to approve in your terminal), so something waits for you when you come back.
- A call that failed fires `PostToolUseFailure`: the decision it ran under closes as an observed failure, so the next identical call is asked again instead of held. An interrupted call may have run in part: its hold stays.

## 4. What only a human can decide

Human-only classes (`ho_ids` on the assessment and on the decision):

| Class | What it is | Channel |
|---|---|---|
| HO-wiring | A write into the running install, the uv tool directory, the launchers, the hook files, the JADE config or state, or `uv tool` install, upgrade or removal of JADE | signed permit |
| HO-checkout-install | An install built from a JADE checkout | signed permit |
| HO-settings-shadow | A project settings write that disables or shadows the JADE hooks | signed permit |
| HO-state-path | A script that names the JADE state paths | signed permit |
| HO-mod | A write under the Claude mod directories | chat or dialog, never memory |
| HO-R17 | Skipping the pre-push guard | chat or dialog |
| HO-terminal | A call through the terminal panel tools (the agent typing into the human's shell) | chat or dialog |

JADE's own source in a checkout is plain high work, asked like any other call. `tests/acceptance/test_r13_walk_w100d.py` walks every alternative of R-13 and pins which of these it lands in.

## 5. What you see when something fails

| You see | Cause | Do |
|---|---|---|
| The agent says the gate wants a permit and gives an id | no level is set, or the call is live wiring, a secret, or a mode the chat does not reach | `jade permit approve <id>` in your terminal, or `jade level high` |
| A question starting with `[jade:ask_...]` | the chat channel | Allow or Deny |
| A dialog on your screen | a subagent call | Allow or Deny; no answer in 120 s falls back to the permit |
| A permit id although a level is set | 30 asks of the session expired unanswered, so asking is paused | `jade permit approve <id>`, or answer one ask; it ends within the hour |
| "denied this call 3 times" | you said no three times | nothing: the agent was told to stop |
| `jade check` fails `hooks installed` | hooks from before 0.5.0 (shorter PreToolUse timeout, no failure hook, no AskUserQuestion matcher) | `jade hooks install`, then open a new session |
| Nothing happens on a high call | the hook is dead or the host is older than 2.1.295 and fails open | `jade check`; `jade setup` says what is todo |

## 6. Owner steps for 0.5.0

1. Back up `~/.local/share/jade`.
2. `jade upgrade`: read the file list it prints and type `yes`.
3. `jade hooks install`, then open a new Claude Code session.
4. `jade check` and `jade ledger stats`: read the gate overrides, overlay, hooks installed and the safety block count.
5. `jade gate enforce`, then `jade level high`, then `jade check`.
6. Apply the CLAUDE.md amendment: the rule that a probability is never permission now says that a chat or dialog Allow is a human answer (the text is in `docs/claude-md-jev-section.md`; `jade claude-md apply` is yours to run).

Measure it with `jade ledger stats`: the `asks` block gives answers by channel and terminal approvals per 100 gated calls. The target is terminal approvals only for live JADE wiring.

## 7. Risks accepted

- Any program the agent starts, not only one in your terminal, can change the level or the config file, or fake one hook event. A fake approval covers one call.
- An agent that drives the mouse can press Allow when no ask is open to pause it, through a screen-control tool the name list does not know, through a background `osascript` loop started before the ask, or by a `computer_batch` already in flight.
- `uv tool install --force` of another package can overwrite the `jade` launcher; it is not classed as live wiring.
- The ask button labels are fixed (Allow, Deny), with no per-ask nonce.
- `jade upgrade` lists changed files from GitHub's compare answer, which caps at 300 files and a size; a name that cannot be shown safely is counted and the note says to read the diff on GitHub before typing yes. A ref behind the installed commit is shown as a rollback.
- A mod loaded in Claude Code can answer a chat question or approve a call JADE blocked. Mods are not locked.
- The chat channel rests on host behaviour the Claude Code documentation does not describe (`evals/probes/hook_ask/RESULTS.md`, Claude Code 2.1.314). A host update can break it. `jade check` reports the hooks; the probe can be re-run.
- Hosts older than 2.1.295 ignore `onFailure` and fail open on a hook crash or timeout.
- For a long command you approve a summary plus the head and tail, not the full text.
- Until the new hooks are installed, nothing in 0.5.0 runs: the gate is as it was.

## 8. Not in 0.5.0

Memory of confirmed answers, Jev deciding in your place, `jade review confirm`, `jade knows`, levels low and medium, batch asks, and the per-project level. They are the 0.6.0 plan.
