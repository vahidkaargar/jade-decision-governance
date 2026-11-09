"""SQLite ledger (ADR-08): append-only events plus current-state rows. One writer at a time via
BEGIN IMMEDIATE. The Jev call row is written before the provider is contacted (write-ahead, ADR-04)."""

from __future__ import annotations

import json
import os
import random
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from . import paths
from .util import new_id, now, parse_ts, ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS jev_calls(
  call_id TEXT PRIMARY KEY, decision_id TEXT, question_id TEXT NOT NULL, question_version INTEGER NOT NULL DEFAULT 1,
  primitive TEXT, task_family TEXT, claim_hash TEXT, request_model TEXT, resolved_model TEXT, provider TEXT,
  provider_request_id TEXT, state_hash TEXT NOT NULL, state_bytes INTEGER, state_text TEXT, question_text TEXT,
  answers_json TEXT, p REAL, verdict TEXT, latency_ms REAL, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
  error_kind TEXT, exit_code INTEGER, session_id TEXT, dedup_of TEXT, state TEXT NOT NULL, risk TEXT, redactions INTEGER,
  disposition TEXT, disposition_note TEXT, language TEXT, reminded INTEGER NOT NULL DEFAULT 0, batch_id TEXT, ts TEXT NOT NULL, done_ts TEXT);
CREATE INDEX IF NOT EXISTS calls_session ON jev_calls(session_id, question_id, state_hash);
CREATE INDEX IF NOT EXISTS calls_ts ON jev_calls(ts);
CREATE TABLE IF NOT EXISTS decisions(
  decision_id TEXT PRIMARY KEY, status TEXT NOT NULL, prev_status TEXT, risk_level TEXT, rule_id TEXT, route TEXT,
  session_id TEXT, plan_hash TEXT, revision_count INTEGER NOT NULL DEFAULT 0, doc_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL, kind TEXT NOT NULL, prev_state TEXT, next_state TEXT,
  guard TEXT, actor TEXT, idempotency_key TEXT UNIQUE, detail_json TEXT, ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_entity ON events(entity_id);
CREATE TABLE IF NOT EXISTS permits(
  permit_id TEXT PRIMARY KEY, decision_id TEXT, state TEXT NOT NULL, risk_level TEXT NOT NULL, plan_hash TEXT NOT NULL,
  action TEXT, scope TEXT, uses INTEGER NOT NULL DEFAULT 0, ttl_s INTEGER NOT NULL, doc_json TEXT NOT NULL,
  issued_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS permits_plan ON permits(plan_hash, state);
CREATE TABLE IF NOT EXISTS outcomes(outcome_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, doc_json TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS labels(call_id TEXT PRIMARY KEY, label INTEGER NOT NULL, source TEXT NOT NULL, note TEXT, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_items(id TEXT PRIMARY KEY, status TEXT, doc_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS improvements(candidate_id TEXT PRIMARY KEY, decision TEXT, doc_json TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
MIGRATIONS = {"jev_calls": [("language", "TEXT")]}   # columns added after the first release: (name, type)
DEDUP_TTL_S = 3600
STATE_CAP = 32 * 1024
DEFAULT_TIMEOUT_S = 10.0


def _ns(row: sqlite3.Row | None):
    return None if row is None else SimpleNamespace(**dict(row))


class Ledger:
    def __init__(self, path: Path, timeout: float = DEFAULT_TIMEOUT_S):
        """`timeout` bounds every wait for a lock: the busy timeout and the schema-step retry. The PreToolUse hook passes a
        short one so that it answers before the host kills it (hooks.LEDGER_TIMEOUT_S)."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = float(timeout)
        self.con = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self._init()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _init(self) -> None:
        """WAL switch and schema creation ignore the busy timeout when several processes open a fresh file at
        once, so retry them briefly instead of failing the caller."""
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.con.execute("PRAGMA journal_mode=WAL")
                self.con.execute("PRAGMA synchronous=NORMAL")
                self.con.executescript(SCHEMA)
                self._migrate()
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) and "busy" not in str(e) or time.monotonic() > deadline:
                    raise
                time.sleep(0.02 + random.random() * 0.05)  # noqa: S311 - backoff jitter

    def _migrate(self) -> None:
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in self.con.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - constant table names
            for name, typ in cols:
                if name not in have:
                    try:
                        self.con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")  # noqa: S608
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e):  # another process migrated first
                            raise

    @contextmanager
    def tx(self):
        """One immediate write transaction; rolls back on any exception."""
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield self.con
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        else:
            self.con.execute("COMMIT")

    def one(self, sql: str, args=()):
        return _ns(self.con.execute(sql, args).fetchone())

    def all(self, sql: str, args=()):
        return [_ns(r) for r in self.con.execute(sql, args).fetchall()]

    # ---- Jev calls -------------------------------------------------------------------------
    def begin_call(self, question_id: str, state_hash: str, session: str | None = None, *, version: int = 1,
                   claim_hash: str | None = None, **cols) -> str:
        """Write-ahead row in state EVALUATING. Marks a duplicate of an earlier call in the same session."""
        call_id = new_id("call")
        with self.tx() as con:
            dedup_of = None
            if session:
                for row in con.execute(
                        "SELECT call_id, ts FROM jev_calls WHERE session_id=? AND question_id=? AND question_version=? "
                        "AND state_hash=? AND COALESCE(claim_hash,'')=COALESCE(?, '') AND dedup_of IS NULL ORDER BY rowid DESC LIMIT 1",
                        (session, question_id, version, state_hash, claim_hash)):
                    if (now() - parse_ts(row["ts"])).total_seconds() <= DEDUP_TTL_S:
                        dedup_of = row["call_id"]
            data = {"call_id": call_id, "question_id": question_id, "question_version": version, "state_hash": state_hash,
                    "session_id": session, "claim_hash": claim_hash, "dedup_of": dedup_of, "state": "EVALUATING", "ts": ts(), **cols}
            if data.get("state_text"):
                data["state_text"] = data["state_text"][:STATE_CAP]
            keys = ",".join(data)
            con.execute(f"INSERT INTO jev_calls({keys}) VALUES({','.join('?' * len(data))})", tuple(data.values()))  # noqa: S608 - keys are code-defined
        return call_id

    def finish_call(self, call_id: str, **cols) -> None:
        cols.setdefault("state", "DONE")
        cols.setdefault("done_ts", ts())
        if isinstance(cols.get("answers_json"), dict):
            cols["answers_json"] = json.dumps(cols["answers_json"], ensure_ascii=False)
        sets = ",".join(f"{k}=?" for k in cols)
        with self.tx() as con:
            con.execute(f"UPDATE jev_calls SET {sets} WHERE call_id=?", (*cols.values(), call_id))  # noqa: S608 - keys are code-defined

    def get_call(self, call_id: str):
        return self.one("SELECT * FROM jev_calls WHERE call_id=?", (call_id,))

    # ---- events and meta ---------------------------------------------------------------------
    def event(self, entity_id: str, kind: str, *, prev=None, next=None, guard=None, actor="runtime", key=None, detail=None, con=None) -> bool:  # noqa: A002
        """Append an event. Returns False when the idempotency key was already applied."""
        sql = ("INSERT INTO events(entity_id, kind, prev_state, next_state, guard, actor, idempotency_key, detail_json, ts) "
               "VALUES(?,?,?,?,?,?,?,?,?)")
        args = (entity_id, kind, prev, next, guard, actor, key, json.dumps(detail, ensure_ascii=False) if detail is not None else None, ts())
        try:
            (con or self.con).execute(sql, args)
        except sqlite3.IntegrityError:
            return False
        return True

    def meta_get(self, k: str, default=None):
        row = self.con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    def meta_set(self, k: str, v: str) -> None:
        self.con.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def close(self) -> None:
        self.con.close()


def open(path: Path | str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> Ledger:  # noqa: A001 - `ledger.open()` is the public name
    """Open the ledger. `path` may be a directory (uses <dir>/jade.sqlite) or a file; default is the JADE home."""
    p = Path(path) if path else paths.home()
    if p.is_dir() or p.suffix == "":
        p.mkdir(parents=True, exist_ok=True)
        p = p / "jade.sqlite"
    return Ledger(p, timeout)
