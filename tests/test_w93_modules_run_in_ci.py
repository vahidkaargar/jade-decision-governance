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


def test_the_session_start_hook_stops_the_run_in_the_main_process(anchor, monkeypatch):
    monkeypatch.setenv("JADE_REQUIRE_APPROVE_MODULES", "1")
    with pytest.raises(pytest.UsageError, match="W-201"):
        cf.pytest_sessionstart(None)
    monkeypatch.setenv("JADE_REQUIRE_APPROVE_MODULES", "0")
    cf.pytest_sessionstart(None)   # no guard, no error
    monkeypatch.delenv("JADE_REQUIRE_APPROVE_MODULES")
    cf.pytest_sessionstart(None)


def test_in_ci_a_missing_anchor_runs_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(signing, "DEFAULT_ANCHOR", tmp_path / "absent")
    monkeypatch.setenv("JADE_REQUIRE_APPROVE_MODULES", "1")
    cf.pytest_collection_modifyitems(None, items_for("test_gate_failure_modes", "test_hook_payloads_w40"))
    assert not marks


def test_a_value_other_than_one_does_not_turn_the_guard_on(anchor, monkeypatch):
    monkeypatch.setenv("JADE_REQUIRE_APPROVE_MODULES", "0")
    cf.pytest_collection_modifyitems(None, items_for("test_gate_failure_modes"))
    assert [m for m, _ in marks] == ["test_gate_failure_modes"]


# ---- the same guard in a real pytest process, serial and under xdist (CI runs `-n auto`) -----------------------------------------------------------
def real_run(tmp_path, extra: list[str], require: str | None):
    """Run pytest in a throw-away project whose conftest pretends that the real anchor exists and loads the hooks of tests/conftest.py."""
    import os
    import subprocess
    import sys

    (tmp_path / "conftest.py").write_text(
        "import importlib.util\n"
        "from pathlib import Path\n"
        "from jade import signing\n"
        "signing.DEFAULT_ANCHOR = Path(__file__)   # a path that exists: the anchor 'is installed'\n"
        f"spec = importlib.util.spec_from_file_location('repo_conftest', {str(ROOT / 'tests' / 'conftest.py')!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "globals().update({k: v for k, v in vars(m).items() if k.startswith('pytest_')})   # every hook of tests/conftest.py\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_hook_payloads_w40.py").write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("JADE_REQUIRE")}
    if require is not None:
        env["JADE_REQUIRE_APPROVE_MODULES"] = require
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path), *extra, str(tmp_path)],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("extra", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_a_real_run_with_the_guard_on_stops_with_the_w201_message(tmp_path, extra):
    # Under xdist the error used to come out of each worker as an INTERNALERROR (exit 3) and the W-201 text was lost.
    r = real_run(tmp_path, extra, "1")
    out = r.stdout + r.stderr
    assert r.returncode == 4, out
    assert "W-201" in out and "INTERNALERROR" not in out, out


@pytest.mark.parametrize("extra", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_a_real_run_with_the_guard_off_still_skips_the_listed_module(tmp_path, extra):
    r = real_run(tmp_path, extra, None)
    assert r.returncode == 0 and "2 skipped" in r.stdout, r.stdout + r.stderr
