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


# ------------------------------------------------------------------ V6-09: text is shown safely (N13, N14)

HIDDEN = "‮⁧​⁦‪\x1b\x07"   # right-to-left override, isolate, zero-width space, more bidi, ESC, BEL


def _assess(cmd):
    from jade import policy
    return policy.assess("Bash", {"command": cmd}, cwd="/tmp")


def _no_raw(text):
    from jade import util
    bad = [c for c in text if c != "\n" and util.show_safely(c) != c]
    assert not bad, [hex(ord(c)) for c in bad]


def test_n13_bidi_control_and_zero_width_characters_are_written_out_in_the_chat_question(level_high):
    q = shown(token_of(hooks.pretool(call("git reset --hard HEAD~1 # a" + HIDDEN + "b")).stderr))["question"]
    _no_raw(q)
    for esc in ("\\u202e", "\\u2067", "\\u200b", "\\x1b", "\\x07"):
        assert esc in q, esc


def test_n13_the_dialog_text_writes_them_out_too():
    from jade import askdialog
    cmd = "git reset --hard HEAD~1 # a" + HIDDEN + "b"
    text = askdialog.text_for(_assess(cmd), "Bash", {"subject": cmd, "cwd": "/tmp"})
    _no_raw(text)
    assert "\\u202e" in text


def test_n13_the_tool_name_and_the_folder_and_the_content_are_written_out():
    from jade import askchat
    q = askchat.question_for("0123456789ab", _assess(CMD), "Bash‮",
                             {"subject": CMD, "cwd": "/tmp/a⁧b", "content": {"length": 9, "sha256": "ab" * 32, "text": "x​y"}})["question"]
    _no_raw(q)
    assert "\\u202e" in q and "\\u2067" in q and "\\u200b" in q


def test_a_zero_width_non_joiner_is_kept_in_prose_and_written_out_elsewhere():
    from jade import util
    fa = "می‌خواهم"
    assert util.show_safely(fa, prose=True) == fa
    assert util.show_safely(fa) == "می\\u200cخواهم"
    assert util.show_safely("a‮b", prose=True) == "a\\u202eb"   # prose keeps only the half-space, not the overrides


def test_the_terminal_permit_screen_and_printable_use_the_same_function():
    from jade import permits, util
    s = "x‮y‌z\x1b\n\tw"
    assert permits.printable(s) == util.show_safely(s) == "x\\u202ey\\u200cz\\x1b\n\\x09w"
    assert util.show_safely("a\tb\n c", one_line=True) == "a b c"


def _middle_command():
    return "echo " + "a" * 500 + " ; git reset --hard HEAD~1 ; echo " + "b" * 500


def test_n14_a_risky_part_in_the_clipped_middle_is_shown_in_full(level_high):
    cmd = _middle_command()
    assert len(cmd) > 1000
    q = shown(token_of(hooks.pretool(call(cmd)).stderr))["question"]
    head, sep, rest = q.partition("characters not shown")
    assert sep and "git reset --hard HEAD~1" not in head   # it is in the hidden middle
    assert "git reset --hard HEAD~1" in rest and "not shown above" in rest   # and it is shown again, in full, with a line that says why


def test_n14_a_risky_part_that_is_too_long_or_cannot_be_found_says_to_read_it_elsewhere():
    from types import SimpleNamespace

    from jade import askchat
    a = SimpleNamespace(seg_keys=["git reset --hard HEAD~1"])
    out = askchat.command_text(a, "echo " + "a" * 600 + " ; echo " + "b" * 600)   # no piece has the key: the gate read the call some other way
    assert "cannot show which part" in out and "JADE rated this:\ngit reset --hard HEAD~1" in out and "Deny it" in out
    key = " ".join(["git", "reset", "--hard", "HEAD~1"] + ["-x"] * 1000)
    out = askchat.command_text(SimpleNamespace(seg_keys=[key]), "echo " + "a" * 500 + " ; " + key + " ; echo " + "b" * 500)
    assert "Deny it" in out and "not shown above" in out


def test_n14_a_command_too_big_to_search_says_to_read_it_elsewhere():
    from types import SimpleNamespace

    from jade import askchat
    out = askchat.command_text(SimpleNamespace(seg_keys=["git reset --hard HEAD~1"]), "echo " + "a" * 30000 + " ; git reset --hard HEAD~1")
    assert "cannot show which part" in out and "JADE rated this:" in out and "Deny it" in out and len(out) < 2000


def test_n14_a_visible_risky_part_adds_no_extra_block(level_high):
    q = shown(token_of(hooks.pretool(call("git reset --hard HEAD~1 ; echo " + "b" * 1000)).stderr))["question"]
    assert "characters not shown" in q and "not shown above" not in q and "cannot show which part" not in q


def test_n14_the_dialog_shows_the_hidden_risky_part_too():
    from jade import askdialog
    cmd = _middle_command()
    text = askdialog.text_for(_assess(cmd), "Bash", {"subject": cmd, "cwd": "/tmp"})
    assert "not shown above" in text and text.count("git reset --hard HEAD~1") == 1


def test_a_short_command_is_shown_as_it_is(level_high):
    q = shown(token_of(hooks.pretool(call()).stderr))["question"]
    assert "Command:\n" + CMD + "\n" in q


def _risky_part_is_not_lost(cmd, key_word):
    """Through the real gate: the question for a long call never leaves a rated command out. Either the command is shown again in full, or the question says it cannot find it
    and prints the gate's own reading of it."""
    from jade import askchat
    a = _assess(cmd)
    assert a.seg_keys and len(cmd) > 1000, a.seg_keys
    out = askchat.command_text(a, cmd)
    assert "characters not shown" in out
    tail = out.partition("characters not shown")[2]
    assert ("not shown above" in tail and key_word in tail) or ("cannot show which part" in tail and key_word in tail), tail
    return tail


def test_n14_through_the_real_gate_a_quoted_separator_inside_the_risky_part():
    cmd = "echo " + "a" * 500 + ' ; rm -rf "/tmp/x; y/z" ; echo ' + "b" * 500
    _risky_part_is_not_lost(cmd, "rm -rf")


def test_n14_through_the_real_gate_a_backslash_line_break_inside_the_risky_part():
    cmd = "echo " + "a" * 500 + " ; git reset \\\n--hard HEAD~1 ; echo " + "b" * 500
    _risky_part_is_not_lost(cmd, "git reset")


def test_n14_through_the_real_gate_a_here_document_before_the_risky_part():
    cmd = "cat <<'EOF' > /tmp/jade-note.txt\n" + ("line of text\n" * 80) + "EOF\ngit reset --hard HEAD~1\n" + "echo " + "b" * 500
    _risky_part_is_not_lost(cmd, "git reset")


def test_n14_through_the_real_gate_a_plain_risky_part_is_found_and_shown_once():
    tail = _risky_part_is_not_lost("echo " + "a" * 500 + " ; git reset --hard HEAD~1 ; echo " + "b" * 500, "git reset --hard HEAD~1")
    assert "not shown above" in tail and "cannot show which part" not in tail


def test_the_scope_options_and_lines_have_no_raw_bidi_characters_when_the_folder_name_has_them(tmp_path):
    from jade import askchat, scopes
    root = tmp_path / "proj\u202ex\u2067y"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text('[core]\n\tbare = false\n[remote "origin"]\n\turl = https://github.com/owner/repoA.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    merge = "gh pr merge 940 --repo github.com/owner/repoA --squash"
    a = _assess(merge)
    offered = scopes.offered(a, "Bash", {"command": merge}, {}, str(root))
    assert offered, "the test folder should be offered a scope"
    q = askchat.question_for("0123456789ab", a, "Bash", {"subject": merge, "cwd": str(root)}, offered)
    _no_raw(q["question"])
    for o in q["options"]:
        _no_raw(o["label"] + o["description"])


# ------------------------------------------------------------------ V6-09 review fixes: invisible look-alikes, a rated command hidden in a nested form

LOOKALIKES = ["ㅤ", "ᅟ", "ᅠ", "ﾠ", "⠀", "͏", "᠋", "᠏", "︀", "️", "\U000e0100", "឴", "̸"]


@pytest.mark.parametrize("ch", LOOKALIKES)
def test_invisible_letters_symbols_and_marks_are_written_out_in_a_command_or_path(ch):
    from jade import util
    assert ch not in util.show_safely(f"/Users/me/project{ch}")
    assert ch not in util.show_safely(f"a{ch}b")


@pytest.mark.parametrize("ch", ["ㅤ", "ᅟ", "ᅠ", "ﾠ", "⠀"])
def test_a_blank_filler_letter_is_written_out_even_in_prose(ch):
    from jade import util
    assert ch not in util.show_safely(f"a{ch}b", prose=True)


def test_persian_marks_and_the_half_space_survive_where_they_are_needed():
    from jade import util
    word = "مَن‌خواهم"   # a vowel mark on an Arabic letter, then the half-space
    assert util.show_safely(word, prose=True) == word
    assert util.show_safely("مَن") == "مَن"   # a mark after a non-ASCII letter is part of the word, in a path too
    assert util.show_safely("xّ", prose=True) == "xّ"   # prose keeps marks


def test_the_gate_writes_out_an_invisible_filler_in_the_folder_and_the_command(level_high):
    cmd = "cd /Users/me/projectㅤ && git reset --hard HEAD~1"
    q = shown(token_of(hooks.pretool(call(cmd, cwd="/Users/me/project⠀")).stderr))["question"]
    _no_raw(q)
    assert "\\u3164" in q


def test_the_question_for_a_write_writes_out_an_invisible_path_and_content():
    from jade import askchat
    q = askchat.question_for("0123456789ab", _assess(CMD), "Write᠋",
                             {"subject": "/tmp/aㅤb", "cwd": "/tmp/x️y", "content": {"length": 9, "sha256": "ab" * 32, "text": "xᅟy͏"}})["question"]
    _no_raw(q)


def _nested_tail(cmd):
    from jade import askchat
    a = _assess(cmd)
    assert a.seg_keys and len(cmd) > 1000, (a.seg_keys, len(cmd))
    out = askchat.command_text(a, cmd)
    assert "characters not shown" in out
    return a, out.partition("characters not shown")[2]


PAD = "echo " + "x" * 400


def test_a_rated_command_in_a_nested_form_is_shown_when_a_visible_command_has_the_same_key():
    cmd = f"kubectl apply -f a.yaml ; {PAD} ; {PAD} ; bash -c 'KUBECONFIG=/prod.yaml kubectl apply -f a.yaml' ; {PAD} ; {PAD}"
    a, tail = _nested_tail(cmd)
    assert a.seg_keys == ["kubectl apply -f a.yaml"]
    assert "KUBECONFIG=/prod.yaml kubectl apply -f a.yaml" in tail and "not shown above" in tail, tail


def test_a_nested_form_with_a_changed_folder_is_shown_with_its_cd():
    cmd = f"git reset --hard HEAD~1 ; {PAD} ; {PAD} ; echo $(cd /prod; git reset --hard HEAD~1) ; {PAD} ; {PAD}"
    _, tail = _nested_tail(cmd)
    assert "echo $(cd /prod; git reset --hard HEAD~1)" in tail and "not shown above" in tail, tail
    assert "in full:\ngit reset --hard HEAD~1" not in tail   # the old text: the part alone, as if it were the whole call


def test_a_top_level_cd_before_a_hidden_rated_part_is_shown_with_it():
    cmd = f"git reset --hard HEAD~1 ; {PAD} ; {PAD} ; cd /prod && git reset --hard HEAD~1 ; {PAD} ; {PAD}"
    _, tail = _nested_tail(cmd)
    assert "cd /prod && git reset --hard HEAD~1" in tail and "not shown above" in tail, tail


def test_a_harmless_nested_form_in_the_hidden_middle_adds_no_warning():
    cmd = f"git reset --hard HEAD~1 ; {PAD} ; {PAD} ; echo $(date) ; bash -c 'echo hello' ; {PAD} ; {PAD}"
    _, tail = _nested_tail(cmd)
    assert "cannot show which part" not in tail and "not shown above" not in tail, tail


def test_a_nested_rated_command_behind_a_quoted_separator_is_shown_whole():
    cmd = f"git reset --hard HEAD~1 ; {PAD} ; {PAD} ; bash -c 'cd /prod; git reset --hard HEAD~1' ; {PAD} ; {PAD}"
    _, tail = _nested_tail(cmd)
    assert "bash -c 'cd /prod; git reset --hard HEAD~1'" in tail and "not shown above" in tail, tail


def test_more_nested_statements_than_the_cap_say_so_and_stay_fast():
    import time
    from types import SimpleNamespace

    from jade import askchat
    nested = " ; ".join(f"bash -c 'echo {i} > /tmp/f{i}'" for i in range(askchat.ASSESS_CAP + 20))
    cmd = f"git reset --hard HEAD~1 ; {PAD} ; {PAD} ; {nested} ; {PAD}"
    t0 = time.monotonic()
    out = askchat.command_text(SimpleNamespace(seg_keys=["git reset --hard HEAD~1"]), cmd)
    assert "cannot show which part" in out and time.monotonic() - t0 < 5
