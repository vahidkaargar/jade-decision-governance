-- Ledger DDL of release 0.2.2 (commit 6ee8641, jade/ledger.py SCHEMA): the `hold_key` column of `decisions` is not here, it is added by ledger.MIGRATIONS.
-- Snapshot kept so that a ledger made by 0.2.2 can be opened by later code (tests/test_ledger_migration.py). Do not edit.
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
