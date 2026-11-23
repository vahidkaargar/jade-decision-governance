# 08 Security and operations

Status: Planning only. Nothing in this document is implemented.

Scope: v7 security and operations, delta on v6. Threats stay in [docs/05](../../05-threat-model.md) (T1 to T30), v6 delta in [v6 doc 05](../../14-v6/05-security-and-autonomy-model.md) s7-8. Tags: [fact] (with source), [planned], [assumption], [hypothesis], [unknown]. Meets V7-R124.

`EV-nn`, `U-nn` = rows of `EVD/v7-plan/evidence-index.md` (2026-11-16), `EVD` as defined there; `REPORT` = `EVD/jev-models/REPORT.md`; "REPORT rule N" = its section 5 list. Repo paths at `8dfa86d` (parent `e9412f3` = origin/main).

## 1. Fixed principles (V7-R16, V7-R14, V7-A85, V7-A86)

| # | Principle | Today |
|---|---|---|
| P1 | A probability is never permission. No model output, majority, confidence score or approval of a different action authorizes a side effect. Sole exception (ADR-13): level low/medium Jev ACCEPT inside its band, never HO, SI, R-13, R-18, R-22 (1.1); v7 adds none (H-02). | [fact] SI-01, `policies/safety.yaml`; permit bound to one `plan_hash` (`jade/permits.py:673-676`) |
| P2 | Deterministic gates (SI, R, HO) run before and independent of any model; untrusted text stays data. | [fact] [docs/05 T5, T17](../../05-threat-model.md); v6 doc 05 section 4.1 |
| P3 | Only the human sets the JADE level and gate mode. | [fact] SI-09, SI-11, SI-12 |
| P4 | A fallback never lowers a bar. Provider down = ask the human or block, never allow. | [fact] for Jev (exit 5 is not an accept, T21); [planned] every new vendor |
| P5 | Every added vendor is a new egress destination and needs owner approval in writing. | [planned] (B4, section 4.1) |
| P6 | `typesafe/jev-router` stays out of every gate decision. It is a chat router on 9router, not a Decisions primitive. | [fact] EV-21, EV-22; [planned] registry test refusing it |
| P7 | Silence, timeout, expiry or a dead channel is never approval. | [fact] section 2.3 |

### 1.1 Four planes and reconciliation (V7-A08, V7-A119; ownership owned by doc 11, V7-A25..A28)

Reconciliation: directive component 8 "Execution & Continuity Orchestrator" (V7-R38) is split per the addendum. JADE owns the decision lifecycle and blocker records; the host (Claude Code, Cursor, other runtime) owns execution, tools, task retries and checkpoints ([03](03-reference-architecture.md) P5, [ADR-v6-01](../../14-v6/04-adrs.md)).

| Plane | In JADE today [fact] | Trust boundary to | Crossing rule |
|---|---|---|---|
| Decision (JADE) | `jade ask`, `route.py`, `decide.py`, ledger | Policy: proposal in, never a grant | A proposal is data to the gate; it raises no permit (T7) |
| Execution (host) | Claude Code tool loop; outside JADE | Policy: every tool call crosses the PreToolUse hook (B2) | No hook bypass or recommendation promoted to grant; bypass/auto modes residual (T18) |
| Policy / authorization (independent) | PreToolUse hook (`jade/hooks.py`), `policy.assess` (`jade/policy.py:1441`), permits, SI/HO | Human approval channel (B5) | Grants from a human act or deterministic rule; exception: a Jev `q.human_proxy.would_allow` ACCEPT can accept an ASK decision (`jade/state_machine.py:128-130`), residual in 2.2 |
| Evaluation / learning | `improve.py`, `evals/`, `labels`, `outcomes` | Policy: proposals only | `propose` refuses protected targets (`jade/improve.py:57-58`, `policies/safety.yaml:65-74`) |
| Host to Decision (return path) | `jade decide outcome` (`jade/cli.py:693`) | Decision | Observed results; agent-typed = claim ([09](09-release-and-learning.md) 8.1) |

## 2. Threat model per trust boundary (V7-R27, V7-R88, V7-R16, V7-A119)

| Boundary | Main threats | Today [fact] | Residual [fact] | v7 [planned] |
|---|---|---|---|---|
| B1 Repo, web, tool output into agent context | Prompt injection, tool-result poisoning, hidden Unicode | T1, T3 (R-18), 242 adversarial cases (EV-55) | Injection steers intent (v6 F9) | Hidden-Unicode scan, provenance per evidence item ([03](03-reference-architecture.md)); hostile-text flag = abstain reason (REPORT rule 18) |
| B2 Host tool call into PreToolUse hook (Execution to Policy) | Escalation, unsafe execution, classification bypass | SI-02..SI-13, R-01..R-32, HO (`jade/humanonly.py`), fail-closed T17 | T18 gaps; bypass/auto modes; v6 F4, F5, F7, F11 | v6 7.1 fixes (F1, F2, F10 first) before any v7 autonomy; HO-agent-launch (F7) |
| B3 Hook and CLI into local state (ledger, config, seal key) | Audit tampering, self-modification of wiring | Append-only (ADR-08), HMAC seal (T20), SI-09..SI-12, R-13, R-16, mode 600 (T15) | Same OS user rewrites `jade.sqlite`, reads seal key (T19) | 6.2 |
| B4 Decision plane to Decisions vendors (OpenRouter, TypeSafe, any lane vendor) | Secret leakage, "a company sees the state", silent vendor switch, model drift, authority spoofing | `redact()`, `residual()` (`jade/redact.py:237,263`), 32 KiB cap, T4, T9, T21 | No ZDR request sent (EV-33); retention [unknown] (U-05); ordinary text leaves | Section 4.1. Model ids only from a human-only accepted-model list outside `registry/` (REPORT risk 6); `~typesafe/jev-latest` only with that list, a band floor and vendor in the calibration key (REPORT section 3) |
| B5 Human approval channel (chat, dialog, tty, signed backend) | Forged, laundered, stale or batch-to-standing approval | T2, T25, T27; signed backend off (W-90) | Same-user pty forge (T2); chat grant unbound (F2); bidi unescaped (F6, EV-49) | Section 3.2 |
| B6 JADE to git remote and production | Unsafe autonomous execution | SI-06, SI-08, R-03, R-21, HO-release; T23 | `gh api` merge, wrappers (F4); guard install [unknown] (F12) | `jade check` row for guard and branch protection |
| B7 Supply chain | Dependency or skill compromise | §9 | §9 | §9 |
| B8 9router chat gateway | Mistaken use as a gate source | [assumption] chat only (EV-23, probe not saved) | U-04 | Registry refuses gateway routes and text-output ids for gate roles (P6) |

### 2.1 Threat class to control map (V7-R27)

| Threat class | docs/05 rows | Rules | v7 delta |
|---|---|---|---|
| Prompt injection | T1, T5, T28 | SI-01 | Errors correlate (EV-32, [hypothesis]): no panel defence |
| Tool-result poisoning | T1, T8, T10 | SI-01, R-31 | Evidence provenance ([03](03-reference-architecture.md)) |
| Privilege escalation | T2, T19, T26 | SI-09..SI-12 | New verbs and registry edits join SI-09, R-13 first |
| Unsafe autonomous execution | T6, T7, T18, T29 | SI-06..SI-08, HO-* | 2.2, 5; shadow cannot execute ([09](09-release-and-learning.md)) |
| Secret leakage | T4, T15, T22 | SI-05, SI-10, R-21 | Section 4 |
| Audit tampering | T11, T19, T20 | SI-10 | Section 6.2 |
| A company sees the state | none (REPORT §7 row 3) | none | [planned] docs/05 row for OpenRouter, TypeSafe, each lane vendor (4.1, SO-07) |
| Multi-vendor egress | T4, T9 | SI-05 | B4; docs/05 row per vendor first |

### 2.2 Action-time re-authorization (V7-A23, V7-A87, V7-A24 cross-ref)

Rechecked at the PreToolUse hook for the exact call, after approval, before the side effect.

| Check at consume | Status |
|---|---|
| ISSUED, unexpired, unused, same `plan_hash`, session, agent | [fact] `jade/hooks.py:564-569`, `jade/permits.py:673-690` |
| Policy version changed since approval: permit revoked | [fact] `jade/permits.py:699-701` |
| Preconditions (git HEAD, content digest) differ: refused, new permit needed | [fact] `jade/permits.py:706-709`; W-39 content binding `jade/hooks.py:543-544` |
| Risk re-classified now (`level` passed at consume) | [fact] `jade/permits.py:675` |
| Earlier run OUTCOME_UNKNOWN or still EXECUTING: blocked, no permit used (SI-07) | [fact] `jade/hooks.py:555-561` |
| Host replan or retry = new tool call, re-assessed, own grant | [fact] hook per call; [planned] doc 11 link (V7-A24) |
| Residual: gap between hook allow and side effect; scripts, computed paths (T18) | [fact] accepted; sandbox (3.3) |
| Residual vs V7-A86: memory covers a class of critical calls (F1); chat grant unbound (F2); Jev proxy ACCEPT acts as a grant | [planned] F1, F2 first; proxy per [10](10-decisions-and-questions.md) H-02 (v6 Q-07/Q-08) |

### 2.3 Timeout and silence are never approval (V7-A52 cross-ref, P7)

| Path | Behaviour on timeout or no answer |
|---|---|
| Dialog ask | [fact] "Deny, or no answer, blocks it" (`jade/askdialog.py:97`) |
| Decisions provider | [fact] exit 5, human asked (T21); 20 s (`jade/adapter.py:29`) |
| Permit | [fact] 30 min TTL; expired = refused (`jade/permits.py:688-689`) |
| Execution without outcome | [fact] OUTCOME_UNKNOWN, never success (`jade/decide.py:127`) |
| Challenger or lane vendor | [planned] down, late, malformed = no accept, never skip (REPORT rules 3, 5) |
| Pending human decision | [planned] blocker with owner and resume condition (doc 11); resume after valid answer plus revalidation |

## 3. Governance (V7-R14, V7-R89, V7-R90, V7-R95, V7-R104)

### 3.1 Actions that need explicit human approval

| Action | Mechanism today | v7 |
|---|---|---|
| Modify JADE code | [fact] R-16, R-24 permit | unchanged |
| Merge, push to default branch | [fact] SI-08 + pre-push guard (T23) | unchanged |
| Production release or deploy | [fact] SI-06, R-03, HO-release: never auto-issued | [planned] no automatic deploy (V7-R95); canary after owner approval ([09](09-release-and-learning.md)) |
| Paid inference above a ceiling | [fact] none: 500 calls/day warns only (`jade/ask.py:20`) | [planned] 5.3 |
| Change credentials, policies, accepted-model list | [fact] R-13 (policy files), SI-12 (config dir), Open Vault rule | [planned] list outside `registry/`, human-only |
| New execution privilege, vendor, model role | [fact] none for vendors | [planned] owner approval as a decision row |
| Set JADE level | [fact] SI-12, human only | unchanged |

### 3.2 Protected approval workflow (V7-R90, V7-R104)

| Control | Status |
|---|---|
| Permit bound to plan, content, cwd, HEAD, session, agent, policy version; single use; 30 min | [fact] T6, T20; 2.2 |
| Approval needs a human tty | [fact] T2 |
| Chat grant bound to `plan_hash` | [planned] v6 F2 (v6 Q-04) |
| Escaped approval text (bidi, format chars) | [planned] v6 F6 |
| Batch approval | [fact] not batched (T14). [planned] list of exact `plan_hash` items, each single-use, shown in full; never a standing grant, memory row or level change; critical and HO-* never |
| Memory covering a class of critical calls | [fact] gap (v6 F1, EV-38); [planned] F1 first |

### 3.3 Least privilege (V7-R89)

| Item | Status |
|---|---|
| Agent cannot approve, change level, gate, config or env | [fact] SI-09, SI-11, SI-12 |
| Decisions calls have no side effects | [fact] T7 |
| Separate OS user or sandbox for the agent | [planned] recommended, not installed (W-34, W-37) |
| A verifier model may only veto, never loosen | [planned] EV-34; REPORT rule 2 |
| Provider key | [fact] Keychain `OPENROUTER_API_KEY`; [planned] optional limited lane key only via `request_secret` into Open Vault (REPORT rule 19) |

### 3.4 Ownership and cadence (V7-R87)

"agent" = a gated Claude Code session that only proposes.

| Asset | Owner | Cadence | Output |
|---|---|---|---|
| Provider registry; accepted-model list | agent proposes, human approves (list: human only) | monthly, on `MODEL_DRIFT` | dated diff |
| Eval corpus | human (holdout), agent (dev) | per release | `dataset_versions` |
| Security rules (`safety.yaml`, `risk.yaml`, HO classes) | human only (R-13) | per incident; quarterly | threat-row evidence (`tests/test_docs_truth.py`) |
| Decision policy | human | quarterly, after a canary trigger | ADR or promotion record |
| Documentation | agent drafts, human approves | per release | docs-truth tests |
| Deferred backlog (`work-items.jsonl`; no Obsidian/Kanban, EV-62) | human prioritises | weekly | open P1 list at SessionStart |

## 4. Secrets, data, traces and retention (V7-R91, V7-R92, V7-R93, V7-A92, V7-A97)

| Control | Status |
|---|---|
| Secrets only via Open Vault or Keychain, never prompt text; `request_secret` dialog | [fact] owner rule; SI-05 |
| Redaction before every provider call and ledger write; residual refuses the call (exit 6) | [fact] `jade/redact.py:237,263`, T4 |
| Unknown-shape secrets pass redaction | [fact] accepted residual (W-42) |
| Decision, outcome and event text scrubbed of credential shapes; refs cut to 512 chars | [fact] `jade/decide.py:14-17,99` |
| Trace minimization: ids, hashes, enums, counts; raw text only redacted and purgeable | [planned] V7-A97 |
| Vendor output stored as checked fields only (p, enum verdict, regex-matched id, latency, cost, error enum); no vendor free text | [planned] REPORT rule 13 |
| Ledger at rest: mode 600, not encrypted | [fact] T15; no encryption code in `jade/` |
| `jade ledger purge --older-than 90d` clears text, keeps metrics | [fact] T15; schedule [unknown] |
| Encryption at rest | [planned] FileVault in `jade check` (Q2) |
| Retention enforcement | [planned] `jade check` warns; human purges |

### 4.1 Vendor egress and "a company sees the state" (V7-R92, P5)

[assumption, REPORT section 7 row 3] Redaction removes secret shapes only; paths, project names, code and owner emails (kept by design, `jade/redact.py:230-233`) still leave, today too (OpenRouter, TypeSafe).

| Control | Status |
|---|---|
| `provider:{"zdr":true,"only":[<slug>]}` on every Decisions and lane call | [planned] REPORT rule 12; Q5 |
| Record answering provider; alert on change (host stays `openrouter.ai`) | [planned] REPORT rule 12 |
| ZDR canary: non-ZDR model with `zdr:true` must 404; 404 = exit 5 | [planned] REPORT risk 11 |
| Lane egress filter: never send human-only, critical, instruction files, blocked calls, redaction-changed text, over 4 KiB | [planned] REPORT rule 11 |
| Second vendor: verify only; `would_allow` only after written owner approval; 5 non-ZDR vendors excluded | [planned] REPORT D6; EV-33 |
| Retention terms per vendor recorded with dates | [unknown] U-05; SO-03 |

## 5. Continuity and recovery (V7-R96, V7-R97, V7-R98, V7-R100, V7-R112)

Decision-plane continuity only (Decisions calls, permits, SI-07 holds, decision records); task retries and checkpoints are host-owned (1.1).

### 5.1 Retry, timeout, circuit breaker

| Control | Today [fact] | v7 [planned] |
|---|---|---|
| Provider deadline | 20 s incl. DNS and connect (`jade/adapter.py:29`) | per role, <= 20 s; lane shorter |
| Retries | 2, only `RATE_LIMITED`, `OVERLOADED` (`jade/adapter.py:27-28`) | jittered backoff; count against spend |
| Hook budget | 6 s alarm, ledger lock 1.5 s, git 2 s (T17) | unchanged (T13) |
| Circuit breaker | none | per lane, as in [06 §7](06-model-routing-strategy.md): primary closes by half-open probe (open = not-yes); shadow/verifier lanes resume only by a human; never a weaker vendor |
| Outage failover | exit 5 = human asked (T21) | a fallback may only ABSTAIN or REJECT (REPORT rule 6); jev-router never (P6) |
| Idempotency | permit single use; replay answered once (`jade/hooks.py:548-551`); SI-07 | unchanged |
| Cancellation | `cancel` human-only under SI-07 (`jade/cli.py:270-278`) | unchanged |

### 5.2 Checkpoint, replay, interrupted sessions (V7-R98)

| Control | Status |
|---|---|
| Durable decision state machine, transitions as `events` rows | [fact] ADR-08; EV-40 |
| No workflow engine (Temporal, DBOS, LangGraph) | [fact] ADR-v6-07 (EV-45) |
| After a crash, only a human lifts an irreversible hold | [planned] v6 F13 |
| Atomic `work-items.jsonl` write | [planned] v6 07 s6.4 |
| Concurrency | [fact] `max_parallel_requests` 4, soft (`policies/routing.yaml:48`) |
| Replay | [fact] `evals/gate_replay.py`; [planned] keyed by resolved model id |

### 5.3 Loops and runaway spend (V7-R100, V7-R112)

| Control | Today [fact] | v7 [planned] |
|---|---|---|
| Question recursion | `revision_count` <= 3; 8 questions per request (T13) | unchanged |
| Repeat ask | deny cap 3/session; pause at 30/h; dedup 1 h (T8) | cross-session count; per-claim-hash cap (REPORT rule 10) |
| Daily budget | 500 calls, warns only (`jade/ask.py:20`) | hard USD ceiling per day and workflow; on hit gated calls go to the human |
| Backpressure | `TOO_MANY_IN_FLIGHT`, exit 5 | in-flight ceiling per vendor |
| Baseline spend | $0.013842 over 14 days, 375 calls (EV-51) | ceiling [hypothesis] $1/day (Q3) |

## 6. SLOs and audit integrity (V7-R97, V7-R99)

### 6.1 SLOs [hypothesis until measured over 14 days]

| SLI | Baseline [fact] | Target | Breach action |
|---|---|---|---|
| Hook latency p95 | [unknown] | <= 300 ms | S3 |
| Decisions latency p95 | 1395 ms, 14 days (EV-51) | <= 2000 ms | human asked |
| Availability per vendor | not split today | >= 99% answered, 7 days | breaker (5.1) |
| Fail-closed | T17 tests in CI | 0 high/critical allowed on hook error | S1 |
| False approvals | 0 labelled rows (EV-41) | set in [05](05-benchmark-design.md) only | halt canary ([09](09-release-and-learning.md)) |
| Human interruption | 4.4 asks per 100 gated (EV-53) | owner sets | review |

### 6.2 Audit integrity

| Control | Status |
|---|---|
| Append-only tables, purge clears text only | [fact] ADR-08 |
| Permit HMAC seal | [fact] T20; same-user readable key (T19) |
| Daily `events` digest by `jade check`, signed by the approver OS user; gap or rewrite = S2 | [planned] ADR revising ADR-08 (Q1) |

## 7. Telemetry and evaluation signals (V7-R41, V7-R92)

Today: `jade ledger stats` (EV-51..EV-53); no export ([fact] ADR-v6-10).

| Signal | Today [fact] | v7 [planned] |
|---|---|---|
| Decision trace | `events` table | `tool_use_id` on decision rows |
| Quality labels | only accepted-and-ran calls (EV-48); 0 rows (EV-41) | label plumbing V6-23 |
| Error taxonomy | `safety_block`, `would_block`, `blind_spot`, `INVALID_QUESTION`, `MODEL_DRIFT` (EV-52) | `vendor_unavailable`, `breaker_open`, `budget_hit`, `provider_mismatch` |
| Cost, tokens, latency | per call; p50/p95/p99 (EV-51) | per vendor, role; hook latency |
| Escalation, false blocks | asks per 100 gated (EV-53); false blocks unmeasurable (EV-48) | block sample reviewed |
| Downstream outcome | `outcomes` table | observed vs claimed split ([09](09-release-and-learning.md) 8.1) |
| Export | none | ids, counts; opt-in `--otlp-json`, never from the hook |

## 8. Incident response (V7-R84)

| Severity | Examples | First action | Who |
|---|---|---|---|
| S1 | High/critical call without valid permit; secret reached a vendor; default-branch push | Stop session; keep enforce (EV-10); rotate key | human |
| S2 | Ledger gap or rewrite; gate-role `MODEL_DRIFT`; provider mismatch; canary trigger | Pin previous id; `jade improve rollback`; RCA | human approves, agent drafts |
| S3 | SLO breach, outage, breaker open, budget hit | Calls already fall to the human; record | agent records |

[planned] Each S1/S2: owned work item, RCA, regression case, docs/05 update if a rule changed.

## 9. Supply chain (V7-R94)

| Control | Status |
|---|---|
| 3 runtime deps, bounded ranges | [fact] `pyproject.toml:11-14` |
| `uv sync --frozen`, uv 0.13.6 | [fact] `.github/workflows/ci.yml:19-21`, `release.yml:22-24` |
| Dependabot weekly (`uv`, `github-actions`) | [fact] `.github/dependabot.yml` |
| Release install test, `SHA256SUMS` | [fact] `release.yml:53-61` |
| `.gitleaks.toml` exists; no gitleaks step in CI | [fact] grep 2026-11-16 |
| Actions pinned by major tag, not SHA | [fact] `ci.yml:14` |
| v7 | [planned] SHA-pin actions; `pip-audit`/OSV and gitleaks in CI (dev-only, Q4); stdlib HTTP for vendors; vendor SDK refused |

## 10. Operations routine (V7-R84)

| Task | Cadence | Mechanism |
|---|---|---|
| Drift detection | per call | [fact] `resolved_model`, `MODEL_DRIFT` (T9); [planned] provider mismatch |
| Provider health | daily | [fact] `jade check` canary (T9); [planned] per vendor, ZDR canary, rotating battery (rule 15) |
| Benchmark refresh | per new model or quarterly | [planned] [05](05-benchmark-design.md); owner approves spend |
| Regression monitoring | per PR | [fact] `evals/run_cases.py`, CI |
| RCA; policy review | weekly while S1/S2 open; quarterly | [planned] 8, 3.4 |

## Open questions for the owner

| # | Question | Recommended default | Why |
|---|---|---|---|
| Q1/[A-05](10-decisions-and-questions.md) | Revise ADR-08 for a signed daily ledger digest? | Yes, signed when the signed backend is installed | Only a separate-user signature stops a same-user agent |
| Q2/[A-05](10-decisions-and-questions.md) | Ledger encryption at rest? | FileVault plus `jade check` row | Keeps 3 dependencies |
| Q3/[H-06](10-decisions-and-questions.md) | Hard daily spend ceiling? | $1/day, fail toward the human | 14-day spend $0.013842 (EV-51) |
| Q4/[A-06](10-decisions-and-questions.md) | `pip-audit`/OSV, gitleaks, SHA-pinned actions? | Yes, dev-only | No runtime change |
| Q5/[H-03](10-decisions-and-questions.md) | `provider:{zdr,only}` on every call plus mismatch alert? | Yes | 4.1; U-05 |
| Q6/[A-09](10-decisions-and-questions.md) | Breaker fallback to a second vendor, or human only? | Human only | Exploratory bench cannot qualify a vendor (EV-29) |
| Q7/[H-08](10-decisions-and-questions.md) | Extend the "Jev only" OpenRouter exception to one vendor? | `baseten/mammoth-1` only, verify only, limited key | REPORT D6, rule 20 |

## Deferred items

Proposed ids, not in `work-items.jsonl`.

| Id | Item | Owner | Priority | Next action |
|---|---|---|---|---|
| SO-01 | Hook latency p95 | agent | P2 | [07](07-implementation-roadmap.md) instrumentation |
| SO-02 | Probe log 9router, jev-router (U-04) | human | P2 | Approved probe |
| SO-03 | Vendor retention terms (U-05) | human asks, agent records | P1 before any second vendor | Ask OpenRouter and TypeSafe (REPORT D4) |
| SO-04 | Pre-push guard, branch protection (F12) | human | P1 | `jade check` |
| SO-05 | Retention purge schedule | human | P3 | After Q2 |
| SO-06 | Breaker N for shadow/verifier lanes (06 §7) | agent | P2 | Shadow error rates |
| SO-07 | docs/05 row "a company sees the redacted state" | agent drafts, human approves | P1 | REPORT step 0b |
