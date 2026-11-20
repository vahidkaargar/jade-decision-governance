"""W-201: the modules that W-93 skips where the real approver anchor is installed must run in CI, and CI must fail, not skip, if it ever skips them."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from jade import signing
from tests import conftest as cf

ROOT = Path(__file__).resolve().parents[1]


def items_for(*modules: str) -> list:
    return [SimpleNamespace(module=SimpleNamespace(__name__=f"tests.{m}"), markers=[], add_marker=lambda mk, _m=m: marks.append((_m, mk))) for m in modules]


marks: list = []


@pytest.fixture(autouse=True)
def reset_marks():
    marks.clear()


@pytest.fixture
def anchor(tmp_path, monkeypatch):
    path = tmp_path / "allowed_signers"
    path.write_text("x\n")
    monkeypatch.setattr(signing, "DEFAULT_ANCHOR", path)
    return path


def test_every_listed_module_exists():
    # a renamed module would silently stop being listed (and so stop being skipped on an anchor machine) and the list would rot
    present = {p.stem for p in (ROOT / "tests").rglob("test_*.py")}
    assert cf.SUBPROCESS_APPROVE_MODULES <= present, sorted(cf.SUBPROCESS_APPROVE_MODULES - present)


def test_with_the_anchor_installed_the_listed_modules_are_skipped_as_before(anchor, monkeypatch):
    monkeypatch.delenv("JADE_REQUIRE_APPROVE_MODULES", raising=False)
    cf.pytest_collection_modifyitems(None, items_for("test_gate_failure_modes", "test_hook_payloads_w40", "test_phase2_enforce", "test_ci_workflow"))
    assert [m for m, _ in marks] == ["test_gate_failure_modes", "test_hook_payloads_w40", "test_phase2_enforce"]


def test_in_ci_an_installed_anchor_fails_the_run_instead_of_skipping(anchor, monkeypatch):
    monkeypatch.setenv("JADE_REQUIRE_APPROVE_MODULES", "1")
    with pytest.raises(pytest.UsageError, match="W-201"):
        cf.pytest_collection_modifyitems(None, items_for("test_gate_failure_modes"))
    assert not marks


def test_in_ci_a_missing_anchor_runs_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(signing, "DEFAULT_ANCHOR", tmp_path / "absent")
    monkeypatch.setenv("JADE_REQUIRE_APPROVE_MODULES", "1")
    cf.pytest_collection_modifyitems(None, items_for("test_gate_failure_modes", "test_hook_payloads_w40"))
    assert not marks


def test_a_value_other_than_one_does_not_turn_the_guard_on(anchor, monkeypatch):
    monkeypatch.setenv("JADE_REQUIRE_APPROVE_MODULES", "0")
    cf.pytest_collection_modifyitems(None, items_for("test_gate_failure_modes"))
    assert [m for m, _ in marks] == ["test_gate_failure_modes"]
