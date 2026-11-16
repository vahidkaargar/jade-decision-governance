"""W-115 / SI-13: the gate blocks a command that runs a Python payload runner which starts real shells without evals/realshell.py (docs/13).

Every test here only classifies command text with `policy.assess`. No runner, corpus or shell from these fixtures is ever executed: the files are
written to a temporary folder so that the gate can read them, which is all SI-13 does (a static read, at most 1 MiB).
"""

import importlib.util
import time
from pathlib import Path

import pytest

from jade import harness_audit, hooks, policy

ROOT = Path(__file__).resolve().parents[2]

# The shape of the 2026-11-15 runner: corpus file from argv, PATH stubs for a few programs, real /bin/bash and /bin/zsh, a pool, a timeout that
# kills only the shell. Never run.
INCIDENT_RUNNER = '''
import json, os, subprocess, sys
from multiprocessing import Pool
STUB = "stubs"
def run_one(cmd):
    d = "case"
    open(os.path.join(d, "cmd.sh"), "w").write(cmd)
    out = []
    for argv in (["/bin/bash", "cmd.sh"], ["/bin/zsh", "-f", "cmd.sh"]):
        out.append(subprocess.run(argv, cwd=d, env={"PATH": STUB + ":/bin:/usr/bin"}, timeout=6).returncode)
    return out
if __name__ == "__main__":
    cmds = json.load(open(sys.argv[1]))
    with Pool(8) as p:
        res = p.map(run_one, cmds)
    json.dump(res, open(sys.argv[2], "w"))
'''

# A corpus kept as Python source and run in a loop (review round 4 shape). Never run.
EXEC_RUNNER = '''
import os, subprocess
exec(open("cmds.py").read())
for c in cmds:
    for sh in ("/bin/bash", "/bin/zsh"):
        subprocess.run(["perl", "-e", "alarm 10; exec @ARGV", sh, "s.sh"], timeout=15)
'''

HELPER_RUNNER = '''
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("realshell", "evals/realshell.py")
realshell = importlib.util.module_from_spec(spec); spec.loader.exec_module(realshell)
SH = "/bin/bash"
cmds = json.load(open(sys.argv[1]))
with realshell.Sandbox() as sb:
    print(sb.run_corpus(cmds))
'''

# Ordinary scripts that start a shell: a build step, and a test that runs one script and parses its output.
BUILD_SCRIPT = '''
import subprocess, sys
subprocess.run("make all && make install", shell=True, check=True)
'''
CI_CHECK = '''
import json, subprocess
def test_runner():
    for name in ("a", "b"):
        out = subprocess.run(["bash", "tools/x.sh", name], capture_output=True, text=True).stdout
        assert json.loads(out)["ok"]
'''
SANDBOX_ALLOW_DEFAULT = '''
import json, subprocess, sys
cmds = json.load(open(sys.argv[1]))
for c in cmds:
    subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", "/bin/bash", "-c", c])
'''


@pytest.fixture
def harness(tmp_path):
    (tmp_path / "run.py").write_text(INCIDENT_RUNNER)
    (tmp_path / "loop.py").write_text(EXEC_RUNNER)
    (tmp_path / "safe.py").write_text(HELPER_RUNNER)
    (tmp_path / "build.py").write_text(BUILD_SCRIPT)
    (tmp_path / "check.py").write_text(CI_CHECK)
    (tmp_path / "echo.py").write_text(SANDBOX_ALLOW_DEFAULT)
    (tmp_path / "batch.sh").write_text("for k in odd mix A; do python3 run.py corp$k.json real-$k.json; done\n")
    (tmp_path / "plain.sh").write_text("python3 build.py\n")
    (tmp_path / "corpA.json").write_text('["echo x"]')
    return tmp_path


def blocked(cmd, cwd):
    return "SI-13" in policy.assess("Bash", {"command": cmd}, str(cwd)).blocked


@pytest.mark.parametrize("cmd", [
    "python3 run.py corpA.json real-A.json",
    "python3 -u run.py corpA.json real-A.json",
    "nohup python3 run.py corpA.json real-A.json > log 2>&1 &",
    "uv run python run.py corpA.json out.json",
    "cd {d} && python3 run.py corpA.json real-A.json",
    "python3 {d}/run.py {d}/corpA.json /tmp/out.json",
    "echo start; python3 run.py corpA.json real-A.json",
    "python3 loop.py",
    "python3 echo.py corpA.json",
    "bash batch.sh",
    "sh ./batch.sh",
])
def test_si13_blocks_a_payload_runner_without_the_sandbox(harness, cmd):
    assert blocked(cmd.format(d=harness), harness)


@pytest.mark.parametrize("cmd", [
    "python3 safe.py corpA.json",
    "python3 build.py",
    "python3 build.py config.json",
    "python3 check.py",
    "python3 run.py",            # no corpus on the command line and none named in the script: not the payload-runner shape
    "bash plain.sh",
    "cat run.py",
    "python3 -m pytest check.py",
    "python3 missing.py corpA.json",
    "python3 evals/realshell.py run corpA.json out.json",
])
def test_si13_leaves_other_scripts_alone(harness, cmd):
    assert not blocked(cmd, harness)


def test_si13_does_not_block_the_repository_helper(harness):
    assert not blocked(f"python3 {ROOT / 'evals' / 'realshell.py'} run corpA.json out.json", harness)
    assert not blocked(f"python3 {ROOT / 'evals' / 'realshell.py'} audit .", harness)


def test_si13_is_a_safety_invariant_that_no_permit_authorizes(harness):
    a = policy.assess("Bash", {"command": "python3 run.py corpA.json real-A.json"}, str(harness))
    assert a.blocked == ["SI-13"] or "SI-13" in a.blocked
    assert "SI-13" not in hooks.GOVERNANCE_INVARIANTS   # advisory at level off and in advisory gate mode, like SI-01 to SI-08
    ids = [i["id"] for i in policy.safety()["invariants"]]
    assert "SI-13" in ids and ids.count("SI-13") == 1


def test_the_gate_and_the_audit_command_share_one_implementation():
    spec = importlib.util.spec_from_file_location("realshell_for_test", ROOT / "evals" / "realshell.py")
    realshell = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(realshell)
    for src in (INCIDENT_RUNNER, EXEC_RUNNER, HELPER_RUNNER, BUILD_SCRIPT, CI_CHECK, SANDBOX_ALLOW_DEFAULT):
        assert realshell.audit_source(src) == harness_audit.audit_source(src)
        assert realshell.gate_findings(src, True) == harness_audit.gate_findings(src, True)


@pytest.mark.parametrize("src", ["def f(:\n", "x = " + "(" * 5000 + ")" * 5000 + "\n", "\x00\x00", ""])
def test_a_file_the_check_cannot_parse_is_not_blocked(src):
    assert harness_audit.gate_findings(src, True) == []


def test_a_file_over_the_read_limit_is_not_read(tmp_path):
    big = tmp_path / "big.py"
    big.write_text(INCIDENT_RUNNER + "#" * (harness_audit.READ_MAX + 1))
    assert harness_audit.gate_file(str(big), data_arg=True) == []


@pytest.mark.timing
def test_the_check_is_cheap_on_a_large_script(tmp_path):
    src = INCIDENT_RUNNER + "\n".join(f"def f{i}(x):\n    return [y for y in x if y]\n" for i in range(4000))
    assert len(src) < harness_audit.READ_MAX
    (tmp_path / "big.py").write_text(src)
    t = time.perf_counter()
    assert harness_audit.gate_file(str(tmp_path / "big.py"), data_arg=True)
    assert time.perf_counter() - t < 1.0
