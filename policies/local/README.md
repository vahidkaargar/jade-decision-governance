# Local policy overlays

Files here named `*.local.yaml` are gitignored and hold what is specific to one machine: production host names, paths, deploy scripts, retired services. The shipped `policies/*.yaml` stay generic so the repository can be public.

- `risk.local.yaml` — see `risk.local.example.yaml`. Appends hosts/paths and adds site-specific rules, merged by severity tier ahead of the shipped rules of that tier. An overlay can raise a command's level; it cannot shadow a shipped rule of a higher tier.
- `safety.local.yaml` — see `safety.local.example.yaml`. Appends invariants only.

`policies/loader.py` is the reference merge implementation used by the tests and, later, by the `jade` runtime.
