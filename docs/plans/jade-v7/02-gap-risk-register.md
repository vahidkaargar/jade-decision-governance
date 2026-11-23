# JADE v7 gap and risk register

Planning only. Nothing in this document is implemented.

**Scope.** The one register of capability gaps, threats, technical debt and unresolved items for JADE v7 (V7-R118, V7-A123). v7 is a delta on the v6 plan ([../../14-v6-master-plan.md](../../14-v6-master-plan.md)); this file links to v6 rows instead of restating them. Every item has an owner, priority, dependencies, evidence, acceptance criterion and next action. It also closes the directive's Phase 1 gate. No row is written to `jade work` yet (Q7-01).

**Reconciliation (V7-A07).** JADE is a decision plane: it owns the decision lifecycle and blocker records. The host (Claude Code or another runtime) owns execution, tools, operational retries and checkpoints. An independent policy and authorization plane enforces at action time; in JADE today that is the PreToolUse hook, `policy.assess` and permits. Where directive component 8 "Execution & Continuity Orchestrator" conflicts, this split wins.

## 0. Reading rules

| Item | Rule |
|---|---|
| Tags | [fact] cites a repo path, evidence file or URL with capture date. [assumption], [hypothesis], [unknown] as in the evidence index. |
| Evidence ids | `EV-nn`, `U-nn`: v7 evidence index `EVD/v7-plan/evidence-index.md`. `v6/U-nn`, `v6/Q-nn`, `v6/LD-nn`, `v6/DF-nn`: [v6 doc 13](../../14-v6/13-deferred-and-open-questions.md). `V6-nn`: task of [v6 doc 12](../../14-v6/12-roadmap.md). |
| `EVD` | `/Users/example/.claude/projects/-Users-example-Documents-projects-jade--claude-worktrees-plan-v7/b70d8811-d956-4521-97e3-229a4c11d2f4/evidence` |
| `JR`, `R5` | `JR` = `EVD/jev-models/REPORT.md` (final Jev study; its code claims come from analysis files, not re-read). `R5` = `EVD/round5-findings-c676801.json` (PR 47 head `c676801` vs BASE `8100ea1`). |
| Priority | P0: hides a safety hole or blocks baseline B. P1: needed before the 07 milestone that holds it. P2: useful, can wait. P3: only on its trigger. §3 groups rows by priority. |
| Blind spots (V7-R139) | v6-derived rows not re-read in code (01 §4); host probe stale (U-09); one-host evidence (EV-27); zero labels (EV-41). |
| Owner | human: git write, sudo, live provider call, ledger read, JADE level or policy change. agent: read, test or doc on a branch after approval. both: agent prepares, human runs or decides. |
| Ids | `G7-nn` is a register id, never a `W-` number (G7-12). |
| Hard rules | A probability is never permission. Deterministic gates stay independent of any model. Only the human sets the JADE level. A fallback never lowers a bar. Silence or timeout is never approval. Each added vendor is a new egress destination. |

## 1. Capability matrix delta (V7-R26)

Full v6 matrix: [v6 doc 01 Part C](../../14-v6/01-inventory-and-capability-matrix.md). Only changed or added rows. Migration cost S (days), M (1-2 weeks), L (more) [assumption].

| Component | Verdict | Impact | Evidence | Risk | Cost | Depends |
|---|---|---|---|---|---|---|
| Deterministic layer (SI rules, `shellnorm`, policy YAML) | Keep, Fix | Floor of every decision | EV-38, EV-46, EV-49, R5 | Holes run silently (G7-04, G7-18, G7-38, G7-39) | M | G7-01 |
| Jev adapter pinned to `typesafe/jev-1.14` | Keep | Live verify/choice path | EV-16, EV-17 | Snapshot change (G7-10, G7-43) | none | none |
| `~typesafe/jev-latest` as default | Remove now; reopen only with G7-42..G7-44 guard (JR §3, D3) | Moving pointer breaks per-model calibration | EV-18, EV-20 | Silent model change | S | G7-42 |
| Model accept list `accepted_resolved` in `registry/` | Fix: move to human-only store | It is authority to decide for the owner | JR §10 | Agent adds a model (G7-42) | S | JR D2 |
| `typesafe/jev-router` | Remove from every gate path; Unknown as a chat arm outside the gate | Chat router, not a Decisions primitive | EV-21, EV-22 | Chat text treated as a verdict | none | G7-08 |
| Other Decisions models (41 ids, 17 vendors) | Unknown; Extend only as a stricter veto in shadow | Possible independent check | EV-28 to EV-34, JR §2 | Correlated errors, egress (G7-45) | M | G7-25, G7-26 |
| Memory (remembered answers) | Fix | Silences asks | W-116, EV-38, EV-39 | Critical calls silenced | S | v6/Q-03 |
| Chat and dialog ask channels | Fix | What the owner sees and approves | EV-38, EV-49 | Wrong-call grant; hidden text | M | v6/Q-04, v6/Q-12 |
| Ledger and seal | Keep; Fix purge | Audit trail | EV-12, EV-52 | Retention promise broken (v6/Q-11) | S | v6/Q-11 |
| Work-item store | Fix | Deferred-work persistence | EV-09, EV-54 | Id collisions, cwd-relative, non-atomic | S | G7-12 |
| Decision record, state machine, contracts | Extend (doc 11) | Addendum lifecycle | EV-16, EV-40, addendum §4 | Duplicate subsystem (V7-A04) | M | G7-49, G7-50 |
| Gate mode and level | Keep | Human-only safety knob | EV-10, EV-11 | Level `off` floor is soft (G7-06) | none | v6/Q-05 |
| Calibration and learning | Extend | Threshold meaning | EV-41, EV-48 | No labels; group key lacks vendor (G7-44) | L | G7-19 |
| MODEL_DRIFT / QUESTION_DRIFT checks | Keep; Extend | Snapshot and template change | `WT:jade/ops.py:155-208@8dfa86d`, EV-52 | Informational only | S | G7-10, G7-11 |
| Real-shell sandbox (SI-13) | Keep; Extend (Linux) | Containment | EV-01, EV-04 | Installed 0.6.1 lacks it | M | G7-24 |
| Durable-execution runtimes, OPA/Cedar | Remove (not adopt); host owns execution | none | EV-45, EV-46 | Rewrite risk | none | none |

9router stays chat-only (EV-23, G7-08). Contextum, Obsidian/Kanban, PHP/Vue corpus: not present (G7-37).

## 2. Gap areas (V7-R28, V7-A105)

| Area | Rows |
|---|---|
| Decision traceability | G7-20, G7-10, G7-11 |
| Deterministic enforcement | G7-01 to G7-06, G7-18, G7-38 to G7-41 |
| Model identity and egress | G7-26, G7-42 to G7-47 |
| Calibration | G7-19, G7-25, G7-27 |
| Outcome tracking | G7-21, G7-30 |
| Recovery, lifecycle | G7-22, G7-49 to G7-51 |
| Test coverage | G7-15, G7-28 |

**Addendum component map (V7-A105).** Status per 01 §10 where read in code, else v6 evidence.

| Addendum part | Status (basis) | Row |
|---|---|---|
| Policy plane (hook, `policy.assess`, permits) | implemented, with holes (EV-38) | G7-01..G7-06 |
| Action-time recheck after approval | partial: Allow keyed on normalised command (EV-38) | G7-03, G7-51 |
| Decision record with addendum fields | partial: ledger, permit, work states (EV-40) | G7-49 |
| Eight versioned contracts | partial: 6 schemas (01 A106) | G7-50 |
| Idempotency, replay, stale approval | partial: keys, BEGIN IMMEDIATE, replay and stale revoke exist (01 §10.3); no host-callback or ExecutionResult rule | G7-51 |
| Executor inside JADE (directive component 8) | contradictory; unnecessary, host owns it | none |
| FAST/JEV/DEEP | partial: rule fast path + Jev; no DEEP model (EV-34) | §9 |

## 3. Register (V7-R13, V7-R118, V7-A123)

### 3.1 P0

| Id | Title | Type | Owner | Depends | Ev | Accept | Next action |
|---|---|---|---|---|---|---|---|
| G7-01 | PR 47 still weaker than main on SI-08 shapes (dirty W-108..W-115) | threat | both | v6/Q-02, V6-03 | EV-50, R5 | `never_looser`: every shape main blocks is blocked at PR 47 head; G7-38..G7-41 closed; one clean independent round | Fix G7-38, G7-39 on the PR branch; rerun R5 drivers |
| G7-38 | R5-L4-1 (critical): `sed p<<<'git push origin main'\|sh 2>&1` passes SI-08 at `c676801`; BASE blocks. Also `uniq`, `sort -u`, 16 sink spellings; famG grid 1,344 weaker rows | threat | both | G7-01 | R5 (2 verifiers; real-shell stub push, bash 3.2, zsh 5.9) | famG and famA grids: zero weaker-than-BASE rows; real-shell stub run logs no push | Fix the sink redirect parse [hypothesis R5: `2` read as a script argument]; rerun grid |
| G7-39 | CODE5-1 (important): `cd` plus a brace word or escaped operator with unbalanced parens runs `cd` twice in the cwd tracker; a later bare `git push` loses SI-08 | threat | both | G7-01 | R5 (2 verifiers, fixture: outer on feature, `sub/` on main) | The six R5 fixture commands fire SI-08 as on BASE | Fix cwd tracking; add the six as tests |
| G7-42 | `accepted_resolved` (who may decide for the owner) lives in `registry/`; R-13 protects neither `registry/` nor `policies/models.yaml` | threat | both | JR D2 | JR §3 table, §10 critic 2 | List in a human-only store changed only by a terminal command; test: agent write to both paths blocked; `request_id` equals `DEFAULT_MODEL` test | Owner answers D2 |
| G7-02 | Memory covers critical calls R-01, R-03, R-04 (W-116) | threat | both | v6/Q-03, G7-01 | EV-38, EV-39 | `gate_replay`: silent allows of critical calls = 0; N7..N9 pass | Owner answers v6/Q-03 |
| G7-03 | Chat Allow keyed on normalised command, not `plan_hash` | threat | both | v6/Q-04 | EV-38 (C4) | Wrong-call grants = 0 (N10, N11) | Owner answers v6/Q-04 |
| G7-04 | Rule holes: `curl URL \| sh # x` low; `gh api .../merge`; `git send-pack`; writes to `authorized_keys`, `CLAUDE.md`, `.mcp.json` | threat | both | v6/Q-10, G7-01 | EV-49; [v6 doc 03](../../14-v6/03-gaps-and-blind-spots.md) | Each shape rated like its sibling | Owner answers v6/Q-10 |
| G7-05 | Bidi and control characters reach chat, dialog, permit text; clipped commands hide the middle | threat | both | v6/Q-12 | EV-49 | Raw bidi or clipped risky text shown = 0 (N13, N14) | Owner answers v6/Q-12 |
| G7-12 | Work-item id collisions (`W-115` two meanings; W-96..W-116 only in dirty file; `jade work list` reads cwd) | debt | both | G7-13 | EV-09, EV-54 | One id register; duplicate ids = 0 (V6-02); fresh G7 ids | Owner approves V6-01, V6-02 order |
| G7-13 | Loose uncommitted work in main checkout (W-96..W-116, BACKLOG, plans) | debt | human | none | EV-09, U-08 | Every loose file on a named branch, explicit paths | Owner decides v6/LD-03 |

### 3.2 P1

| Id | Title | Type | Owner | Depends | Ev | Accept | Next action |
|---|---|---|---|---|---|---|---|
| G7-41 | R5-DIFF-1 (disputed): glued or unquoted here-string pushes weaker on counts (1,222 of 40,000 matrix rows); one verifier's real shell shows `cat 0<<< git push origin :main \| sh` pushes nothing | unknown | agent | G7-38 | R5 | Real-shell stub check per weaker class; real ones move to G7-38, rest closed as rating-only | Run R5 real-shell driver per class |
| G7-43 | New resolved snapshot restarts at default bands 0.70/0.30 and drops a stricter promoted band | threat | both | G7-42 | JR §3 rule 7, §10 | Band floor: no new snapshot below the strictest band its family holds; JR §3 test 5 passes | Design note with G7-42 |
| G7-44 | Calibration group key (`jade/thresholds.py` `group_of`) lacks the vendor | threat | agent | G7-42 | JR §3, §10 | Vendor in key; dated-id regex; uniqueness test | Test-first PR after D2 |
| G7-45 | Shadow lane egress: a second company gets redacted state; redaction removes secret shapes only (paths, code, names, emails leave); queue unbounded; host checks miss a company change | threat | both | JR D6, G7-26 | JR §5.2 rules 11-13, §7 row 3 | Never-sent filter as for Jev; verify only; `provider {zdr, only}`; provider recorded and alerted; validated fields only; bounded tables; threat-model row; written D6 yes | Lane stays off until D6 |
| G7-46 | A fallback model lets an agent push its claims onto a weaker model by causing rate limits | threat | agent | none | JR §5.2 rule 6, §10 | No fallback model; if ever built it yields only ABSTAIN or REJECT; test: forced 429 never ends in accept | Add exit-5 counter only |
| G7-47 | Retired-model 404 and ZDR 404 map to exit 6 ("fix the question") instead of exit 5 | gap | agent | none | JR §3, §8 step 0c, §7 row 11 | Both give exit 5 with a clear message and a work item; canary: non-ZDR model with `zdr:true` expects 404 | Small PR with tests (no paid call) |
| G7-49 | No durable decision record with the addendum §4 fields (correlation id, budgets, policy verdict, approval ref, outcome missing) | gap | agent | G7-12 | addendum §4; EV-40 | 11 §2 maps each addendum state (V7-A34); state-machine tests per 07 M7 | Drafted in 11 §2 |
| G7-50 | No versioned schemas for the eight doc-11 contracts (11 §4) | gap | agent | G7-49 | addendum §8.3; EV-16 | Schemas per 11 §4; contract tests per 07 M2 (round trip, unknown version rejected, previous version read) | Drafted in 11 §4 |
| G7-51 | Recovery rules for host callbacks, changed evidence and bounds (idempotency and concurrency exist, 01 §10.3) | gap | agent | G7-03, G7-50 | addendum §4; EV-38, EV-40 | Duplicate callback no-op; approval bound to `plan_hash`, rechecked at action time; changed evidence re-asks; bounds per 11 §3.1 | Drafted in 11 §3 |
| G7-06 | Level `off`: only SI-09..SI-12 block; `jade check` prints ok | gap | human | v6/Q-05 | EV-10, EV-11 | `jade check` states the real floor (V6-10) | Owner answers v6/Q-05; plan never changes the level |
| G7-07 | Docs say `jev-latest` does not exist (docs/00 S3, docs/01 ADR-02 [JR §9 row 2], `README.md:219`, `adapters/jev/probe_openrouter.py:60`) | debt | agent | Q7-04 | EV-18, EV-19 | All four: `~typesafe/jev-latest` exists, resolved to `typesafe/jev-1.14-20261014` 2026-11-16, not default; probe uses tilde id | Docs PR after owner yes |
| G7-14 | Host probe session not run (v6/U-01..U-08) | unknown | human | none | EV-27, U-09 | Probe table for current `claude --version` | Owner runs V6-15 |
| G7-16 | Owner wiring unknown: branch protection, pre-push guard, maintain job | unknown | human | none | v6/U-16 | Owner confirms each (W-184) | Owner checks GitHub |
| G7-19 | Zero labelled ledger rows; Jev p not calibrated | gap | human | V6-20, V6-21 | EV-41, EV-48; JR D5 | 150 labelled rows, rubric, kappa; fresh 200-item set labelled blind by two people | Owner sets label hours |
| G7-20 | No evidence record per decision; `evidence_source` sent to Jev | gap | agent | v6/Q-07 | EV-37 | Each decision links evidence ids; docs and code agree on egress | Design in `03` |
| G7-21 | Labels only for accepted-and-ran; false-reject unmeasurable | gap | both | G7-19 | EV-48, EV-53 | Both error rates measurable | Read-only ledger script |
| G7-25 | jev-models bench EXPLORATORY (3620 rows, 54 cases) | gap | both | G7-19 | EV-29 to EV-32, U-06 | Thresholds and holdout fixed in `05` before any run | Write spec |
| G7-26 | Egress: no `provider` field; ZDR not requested; 5 vendors fail ZDR; terms unknown | threat | both | Q7-06, JR D1, D4 | EV-33, U-05 | `provider {zdr:true, only:["typesafe"]}`; dated terms per vendor | Owner answers D1, D4 |
| G7-27 | Date-arithmetic claims fool half the models | gap | agent | none | EV-31, U-07 | Deterministic date check before the model (JR step 0e) | Code read of `jade/` |
| G7-28 | CI at `e9412f3` unread; 89 enforce tests skip | gap | agent | none | U-01, EV-60 | `gh run list` read; empty-HOME `CI=1` run | Read CI |
| G7-29 | PR stack is not one chain | unknown | agent | none | EV-07, EV-15, U-02; 01 D3, D4 | merge-base table for 42-47 incl. conflicts | Finish U-02 conflict check |
| G7-30 | Jev proxy passes its gate while owner denials = 0 | threat | human | v6/Q-07, v6/Q-08 | EV-53a | Deny gate or proxy off | Owner answers v6/Q-07, Q-08 |
| G7-31 | v6 owner batch v6/Q-01..Q-12 unanswered | unknown | human | none | v6 doc 13 §2 | Each answered | Carried in `10` |

### 3.3 P2

| Id | Title | Type | Owner | Depends | Ev | Accept | Next action |
|---|---|---|---|---|---|---|---|
| G7-40 | R5-L4-2 (minor): ratings below BASE, no block lost: `<&- cat ~/.ssh/id_rsa` critical R-04 to medium R-30; `git push origin feature <&0` high R-10 to medium R-19 (not in CHANGELOG). R5-L4-3 documented (W-114) | debt | both | G7-01 | R5 | CHANGELOG names both shapes, or rating restored; V6-03 known-gap list signed | Add CHANGELOG line on PR 47 |
| G7-08 | `jev-router` via 9router and 9router chat-only rest on assertion | unknown | human | Q7-05 | EV-22, EV-23, U-04 | Saved redacted probe log; test fails on a chat model in any decision role | Owner approves one probe |
| G7-48 | Doc corrections beyond G7-07 (JR §9 rows 5-10): threat row "a company sees the redacted state"; gateway list after D6; `accepted_resolved` wording (docs/00, docs/07); Jev 0.89 vs 0.83; exit-6 text; ADR-13: no model-agreement rule adds an exception | debt | agent | G7-42, G7-47 | JR §9 | Each row fixed; grep test for exit-6 and `jev-latest` wording | Docs PR with G7-07 |
| G7-10 | MODEL_DRIFT: 1 event; drift informational; new snapshot runs at default bands | gap | human | G7-19, G7-43 | EV-52 | Stray row reviewed; alias ids never accepted silently | Owner reads the row |
| G7-11 | `q.human_proxy.would_allow` v2 not calibrated (W-102) | gap | human | v6/Q-09 | EV-59 | v2 out of champion slot | Owner confirms v6/Q-09 |
| G7-15 | Flaky SQLite "malformed" (W-95) | debt | agent | G7-28 | W-95 | Root cause; 50 clean reruns | Repro in `python:3.13-slim` |
| G7-17 | Seal-key read gate misses MCP readers and ancestor Grep (W-73) | threat | agent | V6-18 | W-73; EV-12 | Each reader path blocked | Fold into V6-18 (c) |
| G7-18 | Redirect specs (W-96, W-99, W-104) residuals; `NULL_REDIRECT` quadratic | debt | agent | G7-01, G7-13 | dirty W rows | One redirect grammar, test per form; linear on 100k digits | After G7-38 |
| G7-22 | Recovery: no decision re-ask bound; partial resume briefing; non-atomic save | gap | agent | G7-12, G7-49 | EV-40 | Bounded JADE re-asks (tool retries are host-owned); atomic save; briefing test | V6-27 |
| G7-23 | No plain-language mode | gap | both | v6/Q-12 | EV-42 | Ask text passes plain-language check | v6/LD-14 |
| G7-24 | Installed 0.6.1 lacks PR 48 (SI-13) | debt | human | v6/LD-01 | EV-03, EV-04 | Installed version has SI-13 | Owner installs next tag |
| G7-32 | Seal key same-user; `jade ack` session self-asserted; `JADE_SESSION_ID` not in SI-11 | threat | both | v6/Q-06 | W-41, W-81, W-92 | Closed or signed known gap | Owner answers v6/Q-06 |
| G7-33 | Host `JADE_*` overrides journaled (W-46) | gap | human | G7-16 | W-46 | `jade check` shows each override | Owner reviews scope |
| G7-34 | Neither v6 nor v7 plan is Jev-verified | gap | human | final docs | v6/U-25 | `jev verify` exit 0 or code reported | Orchestrator runs it |

### 3.4 P3

| Id | Title | Type | Owner | Depends | Ev | Accept | Next action |
|---|---|---|---|---|---|---|---|
| G7-35 | Stale rows W-88, W-23 | debt | human | G7-12 | EV-03, EV-10 | Closed as superseded | Owner approves |
| G7-36 | Jev latency p95 1,395 ms vs 1.0 s budget | gap | human | none | EV-51 | Owner decides after 300 live calls | v6/LD-20 |
| G7-37 | Contextum, Obsidian/Kanban, PHP/Vue corpus not present | unknown | human | Q7-02 | EV-61 to EV-63 | Owner says present (path) or absent | Q7-02 |

G7-09 is unused (merged into G7-08); no item was removed.

## 4. Baseline metrics (V7-R29)

[fact] `jade ledger stats`, 14 days to 2026-11-16.

Jev calls 375 in 26 sessions (46.9/day); verify mean p 0.59, 54.7% below 0.70; exits accept 206, abstain 61, reject 107, none 1; latency p50/p95/p99 716/1,395/2,617 ms; spend $0.013842 (EV-51). Gated calls 2,720; asks per 100: chat 2.8, dialog 0.9, terminal 0.7 (EV-53). Owner denials 0 (EV-53a); labelled rows 0 (EV-41); eval rows 505 (EV-55); open work items 37 (EV-54).

## 5. Reuse strategy (V7-R29, V7-A04)

| Rule | Basis |
|---|---|
| Reuse every Keep component; v7 adds roles around them, never replaces the deterministic layer | EV-45, EV-46 |
| No duplicate subsystem: the decision record extends ledger, permit and work states; no in-JADE executor | addendum §3 |
| v6 V6-01..V6-34 is the v7 base; v7 milestones start after V6-14 (0.7.0) | v6 doc 12 §4.2 |
| New model roles enter in shadow, may only veto | EV-34 |
| No new store until an Obsidian/Kanban target is shown present | EV-62 |

## 6. Phase 1 gate check (V7-R29)

Audit: [01](01-current-state-audit.md) (draft). Prioritized gaps: section 3 (draft). Baseline: section 4; CI open (G7-28). Reuse: sections 1, 5.

## 7. Persistence (V7-R13, V7-R136)

[fact] Obsidian/Kanban is not present (EV-62); target is `work-items.jsonl` on a committed branch, mirrored by the ledger. After Q7-01 and V6-02, each row becomes one work item with `owner`, `priority`, `depends`, `evidence_refs`, `acceptance`, `next_action`, `retry_or_revisit_trigger`. Rows that duplicate a W row link to it (the W id is in each row). New ids start above W-117..W-137 and W-140..W-190.

## 8. Closing crosswalk: v6 doc 13 to v7

Nothing from v6 doc 13 is dropped.

| v6 doc 13 group | Count | Where now |
|---|---|---|
| Owner batch Q-01..Q-12 | 12 | v6 doc 13 §2; G7-31; safety ones G7-01..G7-06, G7-30, G7-32 |
| LD-01..LD-32 | 32 | Unchanged. Taken: LD-02 (G7-12), LD-03 (G7-13), LD-20 (G7-36). Linked: LD-01, LD-08, LD-10, LD-14, LD-29, LD-30 |
| v6/U-01..v6/U-26 | 26 | v6/U-01..U-08 G7-14; v6/U-09..U-12 G7-21; v6/U-14 G7-26; v6/U-15 G7-28; v6/U-16 G7-16; v6/U-17, U-18 G7-31; v6/U-19 G7-01; v6/U-25 G7-34; rest stay in v6 |
| DF-01..DF-47, Never rows | 47+ | Unchanged; DF-28 linked G7-32; DF-25, DF-31 feed `08` |
| X-01..X-22 | 22 | Settled in v6 |
| Proposed W-174..W-190 | 17 | Renumber per G7-12; W-182 G7-35, W-184 G7-16, W-186 G7-04 |
| V6-01..V6-34 | 34 | Adopted as v7 base |

## 9. Deferred work register (V7-A125, V7-R13, V7-R136)

Each item stays parked until its trigger; none is dropped.

| Item | Owner | P | Depends | Ev | Accept | Reopen trigger | Next action |
|---|---|---|---|---|---|---|---|
| Switch default to `~typesafe/jev-latest` | human | P2 | G7-42..G7-44 | EV-18, EV-20 | Guard in the same release | JR D3 yes, guard merged | Stay pinned on `typesafe/jev-1.14` |
| Shadow lane `baseten/mammoth-1`, verify only | both | P2 | G7-45 | JR D6 | G7-45 accept met | D6 written yes, capped key in Open Vault | None before D6 |
| Veto on `would_allow` | human | P3 | L-01 | JR D7 | Fresh-set score | 3-4 weeks shadow (JR D7) | Do not decide now |
| Fallback model | agent | P3 | G7-46 | JR step 9 | ABSTAIN/REJECT only | exit 5 above 2% over 14 days | Exit-5 counter only |
| DEEP route model | both | P3 | `05` | EV-34 | Benchmark shows a need | `05` result | No model named |
| `jev-router` chat arm outside the gate | agent | P3 | G7-08 | EV-21 | Saved probe | G7-08 evidence | Scope in `06` |
| Adaptive routing | agent | P3 | G7-19 | EV-41 | 200 labels per group | Labels reached | [06 §11](06-model-routing-strategy.md) |
| Obsidian/Kanban sync | human | P3 | Q7-02 | EV-62 | Board shown present | Q7-02 | One-way export design |
| PHP/Vue benchmark slice | human | P3 | Q7-02 | EV-63 | Corpus shown present | Q7-02 | Shell, claim cases only |
| Optional OTel export | agent | P3 | M1 | EV-47 | Trace fields stable | M1 done | Design only |
| v6 DF and Never rows | as in v6 | P3 | v6 | v6 doc 13 | v6 rule | v6 triggers | Section 8 |
| Per-doc deferred tables (01, 03-09, 11; 04/06 backlogs) | agent | P2 | Q7-01 | each doc | Merged here with full columns | Before Q7-01 export | Merge |

## Open questions for the owner

JR decisions D1-D7 are carried to [10](10-decisions-and-questions.md); recommended defaults as in JR §8.

| Id | Question | Default (why) |
|---|---|---|
| Q7-01/[A-03](10-decisions-and-questions.md) | May G7 rows go to `jade work`, and when? | After V6-01, V6-02, fresh ids (G7-12) |
| Q7-02/[A-01](10-decisions-and-questions.md) | Do Contextum, Obsidian/Kanban or a PHP/Vue corpus exist anywhere? | Treat as absent (EV-61 to EV-63) |
| Q7-03/[H-02](10-decisions-and-questions.md) | Does the v6 owner batch still stand? | Yes, answer first (seven P0 rows wait) |
| Q7-04/[DM-11](10-decisions-and-questions.md) | Docs-only PR for G7-07, G7-48? | Yes (PR needs your yes); `jev-1.14` stays pinned |
| Q7-05/[L-04](10-decisions-and-questions.md) | One redacted chat probe each for `jev-router` via 9router and 9router Decisions refusal? | Yes (U-04) |
| Q7-06/[H-03](10-decisions-and-questions.md) | ZDR on the Decisions route before any second vendor? | Yes (JR D1, EV-33) |
