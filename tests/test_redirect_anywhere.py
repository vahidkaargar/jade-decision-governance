"""A redirect written anywhere in a command must not change what the gate sees (SI-08 bypass, found in review of the 0.6.x branches).

`git push origin feature &>log +feature:main` pushes `feature` to main with a force. The risk rules and SI-08 read the words of a command up to the next `&`, `|`
or `;`, so `&>log` hid every word after it: level medium, nothing blocked, no key. The same hole sat in `>&log`, `>&-`, `<&-` and `>|log`, and a redirect
written before the subcommand (`git >log push origin main`) hid the whole push. The shell takes a redirect out of the words wherever it stands, so the gate
does too: the call, with the redirect taken out, is what every rule reads (`shellnorm._hoist_redirects`).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jade import hooks, policy, shellnorm

FIXTURES = Path(__file__).parent / "fixtures"

# Redirects that carry a file name (a write or a read of a path), and redirects that carry none (a descriptor copy or close).
FILE_REDIRECTS = ["&>log", "&>>log", "&> log", "&>>  log", ">&log", ">& log", ">|log", ">log", "> log", "2>log", "2>>log", "1>log", "3>log",
                  "<in", "< in", "0<in", "<>log", "<<<x", "<<< x"]
FD_REDIRECTS = [">&-", "<&-", "2>&-", "2>&1", "1>&2", ">&2", "<&0", "3<&-", "&>/dev/null", "&>>/dev/null", ">/dev/null", "2>/dev/null", "</dev/null"]
GLUED = ["&>log", "&>>log", ">log", ">>log", ">&log", ">|log", "<in", "&>/dev/null"]   # no space before it: `feature&>log`

PUSHES = [
    "git push origin feature --no-verify +feature:main",
    "git push origin feature :main",
    "git push origin feature --force",
    "git push origin feature --mirror",
    "git push origin feature --delete",
    "git push origin feature --tags",
    "git push origin feature main",
    "git push origin feature feature:main",
    "git push origin feature +feature:main",
    "git push origin feature -o x",
    "git push --all",
    "git push --force",
]
OTHERS = [
    "git reset --hard HEAD~1",
    "git clean -fd",
    "git clean -fdx",
    "git branch -D old",
    "git branch -d -f old",
    "git checkout -- .",
    "rm -rf /Users/x/proj",
    "rm -r -f /Users/x/proj",
]

_REDIRECT_WORD = re.compile(r"^(?:\d*|&)(?:>>?|<>|<<<|<)[|&!]*")


def cur_segments(command: str) -> list[str]:
    """The segments of the current reading alone (`shellnorm.skeleton`), without the reading origin/main gave that `shellnorm.segments` adds."""
    return [re.sub(r"\s+", " ", seg).strip() for seg in shellnorm.SPLIT.split(shellnorm.skeleton(command)) if seg.strip()]


class Verdict(tuple):
    """What the gate decides: level, rule, blocked invariants, human-only classes, gated segment keys (a redirect cut off).

    The gate reads a command in two readings (`shellnorm.skeleton_both`: the current one and the one origin/main gave), so a spelling with a redirect may carry the words of
    the second reading as well. `a == b` therefore means: `a` is at least as gated as `b` (level, blocked and human-only classes), and every key of `b` is a key of `a`;
    when the level is the same the rule is the same."""

    def __eq__(self, other):
        level, rule, blocked, ho, keys = self
        o_level, o_rule, o_blocked, o_ho, o_keys = other
        if policy.LEVELS[level] < policy.LEVELS[o_level] or not set(o_blocked) <= set(blocked) or not set(o_ho) <= set(ho) or not set(o_keys) <= set(keys):
            return False
        return level != o_level or rule == o_rule or bool(set(blocked) - set(o_blocked))

    __hash__ = None


def sig(command: str) -> tuple:
    """What the gate decides for a command: level, rule, blocked invariants, human-only classes and the gated segment keys with any redirect cut off."""
    a = policy.assess("Bash", {"command": command}, cwd="/tmp")
    keys = []
    for k in a.seg_keys:
        words = k.split(" ")
        cut = next((i for i, w in enumerate(words) if _REDIRECT_WORD.match(w)), len(words))
        if cut:
            keys.append(" ".join(words[:cut]))   # a key that is only a redirect is a reading of the other spelling
    return Verdict((a.level, a.rule_id, tuple(a.blocked), tuple(a.ho_ids), tuple(sorted(set(keys)))))


def si08(command: str) -> bool:
    """Is the command stopped as a push to the default branch: the pattern in the assessment, or the hook that parses the push arguments (`--mirror`)."""
    return "SI-08" in policy.assess("Bash", {"command": command}, cwd="/tmp").blocked or hooks._on_default_branch("/tmp", command)


def inserted(base: str, redirect: str) -> list[str]:
    """`base` with `redirect` written as its own word at every position, in front of the program included, and at the end."""
    w = base.split(" ")
    return [" ".join(w[:i] + [redirect] + w[i:]) for i in range(len(w) + 1)]


def glued(base: str, redirect: str) -> list[str]:
    """`base` with `redirect` written against the end of each word (`feature&>log`): the shell ends the word at the operator."""
    w = base.split(" ")
    return [" ".join(w[:i] + [w[i] + redirect] + w[i + 1:]) for i in range(1, len(w))]


def fixture(name: str) -> list[str]:
    return json.loads((FIXTURES / name).read_text())


def reference(command: str) -> str:
    """The reported commands with the redirect written out of them (what the shell runs once it has taken the redirect out of the words)."""
    c = re.sub(r"(?<=\s)(?:&>>?|\d*>>?&?|\d*<)\s?(?:log|in|1)(?=\s|$)", "", command)
    c = re.sub(r"(?<=[a-z])&>log(?=\s|$)", "", c)
    return re.sub(r"\s+", " ", c).strip()


# ---------------------------------------------------------------- the reported inputs

@pytest.mark.parametrize("command", fixture("redirect_push_cmds4.json") + fixture("redirect_push_cmds5.json"))
def test_reported_push_is_read_as_the_push_without_the_redirect(command):
    want = sig(reference(command))
    assert sig(command) == want, f"{command!r} differs from {reference(command)!r}"


@pytest.mark.parametrize("command", [c for c in fixture("redirect_push_cmds4.json") + fixture("redirect_push_cmds5.json")
                                     if re.search(r"(?::main|--mirror|\bmain\b)", c)])
def test_reported_push_to_main_is_blocked_and_keyed(command):
    level, _rule, _blocked, _ho, keys = sig(command)
    assert si08(command), command
    assert level == "high" and keys, command


@pytest.mark.parametrize("command", [c for c in fixture("redirect_push_cmds4.json") + fixture("redirect_push_cmds5.json") if "main" not in c])
def test_reported_push_is_keyed_and_high(command):
    level, _rule, _blocked, _ho, keys = sig(command)
    assert level == "high" and keys, command


@pytest.mark.parametrize("command", [
    "git push origin feature &>log --no-verify +feature:main",
    "git push origin feature &>log :main",
    "git push origin feature &>>log --no-verify :main",
    "git push origin feature &> log :main",
    "git push origin feature >&log :main",
    "git push origin feature >&- :main",
    "git push origin feature >|log :main",
    "git push origin feature&>log :main",
    "git >log push origin feature :main",
    "git push origin feature <in +feature:main",
    "cd x && git push origin feature &>log :main",
    "echo hi; git push origin feature &>log --mirror",
    "git push origin feature &>log --mirror | cat",
    "&>log git push origin feature :main",
])
def test_si08_blocks_with_a_redirect_in_the_push(command):
    assert si08(command), command


# ---------------------------------------------------------------- every redirect at every position

@pytest.mark.parametrize("base", PUSHES + OTHERS)
@pytest.mark.parametrize("redirect", FILE_REDIRECTS + FD_REDIRECTS)
def test_redirect_in_any_position_changes_nothing(base, redirect):
    want = sig(base)
    for command in inserted(base, redirect):
        assert sig(command) == want, f"{command!r}"


@pytest.mark.parametrize("base", PUSHES + OTHERS)
@pytest.mark.parametrize("redirect", GLUED)
def test_redirect_glued_to_a_word_changes_nothing(base, redirect):
    want = sig(base)
    for command in glued(base, redirect):
        assert sig(command) == want, f"{command!r}"


@pytest.mark.parametrize("redirect", ["&>log", ">&log", ">|log", "2>log", "<in"])
def test_two_redirects_and_a_subshell(redirect):
    for command in (f"git push origin feature {redirect} {redirect} :main", f"(git push origin feature {redirect} :main)",
                    f"{{ git push origin feature {redirect} :main; }}", f"true && git push {redirect} origin feature :main"):
        assert si08(command), command


# ---------------------------------------------------------------- what must not change

def test_a_redirect_is_still_a_write():
    """Taking a redirect out of the words must not hide the file it writes."""
    target = "~/.claude/settings.json"   # a file the gate rates high for any write (R-13)
    for command in (f"echo x > {target}", f"echo x &> {target}", f"echo x &>>{target}", f"echo x >&{target}", f"echo x >|{target}", f"echo x 2>{target}",
                    f"> {target} echo x", f"&>{target} echo x", f"echo &>{target} x"):
        a = policy.assess("Bash", {"command": command}, cwd="/tmp")
        assert (a.level, a.rule_id) == ("high", "R-13"), command


def test_quoted_and_escaped_operators_are_data():
    assert cur_segments("echo 'a>b' c") == ["echo a\x06b c"]
    assert "git log --format=%h\x06x" in cur_segments("git log --format=%h\\>x")   # an escaped operator is data the way a quoted one is (W-96); the reading as syntax is kept too (W-113)


def test_skeleton_puts_the_redirect_last_and_spells_it_one_way():
    assert cur_segments("git push origin feature &>log :main") == ["git push origin feature :main > log"]
    assert cur_segments("git push origin feature >&log :main") == ["git push origin feature :main > log"]
    assert cur_segments("git >|log push origin") == ["git push origin > log"]
    assert cur_segments("git push >&- origin 2>&- feature") == ["git push origin feature >&- 2>&-"]   # a close names no file: it moves, as written (W-115)
    assert cur_segments("git push origin feature&>>log :main") == ["git push origin feature :main >> log"]
    assert cur_segments("echo a2>log b") == ["echo a2 b > log"]   # the 2 belongs to the word `a2`, not to the redirect
    assert cur_segments("git push 2>log origin") == ["git push origin 2> log"]


# ---------------------------------------------------------------- an escaped operator is data, a real one after an escaped backslash is not (W-96)
#
# `a\&>log` is the word `a&` and a redirect `>log`; `a\\&>log` is the word `a\` and a redirect `&>log`. The scanner decides which is which (an escaped
# operator character becomes the inert mark, as a quoted one does), because no regex can tell an escaped backslash from an escape. The oracle below runs the
# command in the real bash and zsh with a `git` function that records its arguments in a file (never in the redirected output), and the gate must stop every
# command in which real git would push to the default branch.

ESCAPED_OPERATORS = [";", "|", "(", ")", "{", "}", "&", "<", ">", "\\&\\&", "\\|\\|", "\\;\\;"]
PUSH_TO_MAIN = ["feature :main", "feature main", "feature +feature:main", "feature feature:main", "--no-verify feature +feature:main", "feature --mirror"]
PRELUDE = "git() { printf '%s\\0' \"$@\" >> \"$ARGV_OUT\"; printf '\\1' >> \"$ARGV_OUT\"; }; export -f git 2>/dev/null; "   # the shell that runs the command: a function, fast
FAKE_GIT = "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\0' \"$a\" >> \"$ARGV_OUT\"; done\nprintf '\\1' >> \"$ARGV_OUT\"\n"


def ran_git(command: str, shell: str, where: Path) -> list[list[str]]:
    """The argument lists the shells pass to `git` for `command` (a function stands in for git in the shell that runs it, and a program named `git` on PATH in any
    shell that one starts: `sh -c`, a heredoc or a pipe into `zsh`; nothing else is run)."""
    out = where / "argv"
    out.write_bytes(b"")
    fake = where / "bin" / "git"
    if not fake.exists():
        fake.parent.mkdir(exist_ok=True)
        fake.write_text(FAKE_GIT)
        fake.chmod(0o755)
    script = where / "command.sh"
    script.write_text(PRELUDE + command + "\n")   # a file, as the review ran it: `zsh -c` and `zsh file` do not always agree
    try:
        subprocess.run([shell, *(["-f"] if shell == "zsh" else []), str(script)], cwd=where, env={"PATH": f"{fake.parent}:/usr/bin:/bin", "ARGV_OUT": str(out), "HOME": str(where)},
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
    except subprocess.TimeoutExpired:
        pass   # a command that waits for input ran what it ran; the arguments recorded so far are what counts
    return [r.decode().split("\0")[:-1] for r in out.read_bytes().split(b"\1") if r]


GIT_VALUE_OPTIONS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env")


def git_subcommand(args: list[str]) -> list[str]:
    """`args` without git's global options (`-C dir`, `-c k=v`, `--no-pager`): what follows is the subcommand and its arguments."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_VALUE_OPTIONS else 1
    return args[i:]


def pushes_to_default(calls: list[list[str]]) -> bool:
    for args in calls:
        args = git_subcommand(args)
        if args[:1] != ["push"]:
            continue
        pos, skip = [], False
        for a in args[1:]:
            if skip:
                skip = False
            elif a in ("--mirror", "--all"):
                return True
            elif a == "-o":
                skip = True
            elif not a.startswith("-"):
                pos.append(a)
        for ref in pos[1:]:
            src, colon, dst = ref.lstrip("+").partition(":")
            if re.sub(r"^refs/heads/", "", dst if colon else src) in ("main", "master"):
                return True
    return False


def with_escape(base: str, escapes: int, tail: str) -> str:
    """`base` with an extra word `a`, `escapes` backslashes and `tail` written against it, before the last word of the push."""
    head, _, last = base.rpartition(" ")
    return f"{head} a{chr(92) * escapes}{tail} {last}"


def agrees_with_the_shell(command: str, tmp_path: Path) -> None:
    for shell in ("bash", "zsh"):
        if shutil.which(shell) and pushes_to_default(ran_git(command, shell, tmp_path)):
            assert si08(command), f"{shell} pushes to the default branch and the gate does not stop {command!r}"


@pytest.mark.parametrize("command", [
    "git push origin feature a>&-main",              # bash closes a descriptor with `>&-` and the word `main` follows with no blank
    "git push origin feature a<&-main",
    "git push origin feature >&-main",
    "git push origin feature a>&---mirror",
    "git push origin feature a\\&>log :main",        # the word `a&`, then a redirect: the push goes to main
    "git push origin feature a\\\\&>log :main",      # the word `a\`, then the redirect `&>log`
    "git push origin feature a\\\\\\&>log :main",    # `a\&`, then `>log`
    "git push origin feature a\\\\\\\\&>log :main",
    "git push origin feature a\\&>>log :main",
    "git push origin feature a\\\\&>>log :main",
    "git push origin feature a\\&> log :main",
    "git push origin feature a\\\\&> log :main",
    "git push origin feature a\\>log :main",
    "git push origin feature a\\\\>log :main",
    "git push origin feature a\\|log :main",
    "git push origin feature a\\\\>|log :main",
    "git push origin feature a\\;:main",
    "git push origin feature a\\;  :main",
    "git push origin feature a\\(  :main",
    "git push origin feature a\\{ :main",
    "git push origin feature a\\}\\& :main",
    "git push origin feature \"a\\&\" :main",         # inside double quotes the backslash stays and the `&` is data
    "git push origin feature \"a\\&>log\" :main",
    "git push origin feature a\\\n&>log :main",       # a backslash at the end of a line joins the lines: `a&>log`
    "git push origin feature &>log\\\n :main",
    "bash -c 'git push origin feature a\\&>log :main'",
    "bash -c 'git push origin feature a\\\\&>log :main'",
    "sh -c \"git push origin feature a\\\\&>log :main\"",
    "eval 'git push origin feature a\\&>log :main'",
    "eval 'git push origin feature a\\\\&>log :main'",
    "eval \"git push origin feature a\\\\\\\\&>log :main\"",
])
def test_escaped_operator_before_a_redirect_still_stops_a_push_to_main(command, tmp_path):
    assert si08(command), command
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("escapes", [1, 2, 3, 4])
@pytest.mark.parametrize("tail", FILE_REDIRECTS + FD_REDIRECTS + ESCAPED_OPERATORS)
@pytest.mark.parametrize("base", ["git push origin " + p for p in PUSH_TO_MAIN])
def test_backslashes_before_any_operator_never_hide_a_push_to_main(base, tail, escapes, tmp_path):
    command = with_escape(base, escapes, tail)
    agrees_with_the_shell(command, tmp_path)


@pytest.mark.parametrize("escapes", [1, 2, 3, 4])
@pytest.mark.parametrize("tail", ["&>log", "&>>log", "&>/dev/null", "&> log", ">log", ">|log", "2>log", "<in"])
@pytest.mark.parametrize("base", PUSHES + OTHERS)
def test_backslashes_before_a_redirect_do_not_lower_the_rating(base, tail, escapes):
    """An extra word (`a&`, `a` and a backslash, `a>log`) written after the subcommand never makes a gated command less gated."""
    words = base.split(" ")
    at = 2 if base.startswith("git ") else len(words)   # `rm a -r -f x` is not `rm -rf x` whatever `a` is; that is not about redirects
    if escapes % 2 and tail.startswith(">|"):
        pytest.skip("`a\\>|log` is the word `a>` piped to `log`: a different command, not an escaped redirect")
    command = " ".join([*words[:at], f"a{chr(92) * escapes}{tail}", *words[at:]])
    ref_level, _rule, ref_blocked, _ho, _keys = sig(base)
    level, _rule, blocked, _ho, _keys = sig(command)
    assert policy.LEVELS[level] >= policy.LEVELS[ref_level], command
    assert set(ref_blocked) <= set(blocked), command
    if si08(base):
        assert si08(command), command


def test_a_real_operator_after_an_escaped_backslash_is_still_hoisted():
    assert cur_segments("git push origin feature a\\\\&>log :main") == ["git push origin feature a\\\\ :main > log"]
    segs = cur_segments("git push origin feature a\\&>log :main")
    assert segs[0] == "git push origin feature a\x06 :main > log"   # data first; the reading as syntax follows (W-113)
    assert "git push origin feature a\\ :main > log" in segs
    assert cur_segments("git push origin feature a\\\\\\&>log :main")[0] == "git push origin feature a\\\\\x06 :main > log"
    assert cur_segments('git push origin feature "a\\&" :main') == ["git push origin feature a\\\x06 :main"]
    assert cur_segments("git push origin feature a\\\n&>log :main") == ["git push origin feature a :main > log"]
