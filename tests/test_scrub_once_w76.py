"""W-76, scrub part (owner decision): `Ledger.scrub` runs a full `VACUUM` once per ledger file, marks that in the `meta` table, and from then on only a `TRUNCATE`
checkpoint. `VACUUM` rewrites the whole file and holds the lock for a time that grows with the file (7 to 9 s at 930k rows), so a hook that waits 1.5 s blocked a
high-risk call (docs/05 T17). The one `VACUUM` rebuilds the pages freed before `secure_delete=ON` (an older ledger); after it, every connection JADE opens zeroes
what it deletes or overwrites, and the checkpoint folds the write-ahead log into the file and cuts it to zero.
"""

from __future__ import annotations

import json
import sqlite3

from jade import ledger, ops

MARK = "zq-scrub-once-7c3a"
BIG = MARK + "-" + "Q" * 6000 + "-" + MARK   # larger than a page: the text goes to overflow pages
OLD = "2000-01-01T00:00:00Z"


def statements(db) -> list[str]:
    seen: list[str] = []
    db.con.set_trace_callback(lambda s: seen.append(s.strip().upper()))
    return seen


def vacuums(seen: list[str]) -> int:
    return sum(1 for s in seen if s.startswith("VACUUM"))


def checkpoints(seen: list[str]) -> int:
    return sum(1 for s in seen if s.startswith("PRAGMA WAL_CHECKPOINT"))


def file_bytes(db) -> list[tuple[str, bytes]]:
    return [(f.name, f.read_bytes()) for f in db.path.parent.glob(db.path.name + "*")]


def plant_old_events(db, n: int = 6) -> None:
    for i in range(n):
        db.con.execute("INSERT INTO events(entity_id, kind, detail_json, ts) VALUES(?,?,?,?)",
                       (f"dec_{i:012x}", "note", json.dumps({"x": BIG if i % 2 else f"short {MARK} {i}"}), OLD))


def test_the_first_scrub_vacuums_and_marks_the_file_and_a_later_one_only_checkpoints():
    db = ledger.open()
    assert db.meta_get(ledger.SCRUB_VACUUM_KEY) is None
    seen = statements(db)
    assert db.scrub() is True
    assert vacuums(seen) == 1 and checkpoints(seen) == 1
    assert db.meta_get(ledger.SCRUB_VACUUM_KEY), "the one VACUUM must be recorded in the file"
    seen.clear()
    assert db.scrub() is True
    assert vacuums(seen) == 0, "after the one VACUUM a scrub must not rewrite the whole file again"
    assert checkpoints(seen) == 1, "the write-ahead log is still emptied every time"
    db.close()
    again = ledger.open()   # the mark lives in the file, not in the process
    seen = statements(again)
    assert again.scrub() is True
    assert vacuums(seen) == 0 and checkpoints(seen) == 1


def test_a_vacuum_that_cannot_run_leaves_no_mark_and_the_next_scrub_tries_again():
    db = ledger.open()
    db.con.execute("PRAGMA busy_timeout=0")
    other = sqlite3.connect(db.path, timeout=0, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")   # another process holds the write lock
    assert db.scrub() is False
    assert db.meta_get(ledger.SCRUB_VACUUM_KEY) is None, "a VACUUM that did not run must not be marked as done"
    other.execute("ROLLBACK")
    other.close()
    seen = statements(db)
    assert db.scrub() is True
    assert vacuums(seen) == 1
    assert db.meta_get(ledger.SCRUB_VACUUM_KEY)


def test_a_checkpoint_that_cannot_finish_reports_false():
    db = ledger.open()
    assert db.scrub() is True   # the one VACUUM is done
    db.con.execute("PRAGMA busy_timeout=0")
    db.con.execute("INSERT INTO events(entity_id, kind, detail_json, ts) VALUES('x','note','{}',?)", (OLD,))
    reader = sqlite3.connect(db.path, timeout=0, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM events").fetchone()   # an open read keeps the log in use, so it cannot be cut to zero
    try:
        assert db.scrub() is False, "the checkpoint is the whole scrub now: a blocked one must not be reported as done"
    finally:
        reader.execute("ROLLBACK")
        reader.close()


def test_on_a_marked_file_a_purge_leaves_no_trace_of_short_or_page_sized_text_in_the_database_or_the_log():
    db = ledger.open()
    assert db.scrub() is True   # the one VACUUM is done: from here on only secure_delete and the checkpoint keep the text out of the file
    plant_old_events(db)
    assert any(MARK.encode() in b for _, b in file_bytes(db)), "the probe must be in the file first"
    seen = statements(db)
    assert ops.purge(db, 90) >= 6
    assert vacuums(seen) == 0
    found = [name for name, b in file_bytes(db) if MARK.encode() in b]
    assert not found, found


def test_a_ledger_with_text_freed_before_secure_delete_is_rebuilt_by_the_first_scrub():
    db = ledger.open()
    plant_old_events(db)
    db.close()
    raw = sqlite3.connect(db.path, isolation_level=None)   # an older ledger: rows deleted with secure_delete off leave their text in free pages
    raw.execute("PRAGMA secure_delete=OFF")
    raw.execute("DELETE FROM events WHERE kind='note'")
    raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw.close()
    db = ledger.open()
    assert any(MARK.encode() in b for _, b in file_bytes(db)), "the freed text must be in the file before the scrub"
    assert db.scrub() is True
    found = [name for name, b in file_bytes(db) if MARK.encode() in b]
    assert not found, found
