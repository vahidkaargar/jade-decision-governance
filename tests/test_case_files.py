"""The regression and adversarial case files replay clean through the real code paths (evals/run_cases.py)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _runner():
    spec = importlib.util.spec_from_file_location("run_cases", ROOT / "evals" / "run_cases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_regression_and_adversarial_cases_all_pass(capsys):
    """The default run covers the regression, adversarial, routing, judgment and calibration sets (W-49) and the known-gap list (V6-03)."""
    assert _runner().main([]) == 0
    total = sum(1 for f in ("regression", "adversarial", "routing", "judgment", "calibration", "known_gaps") for line in (ROOT / "evals" / f / "cases.jsonl").read_text().splitlines() if line.strip())
    assert f"{total}/{total} cases pass" in capsys.readouterr().out


def test_runner_detects_a_wrong_expectation(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id":"N1","class":"wrong","kind":"gate","command":"git push origin v1-feat-x","expect":"allow"}\n')
    assert _runner().main([str(bad)]) == 1


def _gap_row(**extra):
    row = {"id": "KGX1", "class": "variable_as_git", "kind": "gate", "command": "g=git; $g push origin main", "expect": "block",
           "known_gap": "a variable as the command name hides the push"}
    return json.dumps({**row, **extra}) + "\n"


def test_a_known_gap_row_without_a_work_item_fails(tmp_path, capsys):
    """V6-03: a known gap is a visible ceiling with an owner, never an anonymous pass."""
    f = tmp_path / "gap.jsonl"
    f.write_text(_gap_row(gap_date="2026-11-16"))
    assert _runner().main([str(f)]) == 1
    assert "work_item" in capsys.readouterr().out


def test_a_known_gap_row_without_a_date_fails(tmp_path, capsys):
    f = tmp_path / "gap.jsonl"
    f.write_text(_gap_row(work_item="V6-08"))
    assert _runner().main([str(f)]) == 1
    assert "gap_date" in capsys.readouterr().out


def test_a_known_gap_row_with_a_malformed_work_item_fails(tmp_path):
    f = tmp_path / "gap.jsonl"
    f.write_text(_gap_row(work_item="later", gap_date="2026-11-16"))
    assert _runner().main([str(f)]) == 1


def test_a_known_gap_row_with_a_work_item_and_a_date_is_a_counted_gap(tmp_path, capsys):
    f = tmp_path / "gap.jsonl"
    f.write_text(_gap_row(work_item="V6-08", gap_date="2026-11-16"))
    assert _runner().main([str(f)]) == 0
    assert "(1 known gaps)" in capsys.readouterr().out


def test_every_shipped_known_gap_row_has_a_work_item_and_a_date():
    import re

    for f in sorted((ROOT / "evals").glob("*/cases.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            c = json.loads(line) if line.strip() else {}
            if c.get("known_gap"):
                assert re.fullmatch(r"(W|V6)-\d+[a-z]?", str(c.get("work_item", ""))), (f.name, c["id"])
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(c.get("gap_date", ""))), (f.name, c["id"])


def test_the_known_gap_list_names_every_open_vector_of_the_v6_03_card():
    """A3 8b vectors still open on main, the agent-config and shell-profile writes, child-agent launch, the N21/N25 wrappers and the
    NULL_REDIRECT quadratic case (V6-03). V6-08 and V6-18 remove rows as they close them."""
    rows = [json.loads(line) for line in (ROOT / "evals" / "known_gaps" / "cases.jsonl").read_text().splitlines() if line.strip()]
    assert all(r.get("known_gap") for r in rows)
    classes = {r["class"] for r in rows}
    assert {"git_dash_c_escaped_space_push", "git_send_pack_push", "variable_as_git_push", "gh_api_pull_merge", "gh_api_graphql_merge",
            "shell_profile_write", "authorized_keys_write", "agent_config_write", "child_agent_launch", "null_redirect_quadratic_time",
            "wrapper_hides_push", "hook_host_kill"} <= classes


def test_a_classify_time_gap_is_a_gap_while_the_classifier_scales_badly(tmp_path, capsys):
    """The timing kind: a doubling of the input must not more than about double the classify time, and the larger input must fit the budget."""
    row = {"id": "KGX2", "class": "null_redirect_quadratic_time", "kind": "classify_time", "command_prefix": "echo ", "digit_run": 10000,
           "command_suffix": "x", "budget_s": 1.0, "max_ratio": 2.5}
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps(row) + "\n")
    rc = _runner().main([str(f)])
    out = capsys.readouterr().out
    assert rc == 1 and "ratio" in out   # quadratic on main today (WT W-104); a fix flips this and the known_gap row in evals/known_gaps


def test_language_slices():
    from jade import evals

    assert evals.language("The migration finished and all tests passed") == "en"
    assert evals.language("مهاجرت دیتابیس با موفقیت انجام شد و همه تست ها پاس شدند") == "fa"
    assert evals.language("the migration finished و همه تست ها pass شدند without errors") == "mixed"
    assert evals.language("ok") == "unknown" and evals.language(None) == "unknown"


def test_eval_report_slices_by_language():
    from jade import adapter, ask, evals, ledger

    db = ledger.open()
    for i, claim in enumerate(["the database migration finished without any errors", "مهاجرت دیتابیس بدون هیچ خطایی با موفقیت تمام شد"] * 3):
        prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
        r = ask.run(ask.verify_specs(companion=False), {"claim": claim, "evidence": f"raw {i}"}, provider=prov, session=f"s{i}", db=db)
        evals.set_label(db, r.primary.call_id, 1, "adjudicated")
    rep = evals.report(db, include_protected=True)
    assert {"en", "fa"} <= set(rep["by_language"])


def test_ledger_migrates_an_older_database_in_place(tmp_path):
    import sqlite3

    from jade import ledger

    old = tmp_path / "jade.sqlite"
    con = sqlite3.connect(old)
    assert " language TEXT," in ledger.SCHEMA
    con.executescript(ledger.SCHEMA.replace(" language TEXT,", ""))  # the schema as shipped before the column existed
    con.execute("INSERT INTO jev_calls(call_id, question_id, state_hash, state, ts) VALUES('call_old000000001','q.x',?, 'DONE','2026-11-09T00:00:00Z')", ("a" * 64,))
    con.commit()
    assert "language" not in {r[1] for r in con.execute("PRAGMA table_info(jev_calls)")}
    con.close()
    db = ledger.open(old)
    assert db.one("SELECT language FROM jev_calls WHERE call_id='call_old000000001'").language is None
    db.begin_call("q.x", "b" * 64, "s", language="fa")
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls").n == 2 and db.one("SELECT language FROM jev_calls WHERE language='fa'").language == "fa"
    ledger.open(old).close()  # reopening an already migrated database is a no-op
