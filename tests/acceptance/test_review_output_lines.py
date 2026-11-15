"""PR-D of the open-items plan: `jade review`, `jade review confirm` and `jade knows` print every label on one printable line, the confirm summary is three short
sentences, and in the preferences template a newer deny hides an older allow of the same kind."""
from __future__ import annotations

import re

import pytest

from jade import ledger, ops, util

DIRTY = "a\x1b[2Kb\x07c\nd"


@pytest.fixture(autouse=True)
def human_terminal(monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


def clean(text: str | list[str]) -> None:
    if isinstance(text, list):
        assert all("\n" not in x for x in text), text   # a newline inside one printed item is two lines
        text = "\n".join(text)
    assert "\x1b" not in text and "\x07" not in text, repr(text)
    for line in text.splitlines():
        assert all(c.isprintable() for c in line), repr(line)


def add(db, *, label=DIRTY, scope=True, key="k1", rule="R-11"):
    aid = db.record_human_answer(session="sess-out-0001", decision_id=None, rule_id=rule, class_key=None, exact_key=key, answer="allow", channel="chat", label="x",
                                 scope={"kind": "merge_project", "key": "scope-" + key, "label": "x"} if scope else None)
    # a row stored before 0.7.0 can hold raw control characters: write them straight into the table
    db.con.execute("UPDATE human_answers SET label=?, scope_label=? WHERE answer_id=?", (label, label if scope else None, aid))
    return aid


def test_one_line_helper():
    assert util.one_line(None) == ""
    assert util.one_line(DIRTY) == "a [2Kb c d"
    assert util.one_line("  x \t y\n") == "x y"


def test_review_confirm_prints_clean_lines():
    db = ledger.open(timeout=5)
    try:
        with db.tx():
            add(db)
            add(db, scope=False, key="k2", rule="R-07")
    finally:
        db.close()
    out: list[str] = []
    ops.review_confirm(stdin_isatty=True, ask=lambda _="": "no", say=out.append, all_=True)
    assert any("a [2Kb c d" in x for x in out)
    clean(out)


def test_knows_list_and_template_print_clean_lines():
    db = ledger.open(timeout=5)
    try:
        with db.tx():
            aid = add(db)
            db.confirm_answers([aid])
        rows = db.human_answers(strength="strong", limit=100)
    finally:
        db.close()
    listing = ops.knows_list()
    assert "a [2Kb c d" in listing
    clean(listing)
    tpl = ops.knows_template(rows)
    clean(tpl)
    assert all(x.startswith("#") for x in tpl.splitlines())


def test_review_lines_print_clean_lines():
    db = ledger.open(timeout=5)
    try:
        with db.tx():
            aid = add(db)
            db.event(aid, "memory_allowed", detail={"decision_id": "dec_1", "channel": "memory"})
        lines = ops.review_lines(db, None)
    finally:
        db.close()
    assert any("a [2Kb c d" in x for x in lines)
    clean(lines)


def test_confirm_summary_is_three_short_sentences():
    db = ledger.open(timeout=5)
    try:
        with db.tx():
            add(db)
    finally:
        db.close()
    out: list[str] = []
    ops.review_confirm(stdin_isatty=True, ask=lambda _="": "no", say=out.append, all_=True)
    summary = [x for x in out if "will become standing." in x or "Undo one with" in x or "runs without a question" in x]
    assert len(summary) >= 3, out
    sentences = [s for line in summary for s in re.split(r"(?<=[.!?])\s+", line.strip())]
    assert sentences and all(len(s.split()) <= 20 and s.endswith(".") for s in sentences), sentences
    text = " ".join(summary)
    assert "1 answer(s) in 1 group(s) will become standing." in text
    assert "Undo one with jade knows rm ID." in text and "jade knows list shows each ID." in text


def row(label, answer, rule="R-11"):
    return {"answer": answer, "label": label, "rule_id": rule, "scope_kind": None, "scope_key": None, "scope_label": None, "class_key": None, "exact_key": "x", "strength": "strong"}


def fine(rows):
    return [x for x in ops.knows_template(rows).splitlines() if "I am fine with" in x]


def test_a_newer_deny_hides_an_older_allow():
    assert fine([row("git reset --hard HEAD~1", "deny"), row("git reset --hard HEAD~2", "allow")]) == []


def test_a_newer_allow_over_an_older_deny_still_shows():
    assert len(fine([row("git reset --hard HEAD~1", "allow"), row("git reset --hard HEAD~2", "deny")])) == 1


# ---- through the real caller: knows_edit reads the ledger, writes the preferences file

def edit_and_read():
    p = ops.knows_edit(stdin_isatty=True, editor="true")
    return [x for x in p.read_text().splitlines() if "I am fine with" in x]


def record(answer, label, key, rule="R-11", scope=None, confirm=False):
    db = ledger.open(timeout=5)
    try:
        with db.tx():
            aid = db.record_human_answer(session="sess-out-0002", decision_id=None, rule_id=rule, class_key=None, exact_key=key, answer=answer, channel="chat",
                                         label=label, scope=scope)
            if confirm:
                db.confirm_answers([aid])
    finally:
        db.close()


def test_knows_edit_a_later_deny_hides_a_confirmed_allow():
    record("allow", "git reset --hard HEAD~1", "k1", confirm=True)
    assert len(edit_and_read()) == 1
    ops.preferences_path().unlink()
    record("deny", "git reset --hard HEAD~1", "k1")
    assert edit_and_read() == []


def test_knows_edit_a_later_deny_hides_a_confirmed_scope_line():
    record("allow", "gh pr merge 940 --squash", "k940", rule="R-10", scope={"kind": "merge_project", "key": "S1", "label": "gh pr merge * in /p"}, confirm=True)
    record("deny", "gh pr merge 941 --squash", "k941", rule="R-10")
    assert edit_and_read() == []


def test_knows_edit_a_hint_allow_never_appears():
    record("allow", "git clean -fd", "k2")
    assert edit_and_read() == []
