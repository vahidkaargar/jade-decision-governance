"""SQLite ledger (ADR-08): append-only events plus current-state rows. One writer at a time via
BEGIN IMMEDIATE. The Jev call row is written before the provider is contacted (write-ahead, ADR-04)."""

from __future__ import annotations

import json
import os
import random
import sqlite3
import time
import zlib
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from . import paths, redact
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
CREATE TABLE IF NOT EXISTS asks(
  ask_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, agent_id TEXT, decision_id TEXT, plan_hash TEXT NOT NULL, hold_key TEXT NOT NULL,
  fingerprints_json TEXT NOT NULL, rule_id TEXT NOT NULL, level TEXT NOT NULL, tool TEXT NOT NULL, question_json TEXT NOT NULL, status TEXT NOT NULL,
  created_ts TEXT NOT NULL, answered_ts TEXT, expires_ts TEXT NOT NULL, source TEXT);
CREATE TABLE IF NOT EXISTS human_answers(
  answer_id TEXT PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, decision_id TEXT, rule_id TEXT NOT NULL, class_key TEXT, exact_key TEXT NOT NULL,
  answer TEXT NOT NULL, channel TEXT NOT NULL, strength TEXT NOT NULL DEFAULT 'hint', confirmed_ts TEXT, source TEXT);
"""
# Indexes created after the migrations: some name a column added after the first release (hold_key, source), which an older file lacks until `_migrate`
# has run. Each serves a query that every gated call or every Stop hook runs, measured at 100k rows in evals/performance/ledger_load.py (W-58); the
# test tests/acceptance/test_ledger_scale_w58.py fails when one of those queries scans a whole table again.
INDEXES = """
CREATE INDEX IF NOT EXISTS decisions_open ON decisions(status, expires_at);
CREATE INDEX IF NOT EXISTS decisions_plan ON decisions(plan_hash, status);
CREATE INDEX IF NOT EXISTS decisions_hold ON decisions(hold_key, status);
CREATE INDEX IF NOT EXISTS permits_open ON permits(state, expires_at);
CREATE INDEX IF NOT EXISTS permits_decision ON permits(decision_id);
CREATE INDEX IF NOT EXISTS outcomes_decision ON outcomes(decision_id);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events(kind, ts);
CREATE INDEX IF NOT EXISTS calls_claim ON jev_calls(session_id, claim_hash);
CREATE INDEX IF NOT EXISTS decisions_test ON decisions(source) WHERE source='test';
CREATE INDEX IF NOT EXISTS permits_test ON permits(source) WHERE source='test';
CREATE INDEX IF NOT EXISTS calls_evaluating ON jev_calls(ts) WHERE state='EVALUATING';
CREATE INDEX IF NOT EXISTS human_answers_exact ON human_answers(exact_key, strength);
CREATE INDEX IF NOT EXISTS human_answers_ts ON human_answers(ts);
CREATE INDEX IF NOT EXISTS asks_call ON asks(session_id, hold_key, status);
"""
MIGRATIONS = {"jev_calls": [("language", "TEXT"), ("source", "TEXT")], "decisions": [("hold_key", "TEXT"), ("source", "TEXT")],
              "permits": [("source", "TEXT")], "events": [("source", "TEXT")]}   # columns added after the first release: (name, type)
# PRAGMA user_version of a file once SCHEMA, the migrations and INDEXES have been applied to it; a file already at this value skips all three (W-58). It is a
# digest of those three, so changing any of them changes the version and every file is brought up to date once: nobody has to remember to bump a number.
SCHEMA_VERSION = zlib.crc32(json.dumps([SCHEMA, INDEXES, MIGRATIONS], sort_keys=True).encode()) & 0x7FFFFFFF
LIVE = "COALESCE({a}source,'')<>'test'"   # rows a verification run wrote under JADE_SOURCE=test are not live data (W-72)


def live(alias: str = "") -> str:
    """SQL condition that leaves rows tagged `test` out. `alias` is a table alias such as `c` (no dot)."""
    return LIVE.format(a=f"{alias}." if alias else "")


def dedup_ttl_s() -> int:
    """How long an answered call anchors a repeat: `dedup.ttl_s` of policies/routing.yaml (3600 as shipped)."""
    from . import policy

    return int(policy.routing()["dedup"]["ttl_s"])


def __getattr__(name: str):
    if name == "DEDUP_TTL_S":   # the old constant, kept as a name for callers; the value is the policy's
        return dedup_ttl_s()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


STALE_EVALUATING_S = 600   # a provider call ends within 20 s; a row older than this belongs to a process that died
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
        self._depth = 0
        self.source = paths.row_source()   # stamped on every row this connection writes (W-72)
        self._create_private()
        self.con = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self._init()
        self._lock_down()

    def _create_private(self) -> None:
        """Create the database file mode 600 before SQLite opens it. SQLite gives `-wal` and `-shm` the mode of the main file, so no sidecar
        is ever group- or world-readable, whatever the umask (docs/05 T15)."""
        try:
            os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600))
        except OSError:
            pass

    def _lock_down(self) -> None:
        """Mode 600 on the database and on the sidecars that exist now (an older file, or one made by another tool, is tightened too)."""
        for suffix in ("", "-wal", "-shm"):
            try:
                os.chmod(f"{self.path}{suffix}", 0o600)
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
                self.con.execute("PRAGMA secure_delete=ON")   # a deleted or overwritten value is zeroed in its page, not left in a free block
                if self.con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:   # every hook process opens the ledger: do the work once per file
                    self.con.executescript(SCHEMA)
                    self._migrate()
                    self.con.executescript(INDEXES)
                    self.con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) and "busy" not in str(e) or time.monotonic() > deadline:
                    raise
                time.sleep(0.02 + random.random() * 0.05)  # noqa: S311 - backoff jitter

    def scrub(self) -> bool:
        """Rewrite the database file and empty the write-ahead log, so that text a purge cleared is not left in free pages or in old log frames.
        `secure_delete` zeroes what a later write removes; `VACUUM` also rebuilds the pages freed before it was on (an older ledger), and a
        `TRUNCATE` checkpoint folds the log into the file and cuts it to zero. Best effort: False when another process holds the database."""
        if self._depth:
            return False
        try:
            self.con.execute("VACUUM")
            self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            return False
        return True

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
        """One immediate write transaction; rolls back on any exception. Re-entrant: a `tx()` opened inside another one is a savepoint, so a
        multi-step flow (consume a permit and advance its decision) commits or rolls back as a whole while each step stays usable alone."""
        if self._depth:
            name = f"sp{self._depth}"
            self.con.execute(f"SAVEPOINT {name}")  # noqa: S608 - constant name
            self._depth += 1
            try:
                yield self.con
            except BaseException:
                self.con.execute(f"ROLLBACK TO {name}")
                self.con.execute(f"RELEASE {name}")
                raise
            else:
                self.con.execute(f"RELEASE {name}")
            finally:
                self._depth -= 1
            return
        self.con.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self.con
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        else:
            self.con.execute("COMMIT")
        finally:
            self._depth = 0

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
            self._sweep_stale(con)
            if session:
                row = self._anchor(con, session, question_id, version, state_hash, claim_hash)
                dedup_of = row["call_id"] if row else None
            for col in ("state_text", "question_text"):  # nothing stored in the ledger carries a credential, whoever the caller is
                if isinstance(cols.get(col), str):
                    cols[col] = redact.redact(cols[col], secrets_only=True)[0]
            if self.source:
                cols.setdefault("source", self.source)
            data = {"call_id": call_id, "question_id": question_id, "question_version": version, "state_hash": state_hash,
                    "session_id": session, "claim_hash": claim_hash, "dedup_of": dedup_of, "state": "EVALUATING", "ts": ts(), **cols}
            if data.get("state_text"):
                data["state_text"] = data["state_text"][:STATE_CAP]
            keys = ",".join(data)
            con.execute(f"INSERT INTO jev_calls({keys}) VALUES({','.join('?' * len(data))})", tuple(data.values()))  # noqa: S608 - keys are code-defined
        return call_id

    def _anchor(self, con, session: str, question_id: str, version: int, state_hash: str, claim_hash: str | None):
        """The answered call a repeat of this question points at, or None. The anchor is an answered call from the same kind of run (a `JADE_SOURCE=test`
        row never anchors a live one, and a purge of test rows must not leave a live row pointing at a deleted anchor): DONE with an answer (exit 0, 3 or 4).
        An errored, rejected or still EVALUATING original is no anchor, so a later success becomes the new original and stays visible to calibration."""
        ttl = dedup_ttl_s()
        for row in con.execute(
                "SELECT call_id, ts FROM jev_calls WHERE session_id=? AND question_id=? AND question_version=? "
                "AND state_hash=? AND COALESCE(claim_hash,'')=COALESCE(?, '') AND dedup_of IS NULL "
                "AND state='DONE' AND exit_code IN (0,3,4) AND COALESCE(source,'')=COALESCE(?, '') ORDER BY rowid DESC LIMIT 1",
                (session, question_id, version, state_hash, claim_hash, self.source)):
            if (now() - parse_ts(row["ts"])).total_seconds() <= ttl:
                return row
        return None

    def find_repeat(self, question_id: str, version: int, state_hash: str, claim_hash: str | None, session: str):
        """Read-only form of the lookup `begin_call` does: the answered call that a new ask of this question on this state would repeat (PF-04)."""
        row = self._anchor(self.con, session, question_id, version, state_hash, claim_hash)
        return self.get_call(row["call_id"]) if row else None

    def in_flight_batches(self, within_s: float, exclude: tuple[str, ...] = ()) -> int:
        """Provider requests still waiting for an answer, over every process: the distinct (session, state) pairs of calls left EVALUATING in the
        last `within_s` seconds (the questions of one batch share both). Older rows belong to a process that died, not to a live exchange."""
        sql = ("SELECT COUNT(DISTINCT COALESCE(session_id,'') || '|' || state_hash) AS n FROM jev_calls "
               "WHERE state='EVALUATING' AND ts>=? AND COALESCE(source,'')=COALESCE(?, '')")
        args: list = [ts(now() - timedelta(seconds=within_s)), self.source]
        if exclude:
            sql += f" AND call_id NOT IN ({','.join('?' * len(exclude))})"  # noqa: S608 - placeholders only
            args += list(exclude)
        return int(self.one(sql, tuple(args)).n)

    def _sweep_stale(self, con) -> None:
        """A row left EVALUATING by a crashed process is closed as an error, so it is neither in flight for ever nor an anchor."""
        cutoff = ts(now() - timedelta(seconds=STALE_EVALUATING_S))
        con.execute("UPDATE jev_calls SET state='DONE', error_kind='STALE_EVALUATING', exit_code=5, done_ts=? WHERE state='EVALUATING' AND ts<?", (ts(), cutoff))

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
        sql = ("INSERT INTO events(entity_id, kind, prev_state, next_state, guard, actor, idempotency_key, detail_json, ts, source) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)")
        guard = redact.redact(guard, secrets_only=True)[0] if isinstance(guard, str) else guard
        detail = redact.redact_obj(detail, secrets_only=True)[0] if detail is not None else None
        args = (entity_id, kind, prev, next, guard, actor, key, json.dumps(detail, ensure_ascii=False) if detail is not None else None, ts(), self.source)
        try:
            (con or self.con).execute(sql, args)
        except sqlite3.IntegrityError:
            return False
        return True

    HUMAN_ANSWERS = ("allow", "deny")
    ANSWER_CHANNELS = ("chat", "dialog", "terminal")

    def record_human_answer(self, *, session: str | None, decision_id: str | None, rule_id: str, class_key: str | None, exact_key: str, answer: str,
                            channel: str) -> str:
        """Store what the human answered to an ask, as a `hint` (docs/12): it decides nothing and goes to no model until `jade review confirm` (0.6.0).
        `exact_key` identifies the call, `class_key` the kind of call. Returns the answer id."""
        if answer not in self.HUMAN_ANSWERS or channel not in self.ANSWER_CHANNELS or not exact_key or not rule_id:
            raise ValueError(f"a human answer needs answer in {self.HUMAN_ANSWERS}, channel in {self.ANSWER_CHANNELS}, an exact key and a rule id")
        aid = new_id("ans")
        ck = redact.redact(class_key, secrets_only=True)[0] if class_key else None
        self.con.execute("INSERT INTO human_answers(answer_id, ts, session_id, decision_id, rule_id, class_key, exact_key, answer, channel, strength, source) "
                         "VALUES(?,?,?,?,?,?,?,?,?,'hint',?)", (aid, ts(), session, decision_id, rule_id, ck, exact_key, answer, channel, self.source))
        self.event(aid, "human_answer", guard=f"{rule_id} {answer} via {channel}", actor="human", detail={"decision_id": decision_id, "rule_id": rule_id})
        return aid

    def human_answers(self, strength: str | None = None, since_s: float | None = None, limit: int = 500):
        """The recorded answers, newest first; live rows only (a verification run's rows are left out, W-72)."""
        sql, args = "SELECT * FROM human_answers WHERE " + LIVE.format(a=""), []
        if strength:
            sql += " AND strength=?"
            args.append(strength)
        if since_s is not None:
            sql += " AND ts>=?"
            args.append(ts(now() - timedelta(seconds=since_s)))
        return self.con.execute(sql + " ORDER BY ts DESC, rowid DESC LIMIT ?", (*args, int(limit))).fetchall()

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
