"""W-35: the `context_changed` permit revocation (T8) and the escalation batching window (T14) were never built; the claims are retired.

Nothing raises `context_changed`: the hook consumes a permit and moves its decision to EXECUTING in one transaction, so a decision is never
left in AUTHORIZED for a changed precondition to act on, and a permit whose precondition moved is refused at use (`permits.try_consume`).
Nothing groups escalations in a window. A mechanism that exists only on paper reads as a control, so the edge, the event and the policy key
are deleted, and these tests fail if a policy or a document promises either again.
"""

from __future__ import annotations

import re

import pytest
import yaml

from jade import paths, state_machine

ROOT = paths.shipped_policy_dir().parent
RETIRED_PROMISES = ("designed and not built", "designed but not implemented", "no code raises", "nothing raises it", "nothing raises the event")


def _text(rel: str) -> str:
    return (ROOT / rel).read_text()


def _rows(rel: str, key: str) -> list[str]:
    """The threat's mitigation row (first) and its evidence row (last)."""
    return [r for r in _text(rel).splitlines() if r.startswith(f"| {key} ")]


def _row(rel: str, key: str) -> str:
    return _rows(rel, key)[0]


def test_the_state_machine_has_no_context_changed_edge_or_event():
    assert not any(event == "context_changed" for _, event in state_machine.T)
    assert "context_changed" not in state_machine.RUNTIME_EVENTS | state_machine.OUTCOME_EVENTS | state_machine.HUMAN_EVENTS
    assert not state_machine.agent_may("context_changed", "AUTHORIZED")
    with pytest.raises(state_machine.IllegalTransition, match="no transition"):
        state_machine.next_state("AUTHORIZED", "context_changed")


def test_no_policy_file_declares_a_batching_window_or_a_context_changed_event():
    for f in sorted((ROOT / "policies").glob("*.yaml")):
        text = f.read_text()
        assert "group_window_s" not in text, f"{f.name} declares an escalation batching window that no code applies"
        assert "context_changed" not in text, f.name
    esc = yaml.safe_load(_text("policies/escalation.yaml"))
    assert not any("window" in k for block in esc.values() if isinstance(block, dict) for k in block)


def test_no_document_promises_batching_or_a_context_changed_revocation():
    for rel in ("README.md", *(f"docs/{p.name}" for p in sorted((ROOT / "docs").glob("0*.md")))):
        text = _text(rel)
        assert "group_window_s" not in text, rel
        for line in text.splitlines():
            low = line.lower()
            assert not any(s in low for s in RETIRED_PROMISES), f"{rel}: {line[:120]}"
            if "context_changed" in line:
                assert not re.match(r"\|\s*AUTHORIZED\s*\|\s*`context_changed`", line), f"{rel}: the state table lists the deleted edge"


def test_t8_and_t14_say_what_exists_and_cite_no_open_item_for_it():
    t8, t14 = _row("docs/05-threat-model.md", "T8"), _row("docs/05-threat-model.md", "T14")
    assert "context_changed" in t8 and "W-35" in t8, "T8 must say the edge was deleted and why"
    assert "unusable" in t8, "T8 must still state what protects a permit against changed state"
    assert "not batched" in t14 and "W-35" in t14
    for key in ("T8", "T14"):
        evidence = _rows("docs/05-threat-model.md", key)[-1]
        assert "residual W-35" not in evidence and "accepted risk W-35" not in evidence, evidence[:80]


def test_the_protection_that_exists_is_cited_for_t8():
    """A permit whose precondition moved is refused at use: the test that pins it is the evidence for the T8 row."""
    assert "test_f11_a_push_permit_is_not_used_after_head_moved" in _rows("docs/05-threat-model.md", "T8")[-1]
