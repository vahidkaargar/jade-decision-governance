"""W-131: the SI-08 gaps that origin/main had and the review of PR 47 listed as "pre-existing" (`git -C my\\ repo push`, `a\\ #c`, an odd word as a redirect target, a zsh
`>!` in the slot of the verb, wrappers that halve backslashes).

Every command here is run in the real bash and zsh with a `git` that records its arguments (`tests/test_redirect_anywhere.py`, `ran_git`): when either shell pushes
to the default branch, the gate must stop the command. Most of the spellings were closed on the way to 0.7.0 (V6-08, W-129, W-130, W-134); they are pinned here.
The one that was still open: zsh expands a brace word that follows a redirect operator (`> {a,b}`) into several targets, all of them redirects, and the command is
the words around it. The gate expanded the word first and took the first result for the redirect and the rest for words of the command, so `git > {a,b} push origin
main` was read as `git b push origin main` and the push was lost.
"""

import re
import shutil

import pytest

from jade import policy, shellnorm
from tests.test_redirect_anywhere import (
    agrees_with_the_shell,
    cur_segments,
    pushes_to_default,
    ran_git,
    si08,
)

# ---------------------------------------------------------------- a brace word after a redirect operator: every result is a target (zsh MULTIOS)

BRACE_TARGETS = ["{a,b}", "a{b,c}", "{a,b}c", "{a,b,c}", "{a,{b,c}}", "{,a}", "{a,}", "x{a,b}y", "{a,b}{c,d}", "{a,b}/x"]
OPERATORS = [">", ">>", "2>", "&>", ">|", ">&", ">!", ">>!", "&>>", ">>&", "<"]


@pytest.mark.parametrize("target", BRACE_TARGETS)
@pytest.mark.parametrize("op", OPERATORS)
@pytest.mark.parametrize("space", ["", " "])
def test_a_brace_word_as_a_redirect_target_never_hides_the_push(op, target, space):
    for command in (f"git {op}{space}{target} push origin main",
                    f"git push origin feature {op}{space}{target} :main",
                    f"git push {op}{space}{target} origin feature :main",
                    f"{op}{space}{target} git push origin main",
                    f"cd x && git {op}{space}{target} push origin main"):
        assert si08(command), command


@pytest.mark.parametrize("command", [
    "git > {a,b} push origin main",
    "git >{a,b} push origin main",
    "git 2> {a,b} push origin main",
    "git &> {a,b} push origin main",
    "git > a{b,c} push origin main",
    "git > {a,b,c} push origin main",
    "git >! {a,b} push origin main",
    "sh -c 'git > {a,b} push origin main'",
    "zsh -c 'git > {a,b} push origin main'",
    "echo 'git > {a,b} push origin main' | zsh",
    "eval 'git > {a,b} push origin main'",
])
def test_zsh_multios_push_is_stopped_and_the_shell_agrees(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.skipif(not shutil.which("zsh"), reason="needs zsh")
def test_the_shell_really_pushes_here(tmp_path):
    """The oracle behind the tests above: zsh runs `git > {a,b} push origin main` as the push (bash reports an ambiguous redirect and runs nothing)."""
    assert pushes_to_default(ran_git("git > {a,b} push origin main", "zsh", tmp_path))


def readings(command: str) -> list[str]:
    """The segments of every reading the gate holds for the command (`skeleton_both`): the current reading, the one with every result of a brace target as a target, main's."""
    return [re.sub(r"\s+", " ", seg).strip() for part in shellnorm.skeleton_both(command).split(shellnorm.JOIN) for seg in shellnorm.SPLIT.split(part) if seg.strip()]


def test_every_result_of_a_brace_target_is_a_redirect_in_one_reading_and_the_words_stay_in_the_other():
    got = readings("git > {a,b} push origin main")
    assert "git push origin main > a > b" in got   # zsh: both are targets
    assert "git b push origin main > a" in got     # the reading of origin/main stays (the first result is the target, the rest are words)
    assert "git push origin feature :main 2> a > b" in readings("git push origin feature 2>{a,b} :main")
    assert "git push origin main > a" in readings("git > {a,} push origin main")   # an empty result names no file


def test_a_brace_word_that_is_not_a_target_is_expanded_as_before():
    assert cur_segments("git push origin {main,x}") == ["git push origin main x"]
    assert cur_segments("echo {a,b} > f") == ["echo a b > f"]
    assert cur_segments("echo > f {a,b}") == ["echo a b > f"]
    assert not any(" > " in r for r in readings("git push origin {main,x}"))   # no redirect, no extra reading


@pytest.mark.parametrize("command", [
    "rm >{x,-rf} /Users/x/proj", "rm 2>{x,-rf} /Users/x/proj", "rm -r >>{x,-f} /Users/x/proj", "rm -r &>{x,--force} /Users/x/proj", "sudo rm >{x,-rf} /etc/x",
    "sh -c 'rm 2>{x,-rf} /Users/x/proj'", "rm -r <>{x,-fd} /Users/x/proj",
])
def test_the_words_reading_stays_so_a_dangerous_result_is_never_rated_lower_than_origin_main_rated_it(command):
    a = policy.assess("Bash", {"command": command}, cwd="/tmp")
    assert a.level in ("high", "critical"), (command, a.level, a.rule_id)


@pytest.mark.parametrize("command", [
    "git push origin feature >{x,:main}", "git push origin feature > {x,main}", "git push > {x,--mirror} origin",
    "git push origin feature >{x,HEAD:main}", "git push origin feature &>{x,+main}",
])
def test_a_dangerous_result_of_a_brace_target_is_stopped_in_either_reading(command):
    assert si08(command), command


def test_a_brace_target_is_still_a_write():
    for target in ("{~/.claude/settings.json,b}", "{b,~/.claude/settings.json}"):
        for op in (">", ">>", "2>", "&>"):
            a = policy.assess("Bash", {"command": f"echo x {op} {target}"}, cwd="/tmp")
            assert (a.level, a.rule_id) == ("high", "R-13"), (op, target)


# ---------------------------------------------------------------- spellings that were open on origin/main and are closed now (pinned)

PINNED = [
    r"git -C my\ repo push origin main",
    r"git -C a\ b\ c push origin main",
    r"git -c a\ b=c push origin main",
    r"git --git-dir my\ repo/.git push origin main",
    r"git push origin feature a\ #c :main",
    r"echo a\ #c; git push origin main",
    r"git push origin a\ #c main",
    r"git >! push origin HEAD:main",
    r"git >! lg push origin main",
    r"git >>! push origin HEAD:main",
    r"git >&! push origin main",
    r"1>! git push origin main",
    r"git push origin feature > a\ b :main",
    r"git push origin feature >#c :main",
    r"git push origin feature >a\;b :main",
    r"git push origin feature >$'a b' :main",
    r"git push origin feature >'a b' :main",
    r"git push origin feature >$(echo a) :main",
    r"git push origin feature >\#c :main",
    r"bash -c 'git push origin feature a\\\\&>x :main'",
    r'bash -c "git push origin feature a\\\\&>x :main"',
    r'eval "git push origin feature a\\\\\\\\&>x :main"',
    r'echo "git push origin feature a\\\\&>x :main" | sh',
    r'echo "git push origin feature a\\\\\\\\&>x :main" | zsh',
    "sh <<EOF\ngit push origin feature a\\\\&>x :main\nEOF",
    r'bash -c "bash -c \"git push origin feature a\\\\\\\\&>x :main\""',
]


@pytest.mark.parametrize("command", PINNED)
def test_pinned_spelling_is_stopped_and_the_shell_agrees(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)
