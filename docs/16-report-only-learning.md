# Report-only learning and the case ratchet (V6-32)

This page says what V6-32 adds, what it cannot do, and how to undo it. The idea is small: JADE counts what happened and writes a report. A human reads the report and decides. Nothing in this card changes a policy file, a threshold, a memory row, a label or a case file.

## Why

Most of what a learning loop needs is counting plus a report for a human: which blocks happen most, which calls the owner marked wrong, which rule asks a lot and is always allowed. Until now the ledger did not say which safety invariant stopped a call (W-74), and a call marked wrong was only retired from memory. It did not become a permanent test.

## What changed

| Piece | What it does |
|---|---|
| The invariant id in the ledger | Each `safety_block` event now has `invariants` in its detail, for example `["SI-08"]`. An SI-07 hold writes `["SI-07"]`. The guard text of the event is the same as before. Old rows have no such key; the report reads their guard text instead and counts how many rows it had to do that for. This is additive: no column and no schema change. |
| `jade learn blockers` | What stopped calls in the window (default 30 days; `--days 0` is all time): safety blocks by invariant id, advisory `would_block` and gated calls by rule id, and the asks by rule beside them. `--sample N` lists N blocker rows, fixed by a seed, with ids only, for your precision check. `--json` prints one line. |
| `jade learn policy` | One row per rule: asks, allowed, denied, no answer, wrong marks, and a word of advice. Friction and error are side by side. The window (`--days`, default 30) counts the asks, the answers and the advisory blocks. Wrong marks and human-only asks always count all time, so the advice for one ledger is the same for any window. There is no "unnecessary asks" number: an allow answer is a hint, not evidence, and the audit of silent runs is `not measured` until V6-29. |
| `jade learn cases` | A diff that adds a regression case to `evals/regression/cases.jsonl` for each call you marked wrong (`jade review mark ID wrong`), however long ago. It has no window. It prints the diff, or writes it to a new file with `--out`. It applies nothing. |
| `evals/case_ratchet.py check` | Runs the proposed cases on the version that erred and on the fix. A case is `ready` only if it fails on the first and passes on the second. |
| `evals/learn_replay.py` | Replays recorded calls against each rule that the table marks `review`, and counts the human-only calls that rule decides, one by one. |

## The advice words

| Advice | Meaning |
|---|---|
| `protected` | A human edits this rule. Learning never proposes a change. It covers critical rules (shell rules and MCP rules such as M-01), rules in `memory.NEVER_RULES` or `proxy.NEVER_RULES`, any rule that ever had a human-only ask, and any rule id that the risk policy has no level for (JADE cannot tell if it is critical). |
| `hold` | A call this rule covers was marked wrong, at any time. Look at that first. |
| `too_few` | Fewer than 20 answers. |
| `keep` | You denied some of the asks. |
| `review` | At least 20 answers, all allow, no wrong mark. A human may look for a narrow scope. Replay it first. The table never says "loosen". |

The table never gives a `review` to a `protected` rule, however many allows it has. Learning also never changes the safety invariants SI-xx, the human-only classes, permits, the autonomy level, the learning limits or the labels. A report has no way to do it.

## The case ratchet, step by step

1. `jade review mark ID wrong` (you, at a terminal). This already retires the standing answer.
2. `jade learn cases --out /some/new/file.diff`. For each wrong mark JADE builds a plain `gate` case that says the call must be stopped. The twin (a harmless look-alike) is written by a human: put it in a records file as `twin_command`. With no twin the case says `twin: unverified`, because a model-written label carries known error and JADE never writes one.
3. `uv run python evals/case_ratchet.py check /some/new/file.diff --erred <commit that erred> --fixed <commit with the fix>`. Verdicts: `ready`, `not reproduced`, `not fixed`, `twin ok`, or a twin failure. A row is a twin only if its `twin_of` names a case of the same file and it expects the opposite of that case; any other row with `twin_of` fails. Each `id` is used once, and an `id` and a `class` are one line of printable text. `not reproduced` is common and honest: a wrong silent allow is usually an error of a standing answer or of Jev, not of a rule, so a plain case passes on the old version too. A human then writes a test with that state.
4. A human reads the diff, applies it with `git apply`, and merges it in a normal pull request.

A record that cannot be a safe case is skipped with its reason: a command that may be cut (500 characters or more), a redacted part, a real host, a home or user path, or a tool that is not Bash. A real host is a URL or an address that is not a reserved name, a `user@host`, a dotted name that ends in a common top-level word (`.com`, `.org`, `.io`, `.ir`, `.internal` and others), or the first argument of `ssh` and its kind. A home or user path is `/Users`, `/home`, `/root`, `/var/root`, `/Volumes` or `~name` in any spelling and any letter case. The check is cautious: it can skip a harmless command, and then a human rewrites it with a `.test` or `.example` name and a neutral path. The three characters U+2028, U+2029 and U+0085 are written as `\u` escapes in the diff, so they cannot cut a case in two.

Two error kinds have no producer in the ledger yet: an audit false allow (needs V6-29) and a counterfactual false reject. `jade learn cases --records FILE` takes them from a JSONL file with `kind`, `command` and optional `rule_id`, `tool` and `twin_command`. Nothing detects them for you.

## What a report can never do

- It writes no policy file, no threshold, no memory row, no label, no case file and no ledger row. `tests/test_learn_v632.py` hashes `policies/`, `evals/`, the config directory and the work items before and after every command, and counts every ledger table.
- `jade/learn.py` has one write, `write_out`. It refuses anything inside the checkout or the installed package, the config directory (config, overlays, calibration, pins), the policy directory and the JADE home, and it never overwrites a file. A path that only differs in letter case or Unicode form is the same place (the Mac disk does not tell them apart), so it is refused too.
- A report prints counts, rule ids, invariant ids and times. It does not print a command. A proposed case holds a command, because a case is a command; you read the diff before you merge it.

## Risks

- A wrong report still steers you. Check it: `jade learn blockers --sample 30` gives a fixed sample. Read each row's class against the rule text. The target is at least 80% right. This check is yours to run; no one has run it yet.
- The replay is conservative and simple. A rule decides a call by its id, so the replay applies the candidate level to the calls that rule decides and does not run the classifier again with a changed rule. It counts every human-only call the rule decides, although at a level the hook raises such a call to high anyway.
- An empty corpus proves nothing: a candidate with no call in the corpus is `not_replayed`.

## Migration

The `invariants` key in the event detail. No schema change.

## Rollback

Revert the commit. The `invariants` key stays in old events and is ignored by old code. Delete any diff you wrote with `--out`.

## Not done here

- The precision check of 20 to 30 rows per report (yours).
- The replay on your real transcripts (yours, `evals/learn_replay.py` with no input reads `~/.claude/projects`).
- The audit false-allow producer (V6-29) and the counterfactual false-reject producer (none exists).
- W-74 stays open: the field is written, but the false-positive counts of SI-09 to SI-11 are not yet re-measured from a real advisory week.
