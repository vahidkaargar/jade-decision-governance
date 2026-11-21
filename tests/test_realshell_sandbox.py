"""evals/realshell.py: shell payload corpora run in a real shell only inside a checked sandbox (docs/13-real-shell-payload-tests.md).

Every destructive call in this file targets a canary the test made in its own temporary folder, outside the sandbox root. No test runs a
payload against /, the home folder or any path it did not create. The sandbox tests need macOS sandbox-exec; elsewhere they are skipped with
a loud reason, and the refusal tests and the audit tests run on every platform.
"""

import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("realshell", ROOT / "evals" / "realshell.py")
realshell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(realshell)

HAVE_SANDBOX = sys.platform == "darwin" and os.access(realshell.SANDBOX_EXEC, os.X_OK)
needs_sandbox = pytest.mark.skipif(not HAVE_SANDBOX, reason="NOT VERIFIED HERE: macOS sandbox-exec is missing, so the sandbox itself is untested "
                                                              "on this platform (the refusal and audit tests still ran)")

# Files that start a shell for a reason other than running a payload corpus. Adding a file here needs a reason a reviewer can check.
ALLOWED_SHELL_RUNNERS = {
    "jade/improve.py": "runs the task-observer new-observation.sh helper with fixed arguments; no corpus",
    "tests/acceptance/test_gate_residuals_w209.py": "starts no shell: ['bash', '-c', ...] is only data passed to policy.assess / policy.mcp_command as the text of an MCP tool call; nothing is run",
    "tests/acceptance/test_signed_approval_w89.py": "runs scripts/jade-approve-sign with a fixed message; no corpus",
    "tests/test_release_guard.py": "runs scripts/check_release_tag.sh in a temporary git repository with a fixed list of tag names as one argument each; the script refuses a bad tag before it runs anything; no corpus",
    "tests/test_secret_scan.py": "runs scripts/scan_new_changes.sh in a temporary git repository with fixed arguments and a fake token that is only scanned; no corpus",
}


@pytest.fixture
def canary(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    f = d / "file"
    f.write_text("canary\n")
    os.chmod(f, 0o644)
    return f


@pytest.fixture
def sb(tmp_path):
    with realshell.Sandbox(deny_read=tmp_path / "secret") as s:
        yield s


def unchanged(f: Path) -> bool:
    return f.exists() and f.read_text() == "canary\n" and (f.stat().st_mode & 0o777) == 0o644


# ---- the sandbox (macOS) ------------------------------------------------------------------------------------------------------------------
@needs_sandbox
@pytest.mark.parametrize("payload", [
    "/bin/chmod 600 {f}",
    "/bin/chmod -R 700 {d}",
    "command -p chmod 600 {f}",
    "env -i /bin/chmod 600 {f}",
    "env -i chmod 600 {f}",
    "/bin/rm -f {f}",
    "/bin/rm -rf {d}",
    "/bin/mv {f} {d}/moved",
    "echo x >> {f}",
    "echo x > {f}",
    ": > {f}",
    "/usr/bin/touch {d}/new",
    "/bin/mkdir {d}/new",
    "/bin/ln -s /tmp {d}/link",
    "/usr/bin/xattr -w k v {f}",
    "/bin/cp /etc/hosts {d}/new",
    "/usr/bin/sudo -n /bin/chmod 600 {f}",
    "find {d} -exec /bin/chmod 600 {{}} \\;",
    "/usr/bin/perl -e 'chmod 0600, \"{f}\"'",
    "/usr/bin/python3 -c 'import os; os.chmod(\"{f}\", 0o600)'",
    "/bin/zsh -fc '/bin/chmod 600 {f}'",
])
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_no_payload_changes_a_file_outside_the_root(sb, canary, payload, shell):
    cmd = payload.format(f=canary, d=canary.parent)
    sb.run(cmd, shell, timeout=10)
    assert unchanged(canary), cmd
    assert sorted(p.name for p in canary.parent.iterdir()) == ["file"], cmd


@needs_sandbox
def test_writes_inside_the_case_folder_work(sb):
    r = sb.run('echo hi > "$HOME/a" && /bin/chmod 600 "$HOME/a" && cat "$HOME/a" && echo t > "$TMPDIR/t" && cat "$TMPDIR/t"', "bash")
    assert r["rc"] == 0 and r["stdout"] == "hi\nt\n", r


@needs_sandbox
def test_stubs_record_the_calls_and_do_not_run_them(sb):
    r = sb.run("git push origin main; chmod -R 700 /; rm -rf ~; sudo -u root git push origin :main", "zsh")
    assert r["calls"] == [["git", "push", "origin", "main"], ["chmod", "-R", "700", "/"], ["rm", "-rf", r["calls"][2][2]],
                          ["sudo", "-u", "root", "git", "push", "origin", ":main"], ["git", "push", "origin", ":main"]]
    assert r["calls"][2][2].endswith("/home")


@needs_sandbox
def test_a_background_child_does_not_outlive_its_case(sb):
    r = sb.run("/bin/sleep 300 & echo $!", "bash", timeout=5)
    pid = int(r["stdout"].strip())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail("a background child survived its case")


@pytest.mark.timing
@needs_sandbox
def test_a_case_that_runs_too_long_is_stopped(sb):
    t = time.monotonic()
    r = sb.run("/bin/sleep 30", "bash", timeout=1)
    assert r["timed_out"] and time.monotonic() - t < 10


@needs_sandbox
def test_the_network_is_closed_even_to_this_machine(sb):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(0.5)
    port = srv.getsockname()[1]
    try:
        r = sb.run(f"/usr/bin/curl -s -m 3 http://127.0.0.1:{port}/", "bash", timeout=10)
        with pytest.raises(socket.timeout):
            srv.accept()
        assert r["rc"] != 0
    finally:
        srv.close()


@needs_sandbox
def test_a_process_outside_the_sandbox_cannot_be_signalled(sb):
    p = subprocess.Popen(["/bin/sleep", "60"])
    try:
        sb.run(f"kill -9 {p.pid}; /bin/kill -9 {p.pid}", "bash")
        assert p.poll() is None
    finally:
        p.kill()
        p.wait()


@needs_sandbox
def test_the_denied_read_folder_cannot_be_read(sb, tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "id_rsa").write_text("PRIVATE\n")
    r = sb.run(f"/bin/cat {secret}/id_rsa; /bin/ls {secret}", "bash")
    assert "PRIVATE" not in r["stdout"] and "id_rsa" not in r["stdout"]


@needs_sandbox
@pytest.mark.parametrize("prog", ["/bin/launchctl version", "/usr/bin/osascript -e 'return 1'", "/usr/bin/open -h", "/usr/bin/crontab -l"])
def test_programs_that_could_act_outside_the_sandbox_do_not_start(sb, prog):
    r = sb.run(prog, "bash")
    assert r["rc"] != 0 and "not permitted" in r["stderr"].lower(), r


@needs_sandbox
def test_a_corpus_runs_in_order_in_each_shell(sb):
    res = sb.run_corpus(["git push origin main", "echo hi"], shells=("bash", "zsh"), jobs=2)
    assert [x["cmd"] for x in res] == ["git push origin main", "echo hi"]
    assert res[0]["results"]["zsh"]["calls"] == [["git", "push", "origin", "main"]]
    assert res[1]["results"]["bash"]["stdout"] == "hi\n"


@needs_sandbox
def test_a_profile_that_lets_writes_through_is_caught_before_anything_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(realshell, "profile_text", lambda: "(version 1)\n(allow default)\n")
    with pytest.raises(realshell.SandboxBroken):
        realshell.Sandbox(root=tmp_path, deny_read=tmp_path / "x")


@needs_sandbox
def test_the_cli_runs_a_corpus(tmp_path):
    corpus = tmp_path / "c.jsonl"
    corpus.write_text('{"cmd": "git push origin main"}\n{"command": "echo ok"}\n')
    out = tmp_path / "out.json"
    r = subprocess.run([sys.executable, str(ROOT / "evals" / "realshell.py"), "run", str(corpus), str(out), "--shells", "bash"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    import json
    res = json.loads(out.read_text())
    assert res[0]["results"]["bash"]["calls"] == [["git", "push", "origin", "main"]] and res[1]["results"]["bash"]["stdout"] == "ok\n"


@needs_sandbox
def test_selftest_passes_here():
    r = subprocess.run([sys.executable, str(ROOT / "evals" / "realshell.py"), "selftest"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "canary check passed" in r.stdout, r.stderr


# ---- refusal, on every platform ----------------------------------------------------------------------------------------------------------
def test_no_backend_means_nothing_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(realshell, "SANDBOX_EXEC", str(tmp_path / "missing"))
    with pytest.raises(realshell.SandboxUnavailable):
        realshell.Sandbox()


def test_the_cli_refuses_without_a_backend_and_writes_no_results(tmp_path):
    corpus = tmp_path / "c.json"
    corpus.write_text('["echo hi"]')
    out = tmp_path / "out.json"
    code = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('r',sys.argv[1]);m=importlib.util.module_from_spec(s);"
            "s.loader.exec_module(m);m.SANDBOX_EXEC='/nonexistent/sandbox-exec';sys.exit(m.main(['run',sys.argv[2],sys.argv[3]]))")
    r = subprocess.run([sys.executable, "-c", code, str(ROOT / "evals" / "realshell.py"), str(corpus), str(out)], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 2 and "Nothing ran" in r.stderr and not out.exists()


def test_the_root_may_not_be_the_home_folder_or_slash(monkeypatch):
    if not HAVE_SANDBOX:
        pytest.skip("NOT VERIFIED HERE: the root check sits behind the backend check")
    for bad in ("/", realshell.real_home()):
        with pytest.raises(realshell.SandboxBroken):
            realshell.Sandbox(root=bad)


def test_corpus_formats(tmp_path):
    (tmp_path / "a.json").write_text('["a", {"cmd": "b"}]')
    (tmp_path / "b.jsonl").write_text('{"command": "c"}\n\n{"cmd": "d"}\n')
    assert realshell.load_corpus(tmp_path / "a.json") == ["a", "b"]
    assert realshell.load_corpus(tmp_path / "b.jsonl") == ["c", "d"]
    (tmp_path / "bad.json").write_text('[{"x": 1}]')
    with pytest.raises(ValueError):
        realshell.load_corpus(tmp_path / "bad.json")


# ---- audit: harnesses that start a shell must use the helper -----------------------------------------------------------------------------
INCIDENT_RUNNER = '''
import os, subprocess
STUB = "stubs"
def run_one(cmd, script, d, log):
    env = {"PATH": STUB + ":/bin:/usr/bin", "JLOG": log}
    argv = ["/bin/bash", script] if True else ["/bin/zsh", "-f", script]
    subprocess.run(argv, cwd=d, env=env, timeout=6)
'''


@pytest.mark.parametrize("src", [
    INCIDENT_RUNNER,
    "import subprocess\nsubprocess.run('echo ' + c, shell=True)\n",
    "import os\nos.system(cmd)\n",
    "from subprocess import Popen\nPopen(['zsh', '-c', c])\n",
    "import subprocess\nsubprocess.run(['sandbox-exec', '-p', '(allow default)', '/bin/sh', 'x.sh'])\n",
])
def test_audit_flags_a_runner_that_starts_a_shell_by_itself(src):
    assert realshell.audit_source(src)


@pytest.mark.parametrize("src", [
    "import subprocess\nsubprocess.run(['git', 'status'])\n",
    "x = ['bash', 'zsh']\n",
    "import realshell\nsb = realshell.Sandbox()\nsb.run('x', 'bash')\n",
    "import subprocess, sys\nsubprocess.run([sys.executable, 'evals/realshell.py', 'run', 'c.json', 'o.json'])\nSH = '/bin/bash'\n",
])
def test_audit_passes_code_that_starts_no_shell_or_uses_the_helper(src):
    assert realshell.audit_source(src) == []


def test_every_shell_runner_in_the_repository_is_known():
    files = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True, cwd=ROOT, check=True).stdout.split()
    found = {os.path.relpath(f, ROOT) for f, _, _ in realshell.audit_paths([ROOT / f for f in files])}
    assert found == set(ALLOWED_SHELL_RUNNERS), ("a file starts a shell without evals/realshell.py; run payloads through it, or add the file "
                                                 "with a reason to ALLOWED_SHELL_RUNNERS if it runs no corpus", sorted(found ^ set(ALLOWED_SHELL_RUNNERS)))


def test_the_audit_cli_exits_non_zero_on_a_bad_harness(tmp_path):
    (tmp_path / "bad.py").write_text(INCIDENT_RUNNER)
    (tmp_path / "good.py").write_text("print(1)\n")
    r = subprocess.run([sys.executable, str(ROOT / "evals" / "realshell.py"), "audit", str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "bad.py" in r.stdout and "good.py" not in r.stdout
    (tmp_path / "bad.py").unlink()
    r = subprocess.run([sys.executable, str(ROOT / "evals" / "realshell.py"), "audit", str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
