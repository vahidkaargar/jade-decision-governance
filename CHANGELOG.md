# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog; versioning: SemVer once code ships.

## [Unreleased]

## [0.2.0] - 2026-11-09

### Added
- `jade` runtime package and `jev` compatibility shim (same flags and output line, plus exit codes 0/3/4/5/6).
- SQLite ledger with write-ahead call rows, journaled state-machine events, dedup, 90-day state purge and in-place migrations.
- OpenRouter adapter with typed errors and bounded retries; redaction with a 42-item corpus and a residual-secret refusal.
- Question registry (10 entries), lint, preflight routing, deterministic `jade fast` checks.
- Permits (single use, 30-minute TTL, plan-hash bound), TTY-only approval, PreToolUse gate for Bash, file edits and MCP tools, Stop-hook disposition reminder, SessionStart resumption, advisory/enforce/off modes.
- Calibration: labels, session-hash splits, metrics with Wilson and paired-bootstrap intervals, adjudication queue, threshold proposals, language slices.
- Bounded improvement loop: ledger miner writing task-observer observations, offline replay, async shadow runner, 20% canary, champion/challenger pins, kill switch, rollback.
- `jade check`, `jade maintain`, launchd plist generator, hook and CLAUDE.md installers that refuse to run for an agent.
- 35 replayable regression and adversarial cases; `skills/jade/SKILL.md`; `docs/08-deployment-runbook.md`.

### Changed
- Policies: read-only commands and quoted or heredoc text no longer trigger rules (found by false positives while building the classifier); `mcp_tools` rules added; SI-09 stops an agent from approving permits or changing its own wiring.

### Added
- Phase 0 design package: inventory and gap register, 12 ADRs, state machine, six JSON Schema contracts with examples, five policy files with a local-overlay mechanism, evaluation protocol, threat model, roadmap, backlog.
- Baseline tooling: transcript miner, policy replay, live performance harness, OpenRouter probe.
- Executable acceptance-test skeletons for Phases 1–5 (skip until the `jade` package exists).
