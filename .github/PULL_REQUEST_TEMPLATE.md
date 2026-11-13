## What and why

## Checks
- [ ] `uv run ruff check .` and `uv run pytest -q` pass
- [ ] A gate or classifier change ships a replayable case in `evals/adversarial/cases.jsonl` or `evals/regression/cases.jsonl`
- [ ] A policy or schema change ships a test; `CHANGELOG.md` has an `Unreleased` entry
- [ ] No hosts, paths, keys or personal data (those belong in the gitignored `policies/local/` overlay)
- [ ] I did not edit `policies/safety.yaml` or `policies/promotion.yaml` without an issue first
