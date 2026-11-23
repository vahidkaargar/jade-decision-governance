# JADE v6 implementation handoff

This section answers one question: how can a future Claude Code session build JADE v6 in the right order, with the right checks, without having to rebuild the planning discussion? It gives the standing rules, the preconditions, a first-session checklist, one repeatable loop for every step, an ordered list of steps with exact commands, and a resume protocol. It is a plan. Nothing here is built, merged or approved. The task cards of section 12 (the roadmap, tasks V6-01 to V6-34) hold the reasons and risks. This section holds the order of work and how to run it.

## 0. How to read this section

**Tags.** [VERIFIED] has evidence: a file, a command I ran in this session, or an evidence id (A1 to A8, R1 to R6, or a sibling section: d03 is section 03, and so on). [INFERENCE] is my reasoning. [RECOMMENDATION] is a design proposal. [UNRESOLVED] was not settled. Sibling sections are proposals; I cite them as the source of an idea, never as proof.

**Every step below is a [RECOMMENDATION].** A step marked "safety change: yes" changes what JADE blocks, asks or remembers. The owner must say yes in chat before the agent builds it. The owner is the human.

**Facts I re-checked for this section** (read-only, this session):
- [VERIFIED `git rev-parse origin/main`] `origin/main` is `e9412f33f8678cc41ec444b6de48849c723ba158` (0.6.1 plus PR 48).
- [VERIFIED `gh pr list`] Open PRs: 42 (head `design-clear-questions`, `8866872`, base main); 43 (`open-items-plain-text`, `3b5a456`) and 44 (`open-items-scopes`, `ed0d26d`) and 46 (`open-items-shell-startup`, `a18bf71`), all based on `design-clear-questions`; 45 (`open-items-review-output`, `8c8163e`, base `open-items-scopes`); 47 (`claude/heuristic-hamilton-0eae1d`, `c676801`, base main).
- [VERIFIED `git worktree list`] The owner's main checkout is `/Users/example/Documents/projects/jade` on branch `harness-tools-w97` at `e2efe02`. It is dirty (A1: 236 mode changes). The folder `.claude/worktrees/harness-tools-w97` holds a different branch, `design-clear-questions`. This is a name trap. A worktree `plan-v6` (branch `plan-jade-v6-master`, `e9412f3`) holds the planning work.
- [VERIFIED `jev verify --help`, `jade work -h`, `jade ack -h`] The command shapes used below exist. `jev verify` takes `-s/--state` (evidence text, or `-` for stdin), `--risk` and `--decision`. `jade work` has `add|list|set|render|sync`. `jade ack` needs `--as revised|escalated|reported|dropped`.
- [VERIFIED A8, CONTRIBUTING.md, `.github/workflows/ci.yml`] The repo gate is: `uv sync`, `uv run ruff check .`, `uv run pytest -q -n auto --dist loadgroup -m "not timing"`, `uv run pytest -q -m timing`, `uv run python evals/run_cases.py`.

**Assumptions (reversible).**
- H-1: The settled assumptions of the task hold. "v6" means the 0.6.x line plus main plus the 0.7.0 stack (PR 42 to 46), with PR 47 first (roadmap A-1).
- H-2: Claude Code on macOS, one owner, one harness (roadmap A-2).
- H-3: The owner has answered, or accepts the defaults of, the decisions in section 10 before the step that needs them.
- H-4: Step ids are the roadmap task ids (V6-nn). The order here is my proposal and follows the dependency graph of roadmap section 4.1. [INFERENCE]

## 1. Short answer

1. [RECOMMENDATION] Work as a chain of small steps. One step is one branch, one worktree, one writer, one pull request. Each step runs the same loop (section 6): tests first, build, local gate, independent review, Jev verify, record, hand to the owner.
2. [RECOMMENDATION] The first half of the work is Phase 0 (V6-01 to V6-14): save loose work, make ids unique, merge the pile, close three known holes, make docs true, cut 0.7.0. Do not skip it. Every later step stands on it.
3. [RECOMMENDATION] An agent never merges, tags, approves a permit, sets a `JADE_*` variable or pushes to main. The owner does those. Where the agent cannot run a check (the case runner, the real-shell oracle), the owner or CI runs it.
4. [RECOMMENDATION] The state of the work lives in git and in the work items, not in anyone's memory. A new session reads that state first (section 8).
5. [RECOMMENDATION] Smallest useful path: the 25 tasks of roadmap section 8. Steps outside it are marked "optional" in the table of section 7.

## 2. Standing rules for any implementer

These apply to every step. A step card never loosens them. [RECOMMENDATION], with the evidence noted.

| # | Rule | Why, evidence |
|---|---|---|
| R1 | One writer at a time per tree. Reviewers read; they do not edit. Two agents never edit the same worktree. | Owner learned rule; A1 worktree trap |
| R2 | Build in a fresh worktree from `origin/main` (or the named base), never in the owner's main checkout `/Users/example/Documents/projects/jade`. Install dependencies for real inside the worktree (`uv sync`). Never symlink `.venv`, `vendor` or `node_modules` from another tree. | Main checkout is dirty with mode noise [A1]; a symlinked environment loads stale code [owner learned rule] |
| R3 | Every gate runs with an empty HOME and `CI=1`: `EH=$(mktemp -d); HOME="$EH" CI=1 uv run ...`. A green gate on this Mac is not a green CI. | Owner learned rules; about 1,091 tests skip when the approver is installed [INFERENCE: an old 0.5.0 changelog figure via A8, not re-measured] |
| R4 | Never push to `main`. Push only feature branches (`v6/<nn>-<slug>`). Open a pull request only after the owner said yes once in this session (checklist item 9). | CLAUDE.md "never push to main"; opening a PR is a publish |
| R5 | The owner merges and the owner tags. An agent never runs `gh pr merge`, never creates a tag, never runs `gh release`. After the owner merges, the agent fast-forwards its local view: `git fetch origin && git merge --ff-only origin/main` in a clean tree. Merge `origin/main`, never a stale local `main`. | Owner learned rules |
| R6 | Stage explicit paths: `git add <file> <file>`. Never `git add -A`, `git add .` or `git commit -a`. Hooks generate artifacts (`PROJECT_INDEX.json`, `.codebase-memory/`); do not stage them unless the step says so. | Owner learned rules; 236 mode changes in the main checkout [A1] |
| R7 | Mode-clean commits. Before each commit: `git diff --cached --summary \| grep -E "mode change"` must print nothing (new files are 644). A step that finds mode noise fixes it in its own commit. | A1: new files of PR 47 are 755 |
| R8 | Never approve a permit and never route around a block. Do not run `jade permit approve`, `jade level`, `jade gate`, `jade hooks`, `jade upgrade`, `jade setup` or `jade review confirm`. If the JADE gate blocks a call, stop, show the permit id to the owner and wait. Do not rephrase the command. If a hook blocks a Bash call, write a script file under the scratchpad or read code instead. | CLAUDE.md "Jev / JADE"; SI-09 |
| R9 | Never set, export or inline `JADE_HOME`, `JADE_CONFIG`, `JADE_GATE_MODE` or `JADE_POLICY_DIR` (or `JADE_APPROVER_ANCHOR`, `JADE_REGISTRY_DIR`) in a command. SI-11 blocks them. So an agent cannot launch `evals/run_cases.py`, `evals/realshell_oracle.py` or any runner that sets them itself. The owner or CI launches those. | [VERIFIED roadmap section 3 rule 2; `policies/safety.yaml` SI-11 list per A5] |
| R10 | Real shells only through `evals/realshell.py` (SI-13). Before each review round the owner runs `python3 evals/realshell.py audit evals/`; it must print nothing. Never aim a destructive payload at a real path. | [VERIFIED repo docs/13 on main, via roadmap 3 rule 7] |
| R11 | Human messages in simple English: short sentences, one idea each, say what will happen and what the risk is. This is for messages to the owner (questions, summaries, PR text, CHANGELOG lines for users). Code, commands, error text and prompts to other tools stay exact. No emoji. | Owner preference (hard rule) |
| R12 | Skill and CLAUDE.md changes are staged for approval, never applied. Write the proposed text into the PR or a scratch file and ask the owner. Do not run `jade claude-md apply`. Invoke skill `task-observer` before the first tool call of a session. | CLAUDE.md |
| R13 | Secrets: never print, write or commit one. Real ledger rows hold real commands: keep them in a private path outside the repo. Commit only synthetic or redacted rows. Anything that goes to `jev` or `jade ask` is redacted first (state goes to openrouter.ai). | CLAUDE.md Open Vault and Jev rules; d10 8.4 |
| R14 | A change that makes JADE ask more must show extra asks per day from `evals/gate_replay.py` on real transcripts (the owner runs it). A change that makes JADE ask less must name the metric it expects to hold and the rows that support it. | Roadmap section 3 rule 1 |
| R15 | Safety changes need the owner's written yes in chat before the build. Files `policies/safety.yaml` and `policies/promotion.yaml` change only with an issue first and the threat-model row (repo `docs/05`). Record the yes (date, words) in the PR body and the work item. | CONTRIBUTING.md; roadmap safety flag |
| R16 | Red then green. A new negative test must fail on a recorded pre-fix sha and pass after. It has a positive twin so "block everything" cannot pass. A test enters a PR gate only when its fix is merged; before that it is a counted `known_gap` with a work item. | d10 section 10.1 and 11.1 |
| R17 | Edit tracked files with the Edit tool or a python script, never `sed -i` with stdin. Shell on macOS has no `timeout`; use `perl -e 'alarm N; exec @ARGV'`, but never wrap a test suite that asserts on timers. | Owner learned rules |
| R18 | Watch a CI run by id with Monitor, never poll in a loop: `gh run list --branch <b> --limit 1 --json databaseId` then Monitor `gh run watch <id> --exit-status`. Reproduce a Linux-only failure in `docker run python:3.13-slim` on a `git archive` copy before guessing. | Owner learned rules |
| R19 | Before any merge or release the owner reads the diff stat. A pre-flight check compares exact items (names, ids), never counts. A transport failure (ssh rc 255) is not the command's result: retry before you abort. | Owner learned rules |
| R20 | Stay in scope. Report an adjacent problem as a work item; do not fix it in the same branch. Rule changes go in separate commits, one rule per commit, so one revert undoes one rule. | Roadmap V6-08 rollback |
| R21 | If a step fails twice, stop, find the root cause, and change track. If a parser or rule hunt finds new gaps for a third review round, stop and use the stop-loss (section 6, step L8). | AGENT_RULES section 11; roadmap V6-08, V6-22 |
| R22 | Commit messages end with the attribution line the harness asks for. A commit body says what changed and how it was verified, in simple English. | Harness instruction |
| R23 | After a Deny or a JADE block, do not reach the same goal by splitting the command into parts, writing a script and running it, rewording the request, or changing the working folder. Do other work, or report the block and stop. JADE cannot see intent, so it can only record and count these retries. | d05 section 4.1 note; d07 section 8.2; roadmap V6-26 and V6-17 (N39 to N43) |

## 3. Preconditions

Check these before step 1. Each has a command and an expected result. If one fails, stop and tell the owner. [RECOMMENDATION]

| # | Precondition | Check | Expected |
|---|---|---|---|
| PC-1 | The planning documents are approved by the owner, or the owner said "proceed with defaults" for decisions 1 to 4 of section 10. | Ask in chat. | A written yes. |
| PC-2 | `origin/main` is known and CI is green on it. | `git fetch origin && git rev-parse origin/main`; `gh run list --branch main --limit 1 --json conclusion,headSha` | `conclusion: success` on the sha. Today `e9412f3` [VERIFIED rev-parse; CI state not read by me: [UNRESOLVED]] |
| PC-3 | The PR list matches section 0. | `gh pr list --state open --json number,headRefName,baseRefName,headRefOid` | Heads match, or the new head is named in the session log. Refs moved during research: always name the ref. |
| PC-4 | Tools exist. | `uv --version`; `python3 --version` (3.13 or 3.14); `gh auth status`; `jev --help`; `jade --version` | All run. |
| PC-5 | The installed gate on this Mac is known. It was `8100ea1` = v0.6.1 [VERIFIED A3]. Local green does not test the new code through the installed hooks. | `jade version` | Note the version in the session log. |
| PC-6 | Level and gate mode of this Mac are known. At the A3 audit (2026-11-16) the level was `off`, and `jade check` showed that in the last 7 days 3,416 of 3,424 gated calls ran with no host prompt [VERIFIED A3 line 43]. | `jade level` (read only); `jade check` | Note result. Do not change it (R8). |
| PC-7 | The owner's main checkout is not used for builds. | `git -C /Users/example/Documents/projects/jade status --short \| head` | Dirty is expected. Do not touch. |
| PC-8 | Work-item and threat-row id collisions are known (A1). Until V6-02 and the renumber after V6-04, do not add work items with new W numbers on a branch that will be merged. | Read repo `docs/07` note once V6-02 lands. | Follow section 7, step 2. |
| PC-9 | Owner-run tools are agreed. The owner has agreed to run `evals/run_cases.py`, `evals/never_looser.py`, `python3 evals/realshell.py audit evals/` and `evals/gate_replay.py` on request, or CI runs them. | Ask in chat. | A written yes. |

Branch preconditions per step are in the cards ("Needs"). A step may start only when everything in "Needs" is merged to `origin/main` (not only approved), unless the card says "stacked".

## 4. First-session checklist

Run in order. Write the answers into the session log (a scratch file in the scratchpad; the durable log is the pull request and the work items, section 8). [RECOMMENDATION]

1. Invoke skill `task-observer` (first tool call of the session).
2. Read, in this order: this section; the roadmap (section 12) cards for the step you will build; `AGENTS.md` and `CONTRIBUTING.md` in the repo; evidence A1 and A3 (grep for the step's topic). Do not redo discovery.
3. Run `skillfind "jade"` and read skill `jade`. Never list skill directories.
4. Run PC-1 to PC-9. Ask the owner about PC-1 and PC-9.
5. Pick the first open step by the resume protocol (section 8). On a fresh start it is V6-01.
6. Create the work area: `git -C /Users/example/Documents/projects/jade worktree add .claude/worktrees/v6-<nn>-<slug> -b v6/<nn>-<slug> origin/main` (use the base named in the card). If the JADE gate blocks this, show the owner and wait (R8).
7. In the new worktree: `cd <worktree> && uv sync --frozen`. Confirm the baseline gate is green before any change: `EH=$(mktemp -d); HOME="$EH" CI=1 uv run ruff check . && HOME="$EH" CI=1 uv run pytest -q -n auto --dist loadgroup -m "not timing"`. If the baseline is red, that is the first finding. Stop and report; do not build on red.
8. Record the baseline: sha, test count, time, skip count. Put it in the PR body later.
9. Ask the owner once, in simple English: "May I push branches named v6/* and open pull requests? I will never merge, tag or push to main." Wait for a yes.
10. Read the card's "Safety change" flag. If yes, ask for the owner's yes now, before step L2.

## 5. Words used here

- **Gate-local**: the commands of section 6, step L4, run with empty HOME and `CI=1`.
- **Gate-CI**: the CI run on the pushed branch (ubuntu, Python 3.13 and 3.14) [VERIFIED `.github/workflows/ci.yml`].
- **Gate-owner**: checks an agent cannot launch (R9): `evals/run_cases.py`, `evals/never_looser.py`, `evals/gate_replay.py`, the real-shell oracle, any live Jev run, sudo, a new session.
- **Differential**: a comparison of a candidate ref with `origin/main` on the same inputs. "Never looser than main" means the candidate blocks everything main blocks.
- **Oracle**: the real-shell harness `evals/realshell.py` (PR 48, now on main [VERIFIED docs/13 per roadmap]). It runs a command in a real shell under macOS `sandbox-exec` with stubs that record calls to `git`, `gh`, `rm`, `curl`, `ssh` and others. Truth comes from execution, not from a rater.
- **Gate change**: any change to `jade/hooks.py`, `jade/policy.py`, `jade/blind.py`, `jade/shellnorm.py`, `jade/memory.py`, `jade/proxy.py`, `jade/askchat.py`, `jade/permits.py`, `policies/*.yaml`, or `evals/**/cases.jsonl`. [INFERENCE: the list is mine, from the files the roadmap cards touch]

## 6. The step loop

Every step runs L1 to L12. Cards in section 7 give only the specifics. A step is not done until L12.

**L1. Read.** Read the card, the roadmap card of the same id, and every file in "Read first". Name the ref each claim is about.

**L2. Tests first.** Write the tests of the card. Run them. Record the failing output and the sha it failed on. For a negative test, also write the positive twin. A test that passes before the fix is wrong or the bug is not there: stop and tell the owner.

**L3. Build.** The smallest change that makes the tests pass. Reuse the symbols named in the card. No new abstraction without a demonstrated need. One rule per commit for rule changes (R20). Stage explicit paths (R6), check modes (R7).

**L4. Gate-local.** Run, in the worktree:

```
EH=$(mktemp -d)
HOME="$EH" CI=1 uv run ruff check .
HOME="$EH" CI=1 uv run pytest -q -n auto --dist loadgroup -m "not timing"
HOME="$EH" CI=1 uv run pytest -q -m timing
```

Save the tail of each output to a file under the scratchpad (for L9). Do not run `evals/run_cases.py` (R9); gate-CI and the owner run it.

**L5. Gate-CI.** Push the branch. Watch the run (R18). On a Linux-only failure, reproduce in `docker run python:3.13-slim` on a `git archive` copy with `uv sync`.

**L6. Gate-owner.** Ask the owner to run what the card lists. Give exact commands and say what output means pass. For a gate change this always includes the differential: `uv run python evals/never_looser.py origin/main <branch>` [tool built in V6-03; before V6-03 exists use the four-command probe of A1 7.2 and the pinned tests]. Expected output: empty. Also `python3 evals/realshell.py audit evals/` (prints nothing) and `evals/gate_replay.py` on real transcripts for any step that can add asks (R14). Record the numbers.

**L7. Independent review (adversarial).** Start a reviewer that did not write the change. Use skill `adversarial-reviewer` or a fresh subagent. Give it: the diff against the base, the card, and the question "what is weaker than `origin/main`, and what can bypass this?". Do not give it the writer's reasoning. For a gate change the reviewer must:
- build payload lists from `evals/**/*.jsonl`, `tests/test_redirect_*.py` and the card's named shapes;
- classify each with `policy.assess` on both refs and list every command the candidate allows and main blocks (the differential);
- for shell shapes, ask the owner to run the payloads through the oracle (R10) and compare: any payload where the real shell reaches `git push origin main` (or another stubbed program) and the classifier says allow is a bypass;
- read the diff for fail-open paths (an exception that exits 0), clipped or hidden text, and loosened patterns.
For each finding, run two skeptical verifiers that try to refute it. A finding both fail to refute is real. Real findings go back to L2 (red test first). The owner re-runs the full gate after fixes.

**L8. Stop-loss.** Count new safety-class gaps per review round per 100 payloads. After two independent rounds with none, stop adding waves. After a third round that still finds gaps, stop and ask the owner (the parser may need a different approach). [RECOMMENDATION, d10 11.1 rule 5; A1: four rounds of PR 47 each found new gaps]

**L9. Jev verify.** Only after L4 to L8 are green. Build one evidence file of raw output (not your summary): the tail of the L4 outputs, `git diff --stat <base>...HEAD`, the CI run conclusion lines, the differential output, the review findings list with their fate. Redact secrets and private rows first. Then:

```
jev verify --risk <medium|high> -s - "<one sentence claim, for example: step V6-06 closes N7 to N9 and breaks no existing test>" < /path/to/evidence.txt
echo "jev exit: $?"
```

(`--dry-run` first to see what leaves the machine.) Rules: exit `0` is the only yes. Exit `3` abstain, `4` reject, `5` provider unavailable, `6` invalid: do not say "verified". Add evidence and retry at most 3 times, or report "unverified" to the owner. Record with `jade ack --all --as revised|escalated|reported|dropped --note "<why>"`. Use `--risk high` for a gate change or a safety change. A probability is never permission. For a choice between real alternatives use `jev choice` with the raw alternatives text. [VERIFIED CLAUDE.md; command shapes from `jev --help`] If a JADE gate holds the call, wait for the owner's permit (R8).

**L10. Record.** (a) Pull request body in simple English: what changed; what the user will notice (more or fewer asks); how verified, with exact commands and results; the baseline numbers; the owner's yes for a safety change; known gaps and their work items; rollback. (b) A work item or a status note per the card, through `jade work add|set` (check `jade work -h`; use ids from the V6-02 register after it exists). (c) CHANGELOG entry under the unreleased heading, simple English. (d) Docs the card names. `tests/test_docs_truth.py` must stay green.

**L11. Hand over.** Tell the owner in simple English: what is ready, which PR, which checks are green, which are owner-only, what is the risk, what you did not verify. Ask for the merge. Do not merge.

**L12. After the merge.** Fast-forward (R5). Confirm the step's tests are in the merged tree. Update the roadmap status in the work items. Remove the worktree by its path (never by a shared name): `git worktree remove <path>` only after the owner agrees and the branch is merged.

**What every card ends with.** "Stop" lists the conditions that end the step and go back to the owner. "Human" lists what only the owner can do.

## 7. The ordered sequence

Order follows roadmap section 4.1. "Opt" = outside the minimum v6 of roadmap section 8. "Safety: yes" needs the owner's written yes first (R15). "Needs" lists what must be merged to `origin/main` first.

| Step | Task | Needs | Safety | Min v6 |
|---|---|---|---|---|
| 1 | V6-01 Save the loose work | none | no | yes |
| 2 | V6-02 One id register | V6-01 | no | yes |
| 3 | V6-03 "Never looser than main" check and known-gap list | none | no | yes |
| 4 | V6-10 `jade check` tells the truth | none | no (text) | yes |
| 5 | V6-04 PR 47: verify, then merge | 1, 2, 3 | yes | yes |
| 5b | Renumber WT backlog rows from W-117 | 5 | no | yes |
| 6 | V6-05 Merge train PR 42, 43, 44, 46, 45 | 1, 2, 3, 5 | yes | yes |
| 7 | V6-06 Memory never covers a critical call | 6 | yes | yes |
| 8 | V6-07 Chat grant bound to the exact call | 6 | yes | yes |
| 9 | V6-09 Show text safely | 6 | yes | yes |
| 10 | V6-11 Docs and data promises true | 2, 6, 7, 4 | yes (purge) | yes |
| 11 | V6-12 Owner wiring steps | 4 | no | yes |
| 12 | V6-13 Branch and worktree clean-up | 1, 5, 6 | no | yes |
| 13 | V6-14 Release 0.7.0 | 3, 5, 6, 7, 8, 9, 10 | no | yes |
| 14 | V6-08 Close known rule holes (0.7.1 by default) | 3, 5, 6 | yes | yes (counted in the 25 of roadmap section 8; may ship after the 0.7.0 tag) |
| 15 | V6-15 Host probe session (owner; start at step 1) | none | no | yes |
| 16 | V6-16 Level `off` observe plus floor | 6, 4, 13 | yes | yes |
| 17 | V6-17 Floor tests, never-learn test | 7, 8, 16 | no | yes |
| 18 | V6-28 `jev` key, deny-count gate | 6, 11, 17 | yes | yes |
| 19 | V6-19 Leave level `off` (owner) | 11, 13, 16, 17, 18 | yes | yes |
| 20 | V6-18 Safety backlog (five items) | 6, 15 | yes | opt |
| 21 | V6-20 Replay runner, stats, report | 3, 13, 17 | no | yes |
| 22 | V6-22 Real-shell oracle snapshot | 5, 13 | no | opt |
| 23 | V6-23 Ledger readouts, label plumbing | 6, 13 | record-only | yes (trimmed) |
| 24 | V6-21 Rubric and labelled corpus (owner hours) | 13, 21 | no | yes |
| 25 | V6-24 Decision hygiene, spikes | 6, 23 | yes | opt |
| 26 | V6-25 Native layers (owner) | 4, 11, 15 | no | step 1 only |
| 27 | V6-26 Human text tier 1 | 6, 9, 15 | yes | opt |
| 28 | V6-27 Small durability fixes | 6 | no | opt |
| 29 | V6-29 Memory hardening, audit | 6, 7, 17, 24, 23, 18 | yes | yes |
| 30 | V6-30 First memory scope (owner) | 16, 19, 18, 29 | yes | yes |
| 31 | V6-31 Jev proxy keep or retire (owner decides) | 15, 23, 18, 29 | yes | opt |
| 32 | V6-32 Report-only learning | 23, 25 | no | opt |
| 33 | V6-33 True front page | 10, 13 | no | opt |
| 34 | V6-34 Release and CI hardening | 13 | no | opt |

[INFERENCE] Step 14 note: the 0.7.0 release gate needs the pathological-input timing test, which is built in V6-08 (roadmap V6-14). If V6-08 is not merged before the tag, that test stays a `known_gap` row, signed by the owner. Decision 2 of section 10 chooses. Steps 15, 26 and 30 are mostly human work; the agent prepares text and checks only.

The cards follow. Each card lists: Needs, Goal, Read first, Change, Tests first, Run (beyond L4), Done, Review, Jev evidence, Record, Stop, Human. A line that says "standard" means the loop of section 6 applies unchanged.

### Phase 0: clean-up and known holes

#### Step 1. V6-01 Save the loose work
- **Needs.** none. **Safety.** no.
- **Goal.** Everything that exists only outside git history gets a pushed ref, or the owner drops it in writing.
- **Read first.** A1 section 6; roadmap V6-01; d01 B6.
- **Change.** No code. For each item, a branch with explicit paths: (a) the spec B plans (3 plans, 1 design, approved 2026-11-14) untracked in `.claude/worktrees/harness-tools-w97`, saved as the same files on a new branch; (b) the 3 probe runtime files; (c) the 21 WT backlog rows of the dirty main checkout, saved as a verbatim file copy on a "save" branch (not merged to main yet; renumbered at step 5b); (d) local-only `docs-approvals-wording` (`ff84b03`), pushed; (e) remote-only `open-items-shell-startup-waves4-6` (`7507c10`), kept; (f) orphan `967b166`, kept until step 5 has looked.
- **Tests first.** A script in the scratchpad: for each sha, `git branch -r --contains <sha>` is non-empty; for the plan files, `git ls-files <path>` lists them on the new branch.
- **Run.** The script (owner may run it; it only reads).
- **Done.** Every item exists on a pushed ref, or the owner wrote "drop". `git status` in the spec B worktree shows no untracked plan files.
- **Review.** Standard, short. Reviewer checks no `git add -A` happened and no secret is in the saved rows (R13).
- **Jev evidence.** The script output and `git branch -r --contains` lines.
- **Record.** PR for the plans branch (docs only). Note of the sha list in the PR body.
- **Stop.** A branch delete is asked for: refuse; deletes are step 12. A file holds a secret: stop, tell the owner, migrate it per `open-vault-passwords`.
- **Human.** Orders the saves; says which items to drop.

#### Step 2. V6-02 One id register
- **Needs.** step 1. **Safety.** no.
- **Goal.** Work-item ids and threat-row ids are unique, and old collisions are on a visible allow-list.
- **Read first.** A1 section 5; roadmap V6-02; `jade/work.py` (`save`, `table`, `dedupe`); `tests/test_docs_truth.py`; repo `docs/07`; repo `docs/05` threat table.
- **Change.** repo `docs/07`: a note that W-96 to W-105 are historic feature ids. New `tests/test_ids_unique.py`. The existing duplicates sit on an allow-list in the test.
- **Tests first.** The test fails on a planted duplicate W or T id and passes on the allow-list.
- **Done.** Test green in CI. The allow-list is the only place old duplicates live.
- **Review.** Standard. Reviewer looks for a duplicate that the test misses (ids in prose, in `work-items.jsonl`, in `BACKLOG.md`, in threat rows).
- **Jev evidence.** L4 tails; the test file; the allow-list.
- **Record.** CHANGELOG line. No renumber yet (step 5b).
- **Stop.** The test would need to change `jade/work.py` behaviour: stop, ask.
- **Human.** Approves the renumber plan (decision 4).

#### Step 3. V6-03 "Never looser than main" check, known-gap list
- **Needs.** none. **Safety.** no.
- **Goal.** A script that finds every command the candidate ref blocks less than a base ref. And a visible list of open bypass vectors.
- **Read first.** A1 section 7.2; A3 section 8b and 9; `evals/gate_replay.py`, `evals/replay_policy.py`, `evals/run_cases.py` (case loader and `known_gap`, header line 6); `jade/policy.py` (`assess`); `jade/hooks.py` (`_on_default_branch`).
- **Change.** `evals/never_looser.py <ref-a> <ref-b>`: reads every command in `evals/**/*.jsonl`, the payload lists in `tests/test_redirect_*.py` and the four commands of A1 7.2; prints each command that ref-a blocks and ref-b does not (`policy.assess` plus `hooks._on_default_branch`); exits non-zero on any. `known_gap` rows (with date and work item) for every open vector of A3 8b (`git -C 'a\ b' push`, `git send-pack`, `g=git; $g push`, `gh api .../merge`, shell-profile and agent-config writes, child-agent launch) and the NULL_REDIRECT quadratic case (WT W-104). CI fails on a `known_gap` row with no work item.
- **Tests first.** A unit test with two fixture classifiers (one looser); a test that a `known_gap` row with no work item fails.
- **Run.** Owner: `uv run python evals/never_looser.py c6ab27a c676801` reports `c6ab27a` weaker and `c676801` not weaker on the four commands [expected per roadmap V6-03; [UNRESOLVED] until run]. Run with empty HOME and `CI=1`. If the script needs a `JADE_*` variable, stop (R9): it must call `policy.assess` in process.
- **Done.** As in roadmap V6-03. The owner signs the known-gap list.
- **Review.** Standard plus: the reviewer tries to make the script say "not looser" for a looser ref (a payload set that is too thin gives false comfort). The script is labelled "minimal".
- **Jev evidence.** Unit test output; the owner's run output on the two refs; the known-gap list.
- **Record.** Work items for each known gap. CHANGELOG. repo `docs/05` note.
- **Stop.** The payload set cannot be mined without real ledger rows: use synthetic rows only (R13).
- **Human.** Signs the known-gap list (decision 14).

#### Step 4. V6-10 `jade check` tells the truth
- **Needs.** none. **Safety.** no (text).
- **Goal.** `jade check` and `jade hooks install` say what is true at level `off` and what is unknown.
- **Read first.** A3 sections 1 and 10; roadmap V6-10; `jade/ops.py` (`check` near line 1433, `HOOKS` near line 38, `_recorded_level` near line 599). [line numbers are from the roadmap on main; re-check]
- **Change.** Rows and text only, as listed in the roadmap card. No default changes. Do not print payloads: print rule ids and levels.
- **Tests first.** New `tests/test_check_rows.py`: one test per row in both states; a table test over every gate mode and level pair.
- **Done.** As in roadmap V6-10.
- **Review.** Standard. Reviewer checks that no row can read as "safe" when it is not.
- **Jev evidence.** Test names and results; sample `jade check` output text of each state (from the tests).
- **Record.** CHANGELOG (simple English: "level off shows what it really does").
- **Stop.** A change to behaviour is needed: that is step 16; stop.
- **Human.** Nothing, except reading the new text.

#### Step 5. V6-04 PR 47: verify, then merge
- **Needs.** steps 1, 2, 3 merged. **Safety.** yes (a gate change).
- **Goal.** Merge PR 47 only if it is not weaker than main.
- **Read first.** A1 sections 4c and 7.2; roadmap V6-04 and section 0; `jade/shellnorm.py`, `jade/hooks.py` (`pretool`); `tests/test_redirect_anywhere.py`, `test_redirect_round3.py`, `test_redirect_zsh_and_braces.py`, `test_redirect_round4.py`.
- **Change.** Work in a worktree on the PR head (`claude/heuristic-hamilton-0eae1d`, today `c676801`; re-check). Only fixes found by the review. Restore file modes to 644 (A1: `shellnorm.py`, `CHANGELOG.md`, new tests are 755). Check `967b166` for anything the head lacks (`git diff c676801 967b166 --stat`): port or drop with a note.
- **Tests first.** A pinned test for `cat<<<'git push origin main'|sh` (and the three other commands of A1 7.2) if not present. DIFF-W1 (leading input redirect) and WT W-114 as `known_gap` rows or fixed.
- **Run.** Owner: `evals/never_looser.py origin/main <head>` empty; real-shell audit; `evals/run_cases.py`.
- **Done.** Roadmap V6-04 items 1 to 5. CI green on the merge commit.
- **Review.** Adversarial, limited to "weaker than main". Full recipe of L7 including the oracle. One independent round; stop-loss L8 applies.
- **Jev evidence.** The never-looser output (empty), the oracle comparison, the CI conclusions, the `967b166` diff stat.
- **Record.** CHANGELOG. Work items renumbered at step 5b.
- **Stop.** The review finds a regression and the fix is not small: tell the owner; the default is to hold PR 47 (roadmap decision 3).
- **Human.** Approves the route (no 0.6.2); merges.

#### Step 5b. Renumber the work-item rows
- **Needs.** step 5 merged. **Safety.** no.
- **Goal.** WT rows get ids from W-117; PR 47's W-115 meaning gets a new id; shrink the allow-list of step 2.
- **Change.** One commit that changes `work-items.jsonl` and `BACKLOG.md` through `jade work` (check `jade work -h`) or a python script. Update citations in docs.
- **Tests first.** `tests/test_ids_unique.py` with a smaller allow-list.
- **Review.** Standard. **Jev evidence.** Test output and the old-to-new id map. **Record.** CHANGELOG lists renamed ids. **Human.** Approves the map.

#### Step 6. V6-05 Merge train PR 42, 43, 44, 46, 45
- **Needs.** steps 1, 2, 3, 5. **Safety.** yes.
- **Goal.** The 0.7.0 stack is merged in order, each merge green and never looser.
- **Read first.** A1 sections 4a, 7.1; A7 2.1; roadmap V6-05; `jade/askplain.py`, `jade/scopes.py`, `jade/memory.py` (`lookup`; `honoured`).
- **Change.** Order: PR 42; rebase 43, 44 on it; rebase 46 after PR 47 is merged (one real conflict in `jade/shellnorm.py`, resolved once); rebase 45 onto the new scopes tip last. One PR per rebase, owner merges each. Keep `q.human_proxy.would_allow.v2.json` out of the champion slot (decision 10). Never delete `open-items-scopes` while PR 45 targets it.
- **Tests first.** A new test that the plain merge line shows `--admin` and `--delete-branch` (merge gate for PR 42 and PR 43; `tests/acceptance/test_ask_plain_text.py:208` pins the old hiding line).
- **Run.** Owner per merge: `evals/never_looser.py <previous main> <PR head>` empty; `evals/run_cases.py`. [UNRESOLVED] whether `tests/test_ledger_migration.py` opens a previous-release ledger: check and note.
- **Done.** As in roadmap V6-05. T ids unique.
- **Review.** Standard per PR; adversarial with differential for any PR that touches a gate file (42, 43, 44, 46).
- **Jev evidence.** Per PR: CI conclusions, never-looser output, test names.
- **Record.** One CHANGELOG section per merge; list user-visible ask changes.
- **Stop.** A merge makes CI red: revert that merge, newest first, and tell the owner. Scopes widen memory reach before V6-06: do not tag between merges.
- **Human.** Merges each PR in order.

#### Step 7. V6-06 Memory never covers a critical call
- **Needs.** step 6. **Safety.** yes.
- **Goal.** A confirmed answer never silently covers a critical call; a different target gives a different key.
- **Read first.** A3 section 6; roadmap V6-06; `jade/memory.py` (`eligibility`, `class_key`, `NEVER_RULES`, `lookup`), `jade/proxy.py` (`skip_reason`, `NEVER_RULES`); `tests/acceptance/test_memory_w101.py`. First trace [UNRESOLVED]: can a shell write to a protected instruction file reach class eligibility?
- **Change.** `eligibility` returns `never` for critical; R-13 and R-18 added to `memory.NEVER_RULES` unless the trace shows they cannot reach it; `class_key` includes `KUBECONFIG`, `AWS_PROFILE`, `--context` and cwd for gated rules. R-12 and R-15 stay exact-only (decision 6); R-23 is exact-only too if the owner answers LD-06 so (LD-06 only, not Q-03).
- **Tests first.** N7, N8, N9 in `tests/acceptance/test_memory_w101.py` plus the positive twin. Red on the pre-fix sha (record it).
- **Run.** Owner: `evals/gate_replay.py` before and after on real transcripts; extra asks per day recorded.
- **Done.** Roadmap V6-06 "Done when".
- **Review.** Adversarial with differential. Reviewer tries class keys that collide (env prefix, sudo, quoting, path forms).
- **Jev evidence.** Red output on the pre-fix sha, green output after, replay counts, diff stat.
- **Record.** CHANGELOG: old class keys no longer match and age out in 30 and 180 days; asks rise. Work items for any residual.
- **Stop.** A wide class key breaks many existing memory tests: stop, ask for the narrow option.
- **Human.** Decision 6; runs the replay; merges.

#### Step 8. V6-07 Chat grant bound to the exact call
- **Needs.** step 6. **Safety.** yes.
- **Goal.** A chat Allow is spent only on the same `plan_hash` (tool, text, cwd).
- **Read first.** A3 section 7 probe C4; roadmap V6-07 and section 2 row 4; `jade/askchat.py` (`question_for`, grant lookup), `jade/permits.py` (`plan_hash`, near lines 130 and 280); `tests/acceptance/test_chat_ask_w100b.py` (line 241 pins the old behaviour).
- **Change.** Key grants on `plan_hash`. Question text says what the grant covers. Old grant rows without `plan_hash` are not honoured. Rewrite (not delete) `test_quoting_and_an_env_prefix_do_not_make_it_a_new_command`.
- **Tests first.** N10 (Allow for text A refused for text B or another cwd), N11 (other session or `agent_id`, first call closed), N12 exists.
- **Run.** Owner: replay, extra asks per day. If above the owner's bar, switch to the narrow rule (decision 5); that needs a new yes.
- **Done.** Roadmap V6-07.
- **Review.** Adversarial with differential. Reviewer retries probes C4 to C6 with the first call closed ([UNRESOLVED] C5, C6).
- **Jev evidence.** As step 7.
- **Record.** CHANGELOG, simple English: "a grant now covers one exact command".
- **Stop.** The replay count is above the owner's bar: stop and ask.
- **Human.** Decision 5; replay; merge.

#### Step 9. V6-09 Show text safely in chat and dialog
- **Needs.** step 6. **Safety.** yes.
- **Goal.** One function escapes bidi and control characters and never hides the gated part of a clipped command.
- **Read first.** d03 FN-06; R6 section 1.5; roadmap V6-09; `jade/permits.py` (`printable`, near line 389), `jade/askchat.py` (`HEAD_CHARS, TAIL_CHARS = 450, 150`, near line 31), `jade/askdialog.py` (`text_for`, near line 87).
- **Change.** One "show safely" function used by `askchat.question_for`, `askdialog.text_for`, the permit screen, `jade review`, `jade knows`. Escape commands, paths and content fields only. Do not use `printable()` on prose (it escapes U+200C, the Persian half-space). When clipped, show the gated segment in full or say "open the terminal".
- **Tests first.** N13, N14, and a U+200C-kept test in `tests/acceptance/test_chat_ask_w100b.py`.
- **Done.** Roadmap V6-09. [UNRESOLVED] how the live chat box draws these characters; V6-15 probes it.
- **Review.** Adversarial. Reviewer feeds U+202E, U+2067, zero-width characters and a 1,000-character command with the risky part in the middle.
- **Jev evidence.** Red and green output; sample escaped output text.
- **Record.** CHANGELOG. **Stop.** Escaping breaks a Persian prompt: stop, ask. **Human.** Merge; later probe in step 15.

#### Step 10. V6-11 Docs and data promises true
- **Needs.** steps 2, 4, 6, 7. **Safety.** yes (purge).
- **Goal.** Docs, README and data promises match the code.
- **Read first.** A8 sections 9 to 11; d03 Part D; roadmap V6-11; `jade/ops.py` (`purge` near line 227, `check_drift` near line 155), `jade/ask.py` (near line 252), `jade/state_machine.py` (`MAX_REVISIONS` near line 11, `WAITING_DEPENDENCY` near line 44), `policies/escalation.yaml`, `tests/test_docs_truth.py`, `tests/test_routing_policy_w53.py`.
- **Change.** The list of the roadmap card. Mark dead states "reserved, not entered" (do not delete). Purge scrub of label text only after the owner's yes (decision 12). Merge `docs-approvals-wording`.
- **Tests first.** `tests/test_docs_truth.py` extended; new `tests/test_purge_columns.py` (text planted in each table; none survives); new `tests/test_fixture_home.py`; an unread-key test for `escalation.yaml`.
- **Done.** Roadmap V6-11.
- **Review.** Standard. Reviewer checks every promise in README against `adapter.py`, `proxy.py`, `ask.py`.
- **Jev evidence.** Test output; the list of changed claims.
- **Record.** CHANGELOG: purge now clears two more columns (not reversible). **Stop.** Purge touches the ledger schema: stop, ask. **Human.** Decision 12; merge.

#### Step 11. V6-12 Owner wiring steps (human)
- **Needs.** step 4. **Goal.** The owner loads the maintain job, installs the signed approver (`scripts/install-approver.sh`, sudo), confirms branch protection and the pre-push guard, turns on private vulnerability reporting, runs one full-history gitleaks scan, and installs the new release after step 13.
- **Agent job.** Write the list in simple English with exact commands and what output means done. Never run these steps (SI-09, sudo). Read `jade check` after and report which rows are still unknown.
- **Stop.** The owner skips a step: record the row as "unknown" in `jade check`; continue.

#### Step 12. V6-13 Branch and worktree clean-up (human)
- **Needs.** steps 1, 5, 6. **Goal.** Merged branches and scratch worktrees are removed with no lost work.
- **Agent job.** Save the sha list of every ref to delete first. Give the owner commands by path (never by shared name; the folder `harness-tools-w97` holds `design-clear-questions`). Do not delete `open-items-scopes` until PR 45 is merged. `git fetch --prune` writes refs; warn.
- **Stop.** Any ref with an unmerged sha: keep it.

#### Step 13. V6-14 Release 0.7.0 (owner cuts)
- **Needs.** steps 3, 5, 6, 7, 8, 9, 10. **Safety.** no.
- **Goal.** A tag cut by the owner from merged `origin/main`, with CI green on that commit.
- **Read first.** A8 section 8; roadmap V6-14; `.github/workflows/release.yml`; `scripts/verify_install.sh`; `jade/cli.py` (`upgrade`).
- **Agent job.** Prepare: version bump in `pyproject.toml` and `jade/__init__.py` (they must agree: release workflow checks), CHANGELOG section (renamed ids, memory key change, ask changes, known gaps), release gate list. Run G1 (by CI), G2, G5, G7, G8 as named in d10 10.1. G8: `python3 evals/realshell.py audit evals/` prints nothing.
- **Owner steps.** Never-looser check against 0.6.1 empty; sign the known-gap list; one hook-process latency measure (empty HOME) beside the 2026-11-09 result file; the sandbox install in a clean place (own HOME, tool dir, ledger): checksums, `--version`, upgrade check; tag and push the tag.
- **Review.** Adversarial review of the release diff against `v0.6.1`, with the differential.
- **Jev evidence.** Gate outputs, never-looser output, sandbox install output, CI run conclusions for the commit.
- **Stop.** CI red on the commit, a `known_gap` list not signed, or step 7, 8 or 9 not merged: no tag (roadmap section 2 row 1).
- **Rollback.** `jade upgrade --ref v0.6.1` (owner).

#### Step 14. V6-08 Close the known rule holes
- **Needs.** steps 3, 5, 6. **Safety.** yes. Default: ships as 0.7.1 (decision 2).
- **Goal.** One batch closes: `curl URL | sh # x` (trailing comment hides R-22), `gh api .../merge`, `git send-pack`, `git -C 'a\ b' push`, `g=git; $g push`, writes to `~/.zshrc`, `authorized_keys`, `CLAUDE.md`, `.mcp.json`, `.vscode/settings.json`, `.codex/config.toml`. Shell start-up files belong to PR 46 (`HO-shell-startup`); do the rest here.
- **Read first.** A3 section 8b; R6 section 1.5; roadmap V6-08; `policies/risk.yaml` (R-10, R-13, R-22, R-99), `jade/blind.py`, `jade/shellnorm.py`, `jade/policy.py`.
- **Tests first.** N18 (trailing comment, Persian comment), N19, N20, N23 in `evals/adversarial/cases.jsonl` and `tests/acceptance/test_gate_blind_spots_w31.py`, `test_gate_canonicalisation.py`, `test_gate_surface.py`. Each shape has a benign twin (`echo hi # curl x | sh` stays allowed). Pathological input (long digit run, NULL_REDIRECT quadratic case) classifies under 1 s and within 2 times main in the same job (G7 for 0.7.0 only if this step merges before the tag; otherwise it is a signed `known_gap` row, see V6-14).
- **Run.** Owner: replay (extra asks), never-looser empty, oracle comparison.
- **Done.** Roadmap V6-08. Remaining holes stay `known_gap` rows.
- **Review.** The strongest loop: L7 with oracle, two skeptical verifiers per finding, stop-loss L8.
- **Jev evidence.** Red and green per shape; oracle comparison; timing numbers.
- **Record.** CHANGELOG lists new asks. One commit per rule (R20).
- **Stop.** Two independent rounds find none: stop. A third round still finds gaps: ask. Do not mix with question text changes.
- **Human.** Yes for each new block; replay; merge.

### Phase 1: safety floor

#### Step 15. V6-15 Host probe session (owner; start at step 1)
- **Goal.** Six facts that decide the native-layer design (P1 to P6 of roadmap V6-15) plus: `claude --resume` session id, `defer` in `claude -p`, `askUserQuestionTimeout`, SessionStart `source=compact`, which host mode the 3,416 calls used, how chat box and dialog draw bidi and Persian text.
- **Agent job.** Prepare a throwaway folder and a script in the style of `evals/probes/hook_ask/` (`probe_hook.py`, `RESULTS.md`). The owner runs it in a new session (settings and MCP changes are invisible to a running session). The results table (host version, one row per probe) is committed under `evals/probes/`.
- **Stop.** A probe needs sudo or touches real settings: the owner decides; the agent does not.

#### Step 16. V6-16 Level `off` becomes "observe plus floor"
- **Needs.** steps 4, 6, 13. **Safety.** yes.
- **Goal.** At level `off`, SI-07, SI-08 and writes to `settings.json` and hook wiring block; other risk rules advise. R-16 and R-14 get project scope for the maintainer's own repo in the same change.
- **Read first.** A3 sections 1, 3, 5; A6 (R-16 and R-14 were 77 of 100 answers, one snapshot); roadmap V6-16; `jade/hooks.py` (`_verdict` near line 427, `pretool` near line 375), `jade/ops.py` (`_recorded_level`), `policies/risk.yaml`, `jade/scopes.py` (`kind_project`).
- **Tests first.** N4 (exists), N5, N6, N6b in new `tests/acceptance/test_autonomy_floor.py`; `tests/acceptance/test_level_si12_w99.py` stays green.
- **Run.** Owner: `gate_replay` before and after; asks per day (R14). The replay must show no should-block call allowed by the narrowed rules. Human review of the two rules first.
- **Review.** Adversarial with differential and the oracle. Narrowing a rule is a loosening: the differential is the main check.
- **Jev evidence.** Red and green N-tests; replay counts; differential output.
- **Record.** CHANGELOG: asks will rise at `off`. Work items.
- **Stop.** The owner prefers text only (decision 7 option a): stop after step 4's text.
- **Human.** Decision 7; replay; merge.

#### Step 17. V6-17 Floor tests and the never-learn test
- **Needs.** steps 7, 8, 16. **Safety.** no.
- **Goal.** "Safety and autonomy are separate" becomes a test.
- **Change.** New `tests/acceptance/test_autonomy_floor.py` (N1 to N6b, List A and List B), `tests/acceptance/test_never_learn_corpus.py`, `tests/acceptance/test_permits_lifecycle.py` (N15 to N17). List B is printed by `jade check`.
- **Tests first.** These are the deliverable. A test enters a gate only after its fix is merged (R16).
- **Review.** Standard. Reviewer checks List A names rows by SI, R and HO id, not counts.
- **Stop.** List A fails on a row whose fix is not approved: mark a counted `known_gap`, do not weaken the test.

#### Step 18. V6-28 The `jev` key and the deny-count gate
- **Needs.** steps 6, 11, 17. **Safety.** yes.
- **Goal.** R-21 (egress) added to `proxy.NEVER_RULES` (a tightening; route LD-22). `jev on|off`, last ledger event wins, an invalid value reads `off`; at `off` `proxy.step` returns before it builds any state; the readiness gate counts owner denies (at least 30 denies from at least 3 rules, zero false allows), not answers.
- **Read first.** A4; A6 2.1; roadmap V6-28 and section 2 row 5; `jade/proxy.py` (`step`, `skip_reason`), `jade/ops.py` (`_recorded_level`, `JEV_BAND_ANSWERS` near line 687), `policies/safety.yaml` (SI-09, SI-11).
- **Tests first.** N28 to N32 in new `tests/acceptance/test_autonomy_keys.py`; do not add `JADE_JEV` (N33). Shadow-ends-by-denies test. R-21 test: the proxy never decides an R-21 call at any level (R-21 in `proxy.NEVER_RULES`, route LD-22).
- **Review.** Adversarial. Reviewer tries to delete a `jev: off` line and raise autonomy silently, and to import `jade.proxy` as an agent.
- **Jev evidence.** Red and green output; a fake-provider run with zero provider calls.
- **Record.** CHANGELOG: at `low` and `medium` Jev stays in shadow until enough denies exist; at `high` the shadow call stops (a privacy tightening).
- **Stop.** A label above MANUAL is wanted but the signed approver is not installed: stop (needs step 11).
- **Human.** Decision 9; merge.

#### Step 19. V6-19 Leave level `off` (owner)
- **Needs.** steps 11, 13, 16, 17, 18. **Safety.** yes.
- **Owner commands (terminal, never the agent):** `jade jev off`, then `jade level high`. Rule: if asks per day rise and the owner stops answering, fix the rule (step 16); do not go lower. Agent job: write the before and after readout from the ledger (`jade ledger stats`, `would_block` events as baseline; many come from building JADE).

#### Step 20. V6-18 Safety backlog (five items, optional)
- **Needs.** steps 6, 15. **Safety.** yes. Each item is its own branch and commit: (a) SI-07 session id (`JADE_SESSION_ID`, `jade/util.py` near line 260; add to the SI-11 list or stop trusting it); (b) `HO-agent-launch`; (c) MCP file readers and the seal key (N22); (d) fail closed on malformed events (N26, N27 in new `tests/acceptance/test_gate_failure_modes.py`); (e) repo-controlled config row (display only).
- **Review.** Adversarial with differential for (a) to (d). (b) is a speed bump, not a close; say so in the PR.
- **Stop.** The `--resume` or `--bare` probe (step 15) is missing for (a) or (b): wait.

### Phase 2: measure

#### Step 21. V6-20 Replay runner, stats helpers, report
- **Needs.** steps 3, 13, 17. **Safety.** no.
- **Change.** `evals/decision_replay.py`, `evals/stats.py`, `evals/report.py` as in the roadmap card. Reuse `evals/gate_replay.py`, `evals/replay_policy.py`, `evals/run_cases.py`, `jade/evals.py` (`wilson`, `holm_adjust`). [UNRESOLVED] whether evals code may add dependencies: assume no.
- **Tests first.** G3 and G6: stats tested against known tables; seeded runs repeat byte for byte; a cassette miss fails.
- **Stop.** The stats module grows past the helpers of d10 section 15: stop.

#### Step 22. V6-22 Real-shell oracle snapshot (optional)
- **Needs.** steps 5, 13. The owner runs `evals/realshell_oracle.py` (written by the agent) on the Mac before each release; the snapshot `evals/oracle/realshell-<shell versions>.json` is committed; G4 replays the classifier against it in CI. A stale snapshot fails. Print the stop-loss number (L8).

#### Step 23. V6-23 Ledger readouts and label plumbing
- **Needs.** steps 6, 13. **Safety.** record-only but needs approval for the label filter.
- **Change.** Additive ledger columns and commands as in the roadmap card. Comes after the train because it touches `jade/ledger.py`.
- **Tests first.** An old ledger opens with the new columns inside the hook budget; a purge test covers the new columns; forged "ci passed" text stays `agent_reported` (new `tests/acceptance/test_label_sources.py`).
- **Stop.** A migration cannot be additive: stop and ask. Real ledger text stays private.

#### Step 24. V6-21 Rubric and labelled corpus (owner hours)
- **Needs.** steps 13, 21. Agent writes `evals/rubric/` (harm, reversibility, scope; no rule ids) and `evals/labels/agree.py`. The owner labels about 150 gray-zone rows with a second rater. Kappa at least 0.70 (allow vs not-allow). Real rows stay in a private path. Stop state: if the owner cannot label 150 rows, the phase ends with "no accuracy number".

#### Step 25. V6-24 Decision hygiene and spikes (optional)
- **Needs.** steps 6, 23. **Safety.** yes (tightening). DI-3a, DI-4, DI-5, `--no-cache` limits, only `jade ask` writes `jev_result_*` (H17). Spikes SP-0, SP-1, SP-3 send state to openrouter.ai: owner's word first (R13).

### Phase 3: contain and cut asks

#### Step 26. V6-25 Native layers (owner)
- Sandbox pilot after a dry run in a throwaway HOME; one week of the three-part table (prompts per day, sandbox-blocked failures, escapes found). Managed settings only after P1, P2, P6 are answered; a documented manual file and a `jade check` row. Agent job: the `jade check` row tests (CI) and the written steps. Never edit `~/.claude` settings or `/Library` files (SI-09, sudo).

#### Step 27. V6-26 Human text tier 1 (optional)
- **Needs.** steps 6, 9, 15. **Safety.** yes. One question builder for chat, dialog and permit; each question states what Deny and no answer do; plain lines list every widening flag; fix the permit screen two-code mismatch (A7 section 4); `askchat.rewrite` follows d13 X-15: Fix B now (the gate text says to send the JADE question alone), Fix C next release (refuse a batch that carries a JADE token). Fix A, keeping the other questions, is rejected. Facts only: no "suggest Allow". The staged skill and flow text carries the rule of R23 (proposal in the PR; the skill is outside the repo). Reviewer checks that a two-question payload with a JADE token is refused clearly and no loop forms (owner run for the behaviour test).

#### Step 28. V6-27 Small durability fixes (optional)
- **Needs.** step 6. `work.save` writes a temp file, `os.replace`, with a file lock; two SessionStart briefing lines. Tests: a concurrent-save test; a briefing-line test. No auto-opened blocker work items.

### Phase 4: audit and staged autonomy

#### Step 29. V6-29 Memory hardening and the audit of silent allows
- **Needs.** steps 6, 7, 17, 23, 24, 18. **Safety.** yes. Also needs the owner's written friction and autonomy targets from the step 23 baseline (d10 section 13, D-B5, LD-21): do not start this step without them.
- **Goal.** Memory ages by evidence; `jade review confirm --all` is limited; an audit draw the agent cannot read, with stored `sample_prob`; a wrong marked run suspends the scope.
- **Tests first.** The draw cannot be read or set by the agent; wrong mark retires the row and suspends the scope; audited rows weighted by 1/p; `--all` limit; the never-learn test stays green.
- **Review.** Adversarial. Reviewer tries to read the seed or the key.
- **Stop.** Audit load above the cap of 10 labels per week (`ADJUDICATION_PER_WEEK`, `jade/evals.py` near line 21, a code constant, not the owner's measured capacity [UNRESOLVED]): stop and ask.
- **Human.** Picks weekly count and budget (decision 13).

#### Step 30. V6-30 First memory scope, stage 1 (owner)
- Level `medium` with `jev off`, one narrow reversible scope, the owner's written yes. Stage 1 reviews 100% of silent runs until 59 clean reviewed runs from at least 10 sessions with zero wrong (upper bound 4.95% [VERIFIED by script per roadmap, R5 5.4]). One wrong audited run suspends the scope. Never critical, irreversible, R-13, R-18, R-21, R-22 or human-only. Agent job: weekly readout of audits and asks saved; enable only if asks saved are at least 3 times audit reviews (d08 section 11).

### Phase 5: only on evidence

#### Step 31. V6-31 Jev proxy keep or retire (owner decides)
- Run shadow with `jev on` and the deny-count gate. Report agreement. Default (decision 10): stay at shadow; retire if about 97 owner denies still show misses. Proxy v2 stays out of the champion slot.

#### Step 32. V6-32 Report-only learning (optional)
- Reports with no write path to policy. Test that no policy file is written. Precision check on 20 to 30 sampled rows per report (target at least 80%, my number). Regression-case proposals are diffs for a human to merge.

### Phase 6: public readiness (parallel track, optional)

#### Step 33. V6-33 True front page
- **Needs.** steps 10, 13. README, privacy page, platform matrix ("Claude Code, macOS tested, Linux CI only"), demo transcript. State only what JADE sends, not provider terms. Acceptance trial: one developer who is not the maintainer installs on clean macOS and clean Linux (human run).

#### Step 34. V6-34 Release and CI hardening
- **Needs.** step 13. `release.yml`: `fetch-depth: 0`, tag ancestry check, required CHANGELOG section. `ci.yml`: `permissions`, `timeout-minutes`, `concurrency`, pinned actions, gitleaks on new changes, a macOS job, `timing` marks. Test on a fork with dry-run tags. [UNRESOLVED] whether PR 48's real-shell tests may run on a hosted macOS runner.

## 8. Resume protocol

A session can end at any point. The next session must not trust its own memory or this document's status. It reads the state. [RECOMMENDATION]

1. **Read the state, in this order** (read-only commands):
   - `git -C /Users/example/Documents/projects/jade fetch origin` then `git rev-parse origin/main` and `git log --oneline origin/main -15`.
   - `gh pr list --state all --search "V6-" --json number,title,state,headRefName,headRefOid,mergedAt` (PR titles start with `V6-<nn>`).
   - `git branch -r --list 'origin/v6/*'` and `git worktree list`.
   - `jade work list` (the V6 work items, after step 5b).
   - `jade check` and `jade version`.
2. **Mark each step.** DONE = a merged PR titled `V6-<nn>` whose commit is an ancestor of `origin/main` (`git merge-base --is-ancestor <sha> origin/main`). IN PROGRESS = a `v6/<nn>-*` branch or an open PR, not merged. NEXT = the first step in section 7 order whose "Needs" are all DONE.
3. **Verify, do not assume.** For an IN PROGRESS step, open its worktree, run `git status --short` and `git log origin/main..HEAD --oneline`. Run L4 first: the tree may be red or half edited. If another writer holds the worktree (R1), stop and ask.
4. **Check that the base moved.** If `origin/main` is newer than the branch base, merge `origin/main` into the branch (never a stale local `main`), re-run L4, and re-run the differential (L6) because gate files may have changed.
5. **Re-run the last unfinished loop stage.** If L7 findings exist but their fate is not in the PR body, repeat L7. If L9 returned non-zero, do not report verified; follow L9.
6. **Refs move.** Say which ref every claim is about (sha, not branch name). Re-check PR heads against section 0.
7. **Owner decisions.** Re-read section 10; a decision answered in an earlier session lives in the PR body or work item note. If you cannot find it, ask again.
8. **Log.** At the end of every session, write in the PR body (or work item note): the step, the last stage done, the sha, the open questions, the owner-only checks still waiting. In simple English.
9. If anything in the state contradicts this document, the state wins. Tell the owner.

## 9. Global stop conditions

Stop and ask the owner when any of these happens. [RECOMMENDATION]

- The baseline gate is red before you change anything.
- A JADE gate holds a call (R8), or a hook blocks a command.
- A step needs a `JADE_*` variable, sudo, a git write the gate blocks, a settings change, or a live Jev run that the step did not name.
- A review finds a safety regression and the fix is not small.
- Replay shows asks above the owner's bar (R14).
- `jev verify` returns non-zero three times in a row.
- A step touches `policies/safety.yaml`, the permit signing code or the approver scripts, and no written yes exists.
- A secret or private ledger text appears in a file to commit (R13).
- The PR list or `origin/main` moved in a way that changes the order (for example PR 47 merged by the owner with other changes).
- Two attempts at the same fix fail (R21).

## 10. What needs the human

Each item has the roadmap decision number and a default. A safety change waits for the yes. [RECOMMENDATION, from roadmap section 9] The question text and the full options are in d13 section 2 (Q-01 to Q-12), which is the authority if this table and d13 differ. Rows 17 to 20 are batch questions that gate a step.

| # | Decision | Default | Needed before step |
|---|---|---|---|
| 1 | What "v6" means | 0.6.x plus main plus the 0.7.0 stack, PR 47 first | 1 |
| 2 | 0.7.0 timing and contents | Tag after steps 7, 8, 9; step 14 may be 0.7.1 | 13 |
| 3 | PR 47 route | Merge to main first, ship in 0.7.0, no 0.6.2 | 5 |
| 4 | Id plan | Note and test now; renumber from W-117 after PR 47 | 2, 5b |
| 5 | Chat grant key | `plan_hash`; narrow rule only if replay count is too high | 8 |
| 6 | Memory of critical, R-13, R-18, R-12, R-15 | Critical, R-13, R-18 never; R-12 and R-15 exact-only (R-23 too, LD-06 only) | 7 |
| 7 | Level `off` behaviour | Text now; block after the replay count, with R-16 and R-14 scope | 16 |
| 8 | Leave `off`, to which level | `high`, only after step 18 | 19 |
| 9 | `jev` key and default | Build with the rule of roadmap section 2 row 5; no MAXIMUM, no HARD | 18 |
| 10 | Proxy keep or retire | Freeze at shadow; retire if about 97 denies show misses | 31 |
| 11 | OS sandbox and managed settings | Sandbox pilot after a dry run; managed file later | 26 |
| 12 | Purge scrubs label text | Yes, keep hashes and the decision | 10 |
| 13 | Labelling: second rater, hours, audit count, honeypots | Different reader of the written rubric; owner states real hours; no honeypots | 24, 29 |
| 14 | Known-gap list and signer | Owner signs; each row has a date and a work item | 3, 13 |
| 15 | Public goal | Claude Code on macOS, owner first | 33 |
| 16 | Which spec items to drop | Drop MAXIMUM, HARD, DEFER_WITH_PLAN, SAFE_ALTERNATIVE as states | none (docs) |
| 17 | Q-09: may the merge train go onto main | Yes, order 42, 43, 44, 46, 45; no tag | 6 |
| 18 | Q-12: safe display of chat and dialog text | Yes, escape bidi, control and format characters; keep Persian prose | 9 |
| 19 | Q-10: close the known rule holes | Yes, one batch, one independent review, after a replay count | 14 |
| 20 | Q-06: block an old session id (`JADE_SESSION_ID` in the SI-11 list) | Yes | 20 |

**Only the owner does these actions:** merge a pull request; create a tag or release; `jade permit approve`; `jade level`, `jade jev`, `jade gate`, `jade hooks`, `jade upgrade`, `jade setup`, `jade review confirm`; anything with sudo (`scripts/install-approver.sh`); GitHub settings (branch protection, private vulnerability reporting); branch deletes; runs the agent cannot launch (R9); the second rater's labels; the signed known-gap list; any step that sends private ledger text out.

## 11. What I did not do, and what is unresolved

- [VERIFIED] I ran no test suite, no case runner, no oracle, no live Jev call and no write command. I read `origin/main`, the PR list, the worktree list and the help text of `jev`, `jade work` and `jade ack`.
- [UNRESOLVED] The exact flags of `evals/never_looser.py`, `evals/decision_replay.py` and `evals/realshell_oracle.py`. They do not exist yet; V6-03, V6-20 and V6-22 build them. The cards use the shape named in the roadmap.
- [UNRESOLVED] Line numbers (for example `jade/ops.py` near line 1433) come from the roadmap and may have moved. Search by symbol.
- [UNRESOLVED] Whether CI is green on `e9412f3`, and whether `c676801` is weaker than main on shapes beyond the four of A1 7.2. Check at PC-2 and step 5.
- [UNRESOLVED] Whether the JADE gate lets an implementer run `git worktree add`, `git push` of a feature branch and `gh pr create` without a permit. If it holds them, the owner approves (R8); do not work around it.
- [UNRESOLVED] The owner's real weekly labelling hours and deny rate. They set the time of steps 24, 29, 30 and 31.
- [UNRESOLVED] Probes P1 to P6 (step 15). Steps 20, 26, 27 and 31 wait for them.
- [INFERENCE] Step order is mine. Phases 2 and 3 can run in parallel after step 13 if the owner has two worktrees and one writer each (R1 holds per tree).
- Effort sizes live in the roadmap (S, M, L; my estimates). This section gives no time estimate.
