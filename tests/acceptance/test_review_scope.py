"""Task 6 of the clear-questions plan: `jade review confirm` and `jade knows list` show the scope of an answer in plain words.

A hint with a scope is a proposal. The text must say what confirming makes standing, how long it lasts and how to undo it, and it must not promise more
than the code does. A hint without a scope reads as before.
"""

import dataclasses
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from jade import ledger, memory, ops, policy, scopes, util

REAL_REFUSAL = util.human_tty_refusal
SESSION = "sess-review-scope-0001"
MERGE = "gh pr merge 940 --repo github.com/owner/repoA --squash"
CLASS_CMD = "git reset --hard HEAD~1"   # a real `class` call (R-11)


@pytest.fixture(autouse=True)
def human_terminal(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)   # the tests play the human at a terminal; one test puts the real check back


@pytest.fixture
def repo():
    root = Path(tempfile.mkdtemp(prefix="jrscope-", dir="/tmp")).resolve() / "repoA"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/owner/repoA.git\n')   # the merge scope reads the remotes (final review finding 2)
    yield str(root)
    shutil.rmtree(root.parent, ignore_errors=True)


def hint(cmd, cwd, kind=None):
    """What a chat click leaves behind: a `hint` row, with the scope of `kind` when one is given."""
    a = policy.assess("Bash", {"command": cmd}, cwd)
    scope = None
    if kind:
        got = [s for s in scopes.offered(a, "Bash", {"command": cmd}, {}, cwd) if s.kind == kind]
        assert got, (cmd, kind)
        scope = dataclasses.asdict(got[0])
    exact, cls, _ = memory.keys(a, "Bash", {"command": cmd}, {})
    d = ledger.open(timeout=5)
    try:
        with d.tx():
            return d.record_human_answer(session=SESSION, decision_id=None, rule_id=a.rule_id, class_key=cls, exact_key=exact, answer="allow", channel="chat",
                                         label=cmd, scope=scope)
    finally:
        d.close()


def strength(aid):
    d = ledger.open(timeout=5)
    try:
        return d.con.execute("SELECT strength FROM human_answers WHERE answer_id=?", (aid,)).fetchone()["strength"]
    finally:
        d.close()


def confirm(answers, all_=False):
    said = []
    it = iter(answers)
    n = ops.review_confirm(stdin_isatty=True, ask=lambda _="": next(it), say=said.append, all_=all_)
    return n, "\n".join(said)


def test_scope_hint_shows_label_lifetime_and_undo(repo):
    aid = hint(MERGE, repo, "merge_project")
    n, text = confirm(["y", "yes"])
    assert n == 1 and strength(aid) == "strong"
    assert f"Would become standing for: gh pr merge * --repo github.com/owner/repoa in {repo} ({memory.USE_DAYS} days after last use)" in text
    assert "jade knows rm" in text and str(memory.CAP_DAYS) in text
    assert "this exact call only" not in text and "this kind of call" not in text
    scope_lines = [ln.strip() for ln in text.splitlines() if ln.startswith("   ")]
    assert scope_lines
    for line in scope_lines:
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            assert len(sentence.split()) <= 20, sentence


def test_two_scopes_of_one_rule_are_two_groups(repo):
    a = hint(CLASS_CMD, repo, "kind_project")
    b = hint(CLASS_CMD, repo, "kind_any")
    said = []
    answers = iter(["y", "n", "yes"])
    ops.review_confirm(stdin_isatty=True, ask=lambda _="": next(answers), say=said.append)
    text = "\n".join(said)
    assert text.count("Would become standing for:") == 2
    assert f"in {repo}" in text and "any project" in text
    assert strength(a) == "strong" and strength(b) == "hint"


def test_two_merge_scopes_in_two_projects_do_not_merge_into_one_group(repo):
    other = str(Path(repo).parent / "repoB")
    (Path(other) / ".git").mkdir(parents=True)
    (Path(other) / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/owner/repoB.git\n')
    hint(MERGE, repo, "merge_project")
    hint("gh pr merge 12 --repo github.com/owner/repoB --squash", other, "merge_project")
    _, text = confirm(["n", "n"])
    assert text.count("Would become standing for:") == 2


def test_kind_project_text_does_not_promise_this_project_only(repo):
    hint(CLASS_CMD, repo, "kind_project")
    _, text = confirm(["n"])
    assert "same exact call" in text.lower()
    assert "this project only" not in text.lower()


def test_n_leaves_the_scope_row_a_hint(repo):
    aid = hint(MERGE, repo, "merge_project")
    n, text = confirm(["n"])
    assert n == 0 and "nothing confirmed" in text and strength(aid) == "hint"


def test_wrong_final_word_cancels_and_leaves_a_hint(repo):
    aid = hint(MERGE, repo, "merge_project")
    n, _ = confirm(["y", "no"])
    assert n == 0 and strength(aid) == "hint"


def test_confirm_still_needs_a_human_terminal(repo, monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", REAL_REFUSAL)
    aid = hint(MERGE, repo, "merge_project")
    with pytest.raises(ops.HumanOnly):
        ops.review_confirm(stdin_isatty=False, ask=lambda _="": "yes", say=lambda *_: None)
    assert strength(aid) == "hint"


def test_hint_without_scope_reads_as_before(repo):
    hint(CLASS_CMD, repo)
    _, text = confirm(["n"])
    assert "would be standing for this kind of call" in text
    assert "Would become standing for:" not in text


def test_knows_list_shows_the_scope_label_for_a_scoped_row(repo):
    hint(MERGE, repo, "merge_project")
    confirm(["y", "yes"])
    out = ops.knows_list()
    assert f"gh pr merge * --repo github.com/owner/repoa in {repo}" in out and "stops deciding" in out


def test_knows_list_without_scope_is_unchanged(repo):
    hint(CLASS_CMD, repo)
    confirm(["y", "yes"])
    out = ops.knows_list()
    assert "kind" in out and CLASS_CMD in out and "scope" not in out


# ------------------------------------------------------------------ final whole-branch review

def test_two_merge_hints_of_one_scope_for_two_prs_are_one_group(repo):
    """Finding 5: one "y" makes the shared scope standing for every PR, so the hints of one scope are one question, not one per PR."""
    a = hint(MERGE, repo, "merge_project")
    b = hint("gh pr merge 941 --repo github.com/owner/repoA --rebase", repo, "merge_project")
    said, prompts = [], []

    def ask(prompt=""):
        prompts.append(prompt)
        return "yes" if prompt.startswith("Type yes") else "y"

    n = ops.review_confirm(stdin_isatty=True, ask=ask, say=said.append)
    text = "\n".join(said)
    assert sum(1 for p in prompts if "stand for this?" in p) == 1, prompts
    assert text.count("Would become standing for:") == 1 and "allowed 2x" in text
    assert "in 1 group(s)" in text
    assert n == 2 and strength(a) == strength(b) == "strong"


@pytest.mark.parametrize("cmd,kind", [(MERGE, "merge_project"), (CLASS_CMD, "kind_project"), (CLASS_CMD, "kind_any")])
def test_the_confirm_text_is_the_scope_covers_text(repo, cmd, kind):
    """Finding 7: the terminal says what the chat said; the text has one source, `scopes.COVERS`."""
    a = policy.assess("Bash", {"command": cmd}, repo)
    sc = next(s for s in scopes.offered(a, "Bash", {"command": cmd}, {}, repo) if s.kind == kind)
    hint(cmd, repo, kind)
    _, text = confirm(["n"])
    assert f"   {sc.covers}" in text.splitlines(), text

