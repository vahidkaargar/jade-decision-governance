# 02 — Decision lifecycle, state machine and contracts

## 1. Lifecycle in this environment

A *decision* is one governed judgment inside an agent task: "is this fix verified", "which strategy", "may I push". The agent (Claude Code) drives the lifecycle through `jade` CLI calls; the runtime journals every transition and refuses invalid ones. Execution itself happens in the harness (Bash/Edit/Write tools); JADE sees it at the `PreToolUse` boundary and records outcomes when the agent (or a hook) reports them.

```
jade decide open                  → RECEIVED → VALIDATED → CLASSIFIED
jade ask --decision <id>          → EVALUATING → {ACCEPTED | ABSTAINED | REJECTED | NEEDS_EVIDENCE}   (`jade route` only advises; it moves nothing)
jade decide event <id> authorize  → AUTHORIZED   (only from ACCEPTED; below risk high at once, at high and above only the PreToolUse gate
                                                  authorizes, with an issued permit: `jade permit request`, human `jade permit approve`)
PreToolUse gate                   → EXECUTING    (permit consumed)
jade decide outcome <id> ...      → VERIFYING → {SUCCEEDED | FAILED | OUTCOME_UNKNOWN}; from FAILED, `jade decide event <id> compensate|rollback`
```

Resumable side states: `WAITING_HUMAN`, `WAITING_DEPENDENCY`, `RETRY_SCHEDULED`. Terminal: `SUCCEEDED`, `FAILED`, `COMPENSATED`, `ROLLED_BACK`, `OUTCOME_UNKNOWN`, `REJECTED`, `CANCELLED`, `EXPIRED`.

## 2. Transition table

| From | Event | Guard | To | Actor |
|------|-------|-------|----|-------|
| — | `open` | payload validates against `decision.schema.json` | RECEIVED→VALIDATED (atomic) | agent |
| VALIDATED | `classify` | risk rule matched (`policies/risk.yaml`); rationale non-empty | CLASSIFIED | runtime |
| CLASSIFIED | `route` | preflight run; route ∈ {FAST, JEV, DEEP, ESCALATE, ASK} | EVALUATING | runtime |
| EVALUATING | `fast_result` | deterministic check returned a boolean with evidence ref | ACCEPTED / REJECTED | runtime |
| EVALUATING | `jev_result` | provider returned a parseable answer; question valid; not a dedup hit without new evidence (a dedup anchor is an answered call: DONE, exit 0, 3 or 4) | ACCEPTED (≥ accept_t) / ABSTAINED (band) / REJECTED (≤ reject_t) | runtime |
| EVALUATING | `jev_error` | provider UNAVAILABLE / RATE_LIMITED after retries, or INVALID_QUESTION | NEEDS_EVIDENCE (never ACCEPTED) | runtime |
| EVALUATING | `deep_result` | reasoning model's structured verdict attached with evidence refs | ACCEPTED / REJECTED / NEEDS_REVISION | agent |
| ABSTAINED / NEEDS_EVIDENCE | `add_evidence` | new `evidence_refs` ⇒ new `context_fingerprint`; revision_count < max (3) | EVALUATING | agent |
| ABSTAINED | `escalate` | risk ≥ medium, or revision_count = max (refused below that: `state_machine.next_state`) | WAITING_HUMAN | runtime |
| NEEDS_REVISION | `revise` | plan changed ⇒ new `plan_hash`; revision_count < max | VALIDATED | agent |
| NEEDS_REVISION | `escalate` | revision_count reached the max (the exit the `revise` refusal points to) | WAITING_HUMAN | agent |
| ACCEPTED | `authorize` | risk < high **or** permit `ISSUED`, unexpired, unused, `plan_hash` equal, no safety-invariant match | AUTHORIZED | runtime |
| ACCEPTED | `authorize` fails | permit missing/PENDING | WAITING_HUMAN | runtime |
| AUTHORIZED | `execute` | PreToolUse hook sees matching command; permit nonce consumed in same transaction | EXECUTING | hook |
| EXECUTING | `observe` | outcome record with `observation_type` | VERIFYING | agent/hook |
| VERIFYING | `verified` | `observation_type=observed` and `success=true` | SUCCEEDED | runtime |
| VERIFYING | `failed` | `success=false` | FAILED | runtime |
| VERIFYING | `unknown` | `observation_type=unknown` | OUTCOME_UNKNOWN | runtime |
| FAILED | `compensate` / `rollback` | compensation plan ref present | COMPENSATED / ROLLED_BACK | agent |
| OUTCOME_UNKNOWN | `reconcile` | external state read and attached | VERIFYING | agent |
| WAITING_HUMAN | `human_answer` / `human_accept` / `human_reject` | from a human terminal: `jade permit approve` for a permit, `jade decide event <id> human_answer\|human_accept\|human_reject` for the decision | previous state re-entered / ACCEPTED / REJECTED | human |
| any state that waits (everything non-terminal except EXECUTING, VERIFYING, OUTCOME_UNKNOWN) | `deadline` | `expires_at` passed (refused before) | EXPIRED, and the decision's open permits are revoked | runtime (sweep on SessionStart, daily, and at every gated PreToolUse) |
| EXECUTING / VERIFYING | `deadline` | `expires_at` passed with no outcome | `observe` then `unknown`, or `unknown`: OUTCOME_UNKNOWN, never SUCCEEDED and never EXPIRED (SI-07) | runtime (sweep) |
| any non-terminal | `cancel` | reason; revokes the decision's open permits. From EXECUTING, VERIFYING or OUTCOME_UNKNOWN it lifts the SI-07 hold, so it needs a human terminal | CANCELLED | agent/human |

Rules enforced in code:
- No edge from EVALUATING or ACCEPTED to EXECUTING. `jade hook pretool` checks `AUTHORIZED`.
- `ACCEPTED` is a quality judgment; `AUTHORIZED` is a permission fact; `SUCCEEDED` needs `observed`. An *estimated* outcome can at most produce `VERIFYING`, never `SUCCEEDED`.
- `OUTCOME_UNKNOWN` blocks automatic retry of irreversible actions: `retry` is only legal after `reconcile`.
- Bounded loops: `revision_count ≤ 3`, provider retries ≤ 2, `expires_at` mandatory on every non-terminal state (default 24 h; permits 30 min).
- Concurrency: one writer (SQLite `BEGIN IMMEDIATE`; a nested step is a savepoint, so a permit consume and its decision steps commit or roll back together); a decision is owned by the `session_id` that opened it; another session may only `cancel` or answer for the human (enforced by `state_machine.apply` for every CLI call, which passes `caller_session`).
- Who may send what through `jade decide event`: an agent session sends evaluation results, evidence, `escalate`, `authorize`, `cancel`, and `execute` below risk `high`. `execute` at risk >= high belongs to the hook (a matching command and a consumed permit); `observe`, `verified`, `failed`, `unknown` and `reconcile` follow only from `jade decide outcome`, which stores the observation first; `deadline` is the runtime's (a `context_changed` event was deleted: a permit whose precondition moved is refused at use, docs/05 T6 and T8, W-35); the `human_*` events need a human terminal. `SUCCEEDED` is refused unless the latest outcome row is `observed` with `success=true`, whoever calls.
- A permit lives only while its decision does: a decision that is cancelled, expired or finished revokes its PENDING and ISSUED permits in the same transaction, approval and use are refused for a decision that is not WAITING_HUMAN or AUTHORIZED, and a changed `policy_version` revokes every open permit (docs/05 T6, T20).
- `plan_hash` is sha256 of the canonical JSON of the tool, the command or path as written (leading and trailing whitespace trimmed, nothing inside folded) and the symlink-resolved cwd.
- Re-evaluation triggers: changed `plan_hash`, `policy_version`, `resolved_model`, or precondition fingerprint.

## 3. Contracts

All records: `schema_version` (semver string), `additionalProperties: false`, timestamps RFC 3339 UTC, ids `^[a-z]+_[0-9a-f]{12}$` (prefix `dec_`, `q_`, `prm_`, `out_`, `wi_`, `imp_`). Files: `schemas/<name>.schema.json`; examples: `schemas/examples/<name>.json`; test: `tests/test_schemas.py`.

### 3.1 Decision (`decision.schema.json`)
`decision_id, task_id, tenant_scope ("local:<user>"), objective, candidate_actions[], constraints[], assumptions[], evidence_refs[], context_fingerprint (sha256 of canonical evidence+plan), context_freshness {observed_at, ttl_s}, risk {level, reversibility, environment, blast_radius, data_sensitivity, permission_scope, rationale, rule_id}, route, evaluation_refs[], policy_version, status, timestamps {created_at, updated_at, expires_at}, trace_id, session_id, revision_count`, and optionally `human_only`, `ho_ids[]` (the human-only classes the gate named) and `ask {channel (chat|dialog), ask_id, answer, answered_at}` (the human's answer to a call in the chat or in a dialog). Route `ASK` means the human decides in the chat or a dialog; a decision with that route is accepted only with `ask.answer = allow` recorded (docs/12).

### 3.2 Jev Question (`question.schema.json`) — the registry entry
`question_id, version, primitive (noul|choice|score), task_family, objective, input_schema (JSON Schema for state), output_schema, required_evidence[], template {instructions, criteria}, prompt_or_template_version, model_version (request id + accepted resolved ids), calibration_group, threshold_policy {accept, reject, metric}, abstention_behavior, fallback (deep|human|fast), dependencies[], batch_eligibility, no_match_option (for choice), min_options`.

### 3.3 Execution Permit (`permit.schema.json`)
`permit_id, decision_id, principal, authorized_action (canonical verb), resource_scope, environment, plan_hash, preconditions[] {kind, fingerprint}, policy_version, approval_reference (required when risk ≥ high: `tty:<user>@<ts>`), issued_at, expires_at, nonce, usage_limit (1), uses, state (PENDING|ISSUED|CONSUMED|REVOKED|EXPIRED), seal (HMAC-SHA256 hex over the fields above and the row columns, set when the permit becomes ISSUED; the hook refuses an ISSUED permit whose seal does not match; `state` and `uses` are not sealed)`.

### 3.4 Outcome (`outcome.schema.json`)
`outcome_id, decision_id, execution_id, observation_type (observed|estimated|unknown), verification_method, measurements{}, attribution_quality (direct|indirect|none), observed_at, evidence_refs[], success (true|false|null)`. Invariant: `success` is `null` iff `observation_type = unknown`; `estimated` never yields SUCCEEDED.

### 3.5 Work Item (`work-item.schema.json`)
`id, parent, type (blocker|ambiguity|risk|debt|followup), priority (P0–P3), status (open|in_progress|waiting_human|blocked|done|dropped), owner, dependencies[], impact, rationale, evidence_refs[], next_action, retry_or_revisit_trigger, created_at, updated_at, history[]`.

### 3.6 Improvement Candidate (`improvement.schema.json`)
`candidate_id, baseline_version, target_component (enum of permitted targets only), change_diff, hypothesis, predicted_benefit, risk, evaluation_plan, dataset_versions[], results, promotion_decision (pending|promoted|rejected|rolled_back), rollout_scope, rollback_reference, audit_trail[]`. The enum excludes every protected target (`policies/safety.yaml`), so a candidate against a protected target fails validation.

### 3.7 Jev call row (ledger-internal, not a contract)
`call_id, decision_id?, question_id, question_version, request_model, resolved_model, provider, provider_request_id, state_hash, state_bytes, answers_json, latency_ms, input_tokens, output_tokens, cost_usd, error_kind, exit_code, session_id, ts`. State text itself is stored only after redaction (`docs/05`), capped at 32 KiB. Decision objectives and commands, permit commands, outcomes and event details are stored redacted too, and the retention purge clears that text as well (statuses, hashes and metrics stay).

## 4. Serialization and privacy
Canonical JSON (sorted keys, no whitespace) for every hash. Redaction before storage and before sending: Keychain-shaped tokens, `sk-`/`ghp_`/`glpat-` prefixes, `Authorization:` lines, `.env` assignments, email addresses outside the user's own. Redaction is a pure function with its own tests (Phase 1).
