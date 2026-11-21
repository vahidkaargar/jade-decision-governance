"""Round-4 review of PR 47 (W-136): commands that origin/main stops and this branch let through, and the cost of ordinary brace words.

The merge bar: fix the redirect that hid the rest of a push, never be weaker than origin/main on any command, cost at most about 3 times as much on realistic input.

* R4-O1, CR4-1: a here-string glued to its program (`cat<<<'x'|sh`) or with a descriptor (`cat 0<<< 'x'`) was not read. The redirect hoist took the first word of the here-string for its file.
* R4-O2: `git push origin main& >log`: the move of a redirect that follows a `&` wrote `&` and `>` next to each other, and `&>` is another operator.
* R4-O3: a brace inside a pipeline stage cut the pipeline, so the text an `echo` hands to `sh` was cut short at the brace.
* CR4-2: only the first heredoc of a process substitution was read.
* DIFF-W1: a closed descriptor (`2>&-`) lowered a secret-file read from critical to medium.
* DIFF-W2: a chain of here-strings 10 deep was read twice per level.
* R5-L4-1: a shell with a descriptor redirect after it (`sed p<<<'x'|sh 2>&1`) took the `2` for a script name, so the stage was not read as code.
* CODE5-1: a reading with a `)` that closes nothing (`echo a\\)b`) was not written as a subshell, so `cd sub` ran twice for the lines after it.
* R5-DIFF-1: an unquoted here-string (`cat <<< git push origin :main | sh`) is the word `git` only, so no shell pushes: origin/main stops it, this branch need not.
* DIFF-P2, R4-O4, CR4-3: ordinary words with a brace (`stash@{0}`, `find -exec {} \\;`, `xargs -I{}`) and escaped operators cost several times what they cost on origin/main.
"""

import shlex
import shutil
import subprocess
import time

import pytest

from jade import hooks, policy, shellnorm
from tests.loadproof import retry_under_load
from tests.test_redirect_anywhere import si08
from tests.test_redirect_zsh_and_braces import confirm_real_push, really_pushes

PUSH = "git push origin main"

# ---------------------------------------------------------------- R4-O1, CR4-1: a here-string in every token layout

HERE_STRINGS = [
    "cat<<<'git push origin main'|sh",
    "cat<<<'git push origin :main'|sh",
    "cat<<<'git push origin HEAD:main'|sh",
    "cat<<<\"git push origin main\"|sh",
    "cat<<<'git push origin main' | sh",
    "cat<<<'git push origin main' |sh",
    "cat 0<<< 'git push origin main' | sh",
    "cat 0<<<'git push origin main' | sh",
    "cat - 0<<< 'git push origin main' | sh",
    "0<<< 'git push origin main' cat | sh",
    "tee<<<'git push origin main'|sh",
    "tee f<<<'git push origin main'|sh",
    "tee /dev/null<<<'git push origin main'|sh",
    "sed p<<<'git push origin main'|sh",
    "cat -u<<<'git push origin main'|sh",
    "/bin/cat<<<'git push origin main'|sh",
    "fold -w 999<<<'git push origin main'|sh",
    "uniq 0<<<'git push origin main'|sh",
    "cat<<<'git push origin main'|bash",
    "cat<<<'git push origin main'|zsh",
    "cat<<<'git push origin main'|sh -s",
    "cat<<<'git push origin main'|env sh",
    "cat<<<'git push origin main'|tee f|sh",
    "bash <(cat<<<'git push origin main')",
    "sh <(cat<<<'git push origin :main')",
    "sh < <(cat<<<'git push origin :main')",
    ". <(cat 0<<<'git push origin :main')",
    "eval \"$(cat<<<'git push origin :main')\"",
    "sh -c \"$(cat 0<<< 'git push origin :main')\"",
    "cat<<<'echo hi; git push origin main'|sh",
]


@pytest.mark.parametrize("command", HERE_STRINGS)
def test_a_here_string_in_any_layout_is_read(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


@pytest.mark.parametrize("plain", ["rm -rf /Users/x/proj", "git reset --hard", "git push --force origin feature", "git clean -fd"])
@pytest.mark.parametrize("wrapper", ["cat<<<'{c}'|sh", "cat 0<<< '{c}' | sh", "tee f<<<'{c}'|sh", "bash <(cat<<<'{c}')", "eval \"$(cat<<<'{c}')\""])
def test_a_glued_here_string_keeps_the_rating(wrapper, plain):
    want = policy.assess("Bash", {"command": plain}, cwd="/tmp").level
    got = policy.assess("Bash", {"command": wrapper.format(c=plain)}, cwd="/tmp").level
    assert policy.LEVELS[got] >= policy.LEVELS[want], wrapper


def test_a_here_string_names_no_file():
    assert "git" in shellnorm.skeleton("cat<<<git push origin main|sh").split("|")[0].replace("<<<", " ").split()


# ---------------------------------------------------------------- DIFF-W2: a chain of here-strings

def nested(depth: int) -> str:
    command = PUSH
    for _ in range(depth):
        command = "cat <<< " + shlex.quote(command) + " | sh"
    return command


@pytest.mark.parametrize("depth", [1, 5, 9, 10, 12, 13])
def test_a_chain_of_here_strings_is_read_at_any_depth(depth):
    assert si08(nested(depth)), depth


def test_a_chain_of_here_strings_costs_about_one_read_per_level():
    command = nested(8)
    shellnorm._skeleton_top.cache_clear()
    assert len(shellnorm.skeleton(command)) < 20 * len(command)


# ---------------------------------------------------------------- CR4-2: every heredoc of a process substitution

HEREDOCS = [
    "bash <(cat <<'EOF'\necho hi\nEOF\ncat <<'EOF'\ngit push origin :main\nEOF\n)",
    "bash <(cat <<EOF1\necho hi\nEOF1\ncat <<EOF2\ngit push origin :main\nEOF2\n)",
    "bash <(cat <<A <<'B'\necho hi\nA\ngit push origin :main\nB\n)",
    "bash <(cat <<-EOF\n\techo hi\n\tEOF\ncat <<-EOF2\n\tgit push origin :main\n\tEOF2\n)",
    "bash <(cat <<EOF\necho \\$(date)\nEOF\ncat <<EOF2\ngit push origin :main\nEOF2\n)",
    "bash <(cat <<< 'X'; cat <<EOF\nX\ngit push origin :main\nEOF\n)",
    "bash <(cat <<EOF\necho hi\ngit push origin :main\nEOF\n)",
]


@pytest.mark.parametrize("command", HEREDOCS)
def test_every_heredoc_of_a_process_substitution_is_read(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


# ---------------------------------------------------------------- R4-O2: a push in the background, the redirect after a blank

BACKGROUND = [
    "git push origin main& >log",
    "git push origin main&  >log",
    "git push origin main&\t>log",
    "git push origin main& > log",
    "git push origin main& >>log",
    "git push origin main& &>log",
    "git push origin main& >log &",
    "git push origin main& >log; echo",
    "git push origin main& >log && echo",
    "git push origin main& >log 2>&1",
    "git push origin main& 2>&1 >log",
    "git push origin main& >|log",
    "git push origin main& >!log",
    "git push origin main& >&log",
    "git push origin main& >\"log\"",
    "git push origin HEAD:main& >log",
    "git push origin :main& >log",
    "git push origin feature :main& >log",
    "git push -f origin main& >log",
    "git push --delete origin main& >log",
    "echo a; git push origin main& >log",
    "true && git push origin main& >log",
    "{ git push origin main& >log; }",
]


@pytest.mark.parametrize("command", BACKGROUND)
def test_a_background_push_with_a_redirect_after_a_blank_is_stopped(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


def test_the_redirect_after_an_ampersand_does_not_join_it():
    assert "&>" not in shellnorm.skeleton("git push origin main& >log")
    assert "&>" not in shellnorm.skeleton("true & >log")


# ---------------------------------------------------------------- R4-O3: a brace inside the text an echo hands to a shell

BRACE_IN_ECHO = [
    "nocorrect echo git push origin main {x | sh",
    "nocorrect echo git push origin HEAD:main {x | zsh",
    "nocorrect nocorrect echo git push origin main {x | sh",
    "nocorrect echo git push origin feature :main {x | sh",
    "nocorrect echo git push origin main x} | sh",
    "nocorrect echo git push origin main {a | sh",
    "nocorrect echo git push origin main {} x | sh",
    "nocorrect noglob echo git push origin main {x | sh",
    "echo git push origin main {x | sh",
    "echo git push origin main x} | sh",
    "echo git push origin main a{b | zsh",
    "command echo git push origin main {x | sh",
    "env echo git push origin main {x | sh",
    "echo git push origin feature :main {x | sh",
    "printf '%s ' git push origin main {x | sh",
]


@pytest.mark.parametrize("command", BRACE_IN_ECHO)
def test_a_brace_in_text_that_a_shell_reads_does_not_hide_a_push(command, tmp_path):
    if really_pushes(command, tmp_path):
        assert si08(command), command


@pytest.mark.parametrize("command", BRACE_IN_ECHO[:3])
def test_the_findings_of_r4_o3(command, tmp_path):
    confirm_real_push(command, tmp_path)
    assert si08(command), command


# ---------------------------------------------------------------- DIFF-W1: a closed descriptor does not lower a rating

CLOSED = ["2>&-", ">&-", "1>&-", "<&-", "0<&-"]


@pytest.mark.parametrize("close", ["2>&-", ">&-", "1>&-"])   # origin/main rates a read of a key with a closed input (`<&-`) medium, in every place but a leading one
@pytest.mark.parametrize("place", ["cat /Users/x/.ssh/id_rsa {r}", "cat {r} /Users/x/.ssh/id_rsa", "{r} cat /Users/x/.ssh/id_rsa"])
def test_a_closed_descriptor_keeps_a_secret_read_critical(close, place):
    command = place.format(r=close)
    assert policy.assess("Bash", {"command": command}, cwd="/tmp").level == "critical", command


@pytest.mark.parametrize("command", [
    "cat x > /Users/x/.ssh/id_rsa",
    "> /Users/x/.ssh/id_rsa cat x",
    "cat x 2> /Users/x/.ssh/id_rsa",
    "echo k &> /Users/x/.ssh/id_rsa",
    "echo k >> /Users/x/.ssh/id_rsa",
    "echo k >| /Users/x/.ssh/id_rsa",
    "cat x >&- > /Users/x/.ssh/id_rsa",
])
def test_a_real_write_to_a_key_stays_critical(command):
    """DIFF-W1: what the redirect move changes is the rating of a read with a leading input redirect (`0<&- cat key`: critical on main only because the command starts with a
    redirect, medium as `cat key 0<&-` on both). A write to a key is critical wherever the redirect stands."""
    assert policy.assess("Bash", {"command": command}, cwd="/tmp").level == "critical", command


@pytest.mark.parametrize("close", CLOSED)
def test_a_closed_descriptor_keeps_a_push_high(close):
    for command in (f"git push origin feature {close}", f"git push {close} origin feature", f"git push origin {close} feature"):
        assert policy.assess("Bash", {"command": command}, cwd="/tmp").level == "high", command


@pytest.mark.parametrize("close", CLOSED)
def test_a_closed_descriptor_does_not_hide_a_push_to_main(close, tmp_path):
    for command in (f"git push origin main {close}", f"git push {close} origin main", f"git push origin {close} main"):
        confirm_real_push(command, tmp_path)
        assert si08(command), command


# ---------------------------------------------------------------- DIFF-P2, R4-O4, CR4-3: what ordinary brace words and escapes cost

ORDINARY_BRACE_LINES = [
    "git stash apply stash@{0}",
    "find . -name '*.py' -exec grep -l foo {} \\;",
    "xargs -I{} cp {} /tmp/x < list",
    "git log -1 --format=%H HEAD@{1}",
    "cp file{,.bak}",
    "touch f{1..5}",
    "mkdir -p a/{b,c}",
    "echo ${x:-y} ${#z}",
    "function g { ls; }; g",
    "git diff @{u}",
]


@pytest.mark.parametrize("line", ORDINARY_BRACE_LINES)
def test_an_ordinary_brace_word_has_two_readings_at_most(line):
    """The reading that cuts at every brace (main), the one that takes it for a word, and the escaped operator both ways: the line a few times, not ten times.

    W-135: the stage is also read whole (a brace being a character of a word), so `find ... -exec grep -l foo {} \\;` carries the `-exec` command in one more spelling: 7 times, not 5."""
    assert len(shellnorm.skeleton(line)) <= 7 * len(line) + 40, line


def test_a_script_with_brace_words_costs_a_few_lines_not_a_few_scripts():
    """A reading of a line is written beside the line (DIFF-P2): the other lines of the script are not read again."""
    script = "\n".join(ORDINARY_BRACE_LINES * 20)
    assert len(shellnorm.skeleton(script)) <= 5 * len(script) + 40 * 20 * len(ORDINARY_BRACE_LINES)
    plain = "\n".join(["git status", "ls -la", "echo hi"] * 50 + ["git stash apply stash@{0}"])
    assert len(shellnorm.skeleton(plain)) <= len(plain) + 60


def test_a_top_level_escaped_operator_has_few_readings():
    command = "find . -name x -exec rm {} \\;; git push origin feature a\\&b"
    assert shellnorm.skeleton(command).count("git push origin feature") <= 4


def test_a_long_run_of_one_word_is_cut_in_the_readings(tmp_path):
    """R4-O4: the same segments are read again and again: 3000 repeats cost as 4 do, and the push after them is still read."""
    command = "git push origin feature " + "{ a\\&>log " * 3000 + " :main"
    assert len(shellnorm.skeleton(command)) < 2000
    assert si08(command)
    assert si08("echo 'git push origin " + "a\\>&log " * 3000 + " main' | sh")
    assert shellnorm.skeleton("cd a; " + "cd .. ; " * 400 + "echo a\\&b").count("cd ..") >= 400   # a run that moves the directory is kept


@pytest.mark.timing
@pytest.mark.parametrize("make", [
    lambda n: "\n".join(ORDINARY_BRACE_LINES * n),
    lambda n: "; ".join(["find . -exec rm {} \\;"] * (n * 10)),
    lambda n: "git push origin feature " + "a\\&b a{ } " * (n * 10),
    lambda n: "echo 'git push origin feature " + "a\\>&log " * (n * 100) + "' | sh",
])
@retry_under_load
def test_brace_words_and_escapes_cost_about_what_the_same_words_cost(make):
    def cost(command: str) -> float:
        best = 1e9
        for _ in range(3):
            shellnorm._skeleton_top.cache_clear()
            t = time.process_time()
            policy.assess("Bash", {"command": command}, cwd="/tmp")
            best = min(best, time.process_time() - t)
        return best

    policy.assess("Bash", {"command": "echo warm"}, cwd="/tmp")
    command = make(30)
    plain = command.replace("{", "x").replace("}", "y").replace("\\", "")   # no brace and no escape: what origin/main reads as plain words
    assert cost(command) < 4 * cost(plain) + 0.3


# ---------------------------------------------------------------- the review of this round's own fix

def test_a_here_string_in_quotes_is_text_not_an_operator(tmp_path):
    """`echo 'git push origin <<< x main' | sh` prints the whole text, `<<< x` included: the push is read."""
    for command in ("echo 'git push origin <<< x main' | sh", "echo \"git push --delete origin <<<'x' main\" | sh", "echo 'git push 0<<< x origin main' | sh"):
        confirm_real_push(command, tmp_path)
        assert si08(command), command
    assert shellnorm._here_strings("cat 0<<< 'a b' x") == ("cat   x", ["a b"])
    assert shellnorm._here_strings("cat<<<a\\ b") == ("cat ", ["a b"])
    assert shellnorm._here_strings("echo '<<< x'") == ("echo '<<< x'", [])
    assert shellnorm._here_strings("cat a2<<< x")[0].startswith("cat a2")   # the 2 ends a word: it is not a descriptor


def _two_repos(root):
    """The outer repository is on `feature`, the one in sub/ is on `main`."""
    for path, branch in ((root, "feature"), (root / "sub", "main")):
        path.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.email=a@b", "-c", "user.name=n", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    return str(root)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("command", [
    "cd sub && echo a{b\nPUSH",
    "cd sub && echo a\\>b\nPUSH",
    "cd sub; echo {x\nPUSH",
    "echo a{b; cd sub\nPUSH",
    "pushd sub && echo a{b\nPUSH",
    "cd sub && echo a\\&b && PUSH",
    "cd sub && echo a{b && PUSH",
    "cd sub\ncd ..\ncd sub && echo a{b\nPUSH",
])
def test_a_reading_written_beside_a_line_does_not_move_the_directory_twice(command, tmp_path):
    """The readings of a line that moves the directory are subshells: `cd sub` is read once for the lines that follow, so a bare push after it is a push on sub's branch."""
    outer = _two_repos(tmp_path / "outer")
    command = command.replace("PUSH", "git " + "push")
    assert hooks._on_default_branch(outer, command), command
    assert not hooks._on_default_branch(outer, command.replace(" sub", " .")), command


def test_a_run_that_counts_is_kept_and_a_plain_run_is_cut():
    counted = "git " + "-C .. " * 12 + "push origin main { x"
    assert shellnorm._collapse_runs(counted) == counted
    assert shellnorm._collapse_runs("a " * 12) == "a " * 4
    assert shellnorm._collapse_runs("cd .. ; " * 12) == "cd .. ; " * 12
    assert shellnorm._collapse_runs("x y z " * 12) == "x y z " * 4


@pytest.mark.timing
@pytest.mark.parametrize("depth, pipes", [(6, 8), (8, 4), (10, 3)])
@retry_under_load
def test_a_chain_of_pipelines_is_not_read_once_per_pipe_per_level(depth, pipes):
    """Each `| sh` after a here-string re-produced the text of the stage: the output grew as pipes ** depth (24 s for 1.7 kB on main)."""
    command = PUSH
    for _ in range(depth):
        command = "cat <<< " + shlex.quote(command) + " | sh" * pipes
    shellnorm._skeleton_top.cache_clear()
    start = time.process_time()
    shellnorm.skeleton(command)   # the current reading; the gate also runs the reading of origin/main in full (round 7), which costs what it cost there
    assert time.process_time() - start < 5
    assert si08(command)


@pytest.mark.timing
@retry_under_load
def test_heredoc_bodies_with_backslashes_are_not_squared_by_a_nest():
    command = PUSH
    for k in range(10):
        command = "bash <(cat <<'EOF'\n" + command.replace("\\", "\\\\") + "\nEOF\n)" if k % 2 else "cat <<< " + shlex.quote(command) + " | sh"
    start = time.process_time()
    assert si08(command)
    assert time.process_time() - start < 5


# ---------------------------------------------------------------- round 5: a descriptor redirect after the shell, a `)` that closes nothing, an unquoted here-string

@pytest.mark.parametrize("producer", ["sed p", "uniq", "sort -u", "sed -n p", "sed 1p", "cat", "tee f"])
@pytest.mark.parametrize("sink", ["sh 2>&1", "sh 2>/dev/null", "sh -x 2>&1", "bash 2>&1", "zsh 2>&1", "dash 2>&1", "env sh 2>&1", "nohup sh 2>&1", "sh 1>&2", "sh 3>&1",
                                  "sh 2<&1", "sh 2>>log", "sh 2>err", "sh 2>&1 >/dev/null", "sh 0<&0 2>&1", "sh -e 2>&1", "sh -s 2>&1", "sh >/dev/null", "sh"])
def test_a_descriptor_redirect_after_the_shell_does_not_hide_the_text(producer, sink, tmp_path):
    command = f"{producer}<<<'{PUSH}'|{sink}"
    assert si08(command), command
    if sink in ("sh 2>&1", "bash 2>&1", "zsh 2>&1", "sh 2>/dev/null"):
        confirm_real_push(command, tmp_path)


@pytest.mark.parametrize("payload", ["git push origin :main", "git push origin HEAD:main", "git push --delete origin main", "git push -f origin main", "git reset --hard"])
def test_the_payload_of_a_here_string_into_a_shell_with_a_redirect_is_read(payload):
    assert policy.assess("Bash", {"command": f"sed p<<<'{payload}'|sh 2>&1"}, cwd="/tmp").level == "high"


@pytest.mark.parametrize("command", [
    "sh 2>&1", "bash 2>/dev/null", "env sh 3>&1", "sh -x 2>&1",
])
def test_the_stage_after_a_pipe_is_code_with_a_descriptor_redirect(command):
    assert shellnorm._next_stage_kind(command, 0) == "shell", command


def test_a_digit_that_is_a_script_name_is_not_a_descriptor():
    assert shellnorm._next_stage_kind("sh 2 >x", 0) is None   # `2` is a file name here: the redirect is `>x`
    assert shellnorm._next_stage_kind("sh 2", 0) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("command", [
    "cd sub && echo a\\)b\nPUSH",
    "cd sub; case $x in y) echo a{b;; esac\nPUSH",
    "cd sub && echo :\\)\nPUSH",
    "cd sub && echo a\\(b\nPUSH",
    "cd sub && echo a\\)b && PUSH",
    "cd sub\necho a\\)b\nPUSH",
    "cd sub && echo \")\" && echo a{b\nPUSH",
    "cd sub; case $x in y) nocorrect echo PUSH {x | sh;; esac",
    "cd sub && echo a\\\\)b && nocorrect echo PUSH {x | sh",
    "cd sub && echo it\\\\'s \\\\( don\\\\'t a{b\nPUSH",
    "cd sub && echo 'a)' a{b\nPUSH",
    "cd sub; echo \\\\) {x ; PUSH",
])
def test_a_reading_with_an_unbalanced_parenthesis_does_not_move_the_directory_twice(command, tmp_path):
    outer = _two_repos(tmp_path / "outer")
    command = command.replace("PUSH", "git " + "push")
    assert hooks._on_default_branch(outer, command), command
    assert not hooks._on_default_branch(outer, command.replace(" sub", " .")), command


@pytest.mark.parametrize("text", ["a b", "a ) b", "a ( b", "a )( b", "a ))) b ((", "echo \")\"", "echo ')' ( x"])
def test_a_subshell_wrapper_holds_what_it_wraps_whole(text):
    """The depth is 1 or more from the first character to the one before the last, and 0 at the end: nothing in the text closes the wrapper early or leaves it open."""
    depth, profile = 0, []
    for ch in shellnorm._QUOTED.sub("", shellnorm._subshell(text)):
        depth += (ch == "(") - (ch == ")")
        profile.append(depth)
    assert profile[-1] == 0 and min(profile[:-1]) >= 1, (text, shellnorm._subshell(text))


@pytest.mark.parametrize("command", [
    "cat 0<<< git push origin :main | sh", "cat <<< git push origin :main | sh", "cat<<<git push origin :main|sh",
    "tee f<<<git push origin main|sh", "sed p<<<git push origin main|sh", "uniq <<<git push origin main|sh",
])
def test_an_unquoted_here_string_is_one_word_and_pushes_nothing(command, tmp_path):
    """The here-string is the word `git`; `push origin :main` are arguments of the first program. No shell pushes, so a lower rating than main's is not a miss."""
    assert not really_pushes(command, tmp_path), command


def test_a_key_read_after_a_leading_input_redirect_is_not_rated_below_the_trailing_one():
    """Main rated `<&- cat key` critical only because the command starts with a redirect; `cat key <&-` was medium. The gate reads both readings and takes the stricter,
    so the leading form keeps main's rating (R5-L4-2, round 6)."""
    key = "/Users/x/.ssh/id_rsa"
    lead = policy.assess("Bash", {"command": f"<&- cat {key}"}, cwd="/tmp").level
    assert policy.LEVELS[lead] >= policy.LEVELS[policy.assess("Bash", {"command": f"cat {key} <&-"}, cwd="/tmp").level]


SINKS = [
    "2>&1 sh", "   >/dev/null sh", "2>/dev/null env sh", "sh </dev/stdin", "sh 0</dev/stdin", "sh < /dev/fd/0", "(sh)", "{ sh; }", "tee >(sh)",
    "while read l; do eval \"$l\"; done", "xargs -I% sh -c %", "sh -c 'sh'", "sh /dev/stdin", ". /dev/stdin",
]


@pytest.mark.parametrize("producer", ["sed p<<<'git push origin main'", "uniq <<< 'git push origin :main'", "sort -u<<<'git push origin HEAD:main'"])
@pytest.mark.parametrize("sink", SINKS)
def test_the_text_a_stage_passes_on_is_read_whatever_the_reader_is(producer, sink):
    """The reader after the pipe may be a spelling the splitter does not know. A non-printer stage's text is read as shell by the reading origin/main gave
    (`shellnorm.skeleton_both` keeps it), so no spelling of the reader makes it less gated than main did."""
    command = f"{producer} | {sink}"
    assert si08(command), command


@pytest.mark.parametrize("producer", ["sed p", "uniq", "sort -u", "cat", "tee f"])
@pytest.mark.parametrize("quote", ["E", "'E'"])
def test_a_heredoc_into_a_shell_with_a_descriptor_redirect_is_read(producer, quote, tmp_path):
    """The heredoc of such a stage came in the text of the command after its line; origin/main did not stop `sed p <<E | sh 2>&1` at all."""
    command = f"{producer} <<{quote} | sh 2>&1\n{PUSH}\nE\n"
    assert si08(command), command


def test_a_printer_with_a_reader_that_is_not_known_is_words():
    assert not si08("echo git push origin main | wc -l")
    assert not si08("cat <<< 'git push origin main' | wc -l")


@pytest.mark.parametrize("sink", ["2>&1 sh", "sh </dev/stdin", "sh /dev/stdin", "(sh)", "{ sh; }", "sh 2>/dev/null", ". /dev/stdin"])
def test_these_readers_run_the_text_in_the_real_shells(sink, tmp_path):
    command = f"sed p<<<'{PUSH}' | {sink}"
    if shutil.which("bash") and shutil.which("zsh") and sink not in ("sh /dev/stdin", ". /dev/stdin"):   # `/dev/stdin` as a file is not a pipe on every system
        confirm_real_push(command, tmp_path)
    assert si08(command), command
