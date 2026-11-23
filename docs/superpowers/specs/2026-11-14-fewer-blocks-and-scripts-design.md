# Fewer blocks, joined commands and disciplined scripts (design)

Status: **v2, approved by the owner on 2026-11-14. No code is changed; build starts after 0.7.0 is merged.** Date: 2026-11-14. Written in simple English on purpose (the owner's English is not their first language). v2 folds in an adversarial review (8 bypasses and 9 plan defects found and fixed here).

Depends on: the "clear questions and bigger scopes" branch (`design-clear-questions`, release 0.7.0). This work starts only after 0.7.0 is merged.

## 1. Why

The owner wants JADE to ask less, but stay safe. Three sources of repeat questions:

1. The same kind of call is asked again and again (one permit per exact call, single use, 30 minutes).
2. A task needs several risky calls. Each one is a separate question. In 0.7.0, 4 edits to one file needed 4 permits, and twice the approved permits were never used. Checked on 2026-11-15: the permit's 30-minute TTL starts when the permit is created, not when the owner approves it. The loop of subagent block, report, owner approval and subagent resume took more than 30 minutes, so the permits had expired (state REVOKED, 0 uses, same `plan_hash` and same `file_sha` as the resent call). A subagent also cannot use a chat question: only the main session can. In the main session the same 4 edits passed with 4 chat clicks in a few minutes.
3. The same commands are typed by hand in many projects and sessions, instead of living in one reviewed script.

The owner also asked for discipline when scripts are written: a structured hierarchy, no one-task scripts, scripts that work together, continuous automatic audit, and continuous automatic cleanup of old, retired, unused, unreferenced, orphaned, trashed and temporary files, decisions and permissions.

## 2. What the code does today (checked, and probed)

- **Permits** (`jade/permits.py`). One permit is bound to one `plan_hash` (tool, command text, cwd), single use, TTL 1800 s. Approval takes one id at a time in a human terminal, and shows only the command text. There is no bundle approval.
- **Scripts are a gap.** These all assess as low today and run with no question, even when the file runs `gh pr merge` (probed):
  - `bash scripts/x.sh`, `scripts/x.sh` (no leading `./`): not seen as a script at all (`jade/blind.py:232-249` needs `./`, `../`, `/` or `~/`);
  - `printf x.sh | xargs bash`, `find . -exec bash {} \;`;
  - `make x`, `npm run deploy`, a git hook written to `.git/hooks/pre-commit` then `git commit`;
  - a script written into a PATH folder and run by its bare name.
- **What is read when a script is seen** (`./x.sh`, rule R-31, low, not gated): `_script_ho` (`jade/policy.py:932-948`) checks only the delete, money and release classes, and `_script_names_state` checks JADE state paths. Live wiring, mod and settings paths, SI-09..SI-12 are not checked. Unreadable or oversize (over 1 MiB) files are skipped silently.
- **Readers that already exist.** `blind.written_scripts` (`jade/blind.py:283`) reads a script that the same command writes, and feeds it to every rule. `jade/interp.py` already extracts commands from Python and Node code.
- **No content binding.** No fingerprint holds a script's content. A permit or answer for `bash x.sh` still fits after `x.sh` is changed.
- **Joined commands.** Read-only segments do not change `hold_key` and are not gated (`jade/policy.py:1244-1336`). Joining read-only commands is free for permits and memory. It is not free for the 0.7.0 merge scope, which needs `gh pr merge` alone in its call (`jade/scopes.py:113-122`).
- **Memory** (`jade/memory.py`). Exact key = `hold_key` plus fingerprints. Works only at level `low` or `medium`.
- **Cleanup.** `jade maintain` sweeps permits, decisions and test rows. Nothing cleans scripts, temp files, orphan scope rows or old hints.

## 3. Goals, non-goals, anti-goals

**Goals**
- Fewer questions for repeated safe work, measured (section 8).
- A script is approved once, bound to its exact content, its arguments and its project. One changed character or argument asks again.
- One question for a planned list of calls (a bundle).
- One place and one shape for scripts, with an index, a regular audit and a cleanup that proposes first.

**Non-goals**
- No change to the human-only classes (live JADE wiring, delete that cannot be undone, money, public release).
- No change to the 50-answer line for Jev, or to the levels.
- Not the repo skill for duplicate and issue watching (still parked).

**Anti-goals (a failure even if it works)**
- A script becomes a way around the gate.
- An approval covers code or arguments the owner did not see.
- Cleanup deletes something that cannot be recovered, or touches JADE's own state or a file git tracks.
- A bundle hides a risky item among safe ones, or teaches memory.

## 4. Approaches considered

- **A. Scripts only.** Read scripts with the gate's rules, approve by content. Big cut for repeated work. Nothing for a one-time list of risky calls.
- **B. Bundles only.** One question for a list. Repeated work still asks every session, and the script gap stays open.
- **C. Both, in phases, safety first (recommended).** Close the script gap in shadow mode first and measure. Then enforce, then content approval, then bundles, then the library, audit and cleanup. Each phase is its own PR and the owner can stop after any of them.

**Pick: C.** The script gap is a real hole today. Approval by content only makes sense once content is read with the same rules. Shadow mode first shows how many new questions enforcing would add, before anyone sees them.

## 5. Design

### Phase 0. Measure first

A script in `evals/` reads the ledger and prints counts only: gated calls, questions and terminal approvals per 100 calls, the top 10 repeated call shapes, and the calls that ran a script. The data answers "which command repeats most". The `blind_spot` event is journaled once per session and label, so script runs are counted from gate events, not from it.

### Phase 0b. Permit time counts from approval

A pending permit keeps its 30 minutes, counted from the moment the owner approves it. A pending permit that is never approved still expires 24 hours after it was created. The gate message for a blocked subagent call says: the main session can resend this call and ask in the chat. This cuts expired permits with no new power: each permit is still single use and bound to the exact call.

### Phase 1. A script is read like an inline command

**1a. Find every script a call runs.**
- A program word with a `/` (`scripts/x.sh`) is resolved against the cwd. A bare program word is resolved through PATH; a hit outside the system folders (`/usr`, `/bin`, `/sbin`, `/System`, `/opt/homebrew`, the uv tool folder) is read as a script.
- `jx <area> <verb>` is resolved by the gate to the file it would run.
- A shell or interpreter runner with no literal file (`xargs bash`, `find -exec bash {}`, `bash "$f"`, a glob) gets **R-31b**.
- A write to `.git/hooks/*` is high (live hook code).
- `make`, `just`, `npm run`, `pytest` (conftest) are not read. This stays a residual the owner accepts (section 9), and a backlog item.

**1b. Read it with every rule.** The script bytes are added to the call's `extra` text, the way `written_scripts` already does, so every rule and safety invariant runs on it (risk rules, human-only, live wiring, SI-09..SI-12). Invariants that read only the command text today are extended to `extra`. Python and Node scripts go through `jade/interp.py`. Nested scripts are read to depth 2. When a nested path has several possible folders, every one must be read, or the call is R-31b.

**1c. When reading is not enough: R-31b (high).** Matched by a marker `blind:script-unread`. Any of: unreadable, over 1 MiB, nested deeper than 2, an unknown language, interp could not extract all commands, or the script text itself runs unseen code (`source "$X"`, `eval`, a command word that is an expansion, `while read c; do $c`). R-31b asks every time. It can get a single-use permit. It is never remembered, never in a scope.

**1d. Bind to content.** A new fingerprint `script_sha` holds, for each script read: its sha256 (from the same bytes the assessment read, carried on the assessment, never read twice), the full argument list, the env prefix assignments, and the project root. `try_consume` and `consumed_by_call` refuse a permit that lacks `script_sha` when the call has one (as they already do for `file_sha`).

**1e. Show it.** `jade permit show` and `jade permit approve` print the script path, sha256 (first 12 characters) and the gated lines found in it. The chat question shows the same.

**1f. Shadow first.** Phase 1 ships with the new level journaled as `would_gate` and not enforced. After at least 7 days, the phase 0 script prints how many extra questions enforcing would add. The owner turns it on with `jade gate scripts enforce` (human terminal).

A script with no gated lines stays ungated and asks nothing, as today, but now it is really read.

### Phase 2. Approve a script once

- **Run the checked copy.** Before any standing approval exists, the gate copies each read script to a content-addressed file (`<cache>/scripts/<sha>.sh`, read-only) and rewrites the call to run the copy (`updatedInput`). A file changed after the check no longer matters. If the rewrite is not possible (the host does not accept `updatedInput` for this call), standing approval is not offered.
- The chat question for a script call adds one choice: **"Allow, and suggest this exact script for later"**. The click records a hint only (owner decision 7). `jade review confirm` in a terminal makes it standing.
- A fully read script loses its blind label, so the 0.7.0 scope rules can see it as a plain call.
- Lifetime as for other exact rows: 30 days after last use, never more than 180 days. Undo with `jade knows rm ID`.
- Never: R-31b, human-only content, live JADE wiring, R-21 or R-22 content.

### Phase 3. One question for a list (bundle)

- The agent writes the calls it plans into a file and runs `jade bundle propose FILE` (at most 10 calls). That command is itself a gated call: the hook assesses every listed call, and the question for this one call is the bundle question. The text comes only from the gate.
- The question lists each call in one plain line with its risk line. One "Allow all listed" creates one sealed (HMAC, like permits) single-use grant per call, valid 30 minutes.
- **Binding.** A command item binds its `plan_hash` and fingerprints. An Edit item binds the path and the digest of its own input (old and new text), not the whole file: this fixes the 0.7.0 retry problem, because earlier edits in the same bundle change the file. A Write item binds path and content digest.
- **Never in a bundle**, listed as "asked alone": human-only, live JADE wiring, blocked, R-31b, pushes and merges (their `git_head` binding cannot be known in advance).
- A bundle writes no `human_answers` row at all. It teaches nothing.
- A call that matches no grant asks as today. The message names the closest item and the first differing position.

### Phase 4. A script library with rules

- **Place.** Per project: `scripts/<area>/<verb>.sh`. Shared across projects: `~/scripts/<area>/<verb>.sh`. Entry point `jx <area> <verb> [args]`: project first, then shared. The gate resolves `jx` (phase 1a).
- **Header.** Every script starts with: purpose, owner, declared capability (`read`, `write`, `network`, `delete`), callers.
- **Index.** `scripts/INDEX.toml`, generated: path, purpose, capability, sha256, last use, callers.
- **`jade scripts audit` checks:** header present; declared capability at least what the gate finds; no one-task script (one caller, one use in 30 days); no duplicate (same normalized content); unreferenced (no caller, no use in 30 days); stale index; no human-only action inside; no R-31b pattern inside.
- **`jade scripts suggest`:** the same call shape 3 or more times in 7 days in one project gives a proposed script. It proposes only.

### Phase 5. Cleanup that proposes first

- `jade tidy` lists, grouped and counted: expired or revoked permits and bundle grants, hints older than 30 days never confirmed, revoked memory rows, scope rows whose exact row is gone, scripts flagged by the audit, checked copies no row refers to, and temp files under known temp roots (the session scratchpad, `/tmp/jr*`, probe output under `evals/probes/*/`).
- `jade tidy --apply` needs a human terminal. Files go to the system Trash (`~/.Trash` on macOS, `$XDG_DATA_HOME/Trash/files` on Linux), never a hard delete. Ledger rows are marked retired, not dropped. `jade ledger purge` stays the only hard delete.
- Never touched: JADE's own state and config, files git tracks, anything outside the listed roots.
- `jade maintain` runs audit and tidy in report mode. `jade check` shows "N items to tidy".

### Phase 6. Joined commands (docs only)

Agent texts get: join read-only commands in one call; put each risky action in its own call or in a bundle; run `gh pr merge` alone so the merge scope can match; repeated work goes into a `jx` script.

## 6. Safety table

| Item | Remembered? | Widest approval |
|---|---|---|
| Fully read script, no gated lines | not needed | runs, no question |
| Fully read script with gated lines, no human-only | yes, after confirm | exact content, arguments, project; run from the checked copy |
| R-31b script (not fully readable, or runs unseen code) | no | single-use permit |
| Script with human-only, live wiring, R-21 or R-22 content | no | asks every time |
| Bundle item | no | one use, 30 minutes |
| Human-only, live wiring, push, merge | no | never in a bundle |

## 7. Constraints

- Tests first, one change at a time. Full gate green on this Mac and on Linux CI.
- Question text only from the gate. No secret in any text, index or log.
- Each phase is one PR, merged only by the owner. Tags and releases stay the owner's.
- Script reads are never cached across calls (no `lru_cache`).
- New rules (R-31b, the `.git/hooks` write rule) go into `policies/` with tests.

## 8. Success, measured by the phase 0 script

- Questions per 100 gated calls after phase 2: lower than the phase 0 baseline, at the same level.
- Repeat questions for the same confirmed script: zero.
- Scripts run from a `/` path, PATH, `jx` or a runner with no file, unread and unlabelled: zero after phase 1.
- Wrong allows found by `jade review mark wrong`: no rise.

## 9. Risks the owner accepts

- `make`, `just`, `npm run` and test runners run code the gate does not read.
- Phase 1 enforce adds questions before phase 2 removes them. Shadow mode measures how many first.
- A script that reads data files at run time (not code) can act differently on different data. The arguments and env are bound; the data is not.

## 10. Left to the builder

The exact header format; the INDEX.toml fields beyond the list; the bundle file format (JSON list of `{tool, input}`); the plain text of each new line; the system-folder list and the temp-roots list (each kept in one place, with a test); the cache folder for checked copies (outside JADE state, so tidy may list it).
