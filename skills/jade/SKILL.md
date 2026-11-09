---
name: jade
description: Use when about to claim work is verified or done, when choosing between real alternatives, when a `jev` or `jade ask` call returned a non-zero exit code, or when a tool call was blocked by the JADE gate or is waiting for a human permit. Explains how to ask Jev with evidence, what each exit code means, and what to do when blocked.
---

# JADE: using Jev without treating it as permission

JADE journals every Jev call, turns a probability into an exit code, and blocks risky tool calls until a human approves them. This skill is the how-to. The enforcement lives in the runtime (exit codes, hooks, permits), not in this text.

## Ask

```bash
jev verify "<one specific claim>" --state-file <file with raw tool output>
jev choice "<question>" -o name=one-line-tradeoff -o other=one-line-tradeoff -s "<state>"
jade ask question q.strategy.best_option --field goal="..." --field context=@notes.txt -o a=... -o b=...
```

- **Claim**: one atomic, checkable statement. Split a compound claim into several asks.
- **Evidence**: raw tool output, a diff, a log, a quoted document. Your own summary scores low and the companion check flags it. Write the output to a file and pass `--state-file`.
- **Choice**: at least two real options, each with a tradeoff. One option is rejected (exit 6).
- Global flags go before the subcommand: `jev --dry-run verify ...`. `--risk high` applies the stricter acceptance band.
- Skip Jev when a deterministic check answers it: `jade fast exit-code 0 "$?"`, `jade fast file-exists PATH`, `jade fast regex PATTERN FILE`. `jade route --claim ... --evidence-file ...` tells you FAST, JEV, DEEP or NEEDS_EVIDENCE.

## Read the exit code, not the number

| Exit | Meaning | Do |
|---|---|---|
| 0 | accept band | The claim is supported by this evidence. Still report the probability. |
| 3 | abstain | Not a yes. Add raw evidence and ask again, or say it is unverified. |
| 4 | reject | Not supported. Fix the work or the claim. |
| 5 | provider unavailable | No answer. Not a yes. Retry later or report unverified. |
| 6 | invalid question | Nothing was sent. Fix the question. |

After 3, 4, 5 or 6, record what you did: `jade ack --all --as revised|escalated|reported|dropped --note "..."`. The Stop hook reminds once if you do not.

Same claim and same state returns the cached answer, flagged `repeat_without_new_evidence`. Change the evidence, not the wording.

## When a tool call is blocked

High and critical actions (push, publish, remote shell or copy, deploy, service lifecycle, edits to agent policy or hooks, connected-app actions, cluster and merge actions through MCP) stop with a pending permit id.

1. Tell the user exactly what is waiting and the id.
2. The user runs `jade permit approve <id>` in their own terminal. You cannot, and `jade permit approve`, `jade hooks ...`, `jade gate ...` and `JADE_GATE_MODE=` are refused for you by design.
3. Retry the identical command once. A permit is single use and lasts 30 minutes. A different command needs its own permit.
4. Do not rephrase, split or wrap the command to get past the gate. Continue other work meanwhile.

A safety invariant block (for example pushing the default branch) cannot be approved at all.

## Never

- Treat a probability, from Jev or any model, as authorization.
- Report something as verified or done after a non-zero exit.
- Put secrets in state. JADE redacts known shapes and refuses the call if one survives; redact first anyway.

Health: `jade check`. Ledger summary: `jade ledger stats`. Design and policies: the `jade-decision-governance` repository, `docs/`.
