# JADE v6 master plan

This document answers one question: what should JADE v6 be, in what order should it be built, and what must the owner decide first? It gives the executive assessment, explains how the other 14 sections fit together, and ends with seven final conclusions: the architecture, the best improvements, what not to build, the critical risks, the phases, the human decisions and the readiness to start. It is a plan. Nothing in it is built, merged, approved or verified by Jev.

## 0. How the tags work in this document

- [VERIFIED] means there is concrete evidence: an audit or research id (A1 to A8, R1 to R6), a file or symbol, or a command I ran in this session (`git rev-parse`, `gh pr list`, `gh repo view`, `git worktree list`). Refs moved during the work, so every claim names its ref.
- [INFERENCE] is reasoning from verified facts. [RECOMMENDATION] is a design proposal. [UNRESOLVED] means nobody settled it.
- A sibling section (d01 to d14, see section B) is a proposal, not proof. When I repeat its claim I write "per d12" and keep its tag.
- Research claims keep their confidence. Most web facts in R1 to R6 were read through a summary model, not as raw pages. So a price, licence, version, default or product behaviour from a web page is tagged [INFERENCE, web page via summariser], never [VERIFIED]. A vendor figure such as 93%, 84% or 17% is tagged [INFERENCE: vendor figure via summary]. [VERIFIED] is kept for raw page text, code, probes and git. Other tags used in sibling drafts are written with the four tags plus a plain qualifier, for example [INFERENCE, assumption] or [INFERENCE, paper abstract only].
- Anything that changes what JADE blocks, asks, remembers, shows or sends out is a proposal and needs the owner's yes. Every such task is marked "safety change" in d12.
- Nothing here is Jev-verified. `jev verify` writes the live ledger and sends text to a provider, so I did not run it. The owner or the orchestrator should (d13 U-25, work item W-190). [UNRESOLVED]
- Words are simple on purpose. Technical names, paths and commands are exact.

## A. Executive architecture assessment

### A.1 What "v6" means (settled assumption, reversible)

[VERIFIED A1] The label "v6" does not appear in the repository. I take it to mean the 0.6.x line (tags v0.6.0 and v0.6.1, installed 0.6.1), plus `origin/main` after 0.6.1, plus the unmerged stack planned as 0.7.0. That stack is PR 42, 43, 44, 45 and 46, with PR 47 handled first on its own. PR 48 is already merged.

[VERIFIED by me this session, `git rev-parse origin/main`, `gh pr list`] `origin/main` is `e9412f3` (0.6.1 plus PR 48). Open PRs are 42 to 47. The head of PR 47 is `c676801`. `pyproject.toml` still says 0.6.1. [UNRESOLVED] Other readings (the `typesafe-ai` plugin line, the Jev question registry v2) and the owner's original spec text. I never saw that spec. "The spec says" in this set means the wording quoted by A2, A4 and the task. Q-01 asks the owner.

### A.2 The actual state of JADE

- [VERIFIED A3] The installed gate is 0.6.1 at level `off`. At `off` only SI-09 to SI-12 block. SI-07, SI-08 and every risk rule only advise. In the last 7 days, 3,416 of 3,424 gated calls ran with no host prompt. So on this Mac JADE is the only gate, and it is mostly a logger. `jade check` still prints "ok".
- [VERIFIED A6] The ledger has zero labelled rows. No threshold was ever calibrated. No question template was ever promoted. The Jev proxy gate says "67 of 50" ready, but the 100 owner answers are all allow and none is deny. An "always allow" model would agree with the owner 100% of the time.
- [VERIFIED A8, A6] Human load is small and concentrated. 2,666 gated calls in 14 days produced 100 asks and 20 terminal approvals, about 4.5% [INFERENCE A8, one machine, the maintainer who is building JADE]. R-16 and R-14 caused most of it (77 of 100 answers in A6; 65 and 35 of 100 asks in A8; different windows). The two load counters disagree (3,424 in 7 days against 2,666 in 14 days) and nobody reconciled them (d13 U-10).
- [VERIFIED A2] Jev returns only probabilities. It has no explanation field. [VERIFIED A8] Live latency is p50 712 ms and p95 1,395 ms (369 calls, 14 days) against a 1.0 s budget. The README says about 500 ms.
- [per d01, its own count of 79 rows from A2 to A8] Capability matrix: 17 IMPLEMENTED, 2 CODE-ONLY, 30 PARTIAL, 4 UNVERIFIED, 1 REDUNDANT, 25 ABSENT. Need: 26 proven, 19 unproven, 3 rejected, 31 keep. A large IMPLEMENTED count is not protection on this Mac.
- [VERIFIED A1] Real work sat outside git history at audit time: an uncommitted PR 47 fix, four untracked plan files ("spec B") in a worktree whose folder name does not match its branch, 21 new backlog rows in a dirty main checkout (236 file-mode changes), and one local-only branch. Work-item ids W-96 to W-105 and W-115, and threat row T30, each mean two things on different refs.
- [VERIFIED by me, `gh repo view`] The repository is already PUBLIC under the MIT license. So the question is not "may we publish". It is "what is exposed, and is it true".

### A.3 Strengths

1. [VERIFIED A2, A3, A4] The deterministic core is mature. It has a signed-permit approver, safety invariants (SI-01, SI-02 and SI-05 to SI-13; SI-03 and SI-04 were never assigned), human-only classes that no memory or model can answer, a crash-safe ledger and fail-closed paths. [VERIFIED R3] About 4,100 lines of shell classifier and three pure-Python runtime packages.
2. [VERIFIED d02, R2, R3] The likely differentiator is "who may say yes", plus the record and the abstention discipline. It is not shell parsing and not a state machine. No page read in R2 describes an authenticated approver. [INFERENCE d02: absence in pages read through a summariser.]
3. [VERIFIED A6, R5] The offline evaluation kit is real code (Wilson bounds, Brier, bootstrap, session splits). It is ready for real labels when they exist.
4. [INFERENCE A8, A1] The release line is healthy: eight tags, 0.3.0 to 0.6.1, all ancestors of main.

### A.4 Risks

1. **Believed safety that is not there.** The default Mac state is advisory. The task text given to audit agents said the hook fails closed on a push to a default branch. At `off` that is false (d03 CT-01).
2. **Autonomy sources on weak ground.** Memory can silence a whole kind of critical call (d03 FN-02). A chat Allow is spent on a different call (d03 FN-03, A3 probe C4). The proxy gate counts answers, not denies.
3. **The third goal has no instrument.** Decision accuracy cannot be measured. The only label is "the action succeeded", reported by the agent. That breaks the owner's own rule: execution success is not decision correctness.
4. **Holes no smarter model closes.** Parser gaps, config that loads before trust, child agents, GUI tools and other harnesses (d03 BS-01 to BS-11). A hook cannot see them. Containment can.
5. **The pile on top of a healthy line.** Six open PRs, loose work, colliding ids. One wrong cleanup can lose work.
6. **The first result of v6 is more asks, not fewer** [INFERENCE d03, d12]. Closing the floor adds questions before scopes and containment take any away.

### A.5 Strategic direction

[RECOMMENDATION] JADE v6 is a small local control plane for Claude Code on macOS. It decides who may say yes. It does not run the agent. The order is: save and clean up, close the verified holes, make the floor a test, measure, contain with native layers, and only then widen autonomy one small scope at a time, behind an audit.

[RECOMMENDATION] Challenges to the owner's spec, all reversible:

- "Fewer approvals" is not a goal alone. A yes-to-all model scores perfectly on it. Show approvals only beside a false-allow bound.
- Safety and autonomy are separate in the spec. In code one key (`level`) joins them. Prove the floor with tests. Add one key, `jev`, for the real missing step. Do not add five profiles.
- MAXIMUM, HARD, budgets, DEFER_WITH_PLAN and SAFE_ALTERNATIVE name things the code can already say or nothing can produce (d03 Part B, d05 section 3).
- "Continuous execution" is host work (`/goal`, `/loop`, Monitor, cron). JADE keeps the record and the boundary.
- "Harness-agnostic" is only intent. One harness is wired.

## B. How to read this document set

The final repository paths are `docs/14-v6-master-plan.md` for this file and `docs/14-v6/NN-*.md` for the others. [VERIFIED, `git for-each-ref` and `git ls-tree` on every ref, 2026-11-16] No ref has a `docs/14` path today, so `docs/14` is free. [RECOMMENDATION] Confirm that again just before the move.

Two naming rules, to stop wrong citations. First, "repo docs/12" means the existing repository file `docs/12-jev-decides-phase1.md`. "d12" means the draft roadmap `12-roadmap.md`. A draft is never called "docs/NN" in these files. Second, the sections were written in parallel, so some decision ids collide (H1 in d04 and d07, D-n in d03 and d07, D-B in d10). Cite `dNN/ID`. [RECOMMENDATION] Before the files go into the repo, prefix every decision id with its file number (for example `d04-H1`, `d03-D-6`), or replace it with the Q and LD ids of d13. d13 section 9 is the crosswalk for the old ids until then. The owner only sees Q and LD ids.

| File (draft name) | Owner spec deliverable | Answers |
|---|---|---|
| `00-master-plan.md` (this file) | A. Executive assessment; final conclusions | State, direction, final architecture, readiness |
| `01-inventory-and-capability-matrix.md` | B. Inventory; C. Capability matrix | Branches, PRs, worktrees, loose work, collisions; 79 capability rows with code, live and need status |
| `02-competitive-research.md` | D. Competitive research | Integrate, adopt, spike or skip, for each tool and standard; 7 spikes |
| `03-gaps-and-blind-spots.md` | E. Gaps | 13 fix-now items (FN), spec challenges (SP), hidden risks (HR), contradictions (CT), blind spots (BS), opportunities (OP), measurement gaps (MG) |
| `04-adrs.md` | F. ADRs | 14 decisions (8 full ADRs, 6 short), all "Proposed" |
| `05-security-and-autonomy-model.md` | G. Security and autonomy model | Two dimensions, the `jev` key, precedence, enforcement boundaries, fixes F1 to F13, negative tests N1 to N38 (d05 has no N2 and has an extra N6b, so 38 labels), plus N39 to N43 for retries after a Deny |
| `06-decision-intelligence.md` | H. Decision intelligence | Evidence, repeats, diagnosis, calibration versus correctness; Tier A and Tier B |
| `07-continuous-execution.md` | I. Continuous execution | The bounded loop, blockers, recovery, parallel agents; no engine |
| `08-learning-and-improvement.md` | J. Learning | Selective labels, staged exposure, never-learn list, minimum data |
| `09-human-experience.md` | K. Human experience | The question, channels, safe display, language, friction measures |
| `10-verification-and-benchmark.md` | L. Verification and benchmark | Four layers, arms, metrics, CI gates G1 to G8, claims allowed |
| `11-developer-experience-and-public-readiness.md` | Developer experience (added by the task) | Install, truth of `jade check` and docs, privacy, release and CI; ranked adoption blockers |
| `12-roadmap.md` | M. Roadmap | 34 tasks V6-01 to V6-34 in 7 phases; exit gates; minimum v6 |
| `13-deferred-and-open-questions.md` | N. Deferred work | 12 owner questions Q, 32 later decisions LD, 26 unresolved facts U, 47 deferred items DF, 22 conflicts X, 51 proposed work items |
| `14-implementation-handoff.md` | O. Handoff | Standing rules, preconditions, the step loop, ordered steps, resume protocol |

Read order for a decision maker: this file, then d12 sections 1, 2, 8 and 9, then d13 section 2. A reader does not need the other files in full. Each file starts with a short answer (its section 1, or the top paragraph); read only those, plus the sections named here. [INFERENCE] The files are long on purpose (each is above the 2,500 to 6,000 word target of the drafting task; d12 is the longest). The long parts are tables, task cards and evidence that an implementer looks up, not text a decision maker must read. Read order for an implementer: d14 first, then the d12 card of the step.

[VERIFIED by me, `wc -w`] The 14 sections are about 140,000 words in all (a recount after the second editing pass: 147,000 including this master). This master repeats none of them. It points into them.

## C. Corrections made to the sibling drafts while writing this master

The task says to fix the draft that is wrong. These edits are in the drafts folder. Nothing outside it was touched. Ref checks are mine this session.

| File | What was wrong or stale | Fix |
|---|---|---|
| d01 | Said the PR 47 round-4 fix was local only at `967b166`, PR head `c6ab27a`. The head on origin is now `c676801` [VERIFIED `git rev-parse`, `git worktree list`]. Part E item 7 (add no `jev` key) and item 4 (merge order) were overruled by d12 and d13. Repo visibility was listed as unresolved. | Correction note after the B1 table; table row marked superseded; visibility marked PUBLIC [VERIFIED `gh repo view`] |
| d03 | FN-01 and D-6 name `c6ab27a` and an uncommitted fix. | Correction note before the fix order. FN-01 is now "verify `c676801` against main" |
| d04 | Written before d12. Ten choices differ from the roadmap (`tool_use_id`, `jade quickstart`, `setup --managed`, `tree-sitter-bash`, auto-opened work items and `reconcile`, the batch rule in ADR-v6-07 item 7, the typed verdict module and sample cap of ADR-v6-06, honeypots H18, "high now" H22, the PR 47 split). | Reconciliation note before ADR-v6-01. Second pass: ADR-v6-08 seeded calls and the 5% start, ADR-v6-12 Tier 1 and Tier 2, ADR-v6-07 items 2 and 6, ADR-v6-03 points 3, 7, 8 and H1, H2 are struck or corrected in place and named in the note |
| d01, d12 | (also) B7 said SI-01 to SI-13 are all present with no gap. `policies/safety.yaml` on `origin/main` has SI-01, 02, 05 to 13; SI-03 and SI-04 were never assigned [VERIFIED by me, `git show origin/main:policies/safety.yaml`; d05 section 0 had it right]. d04, d08 and d12 wrote "SI-01 to SI-13" as a range. | d01 B7, d04 ADR-v6-03 point 3, d08 section 1 and the d12 section 8 line corrected (second pass, 2026-11-16) |
| d05 | Defaults for F2 (narrow rule), F3 (block at `off`), the `jev` default at `high`, and a 50-call auto-mode replay differ from d12 and d13. | Correction note before section 1 |
| d09 | Called all of tier 1 "Now"; the roadmap puts only safe display in the minimum. Proposal 6 (keep other questions in the batch) conflicts with d13 X-15. | Correction note in 0.1; proposal 6 marked superseded |
| d10 | "Build now" included the real-shell oracle; the roadmap makes it optional. D-B2 said PR 47 only after a clean round. | Correction note before the defer table |
| d11 | Said `T30` is claimed by PR 42 and PR 48 and that the PR 47 fix is uncommitted. PR 48 is merged and keeps `T30`. | Three lines changed; PR 47 head updated |
| d12 | Phase 2 gate G4 needs V6-22, which is outside the minimum. V6-26 batch rule was Fix A. | G4 text clarified; V6-26 now uses Fix B then Fix C |
| d13 | LD-02 said `T30` goes to PR 42. U-16 listed repo visibility as unknown. | Both corrected |
| d14 | The V6-26 step card kept non-token questions (Fix A). The step table marked V6-08 as outside the minimum, which gives 24 tasks, while d12 section 8 counts 25. PC-6 dated the 3,416 figure "2026-11-08 to 2026-11-09". | V6-26 card now says Fix B then Fix C; V6-08 marked "yes, may ship after the 0.7.0 tag"; PC-6 now says the A3 audit date and "last 7 days" [VERIFIED A3 line 43] |

Disagreements that d12 section 2 and d13 section 6 already settle (X-01 to X-22) are not repeated here. The choices in this master follow them.

[INFERENCE] One minor difference is left as is. d04 ADR-v6-03 calls profiles "presets that write `(level, jev)`". d05 section 3.4 and d12 V6-28 make them derived display labels that are never stored, and a pair command only on request. This master follows d05 and d12.

[RECOMMENDATION] d04 H18, d10 D-B9 and d08 disagreed on honeypots. d13 X-20 settles it: passive numbers only, no honeypots in v6. The owner confirms in LD-11.

## Final conclusions

Seven conclusions follow, numbered 1 to 7. "Master section 6" in the other files means conclusion 6 (the human-only decisions).

### 1. Recommended final architecture

[RECOMMENDATION, d04 ADR-v6-01, 03, 04, 12, d05, d12] JADE v6 is a local, optional control plane with five parts: a deterministic gate, a decision ledger, advisory verdicts from Jev with abstention, human approval channels, and measurement of the other four. It governs Claude Code on macOS. It does not own the agent loop, the scheduler, retries or resume. The host does.

```
Outside JADE (owner steps, host features)
  remote branch protection, pre-push guard, host sandbox, managed settings, auto mode
        |
Layer 1  THE FLOOR (code constants; no level, profile or learning step can change it)
  SI-01, 02, 05 to 13, human-only classes, never-remember and never-send-to-Jev lists
  (R-21 egress is added to the never-send-to-Jev list in V6-28, a tightening, LD-22),
  fail closed, permit binding. Tested as "no floor call runs silently".
        |
Layer 2  WHO MAY ANSWER (autonomy; goes up only by a human act, with a ledger event)
  sources, in code order: signed permit, scoped memory, Jev proxy, then chat or dialog
  (chat grant bound to plan_hash). Memory and Jev answer before chat is asked [A2].
  No "strongest authority first" order is added (d05 section 4.2).
  Set by (level, jev) and by memory scopes; each new source starts in shadow
        |
Layer 3  RECORD AND MEASURE
  ledger, replay corpus, labels, random audit of silent allows,
  readouts of asks per rule shown beside a false-allow bound
        |
Layer 4  HUMAN TEXT
  one builder, facts only, safe display, simple English
```

Key choices, each a proposal:

| Choice | Decision | Source |
|---|---|---|
| Runtime | No JADE runtime, scheduler or workflow engine. The record and the boundary only. | d04 ADR-v6-01, d07, d12 section 6 |
| Floor versus autonomy | The floor is constants: the SI invariants (SI-01, SI-02, SI-05 to SI-13), human-only classes, two "never" lists. Autonomy is `(level, jev)` plus memory scopes. An answer source never turns a block into a pass. | d04 ADR-v6-03, d05 sections 1, 4 |
| Level `off` | Shown honestly as "observe plus floor". Text first. Blocking SI-07, SI-08 and wiring writes at `off` only after a replay count, with the R-16 and R-14 project scope in the same change. | d12 V6-10, V6-16; d13 Q-05, LD-29 |
| The one new key | `jev on or off`, with the same ledger-provenance check as `level`. Labels (MANUAL, ASSISTED, BALANCED, FAST) are derived for display and never stored. No MAXIMUM, no HARD. | d05 section 3, d12 V6-28 |
| Native layers | Use the host sandbox, managed settings and auto mode under JADE, not instead of it. JADE reads and checks them. It does not write native deny rules and does not build containment. | d04 ADR-v6-04, d02 section 4.1 |
| Shell parsing | Keep `shellnorm`, rules and permits custom. Freeze the classifier: bug fixes with a test, no new rule family without a replay case. No OPA or Cedar in the hot path. | d04 ADR-v6-02, d02 section 4.3 |
| Verdict words | Keep the four exit-code verdicts. No new verdict class, no `SAFE_ALTERNATIVE` or `DEFER_WITH_PLAN`, no model-written alternative. Only small deterministic checks: DI-3a, DI-4, DI-5 in V6-24 (outside the minimum), and the DI-8 label filter in V6-23 (inside the minimum; it needs the owner yes of LD-31). d04 ADR-v6-06 proposed a typed five-name view; the roadmap does not build it. | d06, d12 sections 6 and V6-24, d13 DF-04 |
| Learning | Learning proposes. It never loosens a floor. Staged exposure of one scope, 100% review at first, then a fixed weekly audit. | d04 ADR-v6-08, d08 |
| Jev | Advice only. Never owns a floor. Stays in shadow. The deny-count gate (30 owner denies from 3 or more rules, zero false allows) is only a minimum before the owner may consider letting Jev decide. A 0-miss run on 30 denies bounds recall only at about 90.5%, and the proxy cannot be validated at this volume (about 97 denies needed, ADR-v6-13). Any move from shadow to decide is a separate human decision with its own evidence (LD-08). | d04 ADR-v6-13, d12 V6-28, V6-31 |
| Approval channels | Keep three: signed permit, chat, macOS dialog. Bind a chat grant to the exact call (`plan_hash`). | d04 ADR-v6-14 |
| Order | Safety fixes and measurement before any autonomy increase. | d04 ADR-v6-12 |

[VERIFIED d12 section 4.2] The build is 34 tasks (V6-01 to V6-34). Of these, 25 are the minimum v6 (section 5 below).

### 2. Highest-value improvements

Ranked by value over cost. All are [RECOMMENDATION]. Task ids are d12 ids.

| Rank | Improvement | Why it matters | Evidence | Tasks |
|---|---|---|---|---|
| 1 | Save the loose work. One register for W and T ids. Commit with explicit paths. | A wrong cleanup loses the only copies of the spec B plans and the open-items backlog. Cheap. | [VERIFIED A1 sections 5, 6] | V6-01, V6-02 |
| 2 | Verify PR 47 against main, then merge the train (42, 43, 44, 46, 45) | Everything else stands on one clean tree. PR 47 at `c6ab27a` blocked less than main. | [VERIFIED A1 7.2; d12 section 0, probed `c676801` on four commands] | V6-03, V6-04, V6-05 |
| 3 | Close the three verified holes: memory never covers a critical call; a chat Allow is bound to `plan_hash`; chat and dialog text is safe to read | Each hole turns a click or a probability into permission for a different call. | [VERIFIED A3 probes, A6, R6 probes 2 and 9] | V6-06, V6-07, V6-09 |
| 4 | Make `jade check` and the docs tell the truth | A governance tool that says "ok" on a mostly advisory gate loses trust faster than any missing feature. | [VERIFIED A3, A8, d11 section 15 ranks 1 to 4] | V6-10, V6-11 |
| 5 | Close the known rule holes (`curl URL \| sh # x` is low while `curl URL \| sh` is high; `gh api .../merge`; `git send-pack`; agent-config writes) | These are cheap rule fixes, each with a harmless twin test. | [VERIFIED R6 probes 5 to 8, A3 8b] | V6-08 |
| 6 | Make level `off` a real floor, and add project scope for R-16 and R-14 in the same change | This is both the safety fix and the likely biggest friction fix. The project scope is a loosening: it needs a replay that shows no should-block call allowed, and the owner yes (LD-29). Part of R-16 and R-14 load is the maintainer building JADE [INFERENCE]. | [VERIFIED A3, A6] | V6-16 |
| 7 | Build the `jev` key and the deny-count gate before leaving `off` | Without an off switch, leaving `off` starts data flow to the provider. Without a deny gate, an "always allow" model passes. | [VERIFIED A4, A6; d12 section 2 row 5] | V6-28, V6-19 |
| 8 | Build the measure: replay runner, 150 labelled rows, ledger readouts | Without it there is no accuracy number and no honest friction claim. | [VERIFIED A6] | V6-20, V6-21, V6-23 |
| 9 | Contain with the native sandbox, and check the two cheap remote boundaries (branch protection, pre-push guard) | Containment cuts risk without parsing a command. These are cheaper and stronger than the SI-08 text scan. | [VERIFIED A3 section 12] [INFERENCE, web page via summariser, R1] | V6-25 step 1, V6-12 |
| 10 | Harden memory and add one audited scope | This is where asks first fall because of learning, with a false-allow bound beside it. | [INFERENCE d08 section 7] | V6-29, V6-30 |

[INFERENCE] Where the ask cut comes from, and no other place (d12 section 8): project scope for R-16 and R-14, one scoped memory class behind a 59-run audit, and containment (which cuts risk, not prompts). On this Mac host prompts are already near zero, so "prompts per day" cannot show a gain. JADE's asks are the friction to cut.

### 3. Features that should NOT be built

Each row is [RECOMMENDATION]. Full list with triggers: d12 section 6, d13 section 5 (DF-01 to DF-47). "Trigger" is the evidence that reopens it.

| Do not build | Why | Trigger |
|---|---|---|
| A JADE runtime, scheduler, resident agent, or a Temporal, DBOS, LangGraph, Restate or Inngest core | Each needs the agent loop inside its code. `/goal`, `/loop`, Monitor and cron exist. [R2, via summary] | A second host with no resume, or unattended runs that outlive the process and cloud routines do not run JADE hooks |
| Stored autonomy profiles, MAXIMUM, HARD, an `approvals` key, a Jev bar override, per-project levels | MAXIMUM has no safe meaning. Labels derived from `(level, jev)` are enough. | The owner states what two adjacent names differ by, with evidence |
| Scoped autonomy budgets | No measured need. Options a to c come first (sandbox, scopes, per-task worktree). | Asks per week at `low` or `medium` with the sandbox on are still too many |
| `DEFER_WITH_PLAN`, `SAFE_ALTERNATIVE` as states or verdicts; wiring `WAITING_DEPENDENCY` or `RETRY_SCHEDULED` | Nothing consumes them. A model-written alternative is a channel the design forbids. | A metric uses the view |
| Model-written human text, runtime translation, a Persian catalogue, a `jade/text/` package | A new channel for an agent to steer the human | The owner says yes, and a native reviewer exists after a render probe |
| Batched asks, planted fake questions (honeypots), per-person timing dashboards | `askchat.rewrite` conflicts with batching. Deception needs the owner's approval. | A measured complaint; rubber-stamping shown by passive numbers |
| A policy engine (OPA, Cedar, Invariant, AGT) in the hot path; a second allow-list engine; native deny rules written by JADE | The parser is the hard part. Cedar skips a policy that errors. [R3] | Several machines need one policy; rules grow about 10 times |
| A second-harness adapter, an MCP server, proxy or gateway, A2A, a JADE mod, an Elicitation hook, MCP annotations as trust | One harness is wired. An Elicitation hook can answer for the user. [R4] | The owner names a second harness with a blocking pre-tool hook |
| OTLP export, `opentelemetry-sdk`, a `tool_use_id` column, `jade quickstart`, PyPI, a package rename (W-28) | No collector, no consumer, name risk; W-28 is a safety change | A named consumer; a public goal and the release gates passing |
| Platt, isotonic, conformal or importance-weighted estimators; threshold loosening; auto-promotion; embedding memory | No labels to fit. Probably not reachable within a year at this labelling capacity. [d08 section 7] | At least 500 clean labels with a protected split |
| More shell-parser waves; `tree-sitter-bash`; waves 4 to 6 of the shell start-up guard | Four review rounds on PR 47 were still whack-a-mole [VERIFIED A1; d03 HR-07]. No stop-loss number yet. | The real-shell oracle (V6-22) shows a family a rule can close |
| A promise that JADE stops an agent from splitting, scripting or rewording a denied operation | A hook sees a call, not intent. The rule is text for the agent and the flow skill (d05 section 4.1, d07 8.2, d14 R23). JADE records and measures it only (benchmark metric 4). Known gap T18 on `origin/main` | A probe (d13 U-07) shows the host gives a signal that links retries to a Deny |
| End-to-end container benchmark, a scripted approver, a general "JADE accuracy" number, a leaderboard | Each needs a named question; one number hides the confusion matrix | L2 shows an asks change the owner wants confirmed |
| A recovery engine, retry counter, blocker table, PreCompact or SessionEnd hooks | `/goal` and Monitor cover it; no incident | One recorded incident the host does not cover |

[INFERENCE] The pattern in the owner's spec: it adds names for things the code can already say, or for things nothing can produce. Add a name only when a number, a test or a hook uses it (d03 Part B).

### 4. Critical risks

| # | Risk | Evidence and ref | What the plan does | Task |
|---|---|---|---|---|
| 1 | The owner believes in safety that is not there. Level `off` is advisory and `jade check` says "ok". | [VERIFIED A3: 3,416 of 3,424 calls; only SI-09 to SI-12 block] | True text now. Floor at `off` after a replay count. | V6-10, V6-16 |
| 2 | Loose work and ref confusion cause loss or a wrong merge. | [VERIFIED A1 sections 5, 6]. Refs moved during research, for example the PR 47 head. | Save work first. One id register. Every claim names a sha. | V6-01, V6-02 |
| 3 | Memory, the chat grant or the Jev proxy turn a past click or a probability into permission for another call. | [VERIFIED A3, A6]. Wakes when the owner sets `low` or `medium` and confirms the 82 waiting hints (d03 FN-02). | Fix before any wider autonomy. Do not set `low` or `medium` until done. | V6-06, V6-07, V6-28 |
| 4 | Decision accuracy cannot be measured, and a wrong label would train a loosening. | [VERIFIED A6: zero labelled rows; on a synthetic 300-row set `propose_threshold` proposed 0.70 to 0.50] | Labels independent of any arm. Never loosen a replay, protected or canary rule in this cycle. | V6-20, V6-21, V6-23 |
| 5 | Parser gaps, config that loads before trust, child agents, GUI tools, other harnesses. | [VERIFIED A3, R6]. Claude Code had at least 8 published parser-bypass advisories [INFERENCE R6, counted from titles]. | Containment, remote protection, a stop-loss for parser work. State "Claude Code on macOS only" in docs. | V6-25, V6-22, V6-33 |
| 6 | The human is the target: clipped text, bidi characters, fatigue. | [VERIFIED R6 probes 2 and 9 on `open-items-scopes`; A6: 100 of 100 answers allow; 18 terminal approvals in one afternoon then 18 asks expired, per d03 HR-03 and repo docs/12 section 1] | Safe display first. One question builder. Passive attention numbers. | V6-09, V6-26, V6-23 |
| 7 | Data leaves the machine. Command text and preferences go to the provider at `low` and `medium`, and in shadow at `high`. Retention purge skips command text in `asks` and in `human_answers.label`. | [VERIFIED docs/12 line 177 per d12; A8 section 9] | `jev` key. Purge scrubs label text (owner decides). Privacy page. | V6-28, V6-11, V6-33 |
| 8 | One maintainer and agent-only review. PR 47 rounds 2 to 4 kept finding P0 regressions. | [VERIFIED A1; d03 HR-06] | Independent review lenses. The owner re-runs every gate. Never-looser check against main. | V6-03, V6-04 |
| 9 | Release rules live in prose. The release workflow does not check that a tag is on main or that CI was green. The installed gate lags main. | [VERIFIED A8 section 8; `release.yml` per d12] | Do those checks by hand for 0.7.0. Automate later. | V6-14, V6-34 |
| 10 | The plan rests on unrun probes (P1 to P6) and summary-read research. | [UNRESOLVED R1: whether `--bare` or `--safe-mode` skip managed hooks; whether auto mode exists through this gateway] | One owner probe session, early. Weak claims keep their tag. | V6-15 |
| 11 | The plan raises asks first and may be abandoned halfway. | [INFERENCE d03, d12] | Every task that adds asks shows the extra asks per day from `evals/gate_replay.py` first. Stop states per phase. | all |
| 12 | The plan itself is unverified. Sizes are estimates. No cost per run is known. | [UNRESOLVED d13 U-25] | Run `jev verify` on the final plan. Treat sizes as estimates. | W-190 |

Evidence caution (do not quote as verified): the "about 93% of prompts are approved", "84% fewer prompts with a sandbox" and "17% false negatives on 52 real overeager actions" figures are Anthropic's. In every draft they carry one label: [INFERENCE: vendor figure via summary]. The 84% is internal use with no method. R1 and R6 read Anthropic pages for it; R5 saw only a search snippet. The 93% was left out by R6 as secondary only. The 17% interval (about 9% to 30%) is Wilson arithmetic, [INFERENCE]. None of these is a Jev number. No Jev miss rate exists [VERIFIED R5: README says VALIDATED: No].

### 5. Prioritized implementation phases

[VERIFIED d12 section 4.2, tally by me] 34 tasks: 10 size S, 16 size M, 8 size L. Sizes are my estimates [INFERENCE]. The minimum v6 is 25 tasks, 14 of them all of Phase 0.

| Phase | Tasks | Purpose | Gate and claim allowed after (d12 section 7) |
|---|---|---|---|
| 0 Clean-up and known holes | V6-01 to V6-14 (14) | Save loose work; one id register; verify PR 47 and merge the train; memory, chat grant, safe display; rule holes; true `jade check` and docs; branch clean-up; release 0.7.0 | G1, G2, G5, G7, G8 on approved behaviour, active tests only; N7 to N11, N13, N14 red before and green after; never-looser check against 0.6.1 empty; unique ids. Claim: "These listed vectors are closed." Not "JADE is safe". |
| 1 Safety floor | V6-15, 16, 17, 19, 28 (V6-18 optional) | Host probe session; level `off` becomes observe plus floor; floor tests and the never-learn test; the `jev` key and deny-count gate; leave `off` for `high` | Same gates plus the N-tests for level and key (N1, N5, N6, N6b, N18 to N20, N28 to N33). Claim: the listed vectors are closed at levels `off`, unset, `low`, `medium`, `high`. |
| 2 Measure | V6-20, 21, 23 (V6-22, V6-24 optional) | Replay runner; 150 labelled gray-zone rows, kappa at least 0.70; ledger readouts and label plumbing | L2 stratum C: zero should-block calls allowed; candidate blocks everything main blocks. If the owner cannot label 150 rows: "no accuracy number". Claim: "0.6.1 and baseline numbers, with intervals, on this corpus." |
| 3 Contain and cut asks | V6-25 step 1 (V6-26, 27 optional) | Sandbox pilot after a dry run | No safety claim. Counts only: prompts per day, sandbox-blocked failures, escapes found, before and after. |
| 4 Audit and staged autonomy | V6-29, 30 | Memory hardening and audit of silent allows; first scope at stage 1 | 59 or more audited clean silent runs from 10 or more sessions, 0 wrong (upper bound 4.95%). One audited wrong run in a costlier class suspends the scope. |
| 5 Only on evidence | V6-31, 32 (both optional) | Jev proxy keep or retire; report-only learning | Proxy: at least 100 shadow samples over 7 days, at least 30 owner denies from at least 3 rules, 0 false allows. Else it stays in shadow or is retired. |
| 6 Public readiness (parallel) | V6-33, 34 (both optional) | True front page, privacy page, release and CI hardening | One acceptance trial with a non-maintainer on clean macOS and Linux |

Critical path [VERIFIED d12 section 4.1]: V6-01, 03, 04, 05, 06, 14, 16, 17, 28, 19, then 29 (which also waits for 21 and 23), then 30. V6-15 (the host probe session) is the longest-lead owner step and has no dependency: start it first. Phases 2 and 3 run in parallel after V6-14 and V6-15. Phase 6 can start after V6-11.

[INFERENCE] 0.7.0 timing (d12 section 2 row 1): merge the train in Phase 0, but tag only after V6-06, V6-07 and V6-09 are merged, because the train adds memory reach (scopes) and a tag is a public statement. V6-08 is in the minimum but may follow as 0.7.1. [UNRESOLVED] This leaves the `| sh # x` hole open for a while. d13 calls it the most urgent safety item that needs no design. The owner may choose to put the comment fix (W-186) ahead of the tag (Q-10, LD-01).

What v6 gives and does not give (d12 section 8): it gives no lost work, a true floor, a deny-based gate for Jev, honest counts, one audited scope and containment. It does not give Jev decisions (the proxy stays shadow or off), stored profiles, an end-to-end benchmark, an accuracy claim for Jev, public release work or a managed-settings install.

### 6. Remaining human-only decisions (batched)

[RECOMMENDATION, d13 section 2] One batch of 12 questions goes to the owner first. This is the first, blocking batch only. It is not the last owner decision. 32 later decisions (LD-01 to LD-32) come in later waves, each when its replay count, probe or build is ready. Several of them change safety behaviour inside the minimum v6 (the second table below). They need an owner yes too. The 16-row lists in d12 section 9 and d14 section 10 map onto the batch and onto those later decisions (see the mapping paragraph below). A yes approves only what the question lists. Every question also accepts "yes, except <part>". Q-03 to Q-12 change what JADE blocks, asks, stores or shows. Nothing is built until the owner says yes. Q-01, Q-02 and Q-09 unblock Phase 0.

| Q | Decision | Recommended default | Maps to |
|---|---|---|---|
| Q-01 | What does v6 cover, and for whom? | 0.6.x plus main plus the 0.7.0 stack, Claude Code on macOS, the owner first. Paste the spec if this is wrong. | d12 row 1, row 15; d14 decisions 1, 15 |
| Q-02 | How does PR 47 ship? | Merge it first, after one clean independent review round, ship inside 0.7.0, no 0.6.2. Never merge `c6ab27a`. | d12 row 3 |
| Q-03 | Should memory stop silencing critical calls? | Yes. Critical, R-13 and R-18 are never remembered. Target-selecting env prefixes and the cwd join the memory key. R-12 and R-15 exact-only (LD-06, which also adds R-23). | d12 row 6 |
| Q-04 | How is a chat Allow tied to a call? | `plan_hash` (tool, text, cwd). Fall back to a narrow rule only if the replay count of extra asks is too high. The pinned test `tests/acceptance/test_chat_ask_w100b.py:241` changes. | d12 row 5 |
| Q-05 | What does level `off` mean? | Text now, in `jade check`. Decide blocking after a replay count (LD-29). | d12 row 7 |
| Q-06 | Should an agent be unable to set an old session id? | Yes. Add `JADE_SESSION_ID` to the SI-11 list. | d12 row 20; d14 section 10 row 20 |
| Q-07 | Build the `jev on or off` key? | Yes. Absent key at `low` or `medium`: on. Other levels: off. New installs write `off`. Invalid value reads `off`. Last ledger event wins. | d12 row 9 |
| Q-08 | When may Jev decide for you? | A deny gate: at least 100 shadow samples over 7 days and at least 30 owner denies from 3 or more rules, zero false allows. Freeze at shadow. Retire if about 97 denies show misses (LD-08). | d12 row 10 |
| Q-09 | May the merge train go onto main? | Yes, in order 42, 43, 44, 46, 45, with the Jev question v2 of PR 45 kept out of the champion slot until the proxy decision. No tag. | d12 row 17; d14 section 10 row 17 |
| Q-10 | May JADE close the known rule holes? | Yes, as one batch with one independent review, after a `gate_replay` count of extra asks for each rule. | d12 row 19; d14 section 10 row 19 |
| Q-11 | May purge scrub stored command text? | Yes. Scrub text in `asks.question_json`, `asks.label` and `human_answers.label`. Keep hashes and the decision. | d12 row 12 |
| Q-12 | May chat and dialog text be made safe to read? | Yes. Escape bidi, control and format characters in commands and paths. Show the gated part in full when clipped. Keep Persian prose (ZWNJ). | d12 row 18; d14 section 10 row 18 |

32 later decisions (LD-01 to LD-32) wait for a replay count, a probe, a build or data. Each has a default. None is approved. Each is a human-only decision. A yes to one approves only the row it names. The ones that gate a phase or change safety behaviour in the minimum v6 are below (this is the second block; d13 section 3 has all 32):

| LD | Decision | Default | Waits for |
|---|---|---|---|
| LD-01 | 0.7.0 contents and tag time | Tag after V6-06, 07, 09; V6-08 may be 0.7.1 | Q-03, Q-04, Q-12 merged |
| LD-02 | Ids | Note and a duplicate-id test now. Renumber the dirty rows from W-117 after PR 47 merges. `T30` stays with the harness row on main (PR 48); PR 42 uses `T31`. | V6-04 |
| LD-07 | Leave `off`, to which level | `high`, only after V6-28 and V6-17. Warn only for an existing `low` or `medium`. | V6-28 |
| LD-09 | Autonomy numbers | First scope: 59 or more audited clean runs from 10 or more sessions; 149 for a riskier scope; no 1% claim before 299 | V6-29 |
| LD-10 | Labelling hours, second rater | The owner states real weekly hours (the code uses 10 a week, a constant, not measured). A different reader of the written rubric if there is no second person. | V6-21 |
| LD-12, LD-27 | Managed settings; OS sandbox | Sandbox pilot for one week after a throwaway-HOME dry run; managed file later, as its own step | Probes P1 to P6 |
| LD-29 | Block SI-07, SI-08 and wiring writes at `off` | Yes, with the R-16 and R-14 project scope in the same change. A narrowing is a loosening: the replay must show no should-block call allowed. | Q-05, U-09 |
| LD-22 | R-21 (egress) out of Jev at every level, plus `--no-cache` for humans and tests only | Yes. R-21 is added to the never-send-to-Jev list with a test (task V6-28). A tightening. No "verified" word without an independent check. | Q-07, V6-28 (R-21 build); V6-24 for the rest |
| LD-24 | Learning rules | Never-learn list; no auto-promotion; a pause only returns to the last human-approved setting, never below it | V6-17, V6-29 |
| LD-26 | Launch of another agent, and hook-skipping flags | Make them a human-only class (never remembered, never sent to Jev). A tightening. | U-01 |
| LD-30 | V6-18 (c) MCP file-reader path check; (d) fail closed on malformed events | Yes after the probe. Both add blocks. | U-05 |
| LD-31 | Decision hygiene: label filter on `goal_check` (DI-8, in V6-23); DI-3a, DI-4, DI-5 in V6-24 | One check at a time; each stricter or record-only | V6-23, Q-07 |
| LD-32 | Promotion gates | A minimum canary sample size and intervals in the protected stage. Changes safety behaviour. | V6-23 |

Mapping of the first 16 rows of d12 section 9 (same order and numbers in d14 section 10; rows 17 to 20 were added later and are the batch questions Q-09, Q-12, Q-10, Q-06) to this batch: rows 1, 3, 5, 6, 7, 9, 10, 12 and 15 are Q-01, Q-02, Q-04, Q-03, Q-05, Q-07, Q-08, Q-11 and Q-01 (option C). Rows 2, 4, 8, 11, 13, 14 and 16 are the later decisions LD-01, LD-02, LD-07, LD-27 and LD-12, LD-10 and LD-11, LD-21 and DF-19 (the owner signs the known-gap list, each row has a date and a work item), and LD-28 (drop MAXIMUM, HARD, DEFER_WITH_PLAN and SAFE_ALTERNATIVE as states). Q-06, Q-09, Q-10 and Q-12 are new in d13. Rows 17 to 20 of d12 section 9 and of d14 section 10 now carry them (Q-09 before step 6, Q-12 before step 9, Q-10 before step 14, Q-06 before step 20).

**Only the owner does these actions** [VERIFIED d14 section 10; SI-09 to SI-12 stop an agent]: merge a pull request; create a tag or release; `jade permit approve`; `jade level`, `jade jev`, `jade gate`, `jade hooks`, `jade upgrade`, `jade setup`, `jade review confirm`; anything with sudo (`scripts/install-approver.sh`, managed settings); GitHub settings (branch protection, private vulnerability reporting); branch deletes; runs the agent cannot launch because they set `JADE_*` variables (`evals/run_cases.py`, `evals/gate_replay.py`, the real-shell oracle); the second rater's labels; any step that sends private ledger text out.

The most urgent owner-only work is not a design question. It is one 1 to 2 hour probe session (V6-15, unresolved facts U-01 to U-08): do `claude --bare` and `--safe-mode` skip hooks, is the mods guard loaded, does git over SSH work in the sandbox, is auto mode available through this gateway, does a settings `ask` rule reach the owner, how are bidi and Persian drawn. It unblocks V6-18, V6-25, V6-26 and V6-31.

### 7. Readiness assessment for implementation

**Answer: Yes, with conditions, for Phase 0 and the read-only work. No, for any task marked "safety change", until the owner has answered the matching question.**

What is ready:

- [INFERENCE] The plan is complete as a document set: 14 sections plus this master, 34 task cards with reason, size, risk, done-when, tests, migration, rollback and reuse. d14 gives standing rules R1 to R22, nine preconditions PC-1 to PC-9, a repeatable step loop, an ordered sequence, a resume protocol and global stop conditions.
- [per d13 section 8, from the d12 safety flags] Every safety-change task has an approval route (a question Q or a later decision LD). No task is without one.
- [VERIFIED by me, `git rev-parse`, `gh pr list`] The base is `origin/main` `e9412f3`. PR heads as I read them: PR 42 `8866872`, PR 43 `3b5a456`, PR 44 `ed0d26d`, PR 45 `8c8163e`, PR 46 `a18bf71`, PR 47 `c676801`. Refs may move again: d14 section 8 tells a new session to re-read them and name each sha.
- [VERIFIED A1] CI shows `test (3.13)` and `test (3.14)` SUCCESS on the PR 42 to 47 heads at the time A1 and d12 looked. [UNRESOLVED] CI on `e9412f3` itself was not read by me (d14 PC-2; tracked as d13 U-15).

What blocks:

1. Owner answers to Q-01, Q-02 and Q-09. Without them Phase 0 has no scope, no PR 47 route and no merge order.
2. Handoff preconditions PC-1 (the owner approved this plan or said "proceed with defaults") and PC-9 (the owner agrees to run, or CI runs, `evals/run_cases.py`, `evals/never_looser.py`, `evals/gate_replay.py` and the real-shell audit, because an agent cannot launch them).
3. The open PR 47 check (d13 U-19): is `c676801` weaker than main on any shape beyond the four commands d12 probed, and does the local-only `967b166` hold anything it lacks? Do not delete that sha until V6-04 has looked.
4. Written yes for each safety change before its build (Q-03, Q-04, Q-05 text, Q-10, Q-11, Q-12 for Phase 0; Q-06, Q-07, Q-08 for Phase 1). A yes to one question approves only what it lists.
5. The host probe session (V6-15). V6-18, V6-25, V6-26 and V6-31 wait for it.
6. The owner's real weekly labelling hours and deny rate. They set the time of V6-21, V6-29, V6-30 and V6-31. If the owner cannot label 150 rows the result is "no accuracy number" and the L1 gates are the only claim.
7. The loose work must be saved first (V6-01): the four untracked spec B files, the dirty backlog, `docs-approvals-wording` and the parked waves 4 to 6 branch (`open-items-shell-startup-waves4-6`, on origin and as a local branch at `7507c10`; never delete it). The main checkout at `/Users/example/Documents/projects/jade` has 236 mode changes: never `git add -A` there. Build in fresh worktrees from `origin/main`.

What this plan did not do [VERIFIED d12 section 10, d13 section 0, d14 section 11]: the planning sections ran no full test suite, no benchmark, no live Jev call and no ledger read. The audits ran selected test files and probes on archive copies (for example A1 section 7.1: 4,443, 207, 150 and 100 tests passed in parts of the suite). I myself ran only read-only `git` and `gh` commands for this master. Everything about real traffic comes from the audits A1 to A8, on one machine and one owner. Effort sizes are estimates. Cost per run is unmeasured. Several fact lists are unresolved (section 4 of d13, U-01 to U-26).

Stop conditions that should pause the build (d14 section 9): a red baseline gate, a JADE gate hold, a replay count above the owner's bar, `jev verify` non-zero three times in a row, any secret or private ledger text in a file to commit, or a PR list that moved in a way that changes the order.

## Closing principle

JADE must become more useful through verified intelligence and reliable autonomy, not through more automation, more approvals or more complexity.
