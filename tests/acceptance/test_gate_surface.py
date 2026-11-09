"""Cluster A2: what the gate can see. Runtime artifacts as protected targets, matcher and MCP surface, SI-02/SI-05 on content,
the host permission mode, subagent calls, and SI-06/SI-07 at the hook.

Commands are only classified, never run. Secrets are assembled from parts.
"""

import json
import re
from pathlib import Path

import pytest

from jade import decide, hooks, ledger, ops, permits, policy
from jade.util import LEVELS

SESSION = "sess-a2-0002"
ROOT = Path(__file__).resolve().parents[2]


def sec(*parts):
    return "".join(parts)


def bash(cmd, **extra):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": "/nonexistent-a2", **extra}


def call(tool, tool_input, **extra):
    return {"tool_name": tool, "tool_input": tool_input, "session_id": SESSION, "cwd": "/nonexistent-a2", **extra}


def level(cmd):
    return policy.assess("Bash", {"command": cmd}, "/nonexistent-a2")


# ---------------------------------------------------------------- G00: JADE's own runtime artifacts are protected targets

WRITE_TARGETS = [
    "~/.local/share/jade/jade.sqlite", "~/.local/share/jade/jade.sqlite-wal", "~/.local/share/jade/jade.sqlite-shm", "~/.local/share/jade/jade.sqlite-journal",
    "~/.local/share/jade/calibration/current.json", "~/.local/share/jade/pins.json", "~/.local/share/jade", "~/.config/jade", "~/.config/jade/config.json",
    "~/.config/jade/local/risk.local.yaml", "~/.config/jade/local/safety.local.yaml",
    "~/.local/share/uv/tools/jade-decision-governance/lib/python3.13/site-packages/jade/hooks.py",
    "~/.local/share/uv/tools/jade-decision-governance/pyvenv.cfg", "~/.local/bin/jade", "~/.local/bin/jev",
    "~/.claude/settings.json", "~/.claude/settings.local.json", "/Users/x/proj/.claude/settings.json", "/Users/x/proj/.claude/settings.local.json",
    "/work/jade/jade/hooks.py", "/work/jade/jade/ops.py", "/work/jade/jade/paths.py",
    "/work/jade/policies/risk.yaml", "/work/jade/policies/routing.yaml", "/work/jade/policies/escalation.yaml", "/work/jade/policies/safety.yaml",
    "/work/jade/policies/promotion.yaml", "/work/jade/policies/loader.py", "/work/jade/policies/local/risk.local.yaml",
    "~/Library/LaunchAgents/com.mobogift.jade.maintain.plist",
]


@pytest.mark.parametrize("target", WRITE_TARGETS)
def test_write_tool_on_a_runtime_artifact_needs_a_permit(target):
    for tool, key in (("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
        a = policy.assess(tool, {key: target, "content": "x", "new_string": "x"}, "/nonexistent-a2")
        assert LEVELS[a.level] >= LEVELS["high"], (tool, target, a)
        assert hooks.pretool(call(tool, {key: target, "content": "x"})).exit_code == 2


BASH_WRITES = [
    "echo x > ~/.local/share/jade/jade.sqlite-wal", "cp /tmp/x ~/.local/share/jade/calibration/current.json", "mv ~/.local/share/jade ~/gone",
    "rm -r ~/.local/share/jade", "rm ~/.local/share/jade/jade.sqlite-shm", "rm -rf ~/.config/jade", "rm ~/.local/bin/jade", "ln -sf /tmp/x ~/.local/bin/jade",
    "uv tool uninstall jade-decision-governance", "uv tool install --force /Users/x/projects/jade", "uv tool upgrade jade-decision-governance",
    "launchctl bootout gui/501/com.mobogift.jade.maintain", "launchctl unload ~/Library/LaunchAgents/com.mobogift.jade.maintain.plist",
    "sed -i '' s/enforce/off/ ~/.config/jade/config.json", "printf x >> ~/.local/share/uv/tools/jade-decision-governance/lib/python3.13/site-packages/jade/policy.py",
    "cd ~/.local/share/jade && sqlite3 jade.sqlite \"UPDATE permits SET state='ISSUED'\"", "sqlite3 /tmp/other/jade.sqlite 'delete from permits'",
    "cd ~/.config/jade && cp /tmp/x config.json", "cd ~/.claude && cp /tmp/x settings.json", "cd ~/.local/share/jade && rm pins.json",
    "cd ~/.local/share/jade && echo '{}' > calibration/current.json", "cp /tmp/x ~/.config/jade/local/risk.local.yaml",
    "tee ~/.local/share/jade/pins.json < /tmp/x", "install -m 644 /tmp/x ~/.local/share/jade/pins.json", "chmod 777 ~/.local/share/jade/jade.sqlite",
    "truncate -s 0 ~/.local/share/jade/jade.sqlite", "python3 -c \"open('/Users/x/.local/share/jade/pins.json','w').write('{}')\"",
]


@pytest.mark.parametrize("cmd", BASH_WRITES)
def test_bash_write_to_a_runtime_artifact_needs_a_permit(cmd):
    a = level(cmd)
    assert LEVELS[a.level] >= LEVELS["high"], a
    assert hooks.pretool(bash(cmd)).exit_code == 2


@pytest.mark.parametrize("cmd", [
    "cat ~/.local/share/jade/pins.json", "ls -la ~/.config/jade", "jade ledger stats", "jade check --offline", "uv tool list", "git status",
    "cd ~/.local/share/jade && ls", "grep -n enforce ~/.config/jade/config.json", "uv run pytest -q", "uv run jade work render", "~/.local/bin/jade check",
    "echo jade.sqlite", "uv tool run ruff check .",
])
def test_reading_or_running_the_cli_stays_low(cmd):
    assert level(cmd).level == "low", level(cmd)


def test_the_cli_itself_still_manages_its_artifacts(tmp_path, human_env):
    """Protection is a gate on the agent's tools; the human-only commands write through Python, not through a gated tool call."""
    ops._save_pins(tmp_path, {"q": "v2"})
    assert ops._pins(tmp_path) == {"q": "v2"}
    db = ledger.open(tmp_path)
    db.meta_set("k", "v")
    assert db.meta_get("k") == "v"


# ---------------------------------------------------------------- G01: matcher and MCP surface

MATCHED = ["Bash", "PowerShell", "Write", "Edit", "MultiEdit", "NotebookEdit", "Monitor", "RemoteTrigger",
           "mcp__Desktop_Commander__write_file", "mcp__anything__at_all"]


@pytest.mark.parametrize("tool", MATCHED)
def test_installed_matcher_covers_the_tool(tool):
    assert re.fullmatch(ops.HOOKS["PreToolUse"][0], tool), tool


def test_the_matcher_is_exactly_the_gated_tool_list():
    assert ops.HOOKS["PreToolUse"][0] == ops.pretool_matcher()
    assert set(policy.GATED_BUILTIN) <= set(ops.HOOKS["PreToolUse"][0].split("|"))


MCP_HIGH = [
    ("mcp__ccd_session_mgmt__set_session_permission_mode", {"mode": "bypassPermissions"}),
    ("mcp__ccd_settings__set_setting", {"key": "permissions", "value": "x"}),
    ("mcp__scheduled-tasks__create_scheduled_task", {"prompt": "do x"}),
    ("mcp__scheduled-tasks__update_scheduled_task", {"id": "1", "prompt": "do x"}),
    ("mcp__scheduled-tasks__run_scheduled_task", {"id": "1"}),
    ("mcp__Desktop_Commander__set_config_value", {"key": "allowedDirectories", "value": []}),
    ("mcp__Desktop_Commander__write_file", {"path": "/tmp/x", "content": "x"}),
    ("mcp__Desktop_Commander__edit_block", {"file_path": "/tmp/x", "old_string": "a", "new_string": "b"}),
    ("mcp__Desktop_Commander__move_file", {"source": "/tmp/a", "destination": "/tmp/b"}),
    ("mcp__Desktop_Commander__interact_with_process", {"pid": 1, "input": "git push origin feat/x"}),
    ("mcp__MCP_DOCKER__mcp-exec", {"name": "x", "arguments": {"command": "ls"}}),
    ("mcp__MCP_DOCKER__code-mode", {"code": "x", "servers": []}),
    ("mcp__MCP_DOCKER__mcp-add", {"name": "x"}), ("mcp__MCP_DOCKER__mcp-config-set", {"server": "x", "config": {}}),
    ("mcp__MacOS-MCP__Shell", {"command": "git push origin feat/x"}),
    ("mcp__Kubernetes_MCP_Server__kubectl_apply", {"manifest": "x"}), ("mcp__Kubernetes_MCP_Server__exec_in_pod", {"name": "p", "command": ["sh"]}),
    ("mcp__GitLab__accept_merge_request", {"id": 1}), ("mcp__GitLab__save_merge_request", {"id": 1}),
    ("mcp__computer-use__app_type", {"app": "Terminal", "text": "git push origin feat/x\n"}),
    ("mcp__MacOS-MCP__Type", {"text": "git push origin feat/x\n"}),
    ("mcp__ccd_session_mgmt__send_message", {"session_id": "x", "message": "do it"}),
    ("mcp__some_new_server__delete_everything", {"x": 1}), ("mcp__some_new_server__write_config", {"x": 1}),
    ("mcp__some_new_server__execute", {"x": 1}),
    ("RemoteTrigger", {"action": "run", "id": "x"}),
]


@pytest.mark.parametrize("tool,tin", MCP_HIGH)
def test_mutating_or_governance_weakening_tools_need_a_permit(tool, tin):
    a = policy.assess(tool, tin, "/nonexistent-a2")
    assert LEVELS[a.level] >= LEVELS["high"], a
    assert hooks.pretool(call(tool, tin)).exit_code == 2


@pytest.mark.parametrize("tool,tin", MCP_HIGH)
def test_the_degraded_failsafe_also_blocks_every_high_tool(tool, tin):
    """When the policy cannot be loaded the name heuristics in hooks.py stand in for risk.yaml; they must agree on the known tools."""
    assert hooks.failsafe_reason(call(tool, tin)) is not None


@pytest.mark.parametrize("tool,tin", [
    ("mcp__GitLab__get_project", {"id": 1}), ("mcp__GitLab__list_merge_requests", {}), ("mcp__Desktop_Commander__read_file", {"path": "~/.claude/settings.json"}),
    ("mcp__Desktop_Commander__list_directory", {"path": "/"}), ("mcp__playwright-cdp__browser_snapshot", {}), ("mcp__context7__query-docs", {"q": "x"}),
    ("mcp__Kubernetes_MCP_Server__kubectl_get", {"resourceType": "pods"}), ("mcp__computer-use__app_screenshot", {"app": "x"}),
    ("mcp__computer-use__app_type", {"app": "Notes", "text": "buy milk"}), ("mcp__Desktop_Commander__interact_with_process", {"pid": 1, "input": "ls"}),
    ("mcp__MacOS-MCP__Shell", {"command": "ls -la"}), ("mcp__ccd_session_mgmt__list_sessions", {}), ("mcp__scheduled-tasks__list_scheduled_tasks", {}),
    ("mcp__mac-mini-stack__stack_health", {}), ("mcp__herd__logs", {"action": "read"}), ("mcp__ccd_session_mgmt__get_session", {"id": "x"}),
])
def test_read_only_tools_stay_ungated(tool, tin):
    a = policy.assess(tool, tin, "/nonexistent-a2")
    assert LEVELS[a.level] < LEVELS["high"], a
    assert hooks.pretool(call(tool, tin)).exit_code == 0


def test_mcp_file_write_applies_the_path_rules():
    a = policy.assess("mcp__Desktop_Commander__write_file", {"path": "/etc/pf.conf", "content": "x"}, "/")
    assert a.level == "critical" and a.rule_id == "R-01"
    assert policy.assess("mcp__Desktop_Commander__write_file", {"path": "/tmp/x", "content": "x"}, "/").rule_id.startswith("M-")


def test_unknown_mcp_tool_without_a_rule_is_labelled_not_silently_low():
    a = policy.assess("mcp__brand_new__poke", {"x": 1}, "")
    assert a.rule_id == "M-99" and a.level == "medium"


def test_agent_and_task_calls_need_no_gate():
    for tool in ("Agent", "Task", "Read", "Grep", "WebFetch"):
        assert hooks.pretool(call(tool, {"prompt": "git push origin feat/x"})).exit_code == 0


def test_subagent_tool_calls_are_gated_exactly_like_the_parents(human_env):
    """Claude Code runs the same PreToolUse hook for a subagent's tool calls and adds agent_id/agent_type; the verdict must not depend on them."""
    main = hooks.pretool(bash("git push origin feat/x"))
    sub = hooks.pretool(bash("git push origin feat/x", agent_id="agent-1", agent_type="general-purpose"))
    assert main.exit_code == sub.exit_code == 2
    assert hooks.pretool(bash("git push origin main", agent_id="agent-1")).exit_code == 2
    row = ledger.open().one("SELECT detail_json FROM events WHERE kind='safety_block'")
    assert json.loads(row.detail_json)["agent_id"] == "agent-1"


# ---------------------------------------------------------------- G02: SI-02 and SI-05 on content

def test_write_content_with_a_secret_is_blocked():
    for key in ("content", "new_string", "new_source"):
        r = hooks.pretool(call("Write", {"file_path": "/tmp/a2/.env", key: "KEY=" + sec("sk-", "Hh8Gg7Ff6Ee5Dd4Cc3Bb2Aa1Zz")}))
        assert r.exit_code == 2 and "SI-05" in r.stderr, key
    r = hooks.pretool(call("Write", {"file_path": "/tmp/a2/k", "content": sec("-----BEGIN RSA ", "PRIVATE KEY-----\nabc")}))
    assert r.exit_code == 2 and "SI-05" in r.stderr


def test_multiedit_and_notebook_content_is_scanned():
    tin = {"file_path": "/tmp/a2/x.py", "edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "t=" + sec("ghp_", "A" * 36)}]}
    assert "SI-05" in hooks.pretool(call("MultiEdit", tin)).stderr
    assert "SI-05" in hooks.pretool(call("NotebookEdit", {"notebook_path": "/tmp/a2/n.ipynb", "new_source": sec("AKIA", "ABCDEFGHIJKLMNOP")})).stderr


def test_write_content_that_binds_a_public_address_is_blocked():
    conf = "server {\n  listen " + sec("0.0", ".0.0:11434") + ";\n}\n"
    r = hooks.pretool(call("Write", {"file_path": "/tmp/a2/nginx.conf", "content": conf}))
    assert r.exit_code == 2 and "SI-02" in r.stderr
    env = "OLLAMA_HOST=" + sec("0.0", ".0.0") + ":11434\n"
    assert "SI-02" in hooks.pretool(call("Write", {"file_path": "/tmp/a2/.env", "content": env})).stderr
    assert "SI-02" in hooks.pretool(call("Edit", {"file_path": "/tmp/a2/c.yml", "old_string": "x", "new_string": "command: serve --host " + sec("0.0", ".0.0")})).stderr


def test_removing_a_secret_is_not_blocked_and_ordinary_content_passes():
    old = "KEY=" + sec("sk-", "Hh8Gg7Ff6Ee5Dd4Cc3Bb2Aa1Zz")
    assert hooks.pretool(call("Edit", {"file_path": "/tmp/a2/.env", "old_string": old, "new_string": "KEY=$(ovault secret get K --project p --raw)"})).exit_code == 0
    assert hooks.pretool(call("Write", {"file_path": "/tmp/a2/a.txt", "content": "listen 127.0.0.1:11434; docs mention jade gate off"})).exit_code == 0


def test_mcp_arguments_without_a_command_are_scanned_too():
    r = hooks.pretool(call("mcp__Desktop_Commander__write_file", {"path": "/tmp/a2/x", "content": "t=" + sec("glpat-", "a" * 24)}))
    assert r.exit_code == 2 and "SI-05" in r.stderr
    r = hooks.pretool(call("mcp__brand_new__poke", {"payload": {"deep": ["x", "bind " + sec("0.0", ".0.0:8080")]}}))
    assert r.exit_code == 2 and "SI-02" in r.stderr
    r = hooks.pretool(call("mcp__GitLab__get_project", {"query": sec("xoxb-", "1234567890abcd")}))
    assert r.exit_code == 2 and "SI-05" in r.stderr


# ---------------------------------------------------------------- G03: host permission mode

@pytest.mark.parametrize("mode", ["bypassPermissions", "acceptEdits", "plan", "default", None])
def test_the_verdict_does_not_depend_on_the_host_permission_mode_and_the_mode_is_recorded(mode):
    extra = {"permission_mode": mode} if mode else {}
    assert hooks.pretool(bash("git push origin feat/x", **extra)).exit_code == 2
    assert hooks.pretool(bash("git push origin main", **extra)).exit_code == 2
    rows = ledger.open().all("SELECT detail_json FROM events WHERE kind='safety_block'")
    assert json.loads(rows[-1].detail_json).get("permission_mode") == mode
    pend = ledger.open().one("SELECT scope FROM permits WHERE state='PENDING'")
    assert json.loads(pend.scope)["permission_mode"] == mode


def test_check_says_plainly_that_jade_is_the_only_gate_without_a_host_prompt():
    rows = {n: (ok, d) for n, ok, d in ops.check(live=False)}
    ok, detail = rows["host permission prompt"]
    assert ok is True
    assert "only gate" in detail and "bypassPermissions" in detail and "headless" in detail


def test_check_reports_gated_calls_seen_without_a_host_prompt():
    hooks.pretool(bash("git push origin feat/x", permission_mode="bypassPermissions"))
    hooks.pretool(bash("git push origin feat/y", permission_mode="default"))
    detail = {n: d for n, _ok, d in ops.check(live=False)}["host permission prompt"]
    assert "1 of 2 gated call" in detail


def test_docs_state_the_no_backstop_modes():
    for doc in ("docs/05-threat-model.md", "docs/08-deployment-runbook.md"):
        text = (ROOT / doc).read_text()
        assert "bypassPermissions" in text and "only gate" in text, doc


# ---------------------------------------------------------------- G06: SI-06 and SI-07 at the gate

DEPLOYS = ["kubectl apply -f prod.yaml", "terraform apply -auto-approve", "fly deploy", "helm upgrade app ./chart", "ansible-playbook site.yml", "vercel --prod"]


@pytest.mark.parametrize("cmd", DEPLOYS)
def test_si06_a_production_release_is_never_auto_issued(cmd):
    db = ledger.open()
    for _ in range(3):
        r = hooks.pretool(bash(cmd))
        assert r.exit_code == 2 and "permit" in r.stderr
    a = policy.assess("Bash", {"command": cmd}, "/nonexistent-a2")
    assert a.level == "critical"
    states = {p.state for p in permits.listing(db, ("PENDING", "ISSUED", "CONSUMED"))}
    assert states == {"PENDING"}
    assert permits.find_usable(db, a.plan_hash) is None
    pend = permits.listing(db, ("PENDING",))[0]
    assert decide.get(db, pend.decision_id)["status"] == "WAITING_HUMAN"


def test_si06_a_permit_cannot_be_issued_without_a_human_terminal(agent_env):
    db = ledger.open()
    hooks.pretool(bash("fly deploy"))
    pid = permits.listing(db, ("PENDING",))[0].permit_id
    with pytest.raises(permits.ApprovalRefused):
        permits.approve(db, pid, stdin_isatty=True)
    assert hooks.pretool(bash("fly deploy")).exit_code == 2


def _run_once(cmd, human):
    db = ledger.open()
    assert hooks.pretool(bash(cmd)).exit_code == 2
    p = permits.listing(db, ("PENDING",))[0]
    permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(bash(cmd)).exit_code == 0
    assert decide.get(db, p.decision_id)["status"] == "EXECUTING"
    return db, p


def test_si07_an_unknown_outcome_blocks_a_retry_until_reconciled(human_env):
    cmd = "fly deploy"
    db, p = _run_once(cmd, human_env)
    decide.record_outcome(db, p.decision_id, "unknown", None, "deploy output lost")
    assert decide.get(db, p.decision_id)["status"] == "OUTCOME_UNKNOWN"
    before = len(permits.listing(db, ("PENDING", "ISSUED")))
    r = hooks.pretool(bash(cmd))
    assert r.exit_code == 2 and "SI-07" in r.stderr and p.decision_id in r.stderr and "jade decide outcome" in r.stderr
    assert len(permits.listing(db, ("PENDING", "ISSUED"))) == before  # no fresh permit is raised for the identical command
    decide.record_outcome(db, p.decision_id, "observed", True, "read the release state from the platform")
    assert decide.get(db, p.decision_id)["status"] == "SUCCEEDED"
    again = hooks.pretool(bash(cmd))
    assert again.exit_code == 2 and "SI-07" not in again.stderr and "permit" in again.stderr


def test_si07_an_issued_permit_is_not_consumed_while_the_outcome_is_unknown(human_env):
    cmd = "fly deploy"
    db, p = _run_once(cmd, human_env)
    decide.record_outcome(db, p.decision_id, "unknown", None, "lost")
    a = policy.assess("Bash", {"command": cmd}, "/nonexistent-a2")
    q = permits.request(db, "fly.deploy", a.plan_hash, risk="critical")
    permits.approve(db, q.permit_id, stdin_isatty=True)
    r = hooks.pretool(bash(cmd))
    assert r.exit_code == 2 and "SI-07" in r.stderr and permits.get(db, q.permit_id).state == "ISSUED"


def test_si07_an_irreversible_action_still_in_flight_is_not_started_twice(human_env):
    cmd = "fly deploy"
    db, p = _run_once(cmd, human_env)
    r = hooks.pretool(bash(cmd))  # PostToolUse has not reported yet
    assert r.exit_code == 2 and "SI-07" in r.stderr and "still executing" in r.stderr


def test_si07_only_an_identical_command_is_held(human_env):
    db, p = _run_once("fly deploy", human_env)
    decide.record_outcome(db, p.decision_id, "unknown", None, "lost")
    r = hooks.pretool(bash("fly deploy --app other"))
    assert r.exit_code == 2 and "SI-07" not in r.stderr


def test_si07_advisory_mode_reports_without_blocking(human_env, monkeypatch):
    db, p = _run_once("fly deploy", human_env)
    decide.record_outcome(db, p.decision_id, "unknown", None, "lost")
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool(bash("fly deploy"))
    assert r.exit_code == 0 and "SI-07" in r.stderr


def test_safety_yaml_documents_si07_enforcement_and_the_reserved_numbers():
    text = (ROOT / "policies" / "safety.yaml").read_text()
    assert "hook" in text.split("id: SI-07")[1].split("id: SI-08")[0]
    assert "SI-03" in text and "reserved" in text
