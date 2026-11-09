"""Replay the regression, adversarial, routing, judgment and calibration case files through the real jade code paths (fixture provider,
isolated home, no network). Exit non-zero on any mismatch. Reports the pass rate per language slice (`lang` tag, else derived from the text).

    uv run python evals/run_cases.py [evals/regression/cases.jsonl evals/adversarial/cases.jsonl ...]

`known_gap` marks a case whose label is right and whose current behaviour is wrong (W-49): it passes while the behaviour is still the
recorded one, and fails when the behaviour changes, so a fix has to remove the marker.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = [ROOT / "evals" / d / "cases.jsonl" for d in ("regression", "adversarial", "routing", "judgment", "calibration")]


def run_case(c: dict) -> list[str]:
    from jade import adapter, ask, hooks, ledger, questions

    problems: list[str] = []
    kind = c["kind"]
    if kind in ("verify", "repeat", "secret"):
        p = c.get("p", 0.9)
        prov = adapter.FixtureProvider(lambda s, q, m, p=p: {k: {"type": "noul", "noul": p} for k in q})
        claim = c.get("claim", "tests pass")
        secret = "".join(c.get("secret_parts", []))
        evidence = c.get("evidence") or (c["template"].replace("@@", secret) if c.get("template") else f"log line with key {secret} inside" if kind == "secret"
                                         else "pytest: 42 passed in 3.1s")
        specs = lambda: ask.verify_specs(companion=False)  # noqa: E731
        r = ask.run(specs(), {"claim": claim, "evidence": evidence}, provider=prov, session="case-session", risk=c.get("risk", "low"))
        if kind == "repeat":
            ask.run(specs(), {"claim": claim, "evidence": evidence}, provider=prov, session="case-session", risk=c.get("risk", "low"))
            if len(prov.calls) != c["expect_provider_calls"]:
                problems.append(f"provider called {len(prov.calls)} times, expected {c['expect_provider_calls']}")
        if "expect_exit" in c and r.exit_code != c["expect_exit"]:
            problems.append(f"exit {r.exit_code}, expected {c['expect_exit']}")
        if "expect_permits" in c and ledger.open().one("SELECT COUNT(*) AS n FROM permits").n != c["expect_permits"]:
            problems.append("a permit exists that should not")
        if kind == "secret" and secret in json.dumps(prov.calls):
            problems.append("secret reached the provider payload")
        if "expect_state_under_bytes" in c and len(json.dumps(prov.calls[0]["state"]).encode()) >= c["expect_state_under_bytes"]:
            problems.append("state not capped")
        if "authorized" in "\n".join(ask.explain(r)).lower():
            problems.append("guidance text contains the word authorized")
    elif kind == "choice":
        try:
            questions.build_choice(None, c["options"], "Which?")
            problems.append("degenerate choice was accepted")
        except questions.InvalidQuestion:
            pass
    elif kind == "provider_error":
        res = adapter.Result(ok=False, error_kind=c["error_kind"], http_status=c.get("http_status"))
        r = ask.run(ask.verify_specs(companion=False), {"claim": "c", "evidence": "e"}, provider=adapter.FixtureProvider(lambda s, q, m: res), session="case-session")
        if r.exit_code != c["expect_exit"]:
            problems.append(f"exit {r.exit_code}, expected {c['expect_exit']}")
    elif kind in ("gate", "gate_write", "gate_mcp"):
        ev = {"tool_name": "Bash", "tool_input": {"command": c.get("command", "")}, "session_id": "case-session", "cwd": "/tmp/case"}
        if kind == "gate_mcp":
            ev = {"tool_name": c["tool"], "tool_input": c["input"], "session_id": "case-session", "cwd": "/tmp/case"}
        if kind == "gate_write":
            tin = {c.get("key", "file_path"): c["path"]}
            if c.get("content_parts"):  # file content assembled from parts so that no case file holds a token-shaped literal
                tin["content"] = "".join(c["content_parts"])
            ev = {"tool_name": c.get("tool", "Write"), "tool_input": tin, "session_id": "case-session", "cwd": "/tmp/case"}
        if kind == "gate_mcp" and c.get("content_parts"):
            ev["tool_input"] = {**ev["tool_input"], "content": "".join(c["content_parts"])}
        ev.update(c.get("event", {}))  # extra host fields: permission_mode, agent_id
        res = hooks.pretool(ev)
        want = 2 if c.get("expect") == "block" else 0
        if res.exit_code != want:
            problems.append(f"gate exit {res.exit_code}, expected {want} ({res.stderr[:80]!r})")
        for inv in c.get("expect_blocked", []):
            if inv not in res.stderr:
                problems.append(f"expected invariant {inv} in the block message")
    elif kind in ("answer_shape", "calibrated_repeat"):
        problems += _run_judging(c)
    elif kind == "secret_stored":
        problems += _run_secret_stored(c)
    elif kind == "route":
        problems += _run_route(c)
    elif kind == "judge_input":
        problems += _run_judge_input(c)
    elif kind == "redact_keep":
        from jade import redact

        if redact.redact(c["text"]) != (c["text"], 0):
            problems.append(f"non-secret text was changed: {redact.redact(c['text'])[0]!r}")
    elif kind in ("permit_variant", "permit_forged", "decide_walk"):
        problems += _run_lifecycle(c)
    else:
        problems.append(f"unknown case kind {kind}")
    return problems


def _run_route(c: dict) -> list[str]:
    """W-49 routing set: `route.preflight` on the case's inputs must give the hand-labelled `should_route`. No provider is involved."""
    from jade import route

    got = route.preflight(claim=c.get("claim", ""), evidence=c.get("evidence", ""), options=c.get("options"), command=c.get("command"), risk=c.get("risk", "low"),
                          about_to_claim_done=c.get("about_to_claim_done", False), deterministic=c.get("deterministic", False)).route
    return [] if got == c["should_route"] else [f"route {got}, expected {c['should_route']}"]


def _run_judge_input(c: dict) -> list[str]:
    """W-49 judgment and calibration sets, the half that needs no provider: the case's text goes through `ask.run` with a fixture provider and
    must reach it byte for byte (Persian script, half-spaces and Persian digits survive redaction and the state cap), with no redaction and no
    truncation. Whether Jev judges the case correctly needs a live call and is not measured here."""
    from jade import adapter, ask

    if c["family"] == "verify_claim":
        spec, state = ask.verify_specs(companion=False), {"claim": c["claim"], "evidence": c["evidence"]}
        prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.5} for k in q})
        r = ask.run(spec, state, provider=prov, session="case-session")
    else:
        state = {"context": c["context"]}
        prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "choice", "choice": {x.split("=", 1)[0]: 1 / len(c["options"]) for x in c["options"]}} for k in q})
        r = ask.run([ask.adhoc_spec("choice", "Which option fits the context?", options=c["options"])], state, provider=prov, session="case-session")
    problems = []
    if len(prov.calls) != 1:
        return [f"provider called {len(prov.calls)} times, expected 1"]
    if prov.calls[0]["state"] != state:
        problems.append("the state that reached the provider differs from the case")
    if c["family"] == "strategy_choice":
        want = {o.split("=", 1)[0].strip(): o.split("=", 1)[1].strip() for o in c["options"]}
        if prov.calls[0]["questions"]["q"].get("criteria") != want:
            problems.append("the options that reached the provider differ from the case")
    if r.redactions or "truncated" in " ".join(r.warnings):
        problems.append("a non-secret case was redacted or truncated")
    return problems


def _run_judging(c: dict) -> list[str]:
    """Review cluster D. `answer_shape`: the provider returns `answer` for the key `q` (an ad hoc `primitive` question with `options`, or the
    registry `question_id` with `fields`): a malformed or mismatched answer must be exit 5, never 0; `none_of_these` must be exit 3.
    `calibrated_repeat`: a promoted accept for the fixture group, then the same question twice: both exits must be the same non-yes."""
    from pathlib import Path

    from jade import adapter, ask, paths

    problems: list[str] = []
    if c["kind"] == "calibrated_repeat":
        cal = Path(paths.home()) / "calibration"
        cal.mkdir(parents=True, exist_ok=True)
        (cal / "current.json").write_text(json.dumps({"verify_claim/v1/jev-1.14-fixture": {"accept": c["accept"], "reject": 0.3}}))
        prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": c["p"]} for k in q})
        exits = [ask.run(ask.verify_specs(companion=False), {"claim": "tests pass", "evidence": "42 passed"}, provider=prov, session="case-session").exit_code for _ in range(2)]
        if exits != c["expect_exits"]:
            problems.append(f"exits {exits}, expected {c['expect_exits']}")
        return problems
    if c.get("question_id"):
        spec = ask.registry_spec(c["question_id"], options=c.get("options"))
        state = c["fields"]
    else:
        spec = ask.adhoc_spec(c["primitive"], "Which?", options=c.get("options"), levels=c.get("levels"), true=c.get("true"), false=c.get("false"))
        state = {"context": "ctx"}
    r = ask.run([spec], state, provider=adapter.FixtureProvider(lambda s, q, m: {k: c["answer"] for k in q}), session="case-session")
    if r.exit_code != c["expect_exit"]:
        problems.append(f"exit {r.exit_code}, expected {c['expect_exit']}")
    return problems


def _run_secret_stored(c: dict) -> list[str]:
    """Review cluster C: a secret in a gated command or in a `jade ask` argument must be absent from stdout, stderr and every ledger table.
    `via` is gate (the PreToolUse hook; the permit must also be unapprovable), dry_run (`jade ask --dry-run`) or invalid_field (`--field` without `=`)."""
    import contextlib
    import io

    from jade import cli, hooks, ledger, permits

    secret = "".join(c["secret_parts"])
    text = c["template"].replace("@@", secret)
    problems: list[str] = []
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        if c["via"] == "gate":
            res = hooks.pretool({"tool_name": "Bash", "tool_input": {"command": text}, "session_id": "case-session", "cwd": "/tmp/case"})
            err.write(res.stderr)
            if res.exit_code != 2:
                problems.append(f"gate exit {res.exit_code}, expected 2")
        elif c["via"] == "dry_run":
            cli.ask_main(["verify", "claim", "-s", text, "--dry-run"], prog="jade ask")
        else:
            cli.ask_main(["question", "q.verify_claim.supported_by_evidence", "--field", text], prog="jade ask")
    db = ledger.open()
    for where, blob in (("stdout", out.getvalue()), ("stderr", err.getvalue()), ("ledger", "\n".join(db.con.iterdump()))):
        if secret in blob:
            problems.append(f"the secret is in {where}")
    if c["via"] == "gate" and c.get("expect_unapprovable"):
        pend = permits.listing(db, ("PENDING",))
        try:
            permits.review_text(pend[0])
            problems.append("a permit for a command with an inline secret can be reviewed and approved")
        except (permits.PermitError, IndexError):
            pass
    return problems


def _run_lifecycle(c: dict) -> list[str]:
    """Permit and decision-lifecycle cases (review cluster B). `permit_variant`: a human approves a permit for `command`, the hook then sees
    `variant`; `permit_forged`: a row nobody approved (`claim_risk`) exists for `command`; `decide_walk`: an agent sends `events` through
    `jade decide event`. The human terminal is simulated by replacing the ancestry probe; nothing here touches a real terminal or ledger."""
    import contextlib
    import io

    from jade import cli, decide, hooks, ledger, permits, policy, util

    problems: list[str] = []
    real, real_cli = util.agent_ancestor, cli.human_tty_refusal
    util.agent_ancestor = lambda: None
    try:
        db = ledger.open()
        ev = lambda cmd: {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "case-session", "cwd": "/tmp/case"}  # noqa: E731
        want = 2 if c.get("expect") == "block" else 0
        if c["kind"] == "permit_variant":
            if hooks.pretool(ev(c["command"])).exit_code != 2:
                problems.append("the command was not blocked before approval")
            permits.approve(db, permits.listing(db, ("PENDING",))[-1].permit_id, stdin_isatty=True)
            res = hooks.pretool(ev(c["variant"]))
            if res.exit_code != want:
                problems.append(f"variant exit {res.exit_code}, expected {want}")
        elif c["kind"] == "permit_forged":
            a = policy.assess("Bash", {"command": c["command"]}, "/tmp/case")
            forged = permits.issue(db, "forged.action", a.plan_hash, risk=c.get("claim_risk", "low"))
            if c.get("tamper"):  # then rewrite the row as a human's approval of a critical plan with raw SQL (the seal must catch it)
                doc = json.loads(db.one("SELECT doc_json FROM permits WHERE permit_id=?", (forged.permit_id,)).doc_json)
                doc.update(principal=f"human:{util.user()}", approval_reference=f"tty:{util.user()}@{util.ts()}", risk_level="critical")
                db.con.execute("UPDATE permits SET risk_level='critical', doc_json=? WHERE permit_id=?", (json.dumps(doc), forged.permit_id))
            res = hooks.pretool(ev(c["command"]))
            if res.exit_code != want:
                problems.append(f"gate exit {res.exit_code}, expected {want} with a forged {c.get('claim_risk', 'low')} permit")
        else:
            os.environ["JADE_SESSION_ID"] = "case-session"
            cli.human_tty_refusal = lambda *a, **k: "agent session"
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                cli.main(["decide", "open", "--objective", "an objective long enough", *(["--command", c["command"]] if c.get("command") else [])])
                did = json.loads(out.getvalue().strip().splitlines()[-1])["decision_id"]
                for e in c["events"]:
                    cli.main(["decide", "event", did, e])
            status = decide.get(db, did)["status"]
            if status == c["expect_status_not"]:
                problems.append(f"an agent reached {status} through `jade decide event`")
            if db.one("SELECT COUNT(*) AS n FROM outcomes").n:
                problems.append("an outcome row exists that no one recorded")
    finally:
        util.agent_ancestor, cli.human_tty_refusal = real, real_cli
        os.environ.pop("JADE_SESSION_ID", None)
    return problems


def seeded(c: dict, home: str) -> dict:
    """A case with `seed` (paths under `{tmp}`) gets those files created in its throwaway directory, and `{tmp}` in the case replaced by it: the
    gate decides on whether a file already exists (R-18), so a case about editing one needs the file to be there."""
    if not c.get("seed"):
        return c
    out = json.loads(json.dumps(c).replace("{tmp}", json.dumps(home)[1:-1]))
    for rel in out["seed"]:
        f = Path(rel)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# existing file\n")
    return out


def _gap_status(c: dict, problems: list[str]) -> tuple[str, list[str]]:
    """`known_gap` cases: still failing in the recorded way is a pass (shown as `gap`); passing means the gap closed, and a different
    failure means the behaviour drifted: both fail so the marker is kept honest."""
    if not problems:
        return "FAIL", ["known_gap is set but the case now passes: remove known_gap and current_route"]
    if c.get("kind") == "route" and problems != [f"route {c['current_route']}, expected {c['should_route']}"]:
        return "FAIL", [f"known_gap drifted (recorded current_route {c['current_route']}): {problems[0]}"]
    return "gap", []


def main(files: list[str]) -> int:
    failures = total = gaps = delivery_only = 0
    by_lang: dict[str, list[int]] = {}
    for f in files or DEFAULT:
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            with tempfile.TemporaryDirectory() as d:
                os.environ.update(JADE_HOME=d, JADE_CONFIG=d, JADE_GATE_MODE="enforce")
                for k in __import__("jade.util", fromlist=["AGENT_ENV"]).AGENT_ENV:
                    os.environ.pop(k, None)
                from jade import policy, questions, thresholds

                for fn in (policy.reset_cache, questions.reset_cache, thresholds.escalation.cache_clear):
                    fn()
                problems = run_case(seeded(c, os.path.realpath(d)))
            status = "ok  "
            if c.get("known_gap"):
                status, problems = _gap_status(c, problems)
            elif problems:
                status = "FAIL"
            total += 1
            failures += bool(problems)
            gaps += status == "gap"
            lang = __import__("jade.evals", fromlist=["case_lang"]).case_lang(c)
            tally = by_lang.setdefault(lang, [0, 0, 0, 0])   # passed, cases, known gaps, input-delivery-only cases
            tally[1] += 1
            tally[0] += not problems and status != "gap"
            tally[2] += status == "gap"
            tally[3] += c.get("kind") == "judge_input"
            delivery_only += c.get("kind") == "judge_input"
            print(f"{status}  {c['id']} {c['class']}" + ("".join(f"\n      {p}" for p in problems)))
    print(f"{total - failures}/{total} cases pass ({gaps} known gaps)")
    if delivery_only:
        print(f"{delivery_only} judge_input cases check input delivery only: the text must reach a fixture provider unchanged. Their truth and best labels are "
              "not read, so judgment quality is not measured offline (docs/04 section 5).")
    print("by language (explicit lang tag, else derived from the case text; a known gap is listed apart and is not a pass):")
    for lang in sorted(by_lang):
        ok, n, g, d = by_lang[lang]
        print(f"{lang:<8}{ok}/{n}" + (f"  ({g} known gap{'s' if g != 1 else ''})" if g else "") + (f"  ({d} input delivery only)" if d else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
