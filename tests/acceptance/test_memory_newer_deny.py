"""PR-C wave 2: a deny (hint or strong) newer than a strong allow stops `memory.lookup` from using that allow, for the same exact key, class key or scope key.

A deny row stores the exact key and, for a class call, the class key (chat and dialog Deny path). It stores no scope key. So a deny blocks a scope allow only for
the very call it was given for (same exact key): a deny of `gh pr merge 941` does not block a merge scope allow for `gh pr merge 942`.
"""

import dataclasses
import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from jade import ledger, memory, paths, policy, scopes
from jade.util import now, ts

SESSION = "sess-newer-deny-0001"
MERGE = "gh pr merge 940 --repo github.com/owner/repoA --squash"
CLASS_CMD = "git reset --hard HEAD~1"


@pytest.fixture
def short():
    root = Path(tempfile.mkdtemp(prefix="jnd-", dir="/tmp")).resolve()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def make_repo(root, name):
    r = root / name
    (r / ".git").mkdir(parents=True)
    (r / ".git" / "config").write_text(f'[remote "origin"]\n\turl = https://github.com/owner/{name}.git\n')
    return str(r)


@pytest.fixture
def repo(short):
    return make_repo(short, "repoA")


@pytest.fixture
def repo_b(short):
    return make_repo(short, "repoB")


@pytest.fixture(autouse=True)
def medium():
    cfg = paths.config()
    cfg["level"] = "medium"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


def db():
    return ledger.open(timeout=5)


def assessed(cmd, cwd):
    return policy.assess("Bash", {"command": cmd}, cwd)


def record(cmd, cwd, answer, kind=None, age_days=0, strong=False, source=None):
    a = assessed(cmd, cwd)
    exact, cls, _ = memory.keys(a, "Bash", {"command": cmd}, {})
    scope = None
    if kind:
        scope = dataclasses.asdict([s for s in scopes.offered(a, "Bash", {"command": cmd}, {}, cwd) if s.kind == kind][0])
    d = db()
    try:
        with d.tx():
            aid = d.record_human_answer(session=SESSION, decision_id=None, rule_id=a.rule_id, class_key=cls, exact_key=exact, answer=answer, channel="chat", label=cmd, scope=scope)
            when = ts(now() - timedelta(days=age_days))
            d.con.execute("UPDATE human_answers SET ts=? WHERE answer_id=?", (when, aid))
            if strong:
                d.confirm_answers([aid]) if answer == "allow" else d.con.execute("UPDATE human_answers SET strength='strong' WHERE answer_id=?", (aid,))
                d.con.execute("UPDATE human_answers SET confirmed_ts=? WHERE answer_id=?", (when, aid))
            if source:
                d.con.execute("UPDATE human_answers SET source=? WHERE answer_id=?", (source, aid))
    finally:
        d.close()
    return aid


def lookup(cmd, cwd):
    d = db()
    try:
        return memory.lookup(d, assessed(cmd, cwd), "Bash", {"command": cmd}, {}, cwd)
    finally:
        d.close()


@pytest.mark.parametrize("strong", [False, True])
@pytest.mark.parametrize("kind,cmd", [("kind_any", CLASS_CMD), ("kind_project", CLASS_CMD), ("merge_project", MERGE)])
def test_a_newer_deny_of_the_same_call_blocks_the_allow(repo, kind, cmd, strong):
    aid = record(cmd, repo, "allow", kind=kind, age_days=2, strong=True)
    assert lookup(cmd, repo)["answer_id"] == aid
    record(cmd, repo, "deny", age_days=1, strong=strong)
    assert lookup(cmd, repo) is None


def test_a_newer_deny_blocks_an_exact_allow_without_a_scope(repo):
    aid = record(CLASS_CMD, repo, "allow", age_days=2, strong=True)
    assert lookup(CLASS_CMD, repo)["answer_id"] == aid
    record(CLASS_CMD, repo, "deny", age_days=1)
    assert lookup(CLASS_CMD, repo) is None


def test_a_deny_older_than_the_allow_does_not_block(repo):
    record(CLASS_CMD, repo, "deny", age_days=3)
    aid = record(CLASS_CMD, repo, "allow", kind="kind_any", age_days=1, strong=True)
    assert lookup(CLASS_CMD, repo)["answer_id"] == aid


def test_a_deny_of_a_different_call_does_not_block(repo):
    aid = record(CLASS_CMD, repo, "allow", kind="kind_any", age_days=2, strong=True)
    record("git clean -fdx", repo, "deny", age_days=1)
    assert lookup(CLASS_CMD, repo)["answer_id"] == aid


def test_a_deny_in_another_project_does_not_block_the_project_scope(repo, repo_b):
    aid = record(CLASS_CMD, repo, "allow", kind="kind_project", age_days=2, strong=True)
    record("git clean -fdx", repo_b, "deny", age_days=1)
    assert lookup(CLASS_CMD, repo)["answer_id"] == aid


def test_a_deny_of_another_pr_does_not_block_the_merge_scope(repo):
    aid = record(MERGE, repo, "allow", kind="merge_project", age_days=2, strong=True)
    record("gh pr merge 941 --repo github.com/owner/repoA --squash", repo, "deny", age_days=1)
    assert lookup(MERGE, repo)["answer_id"] == aid   # the deny row has only the exact key of PR 941: no scope key to compare


def test_a_test_source_deny_does_not_block(repo):
    aid = record(CLASS_CMD, repo, "allow", kind="kind_any", age_days=2, strong=True)
    record(CLASS_CMD, repo, "deny", age_days=1, source="test")
    assert lookup(CLASS_CMD, repo)["answer_id"] == aid


def test_a_revoked_deny_does_not_block(repo):
    aid = record(CLASS_CMD, repo, "allow", kind="kind_any", age_days=2, strong=True)
    did = record(CLASS_CMD, repo, "deny", age_days=1)
    d = db()
    try:
        with d.tx():
            d.con.execute("UPDATE human_answers SET strength='revoked' WHERE answer_id=?", (did,))
    finally:
        d.close()
    assert lookup(CLASS_CMD, repo)["answer_id"] == aid
