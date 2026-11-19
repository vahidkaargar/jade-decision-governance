"""V6-11 (FN-10): a run that has a ledger of its own never writes to the JADE home.

`ask._run` called `ops.check_drift(None, ...)`, and `None` means "the real home". A fixture run (`adapter.FixtureProvider`, resolved model string ending in
`-fixture`) therefore opened the real ledger, recorded a MODEL_DRIFT event and opened a work item there, even when the run was given a ledger of its own.
The drift check now writes to the ledger the call itself uses.
"""

from __future__ import annotations

import json

from jade import adapter, ask, ledger, ops, paths


def fixture(p=0.9):
    return adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": p} for k in q})


def home_is_untouched() -> bool:
    home = paths.home()
    return not any(home.iterdir()) if home.exists() else True


def test_a_fixture_run_with_its_own_ledger_writes_nothing_to_the_jade_home(tmp_path):
    own = ledger.open(tmp_path / "own")
    assert home_is_untouched()
    r = ask.run(ask.verify_specs(), {"claim": "tests pass", "evidence": "42 passed"}, provider=fixture(), session="s", db=own)
    assert r.exit_code == 0 and r.resolved_model.endswith("-fixture")
    assert home_is_untouched(), f"a run with its own ledger touched the JADE home: {sorted(p.name for p in paths.home().iterdir())}"
    assert json.loads(own.meta_get("known_models", "[]")) == [r.resolved_model], "the drift note belongs in the ledger the call used"
    assert own.one("SELECT COUNT(*) AS n FROM events WHERE kind='MODEL_DRIFT'").n == 1
    own.close()


def test_a_run_whose_ledger_is_down_records_no_drift_anywhere(tmp_path, monkeypatch):
    """No ledger, no journal: the drift note is skipped, not sent to the home as a fallback."""
    def refuse(*a, **k):
        raise OSError("no ledger")

    with monkeypatch.context() as m:
        m.setattr(ledger, "open", refuse)
        r = ask.run(ask.verify_specs(), {"claim": "tests pass", "evidence": "42 passed"}, provider=fixture(), session="s")
    assert r.exit_code == 0 and any("not journaled" in w for w in r.warnings)
    assert home_is_untouched()


def test_check_drift_without_a_ledger_argument_still_uses_the_directory_it_is_given(tmp_path):
    d = ops.check_drift(tmp_path, resolved_model="typesafe/jev-9-1", expected=["typesafe/jev-1"])
    assert d.first_seen is True and (tmp_path / "jade.sqlite").exists() and home_is_untouched()


def test_check_drift_given_a_ledger_uses_that_ledger_and_leaves_it_open(tmp_path):
    own = ledger.open(tmp_path / "own")
    d = ops.check_drift(None, resolved_model="typesafe/jev-9-1", expected=["typesafe/jev-1"], db=own)
    assert d.first_seen is True and own.meta_get("known_models") and home_is_untouched()
    assert own.one("SELECT 1 AS x").x == 1, "a ledger the caller passed in is the caller's to close"
    own.close()
