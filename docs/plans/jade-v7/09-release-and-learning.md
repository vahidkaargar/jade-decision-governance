# 09 Release and learning

Status: Planning only. Nothing in this document is implemented.

Scope: how a v7 change (vendor, veto model, resolved id, question template, routing, policy) moves from replay to shadow, canary and GA, and how JADE learns only from verified outcomes. Delta on [v6 doc 08](../../14-v6/08-learning-and-improvement.md), [v6 doc 10](../../14-v6/10-verification-and-benchmark.md) s10, s13 and [v6 doc 12](../../14-v6/12-roadmap.md) s7. Thresholds: [05](05-benchmark-design.md); operations: [08](08-security-operations.md). This doc is the Evaluation/learning plane ([08](08-security-operations.md) 1.1): it proposes, the host executes, the policy plane authorizes. Paths at `8dfa86d`. Tags: [fact] (with source), [planned], [assumption], [hypothesis], [unknown]. `REPORT` = `EVD/jev-models/REPORT.md`; "REPORT rule N" = its section 5 list. Meets V7-R125.

## 1. Fixed rules for every release step

| Rule | Tag |
|---|---|
| A probability is never permission; a model "yes" never lifts SI, HO or critical blocks. | [fact] v6 doc 05 s4.1 |
| Only the human sets the JADE level, starts a canary for a new path, and declares GA. | [planned] (today a canary can also start from a standing opt-in, section 4) |
| A fallback, rollback or halt never lowers a bar; on doubt, the human. | [planned]; extends [fact] `jade/improve.py:445-458` |
| Each vendor is new egress, in shadow too: owner approval and ZDR before its first call ([08](08-security-operations.md) 4.1). | [fact] 5 vendors fail ZDR (EV-33) |
| `typesafe/jev-router` stays out of every gate path, shadow included. | [fact] EV-21, EV-22 |
| The jev-models bench is EXPLORATORY: it can rule out, never select or set a threshold. | [fact] EV-29..EV-32 |

## 2. Shadow mode (V7-R79, V7-A99, V7-A110)

| Stage | Input | Old path | New path | Output |
|---|---|---|---|---|
| S-A offline paired replay | [05](05-benchmark-design.md) holdout plus ledger replays (`evals/gate_replay.py`); under 200 rows: not-enough-data | Champion (`typesafe/jev-1.14`) | Candidate | Paired verdict, P, abstain, latency, cost |
| S-B live shadow | Live calls the champion handles | Decides, as today | Async; shadow table only | Joined to the owner's answer (v6 doc 08 s4.3) |

[fact] Existing shadow: question templates only, outside `jade ask` (`jade/improve.py:44-45`); 7 days, 100 samples, error share <= 0.05, zero hot-path latency (`policies/promotion.yaml:21-26`).

**"Cannot execute" is structural.** [fact] The only model accept that lets a side effect run is `q.human_proxy.would_allow`: channel `jev` needs a `jev_calls` row with that question id, `verdict='ACCEPT'`, `exit_code=0` (`jade/state_machine.py:128-130`). [planned] Shadow rows live in a separate table the authority query never reads. Tests: N-SH1 shadow ACCEPT for the exact `call_id` raises `IllegalTransition`; N-SH2 shadow error adds 0 ms, changes no verdict; N-SH3 no shadow call for human-only, critical or fetch-and-exec calls (as `tests/acceptance/test_jev_proxy_w102.py`).

**First in shadow.** [hypothesis] verify questions on a quiet lane, then a `would_allow` veto; a veto only tightens (EV-34). No loosening candidate reaches GA in v7.

## 3. Disagreement adjudication (V7-R80)

Every old/new disagreement and owner Deny goes to a human-reviewed queue with one root cause:

| Class | Meaning | Typical fix | Change type (section 8) |
|---|---|---|---|
| Model error | Evidence enough; model wrong | Keep champion; regression case | none, or drop candidate |
| Missing context | Model lacked a fact JADE could send | Change the state builder | routing/prompt |
| Policy issue | Policy wrong or silent | Owner edits under R-13 | policy |
| Label error | Reference label wrong | Fix label; record rater disagreement | corpus |
| Provider fault | Timeout, no parse, id drift | No quality verdict; health count | operations ([08](08-security-operations.md) 8) |

- [planned] Measure deny-recall and false allows on owner denies, not raw agreement (v6 doc 08 s4.2 row 1).
- [planned] Two raters on a sample, kappa >= 0.70 (v6 doc 10 s8.2). Labels from the owner or hook-seen/CI evidence, never a model.
- [fact] Stop state: 0 labelled rows (EV-41), 0 denials (EV-53a). Without 30 owner denies from 3+ rules the candidate stays in shadow, no quality claim (v6 doc 12 s7).
- [planned] Each adjudicated row stores class, rater, date and row hash.

## 4. Feature flags and canary (V7-R81, V7-R82, V7-A111)

**Flags.** [fact] Today: `gate_mode`, `level` (EV-10, EV-39), `auto_promotion_opt_in`, `FREEZE` file (`policies/promotion.yaml:39-41`). [planned] One flag per new path, default `off`, in SI-12-protected config:

| Flag (proposed) | Values | Who sets |
|---|---|---|
| `shadow.<role>.<model>` | off / shadow | human, plus egress approval |
| `veto.human_proxy` | off / shadow / canary / on | human at a terminal |
| `pin.<question_id>` | resolved model id | human ([06](06-model-routing-strategy.md)) |

**Canary.** [fact] Today only for `question_template`, fraction 0.2, started from a terminal or the standing opt-in (`jade/improve.py:516-547`; `policies/promotion.yaml:27-30`). [planned] A canary for any model, vendor, veto or routing change needs (1) a human at a terminal per canary, opt-in not enough; (2) passed shadow and adjudication; (3) written safety acceptance citing [05](05-benchmark-design.md); (4) a halt set (section 5). Authority expands only by this path. No loosening canary in v7.

## 5. Halt and rollback triggers (V7-R81)

Triggers act automatically and only toward safety: end the canary, pin the champion, route affected calls to the human. Restart needs the human.

| Trigger | Source | Action | Tag |
|---|---|---|---|
| False-accept rate > champion + 0.03 | `policies/promotion.yaml:31-34` | rollback | [fact]; point estimate, no minimum n (v6 doc 10 s10.2) |
| Provider error rate > 0.05 | same | rollback | [fact] |
| Live resolved id differs from the dataset id | same | rollback, new group | [fact] |
| One audited wrong allow on a canary call | v6 doc 08 section 9 | halt at once, no statistics | [planned] |
| SI/HO block or owner Deny on a new-path call | v6 doc 05 s6.5 | suspend; ask the human | [planned] |
| Added hook latency; p95 over SLO | [08](08-security-operations.md) section 6.1 | halt | [planned] |
| Spend over the canary budget | [08](08-security-operations.md) section 5.3 | halt | [planned] |
| `FREEZE` file present | `policies/promotion.yaml:39-41` | all challengers off; thresholds frozen | [fact] |

[planned] Statistical triggers return pass, fail or not-enough-data; the point estimate stays a tripwire. A fired trigger is S2 ([08](08-security-operations.md) 8). [fact] Under `FREEZE` only a human at a terminal may roll back (`jade/improve.py:448-458`).

## 6. Human approval and audit (V7-R82)

| Action | Approval | Audit record |
|---|---|---|
| Production release | human only; no automatic deployment | [fact] SI-06, R-03; `SHA256SUMS` |
| Shadow with a new vendor | human, egress approval | [planned] registry diff + docs/05 threat row |
| Start canary; declare GA | human at a terminal | [fact] promotion record actor `human:terminal` (`jade/improve.py:546`) |
| Policy edit | human, R-13 | [fact] git history + threat-row test |
| Privileged action during canary | unchanged: signed permit | [fact] T20 |

[planned] Each canary decision stores candidate id, resolved id, question and policy version, redacted state hash, so `evals/gate_replay.py` re-runs it.

## 7. GA gates (V7-R83)

GA needs all four gates, approved before shadow starts and fixed after.

| Gate | Pass condition | Numbers set in | Tag |
|---|---|---|---|
| Quality | HG1.. (HG3: false-accept upper bound <= 5% MVP, 2% full); live deny-recall >= 90.5% on 30+ owner denies (v6 doc 12 s7) | [05](05-benchmark-design.md) 7.2, 7.4 | [planned] |
| Security | G1..G8 (v6 doc 10 s10.1), N-SH1..3, never-looser and never-learn tests green; vendor ZDR and retention recorded (U-05) | v6 doc 10; [08](08-security-operations.md) | [planned] |
| Reliability | 300+ calls over 7+ days within SLOs; no S1; every S2 closed with a regression case | [08](08-security-operations.md) 6.1 | [hypothesis] window |
| Cost | CPVS (cost per verified successful decision) within budget; spend under ceiling | [05](05-benchmark-design.md) 5; [08](08-security-operations.md) 5.3 | [planned] |

[fact] Baseline ~$0.08 per 1000 verify decisions (EV-33). Post-GA claims: counts with n and intervals, never "JADE is safe" (v6 doc 10 s14).

## 8. Controlled improvement loop (V7-R42, V7-R86, V7-A98, V7-A100, V7-A101, V7-A102, V7-A21)

Kept from v6 doc 08 s4 (evaluators only for `calibration_threshold`, `question_template` [fact]); asking less needs a human. Inputs: versioned datasets (`dataset_versions`), pre-registered thresholds ([05](05-benchmark-design.md)), a proposal (`improve.propose`). Routing improvements reach the router only through sections 2 to 7, never automatically (V7-A21).

**Verified-outcome-only feedback.**

| Input | Label? | Why |
|---|---|---|
| Owner Allow/Deny | yes | human act |
| Hook-seen exit code or CI result | yes, `observed` | independent evidence (v6 doc 08 s6.3) |
| `jade review mark wrong` | yes | human act |
| Adjudicated disagreement | yes, with class | reviewed |
| Model answer or consensus | never | EV-32 |
| Agent-typed `--type observed` or a success claim | no, until matched to hook or CI evidence | section 8.1 |
| Model-written benign-twin label | no; "twin unverified" | v6 doc 08 s4.2 |

**Change types are separate pipelines.**

| Change type | Examples | Evaluation | Activation | Rollback |
|---|---|---|---|---|
| Question template | text | replay (200, `promotion.yaml:4-6`), shadow, canary | human at a terminal | `jade improve rollback` |
| Routing | pin, veto, vendor order | sections 2 to 7 | flag, human | flag off; pin old id |
| Policy | rule class, threshold | zero flips on the human-only corpus; 14-day shadow (v6 doc 08 s4.2) | human edit under R-13 | `git revert` |
| Fine-tuning | training | [unknown] no capability evidenced (`jev-models/research`); not planned | none | none |

[planned] Every change: deterministic CI gate and never-looser check; short n returns not-enough-data, never pass.

### 8.1 Observed execution results and outcome assessment (V7-A93..A96 cross-ref; contracts in doc 11)

[planned] The host returns observed results (exit code, test output, diff, CI run id), not a success claim; JADE assesses, never executes.

| Item | Status |
|---|---|
| Only an observed success reaches SUCCEEDED; estimate stays VERIFYING; unknown = OUTCOME_UNKNOWN | [fact] `jade/decide.py:89-90,127` |
| Outcome types `observed`, `estimated`, `unknown` | [fact] `jade/cli.py:693` |
| The agent itself may run `jade decide outcome --type observed` (hook prompts it, `jade/hooks.py:562`): "observed" can be a typed claim | [fact]; gap |
| `ExecutionResult` source (`hook`, `ci`, `human`, `agent`) plus raw refs; only hook, CI, human count for labels and GA | [fact] today only `caller_session`, `attribution` (`jade/decide.py:86-99`); [planned] source |
| Acceptance criteria per decision; `OutcomeAssessment` = per-criterion pass, fail or unknown, discrepancies, root-cause hypothesis tagged [hypothesis] | [planned]; no criteria field in `jade/` (grep 2026-11-16) |
| JADE may request only a bounded replan or retry; the host runs it and it is re-authorized at action time ([08](08-security-operations.md) 2.2) | [fact] question revisions <= 3 (`jade/state_machine.py:11`); [planned] replan bound in doc 11 |

### 8.2 Hard authorization rules are never self-rewritten (V7-A103, V7-A102)

| Asset | Rule | Status |
|---|---|---|
| `policies/safety.yaml`, `promotion.yaml`, protected eval corpora, permit and ledger code, hook config | `propose` refuses non-improvable targets | [fact] `jade/improve.py:17,57-58`; `policies/safety.yaml:65-74` |
| `policies/risk.yaml`, HO classes, accepted-model list | human edit only (R-13, SI-12) | [fact] R-13; [planned] list outside `registry/` |
| JADE level, gate mode, permits | human only | [fact] SI-09, SI-11, SI-12 |
| No value less strict than today may depend on shadow, quorum or learning data | invariant plus test | [planned] REPORT rule 8 |
| `routing_heuristic`, `escalation_heuristic` | improvable; human promotes | [fact] `jade/improve.py:17-18`, `policies/promotion.yaml:38`; [planned] never-looser check |

## 9. Recurring failures to regression cases (V7-R85)

[planned] Builds on v6 doc 08 s4.2 and [v6 doc 12](../../14-v6/12-roadmap.md) V6-32.

| Step | Rule |
|---|---|
| Detect | Same rule or failure class in 2+ sessions, any S1/S2, any `mark wrong` |
| Record | Work item in `work-items.jsonl` with owner, class, evidence ids (no Obsidian/Kanban, EV-62) |
| Case | Redacted case in `evals/regression/` or `evals/adversarial/`; fails before, passes after the fix |
| Merge | Agent proposes; human merges; code never edits the protected corpus |
| Measure | Asks of that class per 100 gated, before and after (baseline EV-53) |

## 10. Drift, provider health, operational review (V7-A112)

| Activity | Rule | Tag |
|---|---|---|
| Model drift | [fact] `MODEL_DRIFT` once per new resolved string (`jade/ops.py:155-172`). [planned] New id = release event: gate-lane accepts become owner asks until a human accepts ([06 §6](06-model-routing-strategy.md)); label clock restarts | [fact]/[planned] |
| Alias choice | Pin `typesafe/jev-1.14` (EV-16); `~typesafe/jev-latest` (EV-18, EV-20) only with a human-only accepted list, band floor, vendor in the calibration key (REPORT section 3): Q1 | [fact]/[planned] |
| Operational review | Monthly: regressions, cost, drift, policy exceptions, open blockers, lessons | [planned] |
| Provider health | Per-vendor answered share and p95, daily; breaker opens to the human | [planned] |
| Benchmark refresh | Per candidate or quarterly, [05](05-benchmark-design.md) protocol | [planned] |

**Ownership and cadence** (operations assets: [08](08-security-operations.md) 3.4; "agent" = a gated session that only proposes).

| Asset | Owner | Cadence |
|---|---|---|
| Shadow and canary flags | human | per change |
| Adjudication queue | human; agent pre-sorts | weekly during shadow |
| GA gate sheet per change | agent drafts; human approves | before shadow starts |
| Regression cases | agent proposes; human merges | per recurring failure |
| Improvement proposals | agent proposes; human promotes | monthly |
| Learning stop-rule check (v6 doc 08 s9) | human | monthly |

## Open questions for the owner

| # | Question | Recommended default | Why |
|---|---|---|---|
| Q1/[H-05](10-decisions-and-questions.md) | Gate on pinned `typesafe/jev-1.14` or `~typesafe/jev-latest` plus guard? | Keep the pin; each new id is a release | REPORT D3 default: alias with the D2 guard; pinning until G1-G3 ship fits it (EV-20) |
| Q2/[A-10](10-decisions-and-questions.md) | Stop the standing `auto_promotion_opt_in` path from starting canaries? | Yes for model, vendor, veto, routing canaries | V7-R81, V7-R82 |
| Q3/[H-08](10-decisions-and-questions.md) | First shadow candidate? | Verify questions only on `baseten/mammoth-1` (quiet lane); veto on `would_allow` only after written vendor approval | REPORT D6; tightens only (EV-34) |
| Q4/[A-10](10-decisions-and-questions.md) | Weekly adjudication time? | 30 min, ~20 rows | No reviewed rows, no claim (EV-41) |
| Q5/[A-10](10-decisions-and-questions.md) | Veto canary share? | 0.2, existing policy | Tested; a veto only adds asks |
| Q6/[A-10](10-decisions-and-questions.md) | Label time window? | 90 days | v6 start value (v6 doc 08 section 6.1) |

## Deferred items

| Item | Owner | Priority | Next action |
|---|---|---|---|
| Point-estimate trigger to pass/fail/not-enough-data | agent | P1 | Design note with minimum n |
| Shadow table, N-SH1..3 | agent | P1 | Schema proposal for [03](03-reference-architecture.md) |
| Outcome source field and acceptance criteria (8.1) | agent | P1 | Contract in doc 11; human review |
| Loosening changes via canary | human | P3 | Revisit after v6 doc 08 section 7 data minimums |
| Fine-tuning | human | P3 | None until capability and labelled corpus exist |
| Per-vendor health counters | agent | P2 | With [08](08-security-operations.md) SO-06 |
