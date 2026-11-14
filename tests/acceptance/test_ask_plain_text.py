"""Clear questions, step 1: the chat question and the macOS dialog start with plain lines.

First lines, in order: the token line (chat only), what will happen, why it is risky, what else is in the call. The full command comes after them.
The text comes only from the gate (jade/askplain.py), never from the agent. Every sentence is short and simple English.
"""

from pathlib import Path

import pytest
import yaml

from jade import askchat, askdialog, askplain, policy

RISK_YAML = Path(__file__).resolve().parents[2] / "policies" / "risk.yaml"
OWNER_CMD = 'cd /Users/x/iro && gh pr merge 940 --squash --delete-branch 2>&1 | tail -1; gh pr view 940 --json state -q .state; cat a.txt; pgrep -fc "cargo test"'
TWO_CMD = "gh pr merge 940 --squash; git push --force origin feature"


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")


def assess(cmd: str):
    return policy.assess("Bash", {"command": cmd}, cwd="/tmp")


def words(sentence: str) -> int:
    return len(sentence.split())


def rule_ids() -> list[str]:
    doc = yaml.safe_load(RISK_YAML.read_text())
    return [r["id"] for r in doc["rules"]] + [r["id"] for r in doc["mcp_tools"]]


def test_every_rule_has_one_short_plain_sentence():
    ids = rule_ids()
    assert len(ids) > 30
    assert [i for i in ids if i not in askplain.RISK_TEXT] == []
    for rid, sentence in askplain.RISK_TEXT.items():
        assert sentence.endswith(".") and sentence.count(". ") == 0, rid
        assert words(sentence) <= 20, rid


def test_risk_line_for_an_unknown_rule_is_generic_and_short():
    line = askplain.risk_line("R-9999")
    assert line and words(line) <= 20 and "R-9999" not in line


def test_the_owner_example_says_the_merge_and_counts_the_rest():
    a = assess(OWNER_CMD)
    assert a.rule_id == "R-10" and len(a.seg_keys) == 1
    assert askplain.what_line(a, "Bash").startswith("Merge pull request 940")
    assert askplain.other_count(a) == 3
    head = askplain.header(a, "Bash")
    assert head[0].startswith("Merge pull request 940")
    assert head[1] == askplain.risk_line("R-10")
    assert head[2] == "Also in this call: 3 other commands that only read."
    assert len(head) == 3


def test_a_call_with_no_other_commands_has_no_also_line():
    a = assess("gh pr merge 940 --squash")
    assert askplain.other_count(a) == 0
    assert all("Also in this call" not in line for line in askplain.header(a, "Bash"))


def test_one_other_command_is_said_in_the_singular():
    a = assess("gh pr merge 940 --squash; cat a.txt")
    assert "Also in this call: 1 other command that only reads." in askplain.header(a, "Bash")


def test_the_chat_question_starts_with_the_plain_lines_and_the_command_comes_after():
    a = assess(OWNER_CMD)
    lines = askchat.question_for("0123456789ab", a, "Bash", {})["question"].split("\n")
    assert lines[0].startswith("[jade:ask_0123456789ab]") and "R-10" in lines[0]
    assert lines[1:4] == askplain.header(a, "Bash")
    text = "\n".join(lines)
    assert text.index("Command:") > text.index("Also in this call") and OWNER_CMD.split(" && ")[0] in text


def test_the_dialog_text_has_the_same_first_lines():
    a = assess(OWNER_CMD)
    lines = askdialog.text_for(a, "Bash", {}).split("\n")
    assert lines[:3] == askplain.header(a, "Bash")
    text = "\n".join(lines)
    assert text.index("Command:") > text.index("Also in this call")


def test_a_call_with_two_risky_commands_says_so():
    a = assess(TWO_CMD)
    assert len(a.seg_keys) == 2
    head = askplain.header(a, "Bash")
    assert "This call joins 2 risky commands. Ask the agent to send them one by one." in head
    assert head[0].startswith("Merge pull request 940")
    assert "Force push" in head[0]
    q = askchat.question_for("0123456789ab", a, "Bash", {})["question"]
    assert "joins 2 risky commands" in q and "joins 2 risky commands" in askdialog.text_for(a, "Bash", {})


def test_no_agent_text_and_old_rationale_line_gone():
    a = assess("git reset --hard HEAD~1")
    q = askchat.question_for("0123456789ab", a, "Bash", {})["question"]
    assert a.rationale not in q and "R-11" in q and "git reset --hard HEAD~1" in q


@pytest.mark.parametrize("cmd,start", [
    ("git push origin main", "Push"),
    ("git push --force origin feature", "Force push"),
    ("git reset --hard HEAD~1", "Throw away"),
    ("npm publish", "Publish"),
    ("gh pr merge --squash", "Merge the pull request"),
    ("unknowncmd --flag", "Run: "),
])
def test_what_line_renderers(cmd, start):
    a = assess(cmd)
    if not a.seg_keys:   # a low call has no gated segment: build the key by hand so the renderer is still exercised
        a.seg_keys, a.seg_rules = [cmd], [a.rule_id]
    line = askplain.what_line(a, "Bash")
    assert line.startswith(start) and words(line) <= 20 + 6   # the fallback may carry 60 characters of the command


def test_what_line_for_other_tools():
    a = assess("echo hi")
    assert askplain.what_line(a, "mcp__kube__kubectl_apply").startswith("Use ")
    assert "kubectl apply" in askplain.what_line(a, "mcp__kube__kubectl_apply")
    assert askplain.what_line(a, "Write").startswith("Write")
    assert askplain.what_line(a, "Edit").startswith("Edit")
    head = askplain.header(a, "mcp__kube__kubectl_apply")
    assert head[0].startswith("Use ") and all(not line.startswith("Also") for line in head)
