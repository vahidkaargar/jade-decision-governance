"""V6-32: report-only learning and the case ratchet (docs/17-report-only-learning.md).

Tier 0 and tier 1 of the learning design: JADE counts and writes a report, and a human decides. Nothing here has a path to a policy file, a threshold, a memory row,
a label or a case file. The functions read the ledger (`ledger.live()` leaves test rows out) and return plain data. The one place that writes is `write_out`, for a
diff the human names, and it refuses every place the gate or the learning loop reads from.

  blocker_classes   what stopped calls, by safety invariant id and by rule id, with the ask load beside it
  blocker_sample    a fixed sample of those rows, ids only, for the owner's precision check
  error_records     the calls the owner marked wrong (the only error the ledger records today)
  case_proposals    a regression case for each error record, as data; `case_diff` renders it as a diff of evals/regression/cases.jsonl
  policy_table      per rule: friction (asks, answers) and error (wrong marks) side by side, and a word of advice for a human

No command text is printed by a report: counts, rule ids, invariant ids, times. A proposed case holds a command because a case is a command; it is built only from a
command that is whole, has no redacted part, no real host and no home directory, and it goes into a diff the human reads before anything is merged.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

from . import ledger, readouts

CASES_REL = "evals/regression/cases.jsonl"
RECORD_KINDS = ("wrong_mark", "audit_false_allow", "counterfactual_false_reject")
MIN_ANSWERED = 20          # fewer answers than this say nothing about a rule
CUT_AT = 500               # a decision keeps the first 500 characters of a command (decide.open_decision): a command this long may be cut
INVARIANT = re.compile(r"\bSI-\d+\b")
FLOOR_CLASS = re.compile(r"HO-[a-z0-9-]+")   # a human-only class id (jade/policy.py HO_*), as the level-off floor writes it in a `safety_block` detail (W-74)
# A home or user path in any spelling: after a space, a quote, `=`, `:`, `<`, `;`, `(` and so on, never in the middle of a longer word (`/tmp/home/x` is not one).
PRIVATE_PATH = re.compile(r"(?<![\w.-])(?:/(?:Users|home|Volumes|root)(?![\w.-])|/(?:private/)?var/root(?![\w.-])|~[A-Za-z_][\w.-]*)", re.I)
# A real host: `user@host` (ssh, scp, git, psql), or a dotted name that ends in a common public or private top-level word. File endings such as .sh, .py, .md, .in or .app
# are left out on purpose.
AT_HOST = re.compile(r"[\w.+-]+@([A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9])")
DOTTED_HOST = re.compile(r"(?<![\w-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:com|net|org|io|dev|co|ai|cloud|info|biz|edu|gov|mil|me|us|uk|de|fr|ru|cn|jp|ir|tv|xyz|tech|online|site|store|internal|corp|lan|intranet|private))(?![\w-])", re.I)
NET_PROGRAMS = frozenset({"ssh", "sftp", "telnet", "nc", "ncat", "ping", "traceroute", "mosh"})   # the first word that is not an option is a host
RULE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
TOOL_OF_OBJECTIVE = re.compile(r"^Execute (\S+?):")


class WriteRefused(Exception):
    """`write_out` will not write there."""


# ---- blocker classes ----------------------------------------------------------------------------------------------------
def _guard(guard: str | None) -> tuple[str | None, str | None]:
    """(rule id, level) from a gate event's guard text, `R-14 high SI-08`."""
    parts = (guard or "").split()
    return (parts[0] if parts and re.fullmatch(r"[A-Z]+-[\w-]+", parts[0]) else None), (parts[1] if len(parts) > 1 else None)


def _detail(detail_json: str | None) -> dict:
    try:
        d = json.loads(detail_json or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def classes_of(kind: str, guard: str | None, detail_json: str | None) -> tuple[list[str], str]:
    """The classes a blocker row falls in, and where they came from. A `safety_block` is classed by the invariant ids: those the hook wrote in the event detail (V6-32),
    else those in the guard text (rows from before). A block by the level-off floor (V6-16) is no invariant: it is classed by the human-only class ids the hook
    wrote (`floor`, W-74). A `would_block` or `gated` event is classed by its rule id."""
    if kind != "safety_block":
        rule, _ = _guard(guard)
        return ([rule] if rule else []), "rule"
    d = _detail(detail_json)
    ids = d.get("invariants")
    if isinstance(ids, list) and ids and all(isinstance(i, str) and INVARIANT.fullmatch(i) for i in ids):
        return list(ids), "detail"
    found = INVARIANT.findall(guard or "")
    if found:
        return found, "guard"
    floor = d.get("floor")
    if isinstance(floor, list) and floor and all(isinstance(i, str) and FLOOR_CLASS.fullmatch(i) for i in floor):
        return list(floor), "floor"
    return [], "none"


def blocker_classes(db, since: str) -> dict:
    """What stopped calls since `since` (an ISO time, "" for all time). Counts only."""
    live = ledger.live()
    invariants: dict[str, int] = {}
    floor: dict[str, int] = {}
    src = {"from_detail": 0, "from_guard_only": 0, "from_floor": 0, "unclassified": 0}
    by_kind: dict[str, dict[str, int]] = {"would_block": {}, "gated": {}}
    blocks = 0
    for r in db.all(f"SELECT kind, guard, detail_json FROM events WHERE kind IN ('safety_block','would_block','gated') AND ts>=? AND {live} ORDER BY id", (since,)):  # noqa: S608
        classes, came = classes_of(r.kind, r.guard, r.detail_json)
        if r.kind == "safety_block":
            blocks += 1
            src[{"detail": "from_detail", "guard": "from_guard_only", "floor": "from_floor", "none": "unclassified"}[came]] += 1
            into = floor if came == "floor" else invariants
            for c in classes:
                into[c] = into.get(c, 0) + 1
        else:
            for c in classes:
                by_kind[r.kind][c] = by_kind[r.kind].get(c, 0) + 1
    asks = readouts.ask_load(db, since)
    return {"window_since": since or "all time", "safety_block_rows": blocks, "invariants": dict(sorted(invariants.items())), "floor_classes": dict(sorted(floor.items())), "invariant_rows": src,
            "would_block_by_rule": dict(sorted(by_kind["would_block"].items(), key=lambda kv: (-kv[1], kv[0]))),
            "gated_by_rule": dict(sorted(by_kind["gated"].items(), key=lambda kv: (-kv[1], kv[0]))),
            "asks_by_rule": {k: {x: v[x] for x in ("asked", "allowed", "denied", "expired", "open", "approve_rate")} for k, v in asks["by_rule"].items()}}


def blocker_sample(db, since: str, n: int = 30, seed: str = "v1") -> list[dict]:
    """A fixed sample of the blocker rows (`safety_block` and `would_block`), for the owner's precision check of the classes: the same seed gives the same rows.
    Ids and times only. The owner reads each row's class against the rule text; a sample of 20 to 30 rows per report, with a target of at least 80% right."""
    rows = db.all(f"SELECT id, ts, kind, guard, detail_json FROM events WHERE kind IN ('safety_block','would_block') AND ts>=? AND {ledger.live()}", (since,))  # noqa: S608
    chosen = sorted(rows, key=lambda r: hashlib.sha256(f"{seed}:{r.id}".encode()).hexdigest())[:max(0, n)]
    out = []
    for r in sorted(chosen, key=lambda r: r.id):
        classes, came = classes_of(r.kind, r.guard, r.detail_json)
        rule, level = _guard(r.guard)
        out.append({"event_id": r.id, "ts": r.ts, "kind": r.kind, "tool": _detail(r.detail_json).get("tool"), "rule": rule, "level": level, "classes": classes, "classes_from": came})
    return out


# ---- error records ------------------------------------------------------------------------------------------------------
def _latest_marks(db) -> dict[str, str]:
    """decision id -> the owner's latest mark (`right` or `wrong`), over all time. A later mark replaces an earlier one. A mark is never aged out of a report: a call marked
    wrong 40 days ago is still a wrong call, and `jade review mark wrong` leaves no other trace."""
    marks: dict[str, str] = {}
    for e in db.all(f"SELECT entity_id, detail_json FROM events WHERE kind='review_mark' AND {ledger.live()} ORDER BY id"):  # noqa: S608
        mark = _detail(e.detail_json).get("mark")
        if mark in ("right", "wrong"):
            marks[e.entity_id] = mark
    return marks


def error_records(db, since: str | None = None) -> list[dict]:
    """The calls the owner marked wrong (`jade review mark ID wrong`), from the decision each mark names. This is the only error the ledger records today: an audit
    false allow and a counterfactual false reject have no producer yet, so they come from a records file (`read_records`). Every wrong mark counts, whatever its age:
    `since` is accepted for old callers and is not used."""
    out = []
    for did, mark in _latest_marks(db).items():
        if mark != "wrong":
            continue
        row = db.one("SELECT doc_json FROM decisions WHERE decision_id=?", (did,))
        if row is None:
            continue
        doc = _detail(row.doc_json)
        actions = doc.get("candidate_actions") or [{}]
        m = TOOL_OF_OBJECTIVE.match(str(doc.get("objective") or ""))
        risk = doc.get("risk") or {}
        out.append({"kind": "wrong_mark", "decision_id": did, "rule_id": risk.get("rule_id"), "level": risk.get("level"), "tool": m.group(1) if m else None,
                    "command": (actions[0] or {}).get("description") or ""})
    return out


def read_records(path) -> list[dict]:
    """A JSONL file of error records the ledger cannot produce yet: one object per line with `kind` (`RECORD_KINDS`) and `command`; optional `rule_id`, `tool` (default
    Bash) and `twin_command` (a harmless look-alike a human wrote). A `rule_id` must look like a rule id and `tool` must be one line of printable text: both are printed
    and become part of a case class. Other keys are dropped. Raises ValueError on a line that is not a valid record, OSError when the file cannot be read."""
    recs = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            raise ValueError(f"{path}: line {n} is not JSON") from None
        if not isinstance(r, dict) or r.get("kind") not in RECORD_KINDS or not isinstance(r.get("command"), str) or not r["command"].strip():
            raise ValueError(f"{path}: line {n} needs an object with kind (one of {', '.join(RECORD_KINDS)}) and a command")
        if "twin_command" in r and not isinstance(r["twin_command"], str):
            raise ValueError(f"{path}: line {n}: twin_command must be text")
        rec = {"tool": r.get("tool", "Bash"), "kind": r["kind"], "command": r["command"]}
        if not isinstance(rec["tool"], str) or not rec["tool"].isprintable() or not 0 < len(rec["tool"]) <= 64:
            raise ValueError(f"{path}: line {n}: tool must be one line of printable text")
        if r.get("rule_id") is not None:
            if not isinstance(r["rule_id"], str) or not RULE_ID.fullmatch(r["rule_id"]):
                raise ValueError(f"{path}: line {n}: rule_id must look like a rule id (for example R-14)")
            rec["rule_id"] = r["rule_id"]
        if "twin_command" in r:
            rec["twin_command"] = r["twin_command"]
        recs.append(rec)
    return recs


# ---- case proposals -----------------------------------------------------------------------------------------------------
def _private_host(command: str) -> str | None:
    """The first host in a command that is not a reserved name, or None. It looks for `user@host`, a dotted name with a common top-level word, and the host that `ssh`
    and its kind take as their first argument. A package spec such as `pkg@1.2.3` is not a host (a name that starts with a digit is a version, or an address that
    `evals.text_problems` checks). The check is cautious: when in doubt it names a host, and the record is skipped for a human to rewrite."""
    from . import evals

    safe = evals._SAFE_HOST  # noqa: SLF001 - the same list of reserved names as the case files
    for m in AT_HOST.finditer(command):
        if not m.group(1)[0].isdigit() and not safe.search(m.group(1)):
            return m.group(1)
    for m in DOTTED_HOST.finditer(command):
        if not safe.search(m.group(1)):
            return m.group(1)
    words = [w.strip("'\"") for w in command.split()]
    for i, w in enumerate(words):
        if os.path.basename(w) in NET_PROGRAMS:
            arg = next((x for x in words[i + 1:] if x and not x.startswith("-") and not x.isdigit()), None)
            if arg and re.fullmatch(r"[A-Za-z0-9][\w.-]*", arg) and not safe.search(arg):
                return arg
    return None


def _unsafe(command: str, tool: str | None) -> str | None:
    """Why this command cannot be written into a case file, or None."""
    from . import evals

    if tool != "Bash":
        return f"only a Bash call can be a gate case (this one is {tool or 'not a tool call'})"
    if not command.strip():
        return "no command text"
    if len(command) >= CUT_AT:
        return f"the command is {len(command)} characters and may be cut (a decision keeps {CUT_AT}): the command that ran is not known"
    if "[REDACTED:" in command:
        return "part of the command is redacted: the stored text is not the command that ran"
    if PRIVATE_PATH.search(command):
        return "the command holds a home directory or user path; a human rewrites it with a neutral path"
    host = _private_host(command)
    if host:
        return f"the command names a host ({host!r}) that is not a reserved name; a human rewrites it with a .test or .example name"
    problems = evals.text_problems(command)
    return "; ".join(problems) if problems else None


def _case_id(expect: str, command: str) -> str:
    return "RC-" + hashlib.sha256(f"{expect}|{command}".encode()).hexdigest()[:8]


def case_proposals(records: list[dict], existing: list[dict]) -> dict:
    """A regression case for each error record, as data. The case is a plain `gate` row (evals/run_cases.py): a wrong mark and an audit false allow say the call
    should have been stopped (`expect: block`); a counterfactual false reject says a call JADE stopped should have run (`expect: allow`).

    The twin: a case is only as good as its benign look-alike. A human-written `twin_command` becomes a second row with the opposite expectation (`twin: human`). With
    none the case says `twin: unverified`: a model-written label carries known error, so JADE never writes one. A record that cannot be a safe case is skipped with its
    reason. Nothing here shows that the case fails on the version that erred: `evals/case_ratchet.py check` does."""
    have_ids = {c.get("id") for c in existing}
    have = {(c.get("command"), c.get("expect")) for c in existing}
    cases: list[dict] = []
    skipped: list[dict] = []

    def skip(rec: dict, why: str) -> None:
        skipped.append({"kind": rec.get("kind"), "decision_id": rec.get("decision_id"), "rule_id": rec.get("rule_id"), "why": why})

    def row(rec: dict, command: str, expect: str, **extra) -> dict:
        who = rec.get("rule_id") or "unknown-rule"
        return {"id": _case_id(expect, command), "class": f"{rec['kind']}_{who}", "kind": "gate", "command": command, "expect": expect,
                "proposed_from": rec["kind"], "note": f"proposed from a {rec['kind'].replace('_', ' ')} on rule {who}; a human reads it before it is merged", **extra}

    for rec in records:
        command = str(rec.get("command") or "")
        expect = "allow" if rec["kind"] == "counterfactual_false_reject" else "block"
        why = _unsafe(command, rec.get("tool"))
        if why:
            skip(rec, why)
            continue
        cid = _case_id(expect, command)
        if cid in have_ids or (command, expect) in have:
            skip(rec, "already in the case file")
            continue
        twin = None
        if rec.get("twin_command"):
            tw_command, tw_expect = str(rec["twin_command"]), "block" if expect == "allow" else "allow"
            tw_why = _unsafe(tw_command, rec.get("tool"))
            if tw_why:
                skip({**rec, "kind": "twin"}, f"the twin: {tw_why}")
            elif (tw_command, tw_expect) not in have:
                twin = row(rec, tw_command, tw_expect, twin="human", twin_of=cid)
                twin["class"] = f"twin_of_{cid}"
                twin["note"] = "the benign twin of " + cid + ", written by a human"
        cases.append(row(rec, command, expect, twin="human" if twin else "unverified"))
        have_ids.add(cid)
        have.add((command, expect))
        if twin:
            cases.append(twin)
            have_ids.add(twin["id"])
            have.add((twin["command"], twin["expect"]))
    return {"cases": cases, "skipped": skipped}


_LINE_BREAKS = {"\u0085": "\\u0085", " ": "\\u2028", " ": "\\u2029"}   # json leaves these raw, and `str.splitlines` (here, in the case runner, in the tests) ends a line at them


def case_line(case: dict) -> str:
    """One case as one physical line of JSONL. The three characters that `splitlines` treats as a line end are written as `\\uXXXX` escapes (valid JSON, same value)."""
    text = json.dumps(case, ensure_ascii=False)
    for ch, esc in _LINE_BREAKS.items():
        text = text.replace(ch, esc)
    return text


def _lines(text: str) -> list[str]:
    """The lines of a text, split at a newline only (git's rule), each with its newline."""
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def case_diff(existing_text: str, cases: list[dict], path: str = CASES_REL) -> str:
    """The cases as a unified diff that adds them to the end of `path`, so `git apply` takes it. The empty string when there is nothing to add."""
    if not cases:
        return ""
    old = existing_text if existing_text.endswith("\n") or not existing_text else existing_text + "\n"
    new = old + "".join(case_line(c) + "\n" for c in cases)
    return "".join(difflib.unified_diff(_lines(old), _lines(new), fromfile=f"a/{path}", tofile=f"b/{path}"))


# ---- the policy table ---------------------------------------------------------------------------------------------------
def _never_rules() -> frozenset[str]:
    from . import memory, proxy

    return frozenset(memory.NEVER_RULES | proxy.NEVER_RULES)


def policy_table(db, since: str) -> list[dict]:
    """Per rule: the friction (asks and how they were answered) and the error (calls marked wrong) side by side, and a word of advice for a human who reads the table.

    The window (`since`) decides which asks, answers and advisory blocks are counted. The safety evidence is counted over all time: a wrong mark and a human-only ask
    keep their effect on the advice however old they are, so the advice for a ledger does not change with the window.

    There is no "unnecessary asks" number: an ask is unnecessary only if the owner's answer says so, and an allow answer is a hint, not evidence (nothing audits the
    calls that ran silently yet: the column says `not measured`). The advice is one of
      protected  a human edits this rule; learning never proposes a change (critical, a human-only class, a rule the memory and the proxy never cover, or a rule id the
                 risk policy has no level for)
      hold       a call this rule covers was marked wrong: look at that first
      too_few    fewer than MIN_ANSWERED answers
      keep       the owner denied some of the asks
      review     enough answers, all allow, no wrong mark: a human may look for a narrow scope. Replay it first (`evals/learn_replay.py`); nothing is changed by the table.
    """
    from . import evals, policy

    live = ledger.live()
    asks = readouts.ask_load(db, since)["by_rule"]
    risk = policy.risk()
    levels = {r["id"]: r.get("level") for r in (*risk["rules"], *risk.get("mcp_tools", ()))}   # shell rules and MCP rules: M-01 is critical
    wrong: dict[str, int] = {}
    for did, mark in _latest_marks(db).items():   # the safety evidence is all time: it never ages out of the window
        if mark == "wrong":
            row = db.one("SELECT rule_id FROM decisions WHERE decision_id=?", (did,))
            if row and row.rule_id:
                wrong[row.rule_id] = wrong.get(row.rule_id, 0) + 1
    would: dict[str, int] = {}
    for r in db.all(f"SELECT guard FROM events WHERE kind='would_block' AND ts>=? AND {live}", (since,)):  # noqa: S608
        rule, _ = _guard(r.guard)
        if rule:
            would[rule] = would.get(rule, 0) + 1
    ho = {r.rule_id: r.n for r in db.all(f"SELECT rule_id, COALESCE(SUM(ho),0) AS n FROM asks WHERE {live} GROUP BY rule_id")}  # noqa: S608   # all time, like the wrong marks
    never = _never_rules()
    table = []
    for rule in sorted(set(asks) | set(wrong) | set(would)):
        a = asks.get(rule, {"asked": 0, "allowed": 0, "denied": 0, "expired": 0, "open": 0, "approve_rate": None})
        answered = a["allowed"] + a["denied"]
        lower = round(100 * evals.wilson(a["allowed"], answered)[0], 1) if answered else None
        level = levels.get(rule)
        if rule in never or level == "critical" or ho.get(rule):
            advice, why = "protected", "a human edits this rule; learning does not propose a change to it"
        elif level is None:
            advice, why = "protected", "the risk policy has no level for this rule id, so JADE cannot tell if it is a critical one: learning does not propose a change to it"
        elif wrong.get(rule):
            advice, why = "hold", "a call this rule covers was marked wrong: look at those marks first"
        elif answered < MIN_ANSWERED:
            advice, why = "too_few", f"{answered} answered; fewer than {MIN_ANSWERED} says nothing"
        elif a["denied"]:
            advice, why = "keep", "the owner denied some of these asks"
        else:
            advice, why = "review", ("every answer was allow, and an allow answer is a hint, not evidence: no audit of silent runs exists yet. A human may look for a "
                                     "narrow scope. Run the replay first (evals/learn_replay.py).")
        table.append({"rule": rule, "level": level, "asked": a["asked"], "allowed": a["allowed"], "denied": a["denied"], "expired": a["expired"], "open": a["open"],
                      "approve_rate": a["approve_rate"], "allow_lower_95_percent": lower, "wrong_marks": wrong.get(rule, 0), "would_block": would.get(rule, 0),
                      "human_only_asks": int(ho.get(rule) or 0), "audit": readouts.NOT_MEASURED, "advice": advice, "why": why})
    return table


# ---- plain words --------------------------------------------------------------------------------------------------------
def render_blockers(r: dict, sample: list[dict] | None = None) -> str:
    s = r["invariant_rows"]
    lines = [f"What stopped calls ({r['window_since']}). Counts and ids only, no command text.",
             f"Safety invariants that fired ({r['safety_block_rows']} blocked call(s)):"]
    lines += [f"  {k}  {v}" for k, v in r["invariants"].items()] or ["  none"]
    if r["floor_classes"]:
        lines.append("Blocked by the level-off floor (a write to JADE's own wiring), by human-only class:")
        lines += [f"  {k}  {v}" for k, v in r["floor_classes"].items()]
    lines.append(f"Where the invariant id came from: event detail {s['from_detail']}, guard text of an older row {s['from_guard_only']}, "
                 f"level-off floor {s['from_floor']}, not recorded at all {s['unclassified']}.")
    lines.append("Calls the gate would have stopped in advisory mode, by rule:")
    lines += [f"  {k}  {v}" for k, v in r["would_block_by_rule"].items()] or ["  none"]
    lines.append("Calls that needed a permit or a question, by rule:")
    lines += [f"  {k}  {v}" for k, v in r["gated_by_rule"].items()] or ["  none"]
    lines.append("Questions asked of you, by rule (asked / allowed / denied / no answer):")
    lines += [f"  {k}  {v['asked']} / {v['allowed']} / {v['denied']} / {v['expired']}" for k, v in r["asks_by_rule"].items()] or ["  none"]
    if sample is not None:
        lines.append(f"Sample for your precision check ({len(sample)} row(s), fixed by its seed). Check each class against the rule text; at least 80% should be right:")
        lines += [f"  event {x['event_id']}  {x['ts']}  {x['kind']}  tool {x['tool']}  rule {x['rule']} {x['level']}  class {','.join(x['classes']) or '-'} ({x['classes_from']})" for x in sample]
    return "\n".join(lines)


def render_table(table: list[dict]) -> str:
    if not table:
        return "No rule has asks, blocks or marks in this window."
    lines = ["Per rule: what it cost you (asks) and what is known about errors (wrong marks), side by side. Friction is not correctness.",
             "Asks, answers and advisory blocks count the window. Wrong marks and human-only asks count all time.",
             "The advice is for you to read. Nothing here changes a rule, a threshold or a memory row."]
    for t in table:
        lines.append(f"rule {t['rule']} ({t['level'] or 'level unknown'}): asked {t['asked']}, allowed {t['allowed']}, denied {t['denied']}, no answer {t['expired']}; "
                     f"wrong marks {t['wrong_marks']}; audit of silent runs {t['audit']}; advice {t['advice']}")
        lines.append(f"    {t['why']}")
    return "\n".join(lines)


# ---- the one write -------------------------------------------------------------------------------------------------------
def _fold(p) -> str:
    return unicodedata.normalize("NFD", str(p)).casefold()


def _inside(path: Path, parent: Path) -> bool:
    """Is `path` in `parent`, or the same place? `resolve()` does not fold letter case, and the macOS disk does not tell `/Users/x/Repo` from `/users/x/repo`, so three tests
    run: the resolved paths; the same two with case and Unicode form folded (a place that does not exist yet has no other test); and `samefile` on every folder of the
    path that exists (the disk's own answer, also for a spelling the first two do not see)."""
    parent = parent.resolve()
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        pass
    p, q = _fold(path), _fold(parent)
    if p == q or p.startswith(q.rstrip(os.sep) + os.sep):
        return True
    if not parent.exists():
        return False
    for place in (path, *path.parents):
        try:
            if place.exists() and os.path.samefile(place, parent):
                return True
        except OSError:
            continue
    return False


def write_out(path, text: str) -> Path:
    """Write a proposed diff to a file the human named. It refuses anything inside the checkout or the installed package (policies, evals, the code), the JADE
    config directory (config, overlays, calibration, pins), the policy directory in use and the JADE home, and it never overwrites a file. The human applies the diff."""
    from . import paths

    target = Path(path).expanduser().resolve()
    code_root = Path(__file__).resolve().parents[1]
    for protected in (code_root, paths.shipped_policy_dir(), paths.policy_dir(), paths.config_dir(), paths.home()):
        if _inside(target, protected):
            raise WriteRefused(f"{target} is inside {protected}: a report never writes there. Name a file elsewhere and apply the diff yourself.")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError:
        raise WriteRefused(f"{target} exists; a report never overwrites a file") from None
    return target
