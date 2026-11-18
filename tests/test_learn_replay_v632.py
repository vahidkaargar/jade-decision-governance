"""V6-32: the replay of a policy table (evals/learn_replay.py) and the case ratchet check (evals/case_ratchet.py).

The transcripts here are synthetic and live in tmp_path. The fake trees for the ratchet check are two directories with a stub `evals/run_cases.py`; one test runs the real
runner on this checkout.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "evals" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def replay():
    return load("learn_replay")


@pytest.fixture
def ratchet():
    return load("case_ratchet")


def transcript(tmp_path, commands, name="t.jsonl"):
    lines = [json.dumps({"cwd": "/tmp/work", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": c}}]}}) for c in commands]
    f = tmp_path / name
    f.write_text("\n".join(lines) + "\n")
    return f


def table(*rules):
    return [{"rule": r, "level": lv, "advice": adv, "asked": 30, "allowed": 30, "denied": 0, "wrong_marks": 0, "why": "x"} for r, lv, adv in rules]


# ---- learn_replay ----------------------------------------------------------------------------------------------------------------------
def test_a_candidate_whose_rule_decides_no_human_only_call_replays_clean(replay, tmp_path):
    t = transcript(tmp_path, ["git reset --hard HEAD~1", "git status", "git reset --hard HEAD~2"])
    out = replay.replay_table(table(("R-11", "high", "review")), replay.classify_corpus(replay.corpus_items([t])))
    (row,) = out
    assert {k: row["replay"][k] for k in ("calls", "decided_by_rule", "candidate_level", "human_only_flips")} == {"calls": 3, "decided_by_rule": 2, "candidate_level": "medium", "human_only_flips": 0}
    assert row["advice"] == "review" and "high" in row["replay"]["moves"] and "medium" in row["replay"]["moves"]


def test_a_human_only_call_decided_by_the_rule_withdraws_the_candidate(replay, tmp_path):
    t = transcript(tmp_path, ["rm -rf ./projectdata", "git reset --hard HEAD~1"])
    (row,) = replay.replay_table(table(("R-11", "high", "review")), replay.classify_corpus(replay.corpus_items([t])))
    assert row["replay"]["human_only_flips"] == 1 and row["advice"] == "blocked_by_replay"


def test_opposite_flips_do_not_cancel_each_other(replay, tmp_path):
    """The aggregate counters of evals/ho_replay.py would net two opposite moves to zero; the replay counts every human-only call the rule decides, one by one."""
    t = transcript(tmp_path, ["rm -rf ./projectdata", "rm -rf ./other-data"])
    (row,) = replay.replay_table(table(("R-11", "high", "review")), replay.classify_corpus(replay.corpus_items([t])))
    assert row["replay"]["human_only_flips"] == 2


def test_only_review_rows_are_replayed_and_the_rest_pass_through_unchanged(replay, tmp_path):
    t = transcript(tmp_path, ["git status"])
    rows = table(("R-11", "high", "hold"), ("R-13", "critical", "protected"), ("R-12", "high", "too_few"))
    assert replay.replay_table(rows, replay.classify_corpus(replay.corpus_items([t]))) == rows


def test_a_rule_at_the_lowest_level_has_no_candidate(replay, tmp_path):
    t = transcript(tmp_path, ["git status"])
    (row,) = replay.replay_table(table(("R-99", "low", "review")), replay.classify_corpus(replay.corpus_items([t])))
    assert row["advice"] == "not_replayed" and "lowest" in row["replay"]["reason"]


def test_an_empty_corpus_proves_nothing(replay, tmp_path):
    (row,) = replay.replay_table(table(("R-11", "high", "review")), [])
    assert row["advice"] == "not_replayed" and "no calls" in row["replay"]["reason"]


def test_the_replay_output_holds_no_command_text(replay, tmp_path, capsys):
    t = transcript(tmp_path, ["rm -rf ./SECRET-PROJECT-NAME", "git reset --hard HEAD~1"])
    tab = tmp_path / "table.json"
    tab.write_text(json.dumps(table(("R-11", "high", "review"))))
    code = replay.main([str(tab), str(t)])
    out = capsys.readouterr().out
    assert code == 1 and "SECRET-PROJECT-NAME" not in out and "blocked_by_replay" in out


def test_exit_codes_clean_flip_and_nothing_replayed(replay, tmp_path, capsys):
    t = transcript(tmp_path, ["git reset --hard HEAD~1"])
    tab = tmp_path / "table.json"
    tab.write_text(json.dumps(table(("R-11", "high", "review"))))
    assert replay.main([str(tab), str(t)]) == 0
    assert replay.main([str(tab), str(tmp_path / "empty-dir-that-does-not-exist")]) == 2
    tab.write_text(json.dumps(table(("R-11", "high", "hold"))))
    assert replay.main([str(tab)]) == 0   # nothing to replay, nothing to prove
    capsys.readouterr()


# ---- case_ratchet ----------------------------------------------------------------------------------------------------------------------
STUB = """
import pathlib, sys
mode = (pathlib.Path(__file__).resolve().parent.parent / "mode.txt").read_text().split()
if mode[0] == "crash":
    sys.exit(3)
import json
bad = set(mode[1:])
fail = False
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    c = json.loads(line)
    f = (mode[0] == "fail-all") or c["id"] in bad
    fail = fail or f
    print(("FAIL  " if f else "ok    ") + c["id"] + " " + c["class"])
print("x/y cases pass")
sys.exit(1 if fail else 0)
"""


def fake_tree(tmp_path, name, mode):
    root = tmp_path / name
    (root / "evals").mkdir(parents=True)
    (root / "evals" / "run_cases.py").write_text(STUB)
    (root / "mode.txt").write_text(mode)
    return str(root)


def rows_file(tmp_path, *rows):
    f = tmp_path / "cases.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return f


CASE = {"id": "RC-aaaaaaaa", "class": "wrong_mark_R-11", "kind": "gate", "command": "x", "expect": "block", "twin": "unverified"}
TWIN = {"id": "RC-bbbbbbbb", "class": "twin_of_RC-aaaaaaaa", "kind": "gate", "command": "y", "expect": "allow", "twin": "human", "twin_of": "RC-aaaaaaaa"}


def test_a_case_that_fails_on_the_erred_version_and_passes_on_the_fix_is_ready(ratchet, tmp_path, capsys):
    f = rows_file(tmp_path, CASE, TWIN)
    code = ratchet.main(["check", str(f), "--erred", fake_tree(tmp_path, "erred", "fail RC-aaaaaaaa"), "--fixed", fake_tree(tmp_path, "fixed", "pass")])
    out = capsys.readouterr().out
    assert code == 0 and "RC-aaaaaaaa  ready" in out and "RC-bbbbbbbb  twin ok" in out


def test_a_case_that_already_passes_on_the_erred_version_is_not_reproduced(ratchet, tmp_path, capsys):
    f = rows_file(tmp_path, CASE)
    code = ratchet.main(["check", str(f), "--erred", fake_tree(tmp_path, "erred", "pass"), "--fixed", fake_tree(tmp_path, "fixed", "pass")])
    assert code == 1 and "not reproduced" in capsys.readouterr().out   # the error lives in learned state (memory or Jev): a human writes that setup


def test_a_case_that_still_fails_on_the_fix_is_not_fixed(ratchet, tmp_path, capsys):
    f = rows_file(tmp_path, CASE)
    code = ratchet.main(["check", str(f), "--erred", fake_tree(tmp_path, "erred", "fail-all"), "--fixed", fake_tree(tmp_path, "fixed", "fail-all")])
    assert code == 1 and "not fixed" in capsys.readouterr().out


def test_a_twin_that_fails_on_the_fix_means_the_fix_over_blocks(ratchet, tmp_path, capsys):
    f = rows_file(tmp_path, CASE, TWIN)
    code = ratchet.main(["check", str(f), "--erred", fake_tree(tmp_path, "erred", "fail RC-aaaaaaaa"), "--fixed", fake_tree(tmp_path, "fixed", "fail RC-bbbbbbbb")])
    assert code == 1 and "twin fails on the fix" in capsys.readouterr().out


def test_a_runner_that_crashes_is_exit_2_and_never_read_as_a_failing_case(ratchet, tmp_path, capsys):
    f = rows_file(tmp_path, CASE)
    code = ratchet.main(["check", str(f), "--erred", fake_tree(tmp_path, "erred", "crash"), "--fixed", fake_tree(tmp_path, "fixed", "pass")])
    assert code == 2 and "could not run" in capsys.readouterr().err


def test_an_unknown_ref_is_exit_2(ratchet, tmp_path, capsys):
    f = rows_file(tmp_path, CASE)
    assert ratchet.main(["check", str(f), "--erred", "no-such-ref-at-all", "--fixed", fake_tree(tmp_path, "fixed", "pass")]) == 2


def test_a_diff_from_jade_learn_cases_is_read_like_a_case_file(ratchet, tmp_path):
    from jade import learn

    old = (ROOT / "evals" / "regression" / "cases.jsonl").read_text()
    diff = tmp_path / "p.diff"
    diff.write_text(learn.case_diff(old, [CASE, TWIN]))
    assert [r["id"] for r in ratchet.read_cases(diff)] == ["RC-aaaaaaaa", "RC-bbbbbbbb"]
    assert [r["id"] for r in ratchet.read_cases(rows_file(tmp_path, CASE))] == ["RC-aaaaaaaa"]


def test_the_real_runner_on_this_checkout_says_not_reproduced_for_a_case_the_code_already_handles(ratchet, tmp_path, capsys):
    """Both refs are this working tree: `git push origin main` is blocked by the code today, so there is nothing to ratchet, and the check says so."""
    good = {"id": "RC-cccccccc", "class": "wrong_mark_R-14", "kind": "gate", "command": "git push origin main", "expect": "block", "twin": "unverified"}
    f = rows_file(tmp_path, good)
    code = ratchet.main(["check", str(f), "--erred", str(ROOT), "--fixed", str(ROOT)])
    assert code == 1 and "RC-cccccccc  not reproduced" in capsys.readouterr().out
