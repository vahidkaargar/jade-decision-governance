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
| Install a named release or commit, or roll back | `jade upgrade --ref v0.3.3` (a `vX.Y.Z` tag or a full 40-hex commit; a branch name is refused) | same as above; an older tag is reported as a rollback |

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

`hooks install` backs up `~/.claude/settings.json` next to itself (`settings.json.bak-jade-<time>`; a second run in the same second gets a `-1` suffix, a backup is never overwritten), writes the file atomically (temp file in the same directory, then rename; a symlink stays a symlink and the target keeps its permission bits, a new file is 0600), adds four hook entries (PreToolUse for Bash, PowerShell, Monitor, file edits, RemoteTrigger and MCP tools; PostToolUse for the same tools except the reads, W-40; Stop; SessionStart). An install made before the matcher grew shows `pretool matcher` as FAIL in `jade check`, and one whose PostToolUse matcher is still `Bash` shows `hook wiring` as FAIL; run `jade hooks install` again, and leaves other hooks alone. Hooks are read at session start: open a new Claude Code session afterwards.

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

- Classification is syntactic. The one rule that must not depend on it, no push to the default branch, is enforced by the pre-push guard and by branch protection on the remote (section 2.7), not by the classifier. Text fetched or decoded at run time and then run (`curl | sh`, `sh -c "$(curl ..)"`, `base64 -d | sh`, a file the same command downloads and runs) is R-22 (high), but a command assembled from variables or pieces is not recognised. Unnamed MCP tools are medium (recorded, not gated) unless their name says they mutate; browser and desktop automation MCP tools that click or type are not gated, but text typed into a shell is classified as a command.
- **What the gate still cannot read, and how to size it before you decide (W-31, W-33).** A script that is already a file (`bash scripts/x.sh`, `python3 tools/y.py`, `./z`), `sed -f` and `awk -f` script files, `eval "$(cat f)"`, `eval "$CMD"` and a relative write after `cd $VAR` have no program on the command line to classify. Flooring them all at high would stop most of what an agent runs, so the code does not decide it: rule R-31 rates them low (except `script-option` and `computed-cwd`, which are R-32, medium, W-33: still recorded and not gated, because `permit_required_at` is high) and the hook journals event `blind_spot` (labels `script-file`, `script-option`, `eval-file`, `eval-var`, `computed-cwd`, `patch-body`; never the command or a path; once per session and label, so a count is sessions, not calls). Read it after a soak week with `jade ledger stats` (the `blind_spot` count) and, per label, read-only: `sqlite3 -readonly ~/.local/share/jade/jade.sqlite "SELECT guard, COUNT(*) FROM events WHERE kind='blind_spot' GROUP BY guard"` (your own terminal; it reads the live ledger). Decide per label: a label that is rare and that you would want a permit for (`script-option`, `eval-file`) can go to high with an overlay rule (`rules_prepend` in `policies/local/risk.local.yaml`, `match: "^blind:script-option "`, `level: high`, `files_only` not set); a label that is most of your sessions (`script-file`) stays recorded. `rules_override: {R-31: {level: high}}` floors the four R-31 labels and `rules_override: {R-32: {level: high}}` the two R-32 labels (`script-option`, `computed-cwd`). Lowering `permit_required_at` to medium would also gate every other medium rule (R-30 credential reads, M-99 unnamed tools), so prefer the per-rule override. Both are overlay edits made by you; nothing in the repository changes them, and no human-only command was added for this (SI-09 is unchanged). Residual either way: the text of a script file is never read, and a script written by an earlier call or by the Write tool and run later is only recorded.
- **A patch applied from a file, a heredoc or a pipe, and a link made by the same command (W-37).** `patch`, `git apply` and `git am` change the files the patch headers name, so the gate reads the headers when it can (a heredoc or here-string of the command, a file the same command writes, a file on disk of at most 1 MiB, an `echo` or `cat` piped in) and classifies each target like a redirect: `git apply fix.patch` for a patch that touches `.claude/settings.json`, `policies/*.yaml`, `AGENTS.md` or `jade/*.py` now needs a permit, and so does the same patch in a heredoc. A patch it cannot read (`git diff | git apply -`, `curl .. | patch -p1`, a variable, a glob, a file that another command of the line writes) is R-31 label `patch-body`: recorded, never gated. `git diff | git apply -R` and `git stash show -p | git apply` are most of what you will see; read the count the same way as the other labels (above) and decide per label. A label that you only want to see when it arrives from the network cannot be told apart from one that does not: the gate records the shape (a pipe), not the producer. To floor it, add `rules_prepend` with `match: "^blind:patch-body "`, `level: high` to `policies/local/risk.local.yaml` (your edit; no human-only command was added, SI-09 is unchanged). A link made and used in one command (`ln -s ~/.claude c && echo x > c/settings.json`) is read through. `pyproject.toml` and `uv.lock` of the JADE checkout are R-24 (high): editing them, `uv add`, `uv remove`, `uv lock` and `uv version --bump` there need a permit or your own terminal, so a release bump of `version` is yours to make or to permit; the same files in any other project are untouched. Not read: a patch applied by a program or script, `find -exec git apply`, a link made by a program, and the files git rewrites from objects it holds (`git checkout <rev> -- .`, `git stash pop`, `git merge`). Evidence: `tests/acceptance/test_gate_residuals_w37.py`, adversarial A234 to A241, regression R165 to R170; docs/05 T19.
- **A permit now covers the content, and the approval screen shows it (W-39).** For a Write, Edit, MultiEdit, NotebookEdit or an MCP call with a command the screen prints `Content`, its length, the first 12 characters of its digest and all of it, and the code you type is the first 6 characters of that digest. Read the content before you type the code: the permit allows exactly it, once. Content over 20000 characters or with a secret-shaped value is refused at approval; ask the agent to make the change in smaller pieces or from a file you can read. Nothing to install or run on your side after the re-install (section 1.1) beyond approving requests again (docs/09 section 3); no human-only command was added (SI-09 unchanged). What a *shell* command writes into a file is still bound by the command text only.
- **The approval terminal check is stricter (W-37).** After the next re-install (section 1.1) `jade permit approve` and the other human-only commands also require that the shell you type in is the leader of its terminal session and is interactive: a login or interactive `zsh`, `bash`, `fish` and kin, not started with `-c` and not running a script. A terminal panel, Terminal.app, iTerm, `tmux` panes and an SSH login are such shells (the panels on this Mac run `/bin/zsh -l` and `/bin/zsh -il`). A command started as the program of a terminal (`tmux new 'jade permit approve'`, an IDE run configuration, `ssh host jade permit approve` without `-t` and a shell) is refused with the leader named: `the leader of its session (pid N) is not an interactive shell: it is ...`. Run the command from an interactive shell. To see what leads your session: `ps -o pid=,comm=,command= -p "$(python3 -c 'import os; print(os.getsid(0))')"`. There is no switch to turn the check off, by design (a switch the agent can set protects nothing). It closes the `pty.fork()` variant of docs/05 T2, not an agent that starts a real interactive shell on a pty it holds and detaches from its parent.
- **Tools no rule names (W-36).** `jade check` prints an informational row `tool coverage` (never a failure). It lists, by name and count only, the tools the hook has recorded that no decision covers: MCP tools that no `mcp_tools` rule names (M-99, medium, recorded and not gated), MCP tools only the verb-in-name heuristic covers (M-07, gated high; add a specific rule so the rationale is yours), and built-in tools in neither `GATED_BUILTIN` nor `UNGATED_BUILTIN` in `jade/policy.py`. The hook cannot see an installed-tool inventory: it records the name of a gated call, and once per tool the name of an MCP call that no specific rule names (event `tool_unnamed`; the arguments are never stored). So a tool appears after its first call, and a built-in tool the matcher does not name never appears because the host does not send it to the hook. After adding an MCP server, call each of its tools once and read the row. Act on a tool in the list by adding a rule to your overlay (`policies/local/risk.yaml`) or, for a built-in, by naming it in the policy and in the matcher (`jade hooks install` in a terminal).
- **JADE's gate is the only gate whenever the host shows no permission prompt.** The hook event carries `permission_mode`. With `bypassPermissions` or `auto`, with `acceptEdits` (file edits), in headless `claude -p` runs and in unattended subagents, Claude Code asks nobody, so the host prompt is not a backstop and every classification gap above is a bypass. `jade check` prints this under `host permission prompt` together with how many gated calls of the last 7 days ran without a prompt. Run such agents in a sandbox or as a separate OS user, and read `jade ledger stats` for `would_block` and `gated` events.
- **Closing the loop for Write, Edit and MCP calls, and a permit that meets a parallel hook (W-40, section 4a).** After the next re-install (section 1.1) PostToolUse reports every call that can run under a permit, so a permitted Write, Edit or MCP call ends as SUCCEEDED or FAILED instead of waiting for its deadline and being held by SI-07. Until you re-run `jade hooks install` `jade check` fails `hook wiring` (PostToolUse matcher `Bash`). A permit is spent when the gate lets a call through and is not returned if another hook or the host prompt then denies the call; the same call delivered twice is answered the same (section 4a). No new human-only command.
- **Promotion now waits for 150 labeled protected rows (W-52).** `protected_test.min_samples` in `policies/promotion.yaml` went from 30 to 150, the design target of docs/04 section 5, so `jade improve promote` stops at `protected_test_non_inferior` ("fewer than 150") until a calibration group holds that many labeled rows in the protected split (about 1000 labeled rows in the group, since the split is 15%). Nothing to run: label answers with `jade label set` as the adjudication queue allows, and read `jade eval run` (`groups.<group>.n`, protected rows only through a human `jade eval run --protected`). A template candidate's protected check re-asks up to 150 stored questions of the provider when a human runs `jade improve protected`, so it costs a few cents more than before. If you would rather lower the bar, that is your edit of the protected policy file; it is not an agent step.
- **New read-only evaluation output, no setup (W-52).** `jade eval run` adds `risk_coverage`, `ece_ci95` and `risk_high` per group plus `choice` and `operations`; `jade eval conditions` adds `paired_metrics` with Holm-adjusted p-values; `jade eval routing` prints the router's confusion matrix on `evals/routing`. All are offline reads of the ledger or of the hand-written cases, none is human-only, and none needs the hook re-installed. Definitions: docs/04 section 7.
- **Shadow runs for choice and score questions (W-30).** `jade improve shadow <candidate> <question_id> <version>` now accepts a challenger version of a choice or a score question as well as a noul one; no setup, no new human-only command. A choice shadow replays the stored state of recent champion calls over the options each call offered and reports `agreement` (verdict and, when both accept, the option), `option_agreement`, `accepted_option_changes` and `reject_to_accept` (the champion answered "none of these", the challenger accepted an option). The ledger keeps the names of the options, not their descriptions, so the challenger is asked each option by the description its own template gives it, else by its name; read a choice shadow as a comparison of the instructions over the same option names. A score shadow reports `mean_abs_score_diff`, `max_abs_score_diff` and `agreement` (scores within 0.1). The shared barriers apply unchanged: 7 days, 100 live samples, the provider error budget, the latency budget, the kill switch and "not while in canary". Labels exist for noul answers only, so a choice or score candidate cannot pass the protected stage or a canary and `jade improve promote` refuses it: if the shadow looks good, applying the new version is a manual change to the question registry and its pins that no JADE command does for you, and the candidate stays at its shadow result as the record. Nothing the agent runs can promote it.
- The gate fails closed (docs/05 T17): an unreadable or locked ledger blocks high-risk calls, an overlay that does not load blocks everything but clearly read-only commands and ordinary file writes, and the whole handler answers within 6 s, below the 10 s host timeout. If the gate blocks everything, run `jade check`; a human can set `JADE_GATE_MODE=advisory` while fixing the cause.
- An earlier run of a command whose outcome is unknown (SI-07) blocks the identical command until `jade decide outcome <decision> --type observed --success true|false --method "..."` records what really happened.
- Provider answers are as good as the evidence passed in. The companion check flags summaries, not lies.
- A model drift is informational by decision (follow upgrades): the new snapshot starts at default thresholds and earns changes with its own labels.

### 4b. A gated command that carries a secret (W-43)

Without any setting, a gated command that carries a secret-shaped value stays unapprovable: the ledger stores it redacted, so you could not read what the plan hash covers, and the agent is told to move the secret into an environment variable or a file (docs/05 T4). That stays the recommended route.

What the repository adds (implemented, off by default, SI-09 extended): if you turn the setting on, the hook also keeps the raw call of that one PENDING permit in a mode-600 file next to the ledger (`<JADE home>/reveal/<permit id>.json`, directory mode 700). It is never written to the ledger, an event, a decision, a provider payload or the block message. It is deleted when the permit is approved, revoked or expires (the hook's sweep also removes any file whose permit is gone). `jade permit show <id> --raw` and `jade permit approve <id>` print it on your terminal only after checking that it redacts to the text on file, hashes to the permit's plan hash and matches the content digest; anything else is refused. `--raw` needs the same human terminal as `approve` and SI-09 blocks its spelling in an agent's command (the flag is exact: an abbreviation is rejected by the parser).

What only you can do:
1. Decide whether you want it. The file holds the secret in plain text for up to 30 minutes on a disk the agent's user can read, so any agent process that reads files could read it, including another session or a subagent that never saw the command. The setting trades that short-lived copy for the ability to approve one-off commands that must carry a literal credential. Add `{"permit_reveal": true}` to `~/.config/jade/config.json` (an agent's write there is gated by R-13 and needs your permit) and ask the agent to request the command again. Remove the key to turn it off; existing files are removed by the next sweep.
2. Run `jade permit show prm_xxxxxxxxxxxx --raw` in a terminal, read the whole command, then `jade permit approve prm_xxxxxxxxxxxx`.
3. Nothing else is needed: the SI-09 pattern ships in `policies/safety.yaml` and takes effect on the next hook call. No hook, settings or launchd change.

Not done, needs your decision with W-38 (out-of-band approval): keeping the raw call in a macOS Keychain item that needs user presence (or in a helper run as another user) instead of a file, so that no agent-readable copy exists at all. The file backend is behind `permits.stash_raw`, `read_raw` and `drop_raw`; a Keychain backend replaces those three.

### 4a. Hook payloads and the permit race (W-40)

What the repository does: `tests/fixtures/hook_payloads.json` has one PreToolUse and one PostToolUse payload for each of Bash, Write, Edit, an MCP tool, a Task call and a Bash call made inside a subagent, and `tests/acceptance/test_hook_payloads_w40.py` asserts against them that a permit is bound to the payload's `session_id`, `agent_id` and `permission_mode`, that a permitted call is closed by its PostToolUse event, and, with concurrent `jade hook pretool` processes against one temporary `JADE_HOME`, that one call delivered several times spends one permit and several different calls spend one permit between them. The fixtures are synthetic: shaped after the host's hooks reference, every value invented, `captured: false`.

Decision, 2026-11-09 (reversible, evidence in the tests): the permit is spent at PreToolUse and never returned. Alternatives: (a) reserve at Pre and finish at Post: a call denied by another hook leaves no Post event, so the reservation could only lapse on a timer, and a timer that returns the permit is a second run of a command that did run when PostToolUse is missing (stale matcher, crashed host); (b) return the permit when the same call is seen again: the same hole. (c, chosen) one call, one permit: the host's `tool_use_id` is recorded with the use, and a second delivery of that call within 120 s is answered the same. The cost is a retry after a denial by another hook or the host prompt: the model asks again, gets a new `tool_use_id` and needs a new permit.

What only a human can do (run in a terminal, never an agent; nothing here is a new human-only command, so SI-09 is unchanged):
1. `jade hooks install`, then a new session, so that the PostToolUse matcher is the wide one (`jade check` row `hook wiring` turns green).
2. Replace the synthetic fixtures with captured ones: in a throwaway session, with a hook of your own that writes its stdin to a file (not JADE's hook, and nothing under `~/.config/jade` or `~/.local/share/jade`), make one call of each family, remove every real path, host name, address, id and text by hand (a permit is bound to `session_id` and `agent_id`, so keep their shape, not their values), put the pair into the family's entry of `tests/fixtures/hook_payloads.json` and set `captured` to `true`. Check in particular that a call made inside a subagent carries `agent_id`, that `tool_use_id` is the same in the Pre and Post payload of one call and differs between two calls, and that a call denied by the host prompt sends no PostToolUse event; a result that differs from the fixtures is a finding, not something to edit away.

## 5. From IMPLEMENTED to VALIDATED

Mechanical criteria, all measured from the ledger (`docs/06` exit lines):

1. 14 days of ledger data under enforce mode: `jade ledger stats --days 14` reproduces `docs/04` section 4.
2. Abstain or reject answers without a disposition no more than 5% of non-accepts; zero unsafe accepts for risk high or above.
3. At least 200 labelled rows in a calibration group before any threshold change: `jade label queue`, `jade label set`, `jade eval run`, `jade eval propose`.
4. One full improvement cycle. Threshold: `jade eval propose` (replays on dev and calibration, holds out validation), a human `jade improve protected <candidate>`, a human `jade improve promote`. Question template: `jade improve shadow` (at least 7 days, 100 live samples), a human `jade improve protected`, `jade improve canary` (a human, or the standing `auto_promotion_opt_in`), then `jade improve promote`. `promote` recomputes the results; nothing is attached by hand.

PRODUCTION_APPROVED is yours: it means enforce mode with critical rules on this machine, decided by you after the above.
