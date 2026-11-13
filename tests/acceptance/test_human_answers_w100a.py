"""W-100a (docs/12 plan v3, PR-5a part 1): the `human_answers` table, the read-only `jade review`, and the hook entry the host fails closed on.

A chat or dialog answer of the human is recorded as a `hint`: it decides nothing and goes to no model (0.6.0 turns hints into standing memory only after
`jade review confirm` in a terminal). `jade review` only reads. `jade hooks install` writes `"onFailure": "block"` on the PreToolUse entry when the host
is new enough to honour it (Claude Code 2.1.295; the probe in evals/probes/hook_ask/RESULTS.md measured 2.1.314), so a hook that crashes or times out
blocks the call instead of letting it run.
"""

import json

import pytest

from jade import cli, ledger, ops, util
from jade.util import ts

SESSION = "sess-w100a-0001"


@pytest.fixture
def db():
    d = ledger.open(timeout=5)
    yield d
    d.close()


def rec(db, **kw):
    args = dict(session=SESSION, decision_id="dec_1", rule_id="R-11", class_key="R-11:git reset --hard", exact_key="k" * 16, answer="allow", channel="chat")
    args.update(kw)
    return db.record_human_answer(**args)


# ------------------------------------------------------------------ the table

def test_the_table_exists_in_a_fresh_ledger(db):
    assert db.con.execute("SELECT count(*) FROM human_answers").fetchone()[0] == 0


def test_an_answer_is_stored_as_a_hint_with_an_event(db):
    aid = rec(db)
    row = db.con.execute("SELECT * FROM human_answers WHERE answer_id=?", (aid,)).fetchone()
    assert row["strength"] == "hint" and row["confirmed_ts"] is None
    assert (row["answer"], row["channel"], row["rule_id"], row["session_id"]) == ("allow", "chat", "R-11", SESSION)
    ev = db.con.execute("SELECT kind, entity_id FROM events WHERE kind='human_answer'").fetchall()
    assert len(ev) == 1 and ev[0]["entity_id"] == aid


@pytest.mark.parametrize("bad", [{"answer": "maybe"}, {"channel": "telepathy"}, {"exact_key": ""}, {"rule_id": ""}])
def test_a_bad_answer_is_refused_and_nothing_is_stored(db, bad):
    with pytest.raises(ValueError):
        rec(db, **bad)
    assert db.con.execute("SELECT count(*) FROM human_answers").fetchone()[0] == 0


def test_listing_is_newest_first_and_filters_by_strength_and_age(db):
    a = rec(db, exact_key="a" * 16)
    b = rec(db, exact_key="b" * 16, answer="deny", channel="dialog")
    rows = db.human_answers()
    assert [r["answer_id"] for r in rows] == [b, a]
    assert db.human_answers(strength="strong") == []
    db.con.execute("UPDATE human_answers SET ts='2000-01-01T00:00:00Z' WHERE answer_id=?", (a,))
    assert [r["answer_id"] for r in db.human_answers(since_s=3600)] == [b]


def test_a_secret_in_a_class_key_is_redacted_before_it_is_stored(db):
    aid = rec(db, class_key="R-14:curl -H 'Authorization: Bearer sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef'")
    row = db.con.execute("SELECT class_key FROM human_answers WHERE answer_id=?", (aid,)).fetchone()
    assert "0123456789abcdef0123456789abcdef" not in row["class_key"]


# ------------------------------------------------------------------ jade review (read-only)

def test_review_of_an_empty_ledger_says_so(db):
    assert "no answers" in ops.review().lower()


def test_review_counts_answers_by_rule_and_channel_and_changes_nothing(db):
    rec(db, exact_key="a" * 16)
    rec(db, exact_key="b" * 16, answer="deny", channel="dialog", rule_id="R-10")
    rec(db, exact_key="c" * 16, rule_id="R-10")
    before = db.con.execute("SELECT count(*) FROM events").fetchone()[0], db.con.execute("SELECT count(*) FROM human_answers").fetchone()[0]
    text = ops.review()
    assert "R-11" in text and "R-10" in text and "chat" in text and "dialog" in text and "3 answer" in text
    assert "2 allow" in text and "1 deny" in text and "3 hint" in text
    after = db.con.execute("SELECT count(*) FROM events").fetchone()[0], db.con.execute("SELECT count(*) FROM human_answers").fetchone()[0]
    assert before == after


def test_review_since_limits_the_window(db):
    old = rec(db, exact_key="a" * 16)
    rec(db, exact_key="b" * 16)
    db.con.execute("UPDATE human_answers SET ts='2000-01-01T00:00:00Z' WHERE answer_id=?", (old,))
    assert "1 answer" in ops.review(since_s=3600)


def test_the_review_command_parses_and_a_bad_since_is_refused(capsys):
    assert cli.build_parser().parse_args(["review"]).cmd == "review"
    assert cli.build_parser().parse_args(["review", "--since", "24h"]).since == "24h"
    assert ops.parse_since("24h") == 86400 and ops.parse_since("7d") == 7 * 86400 and ops.parse_since("90m") == 5400
    for bad in ("", "x", "24", "-5h", "1w"):
        with pytest.raises(ValueError):
            ops.parse_since(bad)


# ------------------------------------------------------------------ onFailure and the whole-entry status

@pytest.fixture
def settings(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setenv("JADE_CLAUDE_SETTINGS", str(p))
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)
    return p


def installed(p):
    return json.loads(p.read_text())["hooks"]


def test_install_adds_on_failure_block_to_pretool_when_the_host_is_new_enough(settings, monkeypatch):
    monkeypatch.setattr(ops, "host_version", lambda: (2, 1, 314))
    ops.hooks_install(stdin_isatty=True)
    h = installed(settings)
    assert h["PreToolUse"][0]["hooks"][0].get("onFailure") == "block"
    for other in ("PostToolUse", "Stop", "SessionStart"):
        assert "onFailure" not in h[other][0]["hooks"][0]


@pytest.mark.parametrize("version", [(2, 1, 294), (2, 0, 999), None])
def test_install_leaves_on_failure_out_for_an_old_or_unknown_host(settings, monkeypatch, version):
    monkeypatch.setattr(ops, "host_version", lambda: version)
    ops.hooks_install(stdin_isatty=True)
    assert "onFailure" not in installed(settings)["PreToolUse"][0]["hooks"][0]


def test_status_is_false_when_the_installed_entry_differs_from_what_install_writes(settings, monkeypatch):
    monkeypatch.setattr(ops, "host_version", lambda: (2, 1, 314))
    ops.hooks_install(stdin_isatty=True)
    assert all(ops.hooks_status().values())
    data = json.loads(settings.read_text())
    data["hooks"]["PreToolUse"][0]["hooks"][0].pop("onFailure")   # an install made before this release
    settings.write_text(json.dumps(data))
    st = ops.hooks_status()
    assert st["PreToolUse"] is False and st["PostToolUse"] is True


@pytest.mark.parametrize("edit", [
    lambda e: e["hooks"][0].__setitem__("timeout", 5),
    lambda e: e.__setitem__("matcher", "Bash"),
])
def test_status_is_false_for_a_changed_timeout_or_matcher(settings, monkeypatch, edit):
    monkeypatch.setattr(ops, "host_version", lambda: (2, 1, 314))
    ops.hooks_install(stdin_isatty=True)
    data = json.loads(settings.read_text())
    edit(data["hooks"]["PreToolUse"][0])
    settings.write_text(json.dumps(data))
    assert ops.hooks_status()["PreToolUse"] is False


def test_an_old_host_install_still_counts_as_installed(settings, monkeypatch):
    monkeypatch.setattr(ops, "host_version", lambda: (2, 1, 200))
    ops.hooks_install(stdin_isatty=True)
    assert all(ops.hooks_status().values())


def test_the_check_row_names_the_one_command_that_fixes_a_stale_entry(settings, monkeypatch):
    monkeypatch.setattr(ops, "host_version", lambda: (2, 1, 314))
    ops.hooks_install(stdin_isatty=True)
    data = json.loads(settings.read_text())
    data["hooks"]["PreToolUse"][0]["hooks"][0].pop("onFailure")
    settings.write_text(json.dumps(data))
    rows = {name: (ok, detail) for name, ok, detail in ops.check(live=False)}
    ok, detail = rows["hooks installed"]
    assert not ok and "jade hooks install" in detail and "PreToolUse" in detail


def test_the_host_version_parser_reads_the_cli_output(monkeypatch):
    class R:
        stdout = "2.1.314 (Claude Code)\n"
        returncode = 0

    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: R())
    assert ops.host_version() == (2, 1, 314)

    def boom(*a, **k):
        raise OSError("no claude")

    monkeypatch.setattr(ops.subprocess, "run", boom)
    assert ops.host_version() is None
    assert ts()
