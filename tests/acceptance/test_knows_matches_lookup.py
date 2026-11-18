"""RR8-7, RR8-8: the `jade knows edit` template lists a line exactly when `memory.lookup` would still honour that answer for some call it covers.

Each case records real answers (chat path, then `jade review confirm`), asks `memory.lookup` for every call the answers cover, and compares the set of rows lookup
returns with the set of lines the template prints. One kind per case: exact, class, kind_any, kind_project, merge_project; and rows too old or unused too long.
"""

import dataclasses
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from jade import ledger, memory, ops, policy, scopes, util
from jade.util import now, ts

SESSION = "sess-knows-lookup-0001"
RESET = "git reset --hard HEAD~1"


@pytest.fixture
def short():
    root = Path(tempfile.mkdtemp(prefix="jkl-", dir="/tmp")).resolve()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def make_repo(root, name):
    r = root / name
    (r / ".git").mkdir(parents=True)
    (r / ".git" / "config").write_text(f'[remote "origin"]\n\turl = https://github.com/owner/{name}.git\n')
    return str(r)


@pytest.fixture
def p(short):
    return make_repo(short, "repoA")


@pytest.fixture
def q(short):
    return make_repo(short, "repoB")


def merge(n):
    return f"gh pr merge {n} --repo github.com/owner/repoA --squash"


def record(cmd, cwd, answer, kind=None, age=0, confirmed_age=None, used_age=None):
    """One answer, `age` days old (a bigger age is older). An allow is confirmed (strong); a deny stays a hint, as the chat path leaves it."""
    a = policy.assess("Bash", {"command": cmd}, cwd)
    exact, cls, _ = memory.keys(a, "Bash", {"command": cmd}, {})
    scope = dataclasses.asdict([s for s in scopes.offered(a, "Bash", {"command": cmd}, {}, cwd) if s.kind == kind][0]) if kind else None
    d = ledger.open(timeout=5)
    try:
        with d.tx():
            aid = d.record_human_answer(session=SESSION, decision_id=None, rule_id=a.rule_id, class_key=cls, exact_key=exact, answer=answer, channel="chat",
                                        label=cmd, scope=scope)
            d.con.execute("UPDATE human_answers SET ts=? WHERE answer_id=?", (ts(now() - timedelta(days=age, seconds=1)), aid))
            if answer == "allow":
                d.confirm_answers([aid])
                ca = age if confirmed_age is None else confirmed_age
                d.con.execute("UPDATE human_answers SET confirmed_ts=?, last_used_ts=? WHERE answer_id=?",
                              (ts(now() - timedelta(days=ca)), ts(now() - timedelta(days=used_age)) if used_age is not None else None, aid))
    finally:
        d.close()
    return aid


def honoured_by_lookup(calls):
    d = ledger.open(timeout=5)
    try:
        out = set()
        for cmd, cwd in calls:
            a = policy.assess("Bash", {"command": cmd}, cwd)
            r = memory.lookup(d, a, "Bash", {"command": cmd}, {}, cwd)
            if r is not None:
                out.add(util.one_line(r["scope_label"] or r["label"]))
        return out
    finally:
        d.close()


def printed():
    d = ledger.open(timeout=5)
    try:
        text = ops.knows_template(d.human_answers(limit=100000))
    finally:
        d.close()
    return {x.split("fine with: ", 1)[1].rsplit("  [", 1)[0] for x in text.splitlines() if "I am fine with: " in x}


def same(calls):
    want = honoured_by_lookup(calls)
    assert printed() == want
    return want


# ---- exact (a merge without a scope: one key per call)

def test_exact_allow_alone(p):
    record(merge(940), p, "allow", age=2)
    assert same([(merge(940), p)])


def test_exact_allow_then_deny_of_the_same_call(p):
    record(merge(940), p, "allow", age=2)
    record(merge(940), p, "deny", age=1)
    assert not same([(merge(940), p)])


def test_exact_allow_then_deny_of_another_call(p):
    record(merge(940), p, "allow", age=2)
    record(merge(941), p, "deny", age=1)
    assert same([(merge(940), p), (merge(941), p)])


# ---- class (no scope)

def test_class_allow_alone(p):
    record(RESET, p, "allow", age=2)
    assert same([(RESET, p), (f"cd {p} && {RESET}", "/tmp")])


def test_class_allow_then_deny_spelled_with_cd(p):
    record(RESET, p, "allow", age=2)
    record(f"cd {p} && {RESET}", "/tmp", "deny", age=1)
    assert not same([(RESET, p), (f"cd {p} && {RESET}", "/tmp")])


def test_class_allow_then_older_deny_stays(p):
    record(RESET, p, "deny", age=3)
    record(RESET, p, "allow", age=2)
    assert same([(RESET, p)])


# ---- kind_any (RR8-7 K0, K1, K2, K6)

def test_kind_any_alone(p, q):
    record(RESET, p, "allow", kind="kind_any", age=2)
    assert same([(RESET, p), (RESET, q)])


def test_kind_any_then_deny_of_the_same_call(p, q):
    record(RESET, p, "allow", kind="kind_any", age=2)
    record(RESET, p, "deny", age=1)
    assert not same([(RESET, p)])   # V6-06: the deny is of this place; repoB (the other call) is a kind of its own and stays allowed


def test_kind_any_then_deny_spelled_with_cd(p, q):
    record(RESET, p, "allow", kind="kind_any", age=2)
    record(f"cd {p} && {RESET}", "/tmp", "deny", age=1)   # the same call in the same place: the leading cd is read
    assert not same([(RESET, p)])


def test_kind_any_then_deny_of_the_same_call_in_another_place_leaves_this_place(p, q):
    record(RESET, p, "allow", kind="kind_any", age=2)
    record(RESET, q, "deny", age=1)   # V6-06: a kind has a place; a deny in repoB does not take the allow out in repoA
    assert same([(RESET, p)])


def test_kind_any_then_deny_of_another_kind(p, q):
    record(RESET, p, "allow", kind="kind_any", age=2)
    record("git reset --hard HEAD~3", p, "deny", age=1)
    assert same([(RESET, p), (RESET, q)])


# ---- kind_project (RR8-7 K3, K4, K5)

def test_kind_project_alone(p):
    record(RESET, p, "allow", kind="kind_project", age=2)
    assert same([(RESET, p), (f"cd {p} && {RESET}", "/tmp")])


def test_kind_project_then_deny_of_the_same_call(p):
    record(RESET, p, "allow", kind="kind_project", age=2)
    record(RESET, p, "deny", age=1)
    assert not same([(RESET, p), (f"cd {p} && {RESET}", "/tmp")])


def test_kind_project_then_deny_in_another_folder_leaves_the_project(p):
    record(RESET, p, "allow", kind="kind_project", age=2)
    record(RESET, "/tmp", "deny", age=1)   # V6-06: another folder is another place
    assert same([(RESET, p), (f"cd {p} && {RESET}", "/tmp")])


# ---- merge_project (RR8-7 M1: a deny of one PR leaves the scope for the others)

def test_merge_project_alone(p):
    record(merge(940), p, "allow", kind="merge_project", age=2)
    assert same([(merge(940), p), (merge(941), p)])


def test_merge_project_then_deny_of_one_pr(p):
    record(merge(940), p, "allow", kind="merge_project", age=2)
    record(merge(940), p, "deny", age=1)
    assert same([(merge(940), p), (merge(941), p)])


# ---- expired by age, unused too long (RR8-8)

@pytest.mark.parametrize("kind", [None, "kind_any", "kind_project", "merge_project"])
def test_confirmed_more_than_cap_days_ago_is_not_listed(p, kind):
    cmd = merge(940) if kind == "merge_project" else RESET
    record(cmd, p, "allow", kind=kind, age=memory.CAP_DAYS + 1)
    assert not same([(cmd, p), (merge(941), p)])


@pytest.mark.parametrize("kind", [None, "kind_any", "kind_project", "merge_project"])
def test_not_used_for_use_days_is_not_listed(p, kind):
    cmd = merge(940) if kind == "merge_project" else RESET
    record(cmd, p, "allow", kind=kind, age=memory.USE_DAYS + 5)
    assert not same([(cmd, p), (merge(941), p)])


def test_used_recently_keeps_an_old_confirm(p):
    record(RESET, p, "allow", kind="kind_any", age=memory.USE_DAYS + 5, used_age=1)
    assert same([(RESET, p)])
