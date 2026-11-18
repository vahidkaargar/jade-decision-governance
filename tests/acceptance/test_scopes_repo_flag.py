"""PR-C wave 3: the merge scope needs `--repo HOST/OWNER/NAME`, equal to a remote of the project (jade/scopes.py).

`gh pr merge N --repo HOST/OWNER/NAME` talks to the API of HOST about OWNER/NAME. No environment variable, shell startup file or git config can change that target,
so the key holds the project and that repository, and nothing else about the machine.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from jade import memory, policy, scopes

REPO = "github.com/owner/repoA"
MERGE = f"gh pr merge 940 --repo {REPO} --squash"


@pytest.fixture
def short():
    root = Path(tempfile.mkdtemp(prefix="jscoper-", dir="/tmp")).resolve()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def make_repo(root, *urls, extra=""):
    (root / ".git").mkdir(parents=True)
    body = "[core]\n\tbare = false\n"
    for i, url in enumerate(urls):
        body += f'[remote "r{i}"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/r{i}/*\n'
    (root / ".git" / "config").write_text(body + extra)
    return str(root)


@pytest.fixture
def repo(short):
    return make_repo(short / "repoA", "https://github.com/owner/repoA.git")


def merge_keys(cmd, cwd):
    a = policy.assess("Bash", {"command": cmd}, cwd)
    return [s.key for s in scopes.offered(a, "Bash", {"command": cmd}, {}, cwd) if s.kind == "merge_project"]


def merge_scope(cmd, cwd):
    a = policy.assess("Bash", {"command": cmd}, cwd)
    got = [s for s in scopes.offered(a, "Bash", {"command": cmd}, {}, cwd) if s.kind == "merge_project"]
    return got[0] if got else None


def test_repo_flag_matching_origin_gives_a_scope(repo):
    s = merge_scope(MERGE, repo)
    assert s is not None
    assert s.label == f"gh pr merge * --repo {REPO.lower()} in {repo}"


@pytest.mark.parametrize("flags", [
    f"--repo {REPO}", f"--repo={REPO}", f"-R {REPO}", f"-R{REPO}", f"-R={REPO}", f"--squash --repo {REPO}", f"--repo {REPO} --squash",
    f"--subject x --repo {REPO} --body y", "--repo GitHub.com/Owner/RepoA", f"--repo '{REPO}'", f'--repo "{REPO}"'])
def test_each_spelling_of_the_repo_flag_in_either_order(repo, flags):
    assert len(merge_keys(f"gh pr merge 940 {flags}", repo)) == 1


def test_all_spellings_share_one_key(repo):
    keys = {merge_keys(f"gh pr merge {n} {f}", repo)[0] for n, f in [(1, f"--repo {REPO}"), (2, f"-R{REPO}"), (3, f"--repo={REPO} --squash"), (4, f"--repo {REPO.upper()}")]}
    assert len(keys) == 1


@pytest.mark.parametrize("cmd", [
    "gh pr merge 940 --squash", "gh pr merge 940 --repo owner/repoA", "gh pr merge 940 -R owner/repoA", f"gh pr merge 940 --repo {REPO} --repo {REPO}",
    f"gh pr merge 940 --repo {REPO} -R {REPO}", "gh pr merge 940 --repo", f"gh pr merge 940 --repo --squash {REPO}", "gh pr merge 940 --repo github.com/owner",
    "gh pr merge 940 --repo github.com/owner/repoA/extra", "gh pr merge 940 --repo github.com/owner/other", "gh pr merge 940 --repo other.com/owner/repoA",
    f"gh pr merge 940 --repo {REPO} --delete-branch", f"gh pr merge 940 --repo {REPO} --admin", f"gh pr merge 940 --repo {REPO} --body-file x",
    "gh pr merge 940 --repo github.com/../repoA", "gh pr merge 940 --repo github.com/./repoA", "gh pr merge 940 --repo https://github.com/owner/repoA"])
def test_no_merge_scope_for(repo, cmd):
    assert merge_keys(cmd, repo) == []


@pytest.mark.parametrize("value", ["github.com/$X/repoA", "github.com/owner/repo$A", "github.com/ow*r/repoA", "github.com/owner/`x`", "github.com/~/repoA",
                                    "github.com/{a,b}/repoA", "github.com/owner/repo[A]", "github.com/owner/repo?"])
def test_an_expansion_character_in_the_value_gives_no_scope(repo, value):
    assert merge_keys(f"gh pr merge 940 --repo {value}", repo) == []


def test_old_form_gets_no_merge_scope_but_the_kind_scopes_are_unaffected(repo):
    cmd = "git reset --hard HEAD~1"
    a = policy.assess("Bash", {"command": cmd}, repo)
    kinds = [s.kind for s in scopes.offered(a, "Bash", {"command": cmd}, {}, repo)]
    assert kinds == ["kind_project", "kind_any"]
    assert merge_keys("gh pr merge 940 --squash", repo) == []


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repoA", "https://github.com/owner/repoA.git", "ssh://git@github.com/owner/repoA.git", "ssh://git@github.com:22/owner/repoA",
    "git@github.com:owner/repoA.git", "git@github.com:owner/repoA", "HTTPS://GitHub.com/Owner/RepoA.git"])
def test_each_github_style_remote_form_matches(short, url):
    assert len(merge_keys(MERGE, make_repo(short / "r", url))) == 1


@pytest.mark.parametrize("url", ["https://github.com/owner/repoB.git", "https://example.com/owner/repoA.git", "/srv/git/repoA.git", "git@github.com:owner/repoA.git/x",
                                 "https://github.com/owner/repoA.git.evil", "https://github.com/owner", "file:///x/owner/repoA"])
def test_a_remote_that_is_not_the_named_repository_gives_no_scope(short, url):
    assert merge_keys(MERGE, make_repo(short / "r", url)) == []


def test_any_remote_may_match(short):
    cwd = make_repo(short / "r", "https://github.com/fork/repoA.git", "git@github.com:owner/repoA.git")
    assert len(merge_keys(MERGE, cwd)) == 1


def test_no_remote_gives_no_scope(short):
    assert merge_keys(MERGE, make_repo(short / "r")) == []


def test_two_different_repo_values_give_different_keys(short):
    cwd = make_repo(short / "r", "https://github.com/owner/repoA.git", "https://github.com/owner/repoB.git")
    a, b = merge_keys(MERGE, cwd), merge_keys("gh pr merge 940 --repo github.com/owner/repoB", cwd)
    assert len(a) == len(b) == 1 and a != b


def test_the_same_repo_in_another_project_gives_another_key(short, repo):
    other = make_repo(short / "other", "https://github.com/owner/repoA.git")
    assert merge_keys(MERGE, repo) != merge_keys(MERGE, other)


@pytest.mark.parametrize("name,value", [("GH_REPO", "evil/repo"), ("GH_HOST", "evil.example"), ("GIT_DIR", "/x"), ("GIT_CONFIG_GLOBAL", "/x"), ("GH_CONFIG_DIR", "/x")])
def test_the_environment_does_not_change_the_key(repo, monkeypatch, name, value):
    before = merge_keys(MERGE, repo)
    monkeypatch.setenv(name, value)
    assert len(before) == 1 and merge_keys(MERGE, repo) == before


def test_a_startup_file_and_a_global_config_do_not_change_the_key(repo, short, monkeypatch):
    before = merge_keys(MERGE, repo)
    home = short / "home"
    home.mkdir()
    (home / ".zshenv").write_text("export GH_REPO=evil/repo\n")
    (home / ".gitconfig").write_text('[url "git@evil:"]\n\tinsteadOf = https://github.com/\n')
    monkeypatch.setenv("HOME", str(home))
    assert merge_keys(MERGE, repo) == before


def test_an_insteadof_in_the_repo_config_does_not_change_the_key(short, repo):
    before = merge_keys(MERGE, repo)
    cwd = make_repo(short / "same", "https://github.com/owner/repoA.git", extra='[url "git@evil:"]\n\tinsteadOf = https://github.com/\n')
    assert len(merge_keys(MERGE, cwd)) == 1
    assert before != merge_keys(MERGE, cwd)   # another project, another key
    assert scopes._remote_urls(cwd) == ["https://github.com/owner/repoA.git"]


def test_the_removed_machine_state_readers_are_gone():
    for name in ("GLOBAL_CONFIGS", "SYSTEM_CONFIGS", "GIT_ENV_OVERRIDES", "SHELL_STARTUP_FILES", "_shell_identity", "_repo_identity"):
        assert not hasattr(scopes, name), name


@pytest.mark.parametrize("body", ["[include]\n\tpath = x\n", '[includeIf "gitdir:~/"]\n\tpath = x\n', "[weird header\n"])
def test_a_repo_config_that_includes_or_has_an_unknown_header_gives_no_scope(short, body):
    assert merge_keys(MERGE, make_repo(short / "r", "https://github.com/owner/repoA.git", extra=body)) == []


def test_an_oversize_repo_config_gives_no_scope(short):
    assert merge_keys(MERGE, make_repo(short / "r", "https://github.com/owner/repoA.git", extra="# " + "x" * scopes.CONFIG_MAX + "\n")) == []


def test_a_per_worktree_config_gives_no_scope(short):
    cwd = make_repo(short / "r", "https://github.com/owner/repoA.git")
    (Path(cwd) / ".git" / "config.worktree").write_text("[remote]\n")
    assert merge_keys(MERGE, cwd) == []


def test_a_fifo_repo_config_gives_no_scope_and_does_not_block(short):
    root = short / "r"
    (root / ".git").mkdir(parents=True)
    os.mkfifo(root / ".git" / "config")
    t = time.perf_counter()
    assert merge_keys(MERGE, str(root)) == []
    assert time.perf_counter() - t < 2.0


def test_a_repo_config_that_is_not_utf8_gives_no_scope(short):
    cwd = make_repo(short / "r", "https://github.com/owner/repoA.git")
    (Path(cwd) / ".git" / "config").write_bytes(b"\xff\xfe")
    assert merge_keys(MERGE, cwd) == []


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


def test_the_covers_text_names_the_repo_flag():
    text = scopes.COVERS["merge_project"]
    assert "--repo" in text and "repository" in text
    for sentence in text.split("."):
        assert len(sentence.split()) <= 20


def test_the_public_memory_names():
    assert memory.INLINE.search("sh -c x") and memory.MERGE.match("gh pr merge 1")
    assert callable(memory.valid_since)
    assert not any(hasattr(memory, n) for n in ("_INLINE", "_MERGE", "_valid_since"))


# ------------------------------------------------------------------ wave 4: RR2-1 (fake separators) and RR2-3 (config line rules)

FAKE_SEPARATORS = [" ", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x1f", "\x85", " ", "　", " "]


@pytest.mark.parametrize("sep", FAKE_SEPARATORS, ids=[hex(ord(s)) for s in FAKE_SEPARATORS])
def test_a_fake_separator_before_repo_gives_no_merge_scope(repo, sep):
    # zsh and bash do not split on these, so gh would get `--body` with one long value and no --repo at all.
    assert merge_keys(f"gh pr merge 5 --squash --body x{sep}--repo{sep}{REPO}", repo) == []
    assert merge_keys(f"gh pr merge 5 --repo{sep}{REPO} --squash", repo) == []
    assert merge_keys(f"gh pr merge 5 --squash{sep}--repo {REPO}", repo) == []
    assert merge_keys(f"gh pr merge 5 --repo {REPO}{sep}--squash", repo) == []


def test_a_fake_separator_after_a_leading_cd_gives_no_merge_scope(repo):
    assert merge_keys(f"cd {repo} && gh pr merge 5 --squash --body x --repo {REPO}", "/") == []
    assert len(merge_keys(f"cd {repo} && gh pr merge 5 --squash --repo {REPO}", "/")) == 1


def test_a_non_ascii_character_anywhere_gives_no_merge_scope(repo):
    assert merge_keys(f"gh pr merge 5 --repo {REPO} --subject 'café'", repo) == []


def test_normal_space_and_tab_still_give_the_scope(repo):
    assert len(merge_keys(f"gh pr merge 5 --squash --repo {REPO}", repo)) == 1
    assert len(merge_keys(f"gh pr merge\t5\t--squash \t--repo {REPO}", repo)) == 1


def test_a_fake_separator_after_cd_does_not_name_the_project(repo, short):
    other = make_repo(short / "other", "https://github.com/owner/repoA.git")
    call = f"gh pr merge 5 --repo {REPO}"
    # `cd PATH<NBSP>&&` and `cd<NBSP>PATH &&`: the shell never cd's to PATH, so no project is named and no merge scope exists
    for cmd in (f"cd {repo}\u00a0&& {call}", f"cd\u00a0{repo} && {call}"):
        assert scopes.project_of(cmd, other) is None
        assert merge_keys(cmd, other) == []
        assert merge_keys(cmd, repo) == []
    # `&&<NBSP>gh`: the cd is real and names the project, but the program word is not `gh`
    cmd = f"cd {repo} &&\u00a0{call}"
    assert scopes.project_of(cmd, other) == repo
    assert merge_keys(cmd, other) == [] and merge_keys(cmd, repo) == []
    assert scopes.project_of(f"cd {repo} && {call}", other) == repo


def test_a_backslash_in_a_word_gives_no_merge_scope(repo):
    # bash and zsh pass `x --repo github.com/...` as ONE word after `x\ `; the skeleton shows it as separate words
    assert merge_keys(f"gh pr merge 5 --squash --body x\\ --repo {REPO}", repo) == []
    assert merge_keys(f"gh pr merge 5 --repo {REPO} --squash --body x\\ y", repo) == []
    assert len(merge_keys(f"gh pr merge 5 --repo {REPO} --squash", repo)) == 1



def test_a_line_ending_with_a_backslash_gives_no_scope(short):
    body = 'x = y \\\n[remote "z"]\n'
    assert merge_keys(MERGE, make_repo(short / "r", "https://github.com/owner/repoA.git", extra="[core]\n\t" + body)) == []
    assert merge_keys(MERGE, make_repo(short / "r2", "https://github.com/owner/repoA.git", extra="[core]\n\tx = y\\\n")) == []


@pytest.mark.parametrize("ctl", ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "])
def test_the_repo_config_is_split_on_newline_only(short, ctl):
    # git sees one line `fetch = y<ctl>url = ...`; Python splitlines() would see a second line.
    root = short / "r"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text(f'[remote "o"]\n\tfetch = y{ctl}url = https://github.com/owner/repoA.git\n')
    assert scopes._remote_urls(str(root)) == []
    assert merge_keys(MERGE, str(root)) == []


def test_a_crlf_config_still_reads(short):
    root = short / "r"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_bytes(b'[remote "o"]\r\n\turl = https://github.com/owner/repoA.git\r\n')
    assert scopes._remote_urls(str(root)) == ["https://github.com/owner/repoA.git"]
    assert len(merge_keys(MERGE, str(root))) == 1
