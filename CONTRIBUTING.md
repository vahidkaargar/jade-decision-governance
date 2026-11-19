# Contributing

JADE is an alpha runtime (design approved and implemented, not yet validated on real ledger data; see `README.md` for the status ladder). Contributions that help most right now:

- Review of the ADRs (`docs/01-adrs.md`) and contracts (`schemas/`): open an issue naming the ADR or schema and the failure you foresee.
- Policy patterns in `policies/risk.yaml` and `policies/safety.yaml`: generic patterns only. Anything specific to your hosts belongs in your gitignored `policies/local/*.yaml` overlay.
- Tests. Every schema or policy change ships with a test in `tests/`; every gate or classifier change also ships a replayable case in `evals/adversarial/cases.jsonl` or `evals/regression/cases.jsonl`.
- Docs that match the code. `tests/test_docs_truth.py` fails when a document, skill or work item names a `jade` command that does not exist, or a threat row (`docs/05-threat-model.md`) has no evidence.

## Workflow

```bash
uv sync
uv run ruff check .
uv run pytest -q -n auto --dist loadgroup -m "not timing"   # parallel (pytest-xdist); plain `uv run pytest -q` runs the same tests one by one
uv run pytest -q -m timing                 # the wall-clock budget tests, serially: load from other workers would fail them
uv run python evals/run_cases.py
```

Every test gets its own JADE home, config and settings directory (`tests/conftest.py`), so tests can run in any order and in parallel. A test that asserts a time budget carries `@pytest.mark.timing`; CI runs those in their own serial step. `tests/test_timing_marks.py` fails when a test reads `time.monotonic`, `time.perf_counter` or `time.process_time` and has no mark.

## Real-shell payload tests: sandbox only

A test, eval, probe or review harness (scratch ones included) that runs a guard-test command in a real shell runs it through `evals/realshell.py` and nothing else: macOS `sandbox-exec`, writes only inside a fresh work root, a canary check before every run, the whole process group killed after every case. PATH stubs alone are never enough; a missing stub once let `chmod -R 777 /` run for real. Destructive payloads target only canaries the test created. Before a review round, `python3 evals/realshell.py audit <harness folder>` must print nothing. The gate enforces the common case: SI-13 blocks a command that runs a Python payload runner without the helper. Details: `docs/13-real-shell-payload-tests.md`.

Pull requests target `main`, pass CI (pytest, ruff and the case replay on Linux; the setup tests, the hook payload tests and `scripts/verify_install.sh` on macOS; a secret scan of the new commits with `.gitleaks.toml`, which allows fake credentials only under `tests/` and `evals/`), and keep `additionalProperties: false` on every contract. Bump `schema_version` in the affected schema and its example when fields change (additive: minor; removal: major, with a migration note in `CHANGELOG.md`).

## Design rules that are not up for a quick PR

The invariants in `policies/safety.yaml` and the promotion rules in `policies/promotion.yaml` are protected targets. Changes to them need an issue first, with the threat-model row (`docs/05-threat-model.md`) they affect.

## Conduct

Be respectful: see `CODE_OF_CONDUCT.md`.

## Reporting a security problem

See `SECURITY.md`.
