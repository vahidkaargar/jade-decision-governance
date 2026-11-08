# Security policy

JADE governs when an AI agent may act on a probabilistic model's output. A bug here can let an agent execute a side effect it should not. Please report such issues privately.

- Report: open a GitHub security advisory on this repository (Security → Report a vulnerability), or email the maintainer listed on the GitHub profile.
- Scope: anything that lets a permit be issued or consumed without the documented human approval path, lets a model output be treated as authorization, bypasses a `policies/safety.yaml` invariant, or leaks state text that should have been redacted.
- Out of scope: the TypeSafe or OpenRouter services themselves; report those upstream.

No runtime code exists yet; until Phase 1 ships, reports about the design (`docs/`) are welcome as ordinary issues.
