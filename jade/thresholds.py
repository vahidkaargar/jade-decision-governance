"""Map a raw answer to ACCEPT / ABSTAIN / REJECT for its calibration group (ADR-07, policies/escalation.yaml).

A raw probability is an input to a threshold policy, never permission. Human-promoted adjustments live in
<home>/calibration/current.json keyed by calibration group; deleting that file restores the defaults.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import yaml

from . import paths

EXIT = {"ACCEPT": 0, "ABSTAIN": 3, "REJECT": 4, "PROVIDER_UNAVAILABLE": 5, "INVALID_QUESTION": 6}


@dataclass
class Verdict:
    verdict: str
    exit_code: int
    value: float | None
    accept: float | None
    reject: float | None
    group: str
    reason: str = ""


@lru_cache(maxsize=1)
def escalation() -> dict:
    return yaml.safe_load((paths.policy_dir() / "escalation.yaml").read_text())


def group_of(family: str, version: int, resolved_model: str | None) -> str:
    return f"{family}/v{version}/{(resolved_model or 'unknown').split('/')[-1]}"


def _adjusted(group: str) -> dict:
    try:
        return json.loads((paths.home() / "calibration" / "current.json").read_text()).get(group, {})
    except (OSError, ValueError):
        return {}


def bands(primitive: str, family: str, risk: str = "low", group: str = "") -> tuple[float | None, float | None]:
    esc = escalation()
    fam = esc.get("families", {}).get(family, {})
    base = {**esc["defaults"].get(primitive, {}), **fam.get(primitive, {})}
    if primitive == "noul":
        accept, reject = base.get("accept", 0.70), base.get("reject", 0.30)
    elif primitive == "choice":
        accept, reject = base.get("accept_top_p", 0.70), None
    else:
        return None, None
    adj = _adjusted(group) if group else {}
    accept, reject = adj.get("accept", accept), adj.get("reject", reject)
    by_risk = fam.get("by_risk", {}).get(risk, {})
    if "accept" in by_risk:  # risk can only make acceptance stricter
        accept = max(accept, by_risk["accept"])
    return accept, reject


def judge(answer: dict, family: str, risk: str = "low", version: int = 1, resolved_model: str | None = None) -> Verdict:
    prim = answer.get("type")
    group = group_of(family, version, resolved_model)
    accept, reject = bands(prim, family, risk, group)
    if prim == "noul":
        p = float(answer["noul"])
        v = "ACCEPT" if p >= accept else "REJECT" if p <= reject else "ABSTAIN"
        return Verdict(v, EXIT[v], p, accept, reject, group, f"P(yes)={p:.2f}, accept>={accept:.2f}, reject<={reject:.2f}")
    if prim == "choice":
        top = float(answer.get("probabilities", {}).get(answer.get("choice"), 0.0))
        v = "ACCEPT" if top >= accept else "ABSTAIN"
        return Verdict(v, EXIT[v], top, accept, None, group, f"top option p={top:.2f}, accept>={accept:.2f}")
    return Verdict("ACCEPT", 0, float(answer.get("score", 0.0)), None, None, group, "score ranks, never gates")
