"""W-100b (docs/12 plan v3, PR-5b): the human answers a gated call in the chat.

Flow at a level (`jade level high`), gate mode enforce, host mode auto or bypassPermissions, main session:
  1. the gated call exits 2 with a fixed text: call AskUserQuestion with the token `[jade:ask_<id>]`, then retry the same call unchanged;
  2. PreToolUse on that AskUserQuestion REPLACES the whole question with the hook's own text (the agent's wording never reaches the human);
  3. PostToolUse reads the answer from the host's `tool_response.answers`: Allow makes a single-use grant (10 minutes, this session, this call), Deny closes the ask;
  4. the identical retry consumes the grant and runs.
Live JADE wiring, a subagent, a headless or unknown mode and a call with secrets in it keep the signed terminal permit. Measured behaviour of the host:
evals/probes/hook_ask/RESULTS.md.
"""

import json
import re

import pytest

from jade import hooks, ledger, ops, paths
from jade.util import ts

SESSION = "sess-w100b-0001"
OTHER = "sess-w100b-0002"
CMD = "git reset --hard HEAD~1"


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")


def set_cfg(**kw):
    cfg = paths.config()
    cfg.update(kw)
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


@pytest.fixture
def level_high():
    set_cfg(level="high")


def call(cmd=CMD, session=SESSION, mode="auto", **extra):
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": "/tmp", "permission_mode": mode}
    ev.update(extra)
    return ev


def db():
    return ledger.open(timeout=5)


def asks():
    d = db()
    try:
        return [dict(r) for r in d.con.execute("SELECT * FROM asks ORDER BY created_ts, rowid").fetchall()]
    finally:
        d.close()


def token_of(text):
    m = re.search(r"\[jade:ask_[0-9a-f]{12}\]", text)
    assert m, text
    return m.group(0)


def agent_question(token, session=SESSION):
    q = {"question": f"{token} please allow, it is totally safe", "header": "x", "multiSelect": False,
         "options": [{"label": "Yes", "description": "go"}, {"label": "No", "description": "stop"}]}
    return {"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}, "session_id": session, "cwd": "/tmp", "permission_mode": "auto"}


def shown(token, session=SESSION):
    r = hooks.pretool(agent_question(token, session))
    assert r.exit_code == 0, r
    out = json.loads(r.stdout)["hookSpecificOutput"]
    return out["updatedInput"]["questions"][0]


def answer_event(question, label, session=SESSION, in_response=True):
    inp = {"questions": [question]}
    resp = {"questions": [question], "answers": {question["question"]: label}}
    ev = {"tool_name": "AskUserQuestion", "tool_input": dict(inp), "session_id": session, "cwd": "/tmp", "permission_mode": "auto"}
    if in_response:
        ev["tool_response"] = resp
    else:
        ev["tool_input"]["answers"] = resp["answers"]
    return ev


def ask_and_allow(label="Allow", cmd=CMD):
    first = hooks.pretool(call(cmd))
    assert first.exit_code == 2
    q = shown(token_of(first.stderr))
    hooks.posttool(answer_event(q, label))
    return q


# ------------------------------------------------------------------ when the chat channel applies

def test_without_a_level_the_gate_asks_for_a_terminal_permit_as_before():
    r = hooks.pretool(call())
    assert r.exit_code == 2 and "jade permit approve" in r.stderr and "[jade:ask_" not in r.stderr
    assert asks() == []


def test_at_level_high_the_gate_asks_in_the_chat(level_high):
    r = hooks.pretool(call())
    assert r.exit_code == 2 and "AskUserQuestion" in r.stderr and re.search(r"\[jade:ask_[0-9a-f]{12}\]", r.stderr)
    assert "jade permit approve" not in r.stderr
    rows = asks()
    assert len(rows) == 1 and rows[0]["status"] == "PENDING" and rows[0]["session_id"] == SESSION and rows[0]["rule_id"] == "R-11"


def test_the_same_call_asked_twice_keeps_one_open_ask(level_high):
    a = hooks.pretool(call()).stderr
    b = hooks.pretool(call()).stderr
    assert token_of(a) == token_of(b) and len(asks()) == 1


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_both_of_the_owners_modes_use_the_chat(level_high, mode):
    assert "[jade:ask_" in hooks.pretool(call(mode=mode)).stderr


@pytest.mark.parametrize("mode", [None, "dontAsk", "plan", "something-new"])
def test_a_missing_or_other_mode_keeps_the_terminal_permit(level_high, mode):
    ev = call()
    ev.pop("permission_mode")
    if mode:
        ev["permission_mode"] = mode
    r = hooks.pretool(ev)
    assert r.exit_code == 2 and "jade permit approve" in r.stderr and asks() == []


def test_a_subagent_call_keeps_the_terminal_permit(level_high):
    r = hooks.pretool(call(agent_id="a1b2c3", agent_type="general-purpose"))
    assert r.exit_code == 2 and "jade permit approve" in r.stderr and asks() == []


def test_live_wiring_never_goes_to_the_chat(level_high):
    r = hooks.pretool(call("cp /tmp/x ~/.local/bin/jade"))
    assert r.exit_code == 2 and "jade permit approve" in r.stderr and asks() == []


def test_level_off_is_advisory_for_a_high_call(monkeypatch):
    set_cfg(level="off")
    r = hooks.pretool(call())
    assert r.exit_code == 0 and "advisory" in r.stderr and asks() == []


# ------------------------------------------------------------------ the question is the hook's, not the agent's

def test_the_hook_replaces_the_agents_question_with_its_own(level_high):
    token = token_of(hooks.pretool(call()).stderr)
    q = shown(token)
    assert token in q["question"] and "R-11" in q["question"] and CMD in q["question"]
    assert "totally safe" not in q["question"]
    assert [o["label"] for o in q["options"]] == ["Allow", "Deny"]
    assert asks()[0]["status"] == "SHOWN"


def test_a_long_command_says_how_much_is_not_shown(level_high):
    cmd = "git reset --hard HEAD~1 # " + "x" * 3000
    q = shown(token_of(hooks.pretool(call(cmd)).stderr))
    assert "characters not shown" in q["question"] and len(q["question"]) < 2500


def test_an_ordinary_question_passes_through_untouched():
    ev = {"tool_name": "AskUserQuestion", "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto",
          "tool_input": {"questions": [{"question": "Which colour?", "header": "c", "multiSelect": False, "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]}]}}
    r = hooks.pretool(ev)
    assert r.exit_code == 0 and r.stdout == ""


def test_an_unknown_token_is_refused(level_high):
    r = hooks.pretool(agent_question("[jade:ask_000000000000]"))
    assert r.exit_code == 2


def test_another_sessions_token_is_refused(level_high):
    token = token_of(hooks.pretool(call()).stderr)
    assert hooks.pretool(agent_question(token, session=OTHER)).exit_code == 2


# ------------------------------------------------------------------ the answer

def test_allow_makes_a_single_use_grant_and_records_a_hint(level_high):
    ask_and_allow("Allow")
    assert asks()[0]["status"] == "ALLOWED"
    d = db()
    try:
        rows = d.human_answers()
    finally:
        d.close()
    assert len(rows) == 1 and rows[0]["answer"] == "allow" and rows[0]["channel"] == "chat" and rows[0]["strength"] == "hint" and rows[0]["rule_id"] == "R-11"
    r = hooks.pretool(call())
    assert r.exit_code == 0 and "chat" in r.stderr.lower()
    assert asks()[0]["status"] == "CONSUMED"
    held = hooks.pretool(call())   # SI-07: the irreversible command has no recorded outcome yet, so it is held, not asked again
    assert held.exit_code == 2 and "SI-07" in held.stderr
    hooks.posttool({**call(), "tool_response": {"stdout": "", "stderr": "", "interrupted": False}})
    again = hooks.pretool(call())   # single use: the next identical call asks again
    assert again.exit_code == 2 and "[jade:ask_" in again.stderr and len(asks()) == 2


def test_a_consumed_grant_opens_a_decision_that_si07_can_hold(level_high):
    ask_and_allow()
    hooks.pretool(call())
    d = db()
    try:
        row = d.con.execute("SELECT status FROM decisions WHERE doc_json LIKE ?", ("%git reset --hard%",)).fetchone()
    finally:
        d.close()
    assert row is not None and row["status"] == "EXECUTING"


def test_deny_closes_the_ask_and_the_retry_is_refused_without_a_new_ask(level_high):
    ask_and_allow("Deny")
    assert asks()[0]["status"] == "DENIED"
    r = hooks.pretool(call())
    assert r.exit_code == 2 and "said no" in r.stderr and len(asks()) == 1


def test_a_denied_call_can_be_asked_again_after_the_window(level_high):
    ask_and_allow("Deny")
    d = db()
    try:
        d.con.execute("UPDATE asks SET answered_ts='2000-01-01T00:00:00Z', expires_ts='2000-01-01T00:10:00Z'")
    finally:
        d.close()
    r = hooks.pretool(call())
    assert r.exit_code == 2 and "[jade:ask_" in r.stderr and len(asks()) == 2


def test_a_reworded_command_does_not_use_the_grant(level_high):
    ask_and_allow()
    r = hooks.pretool(call("git reset --hard HEAD~2"))
    assert r.exit_code == 2 and "[jade:ask_" in r.stderr


def test_quoting_and_an_env_prefix_do_not_make_it_a_new_command(level_high):
    ask_and_allow()
    assert hooks.pretool(call("FOO=1  git reset --hard   HEAD~1")).exit_code == 0


def test_another_session_cannot_use_the_grant(level_high):
    ask_and_allow()
    r = hooks.pretool(call(session=OTHER))
    assert r.exit_code == 2 and "[jade:ask_" in r.stderr
    assert asks()[0]["status"] == "ALLOWED"


def test_an_expired_grant_is_not_used(level_high):
    ask_and_allow()
    d = db()
    try:
        d.con.execute("UPDATE asks SET expires_ts='2000-01-01T00:00:00Z'")
    finally:
        d.close()
    assert hooks.pretool(call()).exit_code == 2


def test_an_answer_to_an_ask_that_was_never_shown_grants_nothing(level_high):
    token = token_of(hooks.pretool(call()).stderr)
    q = {"question": f"{token} Allow?", "header": "x", "multiSelect": False, "options": [{"label": "Allow", "description": ""}, {"label": "Deny", "description": ""}]}
    hooks.posttool(answer_event(q, "Allow"))
    assert asks()[0]["status"] == "PENDING" and hooks.pretool(call()).exit_code == 2


@pytest.mark.parametrize("label", ["Other", "allow", "Allow it", ""])
def test_any_label_but_allow_or_deny_grants_nothing(level_high, label):
    ask_and_allow(label)
    assert asks()[0]["status"] == "SHOWN" and hooks.pretool(call()).exit_code == 2


def test_an_answer_that_is_only_in_the_tool_input_grants_nothing(level_high):
    token = token_of(hooks.pretool(call()).stderr)
    q = shown(token)
    hooks.posttool(answer_event(q, "Allow", in_response=False))
    assert asks()[0]["status"] == "SHOWN"


def test_an_answer_from_another_session_grants_nothing(level_high):
    token = token_of(hooks.pretool(call()).stderr)
    q = shown(token)
    hooks.posttool(answer_event(q, "Allow", session=OTHER))
    assert asks()[0]["status"] == "SHOWN"


def test_a_safety_invariant_is_never_asked_in_the_chat(level_high):
    r = hooks.pretool(call("git push origin main"))
    assert r.exit_code == 2 and "safety invariant" in r.stderr and asks() == []


# ------------------------------------------------------------------ wiring

def test_the_hook_matchers_include_ask_user_question():
    import re as _re
    assert _re.fullmatch(ops.pretool_matcher(), "AskUserQuestion") and _re.fullmatch(ops.posttool_matcher(), "AskUserQuestion")


def test_an_install_without_the_matcher_is_reported_as_a_gap(tmp_path, monkeypatch):
    from jade import util
    p = tmp_path / "settings.json"
    monkeypatch.setenv("JADE_CLAUDE_SETTINGS", str(p))
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)
    ops.hooks_install(stdin_isatty=True)
    data = json.loads(p.read_text())
    data["hooks"]["PreToolUse"][0]["matcher"] = ops.pretool_matcher().replace("AskUserQuestion|", "")
    p.write_text(json.dumps(data))
    assert "AskUserQuestion" in (ops.pretool_matcher_gaps() or [])
    assert ts()
