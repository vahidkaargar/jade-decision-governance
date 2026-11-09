# 07 — Autonomous improvement and work management

## 1. Improvement plane = task-observer + ledger mining

task-observer (v3.4.0, already active in every session) owns the loop *observe → log → weekly review → staged change → human approval* for instructions and skills. JADE does not duplicate it. JADE adds one producer and one evaluator:

- **Producer** `jade improve mine` (Phase 4, launchd daily): reads the ledger for patterns — `repeat_without_new_evidence`, low-P followed by done without disposition, provider errors by kind, questions with abstention > 50%, candidate sets that were degenerate, routing disagreements (FAST would have sufficed) — and writes (a) an observation file in the task-observer log for anything about instructions, and (b) an `improvement` record for anything about registry questions, thresholds, cache or batching.
- **Evaluator** (`jade eval propose` for a threshold, `jade improve shadow`, `jade improve protected`, `jade improve canary`): offline paired replay on dev+calibration splits with a validation holdout, never protected unless a human runs the protected check; writes the evidence into `results`; `promote` recomputes pass or fail from that evidence and reads `policies/promotion.yaml` for every limit (min samples, margins, alpha, shadow duration and samples, canary fraction, required results, who may auto-promote). A caller cannot attach a result. Candidates touching protected targets fail schema validation (`target_component` enum), and editing `policies/safety.yaml`/`promotion.yaml` is R-13 (permit, human TTY).

Separation of duties: the component that proposes (miner) never scores (evaluator), and neither can install: question templates are promoted by writing a new registry version and flipping the champion pointer, which is itself journaled and reversible with `jade improve rollback`.

Drift handling (Q6: follow upgrades): a changed resolved model string opens an informational `MODEL_DRIFT` work item (once per new string) and starts a new calibration group at default thresholds; a new question version starts its own group the same way, but no `QUESTION_DRIFT` item is opened (W-51). Nothing is frozen; the old group's thresholds are not inherited.

## 2. Work management

Contract: `schemas/work-item.schema.json`. Store: `work-items.jsonl` in this repo (Phase 1: also in the ledger, synced by `jade work sync`). Render: `BACKLOG.md`.

Rules:
- Every unresolved item has owner, priority, dependencies, rationale, next action and a reactivation trigger. "Owner: human" items are the only ones that may block.
- Aging P2→P1 at 14 days idle, P3→P2 at 30 days (run by every `jade work` command that writes). Not built: de-duplication by normalised title and target, which needs an `add` command (W-51).
- A human question carries context, impact, options, recommended default and consequence of no answer; a missing permission is typed `blocker`, never `ambiguity`. Batching in a 10-minute window is designed and not built (`group_window_s`, W-35).
- Resumption: the `SessionStart` hook prints open items whose trigger is `on_session_start` or a date that has come, plus pending permits and any `WAITING_HUMAN` decisions, so the next session continues without re-discovery. Items whose dependencies have closed are not announced (W-51). Leases: an item `in_progress` with no update for more than a day reverts to `open` (`jade work`, the history keeps dates, not times).
- Honest limitation: Claude Code has no resident agent. launchd can run `jade` maintenance (mining, drift checks, aging, rendering) but cannot reason or implement. "Continuous autonomy" here means *state survives and the next session resumes automatically*, nothing more.

## 3. Human-only boundaries (never automated)

Design approval (W-01); enabling the PreToolUse gate; any `critical` permit; edits to protected targets; CLAUDE.md text; threshold promotion; TypeSafe/OpenRouter key handling; retention changes; deleting ledger rows.
