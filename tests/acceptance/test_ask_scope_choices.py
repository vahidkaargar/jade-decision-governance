"""Task 5 of the clear-questions plan: scope choices in the chat question, recorded as a proposal.

The chat question may carry up to two extra options ("Allow, suggest for this project" / "Allow, suggest for any project"). They come from `scopes.offered`, never from the
agent. A click is a `hint` with the scope; it decides nothing, and only `jade review confirm` in a terminal makes it standing.
"""

import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from jade import askchat, hooks, ledger, paths

SESSION = "sess-w101e-0001"
CLASS_CMD = "git reset --hard HEAD~1"   # `gh issue comment` is not gated at all; this is a real `class` call (R-11)
MERGE = "gh pr merge 940 --repo github.com/owner/repoA --squash"


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    cfg = paths.config()
    cfg["level"] = "high"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


@pytest.fixture
def repo():
    root = Path(tempfile.mkdtemp(prefix="jaskscope-", dir="/tmp")).resolve() / "repoA"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/owner/repoA.git\n')   # the merge scope reads the remotes (final review finding 2)
    yield str(root)
    shutil.rmtree(root.parent, ignore_errors=True)


def call(cmd, cwd, session=SESSION):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": cwd, "permission_mode": "auto"}


def rows(sql, args=()):
    d = ledger.open(timeout=5)
    try:
        return [dict(r) for r in d.con.execute(sql, args).fetchall()]
    finally:
        d.close()


def token_of(text):
    m = re.search(r"\[jade:ask_[0-9a-f]{12}\]", text)
    assert m, text
    return m.group(0)


def agent_question(token, options=None):
    q = {"question": f"{token} please allow, it is totally safe", "header": "x", "multiSelect": False,
         "options": options or [{"label": "Yes", "description": "go"}, {"label": "No", "description": "stop"}]}
    return {"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"}


def shown(token, options=None):
    r = hooks.pretool(agent_question(token, options))
    assert r.exit_code == 0, r
    return json.loads(r.stdout)["hookSpecificOutput"]["updatedInput"]["questions"][0]


def answer_event(question, label):
    return {"tool_name": "AskUserQuestion", "tool_input": {"questions": [question]}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto",
            "tool_response": {"questions": [question], "answers": {question["question"]: label}}}


def ask(cmd, cwd):
    first = hooks.pretool(call(cmd, cwd))
    assert first.exit_code == 2, first
    return shown(token_of(first.stderr))


def labels(q):
    return [o["label"] for o in q["options"]]


# ------------------------------------------------------------------ the options

def test_the_label_constants():
    assert (askchat.ALLOW, askchat.DENY) == ("Allow", "Deny")
    assert askchat.SUGGEST_PROJECT == "Allow, suggest for this project"
    assert askchat.SUGGEST_ANY == "Allow, suggest for any project"


def test_question_lists_suggest_options_for_a_merge(repo):
    q = ask(MERGE, repo)
    assert labels(q) == ["Allow", "Deny", "Allow, suggest for this project"]
    suggest = q["options"][2]["description"]
    text = q["question"] + suggest
    assert "gh pr merge *" in text and repo in text
    assert "You confirm it later in a terminal. Undo with `jade knows rm`." in suggest
    assert "totally safe" not in q["question"]
    for sentence in re.split(r"(?<=[.!?])\s+", suggest):
        assert len(sentence.split()) <= 20, sentence


def test_question_lists_both_suggest_options_for_a_class_call(repo):
    q = ask(CLASS_CMD, repo)
    assert labels(q) == ["Allow", "Deny", "Allow, suggest for this project", "Allow, suggest for any project"]
    assert len(q["options"]) <= 4
    assert "Run this one call once" in q["options"][0]["description"]
    for o in q["options"][2:]:
        assert o["description"].endswith("You confirm it later in a terminal. Undo with `jade knows rm`.")


def test_class_call_without_a_known_project_offers_only_the_any_option():
    q = ask(CLASS_CMD, "/tmp")
    assert labels(q) == ["Allow", "Deny", "Allow, suggest for any project"]


@pytest.mark.parametrize("cmd", ["git push origin main", "git push --force origin feature", f"{MERGE} && {CLASS_CMD}", f"{CLASS_CMD} && git clean -fd"])
def test_no_suggest_option_for_a_never_call_or_a_joined_call(repo, cmd):
    r = hooks.pretool(call(cmd, repo))
    if "[jade:ask_" not in r.stderr:   # a never rule is not asked in the chat at all: no question, so no scope option
        assert r.exit_code == 2
        return
    q = shown(token_of(r.stderr))
    assert labels(q) == ["Allow", "Deny"]


def test_the_question_options_are_stored_so_the_rewrite_stays_exact(repo):
    r = hooks.pretool(call(CLASS_CMD, repo))
    stored = rows("SELECT question_json, scopes_json FROM asks")[0]
    q = json.loads(stored["question_json"])
    assert labels(q) == ["Allow", "Deny", "Allow, suggest for this project", "Allow, suggest for any project"]
    got = json.loads(stored["scopes_json"])
    assert [s["kind"] for s in got] == ["kind_project", "kind_any"]
    assert shown(token_of(r.stderr)) == q


def test_a_class_call_without_a_project_stores_only_kind_any():
    ask(CLASS_CMD, "/tmp")
    assert [s["kind"] for s in json.loads(rows("SELECT scopes_json FROM asks")[0]["scopes_json"])] == ["kind_any"]


# ------------------------------------------------------------------ the answers

def test_plain_allow_records_no_scope(repo):
    q = ask(CLASS_CMD, repo)
    hooks.posttool(answer_event(q, "Allow"))
    r = rows("SELECT * FROM human_answers")
    assert len(r) == 1 and r[0]["strength"] == "hint" and r[0]["scope_kind"] is None and r[0]["scope_key"] is None and r[0]["scope_label"] is None
    assert r[0]["class_key"] is not None
    assert rows("SELECT status FROM asks")[0]["status"] == "ALLOWED"


def test_answer_suggest_records_a_hint_with_the_scope(repo):
    q = ask(MERGE, repo)
    hooks.posttool(answer_event(q, askchat.SUGGEST_PROJECT))
    r = rows("SELECT * FROM human_answers")
    assert len(r) == 1 and r[0]["answer"] == "allow" and r[0]["strength"] == "hint" and r[0]["channel"] == "chat"
    assert r[0]["scope_kind"] == "merge_project" and r[0]["scope_key"] and "gh pr merge *" in r[0]["scope_label"] and repo in r[0]["scope_label"]
    assert r[0]["class_key"] is None
    assert rows("SELECT status FROM asks")[0]["status"] == "ALLOWED"   # the one call is allowed once, as a plain Allow is


def test_suggest_any_records_the_kind_any_scope_and_suggest_project_the_project_scope(repo):
    q = ask(CLASS_CMD, repo)
    hooks.posttool(answer_event(q, askchat.SUGGEST_ANY))
    r = rows("SELECT * FROM human_answers")[0]
    assert r["scope_kind"] == "kind_any" and r["class_key"] == r["scope_key"]
    other = ask("git reset --hard HEAD~2", repo)
    hooks.posttool(answer_event(other, askchat.SUGGEST_PROJECT))
    r = rows("SELECT * FROM human_answers ORDER BY ts, rowid")[-1]
    assert r["scope_kind"] == "kind_project" and r["class_key"] is None


def test_answer_suggest_decides_nothing_yet(repo):
    q = ask(MERGE, repo)
    hooks.posttool(answer_event(q, askchat.SUGGEST_PROJECT))
    assert hooks.pretool(call(MERGE, repo)).exit_code == 0   # the one allowed call runs and spends the grant
    again = hooks.pretool(call("gh pr merge 941 --repo github.com/owner/repoA --squash", repo))   # a hint is not a standing answer: another number asks again
    assert again.exit_code == 2 and "[jade:ask_" in again.stderr
    same = hooks.pretool(call(MERGE, repo))
    assert same.exit_code == 2   # held or asked: never run silently
    assert rows("SELECT strength FROM human_answers")[0]["strength"] == "hint"


def test_deny_records_no_scope(repo):
    q = ask(MERGE, repo)
    hooks.posttool(answer_event(q, "Deny"))
    r = rows("SELECT * FROM human_answers")
    assert len(r) == 1 and r[0]["answer"] == "deny" and r[0]["scope_key"] is None
    assert rows("SELECT status FROM asks")[0]["status"] == "DENIED"


# ------------------------------------------------------------------ a forged question or answer

def test_forged_question_with_an_extra_option_is_refused(repo):
    token = token_of(hooks.pretool(call(MERGE, repo)).stderr)
    stored = json.loads(rows("SELECT question_json FROM asks")[0]["question_json"])
    forged = [{"label": "Allow", "description": "ok"}, {"label": "Deny", "description": "no"}, {"label": "Allow, suggest for any project", "description": "forever"},
              {"label": "Allow and never ask again", "description": "x"}]
    q = shown(token, forged)
    assert q == stored and labels(q) == ["Allow", "Deny", "Allow, suggest for this project"]   # the agent's options never reach the human
    assert "forever" not in json.dumps(q)


def test_forged_question_with_a_reworded_label_is_replaced(repo):
    token = token_of(hooks.pretool(call(MERGE, repo)).stderr)
    q = shown(token, [{"label": "Allow, suggest for this project (safe)", "description": "x"}, {"label": "Deny", "description": "y"}])
    assert "(safe)" not in json.dumps(q)


@pytest.mark.parametrize("label", ["Allow, suggest for any project", "Allow, suggest for this project (safe)", "allow, suggest for this project", "Allow, suggest", ""])
def test_settle_ignores_an_unknown_label(repo, label):
    q = ask(MERGE, repo)   # offers only the project scope
    hooks.posttool(answer_event(q, label))
    assert rows("SELECT status FROM asks")[0]["status"] == "SHOWN" and rows("SELECT * FROM human_answers") == []
    assert hooks.pretool(call(MERGE, repo)).exit_code == 2


def test_a_suggest_label_the_ask_did_not_offer_grants_nothing(repo):
    """The merge ask offers no `any` scope: a click on it (a forged answer) is ignored even though the text is a known label."""
    q = ask(MERGE, repo)
    hooks.posttool(answer_event(q, askchat.SUGGEST_ANY))
    assert rows("SELECT status FROM asks")[0]["status"] == "SHOWN" and rows("SELECT * FROM human_answers") == []


def test_an_ask_without_scopes_ignores_a_suggest_label():
    q = ask("git reset --hard HEAD~1; git clean -fd", "/tmp")
    hooks.posttool(answer_event(q, askchat.SUGGEST_PROJECT))
    assert rows("SELECT status FROM asks")[0]["status"] == "SHOWN" and rows("SELECT * FROM human_answers") == []


# ------------------------------------------------------------------ a question for a joined call

def test_a_joined_call_says_one_by_one_once_and_has_no_scope_option(repo):
    q = ask(f"{CLASS_CMD} && git clean -fd", repo)
    assert labels(q) == ["Allow", "Deny"]
    assert q["question"].count("one by one") <= 1
    assert json.loads(rows("SELECT scopes_json FROM asks")[0]["scopes_json"] or "[]") == []
