"""Synthetic-load benchmark for the ledger and the hooks (W-58). Builds a ledger of N synthetic rows in a TEMPORARY JADE_HOME (never the live one),
times the hook paths and the main queries, and prints EXPLAIN QUERY PLAN for every statement the code ran. All content is synthetic: no real
command, host, address or session ever enters the file. Usage:

    uv run python evals/performance/ledger_load.py [--rows 100000] [--runs 30] [--out evals/performance/result-ledger-100k-2026-11-09.json]

`build()` and `capture_plans()` are also imported by tests/acceptance/test_ledger_scale_w58.py.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import random
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

STATUS_MIX = (("SUCCEEDED", 0.80), ("EXPIRED", 0.12), ("REJECTED", 0.05), ("CANCELLED", 0.03))
EXIT_MIX = (0, 0, 0, 0, 0, 0, 0, 3, 3, 4, 4, 5, 6)
KINDS = ("gated", "gated", "gated", "open", "classify", "route", "safety_block", "permit_consumed", "BYPASS", "would_block")
SYNTHETIC_GATED = "git reset --hard HEAD~3"   # a high-risk command shape; only ever classified, never run
BENCH_SESSION = "sess-bench-0001"
BENCH_CWD = "/nonexistent-bench"


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build(path: Path | str, n: int, *, seed: int = 20261109, days: int = 90) -> dict:
    """Fill the ledger at `path` with `n` synthetic jev_calls, decisions and events and n/10 permits, spread over `days` days. A tenth of the
    Jev calls sit in one very long session (the worst case for the per-session lookups) and the rest in about n/50 sessions. Returns the counts."""
    from jade import ledger
    from jade.util import now, ts

    rnd = random.Random(seed)  # noqa: S311 - synthetic data, not security
    db = ledger.open(path)
    t0 = now()

    def when(i: int) -> str:
        return ts(t0 - timedelta(seconds=(days * 86400) * (1 - i / max(1, n))))

    sessions = max(1, n // 50)
    policy_version = "synthetic"
    calls, decisions, events, permits_rows = [], [], [], []
    for i in range(n):
        sess = "sess-heavy-0001" if rnd.random() < 0.10 else f"sess-{rnd.randrange(sessions):06d}"
        exit_code = rnd.choice(EXIT_MIX)
        claim = _h(f"claim-{rnd.randrange(max(1, n // 4))}")
        calls.append((f"call_{i:08d}", f"dec_{rnd.randrange(n):012d}" if rnd.random() < 0.3 else None, f"q.synthetic.{rnd.randrange(5)}", 1, "noul", "verify", claim,
                      "synthetic/model", "synthetic/model-1", "synthetic", _h(f"state-{i}"), 200, f"synthetic state text {i}", f"synthetic question {i}",
                      json.dumps({"verdict": "ACCEPT" if exit_code == 0 else "ABSTAIN"}), round(rnd.random(), 3), "ACCEPT" if exit_code == 0 else "ABSTAIN",
                      round(300 + rnd.random() * 400, 1), 900, 40, 0.0001, "PROVIDER" if exit_code in (5, 6) else None, exit_code, sess,
                      "DONE", "low", 0, None, None, "en", 1 if exit_code else 0, when(i), when(i)))
        status = "SUCCEEDED"
        r, acc = rnd.random(), 0.0
        for name, share in STATUS_MIX:
            acc += share
            if r < acc:
                status = name
                break
        plan = _h(f"plan-{rnd.randrange(max(1, n // 2))}")
        did = f"dec_{i:012d}"
        doc = {"decision_id": did, "objective": f"synthetic decision {i}", "status": status, "risk": {"level": "high", "reversibility": "reversible"},
               "policy_version": policy_version}
        decisions.append((did, status, None, "high", "R-00", "ESCALATE", sess, plan, 0, json.dumps(doc), when(i), when(i), when(i)))
        events.append((did if rnd.random() < 0.7 else "gate", rnd.choice(KINDS), None, None, "synthetic guard", "hook", None,
                       json.dumps({"session": sess, "tool": "Bash", "permission_mode": "default"}), when(i)))
        if i % 10 == 0:
            state = rnd.choice(("CONSUMED",) * 8 + ("EXPIRED",) * 2)
            pdoc = {"permit_id": f"prm_{i:012d}", "policy_version": policy_version, "state": state}
            permits_rows.append((f"prm_{i:012d}", did, state, "high", plan, "synthetic.action", "*", 1 if state == "CONSUMED" else 0, 1800, json.dumps(pdoc),
                                 when(i), when(i)))
    # a handful of open rows, the shapes the hot queries look for
    open_ts = ts(t0 + timedelta(hours=12))
    for k, status in enumerate(("WAITING_HUMAN",) * 10 + ("EXECUTING",) * 5 + ("OUTCOME_UNKNOWN",) * 5):
        did = f"dec_open{k:08d}"
        doc = {"decision_id": did, "objective": f"synthetic open decision {k}", "status": status, "risk": {"level": "high", "reversibility": "reversible"}}
        decisions.append((did, status, None, "high", "R-00", "ESCALATE", BENCH_SESSION, _h(f"open-plan-{k}"), 0, json.dumps(doc), ts(t0), ts(t0), open_ts))
    with db.tx() as con:
        con.executemany("INSERT INTO jev_calls(call_id, decision_id, question_id, question_version, primitive, task_family, claim_hash, request_model, resolved_model, "
                        "provider, state_hash, state_bytes, state_text, question_text, answers_json, p, verdict, latency_ms, input_tokens, output_tokens, cost_usd, "
                        "error_kind, exit_code, session_id, state, risk, redactions, disposition, disposition_note, language, reminded, ts, done_ts) "
                        "VALUES(" + ",".join("?" * 33) + ")", calls)
        con.executemany("INSERT INTO decisions(decision_id, status, prev_status, risk_level, rule_id, route, session_id, plan_hash, revision_count, doc_json, "
                        "created_at, updated_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", decisions)
        con.execute("UPDATE decisions SET hold_key=plan_hash")
        con.executemany("INSERT INTO events(entity_id, kind, prev_state, next_state, guard, actor, idempotency_key, detail_json, ts) VALUES(?,?,?,?,?,?,?,?,?)", events)
        con.executemany("INSERT INTO permits(permit_id, decision_id, state, risk_level, plan_hash, action, scope, uses, ttl_s, doc_json, issued_at, expires_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", permits_rows)
    db.meta_set("synthetic", "1")
    counts = {t: db.one(f"SELECT COUNT(*) AS n FROM {t}").n for t in ("jev_calls", "decisions", "events", "permits")}  # noqa: S608 - constant names
    db.close()
    return counts


def explain(con: sqlite3.Connection, sql: str) -> list[str]:
    return [r[3] if len(r) > 3 else str(r) for r in con.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]


class Tracer:
    """Collects every statement a connection runs (with the bound values expanded), de-duplicated by shape."""

    def __init__(self):
        self.sql: dict[str, str] = {}

    def __call__(self, stmt: str) -> None:
        shape = re.sub(r"'[^']*'|\b\d+(?:\.\d+)?\b", "?", stmt)
        self.sql.setdefault(shape, stmt)


@contextlib.contextmanager
def tracing(tracer: Tracer, db=None):
    """Every ledger connection opened inside the block, and `db` if given, reports its statements to `tracer`, so the plans of what the hooks run
    in their own connections are checked too, not a copy of the SQL kept here."""
    from jade import ledger

    real = ledger.Ledger.__init__

    def init(self, *a, **kw):
        real(self, *a, **kw)
        self.con.set_trace_callback(tracer)

    ledger.Ledger.__init__ = init
    if db is not None:
        db.con.set_trace_callback(tracer)
    try:
        yield
    finally:
        ledger.Ledger.__init__ = real
        if db is not None:
            db.con.set_trace_callback(None)


def operations(db):
    """Named operations that exercise the code paths a hook, the stop reminder, the checks and the daily job run. Each takes the open ledger."""
    from jade import decide, hooks, ledger, ops, permits, policy
    from jade.util import now, ts

    ev = {"tool_name": "Bash", "tool_input": {"command": SYNTHETIC_GATED}, "session_id": BENCH_SESSION, "cwd": BENCH_CWD}

    def gate():
        hooks.pretool(ev)

    def posttool():
        hooks.posttool({**ev, "tool_response": {}})

    def find_usable_and_pending():
        a = policy.assess("Bash", ev["tool_input"], BENCH_CWD)
        permits.find_usable(db, a.plan_hash, level=a.level, session=BENCH_SESSION)
        permits.pending_for(db, a.plan_hash)
        hooks._si07_hold(db, a)

    def dedup_and_calls():
        cid = db.begin_call("q.synthetic.0", _h("bench-state"), "sess-heavy-0001", claim_hash=_h("bench-claim"))
        db.finish_call(cid, exit_code=0, p=0.9, verdict="ACCEPT", latency_ms=400.0)
        db.one("SELECT COUNT(*) AS n FROM jev_calls WHERE ts >= strftime('%Y-%m-%dT00:00:00Z','now')")

    def stop_undisposed():
        hooks.undisposed(db, "sess-heavy-0001")
        hooks.undisposed(db, "sess-heavy-0001", only_unreminded=False)   # `jade ack --all`: every undisposed call of the longest session
        hooks.undisposed(db, "sess-000001")

    def sweeps():
        decide.sweep(db)
        permits.sweep(db)

    def check_rows():
        db.one("SELECT COUNT(*) AS n FROM jev_calls")
        db.one("SELECT MAX(ts) AS t FROM jev_calls")
        ops.integrity_problems(db)

    def decision_lookups():
        did = f"dec_{1:012d}"
        decide.get(db, did)
        db.all("SELECT kind FROM events WHERE entity_id=? ORDER BY id", (did,))
        db.all("SELECT permit_id FROM permits WHERE decision_id=? AND state IN ('PENDING','ISSUED')", (did,))
        db.all("SELECT outcome_id FROM outcomes WHERE decision_id=? ORDER BY rowid DESC LIMIT 1", (did,))

    def start_and_events_window():
        db.all("SELECT detail_json FROM events WHERE kind IN ('gated','safety_block') AND " + ledger.live() + " AND ts>=?", (ts(now() - timedelta(days=7)),))

    def session_start():
        hooks.session_start({"session_id": BENCH_SESSION, "cwd": BENCH_CWD})

    return {"session_start": session_start, "pretool_gated": gate, "posttool": posttool, "permit_and_hold_lookups": find_usable_and_pending, "begin_call_dedup": dedup_and_calls,
            "stop_undisposed": stop_undisposed, "sweeps": sweeps, "check_ledger_rows": check_rows, "decision_lookups": decision_lookups,
            "events_window": start_and_events_window}


# ---- measurement ---------------------------------------------------------------------------------
# Statements that read a whole table on purpose, with the reason. Everything else a measured operation runs must search an index. A covering-index
# scan counts as a scan here: it is O(n) however cheap it is today.
ALLOWED_SCANS = {
    "SELECT COUNT(*) AS n FROM jev_calls": "`jade check` prints the row count: a covering-index scan, a few ms at 100k rows, run by a human or the daily job",
    "FROM permits ORDER BY permit_id": "`jade check` integrity row validates every open permit: a read of the permits table, run by a human or the daily job",
    "FROM improvements ORDER BY candidate_id": "`jade check` integrity row: the improvements table is tiny (one row per mined candidate)",
}
SMALL_TABLES = ("work_items", "improvements", "meta", "labels")   # one row per backlog item, mined candidate, setting or adjudicated call: never the tables that grow with every command


def _allowed_scan(stmt: str) -> bool:
    """A whole statement is allowed when it IS an ALLOWED_SCANS key (a `SELECT` key is the whole statement, whitespace aside), or contains one that is a
    clause of its own (a `FROM` key). A longer statement that merely starts with a key is a different query: the per-call `COUNT(*) ... WHERE ts >=` is not
    the row count `jade check` prints."""
    norm = re.sub(r"\s+", " ", stmt).strip().rstrip(";")
    return any(norm == k if k.startswith("SELECT") else k in norm for k in ALLOWED_SCANS)


def full_scans(con: sqlite3.Connection, statements: dict[str, str]) -> list[dict]:
    """The statements among `statements` (shape -> expanded SQL) whose plan reads a whole table that grows with use, minus ALLOWED_SCANS."""
    out = []
    for stmt in statements.values():
        if not stmt.lstrip().upper().startswith(("SELECT", "UPDATE", "DELETE")):
            continue
        try:
            plan = explain(con, stmt)
        except sqlite3.Error:
            continue
        # `SCAN <table|alias>` (a scan of a name) but not `SCAN 1 CONSTANT ROW` or a scan of a subquery's temp result
        scans = [p for p in plan if re.match(r"SCAN (?!CONSTANT)", p) and not re.match(rf"SCAN (?:{'|'.join(SMALL_TABLES)})\b", p)]
        if scans and not _allowed_scan(stmt):
            out.append({"sql": re.sub(r"\s+", " ", stmt)[:200], "plan": plan})
    return out


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def timeit(fn, runs: int) -> dict:
    ms = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        ms.append((time.perf_counter() - t) * 1000)
    return {"runs": runs, "p50_ms": round(pct(ms, 50), 2), "p95_ms": round(pct(ms, 95), 2), "max_ms": round(max(ms), 2), "mean_ms": round(statistics.mean(ms), 2)}


def isolated_env(home: Path, cfg: Path) -> dict:
    """An environment for a child `jade` process that can only reach the temporary home: nothing of the live ledger, config, settings or agents."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("JADE_")}
    env.update({"JADE_HOME": str(home), "JADE_CONFIG": str(cfg), "JADE_CLAUDE_SETTINGS": str(cfg / "settings.json"), "JADE_LAUNCH_AGENTS": str(cfg / "agents"),
                "PYTHONPATH": str(ROOT)})
    return env


LEGACY_HOOK = ("import sys; sys.path.insert(0, 'evals/performance'); import ledger_load, jade.decide as d; d.sweep = ledger_load.legacy_sweep; "
               "from jade.cli import main; sys.argv = ['jade', 'hook', sys.argv[1]]; sys.exit(main())")


def hook_process(env: dict, name: str, event: dict, legacy: bool = False) -> tuple[float, int]:
    argv = [sys.executable, "-c", LEGACY_HOOK, name] if legacy else [sys.executable, "-m", "jade.cli", "hook", name]
    t = time.perf_counter()
    r = subprocess.run(argv, input=json.dumps(event), capture_output=True, text=True, env=env, cwd=ROOT, timeout=60, check=False)
    return (time.perf_counter() - t) * 1000, r.returncode


def hook_end_to_end(home: Path, cfg: Path, runs: int, legacy: bool = False) -> dict:
    """Wall time of a whole `jade hook <name>` process (interpreter start, imports, policy load, ledger open, the work, exit)."""
    env = isolated_env(home, cfg)
    base = {"session_id": BENCH_SESSION, "cwd": BENCH_CWD}
    cases = {
        "pretool_ungated_read": ("pretool", {**base, "tool_name": "Bash", "tool_input": {"command": "ls -la"}}),
        "pretool_gated_pending_permit": ("pretool", {**base, "tool_name": "Bash", "tool_input": {"command": SYNTHETIC_GATED}}),
        "posttool": ("posttool", {**base, "tool_name": "Bash", "tool_input": {"command": SYNTHETIC_GATED}, "tool_response": {}}),
        "stop": ("stop", {**base, "session_id": "sess-heavy-0001"}),
        "session_start": ("session-start", {**base}),
    }
    out = {}
    for label, (name, event) in cases.items():
        hook_process(env, name, event, legacy)   # warm the bytecode and the file cache once
        runs_ms, codes = [], set()
        for _ in range(runs):
            ms, code = hook_process(env, name, event, legacy)
            runs_ms.append(ms)
            codes.add(code)
        out[label] = {"runs": runs, "p50_ms": round(pct(runs_ms, 50), 1), "p95_ms": round(pct(runs_ms, 95), 1), "max_ms": round(max(runs_ms), 1),
                      "exit_codes": sorted(codes)}
    return out


def legacy_sweep(db) -> dict:
    """decide.sweep as it was before W-58 (lists every decision past its deadline, finished ones too), for the baseline run."""
    from jade.util import ts

    db.all("SELECT decision_id, status FROM decisions WHERE expires_at < ?", (ts(),))   # the benchmark ledger holds no overdue open decision: nothing to act on
    return {"expired": 0, "unknown": 0}


def schema_script(path: Path) -> None:
    """The work `Ledger._init` did on every open before W-58: the schema script and the column check of the migrations."""
    from jade import ledger

    con = sqlite3.connect(path, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(ledger.SCHEMA)
    for table in ledger.MIGRATIONS:
        {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - constant names
    con.close()


def drop_w58_indexes(path: Path) -> list[str]:
    """Baseline: the ledger as it was before W-58 (no new index). The file keeps its user_version, so opening it does not re-create them."""
    from jade import ledger

    names = re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", ledger.INDEXES)
    con = sqlite3.connect(path)
    for n in names:
        con.execute(f"DROP INDEX IF EXISTS {n}")  # noqa: S608 - names come from the constant above
    con.commit()
    con.close()
    return names


def maintain_timings(src: Path, workdir: Path) -> dict:
    """The daily job's steps on a COPY of the ledger. `purge` holds the write lock for its whole transaction; a hook waits at most
    hooks.LEDGER_TIMEOUT_S for it and then fails closed for a high-risk call, so that duration is the number that matters."""
    import shutil

    from jade import decide, hooks, improve, ledger, ops, permits

    out = {}
    cp = workdir / "maintain-copy"
    cp.mkdir()
    shutil.copy(src, cp / "jade.sqlite")
    db = ledger.open(cp / "jade.sqlite")
    db.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def once(label, fn):
        t = time.perf_counter()
        try:
            fn()
            out[label] = {"ms": round((time.perf_counter() - t) * 1000, 1)}
        except Exception as e:  # noqa: BLE001 - the harness reports, never hides
            out[label] = {"error": type(e).__name__}

    once("decide_sweep", lambda: decide.sweep(db))
    once("permits_sweep", lambda: permits.sweep(db))
    real_scrub, db.scrub = db.scrub, lambda: False   # time the transaction apart from VACUUM
    once("purge_first_run_30d_transaction", lambda: ops.purge(db, 30))
    once("purge_second_run_30d_transaction", lambda: ops.purge(db, 30))
    db.scrub = real_scrub
    out["database_bytes_before_vacuum"] = (cp / "jade.sqlite").stat().st_size
    once("scrub_vacuum_and_checkpoint", db.scrub)
    out["database_bytes_after_vacuum"] = (cp / "jade.sqlite").stat().st_size
    once("miner", lambda: improve.mine(db))
    out["hook_lock_wait_s"] = hooks.LEDGER_TIMEOUT_S
    db.close()
    return out


def stats_timing(db_dir: Path, runs: int) -> dict:
    import io

    from jade import cli

    class A:
        days = 30

    def run():
        with contextlib.redirect_stdout(io.StringIO()):
            cli._stats(A())

    return timeit(run, runs)


def measure(rows: int, runs: int, baseline: bool = False) -> dict:
    from jade import decide, ledger

    with tempfile.TemporaryDirectory(prefix="jade-w58-") as td:
        td = Path(td)
        home, cfg = td / "home", td / "config"
        cfg.mkdir()
        os.environ.update({"JADE_HOME": str(home), "JADE_CONFIG": str(cfg), "JADE_CLAUDE_SETTINGS": str(cfg / "settings.json")})
        for k in ("JADE_GATE_MODE", "JADE_SOURCE", "JADE_POLICY_DIR"):
            os.environ.pop(k, None)
        path = home / "jade.sqlite"
        t = time.perf_counter()
        counts = build(path, rows)
        built_s = round(time.perf_counter() - t, 1)
        dropped = drop_w58_indexes(path) if baseline else []
        if baseline:
            decide.sweep = legacy_sweep
        if not baseline:   # an older ledger of this size: the first open after the upgrade creates the indexes
            drop_w58_indexes(path)
            con = sqlite3.connect(path)
            con.execute("PRAGMA user_version=0")
            con.commit()
            con.close()
            t = time.perf_counter()
            ledger.open(path).close()
            upgrade_ms = round((time.perf_counter() - t) * 1000, 1)
        db = ledger.open(path)
        report = {"first_open_after_upgrade_ms": None if baseline else upgrade_ms, "rows": counts, "build_s": built_s, "database_bytes": path.stat().st_size, "baseline_without_w58": baseline, "dropped_indexes": dropped}
        db_ops = operations(db)
        db_ops["pretool_gated"]()   # first call loads and compiles the policy: a hook process pays that once, a long-lived benchmark process only here
        tracer = Tracer()
        with tracing(tracer, db):
            report["in_process"] = {name: timeit(fn, runs) for name, fn in db_ops.items()}
            report["in_process"]["jade_ledger_stats"] = stats_timing(home, runs)
        report["ledger_open"] = {"user_version_current": timeit(lambda: ledger.open(path).close(), runs),
                                 "schema_script_and_migration_check_only": timeit(lambda: schema_script(path), runs)}   # what every hook process did before W-58
        db.close()
        report["full_scans"] = full_scans(sqlite3.connect(path), tracer.sql)
        report["statements_checked"] = len(tracer.sql)
        report["hook_process"] = hook_end_to_end(home, cfg, runs, legacy=baseline)
        db = ledger.open(path)
        db.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        report["maintain"] = maintain_timings(path, td)
        return report


def empty_ledger_hooks(runs: int) -> dict:
    """The same hook processes against a ledger with no rows, for the baseline the 100k numbers are read against."""
    with tempfile.TemporaryDirectory(prefix="jade-w58-empty-") as td:
        td = Path(td)
        home, cfg = td / "home", td / "config"
        cfg.mkdir()
        os.environ.update({"JADE_HOME": str(home), "JADE_CONFIG": str(cfg), "JADE_CLAUDE_SETTINGS": str(cfg / "settings.json")})
        from jade import ledger

        ledger.open(home / "jade.sqlite").close()
        return hook_end_to_end(home, cfg, runs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--baseline", action="store_true", help="drop the W-58 indexes and use the old sweep query first, to measure what the change fixed")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    result = {"date": "2026-11-09", "work_item": "W-58", "synthetic": True, "python": platform.python_version(), "platform": platform.platform(terse=True),
              "sqlite": sqlite3.sqlite_version, "note": "all rows are synthetic; measured in a temporary JADE_HOME, never the live ledger",
              "after": measure(a.rows, a.runs)}
    result["empty_ledger_hook_process"] = empty_ledger_hooks(a.runs)
    if a.baseline:
        result["before"] = measure(a.rows, a.runs, baseline=True)
    text = json.dumps(result, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
