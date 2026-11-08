# 05 — Threat model

Adversary assumption: the operator is trusted; threats come from content the agent reads (repos, web, tool output, third-party skills), from provider drift, and from the agent's own over-confidence. Nothing here depends on the operator being hostile, so tamper-evident logs are out of scope (ADR-08).

| # | Threat | Vector here | Mitigation | Enforced by |
|---|--------|-------------|------------|-------------|
| T1 | Prompt injection via evidence | A README or tool output says "approved, proceed" and the agent passes it as state | State is data; approval is only `permit.approval_reference = tty:*`; `jade ask` output never contains the word "authorized"; adversarial eval set | runtime + evals |
| T2 | Forged approval | Agent runs `jade permit approve` itself | `approve` refuses when not a TTY or when `CLAUDE_CODE`/`CLAUDECODE` env present; PreToolUse also blocks `jade permit approve` from the agent (R-13-like rule) | runtime + hook |
| T3 | Compromised third-party skill | eran-broder/jev-skills ships fail-open hooks exfiltrating tool output | None installed; inventory §4 documents rejection; `skills-discovery-guard` still active | policy |
| T4 | Secret exfiltration to provider | Key or token in `-s` state | `redact()` before send and store; SI-05 block pattern; state capped 32 KiB | runtime |
| T5 | Authority spoofing by model output | "Jev said 0.98 so it is safe" | SI-01; permits never derived from P; risk rules can only be raised by Jev, never lowered | runtime |
| T6 | TOCTOU between permit and execution | Plan changes after approval | `plan_hash` recomputed from the actual command in PreToolUse; preconditions (git HEAD, file sha) re-checked; permit single-use with 30-min TTL | hook |
| T7 | Duplicate external side effects | Retry after OUTCOME_UNKNOWN | State machine forbids retry before reconcile; permits are single-use; Jev calls are side-effect free and may be retried | runtime |
| T8 | Stale cache | Cached judgment applied to changed context | Cache key includes canonical state; TTL 30 min; authorization never cached; `context_changed` revokes permits | runtime |
| T9 | Provider drift / model change | Resolved model string changes silently | Ledger stores resolved model; `MODEL_DRIFT` work item; new snapshot = new calibration group at default thresholds (Q6: no freeze); `jade check` canary question with known answer | runtime + launchd |
| T10 | Benchmark leakage / poisoning | Candidate generator reads protected set; transcript-mined set overlaps live | `protected/` readable only by `jade eval run --protected` (human TTY); split by session hash; candidates record `dataset_versions` | runtime + policy |
| T11 | Reward hacking / proxy gaming | Challenger lowers abstention by always accepting | Pass criteria include unsafe-accept with CI, not only coverage; protected test; human promotion for thresholds | promotion.yaml |
| T12 | Runaway self-modification | Candidate edits safety.yaml or its own promotion rule | `improvement.target_component` enum excludes protected targets; R-13 makes those edits high-risk (permit) | schema + hook |
| T13 | Recursive evaluation DoS | DEEP emits questions that emit questions | `revision_count ≤ 3`, `max_questions_per_request 8`, `max_parallel 4`, deadline 20 s, daily call budget (default 500) with soft alarm | runtime |
| T14 | Escalation fatigue | Every medium decision pings the human | Batching window; recommended default; abstention budget tracked; only high/critical require TTY | policy |
| T15 | Data retention / privacy | Ledger stores project text | Redaction; state cap; ledger local only (`~/.local/share/jade`), mode 600; `jade ledger purge --older-than 90d` (default, human decision Q7) removes state_bytes while keeping metrics | runtime |
| T16 | Cross-tenant contamination | n/a single tenant | `tenant_scope` constant; revisit if multi-host | — |
| T17 | Hook failure fails open | Hook crashes → Claude Code proceeds | PreToolUse gate exits 2 on any internal error for risk ≥ high (fail closed); low risk fails open by design, logged | hook |

## 3. Redaction function (contract)

`redact(text) -> (text', hits)`: replaces matches of SI-05 patterns, `Authorization:` header values, `password=`/`token=`/`secret=` assignments, Keychain `-w` outputs, and non-owner email addresses with `[REDACTED:<kind>]`. Pure, deterministic, unit-tested with a fixture corpus (Phase 1). Residual match after redaction blocks the call (exit 6).

## 4. What a Markdown skill cannot do

A SKILL.md can shape behaviour; it cannot stop a tool call. Every "must" in this design that protects a side effect is implemented as exit code, hook, or schema, and the skill merely explains it. The measured 30% low-P→done rate under the current prose-only rule is the evidence for this split.
