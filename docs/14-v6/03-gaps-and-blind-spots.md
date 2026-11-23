# Gap and blind-spot analysis

This section answers one question: what do the owner's v6 spec, the code, the docs and the earlier plans miss or get wrong? It uses the audits (A1 to A8) and the research (R1 to R6) and does not repeat them. Bugs and false safety text come first (Part A). They need no new design.

**Ref for threat-row ids.** [VERIFIED, `git show origin/main:docs/05-threat-model.md`] A `T` id in this file (T14, T18 to T29) is a row of docs/05 on `origin/main` (e9412f3) unless another ref is named. `T30` is the real-shell harness row on `origin/main`. `T31` and `T32` exist only on unmerged branches (PR 42 and PR 46); the same number means a different row on different refs (d01 B7).

## 0. How to read this section

**Tags.** [VERIFIED] has concrete evidence: a file, symbol, test, probe, or an evidence id such as A3 or R6. [INFERENCE] is my reasoning. [RECOMMENDATION] is advice. [UNRESOLVED] means nobody settled it. Research claims keep their original confidence. "Via summary" means a page or search summary, not the full page.

**Which ref.**
- `8100ea1` is tag v0.6.1, installed on this Mac. [VERIFIED A3, A4]
- `e9412f3` is `origin/main` on 2026-11-16: 0.6.1 plus PR 48 (SI-13). [VERIFIED A1, A3]
- "Unmerged stack" is PR 42 to 47 and the branches under them. It does not change level meaning. [VERIFIED A4 section 0]
- W-nn ids mean the dirty backlog in the owner's checkout, unless I write "on main". They collide with main (FN-11).

**Assumptions (reversible).**
- A-1: "v6" means the 0.6.x line, plus main after 0.6.1, plus the unmerged 0.7.0 candidates. No repo file defines "v6". [VERIFIED A1 section 1]
- A-2: I did not see the owner's v6 spec. "The spec says" means wording quoted by A2, A4 and the task (MANUAL, ASSISTED, BALANCED, HIGH, MAXIMUM, MEDIUM-HIGH, HARD, DEFER_WITH_PLAN, SAFE_ALTERNATIVE, NEED_EVIDENCE). [UNRESOLVED] The owner should check my reading.
- A-3: Each row ends with impact (H, M, L), effort (S under a day of agent work plus review; M a few days; L weeks) and a topic: Floor (safety floor and levels), Decide (decision loop and Jev), Measure, Human (human experience), Native (Claude Code layers), Threat, Docs (release and docs), Roadmap. The assembler maps topics to section numbers. Effort is [INFERENCE]. Every fix-now item changes safety behaviour or text, so each is a proposal that needs the owner's approval.

**Words.** A "label" is a known right or wrong answer for one decision. Today labels come only from accepted calls that ran, so refused calls teach nothing ("selective labels"). A "honeypot" is a fake risky call used to test attention. A "parser gap" is when the shell reader misreads a command, so a rule does not fire.

**Checked by me** on a `git archive` copy of `origin/main` (`e9412f3`), empty HOME, nothing executed. [VERIFIED] `memory.eligibility` never reads the risk level. `hooks._verdict` makes SI-08 and all non-governance blocks advisory at `off`. `hooks.py` line 408 raises human-only classes only at `low`, `medium`, `high`. `askchat.py` keys an allowed ask by `session_id`, `hold_key` and `fingerprints_json`, not `plan_hash`; it clips a command to 450 head and 150 tail characters and has no `printable()`. [VERIFIED by my run] The R-99 low shapes named in FN-05.

## 1. Headline findings

1. [VERIFIED A3] On this Mac the gate is mostly advisory. Level is `off` and only SI-09 to SI-12 block. 3416 of 3424 gated calls in 7 days ran without a prompt. `jade check` prints "ok". The task text given to the audit agents said the hook fails closed on a push to a default branch. At `off` that is false. So the team can believe in safety that is not there.
2. [VERIFIED] The spec says safety and autonomy are separate. The code ties them. Any level, even `off`, adds SI-12 and the firm governance invariants (SI-09 to SI-12). Only `low`, `medium` and `high` also add human-only raising, chat ask, memory and Jev. A4 contradicts itself (section 5 exception 2 says not raised at `off`; section 6 item 1 says the reverse). I read the code: section 5 is right. Cutting this tie beats adding profiles.
3. [VERIFIED A6] Decision accuracy cannot be measured today. The live ledger has zero labeled rows. The only label is "the action succeeded", reported by the agent. That breaks the spec's own rule: execution success is not decision correctness.
4. [VERIFIED A6, A8] Human load is concentrated. R-16 and R-14 cause most answers (77 of 100 in A6; R-16 65 and R-14 35 of 100 asks in A8; different windows). 100 asks came from 2666 gated calls in 14 days. [INFERENCE] The first friction lever is rule scope, not autonomy.
5. [VERIFIED A3, R6] Four kinds of silent hole will not close with a smarter model: parser gaps, config that loads before trust, other harnesses and child agents, GUI tools. [RECOMMENDATION] Use containment, not more parsing.
6. [VERIFIED A1] Real work exists nowhere in git, and ids collide (FN-11).
7. [INFERENCE] Fixing the floor makes the owner answer more questions first (table after Part A).

## PART A. Fix now, before any v6 work

Bugs and false statements. Each has a test that proves it fixed.

| ID | What is wrong, and evidence | Proves it fixed | Impact, effort, topic |
|---|---|---|---|
| FN-01 | [Head now `c676801`; see the correction note below the table] PR 47 at `c6ab27a` was weaker than main on SI-08: `cat<<<'git push origin main'\|sh` blocks on main, not on the PR. CI is green: no test covers it. The fix is uncommitted. [VERIFIED A1 section 7.2] Refs may have moved. | Pinned test: this command blocks at the PR head. | H, S to commit and M for round 5, Roadmap |
| FN-02 | Memory can silence critical calls. A confirmed `kubectl apply`, `pfctl` or key removal covers the whole kind. The class key drops env prefixes, sudo and cwd, so a staging answer covers prod. docs/12 section 10 says critical calls are asked. [VERIFIED A3 section 6; A4 section 9.1] W-116. Idle today; it wakes when the owner sets `low` or `medium` and runs `jade review confirm` (82 answers wait). | Tests: critical is never memory-allowed; a staging answer does not cover prod. | H, S, Floor |
| FN-03 | A chat Allow is keyed on the normalised command, not `plan_hash`. An Allow for a staging `kubectl apply` was spent on the prod one in another cwd. The human sees one text; the agent runs another. [VERIFIED A3 probe C4; `asks` lookup] No W row tracks it. | Test: an Allow is not spent on a different `plan_hash`. | H, S to M, Floor |
| FN-04 | Level `off` is advisory, yet `jade check` prints "ok level: off" and docs/12 section 2 lists `off` as a level. At `off`, SI-08, R-13 and R-16 do not block (Write of `settings.json` exits 0). The "ok" text is a cheap truth bug. Changing what `off` blocks is a safety change (D-1). [VERIFIED A3 sections 1, 3, 10; `ops.py` level check] | The row says "advisory" and names what does not block. | H, S text and M behaviour, Floor |
| FN-05 | Rule holes with easy fixes: a trailing comment hides fetch-and-run from R-22 (never remembered, never sent to Jev, yet runs silently); `gh api .../merge` and `git send-pack` to main; shell-profile and `authorized_keys` writes; writes to `CLAUDE.md`, `.mcp.json`, `.vscode/settings.json`, `.codex/config.toml`. All are R-99 low (my run on `e9412f3`). The comment fix is a parser change; the rest are plain rules. [VERIFIED A3 section 8b; R6 section 1.5] Same shape as Copilot CVE-2025-53773 and Codex CVE-2025-61260 (R6, vendor pages read). | A bypass test per shape, plus one `gate_replay` run on real transcripts reporting extra asks per day. One batch, one independent review. | H, S per rule and M for the parser fix, Threat |
| FN-06 | Chat and dialog questions show raw bidi and control characters. A long command is clipped and the middle hides before the human clicks Allow. Only the terminal permit screen escapes characters. [VERIFIED by R6 probes 2 and 9 on `open-items-scopes`; code on `e9412f3` shows the same clip and no `printable()`.] Live drawing: [UNRESOLVED]. Escaping must keep Persian prose (U+200C). | Tests: U+202E escaped; clipped middle marked; U+200C kept. | M to H, S to M, Human |
| FN-07 | The plain first line in PR 43, "Merge pull request 940.", hides `--delete-branch`, `--admin`, `--auto`. [VERIFIED A7 section 2.1] `test_ask_plain_text.py:208` pins it. A merge gate for PR 42/43, not a main bug. | Test: `--admin` shows in the line. | M, S, Human |
| FN-08 | SI-07 reconcile after a crash needs the same session id. The id comes from an env var, and `JADE_SESSION_ID` is not in the SI-11 list. [VERIFIED A5 section 4; I confirmed in `policies/safety.yaml`] [INFERENCE A5] An honest agent is stuck after a new session; a dishonest one could lift the hold. [UNRESOLVED] Nobody ran that attack, or checked if memory re-runs after a nonzero exit. | Test: another session id cannot clear a hold. | M, M, Decide |
| FN-09 | Docs tell a false safety story (Part D). `test_docs_truth.py` missed the hook count, registry count and W-85. [VERIFIED A2 to A8] | The test compares counts with code. | M, S, Docs |
| FN-10 | Live-state truth. The maintain job is not loaded (`jade check` FAIL), so retention, sweeps, drift and the miner do not run, yet README says it is loaded. The installed gate (0.6.1) lags main and nothing says so (A8 section 11; A3 section 8h). [VERIFIED] `ask.py` line 252 calls `check_drift(None, ...)`, which opens the real home, and `FixtureProvider` returns a `-fixture` model string. [INFERENCE A6] That is how one MODEL_DRIFT item came to exist; [UNRESOLVED] nobody read the row. | Test: a fixture run writes nothing to the real home. A `jade check` row for gate version. | M, S owner step and S code, Docs |
| FN-11 | Work that exists nowhere in git, and colliding ids. An uncommitted round-4 fix. Four untracked plan and spec files. 21 new backlog rows in a dirty checkout; `git add -A` there would commit 236 mode changes. W-97, 99, 101 to 105, W-115 and T30 each mean two things. [VERIFIED A1 sections 5, 6] | Clean `git status` on plan files. A test that fails on a duplicate W or T id. | H (loss), S, Roadmap |
| FN-12 | `ops.purge` skips `asks.question_json` and `human_answers.label`, which hold command text, so the retention promise is broken. Nothing schedules purge off macOS. [VERIFIED A8 section 9] | Test: purge clears both columns. | M, S, Docs |
| FN-13 | The gate that lets Jev decide for the owner is weak. Shadow ends after 50 distinct approved calls. It counts hints, and a pushed HEAD counts as a new call. Live: 67 of 50 passed on 100 answers, all allow, zero deny. "Always allow" would agree 100%. At `high` the command goes to the provider in shadow, only when preferences or confirmed answers exist (docs/12 lists this as accepted risk; the word `high` gives no hint). [VERIFIED A6 sections 2.1, 5; A4 sections 3, 9] | Test: shadow ends by a count of owner denies, not answers. | H once a level above `off` is set, S to M, Decide |

**Correction (master plan check, 2026-11-16).** [VERIFIED `git rev-parse origin/claude/heuristic-hamilton-0eae1d`] FN-01 and D-6 name `c6ab27a` as the PR 47 head and the fix as uncommitted. The head on origin is now `c676801` and d12 section 0 found the four A1 commands block there. FN-01 stays open as "verify `c676801` against main on every shape" (V6-04), not as "commit the fix". The 0.6.2 option in D-6 is settled by d12 section 2 row 2: no 0.6.2.

[RECOMMENDATION] Order: FN-11 first (it protects the other work). Then FN-01, FN-02, FN-03, FN-05 as one "close the known holes" release. Then the FN-04 text, FN-06, FN-07 with PR 42 and 43. Do not enable `low` or `medium` until FN-02, FN-03, FN-13 are done.

**Effect on ask load.** [INFERENCE] Fixing the floor raises asks first. That is the honest price of "fewer approvals is not better". Measure each change with `evals/gate_replay.py` on real transcripts before it ships.

| Change | Asks go | Paid back by |
|---|---|---|
| FN-02 critical never remembered; FN-03 `plan_hash` binding (D-3); FN-05 new rules | up | scoped memory (PR 45); a clearer, smaller ask; the replay count decides each rule |
| FN-04 behaviour: `off` blocks SI-08, R-13, R-16 | up, mostly R-16 | project scope for R-16 and R-14 in the same change |
| SP-05 floor split from the level | up | nothing; it is the cost of safety |
| HR-04 project scope for R-16 and R-14 | down | the main friction win |

## PART B. Spec requirements, challenged

Verdicts: KEEP, RESHAPE, DROP, ALREADY. The left column is the quoted wording (A-2). "Need" asks: is there evidence it is needed?

| ID | Requirement and problem | Verdict | Need | Impact, effort, topic |
|---|---|---|---|---|
| SP-01 | "Reduce human approvals." A count can be gamed: a yes-to-all model scores perfectly. Live: 100 answers, 100 allow. Anthropic reports about 93% of prompts approved [INFERENCE: vendor figure via summary]; R1 and R5 opened the post, R6 found only secondary sources. | RESHAPE: show approvals only beside the Part G metric set. "Supervision cost per correct and safe unit of work" is a direction, not a metric today. | Yes | H, M, Measure |
| SP-02 | Three goals: autonomy, low friction, measurable accuracy. They pull apart, and the third has no data. Install friction (12 to 14 human steps, A8 section 1) is bigger than approval friction. | RESHAPE: rank them: safety floor, accuracy, friction. Autonomy is an outcome. | Yes | H, S, Roadmap |
| SP-03 | Levels OFF, LOW, MEDIUM, MEDIUM-HIGH, HIGH, HARD. Two things are called off (`gate off` is inert; `level off` still blocks some). `high` means least autonomy. MEDIUM-HIGH and HARD appear nowhere. A4 line 153: MEDIUM-HIGH is clear (memory on, Jev off) but needs a Jev-off key. HARD is undefined; A4 line 214 guesses "signed permit only, medium risk gated". | RESHAPE: do not drop yet. Ask the owner (D-4). Say once in docs that `high` is the strict end. | Not shown | M, S, Floor |
| SP-04 | Profiles MANUAL, ASSISTED, BALANCED, HIGH, MAXIMUM. BALANCED is `level medium`; HIGH is `level low` (clashes with `level high`). MANUAL and ASSISTED need a missing Jev-off switch. MAXIMUM loosens never-lists: unsafe. | RESHAPE: names in docs, not states. DROP MAXIMUM. Add `jev=on\|off` only if the owner wants Jev off; none is needed if D-5 retires the proxy. | Not shown | M, S to M, Floor |
| SP-05 | "Safety and autonomy are separate." Right idea; false in code (headline 2). SI-08 depends on the level. At `low` and up, human-only raising, chat ask, memory and Jev arrive together. | KEEP and make true: a safety floor that does not read the level. Needs approval. | Yes | H, M, Floor |
| SP-06 | "Evidence before confidence." The code labels a decision by whether the action SUCCEEDED, reported by the agent (`decide.record_outcome(actor="agent")`). 165 decisions have 0 provider calls attached. | KEEP the rule; the code breaks it. Need three label kinds: claim true, owner would allow, harm seen. "Harm seen" has no source; reverted agent commits are the nearest proxy (Part G). | Yes | H, M, Measure |
| SP-07 | "Deterministic enforcement is separate from probabilistic help." Mostly met. Two leaks: memory and the Jev proxy turn a past click or a probability into permission. Auto mode does the same, with 17% misses on 52 real overeager actions (interval about 9% to 30%, R5) [INFERENCE: vendor figure via summary]. | ALREADY, with a named exception. Say in docs that proxy and memory are the only places a probability can stand in for a human. | Yes | M, S, Decide |
| SP-08 | DEFER_WITH_PLAN. Nothing implements it: `WAITING_DEPENDENCY` has no incoming edge, `RETRY_SCHEDULED` never resumes. Native `defer` works only in `claude -p` (read raw; not probed). `/goal`, `/loop`, Monitor and cron already wait. | RESHAPE: not a verdict. One work item (owner human, `next_action`, trigger) when a permit is pending past expiry. Need shown for that case only: on 2026-11-13 the owner gave 18 terminal approvals, then 18 asks expired (repo docs/12 section 1). | Partly | M, M, Decide |
| SP-09, 10 | Verdicts incl. SAFE_ALTERNATIVE, and a "Jev refinement skill". Four verdicts map to exit codes today; NEEDS_EVIDENCE already means two things. Jev returns only probabilities: it cannot propose an alternative or say what evidence is missing, and a model-written alternative is a channel the design forbids. A trailing space counts as "new evidence"; `--no-cache` resamples without limit; `--risk` defaults to `low`. | RESHAPE: a typed `Verdict` view only if a metric uses it. Refinement is deterministic code, at most 2 re-asks per evidence hash, after the runtime holes are fixed. | Not shown | M, S to M, Decide |
| SP-11, 12 | Continuous execution and a blocker taxonomy: Claude Code has no resident agent, and `/goal`, `/loop`, Monitor, cron cover it; A5 shows a gap (no retry counter), no incident. "Optional", "harness-agnostic": install is 12 to 14 human steps, macOS in practice, and docs/00 A2 says "harness-neutral" while only Claude Code is wired. | RESHAPE: no scheduler, no taxonomy; atomic `work.save` only. Say "Claude Code on macOS" until the owner picks a second harness (D-8). | Gap only; not shown | M, S, Decide and Docs |
| SP-13 | "Smallest architecture." The repo has unneeded parts: a dead state, FAST and DEEP routes with no emitter, a `score` that never gates, config keys no code reads (`abstain_to`, `max_revisions`, `fallback`, `each_question_includes`, `deadline_default_h`). | KEEP and apply to the repo: delete or implement each unread key. | Yes | M, S to M, Roadmap |

[INFERENCE] Pattern: the spec adds names for things the code can already say, or for things nothing can produce. Add a name only when a number, a test or a hook uses it.

## PART C. Hidden risks

FN items are not repeated.

| ID | Risk and evidence | Impact, effort, topic |
|---|---|---|
| HR-01 | The agent writes its own decision state. It can send `jev_result_accept`, `fast_accept`, `route`, `authorize` and walk a decision to ACCEPTED with no Jev call. `jade ack` is self-report (W-81). The `observed` outcome that becomes a label is agent text. The hook does not read ACCEPTED, so the gate is safe; the harm is a false "verified" and dirty labels. [VERIFIED A2 section 3, sections 6, 13; A5 section 4; A6 section 3.2] | M to H, M, Decide |
| HR-02 | Labels come only from accepted calls, so the false-reject rate is unknown. On a synthetic 300-row set, `propose_threshold` proposed loosening 0.70 to 0.50. It also needs 200 distinct claims and 30 accepted rows. The live ledger has no labels, so it cannot fire today. The risk begins when labels exist. [VERIFIED by run, A6 section 4.1; R5 did not run it on a ledger] | H, M, Measure |
| HR-03 | Approval fatigue. 100 of 100 answers are allow. On 2026-11-13 the owner gave 18 terminal approvals in one afternoon, then stopped; 18 more asks expired (repo docs/12 section 1). `jade review confirm --all` takes every group after one "yes". 19 of 65 R-16 asks went unanswered. [VERIFIED A6, A7, A8] | H, M, Human |
| HR-04 | R-16 (writes to JADE's own code) and R-14 cause most asks. Part is the maintainer building JADE (1025 `safety_block` events in 14 days; "mostly" is [INFERENCE]). First friction fix: project-aware scope for these rules. [VERIFIED A6 section 2.1, A8 section 11] | M, S to M, Floor |
| HR-06 | One maintainer; agent-only review. No PR has a review decision. Tags are not signed. `jade upgrade` trusts one GitHub account. The owner is the only approver and labeler. PR 47 rounds 2 to 4 kept finding P0 regressions (W-108, W-109, W-111, W-113, W-115) and P1 gaps also on main (W-110, W-112, W-114): the review method is not converging. [VERIFIED A1; A8 section 8; A3 section 11] | H, M, Docs |
| HR-07 | Parser whack-a-mole. PR 47: four review rounds, about 4400 new tests, still weaker than main at its last committed state. Waves 4 to 6 of the shell-start-up guard are parked (W-98). Claude Code had at least 8 published parser-bypass advisories (R6, titles read). Each parser fix needs the OP-11 stop-loss measure first. [VERIFIED A1, R6] | H, n/a, Threat |
| HR-09 | The chat channel relies on host behaviour probed on one version (2.1.314): a hook rewrites `AskUserQuestion` and reads the answer. Docs disagree with the probe on hook `ask`. If the host changes, the ask may fail silently, and no CI job tests it. Whether `askUserQuestionTimeout` closes a JADE ask: [UNRESOLVED]. [VERIFIED R1 sections 3.12, 3.13; RESULTS.md] | H, M, Native |
| HR-11 | The fail-closed budget is also a friction risk. The hook budget is 6 s; a slow classifier fails closed for non-read-only calls. `NULL_REDIRECT` is quadratic on a long digit run (W-104 in the dirty backlog; on main W-104 is the owner page). [VERIFIED A3 section 8g] [INFERENCE A8] A whole hook costs about 100 to 150 ms; A8 did not time a full hook process on 0.6.1. | L to M, S, Floor |
| HR-12 | The continuous-flow skill batches up to 4 decisions in one popup, but `askchat.rewrite` replaces every question of the call with the one JADE question. [VERIFIED by code, A5 section 2b] User-visible effect not run: [UNRESOLVED] | M, S, Human |

**Smaller risks.** [VERIFIED A6 section 7.1; A8 sections 4, 9] HR-05: memory never ages by meaning (a silent use renews the 30-day life up to the 180-day cap), and a later Deny does not cancel an Allow on main (the deny-wins rule is in unmerged PR 44, `test_memory_newer_deny.py`; PR 45 adds `memory.honoured` and the honour test). HR-10: one alpha provider with a hard-wired URL; a new model string starts at default bands, so "new" means unvalidated, not stricter; provider data terms are not in the repo.

## PART D. Contradictions between docs, code and plans

S1: the owner may believe in safety that is not there. S2: misleads about a product claim. S3: cosmetic. [VERIFIED] in the cited audit unless noted.

| ID | Docs or plan say | Code or live state says | Source | Sev |
|---|---|---|---|---|
| CT-01, 02 | The task text says the gate fails closed on a push to a default branch. `jade check` says "ok level: off"; docs/12 section 2 lists `off` as a level. | At `off`, SI-08 is advisory (exit 0, "[advisory, not blocked]"), and so is every risk gate and SI-01 to SI-08. | A3 s1, s10 | S1 |
| CT-03 | repo docs/12 section 10: critical calls, fetch-and-run, instruction-file writes and merges "are asked too". | Memory covers critical (by class), merges and R-13/R-18 writes (exact). repo docs/12 section 9 contradicts section 10. | A3 s10; A4 s9.1 | S1 |
| CT-06 | repo docs/00 A2: the CLI gate is "harness-neutral". | No other harness is wired. `codex exec`, `aider`, `cursor-agent` are R-99 low. | A3 s10, s8e | S1 |
| CT-07 | repo docs/03 section 5 lists `evidence_source` as a state field, then says "Provenance kept in the ledger, not sent". Ambiguous. | `cli.py:74` puts `evidence_source` in the state sent to Jev. [RECOMMENDATION] Fix the doc or stop sending it. | A2 s11 | S2 |
| CT-08 | README: maintain job loaded; latency about 500 ms, budget "met". | `jade check` FAILs the job. Live Jev p50 712 ms, p95 1395 ms against 1.0 s. | A8 s10, s11 | S2 |
| CT-16 | `escalation.yaml` and registry keys (`abstain_to`, `max_revisions`, `fallback`, `each_question_includes`, `deadline_default_h`). repo docs/03 promises a recommended default and the cost of no answer. | No code reads these keys. The question shows neither. | A2 s7; A7 s7 | S2 |
| CT-04, 05, 09, 10, 17, 18 | Six smaller S2 claims: advisory "never blocks" (repo docs/08); gate `off` makes "every hook inert" (`jade check`); shadow ends after "50 distinct" calls (docs/12); a label means "the claim held" (repo docs/04); batched decisions (skill vs repo docs/07); the merge line mentions branch delete (#42 spec). | Each is wrong in code: SI-09 to SI-12 block in advisory; some hooks run at `off`; the count includes hints (FN-13); the label is agent-reported (SP-06); `askchat.rewrite` drops questions (HR-12); the line is plain (FN-07). | A4 s9.2, s9.3; A6 s9; A5 s2b; A7 s7 | S2 |
| CT-11 to 15, 19, 20 | Cosmetic items (Platt steps, hook count, registry count, event names, id collisions, a safe-direction "unverified" claim). | See the A2, A3, A5, A6, A8 tables. | A2 s11; A3 s10; A5 s5; A6 s9; A8 s12 | S3 |

[RECOMMENDATION] Extend `tests/test_docs_truth.py` to compare numbers with code (`len(ops.HOOKS)`, registry count, versions, declared keys against read keys). It checks only command names and threat rows today. `test_routing_policy_w53.py` already enforces "declared equals read" for `routing.yaml`; extend it to `escalation.yaml`.

## PART E. Blind spots in the threat model

Test: can a hook gate act here? A hook acts on a tool call it can read, in a session where hooks load. The rest is outside (R6 section 1.1). repo docs/05 has no row for most lines below (R6 grep).

| ID | Blind spot and what is known | Hook can enforce? | Proposed answer | Impact, effort, topic |
|---|---|---|---|---|
| BS-01, 02 | Config that loads before trust: repo `.claude/settings.json` hooks, `enableAllProjectMcpServers`, `.mcp.json`, repo `env` such as `ANTHROPIC_BASE_URL`; and hidden Unicode in `CLAUDE.md`, `AGENTS.md`, rules. Real advisories ran before the trust dialog: CVE-2025-59536 area, CVE-2026-33068, CVE-2026-21852, Codex CVE-2025-61260 (R6, advisory pages read). JADE only detects shadowed hooks. | Config: no. Instruction writes: yes. | A `jade check` and SessionStart row listing repo-controlled hooks, MCP servers, env keys and hidden Unicode. Rate config writes (FN-05). | H, S to M, Threat |
| BS-03 | Other harnesses (Codex, Cursor, Gemini CLI, Aider, Cline) have no JADE hook. Codex and Gemini hooks fail open; Cursor has `failClosed`; Aider has none (R1 section 5, via summary; Gemini [UNRESOLVED]). | No | Say so in docs (SP-12). Port only on the owner's word (D-8). | M, L, Native |
| BS-04 | Child agents. A child `claude -p` with `--settings '{"hooks":{}}'`, `CLAUDE_CONFIG_DIR=/tmp/c`, `--dangerously-skip-permissions` or `--bare` may run with no JADE hook. [VERIFIED A3 probe_child] The launch is R-99 low. [INFERENCE] The no-hook outcome; not run. A sandbox does not stop a child `claude -p` inside it. | Launch: yes, by rule | A rating rule for child launches. One owner probe of `--bare` and `--safe-mode`. | H, S, Threat |
| BS-05 to 07 | GUI tools (`computer-use`, MacOS-MCP, chrome `computer`) are not gated (BS-05, [VERIFIED A3 section 8a]). The MCP seal key is readable by MCP file readers (W-73), and tool-description changes are not seen (BS-06, R6 section 1.4). A mod can approve past a PreToolUse block when no managed settings exist and the login is not Team or Enterprise (BS-07, R1 section 3.9; status here [UNRESOLVED]). | No, partly, no | Name GUI tools an accepted residual risk, or drop them at high-risk levels (owner choice). Extend SI-10 to MCP readers by path. Owner opens `/plugin` once. | M, S to M, Threat and Native |
| BS-08 | JADE's own wiring. Hooks live in `~/.claude/settings.json`, which any same-user process can edit. Managed settings are root-owned. At `off`, R-13 and R-16 are advisory. `kill -9` of the hook host is not gated. [VERIFIED A3 sections 8a, 8b; R1 section 3.4] | Partly | A documented managed-settings file plus a `jade check` row. | M, S to M, Native |
| BS-09 | Persistence and egress outside tool classes: `~/.zshrc`, `authorized_keys`, `crontab`, `defaults write`, `git remote set-url`, `pip install --user`. Secret reads are "recorded, accepted risk" (T22). [VERIFIED A3 section 8b; my run] | By rule, partly | The sandbox covers credential reads and unknown hosts (OP-01). A rule per path is whack-a-mole. | H, M, Native |
| BS-11 | Remote backstop. JADE does not install the pre-push guard. Remote branch protection is a human step (W-68, W-69). Their state is unknown. Several vectors end here. [VERIFIED A3 sections 8b, 12] | Outside JADE | Two `jade check` rows. Name them in repo docs/05 as the last backstop. | H, S, Threat |

**BS-10, the human as a target.** Lies-in-the-Loop, whitespace padding, a clipped middle, option order, repeated identical frames (R6 sections 1.3, 2.1: attack pages read; habituation studies secondary). The hook cannot help beyond text, so FN-06 is the fix: show the distinct part first. Audit items only under the OP-05 budget. M, S to M, Human.

## PART F. Overlooked opportunities

Ranked by value over effort. Anything near an approval path is "careful": nothing from a spec, annotation or native answer may lower a risk level or answer for the owner (R4 section 10).

**OP-01 sandbox coverage, by class.** [INFERENCE, web page via summariser; R1] The OS sandbox limits Bash and its children: writes go to the working directory and temp; network goes through a hostname proxy.

| Class | Covered? |
|---|---|
| Reads of `~/.ssh`, `~/.aws`, the vault (with `sandbox.credentials` deny) | Yes |
| Network to unknown hosts (with a short `allowedDomains`) | Yes |
| `gh api .../merge` or `git send-pack` to an allowed host | No |
| Writes to `CLAUDE.md`, `.mcp.json` in the working directory via Bash | No |
| A child `claude -p` launched inside | No |
| Edit, Write, MCP, hooks, `excludedCommands` | No |

Cost: git over SSH fails through the proxy on macOS (R1). So the sandbox is strong for BS-09 and part of FN-05. It does not cure BS-04 or the rest of FN-05.

| ID | Opportunity and evidence | Need | Value, effort, topic |
|---|---|---|---|
| OP-01 | OS sandbox as the first layer: `failIfUnavailable`, `allowUnsandboxedCommands: false`, `sandbox.credentials` deny, short `allowedDomains`. No command parsing. Anthropic reports 84% fewer prompts internally, no method or sample size [INFERENCE: vendor figure via summary] (R1 and R6 read the page; R5 saw a snippet). | Yes (T22, T18) | H, M, Native |
| OP-02 | Native auto mode as the baseline for ordinary risk. Compare it with the Jev proxy before more proxy work. Auto mode: 0.4% false positives on 10,000 real calls; 17% misses on 52 real overeager actions, interval about 9% to 30% [INFERENCE: vendor figure via summary]. The proxy has 0 allowed decisions, no calibration, and sends data out. Availability here: [UNRESOLVED]. | Yes | H, M, Decide |
| OP-03 | Managed settings: hooks removable only by root; loads the mods guard; native deny rules no hook `allow` can beat (R1 sections 3.3, 3.4, 3.9). Needs sudo, and each JADE hook change then needs sudo too. | Yes (BS-07, BS-08) | H, M, Native |
| OP-04 | Replay of plain-high calls through JADE, the Jev shadow and auto mode. The JADE versus Jev-shadow part runs now with `evals/gate_replay.py`. The auto-mode part is BLOCKED: auto mode reads the live transcript, no evidence shows it can run offline, and replaying commands to an outside service sends owner data out. A live one-week A/B is the likely form. Probe first. | Yes | H, M, Measure |
| OP-05 | Random audit of silent allows, and honeypots. Fixes selective labels, measures attention. Zero misses in 29 seeded calls bounds approve-bad under 10% (exact, R5). **Proposal, needs owner approval.** Step 1: log time-to-approve and approve rate from the ledger; no fake asks. Step 2, only if the owner agrees: at most 1 audit item per 20 real asks; honeypots on a path that never runs even if approved; flagged so they stay out of labels; varied so the owner cannot learn them. [INFERENCE] At about 7 asks a day (A8), 29 fakes at that rate need about 580 real asks, about 83 days. | Not yet | H, M, Measure |
| OP-06 | `jade check --json` now (MG-12 shows the need). OTLP export later: no collector exists (R4 section 7). | `--json` yes; OTLP no | M, S, Docs |
| OP-10 | CI that matches the claims: macOS job, gitleaks (config exists, unused), SHA-pinned actions, `permissions:` block, tag-on-main and green-CI checks in `release.yml`, `timing` mark on wall-clock asserts (A8 sections 7, 8). | Yes | M, S to M, Docs |
| OP-11 | A `tree-sitter-bash` differential test, dev-only, on the existing corpora (R3 section 6.3). It only measures; coverage against `shellnorm` is [UNRESOLVED]. It gives the stop-loss number for HR-07. | Yes (HR-07) | M, M, Threat |

**Smaller items.** OP-07: native `defer` for headless `-p` runs (R1 section 3.1; not probed; Native). OP-08: an `if` filter on the hook entry; [INFERENCE] A8's 100 to 150 ms per call was not measured on 0.6.1, so measure once with a throwaway HOME before using it as a reason. OP-09: SKILL.md and AGENTS.md checks (R4 section 6); `license` and `compatibility` fields wait for a public release. OP-12: a threshold scan and one-parameter Platt fit (R5 sections 3.2, 5.7), not before labels exist. None has a shown need today.

## PART G. Measurement gaps

The goal says "measurable decision accuracy". These gaps stop it. Sample sizes come from R5's scripts (exact or confidence-interval numbers); I did not rerun them.

| ID | Gap and evidence | Impact, effort, topic |
|---|---|---|
| MG-01 | Zero labeled rows. No `calibration/current.json`. All bands (0.70, 0.30, 0.85) are generic defaults. [VERIFIED A6 section 5; A2 section 2] | H, M, Measure |
| MG-02 | The label is not decision correctness (SP-06, HR-02). Verify calls are not tied to decisions (165 decisions, 0 calls). Proxy decisions are never labeled. `review mark` writes events, not labels. [VERIFIED A6 sections 3.2, 5] | H, M, Measure |
| MG-03 | No shadow agreement metric: nothing compares Jev's shadow with the owner's real answer, so the 50-count gate cannot show deny recall. About 97 owner denies bound deny recall within 10 points; at a 7% deny rate that is about 1,370 answered prompts. The 7% is Anthropic's rate, not this owner's. The owner's rate is [UNRESOLVED]. [VERIFIED A6 section 5; R5 section 4.4] | H, M, Measure |
| MG-04 | No sample size is stated for any target. About 300 clean accepted rows show false-accept under 1%; 150 for 2%; 60 for 5%. At about 28 accepted calls a day that is about 11 days if every accepted call were labeled. The labeled share is unknown, so the real time is [UNRESOLVED]. The protected split needs about 1000 labeled rows. [R5 section 5.4, script; R5 line 168] | M, S, Measure |
| MG-05, 06 | Intervals assume independent rows, but rows in one session are correlated, so intervals are too narrow. Canary rollback has no minimum n (false alarms 23% at n=5, misses 44% at n=5). Promotion rules fail closed, so they are safe, but can only ratchet toward fewer accepts. [R5 sections 3.3, 4.1, 4.2] | M, M, Measure |
| MG-07 | Human load is not measured: no time-to-approve, no prompts per hour, no count of expired asks. `jade review` uses jargon ("silent share", "plain-high", "95% bound"). [VERIFIED A7 section 5; A8 section 11] | M, S, Measure |
| MG-08 | No control group and no task-quality metric. Nobody measures task success or time with and without the gate. Terminal-Bench 2.0 (89 tasks) detects only about 9 to 13 point changes. [R5 sections 3.6, 5.1] | H, L, Measure |
| MG-09 to 12 | Latency claims do not match live data (CT-08). No drift alarm on the spread of P; each model change restarts the 200-label clock. One owner, one machine: results will not transfer (R5 section 3.6). `jade check` and `jade ledger stats` are not machine-readable. [VERIFIED A8 sections 10, 11; A6 sections 6.1, 7.2] | M, S, Measure and Docs |

**The smallest metric set that can run now.** [RECOMMENDATION] The full "supervision cost per correct and safe unit" waits for labels. Start here.

| Metric | Source | Runs today? | Acceptance test |
|---|---|---|---|
| Asks per day, by rule | ledger | Yes | `jade review` prints it, no jargon |
| Time to answer; expired asks | ledger | Yes | printed per week |
| Owner denies that the Jev shadow would have allowed | ledger | Not until about 97 denies exist | count shown with its interval |
| Agent commits the owner later reverted or reset | `git log` | Needs a small script | on one repo, count matches a hand count |
| Honeypot catch rate | OP-05 step 2 | No; needs owner approval | at most 1 audit per 20 asks |
| Task quality | none | [UNRESOLVED] "no metric yet" | the owner picks a measure (MG-08) |

## PART H. What not to build, and when to build it

Reasons are in the cited rows. The order of work is in Part A.

| Do not build | Build only if |
|---|---|
| A JADE-owned runtime or scheduler; a retry counter for repeated tool failures (SP-11) | A host feature is shown not to cover a real stuck case; one real incident |
| Levels MEDIUM-HIGH and HARD; a MAXIMUM profile; preset states (SP-03, SP-04) | The owner defines them (D-4). MAXIMUM never. |
| DEFER_WITH_PLAN as a state; reviving `WAITING_DEPENDENCY`; auto-opened work items beyond the expired-permit case (SP-08) | Ledger data shows how often asks stall |
| A model-written SAFE_ALTERNATIVE or question; a Jev "explain what is missing" feature; a static SAFE_ALTERNATIVE table; a typed `Verdict` view (SP-09, SP-10) | Never model-written. The table only if the owner asks. The view only if a metric uses it. |
| OPA, Cedar or another policy engine in the hot path; a second allow-list engine; a JADE mod; an Elicitation hook; an MCP server, MCP proxy or A2A work | A named need the current hooks cannot meet |
| OTLP export (OP-06); `license` and `compatibility` fields (OP-09); `jade setup --managed` (BS-08); `jade quickstart` (SP-12) | A collector exists; a public release; the manual managed file proves too hard; public use becomes a goal (D-8) |
| Platt or isotonic fitting, off-policy estimators, a threshold scan (OP-12); honeypot machinery (OP-05) | Labels exist (MG-02, OP-05); step 1 of OP-05 shows a real attention problem and the owner approves the budget |
| More shell-parser waves (HR-07); `tree-sitter-bash` (OP-11) | OP-11 gives a stop-loss number; only as a dev-only test dependency, after owner agreement |
| Batching of asks; per-project levels; a second-harness adapter (BS-03) | A measured complaint; a shown need (project scope for R-16 and R-14 is a rule change, not a level, HR-04); the owner names another harness (D-8) |

[INFERENCE] The audits show a mature deterministic core and a thin learning layer built ahead of data. Spend first effort on the floor and the labels. Raising autonomy first would make today's 100-of-100 "allow" history the training signal.

## PART I. Decisions only the human can make

Each has a recommended default. Each is a safety or scope choice.

| ID | Decision | Recommended default |
|---|---|---|
| D-1 | Level `off`. (a) Keep it advisory and only warn in `jade check`. (b) Make SI-08, R-13 and writes to JADE's wiring block at `off`. | (a) now, with the FN-04 text. For (b), first run `gate_replay` to count extra asks and fix R-16 project scope in the same change. The owner chooses after the count. |
| D-2 | Never remember critical calls? | Yes. Add env prefixes that choose the target, and the cwd, to the class key. |
| D-3 | Chat grant key: `plan_hash` for all, or `hold_key` plus env prefixes plus cwd? | `plan_hash`, after `gate_replay` counts the extra asks. |
| D-4 | Keep the level names? Define MEDIUM-HIGH and HARD, or drop them? Add `jev=on\|off`? | Keep the names. Ask what MEDIUM-HIGH and HARD mean before dropping. Add the key only if the owner wants Jev off. |
| D-5 | Jev proxy: (a) freeze at shadow until a deny-count gate and a JADE-versus-shadow replay exist; (b) retire it. | (a). Move to (b) if, with about 97 denies logged, shadow still misses denies. Keeping it costs provider data exposure and upkeep. |
| D-6 | PR 47: 0.6.2 after a clean round 5, fold into 0.7.0, or a narrow patch plus the remote backstop? | [SUPERSEDED by d13 Q-02: merge PR 47 first after one clean independent review round, ship in 0.7.0, no 0.6.2.] Older default: round 5, then 0.6.2 only if clean; else the narrow patch. Never merge `c6ab27a`. |
| D-7 | Renumber dirty work items from W-117 up. Who keeps `T30`? | Renumber. PR 48 is merged, so PR 42 renumbers. |
| D-8 | Does the owner use a harness other than Claude Code? Is public use a goal? | Claude Code on macOS, owner-first, until told otherwise. |
| D-9a | Turn on the OS sandbox? | Yes, after a throwaway-HOME dry run and one week of prompt counts before and after. |
| D-9b | Install managed settings (sudo)? | Yes, as a separate later step with its own before and after count. |
| D-10 | Language of human text. | Simple English only. Persian only on the owner's word, from static tables. |
| D-11 | Is the remote default branch protected? Is the pre-push guard installed anywhere? | Make both `jade check` rows; the owner confirms the remote state. |

## PART J. Unresolved

- [UNRESOLVED] The owner's original v6 spec text (A-2). How the chat and macOS dialog draw bidi, long and Persian text (FN-06): one live probe.
- [UNRESOLVED] Whether the mods guard loads here; whether `--bare` or `--safe-mode` skip managed hooks; whether `defer` works on this host; whether auto mode exists through this gateway and how to measure it; whether `askUserQuestionTimeout` breaks a JADE ask.
- [UNRESOLVED] Whether memory re-runs a call after a nonzero exit (FN-08). The stray MODEL_DRIFT row, and the 18 non-hint answers (reads were blocked by SI-10; nobody routed around it).
- [UNRESOLVED] Whether PR 47's uncommitted fix covers every round-4 item, and whether refs moved after the audits.
- [UNRESOLVED] CI history for W-95 (a flaky SQLite test) and the lifecycle crash tests (skipped on this Mac). Real labeled yield per day, the owner's deny rate, Jev's accuracy on this owner's calls, any task-quality measure.
