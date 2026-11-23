# 06 Model routing strategy

Status: Planning only. Nothing in this document is implemented.

Scope: how JADE v7 picks a logical strategy (FAST, JEV, DEEP) and a model lane for each decision, when it escalates, and how every failure ends. Aligned with the FINAL Jev study (REP). Delta on v6: Jev's authority ([ADR-v6-13](../../14-v6/04-adrs.md)), confidence ([v6 06 §8](../../14-v6/06-decision-intelligence.md)) and benchmark ([v6 10](../../14-v6/10-verification-and-benchmark.md)) stay in force. Candidates: [04](04-model-provider-research.md); measurement: [05](05-benchmark-design.md).

Requirements met: V7-R33, V7-R34, V7-R36, V7-R49, V7-R50, V7-R51, V7-R62, V7-R63, V7-R64, V7-R97, V7-R110, V7-R111, V7-R113, V7-R114, V7-R122, V7-A11..A14, V7-A72..A84.

Citation aliases: `WT` = worktree `plan-v7` at 8dfa86d; `EVD` = `/Users/example/.claude/projects/-Users-example-Documents-projects-jade--claude-worktrees-plan-v7/b70d8811-d956-4521-97e3-229a4c11d2f4/evidence`; `REP` = `EVD/jev-models/REPORT.md`; `EV-nn`, `U-nn` = `EVD/v7-plan/evidence-index.md`; `INT`, `PRIV` = `EVD/jev-models/analysis/integration.md`, `cost_privacy.md` (all 2026-11-16).

## 0. Invariants and plane split

| # | Rule | Tag |
|---|---|---|
| I1 | A probability is never permission. A model answer is advice; authorization is deterministic and separate | [fact] ADR-v6-13; REP §5.2 rule 1 |
| I2 | A second model, fallback, cache hit, timeout or budget limit can only make a result stricter. Every failure ends in abstain, escalate or block, never allow | [fact] REP §5.2 rules 2, 3, 6 |
| I3 | Only the human sets the JADE level, bands, lanes and the accepted-model list | [fact] REP §3, §5.2 rule 8 |
| I4 | Every added vendor is a new egress destination; owner approval first | [fact] REP §8 D6 |
| I5 | The jev-models bench (54 cases, 3620 rows, 0.14219 USD) is EXPLORATORY: it may exclude a model, never select one | [fact] REP §1; [04 §0](04-model-provider-research.md) |
| I6 | Silence or timeout is never approval | [fact] directive |

Plane split (V7-A25, V7-A27; reconciles directive component 8 with the addendum): JADE's decision plane owns strategy choice, verification triggers, the decision record and blocker records. The host orchestrator (Claude Code etc.) owns execution, tool calls, task retries and checkpoints. The independent policy plane (PreToolUse hook, `policy.assess`, permits) enforces at action time. Routing never executes or authorizes anything.

## 1. Logical strategies (V7-R33, V7-A12, V7-A72)

FAST, JEV and DEEP are strategies, not provider names. A strategy binds to a lane in `policies/models.yaml` (section 4). Order: risk and evidence assessment (V7-A11) first, then strategy, then the policy gate (V7-A14).

| Strategy | What runs | Used when | Can produce accept? | Today |
|---|---|---|---|---|
| (gate) | Deterministic rules, permits, SI holds, memory | Always, before any model | Per rule and permit only | [fact] `WT:policies/risk.yaml`, `WT:jade/memory.py` |
| FAST | Deterministic validators: schema, evidence present, exit status, hashes, date arithmetic, instruction-text flag | Claim is code-checkable | Code-checkable claims only | [fact] `WT:policies/routing.yaml:41`; date check [unknown] U-07 |
| JEV | One batched Decisions call to the primary lane | Claim needs judgement and FAST cannot settle it | Yes, within bands, advisory | [fact] `routing.yaml:44`, `default_route: JEV` line 39 |
| DEEP | Chat reasoning model via 9router, outside the gate; emits atomic JEV questions only | Multi-step framing a single claim cannot carry | No. Its verdict needs JEV or FAST on evidence refs | [fact] `routing.yaml:50`; quality [unknown] |
| ESCALATE | Human, batched with a recommended default | Section 2 triggers | Human answer only | [fact] `routing.yaml:52` |

Never sent to any model: R-01, R-03, R-04, human-only classes, critical calls, fetch-and-exec, instruction files, blocked calls, text changed by redaction, over 4 KiB on the hook path [fact] PRIV §1; REP §5.2 rule 11.

## 2. Escalation triggers (V7-R63, V7-A73, V7-A76)

Capability rises only when complexity, uncertainty or harm justifies it.

| From | To | Trigger | Tag |
|---|---|---|---|
| FAST | JEV | Validator cannot decide; claim needs judgement | [fact] PF rules `routing.yaml` |
| FAST | abstain | Instruction-like text in evidence (`SYSTEM`, `IGNORE ALL PREVIOUS`, `Note to the judge`, `owner already`) | [fact] REP rule 18 |
| JEV | verifier (later, veto only) | Risk medium+ and Jev accept; p within 0.15 of a band edge; agent-written evidence | [fact] noise up to 0.15 REP §3; trigger set [assumption] |
| JEV | DEEP | Question cannot be made atomic; decomposition needed | [hypothesis] |
| JEV | abstain | p within 0.15 of a band edge (middle zone) when no verifier is enabled | [hypothesis] not adopted: REP rule 10 sets it for the quorum path only. Use only after a pre-registered arm (A1 plus middle zone) is measured against A1 in [05](05-benchmark-design.md) (arm not yet defined there) |
| any | ESCALATE | Critical rule; abstain at high risk; budget versus safety conflict; drift on a gate lane; open lane breaker on the proxy path | [fact] REP rules 5, 7 |

## 3. Role assignment (V7-R49, V7-R122, V7-A75)

No global winner [fact, REP §0]. Each row is a [hypothesis] for the pre-registered benchmark in [05](05-benchmark-design.md).

| Role | Assignment | Basis |
|---|---|---|
| Primary (JEV lane) | `typesafe/jev-1.14`. **No judge switch.** `~typesafe/jev-latest` only with guards G1-G3 ([04 §8](04-model-provider-research.md)) | No model shown safer; 0 of 28 FA, 18 of 26 TA, 9 of 54 abstains (8 on true claims) [fact, REP §0, §10] |
| `human_proxy.would_allow` | Jev only now; no shadow lane sees it (D6). A later veto needs L-01 plus a written owner yes ([09 §4](09-release-and-learning.md), [02 §9](02-gap-risk-register.md)) | [fact] REP D6 "no to `would_allow`" |
| Shadow lane | `baseten/mammoth-1`, verify questions only | p95 711 ms, 8/8 repeats identical, injection shift +0.02 [fact, REP §2] |
| Verifier (veto) | mammoth-1, later, after shadow data and a fresh set (D7) | [fact] REP §5.1 |
| Triage, `q.risk.*` | Deterministic; no model | [fact] REP §6 |
| DEEP | Chat model via 9router | [unknown] not researched |
| Agent subtask routing | 9router `auto`; `openrouter/typesafe/jev-router` optional, outside JADE | Section 11 |

Independence: same-vendor pairs never count. Errors correlate across vendors (35 of 50 wrong accepts were the JG10/JG23 date pair) [fact, REP §1], so date checks go to FAST, not to a second model.

## 4. Model registry (V7-R34, V7-R109)

New `policies/models.yaml` [assumption: name], protected under R-13 and SI-12 [fact, REP §3 step 1].

| Field | Rule |
|---|---|
| `request_id` | Dated or pinned id; `~` aliases, `:free` and undated ids refused in gate lanes [fact, REP §6] |
| `resolved_snapshot` | Expected dated id (e.g. `typesafe/jev-1.14-20261014` [fact] EV-17); mismatch = drift |
| `vendor`, `base_family`, `provider_only` | Sent as `provider:{"zdr":true,"only":[...]}`; answering provider stored and alerted on change [fact, REP rule 12] |
| `output_modality` | Must be `decisions`; `text` refused at load (blocks jev-router) [fact, REP §4] |
| `lanes` | `primary`, `shadow`, `verifier`; owner-approved per lane. No `fallback` lane (section 6) |
| `latency_p95`, `price`, `context_limit` | With capture date [fact] REP §2 |
| `polarity` per question | permissive or cautious; set by hand, protected [fact, REP rule 16] |

Lane rows go to their own tables, never `jev_calls`: dedup, in-flight and authority checks read it without a model filter [fact, REP §6]. Store only checked fields: numeric p, enum verdict, regex-matched resolved id, latency, cost, error-kind enum; no vendor free text [fact, REP rule 13].

## 5. Shadow lane and verifier (V7-R36, V7-A14, V7-A76, V7-A77)

Shadow lane (REP step 5, owner D6):

| Rule | Detail |
|---|---|
| Model | `baseten/mammoth-1`, verify questions only. Not `would_allow` |
| Egress | `provider:{"zdr":true,"only":[<Baseten slug>]}` on every call [assumption: slug not yet probed with `only`]; same never-sent filter and 4 KiB cap as Jev |
| Effect | None. Never reaches exit code, verdict, permit, cache or Jev's calibration |
| Default | Off. Separate OpenRouter key with its own credit limit, created via `request_secret` into Open Vault; hard daily call and cost caps in code |
| Errors | Ignored (shadow module only) |
| Output | Weekly report: veto share per rule and per distinct call; only owner `right`/`wrong` marks count as labels |

Verifier, later (REP step 8, after D7):

| Primary | Verifier | Result |
|---|---|---|
| accept | accept | accept (advisory; policy plane still decides) |
| accept | abstain, reject | abstain (exit 3); proxy asks the owner |
| accept | error, timeout, malformed, unknown id | no accept (exit 3); never "skip the verifier" |
| abstain, reject | anything | unchanged; a verifier never upgrades |

Three-state result `ACCEPT`, `NOT_ACCEPT`, `NO_ANSWER`; only `ACCEPT` is yes; quorum and shadow code in separate modules [fact, REP rule 4]. Two models only: no majority (2.7 to 3.1% false units through), no averaging, no OR-accept, no retry-until-accept [fact, REP §6]. In the hook only a lane with p95 well under 1 s, with a hard timeout shorter than the primary's: mammoth-1 only [fact, REP rule 14].

## 6. Fallbacks (V7-R51, V7-R97, V7-A79..A84)

No fallback model: skipped [fact, REP rule 6]. If ever built, it may return ABSTAIN or REJECT only, never ACCEPT, at any risk; reopen only if provider exit 5 exceeds 2% over 14 days. OpenRouter down means every Decisions model is down [fact, PRIV §4]. Task retries belong to the host; JADE retries only 429/529 inside the 20 s deadline (`MAX_RETRIES = 2`, `WT:jade/adapter.py:28-29`), and the hook makes one try (`proxy.py:49`).

| Condition | JADE result | Ends in | Never |
|---|---|---|---|
| Provider outage, timeout | exit 5 `UNAVAILABLE`; lane breaker counts it | abstain; proxy path escalates to owner; gate keeps its deterministic result | allow; reuse an older accept |
| Rate limit after retries | exit 5 `RATE_LIMITED` | abstain; escalate if work is blocked | retry past 20 s; switch model |
| Malformed output (no usable answer, wrong primitive, p outside [0,1]) | exit 5 `MALFORMED` [fact] `WT:jade/ask.py:219`, `adapter.py:83` | abstain; work item if repeated | parse free text; re-ask until accept |
| Retired id or ZDR 404 | today exit 6 [fact, REP §3, A]; v7 exit 5 with clear reason and P1 work item | abstain; escalate | silent model switch |
| Budget exhaustion | exit 5 `BUDGET` | drop shadow first; then abstain; escalate at risk medium+ or when work is blocked | accept; skip a gate or a triggered verifier |
| Insufficient evidence | FAST finds required evidence missing | abstain with "evidence missing"; host gathers more (V7-A71); escalate if it cannot | ask a model to fill the gap |
| Verifier disagreement or verifier failure | section 5 table | abstain (exit 3); escalate at high risk | majority vote; primary-only fallback |
| Drift (new resolved id on a gate lane) | `MODEL_DRIFT` [fact] `WT:jade/ops.py:156` | proxy accepts become owner asks until a human accepts the snapshot; band floor applies | inherit tuned bands |

## 7. Circuit breakers (V7-R34, V7-R97)

| Lane | Open after | Effect | Close |
|---|---|---|---|
| Primary | 5 consecutive UNAVAILABLE/RATE_LIMITED, or > 20% of last 20 [assumption] | exit 5 at once; visible notice | half-open probe after 60 s, doubling to 15 min [assumption]; an open breaker only returns "not yes" |
| Shadow, verifier | N failures or slow answers [assumption: N from data] | lane suspended with notice; proxy decides nothing on that path | only a human runs `jade models resume` (proposed) [fact, REP rule 5] |

No automatic fall back to the primary alone; never a weaker model; never a skipped deterministic gate.

## 8. Budgets (V7-R110, V7-R113, V7-R114)

| Budget | Today | v7 plan |
|---|---|---|
| State per request | 32 KiB [fact] `WT:jade/ask.py:19` | Keep |
| Questions per request | 8 [fact] `routing.yaml:47` | Keep |
| Time | `jade ask` 20 s; hook proxy 3 s in a 6 s hook [fact] `adapter.py:29`, `proxy.py:35` | Verifier inside the same deadline |
| Parallel | 4 [fact] `routing.yaml:48` | Lanes get separate slots |
| Per day | `DAILY_BUDGET = 500` warns only [fact] `ask.py:20` | Hard caps on calls, tokens, USD per lane [assumption: 500 calls, $1/day (H-06, 08 Q3), 100/session]; warn at 80% |
| Cost | Jev 0.0789 USD per 1000 verify decisions [fact, REP §2] | Report cost per verified successful outcome ([05](05-benchmark-design.md)) |

Budget never buys a lower bar. Only the human raises a budget.

## 9. Caching (V7-R111)

| Rule | Detail |
|---|---|
| Key | question id + version + state hash + claim hash + resolved model + policy version [fact: today's key lacks model and policy, `routing.yaml:62`] |
| Scope | session, max 3600 s, until the evidence hash changes [fact] `routing.yaml:64` |
| Quorum cache | Includes non-accepts; cap asks per claim hash per day [fact, REP rule 10] |
| Never | a cached result replaces a fresher stricter one; lane answers serve as primary; semantic cache |

## 10. Confidence, thresholds, drift (V7-R64)

- Raw p is a ranking score, never calibrated [fact, REP rule 7]. Bands 0.70/0.30 are defaults.
- Calibration group = task family + question version + full resolved id including vendor (G3) [fact, `WT:jade/thresholds.py:37` drops the vendor today].
- New resolved model: default bands, never below the strictest band its family holds (G2) [fact, REP rule 7].
- Threshold moves need 200+ labelled rows per group and a human promotion; a 1% FA bound needs about 300 independent false claims [fact, REP §5.2, §10]. No auto-tuning; no loosening from model agreement (REP rule 8).
- Identity: the response `model` field, regex-matched; alias metadata is not trusted [fact, REP rule 15]. Rotating canary battery per lane; failure suspends the lane.

Decision trace fields (V7-A32, V7-A33): `decision_id`, `strategy`, `lane`, `request_id`, `resolved_model`, `provider`, `calibration_group`, `question_id@version`, `state_hash`, `p`, `band`, `verdict`, `exit_code`, `trigger`, `cache_hit`, `breaker_state`, `budget_remaining`, `latency_ms`, `cost`, `zdr`. The full set is `DecisionTrace` in [11 §4](11-decision-lifecycle-and-contracts.md), a superset of this list.

Prompt portability (V7-R51): questions are typed `q.*` templates with `question_id@version` (EV-59), not free prompts. Any new model or lane is re-scored on the [05](05-benchmark-design.md) holdout before use; templates are never tuned per vendor.

## 11. Selection and Jev Router (V7-R50, V7-R62, V7-A74)

Pareto selection runs on [05](05-benchmark-design.md) results only (I5): hard gates (SAFE §7 list, no ZDR, undated id, p95 over lane deadline) remove a candidate; compare severity-weighted error, p95, cost per verified outcome; the incumbent stays unless beaten beyond measured noise on safety. Each change is an ADR plus owner approval; rollback reverts the lane.

`typesafe/jev-router` is a chat router (text output, picks a model per call) [fact, REP §4]. **Never in JADE**: no gate, verify, proxy, verifier, triage or calibration. Outside JADE it may route low-stakes agent text via 9router only, enforced by wrapper or allow-list, else skip. The router-versus-direct comparison (30-50 low-stakes tasks, equal budget) is specified in [05](05-benchmark-design.md); JADE side is one load-time test that `models.yaml` refuses `output_modality: text`.

Adaptive routing ([05](05-benchmark-design.md) A5; V7-R50, V7-R111): not in v7. It would learn from unlabelled data (EV-41: 0 labels). Reopen when every routed group has 200+ labelled rows (section 10); the human promotes.

## Owner decisions (REP §8)

| # | Question | Recommended default |
|---|---|---|
| D1/[H-03](10-decisions-and-questions.md) | `zdr` + `only:["typesafe"]` on every Jev call? | Yes |
| D3/[H-05](10-decisions-and-questions.md) | Alias `~typesafe/jev-latest`? | Only with G1-G3; else stay pinned |
| D6/[H-08](10-decisions-and-questions.md) | Shadow lane mammoth-1, verify only, separate limited key? | Yes; no to `would_allow` and other vendors |
| D7/[L-01](10-decisions-and-questions.md) | Veto budget | Decide after 3-4 weeks of shadow data |
| Q-B/[H-06](10-decisions-and-questions.md) | Hard daily caps instead of warn-only? | Yes, escalate never allow |

## Backlog

| Item | Owner | Priority | Next action |
|---|---|---|---|
| Exit-5 counter by error kind | JADE maintainer | P2 | Ledger field; feeds breaker numbers |
| Date validator and instruction-text flag in FAST (U-07) | JADE maintainer | P1 | Read `jade/` evidence prep |
| Saved jev-router probe (U-04) | Owner | P2 | One approved probe, saved under EVD |
| Vendor retention terms (U-05) | Owner | P2 | D4 questions to OpenRouter and TypeSafe |
| Baseten `only` slug probe | Agent | P2 | With D6 |
