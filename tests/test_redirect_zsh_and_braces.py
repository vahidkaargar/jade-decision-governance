"""Review findings W-108 and W-109 on the redirect hoist (PR 47): zsh redirect spellings, a descriptor close with a word glued to it, and braces inside a word.

Every command here is also run in the real bash and zsh with a `git` function that records its arguments (`tests/test_redirect_anywhere.py`, `ran_git`): when
either shell would push to the default branch, the gate must stop the command. The gate reads a command once and the two shells read some of it differently, so
it has to be safe under both readings.

* W-108: zsh has redirect operators bash has not: `>>&`, `>>|`, `&>|`, `&>>|`, `>>&|`, `>>&!`, `>!`, `>>!`, `>&|`, `>&!`. One missing from the operator list cut the push in two.
* W-109a: `>&-` closes a descriptor in bash and the word glued to it (`-lg`) is an argument; zsh reads `-lg` as a file name. `git >&-lg push origin :main` is a push
  in zsh only, `git push origin feature >&-main` a push to main in bash only. The skeleton carries both readings.
* W-109b: a `{` or `}` that is not a word of its own, at the start of a command, is a literal in both shells; it must not cut the command in two.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from jade import policy, shellnorm
from tests.test_redirect_anywhere import (
    OTHERS,
    PUSH_TO_MAIN,
    PUSHES,
    agrees_with_the_shell,
    glued,
    inserted,
    pushes_to_default,
    ran_git,
    si08,
    sig,
)

# zsh spellings with the target glued to the operator: the one form both shells split the same way.
ZSH_GLUED = [">>&log", ">>|log", "&>|log", "&>>|log", ">>&|log", ">>&!log", ">!log", ">>!log", ">&|log", ">&!log", "&>!log", "&>>!log"]
# the same with a blank before the target; the `!` operators are left out (bash reads `>!` as `>` and a file `!`, so the word after it is an argument there)
ZSH_SPACED = [">>& log", ">>| log", "&>| log", "&>>| log", ">>&| log", ">&| log"]


# ---------------------------------------------------------------- W-108: zsh redirect operators

@pytest.mark.parametrize("command", [f"git push origin feature {r} :main" for r in ZSH_GLUED + ZSH_SPACED])
def test_zsh_redirect_in_a_push_to_main_is_blocked(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("base", PUSHES + OTHERS)
@pytest.mark.parametrize("redirect", ZSH_GLUED + ZSH_SPACED)
def test_zsh_redirect_in_any_position_changes_nothing(base, redirect):
    want = sig(base)
    for command in inserted(base, redirect):
        assert sig(command) == want, f"{command!r}"


@pytest.mark.parametrize("base", PUSHES + OTHERS)
@pytest.mark.parametrize("redirect", ZSH_GLUED)
def test_zsh_redirect_glued_to_a_word_changes_nothing(base, redirect):
    want = sig(base)
    for command in glued(base, redirect):
        assert sig(command) == want, f"{command!r}"


@pytest.mark.parametrize("redirect", ZSH_GLUED + ZSH_SPACED)
@pytest.mark.parametrize("base", ["git push origin " + p for p in PUSH_TO_MAIN])
def test_zsh_redirect_never_hides_a_push_to_main_from_the_shell(base, redirect, tmp_path):
    for command in inserted(base, redirect):
        agrees_with_the_shell(command, tmp_path)


def test_zsh_redirects_are_spelled_one_way():
    for r in (">>&log", ">>|log", "&>>|log", ">>&|log", ">>&!log", ">>!log"):
        assert shellnorm.segments(f"git push origin feature {r} :main")[0] == "git push origin feature :main >> log", r
    for r in ("&>|log", ">!log", ">&|log", ">&!log", "&>!log"):
        assert shellnorm.segments(f"git push origin feature {r} :main")[0] == "git push origin feature :main > log", r
    assert shellnorm.segments("git push origin feature >>|log :main") == ["git push origin feature :main >> log"]   # no `!`: one reading


def test_a_zsh_redirect_is_still_a_write():
    target = "~/.claude/settings.json"   # a file the gate rates high for any write (R-13)
    for r in (">>&", ">>|", "&>|", ">>&|", ">!", ">>!", ">&|"):
        a = policy.assess("Bash", {"command": f"echo x {r}{target}"}, cwd="/tmp")
        assert (a.level, a.rule_id) == ("high", "R-13"), r


# ---------------------------------------------------------------- W-109a: `>&-` and a word glued to it

CLOSE_WORDS = ["lg", "-", "x", "main", "-lg", "--mirror", "1"]


@pytest.mark.parametrize("command", [
    "git >&-lg push origin :main",                   # zsh: a file `-lg`, then a push
    "git >&-- push origin :main",
    "git -C . >&-x push origin :main",
    "git push origin feature >&-main",               # bash: closes the descriptor, `main` is a refspec
    "git push origin feature a>&-main",
    "git push origin feature <&-main",
    "git push origin feature 2>&-main",
    "git push origin feature >&---mirror",
])
def test_descriptor_close_with_a_glued_word_is_safe_under_both_readings(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("word", CLOSE_WORDS)
@pytest.mark.parametrize("op", [">&-", "<&-", "2>&-", "3<&-"])
@pytest.mark.parametrize("base", ["git push origin " + p for p in PUSH_TO_MAIN] + ["git push origin feature :main", "git -C . push origin :main"])
def test_descriptor_close_glued_to_any_word_never_hides_a_push_to_main(base, op, word, tmp_path):
    for command in inserted(base, op + word):
        agrees_with_the_shell(command, tmp_path)


def test_a_closed_descriptor_alone_names_no_file():
    assert shellnorm.segments("git push >&- origin 2>&- feature") == ["git push origin feature >&- 2>&-"]   # it moves as written, as on main (W-115)
    assert shellnorm.segments("git push origin feature <&0 :main") == ["git push origin feature :main"]


def test_both_readings_of_a_close_with_a_glued_word_are_in_the_skeleton():
    got = shellnorm.segments("git >&-lg push origin :main")
    assert "git push origin :main > -lg" in got      # zsh: the word is a file
    assert "git lg push origin :main >&-" in got     # bash: the descriptor closes and the word is an argument


# ---------------------------------------------------------------- W-109b: a brace inside a word is a literal

BRACE_WORDS = ["a\\{b,c}", "a\\{lg,x}", "a\\{,}", "a{lg,x\\}", "a{", "a}", "{a", "}a", "a{b", "a}b", "a\\{", "a\\}", "a{}", "a{x}", "{}", "x={", "x=}"]


@pytest.mark.parametrize("word", BRACE_WORDS)
@pytest.mark.parametrize("push", PUSH_TO_MAIN)
def test_a_brace_inside_a_word_never_hides_a_push_to_main(push, word, tmp_path):
    base = "git push origin " + push
    head, _, last = base.rpartition(" ")
    for command in (f"{head} {word} {last}", f"{head} {last} {word}", f"{head} {word}{last}", f"bash -c '{head} {word} {last}'", f"eval '{head} {word} {last}'"):
        agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("command", [
    "git push origin feature a\\{b,c} :main",
    "git push origin feature a\\{lg,x} :main",
    "git push origin feature a\\{,} :main",
    "git push origin feature a{lg,x\\} :main",
    "git push origin feature a{ :main",
    "git push origin feature a} :main",
    "git push origin feature } :main",               # a `}` that is not at the start of a command is an argument in bash
    "git push origin feature { :main",
    "git push origin feature {} :main",
    "git push origin feature x={ :main",
])
def test_brace_in_a_push_to_main_is_blocked(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("command", [
    "{ git push origin main; }",
    "{ git push origin feature :main ; }",
    "echo x; { git push --force origin main; }",
    "true && { git push origin :main; }",
    "if true; then { git push origin main; }; fi",
    "if { git push origin main; }; then true; fi",
    "(git push origin main)",
    "{\ngit push origin main\n}",
    "{ git push origin :main }",                     # zsh closes a group with `}` and no `;`
    "f() { git push origin main; }; f",
    "{ { git push origin main; }; }",
    "time { git push origin main; }",
])
def test_a_command_group_is_still_a_group(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


def test_a_brace_is_read_as_a_group_and_as_a_word():
    """Every reading is in the skeleton: the one in which a brace cuts the command (a group) and the ones in which it is a word."""
    got = shellnorm.segments("{ git push origin main; }")
    assert "git push origin main" in got
    got = shellnorm.segments("git push origin feature a{ :main")
    assert "git push origin feature a\x06 :main" in got and ":main" in got
    got = shellnorm.segments("git push origin feature a\\{b,c} :main")
    assert "git push origin feature a\x06b,c\x06 :main" in got
    assert shellnorm.segments("git push origin {feature,hotfix} :main") == ["git push origin feature hotfix :main"]   # a brace expansion is expanded, one reading
    assert shellnorm.segments("echo ${HOME}") == ["echo $HOME"]


@pytest.mark.parametrize("command", [
    "find . -name '*.pyc' -exec rm {} \\;",
    "find . -type f -exec chmod 644 {} +",
    "awk '{print $1}' f",
    "jq -r '{a: 1}' f",
    "echo ${HOME}/x",
    "echo {a,b}",
    "ls {a,b}/c",
    "git log --format='{%h}'",
])
def test_literal_and_expansion_braces_do_not_change_the_rating(command):
    """The braces that are not a command group are data; none of these is rated higher than the same command without them."""
    plain = command.replace("{}", "x").replace("{print $1}", "x")
    a = policy.assess("Bash", {"command": command}, cwd="/tmp")
    b = policy.assess("Bash", {"command": plain}, cwd="/tmp")
    assert policy.LEVELS[a.level] <= max(policy.LEVELS[b.level], policy.LEVELS["medium"]), command


def test_the_oracle_itself_sees_global_options():
    where = Path(tempfile.mkdtemp())
    try:
        if shutil.which("bash"):
            assert pushes_to_default(ran_git("git -C . push origin :main", "bash", where))
            assert not pushes_to_default(ran_git("git -C . push origin feature", "bash", where))
    finally:
        shutil.rmtree(where, ignore_errors=True)


# ---------------------------------------------------------------- W-109c: backslashes that the shell, a heredoc or `echo` halve before another shell reads the text
#
# `sh <<EOF` with an unquoted delimiter hands the program `\\` as `\`, and `echo` of zsh and sh does the same, so `a\\\>&log` reaches the second shell as `a\\>&log`:
# the word `a\` and a redirect. The first shell's reading (an odd number of backslashes: an escaped `>`) is the other reading; the gate reads both.

LAYERS = [
    "sh <<EOF\n{c}\nEOF",
    "cat <<EOF | sh\n{c}\nEOF",
    "bash <<'EOF'\n{c}\nEOF",
    "echo '{c}' | sh",
    "echo '{c}' | zsh",
    'echo "{c}" | zsh',
    "printf '%s' '{c}' | bash",
    "bash -c '{c}'",
    "eval '{c}'",
]


@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize("escapes", [1, 2, 3, 4])
@pytest.mark.parametrize("tail", [">&log", ">|log", "<&0"])
@pytest.mark.parametrize("base", ["git push origin " + p for p in ("feature :main", "feature --mirror")])
def test_halved_backslashes_before_a_redirect_never_hide_a_push_to_main(base, tail, escapes, layer, tmp_path):
    inner = f"{base.rpartition(' ')[0]} a{chr(92) * escapes}{tail} {base.rpartition(' ')[2]}"
    agrees_with_the_shell(layer.format(c=inner), tmp_path)


def test_a_heredoc_and_echo_hand_the_program_the_halved_text():
    assert "git push origin feature a\\\\ :main > log" in shellnorm.segments("sh <<EOF\ngit push origin feature a\\\\\\>&log :main\nEOF")
    assert "git push origin feature a\\\\ :main > log" in shellnorm.segments("echo 'git push origin feature a\\\\\\>&log :main' | zsh")


# ---------------------------------------------------------------- round 2 of the review of d10abf9 (W-111): the fix must not lose what 3c7d2e9 and main blocked
#
# Every command below is run in the real bash and zsh (a script file, a recording `git`); `really_pushes` says that at least one of them pushes to the default
# branch, so a test cannot pass because the shell never ran the push, and the gate must stop it. The design is "fail closed": where the gate cannot tell whether a
# brace, an escape or a `!` is data or syntax in the shell that will run the text, it reads the command both ways and either reading can block.

def really_pushes(command: str, tmp_path: Path) -> bool:
    return any(shutil.which(sh) and pushes_to_default(ran_git(command, sh, tmp_path)) for sh in ("bash", "zsh"))


def confirm_real_push(command: str, tmp_path: Path) -> None:
    """The test is right: a shell does push with `command`. Needs both shells (some commands are zsh syntax, some bash); the gate assertions run without them."""
    if shutil.which("bash") and shutil.which("zsh"):
        assert really_pushes(command, tmp_path), f"no shell pushes with {command!r}: the test is wrong"


ZSH_GROUPS = [
    "if { true } { git push origin feature :main }",                                       # F1: the `}` that closes a brace condition
    "while { true } { git push origin HEAD:main; break }",
    "until { false } { git push origin HEAD:main; break }",
    "if { false } { true } elif { true } { git push origin +main }",
    "if ! { false } { git push origin main }",
    "if { { true } } { git push origin main }",
    "if { true } {git push origin HEAD:main}",
    "if [[ -z x ]] { echo } elif [[ 1 ]] { git push origin main }",                         # SEG-1
    "if [[ -z x ]] { echo } elif [[ 1 ]] { git push origin HEAD:main }",
    "if [[ -z x ]] { echo } elif [[ -z y ]] { echo } elif [[ 1 ]] { git push origin main }",
    "if [[ -z x ]] { echo } elif [[ 1 ]] { git push origin main } else { echo }",
    "{git push origin HEAD:main}",                                                          # F2: no blank after `{`
    "{git push origin feature :main}",
    "{git push --delete origin main}",
    "{ git push origin :main }",
    "{ git push --force origin main }",
    "{ command git push origin main; }",
    "{ env git push origin main; }",
    "{ eval 'git push origin main'; }",
    "{ ls } ; git push origin main",
    "{ pwd }\ngit push origin main",
    "for i (1) { git push origin main }",
    "repeat 1 { git push origin main }",
]


@pytest.mark.parametrize("command", ZSH_GROUPS)
def test_zsh_brace_groups_still_stop_a_push_to_main(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


@pytest.mark.parametrize("wrapper", ["if {{ true }} {{ {c} }}", "if [[ -z x ]] {{ echo }} elif [[ 1 ]] {{ {c} }}", "{{{c}}}", "while {{ true }} {{ {c}; break }}"])
@pytest.mark.parametrize("plain", ["rm -rf /Users/x/proj", "git reset --hard", "git push --force origin feature", "git clean -fd"])
def test_zsh_brace_groups_do_not_lower_the_rating(wrapper, plain):
    command = wrapper.format(c=plain)
    want = policy.assess("Bash", {"command": plain}, cwd="/tmp").level
    got = policy.assess("Bash", {"command": command}, cwd="/tmp").level
    assert policy.LEVELS[got] >= policy.LEVELS[want], command


BRACE_PUSHES = [
    "git push origin feature a\\{b,c} :main", "git push origin feature a\\{lg,x} :main", "git push origin feature a\\{,} :main", "git push origin feature a{lg,x\\} :main",
    "git push origin feature a{ :main", "git push origin feature a} :main", "git push origin feature } :main", "git push origin feature { :main",
    "if { true } { git push origin feature a{ :main }",                                     # a group and a literal brace in one command
    "{ git push origin feature a} :main; }",
    "{git push origin feature { :main}",
]


@pytest.mark.parametrize("command", BRACE_PUSHES)
def test_brace_readings_never_hide_a_push_to_main(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


# ---------------------------------------------------------------- what zsh `print` and `echo` print, and what runs it

PRINTED = [
    "print 'git push origin feature \\>&x :main' | bash -s",                                # F3: print drops the backslash of any unknown escape
    "print 'git push origin feature \\>&-x :main' | zsh",
    "print 'git push origin \\>&- main' | zsh",                                             # DIFF-2
    "print 'git push origin feature \\$x :main' | bash -s",
    "print 'git push origin feature a\\{ :main' | bash -s",
    "print 'git push origin feature a\\} :main' | bash -s",
    "echo 'git push origin feature a\\\\\\>&log :main' | bash -s",
    "time echo 'git push origin main' | sh",                                                # DIFF-1: the echo has a prefix
    "FOO=1 printf 'git push origin main' | zsh",
    "env echo 'git push origin main' | sh",
    "command echo 'git push origin main' | sh",
    "command printf 'git push origin main' | sh",
    "builtin echo 'git push origin main' | sh",
    "exec echo 'git push origin main' | sh",
    "noglob echo 'git push origin main' | zsh",
    "! echo 'git push origin \\>!log main' | sh",
    "time print 'git push origin \\>&x main' | bash -s",
    "echo git push \\\\<&- origin main | sh",                                               # DIFF-3
]


@pytest.mark.parametrize("command", PRINTED)
def test_text_printed_for_a_shell_is_read_as_that_shell_reads_it(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


@pytest.mark.parametrize("producer", ["echo", "print", "printf", "time echo", "env printf", "! echo", "command print"])
@pytest.mark.parametrize("escape", ["\\>", "\\<", "\\|", "\\;", "\\&", "\\$", "\\{", "\\}", "\\\\\\>", "\\ "])
@pytest.mark.parametrize("consumer", ["sh", "bash -s", "zsh"])
def test_any_escape_in_printed_text_never_hides_a_push_to_main(producer, escape, consumer, tmp_path):
    for text in (f"git push origin feature a{escape}&log :main", f"git push origin feature a{escape}>log :main", f"git push origin feature a{escape}&-x :main"):
        command = f"{producer} '{text}' | {consumer}"
        if really_pushes(command, tmp_path):
            assert si08(command), command


# ---------------------------------------------------------------- F4: bash reads `>!`, `>>!` and `>&!` as `>` and a file named `!`

BASH_BANG = [
    "1>! git push origin main",
    "echo hi; >>! git push origin HEAD:main",
    "env bash -c \"1>! git push origin feature HEAD:refs/heads/main\"",
    "git >! push origin HEAD:main",
    "git >>! push --delete origin main",
    "git push >! origin main",
    "git push origin >&! main",
    "git push origin feature >&! :main",
    "git push origin feature &>! :main",
    ">! git push origin :main",
]


@pytest.mark.parametrize("command", BASH_BANG)
def test_bash_bang_redirects_keep_the_word_after_them(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


@pytest.mark.parametrize("op", [">!", ">>!", ">&!", "&>!", "&>>!"])
@pytest.mark.parametrize("base", ["git push origin " + p for p in PUSH_TO_MAIN])
def test_bang_redirect_at_any_position_never_hides_a_push_to_main(base, op, tmp_path):
    for command in inserted(base, op) + inserted(base, op + "log"):
        agrees_with_the_shell(command, tmp_path)


# ---------------------------------------------------------------- DIFF-7: a closed stdout does not read a file less

def test_a_closed_stdout_does_not_hide_a_credential_read():
    plain = policy.assess("Bash", {"command": "cat /Users/x/.ssh/id_rsa"}, cwd="/tmp")
    closed = policy.assess("Bash", {"command": "cat >&- /Users/x/.ssh/id_rsa"}, cwd="/tmp")
    assert policy.LEVELS[closed.level] >= policy.LEVELS[plain.level]


# ---------------------------------------------------------------- DIFF-5: a brace is cheap

@pytest.mark.timing
@pytest.mark.parametrize("word", ["{ ", "} ", "a{ "])
def test_many_braces_cost_about_as_much_as_many_words(word):
    import time

    def cost(chunk: str) -> float:
        policy.assess("Bash", {"command": "echo warm"}, cwd="/tmp")
        t = time.perf_counter()
        policy.assess("Bash", {"command": "git push origin feature " + chunk * 50000}, cwd="/tmp")
        return time.perf_counter() - t

    # origin/main costs 2.3 times as much on `a{ ` words as on plain words (every brace cuts the command), this tree 3.3 times (the word reading comes on top);
    # the bar is 3 times origin/main, and a CI runner (3.14) measured 4.6 times plain words, so the limit is 8 times: it catches a quadratic, not a constant
    assert cost(word) < 8 * cost("x ") + 1.0
