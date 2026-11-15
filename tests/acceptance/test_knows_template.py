"""`jade knows edit` template (clear questions and scopes, task 7): one commented line per kind of standing answer, two model sentences, only `#` lines."""
from __future__ import annotations

import pytest

from jade import askplain, ledger, ops, util


@pytest.fixture(autouse=True)
def human(monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


def row(label, rule="R-11", scope_kind=None, scope_key=None, scope_label=None, answer="allow"):
    return {"answer": answer, "label": label, "rule_id": rule, "scope_kind": scope_kind, "scope_key": scope_key, "scope_label": scope_label,
            "class_key": None, "exact_key": "x", "strength": "strong"}


def lines_of(rows):
    return ops.knows_template(rows).splitlines()


def test_rows_with_one_scope_become_one_line_with_the_risk_line():
    rows = [row(f"gh pr merge {n} --squash", rule="R-11", scope_kind="merge_project", scope_key="merge:proj", scope_label="merging pull requests in project demo")
            for n in (1, 2, 3)]
    got = [x for x in lines_of(rows) if "I am fine with" in x]
    assert got == [f"# I am fine with: merging pull requests in project demo  [R-11]  (risk: {askplain.risk_line('R-11')})"]


def test_rows_without_a_scope_use_a_short_label_grouped_by_rule_and_first_word():
    rows = [row("git reset --hard HEAD~1"), row("git reset --hard HEAD~2"), row("git clean -fd"), row("docker rm web", rule="R-07")]
    got = [x for x in lines_of(rows) if "I am fine with" in x]
    assert len(got) == 2   # the two git calls of one rule share the first word: one line, named by the newest row
    assert any(x.startswith("# I am fine with: git reset --hard HEAD~1  [R-11]") for x in got)
    assert any("docker rm web  [R-07]" in x and f"(risk: {askplain.risk_line('R-07')})" in x for x in got)


def test_a_long_label_is_cut_short():
    got = [x for x in lines_of([row("echo " + "a" * 300)]) if "I am fine with" in x]
    assert len(got) == 1 and len(got[0]) < 250


def test_denies_and_rows_without_text_are_left_out():
    rows = [row("git push --force", answer="deny"), row(None)]
    assert not [x for x in lines_of(rows) if "I am fine with" in x]


def test_the_file_starts_with_two_header_lines_then_two_model_sentences():
    ls = lines_of([])
    assert ls[0].startswith("# What JADE may decide for you")
    assert ls[1] == "# Lines that start with # are ignored."
    text = "\n".join(ls)
    assert "ssh <user@host>" in text and "(the <name> project)" in text and "Always ask me before" in text
    assert "the <name> projects on GitHub and GitLab" in text and "Never push to the main branch." in text


def test_every_line_starts_with_a_hash_and_there_are_at_most_forty():
    rows = [row(f"tool{n} run", rule=f"R-{n:02d}") for n in range(80)]
    ls = lines_of(rows)
    assert all(x.startswith("#") for x in ls)
    assert len(ls) <= 40
    assert not lines_of([])[-1] == ""


def test_knows_edit_uses_the_template_for_a_new_file_and_never_rewrites_an_existing_one():
    d = ledger.open(timeout=5)
    try:
        with d.tx():
            aid = d.record_human_answer(session="s", decision_id=None, rule_id="R-11", class_key=None, exact_key="k", answer="allow", channel="chat",
                                        label="git reset --hard HEAD~1")
            d.confirm_answers([aid])
    finally:
        d.close()
    p = ops.knows_edit(stdin_isatty=True, editor="true")
    text = p.read_text()
    assert "# I am fine with: git reset --hard HEAD~1  [R-11]" in text and "ssh <user@host>" in text
    p.write_text("# mine\n")
    assert ops.knows_edit(stdin_isatty=True, editor="true").read_text() == "# mine\n"
