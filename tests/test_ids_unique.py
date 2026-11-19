"""One id register (V6-02): a W id and a T id each name one thing.

Before this, `W-115` was the SI-13 sandbox row on main and the meaning of PR 47's fourth review in the same repo; `W-96` to `W-105` were feature ids in the CHANGELOG while
the working-tree backlog reused them for other rows. A new duplicate fails here. The allow-lists are visible and must shrink: an entry that is no longer a duplicate fails too.
"""

import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# W ids that exist twice on purpose today (none), and T ids (none). V6-04 and V6-13 may add an entry only with a work item that removes it.
KNOWN_DUPLICATE_W: set[str] = set()
KNOWN_DUPLICATE_T: set[str] = set()
# Historic feature ids: no row of work-items.jsonl may use them (W-115 is the one real row inside the range: the SI-13 sandbox row).
HISTORIC_FEATURE_IDS = {f"W-{n}" for n in range(96, 106)}
RETIRED_WORKING_TREE_IDS = {f"W-{n}" for n in range(106, 117)} - {"W-115"}
FIRST_FREE_AFTER_THE_RENUMBER = 117


def _rows() -> list[dict]:
    return [json.loads(line) for line in (ROOT / "work-items.jsonl").read_text().splitlines() if line.strip()]


def _threat_tables() -> list[list[str]]:
    """The T ids of each table of docs/05-threat-model.md (a table is a run of lines that start with `|`)."""
    tables: list[list[str]] = []
    run: list[str] = []
    for line in [*(ROOT / "docs" / "05-threat-model.md").read_text().splitlines(), ""]:
        if line.startswith("|"):
            m = re.match(r"\|\s*(T\d+)\s*\|", line)
            if m:
                run.append(m.group(1))
        else:
            if run:
                tables.append(run)
            run = []
    return tables


def test_every_work_item_id_is_used_once():
    count = collections.Counter(r["id"] for r in _rows())
    duplicates = {i for i, n in count.items() if n > 1}
    assert duplicates == KNOWN_DUPLICATE_W, f"duplicate work item ids: {sorted(duplicates - KNOWN_DUPLICATE_W)}; allow-list entries that are fixed: {sorted(KNOWN_DUPLICATE_W - duplicates)}"


def test_the_backlog_lists_the_same_ids_as_the_store():
    listed = re.findall(r"^\|\s*(W-\d+)\s*\|", (ROOT / "BACKLOG.md").read_text(), re.M)
    assert sorted(listed) == sorted(r["id"] for r in _rows())


def test_a_work_item_does_not_reuse_a_historic_feature_id_or_a_retired_working_tree_id():
    used = {r["id"] for r in _rows()}
    assert not used & (HISTORIC_FEATURE_IDS | RETIRED_WORKING_TREE_IDS), sorted(used & (HISTORIC_FEATURE_IDS | RETIRED_WORKING_TREE_IDS))


def test_the_working_tree_rows_moved_to_the_ids_above_the_old_range():
    moved = [r for r in _rows() if any("renumbered from" in h["change"] for h in r["history"])]
    assert moved and all(int(r["id"][2:]) >= FIRST_FREE_AFTER_THE_RENUMBER for r in moved)


def test_every_threat_id_is_used_once_in_each_table_and_the_tables_agree():
    tables = _threat_tables()
    assert len(tables) == 2, "docs/05 has a threat table and an evidence table"
    for ids in tables:
        dup = {i for i, n in collections.Counter(ids).items() if n > 1}
        assert dup == KNOWN_DUPLICATE_T, f"duplicate threat ids: {sorted(dup - KNOWN_DUPLICATE_T)}; allow-list entries that are fixed: {sorted(KNOWN_DUPLICATE_T - dup)}"
    assert set(tables[0]) == set(tables[1]), sorted(set(tables[0]) ^ set(tables[1]))


def test_the_doc_says_what_the_historic_and_renumbered_ids_mean():
    text = (ROOT / "docs" / "07-self-improvement-and-work-management.md").read_text()
    for needle in ("historic feature ids", "`W-96` to `W-105`", "W-117", "W-137", "WT W-110"):
        assert needle in text, needle
