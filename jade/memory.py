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
MERGE = re.compile(r"^(?:gh pr merge|glab mr merge|git merge)(?= |$)")
INLINE = re.compile(r"(?:^|\s)-[A-Za-z]*[ce](?=\s|\Z)|<<|<\(")


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
    if fp or INLINE.search(a.subject or ""):
        return "exact"
    if any(MERGE.match(k) for k in getattr(a, "seg_keys", []) or []) or not getattr(a, "seg_keys", None):
        return "exact"
    return "class"


def class_key(a) -> str:
    return sha256(canonical({"rule": a.rule_id, "segments": sorted({normalise_segment(k) for k in a.seg_keys})}))


def keys(a, tool: str, tool_input: dict | None, fp: dict | None) -> tuple[str, str | None, str]:
    """(exact key, class key or None, eligibility) of a call."""
    el = eligibility(a, tool, tool_input, fp)
    return exact_key(a.hold_key or a.plan_hash, fp), (class_key(a) if el == "class" else None), el


def valid_since() -> tuple[str, str]:
    return ts(now() - timedelta(days=CAP_DAYS)), ts(now() - timedelta(days=USE_DAYS))


def _col(r, name: str):
    """A column of an answer row (a sqlite3.Row or a dict); None when the row has no such column."""
    return r[name] if name in r.keys() else None


def stored_keys(r) -> set[tuple[str, str]]:
    """Every key an answer row stores, typed: ("exact", k), ("class", k), ("scope", k). A deny row stores the exact key and, for a class call, the class key."""
    return {(kind, k) for kind, k in (("exact", _col(r, "exact_key")), ("class", _col(r, "class_key")), ("scope", _col(r, "scope_key"))) if k}


def call_keys(exact: str, cls: str | None, scope_keys) -> set[tuple[str, str]]:
    """The typed keys of the call in front of the gate: what a row must share to cover it, and what a deny must share to take a row out for it."""
    return {("exact", exact), *((("class", cls),) if cls else ()), *(("scope", k) for k in scope_keys)}


def covered_keys(r) -> set[tuple[str, str]]:
    """The keys that every call an allow row covers has: a newer deny that shares one of them takes the row out for all of those calls, so the row decides nothing.
    A `kind_any` scope key is the class key. Every call a `kind_project` scope covers has the class key of the row's family (`family_key`). A `merge_project` scope
    covers calls with other exact keys and no class key, so a deny of one of them leaves the scope for the others. A class row covers the calls of its class key, an
    exact row only its own call."""
    kind, scope = _col(r, "scope_kind"), _col(r, "scope_key")
    if scope:
        extra = _col(r, "class_key") if kind == "kind_any" else _col(r, "family_key") if kind == "kind_project" else None
        return {("scope", scope), *((("class", extra),) if extra else ())}
    if _col(r, "class_key"):
        return {("class", r["class_key"])}
    return {("exact", r["exact_key"])}


def standing(r, since: tuple[str, str] | None = None) -> bool:
    """A confirmed (strong) allow, live (not a test row), confirmed within CAP_DAYS and confirmed or used within USE_DAYS."""
    cap, use = since or valid_since()
    confirmed = _col(r, "confirmed_ts")
    used = _col(r, "last_used_ts")
    return (r["answer"] == "allow" and r["strength"] == "strong" and (_col(r, "source") or "") != "test" and bool(confirmed) and confirmed >= cap
            and (confirmed if used is None else used) >= use)   # COALESCE(last_used_ts, confirmed_ts): only NULL falls back to the confirm


# The deny rows that take an allow out: any strength the human gave (hint or strong), not a test row. DENY_SQL selects the same rows in SQL.
DENY_SQL = "answer='deny' AND strength IN ('hint','strong') AND COALESCE(source,'')<>'test'"
ALLOW_SQL = "answer='allow' AND strength='strong' AND COALESCE(source,'')<>'test'"   # the rows `standing` can pass; it still checks the dates


def is_deny(d) -> bool:
    return d["answer"] == "deny" and d["strength"] in ("hint", "strong") and (_col(d, "source") or "") != "test"


def deny_index(denies) -> dict[tuple[str, str], str]:
    """For each typed key, the time of the newest deny that stores it. Built once, so that `taken_out` is one dict lookup per key."""
    idx: dict[tuple[str, str], str] = {}
    for d in denies:
        if is_deny(d):
            for k in stored_keys(d):
                if d["ts"] > idx.get(k, ""):
                    idx[k] = d["ts"]
    return idx


def taken_out(r, denies, keys: set[tuple[str, str]]) -> bool:
    """A deny (`is_deny`) newer than the allow `r` that shares one of `keys`: the owner's newest answer wins. `denies` is a `deny_index` or the deny rows."""
    idx = denies if isinstance(denies, dict) else deny_index(denies)
    return any(idx.get(k, "") > r["ts"] for k in keys)


def honoured(r, denies, keys: set[tuple[str, str]], since: tuple[str, str] | None = None) -> bool:
    """Does the allow row `r` still decide a call with these typed keys? The one test `lookup` (keys of the call in front of the gate), the `jade knows edit`
    template and the past answers sent to Jev (`covered_keys(r)`: is there a call it still decides) all use."""
    return standing(r, since) and not taken_out(r, denies, keys)


def honoured_allows(db, where: str, args, since: tuple[str, str] | None = None, limit: int | None = None) -> list:
    """The strong allow rows that match `where` (newest confirm first) and that `honoured` with their `covered_keys` still counts: the denies read are only those
    that share a key with one of the allows (exact, class or family, scope), so the work grows with the rows of the keys in question, not with the ledger."""
    since = since or valid_since()
    allows = db.con.execute(f"SELECT * FROM human_answers WHERE {ALLOW_SQL} AND confirmed_ts>=? AND ({where}) ORDER BY confirmed_ts DESC, rowid DESC",  # noqa: S608
                            [since[0], *args]).fetchall()
    allows = [r for r in allows if standing(r, since)]
    if not allows:
        return []
    exact = sorted({r["exact_key"] for r in allows})
    cls = sorted({k for r in allows for k in (_col(r, "class_key"), _col(r, "family_key")) if k})
    scope = sorted({r["scope_key"] for r in allows if _col(r, "scope_key")})
    idx = deny_index(_rows_by_keys(db, DENY_SQL, exact, cls, scope))
    out = []
    for r in allows:
        if not taken_out(r, idx, covered_keys(r)):
            out.append(r)
            if limit is not None and len(out) >= limit:
                break
    return out


_CHUNK = 300


def _rows_by_keys(db, where: str, exact, cls, scope) -> list:
    """Rows matching `where` that store one of these keys, typed by column (exact_key, class_key, scope_key); queried in chunks to stay under SQLite's variable cap."""
    out, seen = [], set()
    for col, ks in (("exact_key", exact), ("class_key", cls), ("scope_key", scope)):
        for i in range(0, len(ks), _CHUNK):
            part = ks[i:i + _CHUNK]
            for d in db.con.execute(f"SELECT * FROM human_answers WHERE {where} AND {col} IN ({','.join('?' * len(part))})", part):  # noqa: S608 - code-defined columns
                if d["answer_id"] not in seen:
                    seen.add(d["answer_id"])
                    out.append(d)
    return out


def lookup(db, a, tool: str, tool_input: dict | None, fp: dict | None, cwd: str = ""):
    """The strong allow row that covers this call, or None. A row matches by the exact key, the class key, or one of the scope keys of this call (jade/scopes.py:
    computed from the call in front of the gate, so a new flag or another project gives another key). `honoured` decides whether a matching row still counts:
    standing (strong, within the lifetime) and not taken out by a newer deny with one of this call's keys."""
    from . import scopes

    exact, cls, el = keys(a, tool, tool_input, fp)
    if el == "never":
        return None
    scope_keys = scopes.keys_for(a, tool, tool_input, fp, cwd)
    same = "exact_key=?" + (" OR class_key=?" if cls else "") + (f" OR scope_key IN ({','.join('?' * len(scope_keys))})" if scope_keys else "")
    args = [exact] + ([cls] if cls else []) + scope_keys
    since, ck = valid_since(), call_keys(exact, cls, scope_keys)
    allows = db.con.execute(f"SELECT * FROM human_answers WHERE {ALLOW_SQL} AND confirmed_ts>=? AND ({same}) ORDER BY confirmed_ts DESC, rowid DESC",  # noqa: S608
                            [since[0], *args]).fetchall()
    if not allows:
        return None
    idx = deny_index(db.con.execute(f"SELECT * FROM human_answers WHERE {DENY_SQL} AND ({same})", args).fetchall())  # noqa: S608
    return next((r for r in allows if honoured(r, idx, ck, since)), None)


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
            row = lookup(db, a, tool, tin if isinstance(tin, dict) else {}, fp, cwd)
            if row is None:
                return None
            did = askchat.run_decision(db, a, tool, cwd, event["session_id"], row["answer_id"][-12:], "memory")
            use(db, row["answer_id"])
            db.event(row["answer_id"], "memory_allowed", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"decision_id": did, "channel": row["channel"]})
    except state_machine.IllegalTransition:
        return None   # the decision could not advance: ask as before
    return HookResult(0, f"JADE: allowed from your confirmed answer {row['answer_id']} (rule {a.rule_id}). `jade knows list` shows what is remembered; `jade knows rm {row['answer_id']}` forgets it.")
