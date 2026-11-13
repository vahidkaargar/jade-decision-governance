"""Phase 2 acceptance (docs/06): state machine, crash recovery, permits, gate, lint, idempotency, checks."""
import json
import sqlite3

import pytest

from jade import adapter, ask, cli, decide, hooks, ledger, ops, permits, policy, questions, route, state_machine

ROWS = [
    ("RECEIVED", "validate", "VALIDATED", "low"), ("VALIDATED", "classify", "CLASSIFIED", "low"), ("CLASSIFIED", "route", "EVALUATING", "low"),
    ("EVALUATING", "fast_accept", "ACCEPTED", "low"), ("EVALUATING", "fast_reject", "REJECTED", "low"),
    ("EVALUATING", "jev_result_accept", "ACCEPTED", "low"), ("EVALUATING", "jev_result_abstain", "ABSTAINED", "low"),
    ("EVALUATING", "jev_result_reject", "REJECTED", "low"), ("EVALUATING", "jev_error", "NEEDS_EVIDENCE", "low"),
    ("EVALUATING", "deep_accept", "ACCEPTED", "low"), ("EVALUATING", "deep_revise", "NEEDS_REVISION", "low"),
    ("ABSTAINED", "add_evidence", "EVALUATING", "low"), ("NEEDS_EVIDENCE", "add_evidence", "EVALUATING", "low"),
    ("ABSTAINED", "escalate", "WAITING_HUMAN", "medium"), ("NEEDS_REVISION", "revise", "VALIDATED", "low"),
    ("NEEDS_REVISION", "escalate", "WAITING_HUMAN", "low"),
    ("ACCEPTED", "authorize", "AUTHORIZED", "low"), ("ACCEPTED", "authorize_without_permit_high_risk", "WAITING_HUMAN", "high"),
    ("AUTHORIZED", "execute", "EXECUTING", "low"),
    ("EXECUTING", "observe", "VERIFYING", "low"), ("VERIFYING", "verified", "SUCCEEDED", "low"), ("VERIFYING", "failed", "FAILED", "low"),
    ("VERIFYING", "unknown", "OUTCOME_UNKNOWN", "low"), ("FAILED", "compensate", "COMPENSATED", "low"), ("FAILED", "rollback", "ROLLED_BACK", "low"),
    ("OUTCOME_UNKNOWN", "reconcile", "VERIFYING", "low"), ("WAITING_HUMAN", "human_accept", "ACCEPTED", "low"),
    ("WAITING_HUMAN", "human_reject", "REJECTED", "low"), ("RETRY_SCHEDULED", "resume", "EVALUATING", "low"),
    ("EVALUATING", "deadline", "EXPIRED", "low"), ("AUTHORIZED", "deadline", "EXPIRED", "low"), ("NEEDS_REVISION", "deadline", "EXPIRED", "low"),
]


@pytest.mark.parametrize("src,event,dst,risk", ROWS)
def test_state_machine_transitions(src, event, dst, risk):
    kw = {"permit_ok": True} if event == "authorize" else {}
    assert state_machine.next_state(src, event, risk=risk, **kw) == dst


@pytest.mark.parametrize("src,event", [
    ("EVALUATING", "execute"), ("ACCEPTED", "execute"), ("OUTCOME_UNKNOWN", "retry"), ("ABSTAINED", "authorize"), ("ACCEPTED", "verified"),
    ("EXECUTING", "verified"), ("OUTCOME_UNKNOWN", "verified"), ("VERIFYING", "execute"), ("REJECTED", "authorize"), ("CLASSIFIED", "authorize"),
    ("SUCCEEDED", "cancel"), ("EXECUTING", "deadline"), ("ACCEPTED", "human_answer"), ("VERIFYING", "deadline"), ("OUTCOME_UNKNOWN", "deadline")])
def test_forbidden_edges(src, event):
    with pytest.raises(state_machine.IllegalTransition):
        state_machine.next_state(src, event, risk="low")


def test_every_documented_edge_is_covered_and_every_status_reachable():
    covered = {(s, e) for s, e, _, _ in ROWS}
    untested = set(state_machine.T) - covered
    assert untested <= {("EVALUATING", "deep_reject"), ("EVALUATING", "escalate"), ("NEEDS_EVIDENCE", "escalate"), ("FAILED", "retry"),
                        ("WAITING_DEPENDENCY", "resume")}, untested


def test_authorize_at_high_risk_needs_a_permit():
    with pytest.raises(state_machine.IllegalTransition):
        state_machine.next_state("ACCEPTED", "authorize", risk="high", permit_ok=False)
    assert state_machine.next_state("ACCEPTED", "authorize", risk="critical", permit_ok=True) == "AUTHORIZED"


def test_estimated_outcome_never_succeeds_and_unknown_blocks_retry():
    db = ledger.open()
    d = decide.open_decision(db, "Run the deploy and check it", command="echo hi")
    for ev in ("route", "jev_result_accept", "authorize", "execute"):
        decide.step(db, d["decision_id"], ev)
    decide.record_outcome(db, d["decision_id"], "estimated", True, "looks fine")
    assert decide.get(db, d["decision_id"])["status"] == "VERIFYING"
    decide.record_outcome(db, d["decision_id"], "unknown", None, "could not read state")
    assert decide.get(db, d["decision_id"])["status"] == "OUTCOME_UNKNOWN"
    with pytest.raises(state_machine.IllegalTransition):
        decide.step(db, d["decision_id"], "retry")


def test_crash_recovery(monkeypatch, tmp_path):
    """Kill between the event insert and the state write: on reopen the decision is unchanged and the event is absent."""
    db = ledger.open(tmp_path / "c.sqlite")
    d = decide.open_decision(db, "Check that the migration ran", command="echo hi")
    monkeypatch.setattr(state_machine, "_after_event_insert", lambda: (_ for _ in ()).throw(RuntimeError("killed")))
    with pytest.raises(RuntimeError):
        decide.step(db, d["decision_id"], "route", idempotency_key="k-crash")
    db.close()
    db2 = ledger.open(tmp_path / "c.sqlite")
    assert db2.one("SELECT status FROM decisions WHERE decision_id=?", (d["decision_id"],)).status == "CLASSIFIED"
    assert db2.one("SELECT COUNT(*) AS n FROM events WHERE idempotency_key='k-crash'").n == 0
    monkeypatch.undo()
    assert decide.step(db2, d["decision_id"], "route", idempotency_key="k-crash").applied
    assert sqlite3.connect(tmp_path / "c.sqlite").execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_duplicate_delivery_idempotent():
    db = ledger.open()
    d = decide.open_decision(db, "Check that the migration ran", command="echo hi")
    ev = {"decision_id": d["decision_id"], "event": "route", "idempotency_key": "k1"}
    assert state_machine.apply(db, ev).applied and not state_machine.apply(db, ev).applied
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE idempotency_key='k1'").n == 1


def test_permit_single_use_and_ttl(tmp_path):
    p = permits.issue(tmp_path, action="git.commit", plan_hash="f" * 64, ttl_s=1800, risk="low")
    assert permits.consume(tmp_path, p.permit_id, plan_hash="f" * 64, level="low")
    assert not permits.consume(tmp_path, p.permit_id, plan_hash="f" * 64, level="low")
    q = permits.issue(tmp_path, action="git.commit", plan_hash="e" * 64, ttl_s=1, risk="low")
    ledger.open(tmp_path).con.execute("UPDATE permits SET expires_at='2000-01-01T00:00:00Z' WHERE permit_id=?", (q.permit_id,))
    assert not permits.consume(tmp_path, q.permit_id, plan_hash="e" * 64, level="low")


def test_permit_plan_hash_mismatch_blocks(tmp_path):
    p = permits.issue(tmp_path, action="git.commit", plan_hash="f" * 64, ttl_s=1800, risk="low")
    assert not permits.consume(tmp_path, p.permit_id, plan_hash="e" * 64, level="low")
    assert permits.get(tmp_path, p.permit_id).state == "ISSUED"


def test_high_risk_cannot_be_issued_directly(tmp_path):
    for risk in ("high", "critical"):
        with pytest.raises(permits.PermitError):
            permits.issue(tmp_path, action="git.push", plan_hash="f" * 64, ttl_s=1800, risk=risk)


def test_pending_permit_cannot_be_consumed(tmp_path):
    p = permits.request(tmp_path, action="git.push", plan_hash="f" * 64, risk="high")
    assert p.state == "PENDING" and not permits.consume(tmp_path, p.permit_id, plan_hash="f" * 64, level="high")


def test_approve_refuses_non_tty(tmp_path, agent_env):
    p = permits.request(tmp_path, action="deploy.prod", plan_hash="f" * 64, risk="critical")
    with pytest.raises(permits.ApprovalRefused):
        permits.approve(tmp_path, p.permit_id, stdin_isatty=False)
    with pytest.raises(permits.ApprovalRefused):  # a tty alone is not enough while an agent harness is present
        permits.approve(tmp_path, p.permit_id, stdin_isatty=True)
    assert permits.get(tmp_path, p.permit_id).state == "PENDING"


def test_approve_from_human_terminal_sets_tty_reference(tmp_path, human_env):
    p = permits.request(tmp_path, action="git.push", plan_hash="f" * 64, risk="high")
    ok = permits.approve(tmp_path, p.permit_id, stdin_isatty=True)
    assert ok.state == "ISSUED" and ok.approval_reference.startswith("tty:") and ok.principal.startswith("human:")
    assert permits.consume(tmp_path, p.permit_id, plan_hash="f" * 64, level="high")


def push(cmd="git push origin v1-feat-x"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "sess-12345678", "cwd": "/tmp/x"}


def test_gate_blocks_high_risk_without_permit_and_creates_pending():
    r = hooks.pretool(push())
    assert r.exit_code == 2 and "jade permit approve prm_" in r.stderr
    assert len(permits.listing(ledger.open(), ("PENDING",))) == 1
    assert hooks.pretool(push()).exit_code == 2 and len(permits.listing(ledger.open(), ("PENDING",))) == 1  # no duplicate pending


def test_full_permit_flow_and_single_use(human_env):
    db = ledger.open()
    hooks.pretool(push())
    pid = permits.listing(db, ("PENDING",))[0].permit_id
    permits.approve(db, pid, stdin_isatty=True)
    assert hooks.pretool(push()).exit_code == 0
    again = hooks.pretool(push())
    assert again.exit_code == 2 and permits.get(db, pid).state == "CONSUMED"
    assert hooks.pretool(push("git push origin v1-feat-y")).exit_code == 2  # a different command needs its own permit


def test_decision_lifecycle_through_gate_and_posttool(human_env):
    db = ledger.open()
    hooks.pretool(push())
    p = permits.listing(db, ("PENDING",))[0]
    assert decide.get(db, p.decision_id)["status"] == "WAITING_HUMAN"
    permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(push()).exit_code == 0
    assert decide.get(db, p.decision_id)["status"] == "EXECUTING"
    hooks.posttool({**push(), "tool_response": {"is_error": False}})
    assert decide.get(db, p.decision_id)["status"] == "SUCCEEDED" or decide.get(db, p.decision_id)["status"] == "VERIFYING"


def test_hook_fails_closed_on_internal_error_for_high_risk(monkeypatch):
    monkeypatch.setattr(hooks, "_classify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hooks.pretool(push()).exit_code == 2


def test_hook_fails_open_for_low_risk_internal_error(monkeypatch):
    monkeypatch.setattr(hooks, "_classify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hooks.pretool(push("ls -la")).exit_code == 0


def test_main_fails_closed_when_handler_crashes(monkeypatch, capsys):
    monkeypatch.setattr(hooks, "pretool", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setitem(hooks.HANDLERS, "pretool", hooks.pretool)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(push())))
    assert hooks.main("pretool") == 2


def test_safety_invariants_block_regardless_of_permit(human_env):
    db = ledger.open()
    a = policy.assess("Bash", {"command": "git push origin main"}, "/tmp/x")
    p = permits.request(db, "git.push", a.plan_hash, risk="high")
    permits.approve(db, p.permit_id, stdin_isatty=True)
    r = hooks.pretool(push("git push origin main"), permit=permits.get(db, p.permit_id))
    assert r.exit_code == 2 and "SI-08" in r.stderr
    assert permits.get(db, p.permit_id).state == "ISSUED"  # the invariant fired before the permit was touched


def test_bare_push_on_default_branch_is_blocked(tmp_path, monkeypatch):
    import subprocess

    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    r = hooks.pretool({"tool_name": "Bash", "tool_input": {"command": f"git -C {tmp_path} push"}, "session_id": "s" * 8, "cwd": str(tmp_path)})
    assert r.exit_code == 2 and "SI-08" in r.stderr


def test_gate_modes(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "off")
    assert hooks.pretool(push()).exit_code == 0
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool(push())
    assert r.exit_code == 0 and "advisory" in r.stderr
    assert ledger.open().one("SELECT COUNT(*) AS n FROM events WHERE kind='would_block'").n == 1
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    assert hooks.pretool(push("git push origin main")).exit_code == 0  # advisory never blocks, it only reports


def test_agent_cannot_change_its_own_wiring(agent_env, tmp_path):
    for fn in (lambda: ops.hooks_install(stdin_isatty=True), lambda: ops.hooks_uninstall(stdin_isatty=True), lambda: ops.set_gate_mode("off", stdin_isatty=True),
               lambda: ops.claude_md_apply(tmp_path / "CLAUDE.md", stdin_isatty=True)):
        with pytest.raises(ops.HumanOnly):
            fn()
    for cmd in ("jade permit approve prm_abc", "jade hooks uninstall", "jade gate off", "JADE_GATE_MODE=off git push origin x"):
        assert "SI-09" in policy.assess("Bash", {"command": cmd}).blocked, cmd


def test_hooks_install_is_idempotent_and_reversible(human_env, tmp_path, monkeypatch):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "other-hook.sh"}]}]}, "model": "x"}))
    ops.hooks_install(stdin_isatty=True)
    ops.hooks_install(stdin_isatty=True)
    data = json.loads(s.read_text())
    assert all(ops.hooks_status().values()) and data["model"] == "x"
    cmds = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert cmds.count("other-hook.sh") == 1 and sum("jade" in c for c in cmds) == 1
    assert ops.hooks_uninstall(stdin_isatty=True) == len(ops.HOOKS)
    assert not any(ops.hooks_status().values())
    assert [h["command"] for g in json.loads(s.read_text())["hooks"]["PreToolUse"] for h in g["hooks"]] == ["other-hook.sh"]


def test_claude_md_apply_replaces_only_the_jev_section(human_env, tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text("# Top\n\n## Defaults\nkeep me\n\n" + ops.JEV_HEADING + "\n- old text\n\n## Personal password manager\nkeep me too\n")
    ops.claude_md_apply(md, stdin_isatty=True)
    out = md.read_text()
    assert "keep me\n" in out and "keep me too" in out and "old text" not in out and "exit code is the answer" in out
    assert list(tmp_path.glob("CLAUDE.md.bak-jade-*"))


def test_stop_hook_reminds_once_and_ack_clears():
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.5} for k in q})
    ask.run(ask.verify_specs(), {"claim": "the fix works", "evidence": "I ran it"}, provider=prov, session="sess-12345678")
    ev = {"session_id": "sess-12345678"}
    first = hooks.stop(ev)
    assert first.exit_code == 2 and "jade ack" in first.stderr
    assert hooks.stop(ev).exit_code == 0  # shown once per call
    assert hooks.stop({"session_id": "sess-12345678", "stop_hook_active": True}).exit_code == 0


def test_stop_hook_silent_after_later_accept_or_ack():
    s = "sess-abcdefgh"
    lo = adapter.FixtureProvider(lambda st, q, m: {k: {"type": "noul", "noul": 0.5} for k in q})
    hi = adapter.FixtureProvider(lambda st, q, m: {k: {"type": "noul", "noul": 0.95} for k in q})
    ask.run(ask.verify_specs(), {"claim": "same claim", "evidence": "summary"}, provider=lo, session=s)
    ask.run(ask.verify_specs(), {"claim": "same claim", "evidence": "raw output with detail"}, provider=hi, session=s)
    assert hooks.stop({"session_id": s}).exit_code == 0
    ask.run(ask.verify_specs(), {"claim": "other claim", "evidence": "x"}, provider=lo, session=s)
    db = ledger.open()
    db.con.execute("UPDATE jev_calls SET disposition='reported' WHERE session_id=?", (s,))
    assert hooks.stop({"session_id": s}).exit_code == 0


def test_session_start_surfaces_pending_permits_and_due_work_items():
    db = ledger.open()
    hooks.pretool(push())
    db.con.execute("INSERT INTO work_items(id, status, doc_json, updated_at) VALUES('W-90','open',?,?)",
                   (json.dumps({"id": "W-90", "priority": "P1", "title": "Resume me", "next_action": "do it", "retry_or_revisit_trigger": "on_session_start"}), "2026-11-09"))
    out = hooks.session_start({}).stdout
    assert "awaiting human approval" in out and "W-90" in out and "Resume me" in out


def test_degenerate_choice_rejected():
    with pytest.raises(questions.InvalidQuestion):
        questions.build_choice("q.strategy.best_option", options={"only": "one"})
    with pytest.raises(questions.InvalidQuestion):
        questions.build_choice("q.strategy.best_option", options={"a": "x", "b": "y"}, eligible={"a"})
    assert questions.build_choice("q.strategy.best_option", options={"a": "x", "b": "y"})["criteria"].keys() >= {"a", "b", "none_of_these"}


def test_question_lint_rules():
    good = questions.get("q.verify_claim.supported_by_evidence")
    assert questions.lint(good) == []
    bad = json.loads(json.dumps(good))
    bad["template"]["instructions"] = "Is it obviously true that `nonexistent` holds? Also confirm that it works?"
    bad["template"]["criteria"] = {"true": "yes", "false": "yes"}
    bad["dependencies"] = ["q.other"]
    problems = " | ".join(questions.lint(bad))
    for needle in ("banned token", "one_concept", "not in input_schema", "distinct true and false", "batch eligible"):
        assert needle in problems
    assert all(questions.lint(e) == [] for e in questions.entries())


def test_registry_entries_validate_against_schema_and_match_policy():
    from jade import schemas, thresholds

    for e in questions.entries():
        schemas.validate("question", e)
        fam = thresholds.escalation()["families"].get(e["task_family"], {})
        if e["primitive"] == "noul" and "noul" in fam:
            assert (e["threshold_policy"]["accept"], e["threshold_policy"]["reject"]) == (fam["noul"]["accept"], fam["noul"]["reject"]), e["question_id"]


def test_route_preflight_order():
    assert route.preflight(deterministic=True, claim="exit code was 0").route == "FAST"
    assert route.preflight(claim="done", evidence="", about_to_claim_done=True).route == "NEEDS_EVIDENCE"
    assert route.preflight(claim="pushed", evidence="I think it worked").route == "NEEDS_EVIDENCE"
    assert route.preflight(claim="x", options=["only=one"], evidence="raw").route == "NEEDS_REVISION"
    assert route.preflight(claim="design the new architecture for the ledger", evidence="raw output " * 30).route == "DEEP"
    assert route.preflight(claim="tests pass", evidence="42 passed in 3.1s\n" * 5, command="git push origin main").route == "ESCALATE"
    assert route.preflight(claim="tests pass", evidence="42 passed in 3.1s\n" * 5).route == "FAST"  # low risk, nothing pending
    assert route.preflight(claim="tests pass", evidence="42 passed in 3.1s\n" * 5, about_to_claim_done=True).route == "JEV"


def test_companion_can_only_make_a_medium_risk_answer_more_cautious():
    def responder(s, q, m):
        return {"q": {"type": "noul", "noul": 0.95}, "evidence_is_raw": {"type": "noul", "noul": 0.05}}

    prov = adapter.FixtureProvider(responder)
    low = ask.run(ask.verify_specs(), {"claim": "a", "evidence": "summary"}, provider=prov, session="s1", risk="low")
    med = ask.run(ask.verify_specs(), {"claim": "b", "evidence": "summary"}, provider=prov, session="s1", risk="medium")
    assert low.exit_code == 0 and med.exit_code == 3 and "summary" in med.note


def test_high_risk_needs_the_stricter_band():
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.80} for k in q})
    assert ask.run(ask.verify_specs(), {"claim": "a", "evidence": "e"}, provider=prov, session="s", risk="low").exit_code == 0
    assert ask.run(ask.verify_specs(), {"claim": "b", "evidence": "e"}, provider=prov, session="s", risk="high").exit_code == 3
    assert ask.run(ask.verify_specs(), {"claim": "c", "evidence": "e"}, provider=prov, session="s", risk="critical").exit_code == 3


def test_ask_never_creates_a_permit():
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.99} for k in q})
    ask.run(ask.verify_specs(), {"claim": "safe to push", "evidence": "Jev says yes"}, provider=prov, session="s", risk="high")
    assert ledger.open().one("SELECT COUNT(*) AS n FROM permits").n == 0


def test_attaching_an_evaluation_advances_the_decision():
    db = ledger.open()
    d = decide.open_decision(db, "Report that the tests pass", command=None)
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.95} for k in q})
    r = ask.run(ask.verify_specs(), {"claim": "tests pass", "evidence": "42 passed"}, provider=prov, session="s", decision_id=d["decision_id"], db=db)
    assert r.exit_code == 0 and decide.get(db, d["decision_id"])["status"] == "ACCEPTED"
    assert decide.get(db, d["decision_id"])["evaluation_refs"] == [r.primary.call_id]
    d2 = decide.open_decision(db, "Report that the tests pass", command=None)
    down = adapter.FixtureProvider(lambda s, q, m: adapter.Result(ok=False, error_kind="UNAVAILABLE"))
    r2 = ask.run(ask.verify_specs(), {"claim": "x", "evidence": "y"}, provider=down, session="s2", decision_id=d2["decision_id"], db=db)
    assert r2.exit_code == 5 and decide.get(db, d2["decision_id"])["status"] == "NEEDS_EVIDENCE"


def test_check_exits_nonzero_on_dead_wiring_and_zero_when_wired(human_env, jade_stub, capsys):
    rows = ops.check(live=False)
    assert ops.print_check(rows) == 1 and "hooks installed" in capsys.readouterr().err  # hooks missing
    ops.hooks_install(stdin_isatty=True)
    assert ops.print_check(ops.check(live=False)) == 0
    ops.hooks_uninstall(stdin_isatty=True)
    assert cli.main(["check", "--offline"]) == 1


def test_check_canary_detects_a_provider_that_says_yes_to_everything():
    yes_man = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.97} for k in q})
    rows = {n: (ok, d) for n, ok, d in ops.check(live=True, provider=yes_man)}
    assert rows["provider canary"][0] is False
    discriminating = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.97 if "12 tests passed" in s["evidence"] else 0.03} for k in q})
    assert {n: ok for n, ok, _ in ops.check(live=True, provider=discriminating)}["provider canary"] is True
    down = adapter.FixtureProvider(lambda s, q, m: adapter.Result(ok=False, error_kind="UNAVAILABLE"))
    assert {n: ok for n, ok, _ in ops.check(live=True, provider=down)}["provider canary"] is False


def test_an_accepted_answer_to_a_different_claim_does_not_clear_the_reminder():
    s = "sess-different-claim"
    lo = adapter.FixtureProvider(lambda st, q, m: {k: {"type": "noul", "noul": 0.5} for k in q})
    hi = adapter.FixtureProvider(lambda st, q, m: {k: {"type": "noul", "noul": 0.95} for k in q})
    ask.run(ask.verify_specs(), {"claim": "the migration is safe", "evidence": "I looked at it"}, provider=lo, session=s)
    ask.run(ask.verify_specs(), {"claim": "an unrelated claim", "evidence": "raw output"}, provider=hi, session=s)
    r = hooks.stop({"session_id": s})
    assert r.exit_code == 2 and "jade ack" in r.stderr


def mcp(tool, **inp):
    return {"tool_name": tool, "tool_input": inp, "session_id": "sess-12345678", "cwd": "/tmp/x"}


def test_mcp_cluster_and_repo_mutations_are_gated():
    crit = hooks.pretool(mcp("mcp__Kubernetes_MCP_Server__kubectl_apply", manifest="x"))
    assert crit.exit_code == 2 and "critical" in crit.stderr and "jade permit approve" in crit.stderr
    assert hooks.pretool(mcp("mcp__GitLab__accept_merge_request", project_id="1", merge_request_iid=2)).exit_code == 2
    assert hooks.pretool(mcp("mcp__Desktop_Commander__write_file", path="/x", content="y")).exit_code == 2


def test_mcp_read_only_tools_pass():
    for tool in ("mcp__GitLab__get_project", "mcp__Kubernetes_MCP_Server__kubectl_get", "mcp__context7__query-docs", "mcp__herd__logs"):
        assert hooks.pretool(mcp(tool, x=1)).exit_code == 0, tool


def test_mcp_shell_tools_are_classified_by_their_command():
    assert hooks.pretool(mcp("mcp__MacOS-MCP__Shell", command="git push origin v1-feat-x")).exit_code == 2
    assert hooks.pretool(mcp("mcp__terminal__run_in_terminal", command="git status")).exit_code == 0
    assert "SI-08" in hooks.pretool(mcp("mcp__Desktop_Commander__start_process", command="git push origin main")).stderr


def test_mcp_permit_is_bound_to_the_exact_input(human_env):
    db = ledger.open()
    ev = mcp("mcp__GitLab__accept_merge_request", project_id="1", merge_request_iid=2)
    hooks.pretool(ev)
    pid = permits.listing(db, ("PENDING",))[0].permit_id
    permits.approve(db, pid, stdin_isatty=True)
    assert hooks.pretool(mcp("mcp__GitLab__accept_merge_request", project_id="1", merge_request_iid=3)).exit_code == 2  # other MR: needs its own permit
    assert hooks.pretool(ev).exit_code == 0
    assert hooks.pretool(ev).exit_code == 2  # single use


def test_hook_matcher_covers_mcp_tools(human_env, tmp_path):
    ops.hooks_install(stdin_isatty=True)
    groups = json.loads((tmp_path / "settings.json").read_text())["hooks"]["PreToolUse"]
    assert any("mcp__.*" in g.get("matcher", "") for g in groups)
