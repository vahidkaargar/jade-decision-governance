# Deferred tasks, unresolved decisions and open questions for JADE v6

This section answers one question: what is still open after the other twelve sections, who must close it, and what waits for what? It merges every unresolved decision, deferred task and open question into one register. It gives the owner one batch of 12 questions. It lists the items that must become work items. Nothing from the sibling sections is dropped: section 9 maps every source id to an item here.

## 0. Reading rules

**Tags.** [VERIFIED] has concrete evidence. Here that means a command I ran in this session, a row I read in `work-items.jsonl`, or an evidence file (A1 to A8, R1 to R6) that a sibling cites. [INFERENCE] is my reasoning. [RECOMMENDATION] is advice. [UNRESOLVED] is not settled. A sibling section is a proposal, not proof. When I only repeat one, I write "per d05" and keep its tag.

**Source ids.** Ids collide across the sections (H1 in d04 and d07, D-n in d03 and d07, SP in d03 and d06, P1 to P6 in d04 and d10). So I write `dNN/ID`: d01 is file `01-inventory-and-capability-matrix.md`, d02 competitive research, d03 gaps, d04 ADRs, d05 security model, d06 decision intelligence, d07 continuous execution, d08 learning, d09 human experience, d10 benchmark, d11 developer experience, d12 roadmap. `V6-nn` is a roadmap task of d12.

**Columns.** In the tables, "Why and evidence" holds the rationale and the evidence together. Evidence ids in brackets are carried from the sibling or the A and R files.

**Item ids.** Q-nn: owner batch (section 2). LD-nn: later human decisions (section 3). U-nn: unresolved facts (section 4). DF-nn: deferred tasks (section 5). X-nn: conflicts between sections (section 6). W-nnn: proposed work items (section 7).

**Priority.** P0: blocks Phase 0 or Phase 1, or hides a safety hole. P1: needed for the minimum v6 of d12. P2: useful, can wait. P3: only when its trigger fires.

**Owner.** human, agent, or both. Anything that needs a new session, sudo, a git write, a live Jev call, or a ledger read is human: SI-10, SI-11 and SI-09 stop an agent from doing it [per d12 rule 2, A3, d10].

**Safety rule.** Q-03 to Q-12 (all but Q-01 and Q-02) change what JADE blocks, asks, stores or shows. They are proposals. Nothing is built until the owner says yes. A yes to a question approves only what that question says.

**Refs checked by me on 2026-11-16 (read-only git).**
- [VERIFIED] `origin/main` is `e9412f3` ("Merge pull request #48 ... sandbox-real-shell-w115"). The installed gate is 0.6.1 (`8100ea1`) [per A3].
- [VERIFIED] PR 47 (`claude/heuristic-hamilton-0eae1d`) head is `c676801` (`git for-each-ref`). d01, d03 and d04 name `c6ab27a` or `967b166`. Those claims are stale for the head. See X-19.
- [VERIFIED, `git for-each-ref`] Branch heads: `design-clear-questions` (PR 42) `8866872`, `open-items-plain-text` (PR 43) `3b5a456`, `open-items-scopes` (PR 44) `ed0d26d`, `open-items-review-output` (PR 45) `8c8163e`, `open-items-shell-startup` (PR 46) `a18bf71`, waves 4 to 6 `7507c10`. The PR numbers come from the task text.
- [VERIFIED] Work items. The dirty file `work-items.jsonl` in the main checkout has 116 rows, highest id W-116. `origin/main:work-items.jsonl` has 96 rows, highest id W-115. Rows W-96 to W-114 and W-116 are only in the dirty file (checked: none of W-96, W-97, W-102, W-105 is on `origin/main`). W-115 exists on both with different titles. No other ref I scanned goes above W-115.
- [VERIFIED] d01, d03, d11 and d12 reserve ids from W-117 for renumbering the dirty rows (`grep` of the drafts).

**Assumptions (reversible).**
- A-1: "v6" means the 0.6.x line, plus main, plus the unmerged 0.7.0 stack (PR 42 to 46, PR 47 first). Q-01 asks the owner.
- A-2: One owner, Claude Code, macOS (d03/D-8 default).
- A-3: Sizes and priorities are my estimates.

**What I did not do.** [VERIFIED] I read A1, the work-items file, d01 to d12 and ran only the git and Python reads above (empty HOME, no state written). I ran no tests, no `jade` command, no live Jev call. This draft is not Jev-verified: `jev verify` writes the live ledger and sends text to a provider, so the orchestrator or the owner should run it (U-25).

## 1. Short answer

1. [RECOMMENDATION] 12 questions go to the owner as the first, blocking batch (section 2). It is not the only owner decision: the later decisions of item 2 come in waves. Q-01, Q-02 and Q-09 unblock Phase 0. Q-03 to Q-08 and Q-10 to Q-12 are safety, privacy or display changes: each needs a yes before any build. Section 8 lists the approval route of every safety-change task of d12.
2. [RECOMMENDATION] 32 later human decisions (LD-01 to LD-32) are not in the batch. Each waits for a replay count, a probe, a build, or data. Each has a default. None is approved.
3. [INFERENCE] One owner session of one to two hours closes the most unknowns: the host probe of V6-15 (U-01 to U-08). It unblocks V6-18, V6-25, V6-26 and V6-31. A second cheap group is five read-only ledger and scratch-copy runs (U-09 to U-13). The owner starts both; an agent may write the scripts.
4. [VERIFIED R6, A3] The most urgent unresolved safety item is not a design question. `curl URL | sh # x` is rated low (R-99) while `curl URL | sh` is high (R-22) (R6 probes 5 to 8, per d02). The fix is part of V6-08 (W-147); Q-10 asks for it.
5. 34 roadmap tasks become 34 proposed work items (W-140 to W-173) plus 17 more (W-174 to W-190). Existing rows are linked first, not duplicated (section 7.2).
6. Counts: 12 questions, 32 later decisions, 26 unresolved facts, 47 deferred items plus a "never" list, 22 conflicts, 51 proposed work items. Section 9 maps all source ids. Section 10 says what a script checked and what I mapped by hand.

## 2. The owner batch: 12 questions

Each question has one decision. All defaults are [RECOMMENDATION]. Every question also accepts "yes, except <part>". A question lists its parts under "A yes approves", so a click never covers more than that. Q-01 to Q-12 are the decisions that either change safety, privacy or what you see at a safety decision (Q-03 to Q-12), or block Phase 0 (Q-01, Q-02, Q-09). The register table after Q-12 gives owner, priority, dependencies, evidence, next action and resume for each.

**Q-01. What does v6 cover, and who is it for?**
- Ask: Is v6 the 0.6.x line plus main plus the 0.7.0 stack, for Claude Code on macOS, with you as the first user?
- Why a human: only the owner has the original spec. The repo has no "v6" label [VERIFIED A1 section 1]. Other readings: `typesafe-ai` plugin 0.5.7 to 0.6.2 (W-12), Jev question registry v2 [A1].
- Options: (A) yes. (B) also a named second harness. (C) a public product is the goal.
- Default: A. Risk: if wrong, the plan builds the wrong thing. B and C add an adapter, install work and privacy duties.
- A yes approves: three parts: the scope (0.6.x plus main plus 0.7.0 stack), the harness (Claude Code only), the user (you first). No code.

**Q-02. How does PR 47 ship?**
- Ask: Merge PR 47 (head `c676801`) into main first, after one clean independent review round, and ship it inside 0.7.0 with no 0.6.2?
- Why a human: release choice; PR 47 changes the SI-08 shell scan. Its older head `c6ab27a` blocks less than main [VERIFIED A1 section 7.2]. At `c676801` the four A1 commands now block [per d12, its own probe; I did not repeat it].
- Options: (A) merge first, ship in 0.7.0. (B) a 0.6.2 hotfix. (C) a narrow patch plus remote branch protection.
- Default: A. At level `off` SI-08 is advisory on this Mac [VERIFIED A3], so a hotfix adds no protection here. Risk: other users who rely on SI-08 wait. Never merge `c6ab27a`.
- A yes approves: the route only. The merge waits for V6-03 and V6-04.

**Q-03. Should memory stop silencing critical calls?**
- Ask: Should a confirmed answer never cover a critical call, nor R-13 or R-18 writes, and should target-selecting env prefixes and the cwd join the memory key?
- Why a human: it changes what you are asked. Code and docs disagree: repo docs/12 section 9 says memory covers them, section 10 says they are asked [VERIFIED A3, A4]. 82 answers wait [per d03/FN-02], idle while the level is `off`.
- Options: (A) fix the code. (B) fix the docs to match the code. Default: A. Risk: more asks; `evals/gate_replay.py` counts them first.
- A yes approves: four parts: critical never remembered; R-13 never; R-18 never; env prefixes and cwd in the key. R-12 and R-15 stay in LD-06.

**Q-04. How is a chat Allow tied to a call?**
- Ask: Should an Allow be valid only for the same `plan_hash` (tool, text, cwd)?
- Why a human: it reverses a pinned test, `tests/acceptance/test_chat_ask_w100b.py:241`. Today an Allow for a staging `kubectl apply` ran the prod one in another cwd [VERIFIED A3 probe C4].
- Options: (A) `plan_hash`. (B) narrow rule: target-selecting prefixes (KUBECONFIG, AWS_PROFILE, `--context`) and cwd only. (C) keep. Default: A; fall back to B if `gate_replay` shows too many extra asks. Risk: `FOO=1 git reset --hard` asks again.
- A yes approves: A, with B as the fallback, and the change to the pinned test.

**Q-05. What does level `off` mean?**
- Ask: Should level `off` stay advisory for now, with only a true text in `jade check`, and decide blocking after a replay count?
- Why a human: it is the safety floor. At `off` only SI-09 to SI-12 block; SI-07, SI-08, R-13 and R-16 do not, yet `jade check` prints "ok" [VERIFIED A3, A4; per d03/CT-01].
- Options: (A) text now, decide blocking after the count (default). (B) block SI-07, SI-08 and wiring writes at `off` now. (C) no change. Risk with A: the floor stays soft longer. Risk with B: more asks, mostly R-16.
- A yes approves: the text change (V6-10) only. The block, and the R-16 and R-14 project scope (a narrowing is a loosening), are LD-29.

**Q-06. Should an agent be unable to set an old session id?**
- Ask: Add `JADE_SESSION_ID` to the SI-11 variable list?
- Why a human: it changes a safety invariant list. The variable is not on the list [per d03/FN-08, which read `policies/safety.yaml`; A5]. An agent could claim an old id [INFERENCE A5; not run].
- What is new: only the SI-11 line. The code already refuses an `outcome` or `reconcile` from another session, and only a human at a terminal may `cancel` a hold [per d07 7.3, A5 section 4]. New hold-message words wait for the `--resume` probe (LD-23, U-07).
- Options: (A) yes (default). (B) keep. Risk: this tightens only; after a crash an honest agent waits for you.
- A yes approves: the SI-11 line and its test.

**Q-07. Build the `jev on|off` key?**
- Ask: Build a `jev` key before you leave `off`?
- Why a human: it changes when your text goes to a provider. At `low` and `medium` 67 distinct answers already pass the 50-count gate, and at `high` text goes out in shadow [VERIFIED A6; repo docs/12 line 177 per d12 section 2 row 5].
- Options: (A) build as written (default). (B) no key; retire the proxy later. (C) key defaults on.
- A yes approves: four parts: an absent key means on at `low` and `medium` and off elsewhere; new installs write `off`; an invalid value reads `off`; the last ledger event wins. Risk: the shadow call at `high` stops; the default is a safety choice. Build is V6-28, before V6-19.

**Q-08. When may Jev decide for you?**
- Ask: Replace the "50 distinct answers" gate with a deny gate: at least 100 shadow samples over 7 days and at least 30 owner denies from 3 or more rules, with zero false allows?
- Why a human: the live data is 100 answers, all allow, zero deny. "Always allow" would agree 100% [VERIFIED A6]. Only you can set how far Jev may stand in your place.
- Options: (A) deny gate (default). (B) keep the count. (C) retire the proxy (LD-08). Risk with A: Jev stays in shadow a long time. At a 7% deny rate, 30 denies is about 430 answered prompts [INFERENCE d08; 7% is Anthropic's rate, not yours].
- A yes approves: the gate rule only. Build is V6-28.

**Q-09. May the merge train go onto main?**
- Ask: After PR 47, may PR 42, 43, 44, 46 and then 45 be merged onto main, with the Jev question v2 of PR 45 kept out of the champion slot until the proxy decision?
- Why a human: the train adds scoped memory reach to main before the memory fix (V6-06) lands [per d12 V6-05 risk (d)]. PR 45's v2 is not calibrated (W-102) [VERIFIED row, my read]. The plain line "Merge pull request 940." hides `--admin` and `--delete-branch` (FN-07) and is a merge gate for PR 42 and 43.
- Options: (A) as asked (default). (B) merge PR 42 to 44 only and hold 45 and 46. Note: deny-wins is in PR 44, so B still brings it; `memory.honoured` and the honour test are in PR 45, so B holds those back. (C) hold the whole train until V6-06 is ready. Risk: no tag between merges; a tag before V6-06 would ship wider memory.
- A yes approves: three parts: the merge order; v2 kept out of the champion slot; the merge-line test that shows `--admin` and `--delete-branch` (d09 proposal 2, tier 1) as a merge gate. It does not approve a tag (LD-01).

**Q-10. May JADE close the known rule holes?**
- Ask: After a `gate_replay` count, may JADE rate these shapes like their siblings: a trailing comment after `| sh` keeps R-22; `gh api .../merge` and graphql `mergePullRequest` like R-10; `git send-pack`, `git -C 'a\ b' push` and `g=git; $g push` under SI-08; writes to `authorized_keys`, `CLAUDE.md`, `.mcp.json`, `.vscode/settings.json`, `.codex/config.toml` like R-13? (Shell start-up files such as `~/.zshrc` ship with PR 46, `HO-shell-startup`, not here.)
- Why a human: each rule adds asks. Today all are R-99 low and run silently [VERIFIED A3 section 8b; R6 section 1.5; my reading of V6-08].
- Options: (A) all, one batch, one independent review (default). (B) only the comment fix and the SI-08 shapes. (C) none. Risk: more asks per day; each rule has a harmless twin test.
- A yes approves: the direction for the V6-08 batch. Each rule's ask count is shown before it ships.

**Q-11. May purge scrub stored command text?**
- Ask: May the purge job scrub command text in `asks.question_json`, `asks.label` and `human_answers.label`, and keep hashes and the confirmed answer?
- Why a human: a privacy change, not reversible by design. Today the retention promise is broken for these columns [VERIFIED A8 section 9; d03/FN-12].
- Options: (A) scrub text, keep hashes and decision (default). (B) scrub nothing and change the README. (C) scrub all, including answers. Risk with A: you cannot re-read old labels.
- A yes approves: the purge part of V6-11.

**Q-12. May chat and dialog text be made safe to read?**
- Ask: May the chat, dialog, permit screen, `jade review` and `jade knows` escape bidi, control and format characters in commands, paths and content, and, when a command is clipped, show the gated part in full or say "open the terminal"?
- Why a human: it changes what you see at a safety decision. Today bidi characters pass and the middle of a long command is hidden before Allow [VERIFIED R6 probes 2 and 9 on `open-items-scopes`; code on `e9412f3` per d03/FN-06].
- Options: (A) yes (default). (B) escape only. (C) no change. Risk: longer questions.
- A yes approves: V6-09 as written, and d09 proposal 1. Persian ZWNJ is kept in prose and is escaped in commands (d09 proposal 10 stays No). A cap on the full segment waits for the command-length read (U-11, d09 proposal 8). Removing the chat Allow for clipped commands stays No (d09 proposal 9).

**Register for Q-01 to Q-12.**

| ID | Owner | Pri | Depends | Evidence | Next action | Resume or unblocks |
|---|---|---|---|---|---|---|
| Q-01 | human | P0 | none | A1; d03/A-2 | Answer; paste the spec if A is wrong | Unblocks all |
| Q-02 | human | P0 | none | A1 7.2; d12 section 0 | Answer; then V6-03, V6-04 | V6-04 |
| Q-03 | human | P0 | none | A3, A4; W-116 | Answer; scratch-copy tests (W-183) | V6-06 |
| Q-04 | human | P0 | none | A3 probe C4 | Answer; replay count | V6-07 |
| Q-05 | human | P0 | none | A3, A4 | Answer | V6-10 text; LD-29 |
| Q-06 | human | P1 | none | A5; d07 7.3 | Answer | V6-18 (a) |
| Q-07 | human | P1 | none | A6; repo docs/12 | Answer | V6-28 |
| Q-08 | human | P1 | Q-07 | A6; d08 6.6 | Answer | V6-28, V6-31 |
| Q-09 | human | P0 | Q-02 | A1; d12 V6-05 | Answer | V6-05 |
| Q-10 | human | P0 | none | A3 8b; R6 1.5 | Answer; replay count | V6-08 |
| Q-11 | human | P0 | none | A8 section 9 | Answer | V6-11 |
| Q-12 | human | P0 | none | R6; d09 | Answer | V6-09 |

## 3. Later human decisions (not in the batch)

These need data, a probe or a build first. Each has a default. None is approved. The owner for every row is the human; the agent prepares the evidence. "Resume" says when to ask. The batch of section 2 is the first, blocking batch only. The decisions below come in later waves, each when its "Resume" point is reached. Several change safety behaviour, and some sit inside the minimum v6 of d12: LD-07 (leave `off`), LD-09 and LD-24 (autonomy numbers and learning rules, tasks V6-29 and V6-30), LD-12 and LD-27 (managed settings, sandbox), LD-22 (R-21 out of Jev), LD-26 (agent launch class), LD-29 (block at `off` with the R-16 and R-14 scope, which is a loosening), LD-30, LD-31 and LD-32. Each of these needs its own owner yes before its task is built. A yes to one row approves only that row. Section 8 lists which task waits for which row.

| ID | Decision and default | Pri | Depends | Evidence | Next action and resume |
|---|---|---|---|---|---|
| LD-01 | Tag 0.7.0 only after V6-06, V6-07, V6-09 merge; V6-08 may be 0.7.1; one train | P0 | Q-03, Q-04, Q-12 | d04/H15, d10/D-B14, d12 section 2 | Resume when those merge |
| LD-02 | Ids: note and duplicate test now; renumber dirty rows from W-117 after PR 47 merges; `T30` stays with the harness row on main (PR 48 is merged); PR 42 already uses `T31`, PR 46 uses `T32`, and the stale PR 43 and PR 45 rebase and renumber their old `T30` [VERIFIED d01 B7; corrected by the master plan check, 2026-11-16]. Agent may start the note | P0 | V6-04 | A1 section 5; my read of `work-items.jsonl` | Note now; renumber after PR 47 |
| LD-03 | Commit loose work on its own branches with explicit paths: any PR 47 fix still uncommitted after `c676801` (check U-19), four untracked plan files (spec B), dirty backlog, `docs-approvals-wording`. Default: yes | P0 | none | A1 section 6 | Now |
| LD-04 | Pointer only. The merge order is decided in Q-09 (W-97 order: 42, 43, 44, 46, 45). d01 E4 says another order (X-16) | P1 | Q-09 | A1 4c; W-97 | Resume when Q-09 is answered |
| LD-05 | Keep branch `open-items-shell-startup-waves4-6` (on origin and as a local branch, both at `7507c10`); redesign later (W-98). Never delete | P2 | none | A1 section 9; W-98 | Resume when V6-22 shows a parser gap |
| LD-06 | Memory for R-12, R-15 and R-23: exact-only until audited (R-23 added: A6 recommends exact-only or never for R-15 and R-23 [VERIFIED A6 section 10 item 5]; Q-03 names R-12 and R-15 only) | P1 | Q-03 | d05 10.1-6; d08 row 10; A6 | After the Q-03 build |
| LD-07 | Leave `off` for `high` after V6-28 and V6-17; warn only (not refuse) for an existing `low` or `medium` while F1 to F6 are open | P1 | V6-28, V6-17, Q-07 | d04/H22; d05 10.1-8 | Resume when V6-28 is done |
| LD-08 | Jev proxy: freeze at shadow; retire if about 97 denies show misses | P2 | Q-08 | d03/D-5; A6 | Resume at 30, then 100 denies logged |
| LD-09 | Autonomy numbers: first scope needs 59 or more audited clean silent runs from 10 or more sessions (upper bound under 5%); 149 for a riskier scope; no 1% claim before 299; stop rule of d08 section 9; random audit of silent allows (it adds owner-facing questions) | P2 | V6-29 | d08 section 7; d10/D-B4; d02 3.6; R5 script | Resume when V6-29 is ready |
| LD-10 | Your weekly labelling hours, a second rater (or a different reader of the rubric), audit count. Default: use 10 a week (a code constant, not measured) | P1 | U-12 | A6 4.1; `jade/evals.py:21` per d12 | Resume at V6-21 start |
| LD-11 | Honeypots: no; passive attention numbers only | P3 | V6-23 | d08 3.3; d10/D-B9 | Resume if rubber-stamping shows |
| LD-12 | Managed settings with sudo (no `allowManagedHooksOnly`): yes, own step, after a dry run | P2 | U-01, U-06 | R1 3.4; d04/ADR-04 | Resume when those probes are done |
| LD-13 | Unknown commands: keep R-99 low | P1 | U-09 | d02 1.1; d04/H13 | Resume when U-09 gives the R-99-only share |
| LD-14 | Language: simple English only, static tables; Persian only after a native reviewer and the render probe | P2 | U-08 | d04/ADR-09; d09 section 7 | Resume when U-08 is done and a reviewer is named |
| LD-15 | Human text beyond Q-12: plain lines list every widening flag; permit screen two-code fix; refusal `n` is not a Deny; no-answer line from channel constants; facts only; Deny-first order is a probe question; full-segment cap after U-11 | P2 | Q-12, U-08, U-11 | d09 section 11 | Tier 1 after Q-12; rest on data |
| LD-16 | Batched asks: one decision per ask. Fix B (words) now, Fix C (fail closed) next release; the flow skill is outside the repo (X-15) | P1 | none | d07 4.1; d09 3.1 | Resume at V6-26 |
| LD-17 | Install and check text: print effective gate mode on `hooks install`; no default change; print the SI-09 to SI-12 notice at setup | P1 | V6-10 | d11 sections 2, 6 | With V6-10 |
| LD-18 | PyPI (and whether the PyPI name is free: d11 section 19, unchecked), rename W-28, `jade upgrade` signature check, provider URL key: none in v6; state the trust root | P3 | Q-01 C | d11 section 14; d04/ADR-11 | Resume if a public goal is set |
| LD-19 | Provider data-terms wording (you write it); acceptance-trial person (a developer outside the project) | P2 | V6-33 | d11 section 13 | Resume at V6-33 |
| LD-20 | Jev latency budget (keep 1.0 s) and a timeout fallback to a human ask | P2 | U-14 | A8 section 10; d10 12 | Resume after 300 or more live calls |
| LD-21 | Benchmark choices: baseline arms, 0.7.0 SHA, margins, where it runs, a macOS CI job for the real-shell oracle (no; d10 D-B8; the macOS CI job for setup and hook payloads is a different job, task V6-34), public data, scripted approver (no), warm snapshot, minimum benchmark, signed known-gap list, and the friction and autonomy targets (set right after the V6-23 baseline; a gate before V6-29, d12 section 7) | P2 | V6-20 | d10 section 17 | Resume at V6-20 start; targets before V6-29 |
| LD-22 | Jev tightening (R-21 part is built in V6-28): `--no-cache` for humans and tests only; only `jade ask` sends `jev_result_*`; R-21 out of Jev at every level; no "verified" word without an independent check; stop "done" on a cap hit at medium and above; claim risk when `--decision` is missing is the highest open one | P1 | Q-07, V6-28 | d04/H16, H17, H20; d06 section 13 | Ask before V6-28 (the R-21 build); the other Jev rules with V6-24 |
| LD-23 | Recovery: mark dead states "reserved"; re-ask failed irreversible commands only if replay shows repeats; keep the Stop early return until two tests exist; ask-pause number after measure; flow skill marks denied todos blocked; irreversible calls out of the proxy after a count; hold-message words after the resume probe; a human-only cross-session `reconcile` command (default: do not build, d07 7.3 step 3; d04 ADR-07 item 4 wants it: X-22); the text rule "do not split or reword a denied operation" in the skill and flow text (staged for you, d14 R23) | P2 | U-07 | d07 section 13 | Resume at V6-27 and U-07 |
| LD-24 | Learning rules: never-learn list; replay, protected and canary rules unchanged; two-input model; scope aging; labels need hook or CI evidence; templates human-promoted; limit on `jade review confirm --all`; no auto-promotion | P2 | V6-17, V6-29 | d08 section 11; d04/H14 | Resume at V6-17 |
| LD-25 | OTLP backend: none; `--json` and ids only | P3 | none | d04/H11; R4 | Resume if a collector exists |
| LD-26 | Make launch of another agent, and hook-skipping flags, a human-only class (never remembered, never sent to Jev) | P1 | U-01 | d04/H19; A3 8e | Resume when U-01 is answered |
| LD-27 | OS sandbox pilot for one week after the probe session and a throwaway-HOME dry run. Default: yes. Rollback `sandbox.enabled: false` | P2 | U-03 | d04/H6; R1 3.5 | Resume after the probe session |
| LD-28 | Spec names: MAXIMUM, HARD, DEFER_WITH_PLAN, SAFE_ALTERNATIVE stay out as states; MEDIUM-HIGH is a label for `medium` plus `jev off`; scale read as strictness. You may instead tell me what HARD and MEDIUM-HIGH mean | P1 | Q-07 | d03 SP-03, SP-04; d05 3.5 | Resume at V6-28 docs |
| LD-29 | V6-16 build: block SI-07, SI-08 and wiring writes at `off`, with R-16 and R-14 project scope in the same change. Narrowing a rule is a loosening: the replay must show no should-block call allowed | P1 | Q-05, U-09 | d12 V6-16; d03 HR-04 | Resume when the replay count exists |
| LD-30 | V6-18 (c) MCP file-reader path check and (d) fail closed on malformed events: both add blocks | P2 | U-05 | d12 V6-18; A3 8a, 8g | Resume after V6-15 |
| LD-31 | Decision hygiene, one check at a time, each stricter or record-only: DI-8 label filter on `goal_check` (built in V6-23, inside the minimum v6, so ask before V6-23); DI-3a, DI-4, DI-5 (V6-24, outside the minimum). Head-plus-tail truncation is not built in v6: the V6-24 card excludes it | P2 | V6-23, Q-07 | d06 section 13; d12 V6-23, V6-24 | DI-8 before V6-23; the rest when `jev` is on |
| LD-32 | Promotion gates: set a minimum canary sample size and use intervals in the protected stage (changes safety behaviour; today three gates are statistically weak) | P2 | V6-23 | d02 4.3; d03 MG-05, MG-06; R5 | Resume when labels exist |

## 4. Unresolved facts

A fact is settled by a probe, a read, a live call, a CI read, or the owner. The last column says which. Evidence ids are carried from the sections.

| ID | Title | Owner | Pri | Depends | Why and evidence | Next action | Settled by |
|---|---|---|---|---|---|---|---|
| U-01 | Do `claude --bare` and `--safe-mode` skip user or managed hooks (P1) | human | P0 | new session | A child `claude -p --bare` may run with no hook; launch is R-99 low [VERIFIED A3 probe_child; R1 section 3.4 unresolved] | Run V6-15 in a throwaway HOME | Probe table in `evals/probes/` |
| U-02 | Is the mods guard `cc-plugin-sec-default` loaded here (P2) | human | P1 | new session | Without it a mod can approve past a PreToolUse block [R1 section 3.9, raw text] | Open `/plugin` once; probe cell 13 | Probe |
| U-03 | Sandbox facts: on here; git over SSH works (P3); applies under `bypassPermissions`; invalid managed settings; runtime repo name | human | P1 | new session | d04/ADR-04, R1 section 3.5 | V6-15 plus the LD-27 dry run | Probe |
| U-04 | Is auto mode available through this gateway (P4); which model; which host mode the 3,416 calls used; can it run on recorded calls | human | P1 | new session | No offline replay exists [per d02]. d12 section 10 | V6-15; add host mode count to `jade check` if the ledger keeps it | Probe; X-18 |
| U-05 | Does a hook `ask`, a settings `ask` rule, `PermissionRequest` or `defer` reach you in auto, bypass and `-p` (P5); what `askUserQuestionTimeout` returns | human | P1 | new session | Probe on 2.1.314: hook `ask` did not reach you [VERIFIED R1, RESULTS.md cells 1, 2, 11]; others "Not run" | V6-15 in `evals/probes/hook_ask/` style | Probe |
| U-06 | Does a hook in both managed and user files run twice (P6) | human | P2 | LD-12 | Double ledger rows and asks [d04/ADR-04] | V6-15 | Probe |
| U-07 | Recovery group: `--resume` session id; subagent session id; Esc event; `SessionStart source=compact`; new permit per retry; task list across sessions; `/goal` restore; cloud routines run hooks | human | P1 | new session | d07 sections 7.3, 8, 13; d04/ADR-07 | V6-15 extra rows | Probe |
| U-08 | How chat, dialog and notification draw bidi, Persian, Markdown, option descriptions, preselected option; `updatedInput` with fewer questions; PreToolUse language field; confusable letters | human | P1 | new session | d09 section 13; R6 | One-hour render probe | Screenshots plus table |
| U-09 | Share of ledger calls that match only R-99; the rate of silent allows (`memory_allowed` events and Jev accepts, d08 lines 117 and 266); the use rate per memory scope (needed before any audit count or the 6-week estimate of V6-30); JADE asks per 100 gated calls by rule; time to answer; expired asks (spike 3) | human runs, agent writes | P0 | none | Decides LD-13 and V6-16 scope [d02 4.2 spike 3] | Read-only ledger query script | Two numbers |
| U-10 | Reconcile 3,424 gated calls in 7 days (`jade check`) with 2,666 in 14 days (`ledger stats`) | human runs | P1 | none | The 7-day count is larger [VERIFIED A3, A8 per d04] | Read counters' code, then query | Reconciled figure (V6-23) |
| U-11 | What the ledger stores per call, for how long; command lengths; cwd on `decisions` and `permits`; rows in dead states; host mode column; which correctness signals exist | human runs | P1 | none | d10 section 6.3; d07 6.3; d11 section 19 | Read-only query | Schema and counts |
| U-12 | Your deny rate, approve rate, weekly label hours, label yield per day; the 18 non-hint answers; the stray MODEL_DRIFT row | human | P1 | U-11 | 100 answers all allow [VERIFIED A6]; 7% is Anthropic's rate [R5] | Read; reads are blocked for agents by SI-10 | Owner numbers |
| U-13 | Scratch-copy tests: SI-07 session-id attack; memory re-run after nonzero exit; confirm-then-silent critical end to end; `sudo git push` at SI-08; chat grant C5 and C6 with the first call closed; batched-popup effect | agent writes, human runs | P0 | none | They write state; SI-11 blocks an agent run [d03/FN-08, d05 10.2, d07 section 13] | Write tests on a `git archive` copy | Test results |
| U-14 | Live Jev facts: raw body; noise on a trivial change; provider caching; spend of 369 calls; live latency gap (p95 1,395 ms vs 1.0 s); accuracy on your calls; `repeat_share` 0.081 vs about 20% | human OK needed | P2 | none | d06 SP-0, SP-1; no live call was allowed [d01] | Owner allows 20 plus 20 calls; raw body saved | Numbers; W-177 |
| U-15 | CI and tests: CI on `origin/main` `e9412f3` (d14 PC-2, not read); 0.6.1 tag CI; local tests on re-merged tips and `967b166`; W-95 flake history; lifecycle crash tests at main; PR 48 real-shell on a hosted macOS runner; `test_ledger_migration.py` opens an old ledger | agent (read CI) | P1 | none | GitHub showed SUCCESS only for pushed heads [d01]; about 1,091 tests skip locally [d12 rule 3] | `gh run list` read, then `docker run python:3.13-slim` repro | CI read |
| U-16 | Remote branch protection, pre-push guard in repos that matter, private vulnerability reporting, maintain job loaded | human | P0 | none | W-68 and W-69 are marked done in the dirty file [VERIFIED by my read]; A3 and A8 found the real state unknown; `jade check` FAIL on maintain job. Repo visibility is settled: PUBLIC, MIT [VERIFIED `gh repo view`, d11 A-3, and again by the master plan check] | Owner checks; V6-10 adds rows | Owner answer |
| U-17 | Does the owner use Codex or Cursor with hooks; could the agent run in a container; is there a collector | human | P2 | Q-01 | `~/AGENTS.md` names Codex and Cursor; it does not show hooks [d04/ADR-05] | Answer in Q-01 follow-up | Owner answer |
| U-18 | The original v6 spec text; other readings of "v6" | human | P0 | none | d03/A-2; A1 | Q-01; paste the spec | Owner |
| U-19 | PR 47 at `c676801`: weaker than main on other shapes (DIFF-W1, W-114)? What does `967b166` hold that `c676801` lacks? Did refs move again? | agent read-only | P0 | none | d12 section 0: four commands block at `c676801`; `967b166` is not an ancestor [per d12 `merge-base`] | `never_looser.py` (V6-03); read `git show` of `967b166` | V6-04 |
| U-20 | Code reads: cause of the comment-after-`sh` gap; do R-13 and R-18 shell writes reach memory eligibility; `check_drift` fixture writes; can `improve.shadow` score a v2 template; does `shellnorm.skeleton` expose redirect targets; does `policy.assess` touch the ledger; PostToolUse output shape; hook hashing cost; every event sets `actor`; why-you field on the ask row; chat and dialog `human_accept` gap | agent | P1 | none | d03/FN-05, d06 sections 2.3, 9; d08 5.3 | Read code, write a test per item | Tests |
| U-21 | Do the unmerged `open-items-*` branches change A2 and A4; overlap of the proxy, memory and decision sections | agent | P2 | none | A2 and A4 did not read them [d04 section 5] | Read branch diffs | Read |
| U-22 | `tree-sitter-bash` coverage vs `shellnorm`; no-new-dependency rule for evals code; eval row totals (A6 170 and 241, docs 191) | agent | P2 | none | d02 spike 7; d10 section 15; d10 section 18 | Count rows at `e9412f3`; read repo rules | Count |
| U-23 | Research not read: dataset and tool licences (AgentHarm clause, R-Judge, Terminal-Bench 2.0 dataset; d10 section 18 and D-B10), Gemini policy, Cursor approvals, Cline hooks, Codex managed config, Inngest and MAF human-in-the-loop, Restate licences, OpenHands `enterprise/`, PromptGuard weights, container isolation, AGT identity, `agent-scan`, hooks page past 100,000 characters, native parser exposure, MCP annotation fields | agent | P3 | trigger | d02 section 4.7 | None now | A named need |
| U-24 | Weak evidence: summariser-read pages; R5 abstracts; snippet-only claims (Snyk, Cursor CVEs, Copilot severity); advisory counts (HTTP 403); A2A date and licence; OPA latency; 93% and 84% figures differ in confidence between R1, R5, R6 | agent | P3 | none | d02 sections 0, 1.2, 4.7 | Keep the secondary tag; never quote as verified | Primary read |
| U-25 | Nothing in d01 to d13 is Jev-verified; sizes are estimates; no cost per run | human or orchestrator | P1 | none | `jev verify` writes the live ledger and sends text out | Run `jev verify` with raw evidence | Exit code 0 |
| U-26 | `jade setup` runs all seven steps without a key; Linux install script never run; live hook latency on the current release; full-history secret scan result | human | P2 | none | d11 section 19; A8 | Owner steps in V6-12 and V6-33 | Run |

## 5. Deferred tasks

Backbone: d12 section 6, plus items from d01 Part D, d02 4.3, d03 Part H, d05 10.3 and A1. Only items with a trigger are deferred. "Watch" means no action until the trigger.

| ID | Item | Owner | Pri | Depends | Why and evidence | Next action | Resume when |
|---|---|---|---|---|---|---|---|
| DF-01 | JADE-owned runtime, scheduler, workflow engine, resident agent | agent | P3 | none | Engines need the agent loop inside them; `/goal`, `/loop`, Monitor, cron exist [d04/ADR-01, R2] | Watch | A host with no resume, and cloud routines do not run hooks |
| DF-02 | Stored profiles; MAXIMUM; HARD; `approvals` key; Jev bar override; per-rule autonomy | human | P3 | LD-28 | No safe meaning; labels are derived [d05 3.5] | Watch | Owner defines the difference between two names, with evidence |
| DF-03 | Scoped autonomy budgets | agent | P3 | LD-09 | No measured need [d05 6.1] | Watch | Asks per week still too high with sandbox, scopes and memory on |
| DF-04 | `DEFER_WITH_PLAN` and `SAFE_ALTERNATIVE` as verdicts; typed `Verdict` class; wiring `WAITING_DEPENDENCY` and `RETRY_SCHEDULED` | agent | P3 | none | Nothing consumes them [d04/ADR-06, d07 section 11] | Mark "reserved, not entered" (V6-11) | A metric uses the view |
| DF-05 | Evidence table, provenance tiers, DI-1, DI-2, DI-3b caps and Jaccard, DI-7, DI-9 to DI-12 | agent | P3 | U-14 | No probe or data [d06 Tier B; d12 section 6] | Watch | SP-1, SP-2, SP-3 results |
| DF-06 | Model-written human text, runtime translation, Persian catalogue, `jade/text/` package | agent | P3 | LD-14 | A new channel to steer the human [d04/ADR-09] | Watch | Owner yes, native reviewer, render probe |
| DF-07 | Batched asks; honeypots; per-person timing dashboards | agent | P3 | LD-11, LD-16 | `askchat.rewrite` conflicts; deception needs approval [d09 section 12] | Watch | Measured complaint; rubber-stamping shown |
| DF-08 | More parser waves; waves 4 to 6 of the shell start-up guard | agent | P3 | V6-22 | HR-07: four review rounds, still whack-a-mole [d03] | Watch | Oracle shows a family a rule can close |
| DF-09 | `tree-sitter-bash` differential test (dev only) | agent | P3 | U-22 | Gives a stop-loss number [d03/OP-11, d02 spike 7] | Watch | A bypass the text scan cannot close |
| DF-10 | OPA, Cedar, Invariant, AGT or another policy engine; second allow-list; native deny rules from JADE | agent | P3 | none | Cedar skips an erroring policy; the parser is the hard part [d04/ADR-02] | Watch | Many machines need one policy; rules grow about 10 times |
| DF-11 | Second-harness adapter, MCP server or proxy, A2A, JADE mod, Elicitation hook, MCP annotations as trust | agent | P3 | Q-01, U-17 | Only Claude Code is wired; an Elicitation hook can answer for you [R4 3.5] | Watch | You name a second harness with a blocking pre-tool hook |
| DF-12 | OTLP export, `opentelemetry-sdk`, `tool_use_id` column | agent | P3 | LD-25 | No collector; no consumer; OTel GenAI conventions are in Development [R4] | Watch | A collector exists and a join is named |
| DF-13 | PyPI, Homebrew, container; rename W-28; `jade quickstart`; provider URL key; signed tags and provenance | human | P3 | LD-18 | W-28 is a safety change (path lists) [d11 section 14] | Watch | Public goal and release gates pass |
| DF-14 | Platt, isotonic, conformal, off-policy estimators, threshold loosening, auto-promotion, embedding memory | agent | P3 | labels | No labels to fit [d08 section 7] | Watch | 500 or more clean labels with a protected split |
| DF-15 | L3 end-to-end runs, `evals/e2e/`, scripted approver, auto-mode arm B2, public-set adapters, `evals/latency/` | agent | P3 | LD-21 | Each needs a named question [d10 4.1] | Watch | L2 shows an asks change worth confirming |
| DF-16 | Scope widening (P4c) and threshold work (P4d) | agent | P3 | V6-30 | Evidence does not carry across scopes [d08] | Watch | V6-30 passes stage 2 |
| DF-17 | Recovery engine, retry counter, blocker table, PreCompact and SessionEnd hooks | agent | P3 | U-07 | `/goal` and Monitor cover it; no incident [d07 section 13] | Watch | One recorded incident the host does not cover |
| DF-18 | Per-project levels | agent | P3 | none | More state in `hooks.py` [d04/ADR-03] | Watch | A real project needs a different floor |
| DF-19 | Known-gap rows N21 (push via `make`, scripts, `tmux`, `at`, `docker exec`) and N25 (`kill -9` of the hook host) | human signs | P2 | V6-03 | Text rules are whack-a-mole [d03 BS-08, BS-09] | One work item each (W-179, W-180) | Oracle finds a closable family |
| DF-20 | Gate GUI tools; detect MCP tool-description change | human | P3 | none | A hook cannot see or judge them [d03 BS-05, BS-06] | Name them accepted residual risk in repo docs/05 (W-181) | You want GUI tools dropped at high levels |
| DF-21 | Native `defer` for `claude -p`; hook `if` filter; SKILL.md and AGENTS.md checks; threshold scan | agent | P3 | U-05 | Not probed or measured [d03 OP-07 to OP-12] | Watch | Probe shows `defer` works; timing shows a need |
| DF-22 | Evidence for ask-on-unknown (R-99 default change) | human | P2 | U-09 | Decides friction cost [d02 1.1] | After U-09 | LD-13 |
| DF-23 | Widen Jev-decides; Jev bar override | human | P3 | LD-08 | 0 allowed decisions; no calibration [A2, A6] | Watch | LD-09 gate passes |
| DF-24 | Auto-opened work items for stale permits or blockers | agent | P3 | V6-27 | d07 6.2 says do not use `work.add` for blockers | Two SessionStart lines only | Ledger shows asks stall |
| DF-25 | Injection scan of tool results (LlamaFirewall, `agent-scan`) | agent | P3 | U-23 | Missing everywhere; needs torch [d02 3.2] | Watch | A tiny dependency-free scan or an incident |
| DF-26 | Agent in a container or VM | human | P3 | U-17 | Not researched [d02 3.5] | Ask in Q-01 follow-up | You say it is possible |
| DF-27 | Spec B "fewer blocks and scripts" (approved 2026-11-14; start after 0.7.0) | human | P2 | V6-14 | Untracked plan and spec in a worktree [VERIFIED A1 section 6] | Commit files (LD-03) | 0.7.0 merged |
| DF-28 | Hardware-key approval (W-91); seal key out of reach (W-41, W-92) | agent | P3 | none | Existing open rows [VERIFIED work-items.jsonl] | Watch | A tty backend becomes the default |
| DF-29 | Task-quality metric and a control group | human | P2 | LD-21 | No metric; Terminal-Bench 2.0 sees only 9 to 13 point changes [d03/MG-08, R5] | You pick a measure | LD-21 |
| DF-30 | JADE as a Claude Code plugin | agent | P3 | none | Not researched [d04/ADR-05] | Watch | A distribution need |
| DF-31 | Intent binding, delegation to subagents, pre-trust execution defence | agent | P3 | none | Missing everywhere; only the vendor can fix the last [d02 4.5] | Watch | A vendor feature |
| DF-32 | A coverage gate, type-check job, protected release environment, plugin API, daemon, dashboard, TUI | agent | P3 | none | No failure or second maintainer behind them [d11 section 17] | Watch | A second maintainer or a repeated user request |
| DF-33 | Roadmap tasks left out of the minimum: V6-18 | both | P1 | U-01, U-05 | Independent safety fixes [d12 section 8] | Do when time allows | After probes |
| DF-34 | V6-22 real-shell oracle snapshot | both | P2 | V6-04 | Gives the parser stop-loss number [d12] | Owner runs before a parser change | A parser change |
| DF-35 | V6-24 decision hygiene (Tier A: DI-3a, DI-4, DI-5; DI-8 is in V6-23) | both | P2 | V6-23 | Matters only when `jev` is on [d12] | After `jev` on | LD-22 answered |
| DF-36 | V6-26 human text beyond safe display | both | P2 | V6-09, U-08 | d09 tiers 2 and 3 | After Q-12 | U-08 done |
| DF-37 | V6-27 durability fixes | agent | P2 | V6-05 | Atomic `work.save`; briefing lines | When time allows | Any time after V6-05 |
| DF-38 | V6-31 Jev proxy keep or retire | human | P2 | V6-28, V6-29 | d12 | Decide on data | LD-08 |
| DF-39 | V6-32 report-only learning | agent | P3 | V6-23, V6-24 | d12 | Watch | Labels exist |
| DF-40 | V6-33, V6-34 public readiness and CI hardening; V6-25 step 2 (managed settings) | both | P2 | V6-14 | Parallel track [d12 section 8] | When a public goal or release is near | Q-01 C or a release |
| DF-41 | Learning mechanisms moved to "not yet": recommendation quality, reuse of verified strategies, recurring-failure detection | agent | P3 | labels | No label or store exists [d08 4.4] | Watch | Audit labels exist; same fix works in 3 sessions; one command class keeps failing |
| DF-42 | `jade explain`; `jade config show`; a conftest timing guard; an AGENTS.md step in `jade setup`; a "CI green" release step | agent | P3 | none | `jade explain` is a bypass oracle; others have no need [d11 section 17] | Watch | A user need with a safe design; a second untagged timing test; a second harness; the release workflow stops re-running tests |
| DF-43 | Linux and Windows backends; contradiction detection; blocker taxonomy; retry guard; `Elicitation`, `SubagentStop`, `PreCompact`, `SessionEnd` hooks | agent | P3 | none | No stated need or incident [d01 Part D] | Watch | A real project or a recorded incident |
| DF-44 | A second classifier model; macaroon or Biscuit tokens | agent | P3 | none | Wrong layer; no delegation in JADE [d02 4.3] | Watch | Data shows a classifier gap; JADE gets delegation |
| DF-45 | Coordinator, lock service or message bus for many agents; `jade blocker add`; ledger-minted ids or a work-file lock; a second memory; a code table for the ten blocker classes; a "continue until done" Stop hook | agent | P3 | none | `/goal` and Monitor cover it [d07 section 13] | Watch | A measured id collision or a recorded incident |
| DF-46 | Model-based contradiction or missing-information judge; embeddings or semantic caches; test-runner parser zoo; evidence `supersedes` chain | agent | P3 | none | No model may add or remove a floor [d06 section 12] | Watch | SP-3 hits show a need |
| DF-47 | General benchmark framework, leaderboard, one "JADE accuracy" number, model-simulated owner, LLM judge as gate, statistical gates in per-PR CI, `evals/e2e/owner_sim.py`, custom statistics module | agent | P3 | LD-21 | d10 section 16 | Watch | A named question needs it |

**Never rows (no trigger).** These have no resume condition by design. They map here so that none is lost. [per d04 section 4, d05 10.3, d09 section 12, d11 section 17] MAXIMUM as a standing profile, or any override that widens a "never" list. A stored `autonomy_profile` key beside `level`. Any autonomy raised by outcome statistics alone; learning from the agent's own reports; reinforcement by use. MCP elicitation or tool annotations as an approval or trust input. Usage telemetry or phone-home from JADE. An environment override of the provider URL, or any setting an agent can change. A three-tier ladder config key. A permit code that works as a reading test, a countdown, or extra permit friction without data. Translated `Allow` and `Deny` labels. Screen or keystroke capture and a "possible approval without reading" metric. A second text source per channel. Any new autonomy source without a shadow period and a measured wrong-rate.

## 6. Conflicts between sections

d12 section 2 already settles X-01 to X-14. I repeat its choice in one line each and add what it missed.

| ID | Conflict | Choice | Where decided |
|---|---|---|---|
| X-01 | When 0.7.0 is tagged (d04 H15 vs d10 D-B14) | After V6-06, V6-07, V6-09 | LD-01 |
| X-02 | PR 47 ship route (d01, d03/D-6, d04 H15) | Merge first, inside 0.7.0 | Q-02 |
| X-03 | Id plan (d01 E2 vs d03/D-7, d11 DX-H9) | Note and test now, renumber after PR 47 | LD-02 |
| X-04 | Chat grant key (d05 F2 narrow vs d03/D-3, d04 ADR-14 `plan_hash`) | `plan_hash`, narrow as fallback | Q-04 |
| X-05 | `jev` key (d01 E7 vs d05 3.3, d04 ADR-03) | Build in Phase 1 | Q-07 |
| X-06 | Level `off` (d04/H2, d05 F3 block vs d03/D-1, d11 DX-H3 warn) | Text now, block after count | Q-05 |
| X-07 | `tool_use_id` (d04 ADR-10 add vs d11, d01 not) | Defer | DF-12 |
| X-08 | `jade quickstart` (d04 ADR-11 vs d11, d03, d01) | Do not build | DF-13 |
| X-09 | `jade setup --managed` (d04 vs d03) | Manual file first | LD-12 |
| X-10 | `tree-sitter-bash` now or later (d04, d03, d01) | Defer | DF-09 |
| X-11 | Auto-opened work items (d04 ADR-07 vs d07 6.2) | Defer | DF-24 |
| X-12 | Kappa and honeypots (d10 vs d08) | Kappa on offline corpus; no honeypots | LD-11 |
| X-13 | Package rename W-28 (d11) | Out of v6 | LD-18 |
| X-14 | `jev` tests N28 to N33 placement (d10 vs d12) | Move with V6-28 | V6-28 |
| X-15 | Batched asks. d07 4.1 rejects "keep the other questions" (Fix A: a mixed answer could read as approval) and picks Fix C. d04 ADR-07 item 7 and d09 proposal 6 pick Fix A | [RECOMMENDATION] d07's Fix B now, Fix C next. A yes-to-all must not approve a gated call | LD-16 |
| X-16 | Merge order: W-97 (42, 43, 44, 46, 45) vs d01 E4 (42; rebase 43 and 45; 44 and 46) | W-97 order; d12 V6-05 uses it | Q-09 |
| X-17 | When to leave `off`: d04 H22 "high now" vs d12 "after V6-28" | After V6-28 | LD-07 |
| X-18 | The 50-call auto-mode replay: d05 10.1 item 7 wants it; d02 4.2 and d03 OP-04 say it cannot run offline | Dropped. The gate does not depend on auto mode [d04/ADR-12] | LD-08, U-04 |
| X-19 | PR 47 head: `c6ab27a` (A1, d03, d04), `967b166` (d01), `c676801` (d12; [VERIFIED] by me) | `c676801` is the head. Old claims are stale. Keep `967b166` until U-19 | Q-02 |
| X-20 | Honeypots: d04 H18 "yes, small" vs d08, d10, d12 "phase 1 passive only" | Passive only | LD-11 |
| X-21 | Spike 5 (owner denies) in d02 has no task in d12 | Gets W-178 | section 7 |
| X-22 | Cross-session `reconcile`: d04 ADR-07 item 4 builds a human-only `jade decide reconcile`; d07 7.3 says only if `cancel` loses information; default do not build | [RECOMMENDATION] d07: do not build until the resume probe (U-07) and ledger show need | LD-23 |

## 7. Work items

### 7.1 Proposed ids

[RECOMMENDATION] These ids are proposals. They are not on any ref. [VERIFIED, my read] The next free id is W-117 on the dirty checkout (highest W-116) and W-116 on `origin/main` (highest W-115). W-117 to about W-137 are already reserved for renumbering the 21 dirty rows (d01, d03, d11, d12). So I start at W-140. When a branch adds these, renumber from the next free id on that branch, and keep the links. Do not number new `T` rows here.

**Roadmap tasks.** Each V6-nn becomes W-(139+nn): V6-01 is W-140, V6-08 is W-147, V6-15 is W-154, V6-28 is W-167, V6-34 is W-173. Owner, size and dependencies stay as in d12 section 4.2. Fields for the work file: `owner`, `next_action`, `retry_or_revisit_trigger`, `evidence_refs`.

**Other proposed items.**

| Proposed id | Title | Owner | Links | Trigger or next action |
|---|---|---|---|---|
| W-174 | Owner batch: collect answers to Q-01 to Q-12 | human | Q-01..Q-12 | Next action: answer; trigger: before V6-03 |
| W-175 | Recovery probe rows (`--resume`, Esc, compact, subagent id, permit per retry) | human | U-07 | In V6-15 session |
| W-176 | Read-only ledger measurement set: R-99-only share, asks per 100 by rule, time to answer, expired asks, counter reconcile | human runs | U-09, U-10, U-11 | Before V6-16 |
| W-177 | Live Jev noise and raw-body test (20 plus 20 calls) | human OK | U-14 | Only with owner yes; writes ledger |
| W-178 | Owner-deny count watch for the Jev gate | agent | LD-08, Q-08 | Count until 30, then 100 |
| W-179 | Known-gap row N21 with date and signer | human | DF-19 | In V6-03 |
| W-180 | Known-gap row N25 with date and signer | human | DF-19 | In V6-03 |
| W-181 | Name GUI tools and MCP description change as accepted residual risk in repo docs/05 | agent | DF-20 | With V6-11 |
| W-182 | Clean stale open rows: W-85 (the event is registered since 0.5.0, per d07), W-88 (0.4.0 plan, trigger date 2026-11-16). Review W-12 and W-23 on their trigger dates (2026-12-01, 2026-11-23); Q-01 cites W-12 | human | section 7.2 | After V6-02 |
| W-183 | Chat grant C5 and C6 rerun, SI-07 session-id attack, memory after nonzero exit, `sudo git push` (scratch copy) | agent writes, human runs | U-13 | Before Q-03 and Q-06 build |
| W-184 | Add `jade check` rows: gate version, host mode count, remote protection, pre-push guard, sandbox, mods guard, repo-controlled hooks and MCP servers | agent | U-04, U-16 | V6-10 |
| W-185 | Chat and dialog `human_accept` gap | agent | U-20, d06 section 9 | After V6-23 |
| W-186 | Trace why a comment after `sh` hides R-22; add the harmless twin test | agent | U-20, d02 spike 1 | First task of V6-08 |
| W-187 | Read docs of a second harness when named (Cursor `failClosed`, Codex, Gemini, Cline) | agent | U-23, DF-11 | Trigger: Q-01 B |
| W-188 | Delete `WAITING_DEPENDENCY`, `RETRY_SCHEDULED` after a ledger row count | agent | DF-04 | After the count (U-11) |
| W-189 | Pick a task-quality measure | human | DF-29 | With LD-21 |
| W-190 | Run `jev verify` on the final plan with raw evidence | human or orchestrator | U-25 | After the sections are final |

### 7.2 Existing rows to link, not duplicate

[VERIFIED, my read of the dirty `work-items.jsonl`] These rows already hold part of the plan. Link the new item to them.

| Existing row | Holds | Link |
|---|---|---|
| W-116 (dirty only) | Memory covers critical calls | V6-06, Q-03 |
| W-96, W-108 to W-115 | PR 47 regressions | V6-04, Q-02 |
| W-97 | Merge order | V6-05, Q-09 |
| W-98 | Waves 4 to 6 | LD-05, DF-08 |
| W-99, W-107, W-110, W-112, W-114 | SI-08 residuals | V6-08 |
| W-104, W-105 | Quadratic regex, fragile timing test | V6-08, V6-34 |
| W-102, W-103 | Jev question v2 not calibrated; proxy residuals | V6-31, LD-08, Q-09 (v2 stays out of the champion slot) |
| W-106 | Owner actions after open-items | V6-12 |
| W-68, W-69 | Marked done; real remote state unconfirmed | U-16 |
| W-73 | Seal key and MCP readers | V6-18 |
| W-81 | `jade ack` session id is self-asserted | V6-18, V6-24 |
| W-83 | Unread `escalation.yaml` keys | V6-11 |
| W-28 | Rename `policies` | DF-13 |
| W-04, W-27 | Re-measure baseline; adjudicate label queue | V6-23, V6-21 |
| W-95 | Flaky SQLite CI test | V6-34, U-15 |
| W-91, W-41, W-92 | Hardware key; seal key | DF-28 |

## 8. Critical path, and the approval route of each safety-change task

[INFERENCE] The order follows d12 section 4.1. It names only what this register adds.
1. Now, no code: Q-01, Q-02, Q-09; U-16 (branch protection, pre-push guard); LD-03 (save loose work); U-18.
2. Start the probe session (U-01 to U-08) and the read-only runs (U-09 to U-13) in parallel with Phase 0. They need only the owner's time.
3. Phase 0 build waits for Q-03, Q-04, Q-10, Q-11, Q-12 before the 0.7.0 tag (LD-01).
4. Phase 1 waits for Q-05, Q-06, Q-07, Q-08. V6-19 (leave `off`) waits for LD-07.
5. Everything in section 5 waits for its trigger. Nothing there is on the critical path.

**Approval route.** [VERIFIED, my reading of d12 section 4.2 "Safety change: yes" and the V6-05, V6-08, V6-09, V6-18 cards] No safety-change task is without a route.

| Task | Route | Task | Route |
|---|---|---|---|
| V6-04 PR 47 | Q-02 | V6-19 leave `off` | LD-07 |
| V6-05 merge train | Q-09 | V6-24 decision hygiene (DI-3a, DI-4, DI-5); V6-23 label filter (DI-8) | LD-31 |
| V6-06 memory | Q-03 | V6-26 human text | Q-12, LD-15 |
| V6-07 chat grant | Q-04 | V6-28 `jev` key, deny gate, R-21 out of Jev | Q-07, Q-08, LD-22 |
| V6-08 rule holes | Q-10 | V6-29, V6-30 audit, first scope | LD-09, LD-24 |
| V6-09 safe display | Q-12 | V6-31 proxy | LD-08 |
| V6-11 purge | Q-11 | V6-18 (a) session id | Q-06 |
| V6-16 `off` and R-16, R-14 scope | Q-05 text, LD-29 build | V6-18 (b) agent launch | LD-26 |
| Promotion gates (d02 4.3) | LD-32 | V6-18 (c), (d) | LD-30 |

## 9. Crosswalk: every source id maps to an item here

Rule: no id is dropped. A decision id maps to Q, LD or U. An unresolved bullet maps to U. A "do not build" row maps to DF. A problem or task id maps to a V6 task in d12 section 6.1 (FN, F, HR, BS, OP, MG, DI, N, d11 checklist) and is not repeated here.

**Human decisions to items.**
- d01: E1 Q-02. E2 LD-02. E3 LD-03. E4 Q-09 and LD-04. E5 LD-05. E6 Q-03. E7 Q-07 and LD-28.
- d03: D-1 Q-05. D-2 Q-03. D-3 Q-04. D-4 LD-28. D-5 LD-08. D-6 Q-02. D-7 LD-02. D-8 Q-01. D-9a LD-27. D-9b LD-12. D-10 LD-14. D-11 U-16.
- d03 spec rows SP-01 to SP-13: SP-01 LD-21. SP-02 section 8. SP-03 LD-28. SP-04 LD-28. SP-05 Q-05. SP-06 V6-23. SP-07 V6-11. SP-08 DF-24. SP-09 DF-04. SP-10 DF-04. SP-11 DF-17. SP-12 DF-01. SP-13 V6-11. Doc contradictions CT-01 to CT-20: V6-11 (CT-01, CT-02 also Q-05; CT-03 also Q-03).
- d04 ADR-v6-01 to 14: 01 DF-01. 02 DF-09 and DF-10. 03 Q-05, Q-07, LD-28. 04 LD-27, LD-12, U-01 to U-06. 05 DF-11, LD-26. 06 LD-22, DF-04. 07 LD-23, Q-06. 08 LD-24. 09 Q-12, LD-14. 10 DF-12. 11 LD-18. 12 section 8. 13 Q-08, LD-22. 14 Q-04, LD-16.
- d04 H1 to H22: H1 LD-28. H2 Q-05. H3 Q-03. H4 Q-04. H5 LD-12. H6 LD-27. H7 LD-08. H8 Q-01. H9 LD-14. H10 LD-15. H11 LD-25. H12 LD-18. H13 LD-13. H14 LD-24. H15 LD-01. H16 LD-22. H17 LD-22. H18 LD-11. H19 LD-26. H20 LD-22. H21 Q-07. H22 Q-06 and LD-07.
- d05 section 10.1 items 1 to 10: 1 LD-28. 2 Q-05. 3 LD-28. 4 Q-07 and LD-28. 5 Q-04. 6 Q-03 and LD-06. 7 LD-08. 8 LD-07. 9 LD-27 and LD-12. 10 LD-09 and LD-10.
- d06 O-1 to O-10: O-1 LD-22. O-2 LD-22. O-3 LD-22. O-4 LD-22. O-5 LD-22. O-6 Q-09 and LD-04. O-7 LD-22. O-8 LD-22. O-9 LD-22. O-10 LD-22.
- d06 spikes SP-0 and SP-1: U-14. SP-2: DF-05. SP-3: U-20.
- d07 H1 to H9: H1 Q-06 and LD-23. H2 LD-16. H3 LD-23. H4 LD-23. H5 LD-23. H6 LD-23. H7 LD-23. H8 LD-23. H9 LD-23. d07 D1 to D6 are design choices carried by V6-27 and DF-01, DF-04, DF-17, DF-24.
- d08 section 11 decision rows 1 to 11 (in table order: never-learn list, replay rules, two-input model, staged exposure, weekly budget, honeypots, proxy gate, aging and label evidence, templates and R-16 review, R-12 and R-15, stop rule): row 1 LD-24. row 2 LD-24. row 3 LD-24. row 4 LD-09. row 5 LD-10. row 6 LD-11. row 7 Q-08. row 8 LD-24. row 9 LD-24. row 10 LD-06. row 11 LD-09.
- d09 section 11 proposals 1 to 14: 1 Q-12. 2 Q-09 and LD-15. 3 LD-15. 4 LD-15. 5 LD-15. 6 LD-16. 7 LD-15. 8 Q-12 (clip rule) and LD-15 (cap). 9 Q-12 (stays No). 10 Q-12 (stays No). 11 LD-14. 12 LD-14. 13 LD-15. 14 LD-15. Owner questions: Q1 LD-14. Q2 LD-14. Q3 LD-15. Q4 LD-15.
- d10 D-B1 to D-B14: B1 LD-21. B2 LD-21. B3 LD-10. B4 LD-09. B5 LD-21. B6 LD-21. B7 LD-20. B8 LD-21. B9 LD-11. B10 LD-21. B11 LD-21. B12 LD-21. B13 LD-21. B14 LD-21.
- d11 DX-H1 to DX-H12: H1 Q-01. H2 Q-11. H3 Q-05. H4 LD-17. H5 LD-18. H6 LD-18. H7 LD-18. H8 LD-19. H9 LD-02. H10 LD-17. H11 LD-20. H12 LD-19.
- d12 section 9 items 1 to 20: 1 Q-01. 2 LD-01. 3 Q-02. 4 LD-02. 5 Q-04. 6 Q-03. 7 Q-05. 8 LD-07. 9 Q-07. 10 LD-08. 11 LD-27 and LD-12. 12 Q-11. 13 LD-10. 14 LD-21. 15 Q-01. 16 LD-28. 17 Q-09. 18 Q-12. 19 Q-10. 20 Q-06. d12 section 2 rows 1 to 14: X-01 to X-14.

**Unresolved bullets to items** (numbered in the order each section lists them).
- d01 Unresolved 1 to 8: 1 U-18. 2 U-19. 3 U-15. 4 U-16. 5 U-14. 6 U-02, U-03, U-04, U-08. 7 U-15. 8 U-25.
- d02 (inline and section 4.7): managed hooks with `--bare` or `--safe-mode` U-01. Hook `ask` reach and `defer` U-05. Invalid managed settings, sandbox under bypass, runtime repo name U-03. Auto mode availability and classifier name U-04. Mods guard U-02. Native parser, Inngest, MAF, OpenAI entry point, LlamaFirewall weights, `agent-scan`, annotation fields, A2A licence U-23. OPA latency, advisory counts, snippet claims, abstracts U-24. Container isolation DF-26. Spikes 1 to 7: spike 1 Q-10 and W-186; 2 U-01; 3 U-09; 4 U-05; 5 W-178 (X-21); 6 LD-27; 7 DF-09.
- d03 Part J, five bullets: 1 U-18, U-08. 2 U-02, U-01, U-05, U-04. 3 U-13, U-12. 4 U-19. 5 U-15, U-12, U-14, DF-29.
- d04 section 5, 13 bullets: 1 U-18. 2 U-10. 3 U-01 to U-07. 4 U-04. 5 U-13. 6 U-20. 7 U-14. 8 U-04. 9 U-08. 10 U-25. 11 U-03. 12 U-21. 13 U-12.
- d05 section 10.2, 9 bullets: 1 U-03, U-09. 2 U-01. 3 U-02. 4 U-05. 5 U-08, U-05. 6 U-16. 7 U-13. 8 U-13. 9 U-14. Inline scale direction: LD-28.
- d06 unresolved: Jev body and noise U-14. Live level history U-14. Provider caching and spend U-14. Values needing data DF-05. `human_accept` gap W-185. Overlap U-21. Inline redirect accessor, hashing cost, `actor`, C6 false positives U-20; `repeat_share` U-14.
- d07 unresolved: resume, Esc, compact, permit per retry, task list U-07. C5 and C6 rerun, nonzero exit U-13. CI lifecycle read U-15. Daily `jade check` job U-16. Cwd on rows U-11. `defer` U-05. Inline subagent session id U-07; ask-pause 30 LD-23; `updatedInput` display U-08.
- d08 unresolved: 1 U-12. 2 U-12. 3 U-20. 4 U-20 (audit key, `blast_radius` vocabulary), LD-10 (weekly budget), LD-21 (kappa). 5 U-24.
- d09 section 13, nine bullets: 1 to 6 U-08 (5 also U-05). 7 U-05. 8 U-11. 9 U-20.
- d10 section 18, nine bullets: 1 LD-21, LD-01. 2 U-25. 3 U-05. 4 U-04. 5 LD-21. 6 U-11, U-12. 7 U-15, DF-15. 8 U-22. 9 U-22, U-14, U-24, and U-23 for the licences.
- d11 section 19, nine bullets: 1 U-26. 2 U-20, U-15. 3 U-15. 4 U-16, U-15, and LD-18 for the PyPI name. 5 U-26. 6 U-26. 7 U-23. 8 U-11. 9 U-26, U-16.
- d12 section 10: 1 U-18. 2 U-19. 3 U-01 to U-07. 4 U-12. 5 U-04. 6 U-25. 7 U-10. 8 U-22, U-15.

**Do-not-build lists to deferred items.** d01 Part D rows and d02 4.3: DF-01, DF-02, DF-04, DF-07, DF-09 to DF-15, DF-18, DF-25, DF-43, DF-44. d03 Part H: DF-01 to DF-04, DF-07, DF-09 to DF-13, DF-18. d04 section 4 and d05 10.3: DF-01 to DF-03, DF-06, DF-10 to DF-14, DF-18, DF-24. d06 section 12: DF-04, DF-05, DF-10, DF-14, DF-46. d07 section 13: DF-01, DF-17, DF-24, DF-45. d08 sections 4.4 and 10: DF-14, DF-16, DF-41. d09 section 12: DF-06, DF-07 and the Never rows. d10 section 16: DF-15, DF-21, DF-47. d11 section 17: DF-12, DF-13, DF-32, DF-42 and the Never rows. d05 10.3: DF-02, DF-03 and the Never rows.

## 10. Verified, inferred, unresolved, and how the crosswalk was checked

- [VERIFIED by me] Refs and ids in section 0, including a scan of all local and remote branches and tags (highest W id on any ref is W-115 committed, W-116 dirty). Existing open rows and their titles in section 7.2. W-68 and W-69 are marked done in the dirty file and on `origin/main`.
- [INFERENCE] The priorities, the 12-question grouping, the W-140 start, and the critical path.
- [UNRESOLVED] Everything in section 4. The owner's real answers to section 2. Whether refs moved after 2026-11-16.
- **Checked by script** (`xwalk_check.py`, in my scratchpad): all item ids used in the text are defined; the decision ids of d03 (D-1 to D-11 with 9a and 9b), d04 (H1 to H22), d06 (O-1 to O-10), d07 (H1 to H9), d10 (D-B1 to D-B14) and d11 (DX-H1 to DX-H12) all appear in the crosswalk; d05 section 10.1 has 10 rows and d12 section 9 has 20 rows mapped (rows 17 to 20 were added later and mapped by hand); d12 has 34 V6 tasks. [INFERENCE] The script was run before the second-pass edits that added N39 to N43, standing rule R23 and the new LD rows. It was rerun on 2026-11-16 after them: no undefined ids. Its row-count line still prints 16 for d12 section 9 because it counts an old pattern; the table has 20.
- **Mapped by hand, not by script:** the unresolved bullets of every section (numbered in the order each section lists them; d02 and d06 inline bullets by topic), the "do not build" rows, the d12 4.2 safety-change rows (section 8 table), and d08 section 11 decision rows. A reader should spot-check these.
- Not checked: the approval mentions of d02, d06 to d09 were counted by `grep`, not read one by one. I read the ones in d02 4.3, d05 and d06 section 13.
