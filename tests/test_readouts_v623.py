"""V6-23: the ledger readouts and the label plumbing around them.

All rows here are synthetic and written into a temporary JADE home (tests/conftest.py). Nothing reads a real ledger.
"""
# ruff: noqa: F811   (the fixtures are imported from the proxy tests and then named as test arguments)
import json
import sqlite3
import time
import uuid
from datetime import timedelta

import pytest

from jade import adapter, ask, cli, hooks, ledger, ops, proxy, readouts
from jade.util import now, ts
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_jev_proxy_w102 import KIND, calls, prov, ready  # noqa: F401  (fixtures)
from tests.acceptance.test_memory_w101 import medium  # noqa: F401  (fixture used by `ready`)
from tests.test_ledger_migration import build, columns, dump

NEW_COLUMNS = {"label_type", "sample_prob", "audit_source"}
SINCE = ""   # all time


def stats(capsys, *extra):
    assert cli.main(["ledger", "stats", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def at(seconds_ago):
    return ts(now() - timedelta(seconds=seconds_ago))


# ---- the migration is additive and stays inside the hook budget ------------------------------------------------
def test_an_old_ledger_opens_with_the_new_columns_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old" / "jade.sqlite"
    path.parent.mkdir()
    counts = build(path)
    before = dump(path)
    assert not NEW_COLUMNS & columns(path, "labels")
    ledger.open(path, timeout=hooks.LEDGER_TIMEOUT_S).close()   # the first open does the migration
    assert NEW_COLUMNS <= columns(path, "labels")
    after = dump(path)
    assert {t: len(r) for t, r in after.items()} == counts
    assert after["labels"][0]["label_type"] is None and after["labels"][0]["sample_prob"] is None   # old rows stay null
    assert [{k: v for k, v in r.items() if k in before["labels"][0]} for r in after["labels"]] == before["labels"]


@pytest.mark.timing
def test_the_migration_of_an_old_ledger_is_inside_the_hook_budget(tmp_path):
    path = tmp_path / "old" / "jade.sqlite"
    path.parent.mkdir()
    build(path)
    started = time.perf_counter()
    ledger.open(path, timeout=hooks.LEDGER_TIMEOUT_S).close()   # the first open does the migration
    first = time.perf_counter() - started
    assert first < hooks.LEDGER_TIMEOUT_S, f"the one-time migration took {first:.2f} s; the hook waits {hooks.LEDGER_TIMEOUT_S} s for the ledger"
    started = time.perf_counter()
    ledger.open(path, timeout=hooks.LEDGER_TIMEOUT_S).close()   # every later hook process: a version check only
    assert time.perf_counter() - started < hooks.LEDGER_TIMEOUT_S / 3


# ---- purge ------------------------------------------------------------------------------------------------------------------
def labelled_call(db, source=None):
    call = db.begin_call("q.verify_claim.supported_by_evidence", "h", "s", claim_hash="c", primitive="noul", task_family="verify_claim", **({"source": source} if source else {}))
    db.finish_call(call, p=0.9, verdict="ACCEPT", exit_code=0)
    return call


def test_the_test_row_purge_removes_the_labels_with_the_new_columns(monkeypatch):
    from jade import evals

    monkeypatch.setenv("JADE_SOURCE", "test")
    db = ledger.open()
    gone = labelled_call(db)
    evals.set_label(db, gone, 1, "adjudicated", label_type="owner_would_allow", sample_prob=0.2, audit_source="random_audit")
    db.close()
    monkeypatch.delenv("JADE_SOURCE")
    db = ledger.open()
    kept = labelled_call(db)
    evals.set_label(db, kept, 0, "adjudicated", sample_prob=0.5, audit_source="adjudication")
    out = ops.purge_test_rows(db)
    assert out["jev_calls"] == 1
    rows = db.all("SELECT call_id, label_type, sample_prob, audit_source FROM labels")
    assert [(r.call_id, r.label_type, r.sample_prob, r.audit_source) for r in rows] == [(kept, "claim_true", 0.5, "adjudication")]


def test_the_retention_purge_keeps_the_label_figures_and_the_new_columns_hold_no_text():
    from jade import evals

    db = ledger.open()
    call = labelled_call(db)
    db.con.execute("UPDATE jev_calls SET ts='2020-01-01T00:00:00Z', state_text='the command', question_text='the claim' WHERE call_id=?", (call,))
    evals.set_label(db, call, 1, "adjudicated", sample_prob=0.25, audit_source="random_audit", label_type="harm_observed")
    ops.purge(db, 90)
    assert db.one("SELECT state_text FROM jev_calls WHERE call_id=?", (call,)).state_text is None
    row = db.one("SELECT label_type, sample_prob, audit_source FROM labels WHERE call_id=?", (call,))
    assert (row.label_type, row.sample_prob, row.audit_source) == ("harm_observed", 0.25, "random_audit")   # metrics stay, as for every other metric
    with pytest.raises(ValueError):
        evals.set_label(db, call, 1, "adjudicated", audit_source="free text with a command in it")   # a short code, never text


# ---- the two load counters are one ----------------------------------------------------------------------------------
def gate_events(db):
    for _ in range(5):
        db.event("gate", "gated", guard="R-11 high", actor="hook", detail={"permission_mode": "auto", "tool": "Bash"})
    for _ in range(2):   # an SI-07 hold: `gated` first, `safety_block` after it, for one call
        db.event("gate", "gated", guard="R-11 high", actor="hook", detail={"permission_mode": "auto", "tool": "Bash"})
        db.event("gate", "safety_block", guard="R-11 high SI-07", actor="hook", detail={"permission_mode": "auto", "tool": "Bash"})
    for _ in range(3):   # an invariant block: `safety_block` only
        db.event("gate", "safety_block", guard="R-02 critical SI-08", actor="hook", detail={"permission_mode": "default", "tool": "Bash"})


def test_check_and_stats_count_a_gated_call_the_same_way(capsys):
    db = ledger.open()
    gate_events(db)
    db.close()
    load = readouts.load_counts(ledger.open(), SINCE)
    assert load == {"calls": 10, "gated_events": 7, "invariant_blocks_without_gated_event": 3, "si07_blocks_counted_once_in_gated": 2}
    seen, no_prompt = ops.no_prompt_gated_calls()
    assert seen == 10 and no_prompt == 7   # `jade check` used to say 12: the SI-07 holds twice
    s = stats(capsys)
    assert s["load"]["calls"] == 10 and s["asks"]["gated_calls"] == 10   # `jade ledger stats` used to say 7: the invariant blocks not at all


def test_the_load_window_is_the_same_in_both(capsys):
    db = ledger.open()
    db.event("gate", "gated", guard="R-11 high", actor="hook")
    db.con.execute("UPDATE events SET ts=? WHERE kind='gated'", (at(10 * 86400),))
    db.event("gate", "gated", guard="R-11 high", actor="hook")
    db.close()
    assert ops.no_prompt_gated_calls(7)[0] == 1 and ops.no_prompt_gated_calls(14)[0] == 2
    assert stats(capsys, "--days", "7")["load"]["calls"] == 1 and stats(capsys, "--days", "14")["load"]["calls"] == 2


def test_per_100_gated_uses_all_gated_calls_and_the_stats_print_both_denominators(capsys):
    db = ledger.open()
    gate_events(db)   # 7 gated events + 3 invariant blocks: 10 calls
    for i in range(5):
        db.record_human_answer(session="s", decision_id=None, rule_id="R-11", class_key=None, exact_key=f"{i:064d}", answer="allow", channel="chat")
    db.close()
    asks = stats(capsys)["asks"]
    assert asks["gated_calls"] == 10 and asks["gated_events"] == 7   # `gated_events` is what `gated_calls` was before V6-23
    assert asks["per_100_gated"]["chat"] == 50.0   # 5 answers over 10 calls; over the old 7 it was 71.4


def test_the_test_tagged_rows_are_left_out_of_the_load(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    db = ledger.open()
    db.event("gate", "gated", guard="R-11 high", actor="hook")
    db.close()
    monkeypatch.delenv("JADE_SOURCE")
    assert readouts.load_counts(ledger.open(), SINCE)["calls"] == 0


# ---- json outputs ---------------------------------------------------------------------------------------------------------
def test_stats_json_is_one_line_with_sorted_keys_and_every_section(capsys):
    ledger.open().close()
    assert cli.main(["ledger", "stats", "--json"]) == 0
    text = capsys.readouterr().out
    assert text.count("\n") == 1
    data = json.loads(text)
    assert text.strip() == json.dumps(data, sort_keys=True, separators=(",", ":"))
    for key in ("load", "friction", "verify_links", "label_coverage", "shadow_agreement"):
        assert key in data
    assert data["shadow_agreement"]["recall_percent"] is None and "not measured" in data["shadow_agreement"]["recall_note"]
    assert data["friction"]["approve_rate"] is None and data["verify_links"]["linked_share_percent"] is None   # an empty ledger gives no rate, not a division by zero


def test_check_json_keeps_the_tri_state_and_the_exit_code(capsys, monkeypatch):
    rows = [("a", True, "fine"), ("b", None, "warn"), ("c", False, "broken")]
    monkeypatch.setattr(ops, "check", lambda live=True, provider=None: rows)
    assert cli.main(["check", "--offline", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [r["ok"] for r in out["rows"]] == [True, None, False] and out["failed"] == ["c"] and out["ok"] is False
    assert out["load_last_7_days"] == {"calls": 0, "gated_events": 0, "invariant_blocks_without_gated_event": 0, "si07_blocks_counted_once_in_gated": 0, "no_host_prompt": 0}
    monkeypatch.setattr(ops, "check", lambda live=True, provider=None: rows[:2])
    assert cli.main(["check", "--offline", "--json"]) == 0   # a warning never fails, in text or in JSON
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_check_json_on_a_real_offline_check_parses(capsys):
    code = cli.main(["check", "--offline", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == (0 if out["ok"] else 1) and {r["name"] for r in out["rows"]} >= {"ledger writable", "policies load"}


# ---- asks: how often, how long, how many ran out --------------------------------------------------------------------------
def add_ask(db, rule, status, created=100, answered=None, expires=-100, session="s-1", hold="h"):
    db.con.execute("INSERT INTO asks(ask_id, session_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, answered_ts, expires_ts) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:12], session, "p", hold, "{}", rule, "high", "Bash", "{}", status, at(created),
                                                          at(answered) if answered is not None else None, at(expires)))


def test_the_ask_load_counts_time_to_answer_expired_asks_and_the_approve_rate():
    db = ledger.open()
    add_ask(db, "R-11", "CONSUMED", created=100, answered=70)      # 30 s, allowed
    add_ask(db, "R-11", "ALLOWED", created=100, answered=40)       # 60 s, allowed
    add_ask(db, "R-11", "DENIED", created=100, answered=90)        # 10 s, denied
    add_ask(db, "R-11", "EXPIRED")                                 # never answered
    add_ask(db, "R-13", "GAVE_UP")                                 # never answered
    add_ask(db, "R-13", "SHOWN", expires=-5)                       # waiting, and its time is past: the end is written lazily, it is over
    add_ask(db, "R-13", "PENDING", expires=300)                    # still waiting
    add_ask(db, "R-13", "EXPIRED", created=100, answered=50, expires=-1)   # an Allow that ran out unused is still an answer
    load = readouts.ask_load(db, SINCE)
    assert {k: load[k] for k in ("asked", "allowed", "denied", "expired", "open")} == {"asked": 8, "allowed": 3, "denied": 1, "expired": 3, "open": 1}
    assert load["approve_rate"] == 75.0
    assert load["answer_seconds"]["n"] == 4 and load["answer_seconds"]["p50"] == 30 and load["answer_seconds"]["max"] == 60
    r11 = load["by_rule"]["R-11"]
    assert (r11["asked"], r11["allowed"], r11["denied"], r11["expired"]) == (4, 2, 1, 1) and r11["approve_rate"] == pytest.approx(66.7)
    assert r11["answer_seconds"] == {"n": 3, "p50": 30, "max": 60}


def test_the_ask_load_leaves_out_other_windows_and_test_rows(monkeypatch):
    db = ledger.open()
    add_ask(db, "R-11", "DENIED", created=40 * 86400, answered=40 * 86400 - 5)
    add_ask(db, "R-11", "DENIED", created=100, answered=90)
    assert readouts.ask_load(db, readouts.since_days(7))["asked"] == 1 and readouts.ask_load(db, SINCE)["asked"] == 2


# ---- verify calls: linked and labelled ---------------------------------------------------------------------------------------
def fixture_provider(p=0.9):
    return adapter.FixtureProvider(lambda s, q, m, p=p: {k: {"type": "noul", "noul": p} for k in q})


def test_a_scripted_run_of_verify_calls_is_linked_to_decisions_by_default():
    for i in range(20):
        assert cli.ask_main(["verify", f"claim number {i}", "-s", f"raw output {i}"], provider=fixture_provider()) == 0
    db = ledger.open()
    links = readouts.verify_links(db, SINCE)
    assert links["verify_calls"] == 20 and links["linked_share_percent"] >= 95   # the figure d10 asks for; here it is every call
    assert links["decisions_without_a_call"] == 0
    # each call has a decision of its own, in this session, and the decision followed the answer
    rows = db.all("SELECT d.status, d.session_id FROM decisions d JOIN jev_calls c ON c.decision_id=d.decision_id WHERE c.question_id='q.verify_claim.supported_by_evidence'")
    assert len(rows) == 20 and {r.status for r in rows} == {"ACCEPTED"} and len({r.session_id for r in rows}) == 1


def test_a_named_decision_is_used_and_no_other_is_opened_or_advanced():
    from jade import decide

    db = ledger.open()
    mine = decide.open_decision(db, "Report that the deploy worked")["decision_id"]
    other = decide.open_decision(db, "Another open decision")["decision_id"]
    db.close()
    assert cli.ask_main(["verify", "the deploy worked", "-s", "raw output", "--decision", mine], provider=fixture_provider()) == 0
    db = ledger.open()
    assert db.one("SELECT COUNT(*) AS n FROM decisions").n == 2
    assert decide.get(db, mine)["status"] == "ACCEPTED" and decide.get(db, other)["status"] == "CLASSIFIED"


def test_a_dry_run_and_a_non_verify_ask_open_no_decision(capsys):
    assert cli.ask_main(["verify", "x claim", "-s", "raw output", "--dry-run"], provider=fixture_provider()) == 0
    assert cli.ask_main(["noul", "is it so", "-s", "raw", "-t", "yes", "-f", "no"], provider=fixture_provider()) == 0
    db = ledger.open()
    assert db.one("SELECT COUNT(*) AS n FROM decisions").n == 0


def test_a_verify_call_still_runs_when_the_decision_cannot_be_opened(monkeypatch):
    from jade import decide

    monkeypatch.setattr(decide, "open_decision", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    assert cli.ask_main(["verify", "the claim", "-s", "raw output"], provider=fixture_provider()) == 0   # unlinked, as before; never a refusal
    assert readouts.verify_links(ledger.open(), SINCE)["linked_to_a_decision"] == 0


def test_label_coverage_counts_fit_labels_apart_from_heuristic_and_other_kinds():
    from jade import evals

    db = ledger.open()
    calls = []
    for i in range(4):
        r = ask.run(ask.verify_specs(companion=False), {"claim": f"c{i}", "evidence": "e"}, provider=fixture_provider(), session=f"s{i}", db=db)
        calls.append(r.primary.call_id)
    evals.set_label(db, calls[0], 1, "adjudicated")
    evals.set_label(db, calls[1], 0, "heuristic")
    evals.set_label(db, calls[2], 1, "adjudicated", label_type="owner_would_allow")
    cov = readouts.label_coverage(db, SINCE)
    assert (cov["verify_calls"], cov["fit_labelled"], cov["heuristic_only"], cov["unlabelled"]) == (4, 1, 1, 2)
    assert cov["fit_coverage_percent"] == 25.0
    assert cov["labels_by_type"] == {"claim_true": 2, "owner_would_allow": 1} and cov["labels_by_source"] == {"adjudicated": 2, "heuristic": 1}


# ---- Jev's shadow answer against the owner's ---------------------------------------------------------------------------------
def shadow_pair(db, owner, jev_exit, session, hold, *, created=60, answered=30, asked=62, called=61, link=True):
    """One ask the owner answered, with the `jev_asked` event and the proxy's call of that pair just before it (what the hook leaves behind). `link` writes the
    `jev_answered` event that ties the call to the pair; an older ledger has none."""
    add_ask(db, "R-11", "DENIED" if owner == "deny" else "CONSUMED", created=created, answered=answered, session=session, hold=hold)
    key = proxy._attempt_key(session, hold)  # noqa: SLF001
    db.event(key, "jev_asked", guard="R-11 high", actor="hook")
    db.con.execute("UPDATE events SET ts=? WHERE entity_id=? AND kind='jev_asked' AND id=(SELECT MAX(id) FROM events)", (at(asked), key))
    call = db.begin_call(proxy.QID, "h" + session + hold, session, version=2, primitive="noul", task_family="human_proxy")
    db.finish_call(call, p=0.5, exit_code=jev_exit, verdict="ACCEPT" if jev_exit == 0 else "ABSTAIN" if jev_exit == 3 else None)
    db.con.execute("UPDATE jev_calls SET ts=? WHERE call_id=?", (at(called), call))
    if link:
        db.event(key, "jev_answered", guard="R-11 high", actor="hook", detail={"call_id": call})
        db.con.execute("UPDATE events SET ts=? WHERE id=(SELECT MAX(id) FROM events)", (at(called),))
    return call


def test_the_shadow_join_reports_owner_denies_jev_recall_and_false_allows_with_counts():
    db = ledger.open()
    shadow_pair(db, "deny", 3, "s1", "h1")    # Jev did not allow what the owner denied: caught
    shadow_pair(db, "deny", 4, "s2", "h2")    # caught
    shadow_pair(db, "deny", 0, "s3", "h3")    # Jev allowed what the owner denied: a false allow
    shadow_pair(db, "deny", 5, "s4", "h4")    # Jev gave no answer
    shadow_pair(db, "allow", 0, "s5", "h5")   # agreed
    shadow_pair(db, "allow", 3, "s6", "h6")   # Jev was more careful than the owner
    add_ask(db, "R-11", "DENIED", created=60, answered=30, session="s7", hold="h7")   # an ask Jev was never asked about: not in the join
    out = readouts.shadow_agreement(db, SINCE)
    assert out["asks_answered"] == 7 and out["joined"] == 6
    assert (out["owner_denies"], out["jev_caught"], out["jev_false_allows"], out["jev_no_answer_on_denies"]) == (4, 2, 1, 1)
    assert (out["owner_allows"], out["jev_agreed_allows"], out["jev_did_not_allow_on_allows"]) == (2, 1, 1)
    assert out["recall_percent"] == 50.0 and 0 < out["recall_lower_95_percent"] < 50 and out["recall_note"] == ""


def test_with_no_owner_deny_the_recall_is_not_measured_never_one_hundred_percent():
    db = ledger.open()
    shadow_pair(db, "allow", 0, "s1", "h1")
    shadow_pair(db, "allow", 0, "s2", "h2")
    out = readouts.shadow_agreement(db, SINCE)
    assert out["joined"] == 2 and out["owner_denies"] == 0 and out["owner_allows"] == 2
    assert out["recall_percent"] is None and out["recall_lower_95_percent"] is None and "not measured" in out["recall_note"]


def test_an_old_jev_asked_event_does_not_pair_with_a_later_ask():
    db = ledger.open()
    shadow_pair(db, "deny", 0, "s1", "h1")
    db.con.execute("UPDATE events SET ts=? WHERE kind='jev_asked'", (at(60 + readouts.SHADOW_PAIR_S + 30),))
    assert readouts.shadow_agreement(db, SINCE)["joined"] == 0

def test_two_commands_of_one_session_are_not_mixed_up():
    db = ledger.open()
    shadow_pair(db, "deny", 3, "s1", "hA", created=60, answered=30, asked=65, called=62)    # A: the owner denied, Jev did not allow
    shadow_pair(db, "allow", 0, "s1", "hB", created=59, answered=30, asked=65, called=61)   # B: the owner allowed, Jev allowed
    out = readouts.shadow_agreement(db, SINCE)
    assert (out["joined"], out["owner_denies"], out["jev_caught"], out["jev_false_allows"]) == (2, 1, 1, 0)
    assert (out["owner_allows"], out["jev_agreed_allows"]) == (1, 1) and out["recall_percent"] == 100.0


def test_a_ledger_without_the_link_event_joins_only_when_one_call_fits():
    db = ledger.open()
    shadow_pair(db, "deny", 3, "s1", "hA", created=60, asked=65, called=62, link=False)
    shadow_pair(db, "allow", 0, "s1", "hB", created=59, asked=65, called=61, link=False)
    out = readouts.shadow_agreement(db, SINCE)   # two calls of the session fit each ask: a guess would mix them up, so there is no join
    assert (out["asks_answered"], out["joined"], out["jev_false_allows"], out["jev_caught"]) == (2, 0, 0, 0)


def test_a_ledger_without_the_link_event_still_joins_a_lone_call():
    db = ledger.open()
    shadow_pair(db, "deny", 3, "s1", "hA", link=False)
    out = readouts.shadow_agreement(db, SINCE)
    assert (out["joined"], out["jev_caught"]) == (1, 1)


def test_one_jev_call_is_not_counted_for_a_second_ask_of_the_same_hold():
    db = ledger.open()
    shadow_pair(db, "deny", 3, "s1", "h1")
    add_ask(db, "R-11", "DENIED", created=40, answered=20, session="s1", hold="h1")   # a retry: a new ask, and no new `jev_asked` event
    out = readouts.shadow_agreement(db, SINCE)
    assert out["asks_answered"] == 2 and out["joined"] == 1 and out["owner_denies"] == 1 and out["jev_caught"] == 1
    assert out["recall_percent"] == 100.0 and out["recall_lower_95_percent"] < 100   # one answer is one answer


def test_the_proxy_leaves_a_link_from_the_pair_to_its_jev_call(ready):
    assert hooks.pretool(m.call(KIND)).exit_code == 0
    (c,) = calls()
    (link,) = m.rows("SELECT entity_id, detail_json FROM events WHERE kind='jev_answered'")
    assert json.loads(link["detail_json"]) == {"call_id": c["call_id"]} and link["entity_id"].startswith("jevtry:")



# ---- jade review: friction beside correctness -------------------------------------------------------------------------------
def test_review_says_correctness_is_not_measured_when_nothing_is_marked_or_labelled():
    db = ledger.open()
    add_ask(db, "R-11", "CONSUMED", created=100, answered=70)
    add_ask(db, "R-11", "DENIED", created=100, answered=90)
    add_ask(db, "R-11", "EXPIRED")
    db.close()
    out = ops.review(None)
    assert "Questions JADE asked you (all time): 3. You allowed 1, denied 1, and 1 ran out with no answer" in out
    assert "You allowed 50% of the questions you answered. This does not say whether those answers were right." in out
    assert "Time to answer: half of the answers took 10 s or less" in out and "rule R-11: asked 3, allowed 1, denied 1, no answer 1" in out
    assert "Correctness is not measured" in out
    assert not any(word in out for word in ("silent share", "plain-high", "95% bound"))


def test_review_says_what_correctness_has_when_a_run_is_marked_or_a_label_exists():
    from jade import evals

    db = ledger.open()
    add_ask(db, "R-11", "CONSUMED", created=100, answered=70)
    r = ask.run(ask.verify_specs(companion=False), {"claim": "c", "evidence": "e"}, provider=fixture_provider(), session="s", db=db)
    evals.set_label(db, r.primary.call_id, 1, "adjudicated")
    db.close()
    out = ops.review(None)
    assert "Correctness, as far as it is measured: 0 run(s) you marked (0 wrong) and 1 Jev answer(s) with a checked label." in out
    assert "Correctness is not measured" not in out
