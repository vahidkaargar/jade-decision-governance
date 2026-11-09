"""Map a raw answer to ACCEPT / ABSTAIN / REJECT for its calibration group (ADR-07, policies/escalation.yaml).

A raw probability is an input to a threshold policy, never permission. Human-promoted adjustments live in
<home>/calibration/current.json keyed by calibration group; deleting that file restores the defaults.
"""

from __future__ import annotations

import json
import math
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


def _calibration() -> dict:
    try:
        cur = json.loads((paths.home() / "calibration" / "current.json").read_text())
    except (OSError, ValueError):
        return {}
    return cur if isinstance(cur, dict) else {}


def _adjusted(group: str) -> dict:
    """The human-promoted band of one group. An answer with no model string belongs to no calibrated group: it is judged at the
    strictest accept any group of the same family and question version was promoted to, never at looser default bands."""
    cur = _calibration()
    if not group.endswith("/unknown"):
        adj = cur.get(group, {})
        return adj if isinstance(adj, dict) else {}
    prefix = group[: -len("unknown")]
    accepts = [v["accept"] for g, v in cur.items() if g.startswith(prefix) and isinstance(v, dict) and isinstance(v.get("accept"), int | float)]
    return {"accept": max(accepts)} if accepts else {}


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


def _finite_probability(x, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x) or not 0 <= x <= 1:
        raise ValueError(f"{what} is not a probability in [0, 1]")
    return float(x)


def judge(answer: dict, family: str, risk: str = "low", version: int = 1, resolved_model: str | None = None, *,
          primitive: str | None = None, no_match: str | None = None) -> Verdict:
    """Judge one answer. `primitive` is what was asked: the answer's own `type` never decides which judgement applies, and an answer
    of another type, an unknown type or a probability outside [0, 1] raises ValueError (the caller treats it as MALFORMED, exit 5).
    `no_match` is the question's no-match option: a confident "none of these" selected nothing, so it is ABSTAIN, never a yes."""
    prim = answer.get("type")
    if prim not in ("noul", "choice", "score") or (primitive is not None and prim != primitive):
        raise ValueError(f"cannot judge a {prim!r} answer to a {primitive or 'typed'} question")
    group = group_of(family, version, resolved_model)
    accept, reject = bands(prim, family, risk, group)
    if prim == "noul":
        p = _finite_probability(answer.get("noul"), "noul")
        v = "ACCEPT" if p >= accept else "REJECT" if p <= reject else "ABSTAIN"
        return Verdict(v, EXIT[v], p, accept, reject, group, f"P(yes)={p:.2f}, accept>={accept:.2f}, reject<={reject:.2f}")
    if prim == "choice":
        probs = answer.get("probabilities")
        if not isinstance(probs, dict) or answer.get("choice") not in probs:
            raise ValueError("choice answer names no option of its own probabilities")
        top = _finite_probability(probs[answer["choice"]], "choice probability")
        if no_match and answer["choice"] == no_match:
            return Verdict("ABSTAIN", EXIT["ABSTAIN"], top, accept, None, group, f"'{no_match}' is the top choice: no option fits (p={top:.2f})")
        v = "ACCEPT" if top >= accept else "ABSTAIN"
        return Verdict(v, EXIT[v], top, accept, None, group, f"top option p={top:.2f}, accept>={accept:.2f}")
    return Verdict("ACCEPT", 0, float(answer.get("score", 0.0)), None, None, group, "score ranks, never gates")
