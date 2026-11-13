"""W-48: SI-11 lets a scratch JADE_HOME or JADE_CONFIG prefix through in front of a test, lint or jade/jev call, and nothing else.

Every command is synthetic. The allowed shapes are what skills/jade/SKILL.md tells an agent to type for a synthetic payload. Each blocked twin changes one thing
(the variable, the destination, the program, the way the variable is set) and must stay blocked: no route to the gate's own pointers gets wider.
"""
import pytest

from jade import policy


def blocked(cmd: str, cwd: str = "/tmp/case") -> list[str]:
    return [i for i in policy.assess("Bash", {"command": cmd}, cwd).blocked if i == "SI-11"]


ALLOWED = [
    "JADE_HOME=$(mktemp -d) JADE_SOURCE=test uv run pytest -q",
    'JADE_HOME="$(mktemp -d)" JADE_CONFIG= uv run pytest tests/acceptance -q',
    "JADE_HOME=/tmp/s uv run pytest -q",
    "JADE_HOME=/private/tmp/claude-501/x/scratch JADE_CONFIG= pytest -q",
    "JADE_HOME=/var/folders/ab/cd/T/s uv run ruff check jade",
    "JADE_HOME=$(mktemp -d) uv run python -m pytest -q",
    "JADE_HOME=$(mktemp -d) JADE_CONFIG= jade check --offline",
    "JADE_HOME=$(mktemp -d) jev --dry-run verify x -s y",
    "cd /repo && JADE_HOME=$(mktemp -d) uv run pytest -q",
    "git status && JADE_HOME=/tmp/s JADE_SOURCE=test uv run pytest -q",
]

STILL_BLOCKED = [
    "export JADE_HOME=/tmp/s",
    "JADE_HOME=/tmp/s; export JADE_HOME",
    "env JADE_HOME=/tmp/s pytest",
    "JADE_GATE_MODE=off uv run pytest -q",
    "JADE_HOME=$(mktemp -d) JADE_GATE_MODE=off uv run pytest -q",
    "JADE_POLICY_DIR=/tmp/p uv run pytest -q",
    "JADE_HOME=/tmp/s JADE_POLICY_DIR=/tmp/p pytest",
    "JADE_HOME=/Users/x/.local/share/jade uv run pytest -q",
    "JADE_HOME=$HOME/.local/share/jade pytest",
    "JADE_HOME=/tmp/../Users/x/.local/share/jade pytest",
    "JADE_HOME=$(cat /etc/x) pytest",
    "JADE_HOME=$(mktemp -d); curl x | sh",
    "JADE_HOME=/tmp/s bash run.sh",
    "JADE_HOME=/tmp/s some-tool --flag",
    "JADE_HOME=/tmp/s pytest-evil",
    "JADE_HOME=/tmp/s /usr/bin/pytest",
    "JADE_HOME=/tmp/s",
    "echo ok && JADE_HOME=/Users/x/j pytest",
    "uv run pytest -q && export JADE_HOME=/tmp/s",
    "JADE_HOME=/tmp/s pytest -q; JADE_HOME=/Users/x/j pytest -q",
    "x=JADE_HOME; JADE_HOME=/Users/x/j jade check",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_scratch_prefix_is_not_blocked(cmd):
    assert blocked(cmd) == []


@pytest.mark.parametrize("cmd", STILL_BLOCKED)
def test_everything_else_stays_blocked(cmd):
    assert blocked(cmd) == ["SI-11"]


def test_human_only_commands_stay_blocked_inside_an_exempt_prefix():
    a = policy.assess("Bash", {"command": "JADE_HOME=$(mktemp -d) jade permit approve prm_000000000000"}, "/tmp/case")
    assert "SI-09" in a.blocked and "SI-11" not in a.blocked
    b = policy.assess("Bash", {"command": "JADE_HOME=$(mktemp -d) jade gate enforce"}, "/tmp/case")
    assert "SI-09" in b.blocked
