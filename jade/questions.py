"""Question registry: load, lint and build Decisions API questions (docs/03 section 4)."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from . import paths

BANNED = ("obviously", "clearly", "confirm that", "as expected", "surely", "of course")
ADHOC = {"noul": ("q.adhoc.noul", "verify_claim"), "choice": ("q.adhoc.choice", "strategy_choice"), "score": ("q.adhoc.score", "evidence_quality")}


class InvalidQuestion(ValueError):
    """The question cannot be sent: malformed, degenerate or unknown (exit code 6)."""


@lru_cache(maxsize=1)
def _all() -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = {}
    for f in sorted(paths.registry_dir().glob("*.json")):
        e = json.loads(f.read_text())
        out.setdefault(e["question_id"], {})[int(e["version"])] = e
    return out


def reset_cache() -> None:
    _all.cache_clear()


def versions(question_id: str) -> list[int]:
    return sorted(_all().get(question_id, {}))


def get(question_id: str, version: int | None = None) -> dict:
    """Registry entry. Without a version: the active one (champion/challenger pin, else the lowest registered)."""
    vs = _all().get(question_id)
    if not vs:
        raise InvalidQuestion(f"unknown question_id {question_id}")
    if version is None:
        from . import ops
        from .util import session_id

        version = ops.active_version(None, question_id, key=session_id()) or min(vs)
    if version not in vs:
        raise InvalidQuestion(f"{question_id} has no version {version}")
    return vs[version]


def entries() -> list[dict]:
    return [v for vs in _all().values() for v in vs.values()]


def _options(options) -> dict[str, str]:
    if isinstance(options, dict):
        return {str(k).strip(): str(v).strip() for k, v in options.items() if str(k).strip()}
    out = {}
    for o in options or []:
        name, sep, desc = str(o).partition("=")
        if not sep or not name.strip():
            raise InvalidQuestion(f"option must be name=description, got {o!r}")
        out[name.strip()] = desc.strip()
    return out


def build_choice(question_id: str | None, options, instructions: str | None = None, eligible: set[str] | None = None) -> dict:
    """Choice question. Rejects a set with fewer than two eligible options: the API answers a one-option
    choice with confidence 1.0, which would look like certainty (inventory S9)."""
    entry = get(question_id) if question_id and not question_id.startswith("q.adhoc.") else None
    opts = _options(options) if options else dict((entry or {}).get("template", {}).get("criteria") or {})
    if eligible is not None:
        opts = {k: v for k, v in opts.items() if k in eligible}
    floor = max(2, int((entry or {}).get("min_options", 2)))
    if len(opts) < floor:
        raise InvalidQuestion(f"choice needs at least {floor} eligible options, got {len(opts)}")
    no_match = (entry or {}).get("no_match_option")
    if no_match and no_match not in opts:
        opts[no_match] = "None of the other options fits."
    text = instructions or (entry or {}).get("template", {}).get("instructions")
    if not text:
        raise InvalidQuestion("choice needs instructions")
    return {"type": "choice", "instructions": text, "criteria": opts}


def build_noul(question_id: str | None, instructions: str | None = None, true: str | None = None, false: str | None = None) -> dict:
    entry = get(question_id) if question_id and not question_id.startswith("q.adhoc.") else None
    tpl = (entry or {}).get("template", {})
    crit = tpl.get("criteria") or {}
    text, t, f = instructions or tpl.get("instructions"), true or crit.get("true"), false or crit.get("false")
    if not (text and t and f):
        raise InvalidQuestion("noul needs instructions and both true/false criteria")
    return {"type": "noul", "instructions": text, "criteria": {"true": t, "false": f}}


def build_score(question_id: str | None, instructions: str | None = None, levels: list[str] | None = None) -> dict:
    entry = get(question_id) if question_id and not question_id.startswith("q.adhoc.") else None
    tpl = (entry or {}).get("template", {})
    text, lv = instructions or tpl.get("instructions"), levels or tpl.get("criteria")
    if not text or not isinstance(lv, list) or len(lv) < 2:
        raise InvalidQuestion("score needs instructions and at least two ordered levels, lowest first")
    return {"type": "score", "instructions": text, "criteria": list(lv)}


def lint(entry: dict) -> list[str]:
    """Quality gates from policies/routing.yaml `question_quality_gates`. Returns human-readable problems."""
    problems = []
    tpl = entry.get("template", {})
    text = tpl.get("instructions", "")
    low = text.lower()
    for tok in BANNED:
        if tok in low:
            problems.append(f"no_leading_language: banned token '{tok}'")
    if text.count("?") > 1 or re.search(r"\?\s+(and|also)\b", low):
        problems.append("one_concept_per_question: more than one question in instructions")
    refs = set(re.findall(r"`([a-z_][a-z0-9_]*)`", text))
    props = set(entry.get("input_schema", {}).get("properties") or {})
    if not refs:
        problems.append("state_fields_named_and_referenced_in_backticks: no `field` reference in instructions")
    for r in sorted(refs - props):
        problems.append(f"state_fields_named_and_referenced_in_backticks: `{r}` is not in input_schema.properties")
    crit = tpl.get("criteria")
    prim = entry.get("primitive")
    if prim == "noul":
        if not isinstance(crit, dict) or not crit.get("true") or not crit.get("false") or crit.get("true") == crit.get("false"):
            problems.append("explicit_positive_and_negative_criteria: noul needs distinct true and false criteria")
    if prim == "choice" and not entry.get("no_match_option") and not any(d.endswith(".any_fits") for d in entry.get("dependencies", [])):
        if not (isinstance(crit, dict) and len(crit) >= 2):
            problems.append("no_match_or_companion_presence_question_for_open_sets: open choice needs no_match_option or an any_fits companion")
    if prim == "score" and not (isinstance(crit, list) and len(crit) >= 2):
        problems.append("explicit_positive_and_negative_criteria: score needs at least two ordered levels")
    if entry.get("dependencies") and entry.get("batch_eligibility"):
        problems.append("no_dependency_on_sibling_answer_in_same_batch: a question with dependencies cannot be batch eligible")
    tp = entry.get("threshold_policy", {})
    if prim == "noul" and not tp.get("accept", 0) > tp.get("reject", 0):
        problems.append("threshold_policy: accept must be greater than reject")
    return problems
