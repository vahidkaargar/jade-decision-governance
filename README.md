# JADE — Adaptive Decision Governance for AI agents

[![CI](https://github.com/vahidkaargar/jade-decision-governance/actions/workflows/ci.yml/badge.svg)](https://github.com/vahidkaargar/jade-decision-governance/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

JADE is a design and a small Python runtime for one problem: **an AI coding agent that asks a decision model "is this right?" and then acts on the answer as if it were permission.**

It governs how agents (Claude Code first) use [TypeSafe Jev](https://docs.typesafe.ai), a System One decision model that returns typed probabilities, and when they may act on the result:

- every model call is journaled, with typed errors that never count as "yes";
- a probability maps to accept / abstain / reject through per-task-family thresholds, with abstention as a real exit code;
- side effects above a risk level need a single-use approval that only a human can give: a signed **execution permit** approved in their own terminal (30 minutes), or, once they set a level with `jade level`, an Allow answer in the chat or a dialog (10 minutes, one call, never for live JADE wiring). Safety invariants such as pushing the default branch cannot be approved by anyone;
- improvement of prompts and thresholds is measured offline and cannot touch its own safety rules.

> **Guiding invariant.** No model output (the decision model, a reasoning model, an evaluator, or an improvement agent) can authorize a side effect, weaken protected policy, declare an unobserved outcome successful, or bypass a human approval boundary.

**Support: Claude Code only; macOS tested; Linux CI only; Windows not supported.** JADE is alpha. It installs in advisory mode, which blocks nothing, and you decide when to make it firmer. With no key, or with no level set, it sends nothing off your machine from the hook (see "What leaves your machine"). An agent can change the two settings in some setups: see "Two settings, read separately".

## Where it runs

| Platform | State |
|---|---|
| macOS (Apple silicon or Intel) with Claude Code | Tested. This is the platform the author uses. CI also runs a macOS job: the setup tests, the hook payload tests and the install check |
| Linux with Claude Code | CI only. The test suite runs on Linux with Python 3.13 and 3.14. No person has done an install trial there yet. There is no Keychain: the key comes from `OPENROUTER_API_KEY`. `jade setup` does not install the maintenance job; `jade maintain-schedule` prints a cron line or a systemd timer for you to install |
| Windows | Not supported and not tested |
| Other agents (Codex, Cursor, Gemini, Aider) | Not wired, so nothing is gated. `jev` and `jade ask` work from any tool and enforce nothing |
| Provider | One: OpenRouter, at the fixed address `openrouter.ai`. No setting changes it |

## Two settings, read separately

JADE has two settings. They are separate. You change them in your own terminal. An agent can change them too in some setups: the hook stops an agent from writing the config file that holds them only when the gate mode is not `off` and a level is set. The last column of the table below shows this.

1. **The gate mode is the safety floor.** It says how firm the gate is: `off`, `advisory` (records what it would block) or `enforce`. Read it with `jade gate status`.
2. **The level is the autonomy setting.** It says how much JADE may answer in your place, and whether text may leave your machine. It is `unset`, `off`, `low`, `medium` or `high`. Read it with `jade level`.

`jade check` shows both: the row `level`, and the row `what blocks`, which lists the blocking rules by id. The table below is not a promise from this page. A test runs the real hook for each pair and compares the result with every cell.

<details>
<summary>What the real hook does, for each pair of settings</summary>

| Gate mode | Level | `git push origin main` | `rm -rf ~/work` | `jade permit approve ...` | Write to `config.json` |
|---|---|---|---|---|---|
| `off` | `unset` | runs | runs | runs | runs |
| `off` | `off` | runs | runs | runs | runs |
| `off` | `low` | runs | runs | runs | runs |
| `off` | `medium` | runs | runs | runs | runs |
| `off` | `high` | runs | runs | runs | runs |
| `advisory` | `unset` | runs | runs | runs | runs |
| `advisory` | `off` | runs | runs | stops | stops |
| `advisory` | `low` | runs | runs | stops | stops |
| `advisory` | `medium` | runs | runs | stops | stops |
| `advisory` | `high` | runs | runs | stops | stops |
| `enforce` | `unset` | stops | stops | stops | runs |
| `enforce` | `off` | stops | runs | stops | stops |
| `enforce` | `low` | stops | stops | stops | stops |
| `enforce` | `medium` | stops | stops | stops | stops |
| `enforce` | `high` | stops | stops | stops | stops |

</details>

The four calls are a push to the default branch (safety invariant SI-08), a high-risk delete (rule R-11), an agent that tries to approve its own permit (SI-09), and an agent that writes the config file with its Write tool (the file that holds the gate mode and the level). "Stops" means the hook refuses the call. "Runs" means the hook lets it through: in advisory mode it still records what it would have blocked. Read three things from the table. First, level `off` is not the firmest setting: in `enforce` it blocks only the safety floor (the safety invariants and writes to JADE's own wiring), so the delete runs there. Second, in `enforce` with the level `unset` every gate rule blocks. Third, the last column is a gap. With gate mode `off`, or with no level set, an agent can write the config file and so move both settings. An agent that writes gate mode `enforce` and level `high` makes the hook send text to the provider, when a key and a preferences text exist. Setting any level with `jade level` (`off` is enough) closes the gap in `advisory` and `enforce`; it does not close it in gate mode `off`. A level an agent writes is not the same as a level you set: `jade check` says when the level was "written some other way". Even with the gap closed a rule is not a wall against an agent that is hostile and runs as your user (docs/05).

## What a blocked command looks like

Claude Code, JADE in enforce mode, no level set, terminal approval. After `jade setup` the gate is in advisory mode and the same calls are not blocked, only recorded.

```text
Agent runs: git push origin main
JADE safety invariant SI-08 blocks this Bash call. No permit can authorize it. Rule text: policies/safety.yaml. Do not rephrase the command to get around the invariant; ask the human.

Agent runs: rm -rf ~/work
JADE gate: high risk (R-11: Destroys local history or files). This Bash call needs a human-approved permit.
Permit prm_xxxxxxxxxxxx is PENDING. Ask the human to run in their own terminal:  jade permit approve prm_xxxxxxxxxxxx
Then retry the identical command (single use, 30 minutes). You cannot approve it yourself; do not rephrase the command to avoid the gate. Continue with other work meanwhile.
```

The first call can never be approved by anyone. What really stops a push to the default branch is git and the remote (the pre-push guard and branch protection, docs/08 section 2.7); JADE reading the command is a second layer. The second call waits for you. You read the command in your own terminal, type the code, and the agent retries the same command once. Any other command does not match the permit.

## What leaves your machine

| Where | What | When |
|---|---|---|
| `openrouter.ai` | Redacted text of a call, your preferences, up to 8 past answers, a fixed question, your key in a header | The hook, only in enforce mode with level `low`, `medium` or `high`, a key and the `jev` setting on (on by default at `low` and `medium`, off at `high` unless you run `jade jev on`). And `jev`, `jade ask` and `jade check` when you run them. And the daily job `jade maintain`, when `jade setup` or you installed it and a key exists: two calls with a fixed text, with no one at the keyboard |
| `github.com` and its release hosts | Release data and the install | `jade upgrade` and `jade upgrade --check` only |
| Nowhere else | No analytics, no crash report, no phone-home | Never |

Ordinary source code and commands are sent as they are. Redaction hides secret shapes and email addresses, not private code. With no key, with level `off` or `unset`, or with the gate not in enforce mode, the hook sends nothing. The full page, with the fields, the limits, the calls that are never sent and how long text stays on your disk, is `PRIVACY.md`. It says nothing about what the provider does with the data: read their terms.

## Which release you are running

You install JADE from GitHub, with the install line in the Quick start. So you trust the repository `github.com/vahidkaargar/jade-decision-governance`, its owner's account and the release tag at the moment `uv` fetches it. JADE runs on every tool call of your agent, so read what you install.

- `jade upgrade` lists the files that change under `jade/`, `policies/`, `registry/` and `scripts/`, and installs only after you type yes.
- It does not check a signature or an attestation. JADE publishes no signed tags and no build attestations.
- A release has a `SHA256SUMS` file. It catches a damaged download. It does not catch a compromised release.
- The release workflow refuses a tag whose commit is not on `main`, and a tag with no CHANGELOG section.

## How fast

The gate runs on every tool call. These are the numbers the repository holds, each with its date and the number of runs.

| What | Runs | p50 | p95 | Date | Source file |
|---|---|---|---|---|---|
| Hook process, a call that is not gated, empty ledger | 30 | 113.5 ms | 156.2 ms | 2026-11-09 | result-ledger-100k-2026-11-09.json: empty_ledger_hook_process/pretool_ungated_read |
| Hook process, a gated call with a permit pending, empty ledger | 30 | 152.2 ms | 165.7 ms | 2026-11-09 | result-ledger-100k-2026-11-09.json: empty_ledger_hook_process/pretool_gated_pending_permit |
| Hook process, a call that is not gated, 100,000 ledger rows | 30 | 109.9 ms | 119.0 ms | 2026-11-09 | result-ledger-100k-2026-11-09.json: after/hook_process/pretool_ungated_read |
| Hook process, a gated call with a permit pending, 100,000 ledger rows | 30 | 150.0 ms | 162.4 ms | 2026-11-09 | result-ledger-100k-2026-11-09.json: after/hook_process/pretool_gated_pending_permit |
| One provider call with one question | 20 | 514 ms | 639 ms | 2026-11-09 | result-2026-11-09.json: single_noul_parallel_1 |

These numbers are old. They were measured on 2026-11-09 on macOS, with a synthetic ledger, before release 0.4.0. They are not measured again on the current release; that is an open item for the owner. The files are in `evals/performance/`.

When a level sends a call to the provider, the hook waits for the answer, also in shadow. It waits at most 3 seconds, inside the hook's own limit of 6 seconds, and then you are asked as before. No live figure for this wait is published here.

## Status

| Stage | State |
|-------|-------|
| DESIGN_READY | **Yes** — design approved by the maintainer 2026-11-09 |
| IMPLEMENTED | **Yes** — `jade` 0.6.1: ledger, adapter, redaction, `jev` shim, question registry, permits, hook gate, calibration, bounded improvement loop, standalone pre-push guard (`jade git-hook`); Phases 1–5 acceptance tests pass. 0.2.1 closed the first independent adversarial review and 0.2.2 the false-positive pass and the structural fix for "never push to the default branch" (see `CHANGELOG.md`). The command classifier is defence in depth, not the enforcement of that rule: the enforcement is the pre-push guard and branch protection on the remote, which only a human can install (`docs/08-deployment-runbook.md` section 2.7, W-68, W-69). The residual risks are listed in `docs/05-threat-model.md` and tracked in `BACKLOG.md` |
| LIVE (enforce) | **Run on the author's machine, in enforce mode with the signed approver, since 2026-11-13. What is on for your machine is what `jade check` says, not this table.** Enforce means: the five hooks are installed, the gate blocks high and critical calls that have no approval, and a permit needs a signature only the `_jadeapprove` account can make. Whether the daily maintenance job has been loaded, whether the pre-push guard is installed and whether branch protection is on for a repository are facts of one machine and one remote: `jade check` shows each row ok, FAIL, or "unknown: owner confirms" |
| VALIDATED | No — needs 14 days of real ledger data, labelled outcomes and one full improvement cycle (`docs/08` section 5); earliest 2026-11-27 (14 days of enforce) |
| PRODUCTION_APPROVED | No — a human decision (`docs/08`) |

**What 0.5 and 0.6 added (docs/12).** A level for the whole machine (`jade level`, set only by you): at `high`, a gated call is one click in the chat or a dialog instead of a terminal permit. At `low` or `medium`, a call you confirmed earlier (`jade review confirm`, `jade knows`) runs with no question, and Jev may answer a plain high call in your place when it is sure enough (`jade review` shows the silent share, your marks and the wrong-rate bound). Human-only classes (live JADE wiring, deletes that cannot be undone, money, public releases) and critical calls are asked every time, and no answer is remembered for them. A failed call closes its decision (`PostToolUseFailure`). Details and the owner page: `docs/12-jev-decides-phase1.md`.

**Supported:** Claude Code is the only harness JADE is wired into, and macOS is the tested platform (CI runs the suite on Linux; see "Where it runs"). The `jev` and `jade ask` commands work from any tool, but no other agent is gated.

Signed permit approval (W-38, W-89) is merged: with the approver installed, a high or critical permit is approved by a signature only a separate OS account can make, and `jade gate enforce` refuses without it. The installer has not yet been run on a second machine; treat it as new (`docs/10`, `docs/11`).

Hardening batch 1 (merged 2026-11-09, `CHANGELOG.md` Unreleased) added the wiring checks: `jade check` now reports shadowed or disabled hooks in project, managed and plugin settings, lints the private overlay, compares the installed code with the checkout, validates permit and improvement rows, and the permit seal key is protected against Read, Grep and Glob as well as shell. After pulling a change like this, re-install the tool and, when the changelog says the hook matcher changed, re-run `jade hooks install` yourself (an agent cannot: SI-09).

What exists today: the design documents, the runtime, six JSON Schema contracts, five policy files with a tested loader, a 12-entry question registry, replayable regression and adversarial cases (`evals/run_cases.py`), the baseline and performance tooling, and the phase acceptance tests. What does not exist yet is listed in `BACKLOG.md` (for example the routing and judgment eval sets, W-49, and several metrics of `docs/04` section 7, W-52); the documents mark each of those as planned. The gate is a syntactic classifier with known residual risks (`docs/05` T2, T3, T18, T19, T22): it is a strong guard rail for an agent that makes mistakes, not a boundary against one that is adversarial and runs as the same user. Wiring it into an agent harness is deliberately a human step: see `docs/08-deployment-runbook.md`.

## Why: the measured baseline

Before designing anything, the current practice was measured on one developer machine: a rule in the agent instructions ("run `jev verify` before presenting work as done; a low probability means investigate") and a 124-line CLI with no ledger.

| Over 14 days, 907 real calls | |
|---|---|
| Verifications below the vendor's suggested 0.70 "yes" line | 57% |
| Of those, followed by a done/verified claim in the next message (heuristic) | 30% |
| Calls that re-asked an identical question in the same session | 20% |
| Calls with an error or unparsable result, none of which blocked anything | 9.4% |
| Latency, one question or eight batched (measured at baseline, 2026-11-08) | ≈ 500 ms either way. A live call is slower and changes with the provider: `jade check` prints the latency of its canary call |

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
| — | `PRIVACY.md` | What JADE sends off your machine, when, what is never sent, and how long text stays on your disk |
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

- macOS or Linux (macOS is the tested platform; `jade setup` installs the maintenance job on macOS only, on Linux `jade maintain-schedule` prints a cron line or a systemd timer for you to install; see `docs/09-install-and-upgrade.md` section 5)
- Python 3.13 or newer and [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://docs.claude.com/en/docs/claude-code) (JADE installs its hooks in `~/.claude/settings.json`)
- An OpenRouter key for the Jev calls: in the macOS Keychain (service `OPENROUTER_API_KEY`, account `$USER`) or in the `OPENROUTER_API_KEY` environment variable of the shell that starts Claude Code. The gate and the hooks work without a key; `jev` and the live `jade check` canary need it. `jade setup` never asks for the value and never prints it

**1. Install** (pinned to a release tag; installs `jade` and the `jev` compatibility shim)

```bash
uv tool install git+https://github.com/vahidkaargar/jade-decision-governance@v0.6.1
jade --version
```

If `uv` answers `Executable already exists: jev`, an older `jev` script sits in uv's tool bin directory and nothing was installed (not even `jade`). Move it aside (`mv ~/.local/bin/jev ~/.local/bin/jev.orig`) and run the install again, or add `--force` to overwrite it.

**2. Wire it into Claude Code**

```bash
jade setup --status      # read-only: what is done and what is missing
jade setup               # asks y/N before each change: advisory gate, an explicit `jev off` when the key is not set, hooks, skill, maintenance job, and the Jev section of an existing CLAUDE.md
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
