"""W-100d: a failed call fires PostToolUseFailure, not PostToolUse (probe cell 10, evals/probes/hook_ask/RESULTS.md).

Before, a call that ran under a grant or a permit and then failed kept its decision EXECUTING until the deadline, and was then held as unknown (SI-07). The new
`posttool-failure` hook closes it as an observed failure. An interrupted call may have run in part, so its decision is left alone (the hold stays).
"""

import json
import re

import pytest

from jade import hooks, ledger, ops

SESSION = "sess-w100d-0001"
CMD = "git reset --hard HEAD~1"


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    from jade import paths

    cfg = paths.config()
    cfg["level"] = "high"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


def call(cmd=CMD):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"}


def failure(cmd=CMD, interrupt=False, **extra):
    ev = {**call(cmd), "hook_event_name": "PostToolUseFailure", "error": "Command failed with exit code 1", "is_interrupt": interrupt, "tool_use_id": "toolu_x"}
    ev.update(extra)
    return ev


def allow_and_run(cmd=CMD):
    first = hooks.pretool(call(cmd))
    token = re.search(r"\[jade:ask_[0-9a-f]{12}\]", first.stderr).group(0)
    q = {"question": f"{token} x", "header": "x", "multiSelect": False, "options": [{"label": "Allow", "description": ""}, {"label": "Deny", "description": ""}]}
    shown = json.loads(hooks.pretool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}, "session_id": SESSION, "cwd": "/tmp",
                                      "permission_mode": "auto"}).stdout)["hookSpecificOutput"]["updatedInput"]["questions"][0]
    hooks.posttool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [shown]}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto",
                    "tool_response": {"questions": [shown], "answers": {shown["question"]: "Allow"}}})
    assert hooks.pretool(call(cmd)).exit_code == 0


def statuses():
    d = ledger.open(timeout=5)
    try:
        return [r["status"] for r in d.con.execute("SELECT status FROM decisions ORDER BY created_at").fetchall()]
    finally:
        d.close()


def test_a_failed_call_closes_its_decision_as_an_observed_failure():
    allow_and_run()
    assert statuses() == ["EXECUTING"]
    r = hooks.posttool_failure(failure())
    assert r.exit_code == 0
    assert "EXECUTING" not in statuses()
    d = ledger.open(timeout=5)
    try:
        out = [json.loads(x["doc_json"]) for x in d.con.execute("SELECT doc_json FROM outcomes").fetchall()]
    finally:
        d.close()
    assert len(out) == 1 and out[0]["success"] is False and out[0]["observation_type"] == "observed"
    assert "PostToolUseFailure" in out[0]["verification_method"]


def test_after_a_failure_the_same_call_is_asked_again_not_held():
    allow_and_run()
    hooks.posttool_failure(failure())
    again = hooks.pretool(call())
    assert again.exit_code == 2 and "SI-07" not in again.stderr and "[jade:ask_" in again.stderr


def test_an_interrupted_call_stays_held():
    allow_and_run()
    hooks.posttool_failure(failure(interrupt=True))
    assert statuses() == ["EXECUTING"]
    held = hooks.pretool(call())
    assert held.exit_code == 2 and "SI-07" in held.stderr


def test_a_failure_of_a_call_with_no_decision_changes_nothing():
    assert hooks.posttool_failure(failure("ls -la")).exit_code == 0
    assert statuses() == []


def test_a_tool_that_is_not_gated_is_ignored():
    assert hooks.posttool_failure({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}, "session_id": SESSION, "error": "no", "is_interrupt": False}).exit_code == 0


def test_a_malformed_event_is_ignored():
    assert hooks.posttool_failure({}).exit_code == 0
    assert hooks.posttool_failure({"tool_name": 5, "tool_input": "x"}).exit_code == 0


# ------------------------------------------------------------------ wiring

def test_the_install_registers_the_failure_event():
    matcher, sub, timeout = ops.HOOKS["PostToolUseFailure"]
    assert sub == "posttool-failure" and matcher == ops.posttool_matcher() and timeout <= 10
    assert ops.parse_hook_command("/usr/local/bin/jade hook posttool-failure").sub == "posttool-failure"
    assert ops.parse_hook_command("/usr/local/bin/jade hook posttool").sub == "posttool"


def test_the_hook_entry_runs_the_handler(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(failure("ls"))))
    assert hooks.main("posttool-failure") == 0


def test_the_probe_event_for_the_failure_hook_is_well_formed():
    ev = ops._probe_event("posttool-failure")
    assert ev["tool_name"] == "Bash" and ev["error"] and ev["is_interrupt"] is False
