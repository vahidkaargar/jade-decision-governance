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
    assert head[2] == "Also in this call: 3 other commands that JADE rates as low risk."
    assert len(head) == 3


def test_a_call_with_no_other_commands_has_no_also_line():
    a = assess("gh pr merge 940 --squash")
    assert askplain.other_count(a) == 0
    assert all("Also in this call" not in line for line in askplain.header(a, "Bash"))


def test_one_other_command_is_said_in_the_singular():
    a = assess("gh pr merge 940 --squash; cat a.txt")
    head = askplain.header(a, "Bash")
    assert "Also in this call: 1 other command that JADE rates as low risk." in head
    assert all("only read" not in line for line in head[2:])   # a low-risk command may also write (git commit), so the line never says it only reads


def test_a_low_risk_write_is_counted_without_claiming_it_only_reads():
    a = assess("gh pr merge 940 --squash; git commit -m x")
    assert askplain.other_count(a) == 1
    assert "Also in this call: 1 other command that JADE rates as low risk." in askplain.header(a, "Bash")


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
    assert line.startswith(start)
    if start == "Run: ":   # the fallback row carries at most FALLBACK_CHARS characters of the command
        assert len(line) <= len("Run: ") + askplain.FALLBACK_CHARS
    else:
        assert words(line) <= 20


def test_what_line_for_other_tools():
    a = assess("echo hi")
    assert askplain.what_line(a, "mcp__kube__kubectl_apply").startswith("Use ")
    assert "kubectl apply" in askplain.what_line(a, "mcp__kube__kubectl_apply")
    assert askplain.what_line(a, "Write").startswith("Write")
    assert askplain.what_line(a, "Edit").startswith("Edit")
    head = askplain.header(a, "mcp__kube__kubectl_apply")
    assert head[0].startswith("Use ") and all(not line.startswith("Also") for line in head)


# ------------------------------------------------------------------ a human-only class on a low call: the risk line must not say "only reads"

def test_a_delete_class_on_a_low_call_says_it_can_delete_data():
    a = assess('psql -c "DROP TABLE x"')
    assert a.rule_id == "R-99" and "HO-delete" in a.ho_ids
    head = askplain.header(a, "Bash")
    assert "only reads" not in " ".join(head) and "edits a local file" not in " ".join(head)
    assert head[1] == "This call can delete data, and only you can decide that."
    q = askchat.question_for("0123456789ab", a, "Bash", {})["question"]
    assert "can delete data" in q and "only reads" not in q and "can delete data" in askdialog.text_for(a, "Bash", {})


def test_a_money_class_on_a_low_call_says_it_can_move_money():
    a = assess("stripe refunds create --charge ch_1 --live")
    assert a.rule_id == "R-99" and "HO-money" in a.ho_ids
    assert askplain.header(a, "Bash")[1] == "This call can move money, and only you can decide that."


def test_an_unknown_class_gets_the_generic_line():
    assert askplain.risk_line("R-99", ("HO-new-thing",)) == "A rule that only you can decide matches this call."
    assert askplain.risk_line("R-99", ("HO-delete", "HO-new-thing")) == "A rule that only you can decide matches this call."


def test_every_class_phrase_is_short_and_the_line_keeps_the_rule_text_for_a_high_rule():
    for ho in askplain.HO_TEXT:
        assert words(askplain.risk_line("R-99", (ho,))) <= 20, ho
    assert askplain.risk_line("R-11", ("HO-delete",)) == askplain.RISK_TEXT["R-11"]


def test_every_human_only_class_has_a_phrase():
    from jade import humanonly
    for ho in (*humanonly.ALL, "HO-wiring", "HO-checkout-install", "HO-settings-shadow", "HO-state-path", "HO-mod", "HO-R17", "HO-terminal"):
        assert ho in askplain.HO_TEXT, ho


# ------------------------------------------------------------------ the merge sentence names the real target

@pytest.mark.parametrize("cmd,line", [
    ("gh pr merge https://github.com/o/r/pull/940 --squash", "Merge pull request 940."),
    ("gh pr merge feature-branch --squash", "Merge the pull request of branch feature-branch."),
    ("gh pr merge 940", "Merge pull request 940."),
    ("gh pr merge --squash 940", "Merge pull request 940."),
    ("gh pr merge", "Merge the pull request of this branch."),
    ("gh pr merge --squash --delete-branch", "Merge the pull request of this branch."),
    ("gh pr merge --body done --squash", "Merge a pull request."),
])
def test_the_merge_sentence_names_the_real_target(cmd, line):
    a = assess(cmd)
    assert a.seg_keys and askplain.what_line(a, "Bash") == line


# Final whole-branch review, finding 3: the value of a flag that takes one is not the PR number.
@pytest.mark.parametrize("cmd,line", [
    ("gh pr merge --body 5 940", "Merge a pull request."),
    ("gh pr merge -b 5 940", "Merge a pull request."),
    ("gh pr merge --subject 7 940", "Merge a pull request."),
    ("gh pr merge -t 7 940", "Merge a pull request."),
    ("gh pr merge --squash --body 5 940", "Merge a pull request."),
    ("gh pr merge --match-head-commit 123 940", "Merge a pull request."),
    ("gh pr merge 940 --squash", "Merge pull request 940."),
    ("gh pr merge 940 --body 5", "Merge pull request 940."),
    ("gh pr merge --squash 940", "Merge pull request 940."),
    ("gh pr merge --squash --delete-branch 940", "Merge pull request 940."),
    ("gh pr merge --body=5 940", "Merge pull request 940."),
])
def test_a_value_flag_before_the_number_does_not_name_a_pull_request(cmd, line):
    a = assess(cmd)
    assert a.seg_keys and askplain.what_line(a, "Bash") == line, a.seg_keys
