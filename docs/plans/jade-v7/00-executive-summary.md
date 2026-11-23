# 00 JADE v7 plan: executive summary

Status: Planning only. Nothing here is implemented. Date of the facts: 2026-11-16.
Requirements: V7-R01, V7-R116, V7-A03, V7-A113.

This is the short plan. Each line is one idea. Detail: docs 01 to 11 ([README](README.md)).

Tags: [fact] cites an EV id (`evidence/v7-plan/evidence-index.md`), a repo path, or the Jev report (`evidence/jev-models/REPORT.md`, "REP"). [assumption], [hypothesis], [unknown] mean what they say.

## 1. What this plan is (V7-R01, V7-A01, V7-A03, V7-A113, V7-A129)

| Item | Content |
|---|---|
| Input 1 | The owner directive: the JADE v7 master roadmap. |
| Input 2 | The flow-architecture addendum. It extends the directive and fixes conflicts. It does not replace it. |
| Input 3 | The v6 plan ([master](../../14-v6-master-plan.md), [14-v6/](../../14-v6/12-roadmap.md)) and the live repo. |
| Output | One merged plan: docs 00 to 11 in `docs/plans/jade-v7/`. |
| Method | Read the repo. Compare with the old plan. Write down every conflict and how we settle it ([01](01-current-state-audit.md), [02](02-gap-risk-register.md)). |

## 2. Goals (V7-R07)

| # | Goal | Where |
|---|---|---|
| G1 | Good decisions, with evidence you can read | [03](03-reference-architecture.md), [11](11-decision-lifecycle-and-contracts.md) |
| G2 | Fewer needless questions to you | [10](10-decisions-and-questions.md), [11](11-decision-lifecycle-and-contracts.md) |
| G3 | Risk controls that fit the risk | [08](08-security-operations.md) |
| G4 | Low model spend, with hard caps | [06](06-model-routing-strategy.md), [05](05-benchmark-design.md) |
| G5 | No lost work: every blocker has an owner and a resume condition | [02](02-gap-risk-register.md), [11](11-decision-lifecycle-and-contracts.md) |
| G6 | Measure before any switch | [05](05-benchmark-design.md), [09](09-release-and-learning.md) |

## 3. Non-goals (V7-R02, V7-R116)

- No rewrite. v6 parts that work stay ([02 §5](02-gap-risk-register.md)).
- No workflow engine, scheduler or resident agent inside JADE ([11 §7](11-decision-lifecycle-and-contracts.md)).
- No OPA or Cedar policy engine (DM-06 in [10](10-decisions-and-questions.md)).
- No judge switch on today's data (section 7).
- No `typesafe/jev-router` anywhere in JADE. It is a chat router for 9router only.
- Not present, so not in scope: Contextum, Obsidian/Kanban, a Laravel/Vue task corpus [fact, EV-61..EV-63]. OmniRoute is removed; the gateway is 9router [fact, EV-64].

## 4. Hard boundaries (V7-R04, V7-R05, V7-R17, V7-R142, V7-A05, V7-A128)

These do not change in any milestone.

| # | Rule |
|---|---|
| B1 | A probability is never permission. Model agreement is not proof. One exception, set by you (ADR-13): at level low or medium Jev may allow a call inside its band, never for human-only, safety-blocked, R-13, R-18 or R-22 calls. v7 adds none (H-02). |
| B2 | Deterministic gates stay independent. They run first and read no model output [fact, `jade/hooks.py:395`]. |
| B3 | Only you set the JADE level. |
| B4 | A fallback never lowers a bar. |
| B5 | A timeout or silence is never approval. |
| B6 | No code change, merge, deploy, paid run, config change or new permission without your explicit yes. |
| B7 | Secrets go through Open Vault or Keychain only. Never in chat, logs or docs. |

## 5. The four planes (V7-A01, V7-A129)

The directive puts an "Execution & Continuity Orchestrator" (component 8) inside JADE. The addendum gives execution to the host. **We follow the addendum split.** Detail: [03 §0](03-reference-architecture.md), [11 §0](11-decision-lifecycle-and-contracts.md), DM-01 in [10](10-decisions-and-questions.md).

| Plane | Owns | In JADE today |
|---|---|---|
| Decision (JADE) | Framing, evidence needs, risk, route, proposal, decision record, blocker records, outcome check | `ask`, `route`, `decide`, `state_machine`, `thresholds`, `adapter` [fact, [11 §1](11-decision-lifecycle-and-contracts.md)] |
| Execution (host) | Task flow, tool calls, retries, checkpoints, resume | Claude Code. JADE may *ask* for a task retry and counts it. It never runs one; it retries only its own model call. |
| Policy / authorization | Permits, approval limits, safety rules, the check at action time | PreToolUse hook + `policy.assess` + permits [fact, `jade/hooks.py:375-421`, `jade/permits.py:672`] |
| Evaluation / learning | Benchmarks, labels, comparisons, proposals | `evals`, `improve`. A human promotes every change. |

Live today (level `off`): only SI-09..SI-12 block at action time; other SI and permit checks are advisory [fact, `jade/hooks.py:418`, `:427-434`; EV-10, EV-11]. Full enforcement needs your level (V6-16, V6-19).

One honest limit: the policy plane is independent in logic, not in process. It runs in the same package and hook process. It reads no model output before its verdict ([03 §1.1](03-reference-architecture.md)).

## 6. Key trade-offs

| Choice | We pick | We give up | Why |
|---|---|---|---|
| Execution owner | Host | One "do everything" engine | No duplicate runtime; v6 ADR-v6-01 ([ADRs](../../14-v6/04-adrs.md)) |
| Second model | Veto only, shadow first | Fewer abstains now | A second "yes" could open a gate (DM-04) |
| Judge id | Stay pinned on `typesafe/jev-1.14` | Auto upgrades | A moving alias breaks calibration (H-05) |
| Fallback model | None now | Answers during an outage | A weaker model must never accept (DM-13) |
| Policy engine | Keep the custom code | A standard engine | It works and is tested (DM-06) |
| New behaviour | Behind a flag, default off | Speed | Off equals v6 (DM-09) |

ADR candidates with alternatives: [03 §9](03-reference-architecture.md).

## 7. Model conclusion (from REP)

| Point | Detail |
|---|---|
| Keep the judge | `typesafe/jev-1.14` stays primary. Do **not** switch [fact, REP §0]. |
| Bench status | The 41-model bench (3620 rows, about 0.14 USD, 54 labelled cases) is **exploratory**. It cannot rank models [fact, REP §1, EV-29, EV-30]. |
| No model shown safer | Jev made no wrong yes on 28 false claims. With 28 claims the true rate could still be up to about 10 to 12% [fact, REP §0]. Never read this as "safe". |
| Many models fail | 17 of 36 working models fail a hard safety check [fact, REP §2]. |
| `~typesafe/jev-latest` | Real. Today it resolves to `typesafe/jev-1.14-20261014`, the same model [fact, EV-18]. Use it only with three guards, in one release: a human-only accepted-model list outside `registry/`, a band floor for new snapshots (a new model starts strict), and the vendor in the calibration-group key ([04 §8](04-model-provider-research.md)). Default today: stay pinned (H-05). |
| Optional shadow lane | `baseten/mammoth-1`, verify questions only; decides nothing (H-08). |
| Human decisions | REP D1 to D7 are mapped to H-03 to H-08 and L-01 in [10](10-decisions-and-questions.md). |

## 8. Versioning (V7-R116)

- "v7" is the name of this plan. Releases keep semver.
- Tag 0.7.0 holds the v6 fixes only, not v7 (DM-07).
- v7 work ships flag-off in a later minor after 0.7.x.
- This is the default of H-09. It waits for your answer.

## 9. What we reuse from v6 (V7-R02, V7-A03)

| Reused | Link |
|---|---|
| The 34 v6 tasks, each placed once in B or M1 to M10 | [07 §2](07-implementation-roadmap.md) |
| v6 ADRs (decision plane, human level, human promotion) | [v6 ADRs](../../14-v6/04-adrs.md) |
| Permits, state machine, `hooks._gate` order, `improve.promote` human gate | [11 §7](11-decision-lifecycle-and-contracts.md) |
| v6 questions Q-01 to Q-12 | [v6 doc 13](../../14-v6/13-deferred-and-open-questions.md), H-02 |
| v6 security model and threat model | [v6 doc 05](../../14-v6/05-security-and-autonomy-model.md), [08](08-security-operations.md) |

## 10. PR 47 status

| Item | Status |
|---|---|
| PR 47 | Not mergeable. It still has a **critical** SI-08 regression (R5-L4-1) and an **important** one (CODE5-1) [fact, `evidence/round5-findings-c676801.json`]. |
| W-115 | Stays open. |
| Effect on the plan | B starts with PR 47, so it waits for both fixes and one clean review round ([07 §3](07-implementation-roadmap.md)). |
| Other PRs | 42 to 46 are open. PR 48 is merged. `origin/main` = `e9412f3` [fact, EV-01, EV-06]. |

## 11. No code changed, nothing pushed (V7-R17, V7-R137, V7-A128)

- **No code, config or policy file was changed.** [fact] `git status --porcelain` shows only `?? docs/plans/` on 2026-11-16.
- **One local commit, nothing pushed.** [fact] Branch `plan-jade-v7` has no upstream.
- No paid or provider call was made for this summary [fact]; for the other docs [assumption] (evidence-index header). The REP bench ran earlier, in its own study.
- The live config stays `gate_mode = enforce`, `level = off` [fact, EV-10].
- These docs are local and untracked. Pushing is your choice (H-10).

## 12. Go / no-go request (V7-R137, V7-R143)

**Please answer [doc 10 §4](10-decisions-and-questions.md) in one batch.** The main question is **H-01**.

| H-01 | Content |
|---|---|
| Question | May work start on baseline B and M1? |
| A yes approves | (a) B pull requests ([07 §3](07-implementation-roadmap.md) order). (b) PR 47 fixes, then a clean review round. (c) M1 at $0 provider spend. (d) Your host probe (V6-15). |
| A yes does not approve | Any merge, tag, paid call, push, flag change, level change, or H-03 to H-08. |
| Default | Go. M1 has no paid inference. Each step can be undone. Every merge stays yours. |
| If no answer | No-go. Silence is never a yes. The plan stays as documents. |

After a yes: H-02, then baseline B, then M1.
