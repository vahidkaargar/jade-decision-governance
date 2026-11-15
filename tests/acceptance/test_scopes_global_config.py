"""PR-C: the merge scope reads the global and system git config too, and fails closed on an env override (jade/scopes.py `_repo_identity`)."""

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from jade import memory, policy, scopes

MERGE = "gh pr merge 940 --squash"
REAL_GLOBAL_CONFIGS = scopes.GLOBAL_CONFIGS   # taken at import, before any fixture replaces it


@pytest.fixture
def short():
    root = Path(tempfile.mkdtemp(prefix="jscopeg-", dir="/tmp")).resolve()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def no_real_git_config(short, monkeypatch):
    """The real ~/.gitconfig and system files must never matter: HOME and XDG point at empty folders, the system list is empty."""
    home = short / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(short / "xdg"))
    monkeypatch.setattr(scopes, "GLOBAL_CONFIGS", REAL_GLOBAL_CONFIGS)   # tests/conftest.py empties it for every other test
    monkeypatch.setattr(scopes, "SYSTEM_CONFIGS", ())
    for name in scopes.GIT_ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def repo(short):
    root = short / "repoA"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text('[core]\n\tbare = false\n[remote "origin"]\n\turl = https://github.com/owner/repoA.git\n')
    return str(root)


def merge_keys(repo):
    a = policy.assess("Bash", {"command": MERGE}, repo)
    return [s.key for s in scopes.offered(a, "Bash", {"command": MERGE}, {}, repo) if s.kind == "merge_project"]


def test_a_global_insteadof_changes_the_key(repo, no_real_git_config):
    before = merge_keys(repo)
    assert len(before) == 1
    (no_real_git_config / ".gitconfig").write_text('[url "git@evil:"]\n\tinsteadOf = https://github.com/\n')
    after = merge_keys(repo)
    assert len(after) == 1 and after != before


def test_a_system_insteadof_changes_the_key(repo, short, monkeypatch):
    before = merge_keys(repo)
    system = short / "gitconfig-system"
    system.write_text('[url "git@evil:"]\n\tinsteadOf = https://github.com/\n')
    monkeypatch.setattr(scopes, "SYSTEM_CONFIGS", (str(system),))
    after = merge_keys(repo)
    assert len(after) == 1 and after != before


def test_the_xdg_config_counts_too(repo, short):
    before = merge_keys(repo)
    (short / "xdg" / "git").mkdir(parents=True)
    (short / "xdg" / "git" / "config").write_text('[url "git@evil:"]\n\tinsteadOf = https://github.com/\n')
    assert merge_keys(repo) not in (before, [])


def test_a_global_file_with_only_user_keeps_the_key(repo, no_real_git_config):
    before = merge_keys(repo)
    (no_real_git_config / ".gitconfig").write_text("[user]\n\tname = A\n\temail = a@b.c\n[core]\n\teditor = vi\n")
    assert merge_keys(repo) == before


@pytest.mark.parametrize("body", [b"[include]\n\tpath = x\n", b'[includeIf "gitdir:~/"]\n\tpath = x\n', b"[weird header\n", b"\xff\xfe"])
def test_a_global_include_or_unknown_header_gives_no_scope(repo, no_real_git_config, body):
    (no_real_git_config / ".gitconfig").write_bytes(body)
    assert merge_keys(repo) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode 000 file")
def test_an_unreadable_global_file_gives_no_scope(repo, no_real_git_config):
    f = no_real_git_config / ".gitconfig"
    f.write_text("[user]\n\tname = A\n")
    f.chmod(0)
    try:
        assert merge_keys(repo) == []
    finally:
        f.chmod(0o600)


def test_an_oversize_global_file_gives_no_scope(repo, no_real_git_config):
    (no_real_git_config / ".gitconfig").write_text("# " + "x" * scopes.CONFIG_MAX + "\n")
    assert merge_keys(repo) == []


def test_a_directory_named_gitconfig_gives_no_scope(repo, no_real_git_config):
    (no_real_git_config / ".gitconfig").mkdir()
    assert merge_keys(repo) == []


@pytest.mark.parametrize("name", ["GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG", "GH_REPO", "GH_HOST"])
def test_an_env_override_gives_no_scope(repo, monkeypatch, name):
    assert len(merge_keys(repo)) == 1
    monkeypatch.setenv(name, "x")
    assert merge_keys(repo) == []


def test_an_empty_env_override_is_ignored(repo, monkeypatch):
    monkeypatch.setenv("GH_REPO", "")
    assert len(merge_keys(repo)) == 1


def test_without_global_or_system_lines_the_identity_is_the_old_one(repo):
    assert scopes._repo_identity(repo) == ['remote "origin"\turl = https://github.com/owner/repoA.git']


def test_global_lines_are_prefixed(repo, no_real_git_config):
    (no_real_git_config / ".gitconfig").write_text('[url "git@evil:"]\n\tinsteadOf = https://github.com/\n')
    ident = scopes._repo_identity(repo)
    assert any(line.startswith("global\t") for line in ident)


def test_keys_for_is_fast(repo):
    a = policy.assess("Bash", {"command": MERGE}, repo)
    t = time.perf_counter()
    for _ in range(1000):
        scopes.keys_for(a, "Bash", {"command": MERGE}, {}, repo)
    assert time.perf_counter() - t < 2.0


def test_a_long_label_is_cut_and_keeps_the_command():
    label = scopes._label("gh pr merge * " + "f" * 200, "/p" * 150)
    assert len(label) <= scopes.LABEL_MAX
    assert label.startswith("gh pr merge *")
    assert "…" in label


def test_the_public_memory_names():
    assert memory.INLINE.search("sh -c x") and memory.MERGE.match("gh pr merge 1")
    assert callable(memory.valid_since)
    assert not any(hasattr(memory, n) for n in ("_INLINE", "_MERGE", "_valid_since"))
