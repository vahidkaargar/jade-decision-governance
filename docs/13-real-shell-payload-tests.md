# 13. Real-shell payload tests run only in a sandbox

Status: rule, in force from 2026-11-16. Tooling: `evals/realshell.py`. Tests: `tests/test_realshell_sandbox.py`.

## The rule

1. A test, eval, probe or review harness that runs a command from a guard-test corpus in a real shell (`/bin/bash`, `/bin/zsh`, `/bin/sh` or any other) runs it through `evals/realshell.py`. Nothing else may execute such a corpus. This holds for scratch harnesses in a scratchpad or worktree too, not only for files in this repository.
2. PATH stubs are not a protection. They record calls for an oracle; they do not stop an absolute path (`/bin/chmod`), `command -p`, `env -i`, an interpreter (`perl -e 'chmod ...'`), or simply a program nobody stubbed.
3. If the sandbox is missing or its canary check fails, nothing runs. There is no flag or environment variable that skips the check, and a harness must not add one.
4. Never point a destructive payload at a real path to "see what happens", not even inside the sandbox. Tests use canary files they created themselves, outside the sandbox root.
5. Before a review round that runs real shells, run `python3 evals/realshell.py audit <scratch folder>` over the harness folder. It must print nothing and exit 0.

## Why

On 2026-11-15 a review harness checked the shell guard against corpora in real `bash` and `zsh`. It set `PATH` to a folder of stubs for `git`, `gh` and `rm`, and a fake `HOME`. The corpus had been generated from a list of gated programs that included `chmod -R 777 /`, and fifty such rows went into one run. `chmod` had no stub, so `/bin/chmod -R 777 /` ran for real. The runner's timeout killed the shell after 6 seconds but not its child, so the orphaned `chmod` kept walking the disk for about 45 minutes and set every file the user owned (home folder, Homebrew, applications) to mode 777. SSH then refused the world-writable `~/.ssh/config`. Two other runners in the same review stubbed a few more programs but still not `chmod`; a third used `sandbox-exec` with `(allow default)` and denied only network and four programs, which stops no file write.

The lessons are built into the helper: deny writes, do not enumerate dangerous programs; check the sandbox with a canary before trusting it; and kill the whole process group, never only the shell.

## How to use it

```bash
python3 evals/realshell.py selftest
python3 evals/realshell.py run corpus.json out.json --shells bash,zsh --jobs 8 --timeout 5
python3 evals/realshell.py audit path/to/harness/folder
```

`corpus.json` is a JSON list of strings or of objects with `cmd`, or JSON Lines with `cmd` or `command`. `out.json` is a list in corpus order; each entry has, per shell, `rc`, `timed_out`, `calls` (the recorded stub calls, for example `["git", "push", "origin", "main"]`), and the first 4000 bytes of stdout and stderr.

From Python, load the module by path (it uses the standard library only, so it works from any interpreter):

```python
import importlib.util
spec = importlib.util.spec_from_file_location("realshell", "<repo>/evals/realshell.py")
realshell = importlib.util.module_from_spec(spec); spec.loader.exec_module(realshell)
with realshell.Sandbox() as sb:                 # builds the sandbox and runs the canary check, or raises
    r = sb.run("git push origin :main", "zsh")  # r["calls"] == [["git", "push", "origin", ":main"]]
```

## What the sandbox does

- macOS `sandbox-exec` with a profile that denies every file write except inside one fresh work root (plus `/dev/null`, `/dev/zero` and `/dev/fd`). That covers create, append, truncate, chmod, chown, rename, unlink, links and extended attributes.
- No network, including to this machine. No Mach lookups, so no launchd job, Apple event or app launch can act for the payload outside the sandbox. No signals to processes outside the sandbox. No reads of the real home folder (the account's home from the user database, not `$HOME`).
- Exec of `launchctl`, `open`, `osascript`, `sudo`, `su`, `login`, `at`, `batch`, `crontab` and `ssh` is denied.
- The canary check runs every time a `Sandbox` is built: inside the sandbox, chmod, append, mv and rm on a file outside the root and a create next to it must all fail, and a write inside the root must work.
- Each case gets its own folder with `HOME`, `TMPDIR` and the call log, a clean environment, and `PATH` with the stubs first. It runs in its own process group, and the whole group is killed when the case ends or times out, so a background job cannot outlive it.
- Stubs that only record: `git gh rm curl wget dd chmod chown chgrp mv ssh scp rsync kill killall pkill launchctl osascript open diskutil shutdown reboot halt jade jev`. `sudo` records, drops its options and runs the rest inside the sandbox. A call that bypasses `PATH` is not recorded, but it is still confined.

## Limits

- macOS only. On Linux (CI included) `Sandbox()` raises `SandboxUnavailable` and the CLI exits 2 without running anything. The sandbox tests are skipped there with a reason that says so; the refusal and audit tests run everywhere. A Linux backend (bubblewrap, or a container with no network and a read-only bind of the host) is not built yet.
- `sandbox-exec` is deprecated by Apple but present and working on current macOS. If a future macOS removes it, the helper refuses instead of running unconfined.
- The audit is a static check of Python files. It finds a file that starts a process and names a shell path, an argv list that starts with a shell name, `shell=True`, `os.system` or `os.popen`, and that does not import or name `realshell`. It does not read shell scripts, and code built to hide from it will pass. Inside this repository `tests/test_realshell_sandbox.py` keeps an exact list of files that may start a shell without the helper, each with a reason; a new one fails the test.
- Scratch harnesses outside the repository are covered by this rule and the audit command only. A gate rule that refuses a real-shell corpus run outside the helper would need a change to protected policy files; it is tracked as a work item for the owner.

## Review checklist for a harness

- Does every real-shell execution go through `evals/realshell.py`?
- Does `audit` over the harness folder print nothing?
- Do destructive payloads target only canaries the harness created?
- Is there no `--no-sandbox`, `SKIP_*` or similar escape?
