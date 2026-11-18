"""PR-D of the open-items plan: `jade review`, `jade review confirm` and `jade knows` print every label on one printable line, the confirm summary is three short
sentences, and in the preferences template a newer deny hides an older allow of the same kind."""
from __future__ import annotations

import re
from datetime import timedelta

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


def row(label, answer, rule="R-11", class_key=None, exact_key="x", scope_key=None):
    return {"answer": answer, "label": label, "rule_id": rule, "scope_kind": None, "scope_key": scope_key, "scope_label": None, "class_key": class_key, "exact_key": exact_key, "strength": "strong"}


def stamped(rows):
    """Rows come newest first: give each a time one second older than the one before, and confirm each allow at that time (what a real ledger row holds)."""
    out = []
    for i, r in enumerate(rows):
        when = util.ts(util.now() - timedelta(seconds=i))
        out.append({"ts": when, "confirmed_ts": when if r["answer"] == "allow" else None, "last_used_ts": None, "source": None, **r})
    return out


def fine(rows):
    return [x for x in ops.knows_template(stamped(rows)).splitlines() if "I am fine with" in x]


def test_a_newer_deny_hides_an_older_allow_of_the_same_class_key():
    assert fine([row("git reset --hard HEAD~1", "deny", class_key="C", exact_key="e1"), row("git reset --hard HEAD~2", "allow", class_key="C", exact_key="e2")]) == []


def test_a_newer_allow_over_an_older_deny_still_shows():
    assert len(fine([row("git reset --hard HEAD~1", "allow", class_key="C", exact_key="e1"), row("git reset --hard HEAD~2", "deny", class_key="C", exact_key="e2")])) == 1


def test_a_deny_does_not_hide_an_allow_with_other_keys_of_the_same_rule():
    assert len(fine([row("gh pr create", "deny", exact_key="e1"), row("gh pr merge 5", "allow", exact_key="e2")])) == 1


# ---- through the real caller: knows_edit reads the ledger, writes the preferences file

def edit_and_read():
    p = ops.knows_edit(stdin_isatty=True, editor="true")
    return [x for x in p.read_text().splitlines() if "I am fine with" in x]


def record(answer, label, key, rule="R-11", scope=None, confirm=False, class_key=None):
    db = ledger.open(timeout=5)
    try:
        with db.tx():
            aid = db.record_human_answer(session="sess-out-0002", decision_id=None, rule_id=rule, class_key=class_key, exact_key=key, answer=answer, channel="chat",
                                         label=label, scope=scope)
            if answer == "allow":   # a minute older: a deny recorded next in the same second is newer, as memory.lookup compares the answer times
                db.con.execute("UPDATE human_answers SET ts=? WHERE answer_id=?", (util.ts(util.now() - timedelta(minutes=1)), aid))
            if confirm:
                db.confirm_answers([aid])
    finally:
        db.close()


def record_call(answer, cmd, confirm=False):
    """Record a call the way the chat answer does: the gate's own exact key and class key (jade.memory.keys of policy.assess)."""
    from jade import memory, policy

    a = policy.assess("Bash", {"command": cmd}, "/tmp/x")
    exact, cls, _ = memory.keys(a, "Bash", {"command": cmd}, None)
    record(answer, cmd, exact, rule=a.rule_id, confirm=confirm, class_key=cls)


def test_knows_edit_a_later_deny_hides_a_confirmed_allow():
    record("allow", "git reset --hard HEAD~1", "k1", confirm=True)
    assert len(edit_and_read()) == 1
    ops.preferences_path().unlink()
    record("deny", "git reset --hard HEAD~1", "k1")
    assert edit_and_read() == []


SCOPE = {"kind": "merge_project", "key": "S1", "label": "gh pr merge * in /p"}


def test_knows_edit_a_deny_of_a_covered_call_leaves_the_scope_line_one_row():
    record("allow", "gh pr merge 940 --squash", "k940", rule="R-10", scope=SCOPE, confirm=True)
    record("deny", "gh pr merge 940 --squash", "k940", rule="R-10")
    assert len(edit_and_read()) == 1   # the scope still decides other pull requests


def test_knows_edit_a_deny_of_a_covered_call_leaves_the_scope_line_two_rows():
    record("allow", "gh pr merge 939 --squash", "k939", rule="R-10", scope=SCOPE, confirm=True)
    record("allow", "gh pr merge 940 --squash", "k940", rule="R-10", scope=SCOPE, confirm=True)
    record("deny", "gh pr merge 940 --squash", "k940", rule="R-10")
    assert len(edit_and_read()) == 1   # once, not hidden and not twice


def test_knows_edit_a_deny_of_another_merge_leaves_the_scope_line():
    record("allow", "gh pr merge 940 --squash", "k940", rule="R-10", scope={"kind": "merge_project", "key": "S1", "label": "gh pr merge * in /p"}, confirm=True)
    record("deny", "gh pr merge 941 --squash", "k941", rule="R-10")
    assert len(edit_and_read()) == 1


def test_knows_edit_a_hint_allow_never_appears():
    record("allow", "git clean -fd", "k2")
    assert edit_and_read() == []


@pytest.mark.parametrize("deny", ['cd "/a b" && git reset --hard HEAD~1', "cd /p; git reset --hard HEAD~1", "git -C /p reset --hard HEAD~1",
                                  "VAR=1 git reset --hard HEAD~1", "/usr/bin/git reset --hard HEAD~1"])
def test_knows_edit_a_deny_in_another_spelling_hides_the_allow_of_the_same_kind(deny):
    record_call("allow", "git reset --hard HEAD~1", confirm=True)
    assert len(edit_and_read()) == 1
    ops.preferences_path().unlink()
    record_call("deny", deny)
    assert edit_and_read() == []


def test_knows_edit_a_deny_of_gh_pr_create_does_not_hide_gh_pr_merge():
    record_call("allow", "gh pr merge 5", confirm=True)
    record_call("deny", "gh pr create --title x")
    assert len(edit_and_read()) == 1   # same first two words, other kind of call


def test_knows_edit_two_cd_allows_stay_two_lines():
    record_call("allow", "cd /p && git push origin a", confirm=True)
    record_call("allow", "cd /p && git push origin main", confirm=True)
    assert len(edit_and_read()) == 2   # each push has its own key: the shared `cd /p &&` start does not merge them


def test_knows_edit_a_cd_prefix_on_the_allow_still_prints_it():
    record_call("allow", "cd /p && gh pr merge 940 --squash", confirm=True)
    assert len(edit_and_read()) == 1
