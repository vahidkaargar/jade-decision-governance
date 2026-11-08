# 00 — Inventory, verified facts and gap register

Date: 2026-11-08. Environment: a single developer Mac (hostname and user redacted for the public repo), Claude Code desktop app. Everything below was checked in this environment unless marked otherwise.

## 1. Source-of-truth table

| # | Claim | Source | Version / date | Status | Implication |
|---|-------|--------|----------------|--------|-------------|
| S1 | Jev is reached at `POST https://openrouter.ai/api/alpha/decisions`, bearer key from Keychain `OPENROUTER_API_KEY` | `~/.local/bin/jev` lines 16–30; live call 2026-11-08 returned HTTP 200 | CLI undated, 124 lines | verified | Transport works; the endpoint is OpenRouter "alpha", undocumented on openrouter.ai (3 doc URLs 404). Provider drift risk. |
| S2 | Model id sent is `typesafe/jev-1.14`; server resolves it to `typesafe/jev-1.14-20261014` | live response `"model": "typesafe/jev-1.14-20261014"` | 2026-11-08 | verified | Record the resolved string per call; the request id is a moving alias. |
| S3 | `typesafe/jev-latest` and `typesafe/jev-1.14.0` do **not** exist on OpenRouter; `typesafe/jev-1.13` does (`jev-1.13-20260917`) | `adapters/jev/probe_openrouter.py` run 2026-11-08: HTTP 400 "Model ... does not exist" | — | verified | Never hardcode `jev-latest` (the master plan's warning confirmed). Official ids differ from OpenRouter ids. |
| S4 | Official API is `POST https://api.typesafe.ai/v1/systemone`; official ids `jev-1.14.0`, `jev-1.13.0`, aliases `jev-latest`, `jev-preview` | docs.typesafe.ai/api.md, models.md (fetched 2026-11-08) | Jev 1.14 "available from October 14, 2026" | verified (docs) | Not used here (no TypeSafe key). Adapter must be swappable. |
| S5 | Primitives: `noul`, `choice`, `score`, `box`. There is no `verify` primitive; the CLI's `verify` is a Noul with fixed criteria | api.md; `jev` source `build_question` | — | verified | "verify" is a JADE question template, not an API type. |
| S6 | Response: `answers.{name}` with `noul` (0–1) / `choice`+`probabilities`+`confidence` / `score`+`legend`+`probabilities`+`confidence`; `usage.input_tokens/output_tokens`; OpenRouter adds `usage.cost`, `id` | live responses | — | verified | Ledger schema fields fixed from this. |
| S7 | Several independent questions run in one request against one state | api.md; live batch of 3 questions: 200 in 0.68 s | — | verified | Batching is available and unused by the CLI (single `q`). |
| S8 | Confidence = distribution concentration, not correctness; Noul has no confidence; docs suggest Noul thresholds 0.7 yes / 0.3 no with 0.3–0.7 "can go to a person"; "thresholds scale with risk"; validate in your domain | confidence.md, primitives/noul.md | — | verified (docs) | Default abstention band 0.3–0.7 until per-family calibration data exists. |
| S9 | Server does not reject a degenerate Choice (one option) — returns that option with confidence 1.0 | probe: `ERR1opt 200 ... "confidence": 1` | — | verified | Candidate validity must be enforced client-side (plan §3 "do not force a choice"). |
| S10 | Pricing $0.042 per million input tokens, output free; observed cost ≈ $1.3e-5–1.7e-5 per call | models.md; `usage.cost` | — | verified | 907 baseline calls cost ≈ $0.013. Cost is not a constraint; latency and correctness are. |
| S11 | Latency: single Noul 0.48–0.54 s (n=4), batch of 3 0.68 s, p99 unknown | probe | — | measured, small n | Hot-path budget: one Jev round trip adds ~0.5 s. |
| S12 | Limits: 64k tokens/request, 32k/question (headers raise to 262k); 32 MiB body; errors 400/401/413/415/422/429/529, back off on 429/529; rate limits per org in console | api.md, api/errors.md | — | verified (docs) | Retry policy: exponential backoff on 429/529 only; never retry 4xx. Idempotency key: UNKNOWN (not documented). |
| S13 | CLI has no logging, no retries, 60 s timeout, state always `{"context": "<string>"}`, one question per call, exits non-zero on HTTP/URL errors, prints `jev: ...` | `jev` source | — | verified | No ledger exists. Everything in §3 was reconstructed from Claude Code transcripts. |
| S14 | Installed skill `typesafe-ai` plugin v0.5.7 (MIT) is docs-pointer only: SKILL.md + LICENSE, no scripts, no hooks | `~/.claude/plugins/cache/typesafe-ai/typesafe/0.5.7/` | 0.5.7 (upstream at v0.6.2, 2026-10-23) | verified | Safe to keep. Not a runtime control. |
| S15 | No hook, rule or other skill references Jev; the only instruction is `~/.claude/CLAUDE.md` §"Jev: verify outputs and decide" | grep of settings.json, hooks dir, CLAUDE.md, rules | — | verified | The CLAUDE.md paragraph *is* the current "integration". |
| S16 | Community repos: see §4 | GitHub, fetched 2026-11-08 | — | verified (README level only) | None adopted as code. |
| S17 | Hook conventions (Claude Code): `~/.claude/settings.json` → scripts in `~/.claude/hooks/` (bash/python/node); PreToolUse exit 2 blocks with stderr to the model; PostToolUse/Stop/SessionStart in use; LaunchAgents `com.<user>.*` for background work; `graph-refresh` is the precedent for CLI + hook + launchd + `check` | settings.json, `skills-discovery-guard.sh`, `graph-refresh` | — | verified | JADE runtime follows these exactly. |
| S18 | Skills convention: source dir `~/.agents/<name>/`, symlinked into `~/.claude/skills/<name>`; discovery via `skillfind` (index at 127.0.0.1:3333) | readlink of task-observer, mac-mini-it-server | — | verified | Skill ships later as `~/.agents/jade/`. |
| S19 | Python 3.15.2 system, `uv` present; no `jsonschema`/`pytest` in system Python; SQLite 3.54 | shell | — | verified | Runtime = Python + stdlib SQLite; dev deps pinned by `uv`. |
| S20 | task-observer v3.4.0 already runs an observe → review → stage → approve loop for skills with a durable observation log at `~/.claude/skill-observations/` | CLAUDE.md, skill scan this session (14 open observations) | 3.4.0 | verified | JADE's improvement plane for *instructions* reuses it instead of building a second loop. |
| S21 | Claude Code transcripts under `~/.claude/projects/**/*.jsonl` contain every Bash `tool_use` and `tool_result`, so historic Jev calls can be mined | `evals/baseline/mine_transcripts.py` | 6,473 files | verified | Baseline dataset exists (§3). |

### 1b. Deliverable-1 checklist (where each required item lives)

| Required by plan deliverable 1 | Where | Finding |
|-------------------------------|-------|---------|
| Exact Jev API version | S1, S4 | OpenRouter `/api/alpha/decisions` (undocumented alpha); official `api.typesafe.ai/v1/systemone` not used |
| Exact model version | S2, S3 | Requested `typesafe/jev-1.14`; resolved `typesafe/jev-1.14-20261014`; `jev-1.13-20260917` also available; `jev-latest` invalid on OpenRouter |
| Skill files | S14, S18, §5 | One installed Jev-related skill (`typesafe-ai` 0.5.7, docs pointer only); no JADE skill yet |
| Hook behavior | S15, S17 | No hook touches Jev today; hook conventions documented for the Phase 2 gate |
| Call sites | S13, S15, §3 | Exactly one: agents invoking `~/.local/bin/jev` from Bash because `~/.claude/CLAUDE.md` tells them to. No code path, library, or service calls Jev. 907 such invocations in 14 days. |
| Failure handling | S13, §3 | CLI exits non-zero with `jev: HTTP <code>` / `jev: request failed` / `jev: no key`; no retry, no logging; the calling agent is free to continue, so every failure is fail-open. 85 calls (9.4%) produced an unparsable or error result (78 unparsable, 6 usage errors, 1 timeout). |
| Available benchmark data | S21, §3 | None existed; `evals/baseline/` was built from transcripts in this phase (heuristic labels), plus live probes for latency/cost and `evals/baseline/replay-2026-11-08.json` for what the proposed thresholds would have done |

## 2. Assumptions and unknowns (recorded, not invented)

| Id | Item | Why it matters | Resolution path |
|----|------|----------------|-----------------|
| U1 | OpenRouter `/api/alpha/decisions` stability, SLA, deprecation | Hot path depends on it | Adapter interface + canary probe (`jade check`) that fails loud; fallback adapter for api.typesafe.ai requires a TypeSafe key (human). |
| U2 | Whether OpenRouter honours an idempotency header or exposes `retry-after` | Retry safety | Probe on first 429; until then: retry only on 429/529, max 2, jittered; Jev calls have no side effects, so duplicates cost only money/latency. |
| U3 | Calibration of Jev on *this* task distribution (agent self-verification claims about code/ops) | Thresholds | Phase 3 labeled set; docs give only generic 0.3/0.7. |
| U4 | Language behaviour (Persian state text) | Agent on this Mac handles Persian content | Subgroup slice in evals; UNKNOWN in docs. |
| U5 | p99 latency and 429 behaviour under 10+ parallel calls | Batching/parallel design | `evals/performance` harness, run before Phase 5. |
| U6 | Whether the user wants a TypeSafe direct key (official endpoint) | Removes U1 | Human decision (BACKLOG W-03). |
| A1 | Single tenant: one human, one Mac, personal Keychain | `tenant_scope` constant `local:<user>`; cross-tenant controls reduced to "no secrets in state" | Revisit if JADE runs on the the production servers. |
| A2 | Claude Code is the only caller today; Cursor/Codex may follow | Enforcement lives in CLI exit codes first, Claude Code hooks second | Hooks are per-harness; CLI gate is harness-neutral. |

## 3. Baseline measured from transcripts (2026-10-26 → 2026-11-08)

Method: `evals/baseline/mine_transcripts.py` pairs each Bash `tool_use` whose command contains `jev verify|noul|choice|score` with its `tool_result` and the next assistant text. Dataset: `evals/baseline/dataset-2026-11-08.jsonl` (gitignored, contains project text); summary: `evals/baseline/summary-2026-11-08.json`. Heuristics are regex-based; treat the acceptance numbers as upper/lower bounds, not labels.

| Metric | Value | Reading |
|--------|-------|---------|
| Transcript files scanned / with Jev calls | 6,473 / 379 | Jev used in ~6% of transcripts (many files are subagents) |
| Real calls (excl. 12 `--dry-run`) | 907 over 14 days ≈ 65/day | Median 2 calls per file, max 56 |
| Primitive mix | verify 684 (75%), choice 223 (25%), noul-custom 0, score 0 | Two of four primitives never used; `verify` template dominates |
| Parsed results | P(yes): 640 · choice: 182 · error/unparsed: 85 (9.4%) | 78 "no_parse" = output not matching any pattern (short outputs ~31–36 chars, likely shell/quoting errors) |
| P(yes) distribution | mean 0.60, median 0.64, p10 0.25, p90 0.88 | Bands: ≥0.85: 17% · 0.7–0.85: 26% · 0.5–0.7: 27% · 0.3–0.5: 17% · <0.3: 13% |
| Share of verify results below the docs' 0.7 "yes" line | 365/640 = **57%** | Most verifications land in or below the abstention band |
| Low-P (<0.7) calls followed by a done/verified/fixed claim in the next assistant message | 109/365 = **30%** (heuristic) | Includes P values as low as 0.02. This is the "accepting ~60% outputs" problem, measured. |
| P reported to the user in the next message (CLAUDE.md rule) | 339/462 = 73% | 27% silent |
| Repeated identical question within one session | 177 calls (20%) across 84 questions; one Choice asked 6× | Re-prompting instead of gathering evidence |
| Choice confidence seen | mostly 0.8–1.0 | Options often shaped so one dominates |
| State passed via stdin (`-s -`) | 23 (2.5%) | 97% pass evidence inline on the command line — truncated, quoting errors (`unrecognized arguments` 6×) |
| Batching | 0 | CLI cannot |
| Failures | 85 calls: 78 unparsable output, 6 CLI usage errors (shell quoting), 1 network timeout; a raw text search of the transcripts also shows `no key` and `No such file or directory` lines from the first days | Fail-open: a failed call never blocked anything |
| Cost | ≈ $0.013 for all 907 calls | Not a constraint |

Baseline latency and cost budgets derived from these numbers are in `docs/04-calibration-and-evaluation.md` §6.

## 4. Third-party skill candidates (reverified 2026-11-08, README level)

| Repo | Last commit | License | What | Verdict |
|------|-------------|---------|------|---------|
| typesafe-ai/skills | 2026-10-23 (v0.6.2) | MIT | Official docs-pointer skill; installed here as plugin 0.5.7 | Keep as docs pointer. Update to 0.6.2 is a BACKLOG item (W-12). |
| dbreunig/building-with-jev-skill | 2026-10-29 | none | Skill + references for writing Jev programs | Do not install (no license). Patterns already covered by official docs. |
| KHAEntertainment/jev-skill | 2026-10-03 (0.1.1) | MIT | Skill, templates incl. a fail-closed Claude Code PreToolUse gate template, `jev_probe.py` (live calls) | Do not install. **Adopt the idea**: fail-closed gate as a hook; JADE writes its own. |
| raphael-liu/jev-skill | 2026-09-21 | MIT | Skill + Python client, no retries, pins jev-1.13.0 | Do not install; superseded by our adapter. |
| eran-broder/jev-skills | 2026-09-23 | MIT | Hooks on UserPromptSubmit and after Read/Bash/Grep/WebFetch send prompt + tool output to the TypeSafe API; fails open; rewrites `~/.codex/hooks.json`; moves `~/.claude/skills/*` | **Reject.** Exfiltrates tool output, fails open, modifies global config. |

No third-party code enters this repo. Vetted patterns extracted: fail-closed gate, batching, companion-Noul for "does anything fit", high initial auto-accept with human review.

## 5. Existing skills that overlap

| Skill | Overlap | Decision |
|-------|---------|----------|
| `task-observer` (global, v3.4.0) | Observe → log → weekly review → staged, approved change to skills | JADE improvement plane *for instructions* = task-observer. JADE adds only decision-ledger-derived observations. |
| `decision-logger`, `decide`, `hard-call` (C-suite pack) | Human business decisions, `~/.claude/decisions/` (dir does not exist; never used) | Different domain. Do not merge. |
| `ship-gate` | Pre-deploy audit, intercepts deploy intent | Complementary: a JADE permit for `release` actions may require ship-gate PASS as a precondition (Phase 2). |
| `self-improving-agent` | Curates MEMORY.md → rules/skills | Adjacent; JADE does not write memory. |
| `typesafe-ai` plugin | Question-design guidance | Referenced from the JADE skill, not duplicated. |

## 6. Gap register (what the current setup lacks)

| Gap | Evidence | Closed by |
|-----|----------|-----------|
| G1 No ledger: calls, results, dispositions unrecorded | S13; baseline had to be mined from transcripts | Phase 1 ledger (SQLite) written by the adapter itself |
| G2 No abstention: a P of 0.02 can precede "done" | §3: 109 cases | Phase 2 `jade` exit codes + Stop-hook reminder; thresholds in `policies/escalation.yaml` |
| G3 No authorization boundary; Jev P treated as permission | CLAUDE.md wording "before presenting ... as verified"; no gate | Phase 2 permits enforced by PreToolUse hook for risky commands |
| G4 Fail-open on error/no key | S13 | Adapter returns typed `UNAVAILABLE`; gate treats it as not-approved |
| G5 Evidence truncated on the command line | 97% inline `-s`, 6 quoting failures | `jade ask --state-file` / stdin default; skill instructs file-based state |
| G6 Re-asking to raise confidence | 20% repeats | Dedup by `context_fingerprint` within session; repeat without new evidence returns the cached answer and flags |
| G7 Only two of four primitives; no batching; no no-match option | S5, S7, §3 | Question registry with templates per task family |
| G8 No calibration data | U3 | Phase 3 labels from outcome records |
| G9 No model pinning record | S2 | Ledger stores resolved model string |
| G10 Nothing measures routing (when Jev should/shouldn't be called) | — | Phase 3 routing eval set |
| G11 Work items live in chat | — | `work-item` schema + `BACKLOG.md` |

## 7. Challenge to the master plan (what to cut or change)

| Plan element | Verdict | Reason |
|--------------|---------|--------|
| Three planes as separate services, outbox/queues, Redis/MySQL | **Cut to one Python package + SQLite + launchd** | Single-user Mac; 65 calls/day; graph-refresh precedent shows CLI+hook+launchd is enough (ADR-01, ADR-08) |
| `tenant_scope`, cross-tenant leakage controls | Keep the field as a constant; drop the machinery | A1 |
| HYBRID as a fourth runtime route | Make it a skill pattern, not a route | Decomposition is done by the reasoning model; the runtime sees atomic JEV questions |
| Expected-value-of-information heuristic | Defer until ≥200 labeled rows per family | Plan itself says so; no data yet |
| Isotonic/Platt calibration | Defer; start with per-family empirical bins and the docs' 0.3/0.7 band | Sparse data; CI widths matter more than method |
| Auto-promotion of improvement candidates | Phase 4 only for question wording and cache TTL, and only after human opt-in | Protected targets must stay human |
| Signed/tamper-evident logs | Append-only SQLite + git-tracked policy files is enough | Threat model §05: the adversary is injection, not a malicious operator |
| A SKILL.md now | Not yet | Plan deliverable 5 requires design validation first (BACKLOG W-01) |
| Persistent background autonomy for blockers | Record the limitation | Claude Code has no resident agent; launchd can run `jade` maintenance, not agent reasoning. Work items are resumed by the next session's SessionStart hook. |
