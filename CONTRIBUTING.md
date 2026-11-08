# Contributing

JADE is at the design stage (see `README.md` for the status ladder). Contributions that help most right now:

- Review of the ADRs (`docs/01-adrs.md`) and contracts (`schemas/`): open an issue naming the ADR or schema and the failure you foresee.
- Policy patterns in `policies/risk.yaml` and `policies/safety.yaml`: generic patterns only. Anything specific to your hosts belongs in your gitignored `policies/local/*.yaml` overlay.
- Tests. Every schema or policy change ships with a test in `tests/`.

## Workflow

```bash
uv sync
uv run ruff check .
uv run pytest -q
```

Pull requests target `main`, pass CI, and keep `additionalProperties: false` on every contract. Bump `schema_version` in the affected schema and its example when fields change (additive: minor; removal: major, with a migration note in `CHANGELOG.md`).

## Design rules that are not up for a quick PR

The invariants in `policies/safety.yaml` and the promotion rules in `policies/promotion.yaml` are protected targets. Changes to them need an issue first, with the threat-model row (`docs/05-threat-model.md`) they affect.

## Reporting a security problem

See `SECURITY.md`.
