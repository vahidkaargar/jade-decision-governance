"""HO-shell-startup: a write to a shell start-up file or to the Claude shell snapshot is human-only (threat T31).

The agent's Bash tool runs `zsh -c` (which loads ~/.zshenv) and Claude Code sources a snapshot before each call, so a write there changes what every later
command runs. HOME and ZDOTDIR point at tmp_path: the real machine's files are never read or written.
"""

import json
import os
import time

import pytest

from jade import hooks, paths, policy

CLASS = "HO-shell-startup"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "userhome"
    (h / ".claude" / "shell-snapshots").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.delenv("BASH_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    return h


def bash(cmd):
    return policy.assess("Bash", {"command": cmd}, os.environ["HOME"])


def write(path, tool="Write"):
    return policy.assess(tool, {"file_path": str(path), "content": "alias git=x", "new_string": "x", "old_string": "y"}, os.environ["HOME"])


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("name", [".zshenv", ".zshrc", ".bashrc", ".profile", ".zprofile", ".bash_profile", ".claude/shell-snapshots/snap.sh"])
def test_a_tool_write_of_a_startup_file_is_human_only(home, tool, name):
    assert CLASS in write(home / name, tool).ho_ids, name


@pytest.mark.parametrize("cmd", [
    "echo 'alias git=x' >> ~/.zshenv", "echo x | tee -a ~/.zshrc", "sed -i '' s/a/b/ ~/.zshrc", "cp x ~/.zshenv",
    "mv x ~/.claude/shell-snapshots/s.sh", "ln -sf x ~/.bashrc", "echo x > $HOME/.profile",
])
def test_a_shell_write_of_a_startup_file_is_human_only(cmd):
    assert CLASS in bash(cmd).ho_ids, cmd


def test_a_verb_that_replaces_the_snapshot_directory_is_human_only():
    assert CLASS in bash("rm -rf ~/.claude/shell-snapshots").ho_ids
    assert CLASS in bash("mv x ~/.claude").ho_ids or CLASS in bash("rm -rf ~/.claude").ho_ids


def test_zdotdir_is_followed(tmp_path, monkeypatch):
    z = tmp_path / "zd"
    z.mkdir()
    monkeypatch.setenv("ZDOTDIR", str(z))
    assert CLASS in write(z / ".zshrc").ho_ids


def test_bash_env_and_env_are_covered(tmp_path, monkeypatch):
    monkeypatch.setenv("BASH_ENV", str(tmp_path / "be.sh"))
    monkeypatch.setenv("ENV", str(tmp_path / "env.sh"))
    assert CLASS in write(tmp_path / "be.sh").ho_ids
    assert CLASS in write(tmp_path / "env.sh").ho_ids


def test_a_file_sourced_by_a_startup_file_is_covered(home):
    (home / ".cargo").mkdir()
    (home / ".zshenv").write_text('. "$HOME/.cargo/env"\nsource ~/x.sh\nsource \'${HOME}/y.sh\'\n')
    assert CLASS in write(home / ".cargo" / "env").ho_ids
    assert CLASS in write(home / "x.sh").ho_ids
    assert CLASS in bash("echo x >> ~/y.sh").ho_ids


def test_a_sourced_path_built_from_another_variable_is_not_covered(home):
    (home / ".zshenv").write_text('source "$FOO/z.sh"\nsource ~/*.sh\n. $(echo q)\n')
    (home / "z.sh").write_text("")
    assert CLASS not in write(home / "z.sh").ho_ids   # documented residual (docs/05 T31)


@pytest.mark.parametrize("cmd", ["cat ~/.zshrc", "grep x ~/.zshenv", "ls ~/.claude/shell-snapshots", "cp ~/.zshrc /tmp/backup"])
def test_a_read_is_not_human_only(cmd):
    assert CLASS not in bash(cmd).ho_ids, cmd


def test_a_neighbour_file_is_not_a_startup_file(home):
    assert CLASS not in write(home / ".zshrc.bak").ho_ids
    assert CLASS not in write(home / "notes.txt").ho_ids


def test_a_fifo_startup_file_does_not_block(home):
    os.mkfifo(home / ".zshenv")
    t = time.monotonic()
    a = bash("echo x > /tmp/not-a-startup-file")
    roots = policy._shell_roots()
    assert time.monotonic() - t < 2
    assert CLASS not in a.ho_ids
    assert str(home / ".zshenv") in roots and not any(r.endswith("env") and "cargo" in r for r in roots)


def test_a_huge_startup_file_is_not_read(home):
    (home / ".zshenv").write_text("source ~/big.sh\n" + "#" * (2 << 20))
    assert CLASS not in write(home / "big.sh").ho_ids


def test_the_decision_at_medium_is_ask(home, monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    cfg = paths.config()
    cfg["level"] = "medium"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))
    ev = {"tool_name": "Write", "tool_input": {"file_path": str(home / ".zshenv"), "content": "alias git=x"}, "session_id": "sess-shellstart-0001", "cwd": str(home),
          "permission_mode": "auto"}
    assert "[jade:ask_" in hooks.pretool(ev).stderr
