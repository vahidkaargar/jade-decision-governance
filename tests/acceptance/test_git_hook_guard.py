"""Round H, part 2: the standalone pre-push guard (structural SI-08) and `jade git-hook check`.

Everything runs in throwaway repositories and a bare remote under tmp_path, with the user's git configuration switched off. Nothing is installed anywhere
else: the hook is written into the throwaway repository by the test, the way a human would.
"""

import os
import shutil
import stat
import subprocess

import pytest

from jade import cli, githook, policy

ZERO = "0" * 40


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """A git that reads no user or system configuration and has an identity."""
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.test", "PATH": os.environ["PATH"]}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def git(cwd, *args, check=True, input=None):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check, input=input)


def install(repo, text=None, mode=0o755):
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(githook.script() if text is None else text)
    hook.chmod(mode)
    return hook


@pytest.fixture
def world(tmp_path, git_env):
    """A bare remote whose default branch is main, a clone with the guard installed, a commit on main and a feature branch."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "remote", "add", "origin", str(remote))
    (work / "a.txt").write_text("a")
    git(work, "add", "a.txt")
    git(work, "commit", "-q", "-m", "one")
    # the seed push happens before the guard exists (a human decision outside this test)
    git(work, "push", "-q", "origin", "main")
    git(work, "remote", "set-head", "origin", "main")
    git(work, "checkout", "-q", "-b", "feat")
    (work / "b.txt").write_text("b")
    git(work, "add", "b.txt")
    git(work, "commit", "-q", "-m", "two")
    install(work)
    return work, remote


def remote_has(remote, branch):
    return subprocess.run(["git", "-C", str(remote), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"], capture_output=True).returncode == 0


# ---- the hook against real pushes to a bare remote ---------------------------------------------------------------------
@pytest.mark.parametrize("refspec", [
    "main", "+main", "HEAD:main", "HEAD:refs/heads/main", "+HEAD:main", "feat:main", "feat:refs/heads/main", "+feat:refs/heads/main", "HEAD:Main", "HEAD:MAIN",
    "refs/heads/feat:refs/heads/main", "@:main",
])
def test_every_spelling_of_a_push_that_updates_the_default_branch_is_refused(world, refspec):
    work, remote = world
    if refspec in ("main", "+main"):   # a local branch of that name, ahead of the remote one, pushed to the same name
        git(work, "branch", "-f", "main", "feat")
    r = git(work, "push", "origin", refspec, check=False)
    assert r.returncode != 0 and "jade pre-push guard: refusing to push the default branch" in r.stderr
    old = git(remote, "rev-parse", "main").stdout.strip()
    assert old != git(work, "rev-parse", "feat").stdout.strip()   # the remote main did not move


def test_the_current_branch_pushed_while_it_is_main_is_refused(world):
    work, remote = world
    git(work, "checkout", "-q", "main")
    git(work, "config", "push.default", "current")
    (work / "c.txt").write_text("c")
    git(work, "add", "c.txt")
    git(work, "commit", "-q", "-m", "three")
    for args in (("push",), ("push", "origin"), ("push", "origin", "HEAD"), ("push", "-u", "origin", "HEAD")):
        r = git(work, *args, check=False)
        assert r.returncode != 0 and "refusing" in r.stderr, args


@pytest.mark.parametrize("args", [("push", "origin", ":main"), ("push", "origin", "--delete", "main"), ("push", "origin", "+:main")])
def test_a_delete_of_the_default_branch_is_refused_and_says_so(world, args):
    work, remote = world
    r = git(work, *args, check=False)
    assert r.returncode != 0 and "refusing to delete the default branch 'main'" in r.stderr
    assert remote_has(remote, "main")


@pytest.mark.parametrize("refspec", ["feat", "HEAD", "feat:feature-2", "+feat", "HEAD:refs/heads/topic/x", "main:work-copy"])
def test_a_push_to_any_other_branch_goes_through(world, refspec):
    work, remote = world
    r = git(work, "push", "origin", refspec, check=False)
    assert r.returncode == 0, r.stderr


def test_tags_and_a_branch_named_like_main_inside_a_path_are_not_the_default_branch(world):
    work, remote = world
    git(work, "tag", "main-v1")
    git(work, "tag", "v2")
    assert git(work, "push", "origin", "main-v1", "v2", check=False).returncode == 0
    assert git(work, "push", "origin", "feat:team/main", check=False).returncode == 0
    assert git(work, "push", "origin", "refs/tags/v2:refs/tags/main", check=False).returncode == 0   # a tag named main is not a branch


def test_master_is_protected_too(world):
    work, remote = world
    r = git(work, "push", "origin", "HEAD:master", check=False)
    assert r.returncode != 0 and "'master'" in r.stderr and not remote_has(remote, "master")


def test_a_default_branch_that_is_neither_main_nor_master_is_read_from_the_remote_head(world):
    work, remote = world
    git(work, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    git(work, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    r = git(work, "push", "origin", "HEAD:trunk", check=False)
    assert r.returncode != 0 and "default branch 'trunk'" in r.stderr and not remote_has(remote, "trunk")
    r = git(work, "push", "origin", "HEAD:refs/heads/Trunk", check=False)
    assert r.returncode != 0
    assert git(work, "push", "origin", "HEAD:develop", check=False).returncode == 0
    assert git(work, "push", "origin", "HEAD:main", check=False).returncode != 0   # main stays protected whatever the remote head says


def test_a_push_to_a_url_instead_of_a_remote_name_still_stops_main(world):
    work, remote = world
    r = git(work, "push", str(remote), "HEAD:main", check=False)
    assert r.returncode != 0 and "refusing" in r.stderr
    assert git(work, "push", str(remote), "HEAD:other", check=False).returncode == 0


def test_a_push_with_no_updates_and_a_push_of_nothing_new_is_not_refused(world):
    work, remote = world
    git(work, "push", "-q", "origin", "feat")
    assert git(work, "push", "origin", "feat", check=False).returncode == 0   # up to date: nothing on stdin


def test_no_verify_is_the_human_way_around_and_nothing_in_the_hook_offers_another(world):
    work, remote = world
    assert git(work, "push", "--no-verify", "origin", "HEAD:main", check=False).returncode == 0   # `--no-verify` skips the hook: R-17 rates the command high
    assert "JADE_" not in githook.script() and "ALLOW" not in githook.script()   # no environment switch an agent could set


# ---- simulated stdin: the script alone ---------------------------------------------------------------------------------
def run_script(repo, lines, *args, shell="sh", path=None):
    script = repo.parent / "guard.sh"
    script.write_text(githook.script())
    env = {**os.environ, **({"PATH": path} if path else {})}
    return subprocess.run([shell, str(script), *args], cwd=repo, input="".join(f"{ln}\n" for ln in lines), capture_output=True, text=True, env=env)


SHELLS = [s for s in ("sh", "dash", "bash") if shutil.which(s)]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("line", [
    f"refs/heads/feat {'a' * 40} refs/heads/main {'b' * 40}", f"(delete) {ZERO} refs/heads/main {'b' * 40}", f"HEAD {'a' * 40} refs/heads/master {ZERO}",
    f"refs/heads/x {'a' * 40} refs/heads/MAIN {ZERO}",
])
def test_the_script_refuses_these_stdin_lines_under_every_posix_shell(world, shell, line):
    work, remote = world
    r = run_script(work, [line], "origin", str(remote), shell=shell)
    assert r.returncode == 1 and "refusing to" in r.stderr and "feature branch" in r.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_the_script_passes_other_refs_and_an_empty_stdin(world, shell):
    work, remote = world
    ok = [f"refs/heads/feat {'a' * 40} refs/heads/feat {ZERO}", f"refs/tags/v1 {'a' * 40} refs/tags/main {ZERO}", f"refs/heads/main {'a' * 40} refs/heads/team/main {ZERO}",
          f"(delete) {ZERO} refs/heads/old {'b' * 40}", f"refs/heads/x {'a' * 40} refs/for/main {ZERO}"]
    assert run_script(work, ok, "origin", str(remote), shell=shell).returncode == 0
    assert run_script(work, [], "origin", str(remote), shell=shell).returncode == 0


def test_a_refused_ref_among_allowed_ones_refuses_the_whole_push(world):
    work, remote = world
    lines = [f"refs/heads/feat {'a' * 40} refs/heads/feat {ZERO}", f"refs/heads/feat {'a' * 40} refs/heads/main {'b' * 40}"]
    assert run_script(work, lines, "origin", str(remote)).returncode == 1


def test_odd_remote_names_and_branch_names_never_break_the_script(world):
    work, remote = world
    for remote_arg in ("", "origin;touch x", "$(id)", "a b", "https://example.test/x.git", "../../etc"):
        r = run_script(work, [f"refs/heads/f {'a' * 40} refs/heads/feat-$(id)`x` {ZERO}"], remote_arg, "u")
        assert r.returncode == 0, (remote_arg, r.stderr)
    assert not (work / "x").exists()


def test_the_script_does_not_need_jade_to_run(world, tmp_path):
    work, remote = world
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("git", "sh", "tr", "cat", "cmp", "printf"):
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    assert shutil.which("jade", path=str(bin_dir)) is None
    line = f"refs/heads/feat {'a' * 40} refs/heads/main {'b' * 40}"
    assert run_script(work, [line], "origin", "u", shell=shutil.which("sh"), path=str(bin_dir)).returncode == 1
    text = githook.script()
    assert text.startswith("#!/bin/sh\n") and githook.MARKER in text
    assert [ln for ln in text.splitlines() if not ln.lstrip().startswith("#") and "jade" in ln.replace("jade pre-push guard", "")] == []


def test_the_repositorys_own_pre_push_hook_still_runs_when_the_guard_is_installed_through_hooks_path(world, tmp_path):
    """Installed through core.hooksPath the guard replaces .git/hooks/pre-push; it runs that hook after its own check."""
    work, remote = world
    shared = tmp_path / "shared-hooks"
    shared.mkdir()
    guard = shared / "pre-push"
    guard.write_text(githook.script())
    guard.chmod(0o755)
    own = work / ".git" / "hooks" / "pre-push"
    own.write_text("#!/bin/sh\ncat > /dev/null\necho own-hook-ran >&2\nexit 7\n")
    own.chmod(0o755)
    git(work, "config", "core.hooksPath", str(shared))
    r = git(work, "push", "origin", "feat", check=False)
    assert r.returncode != 0 and "own-hook-ran" in r.stderr and "refusing" not in r.stderr   # the guard let it through, the repository's own hook decided (git reports 1 for any hook failure)
    r = git(work, "push", "origin", "HEAD:main", check=False)
    assert r.returncode == 1 and "refusing" in r.stderr and "own-hook-ran" not in r.stderr   # the guard first


def test_the_guard_installed_as_the_repository_hook_does_not_run_itself_twice(world):
    work, remote = world
    r = git(work, "push", "origin", "feat", check=False)
    assert r.returncode == 0, r.stderr


# ---- `jade git-hook check` ---------------------------------------------------------------------------------------------
def check_cli(repo, capsys):
    rc = cli.main(["git-hook", "check", str(repo)])
    return rc, capsys.readouterr().out


def test_check_reports_a_missing_hook(world, capsys):
    work, _ = world
    (work / ".git" / "hooks" / "pre-push").unlink()
    rc, out = check_cli(work, capsys)
    assert rc == 1 and "pre-push: missing" in out and out.rstrip().endswith("NOT GUARDED")


def test_check_reports_the_guard_and_its_directory(world, capsys):
    work, _ = world
    rc, out = check_cli(work, capsys)
    assert rc == 0 and "JADE guard v1 installed" in out and "(default)" in out and out.rstrip().endswith("guarded")
    assert "differs" not in out


def test_check_sees_an_older_or_edited_copy_a_custom_hook_and_a_hook_git_will_skip(world, capsys):
    work, _ = world
    hook = work / ".git" / "hooks" / "pre-push"
    install(work, githook.script() + "# local edit\n")
    rc, out = check_cli(work, capsys)
    assert rc == 0 and "differs from `jade git-hook script`" in out
    install(work, "#!/bin/sh\nexit 0\n")
    rc, out = check_cli(work, capsys)
    assert rc == 1 and "not the JADE guard" in out
    install(work, mode=0o644)
    rc, out = check_cli(work, capsys)
    assert rc == 1 and "not executable" in out
    assert not os.access(hook, os.X_OK) and stat.S_IMODE(hook.stat().st_mode) == 0o644


def test_check_follows_core_hooks_path_the_way_husky_and_a_global_setting_use_it(world, tmp_path, capsys):
    work, _ = world
    elsewhere = tmp_path / "other-hooks"
    elsewhere.mkdir()
    git(work, "config", "core.hooksPath", str(elsewhere))
    rc, out = check_cli(work, capsys)
    assert rc == 1 and str(elsewhere) in out and "(core.hooksPath," in out and "missing" in out   # the repository's own .git/hooks/pre-push is no longer what runs
    (elsewhere / "pre-push").write_text(githook.script())
    (elsewhere / "pre-push").chmod(0o755)
    rc, out = check_cli(work, capsys)
    assert rc == 0 and str(elsewhere) in out
    git(work, "config", "core.hooksPath", ".husky/_")   # relative: from the top of the work tree
    (work / ".husky" / "_").mkdir(parents=True)
    rc, out = check_cli(work / ".husky", capsys)   # asked from a subdirectory
    assert rc == 1 and str(work / ".husky" / "_") in out


def test_check_follows_the_global_configuration(world, tmp_path, capsys, monkeypatch):
    work, _ = world
    shared = tmp_path / "global-hooks"
    shared.mkdir()
    (shared / "pre-push").write_text(githook.script())
    (shared / "pre-push").chmod(0o755)
    cfg = tmp_path / "gitconfig"
    cfg.write_text(f"[core]\n\thooksPath = {shared}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    rc, out = check_cli(work, capsys)
    assert rc == 0 and str(shared) in out and "core.hooksPath" in out


def test_check_on_something_that_is_not_a_repository_fails_with_a_line(tmp_path, git_env, capsys):
    rc, out = check_cli(tmp_path / "nowhere", capsys)
    assert rc == 1 and "not a git repository" in out
    (tmp_path / "plain").mkdir()
    rc, out = check_cli(tmp_path / "plain", capsys)
    assert rc == 1 and "not a git repository" in out


def test_check_works_on_a_bare_repository(world, capsys):
    _, remote = world
    rc, out = check_cli(remote, capsys)
    assert rc == 1 and "missing" in out
    hook = remote / "hooks" / "pre-push"
    hook.write_text(githook.script())
    hook.chmod(0o755)
    rc, out = check_cli(remote, capsys)
    assert rc == 0


def test_the_script_command_prints_exactly_the_hook_and_installs_nothing(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["git-hook", "script"]) == 0
    assert capsys.readouterr().out == githook.script()
    assert list(tmp_path.iterdir()) == [] or all(p.name in ("home", "config") for p in tmp_path.iterdir())


# ---- the classifier: what defeats the guard is high, with a reason that says so ------------------------------------
def level(cmd):
    return policy.assess("Bash", {"command": cmd}, "/tmp/x")


@pytest.mark.parametrize("cmd", [
    "git push --no-verify origin feat", "git push origin feat --no-verify", "git push --no-ver origin feat", "git -C /x push --no-verify",
    "git -c core.hooksPath=/dev/null push origin feat", "git -c core.hooksPath=/dev/null commit -m x", "git -c core.hookspath=/x status",
    "git config core.hooksPath /dev/null", "git config --global core.hooksPath /x", "git config --unset core.hooksPath",
    "chmod -x .git/hooks/pre-push", "rm .git/hooks/pre-push", "jade git-hook script > .git/hooks/pre-push", "mv /tmp/h .git/hooks/pre-push",
])
def test_what_defeats_the_pre_push_guard_is_high_and_says_why(cmd):
    a = level(cmd)
    assert a.level == "high" and a.rule_id == "R-17" and "pre-push guard" in a.rationale, a


@pytest.mark.parametrize("cmd", [
    "git config --get core.hooksPath", "git config --list", "git config user.name x", "cat .git/hooks/pre-push", "ls .git/hooks",
    "jade git-hook script", "jade git-hook check .", "git commit --no-verify -m x", "git config --show-origin core.hooksPath",
])
def test_reading_the_hook_or_its_setting_and_the_commands_around_it_stay_low(cmd):
    assert level(cmd).level == "low", cmd


def test_a_write_to_the_hook_file_through_an_edit_tool_is_high(tmp_path):
    a = policy.assess("Write", {"file_path": str(tmp_path / "r" / ".git" / "hooks" / "pre-push"), "content": "#!/bin/sh\nexit 0\n"}, str(tmp_path))
    assert a.level == "high" and a.rule_id == "R-17"
