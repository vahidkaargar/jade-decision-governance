# JADE backlog

Source of truth: `work-items.jsonl` (validated by `tests/test_schemas.py`). This file is the human-readable rendering; until `jade work render` exists (Phase 1) keep both in sync by hand.

## Decisions taken (maintainer, 2026-11-09)

| Q | Question | Decision |
|---|----------|----------|
| Q1 | Approve the 12 ADRs and scope cuts? | Approved. DESIGN_READY. |
| Q2 | TypeSafe direct key or OpenRouter? | Stay on OpenRouter; adapter stays swappable; revisit on first outage. |
| Q3 | Risk rules right? | Edited: commits/checkouts and installs/downloads are low; any `ssh`/`scp` and any `composio execute` are high; machine-specific production hosts, paths and deploy scripts live in the gitignored overlay `policies/local/risk.local.yaml`. |
| Q4 | Permit UX (one TTY approval per high/critical action, 30-minute TTL, single use)? | Accepted. |
| Q5 | Repo home? | Public GitHub repository `jade-decision-governance`, MIT, generic policies plus local overlay. |
| Q6 | Pin model and freeze on drift? | Follow upgrades. A new resolved snapshot is a new calibration group that starts at default thresholds; nothing is frozen. |
| Q7 | Ledger retention? | State text purged after 90 days; metrics kept. |

No human decision is open. Next: W-02 (Phase 1).

## Items

| Id | P | Status | Owner | Phase | Title | Depends on | Trigger | Next action |
|----|---|--------|-------|-------|-------|------------|---------|-------------|
| W-01 | P0 | done | human | 0 | Approve JADE design (DESIGN_READY gate) | — | — | Approved 2026-11-09 (Q1, Q2, Q4, Q6, Q7 answered; Q3, Q5 in progress) |
| W-02 | P0 | open | agent | 1 | Phase 1: jade package, SQLite ledger, adapter, redact(), jev shim | W-01 | W-01 done | Scaffold uv package; adapter vs recorded fixtures |
| W-03 | P2 | done | human | 1 | Decide: TypeSafe direct key or stay on OpenRouter alpha | — | first outage | Stay on OpenRouter (2026-11-09) |
| W-04 | P1 | blocked | runtime | 1 | Re-measure baseline from ledger after 14 days | W-02 | +14 d after W-02 | `jade ledger stats`; update docs/04 §4 |
| W-05 | P1 | blocked | agent | 2 | Question registry, lint, route preflight, exit codes, Stop-hook reminder | W-02 | W-02 done | registry/*.json for 7 v1 questions |
| W-06 | P1 | blocked | agent | 2 | Permits, TTY approval, PreToolUse gate for risk ≥ high | W-02, W-19 | W-02 done | `jade permit`, `jade hook pretool`, tests in docs/06 |
| W-07 | P1 | blocked | human | 2 | Rewrite CLAUDE.md Jev section | W-05 | W-05 done | Agent drafts diff; human applies |
| W-08 | P1 | blocked | agent | 3 | Label pipeline + adjudication queue | W-04 | W-04 done | `jade label` |
| W-09 | P2 | blocked | agent | 3 | Eval sets with session-hash splits | W-05 | W-05 done | Seed regression from baseline failure classes |
| W-10 | P2 | blocked | agent | 3 | Performance harness incl. 429 | W-02 | W-02 done | Extend probe |
| W-11 | P2 | blocked | agent | 4 | `jade improve mine` → task-observer + improvement records | W-08, W-09 | W-09 done | Implement miner |
| W-12 | P3 | open | human | 1 | Update typesafe-ai plugin 0.5.7 → 0.6.2 after diff review | — | 2026-12-01 | Diff upstream; update if docs-only |
| W-13 | P1 | blocked | agent | 2 | jade SKILL.md + references | W-01, W-05 | W-05 done | Draft from docs/03 §6; install `~/.agents/jade` |
| W-14 | P2 | blocked | agent | 3 | Persian/English subgroup slices | W-09 | W-09 done | Tag cases by script |
| W-15 | P2 | blocked | agent | 2 | `jade check` canary + host health check + launchd drift check | W-02 | W-02 done | After adapter |
| W-16 | P3 | open | agent | 1 | Probe idempotency/retry-after on first 429 | — | first 429 | Record headers |
| W-17 | P3 | blocked | human | 5 | Multi-host / multi-tenant | W-06 | request for another host | New ADR |
| W-18 | P3 | open | agent | 1 | Inspect 78 unparsed baseline outputs | — | before W-04 | Classify; update docs/00 §3 |
| W-19 | P1 | done | human | 2 | Review risk.yaml hosts/order and safety.yaml | — | — | Decided 2026-11-09 (Q3); overlay written locally |
| W-20 | P2 | blocked | agent | 5 | Rollback drill, kill switch, drift injection | W-11 | W-11 done | Script and time the drill |
| W-21 | P2 | done | human | 1 | Ledger retention policy | — | — | 90 days state text, metrics forever (2026-11-09) |

Aging: P2 → P1 after 14 idle days; P3 → P2 after 30. Last rendered 2026-11-09.
