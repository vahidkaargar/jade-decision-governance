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
    ("git push --force origin feature", "Force push to a remote."),
    ("git push origin --delete feature", "Delete a branch or tag on a remote."),
])
def test_push_refspec_lines(cmd, expected):
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


def test_ho_without_segments_has_two_lines():
    a = assess('psql -c "DROP TABLE x"')
    assert a.ho_ids and not a.seg_keys
    lines = askplain.header(a, "Bash")
    assert len(lines) == 2
    assert lines[0] == "Run a shell command."
    assert lines[1].startswith("This call ")
