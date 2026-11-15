"""Re-review 10: the owner's newest answer wins on every path that reads the answers.

- RR10-1: Jev is never shown an allow that `memory.lookup` would not honour, it sees the owner's denies of the rule as denies, and a call of a kind the owner
  denied (same class key, another exact call) is never sent to Jev.
- RR10-2: `jade review mark wrong` also retires the project-scoped answers of the kind it marks (their `family_key` is the class key).
- RR10-3: lookup and the `jade knows edit` template stay fast with thousands of answers of one kind.
- RR10-5: an empty last-use time reads as the old SQL read it (used long ago), never as "never used".
"""

# ruff: noqa: F811   (the fixtures are imported from the W-101 and W-102 tests and then named as test arguments)
import dataclasses
import time
from datetime import timedelta

import pytest

from jade import hooks, memory, ops, policy, proxy, scopes
from jade.util import now, ts
from tests.acceptance import test_jev_proxy_w102 as j
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_jev_proxy_w102 import KIND, prov, ready  # noqa: F401  (fixtures)
from tests.acceptance.test_memory_w101 import SESSION, enforce, medium  # noqa: F401  (fixtures)


def keys_of(cmd):
    a = policy.assess("Bash", {"command": cmd}, "/tmp")
    exact, cls, _ = memory.keys(a, "Bash", {"command": cmd}, {})
    return a, exact, cls


def row(rule, exact, cls, answer, label, when, strength="strong", scope=None):
    """One answer of the owner with its time set (`ts` has second resolution, so the order of two answers is set by hand)."""
    d = m.db()
    try:
        with d.tx():
            aid = d.record_human_answer(session=SESSION, decision_id=None, rule_id=rule, class_key=cls, exact_key=exact, answer=answer, channel="chat", label=label, scope=scope)
            if strength == "strong":
                d.confirm_answers([aid])
            d.con.execute("UPDATE human_answers SET ts=? WHERE answer_id=?", (ts(when), aid))
    finally:
        d.close()
    return aid


# ------------------------------------------------------------------ RR10-1

def test_an_allow_taken_out_by_a_deny_of_a_sibling_of_its_kind_is_not_evidence_and_the_kind_is_not_sent(ready):
    a, exact, cls = keys_of(KIND)
    assert cls, "the call must be a class call"
    t0 = now() - timedelta(minutes=5)
    row(a.rule_id, "x" * 64, cls, "allow", "git reset --hard HEAD~3   (allowed)", t0)                        # another exact call of the kind
    row(a.rule_id, "y" * 64, cls, "deny", "git reset --hard HEAD~3   (denied)", t0 + timedelta(minutes=1), strength="hint")   # a newer deny of a sibling
    d = m.db()
    try:
        assert memory.lookup(d, a, "Bash", {"command": KIND}, {}, "/tmp") is None
        past = proxy.past_answers(d, a.rule_id)
        assert [p for p in past if p["answer"] == "allow"] == []
        assert [p["label"] for p in past if p["answer"] == "deny"] == ["git reset --hard HEAD~3   (denied)"]
        assert proxy.denied_before(d, a.hold_key, cls) is True
    finally:
        d.close()
    assert j.asked(hooks.pretool(m.call(KIND)))
    assert ready.sent == []   # a kind the owner denied is never sent to Jev


def test_an_allow_taken_out_by_a_deny_of_the_same_call_is_not_sent(ready):
    a1, e1, c1 = keys_of(m.CMD)
    t0 = now() - timedelta(minutes=5)
    row(a1.rule_id, e1, c1, "allow", m.CMD, t0)
    row(a1.rule_id, e1, c1, "deny", m.CMD, t0 + timedelta(minutes=1), strength="hint")
    hooks.pretool(m.call(KIND))   # a sibling of the rule with another class key: it may go to Jev, but with the deny, never the allow
    for sent in ready.sent:
        assert {(p["answer"], p["label"]) for p in sent["state"]["past_answers"]} == {("deny", m.CMD)}


def test_the_owner_denies_of_the_rule_are_sent_as_denies_next_to_the_allows(ready):
    m.stored(m.CMD)                                              # a confirmed allow of R-11, of another kind
    m.stored("git reset --hard HEAD~5", answer_="deny", strength="hint")
    hooks.pretool(m.call(KIND))
    (sent,) = ready.sent
    got = {(p["rule"], p["answer"], p["label"]) for p in sent["state"]["past_answers"]}
    assert got == {("R-11", "allow", m.CMD), ("R-11", "deny", "git reset --hard HEAD~5")}


def test_denies_alone_without_preferences_are_never_sent(medium, prov):
    j.owner_answers()
    m.stored("git reset --hard HEAD~5", answer_="deny", strength="hint")
    assert j.asked(hooks.pretool(m.call(KIND)))
    assert prov.sent == []   # no preferences and no allow of the rule: nothing for Jev to go by


def test_denies_sent_are_capped_and_newest_first(medium):
    a, _, _ = keys_of(KIND)
    t0 = now() - timedelta(hours=1)
    for i in range(proxy.MAX_PAST + 3):
        row(a.rule_id, f"{i:064d}", None, "deny", f"deny {i}", t0 + timedelta(minutes=i), strength="hint")
    d = m.db()
    try:
        past = proxy.past_answers(d, a.rule_id)
    finally:
        d.close()
    assert [p["label"] for p in past] == [f"deny {i}" for i in reversed(range(3, proxy.MAX_PAST + 3))]


# ------------------------------------------------------------------ RR10-2

def test_mark_wrong_retires_the_project_rows_of_the_kind(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    cmd = "git reset --hard HEAD~2"
    a = policy.assess("Bash", {"command": cmd}, str(proj))
    exact, cls, _ = memory.keys(a, "Bash", {"command": cmd}, {})
    (scope,) = [s for s in scopes.offered(a, "Bash", {"command": cmd}, {}, str(proj)) if s.kind == "kind_project"]
    aid = row(a.rule_id, exact, cls, "allow", cmd, now(), strength="hint", scope=dataclasses.asdict(scope))
    d = m.db()
    try:
        stored = d.con.execute("SELECT class_key, family_key FROM human_answers WHERE answer_id=?", (aid,)).fetchone()
        assert stored["class_key"] is None and stored["family_key"] == cls
        assert d.retire_answers("e" * 64, cls, None) == 1   # what a Jev decision of another call of the kind passes
        assert d.con.execute("SELECT strength FROM human_answers WHERE answer_id=?", (aid,)).fetchone()["strength"] == "revoked"
    finally:
        d.close()


# ------------------------------------------------------------------ RR10-3

def _many(n, cls="c" * 64):
    """n confirmed allows of one kind, each taken out by a newer deny, the denies stored newest first (the order that made the old loop n x n)."""
    d = m.db()
    base = now() - timedelta(days=20)
    try:
        with d.tx():
            for i in reversed(range(n)):
                d.con.execute("INSERT INTO human_answers(answer_id, ts, rule_id, class_key, exact_key, answer, channel, strength) VALUES(?,?,?,?,?,?,?,?)",
                              (f"d{i}", ts(base + timedelta(minutes=2 * i + 1)), "R-11", cls, f"de{i}", "deny", "chat", "hint"))
            for i in range(n):
                d.con.execute("INSERT INTO human_answers(answer_id, ts, rule_id, class_key, exact_key, answer, channel, strength, confirmed_ts, label) VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (f"a{i}", ts(base + timedelta(minutes=2 * i)), "R-11", cls, f"ae{i}", "allow", "chat", "strong", ts(now() - timedelta(days=1)), f"x{i}"))
    finally:
        d.close()
    return cls


def _best(fn, tries=3):
    best = None
    for _ in range(tries):
        t = time.perf_counter()
        out = fn()
        el = time.perf_counter() - t
        best = el if best is None or el < best else best
    return best, out


@pytest.mark.timing
def test_lookup_stays_fast_with_thousands_of_answers_of_one_kind(monkeypatch):
    cls = _many(3000)
    monkeypatch.setattr(memory, "keys", lambda a, t, ti, fp: ("ae0", cls, "class"))
    monkeypatch.setattr(scopes, "keys_for", lambda a, t, ti, fp, cwd: [])
    d = m.db()
    try:
        el, got = _best(lambda: memory.lookup(d, object(), "Bash", {}, {}, ""))
        assert got is None
        assert el < 0.05, el
        pel, past = _best(lambda: proxy.past_answers(d, "R-11"))
        assert [p for p in past if p["answer"] == "allow"] == [] and pel < 0.1, pel
    finally:
        d.close()


@pytest.mark.timing
def test_the_template_stays_fast_with_thousands_of_answers_of_one_kind():
    _many(3000)
    d = m.db()
    try:
        el, text = _best(lambda: ops.knows_template(ops.knows_rows(d)), tries=1)
    finally:
        d.close()
    assert "I am fine with" not in text and el < 0.5, el


# ------------------------------------------------------------------ RR10-5

def test_an_empty_last_use_reads_as_the_old_sql_read_it():
    confirmed = ts(now() - timedelta(days=1))
    r = {"answer": "allow", "strength": "strong", "source": None, "confirmed_ts": confirmed, "ts": confirmed}
    assert memory.standing({**r, "last_used_ts": None}) is True       # never used: the confirm counts
    assert memory.standing({**r, "last_used_ts": ""}) is False        # COALESCE('', confirmed_ts) = '': before the use limit
