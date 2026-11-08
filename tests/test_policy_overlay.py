"""The machine-local overlay mechanism: generic rules stay public, site specifics merge in safely."""
from pathlib import Path

import pytest

from policies.loader import OverlayError, blocked_by, classify, environment_of, load_risk, load_safety

ROOT = Path(__file__).resolve().parents[1]
RISK_EX = ROOT / "policies/local/risk.local.example.yaml"
SAFETY_EX = ROOT / "policies/local/safety.local.example.yaml"


def test_example_overlay_merges_and_keeps_ordering():
    risk = load_risk(overlay=RISK_EX)
    ids = [r["id"] for r in risk["rules"]]
    assert ids[:2] == ["R-L01", "R-L02"] and ids[-1] == "R-99"
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    levels = [order[r["level"]] for r in risk["rules"]]
    assert levels == sorted(levels)
    assert "router.example.net" in risk["environments"]["production_hosts"]
    assert "/etc/" in risk["environments"]["production_paths"] and "~/infra/" in risk["environments"]["production_paths"]


def test_overlay_escalates_router_ssh_to_critical_but_generic_ssh_stays_high():
    risk = load_risk(overlay=RISK_EX)
    assert classify(risk, "ssh admin@router.example.net /export")["level"] == "critical"
    assert classify(risk, "ssh deploy@other.example.net uptime")["level"] == "high"
    assert environment_of(risk, "rsync -a ./ prod.example.net:/srv") == "production"
    assert environment_of(risk, "curl http://localhost:3000") == "staging"
    assert environment_of(risk, "uv run pytest") == "local"


def test_missing_overlay_is_fine():
    risk = load_risk(overlay=ROOT / "policies/local/does-not-exist.yaml")
    assert risk["rules"][0]["id"] == "R-01"


def test_overlay_cannot_lower_a_shipped_high_rule(tmp_path):
    ov = tmp_path / "o.yaml"
    ov.write_text("rules_override:\n  R-10:\n    level: low\n")
    with pytest.raises(OverlayError):
        load_risk(overlay=ov)


def test_overlay_cannot_reuse_shipped_ids(tmp_path):
    ov = tmp_path / "o.yaml"
    ov.write_text("rules_prepend:\n  - {id: R-10, level: low, match: x, rationale: y}\n")
    with pytest.raises(OverlayError):
        load_risk(overlay=ov)
    ov.write_text("rules_prepend:\n  - {id: custom, level: low, match: x, rationale: y}\n")
    with pytest.raises(OverlayError):
        load_risk(overlay=ov)


def test_overlay_low_rule_cannot_shadow_a_shipped_high_rule(tmp_path):
    ov = tmp_path / "o.yaml"
    ov.write_text("rules_prepend:\n  - {id: R-L05, level: low, match: 'git push', rationale: 'try to whitelist pushes'}\n")
    risk = load_risk(overlay=ov)
    hit = classify(risk, "git push origin feature/x")
    assert hit["id"] == "R-10" and hit["level"] == "high"


def test_overlay_mixed_tiers_are_sorted_into_place(tmp_path):
    ov = tmp_path / "o.yaml"
    ov.write_text(
        "rules_prepend:\n"
        "  - {id: R-L10, level: high, match: 'my-service restart', rationale: 'site service'}\n"
        "  - {id: R-L01, level: critical, match: 'ssh core-router', rationale: 'router'}\n"
    )
    risk = load_risk(overlay=ov)
    ids = [r["id"] for r in risk["rules"]]
    assert ids[0] == "R-L01" and ids.index("R-L10") == ids.index("R-10") - 1
    assert classify(risk, "my-service restart")["level"] == "high"


def test_safety_overlay_appends_only():
    safety = load_safety(overlay=SAFETY_EX)
    assert "SL-01" in blocked_by(safety, "docker start example-crawler")
    assert "SI-08" in blocked_by(safety, "git push origin main")


def test_safety_overlay_cannot_touch_shipped_invariants(tmp_path):
    ov = tmp_path / "s.yaml"
    ov.write_text("invariants:\n  - {id: SI-08, description: weakened}\n")
    with pytest.raises(OverlayError):
        load_safety(overlay=ov)
    ov.write_text("invariants_append:\n  - {id: SI-08, description: dup, block: x}\n")
    with pytest.raises(OverlayError):
        load_safety(overlay=ov)
