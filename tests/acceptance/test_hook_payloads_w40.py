"""W-40 (T20): hook payload shapes, the loop for Write/Edit/MCP calls, and a permit that meets a parallel hook.

tests/fixtures/hook_payloads.json holds one PreToolUse and one PostToolUse payload per tool family (Bash, Write, Edit, an MCP tool, a Task call, a Bash call
inside a subagent). They are SYNTHETIC (shaped after the host's documentation, every value invented); `captured: true` marks a family a human replaced with a
redacted real pair, and the assertions below then hold against the real shape.

Decision on the permit race (docs/05 T20, docs/08 section 4a): a permit is spent when the gate lets the call through and is never returned (JADE cannot
tell a call another hook denied from one that ran: no PostToolUse follows either). What the gate does is make one call idempotent: the host's `tool_use_id`
is recorded with the consume, so the same call delivered twice (two registrations of the hook, a host retry, two processes at once) gets the same answer. A
different call, other content, another session or agent, or a late replay needs a new permit."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from jade import decide, hooks, ledger, ops, permits, redact

FIXTURES = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "hook_payloads.json").read_text())
FAMILIES = ("bash", "write", "edit", "mcp", "task", "subagent_bash")
GATED = ("bash", "write", "edit", "mcp", "subagent_bash")   # `task` needs no gate: the calls a subagent makes reach the same hook
CWD = "/tmp/x"                                              # a directory every machine has; the fixtures' own cwd is invented
OTHER = "sess-bbbbbbbb"


@pytest.fixture
def home(tmp_path, monkeypatch, human_env):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


def pre(family, **over):
    ev = json.loads(json.dumps(FIXTURES[family]["pre"]))
    ev["cwd"] = CWD
    ev.update(over)
    return ev


def post(family, **over):
    ev = json.loads(json.dumps(FIXTURES[family]["post"]))
    ev["cwd"] = CWD
    ev.update(over)
    return ev


def pending(db):
    return permits.listing(db, ("PENDING",))[-1]


def approved(event):
    """The gate blocks `event`; a human approves the permit it raised. Returns (db, permit)."""
    db = ledger.open()
    assert hooks.pretool(event).exit_code == 2
    p = pending(db)
    permits.approve(db, p.permit_id, stdin_isatty=True)
    return db, p


def status_of(db, decision_id):
    return db.one("SELECT status FROM decisions WHERE decision_id=?", (decision_id,)).status


def count(db, kind):
    return db.one("SELECT COUNT(*) AS n FROM events WHERE kind=?", (kind,)).n


# ---- the fixtures ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("family", FAMILIES)
def test_each_family_has_a_pre_and_a_post_payload_of_the_documented_shape(family):
    f = FIXTURES[family]
    assert isinstance(f["captured"], bool)
    p, q = f["pre"], f["post"]
    for ev, name in ((p, "PreToolUse"), (q, "PostToolUse")):
        assert ev["hook_event_name"] == name
        assert isinstance(ev["session_id"], str) and ev["session_id"]
        assert isinstance(ev["cwd"], str) and ev["permission_mode"] in ("default", "acceptEdits", "plan", "bypassPermissions", "auto")
        assert isinstance(ev["tool_name"], str) and isinstance(ev["tool_input"], dict)
        assert re.fullmatch(r"toolu_[A-Za-z0-9_]{8,}", ev["tool_use_id"])
    assert "tool_response" not in p and "tool_response" in q
    assert (p["session_id"], p["tool_name"], p["tool_use_id"], p["tool_input"]) == (q["session_id"], q["tool_name"], q["tool_use_id"], q["tool_input"])
    assert ("agent_id" in p) == ("agent_id" in q) == (family == "subagent_bash")   # agent_id only on a call made inside a subagent
    if family == "subagent_bash":
        assert p["agent_id"] == q["agent_id"] and p["agent_type"]


def test_the_fixtures_hold_nothing_real():
    text = (Path(__file__).resolve().parents[1] / "fixtures" / "hook_payloads.json").read_text()
    assert redact.redact(text)[1] == 0
    assert "/Users/" not in text and not re.search(r"(?i)\b[a-z0-9-]+\.(?:com|net|org|io|ir|dev|app)\b", text)
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)


def test_the_task_call_is_not_gated_and_leaves_no_trace(home):
    db = ledger.open()
    assert hooks.pretool(pre("task")).exit_code == 0 and hooks.posttool(post("task")).exit_code == 0
    assert db.one("SELECT COUNT(*) AS n FROM decisions").n == 0 and permits.listing(db, ("PENDING", "ISSUED")) == []


@pytest.mark.parametrize("family", GATED)
def test_the_permit_is_bound_to_the_session_agent_and_mode_of_the_payload(home, family):
    ev = pre(family)
    db, p = approved(ev)
    ctx = permits.ctx_of(p)
    assert (ctx["session_id"], ctx["agent_id"], ctx["permission_mode"]) == (ev["session_id"], ev.get("agent_id"), ev["permission_mode"])
    assert hooks.pretool({**ev, "session_id": OTHER}).exit_code == 2                       # twin: the same call from another session
    assert hooks.pretool({**ev, "agent_id": "agent-0000000000000002"}).exit_code == 2       # ... or from another agent
    assert permits.get(db, p.permit_id).state == "ISSUED"
    r = hooks.pretool(ev)
    assert r.exit_code == 0 and "consumed" in r.stdout + r.stderr


def test_a_permit_requested_in_a_subagent_does_not_serve_the_main_session(home):
    db, p = approved(pre("subagent_bash"))
    main = pre("bash")                       # same command, same session id, no agent_id
    assert main["tool_input"]["command"] == FIXTURES["subagent_bash"]["pre"]["tool_input"]["command"]
    assert hooks.pretool(main).exit_code == 2 and permits.get(db, p.permit_id).state == "ISSUED"


# ---- closing the loop for Write, Edit and MCP -----------------------------------------------------------
@pytest.mark.parametrize("family", GATED)
def test_a_permitted_call_is_closed_by_its_posttool_event(home, family):
    db, p = approved(pre(family))
    assert hooks.pretool(pre(family)).exit_code == 0
    did = p.decision_id
    assert status_of(db, did) == "EXECUTING"
    assert hooks.posttool(post(family)).exit_code == 0
    assert status_of(db, did) == "SUCCEEDED"
    out = json.loads(db.one("SELECT doc_json FROM outcomes WHERE decision_id=?", (did,)).doc_json)
    assert out["observation_type"] == "observed" and out["success"] is True and out["attribution_quality"] == "indirect"


@pytest.mark.parametrize("response", [{"isError": True, "content": [{"type": "text", "text": "boom"}]}, {"is_error": True}, {"error": "x"}, {"interrupted": True}])
def test_a_failed_response_is_recorded_as_a_failure(home, response):
    db, p = approved(pre("mcp"))
    hooks.pretool(pre("mcp"))
    hooks.posttool(post("mcp", tool_response=response))
    assert status_of(db, p.decision_id) == "FAILED"


def test_posttool_of_another_content_session_or_path_closes_nothing(home):
    db, p = approved(pre("write"))
    hooks.pretool(pre("write"))
    other_content = post("write")
    other_content["tool_input"] = {**other_content["tool_input"], "content": "something else\n"}
    hooks.posttool(other_content)                                      # the same path, other content: not the call that was permitted
    hooks.posttool(post("write", session_id=OTHER))                     # another session
    hooks.posttool(post("edit"))                                        # another tool
    assert status_of(db, p.decision_id) == "EXECUTING"
    hooks.posttool(post("write"))                                       # twin: the permitted call
    assert status_of(db, p.decision_id) == "SUCCEEDED"


def test_a_call_that_needs_no_permit_leaves_no_decision_behind(home):
    ev = {**pre("write"), "tool_input": {"file_path": str(home / "notes.txt"), "content": "hi\n"}}
    db = ledger.open()
    assert hooks.pretool(ev).exit_code == 0
    assert hooks.posttool({**ev, "hook_event_name": "PostToolUse", "tool_response": {"type": "create"}}).exit_code == 0
    assert db.one("SELECT COUNT(*) AS n FROM decisions").n == 0


def test_a_payload_posttool_cannot_read_closes_nothing_and_does_not_crash(home):
    db, p = approved(pre("write"))
    hooks.pretool(pre("write"))
    for ev in ({}, {"tool_name": "Write"}, {"tool_name": "Write", "tool_input": "x"}, {"tool_name": "mcp__demo__shell", "tool_input": None},
               {"tool_name": "Write", "tool_input": {"file_path": 5}, "tool_response": 7}):
        assert hooks.posttool(ev).exit_code == 0
    assert status_of(db, p.decision_id) == "EXECUTING"


# ---- the PostToolUse matcher ----------------------------------------------------------------------------
def test_the_posttool_matcher_names_every_call_the_gate_can_permit():
    m = ops.HOOKS["PostToolUse"][0]
    for tool in ("Bash", "PowerShell", "Monitor", "Write", "Edit", "MultiEdit", "NotebookEdit", "RemoteTrigger", "mcp__demo__shell", "mcp__a__b"):
        assert re.fullmatch(m, tool), tool
    for tool in ("Read", "Grep", "Glob", "Task", "Agent", "WebFetch"):   # a read is never permitted, so nothing waits for its end
        assert not re.fullmatch(m, tool), tool


def test_hooks_install_writes_the_wide_posttool_matcher_and_an_old_install_is_reported(human_env, jade_stub, tmp_path):
    ops.hooks_install(stdin_isatty=True)
    s = tmp_path / "settings.json"
    data = json.loads(s.read_text())
    assert data["hooks"]["PostToolUse"][0]["matcher"] == ops.HOOKS["PostToolUse"][0]
    assert not [x for x in ops.wiring_problems(probe=False) if "PostToolUse" in x]
    data["hooks"]["PostToolUse"][0]["matcher"] = "Bash"      # what releases before this one wrote
    s.write_text(json.dumps(data))
    problems = [x for x in ops.wiring_problems(probe=False) if "PostToolUse" in x]
    assert problems and "jade hooks install" in problems[0]


# ---- the permit and a parallel hook ---------------------------------------------------------------------
@pytest.mark.parametrize("family", GATED)
def test_the_same_call_delivered_twice_gets_the_same_answer_and_spends_one_permit(home, family):
    db, p = approved(pre(family))
    first, again = hooks.pretool(pre(family)), hooks.pretool(pre(family))
    assert first.exit_code == 0 and again.exit_code == 0 and p.permit_id in again.stdout + again.stderr
    assert count(db, "permit_consumed") == 1
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE entity_id=? AND next_state='EXECUTING'", (p.decision_id,)).n == 1
    assert permits.get(db, p.permit_id).state == "CONSUMED"


def test_a_second_call_is_a_new_call_and_needs_a_new_permit(home):
    db, p = approved(pre("write"))
    assert hooks.pretool(pre("write")).exit_code == 0
    retry = pre("write", tool_use_id="toolu_synthetic_write_0002")   # the model asks again after the host or another hook denied the first
    r = hooks.pretool(retry)
    assert r.exit_code == 2 and "needs a human-approved permit" in r.stderr
    assert count(db, "permit_consumed") == 1


@pytest.mark.parametrize("bad", ["absent", "", 5, "x" * 300, "toolu 01; DROP", ["a"]])
def test_a_call_without_a_usable_id_is_single_use_as_before(home, bad):
    ev = pre("write")
    ev.pop("tool_use_id")
    if bad != "absent":
        ev["tool_use_id"] = bad
    db, p = approved(ev)
    assert hooks.pretool(ev).exit_code == 0
    assert hooks.pretool(ev).exit_code == 2


@pytest.mark.parametrize("change", [
    {"session_id": OTHER},
    {"agent_id": "agent-0000000000000002"},
    {"tool_input": {"file_path": "~/.claude/settings.json", "content": "other\n"}},
    {"tool_input": {"file_path": "~/.claude/settings.local.json", "content": "{\"hooks\": {}}\n"}},
])
def test_the_same_id_with_another_caller_or_other_content_is_not_the_same_call(home, change):
    db, p = approved(pre("write"))
    assert hooks.pretool(pre("write")).exit_code == 0
    assert hooks.pretool(pre("write", **change)).exit_code == 2


def test_a_late_replay_of_the_same_call_needs_a_new_permit(home):
    db, p = approved(pre("write"))
    assert hooks.pretool(pre("write")).exit_code == 0
    with db.tx() as con:
        con.execute("UPDATE events SET ts='2020-01-01T00:00:00Z' WHERE kind='permit_consumed'")
    assert hooks.pretool(pre("write")).exit_code == 2


def test_a_replay_is_not_served_when_the_permit_was_edited_after_use(home):
    db, p = approved(pre("write"))
    assert hooks.pretool(pre("write")).exit_code == 0
    with db.tx() as con:
        con.execute("UPDATE permits SET plan_hash='0' WHERE permit_id=?", (p.permit_id,))
    assert hooks.pretool(pre("write")).exit_code == 2


def test_an_id_that_spent_one_permit_cannot_spend_a_second(home):
    db, p = approved(pre("write"))
    assert hooks.pretool(pre("write")).exit_code == 0
    # a second permit for the same plan and content, approved afterwards, is not spent by a replay of the first call
    ev = pre("write", tool_use_id="toolu_synthetic_write_0009")
    assert hooks.pretool(ev).exit_code == 2
    p2 = pending(db)
    permits.approve(db, p2.permit_id, stdin_isatty=True)
    assert hooks.pretool(pre("write")).exit_code == 0 and permits.get(db, p2.permit_id).state == "ISSUED"
    assert hooks.pretool(ev).exit_code == 0 and permits.get(db, p2.permit_id).state == "CONSUMED"


# ---- concurrent processes against one JADE_HOME ---------------------------------------------------------
def run_parallel(events):
    """Start one `jade hook pretool` process per event, feed them all together, return their exit codes (the processes use this test's JADE_HOME)."""
    cmd = [sys.executable, "-m", "jade.cli", "hook", "pretool"]   # callers reopen the ledger afterwards: a connection opened before the other processes wrote can hold a stale WAL snapshot (seen on Linux)
    procs = [subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ)) for _ in events]
    for proc, ev in zip(procs, events, strict=True):
        proc.stdin.write(json.dumps(ev))
        proc.stdin.close()
    codes = []
    for proc in procs:
        proc.stdout.read()
        proc.stderr.read()
        codes.append(proc.wait(timeout=60))
    return codes


@pytest.mark.parametrize("family", ("write", "mcp"))
def test_parallel_processes_delivering_one_call_all_pass_and_spend_one_permit(home, family):
    db, p = approved(pre(family))
    db.close()   # a connection kept open while the child processes write and the last one deletes the -wal/-shm files holds a stale shared-memory map; the next connection in this process then reads 'database disk image is malformed' (CI flake, Linux)
    codes = run_parallel([pre(family)] * 6)
    db = ledger.open()
    assert codes == [0] * 6
    assert count(db, "permit_consumed") == 1 and permits.get(db, p.permit_id).state == "CONSUMED"
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE entity_id=? AND next_state='EXECUTING'", (p.decision_id,)).n == 1
    assert status_of(db, p.decision_id) == "EXECUTING"


def test_parallel_processes_delivering_different_calls_spend_one_permit_for_one_call(home):
    db, p = approved(pre("write"))
    evs = [pre("write", tool_use_id=f"toolu_synthetic_parallel_{i:04d}") for i in range(6)]
    db.close()   # a connection kept open while the child processes write and the last one deletes the -wal/-shm files holds a stale shared-memory map; the next connection in this process then reads 'database disk image is malformed' (CI flake, Linux)
    codes = run_parallel(evs)
    db = ledger.open()
    assert sorted(codes) == [0, 2, 2, 2, 2, 2]
    assert count(db, "permit_consumed") == 1
    winner = evs[codes.index(0)]
    assert hooks.pretool(winner).exit_code == 0                       # the call that won is still answered the same
    assert all(hooks.pretool(e).exit_code == 2 for e, c in zip(evs, codes, strict=True) if c == 2)   # the others need a new permit
    assert count(db, "permit_consumed") == 1


def test_the_decision_and_the_permit_move_together_under_parallel_calls(home):
    db, p = approved(pre("mcp"))
    db.close()   # a connection kept open while the child processes write and the last one deletes the -wal/-shm files holds a stale shared-memory map; the next connection in this process then reads 'database disk image is malformed' (CI flake, Linux)
    run_parallel([pre("mcp")] * 4 + [pre("mcp", tool_use_id="toolu_synthetic_mcp_0002")] * 2)
    db = ledger.open()
    states = {r.permit_id: r.state for r in db.all("SELECT permit_id, state FROM permits")}
    assert states[p.permit_id] == "CONSUMED"
    assert status_of(db, p.decision_id) == "EXECUTING" and decide.get(db, p.decision_id)["status"] == "EXECUTING"
