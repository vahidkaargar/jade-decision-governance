"""The machine-local overlay mechanism: generic rules stay public, site specifics merge in safely."""
from pathlib import Path

import pytest

from jade import ops, paths
from policies.loader import OverlayError, blocked_by, classify, environment_of, lint_overlay, load_risk, load_safety

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
    assert ids[0] == "R-L01" and ids.index("R-L10") == min(ids.index("R-17"), ids.index("R-10")) - 1   # before the first high shipped rule
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


# ---- W-56: overlay lint in `jade check`, unread checkout overlays, negative cases ----------------------------------------------
SECRET = "secret-host.internal"


def _overlay(tmp_path, kind, text):
    """Write the overlay where the runtime reads it (JADE_CONFIG is isolated by the autouse fixture) and return the file."""
    f = paths.config_dir() / "local" / f"{kind}.local.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def _row(name):
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}[name]


def test_invalid_regex_in_an_overlay_is_reported_without_the_pattern(tmp_path):
    risk = _overlay(tmp_path, "risk",
                    f"environments:\n  staging_markers: ['({SECRET}']\n"
                    f"rules_prepend:\n  - {{id: R-L01, level: critical, match: '({SECRET}', rationale: x}}\n")
    safety = _overlay(tmp_path, "safety", f"invariants_append:\n  - {{id: SL-01, description: d, block: '[{SECRET}'}}\n")
    problems = lint_overlay("risk", risk) + lint_overlay("safety", safety)
    text = "\n".join(problems)
    assert "environments.staging_markers" in text and "R-L01" in text and "SL-01" in text and "not a valid regular expression" in text
    assert SECRET not in text
    ok, detail = _row("overlay lint")
    assert not ok and "risk.local.yaml" in detail and "safety.local.yaml" in detail and SECRET not in detail
    assert ops.print_check(ops.check(live=False)) == 1


def test_empty_block_in_an_overlay_is_reported(tmp_path):
    f = _overlay(tmp_path, "safety",
                 "invariants_append:\n"
                 "  - {id: SL-01, description: empty, block: ''}\n"
                 "  - {id: SL-02, description: star, block: 'x*'}\n"
                 "  - {id: SL-03, description: none}\n")
    problems = lint_overlay("safety", f)
    assert len(problems) == 3
    assert "SL-01" in problems[0] and "empty" in problems[0]
    assert "SL-02" in problems[1] and "matches the empty string" in problems[1]
    assert "SL-03" in problems[2] and "no block pattern" in problems[2]
    assert not _row("overlay lint")[0]
    # the failure the lint exists for: the loader accepts it and the invariant then blocks every command
    assert blocked_by(load_safety(overlay=f), "echo hello") == ["SL-01", "SL-02"]


def test_empty_rule_match_in_an_overlay_is_reported(tmp_path):
    f = _overlay(tmp_path, "risk", "rules_prepend:\n  - {id: R-L01, level: critical, match: '', rationale: x}\n")
    assert any("R-L01" in p and "empty" in p for p in lint_overlay("risk", f))


def test_production_hosts_win_over_staging_markers_and_a_broken_marker_is_the_lint_not_the_classifier(tmp_path):
    ov = tmp_path / "o.yaml"
    ov.write_text("environments:\n  production_hosts: [prod.example.net]\n  production_paths: ['~/infra/']\n")
    risk = load_risk(overlay=ov)
    assert environment_of(risk, "curl http://localhost:3000") == "staging"
    assert environment_of(risk, "ssh prod.example.net curl http://localhost:3000") == "production"   # host beats the staging marker
    assert environment_of(risk, "cd ~/infra/ && curl http://staging.example.com") == "production"    # so does a path
    assert environment_of(risk, "ssh other.example.net curl http://localhost:3000") == "staging"
    # an empty entry never turns everything into production (guarded in environment_of), and the lint says it does nothing
    ov.write_text("environments:\n  production_hosts: ['']\n  staging_markers: ['']\n")
    assert environment_of(load_risk(overlay=ov), "uv run pytest") == "staging"   # an empty staging marker matches everything
    problems = lint_overlay("risk", ov)
    assert any("production_hosts[0]" in p for p in problems) and any("staging_markers[0]" in p for p in problems)


def test_overlay_that_lowers_a_shipped_high_rule_is_reported_even_to_medium(tmp_path):
    f = _overlay(tmp_path, "risk", "rules_override:\n  R-10:\n    level: medium\n    rationale: ok\n  R-20:\n    level: medium\n")
    load_risk(overlay=f)   # the loader accepts medium
    problems = lint_overlay("risk", f)
    assert len(problems) == 1 and "R-10" in problems[0] and "high" in problems[0] and "medium" in problems[0]
    assert not _row("overlay lint")[0]


def test_overlay_keys_the_loader_does_not_merge_are_reported(tmp_path):
    f = _overlay(tmp_path, "risk", "mcp_tools:\n  - {match: x, level: low}\nread_only: ['^ls']\nhosts: {critical: [a]}\nenvironments: {}\n")
    text = "\n".join(lint_overlay("risk", f))
    assert "mcp_tools" in text and "read_only" in text and "hosts" in text and "not read" in text
    f = _overlay(tmp_path, "safety", "invariants: []\n")
    assert "invariants" in "\n".join(lint_overlay("safety", f))


def test_unparsable_overlay_is_reported_without_its_text(tmp_path):
    f = _overlay(tmp_path, "risk", f"environments: [{SECRET}\n")
    problems = lint_overlay("risk", f)
    assert problems and SECRET not in "\n".join(problems)


def test_shipped_example_overlays_lint_clean():
    assert lint_overlay("risk", RISK_EX) == []
    assert lint_overlay("safety", SAFETY_EX) == []


def test_no_overlay_is_a_passing_row():
    assert _row("overlay lint")[0] and _row("overlay files")[0]


def test_a_local_yaml_in_the_checkout_that_nothing_reads_is_reported(tmp_path):
    d = paths.checkout_overlay_dir()
    d.mkdir(parents=True)
    (d / "risk.local.yaml").write_text(f"environments:\n  production_hosts: [{SECRET}]\n")
    (d / "risk.local.example.yaml").write_text("schema_version: 1.0.0\n")   # an example is not an overlay
    (d / "routing.local.yaml").write_text("x: 1\n")
    ok, detail = _row("overlay files")
    assert not ok and "risk.local.yaml" in detail and "routing.local.yaml" in detail and "example" not in detail
    assert str(paths.config_dir() / "local") in detail and SECRET not in detail
    assert ops.print_check(ops.check(live=False)) == 1


def test_a_checkout_overlay_the_runtime_reads_is_not_reported(tmp_path, monkeypatch):
    d = paths.checkout_overlay_dir()
    d.mkdir(parents=True)
    (d / "risk.local.yaml").write_text("schema_version: 1.0.0\n")
    monkeypatch.setenv("JADE_CONFIG", str(tmp_path / "cfg"))   # the checkout directory is the configuration directory's `local`
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "local").symlink_to(d)
    assert _row("overlay files")[0]
