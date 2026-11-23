# JADE v7 implementation roadmap

Planning only. Nothing in this document is implemented.

**Scope.** This document orders the work that turns the v6 plan into JADE v7. It is a delta on v6, not a rewrite: the 34 v6 tasks (V6-01 to V6-34) stay the unit of work, and each is placed once in one of the owner directive's 10 milestones (M1 to M10) or in the 0.7.0 baseline. New v7 work has local labels (for example M4-b), not registered work items; `G7-nn` rows are in [02](02-gap-risk-register.md). Requirements: V7-R03, V7-R67 to V7-R78, V7-R108, V7-R123, V7-R135, V7-A07, V7-A109, V7-A122.

**Split (V7-A07).** JADE owns the decision lifecycle and blocker records. The host owns execution, tools, operational retries and checkpoints. The policy plane (PreToolUse hook, `policy.assess`, permits) enforces at action time. Directive component 8 is planned on this split, not as an executor inside JADE. Contracts and states: [11](11-decision-lifecycle-and-contracts.md) ("doc 11").

## 1. How to read this

| Item | Meaning |
|---|---|
| Tags | [fact] cites an EV id (`evidence/v7-plan/evidence-index.md`, captured 2026-11-16), task FACTS of that date, or a repo path; citing an evidence file means "the file says so, not re-checked". [assumption]; [hypothesis]; [unknown]. |
| Effort | S, M, L from [12-roadmap §4.2](../../14-v6/12-roadmap.md); XL = several L. All sizes [assumption]. No dates. |
| Owner | A = agent plus independent review; H = human only; A+H = both. Owner-only actions: [14-implementation-handoff §10](../../14-v6/14-implementation-handoff.md). |
| Paid inference | "yes" = new provider calls, beyond today's Jev spend of $0.013842 per 375 calls in 14 days [fact, EV-51]. |
| Common rules | v6 task rules ([12-roadmap §3](../../14-v6/12-roadmap.md)) and global stop conditions ([14-implementation-handoff §9](../../14-v6/14-implementation-handoff.md)) apply to every milestone, plus each card's abort list. |

## 2. Milestone map (V7-R67, V7-R123)

| Milestone | v6 tasks (each placed once) | New v7 work | Paid inference | Owner |
|---|---|---|---|---|
| B: 0.7.0 baseline | V6-01, V6-02, V6-03, V6-04, V6-05, V6-06, V6-07, V6-09, V6-11, V6-13, V6-14 | none | no | A+H |
| M1 Instrumentation | V6-10, V6-12, V6-15, V6-23 | M1-a, M1-b, M1-c | no | A+H |
| M2 Contracts, flags | V6-34 | M2-a to M2-e | no | A |
| M3 Evidence, context | none | M3-a to M3-d | no | A+H |
| M4 Routing, registry | V6-28 | M4-a to M4-g | no (optional smoke: yes) | A+H |
| M5 Authorization | V6-08, V6-16, V6-17, V6-18, V6-25, V6-29 | none | no | A+H |
| M6 Verification | V6-24 | M6-a to M6-d | no | A+H |
| M7 Continuity, UX | V6-26, V6-27 | M7-a to M7-e | no | A+H |
| M8 Evaluation | V6-20, V6-21, V6-22, V6-32 | M8-a to M8-d | yes | A+H |
| M9 Shadow | V6-31 | M9-a | yes | A+H |
| M10 Canary, readiness | V6-19, V6-30, V6-33 | M10-a | yes | H |

**Build order.** [fact] v6 task order is fixed in [12-roadmap §4.1](../../14-v6/12-roadmap.md) and [14-implementation-handoff §7](../../14-v6/14-implementation-handoff.md). Topic and order differ: V6-06, V6-07 and V6-09 gate the 0.7.0 tag, so they sit in B; V6-23 (M1) needs V6-05 and V6-14. Also V6-28 (M4) needs V6-12 and V6-17; V6-29 (M5) needs V6-21 (M8). Order [assumption]: B; M1, M2; M3, M4, M5 start in parallel, V6-28 after V6-17; M6, M7; M8 (V6-21 early); M5 closes with V6-29; M9; M10.

## 3. Baseline B: the 0.7.0 train (prerequisite, not a v7 milestone)

| Field | Content |
|---|---|
| Merge order | PR 47 first, then 42, 43, 44, 46, 45 [fact: V6-04, V6-05, [12-roadmap §5](../../14-v6/12-roadmap.md)]. No tag between merges. |
| PR 47 gate | Not mergeable today [fact, `EVD/round5-findings-c676801.json`]: R5-L4-1 critical (G7-38) and CODE5-1 important (G7-39) are confirmed regressions vs BASE `8100ea1`; W-115 stays open. Merge only when: both fixed; round-5 grids (famA, famG) show zero weaker-than-BASE rows; disputed R5-DIFF-1 settled by real-shell check (G7-41); R5-L4-2 in CHANGELOG or known-gap list (G7-40); one clean independent review round on the new head. |
| State today | PRs 42 to 47 open; PR 48 merged; `origin/main` = `e9412f3`; installed 0.6.1 lacks PR 48 [fact, EV-01, EV-04, EV-06]. Branch relations partly known (01 D3, D4; U-02). |
| Tag rule | Tag 0.7.0 only after V6-06, V6-07 and V6-09 are merged. V6-08 may ship as 0.7.1 ([12-roadmap §2 row 1](../../14-v6/12-roadmap.md)). |
| Gate | v6 Phase 0 exit gate ([12-roadmap §7](../../14-v6/12-roadmap.md)). |
| Human | Owner merges every PR, signs the known-gap list (V6-03) and cuts the tag. |
| Rollback, abort, cost | Per v6 cards V6-04, V6-05, V6-14: revert newest merge first; $0, no paid inference. |

## 4. Milestone cards (V7-R68 to V7-R78, V7-R135)

### M1 Instrumentation and baseline observability (V7-R68)

| Field | Content |
|---|---|
| Owner, artifacts | A+H. Ledger readouts; a baseline readout pack; a host probe record. |
| Work | V6-10 (`jade check` tells the truth), V6-12 (owner wiring steps), V6-15 (host probe), V6-23 (ledger readouts, label plumbing). M1-a: per-decision trace fields where missing (route, resolved model id, cost, latency, evidence-source flag); which exist today is [unknown]. M1-b: frozen baseline pack from `jade ledger stats` (asks per 100 gated calls, exit mix, latency percentiles, spend), seeded from EV-51, EV-53 [fact]. M1-c: suite at `e9412f3`, empty HOME, `CI=1` (U-01). |
| Prerequisites | B done (V6-23 needs V6-05 and V6-14). V6-15 can start right away. |
| Effort | M to L: 3 S tasks, 1 L task, plus owner probe hours [assumption]. |
| Paid inference | No. |
| Acceptance tests | (CI) `jade check` reports only true layer rows; new test: new ledger rows carry trace fields and old rows still read. (Owner run) baseline pack regenerates byte for byte from a frozen ledger copy. |
| Compat, migration | Ledger columns additive only; old rows show null for new fields. |
| Observability | This milestone itself. Reconcile the 7-day and 14-day load counters ([12-roadmap §10](../../14-v6/12-roadmap.md)). |
| Security checks | Readouts never print command text or secrets. Redaction test on the readouts. |
| Cost ceiling | $0 new provider spend. |
| Abort | Trace fields need private text to leave the machine; or migration fails on a 0.6.1 ledger. |
| Rollback | Revert; unused columns ignored. |
| Human approval | Owner runs V6-15 (needs a new session) and the baseline run. Owner approves the schema change. |

### M2 Contract and schema stabilization, feature flags, migration scaffolding (V7-R69)

| Field | Content |
|---|---|
| Owner, artifacts | A. Versioned schemas and contracts; flag registry; migration test; corrected docs. |
| Work | V6-34. M2-a: versioned decision-trace schema; test that opens a previous-release ledger ([unknown] whether one exists). M2-b: flag registry: every v7 behaviour behind one flag, default off = v6 behaviour; flags never set by agents. M2-c: docs fixes G7-07, G7-48 (`docs/01` ADR-02, not ADR-01 [fact, `EVD/jev-models/REPORT.md` §9]); `~typesafe/jev-latest` is a real alias [fact, EV-18]. M2-d: none (U-03 settled: v0.6.1 = `8100ea1`, 01 D1). M2-e: doc-11 contracts as versioned schemas: DecisionRequest, EvidenceBundle, DecisionProposal, PolicyVerdict, ApprovalRequest, ExecutionResult, OutcomeAssessment, DecisionTrace, each with owner and error semantics (G7-50); 6 schemas exist (01 §2.1); 5 objects missing. |
| Prerequisites | B done. |
| Effort | M: 1 M task plus small items [assumption]. |
| Paid inference | No. |
| Acceptance tests | (CI) Contract tests per doc-11 schema: round trip, unknown version rejected, previous version read, error cases typed. Previous-release ledger reads. Flags-off equals v6 output on the 171-row regression corpus [fact, EV-55]. Flag-on never allows what flag-off blocks. Docs-truth grep test (jev-latest, exit-6 text). |
| Compat, migration | Flags default off. Schema version written on every row. |
| Observability | `jade check` lists the flag states. |
| Security checks | No flag can lower a bar: flag-on never allows a call flag-off blocks, except by a separately approved scope change. Agents cannot set flags (SI-11 style protection [assumption]; list in [08 §3.1](08-security-operations.md)). |
| Cost ceiling | $0. |
| Abort | Flags-off output differs from v6 on any corpus row. |
| Rollback | Revert. The schema is versioned, so readers accept both versions. |
| Human approval | Owner merges. Owner approves which flags are allowed to exist. |

### M3 Evidence, provenance and context-quality controls (V7-R70)

| Field | Content |
|---|---|
| Owner, artifacts | A+H. Evidence record; pre-egress redaction; repeat detection. |
| Work | M3-a: evidence record per verify call (source kind, path or URL, hash, capture time, trust class); none exists [fact, EV-37]. M3-b: decide whether `evidence_source` goes to Jev; today it is sent though `docs/03` §5 says not [fact, EV-37]; make code and docs agree. M3-c: redaction before any egress, tested with seeded secrets. M3-d: repeat-question detection beyond exact text [fact, EV-37]. |
| Not present | Contextum is not present [fact, EV-61]. No integration is planned. |
| Prerequisites | M1 and M2. |
| Effort | M to L [assumption]. |
| Paid inference | No. Tests use recorded fixtures. |
| Acceptance tests | (CI) EvidenceBundle contract tests (M2-e). Verify with no evidence record ends as `abstain` or ask, never `accept`; seeded secrets never reach an egress payload; near-duplicates detected. |
| Compat, migration | Behind a flag; old rows read as "unknown provenance". |
| Observability | Count of verify calls with an evidence record; count of redaction hits. |
| Security checks | Tool output and repo text are untrusted. V6-09 escaping also covers evidence shown to the owner. |
| Cost ceiling | $0. |
| Abort | Redaction not shown complete on the corpus. |
| Rollback | Flag off. |
| Human approval | Owner decides M3-b: it changes what leaves the machine. |

### M4 Decision classification, routing, model registry and adapters (V7-R71)

| Field | Content |
|---|---|
| Owner, artifacts | A+H. Intake classifier; registry file; adapter contract tests. |
| Work | V6-28 (the `jev` key and the deny-count gate). M4-a: deterministic intake that routes each call to fast path, structured Jev, or human, reusing rule ids and `q.routing.suitable_for_jev` [fact, EV-59]; R-01, R-03, R-04 and memory need no model [fact, EV-34]. M4-b: model registry (pinned resolved id, output modality, ZDR flag, egress-approval flag, dated price source). M4-c: adapter contract tests on recorded fixtures. M4-d: optional `provider:{"zdr":true}` behind a flag [fact: works on the Decisions route, EV-33]. M4-e: circuit breaker; on outage, deterministic block or human ask, never a weaker model; no fallback model (G7-46). M4-f: guard from the Jev study: human-only accepted-model list outside `registry/`, `registry/` and `policies/models.yaml` protected, band floor, vendor in the group key (G7-42 to G7-44; owner D2). M4-g: retired-model and ZDR 404 give exit 5 (G7-47). Intake emits DecisionRequest, routes return DecisionProposal (M2-e). Detail in 06. |
| Prerequisites | M2, and V6-28's own prerequisites. |
| Effort | L [assumption]. |
| Paid inference | No. Optional live smoke call per newly approved vendor, with its own owner yes. |
| Acceptance tests | (CI) Registry rejects `typesafe/jev-router` and any non-`decisions` output model for a gate role [fact, EV-21]. Rejects a tilde alias as default; default stays pinned `typesafe/jev-1.14` [fact, EV-16, EV-20]. Rejects 9router for Decisions calls [fact: task FACTS; no saved probe, U-04]. Outage or forced 429 never looser than model-available, never `accept`. Agent write to the accepted list blocked. New snapshot keeps the stricter band. Retired and ZDR 404 fixtures give exit 5. |
| Compat, migration | The registry starts with jev-1.14 only. With the flag off, behaviour equals v6. |
| Observability | Route mix per day; circuit-breaker trips; resolved-model drift events (`MODEL_DRIFT` already exists [fact, EV-52]). |
| Security checks | Each vendor is a new egress destination, disabled until the owner says yes. The 5 ZDR-failing vendors [fact, EV-33] stay off while the ZDR flag is on. A probability is never permission. |
| Cost ceiling | $0. Optional smoke: $1 total [assumption, proposed]. |
| Abort | The classifier sends any human-only class to a model. |
| Rollback | Flag off; `jade jev off` (owner). |
| Human approval | Owner approves each vendor and the V6-28 safety change. |

### M5 Independent deterministic authorization and approval controls (V7-R72)

| Field | Content |
|---|---|
| Owner, artifacts | A+H. Closed rule holes; level `off` as "observe plus floor"; floor tests; safety backlog; sandbox pilot; memory audit. |
| Work | V6-08, V6-16, V6-17, V6-18, V6-25, V6-29. No new v7 work: the deterministic layer stays custom [fact: R3 recommends this, EV-46]. |
| Prerequisites | B done. V6-29 also needs V6-21 and V6-23. |
| Effort | XL: 3 L tasks and 3 M tasks, plus owner audit hours [assumption]. |
| Paid inference | No. |
| Acceptance tests | v6 Phase 1 and Phase 4 gates ([12-roadmap §7](../../14-v6/12-roadmap.md)). N-tests red on the pre-fix sha. Never-learn test passes. Never-looser check against main empty. PolicyVerdict and ApprovalRequest contract tests: approval bound to `plan_hash`, rechecked at action time; approval for another call, expired or revoked grant, model score or silence never authorizes (G7-03, G7-51). |
| Compat, migration | Asks rise when level `off` starts blocking; a replay count of extra asks per day comes first (v6 rule 1). |
| Observability | Asks per day by rule, beside the false-allow audit bound. |
| Security checks | A model output can never override a deterministic block. Only the human sets the JADE level. Human-only classes are never remembered. |
| Cost ceiling | $0. |
| Abort | Replay allows a should-block call; one audited wrong silent allow (v6 Phase 4 rule). |
| Rollback | Revert per task; V6-25: `sandbox.enabled: false`. |
| Human approval | Written yes before each safety change. Owner runs the replay and the audit. |

### M6 Selective verification, structured decisions, uncertainty management (V7-R73)

| Field | Content |
|---|---|
| Owner, artifacts | A+H. Veto-only verifier hook point; structured decision record; abstain handling; date validator. |
| Work | V6-24 (decision hygiene Tier A). M6-a: veto-only second verifier, high-risk class only, shadow first; integration.md recommends a second model only make decisions stricter [fact about file, EV-34]. M6-b: DecisionProposal (M2-e): options, evidence, assumptions, counterargument, abstain reason. M6-c: `abstain` always escalates or asks; raw `p` is not calibrated [fact, EV-41, EV-48]. M6-d: deterministic date-arithmetic check for expiry claims [hypothesis: exploratory bench, 18 and 17 of 36 models accepted JG10/JG23, EV-31; coverage U-07]. |
| Prerequisites | M3 and M4. |
| Effort | L [assumption]. |
| Paid inference | No. Live veto calls happen only in M9. |
| Acceptance tests | (CI) New tests: a veto only turns accept into abstain (exit 3) or a human ask, never reject and never the reverse (11 §2.1); abstain never becomes accept; JG10/JG23 date cases are decided without a model. |
| Compat, migration | The structured record goes in a new versioned field; the v6 verdict vocabulary is kept ([04-adrs ADR-v6-06](../../14-v6/04-adrs.md)). |
| Observability | Veto rate; abstain rate by class. |
| Security checks | No always-on consensus: exploratory bench suggests correlated vendor errors and 2-of-3 voting letting false claims through [hypothesis, EV-32]. A fallback never lowers a bar. |
| Cost ceiling | $0. |
| Abort | Second model loosens any verdict. |
| Rollback | Flag off. |
| Human approval | Owner approves V6-24 (a tightening) and any verifier vendor (see M4). |

### M7 Blocker continuity, human question UX, durable tasks, recovery (V7-R74)

| Field | Content |
|---|---|
| Owner, artifacts | A+H. Decision state machine; blocker records; question builder; atomic work-item store; resume briefing. |
| Work | V6-26, V6-27. Per the split, JADE owns decision and blocker state; tool retries and checkpoints stay with the host. M7-a: blocker record per 11 §2.2 (class = rule/SI/HO id, no new taxonomy; missing today [fact, EV-40]) and bounds on JADE re-asks and replans (attempts, time, tokens, money, depth). M7-b: atomic work-item writes at an absolute path [fact today: cwd-relative, non-atomic, EV-40, EV-54]. M7-c: batched human-only questions in simple English [fact: never batched, EV-42]; [09-human-experience §3, §8](../../14-v6/09-human-experience.md). M7-d: doc-11 decision state machine mapped onto ledger, permit and work states (G7-49). M7-e: host-callback, changed-evidence and bound rules (keys, concurrency, replay exist, 01 §10.3); host ExecutionResult intake (observed results, not a success claim) (G7-51). |
| Not present | Obsidian/Kanban sync is not present [fact, EV-62]. `work-items.jsonl`, BACKLOG.md and the ledger stay the store. |
| Prerequisites | B (V6-09), V6-15. |
| Effort | L [assumption]. |
| Paid inference | No. |
| Acceptance tests | (CI) State-machine tests: every legal transition has a guard and owner; illegal ones rejected; timeout or no answer never reaches an approved state; each terminal or deferred state has owner and reopen condition; changed evidence returns to evidence gathering. Duplicate callback is a no-op; replayed approval refused. Killed write leaves the file valid; `jade work list` works anywhere; a batch never widens approval. |
| Compat, migration | Existing `work-items.jsonl` rows read unchanged; one documented path move. |
| Observability | Time to answer, questions per batch, retries per blocker class. |
| Security checks | A batch never becomes a standing approval (today one answer covers a whole kind of critical call [fact, EV-38]; V6-06 fixes it). No secrets asked in chat. |
| Cost ceiling | $0. |
| Abort | Owner misreads a question in the acceptance trial. |
| Rollback | Flag off; old work-item file kept. |
| Human approval | Owner approves the question text (safety: yes, per V6-26). |

### M8 Evaluation harness, regression suites, feedback governance (V7-R75)

| Field | Content |
|---|---|
| Owner, artifacts | A+H. Replay runner; labelled corpus; oracle; pre-registered benchmark; report-only learning. |
| Work | V6-20, V6-21, V6-22, V6-32. M8-a: harness for the pre-registered benchmark in [05](05-benchmark-design.md); thresholds and holdout split hashed before the first run. M8-b: the exploratory jev-models bench may only rule models out [fact: results seen before any threshold, EV-29, EV-30]. M8-c: every recurring failure becomes a regression case. M8-d: OutcomeAssessment (M2-e) scores host ExecutionResult against acceptance criteria; report only. |
| Not present | No Laravel/PHP or Vue/TypeScript corpus [fact, EV-63]. The benchmark uses the existing shell and claim corpora (505 rows [fact, EV-55]) unless the owner supplies one. |
| Prerequisites | M1, M2, M5 (V6-17). |
| Effort | L to XL, plus owner labelling hours, which are [unknown] (V6-21). |
| Paid inference | Yes: benchmark runs. |
| Acceptance tests | v6 Phase 2 gate ([12-roadmap §7](../../14-v6/12-roadmap.md)). Harness refuses to run on a missing or changed threshold hash. Reruns reproduce the report from saved outputs. |
| Compat, migration | Eval-only code; no runtime change. |
| Observability | Run id, resolved model ids, seed where supported, spend per run. |
| Security checks | Real ledger rows stay private, redacted. Learning writes reports only, no path to policy ([08-learning-and-improvement §5](../../14-v6/08-learning-and-improvement.md)). Model output is never ground truth. |
| Cost ceiling | Per 05 §9: L2-M $15 total, $3 per vendor, $5 per run; DEEP $5; router $10 (H-06) [assumption]. Jev-1.14 input $0.042 per M tokens [assumption, EV-24 unverified here]; other vendors [unknown]. |
| Abort | Ceiling reached; or too few labels ("not enough data" is a stop state, not a pass). |
| Rollback | Delete run outputs; nothing ships. |
| Human approval | Owner approves each paid run and its budget. Owner or CI launches the runners (agents cannot set `JADE_*` variables, v6 rule 2). |

### M9 Shadow-mode comparison against the current system (V7-R76)

| Field | Content |
|---|---|
| Owner, artifacts | A+H. Shadow runner; disagreement log; adjudication sample; keep/retire decision. |
| Work | V6-31 (Jev proxy: keep or retire). M9-a: candidate path (M3, M4, M6 flags on) runs beside the current path (pinned jev-1.14); it recommends, never executes. Owner adjudicates sampled disagreements. Optional lane `baseten/mammoth-1`, verify questions only, only after G7-45 controls and owner D6 [fact: recommendation, not a result, `EVD/jev-models/REPORT.md` §5]. |
| Prerequisites | M6, M8 thresholds frozen, V6-28. |
| Effort | M, plus a shadow window that reaches the M8 sample size [assumption]. |
| Paid inference | Yes: shadow calls double the model calls on the shadowed classes. |
| Acceptance tests | v6 Phase 5 gate ([12-roadmap §7](../../14-v6/12-roadmap.md)). Zero shadow-caused actions (test plus ledger audit). The pre-registered thresholds from 05 are met with confidence intervals. |
| Compat, migration | None; shadow path is read-only. |
| Observability | Disagreement rate by class, adjudication results, shadow spend. |
| Security checks | Shadow never sees critical, human-only or fetch-and-exec calls (`tests/acceptance/test_jev_proxy_w102.py`). Each vendor in shadow is an approved egress destination. |
| Cost ceiling | Proposed $10 per shadow window [assumption]. |
| Abort | Any shadow side effect; ceiling reached; thresholds missed. |
| Rollback | Flag off; `jade jev off`. |
| Human approval | Owner starts and stops the window, adjudicates, and decides keep or retire (v6 D-5 = [LD-08](../../14-v6/13-deferred-and-open-questions.md)). |

### M10 Limited canary, gradual expansion, production readiness review (V7-R77)

| Field | Content |
|---|---|
| Owner, artifacts | H. Canary plan; halt triggers; readiness review; public docs. |
| Work | V6-19 (leave level `off`), V6-30 (first memory scope), V6-33 (true front page). M10-a: turn on one flag for one class in one repo, then widen one class at a time, each step a separate owner decision. Release detail in [09](09-release-and-learning.md). |
| Prerequisites | M9 passed; M5 done; owner friction and autonomy targets written (v6 Phase 4 gate). |
| Effort | M, plus waiting time for audited runs (59 clean runs for the first scope [fact: v6 gate, 12-roadmap §7]). |
| Paid inference | Yes, at the volume of live decisions. |
| Acceptance tests | (CI) Halt triggers fire on injected faults. Readiness review checks quality, security, reliability and cost against frozen thresholds. |
| Compat, migration | Every step can be turned off with a flag. Release notes list breaking changes (section 5). |
| Observability | Live window of at least 300 calls over at least 7 days before any latency claim ([12-roadmap §7](../../14-v6/12-roadmap.md)). |
| Security checks | Only the human sets the level or widens a scope. Auto-halt on a wrong silent allow, a new safety-block class, or a spend spike. |
| Cost ceiling | Proposed $5 per week of total provider spend during the canary [assumption]. |
| Abort | Any halt trigger. |
| Rollback | Flag off, scope back to asking, `jade level` back (owner). |
| Human approval | Every expansion step, the release tag, and general availability. |

## 5. Compatibility and breaking changes (V7-R108)

| Change | Breaking? | Preserve and migrate |
|---|---|---|
| Ledger trace and evidence fields (M1, M3) | No | Additive columns; old rows read as null or "unknown provenance". |
| Schema version field (M2) | No | Readers accept both v6 and v7 rows. |
| Level `off` starts blocking (V6-16) | Yes, behaviour | Asks rise; owner yes and replay count first; changelog entry. |
| Work-item store path and atomic writes (M7) | Possibly | Old file read in place; one documented move. |
| Registry rejects tilde aliases and chat models (M4) | No for current users | The live default is already pinned [fact, EV-16]. |
| Evidence no longer sent to Jev, if chosen in M3-b | Behaviour | Documented; flag. |
| Doc-11 contracts and decision states (M2-e, M7-d) | No | Versioned; readers accept the previous version. |
| Retired or ZDR 404: exit 6 to exit 5 (M4-g) | Yes, exit code | Changelog; callers treat 5 as provider problem. |

All data is kept. Nothing in v7 deletes ledger rows; purge stays V6-11's owner-approved scope.

## 6. Lifecycle coverage (V7-R03)

Audit: `01`, `02`. Design: `03`, `04`, `06`, doc 11. Benchmark: `05` (M8). Rollout: M9, M10, `09`. Improvement: M8-c, V6-32, `09`.

## Open questions for the owner

| # | Question | Default (why) |
|---|---|---|
| 1/[A-11](10-decisions-and-questions.md) | Is Jev Router in the benchmark? | No gate path; router-track arm R1 only if that track runs (05 §4.4, A-11; EV-21) |
| 2/[H-06](10-decisions-and-questions.md) | Ceilings per 05 §9 ($5 per run, $15 L2-M total), $10 per shadow window, $5 per canary week? | Yes, hard stops (Jev ~$0.08 per 1000 verifies [fact, EV-33]) |
| 3/[H-03](10-decisions-and-questions.md) | ZDR on by default for Decisions calls (M4-d; REPORT D1)? | Yes, after a fixture test and one approved smoke call (TypeSafe ZDR conflict open) |
| 4/[L-06](10-decisions-and-questions.md) | First canary class? | Verify-claim calls, one repo, level `high`; never human-only (EV-51) |
| 5/[L-07](10-decisions-and-questions.md) | Any vendor beyond TypeSafe? | None until M8 passes; then one at a time (new egress; bench cannot select) |
| 6/[H-09](10-decisions-and-questions.md) | Which release carries v7? | Next minor after 0.7.x, flags off (set in `00`) |

## Deferred items

| Item | Owner | Priority | Next action |
|---|---|---|---|
| Contextum integration (not present, EV-61, U-11) | H | P3 | Owner says if it exists anywhere; else drop it. |
| Obsidian/Kanban sync (not present, EV-62) | H | P3 | Owner confirms not needed; work-item store stays. |
| Laravel/PHP and Vue/TypeScript corpus (not present, EV-63) | H | P3 | Owner supplies cases or approves dropping these families from 05. |
| Jev Router chat comparison outside the gate (open question 1) | H | P3 | Revisit after M9. |
| Optional OTel export ([fact]: R4 recommends opt-in, EV-47) | A | P3 | Design after M1 trace fields settle. |
| Raw probe logs for jev-router and 9router chat-only (U-04) | H | P2 | Owner-approved re-probe with a saved log. |
