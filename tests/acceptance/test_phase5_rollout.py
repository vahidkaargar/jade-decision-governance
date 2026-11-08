from jade import ops  # noqa: F401


def test_rollback_drill_restores_champion(tmp_path):
    ops.pin_champion(tmp_path, "q.verify_claim.supported_by_evidence", 1)
    ops.set_challenger(tmp_path, "q.verify_claim.supported_by_evidence", 2)
    ops.rollback(tmp_path, "q.verify_claim.supported_by_evidence")
    assert ops.active_version(tmp_path, "q.verify_claim.supported_by_evidence") == 1


def test_drift_canary_fires_on_model_string_change(tmp_path):
    assert ops.check_drift(tmp_path, resolved_model="typesafe/jev-1.14-20261014", expected=["typesafe/jev-1.14-20261014"]) is None
    assert ops.check_drift(tmp_path, resolved_model="typesafe/jev-1.15-20270101", expected=["typesafe/jev-1.14-20261014"]).kind == "MODEL_DRIFT"
