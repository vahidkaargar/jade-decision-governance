"""Risk assessment of a proposed tool call against policies/risk.yaml and policies/safety.yaml.

`policies.loader` holds the merge rules and raw first-match classification. This module adds what a
real shell command needs: it classifies per simple command after `shellnorm` has resolved escapes, quoting,
git global options and hidden command text, ignores heredoc bodies and quoted prose that no shell would run,
and treats read-only commands as low (see the `read_only` list in risk.yaml).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

from policies import loader

from . import paths, shellnorm
from .util import LEVELS, canonical, sha256

NULL_REDIRECT = shellnorm.NULL_REDIRECT
SPLIT = shellnorm.SPLIT
PREFIX = shellnorm.PREFIX
WRITE_REDIRECT = re.compile(r">>?\s*\S")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
UNSAFE_GIT = "git:unsafe-config "  # marker prefix R-15 matches; carried only in the text a rule sees


def is_mcp(tool: str) -> bool:
    return tool.startswith("mcp__")


def mcp_command(tool_input: dict) -> str | None:
    for key in risk().get("mcp_command_keys", ["command", "cmd", "script"]):
        if isinstance(tool_input.get(key), str) and tool_input[key].strip():
            return tool_input[key]
    return None


@dataclass
class Assessment:
    level: str
    rule_id: str
    rationale: str
    environment: str = "local"
    reversibility: str = "reversible"
    blast_radius: str = "file"
    data_sensitivity: str = "none"
    blocked: list[str] = field(default_factory=list)
    plan_hash: str = ""
    subject: str = ""


@lru_cache(maxsize=1)
def risk() -> dict:
    r = loader.load_risk(overlay=paths.overlay("risk"), base=paths.policy_dir() / "risk.yaml")
    r["_read_only"] = [re.compile(p) for p in r.get("read_only", [])]
    return r


@lru_cache(maxsize=1)
def safety() -> dict:
    return loader.load_safety(overlay=paths.overlay("safety"), base=paths.policy_dir() / "safety.yaml")


def reset_cache() -> None:
    risk.cache_clear()
    safety.cache_clear()


skeleton = shellnorm.skeleton
segments = shellnorm.segments


def _analyse(seg: str) -> tuple[str, list[shellnorm.GitCall]]:
    """The segment without launcher prefixes (sudo, env, timeout ...) and with git global options removed."""
    return shellnorm.git_calls(PREFIX.sub("", seg, count=1).strip())


def _bare(seg: str) -> str:
    return _analyse(seg)[0]


def git_calls(seg: str) -> list[shellnorm.GitCall]:
    return _analyse(seg)[1]


def _pathfold(text: str) -> str:
    """Lexical path clean-up (`//`, `/./`, `x/../`) so a protected path matches however it is spelled."""
    t = re.sub(r"/{2,}", "/", text)
    t = re.sub(r"/(?:\./)+", "/", t)
    while (u := re.sub(r"/[^/\s]+/\.\./", "/", t)) != t:
        t = u
    return t


def normalise_path(path: str, cwd: str = "") -> str:
    """Absolute, symlink-resolved (deepest existing ancestor), `~`-expanded form of a write target."""
    if not path:
        return ""
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        try:
            p = os.path.join(cwd or os.getcwd(), p)
        except OSError:
            return ""
    return re.sub(r"/{2,}", "/", os.path.realpath(p))


def is_read_only(seg: str) -> bool:
    if WRITE_REDIRECT.search(seg):
        return False
    bare, calls = _analyse(seg)
    if any(c.danger for c in calls):
        return False
    return any(p.search(bare) for p in risk()["_read_only"])


def plan_hash(tool: str, subject: str, cwd: str = "") -> str:
    """Hash of the exact thing a permit authorizes: tool, normalised command or path, and working directory."""
    return sha256(canonical({"tool": tool, "subject": re.sub(r"\s+", " ", subject).strip(), "cwd": cwd or ""}))


def _first_match(text: str) -> dict:
    for r in risk()["rules"]:
        if r["_re"].search(text):
            return r
    return risk()["rules"][-1]


def assess(tool: str, tool_input: dict, cwd: str = "") -> Assessment:
    """Classify one tool call. Raises on malformed policy; callers decide how to fail."""
    if tool in WRITE_TOOLS:
        subject = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        norm = normalise_path(subject, cwd)
        cands = [subject, *([norm] if norm and norm != subject else [])]
        rule = max((_first_match(c) for c in cands), key=lambda r: LEVELS[r["level"]])
        envs = [loader.environment_of(risk(), c) for c in cands]
        env = "production" if "production" in envs else "staging" if "staging" in envs else "local"
        a = _from_rule(rule, env)
        if env == "production" and LEVELS[a.level] < LEVELS["high"]:
            a.level, a.rule_id, a.rationale = "high", "R-ENV", "Write under a production path"
        a.plan_hash, a.subject = plan_hash(tool, subject, ""), subject
        return a
    if is_mcp(tool) and mcp_command(tool_input) is None:
        subject = canonical(tool_input)
        rule = next((m for m in risk().get("mcp_tools", []) if re.search(m["match"], tool)), None)
        if rule is None:
            a = Assessment(level="low", rule_id="R-99", rationale="MCP tool with no mutation rule", environment="local", blast_radius="service")
        else:
            a = Assessment(level=rule["level"], rule_id=rule["id"], rationale=rule["rationale"], environment="production" if rule["level"] == "critical" else "local",
                           reversibility=rule.get("reversibility", "reversible"), blast_radius=rule.get("blast_radius", "service"))
        a.plan_hash, a.subject = plan_hash(tool, subject, ""), f"{tool} {subject}"
        return a
    subject = mcp_command(tool_input) or str(tool_input.get("command") or "") if is_mcp(tool) else str(tool_input.get("command") or "")
    best: dict | None = None
    active: list[str] = []
    for seg in segments(subject):
        if is_read_only(seg):
            continue
        active.append(seg)
        bare, calls = _analyse(seg)
        texts = [seg, bare, _pathfold(bare), *([UNSAFE_GIT + bare] if any(c.danger for c in calls) else [])]
        for text in dict.fromkeys(texts):
            rule = _first_match(text)
            if best is None or LEVELS[rule["level"]] > LEVELS[best["level"]]:
                best = rule
    best = best or risk()["rules"][-1]
    a = _from_rule(best, loader.environment_of(risk(), subject))
    for inv in safety()["invariants"]:
        if "_re" not in inv:
            continue
        haystacks = [subject] if inv.get("match_on") == "raw" else active + [_bare(s) for s in active]
        if any(inv["_re"].search(h) for h in haystacks):
            a.blocked.append(inv["id"])
    a.plan_hash, a.subject = plan_hash(tool, subject, cwd), subject
    return a


def _from_rule(rule: dict, env: str) -> Assessment:
    return Assessment(level=rule["level"], rule_id=rule["id"], rationale=rule.get("rationale", ""), environment=env,
                      reversibility=rule.get("reversibility", "reversible"), blast_radius=rule.get("blast_radius", "file"),
                      data_sensitivity=rule.get("data_sensitivity", "none"))


def permit_floor() -> str:
    return risk().get("permit_required_at", "high")
