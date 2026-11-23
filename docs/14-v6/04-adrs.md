# Architecture decision records for JADE v6

This section answers one question: which big design choices should JADE v6 make, and which should it refuse to make? Eight choices are full ADRs (architecture decision records) with context, decision, rejected options, consequences, migration and rollback. Six smaller choices are short blocks. Every ADR has status Proposed. Nothing here is accepted. Anything that changes safety behaviour is marked and needs the human (the owner).

**Ref for threat-row ids.** [VERIFIED, `git show origin/main:docs/05-threat-model.md`] A `T` id in this file (T14, T18 to T29) is a row of docs/05 on `origin/main` (e9412f3) unless another ref is named. `T30` is the real-shell harness row on `origin/main`. `T31` and `T32` exist only on unmerged branches (PR 42 and PR 46); the same number means a different row on different refs (d01 B7).

## 0. How to read this section

Tags in the text: [VERIFIED] has concrete evidence (a file, a symbol, a probe, or an evidence id such as A3 or R1 with a section). [INFERENCE] is my reasoning. [RECOMMENDATION] is advice. [UNRESOLVED] could not be settled. Web facts keep the confidence of their source: a doc, price, licence or version read through a summary is tagged [INFERENCE, web page via summariser], not [VERIFIED]. The research files read web pages through a small summary model. So a doc fact is "docs via summary" unless R1 to R4 say they read raw text. "Not run" means nobody ran it.

Assumptions (all reversible):
- [INFERENCE] "v6" has no label in the repo. It means the 0.6.x line (tags v0.6.0, v0.6.1, installed 0.6.1), plus main after 0.6.1, plus the unmerged 0.7.0 candidate stack (PR 42 to 46) [A1, A2 section 0, A3 section 0].
- [VERIFIED] "main" in this text is origin/main at e9412f3 (0.6.1 plus PR 48, the SI-13 sandbox guard) [A3 section 0].
- [SUPERSEDED by the owner question Q-02 and d13 X-19] [INFERENCE] I treated PR 47 (`claude/heuristic-hamilton-0eae1d`) separately from that stack. I split it out because its committed head c6ab27a was weaker than main on SI-08, and the real fix was uncommitted in its worktree and not reviewed [A1 section 4, A3 section 11]. The current plan: PR 47 is merged first after one clean review round and ships inside 0.7.0, with no 0.6.2 (Q-02). Its head is now `c676801` (d13 X-19). Never merge `c6ab27a`.
- [VERIFIED] The ADR ids here are `ADR-v6-NN`. The repo already has ADR-01 to ADR-13 in repo `docs/01-adrs.md` [R2 section 1]. If accepted, renumber after ADR-13.
- [VERIFIED] Work-item ids collide across refs [A1 section 5, A3 section 10]. The dirty main checkout (WT) holds uncommitted rows W-96 to W-116. Most do not exist on main. On main, W-102 is the Jev-in-the-hook feature (commits f8c069a, fb8afcd) and W-115 is the SI-13 Linux sandbox item. So I write "WT W-nn" for an uncommitted row and "main W-nn" for a committed one, and I give the title. [RECOMMENDATION] Renumber the WT rows (A1 recommendation) before any ADR cites them as final.

Rules I used to decide: (1) the smallest design that meets the three goals; (2) fewer approvals are not better unless correctness and safety hold, a confidence score is not a calibrated probability, and execution success is not decision correctness; (3) a model answer may add a question and may never remove a floor; (4) integrate a proven tool before rebuilding, and add no abstraction without a shown need.

### 0.1 Facts that shape every ADR

- [VERIFIED] JADE enforces only through Claude Code hooks. No adapter for another harness exists [A3 section 1, A8 section 3].
- [VERIFIED] On this Mac the level is `off` and the `maintain` job is not loaded. In the last 7 days, 3,416 of 3,424 gated calls ran with no host prompt, so the host runs `bypassPermissions` or `auto` [A3 section 0 item 43, `jade check`]. At level `off`, SI-07 and SI-08 (push to a default branch) and every risk rule are advisory, and human-only classes are not even raised. Only SI-09 to SI-12 block [A4 sections 3, 5; A3 section 5].
- [VERIFIED] The live ledger has zero labelled rows. No threshold was ever calibrated [A6 section 1]. Jev was judged against fixtures only [A2 section 2].
- [VERIFIED] Two counters give two loads. `jade check` shows 3,424 gated calls in 7 days [A3]. `jade ledger stats` shows 2,666 gated calls in 14 days over 8 active days [A8 section 11]. The 7-day count is larger than the 14-day count. [UNRESOLVED] The counters measure different things and I did not reconcile them. I do not build a decision on either number.
- [INFERENCE] The human saw about 4.5% of gated calls (100 asks and 20 terminal approvals out of 2,666) on one maintainer's machine [A8 line 158]. It is one data point, and it is not at the intended level. It does not show that friction is small.
- [VERIFIED] Shell parsing is where the bugs are. About 4,100 lines parse shell. On main, A3 section 8b lists gaps in the SI-08 text scan. The WT rows list more, and PR 47 at c6ab27a was weaker than main on several forms (A3 section 11, uncommitted WT rows; the head is now `c676801`, d13 X-19) [R3 section 2].

## 1. Summary

| ADR | Decision in one line | Changes safety behaviour? | Needs human? | Effort [INFERENCE] |
|---|---|---|---|---|
| v6-01 | Control plane over harnesses. No durable runtime. | No | Confirm | none |
| v6-02 | Keep shellnorm, rules, permits custom. Add a test-only tree-sitter check. (short) | No | No | S |
| v6-03 | Safety floor is constant. Autonomy is `(level, jev)`. Only tightening profiles first. | Yes (level off, R-21, jev default) | Yes | M |
| v6-04 | Use sandbox, managed settings and auto mode as layers. JADE reads and checks them. | No (opt-in owner steps) | Yes (sudo) | M |
| v6-05 | No second harness adapter now. Make hook-skipping launches human-only. (short) | Yes (new class) | Yes | S |
| v6-06 | Five verdict names. Deterministic diagnostics. Stop sample shopping. | Tightening only | Yes | M |
| v6-07 | Fix durability gaps. No workflow engine. (short) | Yes (SI-07 session id) | Yes | M |
| v6-08 | Measure first. Learning proposes. It never loosens. | No | Yes | L |
| v6-09 | Show text safely first. Persian only if the owner asks. (short) | No | Yes | S |
| v6-10 | Store `tool_use_id`, add `--json`. OTLP export only on demand. (short) | No | No | S, then M |
| v6-11 | Keep git-tag install. PyPI only after release gates. (short) | No | Yes | M |
| v6-12 | Safety fixes and measurement come before any autonomy increase. | No | Yes | S to L |
| v6-13 | Jev is advice. It never owns a floor. Stop sample shopping. | Tightening only | Yes | S |
| v6-14 | Keep three approval channels. Bind chat grants to the exact call. | Yes (grant scope) | Yes | M |

**Order of the blocks.** The summary above is in number order. Full ADRs (Context, Decision, Alternatives, Consequences, Migration and rollback): 01, 03, 04, 06, 08, 12, 13, 14. They come first. Short blocks are in section 2: 02, 05, 07, 09, 10, 11. A short block has its facts as bullets (the context), a Decision, a Rejected line, a Migration line and a Rollback line.

**Reconciliation with the roadmap (master plan check, 2026-11-16).** This section was written before d12. Where d12 section 2 and d13 section 6 chose differently, they win, and these ADR parts are not in the minimum v6: ADR-v6-10 `tool_use_id` column (deferred, DF-12); ADR-v6-11 `jade quickstart` (not built); ADR-v6-04 `jade setup --managed` (manual file first); ADR-v6-02 `tree-sitter-bash` (deferred, DF-09); ADR-v6-07 item 3 auto-opened work items, item 4 `jade decide reconcile` (deferred, DF-24, X-22) and item 7 (`askchat.rewrite` keeps the non-token questions: rejected, Fix B now and Fix C next release, d13 X-15); ADR-v6-06 Part 1 (a typed five-name verdict module and a static `alternative` text field: not built, d12 section 6, d13 DF-04) and Part 2 (cap of 2 fresh samples per hour: this is DI-3b, deferred; V6-24 keeps only DI-3a, DI-4, DI-5; the DI-8 label filter is in V6-23, LD-31); H20 (R-21 never sent to Jev) is built in V6-28 with a test (LD-22); H18 honeypots (passive numbers only, X-20) and, in the same way, the ADR-v6-08 lines for a seeded audit at 5% and for seeded bad calls, and the ADR-v6-12 Tier 2 "seeded-bad-call check" (struck in place above; the pass rule is 59 audited clean runs from 10 or more sessions plus the owner's sign-off); the ADR-v6-12 Tier 1 `tool_use_id` text (X-07); ADR-v6-07 items 2 (`work sync` at SessionStart) and 6 (a failure counter; DF-17, DF-45); ADR-v6-03 points 7 and 8 and H1, H2 (stored presets and a flat block at `off`: the roadmap uses derived display labels, text now and a block after a replay count with project scope, Q-05, LD-29); ADR-v6-03 point 3, which now carries R-13 and R-18 in the never-remembered list as Q-03 does; H22 "move to `high` now" (after the `jev` key, V6-28, X-17); H15 and the PR 47 split (merge PR 47 first, ship in 0.7.0, no 0.6.2), including the older ADR-v6-12 and section 0 lines on PR 47 (struck or marked above). The PR 47 head is now `c676801`, not `c6ab27a` (d13 X-19). An ADR body above that is not named here still stands as written. The ADR ids are still `ADR-v6-NN` and would be renumbered after ADR-13 of repo `docs/01-adrs.md`.

## ADR-v6-01 Control plane over existing harnesses, not a durable runtime

Status: Proposed. Evidence: A2, A3, A5, R1, R2, A8.

### Context

- [VERIFIED] JADE has a decision state machine and a SQLite ledger. Each step is one transaction, idempotent on `idempotency_key` [R2 section 1, A5 section 4]. It does not run code, replay code, or wake the agent. The agent loop lives in the harness. The docs say "continuous autonomy" means only that state survives and the next session resumes [A5 section 4, repo `docs/07`].
- [INFERENCE, web page via summariser; not run] Claude Code has `/goal`, `/loop`, cron, Monitor, cloud and Desktop scheduled tasks, and PreToolUse `defer` for `claude -p` [A5 section 6, R1 sections 3.1, 3.11]. [UNRESOLVED] No probe ran `defer`, checked `/goal` restore on resume, or checked the session id on `--resume`. Cloud routines running JADE hooks are UNVERIFIED.
- [INFERENCE, web page via summariser] Temporal, LangGraph, DBOS, OpenAI Agents SDK, Microsoft Agent Framework and CrewAI are MIT. Restate is BUSL-1.1 and Inngest is SSPL (LICENSE files read) [R2 section 4]. [INFERENCE] Each needs the agent loop inside its workflow code [R2 section 2]. JADE governs an agent it did not write. Temporal needs a server and a database, or a paid cloud [R2 section 3.3].
- [INFERENCE] The word "control plane" is bigger than what exists. One harness is covered, and the level is `off` here [A3 sections 1, 2].

### Decision [RECOMMENDATION]

Choose option A. In v6, JADE is a local control plane with five parts:
1. A deterministic gate (hooks, rules, permits, invariants).
2. A decision ledger and state record.
3. Advisory verdicts from Jev, with abstention.
4. Human approval channels.
5. Measurement of the four parts above.

JADE does not own the agent loop, the scheduler, retries of the agent's work, or resume of the agent. The harness owns those. JADE makes them safe and recorded.

| Option | Complexity | Reliability | Value |
|---|---|---|---|
| A: control plane over harness | Low. One package, SQLite, hooks. | Depends on harness features. The gate is fail-closed with `onFailure: block` only on Claude Code 2.1.295 or newer. An older or unreadable host is fail-open [A8 section 2, INFERENCE]. | Approver identity, invariants, abstention, outcome ledger. No runtime has these [R2 section 5]. |
| B: plane plus durable runtime | High. Server or database, worker, determinism rules. | Better replay of long jobs. New crash paths inside a 6 s hook budget. | Adds durability the harness already gives for hook-based work. |
| C: shrink JADE to verify and ledger, and let native auto mode and sandbox do the gating | Lowest. | Auto mode misses 17% of real overeager actions (Anthropic) [INFERENCE: vendor figure via summary] [R1 section 3.2]. | Loses human-only classes, approver identity and the gate ledger. See ADR-v6-04: native layers are used under JADE, not instead of it. |

Revisit option B only if one trigger is true:
- (T1) A second host joins. The old ADR-01 says this needs a new ADR. Then evaluate DBOS (MIT, Postgres) or Temporal [R2 section 7].
- (T2) The owner needs unattended runs that survive process death, and native cloud routines do not run JADE hooks [R1 section 3.11, UNVERIFIED].
- (T3) A second harness has no resume.

### Alternatives and why rejected

- B1 (JADE core on Temporal, DBOS or Restate): wrong layer. It needs the agent loop inside the engine. The need is durability, not throughput. Licence fit for Restate and Inngest depends on bundling versus calling a service [UNRESOLVED, R2 section 5 item 5, pending legal review]. It is not the reason for rejection.
- B2 (thin recipes for LangGraph, OpenAI SDK, Temporal, MAF): no evidence that any user needs them [R2 section 7]. Backlog trigger only.
- B3 (own agent runtime): different product. `/goal`, Monitor and cron exist.
- C (shrink JADE): rejected as the whole answer, kept as a layer (ADR-v6-04).

### Consequences

- The spec word "portable" drops from the v6 goal. [VERIFIED] repo `docs/00` A2 calls the CLI gate "harness-neutral", but nothing wires it to another harness [A8 section 3]. Fix that text.
- Continuation quality depends on harness features I did not probe (ADR-v6-07 short block).

### Migration and rollback

No code change. Add this ADR to repo `docs/01-adrs.md`. Rollback: write a new ADR when a trigger fires. The ledger schema is unchanged, so option A does not block option B later.

## ADR-v6-03 Safety and autonomy as two separate settings

Status: Proposed. Evidence: A4, A3, A2. Changes safety behaviour: yes (level `off`, R-21, the `jev` default). Needs human: H1, H2, H20, H21.

### Context

- [VERIFIED] Two stored settings exist: `gate_mode` (`enforce`, `advisory`, `off`) and `level` (`unset`, `off`, `low`, `medium`, `high`). No profile names exist. A higher level word means less autonomy: `high` asks everything [A4 sections 1, 7].
- [VERIFIED] The level flips safety and autonomy together. Setting any level (even `off`) turns on SI-12, firm SI-09 to SI-12 in advisory mode and human-only raising, and also chat asks, memory and Jev [A4 section 6 "Same knob" item 1]. `low` and `medium` differ only in the Jev bar (0.70 and 0.85) and in whether Jev may decide egress (R-21) [A4 section 6 "Separate" item 1]. The host permission mode is a hidden third input: it decides chat ask or terminal permit, and memory and Jev ignore it [A4 section 4].
- [VERIFIED] The floor is not fully a floor today. Memory can silence critical calls (R-01 firewall, R-02, R-03 `kubectl apply`, R-04 key deletion), and exact merges and writes to agent files, at `low` and `medium` (WT W-116, "memory covers a whole kind of critical calls") [A4 section 9 item 1, A3 section 6]. By probe: critical calls are class-eligible. By code, not run end to end: a confirmed Allow then runs silently [A3 section 6]. [UNRESOLVED] The end-to-end run was not done, because `jade review confirm` writes state.
- [VERIFIED] Jev never decides critical, merge, R-13, R-18, R-22, human-only, blocked or secret-shaped calls [A4 section 9 item 1, A6 section 3]. Jev does decide egress (R-21) at level `low`, bar 0.85 [A4 section 3]. Only memory never takes R-21 [A3 section 6]. The two "never" lists differ.
- [VERIFIED] `level high` still sends data to the model in shadow if preferences or answers exist. No `jev` off switch exists, so MANUAL and ASSISTED are not expressible [A4 sections 3, 8].

### Decision [RECOMMENDATION]

1. Say it in the docs and in `jade check`: there is a floor (what is always asked or blocked) and there is autonomy (how much JADE decides without the owner). `gate_mode` is not a third axis. It only says whether JADE runs. Add one `jade check` row for the host mode: "profile X, but host mode is bypass: JADE is the only gate". This Mac would show it today (A3 section 0 item 43).
2. v6 keeps `level` as one stored key. So the key still joins safety features and autonomy features (A4 section 6). [RECOMMENDATION] Do not split it in v6. The claim "separate" is a claim about the floor, proved by test (point 3), not a split of the key. Split only if a later probe shows a need.
3. The floor is code constants. [RECOMMENDATION] The floor is: SI-01, SI-02, SI-05 to SI-13 (SI-03 and SI-04 were never assigned, [VERIFIED on `origin/main`]), the human-only classes, `permit_required_at`, and two "never" lists:
   - Never remembered: human-only, blocked, secret-shaped, R-21, R-22 (today), plus critical, R-13 and R-18 (the WT W-116 fix; Q-03; R-13 and R-18 stay in the list unless the trace of d14 step 7 shows they cannot reach memory). R-12, R-15 and R-23 are exact-only, not never (LD-06).
   - Never sent to Jev: critical, merge, R-13, R-18, R-22, human-only, blocked, secret-shaped (today), plus R-21 (a new tightening, H20).
   No profile, level or learning step may change a constant. [VERIFIED] This is a proposal. Today it is not true at level `off` (A4 section 5, exceptions 1 to 3).
4. Prove "separate" with two test lists. Turn the A4 `probe_matrix.py` idea (120 rows run by script) into an acceptance test.
   - List A, must pass. For gate mode `enforce` and a level of `low`, `medium` or `high`: no floor call runs silently. For SI-09 to SI-12: blocked in every gate mode except `off`. [INFERENCE] Today List A fails on the critical, merge, R-13 and R-18 rows because of the memory gap. That red test is the goal of Tier 0 (ADR-v6-12).
   - List B, asserted as "advisory by design" and printed by `jade check` as a warning: gate `off` (nothing blocks, not even SI-09), gate `advisory` (SI-01, SI-02, SI-05 to SI-08 only journal), level `off` (see point 8). Name the rows by SI, R and HO id.
   Without List B the test either fails on day one or proves nothing.
5. Autonomy is `(level, jev)`. Add key `jev` = `on` or `off`. When `off`, `proxy.step` returns before it builds any state, so nothing goes to the model, not even in shadow [A4 section 8].
6. The `jev` default is one rule, written here once. ADR-v6-13 and H7 point to it.
   - Key absent, level `low` or `medium`: `on` (keeps today's behaviour, so an upgrade changes nothing).
   - Key absent, any other level: `off`. At `high` this stops the shadow call that sends data to the model today. That is a behaviour change, but it tightens privacy.
   - New installs: `jade setup` writes an explicit `jev off`.
   - Invalid value: `off`.
   Extend `_recorded_level` (the provenance check) to cover `jev`, or an agent that writes the config could raise autonomy silently [A4 section 8].
7. [SUPERSEDED, see the reconciliation note in section 1: d05 section 3.4 and d12 V6-28 use derived display labels MANUAL, ASSISTED, BALANCED and FAST (docs and display only, no stored preset), not the table below] Profiles are human-only presets that write `(level, jev)` with one ledger event.

| Profile | Level and jev | Available when |
|---|---|---|
| manual | `high` + `jev off` | Now. It only tightens. |
| assisted | `medium` + `jev off` | After the memory fix in Tier 0. |
| balanced | `medium` (with the point 6 rule) | After the memory fix in Tier 0. It equals the existing `level medium`. It is not what this Mac runs today (`off`). |
| (no name) | `low` | Stays reachable as the raw `level low`. It is not a named profile. |
| high-autonomy, maximum | not defined | Only after Tier 2 of ADR-v6-12. |

   Reason for the last two rows: [VERIFIED] `low` differs from `medium` only by a lower Jev bar (0.70) and by letting Jev decide egress [A4 section 6]. [VERIFIED] Jev has zero labels [A6 section 1]. A name such as "high-autonomy" would sell a looser, uncalibrated threshold as a feature before any measure exists.
8. [SUPERSEDED in part, see section 1: the roadmap says text now in `jade check` (Q-05), and block only after a replay count, with the R-16 and R-14 project scope in the same change (LD-29, d12 V6-16). [INFERENCE] A flat block with no project scope adds asks and may block the owner's own work on JADE code] Level `off` must say what it is. Show it as "watch only". [RECOMMENDATION] At `off`, make these firm (blocked, not advisory): SI-07, SI-08, R-13 and R-16 on `settings.json` and JADE code, and HO-wiring. Reason: at `off`, a Write to `settings.json` passes (exit 0), so the agent can unwire the hooks [A3 section 5, probe_leveloff.out]. An invariant that is advisory is not an invariant. Other risk rules stay advisory at `off`. `jade check` warns (not "ok") when the level is `off` and the host does not prompt.
9. [RECOMMENDATION] For the owner's Mac: do not stay at level `off` with a bypass host. Today JADE is mostly a logger here [INFERENCE from A3 section 0 items 2 and 43]. The first safe step is `high`. Later steps follow the ratchet in ADR-v6-12. This is a safety change and needs approval (H2, H22).

### Alternatives and why rejected

- Keep one scalar (today): it mixes the two and cannot express manual or assisted.
- A user-lowerable "safety level" axis: one setting could lower safety. Overlays cannot lower shipped rules today [A4 section 6].
- Split "a level is set" from the autonomy features in code: more state in the most critical file, and no shown need.
- Per-project levels: more state [A4 section 8]. Rename `level` values to autonomy words: breaks downgrade, because older code reads `level`. A profile DSL: no need shown.

### Consequences

`level high` stays the strict end and the docs must say so. One more key, one more SI-09 pattern (`jade autonomy` or `jade jev`), two more `jade check` rows. Raising R-21 and level-off floors adds asks at the margin.

### Migration and rollback

Files to touch are listed in A4 section 8. The `jev` rule in point 6 changes nothing for level `low` or `medium` and removes the shadow call at `high`. Rollback: delete the key. An older JADE ignores it and keeps reading `level`, so keep `level` in sync.

## ADR-v6-04 Use sandbox, managed settings and auto mode as layers

Status: Proposed. Evidence: R1, R3 section 4, R2, A3. Changes safety behaviour: no (opt-in owner steps).

### Context

- [INFERENCE, web page via summariser; not run] The Claude Code sandbox is an OS boundary (Seatbelt on macOS) for Bash and its children. It does not cover Read, Edit, Write, WebFetch, MCP or hooks, and it has escape hatches (`excludedCommands`, `dangerouslyDisableSandbox`) [R1 section 3.5]. Auto mode uses a classifier: 0.4% false positive on 10,000 real calls, 17% false negative on 52 real overeager actions (Anthropic's numbers). Anthropic says it does not replace review for high-stakes work [R1 section 3.2].
- [VERIFIED] On this Mac host prompts are already near zero: 3,416 of 3,424 gated calls in 7 days ran with no host prompt [A3 section 0 item 43]. Anthropic's "84% fewer prompts" is an internal figure with no method [INFERENCE: vendor figure via summary] [R1 sections 3.5, 4]. It does not transfer here. The value of the sandbox here is containment, not fewer prompts.
- [VERIFIED code, INFERENCE on effect] JADE installs hooks only in `~/.claude/settings.json`. A same-user process outside the gate can change that file. Managed hooks sit in a root-owned file and cannot be removed from below [R1 section 3.4, line 118]. [INFERENCE, web page via summariser] Hooks from all settings levels are merged [R1 section 2]. [INFERENCE from docs] A managed file may also make the mods guard load. The guard status is UNVERIFIED (probe cell 13 is open) [R1 section 3.9].
- [VERIFIED] JADE has no containment. SI-13 covers test harnesses only [A3 section 12].
- [UNRESOLVED] Six facts decide the design: (P1) do `claude --bare` and `--safe-mode` skip managed hooks; (P2) is the mods guard loaded here; (P3) does git over SSH work under the sandbox; (P4) is auto mode available through the owner's gateway; (P5) does a settings `ask` rule reach the owner in auto mode; (P6) does a hook that is in both the managed file and the user file run twice (double ledger rows, double chat asks) [R1 sections 3.4, 3.9, 3.13, 8].

### Decision [RECOMMENDATION]

Treat the stack as layers. JADE does not rebuild any lower one.

| Layer | Owner | JADE's job |
|---|---|---|
| L0 OS sandbox | Claude Code | `jade check` row: enabled, `failIfUnavailable`, `allowUnsandboxedCommands: false`, credential deny for `~/.ssh`, `~/.aws`, the vault. Document the owner step. |
| L1 managed settings | Claude Code, root | Optional human-only `jade setup --managed`. Move the hook entries, do not duplicate them, until P6 is answered. Do not set `allowManagedHooksOnly`. |
| L2 auto mode | Claude Code | Baseline for ordinary risk. Conditional on P4. JADE does not copy its judgement. |
| L3 JADE gate | JADE | Deterministic rules, permits, human-only classes, ledger. |
| L4 Jev | JADE | Advice only (ADR-v6-13). |
| L5 remote | GitHub | Branch protection and the pre-push guard. `jade check` row. For SI-08 the guard is JADE's text scan, and branch protection is the real boundary. |

Do not add native deny rules from JADE. A second rule source would drift from `policies/*.yaml`, and Claude Code itself says rule matching is not a security boundary [R1 section 3.3].

Find out which host mode runs today before counting on L2. [UNRESOLVED] A3 does not say whether the 3,416 calls were `auto` or `bypassPermissions`. Add a count by mode to `jade check` if the ledger keeps the mode. If auto mode is already on, L2 is not new. If it is off or unavailable, L2 is not a layer.

Run probes P1 to P6 first. They need a new session, so they are owner steps.

Measure with one table, not one number: prompts per day, sandbox-blocked failures, and any escape found. A prompt count alone is the "fewer approvals is better" error.

### Alternatives and why rejected

- Build containment in JADE: an OS boundary is not a hook job, and JADE cannot test a sandbox across OS versions.
- Auto mode alone: probabilistic (17% miss), and no human-only classes, approver identity or ledger.
- A container for the agent (OpenHands style): heavy, and it does not stop data theft either [R2 section 3.1].
- JADE only in managed settings: single point of failure. An option, not the only path.
- Native deny rules written by JADE: a second policy source (see above).

### Consequences

Some tools may break under the sandbox. A managed install needs sudo and is one file on a fleet of one. [INFERENCE] The sandbox may reduce the damage of a parser miss (ADR-v6-02 short block). It does not stop a push over an allowed network. Remote branch protection does.

### Migration and rollback

Dry run first in a throwaway HOME, then the human runs it (owner rule). Rollback: remove the managed file with sudo. `jade check` shows the previous state. Sandbox: `sandbox.enabled: false`.

## ADR-v6-06 Verdict vocabulary and decision diagnostics

Status: Proposed. Evidence: A2 sections 3 to 6, 10, 13. Changes safety behaviour: tightening only.

### Context

- [VERIFIED] Three vocabularies exist: exit codes (0, 3, 4, 5, 6), 20 states plus CANCELLED and EXPIRED, and hook results. `NEEDS_EVIDENCE` is both a state (entered only by a provider or input error) and a route (missing or stale evidence). Missing evidence really gives ABSTAINED [A2 sections 2, 3].
- [VERIFIED] Evidence is one text string plus a self-declared `evidence_source`. No id, hash, source command or time. The agent supplies it [A2 sections 4, 5].
- [VERIFIED] Repeat detection is exact text, one session, one hour. Probe: 7 asks gave 5 provider calls (a reworded claim, a trailing space and a trailing newline each made a new call). `--no-cache` gives a fresh sample. `jade ask` uses `--risk` (default `low`), not the decision's risk [A2 sections 2, 6].
- [VERIFIED, docs-backed] Jev returns probabilities, no explanation, so it cannot say what is missing. Not captured live [A2 section 1]. No code reads `abstain_to` and `max_revisions` in `escalation.yaml` [A2 section 7].

### Decision [RECOMMENDATION]

Part 1: verdicts. A verdict is a typed view over the states and exit codes that exist. It adds no state to the table `T`. One small module (a table and one function, about 100 lines [INFERENCE]) maps exit code, state and hook result to a verdict. `jade ask --json` and `jade decide show` print it.

| Candidate | Today | v6 decision |
|---|---|---|
| ALLOW | Implemented (exit 0, ACCEPTED, permit, memory, proxy, chat, dialog) | Keep. |
| DENY | Implemented (exit 4, REJECTED, SI block, denied ask) | Keep. |
| REQUIRE_APPROVAL | Implemented (WAITING_HUMAN, permit, ask) | Keep. |
| NEED_EVIDENCE | Partial. Two meanings. | Keep as a verdict from ABSTAINED only. It carries a `missing[]` list built by deterministic code. Show a provider error as `UNAVAILABLE` (exit 5, 6). Keep the stored state string. |
| SAFE_ALTERNATIVE | Missing | Not a verdict. It is a text field `alternative` on DENY and REQUIRE_APPROVAL. It comes from a static table by rule id, written by JADE, never model text, never run by JADE. The alternative re-enters the gate as a new call. A "do this instead" verdict invites the agent to treat it as approval. |
| DEFER_WITH_PLAN | Partial | Not a verdict. It is what the agent does after REQUIRE_APPROVAL or NEED_EVIDENCE: an automatic work item (owner human, type blocker, with `next_action`) and other work meanwhile. Do not confuse it with the native `defer` of Claude Code. |

Result: five verdict names (ALLOW, DENY, REQUIRE_APPROVAL, NEED_EVIDENCE, UNAVAILABLE). The spec list mixed verdicts with next steps.

Part 2: repeat and risk rules (tightening only).
- Cap fresh Jev samples at 2 per hour per decision id. When there is no decision, key on the canonical target (command hash or commit sha), as `proxy` does with `hold_key` [A2 section 6]. Do not key on evidence text: an agent that changes one irrelevant line would get a fresh sample.
- Keep the stricter verdict on repeats, also with `--no-cache`. Journal `--no-cache`.
- With `--decision`, use the stricter of `--risk` and the decision's risk.
- Acceptance test, added to `evals/adversarial`: same decision, evidence changed by one irrelevant line, 10 times. Expected: the stricter verdict is kept and provider calls are at most 2.
- `add_evidence` still must change the evidence hash [A2 section 14]. A real new fact is allowed to re-ask.

Part 3: diagnostics. Every verdict carries deterministic fields only: `rule_id`, `risk` and its source, `why_human`, `band_used`, `missing[]`, `freshness`, `repeat_of`, `alternative`. Jev supplies none of them.

Part 4: evidence record (deferred). [RECOMMENDATION] Do not build an `evidence` table in v6 until a probe shows that PostToolUse carries enough of `tool_response` [UNRESOLVED: verified only for AskUserQuestion, A3, A7]. If built, name the tiers honestly: tier 1 is "recorded by a hook, source command known". It proves the command ran. It does not prove the claim. The agent picks the command, so `echo tests passed` is tier 1. Tier 2 is "derived by JADE" (assessment, git HEAD, content sha). Tier 3 is "declared by the agent" (the only kind today). One named use: report the share of accepts backed by tier 1 or 2 evidence. If that use is not wanted, cut the table.

### Alternatives and why rejected

- Six new states in `T`: a second vocabulary beside ACCEPT, ABSTAIN, REJECT [A2 section 14].
- Ask Jev to explain what is missing: it returns probabilities only.
- Signed provenance chains: the operator is trusted (ADR-08 of the repo). Costs more than it gives.
- Evidence table now: no probe yet, no named use.

### Consequences

Two escalation keys with no reader should be either implemented or deleted. `WAITING_DEPENDENCY` has no incoming edge and can be removed later [A2 section 3].

### Migration and rollback

Additive: `tool_use_id` on decisions (ADR-v6-10 short block). Old rows stay null. Rollback: revert the repeat key. Old code ignores new columns.

## ADR-v6-08 Learning mechanism, and what it may never change

Status: Proposed. Evidence: A6, R5, A2 sections 6, 13. Changes safety behaviour: no.

### Context

- [VERIFIED] Learning is mostly designed. Live: zero labelled rows, no calibrated threshold, no promoted template [A6 section 1]. Labels exist only for accepted calls. A run on 300 synthetic rows made `propose_threshold` suggest 0.50, and the replay rule rejected it, so the rule is nearly unreachable. Adjudication is capped at 10 per week, so 200 labels take about 20 weeks [A6 section 4.1].
- [VERIFIED] The proxy readiness gate counts 50 distinct approved keys, including hints. The live answers are 100 allow and 0 deny [A6 section 2.1]. Reworded claims count as independent in the 200-label rule [A2 section 6]. Memory use renews its own life, so a silent call never ages out before day 180 [A6 section 7.1].
- [INFERENCE: vendor figure via summary] Anthropic reports that users approved about 93% of prompts [R1 section 4]. [INFERENCE] More asks may mean less care per ask. An owner "yes" is not a clean label [R5 section 3.5]. The owner's own approve rate is [UNRESOLVED] [R5 line 177].
- [VERIFIED] The loop cannot start at level `off`: memory and Jev do not run, so there are no silent allows to audit [A4 section 3].

### Decision [RECOMMENDATION]

Principle: measure first. Then propose. Never loosen on its own.

What the loop may do:
- Propose a threshold, a question template or a scope. A human promotes (as today).
- Add a stricter step without a human: shorten memory age, start a new model or question version in shadow, add an ask. [INFERENCE] A stricter step is not free. It raises ask volume, and that can lower the owner's care. So each such step must report asks per day and the approve rate of the new asks. If that approve rate goes above an owner-set value, the step pauses and the owner reviews it. [RECOMMENDATION] Start the value at 95%. A pause is a loosening, so it may only return to the last human-approved setting, never below it. The loop writes every pause to the ledger as an event the owner sees (master plan check, 2026-11-16).
- Write counts to the task-observer (as today, counts only).

What it may never change:
- SI rules, human-only classes, "never" lists, `permit_required_at`.
- Any rule's risk level downward, any new allow, any new silent run, without a human confirm in a terminal.
- `level`, `jev`, profile, approval backend, who may approve.
- Its own promotion gates, label definitions, split rules, kill switch.
- Anything fed by text an agent wrote. An owner "Allow" stays a hint.

Labels and measurement (this fixes "labels exist only for accepted calls") [R5 section 5.2]:
- Three label types, never merged: claim true, owner would allow, harm observed.
- Define "harm observed" as a concrete signal, not a feeling: a later revert of the change, a failed outcome in `jade decide outcome`, or the owner marking "wrong" in review. If none of these can be read, drop the label.
- A random audit of silent allows, with the sampling probability stored. [SUPERSEDED by d08, d10 D-B9, d12 V6-29 and d13 X-20: the stage-1 audit reviews 100% of one scope's silent runs up to 59 runs, then a fixed weekly count. It is not a flat 5%.]
- A review of a sample of blocks and abstains, so labels exist below the accept line.
- [STRUCK, d13 X-20 and LD-11: no seeded bad calls (honeypots) in v6; passive attention numbers only] A few seeded bad calls in the review queue, to measure attention.
- Report per group, with intervals and counts. [VERIFIED by script, R5 section 5.4] 59 audited rows with no harm show a false-accept rate below 5%. 299 rows show below 1%. Do not state a target tighter than the data allows.

Headline pair (the owner's third goal, in two numbers):
1. Upper bound of the harmful-silent-allow rate among silent allows.
2. Unneeded-ask rate: the share of asks the owner marks "did not need to ask".
Plan: the first usable number comes after 59 audited silent allows. At the existing cap of 10 labels per week that is about 6 weeks. 299 rows is about 30 weeks. [INFERENCE] The audit load must stay inside the same 10 per week cap, not on top of it.

### Alternatives and why rejected

- Automatic threshold learning: labels exist only for accepted calls, and a rubber-stamping owner poisons it. The probe proposed a looser bound from no data.
- Conformal risk control now: needs audit labels. Later: a 30-line scan [R5 section 5.7].
- Platt or isotonic now: the docs promise them, no code exists [A6 section 4]. Later, one-parameter Platt at 500 or more labels.
- Importance-weighting estimators (IPS, doubly robust): no logged choice probabilities, and some calls have no chance of a label [R5 section 0].
- Embedding retrieval for memory: no need shown, and a new poisoning surface.

### Consequences

[INFERENCE] The owner pays audit effort now for accuracy later. This is a real cost against "minimum friction". The weekly cap is the ceiling. The replay rule must change to a non-inferiority margin, and verify calls must link to decisions, before any threshold can move (A6 recommendations 2 to 4).

### Migration and rollback

An additive label-type column and a sampling table. Rollback: stop sampling. The loop stays proposal-only.

## ADR-v6-12 Order of work: safety and measurement before autonomy

Status: Proposed. Evidence: A3, A4, A6, A2, A8. Changes safety behaviour: no (it orders work).

### Context

- [VERIFIED] Floor defects on 0.6.1: memory covers critical kinds (WT W-116); a chat grant is spent on a different command (A3 probe C4); level `off` makes SI-08 advisory; SI-08 has text-scan gaps (A3 section 8b); the `maintain` job is not loaded [A3, A4].
- [SUPERSEDED by Q-02 and d13 X-19] [VERIFIED when written] PR 47 at c6ab27a (committed) was weaker than main on SI-08 for several zsh forms (uncommitted WT rows W-108, W-109, W-111, W-113). A round-4 fix was uncommitted in its worktree and not reviewed [A1 section 4, A3 section 11]. The head is now `c676801`. Never merge `c6ab27a`.
- [VERIFIED] Nothing measures Jev or the proxy. `jade review` shows 67 distinct answered calls against a gate of 50, and all 100 answers are allows [A6 section 2.1]. [INFERENCE] `would_block` events (about 2,380 in 14 days at level `off`) are a free friction baseline, with the caveat that many come from developing JADE [A6 section 8, A8 section 11].

### Decision [RECOMMENDATION]

A ratchet in three tiers. Each autonomy increase is a human act and a ledger event. A learning loop never does it.

- Tier 0, before any autonomy increase:
  - Fix the memory critical kind: critical is never remembered, and env prefixes and cwd that select the target go into the key.
  - Bind chat grants to the exact call (ADR-v6-14).
  - Decide what level `off` means and move the owner off `off` (ADR-v6-03 points 8 and 9).
  - Load the `maintain` job.
  - Verify branch protection on the default branch of the JADE repo and of any repo where SI-08 matters. SI-08 is a guard. Branch protection is the boundary.
  - Make the floor test lists (ADR-v6-03 point 4).
  - Fix the safe-display bug: command text with bidi and control characters is shown raw in chat and dialog [A7 section 3]. This is a bug fix and does not wait for any language choice (short block ADR-v6-09).
  - Install the newest release after CI is green on the merged commit.
- Tier 1, measurement: `--json` and [`tool_use_id` is deferred, d13 X-07 and DF-12] the audit sampling and labels (ADR-v6-08), the headline pair, a Jev noise test (same state, 20 calls; trivially changed state, 20 calls) [A2 section 14]. The baseline is asks per day and approve rate together with false-accept bounds, never asks per day alone.
- Tier 2, autonomy may rise. Pass rule, as a number: the upper bound of the false-accept rate is below 5% on at least 59 owner-audited calls that memory or Jev would silence, and the owner signs off. [The seeded-bad-call check is struck: no honeypots in v6, d13 X-20. The pass rule is 59 audited clean runs from 10 or more sessions plus the owner's sign-off, d12 V6-30.] [RECOMMENDATION] Ground truth is the owner's audit labels. [UNRESOLVED] A comparison with auto mode needs a way to run its classifier on recorded calls. I found none (R1 section 8 item 8 says only that no data exists). So the gate does not depend on auto mode. If P4 shows a way, add it.

Rule: a change that makes JADE ask less must name the metric it expects to hold (decision accuracy, not approval count) and the number of rows that support it.

### Alternatives and why rejected

- Ship autonomy first, measure later: a wall of allow clicks already passes the proxy gate, and no data exists.
- One release for everything: [RECOMMENDATION] merge the 0.7.0 stack in one train after the memory fix [A8 section 15]. [SUPERSEDED: Q-02 merges PR 47 first and ships it inside 0.7.0, not separately.]

### Consequences, migration, rollback

Slower autonomy gains, more trust in them. No code of its own. Rollback: none needed.

## ADR-v6-13 Jev's authority and sample shopping

Status: Proposed. Evidence: A2, A4, A6, R5. Changes safety behaviour: tightening only.

### Context

- [VERIFIED] Bands: verify claim accept 0.70 (0.85 at high). `score` never gates (always exit 0). A companion check only lowers 0 to 3 at medium or higher [A2 section 2].
- [VERIFIED] A cached repeat never loosens. An uncached one can. Probe: P of 0.5 then 0.9 on the same state gave exit 3 then exit 0 [A2 section 6]. [UNRESOLVED] How much Jev's answer moves on a trivial change needs a live call.
- [VERIFIED] An agent may send `jev_result_accept` itself. The hook does not read ACCEPTED, so the damage is to the audit trail [A2 section 3].
- [VERIFIED] The proxy is limited to 2 tries per command per hour. It never judges the "never sent to Jev" list in ADR-v6-03 point 3. Live: 48 calls, 0 allowed [A2 sections 2, 6]. It does judge R-21 at level `low` (A4 section 3).
- [VERIFIED] Question v2 is not calibrated (WT W-102, uncommitted; on main W-102 is the Jev-in-the-hook feature). README says VALIDATED = No [A3 section 11, A8 section 14].

### Decision [RECOMMENDATION]

Jev is advice. Allowed roles:
1. Verify claims: advisory. Feeds the ledger and the Stop reminder.
2. Proxy for plain-high calls only (R-21 excluded, H20). Optional. The default is the single rule in ADR-v6-03 point 6. The bar stays fixed.
3. Shadow measurement, only when `jev` is `on`.

Not allowed: gating anything else, lowering a risk class, deciding a floor call, writing human text, supplying `missing[]`.

Anti-gaming is in ADR-v6-06 part 2. Only `jade ask` writes `jev_result_*`, and it must point to a `jev_calls` row (H17).

Freeze proxy v2 and scope work. Proxy agreement cannot be proven on natural data at this owner's volume. A deny-recall bound of plus or minus 10 points needs about 97 owner denies [R5 section 5.4]. The live data is 100 allows and 0 denies [A6 section 2.1]. Seeded bad calls measure attention, not the real deny mix. [RECOMMENDATION] State plainly that proxy validation is unreachable at this volume. The proxy stays opt-in and unproven. Do not wait for 97 denies. Use the audit labels of ADR-v6-08 for the false-accept bound, which is the safety number.

### Alternatives and why rejected

- Block "done" until Jev accepts: not measured. The "30% of low verifications followed by a done claim" figure is a README heuristic, not a measured base rate [R1 section 3.10].
- Replace Jev with auto mode: no abstention, ledger or claim verification.
- Remove Jev: verify and abstain are what no native feature gives [R1 section 6].

### Consequences, migration, rollback

Jev stays "unproven" until data exists. Changes are additive and tightening. Rollback: revert the repeat key.

## ADR-v6-14 Approval channels

Status: Proposed. Evidence: A3 section 7, A7, R1 sections 3.1, 3.12, 3.13, R4 section 3.5, A5 section 2b. Changes safety behaviour: yes (grant scope; recommendation field).

### Context

- [VERIFIED] Three channels exist: chat (an `AskUserQuestion` rewritten by the hook), a macOS dialog for subagents, and the signed terminal permit [A7 section 1]. A native hook `ask` did not reach the owner in auto or bypass on host 2.1.314. Settings `ask` rules and a PermissionRequest answer are untested [R1 section 3.13].
- [VERIFIED] A chat Allow is keyed on the normalised command, session and fingerprints, not the plan hash. An Allow for `KUBECONFIG=staging kubectl apply` was spent on the prod text in another cwd (probe C4) [A3 section 7]. The question has no consequence of deny or no answer, and no working folder [A7 section 2].
- [VERIFIED] An MCP `Elicitation` hook can answer for the user, so it is not human-only [R4 section 3.5]. [UNRESOLVED] `askUserQuestionTimeout` may close a JADE ask and submit chosen options. Needs a test that `settle` treats it as no grant [R1 section 3.12].

### Decision [RECOMMENDATION]

1. Keep the three channels.
2. Bind a chat grant to the plan hash (tool, text, cwd), as permits are. Add the C4 case as a negative test [A3 section 9].
3. One question builder and one text source for chat, dialog and permit. Field list from `escalation.yaml` `human_question.each_question_includes`. This implements the old work item W-83 instead of deleting it. Add: what Deny does, what no answer does, working folder, who asks.
4. Show facts, not advice. Example: "last allowed: date; times allowed: 3". Do not show "this can be undone" unless the rule marks reversibility on purpose and a test covers that rule. The reversibility guess comes from a regex classifier with documented gaps (A3 section 8b). A wrong "reversible" label would be a false statement shown to the human. Even a fact such as "allowed 3 times" can push the owner toward Allow, in a setting where about 93% of prompts are approved [R1 section 4]. So this field needs approval (H10).
5. Re-probe the native channels (cells 1, 2, 11, 6, and a settings `ask` rule) on the current host. Use `defer` for `-p` only after a probe.
6. Never use MCP elicitation for approval. `jade check` warns on `askUserQuestionTimeout`.

### Alternatives and why rejected

- Native `ask` or PermissionRequest as the channel: it did not reach the owner in auto on 2.1.314. Re-probe first.
- Elicitation: hook-answerable. Same class as T25.
- Terminal permits only: friction. On 2026-11-13 there were 18 terminal approvals, then 18 asks expired (repo docs/12 section 1; [VERIFIED A6, A7, A8; same wording in d03 HR-03]).
- Batch asks: dropped by design (W-35). Scopes already cover "same kind".
- A "recommended" button: anchoring (point 4).

### Consequences, migration, rollback

A grant for text A no longer covers text B. More questions at the margin. Migration: grant rows add `plan_hash`. Rollback: revert the key to `hold_key`.

## 2. Smaller decisions

Each block is a decision that is a work list, not a design choice. Each has a backlog pointer. They stay short on purpose.

### ADR-v6-02 Keep shellnorm, rules and permits custom

Status: Proposed. Evidence: R3, A3, R6, R1. Safety change: no.

- [VERIFIED] Parsing code is about 4,122 lines (`shellnorm` 1384, `interp` 917, `samecmd` 892, `inert` 574, `blind` 355). Rules are Python `re` text in `policies/risk.yaml` and `safety.yaml`. 37 lines use look-around [R3 sections 2, 6.1].
- [INFERENCE, web page via summariser] No engine compared parses shell: OPA, Cedar, Invariant Guardrails, AGT, Cerbos [R3 section 3]. Cedar returns Allow or Deny and skips a policy that errors. A skipped `forbid` is a silent allow [R3 section 3.2]. Snyk announced the Invariant Labs acquisition on 2025-06-24 [VERIFIED announcement, R3 section 3]. Later Snyk product lines are secondary-source [INFERENCE].
- [VERIFIED] Gaps on main: `git -C 'a\ b' push`, `git send-pack`, `g=git; $g push`, `gh api .../merge` pass at SI-08 or rate low [A3 section 8b]. A trailing comment after `| sh` hides R-22 [R6 section 1.5].
- Decision [RECOMMENDATION]: keep shellnorm, rules and permits custom. Do not port rules to Rego or Cedar. Treat the parser as best effort. The boundary for what it misses is the OS sandbox (ADR-v6-04) and remote branch protection.
- Add `tree-sitter-bash` (MIT, 0.25.1 [R3]) as a dev and test dependency only. Run a differential test over the replay and acceptance corpora: compare command words and redirect targets with `shellnorm`. It covers bash forms only. The PR 47 bugs are zsh operators (`>>&`, `>>|`, `&>|`, `>!`) and tree-sitter-bash does not parse zsh. So add a fixed zsh-operator fixture list. Pass bar: any disagreement on a push or redirect target must be triaged within one release. Coverage is [UNRESOLVED] until run [R3 section 6.3].
- Borrow two ideas, not code: a rating "UNKNOWN" (OpenHands) and cross-call data-flow rules (Invariant). Today R-99 rates any unmatched command low. Changing that default is a human choice (H13).
- Revisit an engine only if several machines or users need one shared policy, the rule set grows about 10 times, or a compliance rule names an engine [R3 section 6.2].
- Rejected: OPA or Rego for the match step (it replaces only the match step, not the parser, and adds a failure path in a hook that is fail-open by default on old hosts); Cedar (no level, rule id or reversibility); Invariant, AGT, NeMo, LlamaFirewall, Guardrails AI (wrong layer or no shell parsing); tree-sitter at runtime (a syntax tree is not meaning, and it adds a native dependency); `bashlex` (GPLv3+).
- Migration: none. A test-only dev dependency, no change to an installed hook.
- Rollback: delete the test. Backlog: parser differential test.

### ADR-v6-05 Harness adapters: do not build now

Status: Proposed. Evidence: A3 section 8e, R1 section 5, R4 section 3.8, A8 section 3. Safety change: yes (a new human-only class).

- [VERIFIED] Launching `codex exec`, `cursor-agent`, `aider --yes`, `gemini --yolo`, or `claude -p --settings '{"hooks":{}}'` is rated low (R-99). Those harnesses have no JADE hook [A3 section 8e]. [UNRESOLVED] A child `claude` with `--bare` may skip hooks (P1).
- [VERIFIED] The owner's own `~/AGENTS.md` (loaded in this session) says "Absolute Mode on for Codex chat" and "Codex/Cursor/CLI -> Ollama". That is evidence of Codex and Cursor use. It does not show that they run with hooks. R4 section 10 and A8 section 16 ask the owner.
- Harness hook facts, each with its own confidence [R1 section 5]: Codex and Cursor have pre-tool hooks with exit 2 or `permission` fields, and Cursor has `failClosed` [INFERENCE, web page via summariser; Cursor approval pages not fetched]. Cline hooks come from search snippets and a mirror [secondary-source, UNVERIFIED]. The Gemini CLI hooks page says it was replaced by Antigravity CLI [UNRESOLVED status]. Aider has no hooks [INFERENCE, web page via summariser]. "Only Cursor and Claude can fail closed" is [INFERENCE from absence]. [INFERENCE] The decision below does not depend on any of these facts.
- [VERIFIED] `jade ask` exit codes work from any harness. Nothing blocks there [A8 section 3].
- Decision [RECOMMENDATION]:
  1. No second adapter in v6. Say "Claude Code only" at the top of the README. Remove "harness-neutral".
  2. Make the launch of another agent, and a hook-skipping flag, a human-only class (H19). Do not make it a normal high rule. A normal high rule can be remembered by kind after one confirm, and then the rule that stops hook-skipping would be silenced. The new class is never remembered and never sent to Jev. [INFERENCE] This is a speed bump, not a close. A script, `python -c subprocess`, an alias or `$(...)` goes around a text match, the same class of gap as in ADR-v6-02.
  3. Keep the gate core free of Claude-specific output code. Write the neutral-event spec only when a trigger fires: the owner confirms a second harness that has a blocking pre-tool hook. Until then, Codex would get permit-only approval and the label "advisory-grade" (Codex is fail-open).
- Rejected: refactor `hooks.py` now (churn in the most critical file during the PR 47 regression period); Codex and Cursor adapters now (no shown need); an MCP gateway (covers MCP only [R4 section 3.8]); an MCP server with `jade_ask` (advisory only; cheap later); JADE as a Claude Code plugin [UNRESOLVED, not researched].
- Migration: an existing install gets one new human-only class when it upgrades. It is never remembered, so no stored answer needs a change. The owner message is a line in `jade check` (LD-26).
- Rollback: remove the class. Backlog: launch-gate class.

### ADR-v6-07 Durable work and recovery without a workflow engine

Status: Proposed. Evidence: A5, R2, R1. Safety change: yes, one item (4).

- [VERIFIED] The state record is crash-safe. Lifecycle tests are skipped on this Mac, so CI is the gate [A5 section 4]. Defects: `work.save` is not atomic and has no lock; `jade work sync` is manual; no blocker opens a work item; SessionStart prints at most 8 lines and ignores `source`; nothing counts repeated tool failures; `askchat.rewrite` keeps one question and drops the rest, which conflicts with the flow skill that batches up to four (not run live) [A5 sections 2b, 4, 5]. `WAITING_DEPENDENCY` and `RETRY_SCHEDULED` are never entered or never resumed [A5 section 3].
- [VERIFIED] SI-07 release after a crash needs the same session id. That id comes from `JADE_SESSION_ID`, and SI-11 does not block it [A5 section 4]. [INFERENCE, not run, because it writes state] An honest agent in a new session is stuck. A dishonest one could set the old id and lift the hold. The guard is a speed bump, not a lock.
- Decision [RECOMMENDATION]: no workflow engine (ADR-v6-01). Fix these, each independent and additive:
  1. `work.save`: temp file, `os.replace`, file lock.
  2. [NOT IN V6, d12 V6-27 does not build it] Run `work sync` at SessionStart or in `maintain`. Default path is the JADE repo.
  3. Auto-open a work item (owner human, type blocker) when a permit stays PENDING past expiry, an SI-07 hold has no outcome after 1 h, or asks pause. Use existing dedupe.
  4. Human-only `jade decide reconcile` for a decision of another session, and fix the hold message. Also add `JADE_SESSION_ID` to the SI-11 variable list, or stop trusting the variable for outcome release. Test: the agent sets `JADE_SESSION_ID`, runs `decide outcome`, and is refused. This changes who can release SI-07 (H22).
  5. SessionStart reads `source`. On `compact` or `resume`, re-inject open ask, permit and hold ids. No new hook.
  6. [DEFERRED, d13 DF-17 and DF-45: no retry counter in v6] PostToolUseFailure counts identical failing calls and tells the model to change approach after 2. Count only, never block.
  7. `askchat.rewrite` keeps the non-token questions and replaces only the token one.
  8. Test: a nonzero exit after a partial irreversible action, then memory [A5 section 8].
- Use native features for continuation: `/goal` for "keep going", Monitor for waits, cron and cloud or Desktop tasks for unattended work. Probe `defer` for `claude -p` before using it [R2 section 7]. The FLOW and JADE Stop interplay is in the FLOW hook, which lives outside this repo [A5 section 2].
- Rejected: Temporal, DBOS, LangGraph, Restate, Inngest (ADR-v6-01); a JADE scheduler (`/goal`, Monitor and cron exist); wiring the two dead states (nothing needs them); new hooks PreCompact, SessionEnd, StopFailure (item 5 covers the need; add one only if a probe shows a loss).
- Consequence: recovery after Esc stays human-led. [INFERENCE] Esc may not fire PostToolUseFailure, and JADE keeps the hold on interrupt on purpose [A5 section 4].
- Migration: the SI-11 variable list gains `JADE_SESSION_ID` (Q-06); an agent that set it before now gets a refusal. The other items are additive. [UNRESOLVED] How an honest agent in a new session releases an SI-07 hold after this change depends on the resume probe (d13 U-07).
- Rollback: per item.

### ADR-v6-09 Showing text safely, and language

Status: Proposed. Evidence: A7, R6 section 2.3. Needs human: H9 (language only).

- [VERIFIED] All human text is English in code strings. No language key. No bidi handling on the chat path [A7 section 3]. The chat and dialog channels show command text raw. A probe kept U+202E, U+200B and U+2067 through `question_for`. The terminal permit screen escapes them [A7 section 3, R6 section 1.5]. The permit `printable()` also escapes ZWNJ (the Persian half-space), so do not use it on prose [R6 section 1.5].
- [INFERENCE, web page via summariser] Claude Code `language` sets the model's reply language. It does not translate hook text [R6 section 2.3]. Persian rendering in Claude Code and in osascript dialogs is [UNRESOLVED].
- [VERIFIED] About 40 fixed plain sentences exist on the unmerged branch (`askplain.RISK_TEXT`, tested to at most 20 words per sentence) [A7 section 1].
- Decision [RECOMMENDATION]:
  1. Safe display is a bug fix, not an ADR choice. It goes into Tier 0 of ADR-v6-12. One shared "show safely" function for every command, path and content field in chat, dialog, permit, `jade review` and `jade knows`. Escape bidi and control characters. Wrap values in isolates after cleaning, never before. It does not wait for any language decision.
  2. Human text comes from static tables written by JADE. Never from a model, never from agent text. Default stays simple English, at most 20 words per sentence, tested. Put the owner's simple-English rule into JADE's own skill and `claude-md` section, with a test.
  3. Persian is not built. Trigger: the owner says yes (H9) after one manual probe of Persian rendering in the real host. If built, use a flat table keyed by rule id and language, a human-set `human_lang` key, and a test for equal placeholders. Rule ids, risk words, the command and the ask id are never translated.
- Rejected: runtime model translation (a new channel for an agent to steer the human); relying on Claude's `language` setting (it changes model replies only). A heavier message library is not needed for about 40 sentences.
- Migration: none for safe display (no stored state). If Persian is ever built, a `human_lang` key is added with default `en`; an absent key reads `en`.
- Rollback: delete the key.

### ADR-v6-10 Observability export

Status: Proposed. Evidence: R4 section 7, A8 section 11. Safety change: no.

- [VERIFIED] No export, no OpenTelemetry, no `--json` on `jade check` or `jade ledger stats`. `tool_use_id` is not stored on decisions [R4 section 2, A8 section 11]. [INFERENCE, web page via summariser] The OTel GenAI conventions are in Development status. Claude Code exports its own events keyed by `tool_use_id` and does not pass `OTEL_*` to subprocesses [R4 section 7]. [UNRESOLVED] No evidence the owner runs an OTLP backend [R4 section 10].
- Decision [RECOMMENDATION]: now, store `tool_use_id` on decision rows and add `--json` to `jade check` and `jade ledger stats` (these also feed ADR-v6-08). Later, only if the owner runs a backend: `jade export --otlp-json <file>` as a separate command (the hook cannot export). Stdlib only, opt-in, arguments and results never exported, reuse `redact()`, stamp a `jade.semconv_snapshot` attribute.
- Rejected: an `opentelemetry-sdk` dependency (breaks the 3-dependency footprint); export from inside the hook (no `OTEL_*`, and added latency); two formats (pick one).
- Migration: none to start. `--json` is additive. [`tool_use_id` is deferred, X-07.] Old rows keep a null column.
- Rollback: stop writing the column.

### ADR-v6-11 Packaging and distribution

Status: Proposed. Evidence: A8 sections 1, 4, 6, 8, 14, 15. Needs human: H12.

- [VERIFIED] Install is `uv tool install git+https://...@v0.6.1`. Not on PyPI. Tags are annotated, not signed. No provenance. The trust root is one GitHub account [A8 sections 4, 6]. The wheel installs a top-level package named `policies` (W-28). Python is 3.13 or newer. The release workflow does not check that the tag is on main, that CI was green on that SHA, or that the CHANGELOG section exists [A8 section 8]. CI does not cover macOS or Python 3.12 [A8 section 14].
- [INFERENCE] A full install needs about 6 to 14 human steps over at least a week (A8 gives both figures, in two places, lines 41 and 214). It is a power-user install.
- Decision [RECOMMENDATION]: keep the git-tag install for the alpha. No PyPI yet. Make the release workflow enforce the written rules (tag is an ancestor of `origin/main`, CHANGELOG section exists, CI green on that SHA, pinned actions, signed tags, build provenance). Add an advisory `jade quickstart` with no sudo. Rename the `policies` package before any PyPI release; then use trusted publishing (the account holder is H12). Add a macOS CI job and a short "what leaves your machine" page. State the platform matrix: macOS only is tested.
- Rejected: PyPI now (name risk, no provenance); Homebrew, container, binary (hooks need host access, and `uv` works); a configurable provider URL (a real adoption block [A8 section 14], but a separate human-only config decision).
- Migration: the `policies` package rename (W-28) needs a one-time note and a reinstall step for existing installs. The workflow checks apply to the next tag only.
- Rollback: `jade upgrade --ref` to a prior tag. The rename needs a one-time migration note.

## 3. Decisions that need the human

| # | Decision | Options | Recommended default | Safety change? |
|---|---|---|---|---|
| H1 | Direction and names of the scale | strictness words, autonomy words | Keep `level` strictness. [SUPERSEDED: derived display labels, d05 3.4, not stored presets] No "high-autonomy" or "maximum" yet. | No |
| H2 | What level `off` means | A: SI-07, SI-08, R-13 and R-16 on settings and JADE code, HO-wiring block at `off`. B: warn only. C: unchanged | [SUPERSEDED in part: Q-05 and LD-29] A, plus a `jade check` warning. Text now; block after a replay count, with R-16 and R-14 project scope | Yes |
| H3 | May memory silence a critical call | Never. Exact only. As today | Never (Tier 0 fix) | Yes |
| H4 | Chat grant bound to exact call | Bind. Keep | Bind | Yes |
| H5 | Install managed settings (sudo) | Yes, no, later | Yes, as opt-in, after a throwaway dry run and probes P1, P2, P6. No `allowManagedHooksOnly`. | No |
| H6 | Turn the sandbox on | Pilot, off | Pilot with `failIfUnavailable`. Check git over SSH first. Measure the three-part table. | No |
| H7 | Does the Jev proxy stay | Keep opt-in, remove | Opt-in, with the default rule in ADR-v6-03 point 6. Freeze v2. | No |
| H8 | Is a second harness in scope | Yes, no, ask | The owner's `~/AGENTS.md` names Codex and Cursor. Ask which has a blocking pre-tool hook. No adapter until then. | No |
| H9 | Language for human text | Simple English, Persian, both | Simple English only until the owner asks | No |
| H10 | Show facts in the question | None, facts only, advice | Facts only, tested. No "can be undone" unless the rule marks it. | Yes |
| H11 | Does an OTLP backend exist | Yes, no | No. Ids and `--json` only. | No |
| H12 | PyPI and account holder | Now, later, never | Later, after the release gates | No |
| H13 | Default for unrecognised calls | R-99 low, ask | Keep low. The sandbox covers the gap. | Yes if changed |
| H14 | May learning ever auto-promote | None, templates only | None until labels exist. Templates only after. Never thresholds. | No |
| H15 | Release train | One 0.7.0 train, many | One train after the memory fix. [SUPERSEDED by Q-02: PR 47 merges first and ships in 0.7.0.] | No |
| H16 | Is `--no-cache` for agents | Agent, test only | Test and human only. Cap resamples. | Tightening |
| H17 | May an agent send `jev_result_*` | Yes, only `jade ask` | Only `jade ask` | Tightening |
| H18 | Audits and seeded bad calls in the review queue | Yes, no, capped | Audits yes, inside the 10 per week cap. [STRUCK for seeded bad calls: no honeypots in v6, d13 X-20 and LD-11] | No |
| H19 | Launch of other agents and hook-skipping flags | Human-only class, high rule, leave low | Human-only class | Yes |
| H20 | R-21 (egress) out of Jev at every level | Yes, no | Yes (today Jev decides it at `low`, bar 0.85) | Tightening |
| H21 | Default of the `jev` key | One rule (ADR-v6-03 point 6) | The point 6 rule | Yes (shadow call at `high` stops) |
| H22 | Move the owner off level `off`; session id in SI-11 | Stay, `high`, later steps | `high` now. Add `JADE_SESSION_ID` to SI-11. | Yes |

## 4. Things that should NOT be built

- A durable workflow engine, a scheduler or a resident agent in JADE core (ADR-v6-01, ADR-v6-07).
- A second command allow-list engine, or a port of rules to Rego or Cedar (ADR-v6-02). Native deny rules written by JADE (ADR-v6-04).
- A JADE mod, an Elicitation hook as an approval path, an agent-type hook (R1 section 7).
- An MCP server, proxy or gateway now. A2A. MCP auth. MCP roots. MCP annotations as a trust input (they may only tighten, in an audit row) (R4 section 10).
- New verdicts `SAFE_ALTERNATIVE` and `DEFER_WITH_PLAN` as states or verdicts (ADR-v6-06). An `evidence` table before the PostToolUse probe.
- Wiring `WAITING_DEPENDENCY` or `RETRY_SCHEDULED`.
- Automatic loosening by any learned data. Auto-promotion of thresholds. Embedding memory. Platt, isotonic or importance-weighted estimators before labels exist (ADR-v6-08).
- Model-written human text or runtime translation (ADR-v6-09). A Persian catalogue before the owner asks.
- The `opentelemetry-sdk` dependency. OpenInference unless asked (ADR-v6-10).
- A "high-autonomy" or "maximum" profile, or any profile that touches the floor (ADR-v6-03).
- Per-project levels. Batched asks (A4 section 8, A7 section 9).
- Second-harness adapters now (ADR-v6-05). PyPI, Homebrew, containers or a binary now (ADR-v6-11).
- A containment layer inside JADE (ADR-v6-04).

## 5. Unresolved items

- [UNRESOLVED] What "v6" means. Settled as an assumption. Ask the owner.
- [UNRESOLVED] The two load counters (7-day `jade check` and 14-day `ledger stats`) disagree. Reconcile before any friction claim.
- [UNRESOLVED] Probes P1 to P6 (ADR-v6-04), and the `defer`, `--resume` session id, `/goal` restore, native `ask` rule and `askUserQuestionTimeout` probes. They need a new session.
- [UNRESOLVED] Which host mode (`auto` or `bypassPermissions`) the 3,416 calls used.
- [UNRESOLVED] The end-to-end run of confirm-then-silent-run for a critical call (A3 section 6).
- [UNRESOLVED] Whether PostToolUse carries enough output for tier-1 evidence.
- [UNRESOLVED] How much Jev's answer moves on a trivial change, and a live capture of its response body.
- [UNRESOLVED] How to run auto mode on recorded calls, if ever.
- [UNRESOLVED] Persian rendering in Claude Code and osascript dialogs.
- [UNRESOLVED] Effort sizes are my estimates. Cost and latency of any engine were not measured (R3 section 7).
- [UNRESOLVED] Sandbox runtime repository name (`anthropics/` or `anthropic-experimental/`) [R1 section 3.5].
- [UNRESOLVED] Whether the unmerged `open-items-*` branches change anything in A4 and A2 that I did not read (A4 and A2 say they did not).
- [UNRESOLVED] The owner's own approve rate [R5 line 177].
