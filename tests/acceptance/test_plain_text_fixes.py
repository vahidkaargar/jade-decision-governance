"""Plain-text fixes: push refspecs, glab value flags, no hard words in risk lines, test debt."""

import re

import pytest

from jade import askplain, policy


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")


def assess(cmd: str):
    return policy.assess("Bash", {"command": cmd}, cwd="/tmp")


def line_of(cmd: str) -> str:
    return askplain.what_line(assess(cmd), "Bash")


@pytest.mark.parametrize("cmd,expected", [
    ("git push origin :main", "Delete the remote branch main."),
    ("git push origin :feature", "Delete the remote branch feature."),
    ("git push origin :refs/heads/main", "Delete the remote branch main."),
    ("git push origin +main", "Force push to main."),
    ("git push origin +feature", "Force push to feature."),
    ("git push origin +HEAD:main", "Force push to main."),
    ("git push origin HEAD:main", "Push to main."),
    ("git push origin main:main", "Push to main."),
    ("git push origin HEAD:refs/heads/main", "Push to main."),
    ("git push -u origin main", "Push to the main branch."),
    ("git push --force origin feature", "Force push to feature."),
    ("git push origin --delete feature", "Delete a branch or tag on a remote."),
])
def test_push_refspec_lines(cmd, expected):
    assert line_of(cmd) == expected


@pytest.mark.parametrize("cmd,expected", [
    ("git push origin :feat1 +x:main", "Force push and delete branches on a remote."),
    ("git push origin main +feature", "Force push to a remote, including the main branch."),
        ("git push -fu origin HEAD:main", "Force push to main."),
    ("git push -uf origin feature", "Force push to feature."),
    ("git push origin +refs/heads/*:refs/heads/*", "Force push to a remote."),
    ("git push origin :a :b", "Delete branches on a remote."),
    ("git push origin :refs/tags/v1", "Delete the remote tag v1."),
    ("git push --mirror origin", "Push and delete to match every branch and tag."),
    ("git push origin feature +x:main", "Force push to a remote, including the main branch."),
    ("git push origin main :feature", "Push to the main branch and delete branches on a remote."),
    ("git push origin HEAD", "Push the current branch to a remote."),
    ("git push origin @", "Push the current branch to a remote."),
    ("git push", "Push the current branch to a remote."),
    ("git push --prune origin", "Push and delete remote branches that are gone here."),
    ("git push --prune origin main", "Push to the main branch and delete remote branches that are gone here."),
    ("git push --force --prune origin", "Force push and delete remote branches that are gone here."),
    ("git push --force origin HEAD", "Force push the current branch to a remote."),
    ("git push --tags origin main", "Push to the main branch and push tags to a remote."),
    ("gh pr merge 12 -Ro/r", "Merge a pull request."),
    ("glab mr merge -Ro/r 12", "Merge a merge request."),
    ("git push --tags origin", "Push tags to a remote."),
    ("git push origin main feature", "Push to the main branch."),
    ("git push origin x:*", "Push to a remote."),
    ("glab mr merge 12 -R o/r", "Merge a merge request."),
    ("glab mr merge --repo=o/r 12", "Merge a merge request."),
    ("gh pr merge --repo=o/r 12", "Merge a pull request."),
])
def test_push_multi_refspec_and_flags(cmd, expected):
    assert line_of(cmd) == expected


def test_push_lines_with_long_branch_stay_short():
    name = "b" * 60
    for cmd in (f"git push origin :{name}", f"git push origin +{name}", f"git push origin HEAD:{name}"):
        assert len(line_of(cmd).split()) <= 20


@pytest.mark.parametrize("cmd", [
    "glab mr merge --message 5 12",
    "glab mr merge -m 5 12",
    "glab mr merge --sha abc 12",
    "glab mr merge --squash-message x 12",
    "glab mr merge -R o/r 12",
])
def test_glab_value_flag_is_not_the_number(cmd):
    assert line_of(cmd) == "Merge a merge request."


@pytest.mark.parametrize("cmd", [
    "glab mr merge 12 --squash",
    "glab mr merge --squash 12",
    "glab mr merge -d -y 12",
    "glab mr merge --message=5 12",
])
def test_glab_no_value_flags_keep_the_number(cmd):
    assert line_of(cmd) == "Merge merge request 12."


HARD_WORDS = ("reflog", "boot service", "deployment", "dependencies", "credential", "connectors", "cluster", "pipeline", "overlay", "invariant", "refspec")


def test_no_jargon_in_risk_lines():
    sentences = [*askplain.RISK_TEXT.values(), *askplain.HO_TEXT.values(), askplain.GENERIC_RISK, askplain.GENERIC_HO]
    bad = [(s, w) for s in sentences for w in HARD_WORDS if re.search(rf"\b{re.escape(w)}s?\b", s, re.I)]
    assert bad == []


def test_no_jargon_in_segment_and_push_lines():
    sentences = [t for _, t in askplain.SEGMENT_TEXT]
    for cmd in ("git push origin :a", "git push --prune origin main", "git push --force origin HEAD", "git push origin main :a --tags", "git push -f origin x", "git push --mirror origin", "git push origin :refs/tags/v", "git push origin :a +b:main"):
        sentences.append(askplain._push_line(cmd))
    bad = [(s, w) for s in sentences for w in HARD_WORDS if re.search(rf"\b{re.escape(w)}s?\b", s, re.I)]
    assert bad == []
    assert all(len(askplain._push_line(c).split()) <= 20 for c in ("git push origin :a +b:main", "git push --mirror origin"))


def test_ho_without_segments_has_two_lines():
    a = assess('psql -c "DROP TABLE x"')
    assert a.ho_ids and not a.seg_keys
    lines = askplain.header(a, "Bash")
    assert len(lines) == 2
    assert lines[0] == "Run a shell command."
    assert lines[1].startswith("This call ")
