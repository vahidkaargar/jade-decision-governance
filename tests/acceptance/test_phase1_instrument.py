"""Phase 1 acceptance (docs/06): adapter contract, ledger write-ahead, redaction corpus, shim output, dedup."""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from jade import adapter, ask, cli, ledger, redact

ROOT = Path(__file__).resolve().parents[2]
FX = ROOT / "tests" / "fixtures"


@pytest.mark.parametrize("fx", ["noul", "choice", "score", "batch3"])
def test_adapter_contract_ok_fixtures(fx):
    r = adapter.parse_fixture(FX / f"{fx}.json")
    assert r.ok and r.resolved_model == "typesafe/jev-1.14-20261014" and r.request_id and r.cost_usd is not None


@pytest.mark.parametrize("fx,kind", [("http400", "REJECTED"), ("http429", "RATE_LIMITED"), ("http529", "UNAVAILABLE"), ("timeout", "UNAVAILABLE")])
def test_adapter_contract_error_fixtures(fx, kind):
    r = adapter.parse_fixture(FX / f"{fx}.json")
    assert not r.ok and r.error_kind == kind and not r.answers


def test_malformed_200_is_not_an_answer():
    for body in ({}, {"answers": {}}, {"answers": {"q": {"type": "noul", "noul": 1.7}}}, {"answers": {"q": {"type": "noul"}}}):
        assert adapter.parse_response(200, body).error_kind == "MALFORMED"


def test_retries_only_429_and_529_and_at_most_twice():
    calls = []

    def transport(payload, key, timeout):
        calls.append(1)
        return 429, json.dumps({"error": {"message": "slow down"}}), {"retry-after": "0"}

    p = adapter.OpenRouterProvider(transport=transport, key_fn=lambda: "k", sleep=lambda s: None)
    r = p.ask({}, {"q": {}})
    assert r.error_kind == "RATE_LIMITED" and len(calls) == 3 and r.attempts == 3
    calls.clear()
    p2 = adapter.OpenRouterProvider(transport=lambda *a: (400, "{}", {}) if calls.append(1) is None else None, key_fn=lambda: "k", sleep=lambda s: None)
    assert p2.ask({}, {}).error_kind == "REJECTED" and len(calls) == 1


def test_retry_recovers_after_529():
    seq = iter([(529, "{}", {}), (200, (FX / "noul.json").read_text() and json.dumps(json.loads((FX / "noul.json").read_text())["body"]), {})])
    p = adapter.OpenRouterProvider(transport=lambda *a: next(seq), key_fn=lambda: "k", sleep=lambda s: None)
    r = p.ask({}, {"q": {}})
    assert r.ok and r.attempts == 2


def test_missing_key_is_typed():
    assert adapter.OpenRouterProvider(key_fn=lambda: "").ask({}, {}).error_kind == "NO_KEY"


def test_ledger_write_ahead(tmp_path):
    """Crash between request and response leaves an EVALUATING row, never a result."""
    db = ledger.open(tmp_path / "l.sqlite")
    call_id = db.begin_call(question_id="q.verify_claim.supported_by_evidence", state_hash="0" * 64)
    db.close()
    row = ledger.open(tmp_path / "l.sqlite").get_call(call_id)
    assert row.state == "EVALUATING" and row.answers_json is None and row.exit_code is None


def test_ask_journals_before_provider_is_called(tmp_path):
    seen = {}

    def responder(state, questions, model):
        db = ledger.open()
        seen["rows"] = [(r.state, r.answers_json) for r in db.all("SELECT state, answers_json FROM jev_calls")]
        return {k: {"type": "noul", "noul": 0.9} for k in questions}

    ask.run(ask.verify_specs(), {"claim": "tests pass", "evidence": "42 passed"}, provider=adapter.FixtureProvider(responder), session="s")
    assert seen["rows"] and all(s == "EVALUATING" and a is None for s, a in seen["rows"])


def test_provider_crash_leaves_evaluating_row_and_never_a_yes():
    def boom(state, questions, model):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ask.run(ask.verify_specs(), {"claim": "tests pass", "evidence": "42 passed"}, provider=adapter.FixtureProvider(boom), session="s")
    rows = ledger.open().all("SELECT state, exit_code, answers_json FROM jev_calls")
    assert rows and all(r.state == "EVALUATING" and r.exit_code is None and r.answers_json is None for r in rows)


def test_redact_corpus():
    corpus = json.loads((FX / "redact_corpus.json").read_text())
    assert len(corpus) >= 80
    for item in corpus:
        secret = "".join(item["secret"])  # stored in parts so the repository holds no literal token-shaped strings
        text, hits = redact.redact(item["template"].replace("@@", secret))
        assert secret not in text, item["kind"]
        assert hits >= 1, item["kind"]
        assert not redact.residual(text), item["kind"]


def test_redact_keeps_ordinary_text_and_owner_email():
    plain = "pytest: 42 passed in 3.1s, file jade/ask.py line 12, commit f32b929"
    assert redact.redact(plain) == (plain, 0)
    text, hits = redact.redact("mail me@example.com or other@example.org", owner_emails=("me@example.com",))
    assert "me@example.com" in text and "other@example.org" not in text and hits == 1


def test_secret_in_state_is_never_sent_or_stored():
    secret = "sk-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1"
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    ask.run(ask.verify_specs(), {"claim": "deployed", "evidence": f"key was {secret} in output"}, provider=prov, session="s")
    assert secret not in json.dumps(prov.calls)
    assert all(secret not in (r.state_text or "") for r in ledger.open().all("SELECT state_text FROM jev_calls"))


def _legacy(argv, provider):
    return cli.ask_main(argv, prog="jev", provider=provider)


def test_shim_output_identical(capsys):
    noul = adapter.FixtureProvider(lambda s, q, m: {"q": {"type": "noul", "noul": 0.98}, "evidence_is_raw": {"type": "noul", "noul": 0.9}})
    assert _legacy(["verify", "The suite passed", "-s", "42 passed"], noul) == 0
    assert re.fullmatch(r"P\(yes\)=0\.98  \[cost \$\d+\.\d{6}\]\n", capsys.readouterr().out)
    choice = adapter.FixtureProvider(lambda s, q, m: {"q": {"type": "choice", "choice": "retry", "probabilities": {"retry": 0.76, "cache": 0.24}, "confidence": 0.52}})
    _legacy(["choice", "Which?", "-o", "retry=a", "-o", "cache=b"], choice)
    assert capsys.readouterr().out.startswith("choice=retry confidence=0.52 | retry=0.76 cache=0.24")
    score = adapter.FixtureProvider(lambda s, q, m: {"q": {"type": "score", "score": 1.39, "legend": {"0": "none", "1": "summary only"}, "probabilities": {}, "confidence": 0.61}})
    _legacy(["score", "How raw?", "-c", "none", "-c", "summary only"], score)
    assert capsys.readouterr().out.startswith("score=1.39 confidence=0.61 (~ summary only)")


def test_shim_payload_matches_legacy_script(capsys):
    legacy = ROOT / "tests" / "fixtures" / "legacy_jev.py"
    if not legacy.exists():
        pytest.skip("legacy_jev.py golden copy not present")
    want = subprocess.run([sys.executable, str(legacy), "--dry-run", "choice", "Which?", "-o", "a=x", "-o", "b=y", "-s", "ctx"], capture_output=True, text=True).stdout
    cli.ask_main(["--dry-run", "choice", "Which?", "-o", "a=x", "-o", "b=y", "-s", "ctx"], prog="jev")
    got = json.loads(capsys.readouterr().out)
    assert got["questions"]["q"] == json.loads(want)["questions"]["q"]
    assert got["state"] == json.loads(want)["state"]


def test_exit_codes_and_not_a_yes(capsys):
    def band(p):
        return adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": p} for k in q})

    assert _legacy(["verify", "c", "-s", "raw output"], band(0.95)) == 0
    assert _legacy(["verify", "c2", "-s", "raw output"], band(0.55)) == 3
    assert _legacy(["verify", "c3", "-s", "raw output"], band(0.05)) == 4
    capsys.readouterr()
    down = adapter.FixtureProvider(lambda s, q, m: adapter.Result(ok=False, error_kind="UNAVAILABLE"))
    assert _legacy(["verify", "c4", "-s", "x"], down) == 5
    err = capsys.readouterr().err
    assert "not a yes" in err and "authorized" not in err.lower()
    assert _legacy(["choice", "q", "-o", "only=one"], band(0.9)) == 6


def test_duplicate_detection(tmp_path):
    db = ledger.open(tmp_path / "l.sqlite")
    a = db.begin_call("q.x", "a" * 64, session="s1")
    db.finish_call(a, exit_code=0, answers_json={"type": "noul", "noul": 0.9})   # only an answered call anchors a repeat
    b = db.begin_call("q.x", "a" * 64, session="s1")
    c = db.begin_call("q.x", "a" * 64, session="s2")
    d = db.begin_call("q.x", "b" * 64, session="s1")
    assert db.get_call(b).dedup_of == a and db.get_call(c).dedup_of is None and db.get_call(d).dedup_of is None


def test_repeat_returns_cached_answer_without_a_second_provider_call(capsys):
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    argv = ["verify", "same claim", "-s", "same evidence"]
    assert cli.ask_main(argv, prog="jev", provider=prov) == 0
    capsys.readouterr()
    assert cli.ask_main(argv, prog="jev", provider=prov) == 0
    cap = capsys.readouterr()
    assert len(prov.calls) == 1 and "repeat_without_new_evidence" in cap.err and "P(yes)=0.90" in cap.out


def test_batch_one_request_for_main_and_companion():
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    ask.run(ask.verify_specs(), {"claim": "c", "evidence": "e"}, provider=prov, session="s")
    assert len(prov.calls) == 1 and set(prov.calls[0]["questions"]) == {"q", "evidence_is_raw"}


def test_state_cap_and_ledger_privacy():
    big = {"claim": "c", "evidence": "x" * 100_000}
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    ask.run(ask.verify_specs(), big, provider=prov, session="s")
    assert len(json.dumps(prov.calls[0]["state"]).encode()) < 33_000
    mode = (ledger.open().path.stat().st_mode & 0o777)
    assert mode == 0o600


def test_ledger_survives_concurrent_writers(tmp_path):
    import threading

    path = tmp_path / "c.sqlite"
    errors = []

    def work(n):
        try:
            db = ledger.open(path)
            for i in range(10):
                db.begin_call("q.x", f"{n:02d}{i:062d}", session=f"s{n}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and ledger.open(path).one("SELECT COUNT(*) AS n FROM jev_calls").n == 60
    assert sqlite3.connect(path).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
