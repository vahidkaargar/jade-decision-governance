# 06 — Roadmap, acceptance tests, rollout and rollback

Completion states (plan §16): **DESIGN_READY** → **IMPLEMENTED** → **VALIDATED** → **PRODUCTION_APPROVED**. This repo is at DESIGN_READY-candidate; the human gate is BACKLOG W-01. Phases 1–5 below are implementation and are **not started**.

## Phase 0 — Discover (done 2026-11-08)
Inventory, live API probes, baseline mining, ADRs, contracts, policies, threat model, this roadmap. Evidence: `docs/00`, `evals/baseline/summary-2026-11-08.json`, `adapters/jev/probe_openrouter.py` output recorded in `docs/00` S1–S12.

## Executable today
`uv run pytest -q` runs 46 contract/policy tests. `tests/acceptance/` holds the Phase 1–5 acceptance tests as runnable pytest files that skip until the `jade` package exists (`importorskip`); they are the checklist each phase must turn green. `evals/replay_policy.py` (policy replay over recorded calls) and `evals/performance/run.py` (live latency/batch harness) run now and are the benchmark harness seeds; `evals/baseline/mine_transcripts.py` rebuilds the baseline dataset.

## Phase 1 — Instrument (no behaviour change)
Scope: `jade` package (`uv tool install`), SQLite ledger, OpenRouter adapter with typed errors and bounded retries, `redact()`, `jev` compatibility shim that journals every call and prints exactly today's output format, `jade ledger stats`, `work-items.jsonl` + `jade work render`.
Acceptance tests (pytest): `test_adapter_contract` (recorded fixtures: noul/choice/score/batch/400/429/529/timeout), `test_ledger_write_ahead` (crash between request and response leaves `EVALUATING` row, never a result), `test_redact_corpus` (fixture of 40 secrets → 0 residual), `test_shim_output_identical` (golden outputs vs current `jev`), `test_duplicate_detection`.
Exit: 14 days of ledger data; `jade ledger stats` reproduces §4 of `docs/04` from facts.
Rollback: `jev` shim replaced by the saved original (`~/.local/bin/jev.orig`), one `mv`.

## Phase 2 — Enforce
Scope: question registry + `jade question lint`; `jade route` preflight; exit codes 0/3/4/5/6; `jade decide` state machine with journaled transitions; permits with TTY approval; `PreToolUse` gate for risk ≥ high; `Stop` hook reminder for undisposed abstain/reject; `SessionStart` resumption of work items; CLAUDE.md Jev section rewritten (human-approved text).
Acceptance tests: `test_state_machine_transitions` (every row of `docs/02` §2 plus every forbidden edge), `test_crash_recovery` (kill between event and state write → consistent on reopen), `test_permit_single_use_and_ttl`, `test_permit_plan_hash_mismatch_blocks`, `test_approve_refuses_non_tty`, `test_hook_fails_closed_on_internal_error_for_high_risk`, `test_safety_invariants_block_regardless_of_permit`, `test_degenerate_choice_rejected`, `test_question_lint_rules`, `test_duplicate_delivery_idempotent` (same hook event twice).
Live checks: `jade check` (adapter canary with known-answer question, hook wiring, ledger writable) exits non-zero on dead wiring; hung off the host's existing health-check command.
Exit: on representative replay (`evals/regression`), 0 unsafe accepts for risk ≥ high; abstain/reject never followed by done without disposition in 14 ledger days (measured, target ≤ 5%).
Rollback: remove the two hook entries from `~/.claude/settings.json` (documented diff), keep ledger. Permits become advisory; nothing else changes.

## Phase 3 — Calibrate
Scope: label pipeline (outcome join, user-correction detection, adjudication queue ≤ 10/week), eval sets and splits, `jade eval` metrics, performance harness, threshold proposals as improvement candidates (human-promoted).
Acceptance: `test_split_no_session_leak`, `test_metrics_against_synthetic_truth`, `test_protected_set_unreadable_without_tty`; performance report with p50/p95/p99 at 1/4/8 parallel and batch 1/3/8; the four baseline conditions of `docs/04` §8 compared with paired intervals.
Exit: budgets in `docs/04` §6 met or consciously re-set with rationale.
Rollback: thresholds revert to docs defaults by deleting `calibration/current.json`.

## Phase 4 — Improve (async, bounded)
Scope: `jade improve mine` → task-observer observations + improvement records; offline replay; shadow; canary only for `question_template` and `cache_ttl` after human opt-in; kill switch file; champion/challenger pins.
Acceptance: `test_candidate_cannot_target_protected`, `test_promotion_requires_protected_pass`, `test_kill_switch_freezes`, `test_shadow_adds_zero_hot_path_latency`.
Exit: one full cycle (mine → candidate → replay → shadow → human decision) completed and audited.
Rollback: `FREEZE` file; `jade improve rollback <candidate>` restores champion.

## Phase 5 — Optimise and roll out
Scope: profile real p95; batch all family questions into one request; remove redundant calls found in ledger; canary thresholds (if authorized); rollback drill; drift monitoring via launchd; audit review.
Acceptance: rollback drill executed and timed; drift canary fires on an injected model-string change; p95 within budget.
Exit: VALIDATED. PRODUCTION_APPROVED only by the human (this is personal tooling: "production" = enabling the PreToolUse gate for critical rules on this Mac).

## Dependencies between phases
1 → 2 (ledger needed for dedup and permits) → 3 (labels need outcomes) → 4 (candidates need eval sets) → 5. Phase 1 may start the day W-01 is approved.

## Estimated size (for planning only)
Phase 1 ≈ 600–900 lines Python + tests; Phase 2 ≈ 1,200–1,800; Phase 3 ≈ 800; Phase 4 ≈ 600; Phase 5 mostly measurement. All stdlib + `jsonschema` + `pyyaml`; no services.
