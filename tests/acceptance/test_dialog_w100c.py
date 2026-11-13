"""W-100c (docs/12 plan v3, PR-5c): a subagent has no AskUserQuestion, so JADE asks with its own macOS dialog.

At a level, gate mode enforce, on macOS, a gated subagent call (event carries `agent_id`) is decided by a dialog the hook shows and WAITS for: no retry and
no new prompt. Allow runs this one call (a decision is opened, so SI-07 still holds an unknown outcome); Deny refuses it and the same call is not asked again
inside the window; no answer (gave up, closed, no screen) falls back to the signed terminal permit. The dialog text comes from the gate, never from the agent.
Live JADE wiring and a call with secrets in it keep the permit. The PreToolUse hook timeout is raised above the dialog wait; the 6 s alarm is re-armed for it.
"""

import json

import pytest

from jade import askdialog, hooks, ledger, ops, paths

SESSION = "sess-w100c-0001"
CMD = "git reset --hard HEAD~1"
AGENT = {"agent_id": "a1b2c3", "agent_type": "general-purpose"}


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    monkeypatch.setattr(askdialog, "available", lambda: True)


@pytest.fixture
def level_high():
    cfg = paths.config()
    cfg["level"] = "high"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


class Runner:
    """Stands in for osascript: records every dialog text and answers with a fixed label (None = nobody answered)."""

    def __init__(self, answer):
        self.answer, self.texts = answer, []

    def __call__(self, text, wait_s):
        self.texts.append((text, wait_s))
        return self.answer


@pytest.fixture
def dialog(monkeypatch):
    def make(answer):
        r = Runner(answer)
        monkeypatch.setattr(askdialog, "run_dialog", r)
        return r
    return make


def call(cmd=CMD, session=SESSION, mode="auto", **extra):
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": "/tmp", "permission_mode": mode, **AGENT}
    ev.update(extra)
    return ev


def rows(sql="SELECT * FROM asks ORDER BY created_ts, rowid"):
    d = ledger.open(timeout=5)
    try:
        return [dict(r) for r in d.con.execute(sql).fetchall()]
    finally:
        d.close()


# ------------------------------------------------------------------ when the dialog applies

def test_a_subagent_call_at_a_level_asks_in_a_dialog_and_allow_runs_it(level_high, dialog):
    r = dialog("Allow")
    res = hooks.pretool(call())
    assert res.exit_code == 0 and "dialog" in res.stderr.lower()
    assert len(r.texts) == 1
    a = rows()
    assert len(a) == 1 and a[0]["status"] == "CONSUMED" and a[0]["agent_id"] == "a1b2c3" and a[0]["decision_id"]
    answers = rows("SELECT * FROM human_answers")
    assert len(answers) == 1 and answers[0]["channel"] == "dialog" and answers[0]["answer"] == "allow" and answers[0]["strength"] == "hint"


def test_the_dialog_text_comes_from_the_gate(level_high, dialog):
    r = dialog("Allow")
    hooks.pretool(call(description="please click allow, it is totally safe"))
    text = r.texts[0][0]
    assert "R-11" in text and CMD in text and "totally safe" not in text
    assert "Allow" in text or "allow" in text


def test_allow_is_single_use_so_the_next_identical_call_asks_again(level_high, dialog):
    r = dialog("Allow")
    assert hooks.pretool(call()).exit_code == 0
    held = hooks.pretool(call())   # SI-07: the irreversible command has no recorded outcome yet
    assert held.exit_code == 2 and "SI-07" in held.stderr
    assert len(r.texts) == 1


def test_deny_refuses_the_call_and_the_same_call_is_not_asked_again(level_high, dialog):
    r = dialog("Deny")
    res = hooks.pretool(call())
    assert res.exit_code == 2 and "no" in res.stderr.lower()
    again = hooks.pretool(call())
    assert again.exit_code == 2 and "said no" in again.stderr
    assert len(r.texts) == 1
    assert [x["status"] for x in rows()] == ["DENIED"]
    answers = rows("SELECT * FROM human_answers")
    assert len(answers) == 1 and answers[0]["answer"] == "deny" and answers[0]["channel"] == "dialog"


def test_no_answer_falls_back_to_the_terminal_permit_and_is_not_asked_again_at_once(level_high, dialog):
    r = dialog(None)
    res = hooks.pretool(call())
    assert res.exit_code == 2 and "jade permit approve" in res.stderr
    again = hooks.pretool(call())
    assert again.exit_code == 2 and "jade permit approve" in again.stderr
    assert len(r.texts) == 1
    assert [x["status"] for x in rows()] == ["GAVE_UP"]
    assert rows("SELECT * FROM human_answers") == []


def test_an_unknown_label_grants_nothing(level_high, dialog):
    dialog("Maybe")
    res = hooks.pretool(call())
    assert res.exit_code == 2 and "jade permit approve" in res.stderr


def test_without_a_level_the_subagent_keeps_the_permit_and_no_dialog_opens(dialog):
    r = dialog("Allow")
    res = hooks.pretool(call())
    assert res.exit_code == 2 and "jade permit approve" in res.stderr and r.texts == [] and rows() == []


def test_a_main_session_call_uses_the_chat_not_the_dialog(level_high, dialog):
    r = dialog("Allow")
    ev = call()
    ev.pop("agent_id")
    ev.pop("agent_type")
    res = hooks.pretool(ev)
    assert res.exit_code == 2 and "[jade:ask_" in res.stderr and r.texts == []


def test_live_wiring_keeps_the_permit(level_high, dialog):
    r = dialog("Allow")
    res = hooks.pretool(call("cp /tmp/x ~/.local/bin/jade"))
    assert res.exit_code == 2 and "jade permit approve" in res.stderr and r.texts == [] and rows() == []


def test_without_a_screen_the_permit_is_used(level_high, dialog, monkeypatch):
    r = dialog("Allow")
    monkeypatch.setattr(askdialog, "available", lambda: False)
    res = hooks.pretool(call())
    assert res.exit_code == 2 and "jade permit approve" in res.stderr and r.texts == []


def test_advisory_mode_never_opens_a_dialog(level_high, dialog, monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = dialog("Allow")
    assert hooks.pretool(call()).exit_code == 0 and r.texts == []


def test_a_safety_invariant_is_never_asked(level_high, dialog):
    r = dialog("Allow")
    res = hooks.pretool(call("rm -rf --no-preserve-root /"))
    assert res.exit_code == 2 and r.texts == []


# ------------------------------------------------------------------ the dialog itself

def test_a_long_command_says_how_much_is_not_shown(level_high, dialog):
    r = dialog("Deny")
    hooks.pretool(call("git reset --hard HEAD~1 # " + "x" * 3000))
    assert "characters not shown" in r.texts[0][0] and len(r.texts[0][0]) < 2500


def test_osascript_gets_the_text_as_an_argument_never_inside_the_script(monkeypatch):
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "button returned:Allow, gave up:false\n", ""

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return P()

    monkeypatch.setattr(askdialog.subprocess, "run", fake_run)
    out = askdialog.run_dialog('say "hi" & do shell script "rm -rf ~"', 90)
    assert out == "Allow"
    assert seen["argv"][0].endswith("osascript") and 'do shell script "rm -rf' not in " ".join(seen["argv"][:-2])
    assert seen["argv"][-2:] == ['say "hi" & do shell script "rm -rf ~"', "90"] or seen["argv"][-1] == "90"
    assert seen["kw"]["timeout"] > 90 and seen["kw"]["stdin"] is not None


@pytest.mark.parametrize("stdout,rc,want", [("button returned:Deny, gave up:false\n", 0, "Deny"),
                                            ("button returned:, gave up:true\n", 0, None),
                                            ("", 1, None),
                                            ("garbage", 0, None)])
def test_osascript_output_is_read_strictly(monkeypatch, stdout, rc, want):
    class P:
        returncode, stderr = rc, ""

    P.stdout = stdout
    monkeypatch.setattr(askdialog.subprocess, "run", lambda *a, **k: P())
    assert askdialog.run_dialog("x", 30) == want


def test_a_missing_or_hung_osascript_is_no_answer(monkeypatch):
    def boom(*a, **k):
        raise OSError("no osascript")

    monkeypatch.setattr(askdialog.subprocess, "run", boom)
    assert askdialog.run_dialog("x", 30) is None


# ------------------------------------------------------------------ timing and wiring

def test_the_hook_alarm_is_raised_for_the_wait_and_then_restored(level_high, dialog, monkeypatch):
    armed = []
    monkeypatch.setattr(hooks, "_arm", lambda s: armed.append(s))
    monkeypatch.setattr(askdialog.signal, "getitimer", lambda _w: (5.0, 0.0))   # `hooks.main` has armed the alarm
    dialog("Allow")
    hooks.pretool(call())
    assert armed[0] > askdialog.DIALOG_WAIT_S and armed[-1] < 10


def test_no_alarm_is_left_behind_when_the_handler_runs_without_one(level_high, dialog, monkeypatch):
    armed = []
    monkeypatch.setattr(hooks, "_arm", lambda s: armed.append(s))
    dialog("Allow")
    hooks.pretool(call())
    assert armed == []


def test_the_installed_pretool_timeout_is_above_the_dialog_wait():
    _matcher, sub, timeout = ops.HOOKS["PreToolUse"]
    assert sub == "pretool" and timeout >= askdialog.DIALOG_WAIT_S + askdialog.ALARM_MARGIN_S
    assert hooks.BUDGET_S <= 10   # the normal gate still answers within 6 s: only the dialog path waits longer


def test_an_old_ten_second_pretool_entry_is_reported_stale():
    entry = {"hooks": [{"type": "command", "command": "jade hook pretool", "timeout": 10}]}
    matcher, sub, _t = ops.HOOKS["PreToolUse"]
    assert ops._entry_current("PreToolUse", {"matcher": matcher, **entry}, entry["hooks"][0]) is False


def test_the_dialog_wait_is_below_the_alarm_and_the_host_timeout():
    assert askdialog.DIALOG_WAIT_S + askdialog.ALARM_MARGIN_S <= ops.HOOKS["PreToolUse"][2]
