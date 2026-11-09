"""W-36: tools that no rule names. An unnamed MCP tool is medium (M-99, recorded, not gated) and a built-in tool the matcher does not name is not gated
at all; `jade check` lists both as an informational row (names and counts only, never arguments, never a failure). The hook records the name of an
unnamed MCP tool once, because a low or medium call is otherwise never journaled and the ledger would have no inventory to read."""
import json

import pytest

from jade import hooks, ledger, ops, policy

SECRET_ARG = "argument-text-that-must-never-be-listed"


def seen(tool, **inp):
    return hooks.pretool({"tool_name": tool, "tool_input": inp, "session_id": "s-1", "cwd": "/tmp", "permission_mode": "default"})


def rows():
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}


def events(kind="tool_unnamed"):
    db = ledger.open()
    try:
        return db.all("SELECT entity_id, kind, guard, actor, detail_json, idempotency_key FROM events WHERE kind=? ORDER BY id", (kind,))
    finally:
        db.close()


# ---- what counts as unnamed ------------------------------------------------------------------------------
@pytest.mark.parametrize("tool", ["mcp__acme__frobnicate", "mcp__acme__Widget", "mcp__acme__do-the-thing"])
def test_a_tool_no_rule_names_is_uncovered(tool):
    assert policy.tool_coverage(tool) == "M-99"


@pytest.mark.parametrize("tool", ["mcp__GitLab__list_projects", "mcp__GitLab__accept_merge_request", "mcp__ccd_sidebar__create_group",
                                  "mcp__x__javascript_tool", "mcp__Kubernetes_MCP_Server__kubectl_delete"])
def test_a_tool_a_specific_rule_names_is_covered(tool):
    assert policy.tool_coverage(tool) is None


def test_a_tool_only_the_verb_heuristic_names_is_reported_as_such():
    assert policy.tool_coverage("mcp__acme__set_widget") == "M-07"


def test_a_built_in_tool_is_covered_when_the_gate_names_it():
    assert all(policy.tool_coverage(t) is None for t in policy.GATED_BUILTIN)
    assert policy.tool_coverage("SomeNewBuiltin") == "ungated"


def test_a_built_in_tool_decided_not_to_gate_is_not_uncovered():
    assert policy.UNGATED_BUILTIN and all(policy.tool_coverage(t) is None for t in policy.UNGATED_BUILTIN)
    assert not set(policy.UNGATED_BUILTIN) & set(policy.GATED_BUILTIN)


# ---- the hook records the name, once ---------------------------------------------------------------------
def test_an_unnamed_mcp_call_is_recorded_once_by_name_and_is_not_blocked():
    for _ in range(3):
        assert seen("mcp__acme__frobnicate", note=SECRET_ARG).exit_code == 0
    ev = events()
    assert len(ev) == 1
    assert ev[0].entity_id == "mcp__acme__frobnicate" and ev[0].actor == "hook" and ev[0].guard.startswith("M-99")
    assert json.loads(ev[0].detail_json)["tool"] == "mcp__acme__frobnicate"
    assert SECRET_ARG not in json.dumps([vars(r) for r in events()])


def test_two_unnamed_tools_are_two_events():
    seen("mcp__acme__frobnicate")
    seen("mcp__acme__other_thing")
    assert sorted(json.loads(e.detail_json)["tool"] for e in events()) == ["mcp__acme__frobnicate", "mcp__acme__other_thing"]


@pytest.mark.parametrize("tool", ["mcp__GitLab__list_projects", "Read", "WebFetch", "Agent"])
def test_a_named_or_ungated_call_records_nothing(tool):
    seen(tool, file_path="/tmp/x", url="https://example.invalid")
    assert events() == []


def test_a_ledger_that_cannot_be_written_does_not_change_the_verdict(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "open", boom)
    assert seen("mcp__acme__frobnicate").exit_code == 0


# ---- the check row ---------------------------------------------------------------------------------------
def test_the_row_exists_and_passes_on_an_empty_ledger():
    ok, detail = rows()["tool coverage"]
    assert ok is True and "no tool" in detail


def test_the_row_lists_unnamed_tools_by_name_and_never_fails():
    seen("mcp__acme__frobnicate", note=SECRET_ARG)
    seen("mcp__acme__set_widget", note=SECRET_ARG)
    ok, detail = rows()["tool coverage"]
    assert ok is True
    assert "mcp__acme__frobnicate" in detail and "M-99" in detail
    assert "mcp__acme__set_widget" in detail and "M-07" in detail
    assert SECRET_ARG not in detail


def test_the_row_lists_a_built_in_tool_the_ledger_saw_that_nothing_decided(monkeypatch):
    db = ledger.open()
    db.event("gate", "gated", guard="R-01 high", actor="hook", detail={"tool": "BrandNewTool"})
    db.event("gate", "gated", guard="R-01 high", actor="hook", detail={"tool": "Bash"})
    db.close()
    ok, detail = rows()["tool coverage"]
    assert ok is True and "BrandNewTool" in detail and "1 built-in tool(s) nothing gates" in detail


def test_the_row_counts_gated_tools_it_has_seen():
    db = ledger.open()
    for tool in ("Bash", "Write", "mcp__GitLab__accept_merge_request"):
        db.event("gate", "gated", guard="R-01 high", actor="hook", detail={"tool": tool})
    db.close()
    ok, detail = rows()["tool coverage"]
    assert ok is True and "3 tool name(s)" in detail and "no unnamed" in detail


def test_rows_a_test_run_wrote_are_not_inventory(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    seen("mcp__acme__frobnicate")
    monkeypatch.delenv("JADE_SOURCE")
    ok, detail = rows()["tool coverage"]
    assert ok is True and "frobnicate" not in detail


def test_a_damaged_event_row_does_not_break_the_check():
    db = ledger.open()
    db.con.execute("INSERT INTO events(entity_id, kind, actor, detail_json, ts) VALUES('gate','gated','hook','{not json', '2026-01-01T00:00:00Z')")
    db.con.commit()
    db.close()
    ok, _ = rows()["tool coverage"]
    assert ok is True


def test_a_long_list_is_cut_and_says_how_many_more():
    for i in range(ops.TOOL_LIST_MAX + 3):
        seen(f"mcp__acme__thing{i:02d}")
    ok, detail = rows()["tool coverage"]
    assert ok is True and "+3 more" in detail


def test_a_failing_ledger_makes_an_informational_row_not_a_failure(monkeypatch):
    monkeypatch.setattr(ledger, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    row = ops.tool_coverage_row()
    assert row[0] == "tool coverage" and row[1] is True and "unavailable" in row[2]
