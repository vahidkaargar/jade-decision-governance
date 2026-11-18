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
    assert [o["label"] for o in q["options"]] == ["Allow", "Deny", "Allow, suggest for any project"]   # cwd /tmp is no project: only the any-project scope (Task 5)
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


def test_an_env_prefix_or_other_spacing_is_a_new_call_and_asks_again(level_high):
    """V6-07 (N10): the grant is for the text the human saw. It was keyed on the command shape, so a changed prefix or spacing spent it."""
    ask_and_allow()
    for text in ("FOO=1  git reset --hard   HEAD~1", "git  reset --hard HEAD~1", "KUBECONFIG=prod git reset --hard HEAD~1"):
        r = hooks.pretool(call(text))
        assert r.exit_code == 2 and "[jade:ask_" in r.stderr, text
    assert asks()[0]["status"] == "ALLOWED"   # the grant for the shown text is still unspent


def test_only_the_trimmed_ends_of_the_same_text_use_the_grant(level_high):
    ask_and_allow()
    assert hooks.pretool(call(CMD + "  \n")).exit_code == 0   # plan_hash trims a space, tab or newline at the ends only


def test_a_grant_for_one_text_is_refused_for_another_text_and_another_cwd(level_high):
    """N10: Allow for text A is not spent on text B, nor on text A in another folder.
    Status of this test: V6-06 already put the env prefix and the folder into the keyed fingerprints, so the prod text and the other folder were refused
    before V6-07 too (this part is a regression guard). The respaced text is the part that is red before V6-07: only plan_hash tells it apart."""
    staging = "KUBECONFIG=staging kubectl apply -f x.yaml"
    prod = "KUBECONFIG=prod kubectl apply -f x.yaml"
    ask_and_allow(cmd=staging)
    assert hooks.pretool(call(prod)).exit_code == 2
    respaced = hooks.pretool(call("KUBECONFIG=staging  kubectl  apply  -f x.yaml"))
    assert respaced.exit_code == 2 and "[jade:ask_" in respaced.stderr
    elsewhere = hooks.pretool(call(staging, cwd="/var"))
    assert elsewhere.exit_code == 2 and "[jade:ask_" in elsewhere.stderr
    statuses = {r["plan_hash"]: r["status"] for r in asks()}
    assert list(statuses.values()).count("ALLOWED") == 1 and "CONSUMED" not in statuses.values()
    assert hooks.pretool(call(staging)).exit_code == 0   # the same text in the same folder still uses it


def test_a_second_text_gets_its_own_question_not_the_open_one(level_high):
    first = token_of(hooks.pretool(call("KUBECONFIG=staging kubectl apply -f x.yaml")).stderr)
    second = token_of(hooks.pretool(call("KUBECONFIG=prod kubectl apply -f x.yaml")).stderr)
    assert first != second and len(asks()) == 2


def test_another_agent_cannot_use_the_grant_and_the_grant_stays(level_high):
    """N11: a subagent never takes the chat channel, so it cannot spend a grant the human gave the main session.
    Status: a regression guard, not red before V6-07. The grant lookup already had the session, and a subagent never takes the chat channel, so no earlier
    commit could fail this test. V6-07 does not change that part; the test pins it."""
    ask_and_allow()
    r = hooks.pretool(call(agent_id="a1b2c3", agent_type="general-purpose"))
    assert r.exit_code == 2 and "jade permit approve" in r.stderr
    assert [x["status"] for x in asks()] == ["ALLOWED"]


def test_another_session_cannot_use_the_grant_after_the_first_run_is_closed(level_high):
    """N11: the first run is closed (SI-07 no longer holds), so only the key can stop the other session.
    Status: a regression guard, not red before V6-07 (the session was already part of the grant lookup). It is the probe the V6-07 card asked for, with the
    first run closed, so SI-07 no longer hides the result."""
    ask_and_allow()
    assert hooks.pretool(call()).exit_code == 0
    hooks.posttool({**call(), "tool_response": {"stdout": "", "stderr": "", "interrupted": False}})
    other = hooks.pretool(call(session=OTHER))
    assert other.exit_code == 2 and "[jade:ask_" in other.stderr and "SI-07" not in other.stderr
    assert [x["status"] for x in asks()] == ["CONSUMED", "PENDING"]


def test_a_grant_row_without_the_exact_plan_hash_is_not_honoured(level_high):
    ask_and_allow()
    d = db()
    try:
        d.con.execute("UPDATE asks SET plan_hash=''")
    finally:
        d.close()
    r = hooks.pretool(call())
    assert r.exit_code == 2 and "[jade:ask_" in r.stderr


def test_the_question_says_what_the_grant_covers(level_high):
    q = shown(token_of(hooks.pretool(call()).stderr))
    assert "exactly this text" in q["question"] and "this folder" in q["question"] and "/tmp" in q["question"]


def bash_call(extra=None, cmd=CMD):
    tin = {"command": cmd, **(extra or {})}
    return {"tool_name": "Bash", "tool_input": tin, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"}


@pytest.mark.parametrize("extra", [{"dangerouslyDisableSandbox": True}, {"run_in_background": True}, {"timeout": 600000},
                                   {"dangerouslyDisableSandbox": True, "run_in_background": True}])
def test_an_allow_is_not_spent_on_the_same_text_run_another_way(level_high, extra):
    """V6-07 fix: the plan_hash covers the tool, the text and the folder only. The fields that change how a Bash call runs (sandbox off, background,
    time limit) are part of the call the human allowed."""
    first = hooks.pretool(bash_call())
    assert first.exit_code == 2
    hooks.posttool(answer_event(shown(token_of(first.stderr)), "Allow"))
    r = hooks.pretool(bash_call(extra))
    assert r.exit_code == 2 and "[jade:ask_" in r.stderr, r.stderr
    assert sorted(x["status"] for x in asks()) == ["ALLOWED", "PENDING"]   # the grant for the shown call is still unspent
    assert hooks.pretool(bash_call()).exit_code == 0                         # and it still works for the call as shown


def test_the_question_shows_the_fields_that_change_how_the_call_runs(level_high):
    r = hooks.pretool(bash_call({"dangerouslyDisableSandbox": True, "run_in_background": True}))
    q = shown(token_of(r.stderr))["question"]
    assert "dangerouslyDisableSandbox=True" in q and "run_in_background=True" in q
    plain = shown(token_of(hooks.pretool(bash_call(cmd="git reset --hard HEAD~2")).stderr))["question"]
    assert "dangerouslyDisableSandbox" not in plain


def test_the_same_extra_fields_use_the_grant_and_the_description_does_not_matter(level_high):
    extra = {"dangerouslyDisableSandbox": True}
    first = hooks.pretool(bash_call({**extra, "description": "list"}))
    hooks.posttool(answer_event(shown(token_of(first.stderr)), "Allow"))
    assert hooks.pretool(bash_call({**extra, "description": "something else", "run_in_background": False})).exit_code == 0   # description is display only; False is the default


def test_a_default_field_is_the_same_call(level_high):
    first = hooks.pretool(bash_call())
    hooks.posttool(answer_event(shown(token_of(first.stderr)), "Allow"))
    assert hooks.pretool(bash_call({"dangerouslyDisableSandbox": False, "description": "x"})).exit_code == 0


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
