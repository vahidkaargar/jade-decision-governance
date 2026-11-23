# Developer experience and public repository readiness

This section answers one question. What must change so that a stranger can install JADE, understand it, trust it and leave it, without the owner's help and without JADE saying anything untrue? It covers install, onboarding, configuration, compatibility, portability, dependencies, defaults, diagnostics, docs, upgrade, tests and CI, release governance, observability, performance, privacy and packaging. It separates the small core from the optional parts. It ends with a ranked list of what blocks public adoption, a minimal checklist, and a list of things not to build.

## 0. How to read this section

**Tags.** [VERIFIED] has concrete evidence: a file, symbol, test, command output, or an evidence file id (A1 to A8 are repo audits, R1 to R6 are web research). [INFERENCE] is my reasoning. [RECOMMENDATION] is a design proposal. [UNRESOLVED] was not settled. Research claims keep their original confidence. "Needs human approval" marks anything that changes safety, privacy or release behavior.

**Id prefix.** Ids in this section start with `DX-` (for example DX-D1, DX-P1, DX-H1) so they do not collide with sibling sections.

**Which ref.** Refs moved during research, so I name the ref each time.
- `8100ea1` is tag v0.6.1, what runs on the maintainer's Mac. [VERIFIED A1, A8]
- `e9412f3` is `origin/main` on 2026-11-16: v0.6.1 plus PR 48 (SI-13, a guard for test harnesses). `pyproject.toml` still says `0.6.1`. [VERIFIED: `git rev-parse origin/main`, this session; also R1, R4] A8 and A1 meant `8100ea1` by "main". Their facts hold for `e9412f3` for install, CI, release and README, because PR 48 changes none of those. [VERIFIED: `git diff --stat 8100ea1 e9412f3 -- README.md .github pyproject.toml scripts jade/ops.py jade/setup.py jade/cli.py` is empty]
- The unmerged stack (PR 42 to 47) changes none of `README.md`, `.github`, `pyproject.toml`, `scripts/`. [VERIFIED: `git diff --stat origin/main...<branch>`] The local-only branch `docs-approvals-wording` changes one line in `README.md` and one line in repo `docs/08-deployment-runbook.md`. [VERIFIED: `git diff --stat origin/main...docs-approvals-wording`]

**Assumptions (reversible).**
- A-1: "v6" means the 0.6.x line, plus main after 0.6.1, plus the unmerged 0.7.0 candidate stack. No file defines "v6". [VERIFIED A1 section 1]
- A-2: The public user is a developer who already uses Claude Code. [INFERENCE: JADE only gates Claude Code, A8 section 3]
- A-3: The repository `vahidkaargar/jade-decision-governance` is already public under the MIT license. [VERIFIED: `gh repo view` returned `PUBLIC` and `mit`, this session] This closes A8's first owner question. So the question is not "may we publish". It is "what is already exposed, and is it true".

## 1. Verdict

[INFERENCE] JADE's DX problem is not too many features. It is four things.

1. **The install is a power-user install, and the README does not say what a newcomer gets for it.** The README opens with a baseline table, a status ladder and 12 documents to read. It never mentions levels, memory, `jade review`, `jade knows` or the Jev proxy, the 0.6.0 headline features. [VERIFIED: `git show origin/main:README.md`; the only "level" hits are "risk level"]
2. **JADE's own truth surface is wrong in small ways.** `jade check` and the docs say things the code does not support (section 6). For a tool whose purpose is honest governance, this costs more trust than any missing feature.
3. **Promises about data are only partly kept.** Retention does not cover all stored command text. The scheduler exists only on macOS (section 13).
4. **Nobody has measured whether a stranger succeeds.** The checklist below checks artifacts (a test, a green job). It does not check the outcome. Section 10 adds one acceptance trial and a rule to show friction and correctness together.

[RECOMMENDATION] The smallest public-ready JADE is the current JADE with these changes. A true README front page. A truthful `jade check`. A complete purge. A macOS CI job. A release workflow that enforces its own rules. One page that says what leaves the machine. One acceptance trial with a person who is not the maintainer. It needs no new subsystem. I propose no daemon, registry, plugin API or second harness adapter.

**Challenge to the owner's spec.** The goal is "minimum human friction". For install this needs care. The wiring steps are human-only on purpose (SI-09, `ops._require_human`). [VERIFIED A8 section 1] Removing them would remove the one thing an agent must not do. The right DX measures are "time to first correct mental model" and "time to first useful block, with consent still human". A one-line install that an agent can run is a defect. Also: fewer asks is not a DX win by itself. It counts only when decisions stay correct (section 10).

## 2. Install complexity and human-only steps

[VERIFIED A8 section 1; `jade/setup.py` `TOTAL = 7`] The path to a working advisory install:

| # | Step | Who | Outside account or sudo |
|---|---|---|---|
| 1 | Python 3.13+ and `uv` | human | no |
| 2 | OpenRouter key in Keychain or environment (optional, see correction 1) | human | external account |
| 3 | `uv tool install git+https://github.com/vahidkaargar/jade-decision-governance@v0.6.1` | human | git and GitHub |
| 4 | `jade setup` (up to 7 y/N prompts) | human, interactive terminal | no |
| 5 | New Claude Code session (hooks load at start) | human | no |
| 6 | `jade check --offline`, then `jade check` | human | the second calls the provider |

That is 6 human steps and up to 7 prompts. The full state (signed approver, enforce, levels, memory) is about 12 to 14 human actions over at least a week. One of them needs `sudo` and a hidden OS account `_jadeapprove`. The approver installer ran on one machine, once (2026-11-13). Its Linux branch never ran. [VERIFIED A8 section 1]

**Corrections to A8 that change the plan.**

1. **A key is not needed to install.** The README lists the key under "Requirements", before the numbered steps. It does say "the gate and the hooks work without a key". But the placement reads as a requirement. [VERIFIED: `git show origin/main:README.md`, Requirements bullets and steps 1 to 3] `jade setup` only prints help when the key is missing, and then runs the other steps. [VERIFIED by reading `jade/setup.py` at `origin/main`: `rows, key_ok = prerequisites()`, `if not key_ok: say(_KEY_HELP...)`, then the action loop. A code reading, not a run.] The deterministic gate needs no model. [VERIFIED A8 section 2] So a stranger can get value with no account. [RECOMMENDATION] Move the key out of Requirements into an "optional: Jev" note.
2. **A "yes to all" path already exists, and it already shows every change.** `jade setup --yes` "applies every step without asking (still needs an interactive terminal)". [VERIFIED: `jade/cli.py` line 799] For every step the setup loop prints the change block and the undo block before it applies. [VERIFIED: `jade/setup.py` run loop, `_block(say, 'change', ...)` and `_block(say, 'undo', ...)`] A8 proposed `jade quickstart`. [RECOMMENDATION] Add no command. Keep `--yes` as it is. A single-consent flow would turn seven consents for edits to hooks and instruction files into one. That is a consent change. If the owner ever wants it, it needs a new flag name (not `--yes`) and human approval. I do not recommend it for v6.
3. **The bare default is enforce; setup's default is advisory.** `paths.gate_mode()` returns `enforce` when nothing is set. [VERIFIED `jade/paths.py`] `jade setup` already prints this: "Gate mode is not configured, so the gate runs as enforce; setup did not set advisory". [VERIFIED `jade/setup.py`] A user who runs only `jade hooks install` gets that enforce gate and no such notice. The guard in `ops.set_gate_mode` runs only on `jade gate enforce`. It is also conditional. It refuses enforce without the signed backend only when the ledger shows gated calls with no host prompt in the last 7 days (`if no_prompt and not allow_unsigned`). On a fresh install there are no such calls, so the guard passes. [VERIFIED `jade/ops.py` `set_gate_mode`, `no_prompt_gated_calls`] The code comment says enforce without the signed backend is "not a boundary against a same-user agent". [VERIFIED `jade/ops.py` line 578] Enforce is the stricter default: it blocks high and critical calls without a permit. The weakness is terminal-only approval, not the default itself. [RECOMMENDATION] `jade hooks install` prints the effective gate mode, what it blocks, and the approval backend, with the same text `jade setup` uses. No default change in v6. A default change is a separate safety proposal for the owner (H4).

**Zero-config.** [VERIFIED A8 section 13] "No human step" is MISSING, by design. [RECOMMENDATION] Aim for "zero decisions": a user who accepts the printed plan changes nothing else.

## 3. Minimal core and two independent readouts

[INFERENCE] A user faces several independent dials. Gate mode (`off`, `advisory`, `enforce`). Level (unset, `off`, `low`, `medium`, `high`). Approval backend (terminal or signed). Hooks installed or not. Key present or not. Maintenance job loaded or not. [VERIFIED keys: A4 section 8, `paths.py`] The combinations are the real learning cost.

My first draft collapsed these into one ladder of three tiers (Observe, Govern, Harden). Review showed that is wrong. [VERIFIED by critic reading `ops.set_gate_mode` and `paths.gate_mode`] A ladder mixes two separate things. Safety (how firm is the gate) and autonomy (how much does JADE answer for the owner, and what leaves the machine). The owner's spec says these are separate dimensions. A ladder also has no row for: a key present with level unset; advisory with level `high`; enforce without the signed approver (reachable on a fresh install). So I drop the ladder.

[RECOMMENDATION] `jade check` and the README show **two independent readouts**. Both are derived and read-only. No config key.

| Readout | Values | Derived from |
|---|---|---|
| Enforcement | `watch` (gate off or advisory); `gate, terminal approval` (enforce); `gate, signed approver` (enforce plus signed backend) | gate mode, approval backend |
| Autonomy and egress | `owner decides everything, nothing leaves the machine` (level unset or off, or gate not enforce, or no key); `model advice, data can leave` (enforce, key, level low, medium or high); plus memory on or off | gate mode, level, key |

Example line: "enforcement: gate, terminal approval | autonomy: level low, Jev on, redacted text can go to the provider".

**What the gate blocks, exactly.** [VERIFIED A4 section 2 probe; `jade/hooks.py` `_verdict` and `GOVERNANCE_INVARIANTS`]
- Gate `advisory` with the level **unset**: nothing blocks, including SI-09 to SI-12. The probe gave exit 0 for SI-09.
- Gate `advisory` with any level set (even `off`): SI-09 to SI-12 block (exit 2). Other safety invariants and risk rules are advisory.
- Level `off` with enforce: only the governance invariants block (probe, A3).

An earlier version of this section said that at the lowest setting the gate "does not block, except SI-09 to SI-12". That is wrong for level unset. Do not publish it. [RECOMMENDATION] Do not print any "what this setting blocks" sentence in the README or in `jade check` unless a test generates it. The test drives the real hook for each combination of gate mode and level and records the exit code. The docs-truth test then compares the prose with that table.

This is a new member of the "check says ok but the gate is mostly advisory" family (DX-D6 in section 6). [RECOMMENDATION, needs human approval] Decide whether `jade setup` should set level `off` (so SI-09 to SI-12 are firm from the first day) or whether it should print that they are not firm until a level is set (H10).

**Test for the readouts.** [RECOMMENDATION] One table test runs every combination of the dials (gate mode, level, backend, key present). It checks that both lines are produced and that each line is true against the real hook behavior. This is what the ladder could not give.

**Core and optional.** Core: hook gate, risk rules, safety invariants, ledger, permits with terminal approval, `jade check`, `jade setup`, `jade upgrade`. Optional: chat and dialog approval (needs a level; dialog is macOS only), memory, `jade review` and `jade knows`, Jev verify and the proxy (need a key and an outside provider), the signed approver (sudo), improvement loop and evals (maintainer tooling). One mismatch: the maintenance job is optional, but retention depends on it. So it should move toward core (section 13).

**Progressive enhancement rule.** [RECOMMENDATION] Each layer must fall back to the one below without silent weakening. If the provider is down, the gate still runs and the owner is asked. That already holds for the proxy (one try, owner asked). [VERIFIED A5] If the signed approver is missing, the enforcement line says "terminal approval".

## 4. Configuration discoverability

[VERIFIED A4 section 8] Config lives in `~/.config/jade/config.json`: `gate_mode`, `level`, `checkout`, `approval_backend`, `permit_reveal`, `model`, `owner_emails`, `retention_days`, `auto_promotion_opt_in`, and three improvement keys. Preferences are in `preferences.md`. `JADE_*` environment overrides exist and SI-11 blocks an agent from setting four of them. No single command lists every key with its default and current value.

[RECOMMENDATION] Do not add `jade config show` or `jade explain` in v6. Reasons:
- `jade explain '<command>'` cannot say what each level would do. Level behavior depends on the Jev proxy, memory and past answers. The output would be a partial or false promise. [INFERENCE: A4 sections 3 and 8]
- A command that returns the rule id and outcome for any input is an oracle. An agent could use it to search for a command that slips through. The first draft did not say who may run it.
- `jade config show` has no demonstrated need beyond one inference. It could also print `owner_emails`.

[RECOMMENDATION] Put the safe part into `jade check --json` (section 6): effective gate mode, level, backend, retention days, key present yes or no. Never print `owner_emails` or any key value. If `jade explain` is reopened, it shows only the deterministic rule id and risk level, runs in a human terminal, and writes nothing. See section 17 for the trigger. [UNRESOLVED] Whether `policy.assess` touches the ledger on any path; check before ever building `explain`.

## 5. Backward compatibility

[VERIFIED repo `docs/09` section 2; `tests/test_ledger_migration.py` exists] JADE already has a written policy. It has a table of breaking surfaces. "While the major version is 0, a breaking change raises the MINOR number". Ledger migrations are additive and forward-only. A newer patch ledger opens under an older patch. `level` unset behaves like 0.4.0. The `jev` exit codes `0/3/4/5/6` are stable. [VERIFIED A8 sections 2, 3] A new release changes `policy_version` and revokes unused permits. [VERIFIED A8 section 6]

[INFERENCE] Compatibility is a strength. Spend little here. [RECOMMENDATION] Link repo `docs/09` section 2 from the README with one sentence on what is stable in 0.x (exit codes, forward migration, config keys) and what may change in a minor (rule ids, question versions, hook text). `jade upgrade` prints how many permits it will revoke before it asks. Keep a golden ledger file from the previous release and open it in a test. [UNRESOLVED] The existing test may already do this; I read only its name.

## 6. Diagnostics: `jade check` is not a reliable witness

[VERIFIED A8 section 11, A3, A4; code read at `origin/main`] `jade check` has 24 rows, exits non-zero on dead wiring, and has `--offline` only, no `--json`. The brief says it "lies". The evidence shows five defects, and one case where the check is right and other text is wrong:

| # | Defect | Evidence | Effect |
|---|---|---|---|
| DX-D1 | `level off` returns `ok`, yet at level off only SI-09 to SI-12 block and SI-08 (push to a default branch) is advisory | `ops._level_row` returns `("level", True, "off")` [VERIFIED this session]; behavior [VERIFIED A3 probe: SI-08 push main exit 0 at off] | A green row hides a mostly advisory gate. This Mac runs level off. |
| DX-D2 | Gate `off` text says "every JADE hook is inert"; three of five hooks still run | `ops.py` row text [VERIFIED]; A4 section 3 | A false sentence in a safety tool |
| DX-D3 | "all four" hooks; `ops.HOOKS` registers five. README, `setup.py`, repo `docs/08` say four too | [VERIFIED A8, `ops.HOOKS`] | Users think one is missing |
| DX-D4 | The maintain row is `ok` when no LaunchAgent exists ("optional"). Off macOS there is no scheduler, so retention never runs and the row stays green | `ops.py` maintain row [VERIFIED this session] | Silent retention failure on Linux |
| DX-D5 | Plist exists but launchd has not loaded it: the row correctly FAILS (the maintainer's Mac now). The wrong text is the README status row ("the daily maintenance job is loaded") | A8 section 11; README line 23 [VERIFIED] | Check right, README stale |
| DX-D6 | Gate `advisory` with level unset: `jade check` can pass while SI-09 to SI-12 do not block (section 3) | A4 section 2 probe [VERIFIED] | Same family as D1: green check, soft gate |

[RECOMMENDATION] D1 and D6 become warnings that name what is advisory. D3 prints `len(ops.HOOKS)`. D4 becomes a warning: "no scheduler on this platform; retention does not run". Store a last-maintain timestamp, as `last_check` is stored, so silence is not health. D1 and D6 need human approval because they change what the tool says about safety. The sibling security section owns what `level off` should block.

[RECOMMENDATION] Add `jade check --json` (stable row ids, `ok|warn|fail`, one-line detail, plus the readouts from section 3). Small cost. It is the base for CI and later export. [RECOMMENDATION] Do not add anything beyond it now.

[RECOMMENDATION] SessionStart prints one line when the last maintain run is old or a row is red. [VERIFIED A4: SessionStart already prints notices; about 136 ms on a 100k ledger in the 2026-11-09 run] This puts a dead job in front of the human without a new alert system.

[RECOMMENDATION] Every `jade check` row gets a test that drives it into both states, as `tests/test_docs_truth.py` does for docs. That test did not catch D3 or the registry count. [VERIFIED A8 section 13]

## 7. Documentation quality and contradictions

[VERIFIED A8 section 12] Strengths: 16 files under `docs/`, honest residual-risk language, exact commands in CONTRIBUTING, `tests/test_docs_truth.py` (commands exist, threat rows have evidence, work items exist, one version number), issue and PR templates, SECURITY.md, MIT license.

| # | Contradiction or gap | Evidence | Fix |
|---|---|---|---|
| 1 | README omits levels, memory, review, knows, proxy; reading table stops at repo docs/11 and skips repo docs/12 and repo docs/13 | `git show origin/main:README.md` [VERIFIED] | Rewrite the front |
| 2 | README says "10-entry question registry"; `registry/` has 11 files | [VERIFIED this session] | Generate or drop the number |
| 3 | "four hooks" in README, `setup.py`, `jade check`, repo `docs/08` 2.1; five in `ops.HOOKS`; repo `docs/09` already says five | [VERIFIED A8] | Fix; test against `len(ops.HOOKS)` |
| 4 | README status: "the daily maintenance job is loaded" | [VERIFIED; A8 section 11] | No live-state claims in README |
| 5 | BACKLOG W-85 says PostToolUseFailure is unregistered (it is registered); W-95 "open" though a test-side fix is on main | [VERIFIED A8] | Close W-85; reword W-95 "mitigated, root cause unproven" |
| 6 | `scripts/install-approver.sh` header says untested; W-90 and README say it ran once | [VERIFIED A8] | "tested on one macOS machine" |
| 7 | README latency "about 500 ms"; live p50 712 ms, p95 1395 ms | [VERIFIED A8 section 10] | Section 12 |
| 8 | Two repo `docs/08` sections numbered 1.3; repo `docs/09` says "installable first release" | [VERIFIED A8] | Fix |
| 9 | Work-item ids W-96 to W-105 mean one thing in CHANGELOG and docs on main and another in the maintainer's uncommitted backlog; `T30` was claimed by PR 42 and by PR 48 (PR 48 is now merged and keeps `T30`; PR 42 already uses `T31` and PR 46 `T32`; stale PR 43 and PR 45 still carry the old `T30`, d01 B7) | [VERIFIED A1 section 5] | Renumber before merge. PR 48 is on main, so PR 42 takes the next free `T`. |
| 10 | README says the key is a Requirement | section 2, correction 1 | Move to an optional note |

**Public fit.** [INFERENCE] The docs speak to one owner on one machine ("reference machine", "the Mac mini", "the human"). They mix design history with instructions. A newcomer needs, in order: what it is and for whom; a 60-second demo of one blocked command; the two readouts; install; what leaves the machine; how to leave.

[RECOMMENDATION] **README front page:** one paragraph; the two readouts; a transcript of a blocked command; a platform and harness matrix ("Claude Code only; macOS tested; Linux CI only; Windows no"); the data table (section 13); the trust root (section 9); then the status ladder. The baseline table and reading list move below. Owner-specific text moves to repo `docs/08`. Use short simple sentences, as the owner requires.

[RECOMMENDATION] Extend `tests/test_docs_truth.py` to compare numbers in prose with code (hook count, registry count). Also forbid live-state words ("loaded", "installed") in README. A few lines; closes items 2 to 4.

[RECOMMENDATION] Add a short FAQ: why not use Claude Code's own prompts or auto mode? Auto mode is probabilistic. An Anthropic post reports a 17% false-negative rate on real overeager actions [INFERENCE: vendor figure via summary] (R1 section 3.2). JADE offers deterministic rules, human-held permits and an audit ledger. Claim "different, and says where it stops", not "better".

## 8. Testing and CI

[VERIFIED A8 section 7; `ci.yml` and `release.yml` read this session] CI runs on `ubuntu-latest` only, Python 3.13 and 3.14, `uv==0.13.6`, `uv sync --frozen`: ruff, parallel pytest (`-m "not timing"`), serial `-m timing`, case replay, a replay smoke. `ci.yml` has no `permissions:` block, no `timeout-minutes`, no `concurrency`. Actions are pinned by major tag (`actions/checkout@v7`). `.gitleaks.toml` exists and no workflow runs it. No coverage, no type check. Dependabot covers Actions and uv weekly. The suite is large: 91 files, 1867 test functions, about 5850 cases in CI (CHANGELOG, self-reported).

Gaps, in the order a public user or contributor hits them:

1. **No macOS job, though macOS is the tested platform.** Keychain, `osascript`, launchd, `sandbox-exec` (PR 48's `evals/realshell.py`) and the dialog run only on the maintainer's Mac or through patched tests. [VERIFIED A8 section 7; A1] [RECOMMENDATION] One small serial job: `tests/test_setup.py`, hook payload tests, `scripts/verify_install.sh`. [UNRESOLVED] Whether real-shell payload tests may run on a hosted runner; keep them local until checked.
2. **Flaky and wall-clock tests.** W-95 (sqlite "database disk image is malformed", Linux 3.13) has a test-side mitigation (commit 34b51c8) and no proven root cause. `ledger._init` retries only on `locked` or `busy`. About 15 untagged assertions compare elapsed time with a bound. [VERIFIED A8 section 7] One CI fix was needed on the PR 47 branch (`c6ab27a`, not on main). Its message says a timing-sensitive test "compared with plain words, not with main". A8 reads this as a wall-clock problem; the commit message does not say so. [INFERENCE] [RECOMMENDATION] Tag the roughly 15 known tests `timing` once, now. Add a conftest guard only if a second untagged one appears. A new mechanism for a problem seen once is not worth it. Close W-95 only after a 200-run loop in `python:3.13-slim`, result recorded.
3. **No secret scan of the public repo.** The repo is already public (A-3). [RECOMMENDATION] Two parts. (a) Before any announcement, run gitleaks once over the full git history with the default config and no path allow-list, and look at every hit. A8 only grepped the tree on main for emails and keys. [VERIFIED A8 section 9] (b) In CI, run gitleaks on new changes with the existing `.gitleaks.toml`. It allow-lists `tests/` and `evals/` on purpose, because fake credentials live there. [VERIFIED `.gitleaks.toml`] So the CI test secret must be planted outside those two paths, or the test would pass for the wrong reason.
4. **CI hygiene.** [RECOMMENDATION] `permissions: contents: read`, `timeout-minutes`, `concurrency`, Actions pinned by commit SHA (Dependabot already keeps them current).
5. **Python 3.12 is untested.** It parses (A8) but never ran. [VERIFIED A8 section 4] [RECOMMENDATION] Keep the 3.13 floor: `uv` fetches 3.13, and a wider floor needs a wider matrix.
6. **Repository settings.** [RECOMMENDATION] Turn on GitHub private vulnerability reporting. SECURITY.md today says to use an email on the GitHub profile. [VERIFIED A8 section 14] Confirm branch protection on `main`. [UNRESOLVED] Not checked; the settings are not in the repository.

**Not a public blocker:** the 1091 tests skipped locally when the signed approver is installed. [VERIFIED CHANGELOG 0.5.0 line 66; `tests/conftest.py` `SUBPROCESS_APPROVE_MODULES`] That affects a maintainer with the approver, not a stranger. `pytest -rs` already prints skip reasons.

**Do not add** a coverage gate, type-check job or another distro. [INFERENCE] None has a demonstrated failure behind it. The existing guard tests are better value.

## 9. Release governance and supply chain

[VERIFIED A8 section 8; `release.yml` read this session] On a tag push the workflow checks that the tag equals `pyproject.toml` and `jade.__version__`. It runs ruff, full serial pytest and case replay. It builds wheel and sdist, installs the wheel in an isolated directory (`scripts/verify_install.sh`), writes `SHA256SUMS`, and publishes a GitHub release. Only the last job has `contents: write`. Tags v0.4.0 to v0.6.1 are annotated. Tags v0.3.0 to v0.3.3 are lightweight. No tag is signed. All 8 are ancestors of `origin/main`. [VERIFIED: `git cat-file -t <tag>`, `git for-each-ref` signature check and `git merge-base --is-ancestor`, re-run this session]

**Not enforced by the workflow:** that the tagged commit is on `origin/main`; that the CHANGELOG section exists (it falls back to "Release $tag. See CHANGELOG.md."); tag signing; provenance; any human gate. The rule "only from a merged `origin/main` with CI green" lives in repo `docs/09` and in habit. [VERIFIED A8 section 8] The workflow already re-runs ruff, pytest and case replay. So a red commit does not publish. A tag on a side branch would.

**Who uses what.** [VERIFIED A8 section 6] `jade upgrade` is human-only, shows changed files, asks, runs `uv tool install --reinstall git+https://...@<tag>`, then `jade check --offline`. It installs from the git tag. It never reads `SHA256SUMS`, an attestation or a tag signature. Rollback is `jade upgrade --ref vX.Y.Z`; no ledger downgrade, by design.

So provenance and signed tags would protect nothing the tool uses. A stranger also has no way to get the maintainer's public key today. [INFERENCE]

[RECOMMENDATION] Keep only the controls that stop a real, seen mistake.

| Rule | Step | Needs human approval |
|---|---|---|
| Tag commit is on `origin/main` | `release.yml` uses `actions/checkout@v7` with default depth and does not fetch `origin/main`. [VERIFIED: file read; no `fetch-depth` set] So add `fetch-depth: 0` (or `git fetch origin main`) first. Then `git merge-base --is-ancestor $GITHUB_SHA origin/main`, else fail | no |
| CHANGELOG section exists | fail instead of the fallback | no |

[RECOMMENDATION] Drop these from the first version:
- "CI green on that SHA" as a new step. The workflow already re-runs the checks. It would be a copy.
- A protected release `environment`. With one maintainer it is a pause, not a review.
- Build provenance and signed tags, until `jade upgrade` verifies them. Decide that first (H7). If `jade upgrade` will not verify, skip both. Then say the real trust root in the README: the maintainer's GitHub account and the git tag. repo `docs/09` already says checksums "catch a damaged download, not a compromised release". [VERIFIED A8 section 6] Say the same in the README.

[UNRESOLVED] Whether verifying the wheel checksum in `jade upgrade` is worth a second code path in an alpha. The cheaper option is to document the trust root.

**Release train.** [RECOMMENDATION, from A8 section 15] Land `design-clear-questions` and the stacked branches as one release (0.7.0), after renumbering `T30`/`T31` and the work-item ids. PR 47 is unsafe to merge at `c6ab27a`: its committed state is weaker than main on a glued here-string, and the fix is uncommitted in a worktree. [VERIFIED A1 section 7.2] That is a release gate here, not a DX topic. [Update, master plan check 2026-11-16: the PR 47 head on origin is now `c676801`; see d12 section 0 and V6-04.]

## 10. Observability, friction and accuracy

[VERIFIED A8 section 11; R4 section 2] The ledger is the audit trail. Tools: `jade ledger stats`, `jade decide list|show`, `jade permit list`, `jade review`, `jade knows list`, `jade check`, `jade version --json`, and `jade maintain` (one JSON line). There is no `logging` use, no metrics export, no OpenTelemetry (OTel), no hook log file. No ref has a hit for `opentelemetry|otlp|traceparent|readOnlyHint|elicitation`.

[INFERENCE] For one developer on one Mac the ledger is enough. Exports matter for teams and CI. No user has asked for one.

[RECOMMENDATION] Do one step now: `jade check --json` (section 6). `ledger stats` already prints JSON. [VERIFIED A8]

[RECOMMENDATION] Do not add these until a real consumer exists. Each has a trigger in section 17.
- A `tool_use_id` column on the decision row. Today it appears only in `jade/permits.py` for a 120 s replay. [VERIFIED R4 section 2] Claude Code emits an OTel event `claude_code.tool_decision` with `tool_use_id` and a `source` that includes `hook`. [INFERENCE, web page via summariser; R4 section 7, official monitoring page] A shared id would let a collector join the host event with JADE's reason. But a ledger migration is forward-only and cannot be downgraded. [VERIFIED repo `docs/09` section 2] So this is a one-way change. Make it only when someone needs the join.
- An OTLP export (`jade export --otlp-json`). The OTel GenAI conventions have **Development** stability; only `error.type` is Stable. [VERIFIED R4 section 7, primary source] A hand-written exporter would be a maintenance burden. The claim that they moved in semconv v1.42.0 on 2026-06-12 is secondary (a blog). Do not quote it. [VERIFIED R4 section 7]

If an export is ever built, this is the rule:

| Export | Never export by default |
|---|---|
| rule id, risk level, status, route, timings, cost, error kind, tool name, `tool_use_id`, session id (hashed if the file leaves the machine) | command text, paths in arguments, tool results, `asks.question_json`, human answer labels (all can hold project text) |

Use the `redact` module for any free text. Add a test that an export of a ledger with planted secrets contains none. Use stdlib only: no `opentelemetry-sdk` (it would break the three-dependency footprint). Run it from a human command, never inside the hook, because Claude Code does not pass `OTEL_*` to subprocesses. [VERIFIED R4 section 7]

**Friction and correctness must be shown together.** A8 measured that about 4.5% of gated calls reach the human (120 asks and approvals over 2666 gated calls in 14 days). It is one machine, and its developer is building JADE. [VERIFIED A8 section 11; INFERENCE that it will not generalize] That number has no accuracy number beside it. The project goal says fewer approvals are not better unless decisions stay correct. [RECOMMENDATION] `jade review` shows the asked share next to correctness signals the ledger can give: blocked then overridden by the owner; allowed then failed; Jev verdict versus the owner's answer. If the ledger cannot give a correctness signal yet, `jade review` shows the friction number with the line "correctness not measured" and does not show a friction number alone. [UNRESOLVED] Which of these signals the ledger can produce today; the sibling calibration section owns the definitions.

**Acceptance trial.** [RECOMMENDATION] Before any announcement, one person who is not the maintainer installs JADE on a clean macOS user and in a clean Linux container, using only the README. Record: number of steps, minutes, questions asked of the owner, and whether the first block message was understood. This measures the section's aim. Nothing else in the checklist does. The result is stored beside the performance results. Pass rule: no owner question was needed for install, and the person can say in their own words why the first command was blocked.

## 11. Harness portability and optional dependencies

**Harness.** [VERIFIED A8 section 3] Only Claude Code is gated. Other agents appear only as things to refuse (`util.AGENT_ENV`, `_AGENT_PROGRAMS`) and as protected instruction files (`AGENTS.md`, `.cursor/rules`). The harness-neutral part is the `jev` and `jade ask` exit codes, which enforce nothing.

R1 compared other harnesses. [INFERENCE from R1 section 5, summary-level sources, moderate confidence; R1 itself labels this a portability reading] Claude Code, Codex and Cursor each seem to have a pre-tool hook where exit code 2 blocks, but the JSON shapes differ. The fail-open point for Codex is only from a hooks page summary. The Cursor rows come from a summary, because `docs.cursor.com` redirected. Gemini and Cline are mostly unverified. [VERIFIED R1 section 5 for Claude Code only: `onFailure: "block"` exists on host 2.1.295 or newer.] Aider has nothing to hook.

[RECOMMENDATION, challenge to A8] A second harness adapter is **not in v6**.
1. JADE's strongest promise is fail-closed on a crashed gate (probe cell 9, R1). On Codex it may not be possible with the documented hook, so an adapter could be a weaker product under the same name. [INFERENCE]
2. Chat approval and the macOS dialog are Claude Code specific. [VERIFIED A8, R1 section 3.12]
3. `hooks.py` mixes event parsing and decisions, so an adapter needs a refactor first. [VERIFIED A8 section 3]

If a second harness becomes a goal: split `hooks.py` into parse, decide, render. Cursor is a candidate first target only if Cursor's own docs confirm a `failClosed` option. That is not yet confirmed. [UNRESOLVED]

Cheap now: say "Claude Code only" in the first screen. Document `jev` as harness-neutral and advisory. Put the Jev section for `AGENTS.md` in a document, not in `jade setup`. Reasons: Claude Code reads `AGENTS.md` only when no `CLAUDE.md` exists [VERIFIED R4 section 5], so the step would serve agents JADE does not gate. It would also add a write to a protected instruction file (R-13, R-18) and an eighth action to a wizard with `TOTAL = 7` and tests. [VERIFIED `jade/setup.py`] Reopen only if a second harness becomes a goal.

**Dependencies.** [VERIFIED A8 section 4] Runtime: `jsonschema>=4.26.0,<5`, `pyyaml>=6.0.3,<7`, `referencing>=0.37.0,<1`; no extras. External programs: `git`, `uv` (upgrade), `ssh-keygen` 8.1+ (signing); macOS only `security`, `osascript`, `launchctl`. [RECOMMENDATION] Keep three runtime dependencies. Reject any that is added for convenience (OpenTelemetry SDK, HTTP client, TUI). Add `[project.optional-dependencies]` only when a real optional feature exists. `jade check` should name missing external programs.

## 12. Performance overhead

- **Gate on every tool call.** The 2026-11-09 synthetic run (100000 `jev_calls`, 176 MB, macOS) measured the whole hook process. [VERIFIED: `evals/performance/result-ledger-100k-2026-11-09.json`] It **predates 0.4.0 to 0.6.1** and is synthetic. Two columns, labelled:

  | PreToolUse case | Empty ledger p50 / p95 | 100k ledger p50 / p95 |
  |---|---|---|
  | ungated read | 114 ms / 156 ms | 110 ms / 119 ms |
  | gated, pending permit | 152 ms / 166 ms | 150 ms / 162 ms |

  A8 re-measured only the in-process classifier on 0.6.1: 0.3 to 4.1 ms warm, 24.7 ms cold; `import jade.hooks` about 32 ms. A full hook process on 0.6.1 was not re-measured because it writes the live ledger. [VERIFIED A8 section 10]
- [INFERENCE] The gate costs about 100 to 150 ms per tool call, mostly Python start and imports. A session of 300 tool calls spends about 30 to 45 s in hooks. Acceptable.
- **Live Jev.** Harness (323 calls, quiet network): p50 514 ms, p95 639 ms. Live ledger on the maintainer's Mac (369 calls, 8 active days): p50 712 ms, p95 1395 ms, p99 2358 ms. The documented budget is 1.0 s. [VERIFIED A8 section 10] [INFERENCE] "Budget met" holds for the harness only. Under enforce at levels low, medium and high, Jev runs inside the hook with one try and a 6 s budget (`hooks.BUDGET_S`). At `high` it runs in shadow, so the call still waits for the answer even though the answer is not used. [VERIFIED `jade/proxy.py` `step`: `_ask` runs before the `level == "high"` shadow return] Each such call adds roughly 0.7 to 1.4 s.
- **Ledger scale.** 100k rows is about a year at the maintainer's rate (about 330 gated calls per active day). A test fails when a hot query scans a table. W-76: the maintain job holds the lock linearly with rows (first purge at 100k took 667 ms; scrub and vacuum 570 ms; the hook waits up to 1.5 s). 1M rows is unverified. [VERIFIED A8 section 10]

[RECOMMENDATION]
1. A human re-measures the hook process on the current release in a throwaway home (agents cannot set `JADE_HOME`, SI-11).
2. Keep the 1.0 s budget in repo `docs/04`. It is a target, not a runtime limit. Do not raise it to fit the live number: that makes "budget met" true by moving the goal. Publish live p50, p95, n and date next to it, in `jade review` and the README.
3. A timeout that falls back to asking the owner changes autonomy behavior. It is a proposal that needs human approval, and it belongs to the sibling autonomy section. The real runtime limit already exists (`hooks.BUDGET_S`, 6 s).
4. Skip the hook `if` field in v6: it only helps speed. [INFERENCE from R1 section 3.1]

## 13. Privacy and telemetry controls

**What leaves the machine.** [VERIFIED A8 section 9, from reading every network use in `jade/*.py`; hook call site read in `origin/main:jade/proxy.py` `step`]

| Destination | What | When |
|---|---|---|
| `https://openrouter.ai/api/alpha/decisions` | redacted state and question payloads, model id, API key in the header | `jev`, `jade ask`, the `jade check` canary, `jade maintain` (live), and the Jev call in the hook (see conditions below) |
| `api.github.com`, `raw.githubusercontent.com` (and `github.com` through `uv`) | release metadata, changelog, the install | `jade upgrade [--check]` |
| nothing else | no analytics, crash reports or phone-home | |

**Conditions for the hook call.** [VERIFIED `jade/proxy.py` `step`] The proxy asks the provider only when all of these hold: gate mode is `enforce`; level is `low`, `medium` or `high`; there is a session id; the call is not in the never-sent list; redaction did not change the text; the owner has written preferences or has a confirmed answer for the rule; the call was not denied before and is under the attempt limit; there is a key. At `high`, and at `low` or `medium` until the owner has answered 50 distinct plain-high calls (`ops.JEV_BAND_ANSWERS = 50`, `jade/ops.py` line 687; the shadow rule is in the `proxy.py` docstring), Jev is in **shadow**. In shadow the call is still sent. It is asked and recorded, and decides nothing. So data leaves the machine at levels low, medium **and high**, including in shadow. Data does not leave from the hook when gate mode is not enforce, level is off or unset, or no key exists.

The provider URL is a constant (`adapter.URL`): no config or environment override, so no self-hosted or alternative provider. The state with no key, no level and no enforce sends nothing from the hook. [VERIFIED A8 sections 2, 9]

**Redaction is for secret shapes, not private code.** [VERIFIED A8 section 9]
- `ask.py` redacts secret shapes from state and every question payload.
- It caps the payload at 32 KiB.
- It refuses the call (exit 6, "nothing was sent") if a secret-shaped string survives.
- The Jev proxy sends up to 4 KiB of the command or written content, owner preferences and up to 8 past answers. repo `docs/12` section 10 says "redacted for secret shapes only; ordinary text is not changed". `proxy.MAX_TEXT = 4096`.
- Never-sent classes are in `proxy.py`: human-only classes, critical calls, fetch-and-exec, instruction files, secret-shaped values, merges, programs over 4 KiB.

So **ordinary source code and commands do go to a third party** at levels low, medium and high, when the conditions above hold. [INFERENCE, direct from the above] A public user must read this before turning a level on.

| # | Gap | Evidence | Fix | Human approval |
|---|---|---|---|---|
| DX-P1 | `ops.purge` clears `jev_calls` text, `decisions` objective, `permits` scope subject and `events` detail. It does **not** touch `asks.question_json` (the chat question, with a "Command:" block), `asks.label`, or `human_answers.label` (up to 120 characters of the call). No code deletes from those tables. | [VERIFIED: `ops.purge` read at `origin/main`; the diff of `design-clear-questions` and `open-items-review-output` does not change `purge` and adds more text columns: `scope_label`, `scopes_json`, `family_key`] | Scrub those fields. A test plants a command in each table and checks it is gone | **yes**: deletes stored data. Confirmed answers have a 30 to 180 day lifetime in the unmerged scopes work, so the owner says whether the label may go while the answer stays |
| DX-P2 | Retention runs only in `jade maintain`. The scheduler helper is launchd only. On Linux nothing purges unless the user schedules it | [VERIFIED A8 section 9; DX-D4] | Generate a systemd timer or cron line on request. `jade check` shows the age of the oldest stored command text | no |
| DX-P3 | No provider data-terms page. Retention and training terms of OpenRouter and TypeSafe for the alpha endpoint are not in the repo | [VERIFIED A8 section 9] | One page: what is sent, to whom, what JADE cannot promise; link their own terms; invent none | yes (owner confirms wording) |
| DX-P4 | Single provider, constant URL | [VERIFIED A8] | **Not an environment variable** (an agent could set it). If wanted: a human-only config key with an allow-list, shown in `jade check` | **yes**: new egress path |
| DX-P5 | Public hygiene: the local username appears in four test or eval files (for example the label `com.example.jade.maintain` in `evals/adversarial/cases.jsonl` A57, A62; `tests/fixtures/redact_twins.json`). repo `docs/10` and README name "the Mac mini" and "reference machine". No emails or keys found on the main tree | [VERIFIED A8 section 9] | Neutral labels, through a reviewed PR (the cases file is a protected policy file). Plus the full-history scan from section 8 | no |

Controls that exist: no key (no Jev call), gate not enforce, level `off` or unset (no proxy call from the hook), `jade ledger purge --older-than N`, `retention_days` (default 90, read by `jade maintain`), mode 600 files, `secure_delete=ON`. [VERIFIED A8 sections 5, 9] `evals/baseline/mine_transcripts.py` reads `~/.claude/projects`; the dataset stays local and `.gitignore` excludes it. [VERIFIED A8]

[RECOMMENDATION] State in the README: "With no key, or with level off or unset, JADE sends nothing from the hook. `jade upgrade` contacts GitHub." Add a `jade check` row "egress: none | provider | provider+github", derived from gate mode, level and key (the autonomy readout in section 3).

## 14. Packaging and naming

[VERIFIED A8 section 4; `pyproject.toml` read this session] Distribution `jade-decision-governance`; console scripts `jade` and `jev`; wheel packages `jade` **and `policies`**. W-28 (P3, open) tracks the rename, trigger "before a PyPI release or a second consumer". Not on PyPI: `release.yml` has no publish step. The `jev` script can collide with an existing `jev`, and then `uv` installs nothing. Windows is not supported; Linux is CI only.

[INFERENCE] **Does PyPI block adoption?** For a public alpha aimed at Claude Code users who have `uv`, no. `uv tool install git+https://github.com/...@v0.6.1` works with a public repo and no account. PyPI helps discovery. But it freezes a distribution name and a top-level module name in front of strangers.

**W-28 is a safety change, not only a rename.** [VERIFIED: `git grep` on `origin/main`: `jade/hooks.py` line 57 has a path regex that protects `policies/`; `jade/improve.py` and `jade/ask.py` read `policies/*.yaml`] The policy files hold the safety invariants and risk rules. A rename that misses one path list could silently drop protection of the safety rules. A rename is also a breaking change under the 0.x minor rule. [RECOMMENDATION, needs human approval] Treat W-28 as a safety change: an adversarial test case that tries to edit the renamed policy directory must still block. Or say W-28 is out of scope for v6 and keep the package name until PyPI is decided. I recommend out of scope for v6 (H6). Do not reserve a PyPI name before the final name is chosen. [UNRESOLVED] Whether `jade-decision-governance` is free on PyPI; not checked. Keep `jev` as a compatibility shim; document `jade ask` as the supported name.

## 15. What blocks public adoption, ranked

**Ranking rule.** [RECOMMENDATION] First: anything where JADE says something untrue or breaks a stated promise about safety or data. Second: anything that stops a stranger getting value in the first hour. Third: trust and supply chain. Fourth: reach. Within a class, cheaper fixes first.

| Rank | Blocker | Class | Evidence | Cost |
|---|---|---|---|---|
| 1 | Retention promise not kept for `asks` and `human_answers`; not scheduled off macOS; check row green | untrue promise | DX-P1, DX-P2, DX-D4 | small |
| 2 | `jade check` says `ok` for a mostly advisory gate (level off, and advisory with level unset); "all four" hooks; "every hook inert" | untrue statement | DX-D1 to DX-D3, DX-D6 | small |
| 3 | README omits 0.6 features, says the job is loaded, wrong numbers, no data page | untrue or missing | section 7 | small |
| 4 | Ordinary code and commands go to a third party at levels low, medium and high (also in shadow); no provider terms; not in the first screen | undisclosed flow | section 13 | small |
| 5 | Public history never scanned for secrets; no private vulnerability reporting; branch protection unchecked | trust, already public | section 8 | small |
| 6 | No acceptance trial: nobody has shown a stranger can install and understand it | first hour, unmeasured | section 10 | small |
| 7 | Install reads as a 6-step power-user path with an account; value not shown | first hour | sections 2, 3 | small to medium |
| 8 | Bare `hooks install` gives enforce with terminal-only approval and prints nothing about it | silent default | section 2 | small |
| 9 | Release rules in prose (tag on side branch not caught; no changelog check); no CI `permissions:`; unpinned Actions; gitleaks unused | trust | sections 8, 9 | small |
| 10 | No macOS CI; flaky and wall-clock tests | trust, contributors | section 8 | medium |
| 11 | Merge pile: id collisions, PR 47 weaker than main as committed, dirty local checkout | release hygiene | A1 | medium |
| 12 | Claude Code only; one provider; macOS only in practice | reach | section 11 | large |
| 13 | Not on PyPI; package named `policies` | reach, naming | section 14 | medium |
| 14 | Value unproven: README says VALIDATED = No | evidence | A8 section 14 | needs time |

[INFERENCE] **Check against the brief.** PyPI (rank 13) and multi-harness (rank 12) do not rank high under this rule. A newcomer forgives "Claude Code only, macOS first, install from git" if the page says it. They do not forgive a false retention promise, or a check that says "ok" on a gate that is mostly off. A8 put "power-user install" first. I place it lower because it is a limit that can be stated. Ranks 1 to 4 are statements that are wrong or missing. I changed one item from the first draft: the bare enforce default is no longer called "unsafe". Enforce is the stricter mode. The problem is that nothing tells the user (rank 8).

## 16. Minimal public-ready checklist

Owner: H human, A agent with human review. Flag: S safety, P privacy, R release (needs human approval before work).

| # | Item | Owner | Verify (outcome, not only artifact) | Flag |
|---|---|---|---|---|
| 1 | `ops.purge` covers `asks` text, `asks.label`, `human_answers.label`, new scope labels | A | Test plants text in each table; none survives purge | P |
| 2 | Retention on Linux: generated timer or cron line; `jade check` warns when absent | A | Generator test; check row test in both states | P |
| 3 | `jade check`: D1 and D6 warnings, D2 text, D3 count from `len(ops.HOOKS)`, D4 warning, `--json`, the two readouts | A | A test per row, both states; a table test over every dial combination; JSON schema test | S (D1, D6) |
| 4 | README front page: two readouts, platform matrix, demo transcript, data table, trust root, honest latency with date and n | A drafts, H approves | Docs-truth test extended for counts and live-state words; "what blocks" prose generated from the real-hook table test | none |
| 5 | Fix the doc contradictions of section 7; close W-85; reword W-95 | A | `tests/test_docs_truth.py` | none |
| 6 | Privacy page: what is sent (levels low, medium, high), redaction limits, provider terms not controlled by JADE | A drafts, H confirms | Reviewer checks each line against `adapter.py`, `proxy.py`, `ask.py` | P |
| 7 | `jade hooks install` prints effective gate mode, what it blocks and the approval backend. No default change | A | Unit test of the printed text per mode | none |
| 8 | One-time full-history gitleaks run, default config, no allow-list; every hit reviewed | H runs, A reports | Report lists each hit and its disposition | P |
| 9 | Turn on private vulnerability reporting; confirm branch protection on `main`; update SECURITY.md | H | Setting visible in repository settings; SECURITY.md matches | R |
| 10 | Release workflow: `fetch-depth: 0`, ancestry check, changelog section required | A | Dry-run tag on a fork; a tag on a side branch fails; a tag with no changelog section fails | R |
| 11 | `ci.yml`: `permissions`, `timeout-minutes`, `concurrency`, SHA-pinned Actions, gitleaks on new changes | A | CI green; a fake secret planted outside `tests/` and `evals/` in a scratch PR fails the scan | none |
| 12 | macOS CI job (setup, hook payloads, `verify_install.sh`) | A | Green on a PR | none |
| 13 | Tag the known timing tests `timing`; W-95 200-run loop in `python:3.13-slim` recorded | A | Result stored in the work item | none |
| 14 | Renumber the PR 42 threat row (now `T31`; main keeps `T30`; PR 43 and PR 45 rebase) and the dirty work-item ids; verify PR 47 at its current head `c676801` (d12 V6-04); one 0.7.0 train | H orders, A executes | `git merge-tree` shows only expected conflicts | R |
| 15 | Neutral labels in fixtures; move "Mac mini" text to repo `docs/08` | A | grep finds none | none |
| 16 | Re-measure hook latency on the current release; publish live p50, p95, n, date | H runs, A reports | Result file beside the 2026-11-09 file | none |
| 17 | `jade review` shows friction beside correctness signals, or says "correctness not measured" | A | Output test; sibling calibration section defines the signals | none |
| 18 | Acceptance trial with one non-maintainer on clean macOS and clean Linux | H | Record: steps, minutes, owner questions, first block understood | none |

Items 1 to 9 are the "no false statements and no hidden exposure" set. They ship before any announcement. Items 10 to 13 are the trust set. Items 14 and 15 are the release set. Items 16 to 18 are the measure set; 18 ships before the announcement.

## 17. What should not be built (and what would reopen it)

| Not built | Why | Trigger that reopens it |
|---|---|---|
| A `jade quickstart` command, or a one-consent `--yes` | `jade setup --yes` already prints every change and undo. One consent for seven edits is a consent change | An owner decision, with a new flag name |
| `jade explain` | Level behavior is not computable offline; it is a bypass oracle | A user need, plus a design that shows only rule id and risk level in a human terminal |
| `jade config show` | No demonstrated need; could print `owner_emails` | Folded into `jade check --json` instead; reopen if users cannot find a key |
| Three-tier ladder or a tier config key | Mixes safety and autonomy; does not cover all dial combinations | Never; use the two derived readouts |
| A conftest timing guard | One past incident; brittle | A second untagged timing test appears |
| An `AGENTS.md` step in `jade setup` | Serves agents JADE does not gate; writes a protected file | A second harness becomes a goal |
| A protected release environment | One maintainer: a pause, not a review | A second maintainer |
| Provenance attestation and signed tags | `jade upgrade` verifies neither | `jade upgrade` is changed to verify them (H7) |
| A `tool_use_id` column | One-way ledger change; no consumer | A named consumer that needs the join |
| An OTLP export, or the OTel SDK | Development-stability conventions; no user; breaks three-dependency footprint | A team user with a collector, and conventions that are Stable |
| A second harness adapter, MCP server or proxy for portability (R4 advises staying hook-based) | Weaker fail-closed story; refactor first | Owner picks a second harness and its own docs confirm fail-closed |
| A plugin API, daemon, web dashboard, TUI, config schema or settings-migration layer, hook log file | [INFERENCE] no demonstrated need | A repeated user request with evidence |
| MCP elicitation or tool annotations as an approval or trust input | An `Elicitation` hook can answer for the user, so it is not human-only [VERIFIED R4 section 3.5] | Never as an approval input |
| Usage telemetry or phone-home from JADE | Breaks the data promise | Never by default |
| A coverage gate or type-check job | No failure behind it | A bug class they would have caught |
| An environment-variable override for the provider URL, or any setting an agent can change | An agent could set it | Never; only a human-only key with allow-list (H5) |
| PyPI publishing before the name decision | Freezes names | Owner decides name; W-28 resolved |
| A "CI green" release step | Duplicates `release.yml` | The release workflow stops re-running tests |

## 18. Decisions only the human can make

| # | Decision | Recommended default |
|---|---|---|
| DX-H1 | Public alpha target: "Claude Code, macOS tested, Linux CI only", or wider? | The first; say it in the first screen |
| DX-H2 | May `ops.purge` scrub `asks` and `human_answers` labels while the confirmed answer stays? | Yes: scrub label text, keep hashes and the decision |
| DX-H3 | What `level off` and advisory-with-level-unset report in `jade check` (and, owned by the security section, what they block) | Warning now; blocking follows the security decision |
| DX-H4 | Default gate mode when `jade hooks install` runs with none set | No change in v6: print the effective mode and what it blocks. A move to advisory is a separate safety proposal |
| DX-H5 | May a human-only config key set another provider URL? | Not in v6 |
| DX-H6 | PyPI: yes or no, and which name. W-28 in v6? | Not in v6. If PyPI, W-28 first, with an adversarial test on the policy directory |
| DX-H7 | Will `jade upgrade` verify tag signatures or attestations? If not, skip signing and provenance | Not in v6; state the trust root in the README |
| DX-H8 | Who confirms provider data-terms wording (OpenRouter, TypeSafe)? | The owner, from their own terms; JADE states only what it sends |
| DX-H9 | `T30` owner and work-item renumbering | `T30` stays with PR 48 (merged); PR 42 uses `T31`, PR 46 `T32` (d01 B7); renumber the dirty backlog from W-117 (A1) |
| DX-H10 | Should `jade setup` set level `off`, so SI-09 to SI-12 are firm from day one, or print that they are not firm until a level is set? | Print the notice now; setting level is the owner's call |
| DX-H11 | Any timeout that falls back to asking the owner (section 12) | Not in v6 from this section; sibling autonomy section decides |
| DX-H12 | Who is the acceptance-trial person, and what is the pass rule | A developer outside the project; pass rule as in section 10 |

## 19. Unresolved

- [UNRESOLVED] Whether `jade setup` without a key completes all seven steps on a real run (code reading only).
- [UNRESOLVED] Whether `policy.assess` touches the ledger on any path (needed before any `jade explain`); whether `tests/test_ledger_migration.py` opens a previous-release ledger.
- [UNRESOLVED] Whether PR 48's real-shell sandbox tests may run on a hosted macOS runner.
- [UNRESOLVED] Remote branch protection and required reviews; CI history for the W-95 flake (A8); whether the PyPI name is free.
- [UNRESOLVED] The Linux branch of `scripts/install-approver.sh` and every Linux step beyond CI: never run (A8).
- [UNRESOLVED] Live hook-process latency on the current release; needs a human step because of SI-11.
- [UNRESOLVED] Facts about Codex, Cursor, Gemini and Cline rest on summaries read by R1; do not quote them as verified.
- [UNRESOLVED] Which correctness signals (override rate, allowed-then-failed, Jev versus owner) the ledger can give today.
- [UNRESOLVED] What the full-history secret scan will find; not run.
