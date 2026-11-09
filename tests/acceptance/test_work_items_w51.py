"""W-51: `jade work add`, de-duplication by normalised title and target, announcing items whose dependencies closed, and QUESTION_DRIFT."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from jade import cli, hooks, ledger, ops, paths, questions, work

TODAY = date(2026, 11, 9)
ROOT = Path(__file__).resolve().parents[2]


def item(wid="W-90", *, priority="P2", status="open", updated="2026-11-09", deps=(), title="a test item", **kw):
    return {"schema_version": "1.0.0", "id": wid, "parent": None, "title": title, "type": "followup", "priority": priority, "status": status,
            "owner": "agent", "dependencies": list(deps), "impact": "none", "rationale": "a test", "evidence_refs": [], "next_action": "do it",
            "retry_or_revisit_trigger": "on_session_start", "created_at": updated, "updated_at": updated,
            "history": [{"at": updated, "change": "created"}], "phase": 2, **kw}


def write(path, items):
    path.write_text("".join(json.dumps(w) + "\n" for w in items))
    return path


ADD = ["--impact", "i", "--rationale", "r", "--next", "n", "--trigger", "on_session_start"]


# ---- normalised title and target -------------------------------------------------------------------------------------------
def test_normalised_title_ignores_case_punctuation_and_spacing():
    assert work.normalise_title("  Fix  the  Gate! ") == work.normalise_title("fix the gate")
    assert work.normalise_title("MODEL_DRIFT: x/y") == work.normalise_title("model drift x y")
    assert work.normalise_title("Fix the gate") != work.normalise_title("Fix the hook")


# ---- add ---------------------------------------------------------------------------------------------------------------------
def test_add_assigns_the_next_id_validates_and_records_history():
    items = [item("W-05"), item("W-12", status="done")]
    w = work.add(items, title="A brand new thing", impact="i", rationale="r", next_action="n", trigger="on_session_start", today=TODAY)
    assert w["id"] == "W-13" and items[-1] is w and w["status"] == "open" and w["priority"] == "P2" and w["owner"] == "agent" and w["type"] == "followup"
    assert w["created_at"] == w["updated_at"] == "2026-11-09" and w["history"] == [{"at": "2026-11-09", "change": "created"}] and w["dependencies"] == []


def test_add_ids_grow_past_99_and_stop_at_the_schema_limit():
    items = [item("W-99")]
    assert work.add(items, title="t1", impact="i", rationale="r", next_action="n", trigger="t", today=TODAY)["id"] == "W-100"
    with pytest.raises(ValueError, match="no free id"):
        work.add([item("W-9999")], title="t2", impact="i", rationale="r", next_action="n", trigger="t", today=TODAY)


def test_add_refuses_a_duplicate_normalised_title_with_the_same_target_and_names_the_existing_item():
    items = [item("W-01", title="Fix the gate", evidence_refs=["jade/hooks.py"])]
    with pytest.raises(work.DuplicateItem) as e:
        work.add(items, title="  FIX the gate! ", evidence_refs=["jade/hooks.py"], impact="i", rationale="r", next_action="n", trigger="t", today=TODAY)
    assert e.value.existing == "W-01" and "W-01" in str(e.value) and len(items) == 1


def test_add_allows_the_same_title_for_another_target_or_after_the_first_item_closed():
    items = [item("W-01", title="Fix the gate", evidence_refs=["jade/hooks.py"]), item("W-02", title="Rotate keys", status="done")]
    assert work.add(items, title="Fix the gate", evidence_refs=["jade/permits.py"], impact="i", rationale="r", next_action="n", trigger="t", today=TODAY)["id"] == "W-03"
    assert work.add(items, title="Rotate keys", impact="i", rationale="r", next_action="n", trigger="t", today=TODAY)["id"] == "W-04"


def test_add_rejects_an_unknown_dependency_or_parent_and_an_invalid_item_and_changes_nothing():
    items = [item("W-01")]
    for kw, why in ((dict(dependencies=["W-77"]), "W-77"), (dict(parent="W-78"), "W-78"), (dict(priority="P9"), "priority"), (dict(title="  "), "title")):
        with pytest.raises(ValueError, match=why):
            work.add(items, **{"title": "ok", "impact": "i", "rationale": "r", "next_action": "n", "trigger": "t", "today": TODAY, **kw})
    assert len(items) == 1


# ---- ready (dependency-closed) ------------------------------------------------------------------------------------------------
def test_ready_lists_open_items_whose_dependencies_are_all_done():
    items = [item("W-01", status="done"), item("W-02", status="done"), item("W-03", deps=("W-01",)), item("W-04", deps=("W-01", "W-09")), item("W-09"),
             item("W-05", status="in_progress", deps=("W-01",)), item("W-06", deps=()), item("W-07", status="done", deps=("W-01",)), item("W-08", deps=("W-02",))]
    assert [w["id"] for w in work.ready(items)] == ["W-03", "W-08"]
    assert [w["id"] for w in work.ready(items, closed="W-02")] == ["W-08"]       # only items that the item just closed was holding up


def test_marking_an_item_done_announces_what_is_now_ready(tmp_path, capsys):
    src, out = write(tmp_path / "w.jsonl", [item("W-01"), item("W-02", deps=("W-01",), title="second"), item("W-03", status="blocked", deps=("W-01",), title="third")]), tmp_path / "B.md"
    assert cli.main(["work", "set", "W-01", "--status", "done", "--file", str(src), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "ready: W-02, W-03" in text
    assert cli.main(["work", "set", "W-02", "--note", "n", "--file", str(src), "--out", str(out)]) == 0
    assert "ready:" not in capsys.readouterr().out          # nothing was closed this time


# ---- the add command ---------------------------------------------------------------------------------------------------------
def run_add(src, out, *extra):
    return cli.main(["work", "add", "--file", str(src), "--out", str(out), *extra])


def test_work_add_writes_renders_and_mirrors_into_the_ledger(tmp_path, capsys):
    src, out = write(tmp_path / "w.jsonl", [item("W-01")]), tmp_path / "BACKLOG.md"
    shutil.copy(ROOT / "BACKLOG.md", out)
    head = out.read_text().split("\n## Items\n")[0]
    assert run_add(src, out, "--title", "Add the thing", "--priority", "P1", "--owner", "human", "--type", "risk", "--dep", "W-01", "--evidence", "docs/07.md", *ADD) == 0
    assert "W-02" in capsys.readouterr().out
    w = work.load(src)[-1]
    assert w["id"] == "W-02" and w["title"] == "Add the thing" and w["priority"] == "P1" and w["owner"] == "human" and w["type"] == "risk"
    assert w["dependencies"] == ["W-01"] and w["evidence_refs"] == ["docs/07.md"] and w["next_action"] == "n"
    assert out.read_text().startswith(head) and "| W-02 | P1 | open |" in out.read_text()
    assert ledger.open().one("SELECT status FROM work_items WHERE id='W-02'").status == "open"


def test_work_add_refuses_a_duplicate_and_leaves_the_file_alone(tmp_path, capsys):
    src, out = write(tmp_path / "w.jsonl", [item("W-01", title="Add the thing")]), tmp_path / "B.md"
    before = src.read_text()
    assert run_add(src, out, "--title", "add THE thing.", *ADD) == 1
    assert "W-01" in capsys.readouterr().err and src.read_text() == before and not out.exists()


@pytest.mark.parametrize("drop", ["--title", "--impact", "--rationale", "--next", "--trigger"])
def test_work_add_names_a_missing_field(tmp_path, capsys, drop):
    src, out = write(tmp_path / "w.jsonl", [item("W-01")]), tmp_path / "B.md"
    args = {"--title": "t", "--impact": "i", "--rationale": "r", "--next": "n", "--trigger": "x"}
    del args[drop]
    assert run_add(src, out, *[a for kv in args.items() for a in kv]) == 1
    assert drop in capsys.readouterr().err and len(work.load(src)) == 1


def test_work_add_rejects_an_unknown_dependency_without_a_traceback(tmp_path, capsys):
    src, out = write(tmp_path / "w.jsonl", [item("W-01")]), tmp_path / "B.md"
    assert run_add(src, out, "--title", "t", "--dep", "W-55", *ADD) == 1
    assert "W-55" in capsys.readouterr().err and len(work.load(src)) == 1


def test_work_add_is_not_a_human_only_command():
    """It edits a repository file the agent may edit anyway; SI-09 (human-only commands) is unchanged."""
    from jade import policy

    for cmd in ("jade work add --title t --next n", "uv run jade work add --title t --dep W-01"):
        a = policy.assess("Bash", {"command": cmd}, "/nonexistent-w51")
        assert a.level == "low" and not a.blocked, (cmd, a.rule_id)


# ---- sync de-duplicates ------------------------------------------------------------------------------------------------------
def test_dedupe_keeps_the_first_of_each_active_duplicate_group():
    items = [item("W-01", title="Same thing"), item("W-02", title="same  THING!"), item("W-03", title="same thing", status="done"), item("W-04", title="same thing", evidence_refs=["x"]),
             item("W-05", title="Other")]
    keep, skipped = work.dedupe(items)
    assert [w["id"] for w in keep] == ["W-01", "W-03", "W-04", "W-05"] and skipped == {"W-02": "W-01"}


def test_work_sync_does_not_mirror_a_duplicate_and_says_so(tmp_path, capsys):
    src, out = write(tmp_path / "w.jsonl", [item("W-01", title="Same thing"), item("W-02", title="same thing")]), tmp_path / "B.md"
    db = ledger.open()
    db.con.execute("INSERT INTO work_items(id, status, doc_json, updated_at) VALUES('W-02','open','{}','2026-11-08')")   # an earlier sync mirrored it
    db.close()
    assert cli.main(["work", "sync", "--file", str(src), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "W-02" in text and "duplicate of W-01" in text
    db = ledger.open()
    assert db.one("SELECT COUNT(*) AS n FROM work_items WHERE id='W-01'").n == 1 and db.one("SELECT COUNT(*) AS n FROM work_items WHERE id='W-02'").n == 0
    assert len(work.load(src)) == 2         # the source file is never rewritten by the de-duplication


# ---- SessionStart announces dependency-closed items, once ---------------------------------------------------------------------
def seed_ledger(items):
    db = ledger.open()
    with db.tx() as con:
        for w in items:
            con.execute("INSERT OR REPLACE INTO work_items(id, status, doc_json, updated_at) VALUES(?,?,?,?)", (w["id"], w["status"], json.dumps(w), w["updated_at"]))
    db.close()


def test_session_start_announces_an_item_whose_dependencies_closed_once():
    seed_ledger([item("W-01", status="done"), item("W-02", deps=("W-01",), title="now unblocked", retry_or_revisit_trigger="manual", next_action="go on"),
                 item("W-03", deps=("W-09",), title="still held", retry_or_revisit_trigger="manual"), item("W-09", retry_or_revisit_trigger="manual")])
    out = hooks.session_start({"session_id": "sess-1", "cwd": "/tmp/x"}).stdout
    assert "W-02" in out and "dependencies" in out and "now unblocked" in out and "W-03" not in out
    assert "W-02" not in hooks.session_start({"session_id": "sess-2", "cwd": "/tmp/x"}).stdout     # told once, not every session


def test_session_start_does_not_announce_an_item_twice_when_its_trigger_already_does():
    seed_ledger([item("W-01", status="done"), item("W-02", deps=("W-01",), title="both")])
    assert hooks.session_start({"session_id": "sess-1", "cwd": "/tmp/x"}).stdout.count("W-02") == 1


# ---- QUESTION_DRIFT ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    shutil.copytree(paths.registry_dir(), reg)
    monkeypatch.setenv("JADE_REGISTRY_DIR", str(reg))
    questions.reset_cache()
    yield reg
    questions.reset_cache()


def add_version(reg, qid="q.verify_claim.supported_by_evidence", version=2):
    e = json.loads(next(p for p in reg.glob("*.json") if json.loads(p.read_text())["question_id"] == qid).read_text())
    e["version"] = version
    (reg / f"{qid}.v{version}.json").write_text(json.dumps(e))
    questions.reset_cache()


def drift_rows():
    db = ledger.open()
    return db.one("SELECT COUNT(*) AS n FROM events WHERE kind='QUESTION_DRIFT'").n, db.one("SELECT COUNT(*) AS n FROM work_items WHERE doc_json LIKE '%QUESTION_DRIFT%'").n


def test_the_first_look_at_the_registry_is_a_baseline_and_opens_nothing(registry):
    assert ops.check_question_drift() == [] and drift_rows() == (0, 0)


def test_a_new_question_version_opens_one_informational_work_item_and_one_event(registry):
    ops.check_question_drift()
    add_version(registry)
    found = ops.check_question_drift()
    assert [(f.question_id, f.version) for f in found] == [("q.verify_claim.supported_by_evidence", 2)] and drift_rows() == (1, 1)
    row = ledger.open().one("SELECT doc_json FROM work_items WHERE doc_json LIKE '%QUESTION_DRIFT%'")
    w = json.loads(row.doc_json)
    assert w["title"].startswith("QUESTION_DRIFT:") and "supported_by_evidence" in w["title"] and w["status"] == "open" and w["owner"] == "human" and w["priority"] == "P3"
    assert ops.check_question_drift() == [] and drift_rows() == (1, 1)          # once per new version, however often it is looked at


def test_a_second_new_version_is_its_own_item(registry):
    ops.check_question_drift()
    add_version(registry, version=2)
    add_version(registry, qid="q.risk.irreversible", version=3)
    assert len(ops.check_question_drift()) == 2 and drift_rows() == (2, 2)


def test_session_start_runs_the_check_and_shows_the_item(registry):
    hooks.session_start({"session_id": "sess-1", "cwd": "/tmp/x"})
    add_version(registry)
    out = hooks.session_start({"session_id": "sess-2", "cwd": "/tmp/x"}).stdout
    assert "QUESTION_DRIFT" in out and drift_rows() == (1, 1)


def test_a_registry_that_cannot_be_read_never_breaks_session_start(registry, monkeypatch):
    monkeypatch.setattr(questions, "entries", lambda: (_ for _ in ()).throw(OSError("boom")))
    assert hooks.session_start({"session_id": "sess-1", "cwd": "/tmp/x"}).exit_code == 0
