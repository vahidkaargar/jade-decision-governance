"""V6-21: the rubric, the label loader, the agreement code (kappa, confusion) and the empty corpus file (docs/14-v6/10 section 8, gates G6 and G8).

The 150 labelled rows are owner hours and are not here. These tests use synthetic rows made in the test, in a temp folder outside the repository, so nothing real is read or written."""
import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "evals" / "labels"
RUBRIC = ROOT / "evals" / "rubric"
HARM = {"allow": ("not_harmful", "yes"), "ask": ("needs_human", "no"), "block": ("must_block", "no")}


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ag():
    return _module("agree_v621", LABELS / "agree.py")


@pytest.fixture(scope="module")
def stats():
    return _module("stats_v621", ROOT / "evals" / "stats.py")


def _label(kind):
    h, a = HARM[kind]
    return {"harm": h, "authority": a}


def make_row(i, r1="allow", r2=None, cluster=None, adjudicated=False, final=None, **extra):
    """A synthetic gray-zone row. r1 and r2 are the 3-class answers of the two raters (r2 defaults to r1)."""
    r2 = r2 or r1
    final_kind = final or r1
    row = {"id": f"LB{i:03d}", "class": "gray", "kind": "gate", "command": f"echo step {i}", "source": "synthetic", "cluster": cluster or f"task-{i % 12}",
           "rubric_version": 1, "raw": {"rater1": _label(r1), "rater2": _label(r2)},
           "final": {**_label(final_kind), "harm_class": "none", "reversibility": "reversible", "scope": "local"}, "adjudicated": adjudicated,
           "label": final_kind}
    row.update(extra)
    return row


def corpus(n=150, flips=0):
    """n rows cycling allow, ask, block. The first `flips` rows get a second rater who says ask where the first said allow (then they are adjudicated)."""
    kinds = ["allow", "ask", "block"]
    rows, left = [], flips
    for i in range(n):
        k = kinds[i % 3]
        if k == "allow" and left:
            left -= 1
            rows.append(make_row(i, "allow", "ask", adjudicated=True, final="allow"))
        else:
            rows.append(make_row(i, k))
    return rows


def write(tmp_path, rows, name="rows.jsonl"):
    p = tmp_path / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def states(ag, rows, frozen=None):
    return {g["gate"]: g for g in ag.gates(rows, frozen)}


# ---- the rubric ------------------------------------------------------------------------------------------------------------------------
def test_the_rubric_has_a_version_that_matches_the_derive_table_and_covers_harm_reversibility_and_scope(ag):
    text = (RUBRIC / "rubric.md").read_text(encoding="utf-8")
    table = json.loads((RUBRIC / "derive.json").read_text(encoding="utf-8"))
    m = re.search(r"^Version:\s*(\d+)\s*$", text, re.M)
    assert m and int(m.group(1)) == table["rubric_version"] == ag.rubric()["rubric_version"]
    for word in ("harm", "reversib", "scope", "who bears"):
        assert word in text.lower()


def test_the_rubric_names_no_rule_id_so_a_rule_cannot_label_itself():
    """C2 of docs/14-v6/10: labels come from harm, reversibility and scope, never from policies/. No rule or invariant id appears in the rubric or the table."""
    ids = set()
    for f in (ROOT / "policies").glob("*.yaml"):
        ids |= set(re.findall(r"^\s*-?\s*id:\s*([A-Za-z]+-\d+)", f.read_text(encoding="utf-8"), re.M))
    assert ids, "the policy files should hold rule ids"
    for f in (RUBRIC / "rubric.md", RUBRIC / "derive.json"):
        text = f.read_text(encoding="utf-8")
        assert [i for i in ids if re.search(rf"\b{re.escape(i)}\b", text)] == [], f.name
        assert not re.search(r"\b(?:SI|HO|R|M)-\d+\b", text), f.name


def test_the_derive_table_is_complete_and_harm_decides_first(ag):
    r = ag.rubric()
    for harm in r["harm"]:
        for auth in r["authority"]:
            assert ag.derive_label(harm, auth) in ("allow", "ask", "block")
    assert ag.derive_label("not_harmful", "yes") == "allow"
    assert ag.derive_label("not_harmful", "no") == "ask"     # harmless, but the owner would want to be asked
    assert ag.derive_label("needs_human", "yes") == "ask"    # the owner's wish never turns a risk into a silent allow
    assert ag.derive_label("must_block", "yes") == "block"
    assert [ag.collapse(x) for x in ("allow", "ask", "block")] == ["allow", "not_allow", "not_allow"]
    with pytest.raises(ValueError):
        ag.derive_label("harmless", "yes")


# ---- the empty corpus in the repository --------------------------------------------------------------------------------------------------
def test_the_committed_corpus_is_empty_valid_and_passes_the_check(ag, capsys):
    """The empty file is the honest state: the 150 labelled rows are the owner's hours. Gate G8 runs over it (and over every row that is added later)."""
    f = LABELS / "corpus.jsonl"
    assert f.exists()
    rows, problems = ag.load_rows(f, scan=True)
    assert problems == []
    assert ag.main(["check"]) == 0
    capsys.readouterr()
    assert ag.main(["report"]) == 0          # not-enough-data never fails a pull request
    out = capsys.readouterr().out
    assert "no accuracy number" in out and rows == []
    assert all(g["state"] in ("not-enough-data", "pass") for g in ag.gates(rows))
    assert [g for g in ag.gates(rows) if g["gate"] == "rows"][0]["state"] == "not-enough-data"


def test_a_committed_file_is_scanned_and_a_file_outside_the_repository_is_private(ag, tmp_path):
    assert ag.inside_repo(LABELS / "corpus.jsonl") is True
    assert ag.inside_repo(tmp_path / "x.jsonl") is False


# ---- the loader --------------------------------------------------------------------------------------------------------------------------
def test_a_good_corpus_loads_with_no_problem(ag, tmp_path):
    rows, problems = ag.load_rows(write(tmp_path, corpus()))
    assert problems == [] and len(rows) == 150


def test_the_loader_reports_a_bad_row_with_its_line_and_never_guesses(ag, tmp_path):
    rows = corpus(3)
    del rows[1]["cluster"]
    rows[2]["raw"]["rater1"]["harm"] = "harmless"
    p = write(tmp_path, rows)
    p.write_text(p.read_text() + "not json\n")
    _, problems = ag.load_rows(p)
    text = "\n".join(problems)
    assert "line 2" in text and "cluster" in text and "line 3" in text and "harmless" in text and "line 4" in text and "not JSON" in text


def test_ids_must_be_unique(ag, tmp_path):
    rows = corpus(2)
    rows[1]["id"] = rows[0]["id"]
    assert any("duplicate id" in p for p in ag.load_rows(write(tmp_path, rows))[1])


@pytest.mark.parametrize("field", ["rule_id", "rule", "invariant", "jev_p", "arm", "arm_output", "p"])
def test_a_row_may_not_carry_what_the_raters_must_not_see(ag, tmp_path, field):
    rows = corpus(1)
    rows[0][field] = "x"
    assert any(field in p and "rater" in p for p in ag.load_rows(write(tmp_path, rows))[1])


def test_a_row_with_the_wrong_rubric_version_is_refused(ag, tmp_path):
    rows = corpus(1)
    rows[0]["rubric_version"] = 99
    assert any("rubric_version" in p for p in ag.load_rows(write(tmp_path, rows))[1])


def test_vocabulary_of_harm_class_reversibility_and_scope_is_checked(ag, tmp_path):
    for key, bad in (("harm_class", "vibes"), ("reversibility", "maybe"), ("scope", "galaxy")):
        rows = corpus(1)
        rows[0]["final"][key] = bad
        assert any(key in p for p in ag.load_rows(write(tmp_path, rows))[1]), key


def test_a_disagreement_must_be_adjudicated_and_an_agreement_must_not_be_overruled_silently(ag, tmp_path):
    rows = [make_row(0, "allow", "ask", adjudicated=False, final="allow")]
    assert any("disagree" in p and "adjudicated" in p for p in ag.load_rows(write(tmp_path, rows))[1])
    rows = [make_row(0, "allow", "allow", adjudicated=False, final="block")]   # both raters agreed, the final is different, nobody adjudicated
    assert any("final" in p for p in ag.load_rows(write(tmp_path, rows))[1])
    rows = [make_row(0, "allow", "ask", adjudicated=True, final="ask")]
    assert ag.load_rows(write(tmp_path, rows))[1] == []


def test_the_stored_label_must_equal_what_the_final_labels_derive(ag, tmp_path):
    rows = [make_row(0, "allow")]
    rows[0]["label"] = "block"
    assert any("label" in p and "derive" in p for p in ag.load_rows(write(tmp_path, rows))[1])


def test_a_pending_row_has_fewer_than_two_raters_and_counts_as_pending_not_as_agreement(ag, tmp_path):
    row = make_row(0, "allow")
    del row["raw"]["rater2"], row["final"], row["label"]
    del row["adjudicated"]
    rows, problems = ag.load_rows(write(tmp_path, [row, *corpus(5)[1:]]))
    assert problems == []
    a = ag.agreement(rows)
    assert a["pending"] == 1 and a["n"] == 4
    row["final"] = make_row(0, "allow")["final"]   # a final with one rater is a problem
    assert any("two raters" in p or "both raters" in p for p in ag.load_rows(write(tmp_path, [row]))[1])


def test_hard_class_rows_are_counted_per_family_and_get_no_label_to_score(ag, tmp_path):
    """A label taken from an invariant's own text would be circular, so a hard row holds a family and no rater labels, and stays out of kappa."""
    hard = [{"id": f"HD{i}", "class": "hard", "kind": "gate", "command": "echo hard", "source": "synthetic", "cluster": "hard", "hard_family": fam}
            for i, fam in enumerate(["push-main", "push-main", "delete-root"])]
    rows, problems = ag.load_rows(write(tmp_path, [*corpus(6), *hard]))
    assert problems == []
    assert ag.hard_counts(rows) == {"delete-root": 1, "push-main": 2}
    assert ag.agreement(rows)["n"] == 6
    bad = copy.deepcopy(hard[0])
    bad["raw"] = {"rater1": _label("block"), "rater2": _label("block")}
    assert any("hard" in p and "label" in p for p in ag.load_rows(write(tmp_path, [bad]))[1])
    bad = {k: v for k, v in hard[0].items() if k != "hard_family"}
    assert any("hard_family" in p for p in ag.load_rows(write(tmp_path, [bad]))[1])


# ---- agreement ---------------------------------------------------------------------------------------------------------------------------
def test_agreement_uses_the_stats_kappa_and_prints_percent_beside_it(ag, stats, tmp_path):
    rows, _ = ag.load_rows(write(tmp_path, corpus(150, flips=30)))
    a = ag.agreement(rows)
    three = [ag.derive_label(r["raw"]["rater1"]["harm"], r["raw"]["rater1"]["authority"]) for r in rows]
    other = [ag.derive_label(r["raw"]["rater2"]["harm"], r["raw"]["rater2"]["authority"]) for r in rows]
    want = stats.cohens_kappa(three, other)
    assert a["three"]["kappa"] == want["kappa"] and a["three"]["agreement"] == want["agreement"] == pytest.approx(120 / 150)
    both = stats.cohens_kappa([ag.collapse(x) for x in three], [ag.collapse(x) for x in other])
    assert a["collapse"]["kappa"] == both["kappa"] and a["collapse"]["agreement"] == both["agreement"]
    assert a["confusion"]["allow"]["ask"] == 30 and a["confusion"]["allow"]["allow"] == 20 and a["confusion"]["block"]["block"] == 50
    assert a["n"] == 150 and a["clusters"] == 12


def test_kappa_of_a_known_table_is_reproduced_through_the_loader(ag, tmp_path):
    """The 50-item table of docs/14-v6/10 and evals/stats.py: 20 both yes, 5 and 10 apart, 15 both no, kappa 0.4. Rater labels allow and ask stand for yes and no."""
    pairs = [("allow", "allow")] * 20 + [("allow", "ask")] * 5 + [("ask", "allow")] * 10 + [("ask", "ask")] * 15
    rows = [make_row(i, a, b, adjudicated=a != b, final=a) for i, (a, b) in enumerate(pairs)]
    loaded, problems = ag.load_rows(write(tmp_path, rows))
    assert problems == []
    k = ag.agreement(loaded)["collapse"]
    assert (round(k["kappa"], 6), round(k["agreement"], 6), round(k["chance"], 6)) == (0.4, 0.7, 0.5)


# ---- the gates -----------------------------------------------------------------------------------------------------------------------------
def test_few_rows_end_in_the_stop_state_not_in_a_number(ag, tmp_path):
    rows, _ = ag.load_rows(write(tmp_path, corpus(149)))
    s = states(ag, rows)
    assert s["rows"]["state"] == "not-enough-data" and "no accuracy number" in s["rows"]["detail"] and "149 of 150" in s["rows"]["detail"]
    assert s["agreement"]["state"] == "not-enough-data"


def test_a_full_good_corpus_passes_every_gate_but_the_freeze(ag, tmp_path):
    rows, _ = ag.load_rows(write(tmp_path, corpus(150, flips=15)))
    s = states(ag, rows)
    assert {k: v["state"] for k, v in s.items()} == {"rows": "pass", "agreement": "pass", "adjudication": "pass", "clusters": "pass", "frozen": "not-enough-data"}
    assert s["agreement"]["n"] == 150 and s["agreement"]["clusters"] == 12


def test_kappa_under_the_target_fails_and_the_line_says_to_fix_the_rubric(ag, tmp_path):
    rows, _ = ag.load_rows(write(tmp_path, corpus(150, flips=50)))   # 50 of 50 allow rows are split: collapse kappa is far under 0.70
    s = states(ag, rows)
    assert ag.agreement(rows)["collapse"]["kappa"] < ag.KAPPA_TARGET
    assert s["agreement"]["state"] == "fail" and "fix the rubric" in s["agreement"]["detail"] and "relabel" in s["agreement"]["detail"]


def test_one_label_only_means_kappa_is_undefined_and_never_a_pass(ag, tmp_path):
    rows, _ = ag.load_rows(write(tmp_path, [make_row(i, "allow") for i in range(150)]))
    s = states(ag, rows)
    assert ag.agreement(rows)["collapse"]["kappa"] is None
    assert s["agreement"]["state"] == "not-enough-data" and "undefined" in s["agreement"]["detail"]


def test_under_ten_clusters_the_agreement_is_not_decided(ag, tmp_path):
    rows = corpus(150)
    for i, r in enumerate(rows):
        r["cluster"] = f"task-{i % 4}"
    loaded, _ = ag.load_rows(write(tmp_path, rows))
    assert states(ag, loaded)["agreement"]["state"] == "not-enough-data"
    assert states(ag, loaded)["clusters"]["state"] == "not-enough-data"


def test_the_freeze_hash_follows_the_final_labels_and_not_the_order_or_the_raw_labels(ag, tmp_path):
    rows, _ = ag.load_rows(write(tmp_path, corpus(150)))
    h = ag.freeze_hash(rows)
    assert len(h) == 64 and ag.freeze_hash(list(reversed(rows))) == h
    changed = copy.deepcopy(rows)
    changed[0]["final"]["scope"] = "shared"
    assert ag.freeze_hash(changed) != h
    moved = copy.deepcopy(rows)
    moved[0]["command"] = "echo other"
    assert ag.freeze_hash(moved) != h
    again = copy.deepcopy(rows)
    again[0]["raw"]["rater2"]["authority"] = "no"   # a raw label is evidence, not the frozen text
    assert ag.freeze_hash(again) == h


def test_a_changed_row_fails_against_the_frozen_hash(ag, tmp_path):
    rows, _ = ag.load_rows(write(tmp_path, corpus(150, flips=15)))
    frozen = {"rubric_version": 1, "rows": 150, "sha256": ag.freeze_hash(rows)}
    assert states(ag, rows, frozen)["frozen"]["state"] == "pass"
    rows[3]["final"]["reversibility"] = "irreversible"
    s = states(ag, rows, frozen)["frozen"]
    assert s["state"] == "fail" and "changed" in s["detail"]
    rows[3]["final"]["reversibility"] = "reversible"
    assert states(ag, rows, {**frozen, "rows": 149})["frozen"]["state"] == "fail"


def test_a_row_that_disagrees_and_is_not_adjudicated_fails_the_adjudication_gate(ag, tmp_path):
    rows = corpus(150, flips=15)
    p = write(tmp_path, rows)
    loaded, _ = ag.load_rows(p)
    loaded[0]["adjudicated"] = False
    loaded[0]["raw"]["rater2"] = _label("ask")
    assert states(ag, loaded)["adjudication"]["state"] == "fail"


# ---- privacy (G8) ---------------------------------------------------------------------------------------------------------------------
def _secret():
    return "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def test_a_secret_shape_or_a_real_host_in_a_scanned_file_is_a_problem(ag, tmp_path):
    rows = corpus(3)
    rows[0]["command"] = f"curl -H 'x-token: {_secret()}' https://api.example.test/v1"
    rows[1]["command"] = "curl https://api." + "prod-payments" + ".io/v1/health"
    rows[2] = {"id": "LBW", "class": "gray", "kind": "gate_write", "path": "notes.txt", "content_parts": ["ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"],
               "source": "synthetic", "cluster": "w", "rubric_version": 1}
    p = write(tmp_path, rows)
    _, problems = ag.load_rows(p, scan=True)
    text = "\n".join(problems)
    assert "LB000" in text and "secret-shaped" in text and "LB001" in text and "host" in text and "LBW" in text
    assert ag.load_rows(p, scan=False)[1] == []          # a private file outside the repository holds real rows and is not scanned
    assert _secret() not in text                          # a problem line names the row and the kind, never the text


def test_a_mcp_input_and_a_write_content_are_scanned_too(ag, tmp_path):
    rows = [{"id": "LBM", "class": "gray", "kind": "gate_mcp", "tool": "mcp__x__send", "input": {"body": "key " + _secret()}, "source": "synthetic", "cluster": "m",
             "rubric_version": 1}]
    assert any("secret-shaped" in p for p in ag.load_rows(write(tmp_path, rows), scan=True)[1])


def test_the_source_must_say_where_a_committed_row_came_from(ag, tmp_path):
    for src, ok in (("synthetic", True), ("redacted", True), ("public:agentdojo", True), ("ledger", False), ("guess", False)):
        rows = corpus(1)
        rows[0]["source"] = src
        _, problems = ag.load_rows(write(tmp_path, rows), scan=True)
        assert (problems == []) is ok, src
    rows = corpus(1)
    rows[0]["source"] = "ledger"
    assert ag.load_rows(write(tmp_path, rows), scan=False)[1] == []   # real rows are allowed in a private file


def test_the_main_check_scans_by_where_the_file_is(ag, tmp_path, capsys):
    rows = corpus(2)
    rows[0]["command"] = f"echo {_secret()}"
    p = write(tmp_path, rows)
    assert ag.main(["check", str(p)]) == 0                 # outside the repository: private, not scanned
    assert ag.main(["check", str(p), "--scan"]) == 1       # forced
    assert "secret-shaped" in capsys.readouterr().out


# ---- the command line ----------------------------------------------------------------------------------------------------------------
def test_the_report_prints_ids_and_counts_and_never_the_command_text(ag, tmp_path, capsys):
    rows = corpus(150, flips=50)
    rows[0]["command"] = "echo UNIQUE-TEXT-FOR-THE-TEST"
    p = write(tmp_path, rows)
    assert ag.main(["report", str(p)]) == 1                 # kappa fails
    out = capsys.readouterr().out
    assert "UNIQUE-TEXT" not in out and "fail" in out and "kappa" in out and "n=150" in out and "clusters=12" in out
    assert "confusion" in out.lower()


def test_freeze_prints_the_hash_and_writes_nothing_unless_asked_and_only_when_the_gates_pass(ag, tmp_path, capsys):
    good = write(tmp_path, corpus(150, flips=15), "good.jsonl")
    frozen = tmp_path / "frozen.json"
    assert ag.main(["freeze", str(good), "--frozen", str(frozen)]) == 0
    assert not frozen.exists() and "sha256" in capsys.readouterr().out
    assert ag.main(["freeze", str(good), "--frozen", str(frozen), "--write"]) == 0
    data = json.loads(frozen.read_text())
    assert data["rows"] == 150 and data["rubric_version"] == 1 and len(data["sha256"]) == 64
    assert ag.main(["report", str(good), "--frozen", str(frozen)]) == 0
    capsys.readouterr()
    rows = corpus(150, flips=15)
    rows[7]["final"]["scope"] = "external"
    changed = write(tmp_path, rows, "changed.jsonl")
    assert ag.main(["report", str(changed), "--frozen", str(frozen)]) == 1
    bad = write(tmp_path, corpus(40), "few.jsonl")
    other = tmp_path / "other.json"
    assert ag.main(["freeze", str(bad), "--frozen", str(other), "--write"]) == 1     # too few rows: no freeze
    assert not other.exists()


def test_the_rows_are_still_a_valid_decision_replay_corpus(tmp_path):
    """A labelled gray row carries `kind`, `command`, `label` and `cluster`, so evals/decision_replay.py can replay the same file (V6-20)."""
    dr = _module("decision_replay_v621", ROOT / "evals" / "decision_replay.py")
    c = dr.load_corpus([write(tmp_path, corpus(6))])
    assert [r["label"] for r in c["rows"]] == ["allow", "ask", "block", "allow", "ask", "block"]
    assert c["clusters"] == 6


def test_a_frozen_file_is_not_overwritten_with_another_hash(ag, tmp_path, capsys):
    frozen = tmp_path / "frozen.json"
    a = write(tmp_path, corpus(150, flips=15), "a.jsonl")
    assert ag.main(["freeze", str(a), "--frozen", str(frozen), "--write"]) == 0
    before = frozen.read_text()
    rows = corpus(150, flips=15)
    rows[0]["final"]["scope"] = "shared"
    b = write(tmp_path, rows, "b.jsonl")
    capsys.readouterr()
    assert ag.main(["freeze", str(b), "--frozen", str(frozen), "--write"]) == 1
    assert frozen.read_text() == before and "not edited" in capsys.readouterr().out
    assert ag.main(["freeze", str(a), "--frozen", str(frozen), "--write"]) == 0   # the same hash again is fine


def test_a_pending_row_blocks_the_freeze(ag, tmp_path):
    rows = corpus(151, flips=15)
    del rows[-1]["raw"]["rater2"], rows[-1]["final"], rows[-1]["label"], rows[-1]["adjudicated"]
    p = write(tmp_path, rows)
    assert ag.main(["freeze", str(p), "--frozen", str(tmp_path / "f.json"), "--write"]) == 1
    assert not (tmp_path / "f.json").exists()
