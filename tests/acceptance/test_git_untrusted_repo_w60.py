"""W-60: the hook runs git in a directory the agent controls. That call must not read config, hooks or environment an untrusted
repository or the agent's own environment can steer, must be bounded, and a branch it cannot resolve is unresolved, never "not main".

Repositories are built under tmp_path and git is only asked to read a ref; no marker file may ever appear.
"""

import os
import subprocess

import pytest

from jade import hooks, ledger, permits, policy

SESSION = "sess-w60-0001"


def init(path, branch: str = "feat/x") -> str:
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    return str(path)


def call_for(cmd: str):
    return next(c for seg in policy.segments(cmd) for c in policy.git_calls(seg) if c.sub == "push")


def hostile(repo: str, marker) -> None:
    """A repository config that would run a command if git honoured it, and a hook that would too."""
    sh = f"touch {marker}"
    with open(os.path.join(repo, ".git", "config"), "a") as f:
        f.write(f'[core]\n\tfsmonitor = "{sh}; echo"\n\tpager = "{sh}"\n\thooksPath = {repo}/hooks\n'
                f'[alias]\n\tsymbolic-ref = "!{sh}"\n[include]\n\tpath = /nonexistent-w60\n')
    os.makedirs(os.path.join(repo, "hooks"))
    for h in ("post-checkout", "reference-transaction", "pre-push"):
        p = os.path.join(repo, "hooks", h)
        with open(p, "w") as f:
            f.write(f"#!/bin/sh\n{sh}\n")
        os.chmod(p, 0o755)


# ---------------------------------------------------------------- the argv and the environment

@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def run(argv, **kw):
        seen.update(argv=list(argv), **kw)
        return subprocess.CompletedProcess(argv, 0, stdout="feat/x\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_git_is_called_with_repository_config_hooks_and_pager_neutralised(captured, tmp_path):
    hooks._git(call_for("git push origin HEAD"), str(tmp_path), "symbolic-ref", "--short", "-q", "HEAD")
    argv = captured["argv"]
    assert argv[0] == "git" and "--no-pager" in argv
    cfg = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert "core.fsmonitor=false" in cfg and "core.hooksPath=/dev/null" in cfg
    assert argv.index("symbolic-ref") > max(i for i, a in enumerate(argv) if a in ("-c", "--no-pager", "-C")), "global options come before the subcommand"
    assert captured["timeout"] == hooks.GIT_TIMEOUT_S


def test_git_environment_is_scrubbed_of_the_agents_git_variables(captured, tmp_path, monkeypatch):
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_EXEC_PATH", "GIT_SSH_COMMAND", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        monkeypatch.setenv(k, "/attacker")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    hooks._git(call_for("git push origin HEAD"), str(tmp_path), "symbolic-ref", "-q", "HEAD")
    env = captured["env"]
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_PAGER"] == "cat" and env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_OPTIONAL_LOCKS"] == "0"
    attacker = {k for k, v in env.items() if v == "/attacker"}
    assert not attacker, attacker
    assert env["PATH"] == "/usr/bin:/bin"
    assert captured["stdin"] == subprocess.DEVNULL


def test_an_inherited_git_dir_does_not_redirect_the_branch_lookup(tmp_path, monkeypatch):
    on_main = init(tmp_path / "decoy", "main")
    work = init(tmp_path / "work", "feat/x")
    monkeypatch.setenv("GIT_DIR", os.path.join(on_main, ".git"))
    assert hooks._git(call_for("git push origin HEAD"), work, "symbolic-ref", "--short", "-q", "HEAD") == "feat/x"
    assert hooks._on_default_branch(work, "git push origin HEAD") is False


# ---------------------------------------------------------------- a hostile repository

@pytest.mark.parametrize("cmd", ["git push origin HEAD", "git push", "git push origin feat/x", "git push origin HEAD:trunk"])
def test_hostile_repository_config_and_hooks_never_run(tmp_path, cmd):
    repo = init(tmp_path / "r", "feat/x")
    marker = tmp_path / "ran"
    subprocess.run(["git", "-C", repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"], check=True)
    hostile(repo, marker)
    hooks._on_default_branch(repo, cmd)
    hooks.pretool({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": repo})
    assert not marker.exists(), "git executed something the repository's config or hooks name"


def test_hostile_repository_still_resolves_the_branch(tmp_path):
    repo = init(tmp_path / "r", "main")
    hostile(repo, tmp_path / "ran")
    assert hooks._on_default_branch(repo, "git push") is True
    assert not (tmp_path / "ran").exists()


# ---------------------------------------------------------------- unresolved is unresolved

def broken(repo: str) -> str:
    """A repository git cannot read: a config it refuses to parse (the same exit status as `dubious ownership`)."""
    with open(os.path.join(repo, ".git", "config"), "a") as f:
        f.write("this is not a config line [[[\n")
    return repo


def test_a_repository_git_refuses_to_read_is_unresolved_not_not_main(tmp_path):
    repo = broken(init(tmp_path / "r", "main"))
    with pytest.raises(hooks.GitUnresolved):
        hooks._git(call_for("git push origin HEAD"), repo, "symbolic-ref", "--short", "-q", "HEAD")
    with pytest.raises(hooks.GitUnresolved):
        hooks._on_default_branch(repo, "git push")


def test_dubious_ownership_is_unresolved(tmp_path, monkeypatch):
    msg = "fatal: detected dubious ownership in repository at '/x'\n"
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 128, stdout="", stderr=msg))
    with pytest.raises(hooks.GitUnresolved):
        hooks._git(call_for("git push origin HEAD"), str(tmp_path), "symbolic-ref", "-q", "HEAD")


def test_no_repository_at_all_is_nothing_to_push_from(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    c = call_for("git push origin HEAD")
    assert hooks._git(c, str(plain), "symbolic-ref", "--short", "-q", "HEAD") is None
    assert hooks._git(c, str(tmp_path / "missing"), "symbolic-ref", "-q", "HEAD") is None
    assert hooks._on_default_branch(str(plain), "git push") is False


def test_a_missing_git_is_unresolved(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(hooks.GitUnresolved):
        hooks._git(call_for("git push origin HEAD"), str(tmp_path), "symbolic-ref", "-q", "HEAD")


def test_a_hung_git_is_unresolved(tmp_path, monkeypatch):
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 2)

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(hooks.GitUnresolved):
        hooks._git(call_for("git push origin HEAD"), str(tmp_path), "symbolic-ref", "-q", "HEAD")


def test_git_failing_is_unresolved_but_no_such_ref_is_an_answer(tmp_path):
    repo = init(tmp_path / "r", "feat/x")
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    subprocess.run(["git", "-C", repo, "checkout", "-q", "--detach"], check=True)
    c = call_for("git push origin HEAD")
    assert hooks._git(c, repo, "symbolic-ref", "--short", "-q", "HEAD") is None                    # detached: a definite "no branch"
    assert hooks._git(c, repo, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD") is None  # no recorded remote default
    with pytest.raises(hooks.GitUnresolved):
        hooks._git(c, repo, "symbolic-ref", "--short", "HEAD-that-is-not-a-ref/..")                # a usage error is not an answer


def test_an_unresolved_destination_blocks_a_high_risk_push_even_with_an_issued_permit(tmp_path, human_env):
    bad = broken(init(tmp_path / "bad"))
    db = ledger.open()
    cmd = "git push origin feat/x"
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": bad}
    hooks.pretool({**ev, "cwd": init(tmp_path / "r")})   # the permit is requested where the branch resolves (feat/x)
    pid = permits.listing(db, ("PENDING",))[0].permit_id
    permits.approve(db, pid, stdin_isatty=True)
    r = hooks.pretool(ev)
    assert r.exit_code == 2 and "could not be resolved" in r.stderr and permits.get(db, pid).state == "ISSUED"
