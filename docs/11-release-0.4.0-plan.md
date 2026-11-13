# 0.4.0 plan: the enforce-ready release (W-88)

Status 2026-11-13. Scope: make `enforce` usable on a same-user, no-prompt host without daily false positives. 0.4.0 is not VALIDATED: that needs 14 days under enforce with outcomes and labels (docs/04), so enforce comes first and the label comes after the soak.

## 1. What 0.4.0 contains (all merged on `main` at e7eae15, CI green)

| Item | Work item | Where |
|---|---|---|
| SI-11 stops matching a scratch `JADE_HOME`/`JADE_CONFIG` prefix before a test, lint or jade call (942 false `safety_block` events came from this) | W-48 | CHANGELOG Changed |
| Signed permit approval: a high or critical permit needs an Ed25519 signature that only the `_jadeapprove` account makes; the hook verifies against a root-owned anchor | W-38, W-89 | docs/10 |
| `jade gate enforce` refuses without the signed backend unless `--allow-unsigned` (journaled) | W-89 | docs/10 |
| `jade check` row `approval backend`; install and uninstall script; review hardening (absolute programs, root-owned chain, default anchor wins) | W-89 | docs/10 section 4.4 |

## 2. Gates, in order

1. **Human, due 2026-11-16:** rating decisions W-62, W-63, W-64, W-66 and the W-46 wrapper decision. They change what `enforce` blocks; decide before the switch.
2. **Agent, before the tag:** close residuals that make enforce noisy or bypassable: W-37 and W-31 (classification gaps), W-93 (test isolation: must land before W-90 or the maintainer's suite can fail after the anchor is installed), W-87 (document the release flow under R-24). W-92 and W-91 stay recorded residuals, not blockers.
3. **Agent:** bump `pyproject.toml` to 0.4.0, move CHANGELOG `Unreleased` to `[0.4.0]`, update docs/08 and README. A version bump is gated by R-24: it needs the human's terminal or a permit.
4. **Human, W-90:** read and run `bash scripts/install-approver.sh`; `jade check` shows `approval backend: signed`; one real approval is consumed once and a replay is refused. UNVERIFIED until run: the installer on a real macOS or Linux host.
5. **Human:** read R-14 (ssh, 493) and M-07 (180) volume from the advisory data, then `jade gate enforce`. Without step 4 the command refuses.
6. **Tag:** only from merged `origin/main` with CI green on that commit; watch the release run by id; then install the published assets in a sandbox (own HOME, tool dir, ledger) and check checksums, `--version` and the upgrade check.
7. **Soak:** 14 days under enforce. Then label adjudication (W-27), re-measure the baseline (W-04), and decide VALIDATED.

## 3. Not in 0.4.0

W-91 hardware key, W-92 tamper-proof consumption record, W-17 multi-host, W-28 package rename, W-29 classification beyond syntax. Each stays a backlog row with an owner.

## 4. Rollback

`jade gate advisory` returns to record-only without data loss. `bash scripts/install-approver.sh --uninstall` removes the account, key, helper, sudoers rule, anchor and the `approval_backend` config key.
