"""Review cluster C: redaction and egress (F14, F17-F23).

Every secret-shaped test value is assembled from parts so that the repository holds no literal token-shaped string."""
import json
import os
import stat

import pytest

from jade import adapter, cli, decide, hooks, ledger, ops, permits, redact
from jade.permits import PermitError

TOKEN = "Zq8vK2mN5p" + "Q8sT1vW4xZ7aB3cD"          # the --token= value of the F18 repro
FAKE = "Zq8fakeSecret" + "Value12345"               # the F14 repro value
GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
PW = "Hunter2" + "Hunter2"


@pytest.fixture
def human_env(monkeypatch):
    from jade.util import AGENT_ENV

    for k in AGENT_ENV:
        monkeypatch.delenv(k, raising=False)


def call(cmd, cwd="/tmp/x", session="sess-cccccccc"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": cwd}


def everything(db) -> str:
    """Every row of every table, as text (the ledger's whole stored content)."""
    return "\n".join(db.con.iterdump())


# ---- F14 / F18: gated commands, permit text and decision payloads are redacted before they are stored -------------
@pytest.mark.parametrize("cmd", [
    f"kubectl delete ns prod --token={TOKEN}",
    f"DB_PASSWORD={FAKE} kubectl delete pod web-1 --password={FAKE}",
])
def test_f14_f18_a_gated_command_stores_no_secret(cmd):
    res = hooks.pretool(call(cmd))
    assert res.exit_code == 2
    db = ledger.open()
    dump = everything(db)
    assert TOKEN not in dump and FAKE not in dump
    row = db.one("SELECT doc_json FROM decisions")
    doc = json.loads(row.doc_json)
    assert "[REDACTED:" in json.dumps(doc["candidate_actions"]) and "[REDACTED:" in doc["objective"]
    ctx = permits.ctx_of(permits.listing(db, ("PENDING",))[0])
    assert ctx["redacted"] >= 1 and "[REDACTED:" in ctx["subject"]


def test_f18_a_permit_for_a_redacted_command_cannot_be_approved_and_says_why(human_env):
    hooks.pretool(call(f"kubectl delete ns prod --token={TOKEN}"))
    db = ledger.open()
    p = permits.listing(db, ("PENDING",))[0]
    with pytest.raises(PermitError, match="secret"):
        permits.review_text(p)
    with pytest.raises(PermitError, match="secret"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    assert permits.get(db, p.permit_id).state == "PENDING"


def test_f18_the_block_message_tells_the_agent_to_remove_the_inline_secret():
    res = hooks.pretool(call(f"kubectl delete ns prod --token={TOKEN}"))
    assert TOKEN not in res.stderr and "secret" in res.stderr.lower()


def test_f18_a_command_without_a_secret_is_unchanged_and_approvable(human_env):
    cmd = "git push origin v1-feat-x"
    hooks.pretool(call(cmd))
    db = ledger.open()
    p = permits.listing(db, ("PENDING",))[0]
    assert cmd in permits.review_text(p) and "redacted" not in permits.ctx_of(p)
    assert permits.approve(db, p.permit_id, stdin_isatty=True).state == "ISSUED"
    assert hooks.pretool(call(cmd)).exit_code == 0


def test_f18_an_email_in_a_gated_command_stays_approvable():
    cmd = "git push origin v1-feat-x --push-option=notify=someone@example.org"
    hooks.pretool(call(cmd))
    db = ledger.open()
    p = permits.listing(db, ("PENDING",))[0]
    assert "someone@example.org" in permits.review_text(p)


def test_f14_the_ledger_redacts_event_text_and_fast_args_at_the_choke_point():
    db = ledger.open()
    db.event("e1", "x", guard=f"reason token={TOKEN}", detail={"cmd": f"password={PW}"})
    cid = db.begin_call("fast.x", "0" * 64, "s", question_text=f"curl -H 'Authorization: Bearer {TOKEN}'", state_text=f"key {GHP}")
    dump = everything(db)
    assert TOKEN not in dump and PW not in dump and GHP not in dump
    assert db.get_call(cid).question_text.startswith("curl")


def test_f14_a_decision_opened_with_a_secret_stores_it_redacted():
    db = ledger.open()
    doc = decide.open_decision(db, f"deploy with token={TOKEN} now", command=f"deploy --token={TOKEN}", evidence_refs=[f"log {GHP}"],
                               constraints=[f"password={PW}"])
    assert TOKEN not in everything(db) and GHP not in everything(db) and PW not in everything(db)
    assert doc["objective"].startswith("deploy with")


def test_f14_purge_clears_decision_permit_and_event_text_after_the_window_and_keeps_metrics():
    cmd = "git push origin v1-feat-private-branch-name"
    hooks.pretool(call(cmd))
    db = ledger.open()
    did = db.one("SELECT decision_id FROM decisions").decision_id
    old = "2000-01-01T00:00:00Z"
    db.con.execute("UPDATE decisions SET created_at=?, updated_at=? WHERE decision_id=?", (old, old, did))
    db.con.execute("UPDATE permits SET issued_at=?, expires_at=?", (old, old))
    db.con.execute("UPDATE events SET ts=?", (old,))
    hooks.pretool(call("git push origin v1-feat-fresh-branch"))   # a fresh decision keeps its text
    n = ops.purge(db, 90)
    assert n >= 3
    dump_old = db.one("SELECT doc_json FROM decisions WHERE decision_id=?", (did,)).doc_json
    assert "feat-private-branch-name" not in dump_old
    row = db.one("SELECT status, risk_level, rule_id, plan_hash FROM decisions WHERE decision_id=?", (did,))
    assert row.status and row.risk_level and row.rule_id and row.plan_hash
    p = db.one("SELECT scope, plan_hash, risk_level, state FROM permits WHERE decision_id=?", (did,))
    assert "feat-private-branch-name" not in p.scope and p.plan_hash and p.risk_level
    assert not any("feat-private-branch-name" in (r.detail_json or "") for r in db.all("SELECT detail_json FROM events"))
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE kind='gated'").n >= 1          # events and their metrics stay
    fresh = db.one("SELECT doc_json FROM decisions WHERE decision_id<>?", (did,)).doc_json
    assert "feat-fresh-branch" in fresh
    assert ops.purge(db, 90) == 0                                                         # nothing left to clear: idempotent


def test_f14_purge_leaves_an_open_permit_and_its_decision_alone():
    hooks.pretool(call("git push origin v1-feat-x"))
    db = ledger.open()
    old = "2000-01-01T00:00:00Z"
    db.con.execute("UPDATE permits SET issued_at=?", (old,))
    db.con.execute("UPDATE decisions SET created_at=?, updated_at=?", (old, old))
    ops.purge(db, 90)
    assert "git push origin v1-feat-x" in permits.review_text(permits.listing(db, ("PENDING",))[0])


# ---- F17: sidecars and the database are created 0600 -------------------------------------------------------------
def test_f17_database_and_wal_shm_are_0600_under_a_permissive_umask(tmp_path):
    old = os.umask(0o022)
    try:
        d = tmp_path / "outside-home"
        db = ledger.open(d / "jade.sqlite")
        db.begin_call("q", "h" * 64, "s", state_text="x", question_text="y")
        modes = {f.name: stat.S_IMODE(f.stat().st_mode) for f in d.iterdir()}
        db.close()
    finally:
        os.umask(old)
    assert any(n.endswith("-wal") for n in modes) and any(n.endswith("-shm") for n in modes)
    assert all(m == 0o600 for m in modes.values()), modes


def test_f17_an_existing_world_readable_file_and_sidecars_are_tightened(tmp_path):
    d = tmp_path / "old"
    db = ledger.open(d / "jade.sqlite")
    db.begin_call("q", "h" * 64, "s", state_text="x")
    for f in d.iterdir():
        f.chmod(0o644)
    db2 = ledger.open(d / "jade.sqlite")
    modes = {f.name: stat.S_IMODE(f.stat().st_mode) for f in d.iterdir()}
    db.close(), db2.close()
    assert all(m == 0o600 for m in modes.values()), modes


# ---- F19: vendor token shapes; residual is a real second pass -----------------------------------------------------
VENDOR = {
    "pypi": "pypi-AgEIcHlwaS5vcmcC" + "JGFiY2RlZjAxMjM0NTY3ODlh",
    "slack_webhook": "https://hooks.slack.com/" + "services/T01234ABC/B01234ABC/" + "abcdefghijklmnopqrstuvwx",
    "discord": "https://discordapp.com/" + "api/webhooks/123456789012345678/" + "abcdefghijklmnopqrstuvwxyzABCDEFGH",
    "ya29": "ya29." + "a0AfH6SMBabcdefghijklmnopqrstuvwxyz",
    "gocspx": "GOCSPX-" + "abcdefghijklmnopqrstuvwxyz01",
    "whsec": "whsec_" + "abcdefghijklmnopqrstuvwxyz012345",
    "pk_live": "pk_live_" + "abcdefghijklmnopqrstuvwx",
    "twilio_sk": "SK" + "0123456789abcdef0123456789abcdef",
    "sendgrid": "SG." + "abcdefghijklmnopqrstuv.abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    "xapp": "xapp-" + "1-A01234567-" + "1234567890123-abcdef0123456789abcdef0123456789",
}


@pytest.mark.parametrize("name", sorted(VENDOR))
def test_f19_vendor_token_is_redacted_and_residual_agrees(name):
    tok = VENDOR[name]
    out, hits = redact.redact(f"before {tok} after")
    assert tok not in out and hits >= 1
    assert redact.residual(f"before {tok} after"), "residual must know every vendor shape"
    assert not redact.residual(out)


def test_f19_residual_covers_every_high_confidence_pattern():
    """RESIDUAL is built from the same high-confidence patterns as redact(), so the two cannot drift."""
    for name in redact.HIGH_CONFIDENCE:
        assert any(n == name for n, _, _ in redact.PATTERNS), name
    for tok in VENDOR.values():
        assert redact.residual(tok)
    assert redact.residual("sk-" + "Ab3dE6gH9jK2mN5pQ8sT1vW4xZ7") and redact.residual("AKIA" + "ABCDEFGHIJKLMNOP")


def test_f19_ask_refuses_or_redacts_every_vendor_shape_before_the_provider():
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    from jade import ask

    ask.run(ask.verify_specs(), {"claim": "deployed", "evidence": " ".join(VENDOR.values()) + " AccountKey=" + "abc" * 15 + "=="}, provider=prov, session="s")
    sent = json.dumps(prov.calls)
    for tok in VENDOR.values():
        assert tok not in sent
    assert "abc" * 15 not in sent
    dump = everything(ledger.open())
    assert all(tok not in dump for tok in VENDOR.values())


# ---- F20: assignment, header, flag and URL shapes ----------------------------------------------------------------
@pytest.mark.parametrize("text,secret", [
    ("DB_PASS=" + PW, PW), ("pass=" + PW, PW), ("pwd=" + PW, PW), ("AUTH_KEY=" + PW, PW), ("api-key: " + PW, PW), ("x-api-key: " + PW, PW),
    ("Set-Cookie: sid=" + PW + "; HttpOnly", PW), ("curl -u admin:" + PW + " https://x.example.org", PW), ("mysql -u root -p" + PW, PW),
    ('{\\"password\\": \\"' + PW + '\\"}', PW), ("PASSWORD=[" + PW + "]", PW), ("redis://:" + PW + "@redis:6379/0", PW),
    ("DATABASE_URL=redis://:" + PW + "@localhost:6379", PW), ("postgres://user:pa@ss1234@db/x", "ss1234"),
    ("postgres://user:pa/ss1234@db/x", "ss1234"), ("password=12345678", "12345678"), ("token=123456", "123456"),
])
def test_f20_common_shapes_are_redacted(text, secret):
    out, hits = redact.redact(text)
    assert secret not in out and hits >= 1, out


# ---- F21: the documented Keychain -w output claim is real ---------------------------------------------------------
def test_f21_keychain_w_output_is_redacted_and_the_command_stays_readable():
    secret = "S3cr3tFrom" + "Keychain99"
    out, hits = redact.redact(f"$ security find-generic-password -s FOO -w\n{secret}\nnext line")
    assert secret not in out and hits == 1
    assert "security find-generic-password -s FOO -w" in out and "next line" in out


def test_f21_a_keychain_command_substitution_or_pipe_does_not_eat_the_next_line():
    for text in ('x=$(security find-generic-password -s FOO -w)\nkeep this line', "security find-generic-password -s FOO -w | pbcopy\nkeep this line",
                 "security find-generic-password -s FOO\nkeep this line"):
        out, _ = redact.redact(text)
        assert "keep this line" in out, text


# ---- F22: --dry-run and invalid-question errors show and journal redacted text only -------------------------------
def test_f22_dry_run_prints_the_redacted_payload(capsys):
    rc = cli.ask_main(["verify", "deploy ok", "-s", f"ran with {GHP} and password={PW}", "--dry-run"], prog="jade ask")
    cap = capsys.readouterr()
    assert rc == 0 and GHP not in cap.out and PW not in cap.out and "[REDACTED:" in cap.out
    assert set(json.loads(cap.out)) == {"model", "state", "questions"}   # the legacy `jev --dry-run` shape is unchanged
    assert "redaction(s)" in cap.err and "no secret-shaped string left" in cap.err and GHP not in cap.err


def test_f22_dry_run_matches_what_ask_run_would_send():
    from jade import ask

    state = {"claim": "c", "evidence": f"{GHP} password={PW}"}
    specs = ask.verify_specs()
    prepared, hits, _cut, resid = ask.prepare(specs, dict(state))
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    ask.run(ask.verify_specs(), dict(state), provider=prov, session="s")
    assert prov.calls[0]["state"] == prepared and hits >= 2 and resid is False


def test_f22_invalid_question_error_neither_prints_nor_journals_the_raw_value(capsys):
    secret = "sk-" + "Ab3dE6gH9jK2mN5pQ8sT1vW4xZ7"
    rc = cli.ask_main(["question", "q.verify_claim.supported_by_evidence", "--field", secret], prog="jade ask")
    err = capsys.readouterr().err
    assert rc == 6 and secret not in err
    assert secret not in everything(ledger.open())
    assert ledger.open().one("SELECT COUNT(*) AS n FROM events WHERE kind='INVALID_QUESTION'").n == 1


def test_f22_fast_args_are_stored_redacted(capsys):
    cli.main(["fast", "file-exists", "/nonexistent/" + GHP])
    capsys.readouterr()
    assert GHP not in everything(ledger.open())


# ---- F23: no over-redaction of non-secret evidence ----------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "max_tokens=4096", "token_count: 1532", "usage: prompt_tokens: 1532 completion_tokens: 88", "def login(password: str, token: Optional[str] = None):",
    "api_key: str", "secret_key: bytes = b''", "secret_name: my-db-secret", "TOKENIZERS_PARALLELISM=false", "git remote add origin git@github.com:org/repo.git",
    "git clone ssh://git@github.com/org/repo.git", "authorization: required for /admin", "Authorization: header missing", "PASSED: test_password_reset_flow",
    "PWD=/Users/someone/project", "- PASSWORD_MIN_LENGTH=12", "password: required", "credentials: {}", "TOKEN=$GITHUB_TOKEN", "password: ${DB_PASSWORD}",
    "https://example.com:8080/path/to/page?x=1",
])
def test_f23_non_secret_text_is_left_alone(text):
    assert redact.redact(text) == (text, 0)


def test_f23_a_real_secret_under_a_benign_looking_line_is_still_redacted():
    for text in ("max_tokens=4096 key=" + GHP, "def f(password='" + PW + "'):", "token: Optional[str] = '" + PW + "'"):
        out, hits = redact.redact(text)
        assert GHP not in out and PW not in out and hits >= 1, out


def test_redaction_is_idempotent_and_leaves_its_own_marks_alone():
    text = f"password={PW} token={TOKEN} Authorization: Bearer {TOKEN} redis://:{PW}@h:1/0\n$ security find-generic-password -w\n{PW}"
    once, _ = redact.redact(text)
    twice, hits = redact.redact(once)
    assert twice == once and hits == 0


def test_store_time_redaction_keeps_emails():
    assert redact.redact("a@b.example", secrets_only=True) == ("a@b.example", 0)
    assert redact.redact("a@b.example")[1] == 1


def test_redaction_is_linear_on_long_unbroken_tokens():
    """A base64 line or a long run of key characters must not make a call hang: redact() runs on every byte `jade ask` sends."""
    import random
    import string
    import time

    rnd = random.Random(1)
    big = "".join(rnd.choice(string.ascii_letters + string.digits + "-_") for _ in range(200_000))
    for text in (big, "foo_bar-baz.qux_" * 12_500, "://:" * 50_000, "x" * 100_000 + "@"):
        t0 = time.monotonic()
        redact.redact(text)
        assert time.monotonic() - t0 < 5, "quadratic backtracking in redact()"
