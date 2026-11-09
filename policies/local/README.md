# Local policy overlays

**Nothing reads this directory.** The runtime reads `risk.local.yaml` and `safety.local.yaml` from `~/.config/jade/local/` (or `$JADE_CONFIG/local/`); a file edited here changes no decision and does not change `policy_version`. Copy it there after every edit, or symlink `~/.config/jade/local` to this directory on a development machine. `jade check` row `overlay files` fails and names any `*.local.yaml` here that the runtime is not reading, and row `overlay lint` fails on what the loader would accept silently: an invalid regular expression, an empty or empty-matching `match`/`block`/`staging_markers` entry (it matches every command), an invariant without a block pattern, a `rules_override` that lowers a shipped critical or high rule (even to `medium`), and a key the loader does not merge from an overlay (`hosts`, `mcp_tools`, `read_only`, `inert_for_files`). Findings name the file and the rule id or list index, never the overlay text.

Files here named `*.local.yaml` are gitignored and hold what is specific to one machine: production host names, paths, deploy scripts, retired services. The shipped `policies/*.yaml` stay generic so the repository can be public.

- `risk.local.yaml` — see `risk.local.example.yaml`. Appends hosts/paths and adds site-specific rules, merged by severity tier ahead of the shipped rules of that tier. An overlay can raise a command's level; it cannot shadow a shipped rule of a higher tier.
- `safety.local.yaml` — see `safety.local.example.yaml`. Appends invariants only.

`policies/loader.py` is the reference merge implementation used by the tests and, later, by the `jade` runtime.
