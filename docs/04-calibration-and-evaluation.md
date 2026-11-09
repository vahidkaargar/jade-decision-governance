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

### 4c. Ledger and hook at 100k rows (`evals/performance/ledger_load.py`, `evals/performance/result-ledger-100k-2026-11-09.json`, BACKLOG W-58)

Synthetic ledger in a temporary `JADE_HOME`, never the live one: 100,000 Jev calls, 100,020 decisions, 100,000 events and 10,000 permits over 90 days, with 10% of the calls in one very long session. SQLite 3.53, Python 3.13, macOS, no `ANALYZE` (a real ledger has no statistics either). Every statement the operations ran was captured from the code itself and put through `EXPLAIN QUERY PLAN`.

| path | before p95 | after p95 |
|---|---|---|
| gated PreToolUse handler, in process (sweeps, SI-07 hold, permit lookups, journal) | 107 ms | 1.4 ms |
| `jade hook pretool` process, gated command (empty ledger: 166 ms) | 270 ms | 162 ms |
| `jade hook session-start` process (empty ledger: 151 ms) | 262 ms | 158 ms |
| PostToolUse handler, in process | 17 ms | 0.2 ms |
| decision and permit sweeps | 82 ms | 0.2 ms |
| `jade ledger stats` (30-day window), `jade check` ledger rows | 73 ms, 11 ms | 66 ms, 10 ms |

Reading: a hook process costs what an empty one costs; the interpreter start and the imports (about 100 ms) are the floor and the ledger is no longer part of it. Before, every gated call listed every overdue decision, finished ones included (`decide.sweep` read `expires_at < now` over all 100k rows), and the permit, outcome, event-kind and stale-call lookups read whole tables. Fixed: a status-filtered sweep, eleven indexes (`ledger.INDEXES`, created after the column migrations, idempotent, backward compatible: an older file gets them on its first open), and the SI-07 hold lookup split into one lookup per key (the planner read every held decision for `plan_hash=? OR hold_key=?`). `jade ledger stats` and `jade check` already used the `ts` index; their cost is the work, not a scan. `jade check` still counts the rows and validates every open permit on purpose (named in `ALLOWED_SCANS`). Cost of the indexes: the database is 26% larger after `VACUUM` (132 MB to 166 MB at 100k rows), and the first open of an older 100k-row file creates them in about 0.36 s while holding the write lock (a hook waits up to `hooks.LEDGER_TIMEOUT_S`, 1.5 s). The `PRAGMA user_version` skip of the schema script (a digest of the schema, so no number to bump) saves 0.15 ms per open, which is what the script cost on a current file: kept as asked, but it is not where the time went.

Daily job on a copy of the 100k ledger: sweeps 0.2 ms; `purge` holds the write lock for 0.67 s the first time it clears 30 days of text and 56 ms on later runs; `VACUUM` plus the checkpoint takes 0.57 s and runs after the transaction, not inside it. Both are under the 1.5 s a hook waits, but they grow with the rows: extrapolating linearly, a first purge over about three times that many rows would outlast it, and a high-risk hook call that waits longer fails closed (T17). Not measured: clock steps (DST, NTP) against expiry times, and the `-wal` sidecar in a backup (copy the database with `sqlite3 .backup`, or stop the hooks first).

Regression guard (`tests/acceptance/test_ledger_scale_w58.py`, 20k rows so it stays quick): no statement run by the gate, SessionStart, the Stop hook, the permit and dedup lookups, `jade ledger stats` or `jade check` may scan a table that grows with use, each hot lookup must name its index, and the gated handler must stay under 15 ms p95 in process and 1 s p95 as a process (about ten and six times the measurement; the plan test is the guard, the time budgets are the backstop).

These numbers are heuristic (regex over transcripts). Phase 1's first job is to replace them with ledger facts; the baseline is re-measured over the first 14 ledger days before any threshold or routing change (paired comparison needs a clean "before").

## 5. Evaluation sets and partitioning

```
evals/
  baseline/      transcript-mined calls (dev only; leaky by construction)                       BUILT
  regression/    every past failure class as a replayable case (`evals/run_cases.py`)            BUILT
  adversarial/   injected instructions, forged approvals, gate bypass spellings, secrets         BUILT
  performance/   latency/throughput harness: 1, 4, 8 parallel; batch 1/3/8; p50/p95/p99          BUILT
  routing/       hand-written {task → should_route, should_call_jev, primitive, family}, offline  BUILT (34 synthetic cases, W-49); ledger rows PLANNED
  judgment/      hand-written verify/choice cases with a labelled truth, offline input checks   BUILT (34 synthetic cases, W-49); observed outcomes PLANNED
  calibration/   hand-written held-out inputs with a labelled truth, calibration split only      BUILT (24 synthetic cases, W-49); no probabilities, see below
  */protected/   never read by candidate generation; results only via `jade eval run --protected` (human-run)
```

**What the hand-written sets are (W-49, W-14).** `evals/routing`, `evals/judgment` and `evals/calibration` are small JSONL files written by hand from public, synthetic material: ops-flavoured claims and commands in English, Persian and mixed script, no real hostnames, addresses, secrets or past commands (`jade/evals.py` `validate_sets` rejects a secret-shaped string, a URL host outside `.test`/`.example`/loopback and an address outside the documentation ranges). Every case carries `lang` (`fa`, `en` or `mixed`, checked against the script of its text), a synthetic `session` and a `split` that must equal the session-hash bucket; routing and judgment cases sit in dev or validation, calibration cases in the calibration split, none in the protected split (T10: protected cases are never committed), and no session appears in two splits or two sets. `tests/test_eval_sets_w49.py` validates the files, rejects a duplicate id, a session in two splits, a wrong `lang`, a bad label, a secret or real-looking host, and an empty language slice, and runs them through `evals/run_cases.py` with no network.

| Set | Cases | Slices (en / fa / mixed) | Label | What the offline run checks |
|-----|-------|--------------------------|-------|-----------------------------|
| routing | 34 | 13 / 10 / 11 | `should_route` by hand; `should_call_jev` is true exactly when it is `JEV` | `route.preflight` returns the label. Four Persian cases (a paraphrase spelled in Persian, a Persian design question, two Persian question marks) are marked `known_gap`: PF-03 and PF-06 recognise English phrases and the Latin `?` only, so the router sends them to Jev. The runner passes a gap while the behaviour is the recorded one and fails when it changes, so a fix must remove the marker |
| judgment | 34 (30 verify, 4 choice) | 11 / 12 / 11 | `truth` (is the claim directly supported by the evidence) with a one-line `why`; for a choice, the option that satisfies a stated hard constraint | the case text reaches the provider byte for byte through `ask.run` (Persian script, half-spaces and Persian digits survive redaction and the state cap), with no redaction and no truncation |
| calibration | 24 | 8 / 8 / 8 | `truth`, 12 true and 12 false, both outcomes in every slice, claims paired across languages | the same input check |
| adversarial | 191 (16 added for W-14) | 138 / 8 / 9 (36 too short to tag) | expected gate or exit behaviour | Persian and mixed-script injected approvals, forged approval words, comments after `git push` and `--force`, secrets glued to Persian text, and Persian text that must not be redacted |

The regression and adversarial sets are command and gate cases; `evals/run_cases.py` reports the pass rate per language slice for every case file (the case's `lang`, else the script of its text), and `jade eval run` reports `by_language` for ledger rows.

**What these sets cannot validate.** They are synthetic and tiny: the calibration set is an input set, not calibration evidence, and no threshold or probability in this repository may cite it. They do not measure Jev: nothing in them calls a provider, so no accuracy, false-accept rate, ECE or threshold is computed from them, and the calibration set holds no probabilities (a probability is the output of a live call on a resolved model; a hand-written one would be a made-up number). They are not the 200-label validation of section 3: that needs real labelled ledger rows (W-04 waits for 14 ledger days, W-27 and W-52 build on them), and the hand-written labels are one author's judgment of easy cases, so a pass rate here says nothing about real traffic. The 4 routing gaps were found by writing cases to probe the router, so 4 of 23 not-for-Jev cases is not a routing false-positive rate either; it shows the Persian gap exists. What they do give: a schema and split discipline the ledger exports must follow, a regression guard on the router and the redaction and gate paths in Persian and mixed script, and inputs ready for a later live run (a human or a deliberate paid run of the judgment and calibration inputs against the real model, results written outside the repo).

Splits: dev 50% / calibration 20% / validation 15% / protected test 15%, by session id hash (`jade/evals.py`). Minimum protected size before a promotion: the design target is 150 per family, and the code enforces it: `protected_test.min_samples: 150` in `policies/promotion.yaml` (W-52). The old value was 30. Until a group has 150 labeled protected rows, `jade improve promote` stops at `protected_test_non_inferior` ("fewer than 150"): that is the fail-closed outcome, not a fault. The protected split is 15% of the labeled rows, so about 1000 labeled rows in a group are needed before it can hold 150.

## 6. Budgets derived from baseline

| Metric | Baseline | Budget / target for Phase 3 exit | Rationale |
|--------|----------|----------------------------------|-----------|
| Unsafe accept (P < reject or exit≠0 followed by done without disposition) | 30% (heuristic) | ≤ 5% measured on ledger; 0 for risk ≥ high | plan priority 1 |
| Abstention coverage (share of decisions resolved without human) | unknown (no abstention existed) | ≥ 70% for low risk; report, don't target, for high | avoid escalation fatigue |
| Repeat-without-new-evidence | 20% | ≤ 3% | dedup |
| Provider error/unparsed | 9.4% | ≤ 2%, all typed | adapter |
| Hot-path p95 added latency per decision | ≈ 0.5 s per call, serial; up to 56 calls/file | ≤ 1.0 s per decision (one batched request, p95 672 ms at batch 8, + ledger write) | batching replaces serial calls |
| Cost | $0.013 / 14 days | ≤ $1 / month | not binding |
| Routing false positive (Jev called when FAST sufficed) | unknown | to be measured on ledger-derived routing rows (W-49 remainder); the 34 hand-written cases in `evals/routing` guard the router but do not measure the rate (section 5); target set after first measurement | plan §12: no invented targets |

## 7. Metrics computed by `jade eval`

Computed today (`jade/evals.py`, `jade eval run`): false accept, false reject, abstain rate, coverage and the selective risk at the operating point; reliability table (10 bins), Brier, log loss and ECE (point estimates); Wilson intervals; a paired bootstrap for the champion-versus-challenger comparison (`jade eval conditions`); subgroup slices by language (fa/en/unknown); `evals/run_cases.py` reports the case pass rate per language slice (fa/en/mixed/unknown) for the case files, including the hand-written sets of section 5. Latency and cost come from `evals/performance/run.py` and `jade ledger stats`; crash resumption is covered by a fault-injection test (`tests/acceptance/test_phase2_enforce.py`).

Added by W-52, all offline and tested against synthetic truth (`tests/test_eval_metrics_w52.py`):

| Metric | Where | Reading it |
|--------|-------|------------|
| Selective risk versus coverage | `jade eval run`, per group `risk_coverage` (`evals.risk_coverage_curve`) | Keeps the most confident share of rows (confidence = max(p, 1-p), call = p >= 0.5) and gives the error rate among them. A curve that rises with coverage means abstaining on the least confident rows pays; a flat one means the confidence carries no information. Rows of equal confidence are counted at their group's error rate, so the curve does not depend on ledger order. |
| Bootstrap interval on ECE | per group `ece_ci95` (`evals.ece_bootstrap`) | Percentile interval over the rows. ECE is biased upward on a small sample, so on few rows the interval can sit above the point estimate: read its width, and compare two models by overlap. |
| Top-1 and margin, choice questions | `jade eval run`, `choice` (`evals.choice_metrics`, rows from `evals.choice_rows`) | Needs a label per choice call (`jade label set`: yes means the top option was right). Top-1 accuracy with a Wilson interval, the margin between the two most probable options, and accuracy below and above the median margin. Heuristic labels never count. Empty until such labels exist. |
| FAST/JEV/DEEP confusion matrix | `jade eval routing [--cases FILE]` (`evals.routing_confusion_from_cases`) | `route.preflight` on the hand-written `evals/routing` cases against `should_route`; also recall per route, the rate at which a FAST case went to Jev (the section 6 false positive) and the rate at which a JEV case did not. The 34 cases are synthetic: this guards the router, it does not measure the live rate (section 5). Known gaps show off the diagonal on purpose. |
| Holm correction across metrics | `jade eval conditions`, `paired_metrics` (`evals.holm_adjust`) | Three comparisons that can go either way, each with a two-sided paired-bootstrap `p`, then one Holm family at alpha 0.05 (`holm_p`, `significant_after_holm`, `win`): `correct_decision` (condition 3's accept-or-not call being right, against bare `jev`, which is right exactly on the true claims), and `unsafe_accept_vs_chance` and `safe_accept_vs_chance` (condition 3's accepts against accepting the same share of rows at random). A judge that is noise, or abstains on everything, has no difference to show on any of them. Rule: a metric is a win only when `win` is true (significant after Holm and in the favourable direction). `paired_descriptive` reports `unsafe_accept`, `safe_accept` and `abstain` against bare `jev` as differences with an interval and an untested `p`: bare `jev` accepts every answer, so those can only move one way and say nothing about the judge. |
| Calls per decision | `jade eval run`, `operations.calls_per_decision` | Provider calls (cached repeats excluded) over the decisions in the ledger; mean over all decisions, mean over the decisions that called, and the maximum. |
| Cost per accepted verified decision | `operations.cost_per_verified_decision` | All spend on calls attached to a decision, divided by the decisions that reached SUCCEEDED and made at least one provider call (an observed, verified success: a shell command a human approved at the gate is not one). The spend of decisions that failed or were abandoned is in the numerator on purpose. `null` while no decision has succeeded. |
| Time to resolution, WAITING_HUMAN | `operations.time_to_resolution_waiting_human` | Seconds from entering WAITING_HUMAN to leaving it by any event (an answer, a deadline, a cancel): p50, p95, max, and the count and oldest age of waits still open. |
| Slice for risk >= high | per group `risk_high` | The same decision metrics over rows of risk high or critical, read at the high band (never looser than the low band). The section 6 target for this slice is zero unsafe accepts; the slice is empty until high-risk rows are labeled. |

Still not measurable from this repository: the DEEP verdict conditions (2) and (4) of section 8 (no DEEP labels exist), and any of the above on real traffic until labeled ledger rows exist (W-04, W-27). Every number computed on the synthetic sets is a check of the code, not a measurement of Jev.

## 8. Baseline conditions to compare (Phase 3)

1. Current behaviour (CLAUDE.md rule, bare `jev`) — reconstructed from `evals/baseline`.
2. Reasoning-only (DEEP verdict, no Jev).
3. Jev-only with registry questions and thresholds.
4. Cascade: FAST → JEV → DEEP → human.

Promotion of the cascade requires non-inferiority on unsafe accept vs (2) with the paired interval excluding a 3-point regression, lower latency than (2), and no safety-invariant violation.
