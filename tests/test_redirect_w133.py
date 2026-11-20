"""W-133: the SI-08 gaps the round 2 review of PR 47 found that origin/main had as well (F5 to F10, DIFF-4, SEG-3).

Each command here is a push to the default branch by a spelling the gate did not read. The oracle is the real bash and zsh (`tests/test_redirect_anywhere.py`): when either
pushes to the default branch, the gate must stop the command. Where the real shell of this machine does not read the spelling (bash 3.2 has no `printf '\\u'`) the gate still
reads it, because the shell that runs the text may be any other (bash 5, a zsh).

Not closed here, and named in work-items.jsonl (W-133): F7 (`git $x push origin :main`, a variable that may be unset: the text is read, never evaluated), F11 (a push
built with `eval $x`, `$(echo git)`, `xargs`: evaluation), SEG-4 (`echo main | xargs git push origin`).
"""

import pytest

from tests.test_redirect_anywhere import agrees_with_the_shell, si08

PUSH = "git push origin HEAD:main"

# ---------------------------------------------------------------- the text a program prints: octal without a leading 0, \u and \U (F8, DIFF-4)

ESCAPED_TEXT = [
    "printf 'git pu\\163h origin HEAD:main' | sh",            # bash and zsh printf: \163 is s
    "printf 'git push origin HEAD:\\155ain' | sh",
    "printf 'git push origin \\155ain' | sh",
    "printf 'git push origin feature :\\155ain' | bash",
    "print 'git push origin \\155ain' | zsh",                 # zsh print
    "print 'git push origin HEAD:\\155ain' | sh",
    "echo 'git push origin HEAD:\\u006dain' | zsh",           # zsh echo: \u
    "echo 'git push origin HEAD:\\U0000006dain' | zsh",
    "printf 'git push origin HEAD:\\u006dain' | sh",
    "printf 'git push origin HEAD:\\U0000006dain' | bash",
    "print 'git push origin HEAD:\\u006dain' | zsh",
    "printf 'git push origin feature\\u0020:main' | sh",     # a space written as
    "printf 'git push origin feature\\040:main' | sh",       # a space written as \040
    "printf '%b' 'git push origin HEAD:\\0155ain' | sh",
    "echo -e 'git push origin HEAD:\\u006dain' | bash",
]


@pytest.mark.parametrize("command", ESCAPED_TEXT)
def test_an_escape_the_printer_decodes_never_hides_the_push(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("command", [
    "printf 'git push origin feature\\n' | sh",
    "echo 'git push origin \\u0066eature' | zsh",             # the branch feature
    "printf 'git push origin \\146eature' | sh",              # the branch feature
    "printf 'git push origin HEAD:\\155ainline' | sh",        # the branch mainline
    "printf 'git status \\155ain' | sh",
])
def test_an_escape_that_makes_no_push_to_the_default_branch_is_not_stopped(command):
    assert not si08(command), command


# ---------------------------------------------------------------- the printer behind a launcher or a path (F5: closed on the way to 0.7.0, pinned here)

WRAPPED_PRINTERS = ["/bin/echo", "command echo", "env echo", "builtin echo", "nice echo", "nohup echo", "exec echo", "/usr/bin/printf", "env printf", "command printf", "time echo", "FOO=1 printf", "! echo"]


@pytest.mark.parametrize("printer", WRAPPED_PRINTERS)
@pytest.mark.parametrize("consumer", ["sh", "bash", "zsh", "env sh", "sh -s", "nice sh", "nohup sh", "exec sh", "time sh"])
def test_a_wrapped_printer_piped_to_a_shell_is_read(printer, consumer, tmp_path):
    command = f"{printer} '{PUSH}' | {consumer}"
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


# ---------------------------------------------------------------- the shell that reads the pipe, with more options or inside a group (F6)
#
# Open, and recorded in W-133: the shell is not the first command of the group (`| (true; sh)`, `| { :; sh; }`, `| (cat | sh)`, `| (cd /tmp && sh)`).

CONSUMERS = ["sh -e -x", "bash -ex", "bash -e -u", "zsh -f -e", "sh -x -e", "(sh)", "( sh )", "{ sh; }", "{ bash -e; }", "(bash -e -x)", "sh -c sh", "bash -c 'sh'", "sh -e -c sh"]


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize("producer", ["echo '%s'", "printf '%s\\n'", "cat <<< '%s'"])
def test_a_shell_that_reads_the_pipe_with_more_options_or_in_a_group_is_read(producer, consumer, tmp_path):
    command = (producer % PUSH) + " | " + consumer
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("command", [
    f"echo '{PUSH}' | sh -x ./tool.sh",
    f"echo '{PUSH}' | sh -e ./tool.sh",
    f"echo '{PUSH}' | bash -ex tool.sh",
    f"echo '{PUSH}' | (cat)",
    f"echo '{PUSH}' | {{ cat; }}",
    f"echo '{PUSH}' | sh -c 'cat'",
    f"echo '{PUSH}' | sh -n",
])
def test_a_pipe_that_the_consumer_does_not_run_stays_text(command):
    assert not si08(command), command


# ---------------------------------------------------------------- a file written and then run through a launcher, or fed on stdin (F6)

FILE_RUNS = ["sh < f", "env sh f", "command sh f", "exec bash f", "nohup sh f", "time sh f", "nice sh f", "env -i sh f", "env X=1 sh f", "env -u X sh f", "sh -e < f", "bash < f", "zsh < f",
             "sh -x f", "bash -e -x f"]


@pytest.mark.parametrize("run", FILE_RUNS)
@pytest.mark.parametrize("write", ["echo '%s' > f", "printf '%s\\n' > f", "echo '%s' >> f"])
def test_a_file_the_command_writes_and_runs_is_read(write, run, tmp_path):
    command = (write % PUSH) + "; " + run
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


def test_a_heredoc_written_to_a_file_and_run_with_a_launcher_is_read(tmp_path):
    command = f"cat <<E\"O\"F > f\n{PUSH}\nEOF\nenv sh f"
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


# ---------------------------------------------------------------- a heredoc delimiter that is quoted in part (F9)

@pytest.mark.parametrize("delimiter", ["E\"O\"F", "E'O'F", "\"E\"OF", "EO\\F", "'EOF'", "\"EOF\"", "\\EOF", "E\"\"OF"])
@pytest.mark.parametrize("consumer", ["sh", "bash --norc", "zsh -s", "sh -e"])
def test_a_quoted_heredoc_delimiter_keeps_a_trailing_backslash_a_backslash(delimiter, consumer, tmp_path):
    command = f"{consumer} <<{delimiter}\n{PUSH}\\\nEOF"   # no line joining in a quoted heredoc: the shell reads `git push origin HEAD:main\` and then runs `EOF`
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


# ---------------------------------------------------------------- ANSI-C quotes: `\c` + space ends the string in bash (F10)

@pytest.mark.parametrize("command", [
    "eval $'git push origin feature :main\\c junk'",
    "echo $'git push origin feature :main\\c junk' | sh",
    "bash -c $'git push origin feature :main\\c junk'",
    "sh -c $'git push origin feature :main\\c junk'",
    "eval $'git push origin feature :main\\c@ junk'",
    "eval $'git push origin feature :main\\cj'",              # bash: `\cj` is a newline, the push ends there
    "eval $'git push origin feature :\\c main'",              # zsh reads `\c` as a plain `c`: the words are `:c` and `main`, and `main` is pushed
    "git push origin $'main\\c@'",                            # bash: no wrapper, the word itself ends at the NUL
    "git push origin feature $':main\\c junk'",
    "git push origin feature :main$'\\c@'",
    "git $'push' origin $'main\\c x'",
])


def test_ansi_c_backslash_c_does_not_hide_the_push(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("command, floor", [
    ("git $'\\clean' -fdx", "high"),   # zsh runs `git clean -fdx`: the rating is the one of that command (high on origin/main, and still)
    ("rm -rf $'/Users/x/.\\claude'", "high"),
    ("git reset $'--hard\\c x'", "medium"),   # bash ends the string at the NUL: `git reset --hard`, rated at least as the zsh reading `--hardc x`
])
def test_ansi_c_backslash_c_never_lowers_the_rating(command, floor):
    from jade import policy

    assert policy.LEVELS[policy.assess("Bash", {"command": command}, cwd="/tmp").level] >= policy.LEVELS[floor], command


# ---------------------------------------------------------------- SEG-3: a keyword before `{` is an argument (already closed on the way to this commit; pinned)

@pytest.mark.parametrize("word", ["then", "do", "else", "elif", "if", "while", "until", "time", "!", "always"])
def test_a_keyword_word_before_a_brace_does_not_cut_the_push(word):
    assert si08(f"git push origin feature {word} {{ :main"), word
