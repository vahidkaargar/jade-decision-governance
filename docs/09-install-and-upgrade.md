# 09 — Install, upgrade and release

For people who install JADE from a release and use it with Claude Code, and for the maintainer who cuts releases. The short version is the Quick start in `README.md`; this file holds the rules behind it. The manual wiring steps and their undo are in `docs/08-deployment-runbook.md`.

## 1. What a release is today

A release is a git tag `vX.Y.Z` on the default branch with a GitHub release that carries a wheel, an sdist and `SHA256SUMS`. It is an installable first release that runs in **advisory mode** (the gate records and never blocks). It is not VALIDATED and not production approved: those stages are measured and decided as described in `docs/08` section 5, and nothing in a release changes them. Later releases improve it; `jade upgrade` brings them in.

## 2. Versioning policy

JADE follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`, one number in `pyproject.toml`, `jade.__version__`, the newest `CHANGELOG.md` heading and the tag (a test and the release workflow compare them).

**What counts as breaking.** A change is breaking when something a user, a script, Claude Code or a stored file relies on stops working unchanged:

| Surface | Breaking | Not breaking |
|---------|----------|--------------|
| CLI exit codes | A different meaning for `0` accept, `3` abstain, `4` reject, `5` provider unavailable, `6` invalid question of `jev` and `jade ask`; a different meaning for the hook exit codes (`0` proceed, `2` block); a command or flag removed or renamed; a documented output field removed (`--json`) | A new command, flag or output field; a new line in human-readable output; a new `jade check` row |
| Hook protocol | The events JADE registers (PreToolUse, PostToolUse, Stop, SessionStart), the command form `hooks install` writes, how a hook reads its input and answers the host | A wider PreToolUse matcher (`jade check` row `pretool matcher` tells you to re-run `jade hooks install`); a new field read from the event when it is absent |
| Ledger schema | A table or column removed, renamed or given another meaning; a stored JSON document that an older version of the same minor series could not read | A new table or column (an additive migration) |
| Policy file keys | A key of `policies/*.yaml` or of an overlay (`risk.local.yaml`, `safety.local.yaml`) removed, renamed or read differently; a shipped invariant removed | A new key with a default; a new or stricter shipped rule (see below) |

Shipped rules can get stricter in any release (a new high-risk pattern, a new invariant). That changes `policy_version`, and a permit that was issued but not yet used is revoked by it; it is a behaviour change listed in the CHANGELOG, not a break of the surfaces above.

**Which number moves.** While the major version is 0, a breaking change raises the MINOR number and is marked `BREAKING` in the CHANGELOG under Changed or Removed; a PATCH release only fixes bugs and adds things that break nothing. From 1.0.0 a breaking change raises the MAJOR number. `jade upgrade --check` prints the CHANGELOG headings between the installed and the latest version, so read them before you upgrade across a minor.

**Ledger migrations** (`ledger.MIGRATIONS`, applied when the ledger is opened) are:

- additive: they add a column or a table and never drop, rename or rewrite a row;
- idempotent: running them twice, or from two processes at once, changes nothing the second time;
- forward-only: there is no downgrade script; a newer version migrates the ledger, an older one is not asked to un-migrate it;
- backward compatible within a minor series: a ledger that a newer 0.3.y migrated can still be opened by an older 0.3.y, which ignores the columns it does not know.

Across minors the last promise does not hold. Copy the ledger directory before you upgrade across a minor (`cp -Rp ~/.local/share/jade ~/.local/share/jade.bak`), so that a rollback to the older minor has a ledger it can read. `jade version` prints the ledger schema version. `tests/test_ledger_migration.py` opens a ledger with the 0.2.2 schema with the current code; a release that changes the schema snapshots the previous DDL the same way.

## 3. The upgrade contract

`jade upgrade` runs `uv tool install --reinstall` for a release tag of `github.com/vahidkaargar/jade-decision-governance`, then `jade check --offline` from the new install. Section 1.2 of `docs/08` lists the safety properties of the command itself (human terminal only, constant repository, argument list, https to GitHub only, no token).

| It changes | Why |
|------------|-----|
| The installed code (`jade`, `jev`, the hook entry points) | That is the upgrade |
| The shipped policies, question registry and schemas inside the package | They are part of the code; `policy_version` changes with them |
| The packaged copies of the skill and of the CLAUDE.md Jev section (inside the package) | Your own copies are not edited, see below |

| It never touches | What to do yourself |
|------------------|---------------------|
| The ledger (`~/.local/share/jade`): `jade upgrade` does not open it. The first JADE command of the new code does, and applies any additive migration (section 2). That includes the `jade check --offline` that upgrade runs | Back it up first when you cross a minor |
| Your overlays in `~/.config/jade/local` and `~/.config/jade/config.json` (gate mode, checkout path) | Run `jade check`: rows `overlay lint` and `overlay files` say when a new release no longer accepts an overlay |
| `~/.claude/settings.json` (the hook entries) | Re-run `jade hooks install` only when `jade check` fails `hooks installed`, `hook wiring` or `pretool matcher` |
| `~/.claude/CLAUDE.md` | `jade setup --status` shows `missing` for the CLAUDE.md section when the shipped section differs from yours; `jade claude-md apply` replaces it (backup kept) |
| The skill copy `~/.agents/jade/SKILL.md` and its link | `jade setup --status` shows `missing` for the skill file when the shipped skill differs; `jade setup` shows the change, keeps `SKILL.md.bak-jade-<time>` and replaces it when you say y (a skill file that is a link is `REFUSED` and never replaced) |
| The `jade maintain` LaunchAgent plist | It points at the `jade` binary, which keeps its path; nothing to do |
| The pre-push guard in your repositories | The guard is a standalone shell script; `jade git-hook script` prints a newer one if a release changes it |

When `jade upgrade` ends, it lists the failed rows of `jade check` and says whether `jade hooks install` is needed. A new Claude Code session is needed only after you re-install hooks. `jade check` without `--offline` runs the live provider canary: run it yourself.

**Roll back.** `jade upgrade --ref vX.Y.Z` installs an older tag and says it is a rollback. Rolling back inside a minor series needs nothing else. Rolling back across a minor means restoring the ledger copy you made (section 2) if the newer one migrated it. To go back to a state without JADE enforcing anything, see `docs/08` section 3 (`jade gate advisory`, `jade hooks uninstall`).

## 4. Release process (maintainer)

1. On a branch: bump `version` in `pyproject.toml` and `__version__` in `jade/__init__.py`, refresh `uv.lock`, move the `[Unreleased]` entries of `CHANGELOG.md` under `## [X.Y.Z] - date`, and update the version line of the README status table.
2. Run `uv run ruff check .`, `uv run pytest -q`, `uv run python evals/run_cases.py` and `sh scripts/verify_install.sh` (builds both archives, installs the wheel into a temporary tool directory and runs `jade --version`, `jade check --offline` and `jade setup --status` from it; it never touches your `~/.claude`, ledger or keychain).
3. Open a pull request, let CI pass, merge. The default branch is protected: nobody pushes to it.
4. Tag the merge commit on the default branch and push the tag: `git tag -a vX.Y.Z -m "X.Y.Z"`, then `git push origin vX.Y.Z`.
5. `.github/workflows/release.yml` runs on the tag. It refuses a tag that differs from `pyproject.toml` or `jade.__version__`, runs `ruff`, the suite and the case replay, builds the wheel and sdist with `uv build`, verifies the built wheel the way step 2 does, writes `SHA256SUMS` and creates or updates the GitHub release with the wheel, the sdist, the checksums and the CHANGELOG section as notes. A version that is not plain `X.Y.Z` becomes a prerelease; write it in the PEP 440 spelling (`0.3.0rc1`, not `0.3.0-rc1`): the build normalises the version, the wheel is named `0.3.0rc1`, and `scripts/verify_install.sh` compares that name with the `pyproject.toml` text. Only the last job has `contents: write`.
6. Check the release page, then run `jade upgrade --check` on a machine with an older install: it must name the new tag.

A broken release is not deleted or moved. Fix forward with a new patch tag (`jade upgrade` takes the latest release), and say in the CHANGELOG which version is bad.

**Verify the attached files.** `jade upgrade` installs from the tag with uv. To install a file you downloaded, check it against `SHA256SUMS` first:

```bash
gh release download v0.3.1 --repo vahidkaargar/jade-decision-governance --dir jade-v0.3.1
cd jade-v0.3.1
shasum -a 256 -c SHA256SUMS          # macOS; on Linux: sha256sum -c SHA256SUMS
uv tool install --reinstall ./jade_decision_governance-0.3.1-py3-none-any.whl
```

Both lines of `SHA256SUMS` (wheel and sdist) must print `OK`. The checksums come from the same release page as the files: they catch a damaged or partial download, not a compromised release. For that, compare the tag's commit with the repository history.

## 5. Supported platforms and known limits

| Platform | State |
|----------|-------|
| macOS (Apple silicon or Intel), Python 3.13+, uv | The development and test platform. Keychain lookup of the OpenRouter key, the `jade maintain` LaunchAgent and `jade setup` step 6 exist here |
| Linux, Python 3.13+, uv | CI runs the suite on Linux. The OpenRouter key comes from the `OPENROUTER_API_KEY` environment variable (there is no Keychain). `jade setup` skips the maintenance job step: `jade launchd-plist` writes a launchd file and there is no job helper for systemd or cron; schedule `jade maintain` yourself if you want its daily expiry and purge (`jade ledger purge --older-than 90d` is the manual form) |
| Windows | Not supported or tested |

Known limits, each stated in full elsewhere:

- Advisory mode blocks nothing (`README.md` Quick start). Enforce mode is a later, deliberate step (`docs/08` section 2.2).
- The gate is a syntactic classifier with documented gaps (`docs/05` T18, T19, T22). When the host asks for no permission (`bypassPermissions`, `auto`, headless runs), the gate is the only gate (`docs/08` section 4).
- Pushes to the default branch are stopped by the pre-push guard and branch protection, which are human steps (`docs/08` section 2.7).
- `jade upgrade --check` and `jade upgrade` need to reach `api.github.com`; offline they exit 1 and change nothing. `jade upgrade --ref vX.Y.Z` still needs github.com for the install.
- Hooks are read at Claude Code session start: after `jade hooks install`, open a new session.
- An install made from a release is not updated by pulling a checkout: use `jade upgrade`, or `uv tool install --reinstall .` from the checkout (`docs/08` section 1.1).

## 6. Troubleshooting by `jade check` row

`jade check` prints one line per row, `ok` or `FAIL`, and exits non-zero when any row fails. `jade check --offline` skips the two provider rows. Run it from the project you work in, so that `settings shadowing` can scan that project.

| Row | Failing means | Fix |
|-----|---------------|-----|
| `ledger writable` | The ledger cannot be opened or written (permissions, a full disk, a different user owns it) | Check the directory in the detail line is yours and has room. A locked ledger clears when the other process ends. Without a working ledger the gate fails closed for high-risk calls |
| `policies load` | A shipped policy or an overlay does not parse or validate | If you have an overlay, move `~/.config/jade/local` aside and run again; if it then passes, the overlay is the fault (next rows). If not, reinstall: `uv tool install --reinstall git+https://github.com/vahidkaargar/jade-decision-governance@v0.3.1` |
| `ledger integrity` | A stored permit or improvement row does not match its schema | The detail names the id. Revoke a permit with `jade permit revoke <id>`; the row can no longer be used |
| `code version` | You named a checkout (`JADE_CHECKOUT` or `checkout` in `config.json`) whose version is newer than the installed code | `jade upgrade`, or `uv tool install --reinstall <checkout>` when you develop from it; unset the checkout setting if you installed a release |
| `overlay lint` | An overlay has an invalid pattern, an invariant without a block pattern, a rule that lowers a shipped high or critical one, or a key the loader does not merge | The detail names the file and the rule id or index. Fix or remove that entry in `~/.config/jade/local/*.local.yaml` |
| `overlay files` | A `*.local.yaml` sits in a checkout's `policies/local/`, which the installed tool does not read | Move it to `~/.config/jade/local/` |
| `question registry` | A registry entry failed its lint, or none loaded | Reinstall the release; if it persists, report it |
| `hooks installed` | One or more of the four hooks is not in `~/.claude/settings.json` | `jade hooks install` in your own terminal, then open a new Claude Code session. A fresh install fails this row until you do |
| `hook wiring` | A registered hook binary is missing or not executable, hooks are disabled in settings, or the hook does not decide when run once with a synthetic event | Read the detail. After `uv tool uninstall` or a moved uv tool directory, run `jade hooks install` again so the entries point at the current `jade` |
| `settings shadowing` | A project, managed or plugin settings file disables or wraps the hooks, or a file cannot be parsed | The detail names the file. Remove `disableAllHooks` or the wrapper, or fix the file |
| `pretool matcher` | An install made by an older release covers fewer tools than the current one | `jade hooks install`, then a new session. The usual reason to run it after an upgrade |
| `ack audit` | A storm of `dropped` acks or a refused `jade ack` or `jade label` attempt in the ledger | Read the `ACK` and `ACK_REFUSED` events (`jade ledger stats`). A refused attempt means an agent session tried a human-only command; a storm means answers are being dismissed without action. Resolve the cause, then the row clears as the window moves on |
| `routing policy` | `policies/routing.yaml` is missing or malformed, or the question registry breaks its schema | `jade question lint` names the file and key. Restore the shipped file or fix the entry; limits are never guessed, so calls fail with exit 6 until then |
| `tool coverage` | Informational: MCP or built-in tools that no rule names. It never fails | Review the listed names and add a rule in your overlay (`~/.config/jade/local/risk.local.yaml`) for any that can act outside the machine |
| `gate mode` | The gate is `off`, or `JADE_GATE_MODE` in your environment differs from the configured mode. `advisory` passes | `jade gate advisory`, or `unset JADE_GATE_MODE`. Moving to `jade gate enforce` is a decision, not a fix |
| `gate overrides` | `JADE_POLICY_DIR`, `JADE_CONFIG`, `JADE_HOME` or `JADE_GATE_MODE` weakens the gate in this shell or in a Claude settings `env` block | Unset it. `JADE_ACK_OVERRIDES=VAR,VAR` silences a deliberate one in your own shell (never one in a settings `env` block). A run of `jade check` with `JADE_HOME` set for a test is expected to fail here |
| `host permission prompt` | Claude Code runs with `bypassPermissions` or `auto` and the gate is advisory or off: nothing stops a call | Change the host's permission mode, or the gate to enforce once you have soaked it. Without that combination the row passes and states how many gated calls had no host prompt |
| `maintain job` | A `jade maintain` LaunchAgent plist exists and launchd has not loaded it, or it points at a missing binary | `launchctl bootstrap "gui/$(id -u)" <plist>` (the detail prints it), or delete the plist. Optional; macOS only |
| `jev shim` | `jev` is neither on PATH nor beside `jade` | Add uv's tool bin directory to PATH (`uv tool update-shell`, then a new shell). A `jev` script you installed earlier can stop uv from installing the shim: move it aside and reinstall |
| `provider canary` | The live check against OpenRouter failed or the answers were off (a known-yes or known-no question) | The detail gives the error kind. Check the key (`jade setup --status` says whether one is found; it never prints it), the network and the OpenRouter status page. Exit code 5 from `jev` means the same thing |
| `model drift` | Informational: the provider resolved a different model snapshot | No fix: a new snapshot starts at default thresholds and earns changes with its own labels (`docs/05` T9) |

Problems that are not a `jade check` row:

| Symptom | Fix |
|---------|-----|
| `jade: command not found` after install | uv's tool bin directory is not on PATH: `uv tool update-shell`, open a new shell; `uv tool list` shows what is installed |
| `jade setup` or `jade hooks install` refuses | They need an interactive terminal and refuse inside an agent session on purpose (SI-09, `docs/07` section 3): run them in your own terminal |
| `jade setup` says the skill file is `REFUSED` | `~/.agents/jade/SKILL.md` is a link, or cannot be read as text. Setup never replaces it: compare with `diff`, replace it yourself, run `jade setup` again. (A plain file that only differs is not refused: setup backs it up and replaces it on a y) |
| `jade setup` says `config.json` is not valid JSON or not an object | `~/.config/jade/config.json` could not be parsed. Setup never rewrites it, because that would drop the other keys: fix or move it, run `jade setup` again |
| `uv tool install` says `Executable already exists: jev` | An older `jev` script is in uv's tool bin directory, and uv then installs nothing, not even `jade`. Move it aside (`mv ~/.local/bin/jev ~/.local/bin/jev.orig`) and install again, or add `--force` to the `uv tool install` command to overwrite it |
| `jade upgrade --check` exits 1 | GitHub cannot be reached, you are rate limited, or there is no release yet. Nothing was changed; try later, or name a tag: `jade upgrade --ref vX.Y.Z` |
| `jev` exits 5 | The provider is unavailable or the key is missing. 5 is never a yes: fix the cause or tell the user the claim is unverified |
| Claude Code does not show JADE messages | Hooks load at session start: open a new session; confirm with `jade hooks status` |

If none of that explains it, report it (README, Report a problem): attach `jade version` and `jade check --offline`, and read the output first for paths or names you do not want to share. Do not attach the ledger or an overlay: they hold your commands and hosts.

## 7. Uninstall completely

Order matters: remove the hook entries while `jade` is still installed, because `jade hooks uninstall` is what finds them. Each step is independent and safe to repeat.

```bash
jade hooks uninstall                       # removes only JADE's own entries from ~/.claude/settings.json; a backup stays next to it
jade hooks status                          # four lines, none "installed"
```

1. **CLAUDE.md.** Put back the `CLAUDE.md.bak-jade-<time>` that `jade claude-md apply` wrote next to `~/.claude/CLAUDE.md`, or delete the `## Jev: ...` section by hand.
2. **Skill.** `rm ~/.claude/skills/jade` (the link) and `rm -r ~/.agents/jade` (the directory `jade setup` created), if you ran setup.
3. **Maintenance job** (macOS, only if setup or you installed it): `launchctl bootout "gui/$(id -u)/com.jade.maintain"`, then delete `~/Library/LaunchAgents/com.jade.maintain.plist`. Use your own label if you made the plist with `jade launchd-plist --label`.
4. **Pre-push guard**, if you installed it: delete `.git/hooks/pre-push` in each repository, and for the global form `git config --global --unset core.hooksPath` and the directory (`docs/08` section 2.7).
5. **The tool.** `uv tool uninstall jade-decision-governance` removes `jade` and `jev`. If you had a `jev` script before JADE and moved it aside, move it back.
6. **State (optional, loses your history).** `rm -r ~/.config/jade` (gate mode, overlays) and `rm -r ~/.local/share/jade` (the ledger, pins and the permit seal key). Keep a copy if you want the decision history.
7. **Environment.** Remove `JADE_*` variables from your shell profile and from the `env` block of any Claude settings file. The OpenRouter key in the Keychain is yours: it stays unless you delete it.

Afterwards `command -v jade jev` prints nothing and `~/.claude/settings.json` has no JADE hook command.
