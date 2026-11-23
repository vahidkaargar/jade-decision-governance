# Open items after 0.7.0: grouped, with a fix plan by agent role

Date: 2026-11-15. Written in simple English on purpose. Status: **draft, waiting for the owner. Nothing is built.**

Starts only after PR 42 (0.7.0) is merged into `origin/main`. Every branch below is cut from merged `origin/main`.

## 1. Items by category

| Category | Items | Who fixes |
|---|---|---|
| **A. Security gaps** | Scripts run unasked (`bash scripts/x.sh`, `xargs bash`, PATH scripts); `make`/`npm run` unread (accepted risk); permit time starts at creation; merge scope does not read global `~/.gitconfig` `insteadOf` | Agents (A1 is the fewer-blocks plan) |
| **B. Bugs** | Control characters printed in `jade knows` / `jade review` lines; `glab mr merge` value read as the MR number; `git push origin :main`, `+main`, `HEAD:main` all say "Push to a remote." | Agents |
| **C. Code debt** | `askplain` copies `shellnorm.SPLIT` and uses private `policy` helpers; `scopes` uses private `memory` names; `scopes.keys_for` reads the disk on every gated call; knows template: a newer deny does not hide an older allow; orphan scope rows | Agents |
| **D. Text debt** | Hard words in risk lines ("reflog", "boot service"); `review confirm` summary line over 20 words | Agents |
| **E. Test debt** | No test for the human-only path with no segment keys; no test for a label over 90 characters; 20+6 word slack in one test; empty-HOME `CI=1` run (covered by Linux CI) | Agents, in the PR that owns the code |
| **F. Cleanup (agent)** | Build workspace `.superpowers/sdd/2026-11-14-clear-questions-and-scopes/`; worktree `harness-tools-w97`; commit the approved fewer-blocks spec and plan | Agents, after PR 42 merges |
| **G. Cleanup (owner)** | Let `prm_85da091e7ef6`, `prm_24857c2e877e`, `prm_462046ea9adc` expire; check `prm_5e937a395060` (not ours); delete `/tmp/jr1` and the scratchpad `r2` folder; keep or delete `evals/probes/hook_ask/probe.*` | Owner |
| **H. Owner decisions** | Merge PR 42; tag 0.7.0; level is `off` (back with `jade level low`); mark `dec_838edca8bb53`; `jade review confirm`; 50-answer line; the parked repo skill | Owner |

## 2. Agent roles

| Role | Model | Does |
|---|---|---|
| Lead (this session) | session model | Cuts branches, dispatches, owns the ledger, re-runs the full gate, opens PRs after the owner's yes. Never approves a permit. |
| Security engineer | most capable | A2 (global gitconfig), adversarial review of every PR |
| Backend engineer | standard | B bugs |
| Refactor engineer | standard | C debt |
| QA engineer | standard | E tests, timing test for `keys_for` |
| Technical writer | standard | D text, CHANGELOG, docs rows |
| Task reviewer | standard | Spec + quality gate per task |
| Final reviewer | most capable | Whole-PR review before the owner merges |

Rules for every agent: tests first; `uv run ruff check` and `uv run pytest -q` green; explicit `git add` paths; no push, no PR, no permit approval; no subagents of their own. One writer per worktree. Agents on different PRs work in separate worktrees, so they can run at the same time.

Gate note: at level `off` JADE warns but does not block edits under `jade/`, so agents can edit there. If the owner raises the level first, agents write tests plus an exact edit list, and the lead applies the edits in the main session with one chat click each (the 0.7.0 method).

## 3. Work packages (PRs)

Three PRs run in parallel (different files), then the fewer-blocks plan.

### PR-B "plain text fixes" (`jade/askplain.py`, its tests). Backend engineer + technical writer.
1. Push refspecs: `:main` says "Delete the remote branch main."; `+main` says "Force push to main."; `HEAD:main` says "Push to main." Tests per form.
2. `glab mr merge`: a value flag before the number gives "Merge a merge request." (same rule as `gh pr merge`). Tests.
3. Risk lines without hard words: replace "reflog", "boot service" and every other term a test lists. Test: no word from a jargon list appears in `RISK_TEXT`.
4. Tests from E: the human-only path with no segment keys; remove the 20+6 slack (a sentence is 20 words or fewer).

### PR-C "scopes hardening" (`jade/scopes.py`, `jade/memory.py`, tests). Security engineer + refactor engineer + QA engineer.
1. A2: no merge scope when the user's global git config (`~/.gitconfig`, `$XDG_CONFIG_HOME/git/config`) has any `url.*.insteadOf` or `include`, or when `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_*` is set in the environment (fail closed). Tests with a temp home folder.
2. Public names in `memory` for what `scopes` uses (`memory.INLINE`, etc.); `scopes` stops importing private names. No behaviour change: the existing tests stay green.
3. `keys_for` timing: a test that 1000 gated lookups stay under a set budget; memoise the project lookup per call if the test needs it.
4. A label over 90 characters test (E).

### PR-D "review and knows output" (`jade/ops.py`, tests). Backend engineer + technical writer.
1. Every line `jade knows list`, `jade review` and `jade review confirm` print passes the call label and scope label through one printable-one-line helper (move `scopes._one_line` to a shared module). Test: ESC, BEL and newline never reach the output.
2. The `review confirm` summary in sentences of 20 words or fewer. Test.
3. Knows template: a newer deny of the same group hides the older allow. Test.

### Then: the fewer-blocks plan (`docs/superpowers/plans/2026-11-14-fewer-blocks-and-scripts.md`)
Covers the A1 gaps and the orphan scope rows (C): Task 1b (permit time from approval), phase 1 (scripts read in shadow mode), and later phases. It starts after PR-B, PR-C and PR-D merge, because its phase 1 and PR-C both touch the gate path. The lead commits the spec and plan on the first fewer-blocks branch.

## 4. Order

1. Owner: merge PR 42, then say "start".
2. Lead: cleanup F (delete the workspace, remove the worktree), and cut three worktrees from `origin/main`.
3. In parallel: PR-B, PR-C, PR-D. Each: implementer, then task review, then fix loop (at most 5 rounds), then the security engineer's adversarial review, then the full gate run again by the lead.
4. Lead asks the owner before each push and PR. The owner merges.
5. Fewer-blocks plan, phase by phase, one PR each.
6. Owner items G and H at any time.

## 5. Done means

- Each PR: ruff clean; full suite green on this Mac; Linux CI green; a final review with no Critical or Important finding left open.
- No new question text from the agent; no secret in any text.
- Every residual becomes a backlog line in the PR body, not a silent omission.
