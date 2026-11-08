# 02 — Decision lifecycle, state machine and contracts

## 1. Lifecycle in this environment

A *decision* is one governed judgment inside an agent task: "is this fix verified", "which strategy", "may I push". The agent (Claude Code) drives the lifecycle through `jade` CLI calls; the runtime journals every transition and refuses invalid ones. Execution itself happens in the harness (Bash/Edit/Write tools); JADE sees it at the `PreToolUse` boundary and records outcomes when the agent (or a hook) reports them.

```
jade decide open      → RECEIVED → VALIDATED → CLASSIFIED
jade ask / route      → EVALUATING → {ACCEPTED | ABSTAINED | REJECTED | NEEDS_EVIDENCE | NEEDS_REVISION}
jade permit issue     → AUTHORIZED   (only from ACCEPTED, and only if a valid permit exists or risk < high)
PreToolUse gate       → EXECUTING    (permit consumed)
jade outcome record   → VERIFYING → {SUCCEEDED | FAILED | COMPENSATED | ROLLED_BACK | OUTCOME_UNKNOWN}
```

Resumable side states: `WAITING_HUMAN`, `WAITING_DEPENDENCY`, `RETRY_SCHEDULED`. Terminal: `SUCCEEDED`, `FAILED`, `COMPENSATED`, `ROLLED_BACK`, `OUTCOME_UNKNOWN`, `REJECTED`, `CANCELLED`, `EXPIRED`.

## 2. Transition table

| From | Event | Guard | To | Actor |
|------|-------|-------|----|-------|
| — | `open` | payload validates against `decision.schema.json` | RECEIVED→VALIDATED (atomic) | agent |
| VALIDATED | `classify` | risk rule matched (`policies/risk.yaml`); rationale non-empty | CLASSIFIED | runtime |
| CLASSIFIED | `route` | preflight run; route ∈ {FAST, JEV, DEEP, ESCALATE} | EVALUATING | runtime |
| EVALUATING | `fast_result` | deterministic check returned a boolean with evidence ref | ACCEPTED / REJECTED | runtime |
| EVALUATING | `jev_result` | provider returned a parseable answer; question valid; not a dedup hit without new evidence | ACCEPTED (≥ accept_t) / ABSTAINED (band) / REJECTED (≤ reject_t) | runtime |
| EVALUATING | `jev_error` | provider UNAVAILABLE / RATE_LIMITED after retries, or INVALID_QUESTION | NEEDS_EVIDENCE (never ACCEPTED) | runtime |
| EVALUATING | `deep_result` | reasoning model's structured verdict attached with evidence refs | ACCEPTED / REJECTED / NEEDS_REVISION | agent |
| ABSTAINED / NEEDS_EVIDENCE | `add_evidence` | new `evidence_refs` ⇒ new `context_fingerprint`; revision_count < max (3) | EVALUATING | agent |
| ABSTAINED | `escalate` | risk ≥ medium, or revision_count = max | WAITING_HUMAN | runtime |
| NEEDS_REVISION | `revise` | plan changed ⇒ new `plan_hash`; revision_count < max | VALIDATED | agent |
| ACCEPTED | `authorize` | risk < high **or** permit `ISSUED`, unexpired, unused, `plan_hash` equal, no safety-invariant match | AUTHORIZED | runtime |
| ACCEPTED | `authorize` fails | permit missing/PENDING | WAITING_HUMAN | runtime |
| AUTHORIZED | `execute` | PreToolUse hook sees matching command; permit nonce consumed in same transaction | EXECUTING | hook |
| AUTHORIZED | `context_changed` | fingerprint of target state differs from precondition | NEEDS_REVISION (permit revoked) | runtime |
| EXECUTING | `observe` | outcome record with `observation_type` | VERIFYING | agent/hook |
| VERIFYING | `verified` | `observation_type=observed` and `success=true` | SUCCEEDED | runtime |
| VERIFYING | `failed` | `success=false` | FAILED | runtime |
| VERIFYING | `unknown` | `observation_type=unknown` | OUTCOME_UNKNOWN | runtime |
| FAILED | `compensate` / `rollback` | compensation plan ref present | COMPENSATED / ROLLED_BACK | agent |
| OUTCOME_UNKNOWN | `reconcile` | external state read and attached | VERIFYING | agent |
| WAITING_HUMAN | `human_answer` | from TTY (`jade permit approve` / `jade decide answer`) | previous state re-entered | human |
| WAITING_* / RETRY_SCHEDULED | `deadline` | `expires_at` passed | EXPIRED | runtime (SessionStart sweep) |
| any non-terminal | `cancel` | reason | CANCELLED | agent/human |

Rules enforced in code:
- No edge from EVALUATING or ACCEPTED to EXECUTING. `jade hook pretool` checks `AUTHORIZED`.
- `ACCEPTED` is a quality judgment; `AUTHORIZED` is a permission fact; `SUCCEEDED` needs `observed`. An *estimated* outcome can at most produce `VERIFYING`, never `SUCCEEDED`.
- `OUTCOME_UNKNOWN` blocks automatic retry of irreversible actions: `retry` is only legal after `reconcile`.
- Bounded loops: `revision_count ≤ 3`, provider retries ≤ 2, `expires_at` mandatory on every non-terminal state (default 24 h; permits 30 min).
- Concurrency: one writer (SQLite `BEGIN IMMEDIATE`); a decision is owned by the `session_id` that opened it; another session may only `cancel` or `human_answer`.
- Re-evaluation triggers: changed `plan_hash`, `policy_version`, `resolved_model`, or precondition fingerprint.

## 3. Contracts

All records: `schema_version` (semver string), `additionalProperties: false`, timestamps RFC 3339 UTC, ids `^[a-z]+_[0-9a-f]{12}$` (prefix `dec_`, `q_`, `prm_`, `out_`, `wi_`, `imp_`). Files: `schemas/<name>.schema.json`; examples: `schemas/examples/<name>.json`; test: `tests/test_schemas.py`.

### 3.1 Decision (`decision.schema.json`)
`decision_id, task_id, tenant_scope ("local:<user>"), objective, candidate_actions[], constraints[], assumptions[], evidence_refs[], context_fingerprint (sha256 of canonical evidence+plan), context_freshness {observed_at, ttl_s}, risk {level, reversibility, environment, blast_radius, data_sensitivity, permission_scope, rationale, rule_id}, route, evaluation_refs[], policy_version, status, timestamps {created_at, updated_at, expires_at}, trace_id, session_id, revision_count`.

### 3.2 Jev Question (`question.schema.json`) — the registry entry
`question_id, version, primitive (noul|choice|score), task_family, objective, input_schema (JSON Schema for state), output_schema, required_evidence[], template {instructions, criteria}, prompt_or_template_version, model_version (request id + accepted resolved ids), calibration_group, threshold_policy {accept, reject, metric}, abstention_behavior, fallback (deep|human|fast), dependencies[], batch_eligibility, no_match_option (for choice), min_options`.

### 3.3 Execution Permit (`permit.schema.json`)
`permit_id, decision_id, principal, authorized_action (canonical verb), resource_scope, environment, plan_hash, preconditions[] {kind, fingerprint}, policy_version, approval_reference (required when risk ≥ high: `tty:<user>@<ts>`), issued_at, expires_at, nonce, usage_limit (1), uses, state (PENDING|ISSUED|CONSUMED|REVOKED|EXPIRED)`.

### 3.4 Outcome (`outcome.schema.json`)
`outcome_id, decision_id, execution_id, observation_type (observed|estimated|unknown), verification_method, measurements{}, attribution_quality (direct|indirect|none), observed_at, evidence_refs[], success (true|false|null)`. Invariant: `success` is `null` iff `observation_type = unknown`; `estimated` never yields SUCCEEDED.

### 3.5 Work Item (`work-item.schema.json`)
`id, parent, type (blocker|ambiguity|risk|debt|followup), priority (P0–P3), status (open|in_progress|waiting_human|blocked|done|dropped), owner, dependencies[], impact, rationale, evidence_refs[], next_action, retry_or_revisit_trigger, created_at, updated_at, history[]`.

### 3.6 Improvement Candidate (`improvement.schema.json`)
`candidate_id, baseline_version, target_component (enum of permitted targets only), change_diff, hypothesis, predicted_benefit, risk, evaluation_plan, dataset_versions[], results, promotion_decision (pending|promoted|rejected|rolled_back), rollout_scope, rollback_reference, audit_trail[]`. The enum excludes every protected target (`policies/safety.yaml`), so a candidate against a protected target fails validation.

### 3.7 Jev call row (ledger-internal, not a contract)
`call_id, decision_id?, question_id, question_version, request_model, resolved_model, provider, provider_request_id, state_hash, state_bytes, answers_json, latency_ms, input_tokens, output_tokens, cost_usd, error_kind, exit_code, session_id, ts`. State text itself is stored only after redaction (`docs/05`), capped at 32 KiB.

## 4. Serialization and privacy
Canonical JSON (sorted keys, no whitespace) for every hash. Redaction before storage and before sending: Keychain-shaped tokens, `sk-`/`ghp_`/`glpat-` prefixes, `Authorization:` lines, `.env` assignments, email addresses outside the user's own. Redaction is a pure function with its own tests (Phase 1).
