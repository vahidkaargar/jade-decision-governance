# 04 — Calibration, labels and benchmark protocol

## 1. What a raw P is and is not

Verified from the TypeSafe docs (inventory S8): confidence is distribution concentration; a Noul P is "the probability that the answer is yes" as the model estimates it; the vendor states training for calibrated decisions but publishes no calibration metrics for any domain. Therefore in JADE a raw P is **an input to a threshold policy per calibration group**, never a universal probability of correctness and never permission.

Calibration group = `(task_family, question_version, resolved_model)`. Today there is exactly one resolved model in use (`typesafe/jev-1.14-20261014`), two de-facto families (verify, choice), and zero labels.

## 2. Labels and ground truth per family

| Family | Label | Source of truth | Who adjudicates |
|--------|-------|-----------------|-----------------|
| verify_claim | claim held (true/false) | `outcome.observed` for the same decision; or user correction in the following turn; or CI result | runtime joins ledger → outcome; ambiguous → human adjudication queue (≤ 10/week) |
| root_cause | chosen cause was the actual cause | fix verified by observed outcome | agent proposes, human confirms on disagreement |
| strategy_choice | no label of correctness; label = "regretted" (user reverted/overrode within 7 days) | ledger + git history | runtime heuristic + human spot check |
| risk_triage | rule-based level (`risk.yaml`) | deterministic | none (Jev is advisory) |
| evidence_quality | raw vs paraphrase | deterministic check of evidence_ref kind | none |

The outcome join labels only the call that gated the decision: the primary verify call named last in the decision's `evaluation_refs`, and only when it was accepted (exit 0). A companion question, an earlier rejected call on another claim and a call a human overrode stay unlabelled and reach the adjudication queue. A cached repeat labels the call that produced the answer. Labels carry `label_source` (observed / user_correction / adjudicated / heuristic) and `disagreement` (count of adjudicators disagreeing). Heuristic labels are excluded from calibration fitting, used only for monitoring.

## 3. Thresholds

Start: docs defaults 0.70 / 0.30 (Noul), top-p 0.70 (Choice), stricter for high/critical risk (`policies/escalation.yaml`). Adjustment only when a group has ≥ 200 labeled rows (distinct `(claim, state)` pairs: a cached repeat, a second row for the same claim and state, and an answer with no resolved model string are not evidence and are not counted or fitted) and the Wilson 95% upper bound on false-accept rate at the proposed threshold is ≤ the family budget. Method order as data grows: empirical bins (≥ 200) → logistic/Platt (≥ 500) → isotonic (≥ 1,000). Budgets are set from the baseline in §6, not invented.

## 4. Baseline (measured 2026-11-08; `evals/baseline/summary-2026-11-08.json`)

| Quantity | Value |
|----------|-------|
| Real calls / days / per day | 907 / 14 / 65 |
| Verify P mean / median | 0.60 / 0.64 |
| Verify P < 0.70 | 57% |
| Low-P followed by done-claim (heuristic) | 30% of low-P calls |
| Repeats of identical question in session | 20% of calls |
| Result unparsed or error | 9.4% |
| P reported to user | 73% |
| Latency (see §4b) | p50 ≈ 500 ms at every parallelism and batch size tested |
| Cost per call | ≈ $1.4e-5 |

### 4a. Policy replay: what the proposed thresholds would have done (`evals/replay_policy.py`, `evals/baseline/replay-2026-11-08.json`)

| Outcome under `policies/escalation.yaml` defaults | Count |
|---|---|
| Noul accept (exit 0) / abstain (exit 3) / reject (exit 4) | 275 / 280 / 85 |
| Choice accept / abstain (confidence proxy, option count unknown) | 151 / 31 |
| Provider or parse error (exit 5/6, never "yes") | 85 |
| Repeat calls the dedup rule would have answered from cache | 97 |
| Done-claims the Stop hook would have flagged | 109 |
| Transcript files (of 379) with at least one non-accept | 229 |

Reading: at the docs' default band, 57% of past verifications would not have returned "yes". That is the expected escalation load the human must accept or the thresholds must be re-set against labels (W-08); it is also why abstention routes to DEEP first and to the human only for risk ≥ medium.

### 4b. Live performance (`evals/performance/run.py --rounds 20`, `evals/performance/result-2026-11-09.json`, typesafe/jev-1.14-20261014, 323 timed calls, total cost $0.005365)

| config | n | p50 ms | p95 ms | p99 ms | max ms | errors |
|---|---|---|---|---|---|---|
| single_noul_parallel_1 | 20 | 514 | 639 | 728 | 728 | 0 |
| single_noul_parallel_4 | 80 | 506 | 553 | 562 | 565 | 0 |
| single_noul_parallel_8 | 160 | 511 | 582 | 617 | 717 | 0 |
| batch_1_serial | 21 | 505 | 564 | 574 | 574 | 0 |
| batch_3_serial | 21 | 502 | 551 | 554 | 554 | 0 |
| batch_8_serial | 21 | 515 | 616 | 617 | 617 | 0 |

Reading: latency is flat at about 500 ms whether one question or eight are sent, parallelism 8 produced no 429 in 160 calls, and the earlier 2026-11-08 run (rounds 3) agrees (`result-2026-11-08.json`). One batched request per decision is strictly better than serial calls. The 429 and retry-after behaviour is still unobserved (BACKLOG W-16); the adapter's retry path is tested against recorded fixtures.

These numbers are heuristic (regex over transcripts). Phase 1's first job is to replace them with ledger facts; the baseline is re-measured over the first 14 ledger days before any threshold or routing change (paired comparison needs a clean "before").

## 5. Evaluation sets and partitioning

```
evals/
  baseline/      transcript-mined calls (dev only; leaky by construction)
  routing/       labeled {task → should_call_jev, primitive, family}; partition by session
  judgment/      verify/choice cases with observed outcomes; partition by task family AND by session (no session in two splits)
  calibration/   held-out for threshold fitting
  regression/    every past failure class (low-P→done, repeat, degenerate choice, provider error) as a replay
  adversarial/   injected instructions in evidence, forged "approved" strings, Persian/English mixed, truncated evidence, stale context
  performance/   latency/throughput harness: 1, 4, 8 parallel; batch 1/3/8; p50/p95/p99; 429 behaviour
  */protected/   never read by candidate generation; results only via `jade eval run --protected` (human-run)
```

Splits: dev 50% / calibration 20% / validation 15% / protected test 15%, by session id hash. Minimum protected size before any promotion: 150 per family.

## 6. Budgets derived from baseline

| Metric | Baseline | Budget / target for Phase 3 exit | Rationale |
|--------|----------|----------------------------------|-----------|
| Unsafe accept (P < reject or exit≠0 followed by done without disposition) | 30% (heuristic) | ≤ 5% measured on ledger; 0 for risk ≥ high | plan priority 1 |
| Abstention coverage (share of decisions resolved without human) | unknown (no abstention existed) | ≥ 70% for low risk; report, don't target, for high | avoid escalation fatigue |
| Repeat-without-new-evidence | 20% | ≤ 3% | dedup |
| Provider error/unparsed | 9.4% | ≤ 2%, all typed | adapter |
| Hot-path p95 added latency per decision | ≈ 0.5 s per call, serial; up to 56 calls/file | ≤ 1.0 s per decision (one batched request, p95 672 ms at batch 8, + ledger write) | batching replaces serial calls |
| Cost | $0.013 / 14 days | ≤ $1 / month | not binding |
| Routing false positive (Jev called when FAST sufficed) | unknown | measured on `evals/routing`; target set after first measurement | plan §12: no invented targets |

## 7. Metrics computed by `jade eval`

Decision quality: false accept, false reject, abstain rate, selective risk vs coverage curve. Calibration: reliability diagram (10 bins), Brier, log loss, ECE with CI by bootstrap. Ranking (choice): top-1 accuracy, margin distribution. Routing: confusion matrix FAST/JEV/DEEP. Operations: p50/p95/p99 latency, provider error rate, calls per decision, cost per accepted verified decision. Process: time-to-resolution for WAITING_HUMAN, work items opened/closed, resumption success after crash (fault-injection test).

Paired comparisons (same cases, champion vs challenger), Wilson/bootstrap intervals, Holm correction across metrics, subgroup slices (fa/en, risk ≥ high).

## 8. Baseline conditions to compare (Phase 3)

1. Current behaviour (CLAUDE.md rule, bare `jev`) — reconstructed from `evals/baseline`.
2. Reasoning-only (DEEP verdict, no Jev).
3. Jev-only with registry questions and thresholds.
4. Cascade: FAST → JEV → DEEP → human.

Promotion of the cascade requires non-inferiority on unsafe accept vs (2) with the paired interval excluding a 3-point regression, lower latency than (2), and no safety-invariant violation.
