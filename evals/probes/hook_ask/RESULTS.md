# PR-0 host probe: results (W-96)

Run 2026-11-13 on the owner's Mac, host Claude Code 2.1.314. Cells are those of `README.md`. A driver session changed `probe.action` and read `probe.log.jsonl`; a probe session started by the driver ran the commands in `~/jade-probe`. The first probe session the owner opened by hand never loaded the project hooks (cause not found: folder or trust prompt); the driver-started session did.

| Cell | Result |
|---|---|
| 1 native `ask`, auto | `PermissionRequest` fires, the call runs with no box (the host decides itself, harmless command). Native ask does not reach the owner. |
| 1 native `ask`, bypassPermissions | Same: `PermissionRequest` fires, runs 2 s later, no box. |
| 11 native `ask` on `rm -rf ./*`, auto | Denied by the host before any `PermissionRequest`; the hook's `ask` was not consulted. The owner reports clicking Allow, the agent still got "Permission ... has been denied". |
| 4 AskUserQuestion, log only, auto | The owner is prompted (`PermissionRequest` fires). PostToolUse `tool_input.answers` and `tool_response.answers` both hold the answer. |
| 5 hook answers (`updatedInput.answers` + allow), auto | No box, no `PermissionRequest`, answer returned in about 4 s. A hook-written answer looks like a human one in PostToolUse. A human was prompted only if a `PermissionRequest` for AskUserQuestion fired. |
| 5b hook rewrites the question (`updatedInput`, no decision), auto and bypassPermissions | The owner sees the hook-written question and answers it; the agent sees only the rewritten text. This is the chat channel. Confirmed by the owner in both modes. |
| 8 dialog, main session, auto | The hook waits for the click; Allow lets the call run with no retry. |
| 8b AskUserQuestion in a subagent | The subagent has no AskUserQuestion tool. The chat channel does not exist there. |
| 8c/8d dialog, subagent | With the 10 s hook timeout the hook is killed (blocked by onFailure). With timeout 130 the owner's click after 35 s lets the call run. The event carries `agent_id` and `agent_type`. |
| 9 hook exit 1 / exit 2 / timeout, no onFailure | exit 1 runs, exit 2 blocks (reason shown), timeout runs (fail open). |
| 9b same, `onFailure: "block"` | All three block, with "failed; blocking because onFailure is 'block'" and "timed out; blocking ...". |
| 10 failed call | `PostToolUseFailure` fires (not PostToolUse), with `error`, `is_interrupt`, `tool_use_id`. Interrupt not tested. |
| 7 retries | 5 attempts blocked; the command length and first 200 characters were identical, only `description` changed each time. Byte equality of the whole command is not proven (the log keeps 200 characters). Key a retry on the command, never on the whole tool input. |
| 12 terminal panel | Open: the agent typed `jade gate advisory` into a panel tab; the read of its output was denied by the auto-mode classifier. The owner can read tab `c1`. |
| 13 `/plugin` | Open: needs the owner. |
| Not run | bypassPermissions for cells 8, 8b, 8c; the interrupt part of 10; the variant of cell 6 where a PermissionRequest hook answers the box. |

## What this means for the plan (docs/12, plan v3)

- The chat question works in both owner modes in the main session: the hook rewrites the question, PostToolUse reads the answer, and a `PermissionRequest` for AskUserQuestion is the proof that a human was prompted.
- The native Allow box is not a channel in auto or bypassPermissions.
- Subagents have no AskUserQuestion; they need the dialog, with a hook timeout above the dialog wait.
- `onFailure: "block"` works on 2.1.314: a crashed or hung gate can fail closed.
- A failed call fires `PostToolUseFailure`; the ask state must handle it.
- A retry must be matched on the command text, never on the whole tool input.
