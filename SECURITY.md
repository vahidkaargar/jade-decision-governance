# Security policy

JADE governs when an AI agent may act on a probabilistic model's output. A bug here can let an agent execute a side effect it should not. Please report such issues privately.

- Report: open a GitHub security advisory on this repository (Security → Report a vulnerability), or email the maintainer listed on the GitHub profile.
- Scope: anything that lets a permit be issued or consumed without the documented human approval path, lets a model output be treated as authorization, bypasses a `policies/safety.yaml` invariant, or leaks state text that should have been redacted.
- Out of scope: the TypeSafe or OpenRouter services themselves; report those upstream.

The runtime is alpha; only the latest release receives fixes (see the table below). What it does not stop is written down in `docs/05-threat-model.md` (residual risks T2, T3, T18, T19, T22 and the work items they name); a report that only restates one of those is welcome as an ordinary issue. Reports about the design (`docs/`) are welcome as ordinary issues too.

## Supported versions

| Version | Supported |
|---|---|
| Latest release (see GitHub Releases) | Yes |
| Anything older | No: upgrade with `jade upgrade` |

## What to expect

You get an acknowledgement within 7 days. A confirmed issue is fixed in a new release and credited in `CHANGELOG.md` unless you ask not to be named. Please do not publish details until a fixed release exists. This is a single-maintainer project: there is no bug bounty and no guaranteed fix date.
