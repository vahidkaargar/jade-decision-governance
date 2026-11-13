"""W-58: the ledger and the hooks at scale. The hot statements (the ones a gated call, the Stop hook, SessionStart, the permit lookups and the checks run)
must search an index, whatever the table size, and the gate must stay inside a time budget on a ledger that has grown.

The data is synthetic (evals/performance/ledger_load.py builds it in a temporary home). The full measurement at 100k rows, with the numbers the budgets below
come from, is evals/performance/result-ledger-100k-2026-11-09.json; CI builds 20k rows to stay quick, which is enough for a table scan to show in a plan.
"""

import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

from jade import decide, ledger, state_machine
from jade.util import ts_in

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ledger_load", ROOT / "evals" / "performance" / "ledger_load.py")
ll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ll)

ROWS = 20_000


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """A 20k-row synthetic ledger, built once for the module in a temporary directory."""
    d = tmp_path_factory.mktemp("w58")
    path = d / "jade.sqlite"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("JADE_HOME", str(d))
        mp.setenv("JADE_CONFIG", str(d / "config"))
        mp.delenv("JADE_SOURCE", raising=False)
        counts = ll.build(path, ROWS)
    assert counts["jev_calls"] == counts["events"] == ROWS and counts["decisions"] >= ROWS
    return path


@pytest.fixture
def at_big(big, monkeypatch):
    monkeypatch.setenv("JADE_HOME", str(big.parent))
    monkeypatch.setenv("JADE_CONFIG", str(big.parent / "config"))
    return big


def p95(fn, runs=25) -> float:
    ms = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        ms.append((time.perf_counter() - t) * 1000)
    return sorted(ms)[int(0.95 * (runs - 1))]


# ---- plans -------------------------------------------------------------------------------------------------
def test_no_hot_statement_scans_a_table_at_20k_rows(at_big):
    db = ledger.open(at_big)
    ops = ll.operations(db)
    ops["pretool_gated"]()   # loads the policy
    tracer = ll.Tracer()
    with ll.tracing(tracer, db):
        for fn in ops.values():
            fn()
    db.close()
    assert len(tracer.sql) >= 15, "the operations ran too few statements: the tracer is not seeing the hooks' own connections"
    scans = ll.full_scans(sqlite3.connect(at_big), tracer.sql)
    assert scans == [], "a statement the hooks or the checks run reads a whole table:\n" + "\n".join(f"  {s['sql']}\n    {s['plan']}" for s in scans)


def test_the_scan_detector_flags_a_missing_index(tmp_path, monkeypatch):
    """The check above is not vacuous: without the indexes of this item the same operations do scan."""
    monkeypatch.setenv("JADE_HOME", str(tmp_path / "h"))
    path = tmp_path / "small.sqlite"
    ll.build(path, 2000)
    ll.drop_w58_indexes(path)
    db = ledger.open(path)   # keeps its user_version, so the indexes are not created again
    ops = ll.operations(db)
    ops["pretool_gated"]()
    tracer = ll.Tracer()
    with ll.tracing(tracer, db):
        for fn in ops.values():
            fn()
    db.close()
    flagged = " ".join(s["sql"] for s in ll.full_scans(sqlite3.connect(path), tracer.sql))
    for table in ("permits WHERE decision_id", "outcomes WHERE decision_id", "events WHERE kind", "decisions WHERE status IN"):
        assert table in flagged, table


def test_the_lookups_use_the_indexes_made_for_them(at_big):
    """The plans of the statements the code really ran (not a copy of the SQL): each hot lookup names the index that was made for it."""
    db = ledger.open(at_big)
    ops = ll.operations(db)
    ops["pretool_gated"]()
    tracer = ll.Tracer()
    with ll.tracing(tracer, db):
        for fn in ops.values():
            fn()
    db.close()
    con = sqlite3.connect(at_big)

    def plan_of(*fragments: str) -> str:
        stmt = next((s for s in tracer.sql.values() if all(f in s for f in fragments)), None)
        assert stmt is not None, f"no statement with {fragments} was run"
        return " ".join(ll.explain(con, stmt))

    hold = plan_of("FROM decisions", "hold_key=")
    assert "decisions_plan" in hold and "decisions_hold" in hold, hold                       # SI-07 hold: one lookup per key
    assert "decisions_plan" in plan_of("FROM decisions", "status='EXECUTING'")                  # PostToolUse
    assert "decisions_open" in plan_of("FROM decisions", "expires_at <")                        # sweep, in every gated call
    for state in ("ISSUED", "PENDING"):   # find_usable, pending_for: permits_plan, or permits_open when the planner prefers it; either reads only the few unexpired open permits
        assert any(i in plan_of("FROM permits WHERE plan_hash", f"state='{state}'") for i in ("permits_plan", "permits_open")), state
    assert "permits_open" in plan_of("UPDATE permits SET state='EXPIRED'")                      # permit sweep
    assert "permits_decision" in plan_of("FROM permits WHERE decision_id")                      # revoke when a decision ends
    assert "outcomes_decision" in plan_of("FROM outcomes")
    assert "calls_session" in plan_of("FROM jev_calls WHERE session_id", "question_version")     # dedup anchor
    assert "calls_claim" in plan_of("NOT EXISTS (SELECT 1 FROM jev_calls d")                    # undisposed (Stop hook, jade ack)
    assert "calls_evaluating" in plan_of("UPDATE jev_calls SET state='DONE'", "STALE_EVALUATING")   # stale sweep in every begin_call
    assert "events_kind_ts" in plan_of("FROM events WHERE kind IN ('gated'")


# ---- behaviour that the new sweep query must keep -------------------------------------------------------------
def test_sweep_still_ends_every_overdue_state_and_ignores_finished_decisions():
    db = ledger.open()
    ids = {}
    for status in ("WAITING_HUMAN", "CLASSIFIED", "EXECUTING", "VERIFYING", "OUTCOME_UNKNOWN", "SUCCEEDED", "REJECTED", "EXPIRED"):
        ids[status] = decide.open_decision(db, f"synthetic decision in {status}", command="ls", session="sess-w58-0001")["decision_id"]
        db.con.execute("UPDATE decisions SET status=?, expires_at=? WHERE decision_id=?", (status, "2020-01-01T00:00:00Z", ids[status]))
    db.con.execute("UPDATE decisions SET expires_at=? WHERE decision_id=?", (ts_in(3600), decide.open_decision(db, "synthetic live decision", command="ls", session="sess-w58-0001")["decision_id"]))
    res = decide.sweep(db)
    now = {s: db.one("SELECT status FROM decisions WHERE decision_id=?", (i,)).status for s, i in ids.items()}
    assert now == {"WAITING_HUMAN": "EXPIRED", "CLASSIFIED": "EXPIRED", "EXECUTING": "OUTCOME_UNKNOWN", "VERIFYING": "OUTCOME_UNKNOWN", "OUTCOME_UNKNOWN": "OUTCOME_UNKNOWN",
                   "SUCCEEDED": "SUCCEEDED", "REJECTED": "REJECTED", "EXPIRED": "EXPIRED"}
    assert res == {"expired": 2, "unknown": 2}
    assert decide.sweep(db) == {"expired": 0, "unknown": 0}   # nothing left to do: a second sweep changes nothing


def test_every_state_the_sweep_acts_on_is_listed_in_its_query():
    """The status list of the query is built from the state machine, so a new waiting state is swept without anyone editing the query."""
    db = ledger.open()
    seen = []
    db.con.set_trace_callback(seen.append)
    decide.sweep(db)
    sql = next(s for s in seen if "FROM decisions" in s)
    for status in sorted({*state_machine.EXPIRES, "EXECUTING", "VERIFYING"}):
        assert f"'{status}'" in sql, status
    assert not set(state_machine.TERMINAL) & {s for s in state_machine.EXPIRES}


# ---- the schema version ------------------------------------------------------------------------------------
def test_open_applies_the_schema_once_and_then_skips_it(monkeypatch):
    db = ledger.open()
    assert db.con.execute("PRAGMA user_version").fetchone()[0] == ledger.SCHEMA_VERSION
    db.close()
    calls = []
    monkeypatch.setattr(ledger.Ledger, "_migrate", lambda self: calls.append(1))
    ledger.open().close()
    assert calls == [], "a file already at the current version must not run the schema script or the migration check again"


def test_an_older_file_is_upgraded_in_place_with_its_rows_and_indexed(tmp_path):
    old = tmp_path / "jade.sqlite"
    con = sqlite3.connect(old)
    legacy = ledger.SCHEMA.replace(" language TEXT,", "")   # the first release: no `language`, no `hold_key`, no `source`, no W-58 index, user_version 0
    con.executescript(legacy)
    con.execute("INSERT INTO jev_calls(call_id, question_id, state_hash, state, ts) VALUES('call_old000000001','q.x',?, 'DONE','2026-11-09T00:00:00Z')", ("a" * 64,))
    con.execute("INSERT INTO decisions(decision_id, status, plan_hash, doc_json, created_at, updated_at, expires_at) VALUES('dec_old000000001','SUCCEEDED','p','{}','t','t','t')")
    con.commit()
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    con.close()
    db = ledger.open(old)
    names = {r["name"] for r in db.con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"decisions_open", "decisions_plan", "decisions_hold", "permits_open", "permits_decision", "outcomes_decision", "events_kind_ts", "calls_claim",
            "calls_evaluating", "decisions_test", "permits_test"} <= names
    assert db.con.execute("PRAGMA user_version").fetchone()[0] == ledger.SCHEMA_VERSION
    assert db.one("SELECT hold_key FROM decisions WHERE decision_id='dec_old000000001'").hold_key is None   # the migrated column exists and the row is intact
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls").n == 1
    db.close()
    ledger.open(old).close()   # idempotent


def test_the_schema_version_follows_the_schema_text():
    """It is a digest of the schema, the migrations and the indexes: editing any of them upgrades every file once, nobody bumps a number."""
    import json as j
    import zlib

    assert ledger.SCHEMA_VERSION == zlib.crc32(j.dumps([ledger.SCHEMA, ledger.INDEXES, ledger.MIGRATIONS], sort_keys=True).encode()) & 0x7FFFFFFF
    assert zlib.crc32(j.dumps([ledger.SCHEMA, ledger.INDEXES + "-- x", ledger.MIGRATIONS], sort_keys=True).encode()) & 0x7FFFFFFF != ledger.SCHEMA_VERSION


# ---- time budgets -----------------------------------------------------------------------------------------
# Measured at 100k rows (result-ledger-100k-2026-11-09.json): the gated PreToolUse handler took 1.35 ms p95 in process (107 ms before the indexes and the
# narrower sweep), and a whole `jade hook pretool` process 162 ms p95 against 166 ms on an empty ledger: interpreter start and imports are the cost, the
# ledger is not. The in-process budget is ten times the measurement, so a noisy CI machine passes. A table scan grows linearly with the rows (about 100 ms at
# 100k) but is only about 8 ms at this test's 20k, under the 15 ms budget, and a whole hook process costs the same with and without the indexes: these two
# timing tests cannot notice a missing index, they catch a gross regression of the hook itself (an accidental full-ledger read of 100 ms or more at 100k, a
# slow import). The plan tests above are the guard. The process budget is six times the measurement. The host kills a hook at 10 s and JADE's own alarm is
# 6 s (hooks.BUDGET_S): both budgets sit far below them.
GATE_BUDGET_MS = 15.0
HOOK_PROCESS_BUDGET_MS = 1000.0


@pytest.mark.timing
def test_the_gated_pretool_handler_stays_inside_its_budget_at_20k_rows(at_big):
    from jade import hooks

    event = {"tool_name": "Bash", "tool_input": {"command": ll.SYNTHETIC_GATED}, "session_id": ll.BENCH_SESSION, "cwd": ll.BENCH_CWD}
    assert hooks.pretool(event).exit_code == 2   # also loads the policy: a hook process pays that once and the budget is for the ledger work
    got = p95(lambda: hooks.pretool(event))
    assert got < GATE_BUDGET_MS, f"gated pretool p95 {got:.1f} ms at {ROWS} rows (budget {GATE_BUDGET_MS} ms; 1.35 ms measured at 100k rows)"


@pytest.mark.timing
def test_a_whole_hook_process_stays_inside_its_budget_at_20k_rows(at_big, tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    env = ll.isolated_env(at_big.parent, cfg)
    event = {"tool_name": "Bash", "tool_input": {"command": ll.SYNTHETIC_GATED}, "session_id": ll.BENCH_SESSION, "cwd": ll.BENCH_CWD}
    ll.hook_process(env, "pretool", event)
    runs = [ll.hook_process(env, "pretool", event) for _ in range(8)]
    assert {code for _ms, code in runs} == {2}
    got = sorted(ms for ms, _code in runs)[int(0.95 * 7)]
    assert got < HOOK_PROCESS_BUDGET_MS, f"jade hook pretool p95 {got:.0f} ms at {ROWS} rows (budget {HOOK_PROCESS_BUDGET_MS:.0f} ms; 162 ms measured at 100k rows)"


def test_the_benchmark_only_ever_touches_the_temporary_home(at_big):
    """The environment given to a child hook names the temporary home, the empty config directory, and nothing of the live ledger."""
    env = ll.isolated_env(at_big.parent, at_big.parent / "config")
    assert env["JADE_HOME"] == str(at_big.parent) and "/.local/share/jade" not in json.dumps({k: v for k, v in env.items() if k.startswith("JADE_")})


# ---- review round 2 --------------------------------------------------------------------------------------
def test_an_allowed_scan_exempts_only_that_statement_and_not_one_that_starts_like_it(tmp_path, monkeypatch):
    """`jade check` counts jev_calls on purpose; the per-call `calls today` query starts with the same words and must still be flagged without its index."""
    monkeypatch.setenv("JADE_HOME", str(tmp_path / "h"))
    path = tmp_path / "small.sqlite"
    ll.build(path, 3000)
    con = sqlite3.connect(path)
    con.execute("DROP INDEX IF EXISTS calls_ts")
    con.commit()
    con.close()
    con = sqlite3.connect(path)
    hot = "SELECT COUNT(*) AS n FROM jev_calls WHERE ts >= strftime('%Y-%m-%dT00:00:00Z','now')"
    allowed = "SELECT COUNT(*) AS n FROM jev_calls"
    assert any("SCAN jev_calls" in p for p in ll.explain(con, hot))
    flagged = ll.full_scans(con, {"hot": hot, "allowed": allowed, "spaced": "SELECT  COUNT(*)  AS n\n FROM jev_calls "})
    assert [s["sql"] for s in flagged] == [hot]
    assert ll.full_scans(con, {"order": "SELECT permit_id FROM permits ORDER BY permit_id"}) == []
    con.close()
