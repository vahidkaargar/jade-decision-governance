"""`jade` command line. Every subcommand is small; behaviour lives in the modules."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import ask, ledger, paths, questions, redact
from .util import human_tty_refusal, is_human_tty, session_id, ts

LEGACY = ("verify", "noul", "choice", "score")


# ---- ask -------------------------------------------------------------------------------------------
def _read_file(path: str, what: str) -> str:
    """A file the caller named. One that cannot be read (missing, a directory, no permission) is an invalid question (exit 6), not a traceback."""
    try:
        return Path(path).expanduser().read_text(errors="replace")
    except (OSError, UnicodeError) as e:
        raise questions.InvalidQuestion(f"{what} cannot be read ({type(e).__name__})") from None


def _read_state(a) -> tuple[str, str]:
    if getattr(a, "state_file", None):
        return _read_file(a.state_file, "the state file"), f"file:{a.state_file}"
    if a.state == "-":
        try:
            return sys.stdin.read(), "stdin"
        except (OSError, UnicodeError) as e:
            raise questions.InvalidQuestion(f"stdin cannot be read as text ({type(e).__name__})") from None
    return a.state or "", "inline"


def ask_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="Ask Jev a typed question. Exit 0 accept, 3 abstain, 4 reject, 5 provider unavailable, "
                                                       "6 invalid question. Only 0 is a yes. State is redacted, then sent to openrouter.ai.")
    common = argparse.ArgumentParser(add_help=False)
    for target, default in ((p, None), (common, argparse.SUPPRESS)):
        target.add_argument("--model", default=default)
        target.add_argument("--raw", action="store_true", default=False if default is None else default, help="print the full provider JSON")
        target.add_argument("--dry-run", action="store_true", default=False if default is None else default, help="print the payload, send nothing")
        target.add_argument("--json", action="store_true", default=False if default is None else default, help="machine-readable result")
        target.add_argument("--risk", choices=("low", "medium", "high", "critical"), default="low" if default is None else default)
        target.add_argument("--decision", default=default, help="decision id to attach this evaluation to")
        target.add_argument("--no-companion", action="store_true", default=False if default is None else default)
        target.add_argument("--no-cache", action="store_true", default=False if default is None else default)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in LEGACY:
        sp = sub.add_parser(name, parents=[common])
        sp.add_argument("text", help="claim or question")
        sp.add_argument("-s", "--state", default="", help="evidence text, or - for stdin")
        sp.add_argument("--state-file", help="read the evidence from a file (preferred: raw tool output)")
    sub.choices["noul"].add_argument("-t", "--true", required=True)
    sub.choices["noul"].add_argument("-f", "--false", required=True)
    sub.choices["choice"].add_argument("-o", "--option", action="append", default=[], help="name=description")
    sub.choices["score"].add_argument("-c", "--level", action="append", default=[], help="ordered level, lowest first")
    q = sub.add_parser("question", parents=[common], help="ask a registry question by id")
    q.add_argument("question_id")
    q.add_argument("--field", action="append", default=[], help="state field: name=value or name=@file")
    q.add_argument("-o", "--option", action="append", default=[])
    return p


def ask_main(argv: list[str], prog: str = "jade ask", provider=None) -> int:
    p = ask_parser(prog)
    a = p.parse_args(argv)
    try:
        if a.cmd == "verify":
            evidence, source = _read_state(a)
            specs = ask.verify_specs(companion=not a.no_companion)
            state = {"claim": a.text, "evidence": evidence, "evidence_source": source}
            ask.check_required("q.verify_claim.supported_by_evidence", state)   # a claim with no evidence is not sent (the legacy script sent it)
        elif a.cmd == "question":
            state = {}
            for f in a.field:
                k, sep, v = f.partition("=")
                if not sep:
                    raise questions.InvalidQuestion(f"--field must be name=value ({len(f)} characters given, none of them an '=')")
                state[k.strip()] = _read_file(v[1:], f"field {k.strip()!r} file") if v.startswith("@") else v
            ask.check_required(a.question_id, state)
            specs = [ask.registry_spec(a.question_id, options=a.option or None)]
        else:
            evidence, _ = _read_state(a)
            specs = [ask.adhoc_spec(a.cmd, a.text, options=getattr(a, "option", None), levels=getattr(a, "level", None),
                                    true=getattr(a, "true", None), false=getattr(a, "false", None))]
            state = {"context": evidence}
        ask.check_text(specs, state)
    except questions.InvalidQuestion as e:
        why = redact.redact(str(e))[0]   # an error may quote what the agent typed, and an agent's transcript is not a safe place for a secret
        print(f"jade: INVALID_QUESTION, exit 6 ({why}). Nothing was sent; this is not a yes.", file=sys.stderr)
        _journal_invalid(why)
        return 6
    if a.dry_run:
        state, hits, cut, leaked = ask.prepare(specs, state)   # the same redaction a real call applies: a dry run shows what would leave the machine
        print(json.dumps({"model": ask.resolve_model(a.model), "state": state, "questions": {s.key: s.payload for s in specs}}, ensure_ascii=False, indent=2))
        print(f"jade: dry run: {hits} redaction(s){', state truncated to 32 KiB' if cut else ''}; "
              f"{'a secret-shaped string survived redaction, a real call would be refused (exit 6)' if leaked else 'no secret-shaped string left'}", file=sys.stderr)
        return 0
    result = ask.run(specs, state, risk=a.risk, decision_id=a.decision, provider=provider, model=a.model, use_cache=not a.no_cache)
    return ask.emit(result, raw=a.raw, as_json=a.json)


def _journal_invalid(reason: str) -> None:
    try:
        db = ledger.open()
        db.event(session_id(), "INVALID_QUESTION", guard=reason[:300], actor="agent")
        db.close()
    except Exception:  # noqa: BLE001, S110 - journaling is best effort here
        pass


# ---- other commands ----------------------------------------------------------------------------------
def _stats(a) -> int:
    db = ledger.open()
    w = "ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?) AND COALESCE(primitive,'')<>'fast' AND " + ledger.live()   # `fast` rows from an older version are not Jev calls; rows of a `JADE_SOURCE=test` run are not live data (W-72)
    arg = (f"-{a.days} days",)
    one = lambda sql: db.one(sql, arg)  # noqa: E731
    total = one(f"SELECT COUNT(*) AS n FROM jev_calls WHERE {w}").n  # noqa: S608 - constant fragment
    out = {"window_days": a.days, "calls": total}
    if total:
        r = one(f"SELECT COUNT(DISTINCT substr(ts,1,10)) AS days, SUM(dedup_of IS NOT NULL) AS repeats, SUM(error_kind IS NOT NULL AND error_kind<>'TOO_MANY_IN_FLIGHT') AS errors, "  # noqa: S608
                f"SUM(exit_code=0) AS accept, SUM(exit_code=3) AS abstain, SUM(exit_code=4) AS reject, SUM(exit_code IN (5,6)) AS no_answer, "
                f"ROUND(SUM(cost_usd),6) AS cost, SUM(redactions>0) AS redacted, COUNT(DISTINCT session_id) AS sessions FROM jev_calls WHERE {w}")
        v = one(f"SELECT COUNT(*) AS n, ROUND(AVG(p),3) AS mean, SUM(p<0.70) AS below FROM jev_calls WHERE question_id='q.verify_claim.supported_by_evidence' "  # noqa: S608
                f"AND p IS NOT NULL AND dedup_of IS NULL AND {w}")
        lat = [x.latency_ms for x in db.all(f"SELECT latency_ms FROM jev_calls WHERE latency_ms>0 AND {w} ORDER BY latency_ms", arg)]  # noqa: S608
        und = one(f"SELECT COUNT(*) AS n FROM jev_calls WHERE exit_code IN (3,4) AND disposition IS NULL AND reminded=1 AND {w}").n  # noqa: S608
        nonacc = (r.abstain or 0) + (r.reject or 0)
        pct = lambda q: round(lat[min(len(lat) - 1, int(q * (len(lat) - 1)))]) if lat else None  # noqa: E731
        out.update({"days_with_calls": r.days, "calls_per_day": round(total / max(1, r.days), 1), "sessions": r.sessions,
                    "verify": {"n": v.n, "p_mean": v.mean, "share_below_0.70": round((v.below or 0) / v.n, 3) if v.n else None},
                    "exit_codes": {"accept_0": r.accept, "abstain_3": r.abstain, "reject_4": r.reject, "no_answer_5_6": r.no_answer},
                    "repeat_share": round((r.repeats or 0) / total, 3), "error_share": round((r.errors or 0) / total, 3),
                    "non_accept_reminded_without_disposition": und, "share_of_non_accept": round(und / nonacc, 3) if nonacc else None,
                    "dispositions": {x.disposition: x.n for x in db.all(f"SELECT disposition, COUNT(*) AS n FROM jev_calls WHERE disposition IS NOT NULL AND {w} GROUP BY disposition", arg)},  # noqa: S608
                    "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99)}, "cost_usd": r.cost, "calls_with_redactions": r.redacted,
                    "events": {x.kind: x.n for x in db.all("SELECT kind, COUNT(*) AS n FROM events WHERE kind IN ('BYPASS','MODEL_DRIFT','INVALID_QUESTION','would_block','safety_block','secret_read','blind_spot') AND " + ledger.live() + " GROUP BY kind")}})
    print(json.dumps(out, indent=1))
    return 0


def _permit(a) -> int:
    from . import decide, hooks, permits, policy

    db = ledger.open()
    if a.action == "list":
        for p in permits.listing(db, ("PENDING", "ISSUED") if not a.all else ("PENDING", "ISSUED", "CONSUMED", "REVOKED", "EXPIRED")):
            print(f"{p.permit_id}  {p.state:8} {p.risk_level:8} expires {p.expires_at}{'  [test]' if getattr(p, 'source', None) == 'test' else ''}  {permits.printable(_subject(p))[:100].replace(chr(10), ' ')}")
        return 0
    if a.action == "request":
        cwd = a.cwd or str(Path.cwd())
        assess = policy.assess("Bash", {"command": a.command}, cwd)
        p = permits.request(db, decide.canonical_action(assess.rule_id, a.command), assess.plan_hash, risk=assess.level if assess.level in ("high", "critical") else "high",
                            context=permits.make_context("Bash", assess, cwd=cwd), fingerprints=hooks.fingerprints(a.command, cwd), environment=assess.environment)
        print(f"{p.permit_id} PENDING. A human approves it in a terminal: jade permit approve {p.permit_id}")
        return 0
    p = permits.get(db, a.permit_id)
    if p is None:
        if db.one("SELECT 1 AS x FROM permits WHERE permit_id=?", (a.permit_id,)) is None:
            print(f"jade: unknown permit {a.permit_id}", file=sys.stderr)
            return 1
        if a.action != "revoke":   # a stored record that cannot be read is never shown, approved or used, but it can be revoked
            print(f"jade: permit {a.permit_id} has an unreadable or invalid stored record (`jade check` lists it); revoke it with `jade permit revoke {a.permit_id}`", file=sys.stderr)
            return 1
    if a.action == "show" and a.raw:   # W-43: the raw call, secrets included, for a human terminal only (SI-09 blocks the spelling for an agent)
        why = human_tty_refusal()
        if why:
            print(f"jade: the raw view of a permit needs a human at an interactive terminal; an agent session cannot see it ({why})", file=sys.stderr)
            return 1
        try:
            print(permits.review_raw(db, p))
        except permits.PermitError as e:
            print(f"jade: {e}", file=sys.stderr)
            return 1
        return 0
    if a.action == "show":
        print(json.dumps({k: v for k, v in vars(p).items() if k != "nonce"}, indent=1, default=str))
        return 0
    if a.action == "revoke":
        print("revoked" if permits.revoke(db, a.permit_id, "revoked from CLI") else "nothing to revoke")
        return 0
    try:  # approve
        why = human_tty_refusal()
        if why:
            raise permits.ApprovalRefused(f"permit approval needs a human at an interactive terminal; an agent session cannot approve ({why})")
        if a.yes:
            raise permits.PermitError("-y is not accepted: read the command, then type the code shown")
        print(permits.review_raw(db, p))  # raises instead of showing a command that was cut or does not match the plan hash; shows a secret only from the raw call W-43 keeps
        code = permits.confirm_code(p)
        if input(f"Type {code} to approve, anything else to refuse: ").strip().lower() != code:
            print("not approved")
            return 1
        p = permits.approve(db, a.permit_id)
        print(f"ISSUED until {p.expires_at}. The agent can now retry the identical call.")
        return 0
    except (permits.ApprovalRefused, permits.PermitError) as e:
        print(f"jade: {e}", file=sys.stderr)
        return 1


def _subject(p) -> str:
    from . import permits

    return permits.ctx_of(p).get("subject", p.authorized_action)


def _decide(a) -> int:
    from . import decide, state_machine

    db = ledger.open()
    if a.action == "open":
        doc = decide.open_decision(db, a.objective, command=a.command, evidence_refs=a.evidence, raise_risk_to=a.risk)
        print(json.dumps({"decision_id": doc["decision_id"], "status": doc["status"], "risk": doc["risk"]["level"], "rule_id": doc["risk"]["rule_id"]}))
        return 0
    if a.action == "list":
        q = "SELECT decision_id, status, risk_level, rule_id, updated_at, substr(json_extract(doc_json,'$.objective'),1,80) AS o FROM decisions"
        rows = db.all(q + (" WHERE status=?" if a.status else "") + " ORDER BY updated_at DESC LIMIT 50", (a.status,) if a.status else ())
        for r in rows:
            print(f"{r.decision_id}  {r.status:16} {r.risk_level:8} {r.rule_id:6} {r.updated_at}  {r.o}")
        return 0
    if a.action == "show":
        doc = decide.get(db, a.decision_id)
        if doc is None:
            print("unknown decision", file=sys.stderr)
            return 1
        doc["_events"] = [vars(e) for e in db.all("SELECT kind, prev_state, next_state, guard, actor, ts FROM events WHERE entity_id=? ORDER BY id", (a.decision_id,))]
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        return 0
    try:
        if a.action == "outcome":
            success = None if a.type == "unknown" else a.success == "true"
            out = decide.record_outcome(db, a.decision_id, a.type, success, a.method, evidence_refs=a.evidence, caller_session=session_id())
            print(json.dumps({"outcome_id": out["outcome_id"], "status": decide.get(db, a.decision_id)["status"]}))
            return 0
        event = a.event
        row = db.one("SELECT status, risk_level FROM decisions WHERE decision_id=?", (a.decision_id,))
        if row is None:
            print(f"jade: refused: unknown decision {a.decision_id}", file=sys.stderr)
            return 1
        human = event in state_machine.HUMAN_EVENTS or (event == "cancel" and row.status in state_machine.HOLDING)
        if human:
            why = human_tty_refusal()
            if why:
                print(f"jade: {'human events' if event in state_machine.HUMAN_EVENTS else 'cancelling a decision whose outcome is unknown or still running (it lifts the SI-07 hold)'} "
                      f"need an interactive human terminal ({why})", file=sys.stderr)
                return 1
        elif not state_machine.agent_may(event, row.status, row.risk_level):
            print(f"jade: refused: '{event}' is not an event an agent session may send. Evaluation results, evidence, escalation and cancel are; "
                  "execution is the hook's job (with a permit at risk >= high), and verified/failed/unknown follow only from `jade decide outcome`.", file=sys.stderr)
            return 1
        res = decide.step(db, a.decision_id, event, guard=a.note or "", actor="human" if human else "agent", caller_session=session_id())
        print(json.dumps({"decision_id": a.decision_id, "status": res.status}))
        return 0
    except (state_machine.IllegalTransition, ValueError) as e:
        print(f"jade: refused: {e}", file=sys.stderr)
        return 1


def _ack(a) -> int:
    from . import hooks

    db = ledger.open()
    if not a.all and not a.call_ids:
        print("nothing to acknowledge")
        return 0
    res = hooks.acknowledge(db, session_id(), a.call_ids, all_=a.all, disposition=a.as_, note=a.note, human=is_human_tty())
    if res.refused:
        print(f"jade: ack refused: {res.message}", file=sys.stderr)
        return 1
    print(f"recorded disposition '{a.as_}' for {res.count} call(s)" if res.count else "nothing to acknowledge")
    return 0


def _question(a) -> int:
    if a.action == "list":
        for e in questions.entries():
            print(f"{e['question_id']}@{e['version']}  {e['primitive']:6} {e['task_family']}")
        return 0
    from policies.loader import RoutingError

    try:
        found = questions.validate_registry()
    except RoutingError as e:   # the registry check reads the routing limits
        print(f"jade: the routing policy cannot be used ({e})", file=sys.stderr)
        return 1
    for key, problems in found.items():
        print(f"{key}:\n  " + "\n  ".join(problems))
    print(f"{len(questions.entries())} entries, {len(found)} with problems")
    return 1 if found else 0


def _route(a) -> int:
    from policies.loader import RoutingError

    from . import route

    fresh = None
    if (a.observed_at is None) != (a.ttl_s is None):
        print("jade: --observed-at and --ttl-s go together", file=sys.stderr)
        return 2
    if a.observed_at is not None:
        fresh = {"observed_at": a.observed_at, "ttl_s": a.ttl_s}
    state = None
    try:
        if a.question_id:
            state = {}
            for f in a.field:
                k, sep, v = f.partition("=")
                if not sep:
                    raise questions.InvalidQuestion("--field must be name=value or name=@file")
                state[k.strip()] = _read_file(v[1:], f"field {k.strip()!r} file") if v.startswith("@") else v
        d = route.preflight(claim=a.claim or "", evidence=Path(a.evidence_file).read_text(errors="replace") if a.evidence_file else (a.evidence or ""),
                            options=a.option, command=a.command, risk=a.risk, about_to_claim_done=a.done, deterministic=a.deterministic,
                            question_id=a.question_id, state=state, context_freshness=fresh)
    except questions.InvalidQuestion as e:
        print(f"jade: INVALID_QUESTION, exit 6 ({redact.redact(str(e))[0]})", file=sys.stderr)
        return 6
    except RoutingError as e:
        print(f"jade: the routing policy cannot be used ({e})", file=sys.stderr)
        return 1
    print(json.dumps(vars(d), indent=1))
    return 0


def _fast(a) -> int:
    from . import route

    ok, detail = route.fast_check(a.kind, a.args)
    try:   # a deterministic check is no Jev answer: it is an event, never a jev_calls row (no dedup, no stats, no miner pattern, no Stop-hook reminder)
        db = ledger.open()
        db.event(session_id(), "FAST_CHECK", guard=f"{a.kind}: {'true' if ok else 'false'}", detail={"kind": a.kind, "ok": ok, "args": " ".join(a.args)[:500]}, actor="agent")
        db.close()
    except Exception:  # noqa: BLE001, S110 - the check's own answer must not depend on the journal
        pass
    print(f"{'true' if ok else 'false'}  {detail}")
    return 0 if ok else 4


def _work(a) -> int:
    from . import work

    src = Path(a.file)
    try:
        items = work.load(src)
    except (OSError, ValueError) as e:
        print(f"jade: {e}", file=sys.stderr)
        return 1
    if a.action == "list":
        for w in items:
            if a.all or w["status"] not in ("done", "dropped"):
                print(f"{w['id']}  {w['priority']} {w['status']:13} {w['owner']:7} {w.get('title', '')}")
        return 0
    announce: list[dict] = []
    if a.action == "add":
        missing = [flag for flag, val in (("--title", a.title), ("--impact", a.impact), ("--rationale", a.rationale), ("--next", a.next), ("--trigger", a.trigger)) if not val]
        if missing:
            print(f"jade: work add needs {', '.join(missing)}", file=sys.stderr)
            return 1
        try:
            w = work.add(items, title=a.title, impact=a.impact, rationale=a.rationale, next_action=a.next, trigger=a.trigger, type=a.type, priority=a.priority,
                         owner=a.owner, dependencies=a.dep or [], evidence_refs=a.evidence or [], parent=a.parent, phase=a.phase)
        except ValueError as e:   # DuplicateItem is one
            print(f"jade: {e}", file=sys.stderr)
            return 1
        print(f"added {w['id']}: {w['title']}")
    if a.action == "set":
        try:
            w = work.update(items, a.id, status=a.status, note=a.note or "", next_action=a.next, trigger=a.trigger)
        except KeyError:
            print(f"jade: no work item {a.id!r} in {a.file}", file=sys.stderr)
            return 1
        if a.status == "done":
            announce = work.ready(items, closed=w["id"])
    aged = work.age(items)
    work.save(src, items)
    work.render(src, Path(a.out))
    if a.action in ("sync", "set", "add"):
        mirror, dups = work.dedupe(items)   # a duplicate stays in the file and out of the ledger, so the next session is not told twice
        db = ledger.open()
        with db.tx() as con:
            for w in mirror:
                con.execute("INSERT OR REPLACE INTO work_items(id, status, doc_json, updated_at) VALUES(?,?,?,?)", (w["id"], w["status"], json.dumps(w), w["updated_at"]))
            for dup, first in dups.items():
                con.execute("DELETE FROM work_items WHERE id=?", (dup,))
                print(f"not mirrored: {dup} is a duplicate of {first} (same normalised title and target)")
    print(f"rendered {a.out} from {a.file} ({len(items)} items" + (f", aged {', '.join(aged)}" if aged else "") + ")")
    if announce:
        print("ready: " + ", ".join(w["id"] for w in announce) + f" (all dependencies done; {a.id} closed)")
    return 0


def _label(a) -> int:
    from . import evals

    db = ledger.open()
    if a.action == "queue":
        rows = evals.adjudication_queue(db)
        for r in rows:
            print(f"{r.call_id}  P={r.p:.2f} {r.verdict:8} {r.ts}  {r.q}")
        print(f"{len(rows)} to adjudicate (cap {evals.ADJUDICATION_PER_WEEK}/week): jade label set <call_id> yes|no")
        return 0
    # W-59: a label is calibration input (docs/04, docs/05 T24). Writing one, or joining outcomes into labels, is a human's job at a terminal.
    if not is_human_tty():
        print("jade: calibration labels are written by a human at a terminal (adjudication is a human job, docs/04); an attempt from an agent session is journaled", file=sys.stderr)
        db.event(session_id(), "LABEL_REFUSED", actor="agent", detail={"command": f"label {a.action}", "call_id": getattr(a, "call_id", None)})
        return 1
    if a.action == "auto":
        n = evals.auto_label(db)
        db.event(session_id(), "LABEL_AUTO", actor="human", detail=n)
        print(json.dumps(n))
        return 0
    if db.get_call(a.call_id) is None:
        print(f"jade: unknown call {a.call_id}", file=sys.stderr)
        return 1
    evals.set_label(db, a.call_id, 1 if a.value == "yes" else 0, "adjudicated", a.note or "")
    db.event(a.call_id, "LABEL_SET", next=a.value, guard=a.note or "", actor="human", detail={"source": "adjudicated"})
    print("labeled")
    return 0


def _eval(a) -> int:
    from . import evals

    if a.action == "routing":   # offline: no ledger is opened
        f = Path(a.cases) if a.cases else (paths.checkout_dir() or Path.cwd()) / "evals" / "routing" / "cases.jsonl"
        if not f.is_file():
            print(f"jade: {f} does not exist; pass --cases", file=sys.stderr)
            return 1
        try:
            res = evals.routing_confusion_from_cases(evals.load_cases(f))
        except (ValueError, KeyError, TypeError, AttributeError) as e:   # a malformed case file is the person's to fix, not a traceback
            print(f"jade: {f} is not a valid routing case file: {e}", file=sys.stderr)
            return 1
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0
    db = ledger.open()
    if a.action == "run":
        if a.protected and not is_human_tty():
            print("jade: --protected needs a human terminal", file=sys.stderr)
            return 1
        print(json.dumps(evals.report(db, include_protected=a.protected), indent=1))
    elif a.action == "conditions":
        print(json.dumps(evals.conditions(db), indent=1))
    else:  # propose
        from . import improve, thresholds

        groups: dict[str, list[dict]] = {}
        for r in evals.labeled(db):
            if r["split"] in ("dev", "calibration"):
                groups.setdefault(thresholds.group_of(r["task_family"], r["question_version"], r["resolved_model"]), []).append(r)
        made = []
        for g, rows in groups.items():
            prop = evals.propose_threshold(rows)
            cur = thresholds.bands("noul", rows[0]["task_family"], "low", g)[0]
            if prop and abs(prop["accept"] - cur) >= 0.01:
                c = improve.propose("calibration_threshold", f"{g}: accept {cur:.2f} -> {prop['accept']:.2f}",
                                    f"Wilson 95% upper bound on false accepts is {prop['upper95']} at {prop['accept']:.2f} on {len(rows)} labels",
                                    baseline_version=f"{g}@{cur:.2f}", path=db)
                improve.replay_threshold(db, c.candidate_id, g, prop["accept"])
                made.append(c.candidate_id)
        print(json.dumps({"groups": {g: len(r) for g, r in groups.items()}, "min_labeled": evals.MIN_LABELED, "candidates": made}, indent=1))
    return 0


def evals_denied():
    from . import evals

    return evals.ProtectedSetAccessDenied


def _improve(a) -> int:
    from . import improve

    db = ledger.open()
    home = paths.home()
    if a.action == "mine":
        print(json.dumps(improve.mine(db, a.days), indent=1))
    elif a.action == "list":
        for r in db.all("SELECT candidate_id, decision, updated_at, CASE WHEN json_valid(doc_json) THEN json_extract(doc_json,'$.target_component') END AS t, "
                        "CASE WHEN json_valid(doc_json) THEN substr(json_extract(doc_json,'$.hypothesis'),1,90) END AS h FROM improvements ORDER BY updated_at DESC"):
            print(f"{r.candidate_id}  {r.decision or '?':11} {r.t or '(unreadable record)':22} {r.h or ''}")
    elif a.action == "show":
        try:
            print(json.dumps(improve.get(db, a.candidate_id), indent=1))
        except improve.PromotionBlocked as e:
            print(f"jade: {e}", file=sys.stderr)
            return 1
    elif a.action == "shadow":
        try:
            print(json.dumps(improve.shadow(db, a.candidate_id, a.question_id, a.version, limit=a.limit), indent=1))
        except improve.PromotionBlocked as e:
            print(f"jade: blocked: {e}", file=sys.stderr)
            return 1
    elif a.action in ("protected", "canary"):
        try:
            doc = improve.protected_check(db, a.candidate_id) if a.action == "protected" else improve.start_canary(db, a.candidate_id)
            print(json.dumps((doc.get("results") or {}).get("protected" if a.action == "protected" else "canary"), indent=1))
        except (improve.PromotionBlocked, evals_denied()) as e:
            print(f"jade: blocked: {e}", file=sys.stderr)
            return 1
    elif a.action in ("freeze", "unfreeze"):
        if a.action == "unfreeze" and not is_human_tty():
            print("jade: only a human at a terminal removes the kill switch", file=sys.stderr)
            return 1
        (home / "FREEZE").write_text("frozen\n") if a.action == "freeze" else (home / "FREEZE").unlink(missing_ok=True)
        print(f"FREEZE {'present' if improve.frozen(home) else 'absent'}")
    else:
        try:
            doc = improve.promote(db, a.candidate_id) if a.action == "promote" else improve.rollback(db, a.candidate_id)
            print(f"{doc['candidate_id']} {doc['promotion_decision']}")
        except improve.PromotionBlocked as e:
            print(f"jade: blocked: {e}", file=sys.stderr)
            return 1
    return 0


def _ops(a) -> int:
    from . import ops

    try:
        if a.cmd == "hooks":
            if a.action == "status":
                st = ops.hooks_status()
                print("\n".join(f"{'installed' if v else 'MISSING  '}  {k}" for k, v in st.items()))
                return 0 if all(st.values()) else 1
            if a.action == "install":
                backup = ops.hooks_install()
                print(f"hooks installed in {ops.settings_path()} ({f'backup {backup}' if backup else 'no earlier file, no backup'}). New sessions pick them up.")
            else:
                print(f"removed {ops.hooks_uninstall()} hook entr(ies)")
        elif a.cmd == "gate":
            if a.action == "status":
                print(paths.gate_mode())
            else:
                ops.set_gate_mode(a.action, allow_unsigned=a.allow_unsigned)
                print(f"gate mode {a.action}")
        elif a.cmd == "review":
            print(ops.review(ops.parse_since(a.since) if a.since else None))
        elif a.cmd == "level":
            if a.value is None:
                print(paths.level() or "unset")
            else:
                ops.set_level(a.value)
                print(f"level {a.value}")
        elif a.cmd == "claude-md":
            if a.action == "show":
                print(ops.claude_md_section())
            else:
                print(f"CLAUDE.md Jev section replaced (backup {ops.claude_md_apply()})")
        return 0
    except (ops.HumanOnly, ops.WiringError, ValueError, OSError) as e:
        print(f"jade: {e}", file=sys.stderr)
        return 1


def _git_hook(a) -> int:
    from . import githook

    if a.action == "script":
        print(githook.script(), end="")
        return 0
    guarded, lines = githook.check(a.repo)
    print("\n".join(lines))
    print("guarded" if guarded else "NOT GUARDED")
    return 0 if guarded else 1


def _maintain(a) -> int:
    """Daily job: expiries, retention, drift canary, miner. Prints one JSON line for the log."""
    from . import decide, improve, ops, permits

    db = ledger.open()
    out = {"at": ts(), "decisions": decide.sweep(db), "permits_expired": permits.sweep(db),
           "purged": ops.purge(db, int(paths.config().get("retention_days", 90)))}
    rows = ops.check(live=not a.offline)
    out["check_failed"] = [n for n, ok, _ in rows if not ok]
    out["mined"] = [] if improve.frozen(paths.home()) else improve.mine(db)
    print(json.dumps(out))
    return 1 if out["check_failed"] else 0


def _days(value: str) -> int:
    """`90` or `90d`: a number of days (docs/05 T15 writes the second form)."""
    m = re.fullmatch(r"(\d+)d?", value.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"invalid number of days: {value!r} (use 90 or 90d)")
    return int(m.group(1))


class _VersionAction(argparse.Action):
    """`jade --version`: the code version, commit, ledger schema version and policy_version (the same text as `jade version`)."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):  # noqa: A002 - argparse's own signature
        super().__init__(option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        from . import upgrade

        sys.stdout.write(upgrade.version_text(upgrade.version_info()))
        parser.exit()


def build_parser() -> argparse.ArgumentParser:
    """The `jade` command tree (everything but `ask` and `hook`, which `main` hands off first). Also read by the docs test."""
    p = argparse.ArgumentParser(prog="jade", description="Adaptive decision governance for Jev. `jade ask -h` for questions.")
    p.add_argument("--version", action=_VersionAction, help="print the code version, commit, ledger schema version and policy_version")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ask", help="ask Jev (see `jade ask -h`)")
    sub.add_parser("hook", help="Claude Code hook entry: pretool | posttool | posttool-failure | stop | session-start")

    s = sub.add_parser("ledger").add_subparsers(dest="action", required=True)
    st = s.add_parser("stats")
    st.add_argument("--days", type=int, default=14)
    pg = s.add_parser("purge")
    pg.add_argument("--older-than", type=_days, default=90, metavar="DAYS", help="retention window in days: 90 or 90d (default 90)")
    pg.add_argument("--test-rows", action="store_true", help="instead of the retention purge, remove the rows a run under JADE_SOURCE=test left in the ledger")
    pg.add_argument("--id", action="append", default=[], metavar="ID", help="with --test-rows: a permit or decision id written before the tag existed (repeatable)")

    c = sub.add_parser("check", help="probe ledger, policies, registry, hooks, shim and the provider; non-zero on any failure")
    c.add_argument("--offline", action="store_true")

    pm = sub.add_parser("permit").add_subparsers(dest="action", required=True)
    pm.add_parser("list").add_argument("--all", action="store_true")
    rq = pm.add_parser("request")
    rq.add_argument("--command", required=True)
    rq.add_argument("--cwd")
    for name in ("show", "approve", "revoke"):
        x = pm.add_parser(name, allow_abbrev=False) if name == "show" else pm.add_parser(name)   # `--raw` is spelled out: SI-09 lists that spelling only
        x.add_argument("permit_id")
        if name == "show":
            x.add_argument("--raw", action="store_true", help="human terminal only: the whole command with the secret-shaped values the ledger stores redacted (needs `permit_reveal` in the config)")
        if name == "approve":
            x.add_argument("-y", "--yes", action="store_true", help="not accepted: approval always needs the code typed after the command is shown")

    d = sub.add_parser("decide").add_subparsers(dest="action", required=True)
    o = d.add_parser("open")
    o.add_argument("--objective", required=True)
    o.add_argument("--command")
    o.add_argument("--evidence", action="append", default=[])
    o.add_argument("--risk", choices=("low", "medium", "high", "critical"))
    d.add_parser("list").add_argument("--status")
    d.add_parser("show").add_argument("decision_id")
    ev = d.add_parser("event")
    ev.add_argument("decision_id")
    ev.add_argument("event")
    ev.add_argument("--note")
    oc = d.add_parser("outcome")
    oc.add_argument("decision_id")
    oc.add_argument("--type", choices=("observed", "estimated", "unknown"), required=True)
    oc.add_argument("--success", choices=("true", "false"))
    oc.add_argument("--method", required=True)
    oc.add_argument("--evidence", action="append", default=[])

    k = sub.add_parser("ack", help="record what was done about abstain/reject answers")
    k.add_argument("call_ids", nargs="*")
    k.add_argument("--all", action="store_true")
    k.add_argument("--as", dest="as_", required=True, choices=("revised", "escalated", "reported", "dropped"))
    k.add_argument("--note", default="")

    sub.add_parser("question").add_argument("action", choices=("lint", "list"))

    r = sub.add_parser("route", help="preflight: FAST, JEV, DEEP or ESCALATE")
    r.add_argument("--claim")
    r.add_argument("--evidence")
    r.add_argument("--evidence-file")
    r.add_argument("--command")
    r.add_argument("-o", "--option", action="append")
    r.add_argument("--risk", default="low", choices=("low", "medium", "high", "critical"))
    r.add_argument("--done", action="store_true", help="the agent is about to claim completion")
    r.add_argument("--deterministic", action="store_true", help="a deterministic check can answer this")
    r.add_argument("--question-id", help="PF-04: the registry question about to be asked; with --field, a repeat in this session routes to REUSE")
    r.add_argument("--field", action="append", default=[], help="state field of that question: name=value or name=@file")
    r.add_argument("--observed-at", help="PF-07: when the context was observed (ISO 8601, UTC when no zone is given); needs --ttl-s")
    r.add_argument("--ttl-s", type=float, help="PF-07: how many seconds that observation stays good")
    f = sub.add_parser("fast", help="deterministic checks: exit-code EXPECT ACTUAL | file-exists PATH | regex PATTERN FILE")
    f.add_argument("kind", choices=("exit-code", "file-exists", "regex"))
    f.add_argument("args", nargs="+")

    w = sub.add_parser("work")
    w.add_argument("action", choices=("list", "render", "set", "sync", "add"))
    w.add_argument("id", nargs="?")
    w.add_argument("--file", default="work-items.jsonl")
    w.add_argument("--out", default="BACKLOG.md")
    w.add_argument("--status")
    w.add_argument("--note")
    w.add_argument("--next")
    w.add_argument("--trigger")
    w.add_argument("--all", action="store_true")
    w.add_argument("--title", help="add: the item's title (required)")
    w.add_argument("--impact", help="add: what happens if it is not done (required)")
    w.add_argument("--rationale", help="add: why it is an item (required)")
    w.add_argument("--type", choices=("blocker", "ambiguity", "risk", "debt", "followup"), default="followup", help="add: item type")
    w.add_argument("--priority", choices=("P0", "P1", "P2", "P3"), default="P2", help="add: priority")
    w.add_argument("--owner", choices=("human", "agent", "runtime"), default="agent", help="add: owner")
    w.add_argument("--dep", action="append", help="add: id of an item this one waits on (repeatable)")
    w.add_argument("--evidence", action="append", help="add: a file or document the item is about; with the title it identifies a duplicate (repeatable)")
    w.add_argument("--parent", help="add: id of the parent item")
    w.add_argument("--phase", type=int, choices=range(0, 6), help="add: roadmap phase 0 to 5")

    lb = sub.add_parser("label").add_subparsers(dest="action", required=True)
    lb.add_parser("auto")
    lb.add_parser("queue")
    ls = lb.add_parser("set")
    ls.add_argument("call_id")
    ls.add_argument("value", choices=("yes", "no"))
    ls.add_argument("--note")

    e = sub.add_parser("eval").add_subparsers(dest="action", required=True)
    e.add_parser("run").add_argument("--protected", action="store_true")
    e.add_parser("conditions")
    e.add_parser("routing", help="confusion matrix of the router on the hand-written routing cases (offline)").add_argument("--cases", help="a routing cases file (default: evals/routing/cases.jsonl of the checkout)")
    e.add_parser("propose")

    im = sub.add_parser("improve").add_subparsers(dest="action", required=True)
    im.add_parser("mine").add_argument("--days", type=int, default=7)
    im.add_parser("list")
    for name in ("show", "promote", "rollback"):
        im.add_parser(name).add_argument("candidate_id")
    im.add_parser("protected", help="evaluate a candidate on the protected split (human terminal only)").add_argument("candidate_id")
    im.add_parser("canary", help="serve a shadowed template version to the canary share (human terminal or the standing opt-in)").add_argument("candidate_id")
    sh = im.add_parser("shadow", help="replay recent stored state through a challenger question version (async, off the hot path)")
    sh.add_argument("candidate_id")
    sh.add_argument("--question-id", required=True)
    sh.add_argument("--version", type=int, required=True)
    sh.add_argument("--limit", type=int, default=100)
    im.add_parser("freeze")
    im.add_parser("unfreeze")

    sub.add_parser("hooks").add_argument("action", choices=("status", "install", "uninstall"))
    pgate = sub.add_parser("gate")
    pgate.add_argument("action", choices=("status", "enforce", "advisory", "off"))
    pgate.add_argument("--allow-unsigned", action="store_true", help="enforce without the signed approval backend (docs/10); journaled")
    preview = sub.add_parser("review", help="read-only: what you answered to JADE's asks (counts by rule and channel)")
    preview.add_argument("--since", help="a window such as 90m, 24h or 7d (default: all time)")
    plevel = sub.add_parser("level", help="human only: show or set the autonomy level (docs/12): unset, off or high; no argument prints it")
    plevel.add_argument("value", nargs="?", choices=("unset", "off", "high"))
    sub.add_parser("claude-md").add_argument("action", choices=("show", "apply"))
    gh = sub.add_parser("git-hook", help="the standalone pre-push guard for the default branch (SI-08): `script` prints it, `check <repo>` says whether a repository runs it").add_subparsers(dest="action", required=True)
    gh.add_parser("script", help="print the self-contained POSIX sh pre-push hook (installs nothing; install it yourself, docs/08 section 2.7)")
    gh.add_parser("check", help="report whether the pre-push hook git runs for REPO is the JADE guard; exit 1 when it is not").add_argument("repo")
    sub.add_parser("maintain").add_argument("--offline", action="store_true")
    sub.add_parser("version", help="print the code version, commit, ledger schema version and policy_version").add_argument("--json", action="store_true")
    up = sub.add_parser("upgrade", help="human only: install a release of jade-decision-governance with uv, then run jade check from it; --check only looks")
    ug = up.add_mutually_exclusive_group()
    ug.add_argument("--check", action="store_true", help="read-only: installed version, latest release, CHANGELOG headings in between")
    ug.add_argument("--ref", help="a release tag (vX.Y.Z) or a full commit hash to install (default: the latest release)")
    su = sub.add_parser("setup", help="human only: the first-run wizard (gate advisory, hooks, CLAUDE.md section, skill, maintenance job, check); --status only reads")
    sg = su.add_mutually_exclusive_group()
    sg.add_argument("--yes", action="store_true", help="apply every step without asking (still needs an interactive terminal)")
    sg.add_argument("--status", action="store_true", help="read-only: what is done and what is missing")
    lp = sub.add_parser("launchd-plist")
    lp.add_argument("--label", default="com.jade.maintain")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["ask"]:
        return ask_main(argv[1:])
    if argv[:1] == ["hook"] and len(argv) == 2:
        from . import hooks

        return hooks.main(argv[1])
    a = build_parser().parse_args(argv)
    if a.cmd == "ledger":
        if a.action == "stats":
            return _stats(a)
        from . import ops

        if a.id and not a.test_rows:
            print("jade: --id is only used with --test-rows", file=sys.stderr)
            return 2
        if not is_human_tty():
            print("jade: a purge outside the daily job is run by a human at a terminal", file=sys.stderr)
            return 1
        if a.test_rows:
            try:
                n = ops.purge_test_rows(ledger.open(), a.id)
            except ValueError as e:
                print(f"jade: {e}", file=sys.stderr)
                return 2
            print(f"removed {sum(n.values())} test row(s): " + ", ".join(f"{k} {v}" for k, v in n.items()))
            return 0
        print(f"purged state text from {ops.purge(ledger.open(), a.older_than)} row(s)")
        return 0
    if a.cmd == "check":
        from . import ops

        return ops.print_check(ops.check(live=not a.offline))
    if a.cmd in ("version", "upgrade"):
        from . import upgrade

        return upgrade.cli_version(a) if a.cmd == "version" else upgrade.cli_upgrade(a)
    if a.cmd == "setup":
        from . import setup

        return setup.cli_setup(a)
    if a.cmd == "launchd-plist":
        from . import ops

        print(ops.launchd_plist(a.label), end="")
        return 0
    table = {"permit": _permit, "decide": _decide, "ack": _ack, "question": _question, "route": _route, "fast": _fast, "work": _work,
             "label": _label, "eval": _eval, "improve": _improve, "hooks": _ops, "gate": _ops, "review": _ops, "claude-md": _ops, "maintain": _maintain, "git-hook": _git_hook}
    return table[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
