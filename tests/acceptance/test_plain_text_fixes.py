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


CUR = "Push the current branch to a remote. It can be the main branch."
MANY = "Push every matching branch to a remote, which can include the main branch."
FMANY = "Force push every matching branch to a remote, which can include the main branch."


@pytest.mark.parametrize("cmd,expected", [
    ("git push origin :main", "Delete the main branch on a remote."),
    ("git push origin :feature", "Delete the remote branch feature."),
    ("git push origin :refs/heads/main", "Delete the main branch on a remote."),
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
    ("git push origin :feat1 +x:main", "Force push and delete branches on a remote, including the main branch."),
    ("git push origin main +feature", "Force push to a remote, including the main branch."),
        ("git push -fu origin HEAD:main", "Force push to main."),
    ("git push -uf origin feature", "Force push to feature."),
    ("git push origin :a :b", "Delete branches on a remote."),
    ("git push origin :refs/tags/v1", "Delete the remote tag v1."),
    ("git push --mirror origin", "Push and delete to match every branch and tag."),
    ("git push origin feature +x:main", "Force push to a remote, including the main branch."),
    ("git push origin main :feature", "Delete branches on a remote and push to the main branch."),
    ("git push origin HEAD", CUR),
    ("git push origin @", CUR),
    ("git push", CUR),
    ("git push --prune origin", "Push and delete remote branches that are gone here. It can be the main branch."),
    ("git push --prune origin main", "Delete remote branches that are gone here and push to the main branch."),
    ("git push --force --prune origin", "Force push and delete remote branches that are gone here. It can be the main branch."),
    ("git push --force origin HEAD", "Force push the current branch to a remote. It can be the main branch."),
    ("git push --tags origin main", "Push to the main branch and push tags to a remote."),
    ("gh pr merge 12 -Ro/r", "Merge a pull request."),
    ("glab mr merge -Ro/r 12", "Merge a merge request."),
    ("git push --tags origin", "Push tags to a remote."),
    ("git push origin main feature", "Push to the main branch."),
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


@pytest.mark.parametrize("cmd,expected", [
    ("git push origin main --delete", "Delete the main branch on a remote."),
    ("git push -d origin main", "Delete the main branch on a remote."),
    ("git push origin --delete main feature", "Delete branches on a remote, including the main branch."),
    ("git push -ud origin main", "Delete the main branch on a remote."),
    ("git push origin --delete feature other", "Delete branches on a remote."),
    ("git push origin --delete refs/heads/main", "Delete the main branch on a remote."),
    ("git push -fd origin main", "Force push and delete branches on a remote, including the main branch."),
    ("git push --all origin", MANY),
    ("git push --branches origin", MANY),
    ("git push --all --force origin", FMANY),
    ("git push -f --branches origin", FMANY),
    ("git push origin :", MANY),
    ("git push origin +:", FMANY),
    ("git push origin HEAD", CUR),
    ("git push -f", "Force push the current branch to a remote. It can be the main branch."),
    ("git push origin feature:heads/main", "Push to main."),
    ("git push origin feature:refs/heads/main", "Push to main."),
    ("git push origin HEAD feature", "Push to a remote. It can be the main branch."),
])
def test_rr4_push_lines(cmd, expected):
    assert line_of(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "git push --follow-tags origin feature",
    "git push --follow-tags origin",
    "git push --follow-tags",
])
def test_follow_tags_says_tags(cmd):
    assert "tags" in line_of(cmd)


def test_matching_refspec_is_not_a_delete():
    assert "elete" not in line_of("git push origin :")


@pytest.mark.parametrize("cmd", [
    "git push -fd origin main :a :b +x:main --tags --prune",
    "git push --all -f --prune --tags -d origin main",
    "git push -f --prune --tags origin 'refs/heads/*:refs/heads/*' :a HEAD",
    "git push --follow-tags --prune origin ':' :a",
])
def test_every_worst_case_line_stays_short(cmd):
    line = line_of(cmd)
    assert len(line.split()) <= 20, line
    assert "main" in line


@pytest.mark.parametrize("cmd,expected", [
    ("git push --m origin", "Push and delete to match every branch and tag."),
    ("git push --mi origin", "Push and delete to match every branch and tag."),
    ("git push --b origin", MANY),
    ("git push --b -f origin", FMANY),
    ("git push --a origin", MANY),
    ("git push --d origin main", "Delete the main branch on a remote."),
    ("git push --p origin", "Push and delete remote branches that are gone here. It can be the main branch."),
    ("git push --t origin", "Push tags to a remote."),
    ("git push --atomic --signed --no-verify origin feature", "Push to feature."),
    ("git push --atomic origin feature", "Push to feature."),
])
def test_short_long_flags_read_as_the_worst(cmd, expected):
    assert line_of(cmd) == expected


@pytest.mark.parametrize("cmd", ["git push --f origin", "git push --fo origin"])
def test_f_and_fo_are_force_and_tags(cmd):
    line = line_of(cmd)
    assert line.startswith("Force push") and "tags" in line and len(line.split()) <= 20


WORST = "This push can force push, delete branches and tags, and include the main branch."


@pytest.mark.parametrize("cmd", [
    "git push --recurse-submodules check origin",
    "git push --recurse-submodules=on-demand origin feature",
    "git push --recurse-submodules on-demand origin",
    "git push --push ci.skip origin",
    "git push --pu x origin",
    "git push --push-option=x origin feature",
    "git push --rep x origin",
    "git push --repo=x origin",
    "git push --recu check origin",
    "git push -o x origin feature",
    "git push -of origin feature",
    "git push --unknown origin feature",
    "git push --no-force-with-lease origin feature",
    "git push -- origin main",
    "git push https://x/y.git main",
    "git push ./repo main",
    "git push git@host:o/r.git main",
    "git push origin '^main'",
    "git -c remote.origin.mirror=true push origin",
    "git -c push.default=matching push",
    "git --config-env=remote.origin.mirror=M push origin",
    "git -C /tmp push origin main",
    "git --git-dir=/x push origin main",
    "git --work-tree=/x push origin main",
    "git --exec-path=/x push origin main",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.mirror GIT_CONFIG_VALUE_0=true git push origin",
    "GIT_DIR=/x git push origin main",
])
def test_unknown_push_shape_gets_the_worst_line(cmd):
    assert line_of(cmd) == WORST


def test_worst_line_is_short_and_names_the_worst():
    assert len(WORST.split()) <= 20
    for w in ("force", "delete", "tags", "main"):
        assert w in WORST


@pytest.mark.parametrize("cmd,expected", [
    ("git push -u origin main", "Push to the main branch."),
    ("git push --set-upstream origin main", "Push to the main branch."),
    ("git push -n -v origin feature", "Push to feature."),
    ("git push --dry-run --porcelain --atomic --no-verify origin feature", "Push to feature."),
    ("git push -4 -q origin feature", "Push to feature."),
    ("git push --force-with-lease=main:abc origin feature", "Force push to feature."),
    ("git push --force-if-includes origin feature", "Force push to feature."),
    ("git push -uf origin feature", "Force push to feature."),
    ("git push my-remote.2 main", "Push to the main branch."),
])
def test_known_shapes_keep_their_line(cmd, expected):
    assert line_of(cmd) == expected


@pytest.mark.parametrize("cmd", ["git push --receive-pack=x origin main", "git push --exec=x origin main"])
def test_code_running_push_flag_still_says_the_worst(cmd):
    assert WORST in line_of(cmd)   # the call is also an unsafe one, which adds its own Run: line


# RR8: the push line reads the raw words of the call, not only the key. Quotes, escapes, `$`, glob characters, odd spacing,
# a prefix before `git push`, or any other command in the same call (a leading `cd PATH` aside) can change what git does.
@pytest.mark.parametrize("cmd", [
    '"git" -c remote.origin.mirror=true push origin',
    "'git' -c remote.origin.mirror=true push origin",
    'git "-c" remote.origin.mirror=true push origin',
    "git '-c' remote.origin.mirror=true push origin",
    "git ''-c remote.origin.mirror=true push origin",
    "git \\\n-c remote.origin.mirror=true push origin",
    "gi\\t -c remote.origin.mirror=true push origin",
    'g""it -c remote.origin.mirror=true push origin',
    "\\git -c remote.origin.mirror=true push origin",
    "HOME=/tmp/evil git push origin",
    "XDG_CONFIG_HOME=/tmp/evil git push origin",
    "git  push origin main",
    "git push\torigin main",
    "git push origin 'main'",
    "git push origin $B",
])
def test_rr8_1_quotes_escapes_and_spacing_give_the_worst_line(cmd):
    assert line_of(cmd) == WORST


@pytest.mark.parametrize("cmd", [
    "touch :main && git push origin :*",
    "touch -- --mirror && git push origin *",
    "git push origin :*",
    "git push origin *",
    "git push -f origin *",
    "git push origin f*",
    "git push origin HEAD*",
    "git push origin x:*",
    "git push origin +refs/heads/*:refs/heads/*",
    "git push origin '*:*'",
    "git push origin fe?ture:main",
    "git push origin [m]ain",
    "git push origin {feature,:main}",
])
def test_rr8_2_glob_characters_give_the_worst_line(cmd):
    assert line_of(cmd) == WORST


@pytest.mark.parametrize("cmd", [
    "git config remote.origin.mirror true && git push origin",
    "git config remote.origin.push +refs/heads/*:refs/heads/* && git push origin",
    "git symbolic-ref refs/heads/x refs/heads/main && git push --no-verify origin x",
    "git config remote.origin.push refs/heads/feature:refs/heads/main && git push --no-verify origin feature",
    "export GIT_CONFIG_COUNT=1; git push origin main",
    "git push origin main; git status",
    "git push origin main | tail -1",
    "cd /repo && git status && git push origin main",
    "cd '/repo' && git push origin main",
    "cd $HOME && git push origin main",
])
def test_rr8_4_another_command_in_the_call_gives_the_worst_line(cmd):
    assert line_of(cmd) == WORST


@pytest.mark.parametrize("cmd", [
    "git push origin feature tag v1",
    "git push origin tag v1",
])
def test_rr8_5_the_word_tag_gives_the_worst_line(cmd):
    assert line_of(cmd) == WORST


@pytest.mark.parametrize("cmd", [
    "git push .. feature",
    "git push . feature:main",
    "git push ... feature:main",
    "git push ../other feature:main",
])
def test_rr8_6_dot_remotes_give_the_worst_line(cmd):
    assert line_of(cmd) == WORST


@pytest.mark.parametrize("cmd,expected", [
    ("git push origin main", "Push to the main branch."),
    ("git push -f origin main", "Force push to main."),
    ("git push --delete origin v1", "Delete a branch or tag on a remote."),
    ("git push origin HEAD", CUR),
    ("git push", CUR),
    ("git push origin", CUR),
    ("cd /repo && git push origin main", "Push to the main branch."),
    ("cd /repo&&git push origin main", "Push to the main branch."),
    ("  git push origin main  ", "Push to the main branch."),
    ("git push my-remote.2 main", "Push to the main branch."),
])
def test_rr8_plain_cases_stay_specific(cmd, expected):
    assert line_of(cmd) == expected


@pytest.mark.parametrize("key,expected", [
    ("git push origin +refs/heads/*:refs/heads/*", FMANY),
    ("git push origin x:*", MANY),
    ("git push origin '*:*'", MANY),
    ("git push --prune origin 'refs/heads/*:refs/heads/*'", "This push can delete branches, and can include the main branch."),  # 21 words in full: the short worst-case form
])
def test_key_reader_still_reads_a_refspec_glob(key, expected):
    assert askplain._push_line(key) == expected   # the key reader alone; what_line gives the worst line for any glob in the raw call


def test_push_key_without_raw_subject_gives_the_worst_line():
    from types import SimpleNamespace
    a = SimpleNamespace(seg_keys=["git push origin main"], subject="")
    assert askplain.what_line(a, "Bash") == WORST


@pytest.mark.parametrize("cmd", [
    "cd x>~/.zshrc && git push origin main",
    "cd x || git push origin main",
    "cd x | git push origin main",
    "cd x & git push origin main",
    "cd x; git push origin main",
    "cd x\ngit push origin main",
    "cd x && && git push origin main",
    "cd /repo; git push -f origin main",
])
def test_rr9_only_cd_path_and_and_may_lead_a_push(cmd):
    assert line_of(cmd) == WORST
