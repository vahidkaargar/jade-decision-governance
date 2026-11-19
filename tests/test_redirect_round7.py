"""Round-7 review of PR 47: the reading of origin/main is no longer capped, and a redirect with a space after it no longer hides the current branch.

* R7-TWOR-1, R7-DIFF-1, R7-CAP-1, R7-RS-1: the scan cap on the reading of origin/main let a nested prefix push that reading into plain text, so a round-6 critical passed behind it.
  The cap is gone: the reading of origin/main runs in full, as it ran on origin/main.
* R7-DIFF-2: `git push > log`, `git push --force &> log`, `git >log push` (a redirect and its target as separate words) lost the current-branch half of SI-08.
* Round 8: R8-DIFF-1 (`git push > --mirror` in a directory that is no repository: origin/main reads the target as a flag, so it is read both ways), R8-CLAIM-1 (the operators
  `<>`, `>&` and a target the skeleton split at an escaped space), R8-TEST-1 (every round-6 critical behind every prefix).
"""

import subprocess
import time

import pytest

from jade import hooks, policy, shellnorm, shellnorm_base
from tests.test_redirect_anywhere import si08
from tests.test_redirect_round6 import CRITICAL

PUSH = "git push origin main"


@pytest.fixture
def repo_on_main(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    for argv in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                 ["remote", "add", "origin", "https://example.invalid/x.git"]):
        subprocess.run(["git", "-C", str(r), *argv], check=True)
    return r


# ---------------------------------------------------------------- a redirect and its target as separate words

SPACED = [
    "git push > log",
    "git push >> log",
    "git push 2> log",
    "git push 2>> log",
    "git push 1> log",
    "git push < in",
    "git push 0< in",
    "git push --force > log",
    "git push --force &> log",
    "git push --force &>> log",
    "git push &> log",
    "git push -q > log",
    "git push --force-with-lease > log",
    "git > log push",
    "git >log push",
    "git 2> log push --force",
    "env git push > log",
    "git -C . push > log",
    "git -c core.pager=cat push > log",
    "FOO=1 git push > log",
    "command git push > log",
    "nohup git push > log",
    "git push --force > log\\ x origin",
    "git push --force <> log origin",
    "git push --force >& - origin",
    "git push --force 2>& - origin",
    "git push --force >&- origin",
    "git push --force >| log origin",
    "git push --force >>& log origin",
]


@pytest.mark.parametrize("command", SPACED)
def test_a_spaced_redirect_in_a_bare_push_on_main_still_stops_it(command, repo_on_main):
    assert hooks._on_default_branch(str(repo_on_main), command), command


@pytest.mark.parametrize("command", [
    "git push origin feature > log",
    "git push origin feature 2> log",
    "git push origin feature &> log",
    "git push origin feature < in",
])
def test_a_spaced_redirect_after_a_named_branch_is_not_the_default_branch(command, repo_on_main):
    assert not hooks._on_default_branch(str(repo_on_main), command), command


@pytest.mark.parametrize("command", [
    "git push origin main > log",
    "git push origin > log main",
    "git push origin 2> log main",
    "git push origin HEAD:main > log",
    "git push origin :main &> log",
])
def test_a_spaced_redirect_does_not_hide_the_named_default_branch(command, repo_on_main):
    assert si08(command), command


# ---------------------------------------------------------------- the critical commands of round 6 behind a long or nested prefix

PAD = "".join(f"echo $(echo {i}) ; " for i in range(600))   # 11 kB of command substitutions
NESTED_EVAL = 'eval "$(echo ' * 32 + "x" + ')"' * 32
NESTED_HERE = 'sed p <<< "$(' * 40 + "echo hi" + ')" | wc -l' * 40
SH_C = "sh -c '" * 1 + "x' "

TAILS = CRITICAL + ["<&- echo git p\\ush origin main | sh"]   # every command pinned in round 6, and one that spells the push with a backslash


@pytest.mark.parametrize("prefix", [PAD, NESTED_EVAL, NESTED_HERE, SH_C], ids=["pad", "nested-eval", "nested-here", "sh-c"])
@pytest.mark.parametrize("tail", TAILS)
@pytest.mark.parametrize("sep", ["\n", " ; "])
def test_a_long_or_nested_prefix_does_not_hide_a_round_6_critical(prefix, tail, sep):
    command = prefix + sep + tail
    assert si08(command), command[-80:]
    assert policy.assess("Bash", {"command": command}, cwd="/tmp").level in ("high", "critical")


def test_the_reading_of_origin_main_is_not_capped():
    """`_base_skeleton` is the reading of origin/main for any size or nesting (`nocorrect` aside): it ran under a cap in round 6, and a nested prefix used the cap up."""
    for command in (NESTED_EVAL + "\n" + TAILS[0], PAD * 3 + "\n" + TAILS[2], NESTED_HERE * 2 + "\n" + TAILS[4]):
        assert shellnorm._base_skeleton(command) == shellnorm_base.skeleton(command)


@pytest.mark.timing
def test_a_long_prefix_costs_a_few_times_what_origin_main_cost():
    command = PAD + "\n<&- echo git push origin main | sh"
    shellnorm._skeleton_both_top.cache_clear()
    t = time.process_time()
    policy.assess("Bash", {"command": command}, cwd="/tmp")
    assert time.process_time() - t < 2.0


# ---------------------------------------------------------------- R8-DIFF-1: the words of origin/main are read as well


@pytest.mark.parametrize("command", [
    "git push > --mirror", "git push > --all", "git push 2> --mirror", "git push >>--mirror", "git push &>--all", "git push < --mirror", "git push 2>> --all",
])
def test_a_redirect_target_that_looks_like_a_flag_is_read_as_origin_main_read_it(command, tmp_path):
    """In a directory that is no repository the shell runs a bare `push`. For the spaced forms (`> --mirror`, `2> --all`, `< --mirror`) origin/main took `--mirror` for the
    flag that pushes every branch and stopped the command; the glued forms (`>--mirror`, `>>--mirror`, `&>--all`) it did not stop. The gate stops all of them."""
    assert hooks._on_default_branch(str(tmp_path), command), command
