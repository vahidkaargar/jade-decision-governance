"""Round-3 review of PR 47 (W-134): what origin/main stopped and the redirect hoist let through, a brace mark made out of a decoded byte, and the cost of a brace group.

The merge bar for this PR is: fix the redirect that hid the rest of a push, never be weaker than origin/main on any command, and stay within about 3 times its cost.

* E3-3: `cat <<< 'git push origin :main' | sh`. The here-string is the text the shell reads. The hoist took its first word (`git`) for the target of the `<<<`.
* E3-4, E3-9: a prefix word with a backslash or a quote (`\\env echo '...' | sh`) hid the echo from the reader of the text, and an escaped operator in text that a shell
  reads had only one reading (data); origin/main kept the backslash and read the operator as syntax, which is what a shell that reads `\\\\>` does.
* DF-6: `{git push -o $'\\x0f' origin main}`. The byte \\x0f is the mark of a glued brace; ANSI-C quotes make it after the sanitiser has run.
* B3-5, PF-1: a brace group that is a group in every shell (`{ echo a; }`) needs one reading, not five.
"""

import re
import time

import pytest

from jade import policy, shellnorm
from tests.loadproof import retry_under_load
from tests.test_redirect_anywhere import cur_segments, si08
from tests.test_redirect_zsh_and_braces import confirm_real_push, really_pushes

# ---------------------------------------------------------------- E3-3: the text a stage reads on its stdin

HERE_TEXT = [
    "cat <<< 'git push origin :main' | sh",
    "cat <<< 'git push origin :main' | bash",
    "cat <<< 'git push origin :main' | zsh",
    "cat <<< 'git push origin :main' | sh -s",
    "cat <<< 'git push origin :main' | sh -",
    "cat - <<< 'git push origin :main' | sh",
    "cat <<<'git push origin :main' | sh",
    "cat <<< 'git push origin HEAD:main' | sh",
    "cat <<< 'git push origin +main' | sh",
    "cat <<< 'git push --delete origin main' | sh",
    "cat <<< 'git push origin feature \\> :main' | sh",
    "cat <<< 'git push origin main' | env sh",
    "cat <<< 'git push origin :main' | tee /dev/null | sh",
    "cat <<< 'git push origin :main' | sed p | sh",
    "tee /dev/null <<< 'git push origin :main' | sh",
    "sed p <<< 'git push origin :main' | sh",
    "cat <<< 'echo hi; git push origin :main' | sh",
    "cat <<< 'echo hi' <<< 'git push origin :main' | sh",
    "bash <(cat <<< 'git push origin :main')",
    "sh <(cat <<< 'git push origin :main')",
    "bash <(cat - <<< 'git push origin :main')",
    "bash <(cat <<EOF\ngit push origin :main\nEOF\n)",
    "bash <(cat <<EOF\necho hi\ngit push origin :main\nEOF\n)",
    "bash <(cat <<EOF\necho hi\necho there\ngit push origin HEAD:main\nEOF\n)",
    "bash <(cat <<'EOF'\necho hi\ngit push origin :main\nEOF\n)",
    "cat <<EOF | sh\necho hi\ngit push origin :main\nEOF",
    "sh <<< 'git push origin :main'",
    "bash <<EOF\necho hi\ngit push origin :main\nEOF",
]


@pytest.mark.parametrize("command", HERE_TEXT)
def test_text_given_to_a_shell_on_stdin_is_read(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


@pytest.mark.parametrize("plain", ["rm -rf /Users/x/proj", "git reset --hard", "git push --force origin feature", "git clean -fd"])
@pytest.mark.parametrize("wrapper", ["cat <<< '{c}' | sh", "bash <(cat <<< '{c}')", "bash <(cat <<EOF\necho hi\n{c}\nEOF\n)"])
def test_a_here_string_read_by_a_shell_keeps_the_rating(wrapper, plain):
    want = policy.assess("Bash", {"command": plain}, cwd="/tmp").level
    got = policy.assess("Bash", {"command": wrapper.format(c=plain)}, cwd="/tmp").level
    assert policy.LEVELS[got] >= policy.LEVELS[want], wrapper


# ---------------------------------------------------------------- E3-4, E3-9: a prefix word with a backslash or a quote, an escaped operator

PREFIXES = ["\\env", "\\command", "\\builtin", "\\exec", "'command'", "'env'", "\"command\"", "c\\ommand", "e\\nv", "\\nohup", "\\time", "\\nice", "\\noglob"]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("text", ["git push origin feature \\> :main", "git push origin :main", "git push origin feature a\\>&x :main"])
def test_a_quoted_prefix_before_an_echo_does_not_hide_the_text(prefix, text, tmp_path):
    command = f"{prefix} echo '{text}' | sh"
    if really_pushes(command, tmp_path):
        assert si08(command), command


@pytest.mark.parametrize("command", [
    "\\env echo 'git push origin feature \\> :main' | sh",
    "\\command echo 'git push origin feature \\> :main' | sh",
    "'command' echo 'git push origin feature \\> :main' | sh",
])
def test_the_escaped_operator_after_a_quoted_prefix_is_blocked(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


# What origin/main blocked: an escaped operator in text a shell will read is also read as syntax, as main read it. No real shell pushes with most of these (the
# escaped `>` is a word, the `&` ends the command), and the gate was not asked to be right about them, only not weaker than it was.
KEPT_AS_ON_MAIN = [
    "bash -c 'git push origin feature a\\>&x :main'",
    "echo 'git push origin feature a\\>&x :main' | sh",
    "bash -c 'git push origin feature a\\&>x :main'",
    "bash -c 'git push origin feature a\\|b :main'",
    "bash -c 'git push origin feature a\\;b :main'",
    "bash -c 'git push origin feature \\> :main'",
    "bash -c 'git push origin feature \\>x :main'",
    "bash -c 'git push origin feature \\< :main'",
    "bash -c 'git push origin feature a\\&b :main'",
    "sh -c 'git push origin feature \\\\\\>&x :main'",
    "\\env echo 'git push origin feature \\> :main' | sh",
    "\\env bash -c 'git push origin feature a\\>&x :main'",
]


@pytest.mark.parametrize("command", KEPT_AS_ON_MAIN)
def test_an_escaped_operator_is_read_as_data_and_as_syntax(command):
    assert si08(command), command


def test_an_escaped_operator_has_both_readings_in_the_skeleton():
    text = shellnorm.skeleton("bash -c 'git push origin feature a\\>&x :main'")
    assert "a\x06" in text, "data reading"
    assert re.search(r"a\\\\? *:main|a\\ *:main", text), "syntax reading"


# ---------------------------------------------------------------- DF-6: a mark made after the sanitiser

MARK_CODES = list(range(0x0E, 0x16))


def spellings(code: int) -> list[str]:
    return [f"$'\\x{code:02x}'", f"$'\\x{code:02X}'", f"$'\\{code:o}'", f"$'\\0{code:o}'", f"$'\\u{code:04x}'", f"$'\\U{code:08x}'", f"$'\\x{code:x}'"]


@pytest.mark.parametrize("code", MARK_CODES)
def test_a_decoded_mark_byte_is_a_word_like_any_other(code):
    for spelling in spellings(code):
        for command in (f"echo {spelling}", f"git push -o {spelling} origin feature", f"{{git push -o {spelling} origin feature}}"):
            assert not re.search("[\x0e-\x15]", shellnorm.skeleton(command)), (spelling, command)


GLUED = [
    "{{git push -o {x} origin main}}",
    "{{git push --push-option={x} origin main}}",
    "{{git push origin {x} main}}",
    "{{git push -o {x} -o {x} origin main}}",
    "{{git -c core.x={x} push origin main}}",
    "{{ git push -o {x} origin main }}",
    "{{ git push -o {x} origin main; }}",
    "{{git push -o {x} origin a{{ :main}}",
    "if {{ true }} {{git push -o {x} origin main}}",
    "{{git push origin main {x}}}",
    "{{{x}git push origin main}}",
    "echo {x}; {{git push origin main}}",
    "git push origin feature a{{ {x} :main",
    "git push origin feature {x}{{ :main",
    "bash -c \"{{git push -o {x} origin main}}\"",
    "echo '{{git push -o {x} origin main}}' | zsh",
]


@pytest.mark.parametrize("code", MARK_CODES)
@pytest.mark.parametrize("template", GLUED)
def test_a_forged_brace_mark_never_hides_a_push_to_main(template, code, tmp_path):
    for spelling in spellings(code)[:3] + spellings(code)[4:5]:
        command = template.format(x=spelling)
        if really_pushes(command, tmp_path):
            assert si08(command), command


@pytest.mark.parametrize("command", [
    "{git push -o $'\\x0f' origin main}",
    "{git push -o $'\\x13' origin main}",
    "{git push -o $'\\017' origin main}",
    "{git push origin $'\\x0f' main}",
    "{git push -o $'\\x0e' -o $'\\x0f' origin main}",
    "{git push -o $'\\u000f' origin main}",
])
def test_the_findings_of_df_6(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


@pytest.mark.parametrize("plain", ["git reset --hard", "git push --force origin feature", "git clean -fd"])
def test_a_forged_mark_does_not_lower_the_rating(plain):
    forged = "{git -c core.x=$'\\x0f' " + plain[4:] + "}"
    want = policy.assess("Bash", {"command": "{" + plain + "}"}, cwd="/tmp").level
    got = policy.assess("Bash", {"command": forged}, cwd="/tmp").level
    assert policy.LEVELS[got] >= policy.LEVELS[want], forged


# ---------------------------------------------------------------- B3-5, PF-1: a brace group that is a group in every shell is read once

CLEAR_GROUPS = [
    "{ echo a; }",
    "{ echo a; } ",
    "{ echo a\n}",
    "echo a | { cat; }",
    "echo a && { cat; }",
    "true || { echo a; }",
    "( { echo a; } )",
    "{ { echo a; }; }",
    "f() { echo a; }",
]


@pytest.mark.parametrize("group", CLEAR_GROUPS)
def test_a_group_that_every_shell_reads_the_same_way_has_one_reading(group):
    command = "; ".join([group] * 50 + ["git push origin feature"])
    assert len(shellnorm.skeleton(command)) <= len(command) + 50, group


@pytest.mark.parametrize("command", [
    "if true { echo a; }",
    "if { true } { echo a }",
    "{ echo a }",
    "{echo a}",
    "echo a{ b",
    "git push origin { :main",
])
def test_an_ambiguous_brace_still_has_every_reading(command):
    assert len(shellnorm.skeleton(command)) > len(command), command


def test_a_clear_group_still_cuts_the_command():
    assert "git push origin feature" in cur_segments("{ echo a; } ; { git push origin feature; }")
    assert si08("{ echo a; }; { git push origin main; }")


@pytest.mark.timing
@pytest.mark.parametrize("group", ["{ echo a; }", "f() { echo a; }"])
@retry_under_load
def test_a_script_of_brace_groups_costs_about_what_the_same_commands_cost(group):
    def cost(piece: str) -> float:
        command = "; ".join([piece] * 2000 + ["git push origin feature"])
        policy.assess("Bash", {"command": "echo warm"}, cwd="/tmp")
        shellnorm._skeleton_top.cache_clear()
        best = 1e9
        for _ in range(3):
            shellnorm._skeleton_top.cache_clear()
            t = time.process_time()
            policy.assess("Bash", {"command": command}, cwd="/tmp")
            best = min(best, time.process_time() - t)
        return best

    assert cost(group) < 3 * cost("echo a") + 0.3
