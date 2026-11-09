# JADE — Adaptive Decision Governance for AI agents

[![CI](https://github.com/vahidkaargar/jade-decision-governance/actions/workflows/ci.yml/badge.svg)](https://github.com/vahidkaargar/jade-decision-governance/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

JADE is a design and a small Python runtime for one problem: **an AI coding agent that asks a decision model "is this right?" and then acts on the answer as if it were permission.**

It governs how agents (Claude Code first) use [TypeSafe Jev](https://docs.typesafe.ai), a System One decision model that returns typed probabilities, and when they may act on the result:

- every model call is journaled, with typed errors that never count as "yes";
- a probability maps to accept / abstain / reject through per-task-family thresholds, with abstention as a real exit code;
- side effects above a risk level need a single-use **execution permit** that only a human at a terminal can approve;
- improvement of prompts and thresholds is measured offline and cannot touch its own safety rules.

> **Guiding invariant.** No model output (the decision model, a reasoning model, an evaluator, or an improvement agent) can authorize a side effect, weaken protected policy, declare an unobserved outcome successful, or bypass a human approval boundary.

## Status

| Stage | State |
|-------|-------|
| DESIGN_READY | **Yes** — design approved by the maintainer 2026-11-09 |
| IMPLEMENTED | **Yes** — `jade` 0.2.1: ledger, adapter, redaction, `jev` shim, question registry, permits, hook gate, calibration, bounded improvement loop; Phases 1–5 acceptance tests pass. 0.2.1 is the release that closes the first independent adversarial review (see `CHANGELOG.md`); the residual risks it leaves are listed in `docs/05-threat-model.md` and tracked in `BACKLOG.md` |
| VALIDATED | No — needs 14 days of real ledger data, labelled outcomes and one full improvement cycle (`docs/08` section 5) |
| PRODUCTION_APPROVED | No — a human decision (`docs/08`) |

What exists today: the design documents, the runtime, six JSON Schema contracts, five policy files with a tested loader, a 10-entry question registry, replayable regression and adversarial cases (`evals/run_cases.py`), the baseline and performance tooling, and the phase acceptance tests. What does not exist yet is listed in `BACKLOG.md` (for example the routing and judgment eval sets, W-49, and several metrics of `docs/04` section 7, W-52); the documents mark each of those as planned. The gate is a syntactic classifier with known residual risks (`docs/05` T2, T3, T18, T19, T22): it is a strong guard rail for an agent that makes mistakes, not a boundary against one that is adversarial and runs as the same user. Wiring it into an agent harness is deliberately a human step: see `docs/08-deployment-runbook.md`.

## Why: the measured baseline

Before designing anything, the current practice was measured on one developer machine: a rule in the agent instructions ("run `jev verify` before presenting work as done; a low probability means investigate") and a 124-line CLI with no ledger.

| Over 14 days, 907 real calls | |
|---|---|
| Verifications below the vendor's suggested 0.70 "yes" line | 57% |
| Of those, followed by a done/verified claim in the next message (heuristic) | 30% |
| Calls that re-asked an identical question in the same session | 20% |
| Calls with an error or unparsable result, none of which blocked anything | 9.4% |
| Latency, one question or eight batched | ≈ 500 ms either way |

A prose rule with no threshold and no enforcement point does not hold. Details and method: `docs/00-inventory-and-gaps.md`, `docs/04-calibration-and-evaluation.md`.

## Read in this order

| # | File | What it settles |
|---|------|-----------------|
| 0 | `docs/00-inventory-and-gaps.md` | Verified facts about the Jev API as reached through OpenRouter, unknowns, baseline, gap register, what the original plan oversized |
| 1 | `docs/01-adrs.md` | 12 architecture decisions with rejected alternatives |
| 2 | `docs/02-contracts-and-state-machine.md` | Decision lifecycle, transition guards, the six contracts |
| 3 | `docs/03-routing-and-question-design.md` | Preflight, FAST / JEV / DEEP routing, question registry, batching, no-valid-option handling |
| 4 | `docs/04-calibration-and-evaluation.md` | Thresholds, calibration groups, labels, benchmark protocol, budgets derived from the baseline |
| 5 | `docs/05-threat-model.md` | Threats, mitigations, what a Markdown skill can and cannot enforce |
| 6 | `docs/06-roadmap.md` | Phases 1–5, acceptance tests, rollout and rollback |
| 7 | `docs/07-self-improvement-and-work-management.md` | Bounded improvement loop, durable work items, human-only boundaries |
| 8 | `docs/08-deployment-runbook.md` | What is installed, the human-only wiring steps, rollback, coverage limits, the path to VALIDATED |
| — | `BACKLOG.md` | Every open item: owner, priority, dependency, trigger, next action |
| — | `docs/JADE_Claude_Code_Master_Plan.md` | The original handoff draft this repository completes |

## Layout

```
jade/             the runtime package (CLI `jade`, shim `jev`)
registry/         question registry, one JSON file per question version
skills/jade/      SKILL.md: how agents should use it
docs/             design documents and the deployment runbook
schemas/          JSON Schema 2020-12: decision, question, permit, outcome, work-item, improvement (+ examples)
policies/         risk.yaml routing.yaml escalation.yaml safety.yaml promotion.yaml, loader.py
policies/local/   machine-specific overlays (gitignored) + committed *.example.yaml
evals/            baseline miner, policy replay, regression + adversarial cases, live performance harness
adapters/jev/     OpenRouter Decisions API probe
tests/            contract, policy and overlay tests; tests/acceptance = the Phase 1–5 acceptance checklist
```

## Install and use

Requires [uv](https://docs.astral.sh/uv/), Python 3.13+, and an OpenRouter key in the macOS Keychain (service `OPENROUTER_API_KEY`) or the environment.

```bash
uv tool install .            # installs `jade` and the `jev` compatibility shim
jev verify "The suite passed" --state-file test-output.txt
echo $?                      # 0 accept, 3 abstain, 4 reject, 5 provider unavailable, 6 invalid question
jade check                   # probes ledger, policies, registry, hooks and the provider
jade ledger stats            # what your agents actually did
```

Only exit code 0 is a yes. Enforcement of risky tool calls is opt-in and human-wired: `docs/08-deployment-runbook.md`.

## Develop

Requires uv and Python 3.13+.

```bash
uv sync
uv run pytest -q                      # contract, policy, overlay and phase acceptance tests
uv run python evals/run_cases.py      # regression + adversarial case replay
uv run ruff check .
```

Classify a command with the shipped risk rules:

```bash
uv run python -c "from policies.loader import load_risk, classify; r = classify(load_risk(), 'git push origin feature/x'); print(r['id'], r['level'], '-', r['rationale'])"
```

Measure your own baseline, if you already use the `jev` CLI from Claude Code (reads `~/.claude/projects`; the dataset holds your project text and stays local):

```bash
uv run python evals/baseline/mine_transcripts.py --dataset evals/baseline/dataset-local.jsonl > summary.json
uv run python evals/replay_policy.py evals/baseline/dataset-local.jsonl
```

Live probes need an OpenRouter key in the macOS Keychain (service `OPENROUTER_API_KEY`) or the `OPENROUTER_API_KEY` environment variable. They send only a synthetic fixture and cost well under one cent:

```bash
uv run python adapters/jev/probe_openrouter.py
uv run python evals/performance/run.py --rounds 3
```

## Make the policies yours

The shipped `policies/risk.yaml` and `policies/safety.yaml` are generic. Your production hosts, deploy scripts and retired services go in gitignored overlays:

```bash
mkdir -p ~/.config/jade/local
cp policies/local/risk.local.example.yaml   ~/.config/jade/local/risk.local.yaml
cp policies/local/safety.local.example.yaml ~/.config/jade/local/safety.local.yaml
```

(For development from a checkout, `policies/local/*.local.yaml` is gitignored; the installed tool reads `~/.config/jade/local/`.)

Overlays can add hosts, paths, rules and invariants. They cannot remove a shipped invariant or lower a shipped high-risk rule (`policies/loader.py`, `tests/test_policy_overlay.py`).

## Facts worth knowing before you build on Jev via OpenRouter

Verified 2026-11-08 (`docs/00`, source-of-truth table):

- The endpoint is `POST https://openrouter.ai/api/alpha/decisions`; the request id `typesafe/jev-1.14` resolves to a dated snapshot, and `typesafe/jev-latest` does not exist there.
- Several independent questions fit in one request and cost no extra latency.
- A Choice with a single option is accepted and returns confidence 1.0, so candidate validity must be checked client-side.
- Confidence describes how concentrated the answer distribution is. It is not the probability of being right, and it is not permission.

## Contributing, security, license

See `CONTRIBUTING.md` and `SECURITY.md`. MIT licensed. JADE is an independent project and is not affiliated with TypeSafe, OpenRouter or Anthropic.
