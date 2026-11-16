"""Run shell commands from a test corpus in a real shell, inside a sandbox. Nothing else may run such a corpus.

Why: on 2026-11-15 a review harness ran a guard-test corpus in real /bin/bash and /bin/zsh with PATH stubs for a few programs only. The corpus held
`chmod -R 777 /`; chmod was not stubbed, so the real chmod walked the whole disk and made every file of the user 777. A timeout killed the shell
but not the chmod. PATH stubs are a convenience for recording calls, never a protection: an absolute path, `command -p`, `env -i` or a missing
stub runs the real program. See docs/13-real-shell-payload-tests.md.

What this module does:
- Every command runs under macOS `sandbox-exec` with a profile that denies every file write outside one fresh work root, all network, Mach
  lookups (so no launchd job, Apple event or app launch can act for the payload), signals to other processes, reads of the real home folder,
  and exec of launchctl, open, osascript, sudo, su, login, at, batch, crontab and ssh.
- Before any command runs, a canary check proves the sandbox works: inside it, chmod, append, rm and mv on a file outside the root must all
  fail and a write inside the root must succeed. If the check fails, or no sandbox exists on this platform, nothing runs. There is no flag
  or environment variable that skips the check.
- Each case runs in its own process group with a clean environment (HOME, TMPDIR and the log inside the case folder). The whole group is
  killed when the case ends or times out, so nothing the payload started in the background outlives it.
- PATH stubs still record calls (git, rm, chmod, ...) so an oracle can see what the shell would have run.

CLI:
    python3 evals/realshell.py selftest
    python3 evals/realshell.py run CORPUS OUT [--shells bash,zsh] [--jobs 8] [--timeout 5]
    python3 evals/realshell.py audit PATH [PATH ...]

CORPUS is a JSON list of strings or of objects with "cmd", or JSON Lines with "cmd" or "command". OUT is a JSON list in corpus order.
`audit` lists Python files that start a shell but do not use this module; it exits 1 if it finds any. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SHELLS = {
    "bash": ["/bin/bash", "--noprofile", "--norc"],
    "zsh": ["/bin/zsh", "-f"],
    "sh": ["/bin/sh"],
}
# Recorded, never run. The sandbox is the protection; these only make the calls visible to an oracle.
STUBS = ("git", "gh", "rm", "curl", "wget", "dd", "chmod", "chown", "chgrp", "mv", "ssh", "scp", "rsync", "kill", "killall", "pkill",
         "launchctl", "osascript", "open", "diskutil", "shutdown", "reboot", "halt", "jade", "jev")
# Recorded, then the rest of the command runs (still inside the sandbox), so `sudo git push` shows the git call.
PASSTHROUGH = ("sudo",)
DENIED_EXEC = ("/bin/launchctl", "/usr/bin/open", "/usr/bin/osascript", "/usr/bin/sudo", "/usr/bin/su", "/usr/bin/login", "/usr/bin/at",
               "/usr/bin/batch", "/usr/bin/crontab", "/usr/bin/ssh")
OUTPUT_LIMIT = 4000
LOG_ENV = "JADE_REALSHELL_LOG"


class SandboxUnavailable(RuntimeError):
    """No sandbox backend on this machine. Nothing ran."""


class SandboxBroken(RuntimeError):
    """The canary check found a write outside the work root. Nothing from the corpus ran."""


def profile_text() -> str:
    deny_exec = " ".join(f'(literal "{p}")' for p in DENIED_EXEC)
    return "\n".join([
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny mach-lookup)",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        f"(deny process-exec {deny_exec})",
        '(deny file-read* (subpath (param "DENY_READ")))',
        '(allow file-read* (subpath (param "ROOT")))',
        "(deny file-write*)",
        '(allow file-write* (subpath (param "ROOT")) (literal "/dev/null") (literal "/dev/zero") (regex #"^/dev/fd/"))',
        "",
    ])


def real_home() -> str:
    """The account's home folder from the user database, not $HOME (a harness often points HOME at a fake folder)."""
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def _stub_text(name: str) -> str:
    record = "{ printf '%s\\037' \"${0##*/}\" \"$@\"; printf '\\036'; } >> \"$" + LOG_ENV + "\"\n"
    if name in PASSTHROUGH:
        skip_opts = ('while [ $# -gt 0 ]; do case "$1" in --) shift; break;; -u|-g|-h|-p|-C|-U|-r|-t|-T) shift 2;; -*) shift;; *) break;; '
                     'esac; done\n')
        return "#!/bin/bash\n" + record + skip_opts + '[ $# -gt 0 ] && exec "$@"\nexit 0\n'
    return "#!/bin/bash\n" + record + "exit 0\n"


def _parse_log(data: str) -> list[list[str]]:
    return [[p for p in rec.split("\x1f") if p != ""] for rec in data.split("\x1e") if rec]


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


class Sandbox:
    """A fresh work root plus a checked sandbox profile. Building one runs the canary check; it raises instead of running anything unsafe.

    `deny_read` defaults to the real home folder; tests point it at a temporary folder.
    """

    def __init__(self, root: str | os.PathLike | None = None, deny_read: str | os.PathLike | None = None):
        if sys.platform != "darwin" or not os.access(SANDBOX_EXEC, os.X_OK):
            raise SandboxUnavailable(
                f"no sandbox backend on this machine ({sys.platform}; {SANDBOX_EXEC} not found). Real-shell payload corpora run only on macOS "
                "under sandbox-exec; see docs/13-real-shell-payload-tests.md. Nothing ran.")
        self._own_root = root is None
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="jade-realshell-") if root is None else root)
        if self.root in ("/", real_home()) or not os.path.isdir(self.root):
            raise SandboxBroken(f"refusing work root {self.root!r}: it must be an existing folder that is not / or the home folder")
        self.deny_read = os.path.realpath(deny_read if deny_read is not None else real_home())
        self.profile = os.path.join(self.root, "sandbox.sb")
        Path(self.profile).write_text(profile_text())
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin, exist_ok=True)
        for name in STUBS + PASSTHROUGH:
            p = os.path.join(self.bin, name)
            Path(p).write_text(_stub_text(name))
            os.chmod(p, 0o755)
        self.cases = os.path.join(self.root, "cases")
        os.makedirs(self.cases, exist_ok=True)
        self.check()

    def argv(self, inner: list[str]) -> list[str]:
        return [SANDBOX_EXEC, "-f", self.profile, "-D", f"ROOT={self.root}", "-D", f"DENY_READ={self.deny_read}", *inner]

    def check(self) -> None:
        """Canary check: prove that writes outside the root fail and writes inside it work. Raises SandboxBroken otherwise."""
        outside = os.path.realpath(tempfile.mkdtemp(prefix="jade-realshell-canary-"))
        try:
            canary = os.path.join(outside, "canary")
            Path(canary).write_text("canary\n")
            os.chmod(canary, 0o644)
            inside = os.path.join(self.root, "canary-inside")
            script = ('/bin/chmod 600 "$1"; /usr/bin/touch "$2/new"; echo x >> "$1"; /bin/mv "$1" "$1.moved"; /bin/rm -f "$1"; '
                      'echo ok > "$3"')
            proc = subprocess.Popen(self.argv(["/bin/bash", "--noprofile", "--norc", "-c", script, "canary", canary, outside, inside]),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                                    env={"PATH": "/usr/bin:/bin", "HOME": self.root, "LC_ALL": "C"})
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            finally:
                _kill_group(proc)
            problems = []
            if not os.path.exists(canary) or Path(canary).read_text() != "canary\n":
                problems.append("canary changed or removed")
            elif os.stat(canary).st_mode & 0o777 != 0o644:
                problems.append(f"canary mode changed to {oct(os.stat(canary).st_mode & 0o777)}")
            if os.path.exists(canary + ".moved") or os.path.exists(os.path.join(outside, "new")):
                problems.append("a file was created outside the work root")
            if not os.path.exists(inside):
                problems.append("a write inside the work root failed")
            if problems:
                raise SandboxBroken("sandbox canary check failed: " + "; ".join(problems) + ". Nothing from the corpus ran.")
            os.unlink(inside)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def run(self, cmd: str, shell: str = "bash", timeout: float = 5.0, case: str | None = None) -> dict:
        """Run one command in one shell inside the sandbox. Returns rc, timed_out, the recorded stub calls and capped output."""
        if shell not in SHELLS:
            raise ValueError(f"unknown shell {shell!r}; one of {sorted(SHELLS)}")
        d = tempfile.mkdtemp(prefix=(case or "c") + "-", dir=self.cases)
        home, tmp = os.path.join(d, "home"), os.path.join(d, "tmp")
        os.makedirs(home)
        os.makedirs(tmp)
        script, log = os.path.join(d, "cmd.sh"), os.path.join(d, "calls.log")
        Path(script).write_bytes(cmd.encode("utf-8", "surrogateescape") + b"\n")
        Path(log).write_text("")
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": home, "TMPDIR": tmp + "/", LOG_ENV: log, "TERM": "dumb", "LC_ALL": "C"}
        out_p, err_p = os.path.join(d, "stdout"), os.path.join(d, "stderr")
        timed_out = False
        with open(out_p, "wb") as out, open(err_p, "wb") as err:
            proc = subprocess.Popen(self.argv([*SHELLS[shell], script]), cwd=d, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    start_new_session=True)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                _kill_group(proc)
        result = {
            "shell": shell,
            "rc": proc.returncode,
            "timed_out": timed_out,
            "calls": _parse_log(Path(log).read_text(errors="replace")),
            "stdout": Path(out_p).read_bytes()[:OUTPUT_LIMIT].decode("utf-8", "replace"),
            "stderr": Path(err_p).read_bytes()[:OUTPUT_LIMIT].decode("utf-8", "replace"),
        }
        shutil.rmtree(d, ignore_errors=True)
        return result

    def run_corpus(self, cmds: list[str], shells=("bash", "zsh"), jobs: int = 8, timeout: float = 5.0) -> list[dict]:
        def one(item):
            i, c = item
            return {"cmd": c, "results": {sh: self.run(c, sh, timeout, case=f"{i}-{sh}") for sh in shells}}
        with ThreadPoolExecutor(max(1, jobs)) as ex:
            return list(ex.map(one, enumerate(cmds)))

    def close(self) -> None:
        if self._own_root:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def load_corpus(path: str | os.PathLike) -> list[str]:
    text = Path(path).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    out = []
    for x in data if isinstance(data, list) else [data]:
        c = x if isinstance(x, str) else (x.get("cmd") or x.get("command") if isinstance(x, dict) else None)
        if not isinstance(c, str):
            raise ValueError(f"corpus entry without a command: {str(x)[:80]!r}")
        out.append(c)
    return out


# ---- audit: find harnesses that start a shell without this module ---------------------------------------------------------------------------
# One implementation, shared with the gate (SI-13): jade/harness_audit.py, loaded by path so this file still runs from any interpreter.
def _load_audit():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_jade_harness_audit", Path(__file__).resolve().parents[1] / "jade" / "harness_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_audit = _load_audit()
audit_source = _audit.audit_source
gate_findings = _audit.gate_findings


def audit_paths(paths) -> list[tuple[str, int, str]]:
    out = []
    for p in paths:
        p = Path(p)
        files = sorted(p.rglob("*.py")) if p.is_dir() else [p]
        for f in files:
            try:
                src = f.read_text(errors="replace")
                findings = audit_source(src)
            except (OSError, SyntaxError, ValueError):
                continue
            out += [(str(f), line, why) for line, why in findings]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="realshell", description="Run shell payload corpora only inside a checked sandbox.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest", help="build the sandbox and run the canary check")
    r = sub.add_parser("run", help="run a corpus in real shells inside the sandbox")
    r.add_argument("corpus")
    r.add_argument("out")
    r.add_argument("--shells", default="bash,zsh")
    r.add_argument("--jobs", type=int, default=8)
    r.add_argument("--timeout", type=float, default=5.0)
    a = sub.add_parser("audit", help="list Python files that start a shell without this module")
    a.add_argument("paths", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "audit":
        found = audit_paths(args.paths)
        for f, line, why in found:
            print(f"{f}:{line}: starts a shell without evals/realshell.py ({why})")
        return 1 if found else 0
    try:
        with Sandbox() as sb:
            if args.cmd == "selftest":
                print(f"sandbox ok: sandbox-exec, canary check passed, root {sb.root}")
                return 0
            cmds = load_corpus(args.corpus)
            shells = tuple(s for s in args.shells.split(",") if s)
            res = sb.run_corpus(cmds, shells=shells, jobs=args.jobs, timeout=args.timeout)
            Path(args.out).write_text(json.dumps(res))
            print(f"ran {len(cmds)} commands in {','.join(shells)} inside the sandbox; {args.out}")
            return 0
    except (SandboxUnavailable, SandboxBroken) as e:
        print(f"realshell: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
