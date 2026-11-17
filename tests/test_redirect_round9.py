"""Round-9 review of PR 47.

* R9-CFG-1 / R9-DIFF-2: the reading of origin/main's words (`git config alias.x <> f` takes `<>` for a value) rated the command high on origin/main; it took the danger of the
  reduced words and rated it low. The danger of that reading is now worked out from its own words.
* R9-PERF-1, R9-PERF-2: a word is classified once for the whole text, not once for each git word before it (it was quadratic, and slower than origin/main by 3.8x).
* R9-PERF-1 (second), R9-PERF-3: the two readings of a push ask git the same question; the answer is kept for the length of one command.
* R9-DIFF-1: a target that ends in an escaped backslash is complete: the next word is a real argument.
"""

import subprocess

import pytest

from jade import hooks, policy, shellnorm
from tests.test_redirect_anywhere import si08

CONFIG_ROWS = [
    "git config alias.x <> f",
    "git config alias.x >&-",
    "git config alias.x <&-",
    "git config alias.x >&evil",
    "git config alias.x <>'!sh'",
    "git config alias.x > f",
    "git config alias.x >> f",
    "git config alias.x 2> f",
    "git config alias.x < f",
    "git config core.pager > f",
    "git config credential.helper > f",
    "git config core.sshCommand > x",
    "git config --global alias.x > f",
]


@pytest.mark.parametrize("command", CONFIG_ROWS)
def test_a_config_write_behind_a_redirect_is_not_rated_lower_than_origin_main_rated_it(command):
    assert policy.assess("Bash", {"command": command}, cwd="/tmp").level in ("high", "critical"), command


@pytest.mark.parametrize("command", CONFIG_ROWS[:5])
def test_the_reading_of_the_words_as_origin_main_took_them_carries_its_own_danger(command):
    _, calls = shellnorm.git_calls(shellnorm.skeleton(command))
    assert any(c.danger for c in calls if "alias.x" in c.args and len(c.args) > 1), command


@pytest.mark.parametrize("command", ["git config alias.x", "git config --get alias.x > f", "git config --list > f", "git config user.name > f"])
def test_a_config_read_or_a_harmless_key_behind_a_redirect_stays_low(command):
    assert policy.assess("Bash", {"command": command}, cwd="/tmp").level not in ("high", "critical"), command


def test_a_word_is_classified_once_for_the_whole_text_not_once_per_git_word(monkeypatch):
    seen = []
    real = shellnorm._redirect_kind
    monkeypatch.setattr(shellnorm, "_redirect_kind", lambda w: seen.append(w) or real(w))
    text = r"git log --format=%h\>x " * 300 + "git push > log"
    shellnorm.git_calls(text)
    assert len(seen) <= 2 * len(text.split(" ")), len(seen)   # linear: it was one call per later word of every git word (quadratic)


@pytest.fixture
def repo_on_main(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    for argv in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                 ["remote", "add", "origin", "https://example.invalid/x.git"]):
        subprocess.run(["git", "-C", str(r), *argv], check=True)
    return r


_REAL_RUN = subprocess.run


def _spawns(monkeypatch, cwd, command):
    asked = []
    monkeypatch.setattr(hooks.subprocess, "run", lambda argv, *a, **k: asked.append((tuple(argv), k.get("cwd"))) or _REAL_RUN(argv, *a, **k))
    hooks._on_default_branch(str(cwd), command)
    return asked


@pytest.mark.parametrize(("command", "spawns"), [("git push origin feature > log", 1), ("git push origin HEAD > log", 2)])
def test_a_redirected_push_on_a_feature_branch_asks_git_each_question_once(command, spawns, repo_on_main, monkeypatch):
    """Both readings of the push (the words without the redirect, the words as origin/main took them) ask the same question: 2 and 4 spawns before the memo."""
    subprocess.run(["git", "-C", str(repo_on_main), "checkout", "-q", "-b", "feature"], check=True)
    asked = _spawns(monkeypatch, repo_on_main, command)
    assert len(asked) == spawns, asked
    assert len(asked) == len(set(asked)), asked


def test_a_redirected_push_on_main_asks_git_once(repo_on_main, monkeypatch):
    assert len(_spawns(monkeypatch, repo_on_main, "git push origin feature > log")) == 1


def test_the_answers_are_kept_for_one_command_only(repo_on_main, monkeypatch):
    first = _spawns(monkeypatch, repo_on_main, "git push origin feature > log")
    assert hooks._MEMO is None
    _REAL_RUN(["git", "-C", str(repo_on_main), "checkout", "-q", "-b", "other"], check=True)
    second = _spawns(monkeypatch, repo_on_main, "git push origin feature > log")
    assert len(first) == len(second) == 1   # the second command asks again: nothing is kept between commands


def test_a_target_that_ends_in_an_escaped_backslash_is_complete(repo_on_main):
    """`> lg\\\\` (the target is `lg\\`) and then `origin feature` are real arguments: a push of a feature branch."""
    command = "git push > lg\\\\ origin feature"
    assert not hooks._on_default_branch(str(repo_on_main), command), command


def test_a_target_that_ends_in_an_odd_number_of_backslashes_still_takes_the_next_word(repo_on_main):
    assert hooks._on_default_branch(str(repo_on_main), "git push --force > lg\\ x origin")
    assert not si08("git push origin feature > lg\\ x")


@pytest.mark.parametrize("text", ["git", "git -C", "git -c a=b", "git -C d", "echo x ; git --no-pager", "git > log"])
def test_git_calls_does_not_run_past_the_end_when_the_options_are_the_last_words(text):
    shellnorm.git_calls(text)


# ---------------------------------------------------------------- round 10: cost of the scan, and the key of `git config`

def test_the_redirect_free_words_are_worked_out_once_per_text(monkeypatch):
    seen = []
    real = shellnorm._redirect_kind
    monkeypatch.setattr(shellnorm, "_redirect_kind", lambda w: seen.append(w) or real(w))
    text = "git log --oneline > o.txt " * 400
    shellnorm.git_calls(text)
    assert len(seen) <= len(text.split()) + 1, len(seen)   # once for each word, not once for each git word after it (400 times 1 200 words)


def test_the_arguments_of_a_git_word_are_the_words_after_it_without_the_redirects():
    _, calls = shellnorm.git_calls("git log --oneline > o.txt " * 3)
    reduced = [c.args for c in calls if ">" not in c.args]
    asis = [c.args for c in calls if ">" in c.args]
    assert reduced == [["--oneline", "git", "log", "--oneline", "git", "log", "--oneline"], ["--oneline", "git", "log", "--oneline"], ["--oneline"]]
    assert asis == [["--oneline", ">", "o.txt", "git", "log", "--oneline", ">", "o.txt", "git", "log", "--oneline", ">", "o.txt"],
                    ["--oneline", ">", "o.txt", "git", "log", "--oneline", ">", "o.txt"], ["--oneline", ">", "o.txt"]]


def test_a_subcommand_that_stands_in_the_target_of_a_redirect_is_read_from_its_own_start():
    _, calls = shellnorm.git_calls("echo > git push origin main")
    assert [c.args for c in calls if c.sub == "push"] == [["origin", "main"]]
    assert calls[0].sub == "push"


@pytest.mark.parametrize("args", [
    ["alias.x", "y"], ["--get", "alias.x"], ["--get=alias.x", "y"], ["--list"], ["-l"], ["set", "alias.x", "y"], ["get", "alias.x"], ["--file", "f", "alias.x", "y"],
    ["alias.x", "y", "--unset"], ["--unset-all", "k", "v"], ["--add", "alias.x", "y"], ["--getx", "a", "b"], ["alias.x"], [],
])
def test_config_write_key_is_what_it_was(args):
    reads = ("--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--list", "-l", "--show-origin", "--show-scope",
             "--name-only", "--unset", "--unset-all", "--remove-section", "--rename-section", "--edit", "-e", "--help")
    valued = {"--file", "-f", "--blob", "--type", "--default", "--comment"}

    def reference(args):
        a = list(args)
        if a[:1] and a[0] in ("get", "list", "unset", "edit", "rename-section", "remove-section"):
            return None
        if a[:1] == ["set"]:
            a = a[1:]
        pos, i = [], 0
        while i < len(a):
            x = a[i]
            if x in valued:
                i += 2
                continue
            if any(x == r or x.startswith(r + "=") for r in reads):
                return None
            if x.startswith("-") and x != "-":
                i += 1
                continue
            pos.append(x)
            i += 1
        return pos[0] if len(pos) >= 2 else None

    assert shellnorm.config_write_key(args) == reference(args), args
