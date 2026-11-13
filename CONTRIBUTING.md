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
uv run pytest -q
uv run python evals/run_cases.py
```

Pull requests target `main`, pass CI (pytest, ruff and the case replay), and keep `additionalProperties: false` on every contract. Bump `schema_version` in the affected schema and its example when fields change (additive: minor; removal: major, with a migration note in `CHANGELOG.md`).

## Design rules that are not up for a quick PR

The invariants in `policies/safety.yaml` and the promotion rules in `policies/promotion.yaml` are protected targets. Changes to them need an issue first, with the threat-model row (`docs/05-threat-model.md`) they affect.

## Conduct

Be respectful: see `CODE_OF_CONDUCT.md`.

## Reporting a security problem

See `SECURITY.md`.
