# 03 — Routing and question design

Policy files: `policies/routing.yaml`, `policies/risk.yaml`, `policies/escalation.yaml`. This document explains them and the question registry.

## 1. Two separate classifications

**Suitability decides the route** (FAST / JEV / DEEP / ESCALATE). **Risk decides rigor** (evidence required, permit required, verification required). The baseline shows both were conflated: `jev verify` was called for everything from "is 2+2=4" style checks (should be FAST) to architecture choices (should be DEEP), and the resulting P was treated as permission.

## 2. Preflight (ordered)

1. **Deterministic?** Exit codes, file hashes, regex, schema validation, arithmetic, permission bits → FAST. No model call. Baseline impact: a share of the 684 verify calls were about test exit codes already visible in tool output.
2. **Safety invariant hit?** → ESCALATE (blocked), regardless of evidence.
3. **Evidence present?** A verify question with no raw evidence reference (tool output, diff, document) → NEEDS_EVIDENCE. Baseline: 97% of calls passed evidence inline on the command line, truncated and often a paraphrase. New default: `--state-file <path>` or stdin; the state must include the raw excerpt and its source.
4. **Duplicate?** Same `question_id` + canonical state hash in session → return the cached answer and flag `repeat_without_new_evidence`. Baseline: 20% of calls were repeats. The flag is a ledger pattern the improvement plane mines.
5. **Candidates valid?** Choice with < 2 eligible options after code-level eligibility filtering → NEEDS_REVISION (the API will happily pick the only option with confidence 1.0; S9).
6. **Primitive suitable?** Atomic, bounded, answerable from state. Multi-step synthesis, architecture, open-ended "what should I do" → DEEP.
7. **Context fresh?** else NEEDS_EVIDENCE.
8. **Worth it?** Until Phase 3 data exists: always evaluate for risk ≥ medium; for low risk only when the agent is about to claim completion (the CLAUDE.md trigger).

## 3. Routes

- **FAST** — code. Emits a decision with `evaluation_refs` pointing at the deterministic check.
- **JEV** — one Decisions API request carrying every independent registry question for the task family (batching: verified S7; up to 8 questions, 0.68 s for 3). Questions cannot depend on each other's answers within a batch; a dependent question is a second request.
- **DEEP** — the reasoning model (Claude) answers with a structured verdict and evidence refs. The HYBRID pattern lives here: DEEP decomposes, emits atomic JEV questions, and code combines them with explicit rules ("any serious violation" = max, not mean — sde_cascade cookbook).
- **ESCALATE** — human. Questions are batched (10-minute window) and each carries context, impact, options, recommended default, and the consequence of no answer.

## 4. Question registry

Each entry validates against `schemas/question.schema.json`. Initial families and templates (v1), to be shipped as `registry/*.json` in Phase 2:

| question_id | primitive | family | notes |
|-------------|-----------|--------|-------|
| `q.verify_claim.supported_by_evidence` | noul | verify_claim | Replaces today's `jev verify`. Negative criterion names paraphrase/insufficient evidence explicitly. |
| `q.verify_claim.evidence_is_raw` | noul | evidence_quality | Companion: is the evidence raw tool output rather than the agent's summary? Low P → NEEDS_EVIDENCE. |
| `q.root_cause.best_explanation` | choice | root_cause | Requires `none_of_these` companion Noul `q.root_cause.any_fits`. |
| `q.strategy.best_option` | choice | strategy_choice | Options must each carry a one-line tradeoff; margin recorded; low confidence acceptable for harmless preferences. |
| `q.risk.irreversible` / `q.risk.touches_production` / `q.risk.exposes_secret` | noul ×3 | risk_triage | Batched; code combines with max. Advisory only: `policies/risk.yaml` rules (plus the machine-local overlay) decide; Jev may only *raise* the level, never lower it. |
| `q.evidence.quality` | score | evidence_quality | Levels: none / summary only / partial raw / complete raw with source. Ranks, never gates. |
| `q.routing.suitable_for_jev` | choice | routing | Used in evals to measure routing false positives/negatives, not in the hot path. |

Quality gates (`jade question lint`): one concept; explicit true/false criteria; no sibling dependency; state fields named and referenced in backticks; a no-match path for open sets; banned leading tokens.

## 5. State construction

State is an object with named fields (docs: "use an object for most requests"): `claim`, `evidence` (raw excerpt ≤ 8 KiB per source), `evidence_source`, `environment`, `prior_attempts` (count only, not previous P values — avoids self-confirmation). Redacted before sending (`docs/05` §3). Provenance kept in the ledger, not sent.

## 6. What stays in the skill (documentation) vs runtime (enforced)

| Behaviour | Where |
|-----------|-------|
| When to call Jev, how to phrase, which family | SKILL.md (after W-01) |
| Exit codes 3/4/5/6 are never "yes" | runtime (`jade ask`) + Stop-hook reminder when an abstain/reject in the session has no disposition |
| Permit for high/critical | runtime (`PreToolUse`) |
| Repeat detection | runtime (dedup) |
| Evidence raw, not paraphrase | skill guidance + `q.verify_claim.evidence_is_raw` companion |
