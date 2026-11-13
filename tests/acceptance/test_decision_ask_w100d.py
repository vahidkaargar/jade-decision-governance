"""W-100d: what a decision records about a human answer (docs/02, docs/12).

A call the human allowed in the chat or in a dialog is a decision with route ASK and an `ask` block (channel, ask id, the Allow answer). When the gate named
human-only classes the decision says so (`human_only`, `ho_ids`). The state machine refuses to accept an ASK decision without that recorded Allow: the
authority to accept comes from the ask, never from a bare event.
"""

import json
import re
from pathlib import Path

import pytest
from jsonschema import ValidationError

from jade import askdialog, decide, hooks, ledger, paths, schemas, state_machine

SESSION = "sess-w100d-dec-0001"
CMD = "git reset --hard HEAD~1"


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    cfg = paths.config()
    cfg["level"] = "high"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


def chat_allow(event):
    first = hooks.pretool(event)
    token = re.search(r"\[jade:ask_[0-9a-f]{12}\]", first.stderr).group(0)
    q = {"question": f"{token} x", "header": "x", "multiSelect": False, "options": [{"label": "Allow", "description": ""}, {"label": "Deny", "description": ""}]}
    base = {"session_id": event["session_id"], "cwd": "/tmp", "permission_mode": "auto"}
    shown = json.loads(hooks.pretool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}, **base}).stdout)["hookSpecificOutput"]["updatedInput"]["questions"][0]
    hooks.posttool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [shown]}, "tool_response": {"questions": [shown], "answers": {shown["question"]: "Allow"}}, **base})
    assert hooks.pretool(event).exit_code == 0


def decisions():
    d = ledger.open(timeout=5)
    try:
        return [json.loads(r["doc_json"]) for r in d.con.execute("SELECT doc_json FROM decisions ORDER BY created_at").fetchall()]
    finally:
        d.close()


def bash(cmd=CMD, **kw):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto", **kw}


def test_a_chat_allowed_call_is_a_route_ask_decision_with_the_answer():
    chat_allow(bash())
    (doc,) = decisions()
    assert doc["route"] == "ASK" and doc["status"] == "EXECUTING"
    ask = doc["ask"]
    assert ask["channel"] == "chat" and ask["answer"] == "allow" and re.fullmatch(r"[0-9a-f]{12}", ask["ask_id"]) and ask["answered_at"]
    assert "human_only" not in doc and "ho_ids" not in doc
    schemas.validate("decision", doc)


def test_a_human_only_class_is_named_on_the_decision():
    mod = str(Path.home() / ".claude" / "dev-mods" / "m.js")
    ev = {"tool_name": "Write", "tool_input": {"file_path": mod, "content": "x"}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"}
    chat_allow(ev)
    (doc,) = decisions()
    assert doc["human_only"] is True and doc["ho_ids"] == ["HO-mod"] and doc["ask"]["channel"] == "chat"
    schemas.validate("decision", doc)


def test_a_dialog_allowed_call_records_the_dialog_channel(monkeypatch):
    monkeypatch.setattr(askdialog, "available", lambda: True)
    monkeypatch.setattr(askdialog, "run_dialog", lambda text, wait: "Allow")
    assert hooks.pretool(bash(agent_id="a1", agent_type="general-purpose")).exit_code == 0
    (doc,) = decisions()
    assert doc["route"] == "ASK" and doc["ask"]["channel"] == "dialog" and doc["ask"]["answer"] == "allow"


def test_the_ask_id_of_the_decision_is_the_ask_row():
    chat_allow(bash())
    d = ledger.open(timeout=5)
    try:
        row = d.con.execute("SELECT ask_id, decision_id FROM asks").fetchone()
    finally:
        d.close()
    (doc,) = decisions()
    assert row["ask_id"] == doc["ask"]["ask_id"] and row["decision_id"] == doc["decision_id"]


def test_a_human_only_class_is_raised_to_high_only_when_a_level_is_set(monkeypatch):
    mod = str(Path.home() / ".claude" / "dev-mods" / "m.js")
    ev = {"tool_name": "Write", "tool_input": {"file_path": mod, "content": "x"}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"}
    assert "[jade:ask_" in hooks.pretool(ev).stderr
    for level in ("off", None):
        cfg = paths.config()
        if level is None:
            cfg.pop("level", None)
        else:
            cfg["level"] = level
        (paths.config_dir() / "config.json").write_text(json.dumps(cfg))
        r = hooks.pretool(ev)
        assert r.exit_code == 0 and "[jade:ask_" not in r.stderr, level


# ------------------------------------------------------------------ the contract

def example():
    return json.loads(next((Path(paths.schema_dir()) / "examples").glob("decision*.json")).read_text())


def test_the_schema_knows_route_ask_and_the_new_fields():
    doc = example()
    doc["route"] = "ASK"
    doc["ask"] = {"channel": "chat", "ask_id": "0123456789ab", "answer": "allow", "answered_at": "2026-11-13T10:00:00Z"}
    doc["human_only"], doc["ho_ids"] = True, ["HO-mod"]
    schemas.validate("decision", doc)


@pytest.mark.parametrize("bad", [{"channel": "sms", "ask_id": "0123456789ab", "answer": "allow", "answered_at": "2026-11-13T10:00:00Z"},
                                 {"channel": "chat", "ask_id": "0123456789ab", "answer": "maybe", "answered_at": "2026-11-13T10:00:00Z"},
                                 {"channel": "chat", "answer": "allow", "answered_at": "2026-11-13T10:00:00Z"},
                                 {"channel": "chat", "ask_id": "0123456789ab", "answer": "allow", "answered_at": "2026-11-13T10:00:00Z", "extra": 1}])
def test_a_bad_ask_block_is_refused(bad):
    doc = example()
    doc["ask"] = bad
    with pytest.raises(ValidationError):
        schemas.validate("decision", doc)


def test_an_old_decision_without_the_new_fields_still_validates():
    schemas.validate("decision", example())


# ------------------------------------------------------------------ authority

def asked_decision():
    db = ledger.open(timeout=5)
    doc = decide.open_decision(db, "Execute Bash: thing to run", command=CMD, session=SESSION, route="ASK")
    did = doc["decision_id"]
    decide.step(db, did, "route", guard="route=ASK")
    decide.step(db, did, "escalate", guard="ask")
    return db, did


def test_an_ask_decision_is_not_accepted_without_the_recorded_allow():
    db, did = asked_decision()
    try:
        with pytest.raises(state_machine.IllegalTransition, match="ASK"):
            decide.step(db, did, "human_accept", actor="human")
        with pytest.raises(state_machine.IllegalTransition, match="ASK"):
            decide.step(db, did, "human_accept", actor="human", patch={"ask": {"channel": "chat", "ask_id": "0123456789ab", "answer": "deny", "answered_at": "2026-11-13T10:00:00Z"}})
        with pytest.raises(state_machine.IllegalTransition, match="ASK"):
            decide.step(db, did, "human_accept", actor="human", patch={"ask": {"channel": "terminal", "ask_id": "0123456789ab", "answer": "allow", "answered_at": "2026-11-13T10:00:00Z"}})
        res = decide.step(db, did, "human_accept", actor="human", patch={"ask": {"channel": "dialog", "ask_id": "0123456789ab", "answer": "allow", "answered_at": "2026-11-13T10:00:00Z"}})
        assert res.status == "ACCEPTED"
    finally:
        db.close()


def test_other_routes_accept_as_before():
    db = ledger.open(timeout=5)
    try:
        doc = decide.open_decision(db, "Execute Bash: thing to run", command=CMD, session=SESSION, route="ESCALATE")
        did = doc["decision_id"]
        decide.step(db, did, "route")
        decide.step(db, did, "escalate")
        assert decide.step(db, did, "human_accept", actor="human").status == "ACCEPTED"
    finally:
        db.close()
