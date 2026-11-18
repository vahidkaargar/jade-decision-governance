"""The human answers a gated call in the chat (docs/12, plan v3, PR-5b).

At a level (`jade level`), gate mode enforce, host mode auto or bypassPermissions, in the main session:

1. `_gate` calls `step`: with no usable grant it opens an ask and exits 2 with a fixed text that tells the agent to call AskUserQuestion with the token
   `[jade:ask_<id>]` and then retry the identical call.
2. PreToolUse on that AskUserQuestion calls `rewrite`: the whole question is REPLACED by the hook's own text (rule, rationale, the command, the part that is
   not shown), so the agent's wording never reaches the human. The host shows the replaced question (probe cell 5b, evals/probes/hook_ask/RESULTS.md).
3. PostToolUse on it calls `settle`: the answer is read from the host's `tool_response.answers` (never from agent text). Allow makes a single-use grant
   (10 minutes, this session, this exact call: its tool, text and folder, the `plan_hash`, and the Bash run settings such as the sandbox switch); Deny closes the ask. The answer is recorded as a hint in `human_answers`; it teaches nothing yet.
4. The identical retry reaches `step` again, which consumes the grant, opens a decision (so SI-07 still holds an unknown outcome) and lets the call run.

A grant is never valid for live JADE wiring, a host mode outside the two above, or a call with secrets in it: those keep the signed terminal permit.
A subagent call has no AskUserQuestion: `askdialog` (W-100c) asks it in a macOS dialog instead. Residual: a mod loaded in Claude Code can answer for the human (docs/12 risks).
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import timedelta

from . import askplain, memory, paths, policy, shellnorm
from . import scopes as scopes_mod
from .util import canonical, new_id, now, sha256, show_safely, ts

CHAT_MODES = ("auto", "bypassPermissions")
RUN_FIELDS_IGNORED = frozenset({"command", "cmd", "script", "description"})   # the text (the plan hash covers it) and the display line
ASK_TTL_S = 600
GRANT_TTL_S = 600
PERMIT_ONLY = frozenset({"HO-wiring", "HO-checkout-install", "HO-settings-shadow", "HO-state-path"})   # live JADE wiring: a human types `jade permit approve`
TOKEN = re.compile(r"\[jade:ask_([0-9a-f]{12})\]")
HEAD_CHARS, TAIL_CHARS = 450, 150
GATED_CAP = 600        # a rated command that the clip hides is shown again up to this many characters
GATED_PARTS = 3        # ... and at most this many of them
LOCATE_MAX = 20000     # a longer text is not searched for the rated part: the question shows the gate's own reading instead
DENY_CAP = 3            # the 3rd Deny of one call is a plain refusal: the human has answered, asking again is pressure
EXPIRE_PAUSE = 30       # asks that expired unanswered, in the window below and since the last answer, pause asking for the session
PAUSE_WINDOW_S = 3600


def applicable(mode: str, a, event: dict) -> bool:
    """Does this call take the chat channel? (Secrets in the call are checked by the caller: it needs the context.)"""
    return (mode == "enforce" and paths.level() in ("low", "medium", "high") and event.get("permission_mode") in CHAT_MODES and not event.get("agent_id")
            and bool(event.get("session_id")) and not (set(a.ho_ids) & PERMIT_ONLY))


def run_fields(tool: str, tool_input) -> dict:
    """The fields of a shell call, other than its text, that change how it runs (Bash: `dangerouslyDisableSandbox`, `run_in_background`, `timeout`).
    The plan hash covers the tool, the text and the folder only, so these are bound to the grant here. `description` is display text and does not count.
    A value that is empty or false is the default and does not count either, so an absent field and a false one are the same call."""
    if not isinstance(tool_input, dict) or not policy.runs_shell(tool, tool_input):
        return {}
    return {str(k): v for k, v in sorted(tool_input.items(), key=lambda kv: str(kv[0]))
            if k not in RUN_FIELDS_IGNORED and v not in (None, False, "", 0, [], {})}


def _short(text: str) -> bool:
    return len(text) <= HEAD_CHARS + TAIL_CHARS + 100


def clip(text: str) -> str:
    """A path, a folder or a content text for the human: clipped, and every control, bidi and zero-width character written out (V6-09)."""
    if _short(text):
        return show_safely(text)
    return (f"{show_safely(text[:HEAD_CHARS])}\n... {len(text) - HEAD_CHARS - TAIL_CHARS} characters not shown ...\n{show_safely(text[-TAIL_CHARS:])}")


_clip = clip   # the old name, for any caller outside this package


_HEREDOC = re.compile(r"<<-?[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?(\w+))")
_PLAIN = re.compile(r"[\w./@:=+,%~ \t-]*\Z")   # a statement with no quote, no nesting, no redirect: its key is read from its words alone
ASSESS_CAP = 40        # statements with a quote or a nested form are rated one by one, at most this many


def _statements(text: str) -> list[tuple[int, int]]:
    """(start, end) of each top-level statement of a shell text: split at a line break, `;`, `&&`, `||`, `|` and `&` that are outside quotes, `$( )`, `( )`, `${ }`, backticks
    and here-document bodies. A nested form (`bash -c '...'`, `$(...)`) and everything it holds is one statement, so a `cd` inside it stays with the command that follows it.
    Quotes this cannot close make one long statement: the question then shows its start and says to read the call elsewhere."""
    n, i, start = len(text), 0, 0
    out: list[tuple[int, int]] = []
    pending: list[str] = []   # here-document delimiters opened on this line

    def cut(end: int, width: int):
        nonlocal start
        if text[start:end].strip():
            out.append((start, end))
        start = end + width

    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
        elif c == "'":
            k = text.find("'", i + 1)
            i = n if k < 0 else k + 1
        elif c == '"':
            i = shellnorm._dq_end(text, i + 1) + 1
        elif c == "`":
            k = i + 1
            while k < n and text[k] != "`":
                k += 2 if text[k] == "\\" else 1
            i = k + 1
        elif c == "$" and text.startswith("${", i):
            i = shellnorm._match_brace(text, i + 2)
        elif c == "$" and text.startswith("$(", i):
            i = shellnorm._skip_span(text, i + 1)
        elif c in "<>" and text.startswith("(", i + 1):
            i = shellnorm._skip_span(text, i + 1)
        elif c == "(":
            i = shellnorm._skip_span(text, i)
        elif c == "<" and text.startswith("<<", i) and not text.startswith("<<<", i) and (m := _HEREDOC.match(text, i)):
            pending.append(m.group(1) or m.group(2) or m.group(3))
            i = m.end()
        elif c == "\n":
            if pending:   # the here-document bodies start on the next line and end at their delimiter line: they are part of this statement
                pos = i + 1
                for word in pending:
                    while pos < n:
                        eol = text.find("\n", pos)
                        eol = n if eol < 0 else eol
                        line = text[pos:eol].strip()
                        pos = eol + 1
                        if line == word:
                            break
                pending.clear()
                i = max(i + 1, pos - 1)
            else:
                cut(i, 1)
                i += 1
        elif c == ";":
            cut(i, 1)
            i += 1
        elif c == "&":
            if text.startswith("&&", i):
                cut(i, 2)
                i += 2
            elif (i and text[i - 1] in "<>") or text.startswith("&>", i):
                i += 1
            else:
                cut(i, 1)
                i += 1
        elif c == "|":
            if text.startswith(("||", "|&"), i):
                cut(i, 2)
                i += 2
            elif i and text[i - 1] == ">":
                i += 1
            else:
                cut(i, 1)
                i += 1
        else:
            i += 1
    if text[start:].strip():
        out.append((start, n))
    return out


_CD = re.compile(r"\s*(?:cd|pushd)(?:\s|\Z)")


def _hidden_gated(keys: set[str], text: str) -> tuple[list[tuple[int, int]], bool]:
    """Which parts of `text` hold a command the gate rated and are not wholly in the head or the tail that the question shows, and whether every rated key was found in the text.
    A part is a whole top-level statement, so a rated command inside `bash -c '...'` or `$(...)` is shown with the form it sits in, and a `cd` just before it is shown with it. The
    same key twice, one copy visible and one hidden, shows the hidden one. A statement with a quote or a nested form is rated by the gate's own `assess` (at most ASSESS_CAP of them).
    Never raises: a text this cannot follow gives (no parts, False), and the question then says to read the call elsewhere."""
    try:
        stmts = _statements(text)
        edge = len(text) - TAIL_CHARS
        shown = [e <= HEAD_CHARS or s >= edge for s, e in stmts]
        found: set[str] = set()
        got: dict[int, set[str]] = {}
        calls, unsure = 0, False
        for idx in sorted(range(len(stmts)), key=lambda k: shown[k]):   # the hidden statements first, so the cap never takes them
            s, e = stmts[idx]
            seg = text[s:e].strip()
            if _PLAIN.match(seg):
                got[idx] = {policy._segment_key(policy._bare(seg))} & keys
            elif calls >= ASSESS_CAP:
                unsure = True
            else:
                calls += 1
                got[idx] = set(policy.assess("Bash", {"command": seg}, cwd="").seg_keys) & keys
            found |= got.get(idx, set())
        hidden: list[tuple[int, int]] = []
        for idx, (_s, e) in enumerate(stmts):
            if shown[idx] or not got.get(idx):
                continue
            j = idx
            while j > 0 and not shown[j - 1] and _CD.match(text, stmts[j - 1][0]):   # a hidden `cd` or `pushd` right before it changes where it runs
                j -= 1
            if hidden and hidden[-1][1] >= e:
                continue
            hidden.append((stmts[j][0], e))
    except Exception:   # noqa: BLE001 - a command the segmenter cannot read is never a reason to show less
        return [], False
    return hidden, keys <= found and not unsure


READ_ELSEWHERE = "Deny it and ask the agent to run the risky command on its own, so that you can read all of it."
KEY_CAP = 200


def _gate_reading(keys: set[str]) -> str:
    """The commands the gate rated, in its own words (the canonical key: the program without its folder, launcher prefixes gone, every argument kept)."""
    shown = sorted(keys)[:GATED_PARTS]
    out = "\n".join(show_safely(k[:KEY_CAP], one_line=True) + (" ..." if len(k) > KEY_CAP else "") for k in shown)
    return out + (f"\n... and {len(keys) - len(shown)} more" if len(keys) > len(shown) else "")


def command_text(a, text: str) -> str:
    """The command as the human sees it: clipped, with every control, bidi and zero-width character written out. When the clip hides a part that the gate rated, that part is
    shown again in full (up to GATED_CAP characters). When this cannot find the part in the text (a quote, a line break or a here-document changes where a command starts), or the
    text is too long to search, the gate's own reading of what it rated is shown instead, and the question says that the whole text is not shown (V6-09)."""
    if _short(text):
        return show_safely(text)
    out = clip(text)
    keys = set(getattr(a, "seg_keys", None) or [])
    if len(text) > LOCATE_MAX:
        hidden, complete = [], False
    else:
        hidden, complete = _hidden_gated(keys, text) if keys else ([], False)
    if not complete:
        out += "\nJADE cannot show which part of this call is risky, and the middle of it is not shown. "
        out += (f"JADE rated this:\n{_gate_reading(keys)}\n" if keys else "") + READ_ELSEWHERE
    for s, e in hidden[:GATED_PARTS]:
        part = text[s:e].strip()
        cut = len(part) > GATED_CAP
        out += "\nA risky command in the part that is not shown above, " + ("its start" if cut else "in full") + f":\n{show_safely(part[:GATED_CAP])}"
        if cut:
            out += f"\n... {len(part) - GATED_CAP} more characters of it are not shown. {READ_ELSEWHERE}"
    if len(hidden) > GATED_PARTS:
        out += f"\n{len(hidden) - GATED_PARTS} more risky commands are in the part that is not shown. {READ_ELSEWHERE}"
    return out


ALLOW, DENY = "Allow", "Deny"
SUGGEST_PROJECT = "Allow, suggest for this project"
SUGGEST_ANY = "Allow, suggest for any project"
SUGGEST_LABELS = {"kind_project": SUGGEST_PROJECT, "merge_project": SUGGEST_PROJECT, "kind_any": SUGGEST_ANY}   # scopes.offered gives at most one project scope
SUGGEST_LATER = "You confirm it later in a terminal. Undo with `jade knows rm`."


def question_for(ask_id: str, a, tool: str, ctx: dict, scopes: list | None = None) -> dict:
    """The one question the human sees. Everything in it comes from the gate, none from the agent. `scopes` (from `scopes.offered`) adds one option per label:
    a click on it is the same single-use Allow, recorded with the scope as a hint that only `jade review confirm` makes standing."""
    lines = [f"[jade:ask_{ask_id}] JADE gate: {a.level} risk, rule {a.rule_id}.", *askplain.header(a, tool)]
    if a.ho_ids:
        lines.append("Classes: " + ", ".join(a.ho_ids) + " (only you can decide these)")
    lines.append(f"Tool: {show_safely(tool)}")
    lines.append("Command:\n" + command_text(a, str(ctx.get("subject") or a.subject)))
    content = ctx.get("content")
    if isinstance(content, dict):
        lines.append(f"It also writes {content.get('length')} characters of content (sha256 {str(content.get('sha256'))[:12]}). First part:\n{clip(str(content.get('text') or ''))}")
    if ctx.get("run_fields"):
        lines.append("It also runs with: " + ", ".join(f"{show_safely(str(k))}={show_safely(str(v))}" for k, v in ctx["run_fields"].items()))
    options = [{"label": ALLOW, "description": "Run this one call once. It is not remembered."},
               {"label": DENY, "description": "Do not run it. The agent is told you said no."}]
    seen: set[str] = set()
    for sc in scopes or []:
        label = SUGGEST_LABELS.get(sc.kind)
        if label is None or label in seen:
            continue
        seen.add(label)
        lines.append(f"{label}: {sc.label}")
        options.append({"label": label, "description": f"{sc.covers} {SUGGEST_LATER}"})
    lines.append(f"Folder: {clip(str(ctx.get('cwd') or '(not given)'))}")
    lines.append("Allow this one call? It covers exactly this text, this tool, this folder and any run settings shown above. Single use, 10 minutes, this session only. "
                 "A changed text, prefix or folder asks again.")
    return {"question": "\n".join(lines), "header": "JADE gate", "multiSelect": False, "options": options}


def _scope_for(row, label: str) -> dict | None:
    """The scope this ask stored for a suggest label, or None when the ask offered none under that label (a forged answer, or an ask from before scopes)."""
    try:
        stored = json.loads(row.scopes_json or "[]")
    except ValueError:
        return None
    for sc in stored if isinstance(stored, list) else []:
        if isinstance(sc, dict) and SUGGEST_LABELS.get(sc.get("kind")) == label:
            return sc
    return None


def refusal(db, session: str, hold: str, fpj: str, tool: str, where: str):
    """A call the human already answered no to: refused with no new ask. The window (10 minutes after a Deny) or the cap (the 3rd Deny of the same call, ever) is what ends asking."""
    from .hooks import HookResult

    n = db.one("SELECT COUNT(*) AS n FROM asks WHERE session_id=? AND status='DENIED' AND hold_key=?", (session, hold)).n
    if n >= DENY_CAP:
        return HookResult(2, f"JADE gate: the human denied this {tool} call {n} times. It is not asked again. Do not retry it or rephrase it to get around the answers; "
                             "tell the human what you need and why, or continue with other work.")
    denied = db.one("SELECT ask_id FROM asks WHERE session_id=? AND status='DENIED' AND hold_key=? AND fingerprints_json=? AND expires_ts>?", (session, hold, fpj, ts()))
    if denied is not None:
        return HookResult(2, f"JADE gate: the human said no to this {tool} call in {where} (ask {denied.ask_id}). Do not retry it unchanged and do not rephrase it to get "
                             "around the answer. Continue with other work, or tell the human what you need and why.")
    return None


def paused(db, session: str) -> bool:
    """EXPIRE_PAUSE asks of this session expired unanswered since the human last answered: the human is away, so no new ask is opened (it would only expire too). The call
    then takes the signed permit path, so the human finds something to approve when they are back."""
    since = ts(now() - timedelta(seconds=PAUSE_WINDOW_S))
    n = db.one("SELECT COUNT(*) AS n FROM asks WHERE session_id=? AND status IN ('EXPIRED','GAVE_UP') AND answered_ts IS NULL AND created_ts>=? "
               "AND created_ts>COALESCE((SELECT MAX(answered_ts) FROM asks WHERE session_id=?),'')", (session, since, session)).n
    return n >= EXPIRE_PAUSE


def gui_paused(db, session: str) -> bool:
    """Is an ask of this session open (the chat question not answered), or a dialog on screen for any session? Then the screen-control tools of the session are refused."""
    return db.one("SELECT 1 AS x FROM asks WHERE ((session_id=? AND status IN ('PENDING','SHOWN')) OR status='DIALOG') AND expires_ts>? LIMIT 1", (session, ts())) is not None   # a dialog on screen pauses every session of this Mac


def stop_nudge(db, session: str):
    """An ask opened for this session whose question the agent never put to the human: pointed out once per ask, when the agent tries to stop."""
    from .hooks import HookResult

    if paths.level() not in ("low", "medium", "high"):
        return None
    _expire(db)
    for row in db.all("SELECT ask_id FROM asks WHERE session_id=? AND status='PENDING' AND expires_ts>? ORDER BY created_ts", (session, ts())):
        if db.one("SELECT 1 AS x FROM events WHERE kind='ask_nudged' AND entity_id=? LIMIT 1", (row.ask_id,)) is not None:
            continue
        db.event(row.ask_id, "ask_nudged", guard="stop with an ask that never reached the question", actor="hook")
        return HookResult(2, "JADE: a gated call of this session is waiting for the human's answer in the chat. Call the AskUserQuestion tool now with ONE question whose text starts "
                             f"with [jade:ask_{row.ask_id}] and the options Allow and Deny, wait for the answer, then retry the identical call. This reminder is shown once per ask.")
    return None


def _project(cwd: str) -> str:
    """The folder name of the call's working directory, as plain text for a notification: letters, digits and a few separators, at most 40 characters."""
    name = re.sub(r"[^A-Za-z0-9._ -]", "", (cwd or "").rstrip("/").rsplit("/", 1)[-1])[:40].strip()
    return name or "a project"


def _expire(db) -> None:
    db.con.execute("UPDATE asks SET status='EXPIRED' WHERE status IN ('PENDING','SHOWN','ALLOWED') AND expires_ts<=?", (ts(),))


def run_decision(db, a, tool: str, cwd: str, session: str, ask_id: str, where: str) -> str:
    """Open the decision of a call the human allowed (in the chat or in a dialog), walked to EXECUTING so that SI-07 still holds an unknown outcome."""
    from . import decide, permits

    doc = decide.open_decision(db, f"Execute {tool}: {decide.scrub(a.subject)[:300]}", command=a.subject, tool=tool, cwd=cwd, session=session, route="ASK", assessment=a)
    did = doc["decision_id"]
    decide.step(db, did, "route", guard=f"route=ASK: the human answers in the {where}")
    decide.step(db, did, "escalate", guard=f"{where} ask {ask_id}", ttl_s=permits.DEFAULT_TTL_S)
    accept_why = {"memory": f"confirmed answer {ask_id} covers this call", "jev": f"Jev accepted this call in the owner's place (call_{ask_id}, within the level's band)"}.get(where)
    authorize_why = {"memory": f"memory: confirmed answer {ask_id}, within its lifetime", "jev": f"jev: accepted call_{ask_id}, within the level's band"}.get(where)
    decide.step(db, did, "human_accept", guard=accept_why or f"Allow answered in the {where} (ask {ask_id})", actor="human",
                patch={"ask": {"channel": where, "ask_id": ask_id, "answer": "allow", "answered_at": ts()}})
    decide.step(db, did, "authorize", permit_ok=True, guard=authorize_why or f"{where} grant {ask_id}: single use, unexpired, same session, same call")
    decide.step(db, did, "execute", guard=f"PreToolUse saw the matching call; {where} grant consumed", actor="hook", ttl_s=3600)
    return did


def step(db, mode: str, a, tool: str, event: dict, fp: dict, cwd: str):
    """The chat branch of the gate: a HookResult (run the call, or refuse it with the instruction) or None when this call does not take the chat channel."""
    from . import decide, permits, state_machine
    from .hooks import HookResult

    if not applicable(mode, a, event):
        return None
    ctx = permits.make_context(tool, a, event, cwd=cwd)
    if ctx.get("redacted"):
        return None   # a secret-shaped value is never stored or shown: the terminal permit path says what to do
    extras = run_fields(tool, event.get("tool_input"))
    keyed = memory.keyed_fp(a, fp)
    if extras:   # how the call runs is part of the call the human allows: the key, the open question and the question text all carry it
        keyed = {**keyed, "_run": sha256(canonical(extras))[:16]}
        ctx["run_fields"] = {k: str(v)[:80] for k, v in extras.items()}
    session, hold, fpj = event["session_id"], a.hold_key or a.plan_hash, canonical(keyed)
    _expire(db)
    try:
        with db.tx():
            row = db.one("SELECT ask_id FROM asks WHERE session_id=? AND status='ALLOWED' AND plan_hash=? AND hold_key=? AND fingerprints_json=? AND expires_ts>? ORDER BY answered_ts LIMIT 1",
                         (session, a.plan_hash, hold, fpj, ts())) if a.plan_hash else None   # V6-07: the grant is for this tool, this text and this folder; a row without the hash is never honoured
            if row is not None and db.con.execute("UPDATE asks SET status='CONSUMED' WHERE ask_id=? AND status='ALLOWED'", (row.ask_id,)).rowcount == 1:
                did = run_decision(db, a, tool, cwd, session, row.ask_id, "chat")
                db.con.execute("UPDATE asks SET decision_id=? WHERE ask_id=?", (did, row.ask_id))
                db.event(row.ask_id, "ask_consumed", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"decision_id": did})
                return HookResult(0, f"JADE: your Allow in the chat (ask {row.ask_id}) was used for this {tool} call. It is spent; the next identical call asks again.")
            if (res := refusal(db, session, hold, fpj, tool, "the chat")) is not None:
                return res
            open_ask = db.one("SELECT ask_id FROM asks WHERE session_id=? AND status IN ('PENDING','SHOWN') AND plan_hash=? AND hold_key=? AND fingerprints_json=? AND expires_ts>? ORDER BY created_ts DESC LIMIT 1",
                              (session, a.plan_hash, hold, fpj, ts()))   # an open question shows one text: a changed text gets its own question
            if open_ask is not None:
                ask_id = open_ask.ask_id
            else:
                if paused(db, session):
                    return None   # the human is away: the signed permit path, not another ask that would only expire
                ask_id = new_id("a")[2:]   # twelve hex characters
                offered = scopes_mod.offered(a, tool, event.get("tool_input"), fp, cwd)   # from the gate's own reading of the call, never from the agent
                q = question_for(ask_id, a, tool, ctx, offered)
                _, cls, _ = memory.keys(a, tool, event.get("tool_input"), fp)
                db.con.execute("INSERT INTO asks(ask_id, session_id, agent_id, plan_hash, hold_key, fingerprints_json, rule_id, level, tool, question_json, status, created_ts, expires_ts, source, class_key, label, ho, scopes_json) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?,'PENDING',?,?,?,?,?,?,?)",
                               (ask_id, session, event.get("agent_id"), a.plan_hash, hold, fpj, a.rule_id, a.level, tool, json.dumps(q), ts(), ts(now() + timedelta(seconds=ASK_TTL_S)), db.source, cls,
                                decide.scrub(a.subject).replace("\n", " ")[:120], 1 if a.ho_ids else 0, json.dumps([dataclasses.asdict(sc) for sc in offered])))
                db.event(ask_id, "ask_opened", guard=f"{a.rule_id} {a.level}", actor="hook", detail={"tool": tool, "ho_ids": list(a.ho_ids)})
                from . import askdialog

                askdialog.notify(f"A {a.level}-risk call (rule {a.rule_id}) waits for your answer in {_project(cwd)}. Open the chat.")   # one per new ask: a session in another project is otherwise a silent wait
    except state_machine.IllegalTransition as e:
        return HookResult(2, f"JADE gate: the chat approval for this {tool} call could not be used ({e}). Ask the human to run `jade decide list` and look at it.")
    return HookResult(2, f"JADE gate: {a.level} risk ({a.rule_id}: {a.rationale}). The human decides this {tool} call in the chat.\n"
                         f"Call the AskUserQuestion tool now with ONE question whose text starts with {f'[jade:ask_{ask_id}]'} and the options Allow and Deny. "
                         "JADE replaces the question text with the real one, so do not write the command into it. Wait for the answer. "
                         f"Then retry the identical {tool} call, unchanged. Do not answer the question yourself and do not rephrase the call to avoid the gate.")


def _token_in(obj) -> str | None:
    m = TOKEN.search(json.dumps(obj, ensure_ascii=False))
    return m.group(1) if m else None


def rewrite(event: dict):
    """PreToolUse on AskUserQuestion: a question that carries an ask token of this session is replaced by the hook's own; any other question passes."""
    from . import ledger
    from .hooks import LEDGER_TIMEOUT_S, HookResult

    token = _token_in(event.get("tool_input"))
    if token is None:
        return HookResult()
    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        _expire(db)
        row = db.one("SELECT ask_id, question_json, status FROM asks WHERE ask_id=? AND session_id=? AND status IN ('PENDING','SHOWN')", (token, event.get("session_id")))
        if row is None:
            return HookResult(2, f"JADE: the ask token [jade:ask_{token}] is unknown, expired or belongs to another session. Retry the gated call to get a new one; do not write tokens yourself.")
        db.con.execute("UPDATE asks SET status='SHOWN' WHERE ask_id=? AND status='PENDING'", (token,))
        db.event(token, "ask_shown", guard="question replaced by the hook", actor="hook")
        q = json.loads(row.question_json)
    finally:
        db.close()
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"questions": [q]}}}
    return HookResult(0, "", json.dumps(out, ensure_ascii=False))


def settle(event: dict):
    """PostToolUse on AskUserQuestion: read the human's answer from the host's response and close the ask."""
    from . import ledger
    from .hooks import LEDGER_TIMEOUT_S, HookResult

    resp = event.get("tool_response")
    answers = resp.get("answers") if isinstance(resp, dict) else None
    if not isinstance(answers, dict):
        return HookResult()
    db = ledger.open(timeout=LEDGER_TIMEOUT_S)
    try:
        for question, label in answers.items():
            m = TOKEN.search(str(question))
            if not m or label not in (ALLOW, DENY, *SUGGEST_LABELS.values()):
                continue
            row = db.one("SELECT * FROM asks WHERE ask_id=? AND session_id=? AND status='SHOWN'", (m.group(1), event.get("session_id")))
            if row is None:
                continue
            scope = _scope_for(row, label) if label in SUGGEST_LABELS.values() else None
            if label in SUGGEST_LABELS.values() and scope is None:
                continue   # a suggest label this ask did not offer (a forged answer): nothing is decided
            allow = label != DENY
            until = ts(now() + timedelta(seconds=GRANT_TTL_S)) if allow else ts(now() + timedelta(seconds=ASK_TTL_S))
            with db.tx():
                if db.con.execute("UPDATE asks SET status=?, answered_ts=?, expires_ts=? WHERE ask_id=? AND status='SHOWN'",
                                  ("ALLOWED" if allow else "DENIED", ts(), until, row.ask_id)).rowcount != 1:
                    continue
                db.event(row.ask_id, "ask_allowed" if allow else "ask_denied", guard=f"{row.rule_id} {row.level}", actor="human", detail={"channel": "chat"})
                db.record_human_answer(session=row.session_id, decision_id=row.decision_id, rule_id=row.rule_id, class_key=row.class_key,
                                       exact_key=memory.exact_key(row.hold_key, row.fingerprints_json), answer="allow" if allow else "deny", channel="chat", label=row.label, ho=bool(row.ho), scope=scope)
    finally:
        db.close()
    return HookResult()
