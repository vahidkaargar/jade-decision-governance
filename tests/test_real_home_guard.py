"""A pytest run that has no JADE_HOME / JADE_CONFIG of its own must not reach the human's real JADE state (jade/paths.py).

tests/conftest.py isolates every test under tests/, but a test file run from anywhere else never loads it: a scratch test once wrote a level and a confirmed
answer into the live config and ledger. Every case here runs in a subprocess whose HOME is a temporary directory, so even a failing guard touches nothing real.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def env_for(tmp_path, **extra):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JADE_", "PYTEST_")) and k not in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}
    return {**base, "HOME": str(tmp_path / "fake-home"), "PYTHONPATH": str(ROOT), **extra}


def run(code, tmp_path, **extra):
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env_for(tmp_path, **extra), cwd=tmp_path, check=False)


def test_home_is_refused_while_a_test_runs_without_a_jade_home(tmp_path):
    r = run("from jade import paths; paths.home()", tmp_path, PYTEST_CURRENT_TEST="t.py::x (call)")
    assert r.returncode != 0 and "JADE_HOME" in r.stderr
    assert not (tmp_path / "fake-home" / ".local").exists()


def test_the_config_directory_is_refused_while_a_test_runs_without_a_jade_config(tmp_path):
    r = run("from jade import paths; paths.config_dir()", tmp_path, PYTEST_CURRENT_TEST="t.py::x (call)")
    assert r.returncode != 0 and "JADE_CONFIG" in r.stderr


def test_an_isolated_test_run_is_not_refused(tmp_path):
    r = run("from jade import paths; print(paths.home(), paths.config_dir())", tmp_path,
            PYTEST_CURRENT_TEST="t.py::x (call)", JADE_HOME=str(tmp_path / "h"), JADE_CONFIG=str(tmp_path / "c"))
    assert r.returncode == 0, r.stderr
    assert str(tmp_path / "h") in r.stdout and str(tmp_path / "c") in r.stdout


def test_outside_a_test_run_nothing_changes(tmp_path):
    r = run("from jade import paths; print(paths.home(), paths.config_dir())", tmp_path)
    assert r.returncode == 0, r.stderr
    assert str(tmp_path / "fake-home" / ".local" / "share" / "jade") in r.stdout and str(tmp_path / "fake-home" / ".config" / "jade") in r.stdout


def test_a_scratch_test_file_outside_the_repository_cannot_open_the_real_state(tmp_path):
    """The incident: pytest on a file that lives outside tests/, so tests/conftest.py is never loaded."""
    scratch = tmp_path / "scratch" / "test_scratch.py"
    scratch.parent.mkdir()
    scratch.write_text("def test_it():\n    from jade import ledger\n    ledger.open(timeout=5)\n")
    (tmp_path / "fake-home").mkdir()
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(scratch.parent), str(scratch)], capture_output=True, text=True,
                       timeout=120, env=env_for(tmp_path), cwd=scratch.parent, check=False)
    assert r.returncode != 0 and "refuses to use its real state" in r.stdout + r.stderr, r.stdout[-1500:]
    assert not (tmp_path / "fake-home" / ".local").exists() and not (tmp_path / "fake-home" / ".config").exists()
