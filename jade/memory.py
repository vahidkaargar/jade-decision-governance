"""What the human confirmed, used in their place (docs/12, plan v3, PR-6).

Every answer the human gives to an ask (chat or dialog) is stored as a `hint` in `human_answers`; a hint decides nothing. `jade review confirm`, run by the
human in a terminal, turns allow answers into `strong` rows. At level low or medium the gate looks here before it asks: a call that matches a strong allow row
runs without a question, through a decision walked to EXECUTING (so SI-07 still holds an unknown outcome), and the row's last-use time moves forward.

Two keys per answer:
- `exact_key`: the call's hold key (the canonical gated segments, independent of quoting, prefixes and cwd) with the fingerprints the gate computes (git HEAD of a
  push, the digest of written content), the target and the place, and the text the hold key loses (a quoted word, a substitution, a here-doc body: `_quoted`), in order. The same call in the same state.
- `class_key`: the kind of call: the winning rule and the sorted set of gated segment keys, with the PR number of `gh pr <verb> N` dropped. Only a call that is
  safe to generalise has one; the rest is exact-only. `eligibility` is the whole decision.

Never remembered (always asked): a human-only class (docs/12 section 4), the fetch-and-exec and egress rules (R-21, R-22) and the rules that guard the agent's files
(R-13, R-18), as the top rule or as the rule of any segment, a call the gate blocks, and a call with a secret-shaped value in it. Exact-only: R-14, M-05, M-07, an inline program (`-c`, `-e`, heredoc, here-string, process substitution), a call the gate could not
read (blind labels), a git push or merge (the fingerprint carries the head commit), a merge into the default branch, `launchctl bootout`, `unload` and `remove` (W-137), a segment key that still has a variable word, a call that reads a variable it sets itself, a `$'..'` word, and every write or MCP call (content is part of the key).

Lifetime: a row is valid while its last human confirm or its last use is within 30 days, and never more than 180 days after the confirm.
Evidence (V6-29, `jade/audit.py`): a row that has run silently many times (60) with no mark, or for 90 days after its last evidence, stops deciding until the human marks
one of its runs right or confirms again; a repeat is not evidence. Every call that runs from a row is drawn for the human's check, and a wrong check pauses its scope.
Both only add questions.
"""

from __future__ import annotations

import functools
import os
import re
from datetime import timedelta

from .util import canonical, now, sha256, ts

USE_DAYS = 30
CAP_DAYS = 180
NEAR_CAP_DAYS = 14
NEVER_RULES = frozenset({"R-21", "R-22", "R-13", "R-18"})   # R-13, R-18: what guards the agent and the files it reads its instructions from (as in proxy.NEVER_RULES)
EXACT_ONLY_RULES = frozenset({"R-14", "M-05", "M-07"})
_PLAIN_WORD = re.compile(r"[\w./:@=+,%-]*")   # a quoted word of these characters only is in the segment key as it is
_QUOTE_MARK = re.compile(r"""[\\'"`#<]""")   # where `_quoted_form` stops: a backslash, a quote mark, a backtick, a comment, a possible here-doc
_DQ_BODY = re.compile(r'[^"\\]*(?:\\.[^"\\]*)*', re.S)   # the text of a double-quoted word, up to its closing mark (a backslash takes the next character)
_EXPANDS = re.compile(r"[$`\\]")   # in a quoted word: what a double mark expands and a single mark does not (a `~` is not expanded inside any quotes)
_BLANKS = re.compile(r"\s+")
_LOST = re.compile(r'""|[\x01-\x03\x06]|\$X|/dev/fd/X|<<HEREDOC')   # what a segment key shows in place of text it does not keep: a quoted word with a space, a quoted syntax character, a substitution, a here-doc
_ENV_WORD = re.compile(r"(?<![\w.-])[A-Za-z_]\w*=\S*")   # a NAME=value word: an env prefix, `env X=1 cmd`, `export X=1;` (it may also match inside an argument: that only asks more)
_CD_WORD = re.compile(r"(?:^|[;&|(`{]\s*)(?:cd|pushd|popd)(?=[\s;&|)`}]|$)\s*(\S*)")
_LEADING_CD = re.compile(r"""^[ \t]*cd[ \t]+(?:"([^"$`\\]*)"|'([^']*)'|([^\s"'$`\\;&|()<>*?\[\]{}!#]+))[ \t]*(?:&&|;)[ \t]*""")
_GIT_C = re.compile(r"(?<![\w.-])git(?:\s+-[a-zA-Z](?:\s+\S+)?)*?\s+-C\s+(\S+)")
_SUDO_WORD = re.compile(r"(?<![\w.-])sudo(?![\w.-])")
_PR_NUMBER = re.compile(r"^((?:gh pr|glab mr) [\w-]+) \d+(?= |$)")
LAUNCHD_EXACT = re.compile(r"(?<![\w.-])launchctl (?:bootout|unload|remove)(?= |$)")   # W-137 (owner): stopping or removing a service is never remembered as a kind
KEY_VARIABLE = re.compile(r"\$[A-Za-z_{]")   # a variable word that is still in a segment key: the key stands for every value of it
VARIABLE_USE = re.compile(r"\$\{?([A-Za-z_]\w*)")   # a variable the text reads
DOLLAR_QUOTE = re.compile(r"\$'")   # a `$'..'` word: the key shows the decoded text, only the exact key keeps the spelling
MERGE = re.compile(r"^(?:gh pr merge|glab mr merge|git merge)(?= |$)")
INLINE = re.compile(r"(?:^|\s)-[A-Za-z]*[ce](?=\s|\Z)|<<|<\(")


def normalise_segment(key: str) -> str:
    """One segment key with the volatile part removed: `gh pr edit 12 --add-label x` and `gh pr edit 98 --add-label x` are the same kind of call."""
    return _PR_NUMBER.sub(r"\1 N", key)


def exact_key(hold_key: str, fingerprints: dict | str | None) -> str:
    """The call in the state it was answered in: the hold key and the fingerprints the gate computed (a pushed HEAD, a content digest)."""
    fp = fingerprints if isinstance(fingerprints, str) else canonical(fingerprints or {})
    return sha256(canonical({"hold": hold_key, "fp": fp}))


def _reads_assigned_variable(s: str) -> bool:
    """True when the text reads a variable that it sets itself (`x=--hard; git reset $x`): the segment key shows the value, so it would equal the key of the plain command."""
    return "$" in s and bool({w.partition("=")[0] for w in _ENV_WORD.findall(s)} & set(VARIABLE_USE.findall(s)))


def eligibility(a, tool: str, tool_input: dict | None, fp: dict | None) -> str:
    """`never`, `exact` or `class`: how far an answer to this call may be generalised."""
    from . import policy

    if a.ho_ids or a.blocked or a.level == "critical" or ({a.rule_id, *(getattr(a, "seg_rules", None) or [])} & NEVER_RULES):   # every segment rule, as `proxy.skip_reason` reads them (V6-06b)
        return "never"
    if not policy.runs_shell(tool, tool_input or {}):
        return "exact"
    if a.rule_id in EXACT_ONLY_RULES or set(getattr(a, "seg_rules", []) or []) & EXACT_ONLY_RULES or a.blind or a.rule_id in ("R-31", "R-32"):
        return "exact"
    if fp or INLINE.search(a.subject or "") or DOLLAR_QUOTE.search(a.subject or ""):
        return "exact"
    if _reads_assigned_variable(a.subject or "") or any(LAUNCHD_EXACT.search(k) or KEY_VARIABLE.search(k) for k in getattr(a, "seg_keys", []) or []):
        return "exact"   # W-137: launchctl bootout, unload and remove; a key with an unresolved variable (`git push origin $b`) is not one kind
    if any(MERGE.match(k) for k in getattr(a, "seg_keys", []) or []) or not getattr(a, "seg_keys", None) or policy.gh_api_merge_in(a.subject or ""):
        return "exact"   # a merge, also one made through `gh api` (V6-08 N20), is never remembered as a kind
    return "class"


def _target(a) -> dict:
    """What the segment keys drop but the call's target depends on: env assignments (`KUBECONFIG=`, `AWS_PROFILE=`, `DOCKER_HOST=`), `sudo` and a git `-C DIR`."""
    s = a.subject or ""
    return {"env": sorted(set(_ENV_WORD.findall(s))), "sudo": bool(_SUDO_WORD.search(s)), "git_c": sorted(set(_GIT_C.findall(s)))}


def _leading_cd(s: str) -> tuple[str, str] | None:
    """(path, rest) of a text that starts with the one `cd PATH &&` (or `;`) that `_where` can read, else None."""
    if (m := _LEADING_CD.match(s)) and (path := next(v for v in m.groups() if v is not None)) and not _CD_WORD.search(rest := s[m.end():]):
        return path, rest
    return None


def _where(a) -> str:
    """The directory the call runs in: the launch directory, moved by a leading `cd PATH` (`&&` or `;`) when that is the only `cd`. Another `cd` (a later one, `pushd`, a
    variable or a subshell) makes the place unreadable: the words of every `cd` are part of it, so it never equals a place that was read."""
    base, s = getattr(a, "plan_cwd", "") or "", a.subject or ""
    if lead := _leading_cd(s):
        path = lead[0]
        path = os.path.expanduser(path) if path == "~" or path.startswith("~/") else path
        return os.path.normpath(path if os.path.isabs(path) else os.path.join(base, path))
    cds = sorted(set(_CD_WORD.findall(s)))
    return f"{base}|{'|'.join(cds)}" if cds else base


def _quoted_form(s: str) -> str | None:
    """The command text in a form that keeps what the segment key loses, in order: the unquoted text with its blanks folded, and each quoted word that the segment key may
    not show whole, with its mark when the mark matters. A quoted word of plain characters (letters, digits and `./:@=+,%-`) is part of the unquoted text, because it stays
    in the segment key as it is (`git 'reset'` is `git reset`). The mark (single or double) is dropped unless the text has a `$`, a backtick or a backslash: a double
    mark expands them and a single mark does not. The text keeps its order, so two words that change places between two commands are not the same call. None when the text
    is not simple enough to read here (an unclosed quote, `$'..'`, a command substitution inside quotes, a here-doc, a comment): the caller then uses the whole text."""
    pieces: list = []
    buf: list[str] = []   # the unquoted text since the last kept word
    last = i = 0
    n = len(s)
    while m := _QUOTE_MARK.search(s, i):
        i = m.start()
        c = s[i]
        if c == "\\":
            i += 2
            continue
        start = i
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0 or (i and s[i - 1] == "$"):
                return None
            body, i = s[i + 1:j], j + 1
        elif c == '"':
            j = _DQ_BODY.match(s, i + 1).end()
            if j >= n or s[j] != '"':
                return None
            body, i = s[i + 1:j], j + 1
            if "$(" in body or "`" in body:
                return None
        elif c == "`" or s.startswith("<<", i) or (c == "#" and (i == 0 or s[i - 1] in " \t\n;&|(")):
            return None
        else:
            i += 1
            continue
        buf.append(s[last:start])
        last = i
        if _PLAIN_WORD.fullmatch(body):
            buf.append(body)
        else:
            pieces.append(_BLANKS.sub(" ", "".join(buf)))
            buf = []
            pieces.append([c if _EXPANDS.search(body) else "", body])
    buf.append(s[last:])
    pieces.append(_BLANKS.sub(" ", "".join(buf)))
    return canonical(pieces)


def _quoted(a) -> str:
    """A hash of the command text (`_quoted_form`) when the segment keys lose some of it, else "". The segment key (so the hold key) is blind to a quoted word that has a
    space in it (`""`), to a word with a shell syntax character (one byte, x06), to a substitution (`$X`, `/dev/fd/X`) and to a here-doc body (`<<HEREDOC`):
    `ssh host 'cat /etc/passwd'` and `ssh host 'cat /etc/shadow'` are the same key. So the text is part of the exact key and of the kind (V6-06b). The same words in other
    quote marks, or with other spacing outside the quotes, are the same text. A text that `_quoted_form` cannot read is hashed whole: more asks, never fewer."""
    if not any(_LOST.search(k) for k in getattr(a, "seg_keys", None) or []):
        return ""
    s = a.subject or ""
    return _quoted_hash(lead[1] if (lead := _leading_cd(s)) else s)   # a leading `cd PATH &&` is the place (`_where`), not a word


@functools.lru_cache(maxsize=16)
def _quoted_hash(s: str) -> str:
    """`_quoted` for one text, remembered: the keys of one call are made several times (the exact key, the kind, the scopes), and the text may be large."""
    form = _quoted_form(s)
    return sha256(form if form is not None else s)[:16]


def _with_quoted(a, key: dict) -> dict:
    """`key` plus the quoted-word hash when there is one: a call with no blanked word keeps the key it always had, so answers stored before still match it."""
    return {**key, "quoted": q} if (q := _quoted(a)) else key


def class_key(a) -> str:
    """The kind of call at its target and in its place: the rule, the segment keys, the target (`_target`) and the directory it runs in (`_where`), and the quoted words
    (`_quoted`). A confirmed staging answer does not cover prod."""
    return sha256(canonical(_with_quoted(a, {"rule": a.rule_id, "segments": sorted({normalise_segment(k) for k in a.seg_keys}), "target": _target(a), "where": _where(a)})))


def any_project_key(a) -> str:
    """The kind of call at its target in any directory (the `kind_any` scope): `class_key` without the directory."""
    return sha256(canonical(_with_quoted(a, {"rule": a.rule_id, "segments": sorted({normalise_segment(k) for k in a.seg_keys}), "target": _target(a)})))


def keyed_fp(a, fp: dict | None) -> dict:
    """The fingerprints of the call with its target and place (`_target`, `_where`) added, and the text the segment keys lose (`_quoted`) when they lose some: what the exact
    key and the ask rows (`fingerprints_json`) are made from. The hold key drops prefixes, the directory and the quoted words, so without this a staging answer would be
    the same exact call as the prod one, and `ssh host 'cat /etc/passwd'` the same as `ssh host 'cat /etc/shadow'`."""
    out = {**(fp or {}), "_ctx": sha256(canonical({"target": _target(a), "where": _where(a)}))[:16]}
    if q := _quoted(a):
        out["_q"] = q
    return out


def keys(a, tool: str, tool_input: dict | None, fp: dict | None) -> tuple[str, str | None, str]:
    """(exact key, class key or None, eligibility) of a call."""
    el = eligibility(a, tool, tool_input, fp)
    return exact_key(a.hold_key or a.plan_hash, keyed_fp(a, fp)), (class_key(a) if el == "class" else None), el


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
    """A confirmed (strong) allow, live (not a test row), confirmed within CAP_DAYS, confirmed or used within USE_DAYS, and not aged out by evidence (`audit.aged_out`)."""
    from . import audit

    cap, use = since or valid_since()
    confirmed = _col(r, "confirmed_ts")
    used = _col(r, "last_used_ts")
    return (r["answer"] == "allow" and r["strength"] == "strong" and (_col(r, "source") or "") != "test" and bool(confirmed) and confirmed >= cap
            and (confirmed if used is None else used) >= use   # COALESCE(last_used_ts, confirmed_ts): only NULL falls back to the confirm
            and not audit.aged_out(r))   # V6-29: a row that ran silently with no mark for too long needs evidence; this only adds a condition


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
    """A silent run of the row: its last-use time moves forward and its count of runs with no mark goes up (a `right` mark resets it, `audit.reset_evidence`)."""
    db.con.execute("UPDATE human_answers SET last_used_ts=?, silent_runs=COALESCE(silent_runs,0)+1 WHERE answer_id=?", (ts(), answer_id))


def audit_paused(db, a, tool: str, tool_input: dict | None, fp: dict | None, cwd: str) -> bool:
    """Is this call inside a scope the audit has paused? Found by the keys of the call (exact, class, scope), not by an answer row: a wrong check retires the rows."""
    from . import audit, scopes

    exact, cls, el = keys(a, tool, tool_input, fp)
    if el == "never":
        return False
    return audit.paused(db, [exact, cls, *scopes.keys_for(a, tool, tool_input, fp, cwd)])


def step(db, mode: str, a, tool: str, event: dict, fp: dict, cwd: str, held: dict | None = None):
    """The memory branch of the gate: a HookResult that lets the call run on a confirmed answer, or None (ask as before). When the audit is why the call is asked about
    (its scope is paused, or a limit or the key refused it), `held["audit"]` says why, and the caller must not let Jev decide it in the human's place."""
    from . import askchat, audit, paths, permits, state_machine
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
                if held is not None and audit_paused(db, a, tool, tin if isinstance(tin, dict) else {}, fp, cwd):
                    held["audit"] = "suspended"   # no row to match (a wrong check retired it), the scope is still paused: Jev must not decide it either
                return None
            adm, why = audit.admit(db, row)   # V6-29: a call that cannot be audited (a paused scope, a limit reached, no key) is asked about
            if adm is None:
                audit.refused(db, row, why)
                if held is not None:
                    held["audit"] = why
                return None
            did = askchat.run_decision(db, a, tool, cwd, event["session_id"], row["answer_id"][-12:], "memory")
            use(db, row["answer_id"])
            db.event(row["answer_id"], "memory_allowed", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"decision_id": did, "channel": row["channel"], "scope_id": adm.scope})
            audit.record(db, adm, row, did, event["session_id"], a.subject, cwd)   # the draw, in the same transaction: no row written, no silent run
    except state_machine.IllegalTransition:
        return None   # the decision could not advance: ask as before
    return HookResult(0, f"JADE: allowed from your confirmed answer {row['answer_id']} (rule {a.rule_id}). `jade knows list` shows what is remembered; `jade knows rm {row['answer_id']}` forgets it.")
