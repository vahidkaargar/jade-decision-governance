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


@pytest.mark.parametrize("command", ["git push > log", "git push --force 2> log", "git push origin > log"])
def test_a_redirected_push_asks_git_each_question_once(command, repo_on_main, monkeypatch):
    asked = []
    real = subprocess.run
    monkeypatch.setattr(hooks.subprocess, "run", lambda argv, *a, **k: asked.append((tuple(argv), k.get("cwd"))) or real(argv, *a, **k))
    assert hooks._on_default_branch(str(repo_on_main), command)
    assert len(asked) == len(set(asked)), asked


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
