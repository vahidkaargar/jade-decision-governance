"""R-10 split (2026-11-13, the maintainer's request to cut permit prompts): a plain push of one named branch and a pull request are medium (R-19, recorded);
merge, release, publish, tag, force, delete, bare push, default branch and refspec pushes stay high (R-10)."""
import pytest

from jade import policy


def rated(cmd):
    a = policy.assess("Bash", {"command": cmd}, cmd)
    return a.rule_id, a.level


@pytest.mark.parametrize("cmd", [
    "git push origin fix-x", "git push -u origin fix-x", "git push -q -u origin fix-x 2>&1 | tail -1", "git push --set-upstream origin release/next",
    "git commit -m x && git push -q -u origin w1", "gh pr create --base main --title t", "glab mr create --title t",
])
def test_routine_publishing_of_a_named_branch_or_a_pull_request_is_medium(cmd):
    assert rated(cmd) == ("R-19", "medium")


@pytest.mark.parametrize("cmd", [
    "git push", "git push origin", "git push origin main", "git push origin master", "git push origin HEAD", "git push origin v0.4.0", "git push origin 1.2.3",
    "git push --force origin x", "git push -f origin x", "git push --force-with-lease origin x", "git push origin --delete x", "git push origin :x", "git push origin +x",
    "git push origin x:main", "git push origin HEAD:main", "git push --tags origin", "git push --mirror origin", "git push --all origin", "git push origin --all", "git push --delete origin x", "git push origin x --force", "git push origin x && git push --force origin y",
    "gh pr merge 22 --merge", "gh release create v1", "glab mr merge 3", "npm publish", "cargo publish", "twine upload dist/*", "docker push img", "composio execute X",
])
def test_every_irreversible_or_unnamed_publish_shape_stays_high(cmd):
    assert rated(cmd) == ("R-10", "high")


def test_a_chain_with_a_higher_rated_part_is_never_lowered_by_a_routine_push():
    for cmd in ("git push origin fix-x && git reset --hard HEAD~3", "git push origin fix-x; launchctl bootout gui/501/x", "git push origin fix-x && gh pr merge 22"):
        assert rated(cmd)[1] in ("high", "critical"), cmd
