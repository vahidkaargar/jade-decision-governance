from jade import evals  # noqa: F401


def test_split_no_session_leak():
    splits = evals.split([{"session": f"s{i}", "x": i} for i in range(200)])
    seen = {}
    for name, rows in splits.items():
        for r in rows:
            assert seen.setdefault(r["session"], name) == name


def test_metrics_against_synthetic_truth():
    m = evals.metrics(preds=[0.9, 0.1, 0.9, 0.1], labels=[1, 0, 0, 1], accept=0.7, reject=0.3)
    assert m["false_accept"] == 1 and m["false_reject"] == 1 and 0 <= m["brier"] <= 1


def test_protected_set_unreadable_without_tty(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    import pytest
    with pytest.raises(evals.ProtectedSetAccessDenied):
        evals.load_protected("verify_claim")
