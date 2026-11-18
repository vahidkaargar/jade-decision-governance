"""evals/spikes.py (V6-24): SP-0, SP-1 and SP-3. SP-0 and SP-1 need the owner's word because they send text to openrouter.ai; here they run only against a fixture
provider, and a test proves that without the word nothing is sent. SP-3 is offline and reads a copy of a ledger that the test builds in a temporary directory."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from jade import adapter, ledger

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "evals" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


spikes = _load("spikes")


class Boom:
    name = "boom"

    def ask(self, *a, **k):
        raise AssertionError("the provider must not be called")


def fixture(values):
    seq = list(values)
    return adapter.FixtureProvider(lambda state, q, m: {k: {"type": "noul", "noul": seq.pop(0) if seq else 0.5} for k in q})


# ---- SP-0 and SP-1: the owner's word -----------------------------------------------------------------------
@pytest.mark.parametrize("cmd", ["sp0", "sp1"])
def test_without_the_owners_word_nothing_is_sent(cmd, tmp_path, capsys):
    assert spikes.main([cmd, "--out", str(tmp_path / "o.json")], provider=Boom()) == 2
    assert "only the owner" in capsys.readouterr().err
    assert not (tmp_path / "o.json").exists()


def test_sp0_saves_the_raw_body_and_flags_free_text(tmp_path):
    long_text = "x" * 80

    def responder(state, q, m):
        return adapter.Result(ok=True, answers={k: {"type": "noul", "noul": 0.8} for k in q}, resolved_model="m", raw={"answers": {"q": {"noul": 0.8}}, "note": long_text})
    out = spikes.sp0(adapter.FixtureProvider(responder), tmp_path / "raw.json")
    assert out["ok"] and json.loads((tmp_path / "raw.json").read_text())["note"] == long_text
    assert out["free_text_candidates"] == ["$.note"]
    assert (tmp_path / "raw.json").stat().st_mode & 0o777 == 0o600


def test_sp0_sends_only_the_fixed_fixture(tmp_path):
    prov = fixture([0.8])
    spikes.sp0(prov, tmp_path / "raw.json")
    assert prov.calls[0]["state"] == {"claim": spikes.FIXTURE_CLAIM, "evidence": spikes.FIXTURE_EVIDENCE}


def test_sp1_makes_n_calls_per_variant_and_summarises(tmp_path):
    prov = fixture([0.9, 0.5] * 60)
    out = spikes.sp1(prov, n=4)
    assert len(prov.calls) == 4 * len(spikes.VARIANTS)
    v = out["variants"]["base"]
    assert v["n"] == 4 and v["min"] == 0.5 and v["max"] == 0.9 and v["range"] == 0.4 and v["share_at_or_above_accept"] == 0.5
    assert out["errors"] == 0


def test_sp1_counts_a_call_with_no_usable_answer(tmp_path):
    out = spikes.sp1(adapter.FixtureProvider(lambda s, q, m: adapter.Result(ok=False, error_kind="UNAVAILABLE")), n=2)
    assert out["errors"] == 2 * len(spikes.VARIANTS) and out["variants"]["base"] == {"n": 0}


def test_the_sp1_variants_are_the_ones_dedup_calls_trivial():
    keys = set(spikes.VARIANTS)
    assert {"base", "trailing_newline", "trailing_spaces", "crlf", "claim_reworded"} <= keys


# ---- SP-3 ---------------------------------------------------------------------------------------------------
def test_checks_fire_on_the_right_texts():
    long_ok = "x" * 300
    assert spikes.checks("tests pass", long_ok) == []
    assert spikes.checks("tests pass", "ok") == ["C1"]
    assert "C5" in spikes.checks("412 tests pass in `tests/x`", long_ok + " 412")           # `tests/x` is missing
    assert "C5" not in spikes.checks("412 tests pass in `tests/x`", long_ok + " 412 tests/x")
    assert "C6" in spikes.checks("tests pass", long_ok + "\nTraceback (most recent call last)")
    assert "C9" in spikes.checks("tests pass", long_ok + "\n" + spikes.CAP_MARK)


def test_padding_the_evidence_with_the_claim_does_not_clear_c5():
    claim = "412 tests pass in `tests/x`"
    assert "C5" in spikes.checks(claim, "x" * 300 + "\n" + claim)   # a line that only repeats the claim is not evidence


def test_claim_tokens_are_language_free():
    assert spikes.claim_tokens("۴۱۲ تست در `tests/x` و 412 و abcdef1") == ["۴۱۲", "tests/x", "412", "abcdef1"]
    assert spikes.claim_tokens("a path src/app/main.py and 7") == ["src/app/main.py", "7"]


def build_ledger(path, rows):
    db = ledger.open(path)
    for call_id, state, exit_code, dedup in rows:
        cid = db.begin_call("q.verify_claim.supported_by_evidence", "h" + call_id, f"s-{call_id}", state_text=json.dumps(state))
        db.finish_call(cid, exit_code=exit_code, p=0.9 if exit_code == 0 else 0.5, dedup_of=dedup)
    db.close()


def test_sp3_counts_hits_and_never_prints_the_stored_text(tmp_path):
    p = tmp_path / "copy.sqlite"
    build_ledger(p, [("1", {"claim": "tests pass", "evidence": "x" * 300}, 0, None), ("2", {"claim": "tests pass", "evidence": "short"}, 0, None),
                     ("3", {"claim": "tests pass", "evidence": "y" * 300 + "\nFAILED a"}, 3, None)])
    before = p.read_bytes()
    out = spikes.sp3(p)
    assert out["verify_calls_read"] == 3 and out["calls_with_a_hit"] == 2
    assert out["per_check"]["C1"] == {"hits": 1, "on_accepted": 1, "on_abstained": 0, "on_rejected": 0}
    assert out["per_check"]["C6"]["on_abstained"] == 1
    assert "short" not in json.dumps(out) and "FAILED" not in json.dumps(out)
    assert p.read_bytes() == before                                   # read-only: the copy is byte for byte the same


def test_sp3_refuses_the_live_ledger_and_a_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JADE_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "jade.sqlite").write_text("")
    with pytest.raises(SystemExit, match="COPY"):
        spikes.sp3(tmp_path / "home" / "jade.sqlite")
    with pytest.raises(SystemExit, match="not a file"):
        spikes.sp3(tmp_path / "nope.sqlite")


def test_sp3_cannot_write_to_the_copy(tmp_path):
    p = tmp_path / "copy.sqlite"
    build_ledger(p, [("1", {"claim": "c", "evidence": "e"}, 0, None)])
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM jev_calls")
    con.close()
