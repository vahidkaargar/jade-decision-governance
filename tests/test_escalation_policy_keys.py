"""V6-11 (CT-16, SP-13, W-83): every key `policies/escalation.yaml` declares is read by code, or is listed here as prose or as a value a test pins.

The same rule `tests/test_routing_policy_w53.py` enforces for `routing.yaml`. Five keys were read by nothing and read as controls that did not exist:
`abstain_to` (where an abstain goes: the exit code is 3 and the agent decides), `max_revisions` (the cap is the constant `state_machine.MAX_REVISIONS`;
a cap the policy file could raise is a safety change, so it is not wired), `human_question.each_question_includes` and `human_question.deadline_default_h`
(the question the human sees is built by `askplain` and `askchat` from the gate's own reading, and shows no deadline), and the `score.gates` flag (a score
ranks and never gates; `thresholds.judge` says so in code). They are deleted, and these tests fail if one comes back unread.
"""

from __future__ import annotations

import yaml

from jade import evals, paths, state_machine, thresholds
from tests.test_routing_policy_w53 import Tracked, leaves

REAL = paths.shipped_policy_dir()

# Keys no code reads, with the reason. A key that is neither read nor listed here fails the truth test; a listed key the file no longer declares fails it too.
PROSE_ONLY = {
    "schema_version": "the shape version of the file, for the reader",
    "defaults.ci": "names the interval the calibration uses (evals.wilson, 95%); the code has no other method",
    "defaults.min_labeled_for_adjustment": "mirrors evals.MIN_LABELED; test_the_values_the_code_keeps_are_the_values_the_file_declares pins the two together",
    "exit_codes.accept": "mirrors thresholds.EXIT; the same test pins it",
    "exit_codes.abstain": "mirrors thresholds.EXIT; the same test pins it",
    "exit_codes.reject": "mirrors thresholds.EXIT; the same test pins it",
    "exit_codes.provider_unavailable": "mirrors thresholds.EXIT; the same test pins it",
    "exit_codes.invalid_question": "mirrors thresholds.EXIT; the same test pins it",
    "families.strategy_choice.note": "a reminder for the reader that a split choice is acceptable",
}
DELETED = ("abstain_to", "max_revisions", "each_question_includes", "deadline_default_h", "human_question", "gates")


def _read_keys(doc, monkeypatch) -> set[str]:
    """Run every code path that reads the policy over every family and risk, and return the key paths the code read."""
    seen: set[str] = set()
    monkeypatch.setattr(thresholds, "escalation", lambda: Tracked(doc, "", seen))
    risks = ("low", "medium", "high", "critical")
    for family in doc["families"]:
        for primitive in ("noul", "choice", "score"):
            for risk in risks:
                thresholds.bands(primitive, family, risk, f"{family}/v1/m")
        for answer in ({"type": "noul", "noul": 0.5}, {"type": "choice", "choice": "a", "probabilities": {"a": 0.8, "b": 0.2}}):
            for risk in risks:
                thresholds.judge(answer, family, risk, 1, "p/m", primitive=answer["type"])
    return seen


def test_every_key_in_escalation_yaml_is_read_by_code_or_declared_prose(monkeypatch):
    doc = yaml.safe_load((REAL / "escalation.yaml").read_text())
    seen = _read_keys(doc, monkeypatch)
    unread = sorted(set(leaves(doc)) - seen - set(PROSE_ONLY))
    assert not unread, f"policies/escalation.yaml declares {unread}, which no code reads: read it where it acts, delete it, or list it in PROSE_ONLY with the reason"
    stale = sorted(k for k in PROSE_ONLY if k not in set(leaves(doc)))
    assert not stale, f"PROSE_ONLY names keys the file no longer declares: {stale}"
    read_but_prose = sorted(k for k in PROSE_ONLY if k in seen)
    assert not read_but_prose, f"{read_but_prose} are read by code now: take them off PROSE_ONLY"


def test_a_new_dead_key_inside_a_noul_or_choice_block_is_seen_as_unread(monkeypatch):
    """The reader of a band block must touch only the keys it uses. If it copies the whole block (`{**a, **b}`), every key in it counts as read and a dead key hides."""
    for where in (("defaults", "noul"), ("defaults", "choice"), ("families", "risk_triage", "noul"), ("families", "strategy_choice", "choice")):
        doc = yaml.safe_load((REAL / "escalation.yaml").read_text())
        block = doc
        for part in where:
            block = block[part]
        block["escalate_to"] = "human"
        seen = _read_keys(doc, monkeypatch)
        unread = sorted(set(leaves(doc)) - seen - set(PROSE_ONLY))
        assert unread == [".".join(where) + ".escalate_to"], f"a dead key under {where} was not found: {unread}"


def test_the_five_unread_keys_are_gone_and_no_document_promises_them():
    text = (REAL / "escalation.yaml").read_text()
    for key in DELETED:
        assert f"{key}:" not in text, f"escalation.yaml declares {key} again, and no code reads it"
    root = REAL.parent
    for rel in ("README.md", *(f"docs/{p.name}" for p in sorted((root / "docs").glob("0*.md")))):
        body = (root / rel).read_text()
        for key in ("each_question_includes", "deadline_default_h", "max_revisions"):
            assert key not in body, f"{rel} names {key}, which is deleted"


def test_the_values_the_code_keeps_are_the_values_the_file_declares():
    doc = yaml.safe_load((REAL / "escalation.yaml").read_text())
    assert doc["defaults"]["min_labeled_for_adjustment"] == evals.MIN_LABELED
    assert {k: v for k, v in doc["exit_codes"].items()} == {k.lower(): v for k, v in thresholds.EXIT.items()}
    assert state_machine.MAX_REVISIONS == 3, "the cap lives in code; a change to it is a safety change and needs the owner"


def test_a_score_never_gates():
    ok = thresholds.judge({"type": "score", "score": 0.0}, "evidence_quality", "critical", 1, "p/m", primitive="score")
    assert ok.verdict == "ACCEPT" and ok.accept is None and "never gates" in ok.reason
