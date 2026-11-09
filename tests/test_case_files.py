"""The regression and adversarial case files replay clean through the real code paths (evals/run_cases.py)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _runner():
    spec = importlib.util.spec_from_file_location("run_cases", ROOT / "evals" / "run_cases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_regression_and_adversarial_cases_all_pass(capsys):
    assert _runner().main([]) == 0
    assert "54/54 cases pass" in capsys.readouterr().out


def test_runner_detects_a_wrong_expectation(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id":"N1","class":"wrong","kind":"gate","command":"git push origin feat/x","expect":"allow"}\n')
    assert _runner().main([str(bad)]) == 1


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
