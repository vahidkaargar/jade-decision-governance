"""W-97 (docs/12 plan v3, PR-1): the host app's own bookkeeping tools are low risk.

`mcp__ccd_turn__update_plan` (the task list the app shows), `mcp__ccd_turn__wrap_up` (the turn recap), `mcp__ccd_session__mark_chapter`
and `mcp__ccd_session__dismiss_task` change what the app shows, not what runs. Before M-06b, `update_plan` matched the generic
verb-in-name rule M-07 (high) because its name contains `update`, so every task-list update needed a terminal permit. The tools next
to them that start or steer another session, change settings or merge stay gated. Their text arguments are what the app displays, so
the content scan reads them as data (like M-06 and M-06a). The degraded failsafe (policy not loadable) agrees with the policy rule, and
the two regular expressions are kept equal.
"""

import re
from pathlib import Path

import pytest

from jade import hooks, policy
from jade.util import LEVELS

SESSION = "sess-w97-0001"
ROOT = Path(__file__).resolve().parents[2]
HARNESS = ["mcp__ccd_turn__update_plan", "mcp__ccd_turn__wrap_up", "mcp__ccd_session__mark_chapter", "mcp__ccd_session__dismiss_task"]


def call(tool, tool_input):
    return {"tool_name": tool, "tool_input": tool_input, "session_id": SESSION, "cwd": str(ROOT)}


@pytest.mark.parametrize("tool", HARNESS)
def test_harness_bookkeeping_tools_are_low_m06b(tool):
    a = policy.assess(tool, {"items": [{"content": "Run the tests", "status": "pending"}]}, str(ROOT))
    assert a.level == "low" and a.rule_id == "M-06b", a
    assert hooks.pretool(call(tool, {"items": []})).exit_code == 0


@pytest.mark.parametrize("text", ["Merge PR 23 with gh pr merge 23 --merge", "git push --force origin main", "rm -rf build and retry"])
def test_task_text_naming_a_command_is_data(text):
    a = policy.assess("mcp__ccd_turn__update_plan", {"items": [{"content": text, "status": "pending"}]}, str(ROOT))
    assert a.level == "low" and a.rule_id == "M-06b", a
    a = policy.assess("mcp__ccd_turn__wrap_up", {"recap": text}, str(ROOT))
    assert a.level == "low" and a.rule_id == "M-06b", a


@pytest.mark.parametrize("tool", HARNESS)
def test_failsafe_does_not_block_harness_bookkeeping(tool):
    assert hooks.failsafe_reason(call(tool, {"advance": True})) is None


@pytest.mark.parametrize("tool", [
    "mcp__ccd_session__start_session", "mcp__ccd_session_mgmt__send_message", "mcp__ccd_session__hand_off_to_session",
    "mcp__ccd_settings__set_setting", "mcp__ccd_session_mgmt__set_session_permission_mode", "mcp__ccd_pr__set_auto_merge",
])
def test_session_steering_and_settings_tools_stay_gated(tool):
    assert LEVELS[policy.assess(tool, {"x": 1}, str(ROOT)).level] >= LEVELS["high"], tool


@pytest.mark.parametrize("tool", [
    "mcp__ccd_turn__update_plan_and_run", "mcp__x_ccd_turn__update_plan", "mcp__ccd_turn__update_planx", "mcp__evil__update_plan",
])
def test_lookalike_names_are_not_harness_tools(tool):
    assert policy.assess(tool, {"x": 1}, str(ROOT)).rule_id != "M-06b"
    assert not hooks._HARNESS_TOOL.search(tool)


def test_harness_regex_matches_policy_rule():
    rule = next(r for r in policy.risk()["mcp_tools"] if r["id"] == "M-06b")
    assert re.compile(rule["match"]).pattern == hooks._HARNESS_TOOL.pattern
