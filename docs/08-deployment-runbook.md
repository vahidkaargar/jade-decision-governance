# 08 — Deployment runbook

What is deployed, what only a human can do, how to verify each step, and how to undo it. Written for a single macOS host; paths are the defaults.

## 1. Already done by the agent (reversible, local)

| Step | Command | Undo |
|------|---------|------|
| Install the package as a uv tool (`jade`, `jev`) | `uv tool install .` from the checkout | `uv tool uninstall jade-decision-governance` |
| Keep the original script | `cp -p ~/.local/bin/jev ~/.local/bin/jev.orig` before installing | `rm ~/.local/bin/jev && mv ~/.local/bin/jev.orig ~/.local/bin/jev` |
| Private policy overlay | copy the gitignored `policies/local/*.local.yaml` to `~/.config/jade/local/` | delete that directory |
| Ledger | created on first call at `~/.local/share/jade/jade.sqlite`, mode 600 | `rm -r ~/.local/share/jade` (loses history) |

Measured rollback drill (2026-11-09, real binaries): shim to original in 1.1 ms, back in 0.1 ms.

## 2. Human-only steps

These change what an agent may do on this machine. The runtime refuses them for an agent session on purpose (invariant SI-09, `docs/07` section 3), so run them in your own terminal. Order matters.

### 2.1 Start in advisory mode

```bash
jade gate advisory
jade hooks install
```

`hooks install` backs up `~/.claude/settings.json` next to itself, adds four hook entries (PreToolUse for Bash, file edits and MCP tools; PostToolUse for Bash; Stop; SessionStart), and leaves other hooks alone. Hooks are read at session start: open a new Claude Code session afterwards.

In advisory mode the gate never blocks. It records a `would_block` event and prints what it would have done, so you can see the real false-positive rate before it can interrupt work.

Verify:

```bash
jade hooks status        # four lines, all "installed"
jade check               # every line "ok"; non-zero exit means dead wiring
jade ledger stats        # events.would_block counts the calls the gate would have stopped
```

### 2.2 Soak, then enforce

After at least a week of real use, read `jade ledger stats` and `jade decide list`. If the would-block cases are the ones you want stopped:

```bash
jade gate enforce
```

From then on a high or critical call stops with a pending permit id. You approve in a terminal:

```bash
jade permit list
jade permit approve prm_xxxxxxxxxxxx     # shows the exact command; type y
```

Single use, 30 minutes, bound to the exact command text and working directory. A safety invariant (for example pushing the default branch) cannot be approved. Claude Code's own permission prompt is a second, independent gate.

Emergency bypass for one launch: `JADE_GATE_MODE=off claude` (typed by you; an agent typing it is blocked).

### 2.3 Replace the Jev section of CLAUDE.md

```bash
jade claude-md show      # the replacement text, `docs/claude-md-jev-section.md`
jade claude-md apply     # replaces only the "## Jev: ..." section; writes CLAUDE.md.bak-jade-<time>
```

### 2.4 Daily maintenance job

Expires stale permits and decisions, purges state text older than 90 days, runs the provider canary and the improvement miner, and exits non-zero when wiring is dead.

```bash
jade launchd-plist --label com.example.jade-maintain > ~/Library/LaunchAgents/com.example.jade-maintain.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.example.jade-maintain.plist
launchctl print "gui/$(id -u)/com.example.jade-maintain" | head -5     # registered
launchctl kickstart "gui/$(id -u)/com.example.jade-maintain"           # run once now
tail -1 ~/.local/share/jade/maintain.log                               # one JSON line, "check_failed": []
```

Undo: `launchctl bootout "gui/$(id -u)/com.example.jade-maintain"` and delete the plist.

### 2.5 Hang the check off your health command

Add one line to the existing health script so silence is never mistaken for health: `jade check || exit 1` (or its equivalent in your tooling).

### 2.6 Install the skill

`skills/jade/SKILL.md` is the how-to for agents. Copy it into the place your agent tooling reads skills from. It documents behaviour; the exit codes and the gate enforce it.

## 3. Full rollback

| Layer | Command |
|-------|---------|
| Stop blocking, keep recording | `jade gate advisory` |
| Remove the hooks | `jade hooks uninstall` (backup of settings.json kept) |
| Restore the old CLAUDE.md section | copy back `CLAUDE.md.bak-jade-<time>` |
| Restore the old `jev` | section 1 undo |
| Stop the job | `launchctl bootout ...` |
| Freeze all improvement activity | `jade improve freeze` (human removes it with `jade improve unfreeze`) |
| Revert a promoted candidate | `jade improve rollback <candidate>`; thresholds revert to defaults by deleting `~/.local/share/jade/calibration/current.json` |

## 4. Known coverage limits

- Classification is syntactic. A command built at run time (base64, variable indirection, `eval` of fetched text) is not recognised. Tools the matcher does not name (browser and desktop automation MCP tools) are not gated. Claude Code's own permission prompts remain the backstop.
- Provider answers are as good as the evidence passed in. The companion check flags summaries, not lies.
- A model drift is informational by decision (follow upgrades): the new snapshot starts at default thresholds and earns changes with its own labels.

## 5. From IMPLEMENTED to VALIDATED

Mechanical criteria, all measured from the ledger (`docs/06` exit lines):

1. 14 days of ledger data under enforce mode: `jade ledger stats --days 14` reproduces `docs/04` section 4.
2. Abstain or reject answers without a disposition no more than 5% of non-accepts; zero unsafe accepts for risk high or above.
3. At least 200 labelled rows in a calibration group before any threshold change: `jade label queue`, `jade label set`, `jade eval run`, `jade eval propose`.
4. One full improvement cycle: `jade improve mine`, `jade eval propose`, `jade improve shadow`, a human `jade improve promote`.

PRODUCTION_APPROVED is yours: it means enforce mode with critical rules on this machine, decided by you after the above.
