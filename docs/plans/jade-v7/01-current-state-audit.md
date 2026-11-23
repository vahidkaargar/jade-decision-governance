# JADE v7: current-state audit (Phase 0 and Phase 1)

Planning only. Nothing in this document is implemented.

**Scope.** Phase 0 and Phase 1 of the owner directive (`EVD/v7-plan/directive.md`, 2026-11-16), plus the current-vs-target matrix the flow-architecture addendum asks for (`EVD/v7-plan/addendum-flow-architecture.md`; section 9). It gives a verified inventory, integrations, current decision flows, baselines and a Keep/Fix/Extend/Replace/Remove/Unknown matrix. v7 is a delta on v6: full inventory in [v6 section 01](../../14-v6/01-inventory-and-capability-matrix.md), full gap analysis in [v6 section 03](../../14-v6/03-gaps-and-blind-spots.md). No code, config, ledger, branch or provider touched.

**Reconciliation (V7-A07, V7-A27).** Where directive component 8 "Execution & Continuity Orchestrator" conflicts with the addendum, the addendum split holds: JADE owns the decision lifecycle and blocker records; the host (Claude Code) owns execution, tools, retries and checkpoints; the policy plane (today: PreToolUse hook, `policy.assess`, permits) enforces at action time.

**Tags.** [fact] = checked by a command in this session or in the evidence index, source cited. [assumption] = stated in a source, not re-checked here. [hypothesis] = design or EXPLORATORY bench reading. [unknown] = not settled. `WT` = this worktree (`plan-jade-v7`, HEAD `8dfa86d`; code identical to `e9412f3`). `EVD` = `/Users/example/.claude/projects/-Users-example-Documents-projects-jade--claude-worktrees-plan-v7/b70d8811-d956-4521-97e3-229a4c11d2f4/evidence`. `EV-nn`, `U-nn` = evidence-index rows (section 8). Probes: 2026-11-16.

## 1. What changed since v6 (session probes, 2026-11-16)

Requirements: V7-R08, V7-R18, V7-R24, V7-R131.

| # | Change or correction | Tag and source |
|---|---|---|
| D1 | Tag `v0.6.1` = `8100ea1`; settles U-03. | [fact] `git rev-list -n1 v0.6.1` |
| D2 | `origin/main` = `e9412f3`, 3 commits ahead of v0.6.1 (PR 48, SI-13). `pyproject.toml` still `0.6.1`; installed `jade 0.6.1` lacks SI-13. | [fact] EV-01, EV-03, EV-04; `WT:pyproject.toml:3` |
| D3 | PR ahead/behind `origin/main`: 42 +18/-0; 43 +26/-3; 44 +25/-0; 45 +35/-3; 46 +23/-0; 47 +8/-3. Settles EV-15. | [fact] `git rev-list --count` |
| D4 | PR 45 does NOT contain PR 44 tip `ed0d26d` (sits on the old tip). EV-07 is stale. | [fact] `git merge-base --is-ancestor` (false) |
| D5 | PR 47 head and worktree both `c676801`. | [fact] `gh pr list`, `git worktree list` |
| D6 | PR 46 title says "T31"; content is T32 (v6 B7). | [fact] title; [assumption] v6 B7 |
| D7 | Host Claude Code still `2.1.314`. U-09 half settled (probe cells 1, 2, 11 not re-run). | [fact] `claude --version` |
| D8 | Five JADE hooks wired live (pretool, posttool, posttool-failure, stop, session-start), matching `HOOKS` in `jade/ops.py:38-39`. FLOW stop hook is separate, outside the repo. | [fact] settings grep; `WT:jade/ops.py:38` |
| D9 | `launchctl list` shows no `com.jade.maintain`; `maintain.log` modified 2026-11-15 09:15. Job state [unknown]. | [fact] both observations |
| D10 | `docs/00` S3, `docs/01` ADR-02 (lines 17, 21; REPORT §9 row 2), `README.md:219`, `adapters/jev/probe_openrouter.py:60` wrongly say jev-latest does not exist; `~typesafe/jev-latest` resolves to `typesafe/jev-1.14-20261014`. | [fact] EV-18, EV-19 |
| D11 | `typesafe/jev-router` is a chat router (output `text`), not a Decisions primitive; stays out of every gate decision. Reachable only through 9router. | [fact] EV-21; [assumption] EV-22, EV-23 (U-04) |
| D12 | jev-models study (41 models, 17 vendors, 3620 rows) is EXPLORATORY: it can rule a model out, not pick a winner. | [fact] EV-28, EV-29; [hypothesis] every result |
| D13 | PR 47 round 5 (4 reviewers, 5 findings: 4 confirmed, 1 disputed): critical regression R5-L4-1 (SI-08 lost on a here-string-into-shell shape that BASE `8100ea1` blocks) and important CODE5-1 (cwd tracker runs `cd` twice, a later bare push loses SI-08) are open; R5-L4-2, R5-L4-3 minor. W-115 stays open. | [fact] `EVD/round5-findings-c676801.json` |

## 2. Verified inventory

Requirements: V7-R18, V7-R23, V7-R24, V7-R117, V7-A104.

### 2.1 Codebases and ownership

| Item | Value | Tag |
|---|---|---|
| Repo | `github.com/vahidkaargar/jade-decision-governance`, one Python package, MIT | [fact] EV-05; `WT:pyproject.toml:1-7` |
| Other JADE or Jev codebases | None found in repo or worktrees. Outside the repo: FLOW stop hook, `jade` skill. Other machines [unknown] (U-11) | [fact] repo; [assumption] FLOW path v6 C5 |
| Runtime deps | `jsonschema`, `pyyaml`, `referencing`; Python >= 3.13 | [fact] `WT:pyproject.toml:10-27` |
| Entry points | `jade = jade.cli:main`, `jev = jade.compat:main` | [fact] `WT:pyproject.toml:17-19` |
| Hooks | 5 events (D8). PreToolUse host timeout 130 s (dialog wait); handler budget 6 s | [fact] `WT:jade/ops.py:36-39`, `jade/hooks.py:26` |
| Agent text | `JEV_HEADING` block written into CLAUDE.md; source `docs/claude-md-jev-section.md` | [fact] `WT:jade/ops.py:40` |
| Provider adapter | `jade/adapter.py` calls `POST https://openrouter.ai/api/alpha/decisions`; model `typesafe/jev-1.14` constant | [fact] EV-16 |
| Policy and registry | `policies/{safety,risk,routing,escalation,promotion}.yaml`, `policies/local/`; 11 `registry/q.*.json` | [fact] `ls`; EV-59 |
| Schemas | `schemas/{decision,outcome,permit,work-item,improvement,question}.schema.json`, all `schema_version` | [fact] `ls schemas` |
| Live config | `gate_mode = enforce`, `level = off`; local `risk.local.yaml`, `safety.local.yaml` | [fact] EV-10 |
| Persistence | `~/.local/share/jade/jade.sqlite` (WAL), `permit-seal.key` (not read), `maintain.log` | [fact] EV-12 |
| Deployment | `uv tool install` from tag; `ci.yml` (Linux only), `release.yml`; tags unsigned | [fact] `ls .github/workflows`; [assumption] v6 C8 |

### 2.2 Open PRs (CI: 47 at `c676801` passes `test (3.13)`, `test (3.14)` [fact, `gh pr checks 47`]; 42-46 per v6 B1 [assumption])

| PR | Head to base | Head | Size | What it changes |
|---|---|---|---|---|
| 42 | `design-clear-questions` to main | `8866872` | +2638 -45, 22 files | 0.7.0 base: plain question lines, memory scopes, `jade knows edit`, T31 |
| 43 | `open-items-plain-text` to 42 | `3b5a456` | +577 -18, 4 files | Push/merge question lines never understate the call. 3 behind main |
| 44 | `open-items-scopes` to 42 | `ed0d26d` | +713 -182, 13 files | Merge scope needs a matching `--repo`; newer deny beats older allow |
| 45 | `open-items-review-output` to 44 | `8c8163e` | +1271 -86, 18 files | One-line review output; Jev sees denies; proxy question v2 (uncalibrated). On old 44 tip (D4) |
| 46 | `open-items-shell-startup` to 42 | `a18bf71` | +848 -29, 10 files | Human-only class `HO-shell-startup` |
| 47 | `claude/heuristic-hamilton-0eae1d` to main | `c676801` | +1831 -21, 9 files | SI-08 fix (W-115). Green CI, but round 5 leaves critical R5-L4-1 and important CODE5-1 open (D13). Do not merge |

[fact] `gh pr list --state open`, 2026-11-16. Stack shape, conflicts, 0.7.0 merge order and PR-less branches: [v6 B4-B5](../../14-v6/01-inventory-and-capability-matrix.md#b4-unmerged-work).

### 2.3 Worktrees and loss risk

14 worktrees [fact] (`git worktree list`); main checkout `harness-tools-w97` is dirty. Disk-only work, id collisions, deletion hazards: [v6 B6-B8](../../14-v6/01-inventory-and-capability-matrix.md#b6-work-that-exists-only-outside-git-history-loss-risk) [assumption].

## 3. Integrations: present vs not present

Requirements: V7-R19, V7-A130. Grep scope: `git grep` on `jade/`, `policies/`, `adapters/` at `8dfa86d`, plus EV-61.

| Integration | State | Tag and source |
|---|---|---|
| Claude Code | Present: 5 hooks, chat `AskUserQuestion` rewrite, macOS dialog, CLAUDE.md block | [fact] D8; [assumption] v6 C2, C7 |
| Other harnesses (Codex, Cursor, Gemini, Aider) | Not present: no adapter on any ref | [assumption] v6 C2 |
| OpenRouter Decisions API | Present, only provider path. No `provider` field sent, so ZDR not requested | [fact] EV-16; [assumption] EV-33 |
| 9router gateway | Not used by JADE; serves chat only | [fact] grep; [assumption] EV-23 |
| OmniRoute | Removed service; must not be recreated | [fact] EV-64 |
| Jev Router | Not in any gate path (chat only) | [fact] EV-21; D11 |
| Ollama (local) | Only as a safety pattern (`policies/safety.yaml:17-19`); not called | [fact] grep |
| Memory API (:3402), skills API (:3401) | Not referenced; JADE memory lives in the ledger | [fact] grep; [assumption] v6 C4 |
| MCP | Gated through PreToolUse matcher `mcp__.*`; JADE exposes no MCP server | [fact] `WT:jade/ops.py:26-29` |
| Observability (OTel) | Not present; ledger and `jade ledger stats` only | [fact] grep; [assumption] v6 C9 |
| Contextum | Not present | [fact] EV-61; other machines U-11 |
| Obsidian / Kanban | Not present. Deferred work: `work-items.jsonl`, `BACKLOG.md`, ledger mirror | [fact] EV-62 |
| Laravel/PHP, Vue/TS task corpus | Not present. Corpora are shell and claim cases | [fact] EV-63, EV-55 |

## 4. Audit dimensions (V7-R23)

Rows citing an EV row or repo path are [fact]; rows citing v6 Cn are [assumption]. Lifecycle, routing, authorization and recovery detail: section 10.

| Dimension | Current state | Evidence |
|---|---|---|
| Architecture | Hook-gated CLI plus SQLite ledger. Enforcement only through Claude Code hooks | EV-38; [v6 C2](../../14-v6/01-inventory-and-capability-matrix.md#c2-enforcement-and-bypass) |
| Public contracts | Jev payload `{model,state,questions}` returns probabilities; exit codes 0/3/4/5/6; `evidence_source` is sent, although `docs/03` S5 says not | EV-16, EV-37 |
| Dependency graph | 3 runtime deps, 1 provider | 2.1 |
| State machine | 22 states; drives permit use on the gate path; `_gate` never reads ACCEPTED | EV-40; section 10 |
| Routing | `route.preflight` runs only from `jade route` and the eval kit | `WT:jade/cli.py:342`, `jade/evals.py:197` |
| Policies | SI-01..SI-13 (SI-03, SI-04 reserved); risk rules R-nn; human-only classes | [v6 B7](../../14-v6/01-inventory-and-capability-matrix.md#b7-collisions) |
| Authorization | Permits (plan hash, seal, single use), signed approver, level, gate mode. At level `off` only SI-09..SI-12 block | EV-11, EV-38 |
| Tools | Shell classifier, about 4,100 lines | EV-46 |
| Credentials | Seal key beside the ledger. Provider key: Keychain `OPENROUTER_API_KEY`, else env. MCP file readers can read the seal key (W-73) | EV-12; `WT:jade/adapter.py:132-141` |
| Prompts | 11 versioned question templates | EV-59 |
| Tests | Suite status at `e9412f3` [unknown] (U-01). 89 enforce tests skipped on macOS | EV-60 |
| Evaluation | 505 corpus rows; offline kit; zero labelled real rows | EV-55, EV-41 |
| Deployment, telemetry | Tag install, Linux CI, no signing; ledger only, no hook heartbeat | v6 C8, C9 |
| Persistence | WAL ledger. `work-items.jsonl` not atomic, cwd-relative | EV-40, EV-54 |
| Failure recovery | Fails closed for gated calls, open for malformed events; detail section 10 | v6 C2, C5 |

## 5. Current decision flows

Requirements: V7-R20, V7-R25. Detail: [v6 section 03](../../14-v6/03-gaps-and-blind-spots.md). Live counts: EV-51..EV-53a (14 days to 2026-11-16).

| Flow | What happens today | Tag |
|---|---|---|
| Tool call, normal path | Classifier and rules. At level `off` advisory unless SI-09..SI-12. 2439 `would_block`, 1032 `safety_block` | [fact] EV-52; [assumption] EV-11 |
| Low confidence (`jev verify`) | 54.7% of 139 verify calls below p 0.70. All 375 calls: accept 206, abstain 61, reject 107. The agent picks `--risk`, so the same answer exits 0 at low and 3 at high | [fact] EV-51; [assumption] EV-37 |
| Repeated questioning | Exact repeats cached; rewording or `--no-cache` resamples (7 asks, 5 provider calls) | [assumption] v6 C1 |
| Blocked session | Non-zero exit, then chat ask, then dialog (subagent), then terminal permit. Unanswered: R-16 19, R-14 4, R-11 2, R-15 1 | [assumption] order (v6 C2, C7); [fact] EV-53a |
| Human approvals | 2720 gated calls: chat allow 75, dialog allow 25, 20 terminal approvals, zero denials | [fact] EV-53, EV-53a |
| Skipped tasks | No blocker taxonomy; auto work items only for MODEL_DRIFT and QUESTION_DRIFT. FLOW hook nudges by regex, fails open | [fact] `WT:jade/ops.py:171,207`; [assumption] v6 C5 |
| Hallucinated evidence | `q.verify_claim.evidence_is_raw` exists; the agent supplies evidence; no provenance record; 35 `blind_spot` events; rate [unknown] | [fact] EV-59, EV-52 |
| Jev proxy | Idle at level `off`. 48 live calls, 0 allowed | [assumption] v6 C4 |
| Memory | Can silence a whole kind of critical call at low and medium (W-116) | [assumption] EV-38, EV-39 |

## 6. Baseline metrics

Requirement: V7-R20.

| Metric | Value | Capture | Tag |
|---|---|---|---|
| Jev calls | 375 in 14 days, 26 sessions, 46.9/day | 2026-11-16 | [fact] EV-51 |
| Spend | $0.013842 in 14 days; about $0.08 per 1000 verify decisions | 2026-11-16 | [fact] EV-51; [assumption] EV-33 |
| Latency (ledger) | p50 716, p95 1395, p99 2617 ms | 2026-11-16 | [fact] EV-51 |
| Latency (perf run) | n=20: p50 514, p95 639 ms, 0 errors | 2026-11-09 | [assumption] EV-26 |
| Reliability | 1 no-answer in 375; transcript baseline 85 errors in 919 calls (78 `no_parse`) | 2026-11-16 / 11-08 | [fact] EV-51; [assumption] EV-56 |
| Approval burden | Chat 2.8, dialog 0.9, terminal 0.7 per 100 gated calls | 2026-11-16 | [fact] EV-53 |
| Ledger scale | 100k rows = 176 MB; first open 356 ms (synthetic) | 2026-11-09 | [assumption] EV-57 |
| Eval corpora | 242 adversarial, 171 regression, 34 judgment, 34 routing, 24 calibration | 2026-11-16 | [fact] EV-55 |
| Open work items | 37 in this tree (main checkout U-10) | 2026-11-16 | [fact] EV-54 |

**Missing baselines** [assumption, v6 C6, C9, C10]: accuracy, wrong-allow and false-block rates, calibration, completion, blocker resolution, cost per verified outcome, hook liveness.

## 7. Capability matrix (Keep / Fix / Extend / Replace / Remove / Unknown)

Requirements: V7-R09, V7-R23, V7-A127 (in part). Full matrix: [v6 Part C](../../14-v6/01-inventory-and-capability-matrix.md#part-c-capability-matrix). Proposed mapping: IMPLEMENTED+`keep` = Keep; PARTIAL+`proven` = Fix; ABSENT+`proven` = Extend; measured equal-or-safer swap = Replace; REDUNDANT/`rejected` = Remove; UNVERIFIED/`unproven` = Unknown. "(v7)" rows are `unproven` in v6 but asked for by the directive, so Extend. EV-cited rows [fact]; v6-cited rows [assumption].

| Group | Class | Impact | Evidence | Risk if wrong | Migration cost | Depends on |
|---|---|---|---|---|---|---|
| Jev transport, exit codes, bands (`adapter.py`, `thresholds.py`) | Keep | high | EV-16, v6 C1 | low | none | none |
| Jev model id `typesafe/jev-1.14` pinned | Keep | high | EV-17, EV-20 | a moving alias breaks calibration | none | none |
| Docs that deny jev-latest (D10) | Fix (doc only) | low | EV-19 | misleads research | small | owner approval |
| `jev-router` in any gate path | Remove (never add) | high | EV-21, EV-22 | chat router is not a decision primitive | none | none |
| Decisions model registry / second model | Unknown | medium | EV-28..EV-34 (EXPLORATORY) | correlated vendor errors; new egress | medium | doc 05, U-05, U-06 |
| Deterministic gate: hooks, `shellnorm`, SI rules, human-only classes, permits, signed approver | Keep | high | EV-38, EV-46 | none known | none | none |
| SI-08 text scan (PR 47: R5-L4-1 critical, CODE5-1 important open), chat grant scope, memory on critical kinds (W-116), three bypass gaps | Fix | high | D13, v6 C2, EV-38, EV-49 | silent allow of critical calls | medium | PR 47 round 6, U-02 |
| Pre-push guard and remote branch protection | Unknown | high | v6 C2 | real SI-08 boundary unverified | small | owner checks GitHub |
| Level/autonomy coupling (Fix); missing `jev` off switch (Unknown) | Fix / Unknown | medium | EV-39, v6 C3 | autonomy knob changes the safety floor | medium | owner (v6 Part E) |
| State machine as record; ledger | Keep | high | EV-40, EV-57 | none | none | none |
| State machine as authority, preflight on gate path (v7); risk-tied strictness | Extend | high | EV-37, section 10 | the agent picks its own bar | medium | doc 11 contracts |
| Dead states, routes, `score` | Remove or reuse (owner) | low | v6 C1 | dead text read as behaviour | small | doc 11 |
| Evidence record with provenance (v7); repeat detection beyond exact text | Extend | high | EV-37, v6 C1 | sample shopping | medium | doc 03 |
| Calibration labels, random audit, shadow agreement metric | Extend | high | EV-41, EV-48 | no accuracy instrument | medium | owner labelling |
| Offline eval kit, improve pipeline | Keep | medium | v6 C6 | proves code, not Jev | none | labels |
| Work items (atomic save: Fix); blocker records, retry guard (v7) | Fix + Extend | medium | EV-40, EV-54 | skipped tasks lost | small-medium | U-08, U-10 |
| Human text (bidi escape, recommendation, consequences) | Fix + Extend | medium | EV-42, EV-49 | approval of a disguised command | small-medium | PR 42-45 |
| Maintain job, hook heartbeat, purge gaps | Fix | medium | D9, v6 C8-C9 | silent dead upkeep | small | none |
| Claude Code native auto mode, deny rules, sandbox | Unknown (Replace candidate for part of the proxy) | medium | v6 C11 | vendor figures unchecked | medium | replay test |
| Own runtime/scheduler, Platt/conformal libs, OTel | Remove (do not build now) | low | v6 Part D | scope creep | none | none |
| Other harnesses, Windows, PyPI | Unknown | low-medium | EV-43 | public goals | medium-high | OQ-4 |

Reuse [hypothesis]: build on Fix and Extend; Replace only after a measured replay.

## 8. Evidence index and unknowns

Requirement: V7-R21. Full index (EV-01..EV-64, U-01..U-12): `EVD/v7-plan/evidence-index.md`, outside the repo (OQ-1).

Settled: U-03 (D1). Partly: U-02 (D3, D4), U-09 (D7). Open: U-01 tests, U-04 raw jev-router probe (owner call), U-05 vendor retention, U-06 model accuracy (doc 05), U-07 cert-date check, U-08 uncommitted backlog (owner), U-10 live work items, U-11 absent integrations (owner), U-12 `com.jade.maintain` loaded (D9).

## 9. Gate check (V7-R22)

| Gate item | Met? | Basis |
|---|---|---|
| Verified inventory | Yes, with limits | Sections 1-3; v6-derived rows marked [assumption] |
| Documented unknowns | Yes | Section 8: 12 unknowns |
| Claims backed by repo evidence (V7-R131) | Yes for [fact] rows | Path, command or EV id |
| Phase 1 audit, flows, baseline (V7-R23, V7-R25, V7-R20) | Yes | Sections 4-6 |
| Current vs target matrix (V7-A114) | Yes | Section 10, read from code at `8dfa86d` |

Planning gate passes. Before any v7 code: U-01, U-02, and PR 47 closing R5-L4-1 and CODE5-1 with a clean round.

## 10. Current state vs target state

Requirements: V7-A02, V7-A114, V7-A34; rows cover every V7-A id with target doc 01 or 11. The addendum flow is a target, not a claim about the repo (V7-A02). Status: implemented, partial, missing, contradictory, unnecessary (none found). Conf: H = read in code this session; M = code read, runtime behaviour not exercised. Paths are `WT:jade/` unless shown. All rows [fact] for the code cited; status is [hypothesis] judgement.

### 10.1 Flow and ownership

| Id | Target | Status | Today (file:line) | Conf |
|---|---|---|---|---|
| A09 | Trigger enters evidence collection | partial | Decisions open from the hook (`hooks.py:602`, ESCALATE), chat ask (`askchat.py:124`), CLI (`cli.py:242`). JADE collects no evidence; caller passes `evidence_refs` (`decide.py:20-23`) | H |
| A10, A71 | Sufficiency check, investigate before human | partial | PF-03 raw-evidence check (`route.py:135`) only on `jade route`; NEEDS_EVIDENCE/ABSTAINED `add_evidence` loop (`state_machine.py:35`); low-risk abstain cannot escalate before revisions used (`:75`). No investigate/retrieve step | H |
| A11 | Risk before routing | implemented | `policy.assess` (`policy.py:1441`) inside `open_decision` (`decide.py:26-27`); PF-02 (`route.py`) | H |
| A13, A78 | Proposal: alternatives, evidence, assumptions, confidence, failure modes, next action | partial | Decision has `candidate_actions`, `assumptions`, `constraints`, `evidence_refs` (`schemas/decision.schema.json:7`); Jev `p` in `jev_calls` (`ledger.py:26`). No confidence, failure modes or verification requirement on the record | H |
| A14 | Verify if needed, then policy gate | partial | `jev verify` is the agent's choice; gate runs on every gated call anyway (`hooks.py:532`) | H |
| A15 | Gate: authorize / human / deny + blocker | partial | Permit consume (`hooks.py:564-582`); ask chain (`:585-595`); permit request (`:600-607`); deny = exit 2 + `safety_block` event (`:404-407`). No blocker record with owner and resume condition | H |
| A16 | Approved / pending / rejected | partial | Approved: `human_accept`, `authorize`, `execute` (`hooks.py:572-576`); pending: PENDING permit + WAITING_HUMAN (`:604-606`); rejected: REJECTED (`state_machine.py:45`). No blocker record | H |
| A17, A93 | Host executes, returns observed result | partial | Host runs the tool; PostToolUse stores `observed` success/failure, `attribution="indirect"` (`hooks.py:697`), only for decisions in EXECUTING | H |
| A18 | Valid / retry-replan / unsafe | partial | SUCCEEDED needs observed success (`state_machine.py:136`); retry returns to EVALUATING, not evidence (`:42,44`); no outcome = OUTCOME_UNKNOWN + SI-07 hold (`decide.py:125-138`, `hooks.py:555-562`) | H |
| A19 | Blocker reopens only on evidence or approval | partial | Reopen events are `add_evidence`, `human_accept` (`state_machine.py:35,45`); work items reopen on dependencies (`work.py:82-83`) | H |
| A20 | Outcomes feed evaluation, return control | partial | `labels` table (`ledger.py:45`), offline `improve` pipeline; outcomes not fed automatically | M |
| A24 | Replan re-checks context and authorization | partial | Authorization re-checked on every call (fingerprints at consume, `hooks.py:542-567`); stale PENDING permits revoked (`permits.py:367-372`). `resume` does not refresh context | H |
| A25 | Decision plane owns framing to outcome; no execution, scheduler, grants | partial | No scheduler or executor in JADE; decision core imports the policy plane (`decide.py:8`) | H |
| A26 | Host owns execution, cannot bypass policy | implemented | Claude Code executes; JADE only gates through hooks (`ops.py:38-39`). A `jev verify` exit 0 is not enforced as authorization | H |
| A27 | Independent policy plane; confidence never authority | contradictory | Hook, `policy.assess`, permits (single use, seal, expiry: `permits.py:285`, `:138-144`) exist but share package and ledger. Proxy lets a Jev accept allow a call at levels low/medium (`proxy.py:3-10`, channel `jev` at `state_machine.py:128`); dormant at `off` | M |
| A28 | Learning cannot touch hard policy | implemented | Only evaluated targets can be promoted (`improve.py:21,27`); `human_required_for` and canary opt-in (`policies/promotion.yaml:28,38`) | H |

### 10.2 Record and states (mapped by meaning, not name)

| Id | Target | Status | Today (file:line) | Conf |
|---|---|---|---|---|
| A30 | Durable, auditable record | implemented | WAL ledger; `events` journal (`ledger.py:35-37`) | H |
| A31 | ID, correlation, version, timestamps | partial | `decision_id`, `schema_version`, `timestamps`, `trace_id`; `task_id` is new random per decision, not the host task (`decide.py:37`) | H |
| A32 | Provenance, state, rationale, models | partial | Status yes; `evidence_refs` without provenance class; route string + event guards; models in `jev_calls` by `decision_id` (`ledger.py:22-28`) | H |
| A33 | Budgets, risk, verdict, approval, retries, outcome | partial | Risk + `rule_id`, `permit_id`/`ask`, `outcomes` rows. No budgets; `revision_count` counts only `add_evidence`/`revise` (`state_machine.py:146`) | H |
| A35 RECEIVED | | partial | In the table (`state_machine.py:27`), but rows are inserted at CLASSIFIED (`decide.py:53-58`): never stored | H |
| A36 GATHERING_EVIDENCE | | partial | No step; nearest NEEDS_EVIDENCE (agent supplies) | H |
| A37 ASSESSING | | implemented | VALIDATED to CLASSIFIED with rule risk (`state_machine.py:28`) | H |
| A38 DECIDING | | implemented | Named EVALUATING (`state_machine.py:29-34`); `jev_*` from Jev (`decide.py:80`); FAST/DEEP results are agent self-reports via `jade decide event` (`cli.py:265-281`) | H |
| A39 VERIFYING (pre-authorization) | | contradictory | JADE VERIFYING is the post-execution outcome check (`state_machine.py:40-41`) | H |
| A40 AWAITING_AUTHORIZATION | | implemented | WAITING_HUMAN + PENDING permit (`state_machine.py:38`, `hooks.py:604-606`) | H |
| A41 READY_FOR_EXECUTION | | implemented | AUTHORIZED (`state_machine.py:38-39`) | H |
| A42 EXECUTING_EXTERNAL | | implemented | EXECUTING, at risk >= high only the hook may enter it (`state_machine.py:82-89`) | H |
| A43 EVALUATING (post-outcome) | | contradictory | JADE EVALUATING is pre-decision; post-outcome is VERIFYING | H |
| A44 COMPLETED | | implemented | SUCCEEDED (observed only), COMPENSATED, ROLLED_BACK (`state_machine.py:12`) | H |
| A45 NEEDS_EVIDENCE | | implemented | `state_machine.py:32,35` | H |
| A46 BLOCKED | | partial | No decision state; blocks are events; work items are written only by hand (`cli.py:390-412`) or drift checks (`ops.py:171,207`) | H |
| A47 DEFERRED | | missing | WAITING_DEPENDENCY has an exit but no entering transition (`state_machine.py:44`): unreachable | H |
| A48 RETRYING | | partial | RETRY_SCHEDULED (`state_machine.py:42`); retries uncounted | H |
| A49 FAILED, A50 CANCELLED | | implemented | `state_machine.py:41`, `:58-59`; agent may not cancel while holding (`:25,88`) | H |
| A51, A117 | Guard, owner, recovery per transition | partial | Guards in `next_state` (`state_machine.py:54-79`); owners in `agent_may` (`:82-89`); no owner field, recovery not per transition | H |
| A52 | Timeout is never approval | partial | `deadline` gives EXPIRED (`state_machine.py:60-63`); dialog "gave up" is no answer (`askdialog.py:9`). Hook over budget exits 0 when the failsafe finds no reason (`hooks.py:917-925`), and the host proceeds after its own timeout | M |

### 10.3 Bounds, recovery, outcome, contracts

| Id | Target | Status | Today (file:line) | Conf |
|---|---|---|---|---|
| A53 | No naive loop | implemented | Provider retry loop is deadline-bound (`adapter.py:282-286`); other loops not read | M |
| A54, A59, A96 | Attempts, depth, bounded replan | partial | `MAX_REVISIONS = 3` (`state_machine.py:11,77`); FAILED-retry cycle uncounted; `samecmd.py:30` depth is parser depth, not replan | H |
| A55 | Wall-clock | implemented | `expires_at` + `deadline`; hook 6 s (`hooks.py:26`); provider `deadline_s` (`route.py:24`) | H |
| A56, A57 | Tokens, money | missing / partial | Tokens and `cost_usd` recorded (`ledger.py:26`); `DAILY_BUDGET = 500` calls only warns (`ask.py:20,178-179`) | H |
| A58 | Tool calls | missing | No counter (grep `max_tool`: no hit); host-owned | H |
| A60 | Resumable state | implemented | Ledger rows; WAITING set and `prev_status` restore (`state_machine.py:13,64-67`) | H |
| A61, A62 | Idempotency, concurrency | implemented | `idempotency_key UNIQUE` (`ledger.py:37`), skip on repeat (`state_machine.py:116-118`); `BEGIN IMMEDIATE` (`ledger.py:204`) | H |
| A63, A64 | Stale approvals, replay | implemented | Expiry, seal, stale revoke (`permits.py:138-144,367-372`); nonce + `usage_limit 1` (`:285`); terminal decision revokes permits (`state_machine.py:159`) | H |
| A65 | Duplicated callbacks | implemented | Same host call id answered once (`hooks.py:548-551`) | M |
| A66 | Partial failures | partial | OUTCOME_UNKNOWN + SI-07 hold, gated decisions only (`decide.py:125-138`) | H |
| A67 | Changed evidence | partial | Precondition fingerprints (`hooks.py:542-544`), `context_fingerprint` (`decide.py:40`); PF-07 only on `jade route` | H |
| A68 | Owner + reopen condition | partial | Work items have `owner`, `retry_or_revisit_trigger` (`schemas/work-item.schema.json:15,21`); decisions have neither | H |
| A90, A91 | Host UI; persist pending, resume after revalidation | partial | Chat rewrite, macOS dialog, terminal fallback; Claude Code on macOS only. `asks` table (`ledger.py:48-51`), PENDING permits; revalidated at consume; resume is the agent retrying | M |
| A94, A95 | Acceptance criteria; discrepancy and root cause | missing | No criteria field; `q.root_cause.*` templates not linked to outcomes | H |
| A106, A118 | Versioned contracts | partial | 6 schemas (2.1). No schema file: DecisionRequest, EvidenceBundle, PolicyVerdict, ApprovalRequest, DecisionTrace (data exists, 11 §4) | H |
| A116, A120 | Transition table; idempotency rules | implemented | In code (`state_machine.py:26-46`; A61-A65); doc 11 documents them | H |
| A115, A127 | Mermaid; retain/refactor/remove list | missing / partial | Doc 11 deliverables; section 7 is the start | H |
| A104, A130 | Read-only audit; name absent sources | implemented | Sections 2-4; section 3 and U-11 | H |

**Readings** [hypothesis]: gaps are blocker records, budgets, acceptance criteria, evidence provenance; JADE EVALUATING/VERIFYING invert the addendum meaning, so map before reuse; the proxy is the only place confidence acts as authority (owner decision, doc 10).

## Open questions for the owner

| # | Question | Recommended default | Why |
|---|---|---|---|
| OQ-1/[H-10](10-decisions-and-questions.md) | Copy the evidence index into the repo? | Yes, paths redacted | Links must resolve |
| OQ-2/[L-04](10-decisions-and-questions.md) | One owner-approved probe saving raw jev-router output (U-04)? | Yes, one call | Assertion becomes evidence. Cost [unknown] |
| OQ-3/[A-01](10-decisions-and-questions.md) | Do Contextum, Obsidian or a Laravel/Vue corpus exist elsewhere (U-11)? | Treat as absent | Else we invent capabilities |
| OQ-4/[A-02](10-decisions-and-questions.md) | Is multi-harness use a v7 goal? | No: Claude Code here | No adapter or demand |
| OQ-5/[A-04](10-decisions-and-questions.md) | Should `com.jade.maintain` be loaded? | Yes; check with `jade check` | launchctl and `maintain.log` conflict (D9) |
| OQ-6/[A-03](10-decisions-and-questions.md) | Commit W-96..W-116 before v7 ids (U-08)? | Yes | Avoids id collisions |

## Deferred items

| Item | Owner | Priority | Next action |
|---|---|---|---|
| Full suite at `e9412f3`, empty HOME, `CI=1` (U-01) | agent (after approval) | P1 | Read the CI run first |
| PR 47 round 6 after R5-L4-1 and CODE5-1 fixes (W-115) | agent + owner | P0 | Re-run round-5 shapes against BASE `8100ea1` |
| Docs denying jev-latest (D10) | agent, owner approves | P1 | G7-07 |
| PR 46 title (D6) | owner | P3 | Rename |
| Live work items, main checkout (U-10) | agent | P2 | `jade work list` |
| Hook probe cells 1, 2, 11 (U-09) | human runs | P1 | V6-15 host probe (G7-14) |
| U-05, U-06, U-07 | agent | P1 | Docs 04, 05 |
