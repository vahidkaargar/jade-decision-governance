# 07 — Autonomous improvement and work management

## 1. Improvement plane = task-observer + ledger mining

task-observer (v3.4.0, already active in every session) owns the loop *observe → log → weekly review → staged change → human approval* for instructions and skills. JADE does not duplicate it. JADE adds one producer and one evaluator:

- **Producer** `jade improve mine` (Phase 4, launchd daily): reads the ledger for patterns — `repeat_without_new_evidence`, low-P followed by done without disposition, provider errors by kind, questions with abstention > 50%, candidate sets that were degenerate, routing disagreements (FAST would have sufficed) — and writes (a) an observation file in the task-observer log for anything about instructions, and (b) an `improvement` record for anything about registry questions, thresholds, cache or batching.
- **Evaluator** `jade eval replay <candidate>`: offline paired replay on dev+calibration splits, never protected; writes `results`; promotion per `policies/promotion.yaml`. Candidates touching protected targets fail schema validation (`target_component` enum), and editing `policies/safety.yaml`/`promotion.yaml` is R-13 (permit, human TTY).

Separation of duties: the component that proposes (miner) never scores (evaluator), and neither can install: question templates are promoted by writing a new registry version and flipping the champion pointer, which is itself journaled and reversible with `jade improve rollback`.

Drift handling (Q6: follow upgrades): a changed resolved model string or question version opens an informational `MODEL_DRIFT`/`QUESTION_DRIFT` work item and starts a new calibration group at default thresholds. Nothing is frozen; the old group's thresholds are not inherited.

## 2. Work management

Contract: `schemas/work-item.schema.json`. Store: `work-items.jsonl` in this repo (Phase 1: also in the ledger, synced by `jade work sync`). Render: `BACKLOG.md`.

Rules:
- Every unresolved item has owner, priority, dependencies, rationale, next action and a reactivation trigger. "Owner: human" items are the only ones that may block.
- Deduplicate by normalised title + target; aging P2→P1 at 14 days idle, P3→P2 at 30 days.
- Human questions are batched (10-minute window, or at turn end) and each carries context, impact, options, recommended default and consequence of no answer. A missing permission is typed `blocker`, never `ambiguity`.
- Resumption: the `SessionStart` hook (Phase 2) prints items whose trigger is `on_session_start` or whose dependencies closed, plus any `WAITING_HUMAN` decisions, so the next session continues without re-discovery. Leases: an item `in_progress` for > 24 h without a history entry reverts to `open`.
- Honest limitation: Claude Code has no resident agent. launchd can run `jade` maintenance (mining, drift checks, aging, rendering) but cannot reason or implement. "Continuous autonomy" here means *state survives and the next session resumes automatically*, nothing more.

## 3. Human-only boundaries (never automated)

Design approval (W-01); enabling the PreToolUse gate; any `critical` permit; edits to protected targets; CLAUDE.md text; threshold promotion; TypeSafe/OpenRouter key handling; retention changes; deleting ledger rows.
