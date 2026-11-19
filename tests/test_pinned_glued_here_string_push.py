"""V6-04: the glued here-string push that the first PR 47 head (c6ab27a) let through and origin/main blocked, pinned (docs/14 V6-04 done-when, item 1).

`cat<<<'git push origin main'|sh` is a push to main: the here-string is the text the shell `sh` reads. The first PR 47 head read the glued `<<<` as a redirect on the program and
dropped the pushed text, so SI-08 did not see it. The gate must answer as it does for the spaced spelling, in every spelling of the glue and for every reader that runs the text.
"""

import subprocess

import pytest

from jade import hooks, policy

SPELLINGS = [
    "cat<<<'git push origin main'|sh",
    "cat <<<'git push origin main'|sh",
    "cat<<< 'git push origin main' | sh",
    "cat <<< 'git push origin main' | sh",
    "cat<<<'git push origin main'|bash",
    "cat<<<'git push origin main'|zsh",
    "cat<<<'git push origin main'|sh -s",
    "cat<<<'git push origin :main'|sh",
    "cat<<<'git push --force origin feature:main'|sh",
]


@pytest.fixture
def main_repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    return str(tmp_path)


@pytest.mark.parametrize("cmd", SPELLINGS)
def test_a_glued_here_string_push_to_main_is_blocked_by_si08(cmd, main_repo):
    a = policy.assess("Bash", {"command": cmd}, cwd=main_repo)
    assert "SI-08" in a.blocked, (cmd, a.level, a.rule_id, list(a.blocked))
    assert hooks._on_default_branch(main_repo, cmd), cmd


@pytest.mark.parametrize("cmd", ["cat<<<'git push origin feature'|sh", "cat<<<'git status'|sh"])
def test_a_glued_here_string_that_does_not_push_to_main_is_not_blocked_by_si08(cmd, main_repo):
    a = policy.assess("Bash", {"command": cmd}, cwd=main_repo)
    assert "SI-08" not in a.blocked, (cmd, list(a.blocked))
