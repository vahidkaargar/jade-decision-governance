"""A redirect written anywhere in a command must not change what the gate sees (SI-08 bypass, found in review of the 0.6.x branches).

`git push origin feature &>log +feature:main` pushes `feature` to main with a force. The risk rules and SI-08 read the words of a command up to the next `&`, `|`
or `;`, so `&>log` hid every word after it: level medium, nothing blocked, no key. The same hole sat in `>&log`, `>&-`, `<&-` and `>|log`, and a redirect
written before the subcommand (`git >log push origin main`) hid the whole push. The shell takes a redirect out of the words wherever it stands, so the gate
does too: the call, with the redirect taken out, is what every rule reads (`shellnorm._hoist_redirects`).
"""

import json
import re
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

_REDIRECT_WORD = re.compile(r"^(?:\d*|&)(?:>>?|<>|<<<|<)[|&]?")


def sig(command: str) -> tuple:
    """What the gate decides for a command: level, rule, blocked invariants, human-only classes and the gated segment keys with any redirect cut off."""
    a = policy.assess("Bash", {"command": command}, cwd="/tmp")
    keys = []
    for k in a.seg_keys:
        words = k.split(" ")
        cut = next((i for i, w in enumerate(words) if _REDIRECT_WORD.match(w)), len(words))
        keys.append(" ".join(words[:cut]))
    return a.level, a.rule_id, tuple(a.blocked), tuple(a.ho_ids), tuple(sorted(keys))


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
    assert shellnorm.segments("echo 'a>b' c") == ["echo a\x06b c"]
    assert shellnorm.segments("git log --format=%h\\>x") == ["git log --format=%h\\>x"]


def test_skeleton_puts_the_redirect_last_and_spells_it_one_way():
    assert shellnorm.segments("git push origin feature &>log :main") == ["git push origin feature :main > log"]
    assert shellnorm.segments("git push origin feature >&log :main") == ["git push origin feature :main > log"]
    assert shellnorm.segments("git >|log push origin") == ["git push origin > log"]
    assert shellnorm.segments("git push >&- origin 2>&- feature") == ["git push origin feature"]
    assert shellnorm.segments("git push origin feature&>>log :main") == ["git push origin feature :main >> log"]
    assert shellnorm.segments("echo a2>log b") == ["echo a2 b > log"]   # the 2 belongs to the word `a2`, not to the redirect
    assert shellnorm.segments("git push 2>log origin") == ["git push origin 2> log"]
