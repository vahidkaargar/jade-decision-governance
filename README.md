# JADE — Adaptive Decision Governance for AI agents

[![CI](https://github.com/vahidkaargar/jade-decision-governance/actions/workflows/ci.yml/badge.svg)](https://github.com/vahidkaargar/jade-decision-governance/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

JADE is a design and a small Python runtime for one problem: **an AI coding agent asks a decision model "is this right?" and then acts on the answer as if it were permission.**

JADE sits between Claude Code and [TypeSafe Jev](https://docs.typesafe.ai), a System One decision model that returns typed probabilities. It journals every model call, and an error never counts as "yes". It maps a probability to accept, abstain or reject with thresholds per task family, and a risky side effect waits for a single-use approval that only a human can give. Prompts and thresholds improve offline, by measurement, and that loop cannot touch its own safety rules.

> **Guiding invariant.** No model output (the decision model, a reasoning model, an evaluator, or an improvement agent) can authorize a side effect, weaken protected policy, declare an unobserved outcome successful, or bypass a human approval boundary.

**Support: Claude Code only; macOS tested; Linux CI only; Windows not supported.** JADE is alpha. It installs in advisory mode, which blocks nothing. You decide when to make it firmer. With no key, or with no level set, the hook sends nothing off your machine (see "What leaves your machine"). In some setups an agent can change the two settings: see "Two settings, read separately".

## Where it runs

| Platform | State |
|---|---|
| macOS (Apple silicon or Intel) with Claude Code | Tested. This is the platform the author uses. CI also runs a macOS job: the setup tests, the hook payload tests and the install check |
| Linux with Claude Code | CI only. The test suite runs on Linux with Python 3.13 and 3.14. No person has done an install trial there. There is no Keychain: the key comes from `OPENROUTER_API_KEY`. `jade setup` does not install the maintenance job; `jade maintain-schedule` prints a cron line or a systemd timer for you to install |
| Windows | Not supported and not tested |
| Other agents (Codex, Cursor, Gemini, Aider) | Not wired, so nothing is gated. `jev` and `jade ask` work from any tool and enforce nothing |
| Provider | One: OpenRouter, at the fixed address `openrouter.ai`. No setting changes it |

Claude Code is the only harness JADE is wired into, and macOS is the tested platform.

## Status

| Stage | State |
|-------|-------|
| DESIGN_READY | **Yes** — design approved by the maintainer 2026-11-09 |
| IMPLEMENTED | **Yes** — `jade` 0.7.0. All five phases are built and their acceptance tests pass. What the code holds and what each release added: `docs/09-install-and-upgrade.md` section 1 and `CHANGELOG.md` |
| LIVE (enforce) | **Each owner sets enforce mode on their own machine. What is on for your machine is what `jade check` says, not this table.** Enforce means: the five hooks are installed, the gate blocks high and critical calls with no approval, and a permit needs a signature only the `_jadeapprove` account can make. The daily job, the pre-push guard and branch protection are facts of one machine and one remote: `jade check` shows each as ok, FAIL, or "unknown: owner confirms" |
| VALIDATED | No — needs 14 days of real ledger data in enforce mode, labelled outcomes and one full improvement cycle (`docs/08` section 5) |
| PRODUCTION_APPROVED | No — a human decision (`docs/08`) |

## Quick start (Claude Code)

This release is **enforce-ready**: it installs in advisory mode, and after you install the approver (`scripts/install-approver.sh`, `docs/10`), `jade gate enforce` makes the gate block. It is not VALIDATED and not production approved (see Status). Every step that wires JADE into Claude Code is a human step in your own terminal; an agent cannot run it for you.

**Requirements**

- macOS, or Linux (see "Where it runs" and `docs/09-install-and-upgrade.md` section 5).
- Python 3.13 or newer, [uv](https://docs.astral.sh/uv/) and [Claude Code](https://docs.claude.com/en/docs/claude-code). JADE installs its hooks in `~/.claude/settings.json`.
- An OpenRouter key, in the macOS Keychain (service `OPENROUTER_API_KEY`, account `$USER`) or in the `OPENROUTER_API_KEY` environment variable of the shell that starts Claude Code. The gate and the hooks work without it; `jev` and the live `jade check` canary need it. `jade setup` never asks for the key and never prints it.

**1. Install** (pinned to a release tag; installs `jade` and the `jev` compatibility shim)

```bash
uv tool install git+https://github.com/vahidkaargar/jade-decision-governance@v0.7.0
jade --version
```

If `uv` answers `Executable already exists: jev`, nothing was installed: see `docs/09-install-and-upgrade.md` section 6.

**2. Wire it into Claude Code**

```bash
jade setup --status      # read-only: what is done and what is missing
jade setup               # asks y/N before each change (docs/08 section 2.0 lists the steps)
```

`jade setup` needs an interactive terminal and refuses inside an agent. It backs up `settings.json` and `CLAUDE.md` before it edits them. It never sets enforce mode. Open a new Claude Code session afterwards: Claude Code reads hooks at session start.

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

Only exit code 0 is a yes. Give `jev` raw evidence (a log, a diff, a command's output), not your own summary. Claude Code learns about `jev` from the skill that `jade setup` installs. With no Jev section in your `~/.claude/CLAUDE.md`, add the output of `jade claude-md show` yourself.

**What advisory mode does and does not protect**

- It does: journal and redact every Jev call, record a `would_block` event for every call the gate would stop in enforce mode, and refuse to let an agent approve its own permit.
- It does not: block any command, edit or tool call. A `git push` to the default branch or `rm -rf` runs as it would without JADE.
- Moving to enforce mode (`jade gate enforce`) is your decision. Make it after you have read `jade ledger stats` and `jade decide list` for a week or more (`docs/08-deployment-runbook.md` section 2.2). The full list: `docs/09-install-and-upgrade.md` section 1.

**Upgrade** (in your own terminal; the ledger, your overlays, `settings.json` and `CLAUDE.md` are not touched)

```bash
jade upgrade --check     # installed against latest release, plus the CHANGELOG headings in between (read-only)
jade upgrade             # installs the latest release with uv, then runs jade check from the new install
```

Re-run `jade hooks install` and open a new Claude Code session only when the output says a hook row failed. After an upgrade, `jade setup --status` shows whether the shipped skill or CLAUDE.md text changed. The upgrade contract, and what you trust when you install a release: `docs/09-install-and-upgrade.md`.

**Roll back and uninstall**

```bash
jade gate advisory       # stop blocking, keep recording
jade hooks uninstall     # remove JADE's hook entries from ~/.claude/settings.json
jade upgrade --ref vX.Y.Z   # install an older release (reported as a rollback)
```

The full rollback table is in `docs/08-deployment-runbook.md` section 3. Complete removal, step by step and in the right order, is in `docs/09-install-and-upgrade.md` section 7.

**Report a problem**

Open an issue at https://github.com/vahidkaargar/jade-decision-governance/issues with the output of `jade version` and `jade check --offline` (remove paths you do not want to share), the command you ran and what you expected. A bug that lets an agent act without a human approval, or leaks text redaction should have removed, goes to a private security advisory: `SECURITY.md`.

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

The four calls: a push to the default branch (safety invariant SI-08), a high-risk delete (rule R-11), an agent that approves its own permit (SI-09), and an agent that writes the config file (it holds both settings) with its Write tool. "Stops" means the hook refuses the call. "Runs" means the hook lets it through; advisory mode still records what it would have blocked.

1. Level `off` is not the firmest setting. In `enforce` it blocks only the safety floor (the safety invariants and writes to JADE's own wiring), so the delete runs.
2. In `enforce` with the level `unset`, every gate rule blocks.
3. The last column is a gap. With gate mode `off`, or with no level set, an agent can write the config file and move both settings. If it writes `enforce` and `high`, the hook sends text to the provider when a key and a preferences text exist. Set any level with `jade level` (`off` is enough) to close the gap in `advisory` and `enforce`. Gate mode `off` keeps the gap.

`jade check` says when the level was "written some other way", not set by you. Even with the gap closed, a rule is not a wall against a hostile agent that runs as your user (docs/05).

## What leaves your machine

| Where | What | When |
|---|---|---|
| `openrouter.ai` | Redacted text of a call, your preferences, up to 8 past answers, a fixed question, your key in a header | The hook: only in enforce mode, with level `low`, `medium` or `high`, a key, and the `jev` setting on (`jade jev`; off by default at `high`). `jev`, `jade ask` and `jade check` when you run them. The daily job `jade maintain`, when it is installed and a key exists: two fixed-text calls with no one at the keyboard |
| `github.com` and its release hosts | Release data and the install | `jade upgrade` and `jade upgrade --check` only |
| Nowhere else | No analytics, no crash report, no phone-home | Never |

Ordinary source code and commands are sent as they are. Redaction hides secret shapes and email addresses, not private code. With no key, with level `off` or `unset`, or outside enforce mode, the hook sends nothing. `PRIVACY.md` has the fields, the limits, the calls never sent, and how long text stays on your disk. What the provider does with the data is in their terms, not here.

## What a blocked command looks like

Claude Code, JADE in enforce mode, no level set, terminal approval. After `jade setup` the gate is in advisory mode, and the same calls are only recorded, not blocked.

```text
Agent runs: git push origin main
JADE safety invariant SI-08 blocks this Bash call. No permit can authorize it. Rule text: policies/safety.yaml. Do not rephrase the command to get around the invariant; ask the human.

Agent runs: rm -rf ~/work
JADE gate: high risk (R-11: Destroys local history or files). This Bash call needs a human-approved permit.
Permit prm_xxxxxxxxxxxx is PENDING. Ask the human to run in their own terminal:  jade permit approve prm_xxxxxxxxxxxx
Then retry the identical command (single use, 30 minutes). You cannot approve it yourself; do not rephrase the command to avoid the gate. Continue with other work meanwhile.
```

Nobody can approve a safety invariant, so nobody can approve the first call. The second call waits for a single-use approval from you:

- a signed **execution permit**: you read the command in your own terminal and type the code, and the agent retries the same command once (30 minutes);
- or, once you set a level with `jade level`, an Allow answer in the chat or a dialog (10 minutes, one call, never for live JADE wiring).

Any other command does not match the approval. What really stops a push to the default branch is git and the remote, with the pre-push guard (`jade git-hook script`) and branch protection: `docs/08-deployment-runbook.md` section 2.7.

## Read in this order

| # | File | What it settles |
|---|------|-----------------|
| 0 | `docs/00-inventory-and-gaps.md` | Verified facts about Jev through OpenRouter, the measured baseline (why JADE exists), the gap register |
| 1 | `docs/01-adrs.md` | 13 architecture decisions with rejected alternatives |
| 2 | `docs/02-contracts-and-state-machine.md` | Decision lifecycle, transition guards, the six contracts |
| 3 | `docs/03-routing-and-question-design.md` | Preflight, FAST / JEV / DEEP routing, question registry, batching |
| 4 | `docs/04-calibration-and-evaluation.md` | Thresholds, labels, benchmark protocol, budgets, how fast the hook is |
| 5 | `docs/05-threat-model.md` | Threats, mitigations, what a Markdown skill can and cannot enforce |
| 6 | `docs/06-roadmap.md` | Phases 1–5, acceptance tests, rollout and rollback |
| 7 | `docs/07-self-improvement-and-work-management.md` | Bounded improvement loop, work items, human-only boundaries |
| 8 | `docs/08-deployment-runbook.md` | The human-only wiring steps, rollback, the path to VALIDATED |
| 9 | `docs/09-install-and-upgrade.md` | What the code holds, versions, upgrades, releases and what you trust, platforms, troubleshooting by `jade check` row, uninstall |
| 10 | `docs/10-out-of-band-permit-approval.md` | Signed permit approval, which a same-user agent cannot fake (`scripts/install-approver.sh`) |
| 11 | `docs/11-release-0.4.0-plan.md` | The plan for the first enforce-ready release |
| 12 | `docs/12-jev-decides-phase1.md` | The level, answers in the chat, remembered answers, Jev deciding in your place |
| 13–17 | `docs/13-real-shell-payload-tests.md` to `docs/17-memory-audit.md` | One page per later change |
| — | `PRIVACY.md` | What JADE sends off your machine, and how long text stays on your disk |
| — | `BACKLOG.md` and `CHANGELOG.md` | Every open item; what changed in each release |
| — | `docs/JADE_Claude_Code_Master_Plan.md` | The original handoff draft this repository completes |

## Develop

Requires uv and Python 3.13+.

```bash
uv tool install .                    # install your checkout instead of a release (`uv tool install --reinstall .` after changes)
uv sync
uv run pytest -q                      # contract, policy, overlay and phase acceptance tests
uv run python evals/run_cases.py      # regression + adversarial case replay
uv run ruff check .
```

When you test JADE itself, never feed synthetic dangerous commands to the installed hook. Run every `jade` command with `JADE_HOME=$(mktemp -d)`, so test permits and decisions stay out of your real ledger (backlog W-72).

The repository layout, the risk classifier one-liner, baseline mining from your own transcripts and the live probes are in `CONTRIBUTING.md`.

## Make the policies yours

The shipped `policies/risk.yaml` and `policies/safety.yaml` are generic. Your production hosts, deploy scripts and retired services go in overlays that git ignores:

```bash
# from a checkout; the installed package holds the same two example files in `policies/local/`
mkdir -p ~/.config/jade/local
cp policies/local/risk.local.example.yaml   ~/.config/jade/local/risk.local.yaml
cp policies/local/safety.local.example.yaml ~/.config/jade/local/safety.local.yaml
```

An overlay can add hosts, rules and invariants, but it cannot remove a shipped invariant or lower a shipped high-risk rule. The keys, and how `jade check` lints an overlay: `docs/08-deployment-runbook.md` section 2.8.

## Contributing, security, license

See `CONTRIBUTING.md` and `SECURITY.md`. MIT licensed. JADE is an independent project and is not affiliated with TypeSafe, OpenRouter or Anthropic.
