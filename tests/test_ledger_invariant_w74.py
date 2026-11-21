"""W-74: every `safety_block` event says in its detail what stopped the call, as ids only, so `jade learn blockers` can count blocks per invariant.

V6-32 wrote the invariant ids (`invariants`) for an invariant block and for an SI-07 hold. This file pins that for each invariant the row names (SI-09, SI-10 by
Bash and by the Read tool, SI-11), in advisory mode too (the first weeks of real data are advisory), and closes the one `safety_block` that still carried no id:
the level-off floor (V6-16), which now writes the human-only classes that fired (`floor`). The guard text and the verdict do not change.

Every row is synthetic and lives in a temporary JADE home (tests/conftest.py).
"""
# ruff: noqa: F811   (the fixture is imported from the memory tests and then named as a test argument)
import json
from pathlib import Path

import pytest

from jade import hooks, learn, ledger, ops, paths
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_memory_w101 import enforce  # noqa: F401  (fixture)


def blocks():
    return m.rows("SELECT guard, detail_json FROM events WHERE kind='safety_block' ORDER BY id")


def read_call(path):
    return {"tool_name": "Read", "tool_input": {"file_path": str(path)}, "session_id": m.SESSION, "cwd": "/tmp", "permission_mode": "auto"}


CASES = [("SI-09", lambda: m.call("jade permit approve prm_x")),
         ("SI-10", lambda: m.call("cat permit-seal.key")),
         ("SI-10", lambda: read_call(paths.home() / "permit-seal.key")),
         ("SI-11", lambda: m.call("JADE_GATE_MODE=advisory true"))]


@pytest.mark.parametrize(("inv", "event"), CASES, ids=["SI-09-bash", "SI-10-bash", "SI-10-read", "SI-11-bash"])
def test_each_invariant_block_names_its_id_in_the_event_detail(enforce, inv, event):
    r = hooks.pretool(event())
    assert r.exit_code == 2 and inv in r.stderr
    (row,) = blocks()
    assert inv in json.loads(row["detail_json"])["invariants"]
    assert inv in row["guard"]


@pytest.mark.parametrize(("inv", "event"), [CASES[1], ("SI-08", lambda: m.call("git push origin main"))], ids=["SI-10", "SI-08"])
def test_an_advisory_block_names_its_id_too(monkeypatch, inv, event):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool(event())
    assert r.exit_code == 0 and "[advisory, not blocked]" in r.stderr
    (row,) = blocks()
    assert json.loads(row["detail_json"])["invariants"] == [inv]


# ---- the level-off floor (V6-16) ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def off(enforce):
    ops.set_level("off", stdin_isatty=True)


def settings_write():
    return {"tool_name": "Write", "tool_input": {"file_path": str(Path.home() /".claude" / "settings.json"), "content": "x"},
            "session_id": m.SESSION, "cwd": "/tmp", "permission_mode": "auto"}


def test_a_floor_block_names_the_classes_that_fired_in_the_event_detail(off):
    r = hooks.pretool(settings_write())
    assert r.exit_code == 2 and r.stderr.startswith("JADE floor: level off blocks this Write call.") and "HO-wiring" in r.stderr
    (row,) = blocks()
    d = json.loads(row["detail_json"])
    assert "HO-wiring" in d["floor"] and set(d["floor"]) <= set(hooks.OFF_FLOOR_CLASSES) and d["floor"] == sorted(d["floor"])
    assert "invariants" not in d                          # the floor is not a safety invariant
    assert row["guard"].startswith("R-13 ") and "SI-" not in row["guard"]   # the guard text keeps its shape


def test_the_blocker_report_counts_a_floor_block_by_class_not_as_unrecorded(off):
    hooks.pretool(settings_write())
    db = ledger.open()
    try:
        r = learn.blocker_classes(db, "")
        sample = learn.blocker_sample(db, "")
    finally:
        db.close()
    assert r["safety_block_rows"] == 1
    assert r["invariant_rows"]["unclassified"] == 0 and r["invariant_rows"]["from_floor"] == 1
    assert r["invariants"] == {} and r["floor_classes"]["HO-wiring"] == 1
    assert sample[0]["classes_from"] == "floor" and "HO-wiring" in sample[0]["classes"]
    text = learn.render_blockers(r)
    assert "level-off floor 1" in text and "HO-wiring  1" in text


def test_a_floor_row_with_a_bad_class_list_stays_unclassified():
    assert learn.classes_of("safety_block", "R-13 high", json.dumps({"floor": ["HO-wiring", "rm -rf /"]})) == ([], "none")
    assert learn.classes_of("safety_block", "R-13 high", json.dumps({"floor": "HO-wiring"})) == ([], "none")
    assert learn.classes_of("safety_block", "R-13 high", json.dumps({"floor": []})) == ([], "none")
