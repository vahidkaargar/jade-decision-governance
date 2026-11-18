"""V6-34: scripts/scan_new_changes.sh runs gitleaks over the new commits with the repository's .gitleaks.toml.

The tests build a scratch repository (the dry run of a scratch pull request) and run the real script and the real `.gitleaks.toml` of this repository.
A fake secret outside tests/ and evals/ must fail the scan. The same text under tests/ must pass, because the config allows fake credentials there on purpose,
so the first test would pass for the wrong reason if the secret were planted only under tests/. They skip when `gitleaks` is not installed (CI installs it).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scan_new_changes.sh"
GITLEAKS = shutil.which("gitleaks")

pytestmark = pytest.mark.skipif(GITLEAKS is None, reason="gitleaks is not installed")

FAKE = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5"   # built here so no secret-shaped text sits in this file; 36 characters after ghp_


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    return {
        "PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


def git(cwd: Path, env: dict, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path, env):
    work = tmp_path / "work"
    work.mkdir()
    git(work, env, "init", "-q", "-b", "main")
    (work / "README.md").write_text("hello\n")
    git(work, env, "add", "README.md")
    git(work, env, "commit", "-q", "-m", "base")
    return work


def commit_file(repo: Path, env: dict, rel: str, text: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, env, "add", rel)
    git(repo, env, "commit", "-q", "-m", f"add {rel}")
    return git(repo, env, "rev-parse", "HEAD")


def scan(repo: Path, env: dict, base: str, head: str = "HEAD") -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT), base, head], cwd=repo, env=env, capture_output=True, text=True)


def test_a_planted_secret_outside_tests_and_evals_fails_the_scan(repo, env):
    base = git(repo, env, "rev-parse", "HEAD")
    commit_file(repo, env, "docs/notes.md", f"token: {FAKE}\n")
    r = scan(repo, env, base)
    assert r.returncode == 1, r.stdout + r.stderr
    assert FAKE not in r.stdout + r.stderr   # the finding is redacted


def test_the_same_text_under_tests_or_evals_passes_because_the_config_allows_it(repo, env):
    base = git(repo, env, "rev-parse", "HEAD")
    commit_file(repo, env, "tests/fixture.txt", f"token: {FAKE}\n")
    commit_file(repo, env, "evals/case.txt", f"token: {FAKE}\n")
    r = scan(repo, env, base)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_clean_change_passes(repo, env):
    base = git(repo, env, "rev-parse", "HEAD")
    commit_file(repo, env, "docs/notes.md", "nothing secret here\n")
    assert scan(repo, env, base).returncode == 0


def test_only_the_new_commits_are_read(repo, env):
    commit_file(repo, env, "docs/old.md", f"token: {FAKE}\n")   # already in history before the base
    base = commit_file(repo, env, "docs/mid.md", "clean\n")
    commit_file(repo, env, "docs/new.md", "clean too\n")
    r = scan(repo, env, base)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"{base}..HEAD" in r.stdout


@pytest.mark.parametrize("base", ["", "0" * 40, "1" * 40])
def test_no_usable_base_scans_the_last_commits_instead(repo, env, base):
    commit_file(repo, env, "docs/notes.md", f"token: {FAKE}\n")
    r = scan(repo, env, base)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "-n 20" in r.stdout


def test_a_missing_gitleaks_is_an_error_not_a_pass(repo, env):
    r = subprocess.run(["/bin/sh", str(SCRIPT), "HEAD"], cwd=repo, env={**env, "PATH": "/nonexistent"}, capture_output=True, text=True)
    assert r.returncode == 2 and "gitleaks is not installed" in r.stderr


def test_the_repository_config_still_allows_only_tests_and_evals():
    text = (ROOT / ".gitleaks.toml").read_text()
    assert "useDefault = true" in text
    assert "^tests/" in text and "^evals/" in text
    assert text.count("paths = [") == 1
