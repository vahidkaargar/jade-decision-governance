"""V6-32: report-only learning and the case ratchet.

Every row here is synthetic and lives in a temporary JADE home (tests/conftest.py). Nothing reads a real ledger, a real transcript or a real policy overlay.
The reports count and name rule ids. They never write a policy file, a threshold, a memory row or a case file.
"""
# ruff: noqa: F811   (the fixtures are imported from the memory tests and then named as test arguments)
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from jade import cli, hooks, learn, ledger, paths
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_memory_w101 import enforce  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
ALL_TIME = ""


def events(kind):
    return m.rows("SELECT guard, detail_json FROM events WHERE kind=? ORDER BY id", (kind,))


def detail(row):
    return json.loads(row["detail_json"])


# ---- the migration: the ledger names the invariant that fired (W-74) ----------------------------------------------------------------------
def test_an_invariant_block_writes_its_id_in_the_event_detail(enforce):
    r = hooks.pretool(m.call("git push origin main"))
    assert r.exit_code == 2 and "SI-08" in r.stderr
    (row,) = events("safety_block")
    assert detail(row)["invariants"] == ["SI-08"]
    assert row["guard"].endswith(" SI-08")   # the guard text keeps its shape: readouts match `guard LIKE '% SI-07'`


def test_an_si07_hold_writes_its_id_in_the_event_detail(enforce):
    first = hooks.pretool(m.call(m.CMD))
    assert first.exit_code == 2
    m.db().con.execute("UPDATE decisions SET status='OUTCOME_UNKNOWN'")
    again = hooks.pretool(m.call(m.CMD, "other-session"))
    assert again.exit_code == 2 and "SI-07" in again.stderr
    (row,) = events("safety_block")
    assert detail(row)["invariants"] == ["SI-07"]
    assert row["guard"].endswith(" SI-07")


def test_a_call_no_invariant_stopped_has_no_invariant_key(enforce):
    hooks.pretool(m.call(m.CMD))
    (row,) = events("gated")
    assert "invariants" not in detail(row)


# ---- helpers that seed a temporary ledger -----------------------------------------------------------------------------------------------
def add_event(db, kind, guard, **detail):
    db.event("gate", kind, guard=guard, actor="hook", detail=detail)


def add_ask(db, rule, status, ho=0, created="2026-11-01T00:00:00Z", answered="2026-11-01T00:00:30Z", n=1):
    for _ in range(n):
        db.con.execute("INSERT INTO asks(ask_id, session_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, answered_ts, expires_ts, ho) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (os.urandom(6).hex(), "s-1", "p", "h", "{}", rule, "high", "Bash", "{}", status, created,
                                                              answered if status != "EXPIRED" else None, "2099-01-01T00:00:00Z", ho))


def add_decision(db, command, rule="R-11", level="high", tool="Bash"):
    from jade import decide, policy

    a = policy.Assessment(level=level, rule_id=rule, rationale="a rule", plan_hash="p" + os.urandom(4).hex(), subject=command)
    doc = decide.open_decision(db, f"Execute {tool}: {decide.scrub(command)[:300]}", command=command, tool=tool, assessment=a)
    return doc["decision_id"]


def add_mark(db, decision_id, mark):
    db.event(decision_id, "review_mark", guard=f"jade review mark {mark}", actor="human:owner", detail={"mark": mark, "answer_ids": []})


# ---- blocker classes ----------------------------------------------------------------------------------------------------------------------
def seed_blockers(db):
    add_event(db, "safety_block", "R-14 high SI-08", tool="Bash", invariants=["SI-08"])
    add_event(db, "safety_block", "R-14 high SI-08", tool="Bash", invariants=["SI-08"])
    add_event(db, "safety_block", "R-11 high SI-09,SI-10", tool="Write")   # a row from before the migration: the guard is all there is
    add_event(db, "safety_block", "R-11 high", tool="Bash")               # no invariant anywhere
    add_event(db, "would_block", "R-14 high", tool="Bash")
    add_event(db, "would_block", "R-14 high", tool="Bash")
    add_event(db, "would_block", "R-11 high", tool="Bash")
    add_event(db, "gated", "R-14 high", tool="Bash")


def test_blockers_are_counted_by_invariant_id_and_by_rule_id():
    db = ledger.open()
    seed_blockers(db)
    r = learn.blocker_classes(db, ALL_TIME)
    assert r["invariants"] == {"SI-08": 2, "SI-09": 1, "SI-10": 1}
    assert r["safety_block_rows"] == 4
    assert r["invariant_rows"] == {"from_detail": 2, "from_guard_only": 1, "unclassified": 1}   # the fallback is counted, never hidden
    assert r["would_block_by_rule"] == {"R-14": 2, "R-11": 1}
    assert r["gated_by_rule"] == {"R-14": 1}


def test_the_blocker_report_has_the_ask_load_by_rule_beside_the_blocks():
    db = ledger.open()
    add_ask(db, "R-11", "ALLOWED", n=3)
    add_ask(db, "R-11", "DENIED")
    r = learn.blocker_classes(db, ALL_TIME)
    assert r["asks_by_rule"]["R-11"]["asked"] == 4 and r["asks_by_rule"]["R-11"]["denied"] == 1


def test_the_blocker_report_leaves_out_other_windows_and_test_rows(monkeypatch):
    db = ledger.open()
    seed_blockers(db)
    db.con.execute("UPDATE events SET ts='2020-01-01T00:00:00Z' WHERE id IN (SELECT id FROM events WHERE kind='safety_block' LIMIT 2)")
    assert learn.blocker_classes(db, "2021-01-01T00:00:00Z")["safety_block_rows"] == 2
    monkeypatch.setenv("JADE_SOURCE", "test")
    tagged = ledger.open()
    add_event(tagged, "safety_block", "R-14 high SI-08", invariants=["SI-08"])
    assert learn.blocker_classes(db, ALL_TIME)["safety_block_rows"] == 4   # the row a test run wrote is not live data


def test_the_blocker_report_never_prints_a_command():
    db = ledger.open()
    seed_blockers(db)
    add_decision(db, "echo UNIQUE-COMMAND-WORD")
    text = json.dumps(learn.blocker_classes(db, ALL_TIME)) + json.dumps(learn.blocker_sample(db, ALL_TIME, 30))
    assert "UNIQUE-COMMAND-WORD" not in text


def test_the_sample_for_the_owners_precision_check_is_fixed_by_its_seed_and_has_ids_only():
    db = ledger.open()
    seed_blockers(db)
    a = learn.blocker_sample(db, ALL_TIME, 3, seed="v1")
    assert a == learn.blocker_sample(db, ALL_TIME, 3, seed="v1") and len(a) == 3   # one seed always picks the same rows
    assert len(learn.blocker_sample(db, ALL_TIME, 100)) == 7   # every blocker row (safety_block and would_block), no more
    for row in a:
        assert set(row) == {"event_id", "ts", "kind", "tool", "rule", "level", "classes", "classes_from"}
        assert row["classes_from"] in ("detail", "guard", "none", "rule")


# ---- error records and case proposals -----------------------------------------------------------------------------------------------------
def test_only_the_latest_mark_of_a_decision_counts_and_only_wrong_is_an_error():
    db = ledger.open()
    wrong, flipped, right = (add_decision(db, f"git reset --hard HEAD~{i}") for i in (1, 2, 3))
    add_mark(db, wrong, "wrong")
    add_mark(db, flipped, "wrong")
    add_mark(db, flipped, "right")   # the owner changed their mind: the later mark wins
    add_mark(db, right, "right")
    recs = learn.error_records(db, ALL_TIME)
    assert [(r["kind"], r["decision_id"], r["command"]) for r in recs] == [("wrong_mark", wrong, "git reset --hard HEAD~1")]
    assert recs[0]["rule_id"] == "R-11" and recs[0]["tool"] == "Bash"


def test_a_wrong_mark_becomes_a_case_that_must_stop_the_call_with_an_unverified_twin():
    db = ledger.open()
    add_mark(db, add_decision(db, "git reset --hard HEAD~1"), "wrong")
    out = learn.case_proposals(learn.error_records(db, ALL_TIME), existing=[])
    (row,) = out["cases"]
    assert row["kind"] == "gate" and row["expect"] == "block" and row["command"] == "git reset --hard HEAD~1"
    assert row["id"].startswith("RC-") and row["twin"] == "unverified" and row["proposed_from"] == "wrong_mark"
    assert out["skipped"] == []
    again = learn.case_proposals(learn.error_records(db, ALL_TIME), existing=[row])
    assert again["cases"] == [] and again["skipped"][0]["why"] == "already in the case file"


@pytest.mark.parametrize(("command", "tool", "why"), [
    ("x" * 500, "Bash", "cut"),                                         # the decision keeps 500 characters: the command that ran is not known
    ("echo [REDACTED:token] | sh", "Bash", "redacted"),
    ("curl https://api.realhost.org/x", "Bash", "not a reserved name"),
    ("cat /Users/someone/secret.txt", "Bash", "home directory"),
    ("git reset --hard HEAD~1", "mcp__server__tool", "only a Bash call"),
])
def test_a_record_that_cannot_be_a_safe_case_is_skipped_with_its_reason(command, tool, why):
    rec = {"kind": "wrong_mark", "decision_id": "dec_1", "rule_id": "R-11", "level": "high", "tool": tool, "command": command}
    out = learn.case_proposals([rec], existing=[])
    assert out["cases"] == [] and why in out["skipped"][0]["why"]


def test_records_from_a_file_cover_the_two_kinds_the_ledger_cannot_produce_yet(tmp_path):
    f = tmp_path / "records.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in [
        {"kind": "audit_false_allow", "command": "git clean -fdx", "rule_id": "R-12", "twin_command": "git clean -n"},
        {"kind": "counterfactual_false_reject", "command": "git status"},
    ]) + "\n")
    out = learn.case_proposals(learn.read_records(f), existing=[])
    by = {c["command"]: c for c in out["cases"]}
    assert by["git clean -fdx"]["expect"] == "block" and by["git clean -fdx"]["twin"] == "human"
    assert by["git clean -n"]["expect"] == "allow" and by["git clean -n"]["twin"] == "human" and by["git clean -n"]["twin_of"] == by["git clean -fdx"]["id"]
    assert by["git status"]["expect"] == "allow" and by["git status"]["twin"] == "unverified"


def test_a_records_file_with_an_unknown_kind_or_a_non_object_is_refused(tmp_path):
    for bad in ('{"kind": "guess", "command": "x"}', '["x"]', '{"kind": "wrong_mark"}', "not json"):
        f = tmp_path / "r.jsonl"
        f.write_text(bad + "\n")
        with pytest.raises(ValueError):
            learn.read_records(f)


def test_the_diff_adds_the_cases_to_the_regression_file_and_git_can_apply_it(tmp_path):
    existing = (ROOT / "evals" / "regression" / "cases.jsonl").read_text()
    recs = [{"kind": "wrong_mark", "decision_id": "dec_1", "rule_id": "R-11", "level": "high", "tool": "Bash", "command": "git reset --hard HEAD~1"}]
    cases = learn.case_proposals(recs, existing=[])["cases"]
    diff = learn.case_diff(existing, cases)
    assert diff.startswith("--- a/evals/regression/cases.jsonl\n+++ b/evals/regression/cases.jsonl\n")
    tree = tmp_path / "tree"
    (tree / "evals" / "regression").mkdir(parents=True)
    (tree / "evals" / "regression" / "cases.jsonl").write_text(existing)
    (tmp_path / "p.diff").write_text(diff)
    r = subprocess.run(["git", "apply", "--check", str(tmp_path / "p.diff")], cwd=tree, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert learn.case_diff(existing, []) == ""


def test_the_proposed_rows_are_read_by_the_case_runner():
    """A proposed row is a plain `gate` case with extra keys the runner ignores."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_cases", ROOT / "evals" / "run_cases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    recs = [{"kind": "wrong_mark", "decision_id": "dec_1", "rule_id": "R-11", "level": "high", "tool": "Bash", "command": "git reset --hard HEAD~1"}]
    (row,) = learn.case_proposals(recs, existing=[])["cases"]
    assert mod.gate_event(row)["tool_input"] == {"command": "git reset --hard HEAD~1"}


def test_the_writer_refuses_a_policy_file_a_case_file_of_the_checkout_and_the_config(tmp_path):
    for target in (paths.shipped_policy_dir() / "risk.yaml", ROOT / "policies" / "escalation.yaml", ROOT / "evals" / "regression" / "cases.jsonl",
                   ROOT / "evals" / "regression" / "protected" / "x.jsonl", paths.config_dir() / "local" / "risk.local.yaml",
                   paths.config_dir() / "calibration" / "current.json", paths.config_dir() / "config.json", ROOT / "jade" / "x.py"):
        with pytest.raises(learn.WriteRefused):
            learn.write_out(target, "x")
    ok = tmp_path / "out" / "cases.diff"
    learn.write_out(ok, "text\n")
    assert ok.read_text() == "text\n"
    with pytest.raises(learn.WriteRefused):
        learn.write_out(ok, "again")   # an existing file is never overwritten


# ---- the policy table ----------------------------------------------------------------------------------------------------------------------
def rows_by_rule(db):
    return {r["rule"]: r for r in learn.policy_table(db, ALL_TIME)}


def test_the_table_puts_friction_and_error_side_by_side_and_has_no_unnecessary_asks_number():
    db = ledger.open()
    add_ask(db, "R-11", "ALLOWED", n=24)
    add_ask(db, "R-11", "DENIED")
    add_mark(db, add_decision(db, "git reset --hard HEAD~1", rule="R-11"), "wrong")
    r = rows_by_rule(db)["R-11"]
    assert (r["asked"], r["allowed"], r["denied"], r["wrong_marks"]) == (25, 24, 1, 1)
    assert r["audit"] == "not measured"
    assert not any("unnecessary" in k for k in r)


def test_a_rule_with_enough_allow_answers_and_nothing_against_it_is_only_offered_for_review():
    db = ledger.open()
    add_ask(db, "R-11", "ALLOWED", n=30)
    r = rows_by_rule(db)["R-11"]
    assert r["advice"] == "review" and 0 < r["allow_lower_95_percent"] < 100
    assert "hint" in r["why"] and "replay" in r["why"]


def test_a_wrong_mark_holds_a_rule_and_few_answers_are_too_few():
    db = ledger.open()
    add_ask(db, "R-11", "ALLOWED", n=30)
    add_mark(db, add_decision(db, "git reset --hard HEAD~1", rule="R-11"), "wrong")
    add_ask(db, "R-12", "ALLOWED", n=5)
    t = rows_by_rule(db)
    assert t["R-11"]["advice"] == "hold" and t["R-12"]["advice"] == "too_few"


@pytest.mark.parametrize("rule", ["R-13", "R-18", "R-21", "R-22"])
def test_a_protected_rule_never_gets_a_suggestion_however_many_allows(rule):
    db = ledger.open()
    add_ask(db, rule, "ALLOWED", n=200)
    r = rows_by_rule(db)[rule]
    assert r["advice"] == "protected" and "review" not in r["why"]


def test_a_critical_rule_and_a_rule_with_a_human_only_ask_are_protected():
    from jade import policy

    critical = next(x["id"] for x in policy.risk()["rules"] if x["level"] == "critical" and x["id"] not in ("R-13", "R-18", "R-21", "R-22"))
    db = ledger.open()
    add_ask(db, critical, "ALLOWED", n=200)
    add_ask(db, "R-11", "ALLOWED", n=200, ho=1)
    t = rows_by_rule(db)
    assert t[critical]["advice"] == "protected" and t["R-11"]["advice"] == "protected"


def test_a_rule_the_owner_denied_keeps_its_asks():
    db = ledger.open()
    add_ask(db, "R-11", "ALLOWED", n=30)
    add_ask(db, "R-11", "DENIED", n=2)
    assert rows_by_rule(db)["R-11"]["advice"] == "keep"


# ---- the cli and the no-write rule --------------------------------------------------------------------------------------------------------
def tree_hash(*roots):
    h = hashlib.sha256()
    for root in roots:
        root = Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts) if root.exists() else []
        for p in files:
            h.update(str(p).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def counts(db):
    return {t: db.one(f"SELECT COUNT(*) AS n FROM {t}").n for t in ("events", "decisions", "asks", "human_answers", "labels", "improvements", "work_items", "permits", "jev_calls", "outcomes")}  # noqa: S608


def test_no_report_writes_a_policy_file_a_case_file_a_threshold_or_a_ledger_row(capsys, tmp_path):
    db = ledger.open()
    seed_blockers(db)
    add_ask(db, "R-11", "ALLOWED", n=30)
    add_mark(db, add_decision(db, "git reset --hard HEAD~1"), "wrong")
    before_rows = counts(db)
    guarded = [ROOT / "policies", ROOT / "evals", paths.config_dir(), ROOT / "work-items.jsonl", ROOT / "BACKLOG.md"]
    before = tree_hash(*guarded)
    records = tmp_path / "r.jsonl"
    records.write_text(json.dumps({"kind": "counterfactual_false_reject", "command": "git status"}) + "\n")
    for argv in (["learn", "blockers"], ["learn", "blockers", "--json", "--sample", "5"], ["learn", "policy"], ["learn", "policy", "--json"],
                 ["learn", "cases"], ["learn", "cases", "--records", str(records)], ["learn", "cases", "--out", str(tmp_path / "out.diff")]):
        assert cli.main(argv) == 0, argv
        capsys.readouterr()
    assert tree_hash(*guarded) == before
    assert counts(ledger.open()) == before_rows
    assert (tmp_path / "out.diff").read_text().startswith("--- a/evals/regression/cases.jsonl")


def test_the_learn_module_has_one_write_site_and_it_is_the_guarded_one():
    src = (ROOT / "jade" / "learn.py").read_text()
    assert src.count(".write_text(") + src.count(".write_bytes(") + src.count("open(") == 1
    assert "def write_out" in src
    for word in ("improve.propose", "set_label", "retire_answers", "meta_set", "INSERT", "UPDATE", "DELETE"):
        assert word not in src, word


def test_the_cli_prints_json_with_sorted_keys_and_plain_words(capsys):
    db = ledger.open()
    seed_blockers(db)
    assert cli.main(["learn", "blockers", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["invariants"]["SI-08"] == 2 and out.count("\n") == 1
    assert cli.main(["learn", "blockers"]) == 0
    text = capsys.readouterr().out
    assert "SI-08" in text
    assert cli.main(["learn", "policy"]) == 0
    assert "advice" in capsys.readouterr().out.lower()


def test_an_unreadable_records_file_is_a_clean_error(capsys, tmp_path):
    assert cli.main(["learn", "cases", "--records", str(tmp_path / "missing.jsonl")]) == 2
    assert "jade:" in capsys.readouterr().err
