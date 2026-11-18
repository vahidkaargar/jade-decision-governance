"""V6-34: scripts/check_release_tag.sh. The release workflow runs it first, on the tagged commit, with the full history.

The tests build a small repository with a bare `origin` in a temporary folder and run the real script there. This is the dry run of a tag on a
fork, done locally: a tag on a side branch fails, a tag with no CHANGELOG section fails, and a good tag passes.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_tag.sh"

CHANGELOG_OK = "# Changelog\n\n## [Unreleased]\n\n## [0.7.0]\n\n### Added\n- A thing.\n\n## [0.6.1]\n\n- Older.\n"


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


@pytest.fixture(scope="module")
def template(tmp_path_factory, env):
    """A bare origin and a clone of it whose main branch holds one commit with CHANGELOG_OK. Built once; each test works on a copy."""
    base = tmp_path_factory.mktemp("template")
    origin = base / "origin.git"
    git(base, env, "init", "-q", "--bare", "-b", "main", str(origin))
    work = base / "work"
    git(base, env, "clone", "-q", str(origin), str(work))
    git(work, env, "checkout", "-q", "-b", "main")
    (work / "CHANGELOG.md").write_text(CHANGELOG_OK)
    git(work, env, "add", "CHANGELOG.md")
    git(work, env, "commit", "-q", "-m", "release notes")
    git(work, env, "push", "-q", "origin", "main")
    return base


@pytest.fixture
def repo(tmp_path, env, template):
    shutil.copytree(template, tmp_path / "t")
    work = tmp_path / "t" / "work"
    git(work, env, "remote", "set-url", "origin", str(tmp_path / "t" / "origin.git"))
    return work


def run(repo: Path, env: dict, tag: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT), tag], cwd=repo, env=env, capture_output=True, text=True)


def test_a_tag_on_main_with_a_changelog_section_passes(repo, env):
    r = run(repo, env, "v0.7.0")
    assert r.returncode == 0, r.stderr
    assert "origin/main" in r.stdout


def test_a_tag_on_a_side_branch_fails(repo, env):
    git(repo, env, "checkout", "-q", "-b", "side")
    (repo / "x.txt").write_text("not on main\n")
    git(repo, env, "add", "x.txt")
    git(repo, env, "commit", "-q", "-m", "side work")
    git(repo, env, "push", "-q", "origin", "side")
    r = run(repo, env, "v0.7.0")
    assert r.returncode == 1
    assert "not on origin/main" in r.stderr


def test_a_tag_on_a_commit_that_main_has_merged_passes(repo, env):
    git(repo, env, "checkout", "-q", "-b", "side")
    (repo / "x.txt").write_text("merged later\n")
    git(repo, env, "add", "x.txt")
    git(repo, env, "commit", "-q", "-m", "side work")
    git(repo, env, "checkout", "-q", "main")
    git(repo, env, "merge", "-q", "--ff-only", "side")
    git(repo, env, "push", "-q", "origin", "main")
    assert run(repo, env, "v0.7.0").returncode == 0


def test_a_commit_that_origin_main_does_not_have_yet_fails(repo, env):
    (repo / "y.txt").write_text("local only\n")
    git(repo, env, "add", "y.txt")
    git(repo, env, "commit", "-q", "-m", "local only")   # on local main, never pushed
    r = run(repo, env, "v0.7.0")
    assert r.returncode == 1 and "not on origin/main" in r.stderr


def test_a_tag_with_no_changelog_section_fails(repo, env):
    r = run(repo, env, "v0.8.0")
    assert r.returncode == 1
    assert "CHANGELOG.md has no section for 0.8.0" in r.stderr


def test_an_empty_changelog_section_fails(repo, env):
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [0.7.0]\n\n\n## [0.6.1]\n\n- Older.\n")
    git(repo, env, "commit", "-q", "-am", "empty section")
    git(repo, env, "push", "-q", "origin", "main")
    r = run(repo, env, "v0.7.0")
    assert r.returncode == 1 and "is empty" in r.stderr


def test_a_missing_changelog_file_fails(repo, env):
    git(repo, env, "rm", "-q", "CHANGELOG.md")
    git(repo, env, "commit", "-q", "-m", "no changelog")
    git(repo, env, "push", "-q", "origin", "main")
    r = run(repo, env, "v0.7.0")
    assert r.returncode == 1 and "CHANGELOG.md" in r.stderr


def test_a_prefix_of_another_version_is_not_a_section(repo, env):
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [0.7.10]\n\n- Ten.\n")
    git(repo, env, "commit", "-q", "-am", "0.7.10 only")
    git(repo, env, "push", "-q", "origin", "main")
    assert run(repo, env, "v0.7.1").returncode == 1


def test_no_origin_main_fails_because_the_history_is_not_there(repo, env):
    git(repo, env, "update-ref", "-d", "refs/remotes/origin/main")
    r = run(repo, env, "v0.7.0")
    assert r.returncode == 1 and "origin/main" in r.stderr


def test_a_shallow_checkout_fails_because_the_check_could_be_wrong(repo, env, tmp_path):
    shallow = tmp_path / "shallow"
    git(tmp_path, env, "clone", "-q", "--depth", "1", f"file://{repo.parent / 'origin.git'}", str(shallow))
    r = run(shallow, env, "v0.7.0")
    assert r.returncode == 1 and "shallow" in r.stderr


@pytest.mark.parametrize("tag", ["", "0.7.0", "v1", "v0.7", "vx.y.z", "v0.7.0/../x", "v0.7.0 "])
def test_a_tag_that_is_not_a_version_tag_fails(tmp_path, env, tag):
    r = run(tmp_path, env, tag)   # the tag is refused before git is asked anything, so a plain folder is enough
    assert r.returncode == 1 and "version tag" in r.stderr


@pytest.mark.parametrize("version", ["0.8.0rc1", "0.8.0-rc.1"])   # docs/09 asks for the first spelling; the second is accepted too
def test_a_prerelease_tag_is_a_version_tag(repo, env, version):
    (repo / "CHANGELOG.md").write_text(f"# Changelog\n\n## [{version}]\n\n- Candidate.\n")
    git(repo, env, "commit", "-q", "-am", "rc")
    git(repo, env, "push", "-q", "origin", "main")
    assert run(repo, env, f"v{version}").returncode == 0
