# 01 — Architecture Decision Records

Format: Context → Decision → Alternatives rejected → Consequences. Status of all: **accepted** by the maintainer on 2026-11-09 (Q1, `BACKLOG.md`) and **implemented** in `jade` 0.2.x. Amendments written after the review carry their own heading (ADR-05, ADR-09). Where an ADR describes a mechanism that is designed but not built, the text says so and names the work item.

## ADR-01 Deployment topology: one Python package, SQLite, hooks, launchd

**Context.** 65 Jev calls/day from one user on one Mac (inventory §3). Existing precedent `graph-refresh` = bash CLI + Claude Code hooks + LaunchAgent + `check` subcommand. The master plan sketched three planes and optional services.

**Decision.** Ship `jade` as a single Python 3.13+ package installed with `uv tool install` (entry point `~/.local/bin/jade`). State in `~/.local/share/jade/jade.sqlite` (WAL mode). Policies are YAML files in this repo, loaded read-only. Claude Code hooks (`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`) call `jade hook <event>`. Background maintenance (calibration refresh, drift check, ledger compaction) runs via `com.<user>.jade.plist` only from Phase 3.

**Rejected.** (a) Separate services/queues/Redis: no throughput or isolation need; adds ops surface. (b) Reusing an existing local RAG/cache service as storage: different lifecycle and data model. (c) Laravel stack: not present on this host; the plan's Laravel paragraph applies to a repo we are not in.

**Consequences.** No network listener added (LAN exposure rules kept). Everything testable with `uv run pytest`. If a second host joins, the ledger becomes per-host and syncing is a new ADR.

## ADR-02 Jev transport: OpenRouter Decisions API behind an adapter interface, model pinned

**Context.** S1–S4: OpenRouter `/api/alpha/decisions` works today with the existing Keychain key; the official endpoint needs a TypeSafe key we do not have; OpenRouter aliases differ from official ids and `jev-latest` does not resolve there.

**Decision.** `adapters/jev/` defines `DecisionProvider.ask(state, questions, model) -> Answers | ProviderError`. First implementation: OpenRouter. Request model id `typesafe/jev-1.14` (the only 1.14 id OpenRouter accepts); every ledger row stores the **resolved** `model` from the response (`typesafe/jev-1.14-20261014`) and the response `id`. A change in the resolved string is recorded as a `MODEL_DRIFT` event and opens an informational work item; it does **not** freeze adjustment (human decision Q6, 2026-11-09: follow upgrades). Safety comes from the calibration group key instead: a new snapshot is a new group that starts at the documented default thresholds and earns adjustments only with its own labels (ADR-07).

**Rejected.** Hardcoding `jev-latest` (does not exist on OpenRouter; alias drift). Routing through the private LLM gateway (`<private-gateway>`): it cannot serve the Decisions API (CLAUDE.md exception).

**Consequences.** Switching to api.typesafe.ai is a config change plus a key (BACKLOG W-03). Provider errors are typed: `UNAVAILABLE` (network/5xx/529), `REJECTED` (4xx), `RATE_LIMITED` (429). Retry: 429/529 only, max 2, exponential with jitter, total deadline 20 s.

## ADR-03 Keep `jev` as a compatibility shim over `jade ask`

**Context.** 907 calls in 14 days use the `jev verify|choice ...` syntax taught by CLAUDE.md; agents' memories and the user's muscle memory depend on it.

**Decision.** `~/.local/bin/jev` becomes a thin wrapper that execs `jade ask --compat ...` with identical flags and output format, plus new exit codes (ADR-05). All journaling and policy live in `jade`. CLAUDE.md is updated once, at Phase 2, to describe the exit codes and the state-file flag.

**Intentional divergences from the original script** (tested in `tests/acceptance/test_judging_and_improvement.py`): `JEV_MODEL` is honoured (precedence `--model`, `JEV_MODEL`, `config.json`, built-in default). `noul`, `choice` and `score` send the same payload as the original. `verify` sends the registry question `q.verify_claim.supported_by_evidence` (state keys `claim`, `evidence`, `evidence_source` instead of `context`; richer true/false criteria) and, unless `--no-companion`, an `evidence_is_raw` question in the same request; a `verify` with no evidence exits 6 because the registry marks evidence as required (the original sent it). A `noul` whose true and false criteria are equal or blank exits 6. Output line, flags and `--raw`/`--dry-run` are unchanged; the exit codes are the new ones (ADR-05).

**Rejected.** Renaming the command (breaks every running instruction); leaving `jev` unjournaled (gap G1 stays open).

## ADR-04 Capture at the adapter, not via hooks

**Context.** Hooks see Bash commands, but parsing shell text is fragile (9.4% of baseline outputs unparsable). The adapter sees the exact request and response.

**Decision.** Every call is written to the ledger by the adapter before the result is printed (write-ahead: row `EVALUATING` → updated with answer or error). Hooks are used for *enforcement and reminders*, never as the primary capture path. A PostToolUse(Bash) hook detects raw `curl ... openrouter.ai/api/alpha/decisions` and logs a `BYPASS` observation (not blocked: curl is legitimate for probes).

## ADR-05 Enforcement = CLI exit codes + PreToolUse permit gate; human approval = interactive terminal

**Context.** Plan §9: permissions enforced in code at the side-effect boundary. In Claude Code the side-effect boundary is the tool call; the only pre-execution hook is `PreToolUse` (exit 2 blocks, stderr goes to the model).

**Decision.**
1. `jade ask` exit codes: `0` ACCEPT band, `3` ABSTAIN band, `4` REJECT band, `5` PROVIDER_UNAVAILABLE, `6` INVALID_QUESTION (fails closed: 5 and 6 are never "yes").
2. A permit for `risk >= high` is created `PENDING` (by the gate when it blocks a call, or by `jade permit request --command <cmd>`; the library call `permits.issue(action, plan_hash, ttl_s=1800)` is for lower risk only and refuses high and critical). A `PENDING` permit becomes `ISSUED` only by `jade permit approve <id>` run by a human at an interactive terminal: stdin and stdout are a tty, no agent environment variable is set and no ancestor process is an agent (docs/05 T2 lists the check and its known hole). The agent cannot run it, and the human types a code taken from the plan hash instead of passing `-y`.
3. `PreToolUse(Bash|Write|Edit)` hook `jade hook pretool` classifies the command with `policies/risk.yaml`. For `risk >= high` it requires an `ISSUED`, unexpired, unused permit whose `plan_hash` matches the canonicalised command; otherwise exit 2 with the reason. Permits are single-use (nonce consumed in the same transaction).
4. Immutable guardrails in `policies/safety.yaml` (e.g., never bind a public address, never push to the default branch) block regardless of permits. Machine-specific hosts, paths, deploy scripts and retired services live in gitignored overlays `policies/local/*.yaml` (loader: `policies/loader.py`), so the shipped policies stay generic and public.

**Rejected.** Prompt-only control (the current state; measured failure 30%). Making Jev P part of the permit decision (plan invariant: model output never authorizes). Auto-approval by a second model.

**Consequences.** Low-risk work is unaffected (no permit needed). High-risk commands cost the human one terminal command; Claude Code's own permission prompt is a second, independent gate only when the host asks; in `bypassPermissions`, `auto`, headless and unattended-subagent runs JADE's gate is the only gate (docs/05 T18). TOCTOU: the hook recomputes `plan_hash` from the actual command at call time.

**Amendment 2026-11-09 (implementation).** The PreToolUse matcher is `Bash|PowerShell|Monitor|Write|Edit|MultiEdit|NotebookEdit|RemoteTrigger|mcp__.*` (`policy.GATED_BUILTIN`; review cluster A2). MCP tools are classified by name (`mcp_tools` in `policies/risk.yaml`); an MCP tool that carries a shell command (`command`, `cmd`, `script`) is classified by that command like Bash. Classification works per simple command: read-only commands (`read_only` list), quoted prose and heredoc bodies never match rules, which removed false positives such as `grep pfctl docs/`. Every command is first canonicalised by `jade/shellnorm.py` (git global options, escapes, quoting, command substitutions and shell-fed heredocs, pipes and here-strings), so a rule or invariant sees one spelling per operation (review cluster A1). A `jade gate advisory` mode records `would_block` events without blocking, for a soak before enforcement. `jade hooks install`, `jade gate`, `jade claude-md apply` and `jade permit approve` refuse to run for an agent session (SI-09).

**Amendment 2026-11-09 (review cluster B).** The permit is checked at use, not only looked up: the hook compares the permit's risk level, human principal and `tty:` reference with a fresh assessment of the command, and policy version, preconditions, session and agent, and the decision's state (docs/05 T6, T20). `plan_hash` hashes the command text as written and the resolved cwd. Approval needs a tty, no agent environment and no agent among the ancestor processes, shows the whole command, and takes a typed code instead of `-y`; the heuristic's known hole (own pty, clean environment, detached process) is documented in docs/05 T2 and tracked as W-38.

## ADR-06 Risk classification by precedence rules, not a blended score

**Decision.** `policies/risk.yaml` lists ordered rules; the first match sets `risk ∈ {low, medium, high, critical}` and a rationale string stored on the decision. Dimensions recorded separately: `reversibility`, `environment`, `blast_radius`, `data_sensitivity`, `permission_scope`. Precedence: critical patterns (prod deploy, firewall, destructive sudo, key material; overlay-defined router/deploy hosts) > high (external side effect such as push/publish/connected-app action, local destruction, service lifecycle, agent-policy edits, any remote shell) > medium (history rewrites) > low (commits, installs, downloads, everything else). Human decisions 2026-11-09 (Q3b): commits/checkouts and installs are low; any ssh/scp and any `composio execute` are high.

**Rejected.** Weighted score: hides which rule fired; the plan explicitly asked for conservative precedence.

## ADR-07 Calibration: per family, empirical bins, docs defaults until n ≥ 200, new model snapshot = new group

**Decision.** Calibration group = `(task_family, question_version, resolved_model)`. Default Noul bands from the docs: accept ≥ 0.70, reject ≤ 0.30, abstain between; critical-risk families accept ≥ 0.85 (configurable in `policies/escalation.yaml`). Thresholds may be auto-adjusted only when the group has ≥ 200 labeled outcomes, the Wilson 95% interval of the false-accept rate at the new threshold is below the family's budget, and the group's own labels are used (a new resolved model snapshot or question version is a new group with default thresholds; nothing is frozen, nothing is inherited). Choice: threshold on the chosen option's probability (docs: confidence is a transform of it), plus a companion Noul "does any option fit" for open sets. Score: never gates; it ranks.

**Rejected.** Isotonic/Platt now (no labels yet); a universal 95% target (plan §12 forbids inventing targets).

## ADR-08 Storage: SQLite, append-only ledger tables, policy in git

**Decision.** Tables: `decisions`, `jev_calls`, `permits`, `outcomes`, `work_items`, `improvements`, `events` (every state transition, with `prev_state`, `next_state`, `guard`, `actor`, `ts`). Updates to a decision write a new `events` row; the `decisions` row carries only the current state. Rows are never deleted: the only removal is `jade ledger purge`, which clears stored project text after the retention window and keeps every metric (docs/05 T15). Policy YAML lives in this repo; the ledger stores `policy_version` = the first 12 hex characters of a SHA-256 over the name and bytes of every `policies/*.yaml` file plus the machine-local `risk` and `safety` overlays at call time (not a git SHA, so it also changes for an uncommitted edit; it does not include the code version, W-50).

**Rejected.** JSONL files (no transactions for permit nonce consumption); tamper-evident hash chains (operator is trusted; threat is injection).

## ADR-09 Work items: JSON Lines validated by schema, rendered to BACKLOG.md

**Decision.** `work-items.jsonl` is the source; `BACKLOG.md` is generated by `jade work render` (also run by `jade work set` and `jade work sync`), which rewrites only the table below the `## Items` heading and keeps the hand-written text above it (`jade/work.py`, `tests/acceptance/test_work_items.py`). Every load and save validates the items against `schemas/work-item.schema.json`. Priority aging: P2→P1 after 14 days untouched, P3→P2 after 30 days, run on each `work` command; an item `in_progress` with no update for more than a day goes back to `open`. Resumption: the `SessionStart` hook prints open items whose `retry_or_revisit_trigger` is `on_session_start` or a date that has come. Not built: dependency-closed resumption and title de-duplication (W-51).

**Rejected.** GitLab issues for personal tooling (adds auth and network); task-observer's observation log (it is for skill findings, not work).

## ADR-10 Improvement plane reuses task-observer; JADE adds ledger-derived observations

**Decision.** Observations about *instructions* (CLAUDE.md, skills, question templates) are written to the task-observer log and go through its weekly review and staged-approval path. `jade improve mine` (Phase 4) turns ledger patterns (repeat questions, low-P→done, provider errors) into observation files and `improvement` records. Candidate changes to question templates are evaluated offline against the protected replay set before being staged. Nothing is auto-installed.

**Rejected.** A second review loop; auto-editing CLAUDE.md.

## ADR-11 Contracts: JSON Schema 2020-12, validated in tests and at runtime

**Decision.** Six schemas in `schemas/`, `additionalProperties: false`, `schema_version` on every record, examples in `schemas/examples/` validated by `tests/test_schemas.py`. Cross-field invariants (permit expiry > issue, outcome `success` null iff `observation_type=unknown`, decision status vs permit presence) are asserted in Python, not JSON Schema. Migration rule: additive fields bump minor; removals bump major and ship a migration function.

## ADR-12 Scope cuts

Single tenant: one configured `tenant_scope` value `local:<user>`; no HYBRID runtime route (skill pattern); no VOI heuristic until data; no services; no SKILL.md until design approval; no auto-promotion before Phase 4 opt-in. Each cut is a BACKLOG item with a reopening trigger.
