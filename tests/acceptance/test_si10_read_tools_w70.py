"""W-70: SI-10 also gates the Read, Grep and Glob tools when they target the permit seal key (or a glob of its name), and the installed
PreToolUse matcher must cover them. Reads of any other JADE home file, and of the repository's own files, are not gated."""
import json
import os
import re
from pathlib import Path

import pytest

from jade import hooks, ops, paths, policy

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def key(tmp_path):
    """The seal key of the isolated JADE home (a stand-in file, never a real key)."""
    f = paths.home() / "permit-seal.key"
    f.write_bytes(b"\x01" * 32)
    return f


def blocked(tool, tool_input, cwd="/tmp/x"):
    return policy.assess(tool, tool_input, cwd).blocked


# ---------------------------------------------------------------- the key itself and every way to name it
def test_read_of_the_key_by_absolute_path_is_blocked(key):
    a = policy.assess("Read", {"file_path": str(key)}, "/tmp/x")
    assert "SI-10" in a.blocked and a.level == "critical"


@pytest.mark.parametrize("path", ["~/.local/share/jade/permit-seal.key", "/tmp/backup/permit-seal.key", "/x/../y/permit-seal.key", "/tmp/permit-seal.key.bak"])
def test_read_of_a_path_that_names_the_key_file_is_blocked(path):
    assert "SI-10" in blocked("Read", {"file_path": path})


def test_read_with_a_relative_path_after_the_cwd_is_blocked(key):
    assert "SI-10" in blocked("Read", {"file_path": "permit-seal.key"}, cwd=str(key.parent))
    assert "SI-10" in blocked("Read", {"file_path": "../home/permit-seal.key"}, cwd=str(key.parent))


def test_read_of_a_symlink_to_the_key_is_blocked(key, tmp_path):
    link = tmp_path / "innocent-notes.txt"
    link.symlink_to(key)
    assert "SI-10" in blocked("Read", {"file_path": str(link)})
    nested = tmp_path / "d"
    nested.symlink_to(key.parent)   # a symlinked directory
    assert "SI-10" in blocked("Read", {"file_path": str(nested / "permit-seal.key")})
    assert "SI-10" in blocked("Read", {"file_path": str(nested / "perm") + "it-seal.key"})


def test_read_of_a_hard_link_to_the_key_is_blocked(key, tmp_path):
    other = tmp_path / "hard.txt"
    os.link(key, other)
    assert "SI-10" in blocked("Read", {"file_path": str(other)})


def test_a_key_that_does_not_exist_yet_is_still_protected_by_name(tmp_path):
    assert not (paths.home() / "permit-seal.key").exists()
    assert "SI-10" in blocked("Read", {"file_path": str(paths.home() / "permit-seal.key")})


def test_the_key_location_follows_jade_home(monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("JADE_HOME", str(other))
    (other / "permit-seal.key").write_bytes(b"\x02" * 32)
    (other / "state.bin").symlink_to(other / "permit-seal.key")
    assert "SI-10" in blocked("Read", {"file_path": str(other / "state.bin")})


@pytest.mark.parametrize("tool_input", [
    {"pattern": "x", "path": "KEY"},                                   # the key file itself
    {"pattern": "x", "path": "HOME"},                                  # the directory that holds it, nothing filtered out
    {"pattern": "x", "path": "HOME", "glob": "*"},
    {"pattern": "x", "path": "HOME", "glob": "*.key"},
    {"pattern": "x", "path": "HOME", "glob": "p*"},
    {"pattern": "x", "glob": "permit-s*"},                             # six or more literal characters name the file in any directory
    {"pattern": "x", "glob": "**/*seal.key"},
    {"pattern": "x", "path": "/tmp", "glob": "permit-seal.key"},
    {"pattern": "x", "path": "~/.local/share/jade"},
])
def test_grep_that_can_reach_the_key_is_blocked(key, tool_input):
    ti = {k: str(key) if v == "KEY" else str(key.parent) if v == "HOME" else v for k, v in tool_input.items()}
    assert "SI-10" in blocked("Grep", ti, cwd="/tmp/x")


@pytest.mark.parametrize("tool_input", [
    {"pattern": "permit-seal.key"},                                    # a search for the name in other files
    {"pattern": "permit-seal.key", "path": "REPO/docs"},
    {"pattern": "seal", "path": "REPO/jade", "glob": "*.py"},
    {"pattern": ".", "path": "HOME", "glob": "*.log"},
    {"pattern": ".", "path": "HOME", "glob": "*.sqlite"},
    {"pattern": ".", "path": "HOME/jade.sqlite"},
    {"pattern": ".", "path": "/tmp"},
    {"pattern": ".", "path": "/tmp", "glob": "*.key"},                 # a short glob far from the JADE home is an ordinary argument
    {"pattern": ".", "path": "REPO", "glob": "perm*"},
])
def test_grep_false_positive_twins_are_not_blocked(key, tool_input):
    ti = {k: v.replace("REPO", str(REPO)).replace("HOME", str(key.parent)) if isinstance(v, str) else v for k, v in tool_input.items()}
    assert "SI-10" not in blocked("Grep", ti)


@pytest.mark.parametrize("tool_input", [
    {"pattern": "permit-seal.key"},
    {"pattern": "**/permit-seal.key"},
    {"pattern": "permit-s*"},
    {"pattern": "*seal.key", "path": "/tmp"},
    {"pattern": "permit-seal.k?y"},
    {"pattern": "HOME/*"},
    {"pattern": "HOME/*.key"},
    {"pattern": "*", "path": "HOME"},
    {"pattern": "*.key", "path": "HOME"},
    {"pattern": "p*", "path": "HOME"},
    {"pattern": "**/*", "path": "HOME"},
    {"pattern": "p*", "path": "~/.local/share/jade"},
])
def test_glob_that_would_match_the_key_is_blocked(key, tool_input):
    ti = {k: v.replace("HOME", str(key.parent)) for k, v in tool_input.items()}
    assert "SI-10" in blocked("Glob", ti)


def test_glob_without_a_path_uses_the_cwd(key):
    assert "SI-10" in blocked("Glob", {"pattern": "p*"}, cwd=str(key.parent))
    assert "SI-10" not in blocked("Glob", {"pattern": "p*"}, cwd=str(REPO))


@pytest.mark.parametrize("tool_input", [
    {"pattern": "**/*.py"},
    {"pattern": "*.key", "path": "/tmp/x"},
    {"pattern": "perm*", "path": "REPO"},
    {"pattern": "*", "path": "REPO"},
    {"pattern": "docs/*.md", "path": "REPO"},
    {"pattern": "*.sqlite", "path": "HOME"},
    {"pattern": "*.log", "path": "HOME"},
    {"pattern": "jade.sqlite*", "path": "HOME"},
    {"pattern": "**/*.md", "path": "REPO"},
    {"pattern": "**/*.key"},          # no path: the checkout, not the JADE home
])
def test_glob_false_positive_twins_are_not_blocked(key, tool_input):
    ti = {k: v.replace("REPO", str(REPO)).replace("HOME", str(key.parent)) for k, v in tool_input.items()}
    assert "SI-10" not in blocked("Glob", ti, cwd=str(REPO))


@pytest.mark.parametrize("rel", ["docs/05-threat-model.md", "jade/permits.py", "README.md", "BACKLOG.md", "policies/safety.yaml"])
def test_reading_the_repos_own_files_is_not_gated(rel):
    a = policy.assess("Read", {"file_path": str(REPO / rel)}, str(REPO))
    assert not a.blocked and a.level == "low"


@pytest.mark.parametrize("name", ["jade.sqlite", "jade.sqlite-wal", "pins.json", "logs/gate.log", "calibration/current.json"])
def test_reading_other_jade_home_files_is_not_gated(key, name):
    assert not blocked("Read", {"file_path": str(key.parent / name)})
    assert not blocked("Read", {"file_path": f"~/.local/share/jade/{name}"})


# ---------------------------------------------------------------- the hook
def pre(tool, tool_input, cwd="/tmp/x"):
    return hooks.pretool({"tool_name": tool, "tool_input": tool_input, "cwd": cwd, "session_id": "s-read-1"})


def test_the_hook_blocks_read_grep_and_glob_of_the_key_with_exit_2(key):
    for tool, ti in (("Read", {"file_path": str(key)}), ("Grep", {"pattern": "x", "path": str(key.parent)}), ("Glob", {"pattern": "permit-s*"})):
        r = pre(tool, ti)
        assert r.exit_code == 2 and "SI-10" in r.stderr, (tool, r)


def test_the_hook_lets_ordinary_reads_through_in_silence(key):
    for tool, ti in (("Read", {"file_path": str(REPO / "docs" / "05-threat-model.md")}), ("Grep", {"pattern": "SI-10", "path": str(REPO / "docs")}),
                     ("Glob", {"pattern": "docs/*.md", "path": str(REPO)}), ("Read", {"file_path": str(key.parent / "jade.sqlite")})):
        r = pre(tool, ti, cwd=str(REPO))
        assert r.exit_code == 0 and not r.stderr, (tool, r)


def test_advisory_mode_reports_without_blocking(key, monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = pre("Read", {"file_path": str(key)})
    assert r.exit_code == 0 and "SI-10" in r.stderr


def test_an_unloadable_policy_still_blocks_a_read_of_the_key_and_lets_others_through(key, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("policy broken")

    monkeypatch.setattr(hooks, "_classify", boom)
    assert pre("Read", {"file_path": str(key)}).exit_code == 2
    assert pre("Glob", {"pattern": "permit-s*"}).exit_code == 2
    assert pre("Read", {"file_path": str(REPO / "README.md")}, cwd=str(REPO)).exit_code == 0
    assert pre("Grep", {"pattern": "x", "path": str(REPO)}, cwd=str(REPO)).exit_code == 0


def test_a_malformed_read_input_does_not_crash_the_hook():
    for ti in ({}, {"file_path": None}, {"file_path": 7}, {"pattern": ["a"]}, {"path": "\x00"}):
        for tool in ("Read", "Grep", "Glob"):
            assert pre(tool, ti).exit_code in (0, 2)


# ---------------------------------------------------------------- the matcher and `jade check`
@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob"])
def test_the_matcher_covers_the_read_tools(tool):
    assert tool in policy.GATED_BUILTIN
    assert re.fullmatch(ops.HOOKS["PreToolUse"][0], tool)
    assert policy.is_gated(tool)


def test_the_matcher_does_not_match_lookalike_names():
    assert not re.fullmatch(ops.HOOKS["PreToolUse"][0], "ReadSomething")
    assert not re.fullmatch(ops.HOOKS["PreToolUse"][0], "NotebookRead")


OLD_MATCHER = "Bash|PowerShell|Monitor|Write|Edit|MultiEdit|NotebookEdit|RemoteTrigger|mcp__.*"


def _install_with_matcher(matcher):
    ops.hooks_install(stdin_isatty=True)
    p = ops.settings_path()
    data = json.loads(p.read_text())
    for g in data["hooks"]["PreToolUse"]:
        g["matcher"] = matcher
    p.write_text(json.dumps(data))


def test_check_reports_an_installed_matcher_that_misses_the_read_tools(human_env, jade_stub):
    _install_with_matcher(OLD_MATCHER)
    assert ops.pretool_matcher_gaps() == ["Read", "Grep", "Glob"]
    rows = {n: (ok, d) for n, ok, d in ops.check(live=False)}
    ok, detail = rows["pretool matcher"]
    assert not ok
    assert "Read, Grep, Glob" in detail and "jade hooks install" in detail and "terminal" in detail
    assert ops.print_check(ops.check(live=False)) == 1


def test_check_passes_a_fresh_install(human_env, jade_stub):
    ops.hooks_install(stdin_isatty=True)
    assert ops.pretool_matcher_gaps() == []
    rows = {n: (ok, d) for n, ok, d in ops.check(live=False)}
    assert rows["pretool matcher"][0]


def test_documentation_records_the_decision_and_the_manual_step():
    adrs = (REPO / "docs" / "01-adrs.md").read_text()
    assert "W-70" in adrs and "Read|Grep|Glob" in adrs and "settings.json" in adrs and "jade hooks install" in adrs
