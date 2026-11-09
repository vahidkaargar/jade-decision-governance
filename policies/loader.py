"""Reference loader for policy files plus optional machine-local overlays.

Used by tests today and by the `jade` runtime from Phase 1. Pure functions, no I/O beyond reading
the YAML files given. Overlay rules:

* risk: `environments.*` lists are appended; `rules_prepend` are merged by severity tier, ahead of
  the shipped rules of the same tier. Because the list stays ordered critical > high > medium > low
  and the first match wins, an overlay rule can raise a command's level but can never shadow a
  shipped rule of a higher tier. `rules_override` may change `level` or `rationale` of a shipped
  rule by id but never remove it or lower a critical/high rule below `medium`.
* safety: `invariants_append` only. Shipped invariants cannot be removed or edited.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
POLICIES = Path(__file__).resolve().parent


class OverlayError(ValueError):
    """The overlay tried something the merge rules forbid."""


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def load_risk(overlay: Path | None = None, base: Path | None = None) -> dict:
    risk = _read(base or POLICIES / "risk.yaml")
    if overlay is None or not Path(overlay).exists():
        return _finish(risk)
    ov = _read(Path(overlay))
    envs = ov.get("environments") or {}
    if not isinstance(envs, dict) or any(not isinstance(v, list) for v in envs.values()):
        raise OverlayError("overlay `environments` must be a mapping of lists (a string would be split into one-character hosts)")
    for key, extra in envs.items():
        risk.setdefault("environments", {}).setdefault(key, [])
        risk["environments"][key] = list(dict.fromkeys(risk["environments"][key] + list(extra)))
    prepend = list(ov.get("rules_prepend") or [])
    shipped_ids = {r["id"] for r in risk["rules"]}
    for r in prepend:
        if r["id"] in shipped_ids:
            raise OverlayError(f"overlay rule {r['id']} collides with a shipped rule id")
        if not re.fullmatch(r"R-L\d{2}", r["id"]):
            raise OverlayError(f"overlay rule ids must look like R-L01, got {r['id']}")
    for rid, change in (ov.get("rules_override") or {}).items():
        target = next((r for r in risk["rules"] if r["id"] == rid), None)
        if target is None:
            raise OverlayError(f"rules_override names unknown rule {rid}")
        if "level" in change and ORDER[change["level"]] > ORDER["medium"] and ORDER[target["level"]] < ORDER["medium"]:
            raise OverlayError(f"overlay may not lower {rid} from {target['level']} to {change['level']}")
        target.update({k: v for k, v in change.items() if k in ("level", "rationale")})
    # stable sort: overlay rules first within a tier, tiers in severity order
    risk["rules"] = sorted(prepend + risk["rules"], key=lambda r: ORDER[r["level"]])
    return _finish(risk)


def _finish(risk: dict) -> dict:
    levels = [ORDER[r["level"]] for r in risk["rules"]]
    if levels != sorted(levels):
        raise OverlayError("rules must stay ordered critical > high > medium > low after merge")
    for r in risk["rules"]:
        r["_re"] = re.compile(r["match"])
    return risk


def classify(risk: dict, command: str) -> dict:
    """Return the first matching rule for a whitespace-normalised command."""
    norm = re.sub(r"\s+", " ", command).strip()
    for r in risk["rules"]:
        if r["_re"].search(norm):
            return r
    raise AssertionError("default rule R-99 must match everything")


def environment_of(risk: dict, command: str) -> str:
    env = risk.get("environments", {})
    if any(h and h in command for h in env.get("production_hosts", [])):
        return "production"
    low = command.lower()  # the macOS filesystem is case-insensitive
    if any(p and p.lower() in low for p in env.get("production_paths", [])):
        return "production"
    if any(re.search(m, command) for m in env.get("staging_markers", [])):
        return "staging"
    return "local"


def load_safety(overlay: Path | None = None, base: Path | None = None) -> dict:
    safety = _read(base or POLICIES / "safety.yaml")
    if overlay is not None and Path(overlay).exists():
        ov = _read(Path(overlay))
        if set(ov) - {"schema_version", "invariants_append"}:
            raise OverlayError("safety overlay may only contain invariants_append")
        shipped = {i["id"] for i in safety["invariants"]}
        for inv in ov.get("invariants_append") or []:
            if inv["id"] in shipped or not re.fullmatch(r"SL-\d{2}", inv["id"]):
                raise OverlayError(f"overlay invariant id must be SL-xx and unique, got {inv['id']}")
            safety["invariants"].append(inv)
    for inv in safety["invariants"]:
        if "block" in inv:
            inv["_re"] = re.compile(inv["block"])
        if "block_quoted" in inv:   # also tried on the raw text views, where quoted text is not blanked (policy._assess_shell)
            inv["_qre"] = re.compile(inv["block_quoted"])
        # `content_block` (default: `block`) is what the content scan of a file write looks for, and `scan_content_paths` limits that scan to
        # the files it names (a shell profile, an environment file): see SI-11.
        if "content_block" in inv:
            inv["_cre"] = re.compile(inv["content_block"])
        if "scan_content_paths" in inv:
            inv["_paths_re"] = re.compile(inv["scan_content_paths"])
    return safety


def blocked_by(safety: dict, command: str) -> list[str]:
    return [i["id"] for i in safety["invariants"] if "_re" in i and i["_re"].search(command)]


# ---- overlay lint (`jade check`) ----------------------------------------------------------------------------------------------
# The loader accepts more than is safe (an empty `block` matches every command, a shipped high rule can be set to medium) and ignores more than an
# operator expects (`hosts`, `mcp_tools`, `read_only` are not merged). The lint reports both. Findings name the overlay section and the rule or
# invariant id or list index, never the pattern or host text: an overlay holds private host names and `jade check` output is shared.
RISK_OVERLAY_KEYS = frozenset({"schema_version", "environments", "rules_prepend", "rules_override"})
SAFETY_OVERLAY_KEYS = frozenset({"schema_version", "invariants_append"})
_INVARIANT_PATTERNS = ("block", "block_quoted", "content_block", "scan_content_paths")
_SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")


def _pattern_problem(p) -> str | None:
    if not isinstance(p, str):
        return "is not a string"
    if not p.strip():
        return "is empty, so it matches every command"
    try:
        rx = re.compile(p)
    except re.error as e:
        return f"is not a valid regular expression (position {e.pos})"
    return "matches the empty string, so it matches every command" if rx.search("") else None


def _label(kind: str, ident, i: int, shape: str) -> str:
    """`rules_prepend R-L01`, or the list index when the id is not of the shape the loader demands (it may be anything the operator typed)."""
    return f"{kind} {ident}" if isinstance(ident, str) and re.fullmatch(shape, ident) else f"{kind}[{i}]"


def lint_overlay(kind: str, overlay: Path, base: Path | None = None) -> list[str]:
    """Problems in one overlay file (`kind` is `risk` or `safety`), as short sentences without any overlay text. `base` is the shipped policy file
    the level floor is measured against."""
    try:
        ov = _read(Path(overlay))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return ["cannot be read or parsed as YAML"]
    if not isinstance(ov, dict):
        return ["is not a YAML mapping"]
    out: list[str] = []
    allowed = RISK_OVERLAY_KEYS if kind == "risk" else SAFETY_OVERLAY_KEYS
    for i, key in enumerate(k for k in ov if k not in allowed):
        name = key if isinstance(key, str) and _SAFE_NAME.fullmatch(key) else f"#{i}"
        out.append(f"top-level key `{name}` is not read from an overlay (the loader merges only {', '.join(sorted(allowed))})")
    if kind == "safety":
        invs = ov.get("invariants_append") or []
        if not isinstance(invs, list):
            return out + ["invariants_append is not a list"]
        for i, inv in enumerate(invs):
            if not isinstance(inv, dict):
                out.append(f"invariants_append[{i}] is not a mapping")
                continue
            where = _label("invariants_append", inv.get("id"), i, r"SL-\d{2}")
            present = [f for f in _INVARIANT_PATTERNS[:3] if f in inv]
            if not present:
                out.append(f"{where} has no block pattern, so it blocks nothing")
            for f in (f for f in _INVARIANT_PATTERNS if f in inv):
                if (why := _pattern_problem(inv[f])):
                    out.append(f"{where}: `{f}` {why}")
        return out
    env = ov.get("environments") or {}
    if not isinstance(env, dict):
        out.append("environments is not a mapping of lists")
    for key, items in (env.items() if isinstance(env, dict) else []):
        name = key if isinstance(key, str) and _SAFE_NAME.fullmatch(key) else "#"
        if not isinstance(items, list):
            out.append(f"environments.{name} is not a list (a string would be split into one-character entries)")
            continue
        for i, item in enumerate(items):
            if name == "staging_markers":
                why = _pattern_problem(item)
            else:
                why = None if isinstance(item, str) and item else "is empty or not a string, so it matches nothing"
            if why:
                out.append(f"environments.{name}[{i}] {why}")
    prepend = ov.get("rules_prepend") or []
    if not isinstance(prepend, list):
        out.append("rules_prepend is not a list")
        prepend = []
    for i, rule in enumerate(prepend):
        if not isinstance(rule, dict):
            out.append(f"rules_prepend[{i}] is not a mapping")
            continue
        where = _label("rules_prepend", rule.get("id"), i, r"R-L\d{2}")
        if not isinstance(rule.get("level"), str) or rule.get("level") not in ORDER:
            out.append(f"{where}: `level` is not one of {', '.join(ORDER)}")
        if (why := _pattern_problem(rule.get("match"))):
            out.append(f"{where}: `match` {why}")
    shipped: dict[str, str] = {}
    try:
        shipped = {r["id"]: r["level"] for r in _read(base or POLICIES / "risk.yaml").get("rules", [])}
    except (OSError, UnicodeDecodeError, yaml.YAMLError, KeyError, TypeError, AttributeError):
        pass   # no shipped file to measure against: the level floor is not checked, the rest still is
    overrides = ov.get("rules_override") or {}
    if not isinstance(overrides, dict):
        out.append("rules_override is not a mapping")
    for i, (rid, change) in enumerate(overrides.items() if isinstance(overrides, dict) else []):
        change = change if isinstance(change, dict) else {}
        if rid not in shipped:
            out.append(f"rules_override[{i}] names a rule the shipped policy does not have" if shipped else f"rules_override[{i}]")
            continue
        new = change.get("level")
        new = new if isinstance(new, str) else None
        if "level" in change and new not in ORDER:
            out.append(f"rules_override {rid}: `level` is not one of {', '.join(ORDER)}")
        elif new in ORDER and ORDER[shipped[rid]] <= ORDER["high"] and ORDER[new] > ORDER[shipped[rid]]:
            out.append(f"rules_override {rid} lowers a shipped {shipped[rid]} rule to {new}; an overlay may only raise a shipped critical or high rule")
    return out
