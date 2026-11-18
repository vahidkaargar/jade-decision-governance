"""V6-05 (merge gate for PR 42 and PR 43): the first line of a merge question never says less than the call can do.

`gh pr merge 940 --admin --delete-branch` used to read "Merge pull request 940." The human read that line and the flags stayed in the `Command:` line below it. The line now says
what each flag lets the call do, with the flag as written, in the chat question and in the dialog text.
"""

import pytest

from jade import askchat, askdialog, askplain, policy


def line(cmd: str) -> str:
    a = policy.assess("Bash", {"command": cmd}, cwd="/tmp")
    assert a.seg_keys, cmd
    return askplain.what_line(a, "Bash")


@pytest.mark.parametrize("cmd,flags", [
    ("gh pr merge 940 --admin", ["--admin"]),
    ("gh pr merge 940 --delete-branch", ["--delete-branch"]),
    ("gh pr merge 940 --admin --delete-branch", ["--admin", "--delete-branch"]),
    ("gh pr merge --delete-branch --admin 940", ["--admin", "--delete-branch"]),
    ("gh pr merge 940 --admin=true", ["--admin"]),
    ("gh pr merge 940 --auto --squash", ["--auto"]),
    ("gh pr merge 940 --auto --admin --delete-branch", ["--auto", "--admin", "--delete-branch"]),
    ("gh pr merge https://github.com/o/r/pull/940 --admin", ["--admin"]),
    ("gh pr merge feature-branch --delete-branch", ["--delete-branch"]),
    ("gh pr merge --admin", ["--admin"]),
    ("gh pr merge 940 -d", ["-d"]),
    ("gh pr merge 940 -sd", ["-d"]),
    ("gh pr merge 940 '--admin'", ["--admin"]),
    ("gh pr merge 940 --repo o/r --admin", ["--admin"]),
])
def test_the_merge_line_names_every_flag_that_widens_the_call(cmd, flags):
    text = line(cmd)
    for flag in flags:
        assert flag in text, (cmd, text)


@pytest.mark.parametrize("cmd", ["gh pr merge 940", "gh pr merge 940 --squash", "gh pr merge --rebase 940", "gh pr merge 940 --repo o/r --squash"])
def test_a_plain_merge_says_only_the_merge(cmd):
    text = line(cmd)
    assert "--" not in text and "skip" not in text and "delete" not in text, (cmd, text)


@pytest.mark.parametrize("cmd", ["gh pr merge $N --squash", "gh pr merge 940 $FLAGS", "gh pr merge 940 *"])
def test_a_word_that_is_not_plain_says_there_may_be_more(cmd):
    assert "maybe more" in line(cmd), cmd


def test_a_merge_sentence_stays_one_sentence():
    text = line("gh pr merge 940 --auto --admin --delete-branch")
    assert text.endswith(".") and text.count(". ") == 0 and text.startswith("Merge pull request 940")


def test_the_flags_are_in_the_first_line_of_the_chat_question_and_the_dialog():
    cmd = "gh pr merge 940 --admin --delete-branch"
    a = policy.assess("Bash", {"command": cmd}, cwd="/tmp")
    q = askchat.question_for("0123456789ab", a, "Bash", {})["question"].split("\n")
    assert "--admin" in q[1] and "--delete-branch" in q[1]
    d = askdialog.text_for(a, "Bash", {}).split("\n")
    assert "--admin" in d[0] and "--delete-branch" in d[0]
