"""REL-1: `jade upgrade` and `jade setup` change what runs as the gate, so SI-09 treats them like `jade hooks install`: blocked for an agent in every
spelling of the invocation, and not blocked for the harmless commands that only sit near the words."""

import pytest

from jade import hooks, policy


def si09(cmd: str) -> bool:
    return "SI-09" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


ATTACK = [
    "jade upgrade", "jade upgrade --ref v9.9.9", "jade  upgrade", "jade upgrade --ref 0123456789abcdef0123456789abcdef01234567",
    "jade setup", "jade setup --yes", "jade  setup",
    "~/.local/bin/jade upgrade", "/Users/x/.local/bin/jade setup", "uv run jade upgrade", "uvx jade upgrade", "env -i jade upgrade", "JADE_X=1 jade setup",
    "sudo jade upgrade", "nohup jade upgrade &", "time jade setup", "cd /x && jade upgrade", "echo y | jade upgrade", "(jade upgrade)", "{ jade setup; }",
    "sh -c 'jade upgrade'", "bash -c \"jade setup\"", "script -q /dev/null jade upgrade", "xargs jade upgrade < refs",
    "jade 'upgrade'", "jade \"up\"\"grade\"", "jade up\\grade", "jade ${X:-upgrade}", "jade ${X:-setup} --yes", "jade $(echo upgrade)",
    "$JADE upgrade", "$J setup", "${J} upgrade --ref v9.9.9",
    "python -m jade upgrade", "python -m jade.cli upgrade", "python3 -m jade.cli setup", "python jade/cli.py upgrade", "python jade/cli.py setup",
    "python3 -c \"from jade import upgrade; upgrade.apply('v9.9.9')\"", "python3 -c \"import jade.upgrade as u\"", "python3 -c \"from jade import cli; cli.main(['upgrade'])\"",
    "alias up='jade upgrade'", "alias up=\"jade setup\"", "env -S 'jade upgrade'", "f() { jade upgrade; }",
    "jade upgrade --check --ref v9.9.9; jade upgrade --ref v9.9.9",
    # `--` ends the options and argparse still reads the next word as the subcommand
    "jade -- upgrade", "jade -- setup --yes", "jade --  upgrade", "uv run jade -- upgrade --ref v9.9.9", "python -m jade.cli -- upgrade", "python jade/cli.py -- setup",
    # a read-only flag that is the target of a redirection is not an argument of jade: the command runs in full
    "jade upgrade 2> --check", "jade upgrade --ref v1.0.0 2> --check", "jade setup --yes 2> --status", "jade setup 2> -h", "jade upgrade --ref v1.0.0 >> -h",
    "jade upgrade 2>  --check", "jade upgrade < --help", "python -m jade.cli upgrade 2> --check", "python jade/cli.py setup 2> --status",
]


@pytest.mark.parametrize("cmd", ATTACK)
def test_si09_blocks_every_spelling_of_upgrade_and_setup(cmd):
    assert si09(cmd), cmd


HARMLESS = [
    "jade upgrade --help", "jade upgrade -h", "jade setup --help", "jade setup -h", "uv run jade upgrade --help",
    "jade upgrade --check", "uv run jade upgrade --check", "~/.local/bin/jade upgrade --check", "python -m jade.cli upgrade --check",
    "jade version", "jade --version", "jade version --json", "jade check", "jade hooks status", "jade ledger stats",
    "git commit -m 'jade upgrade notes'", "git log --grep 'jade upgrade'", "rg 'jade setup' docs", "echo 'run jade upgrade later' > /tmp/notes",
    "ls jade setup.py", "cd jade && cat setup.py", "cat jade/upgrade_notes.md", "grep -rn upgrade docs", "pip install --upgrade pip", "brew upgrade", "uv self update",
    "docker exec jade-svc upgrade", "jade-svc upgrade", "uv tool upgrade ruff", "npm run setup", "./setup.sh", "make setup", "pytest tests/test_upgrade.py -k upgrade",
    "jade upgrade-notes", "echo $HOME upgrade", "kubectl rollout status deploy/jade",
    # here the flag really is an argument of jade
    "jade upgrade --ref v1.0.0 2>&1 --help", "jade setup --yes 2>/dev/null -h", "jade upgrade 2>/dev/null --check", "jade setup 2> /dev/null --status",
]


@pytest.mark.parametrize("cmd", HARMLESS)
def test_si09_does_not_block_the_harmless_twins(cmd):
    assert not si09(cmd), cmd


def test_the_help_and_check_exemptions_are_scoped_to_one_segment():
    assert si09("jade upgrade --help; jade upgrade --ref v9.9.9")
    assert si09("jade upgrade --check && jade upgrade")
    assert si09("jade setup --help; jade setup")
    assert si09("jade upgrade --ref v9.9.9 && jade upgrade --check")


def test_setup_has_no_check_exemption():
    assert si09("jade setup --check")


def test_the_pretool_hook_blocks_with_si09(monkeypatch):
    for cmd in ("jade upgrade --ref v9.9.9", "jade setup"):
        r = hooks.pretool({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "/tmp/x", "session_id": "s1", "hook_event_name": "PreToolUse"})
        assert r.exit_code == 2 and "SI-09" in r.stderr, cmd
