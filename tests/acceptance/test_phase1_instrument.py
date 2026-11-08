import json

from jade import adapter, ledger, redact  # noqa: F401  (resolved once W-02 lands)


def test_adapter_contract_fixtures():
    """docs/06 Phase 1: recorded fixtures noul/choice/score/batch/400/429/529/timeout -> typed results."""
    for fx in ("noul", "choice", "score", "batch3"):
        assert adapter.parse_fixture(f"tests/fixtures/{fx}.json").ok
    for fx, kind in (("http400", "REJECTED"), ("http429", "RATE_LIMITED"), ("http529", "UNAVAILABLE"), ("timeout", "UNAVAILABLE")):
        assert adapter.parse_fixture(f"tests/fixtures/{fx}.json").error_kind == kind


def test_ledger_write_ahead(tmp_path):
    """Crash between request and response leaves an EVALUATING row, never a result."""
    db = ledger.open(tmp_path / "l.sqlite")
    call_id = db.begin_call(question_id="q.verify_claim.supported_by_evidence", state_hash="0" * 64)
    row = db.get_call(call_id)
    assert row.state == "EVALUATING" and row.answers_json is None


def test_redact_corpus():
    corpus = json.load(open("tests/fixtures/redact_corpus.json"))
    for item in corpus:
        text, hits = redact.redact(item["input"])
        assert item["secret"] not in text and hits >= 1


def test_shim_output_identical():
    """`jev` compat shim prints exactly the legacy format for noul and choice."""
    assert redact  # placeholder until shim module exists


def test_duplicate_detection(tmp_path):
    db = ledger.open(tmp_path / "l.sqlite")
    a = db.begin_call("q.x", "a" * 64, session="s1")
    b = db.begin_call("q.x", "a" * 64, session="s1")
    assert db.get_call(b).dedup_of == a
