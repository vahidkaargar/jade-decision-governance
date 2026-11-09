"""REL-3: a ledger made by release 0.2.2 is opened by the current code without loss.

`tests/fixtures/ledger_0_2_2.sql` is the DDL of commit 6ee8641 (read from jade/ledger.py there). The rows are synthetic: ids, hashes and text are
placeholders, never taken from a real ledger."""
import json
import sqlite3
from pathlib import Path

import pytest

from jade import ledger, ops, paths

FIXTURE = Path(__file__).parent / "fixtures" / "ledger_0_2_2.sql"
ROOT = Path(__file__).resolve().parents[1]
TABLES = ("meta", "jev_calls", "decisions", "events", "permits", "outcomes", "labels", "work_items", "improvements")
T0 = "2026-11-08T21:00:00Z"


def _example(name: str) -> dict:
    return json.loads((ROOT / "schemas" / "examples" / f"{name}.json").read_text())


def build(path: Path, ddl: str | None = None) -> dict[str, int]:
    """Create a 0.2.2 ledger at `path` and fill every table; returns the row count of each."""
    con = sqlite3.connect(path)
    con.executescript(ddl if ddl is not None else FIXTURE.read_text())
    permit, improvement = _example("permit"), _example("improvement")
    con.executemany("INSERT INTO meta(k,v) VALUES(?,?)", [("last_check", T0), ("last_maintain", T0)])
    con.execute("INSERT INTO jev_calls(call_id,question_id,state_hash,state,ts,primitive,p,verdict,session_id) VALUES('c1','q.test@1','h1','DONE',?,'verify',0.91,'accept','s1')", (T0,))
    con.execute("INSERT INTO jev_calls(call_id,question_id,state_hash,state,ts) VALUES('c2','q.test@1','h2','FAILED',?)", (T0,))
    con.execute("INSERT INTO decisions(decision_id,status,risk_level,rule_id,plan_hash,doc_json,created_at,updated_at,expires_at) VALUES('dec_synthetic01','PENDING','high','r.synthetic','ph1','{}',?,?,?)", (T0, T0, T0))
    con.execute("INSERT INTO events(entity_id,kind,idempotency_key,ts) VALUES('dec_synthetic01','gated','k1',?)", (T0,))
    con.execute("INSERT INTO events(entity_id,kind,idempotency_key,ts) VALUES('dec_synthetic01','safety_block','k2',?)", (T0,))
    con.execute("INSERT INTO permits(permit_id,decision_id,state,risk_level,plan_hash,ttl_s,doc_json,issued_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (permit["permit_id"], permit["decision_id"], "ISSUED", "high", permit["plan_hash"], 1800, json.dumps(permit), T0, T0))
    con.execute("INSERT INTO outcomes(outcome_id,decision_id,doc_json,ts) VALUES('out_1','dec_synthetic01','{}',?)", (T0,))
    con.execute("INSERT INTO labels(call_id,label,source,ts) VALUES('c1',1,'human',?)", (T0,))
    con.execute("INSERT INTO work_items(id,status,doc_json,updated_at) VALUES('W-1','open','{}',?)", (T0,))
    con.execute("INSERT INTO improvements(candidate_id,decision,doc_json,updated_at) VALUES(?,?,?,?)",
                (improvement["candidate_id"], "pending", json.dumps(improvement), T0))
    con.commit()
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}  # noqa: S608 - constant names
    con.close()
    return counts


def dump(path: Path) -> dict[str, list]:
    """Every row of every table, by column name, so that a column added by a migration does not count as a change to an old row."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    out = {t: [dict(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in TABLES}  # noqa: S608
    con.close()
    return out


def columns(path: Path, table: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def test_the_fixture_is_the_release_ddl_and_lacks_the_later_columns():
    ddl = "\n".join(line for line in FIXTURE.read_text().splitlines() if not line.startswith("--"))
    assert "hold_key" not in ddl
    assert [t for t in TABLES if f"CREATE TABLE IF NOT EXISTS {t}(" not in ddl] == []


@pytest.fixture
def old(tmp_path):
    p = tmp_path / "home" / "jade.sqlite"
    p.parent.mkdir()
    return p, build(p)


def test_the_current_code_opens_a_0_2_2_ledger_and_keeps_every_row(old):
    path, counts = old
    before = dump(path)
    assert "hold_key" not in columns(path, "decisions")
    db = ledger.open(path)
    db.close()
    assert "hold_key" in columns(path, "decisions")
    after = dump(path)
    for t in TABLES:
        assert len(after[t]) == counts[t], t
        old_cols = before[t][0].keys() if before[t] else set()
        assert [{k: v for k, v in r.items() if k in old_cols} for r in after[t]] == before[t], t
    assert [r["hold_key"] for r in after["decisions"]] == [None]


def test_a_ledger_older_than_the_language_column_is_migrated_too(tmp_path):
    path = tmp_path / "jade.sqlite"
    ddl = FIXTURE.read_text().replace("disposition_note TEXT, language TEXT, reminded", "disposition_note TEXT, reminded")
    assert "language" not in ddl
    counts = build(path, ddl)
    assert "language" not in columns(path, "jev_calls")
    ledger.open(path).close()
    assert "language" in columns(path, "jev_calls")
    assert "hold_key" in columns(path, "decisions")
    assert {t: len(rows) for t, rows in dump(path).items()} == counts


def test_opening_again_and_again_changes_nothing(old):
    path, counts = old
    ledger.open(path).close()
    first = dump(path)
    for _ in range(3):
        db = ledger.open(path)
        db._migrate()   # a second process that migrates after the first one: every step is a no-op
        db.close()
    assert dump(path) == first
    assert {t: len(rows) for t, rows in first.items()} == counts


def test_the_ledger_stays_usable_after_the_migration(old):
    path, _ = old
    db = ledger.open(path)
    try:
        db.meta_set("k", "v")
        assert db.meta_get("k") == "v"
        assert db.one("SELECT COUNT(*) AS n FROM jev_calls").n == 2
        db.con.execute("UPDATE decisions SET hold_key='x' WHERE decision_id='dec_synthetic01'")
        assert db.one("SELECT hold_key FROM decisions").hold_key == "x"
    finally:
        db.close()


def test_jade_check_ledger_rows_are_ok_on_a_0_2_2_ledger(tmp_path):
    build(paths.home() / "jade.sqlite")
    rows = {n: (ok, d) for n, ok, d in ops.check(live=False)}
    for name in ("ledger writable", "ledger integrity"):
        assert rows[name][0], rows[name]
    assert "2 calls" in rows["ledger writable"][1]
    assert "1 permit row(s) and 1 improvement row(s)" in rows["ledger integrity"][1]
