# 10 Decisions and questions

Status: Planning only. Nothing here is implemented. Requirements: V7-R126, V7-R10, V7-R11, V7-R101, V7-R102, V7-R104, V7-R141, V7-R143, V7-A88, V7-A89, V7-A124, V7-A126.

Tags: [fact] cites an EV id (`evidence/v7-plan/evidence-index.md`, 2026-11-16), a repo path or the Jev REPORT (`evidence/jev-models/REPORT.md`, "REP"); [assumption]; [hypothesis]; [unknown]. Ids: `H-nn` question for you (§4), `DM-nn` decided, `A-nn` assumption, `L-nn` later decision. v6 `Q-01..Q-12` ([v6 doc 13 §2](../../14-v6/13-deferred-and-open-questions.md)), REP `D1..D7` and each doc's own ids keep their names (§5).

## 1. How to answer (V7-R102, V7-R104, V7-A89)

- Answer H-01 to H-10 in one batch. Most important is first.
- Each answer covers only the parts named in that question. "Yes, except <part>" is fine.
- No answer means no. Silence or a timeout is never approval.
- A default is never applied on silence when it sends data out, spends money, gives power to a model, or pushes code.
- A probability is never permission. Only you set the JADE level. One exception, set by you (ADR-13, REP §9 row 10): at level low or medium Jev may allow a call inside its band, never for human-only, safety-blocked, R-13, R-18 or R-22 calls. v7 adds no exception; model agreement never widens it (REP rule 8). Its future is H-02.
- Never paste a secret here; keys go through `request_secret` into Open Vault ([08 §4](08-security-operations.md)) (V7-R15).
- Commands (V7-R105): `jade check`; `launchctl list | grep com.jade.maintain`; `claude --version`; `gh pr checks 47`; `git status --porcelain`. V6-15 probe: [v6 handoff Step 15](../../14-v6/14-implementation-handoff.md#step-15-v6-15-host-probe-session-owner-start-at-step-1), `evals/probes/hook_ask/README.md`.

## 2. Decisions already made (V7-R10, V7-R65)

These need no answer (V7-A131). Each has evidence. You may still object. "ADR v7-nn" means a candidate in [03 §9](03-reference-architecture.md).

| Id | Decision | Evidence |
|---|---|---|
| DM-01 | **Plane split.** JADE owns the decision lifecycle and blocker records. The host (Claude Code) owns execution, tools, retries and checkpoints. The policy plane (PreToolUse hook, `policy.assess`, permits) enforces at action time. Directive component 8 is planned on this split, not as an executor in JADE. | [fact] addendum; [03 §0](03-reference-architecture.md), ADR v7-04; [11](11-decision-lifecycle-and-contracts.md); EV-45 |
| DM-02 | Jev `typesafe/jev-1.14` stays the primary judge. No switch. | [fact] REP §0 items 1, 4; EV-30, EV-31 |
| DM-03 | The 41-model bench is exploratory. It cannot rank models. | [fact] EV-29, EV-30; [05](05-benchmark-design.md) |
| DM-04 | A second model may only veto, never allow. It starts in shadow. | [fact] EV-32, EV-34; ADR v7-03 |
| DM-05 | `typesafe/jev-router` is a chat router. It is never in JADE. Chat use goes through 9router only. | [fact] EV-21, EV-22; ADR v7-09; [06 §11](06-model-routing-strategy.md) |
| DM-06 | The deterministic layer stays custom. No OPA or Cedar. No durable-execution runtime core. | [fact] EV-46, EV-45; ADR v7-01 |
| DM-07 | v6 tasks V6-01..V6-34 are the base. Tag 0.7.0 holds the v6 fixes only, not v7. | [fact] [07 §2-3](07-implementation-roadmap.md); [v6 doc 12 §4.2](../../14-v6/12-roadmap.md) |
| DM-08 | PR 47 is not mergeable. R5-L4-1 (critical, SI-08) and CODE5-1 (important) are confirmed. W-115 stays open. | [fact] `evidence/round5-findings-c676801.json`; [07 §3](07-implementation-roadmap.md) |
| DM-09 | Every v7 behaviour sits behind a flag. Default off equals v6. Agents never set flags. | [assumption] [07 M2-b](07-implementation-roadmap.md); ADR v7-10 |
| DM-10 | Decision approval and action authorization stay separate. | [fact] exists today; ADR v7-08; [11](11-decision-lifecycle-and-contracts.md) |
| DM-11 | `~typesafe/jev-latest` is real. Today it resolves to `typesafe/jev-1.14-20261014`. Old docs are wrong; an agent fixes them (REP step 0a). | [fact] EV-18, EV-19 |
| DM-12 | Agent work with no human decision: threat row "a company sees the redacted state", retired model gives exit 5, date check in code, instruction-text flag (REP steps 0b, 0c, 0e). | [fact] REP §8 |
| DM-13 | No fallback model now. Only an exit-5 counter (REP step 9). | [fact] REP §8 |
| DM-14 | The gateway is 9router. OmniRoute is removed and stays removed. | [fact] EV-64 |
| DM-15 | Accepted: ADR v7-06 (context interface, local impl), v7-11 (logical route names). Waiting: v7-02 (H-04), v7-05 (A-07), v7-07 (A-05). Alternatives in 03 (V7-R134). | [assumption] [03 §9](03-reference-architecture.md) |

### 2.1 Owner answers, 2026-11-16

| Question | Answer | What it allows |
|---|---|---|
| H-01 | Go (all parts) | The agent prepares baseline B and M1 work as local branches at $0 provider spend. No merge, tag, push, paid call, flag change or level change without a new yes. |
| H-02 | Yes, the v6 questions still stand | Q-01 (scope) and Q-02 (PR 47 ships inside 0.7.0) were answered earlier. Q-02's merge waits for DM-08. |
| v6 Q-03, Q-04, Q-05, Q-06, Q-10 | Recommended answer (A) for all five | V6-06, V6-07, V6-10 text, the SI-11 line, the V6-08 batch. Each change shows its ask count first. |
| v6 Q-07, Q-08 | `jev on|off` switch plus the deny gate (A) | V6-28. Jev stays in shadow until 100 samples over 7 days and 30 owner denies from 3+ rules, zero false allows. |
| v6 Q-09 | Yes: 42, 43, 44, 46, then 45 after PR 47 | v2 stays out of the champion slot; the merge-line test shows `--admin` and `--delete-branch`. No tag between merges. |
| v6 Q-11, Q-12 | Yes to both (A) | Purge scrubs stored command text (cannot be undone); V6-09 safe text display. |
| H-03 | Yes (ZDR plus `only`) | REP step 0d may be built. The one live check call still needs a yes. |
| H-04 | Yes | Design note, then a pull request for the owner. |
| H-05 | A: stay pinned on `typesafe/jev-1.14` | The alias waits for the H-04 guard. |
| H-06 | Yes, the ceilings in H-06 | The ceilings are set. No paid run starts without its own yes (L-02). |
| H-07 | Only the owner labels | Weaker than two people: one person's mistakes are not caught. Record this limit in every result. |
| H-08 | Yes, as scoped | `baseten/mammoth-1`, verify questions only, built in M9. |
| H-09 | A: "v7" is only a plan name | Releases keep semver. |
| H-10 | A: keep the local commit only | Nothing is pushed. |

## 3. Assumptions and later decisions (V7-R11, V7-A124)

### 3.1 Assumptions

The plan uses these. Say "change A-nn" to override one.

| Id | Assumption | Tag, source | If wrong |
|---|---|---|---|
| A-01 | Contextum, Obsidian/Kanban and a Laravel/PHP or Vue/TS corpus are not present. They are out of scope. | [fact] EV-61..EV-63; U-11 | Add an adapter or corpus later |
| A-02 | Claude Code on this Mac is the only host. | [fact] EV-43 | A second adapter (DF-11) |
| A-03 | You commit W-96..W-116 from the main checkout before new ids. G7 rows enter `jade work` only after V6-01, V6-02, with fresh ids. | [unknown] U-08; EV-09 | Id collisions (G7-12) |
| A-04 | `com.jade.maintain` should be loaded. `jade check` (V6-10) confirms it. | [unknown] D9 in [01](01-current-state-audit.md) | Maintenance silently stops |
| A-05 | Ledger events get a new version; old rows stay readable. At rest: FileVault plus a `jade check` row. Signed daily digest once the signed backend is installed (ADR-08 revision). | [assumption] [03](03-reference-architecture.md), [08](08-security-operations.md) | Weaker audit trail |
| A-06 | Dev-only checks: `pip-audit`/OSV, gitleaks, SHA-pinned actions. No runtime change. | [assumption] [08](08-security-operations.md) | none at runtime |
| A-07 | One question list at session start. Each answer covers one call (`plan_hash`). JADE counts retries; the host retries. | [assumption] ADR v7-05, DM-01 | More asks |
| A-08 | A remembered answer covers only the exact same call at high risk or above. A deferred decision becomes a work item, not a new state. (Jev proxy: H-02.) | [assumption] [11 §2.3, §6](11-decision-lifecycle-and-contracts.md) | Wider memory or a new state |
| A-09 | Breaker fallback is the human, not a second vendor. Daily caps escalate, never allow. | [fact] REP §5.2 rules 5-6; [06 §6-7](06-model-routing-strategy.md) | none |
| A-10 | Learning: `auto_promotion_opt_in` never starts model, vendor, veto or routing canaries. Weekly review 30 min, about 20 rows. Veto canary share 0.2. Label window 90 days. | [assumption] [09](09-release-and-learning.md); EV-41 | Fewer labels, weaker claims |
| A-11 | Benchmark: MVP tier first. No router track now. Severity weights 20 / 1 / 0.25. `liquid/d1-ultra` is not scored on the holdout. | [assumption] [05](05-benchmark-design.md); REP D6 | Fuller bench costs more labels |

### 3.2 Later decisions (with trigger)

| Id | Decision | Trigger | Default then |
|---|---|---|---|
| L-01 | Veto budget (REP D7) | 3-4 weeks of shadow data plus REP step 6 | At most 10% of calls Jev would allow (REP D7) |
| L-02 | Benchmark run: prereg hash, vendor allow-list, holdout source, PC1-PC7 | Each run, at the execution gate ([05 §9](05-benchmark-design.md)) | ZDR-passing vendors, named one by one |
| L-03 | Pinned 9router chat model for DEEP (S2) and R0 | Before any S2 run | You name one; no measured candidate exists |
| L-04 | One saved probe each: jev-router via 9router, and 9router refusing Decisions (U-04) | M4 starts | Yes, one call each |
| L-05 | Does `claude --resume` keep the hook session id? | V6-15 host probe (M1) | [unknown]; treat as no |
| L-06 | First canary class | M10 | Verify-claim calls, one repo, level `high` |
| L-07 | Vendors beyond TypeSafe and the H-08 lane | M8 passes | One at a time |

## 4. Questions for you (V7-R141, V7-A88, V7-A126)

### H-01 Go or no-go for milestone 1

- **Decide.** May work start on baseline B and M1 ([07](07-implementation-roadmap.md))? M1 needs B first.
- **Why only you.** The directive forbids implementation without your explicit yes. You merge and tag.
- **A yes approves four parts.** (a) The agent prepares B pull requests in the [07 §3](07-implementation-roadmap.md) order. (b) The agent fixes R5-L4-1 and CODE5-1 on PR 47, then a clean independent round 6. (c) M1 work at $0 provider spend: V6-10, V6-12, V6-23, M1-a..c. (d) You run the V6-15 host probe in a new session.
- **A yes does not approve.** Any merge, tag, paid call, push, flag change, level change, or H-03..H-08.
- **Options.** Go (all parts). Go for B only. No-go: the plan stays as documents.
- **Default.** Go. M1 has no paid inference. Each step can be reverted. Every merge stays yours.
- **After.** B starts after H-02, then M1 (baseline pack for shadow comparison).

### H-02 Does the v6 owner batch still stand?

- **Decide.** Do v6 [Q-01..Q-12](../../14-v6/13-deferred-and-open-questions.md) still stand as written? Will you answer them before B?
- **Why only you.** They are safety and release choices. Seven P0 rows wait on them ([02](02-gap-risk-register.md) Q7-03).
- **Includes v6 Q-07/Q-08 (Jev proxy).** The proxy stays delegated authority (11 §6 OR-1), becomes veto-only ([09 §2](09-release-and-learning.md)), or goes off (02 G7-30). No answer keeps today's authority; it stays dormant at level `off`.
- **Options.** Yes. Yes with changes. No: the plan is redone.
- **Default.** Yes. One change: v6 Q-02 ("merge PR 47 first") stays blocked until DM-08 is fixed.
- **After.** v6 W-174 closes. V6-03 can start.

### H-03 ZDR and `only` on every Jev call (REP D1, D4)

- **Decide.** Add `provider: {zdr: true, only: ["typesafe"]}` (ZDR = zero data retention: the company keeps no copy) to every Jev call, plus a ledger provider check and alert?
- **Second part (D4).** You check your OpenRouter privacy settings. The agent drafts one retention question each for OpenRouter and TypeSafe (U-05).
- **Why only you.** It changes where your data may go. Calls can stop if TypeSafe loses its ZDR listing.
- **Options.** Yes. ZDR without `only`. No.
- **Default.** Yes. Today JADE sends no `provider` field. ZDR works on the Decisions route [fact, EV-33]. A stop gives exit 5 (safe), not a silent leak.
- **After.** REP step 0d, after 0c: fixture test, then one approved live call (about $0.00008 [estimate from EV-33]).

### H-04 Accepted-model list out of `registry/` (REP D2)

- **Decide.** Move "which models may decide for me" to a store only you change by a terminal command?
- **Why only you.** It decides who can give a model power.
- **A yes approves.** The design: protect `registry/` and `policies/models.yaml`; vendor in the calibration-group key (dropped today [fact, `jade/thresholds.py:37`]); a band floor for new snapshots; a dated-id test. (A new model starts at its family's strictest bar; each company is scored apart.)
- **Options.** Yes. No: an agent can edit the list.
- **Default.** Yes. Cost: one command per new model.
- **After.** REP step 1: design note, then a pull request you approve.

### H-05 `~typesafe/jev-latest` (REP D3)

- **Decide.** Which id does the gate call?
- **Why only you.** A moving alias changes your judge without a release.
- **Options.** (A) Stay pinned on `typesafe/jev-1.14`; each new id is a release. (B) The alias, only in the same release as the H-04 guard (G1-G3, [04 §8.1](04-model-provider-research.md)). (C) The alias alone: rejected.
- **Default.** A now. B only together with the H-04 guard, in one release. Today the alias is the same model (EV-18), so a switch gains nothing yet. A moving alias breaks calibration (EV-20). REP D3 defaults to B with the guard; A until G1-G3 ship does not contradict it.
- **After.** Docs are fixed either way (DM-11). The retirement alert and exit 5 (DM-12) cover the pinned risk.

### H-06 Spend ceilings

- **Decide.** Approve hard spend ceilings?
- **Why only you.** It is your money. A budget-versus-safety conflict is yours (V7-R113).
- **Ceilings.** Benchmark L2-M: $15 total, $3 per vendor, $5 per run. DEEP $5. Router track $10. L3: none ([05 §9](05-benchmark-design.md)). Shadow window $10. Canary week $5 ([07](07-implementation-roadmap.md)). Live gate: $1 per day ([08](08-security-operations.md)).
- **Options.** Yes. Lower numbers. No: no paid run can start.
- **Default.** Yes. The worst full estimate is $8.10 [estimate, 05 §9]. Live spend was $0.013842 in 14 days (EV-51).
- **After.** At a ceiling the runner stops and reports "not enough data". A stop never lowers a bar. This does not approve any run (L-02).

### H-07 Blind labelling of a fresh test set (REP D5)

- **Decide.** Will you and one other person label a fresh set, blind?
- **Size.** MVP: 259 holdout plus 259 calibration rows (518), each labelled by two people ([05 §9](05-benchmark-design.md)). REP floor: 100 true and 100 false claims, none reused, from real ledger or CI text (REP step 4).
- **Why only you.** It takes your time. Old cases were written with Jev, so they may leak (EV-30).
- **Options.** Two people. You alone (weaker). No.
- **Default.** Two people. You name the second. Two blind labellers remove the known leak.
- **After.** The agent exports redacted candidates. You label. Then REP step 6 under H-06 and L-02. Without labels, any switch or veto is a guess.

### H-08 Shadow lane on `baseten/mammoth-1` (REP D6)

- **Decide.** May a second vendor see verify questions in a quiet lane that decides nothing?
- **Why only you.** Your data goes to a new company. Baseten sees redacted tool output; ordinary text is not redacted. It extends your "Jev only" openrouter.ai exception.
- **A yes approves.** `baseten/mammoth-1`, verify questions only. A separate OpenRouter key with its own credit limit, stored in Open Vault. It needs the D4 part of H-03 first.
- **Not approved.** `would_allow`, Liquid, any other vendor.
- **Options.** Yes as scoped. No: there is no data to judge a second model, and L-01 can never be decided.
- **Default.** Yes as scoped. It only watches, so it cannot loosen any gate (DM-04).
- **After.** Built in M9, after M8 passes ([07](07-implementation-roadmap.md)). A veto waits for L-01.

### H-09 Name: "v7" plan versus the 0.7.0 tag

- **Decide.** What does "v7" mean?
- **Why only you.** You name releases.
- **Options.** (A) "v7" is only the plan name; releases keep semver. (B) Tag 0.7.0 as "v7": confusing, because 0.7.0 carries v6 work (DM-07). (C) 7.0.0: major means breaking; flags off breaks nothing.
- **Default.** A. v7 work ships flag-off in a later minor after 0.7.x ([07](07-implementation-roadmap.md)).
- **After.** The docs say "JADE v7 plan". No tag is created by this answer.

### H-10 Push the plan documents?

- **Decide.** Do the `docs/plans/jade-v7/` files stay local?
- **Why only you.** A push publishes them to the GitHub remote (EV-05). Today they are in one local commit on branch `plan-jade-v7`, not pushed.
- **Options.** (A) Keep the local commit only. (B) Push the branch, no pull request. (C) Push and open a pull request.
- **Default.** A. The docs name local paths and private evidence folders.
- **After.** For B or C: the agent first redacts local paths and copies the evidence index redacted ([01](01-current-state-audit.md)). Nothing is pushed without your word.

## 5. Where each doc's question went

Each doc's open-question table shows its local id with the doc-10 id beside it (for example `Q3/[H-06]` in [05](05-benchmark-design.md)). Those links are the map; they are not repeated here.
