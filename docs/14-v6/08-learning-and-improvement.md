# Learning and improvement design

This section answers one question: how can JADE learn from its own work so that humans supervise less, without any decision becoming less safe or less correct? It rests on A6 (repo audit of learning and calibration) and R5 (web research on evaluation and calibration), plus extra reads of `origin/main` at `e9412f3`, cited by file and symbol. Every claim has a tag: [VERIFIED], [INFERENCE], [RECOMMENDATION] or [UNRESOLVED]. Anything that changes safety behavior is a proposal and needs a human to approve it.

## 0. Refs, assumptions and the main message

**Which ref each claim is about.**

- A6 read `origin/main` at `8100ea1` (release 0.6.1). R5 and my reads use `e9412f3` (0.6.1 plus PR 48). For the learning files (`jade/evals.py`, `improve.py`, `memory.py`, `proxy.py`, `policies/promotion.yaml`) the two refs do not differ in a way that matters here. I call this "main". [VERIFIED] A6 section 0; R5 section 1.
- The unmerged 0.7.0 stack is cumulative. Memory scopes (`jade/scopes.py`; kinds `kind_project`, `kind_any`, `merge_project`) enter at PR 42 (`design-clear-questions`) and are carried by PR 43, 44 and 45. A deny-wins clause in the memory lookup SQL is already on PR 44 (`open-items-scopes`, `jade/memory.py`). `memory.honoured` and the proxy template v2 payload (deny sent as rule, answer and age only) are on PR 45 (`open-items-review-output`) only. None of it is on main. [VERIFIED] `git grep` of those refs; A6 sections 0, 7.1.
- The live numbers (zero labeled rows, 100 answers all allow, 82 hints, "67 of 50", level `off`) are a snapshot of installed `jade 0.6.1`. They describe the data, not the code. [VERIFIED] A6 sections 1, 2.1, 5.
- Work-item numbers collide across branches. W-116 lives only in the uncommitted main-checkout `BACKLOG.md` and `work-items.jsonl`; a reader on any ref will not find it. [VERIFIED] A1 line 175; A3 section 6.

**Settled assumption (reversible).** "v6" means the 0.6.x line, main after it, and the unmerged 0.7.0 candidate stack.

**Four terms in plain words.** A Wilson bound is a safe upper limit on a rate, computed from a small sample. A false allow is a call JADE let through that should not have run. Deny-recall is the share of the owner's real denies that Jev also denied. A silent allow is a call that ran with no question to the owner, because memory or Jev decided.

**The main message.**

1. JADE has a lot of learning machinery and almost no learning data. The live ledger has zero labeled rows. No threshold was ever calibrated. No template was ever promoted. [VERIFIED] A6 section 1.
2. Most of what was asked (blocker classification, recurring-failure detection, regression-test proposals, policy recommendations) is counting plus a report for a human. Build it that way: deterministic, report only, human decides.
3. Only three behaviors really learn: memory of confirmed answers, Jev deciding in the owner's place, and thresholds. Each needs labels that JADE does not yet collect in a valid way.
4. The biggest risk is bad labels, not a missing algorithm. Labels exist only for accepted calls. Outcomes are reported by the agent. The owner has answered "allow" 100 times out of 100. [VERIFIED] A6 sections 3.2, 4.1, 5.
5. The owner's time is the scarce input. Every sampling idea here draws on it, so section 3.4 sets one budget and section 9 names the five numbers that decide success.

## 1. What exists, and the honest state

### 1.1 Built parts and live state

- **Labels** have a source: `observed`, `adjudicated`, `heuristic`, `user_correction`. `observed` and `adjudicated` are written and may fit. `evals.auto_label` also writes `heuristic` ("accepted, then rejected with new evidence in the same session"), but `FIT_SOURCES` excludes it. Nothing writes `user_correction`. [VERIFIED] `jade/evals.py` (`FIT_SOURCES`, `auto_label`); A6 sections 3.2, 3.5.
- **Splits** by session hash (dev 50, calibration 20, validation 15, protected 15 percent) and **metrics** (Wilson 95%, Brier, log loss, ECE, reliability table, risk-coverage curve, paired bootstrap, Holm). [VERIFIED] R5 section 2; A6 section 4.
- **Threshold proposal** needs 200 distinct labeled claims, scans accept 0.50 to 0.95, and picks the lowest value whose Wilson upper bound on false accepts is at most 0.05 with at least 30 accepted rows. **Pipeline** `offline_replay, shadow, canary, promote` with a protected check (human terminal, at least 150 rows) and kill switch `FREEZE`. Shadow and canary exist for templates only. `promote` recomputes every verdict from stored evidence; a rollback cannot loosen a promoted threshold for an agent. [VERIFIED] A6 sections 4, 6.
- **Memory** of confirmed answers (a human Allow is a `hint`; `jade review confirm` makes it `strong`) and the **Jev proxy** (shadow until 50 distinct answered calls). Both are inert at level `off`. `jade maintain` runs `improve.mine` daily and writes counts only. [VERIFIED] A6 sections 2, 2.1, 8.
- **Not built:** Platt and isotonic calibration, conformal thresholds, inter-annotator agreement, session-clustered intervals, a random audit of silent allows, off-policy estimators. [VERIFIED] A6 section 9 item 1; R5 sections 1, 2.
- **Live state:** no labeled rows and no `calibration/current.json`; 0 of 165 decisions have a provider call attached; the 136 verify calls are not tied to decisions. The proxy gate reads "67 of 50", but all 100 answers are allow, 82 are hints, and 46 + 31 come from two rules (R-16, R-14). 48 Jev proxy calls are in the ledger; none labeled; `review mark right|wrong` writes `events`, not labels. [VERIFIED] A6 sections 1, 2.1, 5.

Today JADE can say what it counted. It cannot say how often it was right.

### 1.2 Problems in the threshold pipeline, and what to do

R5 found five weaknesses by synthetic scripts with independent rows; they show the shape of a problem, not this owner's rates. I re-read `origin/main` `jade/improve.py` before deciding.

1. **Labels exist only for accepted calls**, so a fit cannot see below the accept line. On 300 synthetic rows, all P at or above 0.70 and 97% true, `propose_threshold` returned accept 0.50 with upper bound 0.0474: "loosen" with no data between 0.50 and 0.70. [VERIFIED by run] A6 section 4.1.
2. **The replay pass rule is hard to reach.** A stricter challenger (0.80 against 0.70) passes in 5.5% of runs at n=200 and 55% at n=1000. [VERIFIED by script] R5 section 4.1.
3. **The threshold search picks the luckiest of up to 46 correlated values on the rows it tests.** When it returns a value, the true rate was above 5% in 3 of 18 cases at n=1000 (small counts). [VERIFIED by script] R5 section 4.3.
4. **`_non_inferior` compares point estimates**: a champion at 5% and a challenger at 8% pass this one check in 18% of runs at n=150. [VERIFIED by script] R5 section 4.2. It is not the only check. `safety_verdict` rejects any challenger with a higher raw false-accept count, on replay and on the protected split. `replay_verdict` also checks the validation split and `worse_subgroups`. [VERIFIED] `improve.py`. So the combined gate is stricter than 18%; by how much I did not run. [INFERENCE]
5. **The canary trigger has no minimum sample.** It fires on 5 rows when nothing is wrong (23% of runs) and misses a real regression (44% at n=5). [VERIFIED by script] R5 section 4.2.

**Decision: do not loosen any replay, protected or canary rule in this cycle.** [RECOMMENDATION, needs human approval] My first draft proposed a 0.03 margin on a paired difference. Review showed it lets a challenger be 3 points worse against a 5% budget (60% of it), borrows 0.03 from the canary trigger (a different purpose), and would need changes to `safety_verdict`, `replay_verdict`, the subgroup test, `policies/promotion.yaml` and the tests that pin them. Section 7 says a loosening is not reachable within a year. The rules fail closed, the safe direction.

Worth doing now, all tightening or reporting only:

- The threshold search never proposes a value below the lowest P that has labels. This fixes problem 1 and only removes proposals. [RECOMMENDATION, needs approval: it edits `evals.propose_threshold`]
- Add "not enough data" as a named result in `jade eval` and `jade review` for the replay, protected and canary stages, with row count and distinct-session count. No pass or fail rule changes. [RECOMMENDATION]
- Leave the canary trigger alone. It errs toward rollback; a minimum sample would make it fire less, which is a loosening.
- A later redesign must list every gate it touches, call itself a loosening, and use a margin the owner picks as a small share of the budget. It waits for audit labels. [RECOMMENDATION] R5 sections 4.2, 4.3, 5.4.

## 2. The distinctions the system must make

| Distinction | What JADE does today | Where it breaks | Design rule |
|---|---|---|---|
| Repeated execution vs verified correctness | Run and verified are kept apart (SI-07). Memory ignores outcomes; each silent run moves `last_used_ts` forward. | A call run 100 times unseen looks "proven" and lives to day 180. [VERIFIED] A6 sections 3.1, 7.1 | A repeat is never evidence. Only an audited sample is. |
| Execution success vs good decision | The label is the decision status SUCCEEDED or FAILED; the agent reports the outcome. | An action can succeed while the claim was false. [VERIFIED] A6 sections 3.2, 9 item 3 | Three label types, never merged: claim-true, owner-would-allow, harm-observed (R5 section 5.2). |
| Context similarity vs authorization equivalence | The class key is the winning rule plus normalised segment keys; cwd is ignored. | "Same text" can mean another project. Critical rules R-01, R-03, R-04 (`level: critical` in `policies/risk.yaml`) are class-eligible, because `memory.eligibility` never reads `a.level`. A3 section 6 confirmed it at runtime. | Similarity finds candidates; it never authorizes. Authorization is a declared scope with its own audit count. |
| User preference vs security policy | Preferences go only to Jev as state. Policy sits in protected files. | The bands 0.70 and 0.85 are uncalibrated. [VERIFIED] A6 section 3.4 | A preference may make JADE ask less only inside what policy allows. |
| Correlation vs evidence | Heuristic labels are excluded from fitting. Synthetic cases may not set thresholds. | The "wrong rate" in `jade review` uses only runs the owner chose to mark. [VERIFIED] A6 sections 3.5, 7.2 | A number from a non-random sample is labeled "of marked runs" and feeds no gate. |

[VERIFIED] The W-116 code fact: `origin/main` `memory.eligibility` (lines 48-65) has no level check, and `NEVER_RULES` is R-21 and R-22 only. `open-items-review-output` `eligibility` also has no level check, so merging PR 45 does not close W-116.

**Safety and autonomy are two separate inputs.** [RECOMMENDATION, needs human approval]

1. A **deterministic risk class**, set by humans in policy from the `reversibility` and `blast_radius` fields that `policies/risk.yaml` already holds per rule (R-13: reversible, host). Learning can never change a class. An irreversible or critical call is never class-eligible. A rule id is not a risk class. [UNRESOLVED] The full `blast_radius` vocabulary and where to draw the lines; I did not read it.
2. An **evidence gate** that only decides whether a scope inside an allowed class may run silently. It reads audited samples and nothing else.

The false-allow budget scales with the cost of a wrong allow: 5% for a reversible, narrow call; 2% or 1% for wider or harder-to-undo calls (section 7). A confirmed wrong allow in a costlier class suspends that scope and holds every scope in the class until a human looks.

[INFERENCE] One thread runs through all five rows: JADE treats "the owner clicked allow" as information about correctness. Anthropic reports users accept 93% of manual prompts and calls this approval fatigue ([INFERENCE: vendor figure via summary] R5 section 3.5; one page read through a summariser; the post says the figure reflects practice, not a formal study). This owner allowed 100 of 100. An approval is a hint, not a label (repo `docs/12`).

## 3. The selective-labels problem and its fixes

### 3.1 Which calls give which labels

| Path | Free label? | Unlabeled cell |
|---|---|---|
| Call asked to the owner | Yes: Allow or Deny (owner-would-allow). Noisy if the owner is tired. | None, if the owner is attentive. Measure that first (3.3). |
| Call run silently by memory | No. It is only a reuse. | All of it: needs an audit sample. |
| Call allowed by Jev above its band | No. | All of it: needs an audit sample. |
| Call Jev denies or abstains on, so the owner is asked | Yes. | None. |
| `verify_claim` accepted, action ran | Yes, through the outcome (harm-observed), if linked and independent. | Rejected and abstained answers. |
| Blocked by an SI-xx rule or human-only class | Not needed: deterministic. | Not sampled. |

[INFERENCE] In shadow the owner is still asked every time, so Jev's P can be compared with the owner's answer at no extra cost. The problem starts when Jev or memory decides alone. Then the only unlabeled cell is "silent allows".

### 3.2 The fixes (proposals; each rate needs the owner's approval)

1. **A staged audit of silent allows with a stored probability** (plan in section 7). The hook draws the sample, never the agent. Each silent allow gets a stored sampling probability p. Weight each audited row by 1/p for an unbiased false-allow estimate. Sample by scope so rare risky scopes are drawn more. The audit is a fixed weekly count (3.4), not a fixed share, so more autonomy does not mean more owner work. [RECOMMENDATION] R5 section 5.2
2. **Counterfactual review of rejected learned answers**: sample some and ask "should this have run?" without running anything. Lower priority: the proxy path already gets free owner labels for calls Jev does not accept. [RECOMMENDATION] R5 section 5.2
3. **Session counts.** Rows in one session are correlated and the Wilson code treats them as independent, so intervals are too narrow. [VERIFIED] R5 section 4.4. Print the distinct-session count beside every n. A result from fewer than 10 sessions is shown as a count, not a rate. [RECOMMENDATION] (10 is my start value)
4. **Schema.** One new label source (`audit_silent_allow`), one nullable `sample_prob` column, a label-type field on `labels`, and one small new table for the audit queue (draw, probability, status). Nothing else. [RECOMMENDATION]

### 3.3 Safety notes and attention checks (all proposals)

- The draw is a keyed hash of the decision id, made in the hook with a key the agent cannot read, so no stored seed can be read from the ledger. [UNRESOLVED] Which existing JADE key (for example the permit-signing key) fits; not checked. The audit queue sits in the protected family of SI-10 and SI-12.
- Labels and promotions are human-written (`jade label set`, `label auto` refuse an agent session, W-59). The check is `is_human_tty()`, which SI-09 and repo `docs/05` T2 call a heuristic: a command built at run time, or a script the agent writes first, is outside the syntactic block. A strong habit, not a cryptographic wall. [VERIFIED] `cli.py` lines 432-447; `policies/safety.yaml` SI-09.
- Audit labels and owner-would-allow labels stay separate types, so an audit finding is never counted as a harm outcome.
- **Audit findings suspend first.** One audited silent allow the owner calls wrong sends the scope back to asking before anyone checks the label. A human then adjudicates. It costs friction and keeps the safe direction.
- **Attention checks, two phases.** Phase 1 uses data JADE already has: time from question to answer, approve rate, repeat-approve patterns by rule. Show the owner their own approve rate and median time. Phase 2, only if phase 1 shows rubber-stamping and the owner approves: a honeypot, a question about a call that was never issued, so no tool call stands behind it. Tests must show that approving a honeypot cannot reach permit code, that the screen cannot look like a real permit, and that it never becomes a hint or strong row. Cost: a vigilant owner learns to spot fakes and trust may fall. Zero approvals of 14 gives a 95% upper bound near 20%; of 29, near 10% ([VERIFIED by script] R5 section 5.2). R5's alert (approve rate above 95% with median time under 2 seconds) is R5's own guess: calibrate it on the owner's data first.

### 3.4 The owner's time: one budget

[VERIFIED] A6 section 4.1: adjudication is capped at 10 labels a week (`ADJUDICATION_PER_WEEK`); 200 labels take about 20 weeks. That is a code limit, not a measured capacity. [UNRESOLVED] The owner's real weekly hours.

Every item that draws on the owner counts against one weekly budget: audit reviews, adjudication, hint confirmation, the proxy-gate review (6.6), release checks. The owner sets the number. [RECOMMENDATION, needs human approval]

1. Audit is a fixed weekly count per active scope with a stored per-call probability, not a share of silent runs.
2. Before a scope is audited, measure asks saved: how many asks its class key produced in the last 14 days. Enable it only if asks saved per week are at least 3 times the planned audit reviews per week (3 is my start value). Otherwise it costs more than it saves.
3. The rate of silent allows is unmeasured. [UNRESOLVED] Measure it from the ledger (`memory_allowed` events, Jev accepts) in build step 2 before any audit count is set. An earlier draft sized the audit from "28 accepted calls a day". That is [INFERENCE] (0.43 x 65 from repo `docs/04` section 4, a regex count over transcripts; R5 section 4.2 marks the labeled share [UNRESOLVED]) and counts `verify_claim` answers at P 0.70 or more, a different population. I use it only for the template canary (section 7).
4. Drop test-retest sets and kappa until a real label set exists: with one owner and under 100 labels they look exact and are not. Adjudicate every high-P row labeled "false"; label noise of about 3% (Northcutt; [INFERENCE, paper abstract only] R5 section 3.7) is the size of the 5% budget.

## 4. Controlled improvement

### 4.1 Three tiers

Only `calibration_threshold` and `question_template` have an evaluator; `promote` and `start_canary` refuse the other seven allowed targets. `policies/promotion.yaml` also lists `cache_ttl` as auto-allowed, but no evaluator exists, so code can never promote it. [VERIFIED] A6 section 6; repo `docs/07`; `promotion.yaml` lines 29, 37; `improve.AUTO_PROMOTABLE`. So I do not invent nine pipelines.

- **Tier 0, measure.** Counting and reporting. No change in behavior.
- **Tier 1, propose.** JADE writes a diff or work item with evidence; a human applies it in normal review. The default near safety.
- **Tier 2, evaluated change.** The existing pipeline for the two evaluated targets. I recommend `human_proxy` templates and all thresholds stay human-promoted, although `promotion.yaml` lets `question_template` auto-promote under opt-in. The proxy decides in the owner's place, so a template change is a change of authority. [RECOMMENDATION, needs human approval]

**Asymmetry rule** [RECOMMENDATION, needs human approval]: learning may move JADE toward asking more with no human and little evidence. It may move toward asking less only with a human at a terminal and audited evidence. Code already does part of this: a wrong mark retires a row, a rollback cannot loosen a promoted threshold, threshold promotion needs a human terminal. [VERIFIED] A6 sections 3.4, 6, 7.1.

### 4.2 The six mechanisms worth building

| # | Mechanism (tier) | Signal | Evaluation | Staged activation and rollback | Human approval |
|---|---|---|---|---|---|
| 1 | Context retrieval for Jev (1, code diff) | Owner allows and denies with rule and age (PR 45 v2: denies as rule, answer, age only). | Retrieval is code (`jade/proxy.py` `past_answers`, `MAX_PAST`), not a `question_template`, so the template pipeline cannot test or roll it back. Use a code diff plus a human-reviewed replay on stored proxy state: deny-recall and false allows on owner denies, old vs new, same rows. Not agreement: an always-allow model agrees 100% ([VERIFIED] A6 section 5). | Shadow until the deny gate (6.6). `git revert`. | A human merges. |
| 2 | Blocker classification (0) | `safety_block` and `would_block` events with rule or invariant id. The ledger does not record which invariant (W-74, [VERIFIED] A6 section 8). | Owner checks 30 sampled rows per release. | Weekly report of top reasons for asks. | None. |
| 3 | Friction report (0) | Per rule: asks, share answered allow, share a confirmed scope would have absorbed, audit result of that scope. | Measure below. | Report only. Start with per-rule ask counts and a human review of the top two rules (6.7). | A human edits any rule. |
| 4 | Calibration (2, threshold) | Labels of the right type, above and below the line. | Existing gates, unchanged (1.2). Brier and a reliability table first; ECE second, with its perfect-model floor. | Offline replay, protected check, human promotes. No canary. | Human terminal, always. |
| 5 | Regression-case proposals (1) | Every wrong mark, audit false allow, counterfactual false reject, honeypot approval. | The case must fail on the version that erred and pass on the fix (R5 section 5.1). Body is a redacted structured record. The benign twin is written by a human, or the case is marked "twin unverified" (a model-written label carries known error, 68.8% valid in ToolEmu, R5 section 3.6). Code never edits the protected corpus. | JADE writes a proposed diff; no auto commit. | A human merges. |
| 6 | Policy recommendations (1) | Per-rule counts: asks, allows, audit result, wrong marks. | Replay with `evals/gate_replay.py` and `ho_replay.py` ([VERIFIED] A6 section 8); human-only corpus must show zero flips. | Replay, shadow (at least 14 days with an audit sample), then a human edits the file (R-13 protected). | A human edits `risk.yaml` or `escalation.yaml`. |

**Mechanism 3 measure, from data JADE has.** Asks per decision split by rule; the share absorbed by a confirmed scope; the audited false-allow rate of that scope with its upper bound. Show friction and audited error side by side, never as one "unnecessary asks" count: an ask is unnecessary only if the owner's answer says so, and that answer is the hint the plan says is not evidence. The audit covers silent allows, not asked calls. [INFERENCE]

PR 45 ships question v2 as `shipped_champion` without this pipeline ([VERIFIED] A6 section 0). I treat v2 as running under shadow plus the human review of row 1; it is not calibrated (W-102 in the main-checkout working tree). [UNRESOLVED] Whether the proxy path of `improve.shadow` can evaluate a v2 template; I did not read it.

### 4.3 Where the value is

[INFERENCE] If only three things get built, build these: each turns an existing event into evidence with no new model.

1. **The proxy shadow agreement join.** Join Jev's shadow P with the owner's answer to the same exact key. Report owner-deny count, Jev recall on those denies, false-allow count. Nothing like it exists. [VERIFIED] A6 section 5.
2. **The regression-case ratchet** (row 5). Each real mistake becomes a permanent case that gates every pull request with no model call (R5 section 5.1, layer L-A).
3. **The staged audit of silent allows.** Without it no looser behavior can be justified.

### 4.4 Moved to "not yet", with the trigger that brings them back

| Mechanism | Why not now | Trigger |
|---|---|---|
| Recommendation quality | Follow rate is descriptive only: an anchored owner follows a default right or wrong ([INFERENCE, secondary source] R5 section 3.5); no decision depends on it. | Audit labels on asked calls exist and a question-text change is queued. |
| Reuse of verified strategies | No store and no "regretted" label producer exist ([VERIFIED] A6 section 8); agent-chosen text read back is an injection risk. | Same strategy fixes the same failure in 3 sessions; then only a human-approved playbook. |
| Recurring-failure detection | It adds a work-item producer to the owner's queue. | One command class keeps failing (hook-seen exit code); then a report row, not a work item. |

## 5. What learning may never change, and how that is enforced

### 5.1 The list [RECOMMENDATION, needs human approval to adopt]

Learning in any form may never change: (1) safety invariants SI-01, SI-02, SI-05 to SI-13 and their code; (2) human-only classes HO-xx, never remembered and never sent to Jev; (3) permit rules: who approves, human terminal, issuance and approval code; (4) thresholds and routing of critical calls (`abstain_to: human`, the critical accept floor); (5) the autonomy level; (6) a rule's risk class (`reversibility`, `blast_radius`); (7) the learning limits themselves: audit counts, minimum-data gates, this list; (8) the labels and the audit draw key.

### 5.2 Enforced today

[VERIFIED] unless marked. `improve.ALLOWED` is an allow-list and `propose` raises `ProtectedTarget` otherwise (`improve.py` lines 17, 56-58); `promote` recomputes results from stored evidence (lines 21-26). `policies/safety.yaml` protects itself, `promotion.yaml`, the overlay, `evals/*/protected/**`, permit and ledger code, hook config, `calibration/current.json` and `pins.json`; R-13 gates them and SI-12 blocks agent writes to the config directory. Threshold rollback cannot loosen for an agent, and `thresholds.bands` takes `max(accept, by_risk.accept)`, so risk only tightens (A6 section 6). `memory.eligibility` returns `never` for human-only classes, blocked calls, R-21, R-22 and `exact` for R-14, M-05, M-07, inline programs, merges, pushes, writes (`memory.py` lines 30-31, 48-65). `proxy.NEVER_RULES` is R-22, R-13, R-18; `skip_reason` refuses human-only, blocked, not-plain-high (so critical), unreadable, merges and calls over 4 KiB (`proxy.py` lines 43, 125-150).

### 5.3 Gaps

1. **Memory can generalize critical calls.** `memory.eligibility` never reads the level; R-01, R-03, R-04 are class-eligible, as are R-12 and R-15; cwd is not in the class key on main. The proxy refuses critical; memory does not. This is W-116 (P1), confirmed at runtime. PR 45 does not fix it. [VERIFIED] A3 section 6 and line 336; `memory.py`; `policies/risk.yaml`.
2. **R-13 and R-18 may be remembered.** A6 section 3.4 says agent-instruction-file rules are never remembered, citing both `NEVER_RULES` sets together. The code differs: `memory.NEVER_RULES` is only R-21 and R-22; R-13 and R-18 are in `proxy.NEVER_RULES` only. [VERIFIED] `jade/memory.py:30`, `jade/proxy.py:43`. Whether a shell write to a protected instruction file reaches class eligibility, or is caught earlier by `a.blocked` or `a.ho_ids`, I did not trace. [INFERENCE: possible gap] [UNRESOLVED] Trace it before any level above `off`.
3. **No absolute floor under a promoted threshold.** `_band_ok` checks only 0 < accept <= 1 and reject < accept. `by_risk` floors exist only for `verify_claim` and `human_proxy`; `risk_triage`, `root_cause`, `routing` and `candidate_fit` have none. The only guards are the human terminal and a search that scans no lower than 0.50. [VERIFIED] `improve.py` lines 252-257; `policies/escalation.yaml`. Acceptable while a human promotes every threshold.
4. **Two allowed targets could lower escalation** (`escalation_heuristic`, `routing_heuristic`). No evaluator exists, so a human applies them by hand. [VERIFIED] repo `docs/07`.
5. **Use renews a memory row's life** to the 180-day cap. **A later deny does not cancel an allow on main**; PR 44 adds a deny-wins clause and PR 45 adds `memory.honoured`. [VERIFIED] A6 section 7.1; `git grep`.

### 5.4 Proposals to enforce the list in code [RECOMMENDATION, each needs human approval]

Two items now; the rest wait until these find a real mismatch.

1. **Close W-116 and check R-13 and R-18.** `eligibility` reads `a.level` and returns `never` for critical. Add R-13 and R-18 to `memory.NEVER_RULES` unless the trace in 5.3 item 2 shows they cannot reach it. Decide R-12 and R-15 with the owner. W-116 is a missing check, not drift between two lists; the two sets differ because they serve different jobs.
2. **A never-learn corpus test in CI.** For every human-only and adversarial case, assert memory returns `never` (or documented `exact`), `proxy.skip_reason` is not `None`, and no scope is offered. This catches a scope kind from PR 42 that quietly widens authority.

Deferred, with a trigger: one shared never-learn module (if the corpus test finds the two lists out of step); a closed-set-of-writers test and pin tests for new limits (when audit tables land); a floor in `thresholds.bands` (if a threshold is ever promoted without a human; it needs a value per family set by the owner); marking `escalation_heuristic` and `routing_heuristic` "tighten only" (when an evaluator is proposed).

## 6. Drift, stale knowledge, poisoning, overfitting, fatigue, the proxy gate

### 6.1 Drift

[VERIFIED] A6 sections 5.1, 6.1, 7.2: drift detection compares only the model string and question version. Nothing watches the distribution of P, labels have no decay, and a new group starts at the default band, which means unvalidated. A stray MODEL_DRIFT item (`RT-20261114123351`) probably comes from `ops.check_drift` using the real JADE home ([INFERENCE]; row not read).

[RECOMMENDATION] A new model string or question version starts in shadow, or at a stricter band, until it has its own deny-recall data. Add a weekly check of mean P and abstain share per group to `jade maintain`. Labels get a time window (start 90 days, my value). `check_drift` skips fixtures. Wilson and conformal guarantees hold only inside a stable window (R5 section 5.5). [INFERENCE]

### 6.2 Stale knowledge

[VERIFIED] A6 section 7.1: a row lives 30 days after last use, 180 days at most; each silent use moves the clock. A repo, host or branch policy can change underneath it.

[RECOMMENDATION] **Age by scope evidence, not by row.** An earlier draft dropped a row after 20 runs with no audit hit. That is wrong: with a 5% audit a row used 20 times has about a 36% chance (0.95^20) of no hit, so sound rows would drop by chance and the autonomy would vanish. Instead:

- A scope keeps running while its audited sample is clean and large enough (section 7).
- A row ages out by time (the existing 30 and 180 days) or by a context change: project root, branch policy or host. Put the project root in the class key by default; make `kind_any` an explicit per-rule opt-in with its own audit count.
- A `right` mark (ignored today on purpose, [VERIFIED] A6 section 7.1) is a positive path.
- Start with no new row limit. Measure first how many extra asks any limit would add. [UNRESOLVED] Needs ledger data from build step 2.

### 6.3 Poisoned feedback

The path has two steps, and the guard covers only the second.

1. The agent records the outcome with `decide.record_outcome`: `observation_type`, `success`, and free-text `verification_method` (scrubbed for secrets only). The caller session must be the one that opened the decision, which is the agent's own. [VERIFIED] `jade/decide.py` lines 84-122.
2. The human runs `jade label auto`, which joins accepted decisions with outcomes into labels. The terminal guard protects who writes the label row, not the truth of the outcome. [VERIFIED] `cli.py` lines 432-447.

What already holds: an estimated success never reaches SUCCEEDED (only an observed success does), so it never becomes an `observed` label. [VERIFIED] `decide.py` (`record_outcome` docstring); `evals.auto_label` SQL. Two gaps remain: the agent can set `observation_type="observed"` itself with no independent evidence; and `auto_label` labels every FAILED decision 0 whatever the observation type, so an estimated or wrong failure becomes a label. [INFERENCE]

**[RECOMMENDATION, changes how labels are made, needs human approval]** An `observed` outcome must name independent evidence: an exit code recorded by the hook, or a CI result reference. Agent text alone stays `estimated`. A FAILED label needs the same. A mismatch between the agent's claim and the hook's exit code is itself a finding for regression proposals.

Other poison paths: past labels sent to Jev are agent-written command text the owner saw cut at 120 characters ([VERIFIED] A6 section 7.2); keep the PR 45 v2 rule of sending denies as rule, answer and age only. Preference text goes to Jev as model input: keep the 4 KiB cap. Audit results must never be editable by an agent.

### 6.4 Overfitting

[VERIFIED] A6 section 7.1: memory is exact or syntactic, so nothing is fitted; the cost is more questions. [INFERENCE] The overfit risks are the threshold search and tuning a template to about 100 shadow samples. Use a separate validation split and at most one change per group per 28 days (my value). Public benchmarks must not tune anything ([VERIFIED] R5 section 3.6).

### 6.5 Confirm fatigue

[VERIFIED] A6 sections 2.1, 7.1: 82 hints wait. `jade review confirm --all` takes every group after one typed `yes`; the prompt shows the last row's label for a group "allowed Nx". Frequency is shown as a reason, and frequency is not evidence of safety. [RECOMMENDATION, needs human approval]

1. `--all` only for groups all below `high`. High groups one by one, with the full command text.
2. A cap on confirmations per sitting (start 10, my value).
3. Do not clear the 82 hints in bulk: review a random 10, and expire a hint older than 30 days that was never confirmed.
4. Show the owner their own approve rate and median time (3.3).

### 6.6 The proxy readiness gate counts distinct keys

[VERIFIED] A6 section 2.1: the gate is `COUNT(DISTINCT exact_key)` over answers not revoked. The key holds a git HEAD or content digest, so 50 pushes at 50 HEADs are 50 "distinct" calls. Hints and allows count. A wall of allow clicks passes.

R5 shows why this is wrong. If the owner denies 7% of prompts (borrowed from Anthropic's 93% figure, so an assumption about this owner, [UNRESOLVED]), 50 answers hold about 3.5 denies. Deny-recall within plus or minus 10 points needs about 97 denies: about 1,370 prompts at 7%, 960 at 10%, 480 at 20%. [VERIFIED by script] R5 section 4.4.

**[RECOMMENDATION, changes a safety gate, needs human approval]** Among confirmed (not hint) answers the gate counts: distinct kinds of call (class keys); at least 3 rules; owner denies, at least 30 and target 100; and the false-allow count on those denies, which must be zero. Zero misses in 30 denies gives a 95% lower bound near 90% on deny-recall; in 100, near 97% ([INFERENCE] arithmetic, 0.05^(1/n)). Print that number: it is the real strength of "shadow passed".

**Two ways to get deny data.** If the owner never denies, the gate stays shut. That can be the right answer, since the data cannot tell Jev from a yes-sayer. But there is a faster path.

- **Path A: wait for real denies.** No extra owner work. May take months.
- **Path B: one bounded owner review.** In one sitting the owner labels 30 to 50 real past plain-high calls from the ledger, chosen to include risky ones and calls the owner never saw (`would_block` events: 2,379 in 14 days, [VERIFIED] A6 section 8). Labels are kept separate, flagged "reviewed, not asked". Weaknesses: tiredness, my selection, a one-time sample. Cost: one sitting inside the budget of 3.4. [RECOMMENDATION, needs approval]
- **A corpus veto test, necessary only.** Run JADE's known-bad calls through the proxy in shadow: 242 adversarial and 171 regression lines on `e9412f3` (241 and 170 at `8100ea1`; repo `docs/04` lists 191 and is stale). Require that Jev vetoes or abstains. It can stop Jev being switched on; it can never switch it on. repo `docs/04` section 5 bars synthetic cases from setting a threshold, which this respects. [VERIFIED] line counts of `evals/adversarial/cases.jsonl`, `evals/regression/cases.jsonl`; A6 section 3.5.

### 6.7 The cheapest friction fix first

[INFERENCE] 46 of 100 answers come from R-16 and 31 from R-14 (A6 section 2.1, one snapshot): 77 of 100 asks sit in two rules. Before any sampling work a human reviews those two rules. Is the rule too broad? Should some of it be exact-key reuse? That may remove most friction with no learning at all. [RECOMMENDATION, tier 0]

## 7. Minimum data before any learned behavior is switched on

Numbers come from R5 section 5.4 (script output, independent rows, 95%) unless marked. Session clustering makes the real need larger ([INFERENCE] R5 section 4.4). Times assume 10 reviews a week ([VERIFIED] A6 section 4.1) and are upper-side estimates, since labels also arrive free from asks and outcomes ([UNRESOLVED] yield).

**Row 1 is a staged exposure plan, not a pre-condition.** An earlier draft asked for 59 clean audited silent runs before switch-on. That is circular: silent runs exist only after switch-on. [RECOMMENDATION, needs human approval]

1. **Stage 0.** Close W-116, trace R-13 and R-18, merge or port the deny-wins and scope fixes, measure asks saved for the scope (3.4 rule 2).
2. **Stage 1, limited exposure.** One scope only: a reversible, narrow class with at least 10 sessions of confirmed answers behind it. Review 100% of its silent runs after the fact, in batches. Cap total silent runs at 59. A wrong run suspends the scope; stage 1 restarts after a human fix.
3. **Stage 2, sampled.** After 59 clean reviewed runs (zero wrong, at least 10 sessions; "below 5%" at zero failures, [VERIFIED by script] R5), lift the cap and use the fixed weekly audit count with stored probability.
4. **Stage 3, costlier classes.** A scope in a costlier class starts at stage 1 with its own larger number (row 2).

| Behavior to switch on | Minimum data | Time at 10 reviews a week |
|---|---|---|
| Memory class reuse, reversible narrow class | Stages 1 and 2: 59 clean reviewed runs, zero wrong, at least 10 sessions. | about 6 weeks plus the time to collect 59 uses; use rate per scope is [UNRESOLVED] |
| Memory reuse, wider or harder-to-undo class (never critical, never irreversible) | 299 clean runs for "below 1%", or 149 for "below 2%" (R5, exact one-sided). | 30 weeks or 15 weeks |
| Wider scope (`kind_project`, `kind_any`) | Same numbers, counted separately; a narrower scope's evidence does not carry over ([INFERENCE] A6 section 3.3). | per scope |
| Jev decides at level low, plain-high | At least 100 shadow samples over 7 days (existing); 30 owner denies from at least 3 rules with zero false allows (about 90% recall bound), target 100 (about 97%); corpus veto test passes; honeypots 0 of 29 only if phase 2 of 3.3 was approved. | depends on the deny rate: at 7% and 30 denies, about 430 answered prompts ([INFERENCE]); or path B of 6.6 |
| Loosen a threshold for a group | Not this cycle (1.2). When reopened: 200 distinct labeled claims and 30 accepted rows at the tested value (existing); labels below the old line; a protected split of at least 150 rows (about 1,000 labeled rows to fill it, [VERIFIED] A6 section 4); 315 accepted rows to show "at most 5%" with 80% power at a true 2% (R5). | 200 labels: about 20 weeks. 1,000: about 100 weeks. |
| Tighten a threshold or ask more | One audited wrong silent allow suspends the scope. No statistics needed. | none |
| Promote a question template | 100 shadow samples, 7 days, protected 150 rows (existing); canary needs labeled accepted rows. | At a 20% canary share and about 28 accepted calls a day ([INFERENCE], heuristic source) the canary sees about 6 a day: about 17 days if every row is labeled, much longer in practice |
| Platt or isotonic; reading ECE; a numeric safety target | Platt or isotonic: not switched on ([UNRESOLVED] no primary source for the 500 and 1,000 rows in repo `docs/04`, R5 section 3.3). ECE: about 1,000 rows (a perfect model shows ECE near 0.07 at n=200, 0.031 at n=1000, [VERIFIED by script] R5). A target is never tighter than the data: 1% needs about 300 clean rows, 0.1% needs 2,995 (R5). | not applicable |
| Reports (mechanisms 2, 3) | None. Check precision on 20 to 30 sampled rows. | none |

**The challenge to the spec.** The goal says "fewer approvals". The first rows can open in weeks to months. Loosening a threshold from learned data will probably not be reachable within a year at this labeling capacity. The friction that can really fall is the friction from repeated asks: the two busiest rules (6.7), memory with scope aging and a staged audit, and the proxy once it passes a deny-based gate. Calibration is not on the critical path to autonomy; an honest sample is. A new `resolved_model` also restarts the 200-label clock ([VERIFIED] A6 section 4; R5 section 4.4), so changing the Jev model string should be a planned event.

## 8. Build order

[RECOMMENDATION] Each step needs its evidence before the next turns on behavior.

1. **Hygiene, no learning.** Close W-116 with a real `a.level` check (PR 45 does not). Trace and fix R-13 and R-18 in memory. Merge the PR 42 to PR 45 deny-wins and scope work, or port it, before any level above `off`. Fix `check_drift`. Add the never-learn corpus test.
2. **Instrument, no behavior change.** Per-rule ask counts and the review of the top two rules (6.7). Link verify calls to decisions by default. Require hook-seen or CI evidence for `observed` and FAILED labels (6.3). Add `sample_prob` and the audit source. Add the shadow agreement join. Print session counts beside every n. Measure the rate of silent allows and asks saved per scope. Add passive attention numbers (time to approve, approve rate).
3. **Staged audit, with owner approval of the weekly count.** Stage 1 for one scope (section 7). Honeypots only if step 2 showed rubber-stamping.
4. **Report-only mechanisms.** Blocker classification by rule id, regression-case proposals, policy-recommendation tables.
5. **One behavior at a time:** scope aging and the `--all` limit; the proxy at level low behind the deny gate; per-scope memory widening; human-promoted proxy template changes. Thresholds last, after the rules in 1.2 are reopened with audit labels.

## 9. What we measure, and when we stop

Five numbers define success. Start values are for the owner to set. [RECOMMENDATION]

| Measure | What it tells | Source |
|---|---|---|
| Asks per decision, by rule | Friction | ledger |
| Audited false-allow rate per scope, with the 95% upper bound and session count | Correctness of silent allows | audit queue |
| Deny-recall of Jev, with the lower bound | Whether Jev catches what the owner would deny | shadow join |
| Owner hours per week on JADE learning work | The cost of autonomy | budget in 3.4 |
| Approve rate and median time to approve | Whether the owner still looks | ledger timestamps |

**Stop rule** for the sampling programme. [RECOMMENDATION, needs human approval; numbers are my start values] Put every active scope back to asking, and pause new scopes, if any of these holds:

- Owner hours exceed the budget for 4 weeks in a row and asks per decision did not fall.
- One audited wrong silent allow appears in a costlier class.
- Fewer than 10 sessions back a scope that has been on for 8 weeks.
- Phase 1 attention numbers show rubber-stamping and the owner declines phase 2.

## 10. What NOT to build

- **Platt or isotonic fitting.** JADE needs a threshold, not a probability, and has hundreds of rows at best.
- **A conformal library.** The risk-control scan is about 30 lines if ever needed ([INFERENCE] R5 section 3.2).
- **Off-policy estimators** (IPS, doubly robust). The gate is deterministic, so there is no logging probability to weight by ([INFERENCE] R5 section 3.8). Shadow plus a random audit is the valid tool. **Prediction-powered inference** needs a random labeled subset, which the audit creates: audit first.
- **Test-retest sets, kappa, decoy rows, default honeypots.** No real label set yet. Trigger: at least 100 labels and a phase 1 finding.
- **Evaluators for the seven unevaluated targets**; a human applies them by hand. `cache_ttl` is auto-allowed on paper but unreachable.
- **Embedding or vector retrieval of past answers.** Fix retrieval with rule, project and age first (A6 section 7.2).
- **The three mechanisms in 4.4**, until their triggers fire.
- **A shared never-learn module, a writers test, a threshold floor, any loosening of replay, protected or canary rules** (5.4, 1.2).
- **Calibration of `verify_claim` thresholds this cycle.** They gate no autonomy; 136 calls are unlabeled and unlinked.
- **Auto-promotion beyond `question_template`**, any auto-promotion of a `human_proxy` template, and anything that edits `risk.yaml`, `escalation.yaml` or memory scope on its own.
- **Public benchmark adapters in the runtime.** One-time sources of test cases only, with licence checks (R-Judge has no licence file; AgentHarm asks not to train on it; [VERIFIED] R5 section 3.6).
- **Learning from the agent's own reports**, and **reinforcement by use.** A call that runs often is not a call that is right.
- **A generic "learning framework".** Add a stage only where a target has an evaluator.

## 11. Assumptions, decisions and open items

**Assumptions (reversible).** "v6" is 0.6.x plus main plus the PR 42 to 48 stack. Owner labeling capacity is about 10 items a week: the code cap, not a measured number. About 28 accepted calls a day is [INFERENCE], a 14-day heuristic baseline on one machine and one owner (R5 sections 4.2, 5.5).

**Decisions only the human can make, with the recommended default.**

| Decision | Recommended default |
|---|---|
| Adopt the never-learn list and its code enforcement (5.1, 5.4) | Adopt the list. Code: W-116 fix, R-13 and R-18 check, corpus test. Nothing else yet. |
| Keep replay, protected and canary rules unchanged (1.2) | Yes. Add "not enough data" reporting and the lowest-labeled-P floor on the search only. |
| Two-input model: human risk class plus evidence gate (2) | Adopt. Class from `reversibility` and `blast_radius`; never critical or irreversible. |
| Staged exposure for the first scope (7) | One scope; 100% review of the first 59 silent runs; then a weekly audit count. |
| Weekly owner-time budget and scope enable rule (3.4) | Owner picks the budget (start at the existing 10 a week). Enable a scope only if asks saved are at least 3 times audit reviews. |
| Honeypots (3.3) | Phase 1 passive measures only. Phase 2 only if rubber-stamping shows. |
| Replace the proxy gate with a deny-based gate; offer path B (6.6) | Deny-based gate; offer path B once and let the owner choose. |
| Aging by scope evidence; `observed` and FAILED labels need hook or CI evidence (6.2, 6.3) | Adopt both; no new row limit until asks added are measured. |
| Keep `human_proxy` template changes human-promoted (4.1); review R-16 and R-14 first (6.7) | Yes to both. |
| R-12, R-15 and R-23 memory eligibility | Decide with the owner; default: exact-only until audited (LD-06; R-23 added after A6 recommended it with R-15). |
| Stop rule numbers (9) | Owner confirms my start values. |

**Unresolved.**

- [UNRESOLVED] Real label yield per day, the real rate of silent allows, and the real owner deny rate. A 14-day measurement after build step 2 settles them.
- [UNRESOLVED] The strength of the 18 non-hint answers and the state of the two drift work items (A6 sections 2.1, 11).
- [UNRESOLVED] Whether the proxy path of `improve.shadow` can evaluate a v2 template; whether R-13 and R-18 shell writes reach memory class eligibility (5.3 item 2).
- [UNRESOLVED] Which JADE key signs the audit draw; the full `blast_radius` vocabulary for risk classes; the owner's weekly time budget; a kappa cutoff if a label set ever justifies one (R5 gives none).

- [UNRESOLVED] Method details of most cited papers: R5 read most as abstracts only. R5's script numbers are for synthetic independent rows, not this owner's rates.
