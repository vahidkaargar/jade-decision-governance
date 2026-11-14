"""What the human confirmed, used in their place (docs/12, plan v3, PR-6).

Every answer the human gives to an ask (chat or dialog) is stored as a `hint` in `human_answers`; a hint decides nothing. `jade review confirm`, run by the
human in a terminal, turns allow answers into `strong` rows. At level low or medium the gate looks here before it asks: a call that matches a strong allow row
runs without a question, through a decision walked to EXECUTING (so SI-07 still holds an unknown outcome), and the row's last-use time moves forward.

Two keys per answer:
- `exact_key`: the call's hold key (the canonical gated segments, independent of quoting, prefixes and cwd) with the fingerprints the gate computes (git HEAD of a
  push, the digest of written content). The same call in the same state.
- `class_key`: the kind of call: the winning rule and the sorted set of gated segment keys, with the PR number of `gh pr <verb> N` dropped. Only a call that is
  safe to generalise has one; the rest is exact-only. `eligibility` is the whole decision.

Never remembered (always asked): a human-only class (docs/12 section 4), the fetch-and-exec and egress rules (R-21, R-22), a call the gate blocks, and a call with a
secret-shaped value in it. Exact-only: R-14, M-05, M-07, an inline program (`-c`, `-e`, heredoc, here-string, process substitution), a call the gate could not
read (blind labels), a git push or merge (the fingerprint carries the head commit), a merge into the default branch, and every write or MCP call (content is part of the key).

Lifetime: a row is valid while its last human confirm or its last use is within 30 days, and never more than 180 days after the confirm.
"""

from __future__ import annotations

import re
from datetime import timedelta

from .util import canonical, now, sha256, ts

USE_DAYS = 30
CAP_DAYS = 180
NEAR_CAP_DAYS = 14
NEVER_RULES = frozenset({"R-21", "R-22"})
EXACT_ONLY_RULES = frozenset({"R-14", "M-05", "M-07"})
_PR_NUMBER = re.compile(r"^((?:gh pr|glab mr) [\w-]+) \d+(?= |$)")
_MERGE = re.compile(r"^(?:gh pr merge|glab mr merge|git merge)(?= |$)")
_INLINE = re.compile(r"(?:^|\s)-[A-Za-z]*[ce](?=\s|\Z)|<<|<\(")


def normalise_segment(key: str) -> str:
    """One segment key with the volatile part removed: `gh pr edit 12 --add-label x` and `gh pr edit 98 --add-label x` are the same kind of call."""
    return _PR_NUMBER.sub(r"\1 N", key)


def exact_key(hold_key: str, fingerprints: dict | str | None) -> str:
    """The call in the state it was answered in: the hold key and the fingerprints the gate computed (a pushed HEAD, a content digest)."""
    fp = fingerprints if isinstance(fingerprints, str) else canonical(fingerprints or {})
    return sha256(canonical({"hold": hold_key, "fp": fp}))


def eligibility(a, tool: str, tool_input: dict | None, fp: dict | None) -> str:
    """`never`, `exact` or `class`: how far an answer to this call may be generalised."""
    from . import policy

    if a.ho_ids or a.blocked or a.rule_id in NEVER_RULES:
        return "never"
    if not policy.runs_shell(tool, tool_input or {}):
        return "exact"
    if a.rule_id in EXACT_ONLY_RULES or set(getattr(a, "seg_rules", []) or []) & EXACT_ONLY_RULES or a.blind or a.rule_id in ("R-31", "R-32"):
        return "exact"
    if fp or _INLINE.search(a.subject or ""):
        return "exact"
    if any(_MERGE.match(k) for k in getattr(a, "seg_keys", []) or []) or not getattr(a, "seg_keys", None):
        return "exact"
    return "class"


def class_key(a) -> str:
    return sha256(canonical({"rule": a.rule_id, "segments": sorted({normalise_segment(k) for k in a.seg_keys})}))


def keys(a, tool: str, tool_input: dict | None, fp: dict | None) -> tuple[str, str | None, str]:
    """(exact key, class key or None, eligibility) of a call."""
    el = eligibility(a, tool, tool_input, fp)
    return exact_key(a.hold_key or a.plan_hash, fp), (class_key(a) if el == "class" else None), el


def _valid_since() -> tuple[str, str]:
    return ts(now() - timedelta(days=CAP_DAYS)), ts(now() - timedelta(days=USE_DAYS))


def lookup(db, a, tool: str, tool_input: dict | None, fp: dict | None):
    """The strong allow row that covers this call, or None."""
    exact, cls, el = keys(a, tool, tool_input, fp)
    if el == "never":
        return None
    cap, use = _valid_since()
    sql = ("SELECT * FROM human_answers WHERE strength='strong' AND answer='allow' AND confirmed_ts>=? AND COALESCE(last_used_ts, confirmed_ts)>=? "
           "AND COALESCE(source,'')<>'test' AND (exact_key=?" + (" OR class_key=?" if cls else "") + ") ORDER BY confirmed_ts DESC, rowid DESC LIMIT 1")
    args = [cap, use, exact] + ([cls] if cls else [])
    return db.con.execute(sql, args).fetchone()


def use(db, answer_id: str) -> None:
    db.con.execute("UPDATE human_answers SET last_used_ts=? WHERE answer_id=?", (ts(), answer_id))


def step(db, mode: str, a, tool: str, event: dict, fp: dict, cwd: str):
    """The memory branch of the gate: a HookResult that lets the call run on a confirmed answer, or None (ask as before)."""
    from . import askchat, paths, permits, state_machine
    from .hooks import HookResult

    if mode != "enforce" or paths.level() not in ("low", "medium") or not event.get("session_id"):
        return None
    tin = event.get("tool_input") or {}
    if permits.make_context(tool, a, event, cwd=cwd).get("redacted"):
        return None
    try:
        with db.tx():
            row = lookup(db, a, tool, tin if isinstance(tin, dict) else {}, fp)
            if row is None:
                return None
            did = askchat.run_decision(db, a, tool, cwd, event["session_id"], row["answer_id"][-12:], "memory")
            use(db, row["answer_id"])
            db.event(row["answer_id"], "memory_allowed", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"decision_id": did, "channel": row["channel"]})
    except state_machine.IllegalTransition:
        return None   # the decision could not advance: ask as before
    return HookResult(0, f"JADE: allowed from your confirmed answer {row['answer_id']} (rule {a.rule_id}). `jade knows list` shows what is remembered; `jade knows rm {row['answer_id']}` forgets it.")
