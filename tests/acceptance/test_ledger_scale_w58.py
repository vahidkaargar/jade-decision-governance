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


def best_p95(fn, budget_ms: float, tries: int = 5, runs: int = 25, pause_s: float = 0.1) -> tuple[float, list[float]]:
    """W-126: the p95 of `fn` over `runs` calls, measured again when it is over the budget, at most `tries` times. Returns the best p95 and every p95 seen.

    A shared CI runner can stall for tens of milliseconds (one run saw 28 ms against 15 ms, 2 ms locally), and a stall is short: it does not last through
    five separate measurements. A real slowdown of the code is in every measurement, so it is over the budget in the best one too: the budget keeps its
    meaning. Only a try that is over the budget is repeated, and the first try under it ends the loop, so a quiet machine pays for one measurement.
    """
    seen: list[float] = []
    for i in range(tries):
        if i:
            time.sleep(pause_s)   # step out of a short stall before measuring again
        seen.append(p95(fn, runs))
        if seen[-1] < budget_ms:
            break
    return min(seen), seen


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
# W-126: the in-process test measures up to five times and judges the best p95 (see best_p95), because one CI run stalled to 28 ms against 15 ms.
GATE_BUDGET_MS = 15.0
HOOK_PROCESS_BUDGET_MS = 1000.0


@pytest.mark.timing
def test_the_gated_pretool_handler_stays_inside_its_budget_at_20k_rows(at_big):
    from jade import hooks

    event = {"tool_name": "Bash", "tool_input": {"command": ll.SYNTHETIC_GATED}, "session_id": ll.BENCH_SESSION, "cwd": ll.BENCH_CWD}
    assert hooks.pretool(event).exit_code == 2   # also loads the policy: a hook process pays that once and the budget is for the ledger work
    got, tries = best_p95(lambda: hooks.pretool(event), GATE_BUDGET_MS)
    assert got < GATE_BUDGET_MS, (f"gated pretool best p95 {got:.1f} ms at {ROWS} rows in {len(tries)} tries (budget {GATE_BUDGET_MS} ms; 1.35 ms measured at 100k rows); "
                                  f"every try: {', '.join(f'{t:.1f}' for t in tries)} ms")


def _scripted(monkeypatch, values):
    """Replace this module's `p95` by one that returns `values` in turn and records the `runs` it was asked for. No clock and no sleep is involved, so the
    tests below cannot be failed by a loaded machine (the first version slept for real and failed under load: a 0.5 ms try measured over 20 ms)."""
    import sys

    asked = []
    it = iter(values)

    def fake(fn, runs=25):
        asked.append(runs)
        return next(it)

    monkeypatch.setattr(sys.modules[__name__], "p95", fake)
    return asked


def test_a_short_stall_on_the_runner_does_not_fail_the_budget_test(monkeypatch):
    """W-126: the first try meets a stall (40 ms against a 20 ms budget), the second does not: the best try passes. The old single measurement failed here."""
    asked = _scripted(monkeypatch, [40.0, 0.5])
    got, tries = best_p95(lambda: None, 20.0, tries=5, runs=5, pause_s=0)
    assert got == 0.5 and tries == [40.0, 0.5] and asked == [5, 5]


def test_a_stall_in_the_middle_tries_is_also_survived(monkeypatch):
    _scripted(monkeypatch, [40.0, 30.0, 3.0])
    got, tries = best_p95(lambda: None, 20.0, tries=5, runs=5, pause_s=0)
    assert got == 3.0 and len(tries) == 3


def test_a_real_slowdown_still_fails_the_budget_test_in_every_try(monkeypatch):
    """The budget keeps its meaning: code that is slow in all tries (12 ms against 4 ms) is over the budget in the best try, after all the tries."""
    _scripted(monkeypatch, [12.0, 13.0, 12.5])
    got, tries = best_p95(lambda: None, 4.0, tries=3, runs=5, pause_s=0)
    assert got == 12.0 and len(tries) == 3 and min(tries) == got and not got < 4.0


def test_a_quiet_machine_pays_for_one_try(monkeypatch):
    asked = _scripted(monkeypatch, [0.1])
    got, tries = best_p95(lambda: None, 50.0, tries=5, runs=7, pause_s=0)
    assert asked == [7] and tries == [0.1] and got == 0.1


def test_the_pause_between_tries_is_taken_only_between_tries(monkeypatch):
    _scripted(monkeypatch, [40.0, 30.0, 1.0])
    import sys
    from types import SimpleNamespace

    slept = []
    monkeypatch.setattr(sys.modules[__name__], "time", SimpleNamespace(sleep=slept.append))   # this module's `time` only, not the real one
    best_p95(lambda: None, 20.0, tries=5, runs=5, pause_s=0.1)
    assert slept == [0.1, 0.1]


@pytest.mark.timing   # it reads a clock (a fake one, so it cannot fail under load); tests/test_timing_marks.py wants every such test marked
def test_p95_reads_the_95th_percentile_of_the_call_times(monkeypatch):
    """The helper itself, on a fake clock:25 calls of 1..25 ms give the 23rd value (index int(0.95 * 24) = 22)."""
    import sys
    from types import SimpleNamespace

    clock = iter([x for ms in range(1, 26) for x in (0.0, ms / 1000)])
    monkeypatch.setattr(sys.modules[__name__], "time", SimpleNamespace(perf_counter=lambda: next(clock)))   # this module's `time` only
    assert p95(lambda: None, 25) == pytest.approx(23.0)


def test_the_gate_budget_test_uses_the_retrying_measurement():
    """The budget test is wired to the helper (a bare p95 compare is what failed on CI)."""
    import inspect

    src = inspect.getsource(test_the_gated_pretool_handler_stays_inside_its_budget_at_20k_rows)
    assert "best_p95(" in src and "GATE_BUDGET_MS" in src and " p95(" not in src


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
