"""W-39 (T6): the permit of a Write, Edit, MultiEdit, NotebookEdit or MCP call with a command is bound to the content of the call, not only to the path or the command.

The plan hash of a write names the target path; the permit records a `file_sha` precondition (digest of everything the call carries) when it is requested
and the consume check refuses a call whose content differs. Every true positive has a false-positive twin: the same call again, a call that is not gated,
a call to another path. Paths, commands and secret-shaped values are synthetic (a secret-shaped value is assembled from parts)."""
import json

import pytest

from jade import cli, hooks, ledger, permits, policy, util

PATH = "~/.claude/settings.json"   # R-13, high: a write to the agent's own policy file needs a permit
SESSION = "sess-aaaaaaaa"


@pytest.fixture
def home(tmp_path, monkeypatch, human_env):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


def ev(tool, tin, session=SESSION):
    return {"tool_name": tool, "tool_input": tin, "session_id": session, "cwd": "/tmp/x"}


def write(content, path=PATH, **kw):
    return ev("Write", {"file_path": path, "content": content}, **kw)


def pending(db):
    return permits.listing(db, ("PENDING",))[-1]


def approved(event):
    """The gate blocks `event`, a human approves the permit it raised. Returns (db, permit)."""
    db = ledger.open()
    assert hooks.pretool(event).exit_code == 2
    p = pending(db)
    permits.approve(db, p.permit_id, stdin_isatty=True)
    return db, p


def kinds(p):
    return {x["kind"]: x["fingerprint"] for x in p.preconditions}


# ---- the digest -----------------------------------------------------------------------------------------
def test_the_content_view_is_one_to_one_with_the_input():
    a = policy.content_view({"file_path": "p", "content": "x"})
    assert a != policy.content_view({"file_path": "p", "content": "y"})
    assert a != policy.content_view({"file_path": "p", "content": "x", "replace_all": False})
    # a value cannot imitate a header, and a dotted key is not a nested key
    assert policy.content_view({"a": "x\n@@ [\"b\"] (1 chars)\ny\n"}) != policy.content_view({"a": "x", "b": "y"})
    assert policy.content_view({"a.b": "x"}) != policy.content_view({"a": {"b": "x"}})
    assert policy.content_view({"b": 1, "a": 2}) == policy.content_view({"a": 2, "b": 1})   # key order is not content


def test_which_calls_are_content_bound():
    assert all(policy.content_bound(t, {"file_path": "p"}) for t in ("Write", "Edit", "MultiEdit", "NotebookEdit"))
    assert policy.content_bound("mcp__x__run", {"command": "ls"})
    assert not policy.content_bound("mcp__x__run", {"id": "1"})   # no command: the plan hash already covers the whole input
    assert not policy.content_bound("Bash", {"command": "ls"})
    assert not policy.content_bound("Read", {"file_path": "p"})


# ---- Write ----------------------------------------------------------------------------------------------
def test_a_write_permit_records_the_content_digest_and_shows_the_content(home):
    db, p = approved(write("{\"hooks\": {}}\n"))
    got = permits.get(db, p.permit_id)
    assert kinds(got)["file_sha"] == policy.content_sha({"file_path": PATH, "content": "{\"hooks\": {}}\n"})
    screen = permits.review_text(p)
    assert "{\"hooks\": {}}" in screen and "characters" in screen and got.plan_hash[:12] in screen and kinds(got)["file_sha"][:12] in screen


def test_the_same_content_is_allowed_once_and_other_content_to_the_same_path_is_not(home):
    db, p = approved(write("A"))
    r = hooks.pretool(write("B"))      # same path, same plan hash, other content
    assert r.exit_code == 2 and "content" in r.stderr and "differs" in r.stderr
    assert permits.get(db, p.permit_id).state == "ISSUED"     # not used up: the content that was approved can still be written
    r = hooks.pretool(write("A"))      # twin: exactly what the human approved
    assert r.exit_code == 0 and "consumed" in r.stderr + r.stdout
    assert permits.get(db, p.permit_id).state == "CONSUMED"
    assert hooks.pretool(write("A")).exit_code == 2           # single use is kept


def test_other_content_raises_its_own_request_and_replaces_a_pending_one(home):
    db = ledger.open()
    hooks.pretool(write("A"))
    first = pending(db)
    hooks.pretool(write("A"))
    assert pending(db).permit_id == first.permit_id           # twin: the same call asks again and sees the same request
    hooks.pretool(write("B"))
    assert permits.get(db, first.permit_id).state == "REVOKED"   # the human approves what is true now
    second = pending(db)
    assert second.permit_id != first.permit_id and kinds(second)["file_sha"] != kinds(first)["file_sha"]


def test_with_two_approved_contents_each_call_uses_the_permit_of_its_own_content(home):
    db, a = approved(write("A"))
    hooks.pretool(write("B"))
    b = pending(db)
    permits.approve(db, b.permit_id, stdin_isatty=True)
    assert hooks.pretool(write("A")).exit_code == 0           # A was approved first; B is the newer permit and must not hide it
    assert permits.get(db, a.permit_id).state == "CONSUMED" and permits.get(db, b.permit_id).state == "ISSUED"
    assert hooks.pretool(write("B")).exit_code == 0
    assert permits.get(db, b.permit_id).state == "CONSUMED"


def test_a_write_to_another_path_is_another_plan(home):
    db, p = approved(write("A"))
    other = write("A", path="~/.claude/hooks/x.sh")
    assert hooks.pretool(other).exit_code == 2 and permits.get(db, p.permit_id).state == "ISSUED"


def test_an_ungated_write_needs_no_permit_and_records_nothing(home):
    db = ledger.open()
    assert hooks.pretool(write("anything", path="/tmp/x/notes.md")).exit_code == 0
    assert permits.listing(db) == []


# ---- Edit, MultiEdit, NotebookEdit ----------------------------------------------------------------------
EDIT = {"file_path": PATH, "old_string": "a", "new_string": "b"}


@pytest.mark.parametrize("change", [{"new_string": "c"}, {"old_string": "z"}, {"replace_all": True}])
def test_an_edit_permit_covers_that_old_and_new_string_only(home, change):
    db, p = approved(ev("Edit", EDIT))
    assert "old_string" in permits.review_text(p) and "new_string" in permits.review_text(p)
    assert hooks.pretool(ev("Edit", {**EDIT, **change})).exit_code == 2
    assert permits.get(db, p.permit_id).state == "ISSUED"
    assert hooks.pretool(ev("Edit", EDIT)).exit_code == 0     # twin


def test_a_multiedit_permit_covers_every_edit(home):
    edits = [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "d"}]
    db, p = approved(ev("MultiEdit", {"file_path": PATH, "edits": edits}))
    assert hooks.pretool(ev("MultiEdit", {"file_path": PATH, "edits": [edits[0], {"old_string": "c", "new_string": "evil"}]})).exit_code == 2
    assert hooks.pretool(ev("MultiEdit", {"file_path": PATH, "edits": list(reversed(edits))})).exit_code == 2   # order is part of the edit
    assert hooks.pretool(ev("MultiEdit", {"file_path": PATH, "edits": edits})).exit_code == 0
    assert permits.get(db, p.permit_id).state == "CONSUMED"


def test_a_notebook_edit_permit_covers_the_new_source(home):
    nb = {"notebook_path": "~/.claude/hooks/n.ipynb", "new_source": "print(1)", "cell_id": "c1"}
    approved(ev("NotebookEdit", nb))
    assert hooks.pretool(ev("NotebookEdit", {**nb, "new_source": "import os"})).exit_code == 2
    assert hooks.pretool(ev("NotebookEdit", nb)).exit_code == 0


# ---- MCP -----------------------------------------------------------------------------------------------
def test_an_mcp_call_with_a_command_is_bound_to_its_other_arguments(home):
    tool, tin = "mcp__x__shell", {"command": "git push origin feat-x", "cwd": "/tmp/a"}
    db, p = approved(ev(tool, tin))
    assert "file_sha" in kinds(p)
    assert hooks.pretool(ev(tool, {**tin, "cwd": "/tmp/b"})).exit_code == 2
    assert permits.get(db, p.permit_id).state == "ISSUED"
    assert hooks.pretool(ev(tool, tin)).exit_code == 0       # twin


def test_an_mcp_call_without_a_command_was_already_bound_by_its_plan_hash(home):
    tool = "mcp__GitLab__save_note"                          # M-02, high: no command, the plan hash is the whole input
    a = policy.assess(tool, {"body": "one"}, "/tmp/x")
    b = policy.assess(tool, {"body": "two"}, "/tmp/x")
    assert a.level == "high" and a.plan_hash != b.plan_hash
    db, p = approved(ev(tool, {"body": "one"}))
    assert "file_sha" not in kinds(p)
    assert hooks.pretool(ev(tool, {"body": "two"})).exit_code == 2
    assert hooks.pretool(ev(tool, {"body": "one"})).exit_code == 0


# ---- review: too large, secrets, tampering --------------------------------------------------------------
def test_content_too_large_to_review_cannot_be_approved(home, monkeypatch, capsys):
    db = ledger.open()
    big = "x" * (permits.MAX_CONTENT + 10)
    assert hooks.pretool(write(big)).exit_code == 2
    p = pending(db)
    c = permits.ctx_of(p)["content"]
    assert c["truncated"] is True and len(c["text"]) <= permits.CONTENT_PREVIEW and c["length"] > permits.MAX_CONTENT
    for fn in (lambda: permits.review_text(p), lambda: permits.approve(db, p.permit_id, stdin_isatty=True)):
        with pytest.raises(permits.PermitError, match="cannot be shown in full"):
            fn()
    monkeypatch.setattr(cli, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "000000")
    assert cli.main(["permit", "approve", p.permit_id]) == 1
    assert "cannot be shown in full" in capsys.readouterr().err and permits.get(db, p.permit_id).state == "PENDING"


def test_content_just_inside_the_limit_is_shown_in_full(home):
    db = ledger.open()
    body = "y" * 1000
    assert hooks.pretool(write(body)).exit_code == 2
    p = pending(db)
    assert permits.ctx_of(p)["content"]["truncated"] is False and body in permits.review_text(p)
    permits.approve(db, p.permit_id, stdin_isatty=True)


def test_content_with_a_secret_shaped_value_cannot_be_approved():
    token = "ghp_" + "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk1Ll2"   # assembled from parts: the repository holds no literal token
    a = policy.assess("Write", {"file_path": PATH, "content": "x"}, "/tmp/x")
    ctx = permits.make_context("Write", a, ev("Write", {"file_path": PATH, "content": f"t={token}"}), cwd="/tmp/x")
    assert ctx["redacted"] and token not in json.dumps(ctx)
    with pytest.raises(permits.PermitError, match="secret-shaped"):
        permits._verify(ctx, a.plan_hash, {"file_sha": ctx["content"]["sha256"]})


def test_the_confirmation_code_is_the_content_digest_so_two_contents_never_share_it(home, monkeypatch, capsys):
    db = ledger.open()
    hooks.pretool(write("A"))
    pa = pending(db)
    hooks.pretool(write("B"))
    pb = pending(db)
    assert pa.plan_hash == pb.plan_hash and permits.confirm_code(pa) != permits.confirm_code(pb)
    monkeypatch.setattr(cli, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setattr(util, "agent_ancestor", lambda: None)
    monkeypatch.setattr(permits, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": pb.plan_hash[:6])   # the old code, the plan hash prefix, no longer approves
    assert cli.main(["permit", "approve", pb.permit_id]) == 1
    monkeypatch.setattr("builtins.input", lambda prompt="": permits.confirm_code(pb))
    assert cli.main(["permit", "approve", pb.permit_id]) == 0
    assert "B" in capsys.readouterr().out and permits.get(db, pb.permit_id).state == "ISSUED"


def test_the_stored_content_must_be_what_the_digest_and_the_precondition_say(home):
    db = ledger.open()
    hooks.pretool(write("A"))
    p = pending(db)
    ctx = permits.ctx_of(p)
    ctx["content"]["text"] = "@@ [\"content\"] (4 chars)\nEVIL\n"     # a screen that no longer shows what was hashed
    db.con.execute("UPDATE permits SET scope=? WHERE permit_id=?", (json.dumps(ctx), p.permit_id))
    with pytest.raises(permits.PermitError, match="does not match its digest"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    assert permits.get(db, p.permit_id).state == "PENDING"


def test_the_digest_is_sealed_with_the_permit(home):
    db, p = approved(write("A"))
    row = db.one("SELECT doc_json FROM permits WHERE permit_id=?", (p.permit_id,))
    doc = json.loads(row.doc_json)
    for x in doc["preconditions"]:
        if x["kind"] == "file_sha":
            x["fingerprint"] = policy.content_sha({"file_path": PATH, "content": "EVIL"})
    db.con.execute("UPDATE permits SET doc_json=? WHERE permit_id=?", (json.dumps(doc), p.permit_id))
    r = hooks.pretool(write("EVIL"))
    assert r.exit_code == 2                                    # the edited row fails its seal and is not even offered for use
    assert permits.get(db, p.permit_id).state == "ISSUED" and pending(db).permit_id != p.permit_id
    assert "seal" in permits.try_consume(db, p.permit_id, p.plan_hash, level="high", session=SESSION, fingerprints={"file_sha": doc["preconditions"][-1]["fingerprint"]})


# ---- permits requested before the digest existed ------------------------------------------------------
def test_a_permit_without_a_content_digest_does_not_cover_a_content_bound_call(home):
    db = ledger.open()
    p = permits.issue(db, "write", "c" * 64, risk="low")      # what an older release left behind: no file_sha precondition
    why = permits.try_consume(db, p.permit_id, "c" * 64, level="low", fingerprints={"file_sha": "a" * 64})
    assert why and "requested before the content" in why and permits.get(db, p.permit_id).state == "ISSUED"
    assert permits.try_consume(db, p.permit_id, "c" * 64, level="low", fingerprints={}) is None   # twin: a call with no content binding is unchanged


def test_a_pending_write_request_made_before_content_was_bound_cannot_be_approved(home):
    db = ledger.open()
    a = policy.assess("Write", {"file_path": PATH, "content": "A"}, "/tmp/x")
    old = permits.request(db, "write", a.plan_hash, risk=a.level, context=permits.make_context("Write", a, cwd="/tmp/x"))   # no event: no content on file
    with pytest.raises(permits.PermitError, match="before the content of a write was bound"):
        permits.approve(db, old.permit_id, stdin_isatty=True)
    hooks.pretool(write("A"))                                  # the retry raises a request that carries the content
    new = pending(db)
    assert new.permit_id != old.permit_id and permits.get(db, old.permit_id).state == "REVOKED"
    permits.approve(db, new.permit_id, stdin_isatty=True)


def test_a_bash_permit_is_unchanged(home):
    cmd = "git push origin feat-x"
    db = ledger.open()
    assert hooks.pretool(ev("Bash", {"command": cmd})).exit_code == 2
    p = pending(db)
    assert "file_sha" not in kinds(p) and "content" not in permits.ctx_of(p) and "Content" not in permits.review_text(p)
    permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(ev("Bash", {"command": cmd})).exit_code == 0
