"""Preflight (policies/routing.yaml): suitability decides the route, risk decides the rigor."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEEP_HINTS = re.compile(r"\b(design|architecture|architect|refactor plan|strategy for|how should (i|we)|what should (i|we)|roadmap|trade-?offs? between|best approach)\b", re.I)
PARAPHRASE = re.compile(r"^\s*(i (ran|checked|verified|confirmed|think|believe)|it (works|passes|should)|looks (good|fine)|everything (works|passes)|done\b)", re.I)


@dataclass
class RouteDecision:
    route: str
    reason: str
    checks: list[str] = field(default_factory=list)


def preflight(*, claim: str = "", evidence: str = "", options: list[str] | None = None, command: str | None = None, risk: str = "low",
              about_to_claim_done: bool = False, deterministic: bool = False, primitive: str | None = None) -> RouteDecision:
    checks = []
    if deterministic:
        return RouteDecision("FAST", "PF-01: a deterministic check answers this; use `jade fast` and no model call", ["PF-01"])
    checks.append("PF-01")
    if command:
        from . import policy

        a = policy.assess("Bash", {"command": command})
        if a.blocked:
            return RouteDecision("ESCALATE", f"PF-02: safety invariant {', '.join(a.blocked)} matches; blocked whatever the evidence says", checks + ["PF-02"])
        if a.level in ("high", "critical"):
            risk = a.level if risk in ("low", "medium") else risk
    checks.append("PF-02")
    primitive = primitive or ("choice" if options else "noul")
    if primitive == "noul" and (not evidence.strip() or (len(evidence) < 200 and PARAPHRASE.search(evidence))):
        return RouteDecision("NEEDS_EVIDENCE", "PF-03: no raw evidence attached (tool output, diff or document excerpt); a summary is not evidence", checks + ["PF-03"])
    checks.append("PF-03")
    if options is not None and len({o.split('=')[0].strip() for o in options if o.strip()}) < 2:
        return RouteDecision("NEEDS_REVISION", "PF-05: fewer than two eligible options; a one-option choice returns confidence 1.0 and means nothing", checks + ["PF-05"])
    checks.append("PF-05")
    if DEEP_HINTS.search(claim) or claim.count("?") > 1 or len(claim) > 600:
        return RouteDecision("DEEP", "PF-06: not atomic or bounded; decompose with the reasoning model, then ask atomic questions", checks + ["PF-06"])
    checks.append("PF-06")
    if risk == "low" and not about_to_claim_done:
        return RouteDecision("FAST", "PF-08: low risk and no completion claim pending; evaluation is not worth a call yet", checks + ["PF-08"])
    return RouteDecision("JEV", f"default route: one batched request (risk {risk})", checks + ["PF-08"])


def fast_check(kind: str, args: list[str]) -> tuple[bool, str]:
    if kind == "exit-code":
        if len(args) != 2:
            raise SystemExit("usage: jade fast exit-code EXPECTED ACTUAL")
        return args[0].strip() == args[1].strip(), f"expected {args[0]}, actual {args[1]}"
    if kind == "file-exists":
        missing = [a for a in args if not Path(a).expanduser().exists()]
        return not missing, "all exist" if not missing else f"missing: {', '.join(missing)}"
    if len(args) != 2:
        raise SystemExit("usage: jade fast regex PATTERN FILE")
    text = Path(args[1]).expanduser().read_text(errors="replace")
    m = re.search(args[0], text, re.M)
    return bool(m), f"matched at offset {m.start()}" if m else "no match"
