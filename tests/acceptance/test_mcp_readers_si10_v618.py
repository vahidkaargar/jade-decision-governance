"""V6-18 (c), N22 (W-73): SI-10 also covers the MCP file readers.

Before, `read_file`, `read_multiple_files` and `start_search` of Desktop Commander were rated low by M-06 (a read-only name), so an agent could read the permit
seal key through them. They are now checked like the Read, Grep and Glob tools: the same name test, the same symlink and hard link test, the same key
directory test. The argument names are those of the real tools: `path`, `paths` (a list), `pattern`, `filePattern`, `searchType`.

Only a stand-in file is used as the key. Calls are classified, never run.
"""
import os
from pathlib import Path

import pytest

from jade import hooks, paths, policy

REPO = Path(__file__).resolve().parents[2]
DC = "mcp__Desktop_Commander__"


@pytest.fixture
def key(tmp_path):
    f = paths.home() / "permit-seal.key"
    f.write_bytes(b"\x01" * 32)
    return f


def assess(tool, tool_input, cwd="/tmp/x"):
    return policy.assess(tool, tool_input, cwd)


def subst(tool_input, key):
    def one(v):
        if isinstance(v, list):
            return [one(x) for x in v]
        return v.replace("KEY", str(key)).replace("HOME", str(key.parent)).replace("REPO", str(REPO)) if isinstance(v, str) else v
    return {k: one(v) for k, v in tool_input.items()}


READS_THE_KEY = [
    (DC + "read_file", {"path": "KEY"}),
    (DC + "read_file", {"path": "KEY", "offset": 0, "length": 5}),
    (DC + "read_file", {"path": "~/.local/share/jade/permit-seal.key"}),
    (DC + "read_file", {"path": "/tmp/backup/permit-seal.key"}),
    (DC + "read_file", {"path": "/x/../y/permit-seal.key"}),
    (DC + "read_multiple_files", {"paths": ["REPO/README.md", "KEY"]}),
    (DC + "read_multiple_files", {"paths": ["KEY"]}),
    (DC + "get_file_info", {"path": "KEY"}),
    (DC + "start_search", {"path": "HOME", "pattern": "x", "searchType": "content"}),
    (DC + "start_search", {"path": "HOME", "pattern": "x", "searchType": "content", "filePattern": "*.key"}),
    (DC + "start_search", {"path": "KEY", "pattern": "x", "searchType": "content"}),
    (DC + "start_search", {"path": "/tmp", "pattern": "permit-seal.key", "searchType": "files"}),
    (DC + "start_search", {"path": "/tmp", "pattern": "permit-s*"}),
    (DC + "start_search", {"path": "HOME", "pattern": "*", "searchType": "files"}),
    (DC + "start_search", {"path": "/tmp", "pattern": "x", "searchType": "content", "filePattern": "permit-seal.key"}),
    ("mcp__other_server__read_text_file", {"file_path": "KEY"}),
    ("mcp__other_server__read_file", {"filePath": "KEY"}),
    ("mcp__other_server__read_files", {"files": ["KEY"]}),
]


@pytest.mark.parametrize("tool, tool_input", READS_THE_KEY)
def test_an_mcp_reader_that_can_reach_the_key_is_blocked(key, tool, tool_input):
    a = assess(tool, subst(tool_input, key))
    assert "SI-10" in a.blocked and a.level == "critical" and a.rule_id == "SI-10", (tool, tool_input)


def test_a_relative_path_after_the_cwd_is_blocked(key):
    assert "SI-10" in assess(DC + "read_file", {"path": "permit-seal.key"}, cwd=str(key.parent)).blocked
    assert "SI-10" in assess(DC + "read_file", {"path": "../home/permit-seal.key"}, cwd=str(key.parent)).blocked


def test_a_symlink_and_a_hard_link_to_the_key_are_blocked(key, tmp_path):
    link = tmp_path / "innocent-notes.txt"
    link.symlink_to(key)
    hard = tmp_path / "hard.txt"
    os.link(key, hard)
    for p in (link, hard):
        assert "SI-10" in assess(DC + "read_file", {"path": str(p)}).blocked, p
        assert "SI-10" in assess(DC + "read_multiple_files", {"paths": [str(p)]}).blocked, p


def test_the_hook_blocks_with_exit_2_and_names_si10(key):
    for tool, ti in ((DC + "read_file", {"path": str(key)}), (DC + "read_multiple_files", {"paths": [str(key)]}),
                     (DC + "start_search", {"path": str(key.parent), "pattern": "x", "searchType": "content"})):
        r = hooks.pretool({"tool_name": tool, "tool_input": ti, "cwd": "/tmp/x", "session_id": "s-v618-c"})
        assert r.exit_code == 2 and "SI-10" in r.stderr, (tool, r)


def test_advisory_mode_reports_without_blocking(key, monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool({"tool_name": DC + "read_file", "tool_input": {"path": str(key)}, "cwd": "/tmp/x", "session_id": "s-v618-c"})
    assert r.exit_code == 0 and "SI-10" in r.stderr


TWINS = [
    (DC + "read_file", {"path": "HOME/jade.sqlite"}),
    (DC + "read_file", {"path": "HOME/pins.json"}),
    (DC + "read_file", {"path": "REPO/README.md"}),
    (DC + "read_multiple_files", {"paths": ["REPO/README.md", "REPO/BACKLOG.md", "HOME/logs/gate.log"]}),
    (DC + "get_file_info", {"path": "REPO/README.md"}),
    (DC + "list_directory", {"path": "HOME"}),                                                   # names only, no content
    (DC + "start_search", {"path": "REPO", "pattern": "permit-seal.key", "searchType": "content"}),   # a search for the name in other files reads no key
    (DC + "start_search", {"path": "REPO/docs", "pattern": "SI-10", "searchType": "content", "filePattern": "*.md"}),
    (DC + "start_search", {"path": "HOME", "pattern": "x", "searchType": "content", "filePattern": "*.log"}),
    (DC + "start_search", {"path": "HOME", "pattern": "x", "searchType": "content", "filePattern": "*.sqlite"}),
    (DC + "start_search", {"path": "REPO", "pattern": "*.py", "searchType": "files"}),
    (DC + "start_search", {"path": "/tmp", "pattern": "*.key", "searchType": "files"}),             # a short glob far from the JADE home is an ordinary argument
    (DC + "start_search", {"path": "REPO", "pattern": "perm*"}),
    (DC + "read_file", {"path": "/tmp/x/perm.txt"}),
    ("mcp__skills_rules__skills_search", {"query": "permit-seal.key"}),
]


@pytest.mark.parametrize("tool, tool_input", TWINS)
def test_harmless_twins_are_not_blocked_and_stay_low(key, tool, tool_input):
    a = assess(tool, subst(tool_input, key), cwd=str(REPO))
    assert "SI-10" not in a.blocked, (tool, tool_input)
    if tool.endswith(("read_file", "read_multiple_files", "get_file_info", "list_directory", "start_search")):
        assert a.level in ("low", "medium"), (tool, a.level)


def test_a_malformed_reader_input_does_not_crash_the_classifier():
    for ti in ({}, {"path": None}, {"path": 7}, {"paths": "x"}, {"paths": [None, 3, ["a"]]}, {"path": "\x00"}, {"pattern": ["a"], "searchType": 5}):
        for tool in (DC + "read_file", DC + "read_multiple_files", DC + "start_search"):
            assess(tool, ti)


def test_an_unloadable_policy_still_blocks_an_mcp_read_of_the_key_and_lets_others_through(key, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("policy broken")

    monkeypatch.setattr(hooks, "_classify", boom)

    def pre(tool, ti):
        return hooks.pretool({"tool_name": tool, "tool_input": ti, "cwd": str(REPO), "session_id": "s-v618-c"})

    assert pre(DC + "read_file", {"path": str(key)}).exit_code == 2
    assert pre(DC + "read_multiple_files", {"paths": [str(REPO / "README.md"), str(key)]}).exit_code == 2
    assert pre(DC + "start_search", {"path": str(key.parent), "pattern": "x", "searchType": "content"}).exit_code == 2
    assert pre(DC + "read_file", {"path": str(REPO / "README.md")}).exit_code == 0
    assert pre(DC + "get_file_info", {"path": str(REPO / "README.md")}).exit_code == 0


# ---- review fix: the key behind more decoys than the scan reads, or in a string longer than a path -----------------------------------------------
@pytest.mark.parametrize("n", [254, 255, 256, 257, 300, 5000])
def test_the_key_after_any_number_of_decoy_paths_is_blocked(key, n):
    a = assess(DC + "read_multiple_files", {"paths": [str(REPO / "README.md")] * n + [str(key)]})
    assert "SI-10" in a.blocked, n


@pytest.mark.parametrize("tool_input", [
    {"paths": [str(Path("/etc/hosts"))] * 257},
    {"path": "/etc/hosts/" + "a" * 5000},
    {"paths": ["/etc/hosts", "/tmp/" + "b" * 5000]},
    {"path": "/tmp", "pattern": "x" * 5000, "searchType": "files"},
    {"path": "/tmp", "pattern": "x", "searchType": "content", "filePattern": ["*.md"] * 300},
])
def test_a_list_or_a_string_over_the_limits_counts_as_reaching_the_key(tool_input):
    assert "SI-10" in assess(DC + "start_search", tool_input).blocked


def test_the_hook_blocks_the_key_behind_257_decoys_and_names_si10(key):
    ti = {"paths": [str(REPO / "README.md")] * 300 + [str(key)]}
    r = hooks.pretool({"tool_name": DC + "read_multiple_files", "tool_input": ti, "cwd": "/tmp/x", "session_id": "s-v618-c"})
    assert r.exit_code == 2 and "SI-10" in r.stderr


def test_a_content_pattern_over_the_string_limit_is_a_regex_and_not_a_target():
    a = assess(DC + "start_search", {"path": str(REPO / "docs"), "pattern": "x" * 5000, "searchType": "content"}, cwd=str(REPO))
    assert "SI-10" not in a.blocked


def test_an_unloadable_policy_still_blocks_a_list_over_the_limit(key, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("policy broken")

    monkeypatch.setattr(hooks, "_classify", boom)
    ti = {"paths": [str(REPO / "README.md")] * 300 + [str(key)]}
    r = hooks.pretool({"tool_name": DC + "read_multiple_files", "tool_input": ti, "cwd": str(REPO), "session_id": "s-v618-c"})
    assert r.exit_code == 2
