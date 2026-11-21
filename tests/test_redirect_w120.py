"""W-120: a named descriptor redirect (`{fd}>log`) and an output process substitution (`>(cat)`) written in a push must not hide the push to the default branch.

`git {fd}>log push origin feature :main`: zsh (and bash 4.1 and later) take `{fd}>log` as a redirect and run `git push origin feature :main`; the scanner read `{fd}` as
a brace word that sat between `git` and `push`, so the call was rated low and nothing was blocked. `git push origin feature >(cat) :main`: the shell hands git the path
of a pipe as one more argument and runs the push; the scanner read `(cat)` as a group that cut the push short in front of `:main`.
Each case is also run in the real zsh and bash (a recording `git`): when a shell pushes to the default branch, the gate must stop the command.
"""

import re
import shutil

import pytest

from jade import policy
from tests.test_redirect_anywhere import inserted, pushes_to_default, ran_git, si08

NAMED_FD = ["{fd}>log", "{fd}>>log", "{fd}> log", "{fd}>|log", "{fd}>&2", "{fd}>&-", "{fd}<in", "{fd}<&0", "{out}>log", "{_f1}>log"]
PROCESS_SUBST = ["2>(cat)", "&>>(cat)", ">(cat)", "&>(cat)", ">&(cat)", "> >(cat)", "&> >(cat)", "2> >(cat)", "{fd}>(cat)"]
PUSHES = ["git push origin feature :main", "git push origin main", "git push origin feature +feature:main"]


@pytest.mark.parametrize("redirect", NAMED_FD + PROCESS_SUBST)
@pytest.mark.parametrize("base", PUSHES)
def test_a_push_to_main_with_the_redirect_anywhere_is_stopped(base, redirect):
    for command in inserted(base, redirect):
        if redirect in PROCESS_SUBST and command.split(" ").index("push") > command.split(" ").index(redirect.split(" ")[0]):
            continue   # `git >(cat) push ...` hands git a pipe path in front of `push`: git runs no push (the real-shell test below is the proof of the rest)
        assert si08(command), command


@pytest.mark.parametrize("redirect", NAMED_FD + PROCESS_SUBST)
@pytest.mark.parametrize("base", PUSHES[:2])
def test_the_gate_stops_every_command_a_real_shell_pushes_to_main(base, redirect, tmp_path):
    (tmp_path / "in").write_text("x")
    for command in inserted(base, redirect):
        for shell in ("zsh", "bash"):
            if shutil.which(shell) and pushes_to_default(ran_git(command, shell, tmp_path)):
                assert si08(command), f"{shell} pushes to the default branch and the gate does not stop {command!r}"


@pytest.mark.parametrize("redirect", NAMED_FD + PROCESS_SUBST)
def test_a_push_of_a_named_branch_with_the_redirect_is_not_stopped(redirect):
    for command in inserted("git push origin feature", redirect):
        assert not si08(command), command


@pytest.mark.parametrize("redirect", NAMED_FD)
def test_a_named_fd_redirect_in_front_of_the_subcommand_is_rated_as_the_redirect_without_its_name(redirect):
    unnamed = re.sub(r"^\{\w+\}", "", redirect)
    for plain in ("git status", "git push origin feature", "git log -1"):
        want = policy.assess("Bash", {"command": plain.replace("git ", f"git {unnamed} ", 1)}, cwd="/tmp").level
        command = plain.replace("git ", f"git {redirect} ", 1)
        assert policy.assess("Bash", {"command": command}, cwd="/tmp").level == want, command


# A backslash-newline inside the spelling: the shell takes the two characters out before it reads the line, so `git {fd}\<newline>>log push origin main` (zsh) and
# `git push origin >\<newline>(cat) main` (bash) push to main, and the gate must read them as the spelling written in one piece (W-120 review).
CONTINUED_FROM = ["{fd}>log", "{fd}<in", "{fd}>&2", "{fd}>(cat)", *PROCESS_SUBST]
CONTINUATION = "\\\n"


def continued(redirect: str) -> list[str]:
    """`redirect` with a backslash-newline at each place in its operator: inside and around the `{name}`, between the characters of `&>`, `2>`, `>&` and `>(`."""
    stop = redirect.index("(") + 1 if "(" in redirect else len(redirect) - len(re.sub(r"^.*?[<>]&?", "", redirect))
    return [redirect[:k] + CONTINUATION + redirect[k:] for k in range(1, stop) if redirect[k - 1:k + 1] != "> "]


CONTINUED = [(r, c) for r in dict.fromkeys(CONTINUED_FROM) for c in continued(r)]


def test_the_continued_spellings_cover_the_review_cases():
    spelled = {c for _, c in CONTINUED}
    for want in ["{fd}\\\n>log", "{f\\\nd}>log", ">\\\n(cat)", ">&\\\n(cat)", "2>\\\n(cat)", "{fd}\\\n>(cat)", "&\\\n>(cat)"]:
        assert want in spelled, want


@pytest.mark.parametrize(("redirect", "spelling"), CONTINUED)
@pytest.mark.parametrize("base", [*PUSHES, "git push origin feature"])
def test_a_continued_spelling_is_read_as_the_spelling_in_one_piece(base, redirect, spelling):
    for whole, command in zip(inserted(base, redirect), inserted(base, spelling), strict=True):
        assert si08(command) == si08(whole), (command, whole)
        got = policy.assess("Bash", {"command": command}, cwd="/tmp").level
        assert got == policy.assess("Bash", {"command": whole}, cwd="/tmp").level, (command, whole)


@pytest.mark.parametrize("command", [
    "git {fd}\\\n>log push origin main", "git {f\\\nd}>log push origin main", "git push origin >\\\n(cat) main", "git push origin feature >\\\n(cat) :main",
    "git push origin feature >&\\\n(cat) :main", "git push origin feature 2>\\\n(cat) :main", "git push origin feature {fd}\\\n>(cat) :main",
    "git push origin feature &\\\n>(cat) :main", "git {fd}\\\n>log push origin feature :main",
])
def test_the_review_cases_with_a_continuation_are_stopped(command):
    assert si08(command), command


@pytest.mark.parametrize(("redirect", "spelling"), CONTINUED)
def test_the_gate_stops_every_continued_command_a_real_shell_pushes_to_main(redirect, spelling, tmp_path):
    (tmp_path / "in").write_text("x")
    for base in PUSHES[:2]:
        for command in inserted(base, spelling):
            for shell in ("zsh", "bash"):
                if shutil.which(shell) and pushes_to_default(ran_git(command, shell, tmp_path)):
                    assert si08(command), f"{shell} pushes to the default branch and the gate does not stop {command!r}"


@pytest.mark.parametrize("command", [
    "echo {a,b}", "echo {fd}", "echo x{fd}>log", "git push origin feature {fd}", "git push origin feature {fd} {fd}",
    "ls {a,b}>log", "{ git push origin feature; }", "git tag -d v1 && { echo done; }",
])
def test_a_brace_that_is_not_a_named_fd_is_read_as_it_was(command):
    assert not si08(command), command
