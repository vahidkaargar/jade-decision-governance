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


# The quality gates `lint` implements, by the name policies/routing.yaml lists them under. The policy decides which of them run.
GATES = ("one_concept_per_question", "explicit_positive_and_negative_criteria", "no_dependency_on_sibling_answer_in_same_batch",
         "state_fields_named_and_referenced_in_backticks", "no_match_option_for_open_sets", "no_leading_language")


@lru_cache(maxsize=1)
def _scan() -> tuple[dict[str, dict[int, dict]], dict[str, list[str]]]:
    """(entries by question id and version, problems by file). A file that is not valid JSON, or that is no object with an id and a version, is reported
    and not loaded; two files for one id and version are reported and the later one wins."""
    out: dict[str, dict[int, dict]] = {}
    bad: dict[str, list[str]] = {}
    files: dict[tuple[str, int], str] = {}
    for f in sorted(paths.registry_dir().glob("*.json")):
        try:
            e = json.loads(f.read_text())
            qid, ver = e["question_id"], int(e["version"])
            if not isinstance(qid, str):
                raise TypeError
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
            bad.setdefault(f.name, []).append("not a JSON object with a question_id and an integer version")
            continue
        if (qid, ver) in files:
            bad.setdefault(f"{qid}@{ver}", []).append(f"more than one file holds this question and version ({files[(qid, ver)]}, {f.name}); the later one is served")
        files[(qid, ver)] = f.name
        out.setdefault(qid, {})[ver] = e
    return out, bad


def _all() -> dict[str, dict[int, dict]]:
    return _scan()[0]


def reset_cache() -> None:
    _scan.cache_clear()


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
    problem = schema_problem(vs[version])
    if problem:
        raise InvalidQuestion(f"registry entry {question_id}@{version} breaks schemas/question.schema.json ({problem}); it is not served")
    return vs[version]


def schema_problem(entry: dict) -> str | None:
    """First line of what is wrong with the entry against schemas/question.schema.json, or None."""
    from jsonschema import ValidationError

    from . import schemas

    try:
        schemas.validate("question", entry)
    except ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path)
        return f"{where + ': ' if where else ''}{e.message[:160]}"
    return None


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


def build_choice(question_id: str | None, options, instructions: str | None = None, eligible: set[str] | None = None, version: int | None = None) -> dict:
    """Choice question. Rejects a set with fewer than two eligible options: the API answers a one-option
    choice with confidence 1.0, which would look like certainty (inventory S9). `version` builds that registered version, not the active one."""
    entry = get(question_id, version) if question_id and not question_id.startswith("q.adhoc.") else None
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


def build_noul(question_id: str | None, instructions: str | None = None, true: str | None = None, false: str | None = None, version: int | None = None) -> dict:
    entry = get(question_id, version) if question_id and not question_id.startswith("q.adhoc.") else None
    tpl = (entry or {}).get("template", {})
    crit = tpl.get("criteria") or {}
    text, t, f = instructions or tpl.get("instructions"), true or crit.get("true"), false or crit.get("false")
    if not (text and t and f and str(text).strip() and str(t).strip() and str(f).strip()):
        raise InvalidQuestion("noul needs instructions and both true/false criteria")
    if " ".join(str(t).split()).lower() == " ".join(str(f).split()).lower():
        raise InvalidQuestion("noul needs distinct true and false criteria")
    return {"type": "noul", "instructions": text, "criteria": {"true": t, "false": f}}


def build_score(question_id: str | None, instructions: str | None = None, levels: list[str] | None = None, version: int | None = None) -> dict:
    entry = get(question_id, version) if question_id and not question_id.startswith("q.adhoc.") else None
    tpl = (entry or {}).get("template", {})
    text, lv = instructions or tpl.get("instructions"), levels or tpl.get("criteria")
    if not text or not isinstance(lv, list) or len(lv) < 2:
        raise InvalidQuestion("score needs instructions and at least two ordered levels, lowest first")
    return {"type": "score", "instructions": text, "criteria": list(lv)}


def lint(entry: dict) -> list[str]:
    """Quality gates from policies/routing.yaml `question_quality_gates`: only the gates the policy lists run. Returns human-readable problems."""
    from . import policy

    on = set(policy.routing()["question_quality_gates"])
    problems = []

    def gate(name: str, problem: str) -> None:
        if name in on:
            problems.append(f"{name}: {problem}")

    tpl = entry.get("template", {})
    text = tpl.get("instructions", "")
    low = text.lower()
    for tok in BANNED:
        if tok in low:
            gate("no_leading_language", f"banned token '{tok}'")
    if text.count("?") > 1 or re.search(r"\?\s+(and|also)\b", low):
        gate("one_concept_per_question", "more than one question in instructions")
    refs = set(re.findall(r"`([a-z_][a-z0-9_]*)`", text))
    props = set(entry.get("input_schema", {}).get("properties") or {})
    if not refs:
        gate("state_fields_named_and_referenced_in_backticks", "no `field` reference in instructions")
    for r in sorted(refs - props):
        gate("state_fields_named_and_referenced_in_backticks", f"`{r}` is not in input_schema.properties")
    crit = tpl.get("criteria")
    prim = entry.get("primitive")
    if prim == "noul":
        if not isinstance(crit, dict) or not crit.get("true") or not crit.get("false") or crit.get("true") == crit.get("false"):
            gate("explicit_positive_and_negative_criteria", "noul needs distinct true and false criteria")
    if prim == "choice" and not entry.get("no_match_option"):
        if not (isinstance(crit, dict) and len(crit) >= 2):
            gate("no_match_option_for_open_sets", "an open choice needs a no_match_option (selecting it is ABSTAIN)")
    if prim == "score" and not (isinstance(crit, list) and len(crit) >= 2):
        gate("explicit_positive_and_negative_criteria", "score needs at least two ordered levels")
    if entry.get("dependencies") and entry.get("batch_eligibility"):
        gate("no_dependency_on_sibling_answer_in_same_batch", "a question with dependencies cannot be batch eligible")
    tp = entry.get("threshold_policy", {})
    if prim == "noul" and not tp.get("accept", 0) > tp.get("reject", 0):
        problems.append("threshold_policy: accept must be greater than reject")
    return problems


def validate_registry() -> dict[str, list[str]]:
    """Every problem in the registry, by `question_id@version` (a file name when the file is not an entry): the schema, the quality gates, a
    dependency on a question that does not exist, two files for one entry, and the declared limit: a task family may not hold more batch-eligible
    questions than `max_questions_per_request`, because the batch of a family would not fit in one request. Empty means clean."""
    from . import route

    reg, bad = _scan()
    out: dict[str, list[str]] = {k: list(v) for k, v in bad.items()}
    for qid, versions in sorted(reg.items()):
        for ver, e in sorted(versions.items()):
            found = [] if (m := schema_problem(e)) is None else [f"schema: {m}"]
            found += lint(e)
            found += [f"dependencies: {d} is not in the registry" for d in e.get("dependencies") or [] if d not in reg]
            if found:
                out.setdefault(f"{qid}@{ver}", []).extend(found)
    cap = route.limits().max_questions
    batch: dict[str, set[str]] = {}
    for qid, versions in reg.items():
        for e in versions.values():
            if e.get("batch_eligibility") is True and isinstance(e.get("task_family"), str):
                batch.setdefault(e["task_family"], set()).add(qid)
    for family, ids in sorted(batch.items()):
        if len(ids) > cap:
            out.setdefault(f"task_family {family}", []).append(
                f"{len(ids)} batch-eligible questions ({', '.join(sorted(ids))}); one batched request holds at most {cap} (max_questions_per_request in policies/routing.yaml)")
    return out
