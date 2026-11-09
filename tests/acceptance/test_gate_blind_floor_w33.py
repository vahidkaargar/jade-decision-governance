"""W-33 (docs/05 T18): a script file or a computed directory handed to a verb the gate treats as read-only (`sed -f F`, `awk -f F`, `cd $VAR && sort -o out in`) is
floored at medium (R-32), recorded and not gated.

Decision (reversible): medium, not high. The permit floor is high, so nothing is stopped and no routine `sed -f` or `awk -f` is slowed; the rating says in the ledger,
and to a human who lowers `permit_required_at`, that the program or the directory exists only at run time. A script run by name (`bash x.sh`, `python3 x.py`), `eval`
and a patch stay R-31 (low): that is most of what an agent runs. Every true positive sits next to a false-positive twin.

Commands are synthetic. Nothing here runs a command: the cases go through `policy.assess` and `hooks.pretool` with a throwaway JADE home.
"""
import json

import pytest

from jade import hooks, ledger, policy

CWD = "/tmp/case"


@pytest.fixture(autouse=True)
def cold_policy():
    policy.reset_cache()
    yield
    policy.reset_cache()


def sh(cmd):
    return policy.assess("Bash", {"command": cmd}, CWD)


def gate(cmd, session="s-w33"):
    return hooks.pretool({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": CWD})


FLOORED = [
    ("sed -f x.sed in", "script-option"),
    ("sed -nf x.sed in", "script-option"),
    ("sed --file=x.sed in", "script-option"),
    ("awk -f x.awk in", "script-option"),
    ("gawk --file x.awk in", "script-option"),
    ("cd $NOPE/x && sort -o settings.json in", "computed-cwd"),
    ('cd "$DIR" && sort -o out.txt in', "computed-cwd"),
    ("cd $NOPE/x && cat in > out.txt", "computed-cwd"),
    ("cd $NOPE/x && cp a b", "computed-cwd"),
]


@pytest.mark.parametrize("cmd,label", FLOORED)
def test_a_script_option_or_a_computed_directory_is_medium_and_recorded(cmd, label):
    a = sh(cmd)
    assert a.rule_id == "R-32" and a.level == "medium" and a.recorded and label in a.blind and not a.blocked, (cmd, a.rule_id, a.level, a.blind)


@pytest.mark.parametrize("cmd", [c for c, _ in FLOORED])
def test_the_floor_does_not_gate_by_default(cmd):
    assert policy.permit_floor() == "high"
    assert gate(cmd).exit_code == 0


@pytest.mark.parametrize("cmd", [
    "bash x.sh",
    "python3 tools/x.py --week",
    "./scripts/run",
    'eval "$(cat f)"',
    'eval "$CMD"',
    "git diff | git apply -",
])
def test_the_other_blind_labels_stay_low(cmd):
    a = sh(cmd)
    assert a.rule_id == "R-31" and a.level == "low" and a.recorded, (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", [
    "sed -n 1,5p x.txt",
    "sed -i s/a/b/ x.txt",
    "sed -f - in",
    "awk '{print $1}' x.txt",
    "awk -F, '{print $1}' x.txt",
    "cd /tmp/x && sort -o out.txt in",
    "cd $NOPE/x && cat in",
    "cd $NOPE/x && ls -la",
    "cd $NOPE/x && git status",
    "ls ./scripts",
    "cat x.sed",
])
def test_twins_of_the_floor_are_not_rated_medium(cmd):
    a = sh(cmd)
    assert a.rule_id not in ("R-31", "R-32") and a.level == "low" and not a.recorded and not a.blind, (cmd, a.rule_id, a.level, a.blind)


def test_the_floor_never_lowers_a_higher_rating():
    assert sh("sed -f x.sed in > ~/.claude/settings.json").rule_id == "R-13"
    assert sh("awk -f x.awk in && terraform apply").rule_id == "R-03"
    assert sh("cd $NOPE/x && sort -o out in && git push origin feat/x").rule_id == "R-10"


def test_a_medium_label_wins_over_a_low_one_and_both_are_recorded():
    a = sh("bash x.sh && sed -f x.sed in")
    assert a.rule_id == "R-32" and a.level == "medium" and a.blind == ["script-file", "script-option"]


def test_a_human_who_lowers_the_permit_floor_to_medium_gets_a_permit_prompt(monkeypatch):
    monkeypatch.setattr(policy, "permit_floor", lambda: "medium")
    r = gate("sed -f x.sed in")
    assert r.exit_code == 2
    assert gate("bash x.sh").exit_code == 0   # R-31 stays below that floor


def _blind_events():
    db = ledger.open()
    try:
        return db.all("SELECT guard, actor, detail_json FROM events WHERE kind='blind_spot' ORDER BY id")
    finally:
        db.close()


def test_the_floor_is_journaled_by_rule_and_label_only_once_per_session():
    for _ in range(3):
        gate("sed -f secret-looking.sed in", session="s-a")
    gate("cd $NOPE/x && sort -o out in", session="s-a")
    gate("sed -f other.sed in", session="s-b")
    rows = _blind_events()
    assert [r.guard for r in rows] == ["R-32 medium script-option", "R-32 medium computed-cwd", "R-32 medium script-option"]
    assert [json.loads(r.detail_json)["blind"] for r in rows] == [["script-option"], ["computed-cwd"], ["script-option"]]
    text = json.dumps([vars(r) for r in rows])
    assert "secret-looking" not in text and "other.sed" not in text and "NOPE" not in text


def test_the_rule_is_defined_medium_and_listed_as_recorded():
    rules = {r["id"]: r for r in policy.risk()["rules"]}
    assert rules["R-32"]["level"] == "medium" and rules["R-31"]["level"] == "low"
    assert "R-32" in policy.RECORDED_RULES
    ids = [r["id"] for r in policy.risk()["rules"]]
    assert ids.index("R-32") < ids.index("R-31")   # first match wins inside one text


def test_the_decision_is_recorded_in_the_threat_model_and_the_runbook():
    root = __import__("pathlib").Path(hooks.__file__).resolve().parents[1]
    t18 = next(line for line in (root / "docs" / "05-threat-model.md").read_text().splitlines() if line.startswith("| T18 | Classification gaps"))
    assert "R-32" in t18 and "W-33" in t18
    assert "R-32" in (root / "docs" / "08-deployment-runbook.md").read_text()
