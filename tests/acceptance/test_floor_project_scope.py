"""V6-16: a project scope for R-16 (a write to JADE's own code) and R-14 (a remote shell or copy), for the maintainer's own JADE checkout.

R-16 and R-14 were 77 of 100 answers (docs/14-v6/12-roadmap.md, A6): a Write of `jade/*.py` is exact-only (its content is part of the key) and an `ssh` is
exact-only too, so each edit and each call was a new question. The scope `rule_project` lets the owner answer once for "this rule, in this project". It is
offered only when ALL of these hold (a loosening, so the list is short):
- the call is plain: no human-only class, nothing blocked, nothing the gate could not read, not critical, one risky command, no inline program;
- the project (the git root of the folder or the file) is a JADE checkout (`jade/__init__.py` and `jade/hooks.py`);
- the JADE that is running now does not live in that project (an editable install from the checkout would make an edit live at once);
- R-16: a Write, Edit, MultiEdit or NotebookEdit of one file under `<project>/jade/`; R-14: one plain `ssh`, `scp`, `sftp` or `rsync host:` command.
It decides nothing by itself: only `jade review confirm` in a terminal makes it standing (the existing rule for every scope).
"""

import dataclasses
import json
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import timedelta
from pathlib import Path

import pytest

from jade import askchat, hooks, ledger, memory, paths, policy, scopes, util
from jade.util import now, ts

SESSION = "sess-floor-scope-0001"


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


@pytest.fixture
def root():
    d = Path(tempfile.mkdtemp(prefix="jfloorscope-", dir="/tmp")).resolve()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def make_project(base: Path, name: str, checkout: bool = True) -> str:
    p = base / name
    (p / ".git").mkdir(parents=True)
    (p / "jade").mkdir()
    (p / "jade" / "other.py").write_text("x = 1\n")
    if checkout:
        (p / "jade" / "__init__.py").write_text("")
        (p / "jade" / "hooks.py").write_text("")
    return str(p)


@pytest.fixture
def checkout(root):
    return make_project(root, "jade-a")


@pytest.fixture
def checkout_b(root):
    return make_project(root, "jade-b")


@pytest.fixture
def plain(root):
    return make_project(root, "plain", checkout=False)


def set_level(level):
    cfg = paths.config()
    cfg["level"] = level
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


def db():
    return ledger.open(timeout=5)


def tool_call(tool, path, cwd, content="x = 1\n"):
    return {"tool_name": tool, "tool_input": {"file_path": path, "content": content, "new_string": content, "old_string": "y"}, "session_id": SESSION, "cwd": cwd, "permission_mode": "auto"}


def offered_for(tool, tool_input, cwd):
    a = policy.assess(tool, tool_input, cwd)
    return a, scopes.offered(a, tool, tool_input, {}, cwd)


def write_scopes(project, rel="jade/hooks.py", tool="Write", content="x = 1\n", cwd=None):
    path = str(Path(project) / rel)
    tin = {"file_path": path, "content": content, "new_string": content, "old_string": "y"}
    return offered_for(tool, tin, cwd or project)


def shell_scopes(cmd, cwd):
    return offered_for("Bash", {"command": cmd}, cwd)


def kinds(got):
    return [s.kind for s in got]


# ------------------------------------------------------------------ R-16: a write of JADE's own code in a JADE checkout

@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_a_write_of_jade_code_in_a_checkout_offers_one_project_scope_and_no_any_scope(checkout, tool):
    a, got = write_scopes(checkout, tool=tool)
    assert a.rule_id == "R-16" and a.level == "high" and not a.ho_ids and not a.blocked
    assert kinds(got) == ["rule_project"], got
    assert checkout in got[0].label and "R-16" in got[0].label and len(got[0].label) <= scopes.LABEL_MAX


def test_the_scope_key_does_not_depend_on_the_file_or_the_content(checkout):
    _, one = write_scopes(checkout, "jade/hooks.py", content="a = 1\n")
    _, two = write_scopes(checkout, "jade/policy.py", content="completely different text\n")
    _, three = write_scopes(checkout, "jade/new_module.py", tool="Edit", content="b = 2\n")
    assert one[0].key == two[0].key == three[0].key


def test_the_scope_key_is_the_same_from_another_folder_of_the_project(checkout):
    _, here = write_scopes(checkout)
    _, there = write_scopes(checkout, cwd=str(Path(checkout) / "jade"))
    assert here[0].key == there[0].key


def test_another_project_has_another_key(checkout, checkout_b):
    _, a = write_scopes(checkout)
    _, b = write_scopes(checkout_b)
    assert a[0].key != b[0].key


def test_a_project_that_is_not_a_jade_checkout_gets_no_scope(plain):
    a, got = write_scopes(plain, "jade/other.py")
    assert a.rule_id == "R-16" and got == []


def test_a_path_that_is_not_under_the_jade_folder_gets_no_scope(checkout):
    (Path(checkout) / "docs" / "jade").mkdir(parents=True)
    a, got = write_scopes(checkout, "docs/jade/x.py")
    assert a.rule_id == "R-16" and got == []


def test_no_scope_when_the_running_jade_lives_in_the_project(checkout, monkeypatch):
    monkeypatch.setattr(scopes, "_live_code_root", lambda: checkout)   # an editable install from this checkout: an edit would be live
    assert write_scopes(checkout)[1] == []
    assert shell_scopes("ssh host uptime", checkout)[1] == []


def test_the_real_running_checkout_gets_no_scope():
    root = Path(__file__).resolve().parents[2]   # these tests run the jade of this checkout
    assert write_scopes(str(root))[1] == []


@pytest.mark.parametrize("rel", ["AGENTS.md", ".claude/settings.json", "policies/risk.yaml", "policies/safety.yaml"])
def test_other_protected_files_of_the_checkout_get_no_scope(checkout, rel):
    (Path(checkout) / rel).parent.mkdir(parents=True, exist_ok=True)
    a, got = write_scopes(checkout, rel)
    assert a.level in ("high", "critical") and a.rule_id != "R-16" and got == []


def test_a_mcp_file_write_gets_no_scope(checkout):
    tin = {"path": str(Path(checkout) / "jade" / "hooks.py"), "content": "x"}
    _, got = offered_for("mcp__fs__write_file", tin, checkout)
    assert got == []


def test_a_blocked_write_gets_no_scope(checkout):
    secret = "sk-" + "a1b2c3d4" * 4   # assembled so that the file holds no token-shaped literal
    a, got = write_scopes(checkout, content=f"KEY = '{secret}'\n")
    assert a.blocked and got == []


def test_a_write_in_the_wiring_gets_no_scope(checkout):
    tin = {"file_path": str(Path.home() / ".claude" / "settings.json"), "content": "{}"}
    a, got = offered_for("Write", tin, checkout)
    assert a.ho_ids and got == []


def test_a_write_with_two_paths_gets_no_scope(checkout):
    tin = {"file_path": str(Path(checkout) / "jade" / "hooks.py"), "path": str(Path(checkout) / "README.md"), "content": "x"}
    assert offered_for("Write", tin, checkout)[1] == []


# ------------------------------------------------------------------ R-16 by a shell command keeps the scopes it had

def test_an_r16_shell_write_keeps_the_existing_class_scopes_and_adds_no_second_project_scope(checkout):
    a, got = shell_scopes("cp /tmp/x jade/hooks.py", checkout)
    assert a.rule_id == "R-16"
    assert kinds(got) == ["kind_project", "kind_any"], got   # as before V6-16


# ------------------------------------------------------------------ R-14: one plain remote command

def test_a_plain_ssh_in_a_checkout_offers_one_project_scope(checkout):
    a, got = shell_scopes("ssh admin@host.example.net uptime", checkout)
    assert a.rule_id == "R-14" and kinds(got) == ["rule_project"], got
    assert "R-14" in got[0].label or "ssh" in got[0].label


@pytest.mark.parametrize("cmd", ["scp notes.txt host.example.net:/tmp", "rsync -a ./site/ host.example.net:/srv/www", "sftp host.example.net"])
def test_other_remote_commands_offer_the_scope(checkout, cmd):
    a, got = shell_scopes(cmd, checkout)
    assert a.rule_id == "R-14" and kinds(got) == ["rule_project"], (cmd, got)


def test_the_same_command_has_the_same_key_and_another_command_another_key(checkout, checkout_b):
    _, one = shell_scopes("ssh admin@host.example.net uptime", checkout)
    _, same = shell_scopes("ssh admin@host.example.net uptime", checkout)
    _, other_cmd = shell_scopes("ssh admin@host.example.net 'cat /etc/passwd'", checkout)
    _, other_host = shell_scopes("ssh admin@other.example.net uptime", checkout)
    _, other_project = shell_scopes("ssh admin@host.example.net uptime", checkout_b)
    assert one[0].key == same[0].key
    assert len({one[0].key, other_cmd[0].key, other_host[0].key, other_project[0].key}) == 4


def test_the_quoted_words_of_a_remote_command_are_part_of_the_scope_key(checkout):
    """The segment key blanks a quoted word (`ssh host ""`), so the key of the scope is made from the raw text: a confirmed `ssh host 'ls'` must not cover `ssh host 'cat x'`."""
    _, one = shell_scopes("ssh admin@host.example.net 'cat /etc/passwd'", checkout)
    _, two = shell_scopes("ssh admin@host.example.net 'cat /etc/shadow'", checkout)
    _, three = shell_scopes('ssh admin@host.example.net "cat /etc/shadow"', checkout)
    assert len({one[0].key, two[0].key, three[0].key}) == 3


def test_a_leading_cd_into_the_project_is_the_same_scope(checkout):
    _, here = shell_scopes("ssh admin@host.example.net uptime", checkout)
    _, moved = shell_scopes(f"cd {checkout}/jade && ssh admin@host.example.net uptime", "/tmp")
    assert kinds(moved) == ["rule_project"] and moved[0].key == here[0].key   # a leading cd moves the project; the text after it is what is keyed


@pytest.mark.parametrize("cmd", ["ssh a.example.net uptime && ssh b.example.net uptime", "ssh a.example.net uptime; rm -rf build",
                                 "ssh a.example.net 'sh -c \"x\"'; ssh b.example.net ls", "ssh host.example.net <<EOF\nls\nEOF"])
def test_two_risky_commands_or_an_inline_program_get_no_scope(checkout, cmd):
    a, got = shell_scopes(cmd, checkout)
    assert got == [], (cmd, got)


def test_a_remote_command_in_another_project_gets_no_scope(plain):
    a, got = shell_scopes("ssh admin@host.example.net uptime", plain)
    assert a.rule_id == "R-14" and got == []


def test_a_remote_command_outside_any_project_gets_no_scope(root):
    a, got = shell_scopes("ssh admin@host.example.net uptime", str(root))
    assert got == []


def test_an_env_prefix_is_part_of_the_target_not_ignored(checkout):
    _, plain_ = shell_scopes("ssh admin@host.example.net uptime", checkout)
    _, env = shell_scopes("SSH_AUTH_SOCK=/tmp/x ssh admin@host.example.net uptime", checkout)
    assert env == [] or env[0].key != plain_[0].key


# ------------------------------------------------------------------ memory: a confirmed scope covers what it was confirmed for, and no more

def store(a, tool, tin, cwd, scope, answer="allow", strength="strong"):
    scope_d = dataclasses.asdict(scope)
    exact, cls, _ = memory.keys(a, tool, tin, {})
    d = db()
    try:
        with d.tx():
            aid = d.record_human_answer(session=SESSION, decision_id=None, rule_id=a.rule_id, class_key=cls, exact_key=exact, answer=answer, channel="chat",
                                        label="x", scope=scope_d)
            if answer == "allow":
                d.con.execute("UPDATE human_answers SET ts=? WHERE answer_id=?", (ts(now() - timedelta(minutes=5)), aid))   # the newest answer wins: a later deny is newer
            if answer == "allow" and strength == "strong":
                d.confirm_answers([aid])
    finally:
        d.close()
    return aid


def lookup(tool, tin, cwd):
    a = policy.assess(tool, tin, cwd)
    d = db()
    try:
        return memory.lookup(d, a, tool, tin, {}, cwd)
    finally:
        d.close()


def wtin(project, rel, content):
    return {"file_path": str(Path(project) / rel), "content": content, "new_string": content, "old_string": "y"}


def test_a_confirmed_write_scope_covers_another_write_of_the_project_and_nothing_else(checkout, checkout_b, plain):
    first = wtin(checkout, "jade/hooks.py", "a = 1\n")
    a, got = offered_for("Write", first, checkout)
    store(a, "Write", first, checkout, got[0])
    assert lookup("Write", wtin(checkout, "jade/policy.py", "b = 2\n"), checkout) is not None   # another file, another content, same project
    assert lookup("Edit", wtin(checkout, "jade/util.py", "c = 3\n"), checkout) is not None
    assert lookup("Write", wtin(checkout_b, "jade/policy.py", "b = 2\n"), checkout_b) is None   # another project
    assert lookup("Write", wtin(plain, "jade/other.py", "b = 2\n"), plain) is None
    assert lookup("Write", wtin(checkout, "AGENTS.md", "b = 2\n"), checkout) is None   # another rule (R-13)
    assert lookup("Write", {"file_path": str(Path.home() / ".claude" / "settings.json"), "content": "{}"}, checkout) is None   # the wiring is never remembered
    assert lookup("Bash", {"command": "ssh admin@host.example.net uptime"}, checkout) is None   # the R-16 scope does not cover R-14


def test_a_confirmed_ssh_scope_covers_that_command_only(checkout, checkout_b):
    cmd = "ssh admin@host.example.net uptime"
    a, got = shell_scopes(cmd, checkout)
    store(a, "Bash", {"command": cmd}, checkout, got[0])
    assert lookup("Bash", {"command": cmd}, checkout) is not None
    assert lookup("Bash", {"command": cmd}, str(Path(checkout) / "jade")) is not None   # same project, another folder
    assert lookup("Bash", {"command": "ssh admin@host.example.net cat /etc/shadow"}, checkout) is None
    assert lookup("Bash", {"command": "ssh admin@other.example.net uptime"}, checkout) is None
    assert lookup("Bash", {"command": cmd}, checkout_b) is None
    assert lookup("Write", wtin(checkout, "jade/hooks.py", "a"), checkout) is None


def test_an_unconfirmed_hint_decides_nothing(checkout):
    first = wtin(checkout, "jade/hooks.py", "a = 1\n")
    a, got = offered_for("Write", first, checkout)
    store(a, "Write", first, checkout, got[0], strength="hint")
    assert lookup("Write", wtin(checkout, "jade/policy.py", "b"), checkout) is None


def test_a_newer_deny_in_the_project_takes_the_scope_out(checkout):
    first = wtin(checkout, "jade/hooks.py", "a = 1\n")
    a, got = offered_for("Write", first, checkout)
    store(a, "Write", first, checkout, got[0])
    assert lookup("Write", wtin(checkout, "jade/policy.py", "b"), checkout) is not None
    bad = wtin(checkout, "jade/util.py", "c")
    a2, got2 = offered_for("Write", bad, checkout)
    store(a2, "Write", bad, checkout, got2[0], answer="deny")
    assert lookup("Write", wtin(checkout, "jade/policy.py", "b"), checkout) is None


def test_the_scope_never_covers_a_call_that_became_blocked_or_human_only(checkout):
    first = wtin(checkout, "jade/hooks.py", "a = 1\n")
    a, got = offered_for("Write", first, checkout)
    store(a, "Write", first, checkout, got[0])
    secret = "sk-" + "a1b2c3d4" * 4
    assert lookup("Write", wtin(checkout, "jade/policy.py", f"K = '{secret}'"), checkout) is None


# ------------------------------------------------------------------ the question and the answer

def token_of(text):
    m = re.search(r"\[jade:ask_[0-9a-f]{12}\]", text)
    assert m, text
    return m.group(0)


def test_the_chat_question_offers_the_project_option_for_a_write_in_the_checkout(checkout):
    set_level("high")
    first = hooks.pretool(tool_call("Write", str(Path(checkout) / "jade" / "hooks.py"), checkout))
    assert first.exit_code == 2 and "AskUserQuestion" in first.stderr
    q = {"question": f"{token_of(first.stderr)} please", "header": "x", "multiSelect": False, "options": [{"label": "Yes", "description": "go"}, {"label": "No", "description": "stop"}]}
    shown = hooks.pretool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"})
    got = json.loads(shown.stdout)["hookSpecificOutput"]["updatedInput"]["questions"][0]
    labels = [o["label"] for o in got["options"]]
    assert askchat.SUGGEST_PROJECT in labels and askchat.SUGGEST_ANY not in labels
    assert "R-16" in got["question"] and str(checkout) in got["question"]


def _asked(checkout, rel="jade/hooks.py", content="x = 1\n"):
    first = hooks.pretool(tool_call("Write", str(Path(checkout) / rel), checkout, content))
    q = {"question": f"{token_of(first.stderr)} please", "header": "x", "multiSelect": False, "options": [{"label": "Yes", "description": "go"}, {"label": "No", "description": "stop"}]}
    shown = hooks.pretool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"})
    return json.loads(shown.stdout)["hookSpecificOutput"]["updatedInput"]["questions"][0]


def _answer(question, label):
    hooks.posttool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [question]}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto",
                    "tool_response": {"questions": [question], "answers": {question["question"]: label}}})


def _answers():
    d = db()
    try:
        return [dict(r) for r in d.con.execute("SELECT * FROM human_answers ORDER BY rowid").fetchall()]
    finally:
        d.close()


def test_the_project_option_records_a_hint_with_the_scope_and_it_decides_nothing_yet(checkout):
    set_level("medium")
    q = _asked(checkout)
    _answer(q, askchat.SUGGEST_PROJECT)
    rows_ = _answers()
    assert len(rows_) == 1 and rows_[0]["scope_kind"] == "rule_project" and rows_[0]["strength"] == "hint" and rows_[0]["answer"] == "allow"
    assert lookup("Write", wtin(checkout, "jade/policy.py", "b"), checkout) is None   # not confirmed in a terminal: no memory
    again = hooks.pretool(tool_call("Write", str(Path(checkout) / "jade" / "policy.py"), checkout, "b"))
    assert again.exit_code == 2   # still asked


def test_a_deny_records_the_scope_key_so_that_it_takes_a_confirmed_scope_out(checkout):
    set_level("medium")
    q = _asked(checkout)
    _answer(q, askchat.SUGGEST_PROJECT)
    first = _answers()[0]
    d = db()
    try:
        with d.tx():
            d.con.execute("UPDATE human_answers SET ts=? WHERE answer_id=?", (ts(now() - timedelta(minutes=5)), first["answer_id"]))
    finally:
        d.close()
    q2 = _asked(checkout, "jade/util.py", "c = 3\n")   # another write of the project: not standing yet, so it is asked
    _answer(q2, askchat.DENY)
    got = _answers()
    assert got[1]["answer"] == "deny" and got[1]["scope_kind"] == "rule_project" and got[1]["scope_key"] == first["scope_key"]
    d = db()
    try:
        d.confirm_answers([first["answer_id"]])   # the owner confirms the older allow in a terminal, after the newer deny
        d.con.execute("UPDATE human_answers SET ts=? WHERE answer_id=?", (ts(now() - timedelta(minutes=5)), first["answer_id"]))
    finally:
        d.close()
    assert lookup("Write", wtin(checkout, "jade/policy.py", "b"), checkout) is None   # the newest answer wins


def test_the_scope_text_says_what_it_covers():
    text = scopes.COVERS["rule_project"]
    assert "this project" in text and "JADE" in text and "remote" in text
    assert askchat.SUGGEST_LABELS["rule_project"] == askchat.SUGGEST_PROJECT


# ------------------------------------------------------------------ review fix: the live JADE is found whatever the spelling of its path

def _spellings(root, name):
    """The same project folder spelled another way, for each way this volume allows: other case, the data-volume firmlink, NFD for NFC. Spellings the volume does not accept are left out."""
    project = root / name
    out = {"case": str(root / name.upper()), "firmlink": "/System/Volumes/Data" + str(project), "nfd": str(root / unicodedata.normalize("NFD", name))}
    got = {k: v for k, v in out.items() if v != str(project) and os.path.isdir(v)}
    if not got:
        pytest.skip("this volume accepts no other spelling of a path")
    return got


@pytest.mark.parametrize("kind", ["case", "firmlink", "nfd"])
def test_the_running_checkout_gets_no_scope_whatever_the_spelling_of_its_path(root, monkeypatch, kind):
    project = make_project(root, "jadé-live")
    monkeypatch.setattr(scopes, "_live_code_root", lambda: project)   # an editable install from this checkout
    spelled = _spellings(root, "jadé-live").get(kind)
    if spelled is None:
        pytest.skip(f"this volume does not accept the {kind} spelling")
    assert write_scopes(spelled)[1] == []
    assert write_scopes(project, cwd=spelled)[1] == []
    assert offered_for("Write", {"file_path": "jade/hooks.py", "content": "x = 1\n"}, spelled)[1] == []   # a relative path in a folder spelled another way
    assert shell_scopes("ssh host uptime", spelled)[1] == []


def test_a_checkout_that_is_not_running_still_gets_one_scope_whatever_the_spelling_and_the_key_is_the_same(root, monkeypatch):
    project = make_project(root, "jadé-other")
    monkeypatch.setattr(scopes, "_live_code_root", lambda: str(root / "elsewhere"))
    keys = {write_scopes(project)[1][0].key}
    for spelled in _spellings(root, "jadé-other").values():
        got = write_scopes(spelled)[1]
        assert kinds(got) == ["rule_project"]
        keys.add(got[0].key)
    assert len(keys) == 1   # a confirmed answer holds for the project under any spelling of its path
