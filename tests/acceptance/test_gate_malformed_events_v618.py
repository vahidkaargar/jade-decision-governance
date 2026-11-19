"""V6-18 (d), N26 and N27 (F11): what the hook does with an event that is not the shape the host sends.

N26: a `tool_name` that is not a string, a shell `command` that is a list, a `tool_input` that is not an object, and a hook name JADE does not know. A gated-looking
call must exit 2, and an unknown name must exit non-zero, instead of passing in silence. N27: a hook that runs out of time for a neutral MCP tool; today's result
is pinned and written down in docs/05.

This file is separate from test_gate_failure_modes.py because that module is skipped as a whole on a machine that has the real approver installed (W-93);
nothing here approves a permit. Events go through `hooks.main`, with stdin replaced; nothing is run.
"""
import io
import json
import time

import pytest

from jade import cli, hooks, ops

SESSION = "sess-v618-d"


def run(name, event, monkeypatch, raw=None):
    monkeypatch.setattr("sys.stdin", io.StringIO(raw if raw is not None else json.dumps(event)))
    return hooks.main(name)


def ev(tool, tool_input, **extra):
    return {"tool_name": tool, "tool_input": tool_input, "session_id": SESSION, "cwd": "/nonexistent-v618", **extra}


GATED_LOOKING = [
    ev(["Bash"], {"command": "git push origin main"}),
    ev([], {"command": "git reset --hard HEAD~3"}),
    ev({"name": "Bash"}, {"command": "ls"}),
    ev(7, {"command": "ls"}),
    ev(None, {"command": "ls"}),
    ev(["mcp__x__delete_everything"], {"path": "/"}),
    ev("Bash", {"command": ["git", "push", "origin", "main"]}),
    ev("Bash", {"command": ["ls"]}),
    ev("Bash", {"command": {"cmd": "ls"}}),
    ev("PowerShell", {"command": ["Remove-Item", "-Recurse", "/"]}),
    ev("Monitor", {"command": ["tail", "-f", "x"]}),
    ev("Bash", ["git", "push", "origin", "main"]),
    ev("Bash", "git push origin main"),
    ev("Write", ["/etc/hosts", "x"]),
    ev("Edit", "oops"),
    ev("mcp__x__delete_everything", ["/"]),
    ev("mcp__x__read_thing", "x"),
]


@pytest.mark.parametrize("event", GATED_LOOKING, ids=lambda e: f"{e['tool_name']!r}-{e['tool_input']!r}"[:60])
def test_a_malformed_gated_looking_event_exits_2(event, monkeypatch, capsys):
    assert run("pretool", event, monkeypatch) == 2
    assert "JADE gate" in capsys.readouterr().err


def test_the_verdict_for_a_malformed_event_follows_the_gate_mode(monkeypatch, capsys):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    assert run("pretool", GATED_LOOKING[0], monkeypatch) == 0
    assert "JADE gate" in capsys.readouterr().err   # said, not blocked
    monkeypatch.setenv("JADE_GATE_MODE", "off")
    assert run("pretool", GATED_LOOKING[0], monkeypatch) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("event", [
    ev("Bash", {"command": "ls -la"}),
    ev("Bash", {"command": "git status"}),
    ev("Bash", {}),
    ev("Bash", None),
    ev("Read", {"file_path": "/etc/hosts"}),
    ev("mcp__skills_rules__skills_search", {"query": "x", "limit": [1, 2]}),
    ev("mcp__x__exec_in_pod", {"command": ["ls", "-la"]}),   # an MCP tool may take a list for `command` by its own schema: it is rated by its name, as before
    ev("WebSearch", {"query": ["a"]}),
    {"session_id": SESSION},                                  # no tool at all: not a tool call
    {},
])
def test_well_formed_and_non_tool_events_are_not_blocked_by_the_shape_check(event, monkeypatch):
    got = run("pretool", event, monkeypatch)
    if event.get("tool_name") == "mcp__x__exec_in_pod":
        assert got == 2   # rated high by its name (M-07), not by the shape check
    else:
        assert got == 0


def test_a_malformed_event_does_not_reach_the_other_hooks_as_a_block(monkeypatch):
    for name in ("posttool", "posttool-failure", "session-start"):
        assert run(name, ev(["Bash"], ["x"]), monkeypatch) == 0, name


# ---------------------------------------------------------------- an unknown hook name

@pytest.mark.parametrize("name", ["pre-tool", "PreToolUse", "pretool2", "", "stop2", "sessionstart"])
def test_an_unknown_hook_name_exits_non_zero_and_says_so(name, monkeypatch, capsys):
    assert run(name, ev("Bash", {"command": "git push origin main"}), monkeypatch) == 2
    err = capsys.readouterr().err
    assert "unknown hook" in err and "pretool" in err


def test_an_unknown_hook_name_with_the_gate_off_does_nothing(monkeypatch, capsys):
    monkeypatch.setenv("JADE_GATE_MODE", "off")
    assert run("pre-tool", ev("Bash", {"command": "ls"}), monkeypatch) == 0


def test_every_installed_hook_name_is_known():
    """The names `jade hooks install` writes into the host settings are all handled: the new rule cannot turn the installed wiring into a block."""
    for _matcher, sub, _timeout in ops.HOOKS.values():
        assert sub in hooks.HANDLERS


def test_the_cli_entry_passes_an_unknown_name_through_to_the_same_exit(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert cli.main(["hook", "no-such-hook"]) == 2


# ---------------------------------------------------------------- N27: the hook runs out of time

@pytest.mark.parametrize("tool, tool_input, want", [
    ("mcp__skills_rules__skills_search", {"query": "x"}, 0),           # a neutral MCP tool: the host proceeds anyway, so the hook says so (documented, docs/05 T17)
    ("mcp__context7__query-docs", {"libraryId": "/a/b", "query": "x"}, 0),
    ("mcp__x__delete_everything", {"path": "/"}, 2),                  # a name that says it changes state fails closed
    ("Bash", {"command": "git push origin v1-feat-x"}, 2),            # not clearly read-only
    ("Bash", {"command": "ls -la"}, 0),
])
@pytest.mark.timing
def test_a_hook_that_runs_out_of_time_follows_the_degraded_rule_for_the_tool(tool, tool_input, want, monkeypatch, capsys):
    monkeypatch.setattr(hooks, "BUDGET_S", 0.3)
    monkeypatch.setitem(hooks.HANDLERS, "pretool", lambda e: time.sleep(5))
    t0 = time.monotonic()
    assert run("pretool", ev(tool, tool_input), monkeypatch) == want
    assert time.monotonic() - t0 < 3
    assert "budget" in capsys.readouterr().err
