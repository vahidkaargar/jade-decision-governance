"""V6-29: the audit of calls that ran without a question, aging by evidence and the `--all` limit (stage 1 only).

Every call that runs from a standing answer is drawn for the human's check with a stored chance (1.0 in stage 1). The draw is made in the hook with a key the agent does
not read, the row is sealed, a wrong check retires the answer and pauses the scope, a `right` check is evidence for the answer, and a standing answer that ran silently
for long with no mark needs evidence again. All of it only adds questions.
"""

# ruff: noqa: F811   (the fixtures are imported from test_memory_w101 and then named as test arguments)
import json
import math
from datetime import timedelta
from pathlib import Path

import pytest

from jade import audit, hooks, ledger, memory, ops, paths, permits, policy, proxy
from jade.util import now, ts
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_jev_proxy_w102 import prov, ready  # noqa: F401  (fixtures: Jev on, past the band of owner answers)
from tests.acceptance.test_memory_w101 import CMD, OTHER, SESSION, enforce, medium, real_terminal_check  # noqa: F401  (fixtures)
from tests.acceptance.test_review_marks_w103 import decision_ids, finish, mark


def set_audit(**kw):
    cfg = paths.config()
    cfg["audit"] = kw
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


def sql(q, args=()):
    d = m.db()
    try:
        with d.tx():
            d.con.execute(q, args)
    finally:
        d.close()


def queue():
    return m.rows("SELECT * FROM audit_queue ORDER BY drawn_ts, rowid")


def scopes():
    return {r["scope_id"]: r for r in m.rows("SELECT * FROM audit_scopes")}


def standing_call(cmd=CMD):
    """Ask, allow and confirm once, so that the call stands."""
    m.ask_and_allow(cmd)
    m.confirm_all()


def run(i, cmd=CMD):
    """One more silent run of the standing call, in its own session, finished. Returns the exit code of the hook and the decision id of the run."""
    r = hooks.pretool(m.call(cmd, f"sess-v629-{i:03d}"))
    if r.exit_code == 0:
        finish(cmd, f"sess-v629-{i:03d}")
        return r, decision_ids("memory")[-1]
    return r, None


def asked(r):
    return r.exit_code == 2 and "AskUserQuestion" in r.stderr


# ------------------------------------------------------------------ the draw and the queue

def test_a_silent_run_is_queued_with_a_stored_chance_of_one_and_a_valid_seal(medium):
    standing_call()
    r, did = run(1)
    assert r.exit_code == 0 and did
    [q] = queue()
    assert q["decision_id"] == did and q["sample_prob"] == 1.0 and q["stage"] == 1 and q["status"] == "pending" and q["session_id"] == "sess-v629-001"
    d = m.db()
    try:
        key = audit._key(d, create=False)
        assert key is not None and q["seal"] == audit._seal(key, q)
    finally:
        d.close()
    [sc] = scopes().values()
    assert sc["runs"] == 1 and sc["status"] == "active" and sc["stage"] == 1
    ev = m.rows("SELECT detail_json FROM events WHERE kind='memory_allowed'")
    assert json.loads(ev[-1]["detail_json"])["scope_id"] == q["scope_id"]


def test_the_draw_depends_on_the_key_the_scope_and_the_decision_and_is_spread_evenly():
    k1, k2 = b"\x01" * 32, b"\x02" * 32
    assert audit.draw_value(k1, "s", "d") == audit.draw_value(k1, "s", "d")
    assert audit.draw_value(k1, "s", "d") != audit.draw_value(k2, "s", "d")
    assert audit.draw_value(k1, "s", "d") != audit.draw_value(k1, "s2", "d")
    vals = [audit.draw_value(k1, "s", f"dec_{i}") for i in range(4000)]
    assert all(0 <= v < 1 for v in vals)
    assert 0.45 < sum(v < 0.5 for v in vals) / len(vals) < 0.55      # a chance of 0.5 draws about half
    assert 0.07 < sum(v < 0.1 for v in vals) / len(vals) < 0.13


def test_nothing_the_agent_can_set_moves_the_draw(medium, monkeypatch):
    """No environment variable, config key or argument reaches the chance, the key or the outcome of a draw (the module reads none), and the key is never stored."""
    src = Path(audit.__file__).read_text()
    assert "os.environ" not in src and "getenv" not in src and "import os" not in src
    monkeypatch.setenv("JADE_AUDIT_P", "0")
    monkeypatch.setenv("JADE_AUDIT_SEED", "1")
    set_audit(sample_prob=0.0, seed="x", key="y", stage=2, p=0)
    assert audit.settings() == audit.DEFAULTS
    standing_call()
    run(1)
    [q] = queue()
    assert q["sample_prob"] == 1.0 and q["stage"] == 1
    d = m.db()
    try:
        seal = permits._seal_key(d, create=False)
        derived = audit._key(d, create=False)
        dump = "\n".join(d.con.iterdump())
    finally:
        d.close()
    for secret in (seal, derived):
        assert secret.hex() not in dump and secret not in dump.encode()
    assert (Path(paths.home()) / permits.SEAL_KEY_FILE).exists()   # the key is the permit seal key (derived with a fixed label): SI-10 already guards that name


def test_the_key_file_is_guarded_by_si10_and_the_review_commands_are_human_only(real_terminal_check):
    for cmd in ("cat ~/.local/share/jade/permit-seal.key", "xxd $JADE_HOME/permit-s*", "jade review mark", "jade review mark dec_1 right", "jade review confirm --all"):
        assert policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked, cmd
    with pytest.raises(ops.HumanOnly):
        ops.review_audit_walk(stdin_isatty=False)
    with pytest.raises(ops.HumanOnly):
        ops.review_mark("dec_x", "wrong", stdin_isatty=False)


def test_no_key_means_the_call_is_asked_about(medium, monkeypatch):
    standing_call()

    def broken(db, create=False):
        raise permits.PermitError("no key")

    monkeypatch.setattr(permits, "_seal_key", broken)
    r, did = run(1)
    assert asked(r) and did is None and queue() == []


def test_a_row_edited_with_sql_is_not_counted_and_not_labelled(medium):
    standing_call()
    _, did = run(1)
    sql("UPDATE audit_queue SET sample_prob=0.01 WHERE decision_id=?", (did,))   # make one check weigh 100
    d = m.db()
    try:
        s = audit.summary(d)
        assert s["unsealed"] == 1 and s["checked"] == 0 and audit.pending(d, 10) == []
    finally:
        d.close()
    mark(did, "right")
    assert m.rows("SELECT * FROM labels") == [] and queue()[0]["status"] == "pending"
    assert "do not match their seal" in ops.review(None)


# ------------------------------------------------------------------ the limits of stage 1

def test_stage_one_stops_after_59_silent_runs_in_a_scope(medium):
    standing_call()
    r, _ = run(1)
    assert r.exit_code == 0
    sql("UPDATE audit_scopes SET runs=58")
    assert run(2)[0].exit_code == 0                 # the 59th run
    assert scopes() and next(iter(scopes().values()))["runs"] == 59
    r, did = run(3)
    assert asked(r) and did is None and len(queue()) == 2
    assert [e["guard"] for e in m.rows("SELECT guard FROM events WHERE kind='audit_refused'")] == ["stage1_cap"]


def test_the_weekly_count_of_a_scope_and_the_weekly_budget_are_limits_on_silent_runs(medium):
    standing_call()
    set_audit(weekly_count=2, weekly_budget=10)
    assert [run(i)[0].exit_code for i in (1, 2)] == [0, 0]
    assert asked(run(3)[0])
    sql("UPDATE audit_queue SET drawn_ts=?", (ts(now() - timedelta(days=8)),))   # a week later the count is free again
    assert run(4)[0].exit_code == 0
    set_audit(weekly_count=10, weekly_budget=2)
    assert run(5)[0].exit_code == 0 and asked(run(6)[0])


def test_the_limits_can_only_be_lowered_in_config():
    d = audit.DEFAULTS
    for bad in ({"stage1_cap": 500}, {"stage1_cap": "10"}, {"stage1_cap": True}, {"stage1_cap": 0}, {"stage1_cap": -3}, {"stage1_cap": 5.5}, {"age_runs": 61}, {"age_days": 10 ** 6},
                {"weekly_count": 11}, {"weekly_budget": 99}, "x", ["stage1_cap", 1], None):
        set_audit_raw(bad)
        assert audit.settings() == d, bad
    set_audit_raw({"stage1_cap": 20, "weekly_count": 3, "age_runs": 5, "age_days": 7, "weekly_budget": 4})
    s = audit.settings()
    assert (s.stage1_cap, s.weekly_count, s.age_runs, s.age_days, s.weekly_budget, s.min_sessions) == (20, 3, 5, 7, 4, 10)


def set_audit_raw(value):
    cfg = paths.config()
    cfg["audit"] = value
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


# ------------------------------------------------------------------ the verdict

def test_a_wrong_check_retires_the_answer_labels_it_and_pauses_the_scope(medium):
    standing_call()
    _, did = run(1)
    assert "retired" in mark(did, "wrong")
    assert m.strong() == []
    [q] = queue()
    assert q["status"] == "wrong" and q["resolved_ts"]
    [lab] = m.rows("SELECT * FROM labels")
    assert (lab["call_id"], lab["label"], lab["label_type"], lab["sample_prob"], lab["audit_source"]) == (did, 0, "owner_would_allow", 1.0, "random_audit")
    sc = scopes()[q["scope_id"]]
    assert sc["status"] == "suspended" and sc["suspended_ts"]
    assert any(e["kind"] == "audit_scope_suspended" for e in m.rows("SELECT kind FROM events"))
    assert asked(hooks.pretool(m.call(CMD, "sess-v629-900")))


def test_a_hint_does_not_lift_the_pause_and_a_human_confirm_does_and_restarts_the_count(medium):
    standing_call()
    _, did = run(1)
    mark(did, "wrong")
    m.ask_and_allow(CMD, "sess-v629-777")               # the human allows it again in the chat: a hint
    assert next(iter(scopes().values()))["status"] == "suspended"
    assert asked(hooks.pretool(m.call(CMD, "sess-v629-901")))
    m.confirm_all()                                     # the human confirms in a terminal: the pause ends
    sc = next(iter(scopes().values()))
    assert sc["status"] == "active" and sc["runs"] == 0 and sc["stage"] == 1 and sc["suspended_ts"] is None
    assert any(e["kind"] == "audit_scope_resumed" for e in m.rows("SELECT kind FROM events"))
    r, _ = run(2)
    assert r.exit_code == 0 and next(iter(scopes().values()))["runs"] == 1


def test_a_paused_scope_stops_a_strong_row_that_was_confirmed_without_a_terminal_step(medium):
    standing_call()
    _, did = run(1)
    mark(did, "wrong")
    sid = queue()[0]["scope_id"]
    m.ask_and_allow(CMD, "sess-v629-777")
    d = m.db()
    try:
        with d.tx():
            d.confirm_answers([r["answer_id"] for r in d.human_answers(strength="hint")])   # not the terminal step: no resume
    finally:
        d.close()
    assert len(m.strong()) == 1 and scopes()[sid]["status"] == "suspended"
    assert asked(hooks.pretool(m.call(CMD, "sess-v629-902")))


def test_a_right_check_is_evidence_and_a_wrong_one_stays_wrong(medium):
    standing_call()
    _, did = run(1)
    sql("UPDATE human_answers SET silent_runs=40")
    mark(did, "right")
    [q] = queue()
    assert q["status"] == "right"
    [lab] = m.rows("SELECT * FROM labels")
    assert (lab["label"], lab["label_type"]) == (1, "owner_would_allow")
    assert m.strong()[0]["silent_runs"] == 0 and m.strong()[0]["evidence_ts"]
    mark(did, "wrong")
    mark(did, "right")                                    # the latest mark is `right`, but the audited call stays wrong
    assert queue()[0]["status"] == "wrong"


def test_a_label_of_the_audit_never_fits_a_threshold(medium):
    from jade import evals

    standing_call()
    mark(run(1)[1], "right")
    d = m.db()
    try:
        assert d.con.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 1
        assert d.con.execute(f"SELECT COUNT(*) FROM labels l WHERE {evals.FIT_TYPE_SQL}").fetchone()[0] == 0   # what every fit reader adds
    finally:
        d.close()


def test_the_walk_marks_the_waiting_calls_one_by_one_and_can_skip(medium):
    standing_call()
    dids = [run(i)[1] for i in (1, 2, 3)]
    said = []
    answers = iter(["r", "", "w"])
    assert ops.review_audit_walk(stdin_isatty=True, ask=lambda _="": next(answers), say=said.append) == 2
    assert [q["status"] for q in queue()] == ["right", "pending", "wrong"]
    assert any(dids[0] in s for s in said)
    assert ops.review_audit_walk(stdin_isatty=True, ask=lambda _="": "s", say=lambda *_: None) == 0   # the paused scope still has its pending row: skip it


def test_the_walk_with_nothing_waiting_says_so(medium):
    said = []
    assert ops.review_audit_walk(stdin_isatty=True, ask=lambda _="": "r", say=said.append) == 0 and "no calls wait" in said[0]


# ------------------------------------------------------------------ the numbers

def seed_rows(spec):
    """Sealed audit rows with chosen chance, status and session: (p, status, session)."""
    d = m.db()
    try:
        key = audit._key(d, create=True)
        with d.tx():
            for i, (p, status, session) in enumerate(spec):
                r = {"audit_id": f"aud_{i:04d}", "decision_id": f"dec_{i:04d}", "answer_id": "ans_x", "scope_id": "sc", "stage": 1, "sample_prob": p, "drawn_ts": ts(),
                     "rule_id": "R-11", "session_id": session, "subject": CMD, "cwd": "/tmp", "status": status, "resolved_ts": None}
                d.con.execute("INSERT INTO audit_queue(audit_id, decision_id, answer_id, scope_id, rule_id, stage, sample_prob, status, session_id, drawn_ts, subject, cwd, seal) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (r["audit_id"], r["decision_id"], "ans_x", "sc", "R-11", 1, p, status, session, r["drawn_ts"], CMD, "/tmp", audit._seal(key, r)))
    finally:
        d.close()


def summary():
    d = m.db()
    try:
        return audit.summary(d)
    finally:
        d.close()


def test_the_bound_of_59_clean_checks_is_the_exact_one_of_the_plan():
    assert round(audit.upper_bound(0, 59), 4) == 0.0495          # 1 - 0.05^(1/59); the rule of three says 5.08%
    assert round(audit.upper_bound(0, 149), 4) == 0.0199
    assert audit.upper_bound(0, 299) < 0.01 < audit.upper_bound(0, 290)   # "below 1%" needs about 299 clean checks
    assert audit.upper_bound(0, 0) == 1.0 and audit.upper_bound(5, 5) == 1.0


@pytest.mark.parametrize(("k", "n"), [(1, 59), (3, 100), (10, 400)])
def test_the_bound_with_wrong_checks_is_the_limit_where_k_or_fewer_has_five_percent(k, n):
    u = audit.upper_bound(k, n)
    tail = sum(math.comb(n, i) * u ** i * (1 - u) ** (n - i) for i in range(k + 1))
    assert abs(tail - 0.05) < 1e-6 and u > k / n


def test_under_ten_sessions_only_counts_are_shown_and_never_a_rate(medium):
    seed_rows([(1.0, "right", f"s{i % 9}") for i in range(59)])   # 59 clean checks from only 9 sessions
    s = summary()
    assert s["checked"] == 59 and s["sessions"] == 9 and s["rate"] is None and s["upper"] is None
    text = ops.review(None)
    assert "not shown until the checks come from 10 sessions (now 9)" in text and "% of calls like these were wrong" not in text


def test_from_ten_sessions_the_rate_and_its_bound_are_shown(medium):
    seed_rows([(1.0, "right", f"s{i % 10}") for i in range(59)])
    s = summary()
    assert s["sessions"] == 10 and s["rate"] == 0 and round(s["upper"], 4) == 0.0495
    assert "we are 95% sure it is below 4.95%" in ops.review(None)


def test_a_check_is_weighted_by_one_over_its_chance_of_being_drawn(medium):
    # 10 calls drawn with chance 1 (all right) and 5 drawn with chance 0.1 (one wrong): the wrong one stands for 10 calls
    seed_rows([(1.0, "right", f"s{i}") for i in range(10)] + [(0.1, "right", f"t{i}") for i in range(4)] + [(0.1, "wrong", "t4")])
    s = summary()
    assert s["checked"] == 15 and s["wrong"] == 1 and s["weighted"]
    assert abs(s["rate"] - 10 / 60) < 1e-12               # not 1/15 = 0.067
    plain = audit.upper_bound(1, 15)
    assert s["upper"] > plain - 1e-9                       # unequal chances make the effective sample smaller: the bound is never narrower than the plain one
    assert s["n_eff"] < 15


def test_equal_chances_give_the_plain_counts():
    rows = [{"sample_prob": 0.25, "status": "wrong"}] + [{"sample_prob": 0.25, "status": "right"}] * 19
    e = audit.estimate(rows)
    assert abs(e["rate"] - 0.05) < 1e-12 and abs(e["n_eff"] - 20) < 1e-9 and e["upper"] == audit.upper_bound(1, 20)


def test_stage_two_draws_with_the_chance_that_gives_the_weekly_count():
    s = audit.DEFAULTS
    assert audit.stage2_prob(s, 5) == 1.0 and audit.stage2_prob(s, 100) == 0.1 and audit.stage2_prob(s, 10 ** 6) == audit.P_MIN


def test_the_review_says_nothing_about_the_audit_when_nothing_was_drawn(medium):
    assert not any("audit" in line for line in ops.review(None).splitlines())


# ------------------------------------------------------------------ aging by evidence

def standing_row(**cols):
    now_ = now()
    base = {"answer": "allow", "strength": "strong", "source": None, "confirmed_ts": ts(now_ - timedelta(days=1)), "last_used_ts": ts(now_), "ts": ts(now_ - timedelta(days=1))}
    return {**base, **cols}


def test_a_row_that_never_ran_silently_ages_exactly_as_before():
    assert memory.standing(standing_row())
    assert memory.standing(standing_row(confirmed_ts=ts(now() - timedelta(days=100)), last_used_ts=None, silent_runs=None, evidence_ts=None)) is False   # the old 30 day rule
    assert memory.standing(standing_row(confirmed_ts=ts(now() - timedelta(days=100)), last_used_ts=ts(now()), silent_runs=0))               # used, within the 180 days: as before


def test_a_row_that_ran_silently_60_times_with_no_mark_needs_evidence():
    assert memory.standing(standing_row(silent_runs=59))
    assert not memory.standing(standing_row(silent_runs=60))
    set_audit_raw({"age_runs": 5})
    assert memory.standing(standing_row(silent_runs=4)) and not memory.standing(standing_row(silent_runs=5))


def test_a_row_that_ran_silently_and_has_no_evidence_for_90_days_needs_evidence():
    old = ts(now() - timedelta(days=91))
    assert not memory.standing(standing_row(silent_runs=3, confirmed_ts=old, evidence_ts=None))
    assert memory.standing(standing_row(silent_runs=3, confirmed_ts=old, evidence_ts=ts(now() - timedelta(days=5))))   # a right mark five days ago
    assert memory.standing(standing_row(silent_runs=0, confirmed_ts=old))                                              # never ran silently: only the old rules
    set_audit_raw({"age_days": 7})
    assert not memory.standing(standing_row(silent_runs=1, confirmed_ts=ts(now() - timedelta(days=8)), evidence_ts=None))


def test_an_aged_row_stops_deciding_in_the_gate_and_a_right_mark_brings_it_back(medium):
    standing_call()
    sql("UPDATE human_answers SET silent_runs=59 WHERE strength='strong'")
    r, did = run(1)                                          # the 60th silent run: allowed, then the row needs evidence
    assert r.exit_code == 0 and m.strong()[0]["silent_runs"] == 60
    r, _ = run(2)
    assert asked(r)
    d = m.db()
    try:
        assert memory.lookup(d, policy.assess("Bash", {"command": CMD}, "/tmp"), "Bash", {"command": CMD}, {}, "/tmp") is None
    finally:
        d.close()
    mark(did, "right")
    r, _ = run(3)
    assert r.exit_code == 0 and m.strong()[0]["silent_runs"] == 1


def test_a_right_mark_never_lengthens_the_old_lifetime(medium):
    standing_call()
    _, did = run(1)
    sql("UPDATE human_answers SET confirmed_ts=?, last_used_ts=?", (ts(now() - timedelta(days=181)), ts(now() - timedelta(days=1))))
    mark(did, "right")
    assert asked(run(2)[0])                                  # past 180 days after the confirm: the cap still holds


def test_knows_list_says_what_a_run_with_no_mark_costs(medium):
    standing_call()
    run(1)
    assert "1 silent run(s) since its last mark; it stops after 60 with no mark: jade review mark" in ops.knows_list()


# ------------------------------------------------------------------ `--all` and the sitting cap

def risk_rules(level):
    return [r["id"] for r in policy.risk()["rules"] if r.get("level") == level]


def add_hints(rules):
    d = m.db()
    try:
        with d.tx():
            for i, rule in enumerate(rules):
                d.record_human_answer(session=SESSION, decision_id=None, rule_id=rule, class_key=f"{i:064x}", exact_key=f"{i + 1000:064x}", answer="allow", channel="chat", label=f"call {i}")
    finally:
        d.close()


def test_all_never_takes_a_high_risk_group_without_asking(medium):
    high, med = risk_rules("high")[0], risk_rules("medium")[0]
    add_hints([high, med])
    prompts, said = [], []

    def ask(q=""):
        prompts.append(q)
        return "yes"

    assert ops.review_confirm(stdin_isatty=True, ask=ask, say=said.append, all_=True) == 2
    assert sum("stand for this" in p for p in prompts) == 1          # only the high group was asked
    assert len(m.strong()) == 2


def test_all_with_a_no_to_the_high_group_leaves_it_pending(medium):
    high, med = risk_rules("high")[0], risk_rules("medium")[0]
    add_hints([high, med])
    answers = {"  stand for this? [y/N] ": "n", "Type yes to confirm: ": "yes"}
    assert ops.review_confirm(stdin_isatty=True, ask=lambda q="": answers[q], say=lambda *_: None, all_=True) == 1
    assert [r["rule_id"] for r in m.strong()] == [med]


def test_a_sitting_confirms_at_most_ten_groups_and_says_how_many_wait(medium):
    add_hints(risk_rules("medium")[:1] * 12)
    said = []
    assert ops.review_confirm(stdin_isatty=True, ask=lambda q="": "yes", say=said.append, all_=True) == 10
    assert any("2 more group(s) wait" in s for s in said)
    assert len(m.strong()) == 10
    assert ops.review_confirm(stdin_isatty=True, ask=lambda q="": "yes", say=lambda *_: None, all_=True) == 2     # the next sitting takes the rest


def test_an_unknown_rule_counts_as_high():
    assert ops._rule_is_high("R-NOPE") and ops._rule_is_high(risk_rules("high")[0]) and ops._rule_is_high(risk_rules("critical")[0])
    assert not ops._rule_is_high(risk_rules("medium")[0])


# ------------------------------------------------------------------ migration

def test_the_audit_tables_and_columns_are_added_to_an_older_ledger(tmp_path):
    import sqlite3

    f = tmp_path / "old" / "jade.sqlite"
    f.parent.mkdir()
    con = sqlite3.connect(f)
    con.executescript("CREATE TABLE human_answers(answer_id TEXT PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, decision_id TEXT, rule_id TEXT NOT NULL, class_key TEXT, "
                      "exact_key TEXT NOT NULL, answer TEXT NOT NULL, channel TEXT NOT NULL, strength TEXT NOT NULL DEFAULT 'hint', confirmed_ts TEXT, source TEXT);"
                      "INSERT INTO human_answers VALUES('ans_1','2026-01-01T00:00:00Z','s',NULL,'R-11',NULL,'e','allow','chat','strong','2026-01-01T00:00:00Z',NULL);")
    con.commit()
    con.close()
    d = ledger.open(f)
    try:
        cols = {r["name"] for r in d.con.execute("PRAGMA table_info(human_answers)")}
        assert {"silent_runs", "evidence_ts"} <= cols
        assert {r["name"] for r in d.con.execute("PRAGMA table_info(audit_queue)")} >= {"sample_prob", "seal", "decision_id", "status"}
        assert d.con.execute("SELECT COUNT(*) FROM audit_scopes").fetchone()[0] == 0
        assert d.con.execute("SELECT silent_runs, evidence_ts FROM human_answers").fetchone()[:] == (None, None)   # an old row counts from now
    finally:
        d.close()


def test_the_hot_queries_of_the_audit_use_an_index(medium):
    standing_call()
    run(1)
    d = m.db()
    try:
        for q, args in (("SELECT COUNT(*) FROM audit_queue WHERE scope_id=? AND drawn_ts>=?", ("x", "")), ("SELECT COUNT(*) FROM audit_queue WHERE drawn_ts>=?", ("",)),
                        ("SELECT * FROM audit_scopes WHERE scope_id=?", ("x",))):
            plan = " ".join(str(tuple(r)) for r in d.con.execute("EXPLAIN QUERY PLAN " + q, args))
            assert "SEARCH" in plan and "USING" in plan and "SCAN" not in plan, plan
    finally:
        d.close()


# ------------------------------------------------------------------ review fixes: Jev on, what the walk shows, what the seal covers

def jev_standing(cwd="/tmp", kind="kind_any"):
    from tests.acceptance import test_scope_memory as sm

    return sm.store(CMD, cwd, kind)


def test_with_jev_on_a_limit_of_the_audit_still_asks_and_jev_does_not_run_the_call(ready):
    jev_standing()
    set_audit(weekly_count=1)
    assert run(1)[0].exit_code == 0                                 # from the standing answer, queued
    r = hooks.pretool(m.call(CMD, "sess-v629-002"))
    assert asked(r)
    assert m.rows("SELECT * FROM events WHERE kind='jev_allowed'") == []
    assert [e["guard"] for e in m.rows("SELECT guard FROM events WHERE kind='audit_refused'")] == ["weekly_count"]
    assert ready.sent == []                                         # Jev was not even asked: it could only have said yes


def test_with_a_limit_hit_and_the_deny_gate_one_sample_short_jev_stays_shadow_even_though_its_own_question_is_the_last_sample(medium, prov):
    """Review fix: the gate was read twice. The first read (gate not met) let the question go to Jev; the question itself became the 100th sample, so the second read said met
    and Jev's yes ran a call the audit had stopped."""
    from tests.acceptance.test_jev_proxy_w102 import owner_answers, prefs

    prefs()
    owner_answers(samples=proxy.MIN_SAMPLES - 1)
    jev_standing()
    set_audit(weekly_count=1)
    assert run(1)[0].exit_code == 0
    r = hooks.pretool(m.call(CMD, "sess-v629-002"))
    assert asked(r)
    assert m.rows("SELECT * FROM events WHERE kind='jev_allowed'") == []


def test_with_jev_on_a_paused_scope_asks_about_every_call_and_the_mark_text_is_true(ready):
    jev_standing()
    _, did = run(1)
    assert "JADE asks again" in mark(did, "wrong")
    assert m.strong() == [] and next(iter(scopes().values()))["status"] == "suspended"
    r = hooks.pretool(m.call(CMD, "sess-v629-002"))
    assert asked(r) and m.rows("SELECT * FROM events WHERE kind='jev_allowed'") == []
    assert hooks.pretool(m.call(CMD, "sess-v629-003", cwd="/tmp/other")).exit_code == 2   # kind_any: the pause holds in every directory


def test_with_jev_on_a_call_outside_any_paused_scope_still_goes_to_jev(ready):
    jev_standing()
    _, did = run(1)
    mark(did, "wrong")
    r = hooks.pretool(m.call("git reset --hard HEAD~3", "sess-v629-004"))   # another kind of call: not in the paused scope
    assert r.exit_code == 0 and "Jev" in r.stderr


def test_the_walk_and_the_review_show_the_call_that_ran_and_where(medium, tmp_path):
    here, there = tmp_path / "dev-a", tmp_path / "prod-b"
    here.mkdir()
    there.mkdir()
    jev_standing(str(here))
    r = hooks.pretool(m.call(CMD, "sess-adv-1", cwd=str(there)))     # a kind_any answer covers this other directory
    assert r.exit_code == 0
    [q] = queue()
    assert (q["subject"], q["cwd"], q["session_id"]) == (CMD, str(there), "sess-adv-1")
    said = []
    ops.review_audit_walk(stdin_isatty=True, ask=lambda _="": "s", say=said.append)
    assert any(CMD in s and "prod-b" in s and "sess-adv-1" in s for s in said), said
    text = ops.review(None)
    assert "prod-b" in text and CMD in text


def test_the_text_shown_in_the_walk_is_stored_with_the_run_and_sealed(medium):
    standing_call()
    _, did = run(1)
    sql("UPDATE audit_queue SET subject='ls', cwd='/tmp/elsewhere' WHERE decision_id=?", (did,))
    d = m.db()
    try:
        assert audit.summary(d)["unsealed"] == 1 and audit.pending(d, 10) == []     # a changed text is a changed row
    finally:
        d.close()


@pytest.mark.parametrize("edit", ["status='right'", "status='right', resolved_ts='2026-01-01T00:00:00Z'", "session_id='sess-forged'", "rule_id='R-01'", "subject='x'", "cwd='/x'"])
def test_a_row_edited_in_any_field_with_sql_is_not_counted_as_a_clean_check(medium, edit):
    standing_call()
    for i in (1, 2, 3):
        run(i)
    sql(f"UPDATE audit_queue SET {edit}")  # noqa: S608
    d = m.db()
    try:
        s = audit.summary(d)
        assert s["unsealed"] == 3 and s["checked"] == 0 and s["wrong"] == 0
    finally:
        d.close()


def test_a_real_mark_reseals_the_row_so_it_is_counted(medium):
    standing_call()
    _, did = run(1)
    _, did2 = run(2)
    mark(did, "right")
    mark(did2, "wrong")
    d = m.db()
    try:
        s = audit.summary(d)
        assert s["unsealed"] == 0 and s["checked"] == 2 and s["wrong"] == 1
    finally:
        d.close()
    sql("UPDATE audit_queue SET status='right' WHERE decision_id=?", (did2,))   # a wrong check turned into right with SQL
    d = m.db()
    try:
        s = audit.summary(d)
        assert s["unsealed"] == 1 and s["checked"] == 1 and s["wrong"] == 0
    finally:
        d.close()
