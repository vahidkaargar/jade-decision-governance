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
    for key, extra in (ov.get("environments") or {}).items():
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
    return safety


def blocked_by(safety: dict, command: str) -> list[str]:
    return [i["id"] for i in safety["invariants"] if "_re" in i and i["_re"].search(command)]
