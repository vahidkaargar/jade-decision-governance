"""W-49 and W-14: the hand-written routing, judgment and calibration sets, the Persian/English slices, and what the runner does with them.
These sets are synthetic: they prove the schema, the split discipline and the offline paths, not calibration (docs/04 section 5)."""
import copy
import importlib.util
import json
import socket
from pathlib import Path

import pytest

from jade import evals

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
SETS = ("routing", "judgment", "calibration")
ALL_FILES = [EVALS / s / "cases.jsonl" for s in (*SETS, "regression", "adversarial")]


def _runner():
    spec = importlib.util.spec_from_file_location("run_cases", EVALS / "run_cases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(name):
    return evals.load_cases(EVALS / name / "cases.jsonl")


# ---- the shipped sets ------------------------------------------------------------------------------
def test_shipped_sets_validate():
    assert evals.validate_sets(EVALS) == []


def test_case_ids_are_unique_across_every_case_file():
    ids = [c["id"] for f in ALL_FILES for c in evals.load_cases(f)]
    assert len(ids) == len(set(ids))


def test_every_language_slice_is_non_empty_in_every_set():
    for name in (*SETS, "adversarial"):
        slices = {evals.case_lang(c) for c in _rows(name)}
        assert {"fa", "en", "mixed"} <= slices, f"{name} has slices {sorted(slices)}"


def test_calibration_slices_each_hold_both_outcomes():
    for lang in ("fa", "en", "mixed"):
        truths = {c["truth"] for c in _rows("calibration") if c["lang"] == lang}
        assert truths == {True, False}, lang


def test_sessions_are_split_by_hash_and_never_shared_between_sets_or_the_protected_split():
    seen = {}
    for name in SETS:
        for c in _rows(name):
            assert c["split"] == evals.bucket(c["session"]) != "protected"
            assert seen.setdefault(c["session"], name) == name, f"session {c['session']} is in {seen[c['session']]} and {name}"
    assert {c["split"] for c in _rows("calibration")} == {"calibration"}
    assert {c["split"] for c in _rows("judgment")} <= {"dev", "validation"}


def test_the_shipped_sets_hold_no_secret_shape_and_no_real_host():
    for name in SETS:
        for c in _rows(name):
            assert [p for part in evals.case_parts(c) for p in evals.text_problems(part)] == [], c["id"]


def test_persian_cases_are_in_persian_script_with_zwnj_where_the_language_needs_it():
    fa = [c for c in _rows("judgment") if c["lang"] == "fa"]
    assert len(fa) >= 10
    assert any("‌" in evals.case_text(c) for c in fa)   # real Persian typing uses the half-space; a set without it is machine-translated


def test_the_routing_set_labels_the_known_persian_gaps_and_the_rest_agree_with_the_router():
    gaps = [c for c in _rows("routing") if c.get("known_gap")]
    assert gaps and {c["lang"] for c in gaps} <= {"fa", "mixed"} and any(c["lang"] == "fa" for c in gaps)
    assert all(c["current_route"] != c["should_route"] for c in gaps)


# ---- the validator rejects what it should ----------------------------------------------------------
def _good():
    return copy.deepcopy(_rows("judgment")[0])


def _problems(case, family="judgment", extra=()):
    return evals.validate_cases(family, [case, *extra])


def test_validator_rejects_a_duplicate_id():
    a = _good()
    assert any("duplicate id" in p for p in _problems(a, extra=[copy.deepcopy(a)]))


def test_validator_rejects_a_declared_split_that_the_session_hash_does_not_give():
    a = _good()
    a["split"] = "calibration" if a["split"] != "calibration" else "dev"
    assert any("split" in p for p in _problems(a))


def test_validator_rejects_a_session_in_two_splits_and_one_shared_by_two_sets():
    a, b = _good(), copy.deepcopy(_rows("judgment")[1])
    b["session"], b["split"] = a["session"], "dev" if a["split"] != "dev" else "validation"
    assert any("two splits" in p for p in _problems(a, extra=[b]))
    cal = copy.deepcopy(_rows("calibration")[0])
    cal["session"], cal["split"] = a["session"], a["split"]
    probs = evals.cross_check({"judgment": [a], "calibration": [cal]})
    assert any("two sets" in p for p in probs)


def test_validator_rejects_protected_cases_and_the_wrong_split_for_the_family():
    a = _good()
    a["session"] = next(f"x{i}" for i in range(500) if evals.bucket(f"x{i}") == "protected")
    a["split"] = "protected"
    assert any("protected" in p for p in _problems(a))
    cal = copy.deepcopy(_rows("calibration")[0])
    cal["session"], cal["split"] = a["session"].replace("x", "y"), "dev"
    cal["session"] = next(f"y{i}" for i in range(500) if evals.bucket(f"y{i}") == "dev")
    assert any("calibration" in p for p in _problems(cal, "calibration"))


def test_validator_rejects_a_wrong_language_tag_and_a_missing_one():
    a = _good()
    a["lang"] = "fa" if a["lang"] != "fa" else "en"
    assert any("lang" in p for p in _problems(a))
    del a["lang"]
    assert any("lang" in p for p in _problems(a))


def test_validator_rejects_bad_labels():
    a = _good()
    a["truth"] = "yes"
    assert any("truth" in p for p in _problems(a))
    choice = next(copy.deepcopy(c) for c in _rows("judgment") if c["family"] == "strategy_choice")
    choice["best"] = "zz"
    assert any("best" in p for p in _problems(choice))
    r = copy.deepcopy(_rows("routing")[0])
    r["should_route"] = "MAYBE"
    assert any("should_route" in p for p in _problems(r, "routing"))
    r = copy.deepcopy(_rows("routing")[0])
    r["should_call_jev"] = not r["should_call_jev"]
    assert any("should_call_jev" in p for p in _problems(r, "routing"))


def test_validator_rejects_a_known_gap_without_a_different_current_route():
    r = copy.deepcopy(next(c for c in _rows("routing") if c.get("known_gap")))
    r["current_route"] = r["should_route"]
    assert any("known_gap" in p for p in _problems(r, "routing"))
    r = copy.deepcopy(_rows("routing")[0])
    r["current_route"] = "DEEP"
    assert any("current_route" in p for p in _problems(r, "routing"))


def test_validator_rejects_a_secret_shape_and_a_real_looking_host():
    a = _good()
    a["evidence"] = "token " + "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    assert any("secret" in p for p in _problems(a))
    a = _good()
    a["evidence"] = "GET https://api." + "prod-payments" + ".io/v1/health"
    assert any("host" in p for p in _problems(a))
    a = _good()
    a["evidence"] = "connected to 8.8.8.8"
    assert any("host" in p for p in _problems(a))


def test_validator_rejects_a_set_with_an_empty_language_slice(tmp_path):
    for s in SETS:
        (tmp_path / s).mkdir()
        rows = _rows(s) if s != "judgment" else [c for c in _rows(s) if c["lang"] != "fa"]
        (tmp_path / s / "cases.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in rows) + "\n")
    assert any("fa" in p and "empty" in p for p in evals.validate_sets(tmp_path))


def test_loader_reports_a_line_that_is_not_json(tmp_path):
    f = tmp_path / "cases.jsonl"
    f.write_text('{"id": "X1"}\nnot json\n')
    with pytest.raises(ValueError, match="line 2"):
        evals.load_cases(f)


# ---- the offline runner ----------------------------------------------------------------------------
def test_runner_replays_the_new_sets_offline_with_no_network(monkeypatch, capsys):
    def no_network(*a, **k):
        raise AssertionError("an eval set run opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    files = [str(EVALS / s / "cases.jsonl") for s in SETS]
    assert _runner().main(files) == 0
    out = capsys.readouterr().out
    total = sum(len(_rows(s)) for s in SETS)
    assert f"{total}/{total} cases pass" in out
    gaps = sum(1 for c in _rows("routing") if c.get("known_gap"))
    assert f"{gaps} known gaps" in out


def test_runner_reports_pass_rate_per_language_slice(capsys):
    assert _runner().main([]) == 0
    out = capsys.readouterr().out
    for lang in ("en", "fa", "mixed"):
        assert any(line.split()[:1] == [lang] and "/" in line for line in out.splitlines()), lang
    assert "by language" in out


def test_runner_fails_when_a_known_gap_has_closed_or_drifted(tmp_path, capsys):
    gap = copy.deepcopy(next(c for c in _rows("routing") if c.get("known_gap")))
    closed = {**gap, "id": "RTX1", "current_route": gap["should_route"], "known_gap": "now fixed"}
    closed_file = tmp_path / "closed.jsonl"
    closed_file.write_text(json.dumps(closed, ensure_ascii=False) + "\n")
    assert _runner().main([str(closed_file)]) == 1
    drift = {**gap, "id": "RTX2", "current_route": "DEEP"}
    drift_file = tmp_path / "drift.jsonl"
    drift_file.write_text(json.dumps(drift, ensure_ascii=False) + "\n")
    assert _runner().main([str(drift_file)]) == 1


def test_runner_fails_when_a_route_label_is_wrong(tmp_path):
    r = copy.deepcopy(next(c for c in _rows("routing") if not c.get("known_gap")))
    r["should_route"] = "DEEP" if r["should_route"] != "DEEP" else "FAST"
    f = tmp_path / "bad.jsonl"
    f.write_text(json.dumps(r, ensure_ascii=False) + "\n")
    assert _runner().main([str(f)]) == 1


def test_judgment_input_reaches_the_provider_byte_identical(capsys):
    """The offline half of a judgment case: Persian text, half-spaces and Persian digits go through redaction and the state cap unchanged."""
    assert _runner().main([str(EVALS / "judgment" / "cases.jsonl")]) == 0


def test_the_adversarial_set_carries_persian_and_mixed_script_cases_tagged_by_language():
    tagged = [c for c in _rows("adversarial") if c.get("lang")]
    assert {c["lang"] for c in tagged} >= {"fa", "mixed"}
    assert len(tagged) >= 12
    assert all(c["lang"] == evals.case_lang({**c, "lang": None}) for c in tagged)


def test_docs_no_longer_call_the_sets_empty_and_do_not_claim_calibration():
    doc = (ROOT / "docs" / "04-calibration-and-evaluation.md").read_text()
    assert "empty directory" not in doc
    for s in SETS:
        assert f"evals/{s}" in doc or f"{s}/" in doc
    assert "synthetic" in doc.lower() and "not calibrat" in doc.lower()


# ---- review round 2 -------------------------------------------------------------------------------------
def _copy_sets(tmp_path):
    for s in SETS:
        (tmp_path / s).mkdir()
        (tmp_path / s / "cases.jsonl").write_text((EVALS / s / "cases.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("line", ["[1,2]", "null", '"x"', "7", "true",
                                  '{"id":"JG99","session":["a"],"kind":"judge_input","class":"c","lang":"en","family":"verify_claim"}',
                                  '{"id":["JG98"],"session":"s","kind":"judge_input","class":"c","lang":"en","family":"verify_claim"}',
                                  '{"id":"JG97","session":{"a":1},"split":["dev"],"kind":"judge_input","class":"c","lang":["en"],"family":"verify_claim"}',
                                  '{"id":"JG96","session":null,"lang":"en"}'])
def test_the_validator_reports_a_malformed_case_line_instead_of_raising(tmp_path, line):
    root = _copy_sets(tmp_path)
    with (root / "judgment" / "cases.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    problems = evals.validate_sets(root)
    assert problems and all(isinstance(p, str) for p in problems)


def test_the_validator_names_a_case_line_that_is_not_an_object(tmp_path):
    root = _copy_sets(tmp_path)
    with (root / "judgment" / "cases.jsonl").open("a", encoding="utf-8") as f:
        f.write("[1,2]\n")
    assert any("not a JSON object" in p for p in evals.validate_sets(root))


def test_the_runner_says_the_judgment_and_calibration_cases_check_input_delivery_only(capsys):
    assert _runner().main([]) == 0
    out = capsys.readouterr().out
    n = len(_rows("judgment")) + len(_rows("calibration"))
    assert f"{n} judge_input cases check input delivery only" in out
    assert "not measured" in out


def test_the_language_table_does_not_count_a_known_gap_as_a_pass(capsys):
    assert _runner().main([]) == 0
    out = capsys.readouterr().out
    rows = {line.split()[0]: line for line in out.splitlines() if line.split()[:1] and line.split()[0] in ("en", "fa", "mixed", "unknown") and "/" in line}
    gaps_fa = [c for c in _rows("routing") if c.get("known_gap") and evals.case_lang(c) == "fa"]
    assert gaps_fa, "the shipped routing set records Persian gaps"
    ok, n = rows["fa"].split()[1].split("/")
    assert int(ok) < int(n) and f"{len(gaps_fa)} known gap" in rows["fa"]
