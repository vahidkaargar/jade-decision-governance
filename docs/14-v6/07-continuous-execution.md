# Continuous execution and recovery design

This section answers one question. When an AI coding agent is blocked, interrupted or confused, what should JADE do so that work goes on with the least human effort and without a wrong or repeated side effect? It describes a bounded loop (Detect, Classify, Investigate, Evaluate, Resolve or Escalate, Verify, Resume). It says what Claude Code already does, what JADE already does, and the very small list of things JADE should add. It also lists dead states and unread policy keys.

**Ref for threat-row ids.** [VERIFIED, `git show origin/main:docs/05-threat-model.md`] A `T` id in this file (T14, T18 to T29) is a row of docs/05 on `origin/main` (e9412f3) unless another ref is named. `T30` is the real-shell harness row on `origin/main`. `T31` and `T32` exist only on unmerged branches (PR 42 and PR 46); the same number means a different row on different refs (d01 B7).

## 1. The answer in brief

[RECOMMENDATION] JADE builds no workflow engine, no resident agent and no scheduler. The loop is a rule that the agent's model follows. The harness keeps the work moving. JADE does three jobs under it. It draws the boundary (the gate). It keeps the record (the ledger). It tells the next session what is still open (the SessionStart briefing).

Why. [VERIFIED] JADE sits at five hook events and nothing else (`jade/ops.py:38-39`, same on `main` e9412f3 and in A5 at 8100ea1). It cannot start a turn. So "keep going" must come from the harness (R1 section 3.10, A5 section 6). [INFERENCE, docs summary] Claude Code already has `/goal`, `/loop`, cron, Monitor, background tasks, `--resume` and subagents (A5 section 6). R2 reviewed nine runtimes and frameworks. For the four durable engines it names (Temporal, LangGraph, Restate, DBOS) it infers that each needs the agent loop inside its own workflow code (R2 section 2 item 3, [INFERENCE]). JADE governs an agent it did not write, so those do not fit. The other five are agent frameworks, not durable engines, and support the conclusion more weakly.

| # | Decision | Kind |
|---|----------|------|
| D1 | No custom workflow engine, scheduler or resident process. | Recommendation (R2 inference plus five hook events). Record as an ADR. |
| D2 | Add two lines to the SessionStart briefing, computed from existing rows: decisions held in this project, and permits that expired twice. | Design proposal. No safety change. |
| D3 | Blockers only the agent knows (ambiguity, external wait) are not JADE's to store. | Design proposal, trigger-based (6.5). |
| D4 | A count-only warning for repeated identical tool failures, only after measurement. | Measure first (8.1). |
| D5 | After a crash keep today's hold on unknown outcomes. Any change to the hold message or release needs approval, after a probe. | Safety-adjacent. Needs human approval. |
| D6 | Remove what nothing uses, cheapest option first (section 11). | Needs human approval where it touches repo docs/02 or schemas. |

JADE additions, in order of value: the two briefing lines with a small "told once" write (6.3); hold-message words, only if the 7.3 probe shows need; atomic write of `work-items.jsonl` (6.4); optional after measurement, a re-run check for failed irreversible commands (7.4) and a failure-count nudge (8.1); removal of dead items (11). Everything else is guidance for the agent or an owner action in the harness. FLOW is outside the repository (A5 section 2), so changing it is the owner's decision.

## 2. Evidence base and assumptions

- [VERIFIED] I read A5, R1 and R2 fully and re-read cited code on `origin/main` e9412f3. A5 was written at 8100ea1 (v0.6.1). Work-item ids collide across refs (A1 section 5), so every `W-nn` is `main` e9412f3 unless stated. I invent no new `W-` or `T-` ids.
- [INFERENCE, assumption settled by the owner] "v6" means the 0.6.x line, `main` after 0.6.1, and the unmerged 0.7.0 candidates. [INFERENCE] A5 section 0 checked branch diffs and found no change to recovery, resume, retry, the Stop hook or work-item code, so this section probably holds for them all. I checked `origin/design-clear-questions` myself: it changes `jade/askchat.py` only in `question_for`, `step` and `settle`, not in `rewrite()`. I did not re-check the other candidates for `hooks.py`.
- [VERIFIED] Nothing here was run live. The lifecycle crash tests are skipped on this machine (A2, A5 section 4), so crash-safety claims rest on code and tests. [UNRESOLVED] CI at the current `main` was not checked.
- [INFERENCE, docs summary] Facts about `/goal`, `/loop`, SessionStart `source`, background tasks and `--resume` come from official docs read through a summarizing tool (A5 section 6, R1, R2): "the docs say", not "the host does". The only measured host behavior is the JADE probe on Claude Code 2.1.314 (`evals/probes/hook_ask/RESULTS.md`, via R1 and A3).

## 3. The bounded resolution loop

[RECOMMENDATION] The loop is a protocol. Each step has one owner. [VERIFIED] means the code exists today (A5 sections 3 to 5).

| Step | Owner and what exists |
|------|-----------------------|
| Detect | Hooks see gate blocks (PreToolUse exit 2), failed gated calls (`hooks.posttool_failure`) and session start. The model sees the rest. |
| Classify | The model. JADE tags only what it caused: the rule id (R-xx, SI-xx, HO-xx). |
| Investigate | The model, read-only. Calls below `permit_floor` pass without a decision. |
| Evaluate | Rules, then memory, then Jev inside its band, then chat ask (`hooks._gate`). |
| Resolve or Escalate | Chat ask (10 min), dialog for subagents, signed permit (30 min). JADE adds the park rule (10). |
| Verify | The model plus `jev verify`; `decide.record_outcome`; Stop reminder for non-yes Jev answers. |
| Resume | The harness (`--resume`, `/goal`, `/loop`) and the briefing (pending permits, WAITING_HUMAN count, 8-line cap). JADE adds two lines (6.3). |

Three budgets bound the loop.

1. Attempts per same call. [VERIFIED, `origin/main:jade/askchat.py`] `DENY_CAP = 3` counts DENIED asks `WHERE session_id=? AND hold_key=?` (line 69). So it is 3 Denies per call per session. The refusal of the unchanged call also needs `expires_ts>now` (line 73), so it lasts only as long as the ask lives, `ASK_TTL_S = 600` (10 minutes). [INFERENCE] A new session or 10 minutes resets both. A denial does not cross sessions. A5 says "3rd Deny ever" (A5 section 5); that is too strong. Jev is asked at most twice per call per session per hour (`proxy.MAX_ATTEMPTS`). Evidence revisions stop at 3 (`state_machine.MAX_REVISIONS`). "If an approach fails twice, diagnose the root cause" exists only as text (`AGENT_RULES.md` section 11).
2. Human interruptions. [VERIFIED] 30 asks expired unanswered in one hour pause new asks for the session (`askchat.EXPIRE_PAUSE`, `PAUSE_WINDOW_S`). The gate then falls back to a signed permit.
3. Time. Ask and chat grant 10 minutes, permit 30 minutes. A running decision counts as unknown after 3600 s (`ttl_s=3600`, `hooks._gate`, `askchat.run_decision`).

[INFERENCE] JADE should not detect ambiguity, conflicting instructions, missing facts or external waits. They live in the model's reasoning, and a regex detector would miss cases and work only in English (W-75). JADE makes the cheap, safe action easy: look first, record, ask once.

## 4. Blocker classes

[RECOMMENDATION] The owner's brief lists ten classes. I keep the names as a reading aid and group them by who can act, because only the first group has JADE code behind it.

- Caused by JADE (code exists): 2 Permission, 5 Environment failure (fail closed), 8 Risky operation, 9 Interrupted workflow, 10 Human-only decision.
- Known only to the agent (no JADE code; add none without measurement): 1 Missing information, 3 Ambiguity, 4 Tool failure, 6 External dependency, 7 Conflicting instructions.
- Owned by the harness: waiting and resuming.

| # | Class | Today | Proposed handling and retry limit | Escalate when |
|---|-------|-------|-----------------------------------|---------------|
| 1 | Missing information | [VERIFIED] Only for Jev questions (router PF-03, PF-07; NEEDS_EVIDENCE; 3 revisions). Ordinary work: nothing (A5 section 3). | Agent reads files and runs safe commands; facts go to project notes. 2 different read-only tries. | The fact cannot be read and a wrong guess is irreversible or costs rework. |
| 2 | Permission | [VERIFIED] `policy.assess`, `permit_floor`, `hooks._gate`: memory, Jev, chat ask, dialog, permit. Session-bound. | Unchanged. No retry or rephrase after a Deny (8.2). Limits: section 3. | A chat ask or permit is needed. Human away: park (10). |
| 3 | Ambiguity | [VERIFIED] Nothing in code. The answer to an ordinary `AskUserQuestion` stays in the transcript. | Agent picks the reversible default and writes the assumption in the final report and commit message. Irreversible or costly choices: `jade ask q.strategy.*`, then the human if Jev abstains (exit 3). | Options differ in cost and one is irreversible, or Jev abstains on a choice that matters. |
| 4 | Tool failure | [VERIFIED] `posttool_failure` acts only on gated calls. No counter. | Root-cause step on the 2nd identical failure (the written rule). Optional count-only nudge, measured first (8.1). | Two different approaches failed and the cause is outside the agent's reach. |
| 5 | Environment failure | [VERIFIED] `_policy_failure`, `_fail_closed`, `HookBudgetExceeded` (6 s): a gated call fails closed unless clearly read-only. A dead hook is silent until `jade check`. | Make sure the daily job runs `jade check` and exits non-zero on dead wiring. [UNRESOLVED] not verified. | Ledger or policy unreadable for a gated call; hooks missing or shadowed. |
| 6 | External dependency | [VERIFIED] Nothing enters `WAITING_DEPENDENCY` (11). | Agent states what it waits for and the deadline, and uses Monitor, `/loop`, cron or `/goal`. It re-arms background shells after `--resume` (docs summary). | The wait has a human owner and the deadline passed. |
| 7 | Conflicting instructions | [VERIFIED] Written precedence only (`AGENT_RULES.md` section 0). One real conflict in code (4.1). | Follow the stricter safety rule and say which. Limit 0. | The conflict is about safety or an irreversible action. |
| 8 | Risky operation | [VERIFIED] R-xx, SI-xx, `shellnorm`, `humanonly`. Mature (A5 section 3). | Unchanged. Optional: 7.4. Limit 0 for SI and HO. | Always for HO, as its own question. Never for SI: nobody can allow it. |
| 9 | Interrupted workflow | [VERIFIED] `decide.sweep`; a call past its deadline becomes OUTCOME_UNKNOWN; the identical command is then held (SI-07). | Held decisions in the briefing (6.3). Use `--resume`. Hold message: 7.3. 0 automatic retries. | Any irreversible OUTCOME_UNKNOWN from an earlier session. |
| 10 | Human-only decision | [VERIFIED] `humanonly` (HO-xx): never remembered, never sent to Jev, never auto-allowed. | Ask at once, as its own question, with context, options, a recommended default and what happens with no answer (repo docs/05 T14 per A5). Then do other work. Limit 0. | Always. |

### 4.1 The one verified conflict in "conflicting instructions"

[VERIFIED, A5 section 2b; re-read on e9412f3; `origin/design-clear-questions` does not touch `rewrite()`] `askchat.rewrite` searches the whole tool input for a `[jade:ask_...]` token. If it finds one it returns `updatedInput` with exactly one question, replacing every question of the call. The continuous-flow skill tells the agent to batch up to 4 decisions in one `AskUserQuestion`. The only test uses a one-question payload (`tests/acceptance/test_chat_ask_w100b.py:69`, per A5). [INFERENCE] If an agent batches, the other questions vanish before the human sees them. [UNRESOLVED] How Claude Code shows an `updatedInput` with fewer questions than the model sent.

[RECOMMENDATION, needs human approval: it changes the approval channel]
- Fix B (words): the gate text and the flow skill say "send the JADE question alone". The flow skill is outside the repo, so the owner decides.
- Fix C (code, fail closed): when `rewrite` finds a JADE token together with other questions, it refuses and says to send the JADE question alone. Deterministic, testable with a two-question payload, keeps one decision per ask.
- Rejected, Fix A (keep the other questions in the popup): a mixed or "yes to all" answer could be read as approval of the gated call.
Default: B now, C next release.

## 5. What the harness gives and what JADE adds

[INFERENCE, docs summary] Details are in A5 section 6, R1 section 3 and R2. The JADE position:

- Hook events (31 in R1). [VERIFIED] JADE registers five. A crashing hook lets the call run unless `onFailure: block` is set (R1 3.1). [RECOMMENDATION] Add no hook now.
- `--resume`, `/goal`, `/loop`, cron, background tasks, Monitor (A5 section 6, R1 3.11). `/goal` is model judgment, so it is probabilistic. Background shells are not restarted after resume. The background-command limit is 30 minutes by default, up to 2 hours only in unattended sessions (R1 3.11). JADE adds nothing and stays the safety layer under them.
- Subagents. Settings hooks fire inside them, so the JADE gate covers them (R1 3.7; [VERIFIED] A3 probes 8c, 8d on 2.1.314). See section 9.
- `Stop` hook `decision: block` and FLOW (`~/.agents/continuous-flow/hooks/flow_stop.py`, outside the repo). Native cap: 8 blocks in a row (R1 line 56). FLOW cap 6, fails open, reads `TodoWrite` only (A5 section 2). A capped Stop hook cannot be an engine. JADE uses Stop only to remind, once per item.

### 5.1 Two interactions between FLOW and JADE

1. [VERIFIED, `hooks.stop` line 771 on e9412f3; A5 section 2] `hooks.stop` returns at once when `stop_hook_active` is true. So inside a FLOW chain JADE says nothing. [INFERENCE] JADE reminders set a flag once (`ask_nudged` in `askchat.stop_nudge`; `reminded=1` for Jev calls). That is a ledger write, and a write can fail (ledger busy, hook budget). A new ask or Jev call resets the flag. JADE blocks and FLOW blocks share the host cap of 8, so a failed flag write could repeat a reminder up to the cap and use up the chain that FLOW needs. [RECOMMENDATION] Keep the early return: it is the documented loop guard. Close the real gap another way: put the reminder in the SessionStart briefing or the next PostToolUse context, which are not capped. If the owner still wants the guard removed, require two tests first (flag-write failure; the 6 plus 8 cap interaction).
2. [INFERENCE] An open `TodoWrite` item that the human denied can push FLOW to block the stop up to 6 times, pressing the agent to retry it. That works against 8.2. Fix by a rule for the agent: mark a denied item "blocked: human said no" and remove it. The owner decides whether to add it to the flow skill.

[RECOMMENDATION] Do not build a JADE "continue until done" Stop hook. `/goal` does it, and the 8-block cap bounds any such hook.

## 6. Persistence

### 6.1 Three stores, three jobs

[VERIFIED] State lives in three places and they must not be mixed. The harness transcript holds the conversation; hooks cannot read it later. The ledger (SQLite, WAL, one `BEGIN IMMEDIATE` per change; `jade/ledger.py`) holds runtime facts for all projects on the Mac; a row that nothing closes shows forever. `work-items.jsonl` and `BACKLOG.md` in git are the JADE project's own backlog, written by `jade work` in a JADE checkout: per checkout, per branch, cwd-relative, not locked.

[VERIFIED, e9412f3] `work.save` is `Path.write_text` (`jade/work.py`): no temp file, no `os.replace`, no lock. `--file` defaults to `work-items.jsonl` in the current directory (`jade/cli.py`, A5 section 4). `work.add` takes the maximum `W-nn` in that one file plus one. [VERIFIED, A1 section 5] Ids collide across refs. [INFERENCE] The probable cause is the per-file maximum in separate checkouts; A1 shows the collision, not the cause.

### 6.2 Do not use `work.add` for runtime blockers

A5 recommendation 3 says to open a work item automatically for an expired permit, an SI-07 hold or paused asks. [RECOMMENDATION] Do not.
1. `work.add` writes a git-tracked file in the current directory, but a hook runs in any project. The item would land in the wrong repository or fail. [VERIFIED code.]
2. No lock and no atomic write: parallel writers lose an update or leave a cut-off JSONL line that `work.load` rejects (A5 section 4).
3. [VERIFIED, e9412f3] The runtime already creates ledger-only rows `RT-<timestamp>` and `RT-QD-<hash>` (`jade/ops.py:167`, `:203`), trigger `on_session_start`, status open. I found no code that closes them, and `jade work set RT-...` cannot reach them (`jade/cli.py` `_work` loads only the JSONL file). `session_start` shows every such row every session. [INFERENCE, not run] One drift event can keep a briefing line for good.
4. A pending permit and a held decision are already ledger rows. A second row can go out of date.

### 6.3 Design: two briefing lines, computed

[RECOMMENDATION] The demonstrated gap is small. [VERIFIED, `hooks.session_start`] The briefing lists PENDING permits and a count of WAITING_HUMAN decisions. It does not list OUTCOME_UNKNOWN or old EXECUTING decisions. `permits.sweep` runs before the listing, so an expired permit never shows. Add exactly two lines:
1. Held decisions: id, short command text, and the command a human runs. This is the one place a human learns that a command is held.
2. A plan hash whose permit expired twice with no later use (EXPIRED permits of the last day).

Rules:
- The 8-line cap stays. Order: held decisions, pending permits, WAITING_HUMAN, ready work items, drift notices.
- Scope by project. [VERIFIED] The ledger is global and today's briefing is not project-scoped (A5 section 4). A held decision from project Y must not send the agent in project X to run commands about Y. [UNRESOLVED] Do `decisions` and `permits` rows carry the cwd? If yes, filter by it. If no, show the command and its cwd, so it is clear it is another project.
- "Told once" is a small ledger write, not a pure read. [VERIFIED] The pattern exists: `_ready_notice` uses `db.event(..., key=...)` (`hooks.py`). Use it per event key. Exception: `source=compact`, because a compacted session lost its context. [INFERENCE, docs summary] SessionStart has a `source` field; JADE ignores it today (A5 section 4). [UNRESOLVED] Not probed here: does `source=compact` fire and does its context survive?
- Drop the `jade blockers` listing, the eight-field blocker record and the P0 to P3 priorities from the owner's brief: the only consumer is an 8-line briefing, and most fields repeat the source row. Build them when a user asks; then compute every field and store none.

Nothing needs closing: when the permit is approved or the decision cancelled, the line stops by itself. [INFERENCE] Cost: one query over two tables in a hook that already opens the ledger. No migration.

### 6.4 What stays in work items

[RECOMMENDATION] `work-items.jsonl` stays the JADE project backlog, as ADR-09 says. The schema already has what a work item needs (`schemas/work-item.schema.json`, A5 section 4). Add no fields. One change in `jade/work.py`, no safety effect: atomic write (temp file in the same directory, `fsync`, `os.replace`). About four lines, no dependency.

Not now: a file lock and ledger-minted ids. The collision cause is only an inference and the race is rare. Minted ids would make the file depend on global runtime state (today the file is truth and the ledger a mirror), and CI and fresh clones would differ. [INFERENCE] A ledger on one Mac cannot mint ids across machines anyway. Add either only if a collision is measured.

[VERIFIED, A5 section 4] Write order is JSONL, `BACKLOG.md`, then the ledger mirror. A crash between them leaves the file ahead of the mirror. `jade work sync` repairs it and nothing runs it. [RECOMMENDATION] Run it from the daily job, not from a hook: a hook should not write a repository file. Also (backlog hygiene, not continuous execution): [VERIFIED, A5 section 4 at 8100ea1] 13 of 36 open items carry free-text triggers that never show. Add one lint to `jade work add`: the trigger must be `on_session_start`, a date, or marked manual.

### 6.5 Blockers only the agent knows

[VERIFIED] JADE has no row for them, and a work item in the JADE repo is the wrong place for a blocker in another project. Use the harness first (transcript, `/goal`, task list, auto-memory; [UNRESOLVED] does a native task list survive a new session here?), or a project-local notes file. A `jade blocker add` with a `done` command is a new table with a closer, the burden of 6.2. [RECOMMENDATION] Do not build it now. Build it when a human-labelled transcript sample finds lost parked work in a share of sessions that the owner sets.

## 7. Recovery after a crash or an interrupted tool

### 7.1 What happens at each crash point

[VERIFIED by code reading on e9412f3 and A5 section 4; lifecycle tests are skipped locally] Each decision step and its event are one transaction. The SI-07 hold is keyed on the command (`hold_key`), not the session, so a new session still sees it (`hooks._si07_hold`: no session filter).

| Crash or interruption | What follows |
|-----------------------|--------------|
| Before PreToolUse finished | Nothing ran. The agent retries. |
| Grant or permit spent, tool not yet run; or tool ran in part and the harness died | Decision EXECUTING (chat grant, memory and Jev paths all reach it via `askchat.run_decision`). Irreversible: held at once. Reversible: asked again; unknown after 3600 s. The cost is a second human question, not a duplicate effect. [INFERENCE, docs] Esc may not fire PostToolUseFailure; `posttool_failure` keeps the hold when `is_interrupt`. |
| Tool exited with error | `posttool_failure` closes the decision FAILED and releases the hold. The same call is asked again, or allowed by memory or Jev. This is the residual in 7.4. |
| Tool worked, PostToolUse hook failed | EXECUTING stays, so the command is held. A false hold: friction only. |
| Ledger cannot be written | The gated call fails closed (`_policy_failure`, `_fail_closed`). Stops work; correct. |

[VERIFIED code, [UNRESOLVED] host] `hooks._failed` treats `tool_response.interrupted` as a failure, so `posttool` would release the hold if the host sends PostToolUse on Esc, while `posttool_failure` keeps it. The sources do not say which event Esc sends (A5 section 9). [RECOMMENDATION, needs human approval: it changes hold release] After a one-minute owner probe, make `_failed` leave an interrupted call as it is, with one test.

### 7.2 What is safe to repeat

- [VERIFIED] A replay of one host call id gets the same answer and spends no second permit (W-40, `hooks._gate replayed()`). A chat grant is single use (`UPDATE ... rowcount == 1`, A5 section 4).
- [VERIFIED from the `_si07_hold` docstring and the A3 section 7 probe C4] The hold key is the canonical gated segments: quoting, an env prefix, `sh -c`, a program path or another cwd do not make a new command. That stops a rephrase from dodging a hold. It also has a cost: in C4 a chat Allow for `KUBECONFIG=/x/staging.yaml kubectl apply -f deploy.yaml` was reused for the prod KUBECONFIG in another cwd (exit 0). The grant is bound to `hold_key`, fingerprints and session, not to `plan_hash`. A3 recommends binding it to `plan_hash`; that belongs to the enforcement section. Do not extend hold-key reuse to failure retries until it is fixed.
- [VERIFIED, A5 section 4] Calls below `permit_floor` have no decision and no hold, so a resumed session may repeat them. [RECOMMENDATION] Keep it: they are reversible and local. For irreversible effects JADE cannot see (a remote API call from a script) no hook makes a repeat safe: the agent reads the real state first, as the SI-07 message says. Add one line to the `jade` skill.

### 7.3 Resume identity and the release of a hold

- [VERIFIED, A5 section 4] Permits, asks, grants and decisions are bound to `session_id`. Here `CLAUDE_CODE_SESSION_ID` equals the hook payload id. If it is missing the CLI uses `cwd-<hash>-<yyyymmdd>`, which does not match the hook.
- [VERIFIED code] `decide.record_outcome` and `state_machine.apply` refuse an `outcome` or `reconcile` from another session. Only `cancel` and human events pass (`jade/state_machine.py` near line 131).
- [VERIFIED code] `cancel` of a holding decision is not an agent event (`state_machine.agent_may`); only a human at a terminal sends it. Yet the SI-07 message tells the agent to run `jade decide outcome <id> ...` (`hooks._gate`), which a new session is refused. An honest agent is told to do something the code will not allow.
- [VERIFIED code] In the same session `record_outcome` accepts any claim and checks no evidence: the hold guards against an honest agent that forgets, not one that lies. [INFERENCE, A5 section 4] `JADE_SESSION_ID` is not in the SI-11 block list, so an agent could claim an old session id (not run: it writes ledger state). This is for the safety section.
- [UNRESOLVED] Does `claude --resume` keep the hook `session_id`? The SDK docs summary says resume keeps the session and a fork gets a new id (A5 section 4). If resume keeps it, "continue after a crash" never meets this problem. A new unrelated session always does.

[RECOMMENDATION] Steps in this order. Steps 2 and 3 depend on step 1.
1. Probe `--resume`: note the hook `session_id` before and after (a two-command owner step).
2. Only if the problem is common: change the message. When the hold's decision belongs to another session, say: "This decision belongs to an earlier session. A human reads the real state, then runs `jade decide step <id> cancel` in a terminal." One test. Needs human approval: it is words, but the agent obeys them and they name a release path.
3. Only if the ledger shows `cancel` loses useful information: a human at a terminal may record `outcome` across sessions, using the same human-terminal test as `jade label`. Needs human approval. Default: do not build.

Rejected: letting an agent in a new session release the hold on its own claim. The hold becomes a formality; one human question per crash is cheap.

### 7.4 Nonzero exit after a partial action

[VERIFIED code, e9412f3] `posttool_failure` closes the decision FAILED ("exit status only"). `_si07_hold` holds only OUTCOME_UNKNOWN and EXECUTING. `memory.lookup` reads only `human_answers`, not decision history. So after a FAILED run the same command can be allowed again silently, if memory covers it. [INFERENCE, not run; A5 section 4 agrees] This is real for a deploy or push that timed out after part of its work. `proxy.step` refuses a call denied before (`denied_before`); memory has no such check for failures. Re-running after a failure is also the normal wanted case (fix a manifest, run `kubectl apply` again), so a blanket rule adds questions.

[RECOMMENDATION, needs human approval] Where does `reversibility` come from? [VERIFIED] The matched rule's `reversibility` field, default `reversible` when the rule has none (`jade/policy.py:198`, `:1114`, `:1460`), stored in the decision document (`decide.py:42`). A rule without the field counts as reversible: that is the misclassification route. Two options:
- Option 1: when the last decision for the same `hold_key` ended FAILED or OUTCOME_UNKNOWN and is `irreversible`, `memory.step` and `proxy.step` return None, so the call goes to the chat ask ("the last run of this command failed at <time>").
- Option 2: treat a nonzero exit of any held-class command as OUTCOME_UNKNOWN, not FAILED. It does not rely on `reversibility`, so a missing rule field cannot hide a case. It adds more holds.
Before building either, replay the real ledger. Count: irreversible gated decisions that ended FAILED and were repeated within an hour; how many memory or Jev allowed; and, from a read sample, how many were "classified reversible but changed remote state". Choose the option with less friction. If counts are near zero, build neither.

## 8. Repeated questions and infinite retries

[VERIFIED, A5 section 5] Repeat prevention covers gated calls (Deny cap, hold key, ask pause) and Jev questions (2 per call per hour, 3600 s cache). It does not cover ordinary questions or ordinary tool failures. The Deny cap is per session (section 3).

### 8.1 The gaps

| Gap | Evidence | Proposal |
|-----|----------|----------|
| A denied call asked again in a new session or after 10 minutes. | [VERIFIED] `askchat.refusal` filters by `session_id` and, for the unchanged-call refusal, by `expires_ts>now`. | [RECOMMENDATION, needs human approval: it changes ask behavior] Measure first: how often is a denied `hold_key` asked again in another session? If common, count Denies by `hold_key` across sessions for a bounded window. [UNRESOLVED] Does a Deny also write a `human_answers` row that memory reads? |
| An ordinary question is not recorded and can be asked again. | [VERIFIED, A5 section 3] The answer stays in the transcript. | Do not build. A product answer belongs in project notes or auto-memory, where it can be edited. Add a skill rule: write the owner's answer where the next session reads it. |
| A tool that fails the same way again and again, outside the gate. | [VERIFIED] `_close_decision` returns for non-gated calls. No counter. | Measure first. [VERIFIED, from its header] `evals/baseline/mine_transcripts.py` mines only `jev` invocations, so it cannot count failing calls. A small new miner is needed: sessions with 2 or more identical failing calls in a row (same tool, same canonical input). If the share is small, write nothing. If large, add a counter shown through PostToolUseFailure `additionalContext` (A5 section 1). Count only, never block. Effect test: among nudged sessions, the share where the next call differs. Showing that the text was injected is not enough. |
| The ask-pause threshold. | [VERIFIED] 30 unanswered asks per hour pause asking. The Deny cap is 3. | [UNRESOLVED] Is 30 right? It looks high. Measure expired asks in away sessions. The owner sets the number. |

### 8.2 Never disguise or split a denied operation

[VERIFIED] A Deny is recorded by hold key, so quoting, `sh -c`, env prefixes, program paths and cwd do not make a new call (`askchat.refusal`, `_si07_hold`). After the 3rd Deny in a session the answer is a plain refusal. The gate text says "do not rephrase". A human-only class is never remembered and never sent to Jev. [VERIFIED, R1 3.5, A5 section 3] Disguises the shell classifier cannot see remain (encoded commands, a script written then run: T18 in docs/05 on `origin/main` e9412f3; W-31, W-34, W-37, W-77 to W-82 at 8100ea1). They belong to the enforcement section.

[RECOMMENDATION] The rule is about the goal, not the command string. A "no" ends that goal unless the human reopens it. The agent does other work, or reports the block and stops. JADE cannot see intent in a hook, so this is a rule in the skill and the flow text (5.1 item 2). [UNRESOLVED] Probe: does a blocked gated call make a new permit on each retry? Then decide whether a repeat-block nudge is needed. [RECOMMENDATION] Where this rule lives: the text in the skill and the flow text is built in roadmap V6-26 (a staged proposal, because the skill is outside the repo), the standing rule is R23 of d14, and the negative tests are N39 to N43 of d05 section 9.3 (V6-17). JADE only records and measures this; it cannot block a split or reworded goal.

## 9. Parallel execution and multi-agent coordination

[RECOMMENDATION] Build no coordinator, work queue or lock service. The harness has subagents, worktrees and prompt routing (R1 3.7). The remaining needs are small:

1. Approval binding. [VERIFIED, A3 probes P6, P6b] A permit for one session or `agent_id` cannot be used by another. [UNRESOLVED, A3 section 7] The same for a chat grant is not proven: C5, C6 and C8 all hit SI-07 because C4 left a run EXECUTING. Rerun C5 and C6 with the first call closed in the ledger. Until then do not claim grants are isolated between agents.
2. Subagent asks. [VERIFIED, A3] A subagent has no `AskUserQuestion`; JADE opens a macOS dialog the hook waits for (`askdialog`, 120 s). [VERIFIED code, `askchat.gui_paused`] While it is on screen the screen-control tools of every session are paused, so one slow dialog can stall other sessions. [RECOMMENDATION] Accept it: a human decides one thing at a time. R1 3.7 reports (docs summary) that background-subagent permission prompts reach the main session. [UNRESOLVED] Whether that can carry a JADE ask.
3. [VERIFIED code] The ledger uses WAL and one `BEGIN IMMEDIATE` per change with a timeout (`LEDGER_TIMEOUT_S`). Jev requests in flight are capped at 4 (`routing.yaml max_parallel_requests`). Enough.
4. [UNRESOLVED] Does a subagent's hook payload carry the parent's `session_id` or its own (A5 section 4)? This decides whether a subagent can reconcile its parent's decision. Settle it in the 7.3 step 1 probe.

[INFERENCE] Two worktrees can each ask the human about one command: one extra question, not a wrong effect. Do not fix it.

## 10. When escalation to the human is justified

[RECOMMENDATION] Fewer questions are good only if decisions stay right. Escalate, as its own question, when at least one holds:

| Code | Condition |
|------|-----------|
| E1 | The operation is human-only, or needs a human's hands (a secret, `sudo`, approving a permit). Never retry, never batch. |
| E2 | The action is irreversible or visible outside the machine, and no exact confirmed answer covers it. A wrong guess cannot be undone. |
| E3 | A fact is missing and unreadable, and a wrong guess is irreversible or costs real rework. |
| E4 | An irreversible operation has an unknown outcome (SI-07): a person reads the real state. |
| E5 | The same blocker survived the budgets: two different approaches, or the same-call limit. |
| E6 | Two instructions conflict on a safety rule or an irreversible action. |

E2 and Jev. [VERIFIED, `jade/proxy.py` docstring and `skip_reason`] The Jev proxy works at autonomy low and medium (shadow at high). It is asked only about calls assessed "high". It never sees human-only classes, critical calls, blocked calls or calls the gate could not fully read. So today Jev can accept a plain high-risk call, including an irreversible one, inside its band (`policies/escalation.yaml human_proxy`). [VERIFIED, A2] In the live ledger it allowed 0 of 48 calls. The owner's rule says "a probability is never permission". [RECOMMENDATION, needs human approval] Decide whether irreversible or external actions should be excluded from the proxy, so only an exact confirmed answer in memory can cover them. Default: keep today's behavior, and decide after counting how many irreversible calls Jev was asked about. No text the agent reads may call a Jev accept "permission".

Do not escalate when: the choice is reversible or taste (pick, record the assumption, go on; section 12 measures reversals); the answer can be read; a confirmed answer in memory covers it (30 days from last use, 180 at most); a tool failed once and the cause is not examined; a wait has a deadline that has not passed.

When the human is away. [VERIFIED] After 30 unanswered asks in an hour the gate raises a signed permit instead, so the human finds something to approve on return (`askchat.paused` docstring). [RECOMMENDATION] The agent then parks: it records the blocker, does independent work, and ends with one stop reason: done; blocked on the human (with the id); blocked on something external (with the condition); budget used. With no independent work it stops. It does not retry the gated call in a loop.

How to ask. One question per ask, in simple words, with context, impact, options, a recommended default and what happens with no answer. [VERIFIED] `policies/escalation.yaml` lists these under `human_question.each_question_includes`, but no code reads the key (W-83; grep finds no reader in `jade/`). [RECOMMENDATION] Test the real chat-ask text for the five items, then delete the key.

## 11. Dead states, unread keys and things to delete or wire

[VERIFIED by grep on e9412f3 unless noted]

| Item | Evidence | Recommendation |
|------|----------|----------------|
| `WAITING_DEPENDENCY` | In `state_machine.WAITING` and `EXPIRES`. No edge in `T` enters it. `tests/acceptance/test_phase2_enforce.py:45-47` lists its exit edge as untested. | Cheapest: mark "reserved, not entered" in docs/02 and test that nothing enters it. A full delete touches repo docs/02, the `schemas/decision.schema.json` enum and tests, with no behavior gain: P3. Needs human approval. |
| `RETRY_SCHEDULED`, edges `retry`, `resume` | Entered only by `FAILED --retry-->`. [VERIFIED, `state_machine.agent_may`] `retry` and `resume` are not human, outcome or runtime events, so an agent can send both through `jade decide step`. Nothing in the hook path sends `retry`; nothing schedules `resume`. `test_phase2_enforce.py:23` tests `(RETRY_SCHEDULED, resume)`; `(FAILED, retry)` is untested. | Same cheap option. If the owner deletes it, that test row goes too. [UNRESOLVED] Read-only count of ledger rows in these states, to see whether old documents would break on an enum change. |
| `escalation.yaml` keys `human_question.each_question_includes`, `deadline_default_h`, `families.*.abstain_to`, `by_risk.critical.abstain_to`, `families.verify_claim.max_revisions` | No reader in `jade/` (W-83; A2 agrees; re-grepped). `deadline_default_h: 24` contradicts real timeouts (ask 10 min, permit 30 min, decision 3600 s). `MAX_REVISIONS = 3` is hard-coded. | Delete the first four (text test: section 10). Wire `max_revisions` into `state_machine`, or delete it. |
| No guard for unread keys in `escalation.yaml` | `routing.yaml`'s header says `tests/test_routing_policy_w53.py` fails on any unread key. [VERIFIED header; I did not read the test.] No such test for `escalation.yaml`. | Add the same test. |
| `routing.yaml` `routes.ESCALATE.description`: "Batched with other open questions" | [VERIFIED text] `escalation.yaml` and repo docs/05 T14 say no batching. | Reword. One line. |
| `RT-` and `RT-QD-` ledger work items | 6.2 item 3. No closer. The row's own `next_action` says "add the id to accepted_resolved in the registry when reviewed" (`ops.py:170`), and `ops.py:218` reads `accepted_resolved`. | Keep the reminder, but derive "open" from the registry: show the line only while the model or question version is not in `accepted_resolved`. [INFERENCE] I did not trace how `check_drift` avoids raising the same drift again. Existing rows need a one-time human cleanup. Tests to review: `tests/acceptance/test_phase5_rollout.py:36` counts `RT-%` rows; `test_work_items_w51.py:202` counts `QUESTION_DRIFT` events and work items. |
| Stale text: repo `docs/05` T20 and W-85 (open) say PostToolUseFailure is not registered | [VERIFIED, A5 section 5] Registered since 0.5.0 (`ops.py:39`). | Close W-85, fix T20. Docs only. |

## 12. How we will know it works

The design lowers human questions. It must not lower decision quality. These numbers measure both. The owner sets the threshold that makes each proposal worth building; I state none as fact. [UNRESOLVED] No baselines exist yet; take them first.

| Metric | Source | Used for |
|--------|--------|----------|
| Asks per 100 gated calls; expired-ask rate | `asks`, `decisions` | Friction; the pause number (8.1); the park rule (10). |
| Age of a held decision when released | `decisions`, `events` | Does the briefing line (6.3) shorten it? |
| Denied `hold_key` asked again in another session | `asks`, DENIED | The cross-session Deny proposal (8.1). |
| Repeated identical failures per session | New transcript miner (8.1) | D4, then the effect test: next call differs. |
| Assumption-reversal rate | Not in the ledger. Sample final reports with an "assumption" line; a human labels whether the owner later changed it or work was redone. | Class 3 rule. A high rate means escalate more. |
| Lost parked work; done-claims without verification | Human-labelled transcript samples. | The `jade blocker add` trigger (6.5); Stop force (5.1). |

Safety side: a rise in unknown-outcome holds, or in irreversible calls allowed by memory or Jev, is a reason to roll back an autonomy change.

## 13. What not to build, and what remains open

Do not build:
- a workflow engine, durable-execution layer, job queue, retry scheduler or resident process;
- a "continue until done" Stop hook (use `/goal`), a new hook event, or a second ask transport, without a measured need;
- a `jade blocker add` table, a `jade blockers` listing, or automatic work-item rows for blockers;
- detectors for ambiguity, missing facts or conflicting instructions;
- a second memory for the owner's ordinary answers;
- an agent that releases an SI-07 hold across sessions on its own claim;
- a coordinator, lock service or message bus for multiple agents;
- ledger-minted work-item ids or a work-file lock, unless a collision is measured;
- a code table for the ten classes (five have no JADE code path).

Decisions only the human can make, each with a default:

| # | Decision | Default |
|---|----------|---------|
| H1 | Who may release a hold from another session. | A human at a terminal only (`cancel`). A recorded outcome only if `cancel` is shown to lose information (7.3). |
| H2 | One question per ask, or the flow skill's batch of up to four. | One per ask. Fix B now, Fix C next release (4.1). |
| H3 | `WAITING_DEPENDENCY` and `RETRY_SCHEDULED`. | Mark "reserved, not entered". Delete later (P3) after the ledger row count. |
| H4 | Re-ask a failed irreversible command instead of allowing from memory. | Only if the 7.4 replay shows repeats. |
| H5 | Remove the Stop early return. | No, until the two tests in 5.1 exist. |
| H6 | The ask-pause number (30); a cross-session Deny count. | Measure, then lower the pause number. |
| H7 | Flow skill (outside the repo): mark denied todos "blocked". | Yes. |
| H8 | Exclude irreversible or external calls from the Jev proxy. | Keep today's behavior; decide after counting (10). |
| H9 | Change the SI-07 hold message for another session. | Only after the `--resume` probe shows need. |

Unresolved, with what would settle each:
- One probe: does `claude --resume` keep the hook `session_id`, and does a subagent get its own? Does Esc on a running Bash call fire PostToolUseFailure, PostToolUse or neither? Does SessionStart `source=compact` fire, and does its context survive? Does a blocked gated call make a new permit on each retry? Does a native task list survive a new session?
- Rerun A3 C5 and C6 with the first call closed: is a chat grant bound to session and `agent_id`?
- A test and a ledger replay: is the nonzero-exit residual real (7.4)?
- Read the CI run for lifecycle crash tests at the current `main`; read the daily job for `jade check` (A8 may know); read the schema for the cwd on `decisions` and `permits` (6.3).
- `defer` for headless runs (R1 3.6, R2 section 2) is not probed here. It is a candidate for the headless section.
