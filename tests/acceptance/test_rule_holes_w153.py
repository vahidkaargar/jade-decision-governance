"""W-153: a push through the GitHub refs API or `git update-ref`, and the harness config files of other tools, are no longer low.

Commands are only classified, never run. The shapes are also in evals/adversarial/cases.jsonl (A276 and later) and their twins in
evals/regression/cases.jsonl. A rule change may only raise a rating: the twins show what stays as it was.
"""
import pytest

from jade import hooks, policy
from jade.util import LEVELS

CWD = "/nonexistent-w153"


def assess(cmd):
    return policy.assess("Bash", {"command": cmd}, CWD)


def write(path):
    return policy.assess("Write", {"file_path": path, "content": "x"}, CWD)


def gated(a):
    return LEVELS[a.level] >= LEVELS["high"]


# ---------------------------------------------------------------- the refs API

@pytest.mark.parametrize("cmd", [
    "gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=abc123 -F force=true",
    "gh api -X PATCH /repos/o/r/git/refs/heads/main -f sha=abc",
    "gh api --method PATCH 'repos/o/r/git/refs/heads/master' -f sha=abc",
    "gh api --method=PATCH repos/o/r/git/refs/heads/main -f sha=abc",
    "gh api -XPATCH repos/o/r/git/refs/heads/main -f sha=abc",
    "gh api repos/o/r/git/refs/heads/main -f sha=abc",                # a field makes gh send a POST
    "gh api repos/o/r/git/refs/heads/main --input body.json",
    "gh api repos/o/r/git/refs/heads/main -X DELETE",
    "gh api repos/o/r/git/refs -f ref=refs/heads/main -f sha=x",
    "gh api repos/o/r/git/refs -f ref=refs/heads/master -f sha=x",
    "gh api repos/o/r/git/refs -f sha=x -f ref=refs/tags/v1",
    "gh api repos/o/r/git/refs -X POST --input -",
    "gh api repos/o/r/git/refs -F ref=@ref.txt -f sha=x",
    "gh api repos/o/r/git/refs -f ref=\"$R\" -f sha=x",
    "gh api repos/o/r/git/refs/heads/$b -X PATCH -f sha=x",
    "gh api repos/o/r/git/refs/tags/v1 -X PATCH -f sha=x",
    "gh api repos/{owner}/{repo}/git/refs/heads/main -X PATCH -f sha=x",
    "gh api repos/o/r/pulls/1 -X GET; gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=x",
    "gh api repos/o/r/git/refs -f ref=refs/heads/main -f sha=x; curl -X GET https://x.example",   # a later call's GET does not hide the write
    "gh api repos/o/r/git/refs -f ref=refs/heads/main -f sha=x && git fetch --method GET",
    "gh api repos/{owner}/{repo}/git/refs/heads/{branch} -X PATCH -f sha=x",                        # gh fills {branch} with the current branch
    "gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=x -f 'note=a;b'",
    "gh api repos/o/r/git/refs/heads/main -f sha=x | head",
    "gh api --jq '.a | .b' repos/o/r/git/refs/heads/main -X PATCH -f sha=x",
    "env GH_TOKEN=x gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=x",
    "gh api graphql -f query='mutation { updateRef(input: {refId: \"x\", oid: \"y\"}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { createCommitOnBranch(input: {}) { commit { oid } } }'",
])
def test_a_write_to_a_ref_through_the_api_is_rated_like_a_push(cmd):
    a = assess(cmd)
    assert a.rule_id == "R-10" and gated(a)


@pytest.mark.parametrize("cmd", [
    "gh api repos/o/r/git/refs/heads/main",
    "gh api repos/o/r/git/refs",
    "gh api repos/o/r/git/ref/heads/main",
    "gh api repos/o/r/git/refs/heads/main -X GET",
    "gh api repos/o/r/git/refs/heads/main -X GET -f per_page=1",
    "gh api repos/o/r/git/refs/heads/feat -X PATCH -f sha=x",
    "gh api repos/o/r/git/refs/heads/feat -f sha=x",
    "gh api repos/o/r/git/refs -f ref=refs/heads/feat -f sha=x",
    "gh api repos/o/r/git/refs/heads/main --jq .object.sha",
    "gh api repos/o/r/git/refs/heads/mainline -X PATCH -f sha=x",
    "gh api repos/o/r/git/refs/heads/feat -X PATCH -f sha=x; gh api repos/o/r/pulls/1",
    "gh api repos/o/r/git/refs/heads/main -X GET --jq '.a | .b'",
    "gh api repos/o/r/git/refs/heads/feat -X PATCH -f sha=x; curl -X PATCH https://x.example/refs/heads/main",
    "echo gh api repos/o/r/git/refs/heads/main -X PATCH",
    "grep -r 'git/refs/heads/main' docs",
    "gh api graphql -f query='query { viewer { login } }'",
])
def test_the_twins_of_the_refs_api_writes_stay_as_they_were(cmd):
    assert not gated(assess(cmd))


# ---------------------------------------------------------------- git update-ref

@pytest.mark.parametrize("cmd", [
    "git update-ref refs/heads/main HEAD", "git update-ref refs/heads/master abc123", "git update-ref heads/main HEAD", "git update-ref -d refs/heads/main",
    "git update-ref refs/remotes/origin/main HEAD", "git update-ref refs/tags/v1 HEAD", "git update-ref HEAD abc123", "git update-ref --stdin",
    "git update-ref -m msg refs/heads/main HEAD", "git -C x update-ref refs/heads/main HEAD", "git update-ref refs/heads/$b HEAD", "git update-ref \"$r\" HEAD",
    "git update-ref refs/heads/feat HEAD && git update-ref refs/heads/main HEAD",
])
def test_update_ref_that_can_move_the_default_branch_is_high(cmd):
    a = assess(cmd)
    assert a.rule_id == "R-10" and gated(a)


@pytest.mark.parametrize("cmd", [
    "git update-ref refs/heads/feat HEAD", "git update-ref -d refs/heads/feat", "git update-ref refs/heads/mainline HEAD", "git update-ref refs/heads/feat/x abc123",
    "git rev-parse refs/heads/main", "git show-ref refs/heads/main", "echo git update-ref refs/heads/main HEAD",
])
def test_the_twins_of_update_ref_stay_as_they_were(cmd):
    assert not gated(assess(cmd))


# ---------------------------------------------------------------- other tools' harness config

@pytest.mark.parametrize("cmd", [
    "echo x >> ~/.gemini/settings.json", "cat > .gemini/settings.json", "cp x /tmp/p/.gemini/settings.json", "sed -i '' s/a/b/ ~/.gemini/settings.json",
    "echo x >> GEMINI.md", "echo x >> sub/GEMINI.md", "echo x >> ~/.gemini/GEMINI.md",
    "echo x >> ~/.claude.json", "jq '.mcpServers.x={}' ~/.claude.json > /tmp/n && cp /tmp/n ~/.claude.json", "tee ~/.claude.json",
])
def test_a_write_to_a_harness_config_file_is_high(cmd):
    a = assess(cmd)
    assert a.rule_id == "R-13" and gated(a)


@pytest.mark.parametrize("path", [
    "/Users/someone/.gemini/settings.json", "/Users/someone/proj/.gemini/settings.json", "/Users/someone/proj/GEMINI.md", "/Users/someone/.claude.json",
])
def test_a_write_tool_call_to_those_files_is_high(path):
    a = write(path)
    assert a.rule_id == "R-13" and gated(a)


@pytest.mark.parametrize("cmd", [
    "cat ~/.claude.json", "jq .mcpServers ~/.claude.json", "cat .gemini/settings.json", "git add GEMINI.md", "cp ~/.claude.json /tmp/backup",
    "echo x >> MYGEMINI.md", "echo x >> ~/.claude.json.bak", "echo x >> ~/.gemini/history.json", "echo x >> ~/.claudeXjson", "echo x >> ~/.claude.jsonl",
    "echo x >> my.claude.json",
])
def test_the_twins_of_those_writes_stay_as_they_were(cmd):
    assert not gated(assess(cmd))


# ---------------------------------------------------------------- the hook, end to end

@pytest.mark.parametrize("cmd", [
    "gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=abc123 -F force=true", "gh api repos/o/r/git/refs -f ref=refs/heads/main -f sha=x",
    "git update-ref refs/heads/main HEAD", "echo x >> ~/.gemini/settings.json", "echo x >> ~/.claude.json",
])
def test_the_hook_blocks_each_shape_without_a_permit(cmd):
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "sess-w153", "cwd": CWD}
    assert hooks.pretool(ev).exit_code == 2


# ---------------------------------------------------------------- memory and the proxy never judge a ref write by API (as for a merge)

@pytest.mark.parametrize("cmd", [
    "gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=x", "gh api repos/o/r/git/refs -f ref=refs/heads/main -f sha=x",
])
def test_gh_api_merge_in_sees_a_ref_write(cmd):
    assert policy.gh_api_merge_in(cmd)


def test_update_ref_is_never_a_remembered_kind_and_never_sent_to_jev():
    """`git update-ref` moves a branch with no push: exact at most in memory, and Jev never judges it, as for a merge."""
    from jade import memory, proxy
    a = assess("git update-ref refs/heads/main HEAD")
    assert memory.eligibility(a, "Bash", {"command": "git update-ref refs/heads/main HEAD"}, {}) == "exact"
    assert proxy.skip_reason(a, "Bash", {"command": "git update-ref refs/heads/main HEAD"}, "low") == "a merge"


def test_gh_api_merge_in_does_not_see_a_ref_read():
    assert not policy.gh_api_merge_in("gh api repos/o/r/git/refs/heads/main")
