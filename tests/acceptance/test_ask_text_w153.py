"""W-153 review 2: when R-10 names a call that also matches a never-learned rule (R-13, R-18 ...), the human still reads that rule's sentence, and a local
`git update-ref` is not said to change something outside this Mac.

Commands are only classified (and passed to the hook once, which blocks without a permit); nothing is run.
"""
import pytest

from jade import askplain, hooks, policy

CWD = "/nonexistent-w153"


def assess(cmd):
    return policy.assess("Bash", {"command": cmd}, CWD)


# ---------------------------------------------------------------- the human sees every never-learned rule, and update-ref in its own words (W-153 review 2)

def _header(cmd):
    return askplain.header(assess(cmd), "Bash")


@pytest.mark.parametrize("cmd", [
    "git update-ref refs/heads/main HEAD > AGENTS.md", "git update-ref refs/heads/main HEAD > ~/.ssh/authorized_keys",
    "git update-ref -d refs/heads/main > ~/.claude.json", "gh pr merge 1 > AGENTS.md",
])
def test_the_question_keeps_the_agent_guard_sentence_when_r10_names_the_call(cmd):
    """On 0.7.0 the update-ref shapes were R-13 and the human read the R-13 sentence. R-10 now names them, so the R-13 sentence is added, not lost."""
    assert assess(cmd).rule_id == "R-10"
    assert askplain.RISK_TEXT["R-13"] in _header(cmd)


def test_the_question_names_the_skill_rule_too():
    assert askplain.RISK_TEXT["R-18"] in _header("git update-ref refs/heads/main HEAD > ~/.claude/skills/x/SKILL.md")


@pytest.mark.parametrize("cmd", [
    "git update-ref refs/heads/main HEAD", "git update-ref -d refs/heads/main", "git update-ref refs/tags/v1 HEAD", "git -C x update-ref refs/heads/main HEAD",
    "git update-ref refs/heads/main HEAD > AGENTS.md",
])
def test_a_local_update_ref_is_not_said_to_change_something_outside_this_mac(cmd):
    head = _header(cmd)
    assert askplain.UPDATE_REF_RISK in head and askplain.RISK_TEXT["R-10"] not in head


@pytest.mark.parametrize("cmd", [
    "gh pr merge 1", "git update-ref refs/heads/main HEAD; gh pr merge 1",
    "gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=x",
])
def test_a_call_that_also_changes_something_outside_keeps_the_r10_sentence(cmd):
    head = _header(cmd)
    assert askplain.RISK_TEXT["R-10"] in head and askplain.UPDATE_REF_RISK not in head


def test_the_update_ref_sentence_is_short():
    assert len(askplain.UPDATE_REF_RISK.split()) <= 20


def test_a_call_with_one_rule_shows_one_risk_sentence():
    head = _header("gh pr merge 1")
    assert sum(1 for s in askplain.RISK_TEXT.values() if s in head) == 1


def test_the_hook_block_text_names_the_never_learned_rule_too():
    ev = {"tool_name": "Bash", "tool_input": {"command": "git update-ref refs/heads/main HEAD > AGENTS.md"}, "session_id": "sess-w153b", "cwd": CWD}
    r = hooks.pretool(ev)
    assert r.exit_code == 2 and "(R-10:" in r.stderr and "R-13" in r.stderr
