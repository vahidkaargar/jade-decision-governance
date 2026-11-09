# Security policy

JADE governs when an AI agent may act on a probabilistic model's output. A bug here can let an agent execute a side effect it should not. Please report such issues privately.

- Report: open a GitHub security advisory on this repository (Security → Report a vulnerability), or email the maintainer listed on the GitHub profile.
- Scope: anything that lets a permit be issued or consumed without the documented human approval path, lets a model output be treated as authorization, bypasses a `policies/safety.yaml` invariant, or leaks state text that should have been redacted.
- Out of scope: the TypeSafe or OpenRouter services themselves; report those upstream.

The runtime ships in `jade` 0.2.x. What it does not stop is written down in `docs/05-threat-model.md` (residual risks T2, T3, T18, T19, T22 and the work items they name); a report that only restates one of those is welcome as an ordinary issue. Reports about the design (`docs/`) are welcome as ordinary issues too.
