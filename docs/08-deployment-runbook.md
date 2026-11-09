# 08 — Deployment runbook

Installing a release, upgrading, the versioning policy and a complete uninstall are in `docs/09-install-and-upgrade.md`; this runbook is the manual wiring and its undo.

What is deployed, what only a human can do, how to verify each step, and how to undo it. Written for a single macOS host; paths are the defaults.

## 1. Already done by the agent (reversible, local)

| Step | Command | Undo |
|------|---------|------|
| Install the package as a uv tool (`jade`, `jev`) | `uv tool install .` from the checkout | `uv tool uninstall jade-decision-governance` |
| Keep the original script | `cp -p ~/.local/bin/jev ~/.local/bin/jev.orig` before installing | `rm ~/.local/bin/jev && mv ~/.local/bin/jev.orig ~/.local/bin/jev` |
| Private policy overlay | copy the gitignored `policies/local/*.local.yaml` to `~/.config/jade/local/` | delete that directory |
| Ledger | created on first call at `~/.local/share/jade/jade.sqlite`, mode 600 | `rm -r ~/.local/share/jade` (loses history) |

Measured rollback drill (2026-11-09, real binaries): shim to original in 1.1 ms, back in 0.1 ms.

### 1.1 Re-install after every merge (W-50)

`uv tool install .` copies the code into the tool's own site-packages. A merge or `git pull` in the checkout changes nothing that runs: the hook, `jade` and `jev` keep using the installed copy until you re-install.

| Step | Command | Check |
|------|---------|-------|
| Re-install from the checkout after any change to `jade/`, `policies/`, `registry/` or `schemas/` | `uv tool install --reinstall .` from the checkout | `jade check`: row `code version` shows the new version number |
| Let `jade check` compare the installed tool with the checkout | `export JADE_CHECKOUT=/path/to/checkout`, or set `"checkout": "/path/to/checkout"` in `~/.config/jade/config.json` | `code version` fails when the checkout's `pyproject.toml` version is newer than the installed one |

The code version (`jade.__version__`, plus the git commit when run from a checkout; an installed package has no commit) is part of `policy_version`. Re-installing a different version therefore changes `policy_version`, which revokes unapproved-use permits bound to the old value, the same as a policy edit does. The `code version` row cannot see a change that keeps the version number: bump `version` in `pyproject.toml` and `jade/__init__.py` when a fix must reach the installed tool, and re-install.

### 1.2 Upgrade from a release (REL-1)

An install from a release (not from a checkout) is upgraded with `jade upgrade`, in your own terminal. Like `jade hooks install` it replaces the code that decides every gate call, so it refuses without an interactive terminal and for an agent ancestor, and SI-09 blocks it (and `jade setup`, section 2.0) in an agent's command. `jade upgrade --check` only reads and is not blocked.

| Step | Command | Check |
|------|---------|-------|
| See what is installed | `jade version` (also `jade --version`; add `--json` for scripts) | code version, commit (the checkout's HEAD, or the commit uv recorded for a git install), ledger schema version, `policy_version` |
| Look for a newer release | `jade upgrade --check` | installed against latest, plus the CHANGELOG headings in between; exit 1 when GitHub cannot be reached (offline, timeout, rate limit, no release yet) |
| Install the latest release | `jade upgrade` | runs `uv tool install --reinstall git+https://github.com/vahidkaargar/jade-decision-governance@<tag>`, then `jade check --offline` from the new install |
| Install a named release or commit, or roll back | `jade upgrade --ref v0.3.1` (a `vX.Y.Z` tag or a full 40-hex commit; a branch name is refused) | same as above; an older tag is reported as a rollback |

What it does and does not do: the repository URL and scheme are constants; the ref must be a release tag (`vX.Y.Z`; a bare `X.Y.Z` is read as the tag `vX.Y.Z`) or a full commit; the install is an argument list, never a shell; the only network calls are to `api.github.com` and `raw.githubusercontent.com` over https with a 5 second timeout and no token. The upgrade itself opens no ledger and writes no configuration, and it never edits `~/.claude/settings.json`; the `jade check --offline` it runs afterwards is the first command of the new code, so it opens the ledger, may apply an additive migration and records `last_check` (docs/09 section 3). After the install it reads the hook rows of `jade check` (`hooks installed`, `hook wiring`, `pretool matcher`): when one fails, the next steps say to re-run `jade hooks install` and open a new Claude Code session; when none does, it says no hook change is needed. `jade check` without `--offline` runs the live provider canary; do that yourself afterwards. The new code changes `policy_version`, which revokes unapproved-use permits bound to the old value (section 1.1).

### 1.3 Cut a release (REL-3)

A release is a tag. Bump `version` in `pyproject.toml` and `__version__` in `jade/__init__.py`, move the `[Unreleased]` entries of `CHANGELOG.md` under a `## [X.Y.Z] - date` heading, merge to the default branch, then `git tag -a vX.Y.Z -m "X.Y.Z"` and push the tag. `.github/workflows/release.yml` runs on that push: it refuses a tag that differs from either version, runs `ruff` and the suite, builds the wheel and sdist with `uv build`, installs the wheel into an isolated directory and runs `jade --version`, `jade check --offline` and `jade setup --status` from it (`scripts/verify_install.sh`; only the `hooks installed` row may fail on a fresh machine), writes `SHA256SUMS`, and creates or updates the GitHub release with the three files and the CHANGELOG section as notes. Only that last job has `contents: write`. Run `sh scripts/verify_install.sh` yourself before tagging; it needs no network when uv's cache has the build backend and the dependencies (`UV_OFFLINE=0` allows downloads) and never touches your `~/.claude`, ledger or keychain. `tests/test_ledger_migration.py` opens a ledger with the 0.2.2 schema (`tests/fixtures/ledger_0_2_2.sql`) with the current code; when a release changes the ledger schema, snapshot the previous DDL the same way.

### 1.3 Test and verification runs never use the live ledger (W-72)

The installed hook and `jade` write to `~/.local/share/jade`. A synthetic command fed to them (a fake `terraform destroy`, a probe of a rule) leaves a real pending permit and a `WAITING_HUMAN` decision there, in the same queue as the approvals you owe. Three rules keep test noise out:

| Who | Rule |
|-----|------|
| The test suite | `tests/conftest.py` fails any test that reaches the real JADE home (no `JADE_HOME`, or one that names it) before anything is created, opened or migrated. Every test gets its own temporary home. Run `uv run pytest`; never point a test at `~/.local/share/jade`. |
| An agent brief that verifies code | Say so in the brief: "verify with `uv run pytest` or the Python API; do not pipe a synthetic command into the installed `jade hook ...` or `jade permit ...`". An agent cannot set `JADE_HOME` for a CLI probe: SI-11 blocks the assignment on purpose (docs/05 T19). A CLI or hook probe that must run is run by you in a terminal with `JADE_HOME=$(mktemp -d) JADE_CONFIG=$(mktemp -d) jade ...`. |
| A probe that has to reach the live hook anyway | Prefix it with `JADE_SOURCE=test`. Every row the process writes (Jev calls, decisions, permits, events) carries `source = 'test'`. The gate decides exactly as without the tag. `jade ledger stats`, the miner, the calibration labels and the advisory-week counts leave tagged rows out, the SessionStart hook reports them as "test-tagged ... not real approvals" instead of "awaiting human approval", and `jade permit list` marks them `[test]`. Any other value of `JADE_SOURCE` is ignored. |

The tag is a statistics label, not a security control: an agent can tag its own real rows, which hides them from the counts and the notice (a tag that is in force in the hook environment is listed by `jade check` and shown at SessionStart as a weakening override, so a persisted one is not silent) (not from `jade permit list`, `jade decide list` or the gate, which never read the tag). Do not approve a permit only because it carries or lacks the tag; read the command on the approval screen.

To clear what a test run left behind, in your own terminal:

```bash
jade ledger purge --test-rows                       # removes every row tagged source=test, and writes one audit event with counts
jade ledger purge --test-rows --id prm_x --id dec_x # first tags rows written before the tag existed (a permit, a decision, or both) as test, then removes them
```

`--id` takes the exact `prm_` or `dec_` id shown by `jade permit list` or `jade decide list`; it tags the permit or decision, the decision or permits it belongs to, and their events. Rows with no tag that you did not name are never removed. The command needs a human terminal and never reads or keeps command text. The daily `jade maintain` job does not run it.

## 2. Human-only steps

These change what an agent may do on this machine. The runtime refuses them for an agent session on purpose (invariant SI-09, `docs/07` section 3), so run them in your own terminal. Order matters.

### 2.0 First run in one command: `jade setup`

`jade setup` does sections 2.1, 2.3, 2.4 and 2.6 below, one step at a time, in your own terminal. Use it on a new machine; the manual commands stay the reference and the way to undo.

```bash
jade setup --status      # read-only (any caller): what is done and what is missing
jade setup               # the wizard: each step shows the exact change and its undo, then asks y/N
jade setup --yes         # same steps without the questions; still needs an interactive terminal
```

| Step | What it does (through the function the command in the section already uses) | Skipped when |
|------|------|------|
| 1 | Checks Python, `uv` and that the OpenRouter key exists (Keychain item `OPENROUTER_API_KEY`, or the environment variable). It never reads or prints the value; when it is missing it prints how you add it yourself | never (informational, a missing key does not fail the run) |
| 2 | `jade gate advisory` (2.1) | `gate_mode` is already `advisory` or `enforce` in `~/.config/jade/config.json`; a configured enforce is never lowered |
| 3 | `jade hooks install` (2.1) | all four hooks are installed and the PreToolUse matcher is current |
| 4 | `jade claude-md apply` (2.3) | the Jev section already equals the shipped text; not applicable (and not an error) when CLAUDE.md or the section does not exist: add the output of `jade claude-md show` yourself |
| 5 | Copies the skill (`skills/jade/SKILL.md`, shipped in the wheel as `jade/_skills`) to `~/.agents/jade/SKILL.md` and links `~/.claude/skills/jade` to `~/.agents/jade` | file and link already match. A different existing skill file is shown as a change, copied to `SKILL.md.bak-jade-<time>` next to it and replaced when you say y (this is how a release that improves the skill reaches you; `--yes` does the same). A link or a different path is never overwritten: the step is refused, the run exits 1 and says what to compare |
| 6 | macOS only: writes `~/Library/LaunchAgents/com.jade.maintain.plist` from `jade launchd-plist`, then offers `launchctl bootstrap gui/<uid> <plist>` (2.4) as a second question | not macOS; a `jade maintain` job already exists and is loaded |
| 7 | `jade check` offline (the live provider canary is yours to run) and a summary of what is active and what is not | never |

Rules it keeps: it refuses without an interactive terminal, for an agent environment variable and for an agent ancestor (the same test as `jade permit approve`, docs/05 T2), before it reads or writes anything; `--yes` is not a way around that. A declined step changes nothing and the next steps continue. Each step records `setup.<step>` (outcome, time, version) and `setup.last_run` in the ledger meta; `--status` opens no ledger and writes nothing. A second run changes nothing (the ledger records are the only write). The exit code is 1 when a step failed or was refused, or the final check failed; a declined or not applicable step is exit 0.

It does not set enforce mode. Advisory is the starting state (2.1); moving to enforce is your decision after the soak (2.2). To update the skill after `jade upgrade`, run `jade setup`: it shows the skill step as needed, asks, keeps the old copy as `SKILL.md.bak-jade-<time>` and writes the shipped one. Setup refuses to touch a skill file that is a symlink, and a `config.json` that is not a JSON object (it never rewrites a file it could not read: fix or move it, then run `jade setup` again).

Undo: section 3, plus `rm ~/.claude/skills/jade ~/.agents/jade/SKILL.md`.

### 2.1 Start in advisory mode

```bash
jade gate advisory
jade hooks install
```

`hooks install` backs up `~/.claude/settings.json` next to itself (`settings.json.bak-jade-<time>`; a second run in the same second gets a `-1` suffix, a backup is never overwritten), writes the file atomically (temp file in the same directory, then rename; a symlink stays a symlink and the target keeps its permission bits, a new file is 0600), adds four hook entries (PreToolUse for Bash, PowerShell, Monitor, file edits, RemoteTrigger and MCP tools; PostToolUse for Bash; Stop; SessionStart). An install made before the matcher grew shows `pretool matcher` as FAIL in `jade check`; run `jade hooks install` again, and leaves other hooks alone. Hooks are read at session start: open a new Claude Code session afterwards.

In advisory mode the gate never blocks. It records a `would_block` event (or a `safety_block` event when a safety invariant such as SI-08, a push to the default branch, would have stopped the call) and prints what it would have done, so you can see the real false-positive rate before it can interrupt work.

Verify:

```bash
jade hooks status        # four lines, all "installed"
jade check               # every line "ok"; non-zero exit means dead wiring
jade ledger stats        # events.would_block and events.safety_block count the calls the gate would have stopped
```

`jade check` fails (exit 1) on: a missing hook (`hooks installed`); a hook command whose binary is missing or not executable, `disableAllHooks` in `settings.json` or `settings.local.json`, a matcher or event that is not the one `hooks install` writes, and a registered command that does not decide when run once with a synthetic event (`hook wiring`: the PreToolUse command must block a `jade permit approve` call with SI-09, the other three must exit 0 without an internal error; the probe runs in a throwaway `JADE_HOME` with the gate forced to `enforce` and never touches your ledger); a project, managed or plugin settings file that disables or shadows the hooks (`settings shadowing`, W-47: `disableAllHooks`, `allowManagedHooksOnly`, a JADE hook command that is wrapped, redirected or sets a gate variable, a gate variable in an `env` block, or a file it cannot parse); gate mode `off`, or a `JADE_GATE_MODE` in the environment that differs from the configured mode (`gate mode`; `advisory` passes, it is the soak state); a weakening override (`gate overrides`: `JADE_POLICY_DIR`, `JADE_CONFIG`, `JADE_HOME` pointing away from their defaults, `JADE_GATE_MODE` below enforce, in this shell or in the `env` block of the Claude settings; a deliberate one in your own shell is silenced with `JADE_ACK_OVERRIDES=VAR,VAR`, an `env` block entry never is); `permissions.defaultMode` bypassPermissions or auto together with a gate that is advisory or off (`host permission prompt`: nothing stops a call); a `jade maintain` LaunchAgent in `~/Library/LaunchAgents` that launchd has not loaded (`maintain job`); `jev` neither on PATH nor beside `jade` (`jev shim`); an overlay the runtime reads that has an invalid or empty-matching pattern, an invariant without a block pattern, a `rules_override` that lowers a shipped critical or high rule, or a top-level key the loader does not merge (`overlay lint`, W-56; findings name the file and rule id or index, never the overlay text); a `*.local.yaml` in the checkout's `policies/local/` that the runtime is not reading, because it reads `~/.config/jade/local/` (`overlay files`). Not checked: project-level or managed settings files that shadow the user file (W-47).

### 2.2 Soak, then enforce

After at least a week of real use, read `jade ledger stats` and `jade decide list`. If the would-block cases are the ones you want stopped:

```bash
jade gate enforce
```

From then on a high or critical call stops with a pending permit id. You approve in a terminal:

```bash
jade permit list
jade permit approve prm_xxxxxxxxxxxx     # shows the whole command, its length and the plan hash; type the 6-character code shown (no -y)
```

Single use, 30 minutes, bound to the exact command text and working directory. A safety invariant (for example pushing the default branch) cannot be approved. Claude Code's own permission prompt is a second, independent gate only when the host asks: with `bypassPermissions`, `auto`, headless runs and unattended subagents JADE's gate is the only gate (section 4).

Emergency bypass for one launch: `JADE_GATE_MODE=off claude` (typed by you; an agent typing it is blocked).

### 2.3 Replace the Jev section of CLAUDE.md

```bash
jade claude-md show      # the replacement text, `docs/claude-md-jev-section.md`
jade claude-md apply     # replaces only the "## Jev: ..." section; writes CLAUDE.md.bak-jade-<time>
```

The section runs from its `## Jev: ...` heading to the next heading of the same or a higher level (`#` or `##`; a `###` inside belongs to it). Headings inside code fences are ignored, a Jev heading that appears twice is refused (nothing is written), the write is atomic and keeps the file's mode and symlink, and the backup is never overwritten.

### 2.4 Daily maintenance job

Expires stale permits and decisions, purges state text older than 90 days, runs the provider canary and the improvement miner, and exits non-zero when wiring is dead.

```bash
jade launchd-plist --label com.example.jade-maintain > ~/Library/LaunchAgents/com.example.jade-maintain.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.example.jade-maintain.plist
launchctl print "gui/$(id -u)/com.example.jade-maintain" | head -5     # registered
launchctl kickstart "gui/$(id -u)/com.example.jade-maintain"           # run once now
tail -1 ~/.local/share/jade/maintain.log                               # one JSON line, "check_failed": []
```

The plist sets `PATH` (the directory of `jade` and `jev`, then `/opt/homebrew/bin`, `/usr/local/bin` and the system directories) and `HOME`, because launchd starts agents with `PATH=/usr/bin:/bin:/usr/sbin:/sbin`; `jade check` also looks for `jev` beside `jade` when PATH has none. `jade check` fails when this plist exists but launchd has not loaded it.

The job's `purge` holds the ledger's write lock for the length of its transaction (0.67 s the first time it clears 30 days of text at 100k rows, 56 ms afterwards) and `VACUUM` for about 0.6 s; a hook waits at most 1.5 s for the lock and then blocks a high-risk call (docs/05 T17). Schedule the job away from working hours and re-run `evals/performance/ledger_load.py` (docs/04 4c) if the ledger passes about 300k rows. Back the ledger up with `sqlite3 ~/.local/share/jade/jade.sqlite ".backup <file>"`, not a file copy, which misses the `-wal` sidecar.

Undo: `launchctl bootout "gui/$(id -u)/com.example.jade-maintain"` and delete the plist.

### 2.5 Hang the check off your health command

Add one line to the existing health script so silence is never mistaken for health: `jade check || exit 1` (or its equivalent in your tooling).

### 2.6 Install the skill

`skills/jade/SKILL.md` is the how-to for agents. Copy it into the place your agent tooling reads skills from. It documents behaviour; the exit codes and the gate enforce it.

### 2.7 Install the pre-push guard and protect the default branch

"Never push to the default branch" (SI-08) cannot be enforced completely by reading shell: here-string quote escapes, a redirection before the command and wrappers have each been found by a reviewer, and the next spelling will be too (docs/05 T18). Two things see the real ref instead and are the enforcement; the shell classifier is defence in depth.

1. **A pre-push hook** on this machine. git hands it the remote ref it is about to update, in whatever spelling the refspec had (`HEAD:main`, `+main`, `refs/heads/main`, a delete), so nothing is parsed. `jade git-hook script` prints a self-contained POSIX sh hook (it needs only git and sh, not jade, at run time). It refuses a push whose remote ref is `main`, `master`, the branch `refs/remotes/<remote>/HEAD` points at, `init.defaultBranch` or a branch named by `git config --add jade.protectedBranch <name>`, a delete included, with a message that says what to do instead. It is not installed by jade and not by an agent: it changes a repository's hooks or the global git configuration, so it is a human step (and R-17 rates an agent's attempt high).
2. **Branch protection on the remote.** A hook stops this machine only; protection stops every client and a `--no-verify` push.

**Per repository** (one clone, nothing global changes):

```bash
cd ~/path/to/repo
jade git-hook script > .git/hooks/pre-push
chmod +x .git/hooks/pre-push
jade git-hook check .          # "JADE guard v1 installed", exit 0; anything else exits 1 and says why
```

`.git/hooks` is not versioned, so each clone needs it. A repository whose hooks are managed by husky or lefthook has its own `core.hooksPath` or its own `pre-push` entry: add the script there as that tool's pre-push command, then run `jade git-hook check` (it reads the directory git itself reports for hooks, so it follows `core.hooksPath`) and try a push of a scratch branch called `main` to a scratch remote.

**For every repository at once** (global `core.hooksPath`):

```bash
mkdir -p ~/.config/git/hooks
jade git-hook script > ~/.config/git/hooks/pre-push
chmod +x ~/.config/git/hooks/pre-push
git config --global core.hooksPath ~/.config/git/hooks
jade git-hook check ~/path/to/repo
```

Caveats of the global form, read them first: `core.hooksPath` **replaces** the `.git/hooks` directory of every repository that has no hooksPath of its own, so a `pre-commit`, `commit-msg` or lefthook hook that lives in `.git/hooks` stops running (copy those into the global directory if you want them); the guard itself still runs the repository's own `.git/hooks/pre-push` after its check, when there is one. A repository that sets its own `core.hooksPath` (husky does) overrides the global one and is not guarded; `jade git-hook check <repo>` tells you which repositories those are. Undo: `git config --global --unset core.hooksPath` and delete the directory.

**Branch protection** (do this for `vahidkaargar/jade-decision-governance` and your other repositories; it is a setting of the repository owner):

- GitHub: Settings, Rules, Rulesets (or Branches, Add branch protection rule) on the default branch: require a pull request before merging, block force pushes, block deletion, and do not let administrators bypass it. A solo maintainer who still wants to merge their own pull request sets the required approvals to 0.
- GitLab: Settings, Repository, Protected branches: for the default branch, allowed to push and merge set to "No one" (merge through merge requests), force push off.
- Verify from a scratch clone: `git push origin HEAD:main` must be rejected by the remote ("protected branch"), not by a hook.

**Default branch with another name, empty remotes.** The guard knows a default branch other than `main` and `master` only from `refs/remotes/<remote>/HEAD` (a clone records it; `git remote set-head <remote> --auto` records it after the fact), from `init.defaultBranch`, or from `jade.protectedBranch`. A repository whose default branch is, say, `trunk`, with none of the three, is not protected on `trunk` by the hook (a push there succeeds); `jade git-hook check <repo>` prints `default branch not recorded` for such a remote and the command to fix it. Remote branch protection covers it regardless. The very first push of `main` to an empty remote is refused as well (it is a push to the default branch): a human does it once with `git push --no-verify -u origin main`.

What defeats the hook, and how JADE treats it: `git push --no-verify`, `git -c core.hooksPath=...`, `git config core.hooksPath ...`, the `GIT_CONFIG_KEY_n=core.hooksPath` and `GIT_CONFIG_PARAMETERS` environment routes, and an edit, `chmod` or `rm` of the hook file are rated high (R-17), so an agent needs a human-approved permit for each. The hook does not stop a human who passes `--no-verify` on purpose; remote protection does. `jade git-hook check` is not part of `jade check`: the guard is optional per repository and a failing check on every machine without it would be noise.

## 3. Full rollback

| Layer | Command |
|-------|---------|
| Stop blocking, keep recording | `jade gate advisory` |
| Remove the hooks | `jade hooks uninstall` (removes only JADE's own entries, matched by program name `jade` and the `hook <name>` ending, never by a substring; atomic; backup of settings.json kept) |
| Restore the old CLAUDE.md section | copy back `CLAUDE.md.bak-jade-<time>` |
| Restore the old `jev` | section 1 undo |
| Stop the job | `launchctl bootout ...` |
| Freeze all improvement activity | `jade improve freeze` (human removes it with `jade improve unfreeze`) |
| Revert a promoted candidate | `jade improve rollback <candidate>`: a template goes back to the exact champion it replaced (the registry default when it had none), a threshold goes back to the band the group had before. It refuses when the pins no longer match the promotion, while a later challenger serves the question, when a human made the promotion and none is at the terminal, under `FREEZE` unless a human is at the terminal, and for loosening a promoted stricter threshold unless a human is at the terminal; the audit trail records the real actor (`human:terminal` or `runtime:agent`). Deleting `~/.local/share/jade/calibration/current.json` still resets every threshold to the defaults |

## 4. Known coverage limits

- Classification is syntactic. The one rule that must not depend on it, no push to the default branch, is enforced by the pre-push guard and by branch protection on the remote (section 2.7), not by the classifier. A command built at run time (base64, variable indirection, `eval` of fetched text) is not recognised. Unnamed MCP tools are medium (recorded, not gated) unless their name says they mutate; browser and desktop automation MCP tools that click or type are not gated, but text typed into a shell is classified as a command.
- **Tools no rule names (W-36).** `jade check` prints an informational row `tool coverage` (never a failure). It lists, by name and count only, the tools the hook has recorded that no decision covers: MCP tools that no `mcp_tools` rule names (M-99, medium, recorded and not gated), MCP tools only the verb-in-name heuristic covers (M-07, gated high; add a specific rule so the rationale is yours), and built-in tools in neither `GATED_BUILTIN` nor `UNGATED_BUILTIN` in `jade/policy.py`. The hook cannot see an installed-tool inventory: it records the name of a gated call, and once per tool the name of an MCP call that no specific rule names (event `tool_unnamed`; the arguments are never stored). So a tool appears after its first call, and a built-in tool the matcher does not name never appears because the host does not send it to the hook. After adding an MCP server, call each of its tools once and read the row. Act on a tool in the list by adding a rule to your overlay (`policies/local/risk.yaml`) or, for a built-in, by naming it in the policy and in the matcher (`jade hooks install` in a terminal).
- **JADE's gate is the only gate whenever the host shows no permission prompt.** The hook event carries `permission_mode`. With `bypassPermissions` or `auto`, with `acceptEdits` (file edits), in headless `claude -p` runs and in unattended subagents, Claude Code asks nobody, so the host prompt is not a backstop and every classification gap above is a bypass. `jade check` prints this under `host permission prompt` together with how many gated calls of the last 7 days ran without a prompt. Run such agents in a sandbox or as a separate OS user, and read `jade ledger stats` for `would_block` and `gated` events.
- The gate fails closed (docs/05 T17): an unreadable or locked ledger blocks high-risk calls, an overlay that does not load blocks everything but clearly read-only commands and ordinary file writes, and the whole handler answers within 6 s, below the 10 s host timeout. If the gate blocks everything, run `jade check`; a human can set `JADE_GATE_MODE=advisory` while fixing the cause.
- An earlier run of a command whose outcome is unknown (SI-07) blocks the identical command until `jade decide outcome <decision> --type observed --success true|false --method "..."` records what really happened.
- Provider answers are as good as the evidence passed in. The companion check flags summaries, not lies.
- A model drift is informational by decision (follow upgrades): the new snapshot starts at default thresholds and earns changes with its own labels.

## 5. From IMPLEMENTED to VALIDATED

Mechanical criteria, all measured from the ledger (`docs/06` exit lines):

1. 14 days of ledger data under enforce mode: `jade ledger stats --days 14` reproduces `docs/04` section 4.
2. Abstain or reject answers without a disposition no more than 5% of non-accepts; zero unsafe accepts for risk high or above.
3. At least 200 labelled rows in a calibration group before any threshold change: `jade label queue`, `jade label set`, `jade eval run`, `jade eval propose`.
4. One full improvement cycle. Threshold: `jade eval propose` (replays on dev and calibration, holds out validation), a human `jade improve protected <candidate>`, a human `jade improve promote`. Question template: `jade improve shadow` (at least 7 days, 100 live samples), a human `jade improve protected`, `jade improve canary` (a human, or the standing `auto_promotion_opt_in`), then `jade improve promote`. `promote` recomputes the results; nothing is attached by hand.

PRODUCTION_APPROVED is yours: it means enforce mode with critical rules on this machine, decided by you after the above.
