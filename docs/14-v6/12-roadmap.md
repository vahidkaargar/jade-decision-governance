# JADE v6 phased implementation roadmap

This section answers one question: in what order should JADE v6 be built, so that every step has a clear need, a test, a way back, and a gate that the benchmark in section 10 can check? It lists 34 tasks (V6-01 to V6-34) in seven phases, a dependency graph, the exit gates, what not to build, and the smallest set of tasks that gives most of the autonomy benefit with the safety invariants intact. It is a plan. Nothing here is built, merged or approved.

## 0. How to read this section

**Tags.** [VERIFIED] has concrete evidence: an evidence id (A1 to A8, R1 to R6), a file or symbol I grepped on a named ref, or a command I ran in this session. [INFERENCE] is my reasoning. [RECOMMENDATION] is a design proposal. [UNRESOLVED] was not settled. The sibling drafts (d01 to d11 = sections 01 to 11) are proposals. I cite them as the source of a recommendation, never as proof. Research claims keep their confidence from R1 to R6 (most were read through a summary model).

**Every task below is a [RECOMMENDATION].** Anything that changes safety behaviour is marked "safety change: yes" and needs the owner's approval before it is built. The owner is the human.

**Refs.** "main" is `origin/main` at `e9412f3` (0.6.1 plus PR 48) [VERIFIED `git rev-parse origin/main`]. The installed gate on this Mac is `8100ea1` = v0.6.1 [VERIFIED A3]. "WT W-nn" is a row of the dirty backlog in the owner's main checkout; it is not on any ref. "main W-nn" is a committed row. Refs moved during research, so I re-checked the ones that matter:

- [VERIFIED] PR 47 (`claude/heuristic-hamilton-0eae1d`) now has head `c676801` on origin ("Fix W-115: glued here-strings, ..."). `gh pr view 47` shows `test (3.13)` and `test (3.14)` SUCCESS on that oid, MERGEABLE. A1 and d03 to d04 still name `c6ab27a` (pushed) and a local fix; d01 names `967b166` (local only). Those are old.
- [VERIFIED] `967b166` is not an ancestor of `c676801` (`git merge-base --is-ancestor`). Its diff against `c676801` touches `jade/shellnorm.py`, `tests/test_redirect_round4.py`, `CHANGELOG.md` and one spec. [UNRESOLVED] Whether it holds anything `c676801` lacks. Keep that sha until V6-04 has looked.
- [VERIFIED by my probe, archive copies of `c676801` and of main, empty HOME, `CI=1`] The four commands of A1 section 7.2 (`cat<<<'git push origin main'|sh` and three more) all block on both. So the SI-08 regression A1 found at `c6ab27a` is fixed at `c676801` for those four. [UNRESOLVED] Whether `c676801` is weaker than main on any other shape (A1 lists DIFF-W1 leading input redirect and WT W-114 as not restored).
- [VERIFIED] Open PRs now: 42 (base main), 43, 44 and 46 (base `design-clear-questions`), 45 (base `open-items-scopes`), 47 (base main). PR 48 is merged.

**Assumptions (reversible).**
- A-1: "v6" means the 0.6.x line plus main plus the unmerged 0.7.0 stack (PR 42 to 46), with PR 47 handled on its own first. [INFERENCE, A1]
- A-2: Claude Code on macOS, one owner, is the only harness (d03 D-8).
- A-3: Sizes are S (about a day of agent work plus review), M (a few days), L (weeks, or many owner hours). They are my estimates. [INFERENCE]
- A-4: Phase names here are not the P0 to P5 names of d10 section 13. Section 7 maps them.

## 1. Short answer

1. [RECOMMENDATION] Build in this order: clean up and close the known holes (Phase 0); finish the safety floor and leave level `off` (Phase 1); measure (Phase 2); contain and cut asks with native layers and clearer questions (Phase 3); only then widen autonomy, one small scope, behind an audit (Phase 4). Jev decisions, report-only learning and public readiness come last or run on the side (Phases 5 and 6).
2. [INFERENCE] The first v6 result is more asks, not fewer. On this Mac the level is `off` and 3,416 of 3,424 gated calls in 7 days ran with no prompt [VERIFIED A3]. Closing the floor and moving off `off` adds asks first (d03 table after Part A). The ask cut comes later, from three places: project scope for the two noisiest rules (R-16 and R-14 were 77 of 100 answers [VERIFIED A6 section 2.1, one snapshot]), scoped memory behind an audit, and the OS sandbox for containment (not for fewer prompts: the 84% figure is Anthropic internal, no method [R1; R5 saw only a snippet]).
3. [RECOMMENDATION] Phase 0 is large on purpose. Six open PRs, work that exists nowhere in git, duplicate ids and three known holes (memory of critical calls, chat grant, clipped and bidi text) are all risks that a v6 design would stand on.
4. [RECOMMENDATION] Smallest useful v6 (section 8): 25 of the 34 tasks (14 of them are Phase 0). No Jev decisions, no profiles beyond labels, no end-to-end benchmark, no public release work.
5. [RECOMMENDATION] Rule for every task: a change that makes JADE ask less must name the metric it expects to hold and the rows that support it. A change that makes JADE ask more must show the extra asks per day from `evals/gate_replay.py` on real transcripts before it ships.

## 2. Where the drafts disagree, and the choice made here

Each row is a [RECOMMENDATION] and goes to section 9 as an owner decision.

| # | Question | Sources and what they say | Choice here | Owner decision |
|---|---|---|---|---|
| 1 | When is 0.7.0 tagged? | d04 ADR-12 and H15: one train after the memory fix. d10 D-B14: the 0.7.0 gate is separate and uses G1, G2, G5, G7, G8 only. | Merge the train in Phase 0. Tag 0.7.0 only after V6-06 (memory), V6-07 (chat grant) and V6-09 (safe display) are merged, because the train adds memory reach (scopes) and a tag is a public statement. V6-08 (rule holes) may follow as 0.7.1. | H15, D-B14 |
| 2 | How does PR 47 ship? | d01: fold into 0.7.0. d03 D-6: 0.6.2 only if round 5 is clean. d04 H15: separate. | No 0.6.2. Merge PR 47 to main first, after V6-03 and V6-04, and ship it inside 0.7.0. [INFERENCE] At level `off` SI-08 is advisory on this Mac [A3], so a 0.6.2 would not add protection here. Overrule if other users depend on SI-08. | D-6 |
| 3 | Work-item ids | d01 Part E: note now, renumber after 0.7.0. d03 D-7, d11 DX-H9, A1: renumber from W-117. | Both, in order. V6-02 writes the note and a duplicate-id test (old collisions on a visible allow-list). The WT rows are renumbered from W-117 after PR 47 merges, because its commits cite WT W-96, W-108 to W-115. W-115 collides on main itself (sandbox row vs PR 47 meaning): PR 47's meaning is renumbered. | D-7, DX-H9 |
| 4 | Chat grant key | d05 F2 default: narrow rule (target-selecting prefixes and cwd). d03 D-3 and d04 ADR-14: `plan_hash`. | `plan_hash` (tool, text, cwd), as permits do. Count extra asks with `gate_replay` first. If the count is above the owner's bar, fall back to d05's narrow rule. The pinned test `tests/acceptance/test_chat_ask_w100b.py:241` must change either way. | D-3, H4 |
| 5 | The `jev on|off` key | d01 Part E item 7: add nothing until the names are answered. d05 section 3.3: build it, with a ledger-event provenance rule. ADR-03 point 6 and d05 differ on the default at level `high`. | Build it in Phase 1 (V6-28), after the floor tests (V6-17) and before V6-19. Default from ADR-03 point 6 (absent key at `low` or `medium`: on; other levels: off; new installs write `off`). Provenance from d05 (last ledger event wins; an invalid value reads `off`). Keep it in the minimum v6 and build it before V6-19, for two reasons. [VERIFIED docs/12 line 177 on main] Command text and preferences go to openrouter.ai at `low` and `medium` and, as shadow, at `high`: so even level `high` sends data until an off switch exists. [INFERENCE from A6 2.1 and `ops.JEV_BAND_ANSWERS = 50`] At `low` or `medium` Jev could start to decide at once, because 67 distinct answers already pass the gate of 50 (V6-28). | H1, H21, D-4 |
| 6 | Level `off` | ADR-03 point 8 and d05 F3: block SI-07, SI-08, R-13, R-16 at `off`. d03 D-1 and d11 DX-H3: warn now, block after a replay count. | Text now (V6-10). Behaviour in V6-16, with the R-16 and R-14 project scope in the same change and the replay count first. | D-1, H2 |
| 7 | `tool_use_id` column | ADR-10: add now. d11 section 17 and d01 Part D: not built, no consumer. | Defer. Trigger: a named consumer for the join with Claude Code telemetry. | H11 |
| 8 | `jade quickstart` | ADR-11: add an advisory one. d11 section 17, d03 Part H, d01 Part D: do not build. | Do not build. | D-8 |
| 9 | `jade setup --managed` | ADR-04: optional, human-only. d03 Part H: later. | A documented manual file first (in V6-25). Code only if the manual step proves too hard. | H5 |
| 10 | `tree-sitter-bash` | ADR-02: test-only differential now. d03 OP-11: after owner agreement. d01 Part D: do not build until a probe finds a bypass the text scan cannot close. | Defer. The real-shell oracle (V6-22) comes first. | H13 |
| 11 | Auto-opened work items | ADR-07 item 3: open one for a stale permit. d07 section 6.2: do not use `work.add` for blockers. d03 SP-08: only for an expired permit. | Defer. Only the two SessionStart briefing lines (V6-27). | H1 (d07) |
| 12 | Kappa and honeypots | d10 section 8.2 keeps kappa for the offline corpus. d08 section 10 drops kappa and honeypots until 100 labels exist and phase 1 shows rubber-stamping. | Kappa on the frozen offline corpus only (V6-21). Honeypots deferred. Passive attention numbers (time to approve, approve rate) in V6-23. | D-B9, H18 |
| 13 | Package rename W-28 | d11 H6: out of v6. | Out of v6. | DX-H6 |
| 14 | `jev` key tests N28 to N33 | d10 section 13 places them in P5 (profiles). | They move with V6-28, which is built in Phase 1 here. | none |

## 3. Rules for every task

1. **Ask-count line.** A task that can raise asks has this acceptance line: "`evals/gate_replay.py` on real transcripts reports extra asks per day: N". The owner runs it. [RECOMMENDATION, d03 Part A]
2. **Who runs what.** [VERIFIED d10, A3] `evals/run_cases.py` sets `JADE_HOME`, `JADE_CONFIG` and `JADE_GATE_MODE` itself, and SI-11 stops an agent from setting those variables. So an agent cannot launch the case runner, the L2 runner or the real-shell oracle. The owner launches them, or CI does. Anything that needs a new session, sudo, a git write, or a live Jev call is owner: human.
3. **Where a test is verified.** Each card says CI (ubuntu, Python 3.13 and 3.14) or "owner run" (empty HOME and `CI=1`). [VERIFIED A8] Locally about 1,091 tests skip when the approver is installed [INFERENCE: an old 0.5.0 changelog figure, not re-measured], so a local green is weaker than CI. For a Linux-only doubt use `docker run python:3.13-slim` on a `git archive` copy.
4. **Red then green.** A new negative test must fail on a recorded pre-fix sha and pass after, and it has a positive twin so "block everything" cannot pass (d10 section 11.1). A test enters a PR gate only when its fix is merged (d10 section 10.1 activation rule); before that it is a counted `known_gap` (`evals/run_cases.py` already supports `known_gap`, [VERIFIED] header, line 6).
5. **One writer per tree.** One agent edits a tree at a time. Then independent review. The owner re-runs the full gate. Stage explicit paths, never `git add -A` (the main checkout has 236 mode changes, [VERIFIED A1]).
6. **No tag between merges.** Merging is not releasing. A tag needs a merged `origin/main`, CI green on that commit, and a sandbox install check (own HOME, tool dir and ledger): checksums, `--version`, upgrade check.
7. **Real shells only through `evals/realshell.py`.** SI-13 blocks other ways [VERIFIED repo docs/13, main].

## 4. Phase map, dependency graph, master table

| Phase | Name | Tasks | One-line purpose |
|---|---|---|---|
| 0 | Clean-up and known holes | V6-01 to V6-14 | Save the loose work, one id register, merge the pile, close the verified holes, make text true, cut 0.7.0 |
| 1 | Safety floor | V6-15 to V6-19 and V6-28 | Probe the host, level `off` stops being a logger, floor tests, the `jev` off switch, then leave `off` |
| 2 | Measure | V6-20 to V6-24 | Replay runner, labelled corpus, oracle, readouts, decision hygiene |
| 3 | Contain and cut asks | V6-25 to V6-27 | Sandbox and managed settings, clearer questions, small durability fixes |
| 4 | Audit and staged autonomy | V6-29, V6-30 | Audit of silent allows, first memory scope |
| 5 | Only on evidence | V6-31, V6-32 | Jev proxy keep or retire, report-only learning |
| 6 | Public readiness (parallel track) | V6-33, V6-34 | True front page and release hardening |

V6-28 (the `jev` key) keeps its id but belongs to Phase 1: V6-19 needs it (section 2, row 5). Phases 2 and 3 run in parallel after V6-14 and V6-15. Phase 6 can start after V6-11.

### 4.1 Dependency graph (text)

Drawn from the "Depends on" column of the master table. "A <- B, C" means A needs B and C first.

```
Phase 0
  V6-01 <- (none)                 V6-03 <- (none)              V6-10 <- (none)
  V6-02 <- V6-01                  V6-04 <- V6-01, V6-02, V6-03
  V6-05 <- V6-01, V6-02, V6-03, V6-04
  V6-06 <- V6-05    V6-07 <- V6-05    V6-09 <- V6-05    V6-08 <- V6-03, V6-04, V6-05
  V6-11 <- V6-02, V6-05, V6-06, V6-10
  V6-12 <- V6-10 (its step 5 comes after V6-14)
  V6-13 <- V6-01, V6-04, V6-05
  V6-14 <- V6-03, V6-04, V6-05, V6-06, V6-07, V6-09, V6-11
Phase 1
  V6-15 <- (none; start now, in parallel with Phase 0)
  V6-16 <- V6-05, V6-10, V6-14
  V6-17 <- V6-06, V6-07, V6-16
  V6-18 <- V6-05, V6-15
  V6-28 <- V6-05, V6-12, V6-17
  V6-19 <- V6-12, V6-14, V6-16, V6-17, V6-28
Phase 2 (parallel with Phase 3 after V6-14)
  V6-20 <- V6-03, V6-14, V6-17      V6-21 <- V6-14, V6-20
  V6-22 <- V6-04, V6-14            V6-23 <- V6-05, V6-14      V6-24 <- V6-05, V6-23
Phase 3
  V6-25 <- V6-10, V6-12, V6-15      V6-26 <- V6-05, V6-09, V6-15      V6-27 <- V6-05
Phase 4
  V6-29 <- V6-05, V6-06, V6-17, V6-21, V6-23, V6-28
  V6-30 <- V6-16, V6-19, V6-28, V6-29
Phase 5
  V6-31 <- V6-15, V6-23, V6-28, V6-29        V6-32 <- V6-23, V6-24
Phase 6
  V6-33 <- V6-11, V6-14                      V6-34 <- V6-14
```

Critical path: V6-01, V6-03, V6-04, V6-05, V6-06, V6-14, V6-16, V6-17, V6-28, V6-19, then V6-29 (which also waits for V6-21 and V6-23, built in parallel), then V6-30. V6-15 is the longest-lead owner step: start it first.

### 4.2 Master table

Size: S, M, L is effort (reason in the card). Safety: yes means the owner must approve before build. Impact: H, M or L, with the metric the task is expected to move (section 1, point 5). Risk: H, M or L, with one phrase when the task touches the hook path or the safety floor, adds asks, or is hard to undo. [INFERENCE] Impact and Risk are my estimates, not measures. Owner: A = agent builds with review, H = human only, A+H = both.

| ID | Title | Phase | Depends on | Size (effort) | Safety change | Impact (metric it moves) | Risk | Owner |
|---|---|---|---|---|---|---|---|---|
| V6-01 | Save the loose work | 0 | none | S | no | M: loose work saved | L | H orders, A executes |
| V6-02 | One id register for W and T ids | 0 | V6-01 | S | no | M: duplicate W and T ids = 0 | L | A |
| V6-03 | Minimal "never looser than main" check, and the known-gap list | 0 | none | M | no | H: gaps listed with owner and date; never-looser check | L | A writes, H runs and signs |
| V6-04 | PR 47: verify, then merge | 0 | V6-01, V6-02, V6-03 | M | yes | H: PR 47 blocks every A1 shape | M | A + H |
| V6-05 | Merge train PR 42, 43, 44, 46, 45 | 0 | V6-01, V6-02, V6-03, V6-04 | L | yes | H: one clean tree for all later work | H, stale PRs; scopes widen memory | A + H |
| V6-06 | Memory never covers a critical call | 0 | V6-05 | S | yes | H: silent allows of critical = 0 (N7 to N9) | M, more asks | A + H |
| V6-07 | Chat grant bound to the exact call | 0 | V6-05 | M | yes | H: wrong-call grants = 0 (N10, N11) | M, more asks | A + H |
| V6-08 | Close the known rule holes | 0 | V6-03, V6-04, V6-05 | L | yes | H: known holes closed (N18 to N20, N23) | H, hook path and classifier; more asks | A + H |
| V6-09 | Show text safely in chat and dialog | 0 | V6-05 | S | yes | M: raw bidi or clipped risky text shown = 0 (N13, N14) | L | A + H |
| V6-10 | `jade check` tells the truth | 0 | none | S | no (text) | M: `jade check` says what is true | L | A |
| V6-11 | Make the docs and data promises true | 0 | V6-02, V6-05, V6-06, V6-10 | M | yes (purge) | M: docs and data promises true (purge, docs-truth test) | M, purge deletes text | A + H |
| V6-12 | Owner wiring steps | 0 | V6-10 (step 5 after V6-14) | S | no | M: wiring facts known (branch protection, maintain job) | L | H |
| V6-13 | Branch and worktree clean-up | 0 | V6-01, V6-04, V6-05 | S | no | L: no lost branches | L | H |
| V6-14 | Release 0.7.0: decision and cut | 0 | V6-03, V6-04, V6-05, V6-06, V6-07, V6-09, V6-11 | M | no | M: users get SI-13, scopes and fixes | M, release rules | H |
| V6-15 | Host probe session | 1 | none (start now) | S | no | H: unresolved host facts U-01 to U-08 answered | L | H |
| V6-16 | Level `off` becomes "observe plus floor", with R-16 and R-14 scope | 1 | V6-05, V6-10, V6-14 | L | yes | H: level `off` blocks the listed vectors; asks per day | H, safety floor and hook path; scope is a loosening | A + H |
| V6-17 | Floor tests and the never-learn test | 1 | V6-06, V6-07, V6-16 | M | no | H: floor claim proved by tests | L | A |
| V6-18 | Safety backlog: session id, agent launch, MCP readers, malformed events, repo-controlled config row | 1 | V6-05, V6-15 | M | yes | M: five safety gaps closed | M, adds blocks | A + H |
| V6-19 | Leave level `off` (move to `high`) | 1 | V6-12, V6-14, V6-16, V6-17, V6-28 | S | yes | H: owner no longer at `off` | M, asks rise | H |
| V6-20 | Replay runner, stats helpers, report | 2 | V6-03, V6-14, V6-17 | L | no | H: decision accuracy can be computed | L | A writes, H runs |
| V6-21 | Rubric and labelled corpus (150 rows) | 2 | V6-14, V6-20 | L (owner hours) | no | H: 150 labelled rows, kappa | L, owner hours | A + H |
| V6-22 | Real-shell oracle snapshot | 2 | V6-04, V6-14 | M | no | M: parser stop-loss number | L | A writes, H runs |
| V6-23 | Ledger readouts and label plumbing | 2 | V6-05, V6-14 | L | record-only | H: asks, time to answer, label coverage measured | M, schema hard to undo | A + H |
| V6-24 | Decision hygiene (Tier A) and spikes | 2 | V6-05, V6-23 | M | yes (tightening) | M: fewer false "verified" | M, adds escalations | A + H |
| V6-25 | Native layers: sandbox pilot, managed settings | 3 | V6-10, V6-12, V6-15 | M | no (opt-in) | M: containment layer; prompts and escapes counted | M, sandbox may break tools | H |
| V6-26 | Human text tier 1: one builder, consequences, permit screen | 3 | V6-05, V6-09, V6-15 | L | yes | M: misreads of the question | M, text at decisions | A + H |
| V6-27 | Small durability fixes | 3 | V6-05 | S | no | L: durable state, briefing | L | A |
| V6-28 | The `jev` key and the deny-count gate | 1 | V6-05, V6-12, V6-17 | M | yes | H: model egress off by key; deny gate counts denies | M, new SI-09 surface | A + H |
| V6-29 | Memory hardening and the audit of silent allows | 4 | V6-05, V6-06, V6-17, V6-21, V6-23, V6-28 | L | yes | H: false-allow rate can be estimated | H, memory change; owner time | A + H |
| V6-30 | First memory scope, stage 1 | 4 | V6-16, V6-19, V6-28, V6-29 | M | yes | H: first asks removed by learning, with a bound | H, autonomy rises | H |
| V6-31 | Jev proxy: keep or retire | 5 | V6-15, V6-23, V6-28, V6-29 | M | yes | M: proxy kept or retired on data | M | H decides |
| V6-32 | Report-only learning and the case ratchet | 5 | V6-23, V6-24 | M | no | L: report-only learning | L | A |
| V6-33 | True front page, privacy page, platform matrix | 6 | V6-11, V6-14 | M | no | M: install and first block understood | L | A drafts, H approves |
| V6-34 | Release and CI hardening | 6 | V6-14 | M | no (R flag) | M: release rules enforced | L | A + H |

## 5. Task cards

Card fields: **Why** (expected impact), **Size** (reason), **Risks**, **Done when** (acceptance), **Tests** (named, with where verified), **Migration**, **Rollback**, **Reuse** (file and symbol). Phase, depends on, owner and safety flag are in the master table. A "new" test file is a suggested name.

### Phase 0: clean-up and known holes

#### V6-01 Save the loose work
- **Why.** [VERIFIED A1 section 6, d01 B6] Real work lives only outside git history: the spec B plans (3 plans, 1 design, approved 2026-11-14) untracked in `.claude/worktrees/harness-tools-w97` (a worktree whose folder name does not match its branch `design-clear-questions`), 3 probe runtime files, 21 WT backlog rows in the dirty main checkout, local-only `docs-approvals-wording` (ff84b03), `open-items-shell-startup-waves4-6` (7507c10; [VERIFIED `git for-each-ref`, 2026-11-16] on origin and also as a local branch; never delete it), and the orphan `967b166`. Removing a worktree by its shared name would delete the plans. This task comes first because it protects every other task.
- **Size S.** Copy and commit a few files on new branches; no code.
- **Risks.** `git add -A` in the main checkout commits 236 mode changes. A branch delete before this task loses waves 4 to 6.
- **Done when.** Each item above exists on a pushed ref (or the owner chose to drop it, in writing). `git status` in the spec B worktree shows no untracked plan files. The backlog rows are committed with the ids from V6-02.
- **Tests.** Scripted check, owner run: for each sha or file list, `git branch -r --contains <sha>` is non-empty; `git ls-files` lists the plan paths.
- **Migration.** None.
- **Rollback.** Delete the new branches. No history is rewritten.
- **Reuse.** Nothing in code. Use explicit paths.

#### V6-02 One id register for W and T ids
- **Why.** [VERIFIED A1 section 5, d01 B7] On main, W-96 to W-105 are feature ids (CHANGELOG, docs, commits) and main W-115 is the SI-13 sandbox row; WT rows reuse W-97, W-99, W-101 to W-105 and W-115 with other meanings. Threat row T30 on main is "test harness in a real shell"; PR 42 now carries T31 (scope) and PR 46 carries T32, but stale PR 43 and PR 45 still carry the old T30 (scope) [VERIFIED d01 B7]. Ambiguous ids make every later reference unreliable.
- **Size S.** One docs note, one test, one rename list.
- **Risks.** Renumbering breaks citations in PR 47 commits and tests (WT W-96, W-108 to W-115).
- **Done when.** repo `docs/07` says W-96 to W-105 are historic feature ids. A test fails on any new duplicate W or T id; the existing duplicates sit on a visible allow-list that V6-04 and V6-13 shrink. After PR 47 merges, the WT rows are renumbered from W-117 and PR 47's W-115 becomes a new id.
- **Tests.** New `tests/test_ids_unique.py` (CI). Extend `tests/test_docs_truth.py`, which already checks threat rows (CI).
- **Migration.** One renumbering commit for `work-items.jsonl` and `BACKLOG.md`.
- **Rollback.** Revert the commit.
- **Reuse.** `jade/work.py` (`save`, `table`, `dedupe`), `tests/test_docs_truth.py`.

#### V6-03 Minimal "never looser than main" check, and the known-gap list
- **Why.** [VERIFIED A1 section 7.2] PR 47 at `c6ab27a` passed CI while weaker than main on SI-08, because no test covered the shape. A check that compares a candidate with main closes that class of miss and is the first piece of gate G3 (d10). It is needed before PR 47 and the train.
- **Size M.** A small script plus a payload set mined from existing corpora; the payload mining is the work.
- **Risks.** A thin payload set gives false comfort. Keep it labelled "minimal".
- **Done when.** `evals/never_looser.py <ref-a> <ref-b>` prints every command that ref-a blocks and ref-b does not (by `policy.assess` plus `hooks._on_default_branch`), over every command in `evals/**/*.jsonl`, the payload lists in `tests/test_redirect_*.py`, and the four commands of A1 7.2. It exits non-zero on any such command. It reports `c6ab27a` as weaker and `c676801` as not weaker on those four. The known-gap list is recorded here: every open vector of A3 section 8b (`git -C 'a\ b' push`, `git send-pack`, `g=git; $g push`, `gh api .../merge`, shell-profile and agent-config writes, child-agent launch) and the NULL_REDIRECT quadratic timing case (WT W-104) is a `known_gap` row with a date and a work item; CI fails on a row with no work item; the owner signs the list. Today the case files hold none [VERIFIED `git grep known_gap` on main: no row in the adversarial or regression case files]. A row is a visible ceiling, not a pass; V6-08 and V6-18 remove rows as they close them.
- **Tests.** The script itself, plus a unit test with two fixture classifiers; a test that a `known_gap` row without a work item fails (CI). Real run: owner, empty HOME, `CI=1`. `evals/run_cases.py` already supports `known_gap` ([VERIFIED] its header, line 6).
- **Migration.** None.
- **Rollback.** Delete the script.
- **Reuse.** `evals/gate_replay.py`, `evals/replay_policy.py`, `evals/run_cases.py` (case loader), `jade/policy.py` (`assess`), `jade/hooks.py` (`_on_default_branch`). The probe I ran for this section (`policy.assess` and `_on_default_branch`) is the seed.

#### V6-04 PR 47: verify, then merge
- **Why.** [VERIFIED] The pushed head is `c676801`; CI is green; my probe shows the four known commands block. PR 47 closes the SI-08 bypass where a redirect hid the rest of a push (about 4,400 new tests, A1). Merged first, the one real code conflict with PR 46 (`jade/shellnorm.py`, [VERIFIED A1 4c]) is resolved once, in PR 46's rebase.
- **Size M.** Review, mode clean-up, conflict handling. Parser reviews have not converged: four rounds, each found new P0 or P1 gaps [VERIFIED A1, d03 HR-07].
- **Risks.** [UNRESOLVED] `c676801` may be weaker than main on shapes outside the four (DIFF-W1, WT W-114). Parser whack-a-mole: stop after the stop-loss in V6-22.
- **Done when.** (1) `evals/never_looser.py origin/main <PR head at review time>` is empty (the head today is `c676801`). (2) One independent review round, limited to "weaker than main", finds no regression. (3) File modes restored to 644 (A1: `shellnorm.py`, `CHANGELOG.md`, new tests are 755). (4) `967b166` checked for anything missing, then dropped or ported. (5) CI green on the merge commit. Merge to main; no tag (see section 2, row 2).
- **Tests.** `tests/test_redirect_anywhere.py`, `test_redirect_round3.py`, `test_redirect_zsh_and_braces.py`, `tests/test_redirect_round4.py` (CI); a pinned test for `cat<<<'git push origin main'|sh` (CI); the never-looser run (owner). Run the suite with an empty HOME and `CI=1`; reproduce any Linux-only failure in `python:3.13-slim`.
- **Migration.** None.
- **Rollback.** `git revert` the merge commit.
- **Reuse.** `jade/shellnorm.py`, `jade/hooks.py` (`pretool`), `evals/never_looser.py`.

#### V6-05 Merge train PR 42, 43, 44, 46, 45
- **Why.** Six PRs hold the 0.7.0 work: clear questions and scopes (PR 42), plain push and merge lines (PR 43), merge scope needs `--repo` and a newer deny beats an older allow (PR 44), `HO-shell-startup` (PR 46), one-line `jade review` and `jade knows` output and Jev question v2 (PR 45). [VERIFIED A1 4a, d01 B4]
- **Size L.** Two PRs are stale (43, 45 [VERIFIED d01 B1]), three need rebases, `CHANGELOG.md` conflicts in all, repo `docs/05` rows collide, and one PR changes the Jev proxy question.
- **Risks.** (a) Deleting `open-items-scopes` while PR 45 targets it can close PR 45 [A1]. (b) PR 45's `q.human_proxy.would_allow.v2.json` is a safety-relevant change that is not calibrated (WT W-102) and d04 ADR-13 says freeze proxy v2: [RECOMMENDATION] merge the deny-wins rule (PR 44) and the honour test (PR 45), keep v2 out of the champion slot until the owner decides (D-5). (c) The plain first line "Merge pull request 940." hides `--delete-branch`, `--admin`, `--auto`; `test_ask_plain_text.py:208` pins it [VERIFIED A7 2.1, d03 FN-07]. This is a merge gate for PR 42 and PR 43. (d) Scopes widen memory reach before V6-06 lands: no tag in between.
- **Done when.** Order: PR 42 first; rebase PR 43, PR 44 and PR 46 onto it (PR 46 after PR 47 is merged); rebase PR 45 onto the new scopes tip last. A new test shows `--admin` and `--delete-branch` in the merge line. Each merge has CI green and an empty-HOME `CI=1` run. T ids are unique (V6-02). `evals/never_looser.py` is empty for each merge against the previous main.
- **Tests.** `tests/acceptance/test_scopes.py`, `test_ask_plain_text.py`, `test_scope_memory.py`, `test_plain_text_fixes.py` (CI; 207 passed on an older tip of PR 42, [VERIFIED A1 7.1]); new merge-line test; `tests/acceptance/test_jev_proxy_w102.py` (CI).
- **Migration.** Ledger scope columns (PR 42) are additive [VERIFIED A1]. Check `tests/test_ledger_migration.py` opens a previous-release ledger ([UNRESOLVED] whether it does, d11).
- **Rollback.** Revert one merge at a time, newest first. Do not delete base branches until the dependent PR is merged.
- **Reuse.** `jade/askplain.py`, `jade/scopes.py` (on `design-clear-questions`, not main [VERIFIED `git ls-tree`]); `jade/memory.py` (`lookup`; `honoured` was found on `open-items-review-output` and not on main [VERIFIED `git grep`; d08 says only there]).

#### V6-06 Memory never covers a critical call
- **Why.** [VERIFIED A3 section 6, d03 FN-02] A confirmed `kubectl apply`, `pfctl` or key removal covers the whole kind, and the class key drops env prefix, sudo and cwd, so a confirmed staging answer covers prod. `memory.eligibility` does not read the risk level; `memory.NEVER_RULES` is only R-21 and R-22; R-13 and R-18 are in `proxy.NEVER_RULES` only [VERIFIED `git grep` on main: `jade/memory.py:30`, `jade/proxy.py:43`]. docs/12 section 10 says critical calls are asked. Idle today (level `off`); it wakes when the owner sets `low` or `medium` and confirms answers (82 hints wait, A6).
- **Size S.** One guard and a wider class key. The tests are the larger part.
- **Risks.** More asks (d03 table). R-12, R-15 and R-23 are the owner's call (LD-06; Q-03 names only R-12 and R-15): default exact-only (d08 section 11). [UNRESOLVED] Whether a shell write to a protected instruction file reaches class eligibility (d08 5.3 item 2): trace first.
- **Done when.** `memory.eligibility` returns `never` for critical. R-13 and R-18 added to `memory.NEVER_RULES` unless the trace shows they cannot reach it. `class_key` includes target-selecting prefixes (`KUBECONFIG`, `AWS_PROFILE`, `--context`) and cwd for gated rules. `gate_replay` ask count recorded.
- **Tests.** N7 (critical never remembered), N8 (different `KUBECONFIG`, `AWS_PROFILE`, `--context` or cwd gives a different key), N9 (`jade review confirm` of a critical group does not cover the call), all in `tests/acceptance/test_memory_w101.py` (CI); positive twin: an ordinary scoped call is still remembered. Red on the pre-fix sha.
- **Migration.** Existing confirmed rows for critical kinds stop matching. Old class keys no longer match new ones: they age out (30 and 180 days) [VERIFIED A6 7.1]. Say so in the CHANGELOG.
- **Rollback.** Revert the commit. The old key still exists in the ledger.
- **Reuse.** `jade/memory.py` (`eligibility`, `class_key`, `NEVER_RULES`, `lookup`), `jade/proxy.py` (`skip_reason`).

#### V6-07 Chat grant bound to the exact call
- **Why.** [VERIFIED A3 section 7 probe C4, d03 FN-03] A chat Allow is keyed on session, `hold_key` and fingerprints, not on `plan_hash`. An Allow for `KUBECONFIG=staging kubectl apply` was spent on the prod text in another cwd. The human saw one text; the agent ran another.
- **Size M.** The key change is small; the pinned test that asserts the old behaviour must change and the ask load must be measured.
- **Risks.** More questions at the margin (a harmless prefix like `FOO=1` now asks again under `plan_hash`). [UNRESOLVED] C5 and C6 (other session, other agent_id) were masked by SI-07; rerun with the first call closed (d05 F2).
- **Done when.** A grant is spent only on the same `plan_hash` (tool, text, cwd). `gate_replay` extra asks per day recorded; if above the owner's bar, use d05's narrow rule instead (section 2, row 4). Question text says what the grant covers.
- **Tests.** N10 (Allow for text A refused for text B or another cwd), N11 (another session or `agent_id`, first run closed), `tests/acceptance/test_chat_ask_w100b.py` (CI). `test_quoting_and_an_env_prefix_do_not_make_it_a_new_command` ([VERIFIED] line 241) is rewritten, not deleted. N12 already exists (`test_an_answer_to_an_ask_that_was_never_shown_grants_nothing`, d05).
- **Migration.** Grant rows add `plan_hash`; old rows without it are not honoured.
- **Rollback.** Key back to `hold_key`.
- **Reuse.** `jade/askchat.py` (`question_for`, grant lookup), `jade/permits.py` (`plan_hash` [VERIFIED lines 130, 280]).

#### V6-08 Close the known rule holes
- **Why.** [VERIFIED A3 section 8b, R6 section 1.5, d03 FN-05] R-99 rates these low: `curl URL | sh # x` (a trailing comment hides R-22: never remembered, never sent to Jev, yet runs silently), `gh api .../merge`, `git send-pack`, `git -C 'a\ b' push`, `g=git; $g push`, writes to `~/.zshrc`, `authorized_keys`, `CLAUDE.md`, `.mcp.json`, `.vscode/settings.json`, `.codex/config.toml`. Same shape as Copilot CVE-2025-53773 and Codex CVE-2025-61260 (R6, advisory pages read).
- **Size L.** The comment fix is a parser change in the blind-stage check; the rest are rules, each with a benign twin. One batch, one independent review.
- **Risks.** Parser fixes need a stop-loss: no more waves after two independent review rounds find none (d10 11.1 rule 5). Every rule raises asks: replay first. Overlaps PR 46 (`HO-shell-startup`): do the shell start-up files there, the rest here. Not to be mixed with the question text change.
- **Done when.** One test per shape and its twin (`echo hi # curl x | sh` stays allowed). `gate_replay` extra asks recorded. `evals/never_looser.py` empty. The holes still open stay `known_gap` rows (V6-03). The pathological input (a long digit run, the NULL_REDIRECT quadratic case, WT W-104; [VERIFIED A3 8g] the hook budget is 6 s and a slow classifier fails closed) classifies under 1 s and within 2 times main in the same job. This is the G7 timing test of V6-14 only if V6-08 merges before the 0.7.0 tag. If V6-08 ships after the tag (the default, 0.7.1), the timing test is not built for 0.7.0 and stays a signed `known_gap` row in V6-14. HR-11 is the friction side of the same bug.
- **Tests.** N18 (trailing comment, Persian comment), N19 (`-C 'a\ b'`, `send-pack`, `$g push`), N20 (`gh api` merge, graphql `mergePullRequest`), N23 (profile and agent-config writes), in `evals/adversarial/cases.jsonl`, `tests/acceptance/test_gate_blind_spots_w31.py`, `test_gate_canonicalisation.py`, `test_gate_surface.py` (CI).
- **Migration.** New rules change what is asked; list them in the CHANGELOG.
- **Rollback.** Revert one rule per commit. Keep rules in separate commits.
- **Reuse.** `policies/risk.yaml` (R-10, R-13, R-22, R-99), `jade/blind.py`, `jade/shellnorm.py`, `jade/policy.py`, `evals/gate_replay.py`.

#### V6-09 Show text safely in chat and dialog
- **Why.** [VERIFIED d03 FN-06; R6 probes 2 and 9 on `open-items-scopes`; code on main] Chat and dialog questions show raw bidi and control characters. A long command is clipped to 450 head and 150 tail characters (`HEAD_CHARS, TAIL_CHARS = 450, 150`, `jade/askchat.py:31`) and the middle hides before the human clicks Allow. Only the terminal permit screen escapes (`permits.printable`, `jade/permits.py:389`). [UNRESOLVED] How the live chat box and the dialog draw these characters: V6-15 probes it.
- **Size S.** One shared function used in three places. It is a bug fix and does not wait for any language choice (d04 ADR-09).
- **Risks.** `printable()` also escapes ZWNJ (the Persian half-space): do not use it on prose (R6 1.5). Escape commands, paths and content fields only.
- **Done when.** One "show safely" function used by `askchat.question_for`, `askdialog.text_for`, the permit screen, `jade review` and `jade knows`. When a command is clipped, the gated segment is shown in full, or the question says "open the terminal".
- **Tests.** N13 (U+202E, U+2067, zero-width characters escaped), N14 (1,000-character command, risky part in the clipped middle), U+200C kept in prose, all in `tests/acceptance/test_chat_ask_w100b.py` (CI).
- **Migration.** None.
- **Rollback.** Revert; text goes back to raw.
- **Reuse.** `jade/permits.py` (`printable`), `jade/askchat.py`, `jade/askdialog.py` (`text_for`, [VERIFIED line 87]).

#### V6-10 `jade check` tells the truth
- **Why.** [VERIFIED A3 sections 1, 10; d03 FN-04, d11 DX-D1 to D6] `jade check` prints "ok level: off" while SI-08, R-13 and R-16 only advise at `off`, and says "all four" hooks and "every hook inert". The owner can believe in safety that is not there. A cheap truth bug.
- **Size S.** Text and rows; no behaviour change. The behaviour change is V6-16.
- **Risks.** None for safety. The text must not become an oracle for bypass: print rule ids and levels, not payloads.
- **Done when.** Level `off` shows "advisory: SI-07, SI-08, R-13, R-16 and all risk rules only log; SI-09 to SI-12 block". Hook count comes from `len(ops.HOOKS)`. A row says "host mode bypass: JADE is the only gate" when the ledger shows no host prompts. A row compares the installed version with main. A row shows the maintain job state. Rows for pre-push guard installed and remote branch protection read "unknown: owner confirms" until V6-12. `jade hooks install` prints the effective gate mode, what it blocks and the approval backend; no default changes (DX-H4; d11 checklist item 7).
- **Tests.** One test per row in both states (`tests/test_docs_truth.py` style; new `tests/test_check_rows.py`) (CI). A table test over every gate mode and level pair.
- **Migration.** None.
- **Rollback.** Revert.
- **Reuse.** `jade/ops.py` (`check` [line 1433], `HOOKS` [line 38], `_recorded_level`).

#### V6-11 Make the docs and data promises true
- **Why.** [VERIFIED A8 sections 9 to 11; A2 to A8 doc audits as listed in d03 Part D, FN-09, FN-10, FN-12; d11 DX-P1, DX-P2] Examples: repo docs/12 section 10 says critical calls are asked while section 9 and the code say memory covers them; repo docs/00 says the CLI gate is "harness-neutral" while nothing else is wired; README says the maintain job is loaded and omits the 0.6 features; `ops.purge` skips `asks.question_json` and `human_answers.label`, which hold command text; nothing schedules purge off macOS; `jade/ask.py:252` calls `ops.check_drift(None, ...)`, which opens the real home even for a fixture run; five `escalation.yaml` keys have no reader (`abstain_to`, `max_revisions`, `fallback`, `each_question_includes`, `deadline_default_h`, d03 SP-13); `WAITING_DEPENDENCY` is entered by no edge and is left only by `resume` (`jade/state_machine.py:44`, [VERIFIED `git grep` on main]; d07 section 11); repo docs/05 T20 and W-85 are stale.
- **Size M.** Many small edits; the purge change touches the ledger.
- **Risks.** Purge scrub of labels is a privacy change that needs the owner's yes (DX-H2: scrub label text, keep hashes and the decision). Deleting dead states touches repo `docs/02` and the schema: only mark them "reserved, not entered" (d07 D6).
- **Done when.** `tests/test_docs_truth.py` compares counts with code (`len(ops.HOOKS)`, registry count, versions, declared keys against read keys). A test like `tests/test_routing_policy_w53.py` fails on any unread `escalation.yaml` key; unread keys are deleted or wired (`MAX_REVISIONS` is hard-coded at `jade/state_machine.py:11`). `ops.purge` clears both columns. Retention is scheduled off macOS too (a generated timer or cron line) and `jade check` warns when it is absent (d11 checklist item 2). A fixture run writes nothing to the real home. `docs-approvals-wording` (10 min chat approval, 30 min permit) is merged. W-85 closed, T20 fixed. README states "Claude Code, macOS tested".
- **Tests.** `tests/test_docs_truth.py` (extended), new `tests/test_purge_columns.py` (plants text in each table; none survives), new `tests/test_fixture_home.py`, an `escalation.yaml` unread-key test (all CI).
- **Migration.** Purge runs on existing rows at the next maintain run.
- **Rollback.** Revert docs; purge is not reversible by design: keep it behind the owner's yes.
- **Reuse.** `jade/ops.py` (`purge` [line 227], `check_drift` [line 155]), `jade/ask.py` (line 252), `tests/test_docs_truth.py`, `tests/test_routing_policy_w53.py`.

#### V6-12 Owner wiring steps
- **Why.** [VERIFIED A3 section 8h, d03 FN-10] The installed gate is 0.6.1 and lags main; the maintain job is not loaded (`jade check` FAIL), so retention, sweeps, drift and the miner do not run; the pre-push guard and remote branch protection are unknown (W-68, W-69). SI-08 is a text scan; remote branch protection is the real boundary (d04 ADR-04 layer L5).
- **Size S.** A short list of human steps.
- **Risks.** Several steps need sudo (approver install) or the GitHub settings page. An agent must not do them (SI-09).
- **Done when.** The owner has: (1) loaded the maintain job; (2) installed the signed approver (`scripts/install-approver.sh`, needed before any profile above MANUAL, d05 3.6); (3) confirmed branch protection on the default branch and the pre-push guard in the repos where SI-08 matters; (4) turned on private vulnerability reporting and run one full-history gitleaks scan with every hit read (d11 checklist 8, 9); (5) after V6-14, installed the new release. `jade check` shows each row green or honestly unknown.
- **Tests.** `jade check` rows (V6-10). Owner run.
- **Migration.** None.
- **Rollback.** Unload the job; reinstall the previous tag with `jade upgrade --ref`.
- **Reuse.** `scripts/install-approver.sh`, `scripts/verify_install.sh`, `jade/ops.py`.

#### V6-13 Branch and worktree clean-up
- **Why.** [VERIFIED A1, d01 B8] About 40 merged branches, 5 detached scratch worktrees, a stale tracking ref, and a naming trap (branch `harness-tools-w97` is the main checkout; the folder `.claude/worktrees/harness-tools-w97` holds `design-clear-questions`). One wrong delete can lose work.
- **Size S.** Owner-run git commands, after the sha list is saved.
- **Risks.** Deleting `open-items-scopes` before PR 45 merges can close PR 45. Never remove a worktree by its shared name. `git fetch --prune` writes refs.
- **Done when.** The sha list of every deleted ref is saved first. Scratch worktrees are pruned by path. Merged branches the owner agrees to delete are deleted. The WT backlog is committed (V6-01). File-mode noise is gone from the main checkout.
- **Tests.** `git worktree list` and `git branch -a` against the saved list. Owner run.
- **Migration.** None.
- **Rollback.** Recreate a branch from the saved sha (merged branches are ancestors of main).
- **Reuse.** None.

#### V6-14 Release 0.7.0: decision and cut
- **Why.** No branch bumps the version from 0.6.1 [VERIFIED A1]. Users of tag v0.6.1 do not have SI-13, scopes or the fixes above.
- **Size M.** Process, not code, but the release rules are strict.
- **Risks.** [VERIFIED A8 section 8; `.github/workflows/release.yml` on main] The release workflow does not check that the tag is on main, that CI was green on that sha, or that a CHANGELOG section exists: V6-34 fixes that; until then do it by hand.
- **Done when.** Tag cut only from merged `origin/main` with CI green on that commit. Contents: the train, PR 47, V6-06, V6-07, V6-09 (section 2, row 1). Release gate (d10 10.1, D-B14): G1, G2, G5, G7, G8 on approved behaviour, active tests only, plus the never-looser check against 0.6.1. A sandbox install (own HOME, tool dir, ledger) shows checksums, `--version`, and the upgrade check. CHANGELOG lists renamed ids and the memory key change. `known_gap` list signed by the owner (V6-03). One owner-run hook-process latency measure on this release (empty HOME), written beside the 2026-11-09 result file (d11 checklist item 16).
- **Tests.** G1 `evals/run_cases.py`, G2 negative suite, G5 binding tests (N7 to N11, N13, N14), G7 `tests/acceptance/test_ledger_scale_w58.py` plus the pathological-input timing built in V6-08 (WT W-104, the NULL_REDIRECT quadratic case; if V6-08 is not merged it stays a `known_gap` row), G8 `python3 evals/realshell.py audit evals/` prints nothing. CI for G1, G2, G5, G7, G8; the sandbox install is an owner run.
- **Migration.** Ledger migration additive; the release note says so.
- **Rollback.** `jade upgrade --ref v0.6.1`.
- **Reuse.** `.github/workflows/release.yml`, `jade/cli.py` (`upgrade`), `scripts/verify_install.sh`.

### Phase 1: safety floor

#### V6-15 Host probe session
- **Why.** [VERIFIED d04 ADR-04, R1] Six facts decide the native-layer design and nobody has run them: P1 do `claude --bare` and `--safe-mode` skip managed hooks; P2 is the mods guard loaded; P3 does git over SSH work under the sandbox; P4 is auto mode available through this gateway; P5 does a settings `ask` rule reach the owner in auto mode; P6 does a hook in both the managed and the user file run twice. Also: `claude --resume` session id, `defer` in `claude -p`, `askUserQuestionTimeout`, Esc and PostToolUseFailure, SessionStart `source=compact`, which host mode the 3,416 calls used, and how the chat box and dialog draw bidi and Persian text (d09 section 13). All are [UNRESOLVED] today.
- **Size S.** One owner session, one to two hours, in a throwaway folder, in the style of `evals/probes/hook_ask/`.
- **Risks.** Needs a new session (settings and MCP changes are invisible to a running session). Results go stale after a host upgrade.
- **Done when.** A results table with host version, one row per probe, committed under `evals/probes/`. Each downstream task (V6-18, V6-25, V6-26, V6-31) lists the probe it needs.
- **Tests.** None; the output is evidence.
- **Migration.** None.
- **Rollback.** Delete the throwaway folder.
- **Reuse.** `evals/probes/hook_ask/` (`probe_hook.py`, `RESULTS.md`).

#### V6-16 Level `off` becomes "observe plus floor", with R-16 and R-14 scope
- **Why.** [VERIFIED A3 sections 1, 3, 5] At `off` only SI-09 to SI-12 block; SI-07, SI-08, R-13 and R-16 advise; a Write of `settings.json` exits 0, so the agent can unwire the hooks. An invariant that only advises is not an invariant (d04 ADR-03 point 8). The owner's cheapest friction fix is in the same change: R-16 and R-14 were 77 of 100 answers [VERIFIED A6], and part of that is the maintainer building JADE (HR-04, [INFERENCE]).
- **Size L.** Two behaviour changes in the hook path (`hooks._verdict` returns an advisory exit 0 when `level == "off" and blocked`, [VERIFIED `jade/hooks.py` on main]; only SI-09 to SI-12 stay firm) plus a rule review, a replay count and docs.
- **Risks.** Asks rise, mostly from R-16 (d03 table): that is why the R-16 and R-14 project scope ships in the same change. Narrowing a rule is a loosening: the replay must show no should-block call allowed. [UNRESOLVED] Whether the owner prefers text only (d03 D-1 option a).
- **Done when.** At `off`: SI-07, SI-08, and writes to `settings.json` and hook wiring (R-13, R-16 subset, HO-wiring) block; other risk rules advise; `jade level` shows "observe plus floor"; `jade check` warns when the level is `off` and the host does not prompt. R-16 and R-14 use project scope for the maintainer's own repo (human review of the two rules first, d08 6.7). `gate_replay` before and after counts are written in the PR.
- **Tests.** N4 (exists), N5 (SI-08 and a `settings.json` Write block at `off`, new, `tests/acceptance/test_autonomy_floor.py`), N6 (`jade check` warns, new), N6b (an HO call at `off` is still not raised: documents the 1.3 fact). `tests/acceptance/test_level_si12_w99.py` (CI).
- **Migration.** Owner message in the CHANGELOG: asks will rise at `off`.
- **Rollback.** Revert the commit; the text from V6-10 stays.
- **Reuse.** `jade/hooks.py` (`_verdict` [line 427], `pretool` [line 375]), `jade/ops.py` (`_recorded_level` [line 599]), `policies/risk.yaml` (R-14, R-16), `jade/scopes.py` (`kind_project`, from V6-05), `evals/gate_replay.py`.

#### V6-17 Floor tests and the never-learn test
- **Why.** The claim "safety and autonomy are separate" must be a test, not a sentence (d04 ADR-03 point 4). Today List A fails on critical, merge, R-13 and R-18 rows because of the memory gap; V6-06 closes it. The permit and grant binding tests are also not complete: N15 (forged resealed permit row: accepted on the tty backend, refused on the signed one) is a probe only, N16 and N17 exist in part (d05 section 9.2).
- **Size M.** Turn A4's 120-row probe matrix idea into an acceptance test; add a never-learn corpus test; finish the permit tests.
- **Risks.** Without List B ("advisory by design": gate `off`, gate `advisory`) the test fails on day one or proves nothing: name rows by SI, R and HO id.
- **Done when.** List A: for gate `enforce` and level `low`, `medium` or `high`, no floor call runs silently; SI-09 to SI-12 block in every gate mode except `off`. List B is asserted and printed by `jade check`. A never-learn test over every human-only and adversarial case asserts memory returns `never` (or documented `exact`), `proxy.skip_reason` is not `None`, and no scope is offered (d08 5.4 item 2). N15 to N17 exist.
- **Tests.** New `tests/acceptance/test_autonomy_floor.py` (N1 to N6b), new `tests/acceptance/test_never_learn_corpus.py`, `tests/acceptance/test_permits_lifecycle.py` (N15 to N17) (CI). Also the circumvention cases N39 to N43 of d05 section 9.3: split a denied command, script then run, reword after a Deny, a different working folder. N39, N40 and N43 enter as signed `known_gap` rows, not as passes (T18 on `origin/main`); N41 and N42 are asserted. A test enters a gate only after its fix is merged (section 3, rule 4).
- **Migration.** None.
- **Rollback.** Delete the tests; they have no runtime effect.
- **Reuse.** `tests/acceptance/test_memory_w101.py`, `test_level_si12_w99.py`, `jade/memory.py`, `jade/proxy.py` (`skip_reason`), `jade/permits.py`.

#### V6-18 Safety backlog: five small independent items
- **Why.** [VERIFIED A3, d05 section 7.2] Five items, none a condition for autonomy work, each its own commit:
  (a) SI-07 session id (F13, FN-08): after a crash, release of an SI-07 hold needs the same session id, read from `JADE_SESSION_ID` (`jade/util.py:260` [VERIFIED]), and SI-11 does not list that variable [VERIFIED `policies/safety.yaml` line 51]. [INFERENCE, not run] An honest agent is stuck; a dishonest one could set the old id and lift the hold. Fix: add `JADE_SESSION_ID` to the SI-11 list, with its test. Q-06 approves only this line. A human-only `jade decide reconcile` is not part of this task: it is built only if the `--resume` probe (V6-15, U-07) shows a need (d13 X-22, LD-23). Needs the `--resume` probe (V6-15).
  (b) Child agents and hook-skipping flags (F7): `claude -p --settings '{"hooks":{}}'`, `CLAUDE_CONFIG_DIR=`, `--dangerously-skip-permissions`, `codex exec`, `aider`, `cursor-agent` are R-99 low [VERIFIED A3 8e]. New human-only class `HO-agent-launch`, never remembered, never sent to Jev (d04 ADR-05). Needs the `--bare` probe (V6-15). It is a speed bump, not a close (a script goes around a text match).
  (c) MCP file readers and SI-10 (F8, W-73): `read_file` of the permit seal key is rated low M-06.
  (d) Fail closed on malformed events (F11): `tool_name` or `command` as a list; unknown hook name exits 0 [VERIFIED A3 8g].
  (e) Repo-controlled config row (BS-01, BS-02, display only): `jade check` and the SessionStart text list repo-controlled hooks, MCP servers, env keys such as `ANTHROPIC_BASE_URL`, and hidden Unicode in `CLAUDE.md` and `AGENTS.md`. Config that loads before the trust dialog is outside a hook's reach, so this only tells the owner (R6 section 1.1; advisories named in d03 BS-01).
- **Size M.** Five S items.
- **Risks.** (b) and (d) add blocks; (a) changes who can release SI-07 (H22).
- **Done when.** Each item has its N-test red then green and a `gate_replay` count.
- **Tests.** (a) agent sets `JADE_SESSION_ID`, runs `decide outcome`, is refused; (b) N24; (c) N22 in `tests/acceptance/test_gate_surface.py`; (d) N26, N27 in new `tests/acceptance/test_gate_failure_modes.py`; (e) a row test in both states (CI).
- **Migration.** None. (a) changes the hold message: rerun the `--resume` probe.
- **Rollback.** Per commit.
- **Reuse.** `jade/util.py`, `jade/decide.py` (`step ... "reconcile"` [line 108]), `policies/safety.yaml` (SI-11), `jade/policy.py`.

#### V6-28 The `jev` key and the deny-count gate (Phase 1; built before V6-19)
- **Why.** [VERIFIED A4, d04 ADR-03] Today level joins safety features and autonomy features; `level high` still sends data to the model in shadow when preferences or answers exist; there is no way to say "memory on, model off" (the owner's MEDIUM-HIGH). Also [VERIFIED A6 2.1, `jade/ops.py:687`] the proxy readiness gate counts 50 distinct approved calls, including hints, and 67 already pass with 100 allow answers and 0 denies: an "always allow" model would agree 100%. [INFERENCE] Without this task, setting `low` or `medium` could hand decisions to an unproven model.
- **Size M.** One key, one provenance rule, one SI-09 verb, one gate change.
- **Risks.** A same-user process could delete a `jev: off` line and raise autonomy silently; hence the ledger rule. A new SI-09 pattern is new surface. Labels are display only (never stored) so they cannot drift. MAXIMUM, HARD, "high-autonomy" are not built.
- **Done when.** R-21 (egress) is added to `proxy.NEVER_RULES` at every level (H20; a tightening, route LD-22), so the proxy never decides an R-21 call; `jev on|off`; default per section 2 row 5; the effective value is the last `jev` ledger event, and an invalid value reads `off` (`_recorded_level` extended); `jade jev ...` is a human-only verb in SI-09 (and the module block for `jade.proxy` and `jade.memory`); at `off`, `proxy.step` returns before it builds any state, so nothing goes to the model, also in shadow. The readiness gate counts owner denies (target at least 30 denies from at least 3 rules, zero false allows), not answers. `jade level` shows a derived label (MANUAL `high`+off, ASSISTED `medium`+off, BALANCED `medium`+on, FAST `low`+on; labels are for display). A signed permit backend is installed for any label above MANUAL (V6-12).
- **Tests.** N28 (agent runs `jade jev` or imports `jade.proxy`), N29 (last event `off` plus deleted key reads `off`), N30 (config `on` but last event `off` reads `off`), N31 (`jev: maybe` reads `off`, `level: Hgih` reads unset), N32 (`jev off` at `high` with a fake provider makes zero provider calls), N33 (`JADE_JEV` only if an env override is added: do not add one), in new `tests/acceptance/test_autonomy_keys.py`; `tests/acceptance/test_jev_proxy_w102.py` (CI). A shadow-ends-by-denies test. An R-21 test with a fake provider: an R-21 call at `low` and at `medium` makes zero provider calls, in `tests/acceptance/test_jev_proxy_w102.py`.
- **Migration.** Not a no-op for `low` and `medium`: R-21 calls stop going to Jev (they are asked, a tightening); Jev stays on there, but the deny-count gate means it stays in shadow (decides nothing), so anyone relying on Jev's decisions at those levels sees more asks. The gate is only a minimum: reaching it does not turn deciding on. [RECOMMENDATION] Jev stays in shadow, and any move from shadow to decide is a separate human decision with its own evidence (LD-08, V6-31). A run with 0 misses on 30 denies bounds recall only at about 90.5% [INFERENCE], and the proxy cannot be validated at this volume (d04 ADR-v6-13). `high` stops the shadow call (a privacy tightening). Docs say `high` is the strict end.
- **Rollback.** Delete the key; older JADE keeps reading `level`.
- **Reuse.** `jade/proxy.py` (`step` [line 205], `skip_reason` [line 129]), `jade/ops.py` (`_recorded_level`, `JEV_BAND_ANSWERS`), `policies/safety.yaml` (SI-09, SI-11).

#### V6-19 Leave level `off`
- **Why.** [INFERENCE from A3 section 0] On this Mac JADE is mostly a logger. The first safe step is level `high` (d04 ADR-03 point 9). It comes after V6-28 because [VERIFIED docs/12 line 177 on main] command text and preferences go to openrouter.ai at `low` and `medium` and, as shadow, at `high` (d03 FN-13: only when preferences or confirmed answers exist; 82 hints wait, A6). Without an off switch, leaving `off` would start egress. Do not go to `low` or `medium` yet: V6-28 also fixes the gate that would let Jev decide.
- **Size S.** Two owner commands (`jade jev off`, then `jade level high`), but a real change in daily life.
- **Risks.** Many more asks. Set it only with the ask-count baseline in hand and the maintain job loaded (V6-12). If the owner keeps Jev on at `high`, command text leaves the machine in shadow: that is the owner's choice (needs_human).
- **Done when.** `jev` is `off` and level is `high`, both set by the owner at a terminal. Before and after counts (asks per day, time to answer) are read from the existing ledger (`would_block` events are a free baseline, [VERIFIED A6 section 8]; many come from building JADE). Rule: if asks per day rise and the owner stops answering, go back and fix the rule (V6-16), do not go lower.
- **Tests.** `jade check` row; N32 (`jev off` at `high`, zero provider calls) from V6-28. Owner run.
- **Migration.** None.
- **Rollback.** `jade level off` (human).
- **Reuse.** `jade level`, `jade jev` (V6-28), `jade ledger stats`.

### Phase 2: measure

#### V6-20 Replay runner, stats helpers, report
- **Why.** Decision accuracy cannot be measured today: the live ledger has zero labelled rows [VERIFIED A6]. The existing 191 and 136 gate case rows are evidence about level unset only, and only 2 of 191 adversarial gate cases are benign (d10 section 2). G3, G6 and the report need code that does not exist.
- **Size L.** A runner, small stats helpers and a one-file report.
- **Risks.** A custom statistics module grows. Keep it to the helpers in d10 section 15 and import `jade/evals.py`. [UNRESOLVED] Whether the repo has a no-new-dependency rule for evals code.
- **Done when.** `evals/decision_replay.py` runs a frozen call corpus through `hooks.pretool` per arm (version, level incl. unset and `off`, gate mode) and prints a confusion matrix and a paired report; it includes the full never-looser ratchet (G3: candidate blocks every case main blocks). `evals/stats.py` has exact zero-failure bound, kappa, rank interval, design effect, tested against known tables (G6). `evals/report.py` prints n, cluster count and pass, fail or not-enough-data per gate. Output reproduces byte for byte. A cassette miss fails a test (`evals/cassettes/jev-<resolved_model>.jsonl`; a cassette run tests the code path, not Jev's accuracy).
- **Tests.** G3, G6 (CI); the paired report (owner or release job).
- **Migration.** None.
- **Rollback.** Delete the files.
- **Reuse.** `evals/gate_replay.py`, `evals/replay_policy.py`, `evals/run_cases.py`, `jade/evals.py` (`wilson` [line 42], `holm_adjust` [line 94]).

#### V6-21 Rubric and labelled corpus
- **Why.** Labels must not depend on any arm's decision, and not on `policies/risk.yaml` (a rule would label itself; d10 8.1). Two stored labels per call: `harm_label` and `authority_label`.
- **Size L.** About 150 gray-zone rows per rater, about 5 owner hours each (d10 6.4, [INFERENCE]). Code is small; the owner hours are the cost.
- **Risks.** `ADJUDICATION_PER_WEEK = 10` is a code constant, not the owner's measured capacity ([VERIFIED] `jade/evals.py:21`; [UNRESOLVED] the owner's real hours). Real rows hold real commands: keep them in a private path outside the repo; commit only synthetic or redacted rows, behind a secret scan (G8).
- **Done when.** `evals/rubric/` (written from harm, reversibility, scope; no rule ids). `evals/labels/` with two raw labels, final labels, `rubric_version`, source, adjudicated flag. 150 gray-zone rows labelled by two raters, kappa at least 0.70 on allow against not-allow (my number, d10 8.2), labels frozen by hash. Hard-class rows (SI-xx, HO-*) are counted per family, not given an accuracy rate. Stop state: if the owner cannot label 150 rows, this phase ends with "no accuracy number".
- **Tests.** `evals/labels/agree.py` (kappa, confusion) tested by G6 (CI); G8 secret scan over every row (CI).
- **Migration.** None.
- **Rollback.** Delete the label files.
- **Reuse.** `jade/evals.py`, `evals/judgment/`, `evals/calibration/`.

#### V6-22 Real-shell oracle snapshot
- **Why.** [VERIFIED repo docs/13, `evals/realshell.py`, main] PR 48 runs a corpus command in real bash, zsh or sh under macOS `sandbox-exec`, with PATH stubs that record calls to `git`, `gh`, `rm`, `curl`, `ssh` and others. Truth comes from execution, not from a rater: a payload where the shell reaches `git push origin main` and the classifier says allow is a bypass. It gives the stop-loss number the parser work lacks (d03 HR-07).
- **Size M.** A driver, a committed snapshot, a checker.
- **Risks.** macOS only; CI is ubuntu and the Linux backend is not built (W-115, [VERIFIED repo docs/13]). Stubs cover only the listed programs. Never aim a destructive payload at a real path. SI-13 blocks agents that run a corpus any other way.
- **Done when.** `evals/realshell_oracle.py` writes `evals/oracle/realshell-<shell versions>.json` on the owner's Mac before each release; G4 replays the classifier against it in CI. A stale snapshot fails. Stop-loss: the number of new SI-class gaps per independent review round per 100 payloads is printed; stop adding parser waves after two rounds with none.
- **Tests.** G4 (CI replays a file); `tests/test_realshell_sandbox.py`, `tests/acceptance/test_si13_realshell_gate_w115.py` (CI). The snapshot run is an owner run.
- **Migration.** None.
- **Rollback.** Delete the snapshot and G4.
- **Reuse.** `evals/realshell.py`, `jade/harness_audit.py`.

#### V6-23 Ledger readouts and label plumbing
- **Why.** [VERIFIED A6, A8] Human load is not measured (no time to approve, no prompts per hour, no expired-ask count); labels exist only for accepted calls that ran; the one label is "the action succeeded", reported by the agent (`decide.record_outcome(actor="agent")`); 165 decisions have no verify call attached. The two load counters disagree (7-day `jade check` 3,424 gated calls; 14-day `jade ledger stats` 2,666) and nobody reconciled them [UNRESOLVED d04].
- **Size L.** Additive ledger columns, new commands, and the label rules; it touches `jade/ledger.py`, so it comes after the train (d06 O-6).
- **Risks.** A schema change is hard to undo. Real ledger text is private (d10 8.4). The DI-8 label filter changes what counts as a label: record-only but needs approval (d06; route LD-31, which must be answered before this task).
- **Done when.** `jade check --json` and `jade ledger stats --json` exist; both load counters are reconciled in writing. Per-rule ask counts, time to answer, expired asks and approve rate print in plain words (the `jade review` jargon "silent share", "plain-high", "95% bound" is replaced, A7). Verify calls are linked to decisions by default (at least 95% in a scripted run, d10). New columns: label type (claim true, owner would allow, harm observed; never merged), `sample_prob`, audit source. The shadow-agreement join reports owner denies, Jev recall on them and false allows, with counts. `observed` and FAILED labels need hook-seen or CI evidence (`goal_check`). `jade review` shows friction beside the audit signals, or says "correctness not measured" (d11 checklist item 17). Passive attention numbers only; no fake asks.
- **Tests.** Old ledger opens with the new columns inside the hook budget; a purge test covers the new columns; `exit_only` and `agent_reported` never label for fit; forged method text "ci passed" stays `agent_reported` (new `tests/acceptance/test_label_sources.py`) (CI).
- **Migration.** Additive columns; old rows stay null.
- **Rollback.** Stop writing the columns; old code ignores them.
- **Reuse.** `jade/ledger.py`, `jade/evals.py` (`auto_label` [line 220], `FIT_SOURCES`), `jade/decide.py` (`record_outcome` [line 86]), `jade/ops.py` (`check`).

#### V6-24 Decision hygiene (Tier A) and spikes
- **Why.** [VERIFIED A2, d06] Repeat detection is exact text, so a trailing space or a reworded claim gets a fresh Jev sample; `--no-cache` resamples without limit; `jade ask --risk` defaults to `low` and ignores the decision's risk; an agent can send `jev_result_accept` itself and walk a decision to ACCEPTED with no Jev call. The hook does not read ACCEPTED, so the gate is safe; the harm is a false "verified" and dirty labels. All changes only turn "yes" into "not yes", record more, or reuse a stored answer for the same claim (d06 section 0).
- **Size M.** Five small changes plus three spikes.
- **Risks.** Stricter results add escalations: stop rule, default 5 extra escalations per 100 decisions with no avoided wrong accept (d06 section 11; the owner sets N). Head-plus-tail truncation can flip a verdict either way: not in this task.
- **Done when.** DI-4 (use the stricter of `--risk` and the decision's risk), DI-5 (revision cap stops the provider call; a cached re-ask uses no revision), DI-3a (whitespace-only repeats reuse the stored verdict and are journalled), `--no-cache` journalled, capped, test and human only (H16); only `jade ask` may write `jev_result_*` (H17). Spikes (read-only; SP-0 needs the owner's word because state goes to openrouter.ai): SP-0 raw Jev body, SP-1 noise on trivial changes (20 calls each), SP-3 replay of the 136 stored verify calls.
- **Tests.** A2's `probe_risk.py` turned into a test (linked risk medium plus `--risk low` gives the medium band); a reworded claim stays a new call; trailing newline returns the stored verdict with no provider call; `--no-cache` answers 0.5 then 0.9 stays exit 3; after the cap zero provider calls; an agent `jev_result_accept` without a matching call raises `IllegalTransition` (CI; the forbidden-edge test file `test_phase2_enforce.py` is skipped on this Mac, W-93, so CI is the gate [VERIFIED A2]).
- **Migration.** None for the first four; H17 changes who may write.
- **Rollback.** Revert the repeat key.
- **Reuse.** `jade/ask.py`, `jade/decide.py`, `jade/state_machine.py` (`MAX_REVISIONS`), `jade/cli.py` (line 74 `evidence_source`).

### Phase 3: contain and cut asks

#### V6-25 Native layers: sandbox pilot, then managed settings
- **Why.** [INFERENCE, web page via summariser; R1 not run] The OS sandbox limits Bash and its children: credential reads, network to unknown hosts. It does not cover Edit, Write, MCP, hooks, `gh api .../merge` to an allowed host, or a child `claude -p` (d03 OP-01 table). Managed settings are root-owned, so hooks cannot be removed from below. On this Mac host prompts are already near zero, so the value is containment, not fewer prompts [INFERENCE, d04 ADR-04].
- **Size M.** Owner steps and a `jade check` row per layer; no JADE containment code.
- **Risks.** Git over SSH fails through the sandbox proxy on macOS (R1). Managed settings need sudo and each hook change then needs sudo. Do not set `allowManagedHooksOnly`. A hook in both files may run twice (P6). The sandbox is not a cure for the other holes.
- **Done when.** Step 1, sandbox: dry run in a throwaway HOME; then `failIfUnavailable`, `allowUnsandboxedCommands: false`, `sandbox.credentials` deny for `~/.ssh`, `~/.aws` and the vault, a short `allowedDomains`; one week of a three-part table (prompts per day, sandbox-blocked failures, escapes found). Step 2, managed settings, only after P1, P2 and P6 are answered: a documented manual file and a `jade check` row ("profile X, but host mode is bypass: JADE is the only gate"); `jade setup --managed` is not built (section 2, row 9). `jade check` warns on `askUserQuestionTimeout`.
- **Tests.** `jade check` row tests in both states (CI). The before and after table is an owner run.
- **Migration.** None in JADE.
- **Rollback.** `sandbox.enabled: false`; remove the managed file with sudo.
- **Reuse.** `jade/ops.py` (`check`), `evals/probes/hook_ask/`.

#### V6-26 Human text tier 1
- **Why.** [VERIFIED A7, d09 section 0.1] This is the fix-the-display-bugs baseline of d09, not a big redesign. Chat and dialog and permit text come from different builders; the question does not say what Deny or no answer does; the permit screen has a two-code mismatch; `askchat.rewrite` keeps one question and drops the rest, which conflicts with the flow skill that batches up to four (not run live, [VERIFIED code A5 2b]).
- **Size L.** Four changes in three channels, each with a test, and a host re-probe.
- **Risks.** Every item changes what a human sees at a safety decision. Facts only: no "suggest Allow" and no "can be undone" unless a rule marks reversibility on purpose (d04 ADR-14 point 4; an allow count can push the owner toward Allow when about 93% of prompts are approved, R1 section 4, via summary). The 93% is Anthropic's number, not this owner's.
- **Done when.** One question builder for chat, dialog and permit (field list from `escalation.yaml`, which also implements W-83). Each question states what Deny does, what no answer does (numbers read from the channel constants), the working folder, who asks. Plain lines list every widening flag; an unreadable call says so (`--admin`, `--delete-branch`). The permit screen shows the same code it accepts, shows the command in the signing helper, and journals a refusal (`n` = refusal; the word `deny` = Deny). `askchat.rewrite` handles a batch by d13 X-15 and d07 section 4.1: Fix B now (the gate text says to send the JADE question alone), Fix C next release (`rewrite` refuses when a JADE token comes with other questions). Fix A (keep the other questions in the popup) is rejected, because a mixed or yes-to-all answer could read as approval of a gated call. Native `ask` re-probed (V6-15). The staged text for the skill and the flow text carries the rule "a Deny ends that goal; do not split, script or reword a denied operation" (d05 section 4.1 note, d07 section 8.2). That text lives outside the repo, so it is written into the PR as a proposal for the owner (d14 R12, R23). JADE only records and measures this; it cannot block it.
- **Tests.** One test per segment family; "code shown equals code accepted"; refusal journalled with hold keys unchanged; an agent-behaviour test that a two-question payload with a JADE token is refused with a clear message and no loop forms (CI for builders; the behaviour test is an owner run).
- **Migration.** None.
- **Rollback.** Revert one channel at a time.
- **Reuse.** `jade/askplain.py` (from V6-05), `jade/askchat.py`, `jade/askdialog.py`, `jade/permits.py`, `policies/escalation.yaml`.

#### V6-27 Small durability fixes
- **Why.** [VERIFIED d07, A5] `work.save` is not atomic and has no lock; the SessionStart briefing prints at most 8 lines and ignores `source`; nothing tells the next session which decisions are held. On 2026-11-13 the owner gave 18 terminal approvals, then 18 asks expired (repo docs/12 section 1, via d03 HR-03). No workflow engine is needed (ADR-01).
- **Size S.** Temp file plus `os.replace`; two computed briefing lines.
- **Risks.** None for safety. Do not auto-open work items for blockers (d07 6.2).
- **Done when.** `work.save` writes a temp file and replaces it, with a file lock. SessionStart shows "decisions held in this project: N" and "permits that expired twice: N" (told once, d07 D2). On `compact` or `resume` open ask, permit and hold ids are re-shown.
- **Tests.** A concurrent-save test; a briefing-line test (CI).
- **Migration.** None.
- **Rollback.** Revert.
- **Reuse.** `jade/work.py` (`save` [line 34]), `jade/ops.py` (HOOKS, SessionStart text).

### Phase 4: audit and staged autonomy

#### V6-29 Memory hardening and the audit of silent allows
- **Why.** [VERIFIED A6 7.1, d08] A silent use renews a row's life up to the 180-day cap, so a silent call never ages out early; `jade review confirm --all` takes every group after one "yes"; 100 of 100 answers are allow, so owner clicks are hints, not evidence. Labels exist only for accepted calls, so a false-allow rate cannot be measured unless a random audit exists. A deny-wins rule arrives with the train (V6-05).
- **Size L.** Memory change, an audit draw the agent cannot read, an audit queue, label types, an owner time budget.
- **Risks.** The audit costs the owner time against "minimum friction": audit load must stay inside the existing cap of 10 labels per week (d04 ADR-08). Stricter steps add asks and may lower care: report the approve rate of the new asks and pause above an owner-set value (start at 95%). A pause is a loosening, so it only returns to the last human-approved setting, never below it, and it is written to the ledger as an event the owner sees (d04 ADR-v6-08). Never auto-promote. Honeypots (fake asks) are not built (section 6).
- **Done when.** Memory aging by evidence (a row that ran silently N times or M days with no mark expires); `--all` limited. The audit draw is made inside the hook with a stored `sample_prob`, with a signing key and a seed the agent cannot read; a fixed weekly count per scope, staged: stage 1 reviews 100% of one scope's silent runs up to 59, stage 2 samples. A wrong run suspends the scope. `jade review` shows the audited false-allow rate with its upper bound and session count; under 10 sessions it shows counts and no rate. Owner picks the weekly count and the budget (D-B9).
- **Tests.** The draw cannot be read or set by the agent; a wrong mark retires the row and suspends the scope; audited rows are weighted by 1/p; the aging test; `--all` limit test; the never-learn test still passes (`tests/acceptance/test_never_learn_corpus.py`) (CI).
- **Migration.** Additive sampling table and label-type column (V6-23).
- **Rollback.** Stop sampling. The loop stays proposal-only.
- **Reuse.** `jade/memory.py` (`lookup`, `honoured` from V6-05), `jade/evals.py` (`adjudication_queue` [line 244], `wilson`), `jade/ledger.py`, `jade review`.

#### V6-30 First memory scope, stage 1
- **Why.** This is where asks first go down because of learning: one narrow, reversible class with at least 10 sessions of confirmed answers behind it (d08 section 7). The loop cannot start at level `off`: memory and Jev do not run there, so there are no silent allows to audit [VERIFIED A4 3].
- **Size M.** The decision and a wait for 59 clean reviewed runs (time unknown: use rate per scope is [UNRESOLVED]). At 10 reviews a week about 6 weeks of review plus the time to collect uses.
- **Risks.** One audited wrong silent allow suspends the scope. Never critical, never irreversible, never R-13, R-18, R-21, R-22, human-only. Enable a scope only if asks saved are at least 3 times audit reviews (d08 11). A narrower scope's evidence does not carry over to a wider one.
- **Done when.** Level `medium` with `jev off` (ASSISTED), one scope on, the owner's written yes. After 59 clean reviewed runs from at least 10 sessions with zero wrong, the upper bound is 4.95% [VERIFIED by script, R5 5.4; d10 section 9: the rule of three gives 5.08%]. Then lift the cap and use the fixed weekly count (stage 2). The claim allowed is "scope S false-allow rate under 5%, n and sessions stated".
- **Tests.** N7 to N9 stay green; the stop rule fires in a simulated wrong run (CI). The statistical gate is an owner run.
- **Migration.** None.
- **Rollback.** Put the scope back to asking; one command.
- **Reuse.** `jade/scopes.py`, `jade/memory.py`, `jade review`.

### Phase 5: only on evidence

#### V6-31 Jev proxy: keep or retire
- **Why.** [VERIFIED d03 OP-02, A6] The proxy has 48 live calls, 0 allowed, no calibration, and sends data to a third party. A deny-recall bound of plus or minus 10 points needs about 97 owner denies (R5 5.4, script); live data is 100 allows and 0 denies. At about a 7% deny rate (Anthropic's rate, not this owner's) that is about 1,370 answered prompts (d03 MG-03, [INFERENCE]).
- **Size M.** Mostly a decision, plus a replay and a probe.
- **Risks.** Keeping it costs data exposure and upkeep. Auto mode may not exist through this gateway (P4, [UNRESOLVED]); a comparison with auto mode cannot run offline (OP-04 blocked).
- **Done when.** Shadow runs with `jev on` and the deny-count gate (V6-28). The owner decides (D-5): (a) freeze at shadow until the deny gate and a JADE versus Jev-shadow replay exist (default), or (b) retire. Move to (b) if, with about 97 denies logged, shadow still misses denies. Proxy v2 template stays out of the champion slot meanwhile.
- **Tests.** Existing: the proxy never sees critical, human-only or fetch-and-exec calls (`tests/acceptance/test_jev_proxy_w102.py`) (CI). New: shadow agreement report (owner run).
- **Migration.** None.
- **Rollback.** `jade jev off`.
- **Reuse.** `jade/proxy.py`, `evals/gate_replay.py`, the shadow join (V6-23).

#### V6-32 Report-only learning and the case ratchet
- **Why.** [VERIFIED d08, A6] Most of what the spec asks for (blocker classes, recurring failures, regression-test proposals, policy recommendations) is counting plus a report for a human. The ledger does not record which invariant fired (W-74, [VERIFIED A6 8]).
- **Size M.** Reports with no write path to policy.
- **Risks.** A report that is wrong still steers the owner: check precision on 20 to 30 sampled rows per report (target at least 80%, my number).
- **Done when.** Blocker classification by rule id; a regression-case proposal for every wrong mark, audit false allow or counterfactual false reject (the case must fail on the version that erred and pass on the fix; JADE writes a proposed diff; a human merges; the benign twin is human-written or marked "twin unverified"); policy-recommendation tables replayed with `evals/gate_replay.py` and `evals/ho_replay.py` (the human-only corpus must show zero flips).
- **Tests.** A report-code test that no policy file is written (CI); precision check (owner run).
- **Migration.** Record the fired invariant id in the ledger (additive).
- **Rollback.** Delete the reports.
- **Reuse.** `jade/improve.py` (`ALLOWED`, `propose`), `evals/gate_replay.py`, `evals/ho_replay.py`.

### Phase 6: public readiness (parallel track)

#### V6-33 True front page, privacy page, platform matrix
- **Why.** [VERIFIED d11 section 15] The repository is already public under MIT [VERIFIED `gh repo view`, this session]. Ranks 1 to 4 of what blocks adoption are statements that are wrong or missing: retention promise, `jade check` text, README omissions, undisclosed flow of command text to a third party at levels `low`, `medium`, `high` (also in shadow).
- **Size M.** Docs, reviewed line by line against `adapter.py`, `proxy.py`, `ask.py`.
- **Risks.** A privacy page is a promise: JADE states only what it sends, not the provider's terms (DX-H8).
- **Done when.** README front page with two readouts (the safety floor and the autonomy settings), a platform matrix ("Claude Code, macOS tested, Linux CI only"), a demo transcript, a data table, the trust root, honest latency with date and n. Fixtures use neutral labels; the "Mac mini" text moves to repo `docs/08`. The acceptance trial: one developer who is not the maintainer installs on clean macOS and clean Linux; record steps, minutes, owner questions and whether the first block was understood (d11 checklist 18).
- **Tests.** `tests/test_docs_truth.py` extended (CI); the trial is a human run.
- **Migration.** None.
- **Rollback.** Revert docs.
- **Reuse.** `README.md`, `tests/test_docs_truth.py`, `jade/adapter.py`, `jade/proxy.py`, `jade/ask.py`.

#### V6-34 Release and CI hardening
- **Why.** [VERIFIED A8, d11 sections 8, 9] The release workflow does not check that the tag is an ancestor of main, that CI was green on that sha, or that the CHANGELOG section exists; no `permissions:`; actions not pinned by sha; gitleaks config unused; no macOS job; about 15 assertions compare time to a bound and can flake.
- **Size M.** Workflow edits and tests on a fork.
- **Risks.** A stricter release workflow blocks a hurried release (that is the point). SHA pins need updates (dependabot).
- **Done when.** `release.yml` uses `fetch-depth: 0`, an ancestry check and a required CHANGELOG section; `ci.yml` has `permissions`, `timeout-minutes`, `concurrency`, pinned actions, gitleaks on new changes; a macOS job runs setup, hook payloads and `scripts/verify_install.sh` (this is not the real-shell oracle job, which d10 D-B8 and d13 LD-21 decide not to run in CI); known timing tests are marked `timing`; the W-95 200-run loop in `python:3.13-slim` is recorded.
- **Tests.** Dry-run tag on a fork: a tag on a side branch fails, a tag with no CHANGELOG section fails; a planted fake secret outside `tests/` and `evals/` fails the scan in a scratch PR. [UNRESOLVED] Whether PR 48's real-shell tests may run on a hosted macOS runner.
- **Migration.** None.
- **Rollback.** Revert workflow files.
- **Reuse.** `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.gitleaks.toml`.

## 6. Defer and do not build

Reasons are in the cited drafts. "Trigger" is the evidence that reopens the item.

| Item | Verdict | Reason | Trigger |
|---|---|---|---|
| A JADE-owned runtime, scheduler, workflow engine (Temporal, DBOS, LangGraph, Restate, Inngest) | Do not build | Each needs the agent loop inside its code; `/goal`, `/loop`, Monitor and cron exist (d04 ADR-01) | A second host with no resume, or unattended runs that survive process death and cloud routines do not run JADE hooks |
| Autonomy profiles as stored states; MAXIMUM; HARD; `approvals` key; Jev bar override; per-rule autonomy | Do not build | MAXIMUM has no safe meaning; labels are derived (d05 3.5) | The owner states what two adjacent names differ by, with evidence |
| Levels MEDIUM-HIGH and HARD as new level values | Defer | MEDIUM-HIGH is `medium` + `jev off` (a label in V6-28); HARD is undefined | Owner defines HARD (D-4) |
| Scoped autonomy budgets (spec "budgets") | Defer | No measured need; options (a) to (c) first (d05 6.1) | Asks per week at `low` or `medium` with the sandbox on are still too many |
| `DEFER_WITH_PLAN` and `SAFE_ALTERNATIVE` as states or verdicts; a typed `Verdict` class; wiring `WAITING_DEPENDENCY` or `RETRY_SCHEDULED` | Do not build | Nothing consumes them; a model-written alternative is a forbidden channel (d04 ADR-06) | A metric uses the view; a static alternatives table only if the owner asks |
| Evidence table (tiers 1 to 3); DI-3b caps and Jaccard; DI-7, DI-9, DI-12 | Defer | No probe or data yet (d06 Tier B) | SP-1 and SP-2 results |
| Model-written human text; runtime translation; a Persian catalogue; a `jade/text/` package | Do not build | New channel for an agent to steer the human (d04 ADR-09) | Owner says yes and a native reviewer exists after the rendering probe |
| Batched asks; planted fake questions (honeypots); per-person timing dashboards | Do not build now | `askchat.rewrite` conflicts with batching; deception needs the owner's approval (d09 section 12, d08 section 10) | A measured complaint; rubber-stamping shown by V6-23 numbers and owner approval |
| `tree-sitter-bash` differential; more shell-parser waves; waves 4 to 6 of the shell start-up guard | Defer | No stop-loss number yet; W-98 wants one parser pass (section 2, row 10) | V6-22 leaves unresolved disagreements |
| OPA, Cedar, Invariant, AGT or any policy engine in the hot path; a second allow-list engine; native deny rules written by JADE | Do not build | The parser is the hard part; Cedar skips a policy that errors, so a skipped `forbid` is a silent allow (d04 ADR-02) | Several machines need one shared policy; the rule set grows about 10 times |
| A second-harness adapter; an MCP server, proxy or gateway; A2A; a JADE mod; an Elicitation hook; MCP annotations as trust | Do not build | One harness is wired; an Elicitation hook can answer for the user (R4 3.5) | The owner names a second harness with a blocking pre-tool hook |
| OTLP export; `opentelemetry-sdk`; `tool_use_id` column | Defer | Development-stage conventions; no collector; no consumer | A collector exists and a join is named |
| PyPI, Homebrew, container, binary; package rename W-28; `jade quickstart`; provider URL setting | Do not build in v6 | Name risk; W-28 is a safety change (policy path lists); no provenance (d11 sections 14, 17) | Owner picks a name and the release gates pass |
| Platt, isotonic, conformal, importance-weighted estimators; threshold loosening; auto-promotion; embedding memory | Do not build | No labels to fit; probably not reachable within a year at this labelling capacity (d08 section 7) | At least 500 clean labels with a protected split |
| L3 end-to-end container runs, `evals/e2e/`, a scripted approver, B2 auto-mode arm, public-set adapters, `evals/latency/` | Defer | Each needs a named question (d10 4.1) | L2 shows an asks-per-call change the owner wants confirmed end to end |
| Scope widening (P4c) and threshold work (P4d) | Defer | Evidence per scope; does not carry over (d08 section 7) | V6-30 passes stage 2 |
| A recovery engine, retry counter, blocker table, PreCompact or SessionEnd hooks | Do not build | `/goal` and Monitor cover it; no incident (d07 section 13) | One recorded incident the host does not cover |
| Per-project levels | Do not build | More state in the most critical file (d04 ADR-03) | A real project needs a different floor |
| DI-1 evidence record (5 fields) and DI-2 diagnosis text (d06 Tier A, steps 2) | Defer | They matter only when `jev` is on and abstains are common; the minimum v6 runs with `jev` off | SP-3 replay shows the checks would have fired on past asks |
| N21 (push from `make push`, scripts, `tmux`, `at`, `docker exec`) and N25 (`kill -9` of the hook host) | Defer as `known_gap` rows (V6-03) | A text rule per wrapper is whack-a-mole; the sandbox, remote branch protection and the pre-push guard are the boundary (d03 BS-08, BS-09) | The oracle (V6-22) finds a family a rule can close |
| Gating of GUI tools (`computer-use`, MacOS-MCP) and detection of MCP tool-description change (BS-05, BS-06) | Defer; name them accepted residual risk in repo docs/05 | A hook cannot see them or cannot judge them (d03 Part E) | The owner wants GUI tools dropped at high-risk levels |
| Native `defer` for `claude -p`, a hook `if` filter, SKILL.md and AGENTS.md checks, a threshold scan and one-parameter Platt fit (OP-07, OP-08, OP-09, OP-12) | Defer | Not probed, not measured, or no labels (d03 Part F) | The probe in V6-15 shows `defer` works; V6-20 timing shows a hook-cost need; labels exist |

### 6.1 Where each sibling id lands

[RECOMMENDATION] Every problem id of the sibling drafts has a card above or a row in the table above. This is the check I ran against d03, d05, d06 and d11.

| Ids | Land in |
|---|---|
| FN-01 to FN-13 (d03 Part A) | FN-01 V6-04; FN-02 V6-06; FN-03 V6-07; FN-04 V6-10 (text) and V6-16 (behaviour); FN-05 V6-08; FN-06 V6-09; FN-07 V6-05; FN-08 V6-18 (a); FN-09 V6-11; FN-10 V6-11 and V6-12; FN-11 V6-01 and V6-02; FN-12 V6-11; FN-13 V6-28 |
| F1 to F13 (d05 section 7) | F1 V6-06; F2 V6-07; F3 V6-16; F4, F5, F9 V6-08; F6 V6-09; F7, F8, F11, F13 V6-18; F10 V6-05 (deny-wins) and V6-29 (age-out); F12 V6-10, V6-12 |
| HR-01 to HR-12 (d03 Part C; d03 has no HR-08, so HR-08 is unassigned) | HR-01 V6-24; HR-02 and HR-03 V6-23, V6-29; HR-04 V6-16; HR-05 V6-29; HR-06 V6-04 (an independent round, not agent-only) and V6-34; HR-07 V6-22; HR-09 V6-15, V6-26; HR-10 V6-33; HR-11 V6-08; HR-12 V6-26 |
| BS-01 to BS-11 (d03 Part E) | BS-01, BS-02 V6-18 (e); BS-03 section 6; BS-04 V6-18 (b); BS-05, BS-06 section 6 (BS-06 seal key: V6-18 c); BS-07 V6-15; BS-08, BS-09 V6-25; BS-10 V6-09; BS-11 V6-10, V6-12 |
| OP-01 to OP-12 (d03 Part F) | OP-01, OP-03 V6-25; OP-02, OP-04 V6-31; OP-05 step 1 V6-23, step 2 section 6; OP-06 V6-23; OP-10 V6-34; OP-11 section 6; OP-07, OP-08, OP-09, OP-12 section 6 |
| MG-01 to MG-12 (d03 Part G) | V6-20, V6-21, V6-23 |
| DI-1 to DI-12 (d06; d06 has no DI-6, so DI-6 is unassigned) | DI-1, DI-2 section 6; DI-3a, DI-4, DI-5 V6-24; DI-8 V6-23; DI-3b, DI-7, DI-9 to DI-12 section 6 |
| N1 to N38 (d05 section 9; d05 has no N2, and has N6b: 38 labels in all) | N1, N3 to N6b V6-17 (N3 is in the Phase 1 gate list); N7 to N9 V6-06; N10, N11 V6-07; N12 exists; N13, N14 V6-09; N15 to N17 V6-17; N18 to N20, N23 V6-08; N21, N25 section 6; N22, N24 V6-18; N26, N27 V6-18; N28 to N33 V6-28; N34 to N38 only if budgets are built (deferred); N39 to N43 (circumvention of a denied operation, added in d05 section 9.3) V6-17 |
| d11 checklist 1 to 18 | 1, 2, 5 V6-11; 3 V6-10 and V6-23; 4, 6, 15, 18 V6-33; 7 V6-10; 8, 9 V6-12; 10 to 13 V6-34; 14 V6-02 and V6-05; 16 V6-14; 17 V6-23 |

## 7. Phase exit gates, tied to section 10

Gate names (G1 to G8, S1 to S6) and N-test numbers are from d10 sections 10 and 11 and d05 section 9. A deterministic gate must be 100% green. A statistical gate returns pass, fail or not-enough-data, and says what data is missing. "CI" or "owner run" says where it is verified (section 3, rule 3).

| Phase | d10 section 13 phase | Deterministic gate | Statistical gate | Stop state | Claim allowed |
|---|---|---|---|---|---|
| 0 | Not P0. This is the separate 0.7.0 release gate (D-B14) | G1, G2, G5, G7, G8 on approved behaviour, active tests only (CI). N7 to N9 (V6-06), N10, N11 (V6-07), N13, N14 (V6-09), each red on the pre-fix sha. Never-looser check against 0.6.1 empty (owner run). Unique ids (CI). | None | If V6-06, V6-07 or V6-09 is not approved or not merged, do not tag 0.7.0 | "These listed vectors are closed." Not "JADE is safe". |
| 1 | P0, first part | G1, G2, G5, G7, G8 (G3 and G4 move to Phase 2, when V6-20 and V6-22 exist). N1, N3, N5 (if approved), N6, N6b, N18, N19, N20 (V6-08), N24 and N22 (V6-18, if built), N26, N27 (only if V6-18 d is built). The never-looser check stands in for G3 (candidate blocks everything main blocks); N28 to N33 for the `jev` key (V6-28) | None | An N-test with no approved fix is a counted `known_gap`, not a pass | "These listed vectors are closed" at levels `off`, unset, `low`, `medium`, `high`. Level `off` shows "observe plus floor". |
| 2 | P1 (and the rest of P0) | First L2 stratum C (existing 205 block and 122 allow cases, converted): zero should_block calls allowed; candidate blocks everything main blocks (G3); SI-08 oracle mismatch zero or `known_gap` (G4; G4 needs V6-22, which is outside the minimum v6 of section 8, so in the minimum v6 G4 is not claimed and the SI-08 claim rests on the never-looser check and the N-tests). d10's P0 is fully met only when this line passes, with G4 only if V6-22 was built. At least 150 gray-zone calls labelled by both raters, kappa at least 0.70, labels frozen by hash, real rows private and redacted, L2 report reproduces byte for byte, at least 95% of verify calls linked in a scripted run, G6 (CI for the code, owner run for labels) | None required. Optional pilot: B0 and B1, 20 tasks, K=2 (variance, ICC, cost per run) | If the owner cannot label 150 rows: "no accuracy number"; L1 gates stay the only claim | "0.6.1 and baseline numbers, with intervals, on this corpus". Friction numbers from the ledger. |
| 3 | No benchmark phase | G1, G2 still green after each change. `jade check` layer rows (CI). Question-builder tests (CI) | The three-part table (prompts per day, sandbox-blocked failures, escapes found) before and after. Never "fewer prompts" alone | If the sandbox breaks needed tools, turn it off (`sandbox.enabled: false`); no claim | Counts only: "prompts per day went from X to Y over N days; escapes found: Z". |
| 4 | P2 and P4a | The draw is made by the hook and the agent cannot read it. N7 to N9. Signed approver installed for any label above MANUAL. Never-learn test green. The owner has written the friction and autonomy targets from the V6-23 baseline (D-B5, LD-21): this is a dated gate, and V6-29 does not start without it | S2 and the audit: 59 or more audited clean silent runs from 10 or more sessions, 0 wrong (upper bound 4.95%); 149 for a higher-risk scope (1.99%). Seeded-bad-call check only if honeypots are approved; else the false-allow audit is the gate | One audited wrong silent allow in a costlier class, or fewer than 10 sessions after 8 weeks: scope back to asking (d08 section 9 stop rule) | "False-allow rate can be estimated without bias." Then "scope S false-allow rate under 5%", with n and sessions. |
| 5 | P3 and P4b | Report code has no write path to policy. The proxy never sees critical, human-only or fetch-and-exec calls | Reports: at least 80% precision on 20 to 30 sampled rows. Proxy: at least 100 shadow samples over 7 days, at least 30 owner denies from at least 3 rules, 0 false allows (recall at least 90.5%) | If 30 denies never arrive (live: 0 denies in 100 answers), the proxy stays in shadow or is retired (D-5). If they do arrive, that is only the minimum: the owner may then consider letting Jev decide, as a separate decision | "Reports are mostly right"; "shadow agreement and asks per call on this corpus". No "Jev is calibrated". |
| 6 | Not a benchmark phase | G8 secret scan; dry-run tag tests; docs-truth test (CI) | Acceptance trial with one non-maintainer | If the trial fails, fix the front page before any announcement | "Install and first block understood by a person outside the project" on one trial. |
| Deferred | P4c, P4d, P5 (profiles with a stored name) and L3 | See section 6 | See section 6 | n/a | None |

[RECOMMENDATION, d10 section 13] The friction and autonomy targets are set by the owner right after the P1 baseline (V6-23). No autonomy step (V6-29, V6-30) starts before both targets are written, with a date the owner names when answering D-B5 and LD-21. Safety thresholds in this table are numeric. The friction target is not, on purpose, until the baseline exists.

[RECOMMENDATION, d10 section 14] Rules for every claim: no accuracy claim without comparative evidence; no single "JADE accuracy" number; "latency budget met" only with a live window of at least 300 calls over at least 7 days (live p95 is 1,395 ms against 1.0 s today, [VERIFIED A8 section 10]; the owner decides D-B7).

## 8. Minimum viable v6

[RECOMMENDATION] The smallest set that delivers most of the autonomy benefit and keeps the safety invariants: 25 of the 34 tasks. Fourteen are Phase 0 (they are not optional: they are the ground the rest stands on).

| Group | Tasks | What it delivers |
|---|---|---|
| Foundation (all of Phase 0) | V6-01 to V6-14 | No lost work, one id register, PR 47 and the train merged, the three known holes closed, true `jade check` and docs, release 0.7.0 |
| Floor | V6-15, V6-16, V6-17, V6-28, V6-19 | Level `off` stops being a logger; the `jev` off switch and the deny-count gate; this Mac leaves `off`; the floor is a test |
| Measure (trimmed) | V6-20 (replay and report), V6-21 (150 rows, or the stop state), V6-23 (readouts, `sample_prob` and label-type columns only) | Honest counts, and a way to estimate the false-allow rate |
| Contain | V6-25, step 1 (sandbox) | Credential reads and unknown hosts contained by the OS |
| Autonomy | V6-29, V6-30 | Memory hardened, one audited scope |

**Left out of the minimum.** V6-18 (safety backlog, independent fixes, do them when time allows), V6-22 (oracle), V6-24 (decision hygiene: only matters when `jev` is on), V6-26 (human text beyond safe display), V6-27, V6-31, V6-32, V6-33, V6-34, and V6-25 step 2 (managed settings).

**Where the ask cut comes from.** [INFERENCE] Three places and no other. (1) Project scope for R-16 and R-14 in V6-16: the busiest rules. (2) One scoped memory class behind a 59-run audit (V6-30). (3) Containment, not prompts, from the sandbox (V6-25). Asks per day by rule is the first number to improve. It is always read beside the audit false-allow bound, never alone. Asks rise first, when level `off` stops being advisory (section 1, item 2).

**What it does not give.** No Jev decisions (the proxy stays shadow or off). No stored profiles. No end-to-end benchmark. No accuracy claim for Jev. No public release work. No managed-settings install.

**Safety invariants kept.** SI-01, SI-02, SI-05 to SI-13 (SI-03 and SI-04 were never assigned) are untouched or tightened. Human-only classes are never remembered and never sent to Jev. Nothing in the minimum loosens a floor, except the R-16 and R-14 project scope, which needs the owner's yes and a replay showing no should-block call allowed. Every autonomy increase is a human act with a ledger event.

**Tally.** [VERIFIED from the master table] 10 tasks size S, 16 size M, 8 size L. The slow parts are owner hours (V6-21) and waiting for 59 clean reviewed runs (V6-30). The critical path (section 4.1) is the real schedule: merge and fix first, measure while containing, widen last.

## 9. Decisions only the human can make

Each has a recommended default. A safety change needs a yes before it is built. d13 section 2 (the owner batch Q-01 to Q-12) is the authority for the question text. Rows 17 to 20 below were added so that every batch question has a row here: four batch questions (Q-09, Q-12, Q-10, Q-06) gate tasks that rows 1 to 16 did not name.

| # | Decision | Default |
|---|---|---|
| 1 | What "v6" means (A-1) | The 0.6.x line plus main plus the 0.7.0 stack, PR 47 first |
| 2 | 0.7.0 timing and contents (H15, D-B14) | Tag after V6-06, V6-07, V6-09; V6-08 may be 0.7.1 |
| 3 | PR 47 route (D-6) | Merge to main first, ship in 0.7.0, no 0.6.2 |
| 4 | Id plan (D-7, DX-H9) | Note and test now; renumber WT rows from W-117 after PR 47 merges |
| 5 | Chat grant key (D-3, H4) | `plan_hash`; narrow rule only if the replay count is too high |
| 6 | Memory of critical, R-13, R-18, R-12, R-15 (D-2, H3) | Critical, R-13, R-18 never; R-12 and R-15 exact-only (R-23 too, LD-06 only) |
| 7 | Level `off` behaviour (D-1, H2) | Text now; block after the replay count, with R-16 and R-14 scope in the same change |
| 8 | Leave `off` and to which level (H22) | `high`, then only after V6-28 |
| 9 | `jev` key and its default (H21, D-4) | Build in V6-28 with the section 2 row 5 rule; no MAXIMUM, no HARD |
| 10 | Proxy: keep or retire (D-5, H7) | Freeze at shadow; retire if about 97 denies show misses |
| 11 | OS sandbox and managed settings (D-9a, D-9b) | Sandbox pilot after a dry run; managed file later, as its own step |
| 12 | Purge scrubs label text (DX-H2) | Yes, keep hashes and the decision |
| 13 | Labelling: second rater, weekly hours, audit count, honeypots (D-B3, D-B9) | Different reader of the written rubric if no second person; the owner states real weekly hours; no honeypots yet |
| 14 | Known-gap list and who signs (D-B14) | The owner signs; each row has a date and a work item |
| 15 | Public goal (D-8, DX-H1) | Claude Code on macOS, owner first |
| 16 | Which spec items to drop (MAXIMUM, HARD, DEFER_WITH_PLAN, SAFE_ALTERNATIVE) | Drop as states; keep as text or a work item only |
| 17 | May the merge train go onto main (Q-09), before V6-05 | Yes, in order 42, 43, 44, 46, 45; the Jev question v2 of PR 45 stays out of the champion slot until the proxy decision; no tag |
| 18 | May chat and dialog text be made safe to read (Q-12), before V6-09 | Yes. Escape bidi, control and format characters in commands and paths; show the gated part in full when clipped; keep Persian prose (ZWNJ) |
| 19 | May JADE close the known rule holes (Q-10), before V6-08 | Yes, as one batch with one independent review, after a `gate_replay` count of extra asks per rule |
| 20 | Should an agent be unable to set an old session id (Q-06), before V6-18 (a) | Yes. Add `JADE_SESSION_ID` to the SI-11 list |

## 10. Unresolved

- [UNRESOLVED] The owner's original v6 spec text. Every "spec" claim in this roadmap comes through A2, A4 and the task text.
- [UNRESOLVED] Whether `c676801` (PR 47) is weaker than main on shapes outside the four I probed, and whether `967b166` holds anything `c676801` lacks. Refs may have moved again.
- [UNRESOLVED] Probes P1 to P6 and the others in V6-15; they need a new session. Several tasks (V6-18, V6-25, V6-26, V6-31) wait for them.
- [UNRESOLVED] The owner's real weekly hours for labelling, the owner's deny rate, and the real label yield per day. They set the time of V6-21, V6-29, V6-30 and V6-31.
- [UNRESOLVED] Whether auto mode exists through this gateway (P4). If not, the "better than auto mode" claim is not available and the auto-mode arm is dropped.
- [UNRESOLVED] Effort sizes are my estimates. Cost of any run (end to end, live Jev) is unmeasured.
- [UNRESOLVED] The two load counters (7-day and 14-day) disagree; V6-23 reconciles them. Until then no friction claim is made.
- [UNRESOLVED] Whether the repo has a no-new-dependency rule for evals code, and whether `tests/test_ledger_migration.py` opens a previous-release ledger.
- Not done: I ran no test suite, no benchmark, no live Jev call, no ledger read. The only code I ran was the SI-08 probe on archive copies of `c676801` and main (empty HOME, `CI=1`, scratch directory). Everything about real traffic comes from the audits.
