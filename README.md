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
| IMPLEMENTED | **Yes** — `jade` 0.5.0: ledger, adapter, redaction, `jev` shim, question registry, permits, hook gate, calibration, bounded improvement loop, standalone pre-push guard (`jade git-hook`); Phases 1–5 acceptance tests pass. 0.2.1 closed the first independent adversarial review and 0.2.2 the false-positive pass and the structural fix for "never push to the default branch" (see `CHANGELOG.md`). The command classifier is defence in depth, not the enforcement of that rule: the enforcement is the pre-push guard and branch protection on the remote, which only a human can install (`docs/08-deployment-runbook.md` section 2.7, W-68, W-69). The residual risks are listed in `docs/05-threat-model.md` and tracked in `BACKLOG.md` |
| LIVE (enforce) | **Yes, on the reference machine: advisory from 2026-11-09, enforce with the signed approver since 2026-11-13** — the four hooks are installed, the gate blocks high and critical calls that have no permit, a permit needs a signature only the `_jadeapprove` account can make, the daily maintenance job is loaded, the pre-push guard and the remote branch ruleset protect `main` |
| VALIDATED | No — needs 14 days of real ledger data, labelled outcomes and one full improvement cycle (`docs/08` section 5); earliest 2026-11-27 (14 days of enforce) |
| PRODUCTION_APPROVED | No — a human decision (`docs/08`) |

Signed permit approval (W-38, W-89) is merged: with the approver installed, a high or critical permit is approved by a signature only a separate OS account can make, and `jade gate enforce` refuses without it. The installer has not yet been run on a second machine; treat it as new (`docs/10`, `docs/11`).

Hardening batch 1 (merged 2026-11-09, `CHANGELOG.md` Unreleased) added the wiring checks: `jade check` now reports shadowed or disabled hooks in project, managed and plugin settings, lints the private overlay, compares the installed code with the checkout, validates permit and improvement rows, and the permit seal key is protected against Read, Grep and Glob as well as shell. After pulling a change like this, re-install the tool and, when the changelog says the hook matcher changed, re-run `jade hooks install` yourself (an agent cannot: SI-09).

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
| 9 | `docs/09-install-and-upgrade.md` | Versioning policy, the upgrade contract, the release process, supported platforms, troubleshooting by `jade check` row, full uninstall |
| 10 | `docs/10-out-of-band-permit-approval.md` | Signed permit approval: an approval a same-user agent cannot synthesise (implemented; the human installs the approver, `scripts/install-approver.sh`) |
| 11 | `docs/11-release-0.4.0-plan.md` | The plan for the first enforce-ready release: gates in order, what is human-only, rollback |
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

## Quick start (Claude Code)

This release is **enforce-ready**: it installs in advisory mode (records what it would have blocked, blocks nothing), and after you install the approver (`scripts/install-approver.sh`, `docs/10`) `jade gate enforce` makes the gate block. It is not VALIDATED and not production approved (see Status). Every step that wires JADE into Claude Code is a human step in your own terminal; an agent cannot run it for you.

**Requirements**

- macOS or Linux (macOS is the tested platform; the maintenance job helper is macOS only, see `docs/09-install-and-upgrade.md` section 5)
- Python 3.13 or newer and [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://docs.claude.com/en/docs/claude-code) (JADE installs its hooks in `~/.claude/settings.json`)
- An OpenRouter key for the Jev calls: in the macOS Keychain (service `OPENROUTER_API_KEY`, account `$USER`) or in the `OPENROUTER_API_KEY` environment variable of the shell that starts Claude Code. The gate and the hooks work without a key; `jev` and the live `jade check` canary need it. `jade setup` never asks for the value and never prints it

**1. Install** (pinned to a release tag; installs `jade` and the `jev` compatibility shim)

```bash
uv tool install git+https://github.com/vahidkaargar/jade-decision-governance@v0.5.0
jade --version
```

If `uv` answers `Executable already exists: jev`, an older `jev` script sits in uv's tool bin directory and nothing was installed (not even `jade`). Move it aside (`mv ~/.local/bin/jev ~/.local/bin/jev.orig`) and run the install again, or add `--force` to overwrite it.

**2. Wire it into Claude Code**

```bash
jade setup --status      # read-only: what is done and what is missing
jade setup               # asks y/N before each change: advisory gate, hooks, skill, maintenance job, and the Jev section of an existing CLAUDE.md
```

`jade setup` needs an interactive terminal and refuses inside an agent. It backs up `settings.json` and `CLAUDE.md` before it edits them, and it never sets enforce mode. Open a new Claude Code session afterwards: hooks are read at session start.

**3. Verify**

```bash
jade check --offline     # every row ok; a non-zero exit means dead wiring (docs/09 section 6 explains each row)
jade check               # the same plus the live provider canary: it needs your OpenRouter key and fails with NO_KEY without one, which is not dead wiring
```

**4. First use**

```bash
jev verify "The suite passed" --state-file test-output.txt
echo $?                  # 0 accept, 3 abstain, 4 reject, 5 provider unavailable, 6 invalid question
jade ledger stats        # what your agents actually did, including the would_block and safety_block events
```

Only exit code 0 is a yes. Give `jev` raw evidence (a log, a diff, a command's output), not your own summary. Claude Code finds out about `jev` from the skill `jade setup` installs. `jade setup` only replaces the `## Jev` section of an existing `~/.claude/CLAUDE.md`; when you have none, or none with that heading, it skips the step and you add the output of `jade claude-md show` to your CLAUDE.md yourself.

**What advisory mode does and does not protect**

- It does: journal and redact every Jev call, map a probability to accept, abstain or reject, record a `would_block` event (`safety_block` for a safety invariant such as a push to the default branch) for every call the gate would stop in enforce mode and print `[advisory, not blocked]` with the reason, remind the agent at Stop about an abstain or reject it has not dealt with, and refuse to let an agent approve its own permit.
- It does not: block any command, edit or tool call. A `git push` to the default branch, `terraform destroy` or `rm -rf` runs as it would without JADE. The gate is also a syntactic classifier with known gaps (`docs/05-threat-model.md` T18, T19, T22): a guard rail for an agent that makes mistakes, not a boundary against one that is hostile and runs as your user.
- Pushes to the default branch are stopped by git and the remote, not by JADE's shell reading: install the pre-push guard and turn on branch protection (`docs/08-deployment-runbook.md` section 2.7).
- Moving to enforce mode (`jade gate enforce`) is your decision after you have read `jade ledger stats` and `jade decide list` for a week or more (`docs/08-deployment-runbook.md` section 2.2).

**Upgrade** (in your own terminal; the ledger, your overlays, `settings.json` and `CLAUDE.md` are not touched)

```bash
jade upgrade --check     # installed against latest release, plus the CHANGELOG headings in between (read-only)
jade upgrade             # installs the latest release with uv, then runs jade check from the new install
```

Re-run `jade hooks install` and open a new Claude Code session only when the output says a hook row failed. After an upgrade `jade setup --status` shows whether the shipped skill or CLAUDE.md text changed. Details and the upgrade contract: `docs/09-install-and-upgrade.md`.

**Roll back and uninstall**

```bash
jade gate advisory       # stop blocking, keep recording
jade hooks uninstall     # remove JADE's hook entries from ~/.claude/settings.json
jade upgrade --ref vX.Y.Z   # install an older release (reported as a rollback)
```

Full rollback table: `docs/08-deployment-runbook.md` section 3. Complete removal, step by step, in the right order: `docs/09-install-and-upgrade.md` section 7.

**Report a problem**

Open an issue at https://github.com/vahidkaargar/jade-decision-governance/issues with the output of `jade version` and `jade check --offline` (check the text for paths you do not want to share), the command you ran and what you expected. A bug that lets an agent act without a human approval, or leaks text redaction should have removed, goes to a private security advisory instead: `SECURITY.md`.

More commands, in a checkout or installed:

```bash
jade version             # code version, commit, ledger schema version, policy_version (also `jade --version`)
jade setup --status      # what setup has and has not done
```

Never pushing to the default branch is enforced by git and the remote, not by reading shell: `jade git-hook script` prints a standalone pre-push guard, `jade git-hook check <repo>` says whether a repository runs it, and branch protection on the remote covers every other client. Both are installed by a human (`docs/08-deployment-runbook.md` section 2.7). The rest of the manual wiring and each undo are in `docs/08-deployment-runbook.md`.

When testing JADE itself, never feed it synthetic dangerous commands through the installed hook: run every `jade` invocation with `JADE_HOME=$(mktemp -d)` so test permits and decisions stay out of your real ledger (backlog W-72).

## Develop

Requires uv and Python 3.13+.

```bash
uv tool install .                    # install your checkout instead of a release (`uv tool install --reinstall .` after changes)
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
# run from a checkout of this repository; the same two example files are in the installed package (`policies/local/` in its site-packages directory)
mkdir -p ~/.config/jade/local
cp policies/local/risk.local.example.yaml   ~/.config/jade/local/risk.local.yaml
cp policies/local/safety.local.example.yaml ~/.config/jade/local/safety.local.yaml
```

(For development from a checkout, `policies/local/*.local.yaml` is gitignored; the installed tool reads `~/.config/jade/local/`.)

Overlays can add production hosts and paths (`environments`) and rules (`rules_prepend`, `rules_override`) and invariants (`invariants_append`). A top-level `hosts:` key is not read; to make a host critical, write an `R-Lxx` rule for it. `jade check` lints the overlay (rows `overlay lint`, `overlay files`) and names the file and rule, never the pattern or host. They cannot remove a shipped invariant or lower a shipped high-risk rule (`policies/loader.py`, `tests/test_policy_overlay.py`).

## Facts worth knowing before you build on Jev via OpenRouter

Verified 2026-11-08 (`docs/00`, source-of-truth table):

- The endpoint is `POST https://openrouter.ai/api/alpha/decisions`; the request id `typesafe/jev-1.14` resolves to a dated snapshot, and `typesafe/jev-latest` does not exist there.
- Several independent questions fit in one request and cost no extra latency.
- A Choice with a single option is accepted and returns confidence 1.0, so candidate validity must be checked client-side.
- Confidence describes how concentrated the answer distribution is. It is not the probability of being right, and it is not permission.

## Contributing, security, license

See `CONTRIBUTING.md` and `SECURITY.md`. MIT licensed. JADE is an independent project and is not affiliated with TypeSafe, OpenRouter or Anthropic.
