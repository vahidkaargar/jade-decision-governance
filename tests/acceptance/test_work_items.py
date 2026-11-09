"""jade/work.py (ADR-09): the JSONL is the source, BACKLOG.md is rendered from it and keeps its hand-written head (review F43)."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from jade import cli, ledger, work

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 9)


def item(wid="W-90", *, priority="P2", status="open", updated="2026-11-09", deps=(), title="a test item", **kw):
    return {"schema_version": "1.0.0", "id": wid, "parent": None, "title": title, "type": "followup", "priority": priority, "status": status,
            "owner": "agent", "dependencies": list(deps), "impact": "none", "rationale": "a test", "evidence_refs": [], "next_action": "do it",
            "retry_or_revisit_trigger": "on_session_start", "created_at": updated, "updated_at": updated,
            "history": [{"at": updated, "change": "created"}], "phase": 2, **kw}


def write(path, items):
    path.write_text("".join(json.dumps(w) + "\n" for w in items))
    return path


# ---- render keeps the hand-written head -----------------------------------------------------------------------------------
def test_render_round_trips_the_real_backlog_and_is_idempotent(tmp_path):
    backlog = tmp_path / "BACKLOG.md"
    shutil.copy(ROOT / "BACKLOG.md", backlog)
    original_head = (ROOT / "BACKLOG.md").read_text().split("\n## Items\n")[0]
    first = work.render(ROOT / "work-items.jsonl", backlog, today=TODAY)
    assert first.startswith(original_head)
    assert "## Decisions taken" in first and "| Q3 |" in first and "| Q7 |" in first           # the maintainers' decision record survives
    assert "everything below `## Items`" in first                                                # ... and so does the intro that quotes the heading
    second = work.render(ROOT / "work-items.jsonl", backlog, today=TODAY)
    assert second == first and backlog.read_text() == first
    assert first.count("\n## Items\n") == 1


def test_the_committed_backlog_lists_every_work_item_as_the_renderer_writes_it():
    items = work.load(ROOT / "work-items.jsonl")
    assert work.table(items) in (ROOT / "BACKLOG.md").read_text(), "BACKLOG.md is stale: run `uv run jade work render`"


@pytest.mark.parametrize("head", [
    "# Backlog\n\nSee the `## Items` table below; edit with `jade work set`.\n\n## Notes\n\nhand-written\n",
    "# Backlog\n\n## Items are generated\n\nnot the heading\n\n## Notes\n\nkept\n",
    "# Backlog\n\n    ## Items\n\nindented, so not the heading\n",
])
def test_a_quotation_of_the_heading_does_not_cut_the_head(tmp_path, head):
    backlog = tmp_path / "B.md"
    backlog.write_text(head + "\n## Items\n\nold table\n\nold footer\n")
    out = work.render(write(tmp_path / "w.jsonl", [item()]), backlog, today=TODAY)
    assert out.startswith(head.rstrip("\n")) and "old table" not in out and "| W-90 |" in out and out.count("\n## Items\n") == 1


def test_a_heading_on_the_very_first_line_is_found(tmp_path):
    backlog = tmp_path / "B.md"
    backlog.write_text("## Items\n\nold table\n")
    out = work.render(write(tmp_path / "w.jsonl", [item()]), backlog, today=TODAY)
    assert out.count("## Items") == 1 and "old table" not in out and "| W-90 |" in out


def test_a_backlog_with_no_heading_gets_one_and_a_missing_file_is_created(tmp_path):
    backlog = tmp_path / "B.md"
    backlog.write_text("# Backlog\n\nhand-written only\n")
    out = work.render(write(tmp_path / "w.jsonl", [item()]), backlog, today=TODAY)
    assert out.startswith("# Backlog\n\nhand-written only") and "\n## Items\n" in out
    fresh = tmp_path / "new" / "B.md"
    fresh.parent.mkdir()
    assert "| W-90 |" in work.render(tmp_path / "w.jsonl", fresh, today=TODAY) and fresh.exists()


def test_table_orders_by_status_then_priority_and_escapes_cells():
    items = [item("W-91", status="done"), item("W-92", priority="P0", title="has | a pipe\nand a newline"), item("W-93", priority="P1"),
             item("W-94", status="waiting_human", priority="P0"), item("W-95", status="in_progress", priority="P3"), item("W-96", deps=("W-92", "W-93"))]
    rows = [r.split(" | ")[0].strip("| ") for r in work.table(items).splitlines()[2:]]
    assert rows == ["W-95", "W-92", "W-93", "W-96", "W-94", "W-91"]
    assert "has \\| a pipe and a newline" in work.table(items) and "W-92, W-93" in work.table(items)


# ---- load and save validate ------------------------------------------------------------------------------------------
def test_load_rejects_an_item_that_breaks_the_contract_and_names_the_line(tmp_path):
    bad = item("W-97")
    del bad["next_action"]
    path = write(tmp_path / "w.jsonl", [item(), bad])
    with pytest.raises(ValueError, match=r"w\.jsonl:2: not a valid work item"):
        work.load(path)
    assert [w["id"] for w in work.load(write(tmp_path / "ok.jsonl", [item()]))] == ["W-90"]


def test_save_validates_and_round_trips_unicode(tmp_path):
    path = tmp_path / "w.jsonl"
    work.save(path, [item(title="Persian title: سلام")])
    assert "سلام" in path.read_text() and work.load(path)[0]["title"].endswith("سلام")
    with pytest.raises(Exception):  # noqa: B017 - jsonschema.ValidationError
        work.save(path, [{**item(), "priority": "P9"}])
    assert "سلام" in path.read_text()      # a refused save leaves the file as it was


def test_the_committed_work_items_all_load():
    items = work.load(ROOT / "work-items.jsonl")
    assert len({w["id"] for w in items}) == len(items)
    ids = {w["id"] for w in items}
    for w in items:
        assert set(w["dependencies"]) <= ids, w["id"]


# ---- aging, lease, update --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("prio,idle,aged_to", [("P2", 13, None), ("P2", 14, "P1"), ("P3", 29, None), ("P3", 30, "P2"), ("P1", 400, None), ("P0", 400, None)])
def test_aging_thresholds(prio, idle, aged_to):
    w = item(priority=prio, updated=date.fromordinal(TODAY.toordinal() - idle).isoformat())
    changed = work.age([w], TODAY)
    assert w["priority"] == (aged_to or prio) and changed == ([w["id"]] if aged_to else [])
    if aged_to:
        assert w["updated_at"] == TODAY.isoformat() and "aged" in w["history"][-1]["change"]


def test_only_open_work_ages_and_a_step_down_happens_once():
    items = [item("W-01", status="done", updated="2026-01-01"), item("W-02", status="dropped", updated="2026-01-01"), item("W-03", status="blocked", updated="2026-01-01"),
             item("W-04", status="waiting_human", priority="P3", updated="2026-01-01")]
    assert work.age(items, TODAY) == ["W-03", "W-04"]
    assert [w["priority"] for w in items] == ["P2", "P2", "P1", "P2"] and items[0]["status"] == "done"
    assert work.age(items, TODAY) == []          # the update date moved, nothing is idle now


def test_an_in_progress_lease_expires_after_a_day_without_history():
    items = [item("W-01", status="in_progress", updated="2026-11-08"), item("W-02", status="in_progress", updated="2026-11-07"), item("W-03", status="in_progress", updated="2026-11-09")]
    assert work.age(items, TODAY) == ["W-02"]
    assert [w["status"] for w in items] == ["in_progress", "open", "in_progress"]
    assert "lease expired" in items[1]["history"][-1]["change"]


def test_update_records_the_change_and_unblocks_dependants():
    items = [item("W-01"), item("W-02", status="blocked", deps=("W-01",)), item("W-03", status="blocked", deps=("W-01", "W-09")), item("W-09")]
    w = work.update(items, "W-01", status="done", note="shipped", next_action="none", trigger="never", today=TODAY)
    assert w["status"] == "done" and w["next_action"] == "none" and w["retry_or_revisit_trigger"] == "never" and w["updated_at"] == "2026-11-09"
    assert w["history"][-1] == {"at": "2026-11-09", "change": "status open -> done; shipped"}
    assert items[1]["status"] == "open" and "unblocked" in items[1]["history"][-1]["change"]
    assert items[2]["status"] == "blocked"       # W-09 is not done
    work.update(items, "W-09", status="done", today=TODAY)
    assert items[2]["status"] == "open"
    assert work.update(items, "W-09", today=TODAY)["history"][-1]["change"] == "touched"
    with pytest.raises(KeyError):
        work.update(items, "W-404")


# ---- the CLI path -------------------------------------------------------------------------------------------------------
def test_work_set_rerenders_without_touching_the_hand_written_head(tmp_path, capsys):
    src, out = write(tmp_path / "w.jsonl", [item("W-01"), item("W-02", status="blocked", deps=("W-01",))]), tmp_path / "BACKLOG.md"
    shutil.copy(ROOT / "BACKLOG.md", out)
    head = out.read_text().split("\n## Items\n")[0]
    assert cli.main(["work", "set", "W-01", "--status", "done", "--note", "ok", "--file", str(src), "--out", str(out)]) == 0
    text = out.read_text()
    assert text.startswith(head) and "| Q5 |" in text and "| W-02 | P2 | open |" in text and "| W-01 | P2 | done |" in text
    assert [w["status"] for w in work.load(src)] == ["done", "open"]
    assert ledger.open().one("SELECT status FROM work_items WHERE id='W-01'").status == "done"      # `set` and `sync` mirror into the ledger
    assert "rendered" in capsys.readouterr().out


def test_work_set_on_an_unknown_id_and_a_broken_file_are_errors_not_tracebacks(tmp_path, capsys):
    src, out = write(tmp_path / "w.jsonl", [item()]), tmp_path / "B.md"
    assert cli.main(["work", "set", "W-404", "--status", "done", "--file", str(src), "--out", str(out)]) == 1
    assert "no work item" in capsys.readouterr().err and not out.exists()
    src.write_text('{"id": "W-01"}\n')
    assert cli.main(["work", "list", "--file", str(src)]) == 1
    assert "not a valid work item" in capsys.readouterr().err
    assert cli.main(["work", "list", "--file", str(tmp_path / "missing.jsonl")]) == 1


def test_work_list_hides_finished_items_unless_asked(tmp_path, capsys):
    src = write(tmp_path / "w.jsonl", [item("W-01"), item("W-02", status="done"), item("W-03", status="dropped")])
    cli.main(["work", "list", "--file", str(src)])
    out = capsys.readouterr().out
    assert "W-01" in out and "W-02" not in out and "W-03" not in out
    cli.main(["work", "list", "--all", "--file", str(src)])
    assert {"W-01", "W-02", "W-03"} <= set(capsys.readouterr().out.split())
