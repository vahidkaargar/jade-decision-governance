"""V6-18 (a): SI-11 also lists JADE_SESSION_ID.

The SI-07 hold of an irreversible call is released only by the session that opened it, and the CLI reads that session from JADE_SESSION_ID
(`jade/util.py` `session_id`). An agent that could set the old id in its own command could lift the hold. SI-11 now blocks the assignment like the
other gate variables. Reading the variable, or naming it in text that only holds data, stays allowed.

Every command is synthetic and is only classified, never run.
"""
import pytest

from jade import hooks, policy

SESSION = "sess-v618-a"
OUTCOME = "jade decide outcome dec-0001 --type observed --success --method 'read the release state'"


def blocked(cmd: str, cwd: str = "/nonexistent-v618") -> list[str]:
    return policy.assess("Bash", {"command": cmd}, cwd).blocked


def pre(tool: str, tool_input: dict) -> hooks.HookResult:
    return hooks.pretool({"tool_name": tool, "tool_input": tool_input, "session_id": SESSION, "cwd": "/nonexistent-v618"})


SETTING = [
    f"JADE_SESSION_ID=sess-old {OUTCOME}",
    f"export JADE_SESSION_ID=sess-old && {OUTCOME}",
    f"env JADE_SESSION_ID=sess-old {OUTCOME}",
    f"JADE_SESSION_ID=sess-old; {OUTCOME}",
    f"export 'JADE_SESSION_ID'=sess-old; {OUTCOME}",
    f'JADE_SESSION_""ID=sess-old {OUTCOME}',
    f"JADE_SESSION_${{E}}ID=sess-old {OUTCOME}",
    f"J=JADE_SESSION_ID; env $J=sess-old {OUTCOME}",
    "launchctl setenv JADE_SESSION_ID sess-old",
    "read JADE_SESSION_ID <<< sess-old",
    "echo 'export JADE_SESSION_ID=sess-old' >> ~/.zshrc",
    "JADE_SESSION_ID+=x jade decide list",
]


@pytest.mark.parametrize("cmd", SETTING)
def test_setting_the_session_id_in_a_command_is_blocked_by_si11(cmd):
    assert "SI-11" in blocked(cmd), cmd


@pytest.mark.parametrize("cmd", SETTING[:5])
def test_the_hook_refuses_an_agent_that_sets_an_old_session_id_before_decide_outcome(cmd):
    r = pre("Bash", {"command": cmd})
    assert r.exit_code == 2 and "SI-11" in r.stderr, (cmd, r)


@pytest.mark.parametrize("cmd", [
    "printenv JADE_SESSION_ID",
    "echo $JADE_SESSION_ID",
    'echo "session: ${JADE_SESSION_ID:-none}"',
    "grep -n JADE_SESSION_ID jade/util.py",
    "git log --oneline -3 -- jade/util.py",
    OUTCOME,
    "env | grep JADE_SESSION_ID",
])
def test_reading_the_session_id_and_the_ordinary_outcome_command_are_not_blocked_by_si11(cmd):
    assert "SI-11" not in blocked(cmd), cmd


@pytest.mark.parametrize("tool, tool_input", [
    ("Write", {"file_path": "/Users/x/.zshrc", "content": "export JADE_SESSION_ID=sess-old\n"}),
    ("Write", {"file_path": "/Users/x/proj/.env", "content": "JADE_SESSION_ID=sess-old\n"}),
    ("Write", {"file_path": "/Users/x/Library/LaunchAgents/a.plist", "content": "<key>JADE_SESSION_ID</key><string>x</string>"}),
])
def test_a_profile_or_environment_file_that_sets_it_is_blocked_by_si11(tool, tool_input):
    assert "SI-11" in policy.assess(tool, tool_input, "/nonexistent-v618").blocked


def test_the_same_text_in_an_ordinary_file_is_not_blocked():
    a = policy.assess("Write", {"file_path": "/Users/x/proj/notes.md", "content": "export JADE_SESSION_ID=sess-old\n"}, "/nonexistent-v618")
    assert "SI-11" not in a.blocked


def test_si11_names_the_variable_in_its_description():
    inv = next(i for i in policy.safety()["invariants"] if i["id"] == "SI-11")
    assert "JADE_SESSION_ID" in inv["description"]


def test_advisory_gate_mode_still_reports_and_does_not_block(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = pre("Bash", {"command": SETTING[0]})
    assert r.exit_code == 0 and "SI-11" in r.stderr
