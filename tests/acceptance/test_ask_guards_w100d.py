"""W-100d: what keeps the chat and dialog channels from being worn down or used against the human.

- Ask caps: the 3rd Deny of the same call is a plain refusal (no new ask); 30 asks that expired unanswered pause asking for the session until the human answers.
- GUI pause: while an ask of the session is open (chat question not answered, dialog on screen) the computer-use tools of that session are refused, so the agent
  cannot press Allow itself.
- Stop nudge: an ask that never reached the question is pointed out once when the agent tries to stop.
- Notification: opening an ask posts one macOS notification written by the hook (rule and project folder, never the command).
"""

import json
import re
import uuid

import pytest

from jade import askchat, askdialog, hooks, ledger, paths
from jade.util import ts

SESSION = "sess-w100d-guard-1"
OTHER = "sess-w100d-guard-2"
CMD = "git reset --hard HEAD~1"
GUI = "mcp__computer-use__left_click"
REAL_NOTIFY = askdialog.notify   # the autouse fixture stubs `askdialog.notify`; the notify tests call the real one


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    cfg = paths.config()
    cfg["level"] = "high"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(askdialog, "notify", lambda text: None)


def call(cmd=CMD, session=SESSION, **extra):
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": "/tmp/myproject", "permission_mode": "auto"}
    ev.update(extra)
    return ev


def gui(session=SESSION):
    return {"tool_name": GUI, "tool_input": {"coordinate": [1, 2]}, "session_id": session, "cwd": "/tmp", "permission_mode": "auto"}


def rows(sql="SELECT * FROM asks ORDER BY created_ts, rowid"):
    d = ledger.open(timeout=5)
    try:
        return [dict(r) for r in d.con.execute(sql).fetchall()]
    finally:
        d.close()


def exec_sql(sql, *args):
    d = ledger.open(timeout=5)
    try:
        d.con.execute(sql, args)
    finally:
        d.close()


def token_of(text):
    return re.search(r"\[jade:ask_([0-9a-f]{12})\]", text).group(0)


def shown(token, session=SESSION):
    q = {"question": f"{token} x", "header": "x", "multiSelect": False, "options": [{"label": "Allow", "description": ""}, {"label": "Deny", "description": ""}]}
    r = hooks.pretool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}, "session_id": session, "cwd": "/tmp", "permission_mode": "auto"})
    return json.loads(r.stdout)["hookSpecificOutput"]["updatedInput"]["questions"][0]


def answer(question, label, session=SESSION):
    hooks.posttool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [question]}, "session_id": session, "cwd": "/tmp", "permission_mode": "auto",
                    "tool_response": {"questions": [question], "answers": {question["question"]: label}}})


def deny_once(cmd=CMD):
    first = hooks.pretool(call(cmd))
    answer(shown(token_of(first.stderr)), "Deny")


def add_ask(status, session=SESSION, age_s=0, answered=None, hold="h-fixed"):
    from datetime import timedelta

    from jade.util import now

    created = ts(now() - timedelta(seconds=age_s))
    exec_sql("INSERT INTO asks(ask_id, session_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, answered_ts, expires_ts) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
             uuid.uuid4().hex[:12], session, "p", hold, "{}", "R-11", "high", "Bash", "{}", status, created, answered, ts(now() + timedelta(seconds=300)))


# ------------------------------------------------------------------ ask caps

def test_the_third_deny_of_one_call_is_a_plain_refusal_and_opens_no_ask():
    for _ in range(3):
        deny_once()
        exec_sql("UPDATE asks SET expires_ts='2000-01-01T00:00:00Z' WHERE status='DENIED'")   # the 10 minute window ends
    before = len(rows())
    r = hooks.pretool(call())
    assert r.exit_code == 2 and "3 times" in r.stderr and "[jade:ask_" not in r.stderr and "jade permit approve" not in r.stderr
    assert len(rows()) == before


def test_two_denies_still_allow_a_new_ask():
    for _ in range(2):
        deny_once()
        exec_sql("UPDATE asks SET expires_ts='2000-01-01T00:00:00Z' WHERE status='DENIED'")
    assert "[jade:ask_" in hooks.pretool(call()).stderr


def test_denies_of_another_command_do_not_count():
    for _ in range(3):
        deny_once("git reset --hard HEAD~2")
        exec_sql("UPDATE asks SET expires_ts='2000-01-01T00:00:00Z' WHERE status='DENIED'")
    assert "[jade:ask_" in hooks.pretool(call()).stderr


def test_thirty_expired_asks_pause_asking_for_the_session():
    for _ in range(30):
        add_ask("EXPIRED")
    r = hooks.pretool(call())
    assert r.exit_code == 2 and "away" in r.stderr and "[jade:ask_" not in r.stderr and "jade permit approve" not in r.stderr
    assert len([x for x in rows() if x["status"] == "PENDING"]) == 0


def test_twenty_nine_expired_asks_do_not_pause():
    for _ in range(29):
        add_ask("EXPIRED")
    assert "[jade:ask_" in hooks.pretool(call()).stderr


def test_an_answer_ends_the_pause():
    for _ in range(30):
        add_ask("EXPIRED", age_s=60)
    add_ask("ALLOWED", age_s=5, answered=ts())
    assert "[jade:ask_" in hooks.pretool(call()).stderr


def test_the_pause_is_per_session():
    for _ in range(30):
        add_ask("EXPIRED")
    assert "[jade:ask_" in hooks.pretool(call(session=OTHER)).stderr


def test_the_dialog_honours_the_deny_cap(monkeypatch):
    monkeypatch.setattr(askdialog, "available", lambda: True)
    shown_texts = []
    monkeypatch.setattr(askdialog, "run_dialog", lambda text, wait: shown_texts.append(text) or "Deny")
    ev = call(agent_id="a1", agent_type="general-purpose")
    for _ in range(3):
        hooks.pretool(ev)
        exec_sql("UPDATE asks SET expires_ts='2000-01-01T00:00:00Z' WHERE status='DENIED'")
    n = len(shown_texts)
    r = hooks.pretool(ev)
    assert r.exit_code == 2 and "3 times" in r.stderr and len(shown_texts) == n == 3


# ------------------------------------------------------------------ GUI pause

def test_gui_tools_are_paused_while_a_chat_ask_is_open():
    hooks.pretool(call())
    r = hooks.pretool(gui())
    assert r.exit_code == 2 and "paused" in r.stderr


def test_gui_tools_are_paused_after_the_question_is_shown_and_free_after_the_answer():
    first = hooks.pretool(call())
    q = shown(token_of(first.stderr))
    assert "paused" in hooks.pretool(gui()).stderr
    answer(q, "Allow")
    assert "paused" not in hooks.pretool(gui()).stderr


def test_gui_tools_are_free_with_no_open_ask():
    assert "paused" not in hooks.pretool(gui()).stderr


def test_another_sessions_gui_tools_are_free():
    hooks.pretool(call())
    assert "paused" not in hooks.pretool(gui(OTHER)).stderr


def test_an_expired_ask_no_longer_pauses_gui_tools():
    hooks.pretool(call())
    exec_sql("UPDATE asks SET expires_ts='2000-01-01T00:00:00Z'")
    assert "paused" not in hooks.pretool(gui()).stderr


def test_without_a_level_nothing_is_paused():
    hooks.pretool(call())
    cfg = paths.config()
    cfg.pop("level", None)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))
    assert "paused" not in hooks.pretool(gui()).stderr


def test_other_mcp_tools_are_not_paused():
    hooks.pretool(call())
    ev = {"tool_name": "mcp__context7__query-docs", "tool_input": {}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"}
    assert "paused" not in hooks.pretool(ev).stderr


def test_gui_tools_are_paused_while_a_dialog_is_on_screen(monkeypatch):
    monkeypatch.setattr(askdialog, "available", lambda: True)
    seen = {}

    def fake(text, wait):
        seen["status"] = [r["status"] for r in rows()]
        seen["gui"] = hooks.pretool(gui()).stderr
        return "Deny"

    monkeypatch.setattr(askdialog, "run_dialog", fake)
    hooks.pretool(call(agent_id="a1", agent_type="general-purpose"))
    assert seen["status"] == ["DIALOG"] and "paused" in seen["gui"]
    assert "paused" not in hooks.pretool(gui()).stderr


# ------------------------------------------------------------------ Stop nudge

def stop_event(**kw):
    return {"session_id": SESSION, "cwd": "/tmp", **kw}


def test_stop_points_at_an_ask_that_never_reached_the_question_once():
    token = token_of(hooks.pretool(call()).stderr)
    r = hooks.stop(stop_event())
    assert r.exit_code == 2 and token in r.stderr and "AskUserQuestion" in r.stderr
    assert hooks.stop(stop_event()).exit_code == 0


def test_stop_does_nothing_when_the_question_was_shown():
    q = shown(token_of(hooks.pretool(call()).stderr))
    assert q
    assert hooks.stop(stop_event()).exit_code == 0


def test_stop_does_nothing_inside_a_stop_hook_loop():
    hooks.pretool(call())
    assert hooks.stop(stop_event(stop_hook_active=True)).exit_code == 0


def test_stop_ignores_another_sessions_ask_and_an_expired_one():
    hooks.pretool(call(session=OTHER))
    assert hooks.stop(stop_event()).exit_code == 0
    hooks.pretool(call())
    exec_sql("UPDATE asks SET expires_ts='2000-01-01T00:00:00Z'")
    assert hooks.stop(stop_event()).exit_code == 0


# ------------------------------------------------------------------ notification

def test_a_new_ask_posts_one_notification_without_the_command(monkeypatch):
    sent = []
    monkeypatch.setattr(askdialog, "notify", lambda text: sent.append(text))
    hooks.pretool(call())
    hooks.pretool(call())
    assert len(sent) == 1
    assert "R-11" in sent[0] and "myproject" in sent[0] and "reset" not in sent[0] and "--hard" not in sent[0]


def test_the_project_name_in_the_notification_is_plain_text(monkeypatch):
    sent = []
    monkeypatch.setattr(askdialog, "notify", lambda text: sent.append(text))
    hooks.pretool(call(cwd='/tmp/x"; do shell script "rm -rf ~"'))
    assert len(sent) == 1 and '"' not in sent[0] and ";" not in sent[0]


def test_notify_passes_the_text_as_an_argument(monkeypatch):
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"] = argv

        class P:
            pass

        return P()

    monkeypatch.setattr(askdialog, "available", lambda: True)
    monkeypatch.setattr(askdialog.subprocess, "Popen", fake_popen)
    REAL_NOTIFY('hello "x"')
    assert seen["argv"][0] == askdialog.OSASCRIPT and seen["argv"][-1] == 'hello "x"' and 'hello "x"' not in " ".join(seen["argv"][:-1])


def test_notify_swallows_errors_and_does_nothing_without_a_screen(monkeypatch):
    monkeypatch.setattr(askdialog, "available", lambda: True)

    def boom(*a, **k):
        raise OSError("no osascript")

    monkeypatch.setattr(askdialog.subprocess, "Popen", boom)
    REAL_NOTIFY("x")
    monkeypatch.setattr(askdialog, "available", lambda: False)
    monkeypatch.setattr(askdialog.subprocess, "Popen", lambda *a, **k: pytest.fail("no screen: nothing may run"))
    REAL_NOTIFY("x")


def test_the_caps_are_what_the_plan_says():
    assert askchat.DENY_CAP == 3 and askchat.EXPIRE_PAUSE == 30
