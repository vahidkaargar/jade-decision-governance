"""Review cluster B: permits and the decision lifecycle (F06-F13, F16, G08).

Each test reproduces one finding against the real hook, CLI or module with an isolated home (tests/conftest.py) and no network."""
import json
import os
import sqlite3
import subprocess

import pytest

from jade import cli, decide, hooks, ledger, paths, permits, policy, state_machine, util

REAL_ANCESTOR = util.agent_ancestor  # tests/conftest.py replaces util.agent_ancestor for every test; this is the real one
PUSH = "git push origin feat-x"
CRITICAL = "kubectl delete namespace production"


def call(cmd, cwd="/tmp/x", session="sess-aaaaaaaa", agent=None, tool="Bash"):
    ev = {"tool_name": tool, "tool_input": {"command": cmd}, "session_id": session, "cwd": cwd}
    if agent:
        ev["agent_id"] = agent
    return ev


def pending(db):
    return permits.listing(db, ("PENDING",))[-1]


def blocked_then_approved(cmd=PUSH, **kw):
    """The gate blocks `cmd`, a human approves the permit it raised. Returns (db, permit)."""
    db = ledger.open()
    assert hooks.pretool(call(cmd, **kw)).exit_code == 2
    p = pending(db)
    permits.approve(db, p.permit_id, stdin_isatty=True)
    return db, p


def executed(cmd, **kw):
    """The gate blocks `cmd`, a human approves, the retry runs: the decision is EXECUTING."""
    db, p = blocked_then_approved(cmd, **kw)
    assert hooks.pretool(call(cmd, **kw)).exit_code == 0
    assert decide.get(db, p.decision_id)["status"] == "EXECUTING"
    return db, p


def status(db, decision_id):
    return db.one("SELECT status FROM decisions WHERE decision_id=?", (decision_id,)).status


# ---- F06: approval is a heuristic, and SI-09 is not one spelling ---------------------------------------
SI09_BLOCKED = [
    "jade permit approve prm_x -y", "python -m jade.cli permit approve prm_x -y", "python -m jade permit approve prm_x",
    "uv run python -m jade permit approve prm_x", "env jade permit approve prm_x", "/Users/x/.local/bin/jade permit approve prm_x",
    "~/.local/bin/jade  permit   approve prm_x", "'jade' 'permit' \"approve\" prm_x", "j=jade; $j permit approve prm_x",
    "J=jade;K=permit; $J $K approve prm_x", "alias j=jade; j permit approve prm_x", "j() { jade \"$@\"; }; j permit approve prm_x",
    "python3 -c \"import pty; pty.spawn(['jade','permit','approve','prm_x','-y'])\"", "script -q /dev/null jade permit approve prm_x",
    "printf 'permit approve prm_x' | xargs jade", "ln -s $(which jade) /tmp/j && /tmp/j permit approve prm_x", "jade ${X:-permit} approve prm_x",
    "jade$IFS permit approve", "jade hooks uninstall", "jade   hooks   install", "jade gate off", "jade claude-md apply",
    "python3 -c 'from jade import state_machine; state_machine.apply(1, {})'", "python3 -c 'import jade.state_machine'",
]
SI09_ALLOWED = ["jade check", "jade permit list", "jade permit request --command 'git push origin feat'", "uv run jade ask verify x -s y",
                "git commit -m 'document jade permit approve'", "grep -rn 'permit approve' docs", "cat jade/permits.py", "uv run pytest tests -q",
                "git add jade/permits.py policies/safety.yaml", "jade decide list", "uv run --quiet jade work render"]


@pytest.mark.parametrize("cmd", SI09_BLOCKED)
def test_f06_si09_matches_the_words_not_one_spelling(cmd):
    assert "SI-09" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked
    r = hooks.pretool(call(cmd))
    assert r.exit_code == 2 and "SI-09" in r.stderr


@pytest.mark.parametrize("cmd", SI09_ALLOWED)
def test_f06_si09_leaves_ordinary_jade_commands_alone(cmd):
    assert "SI-09" not in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


def _fake_ps(monkeypatch, rows):
    out = "\n".join(f"{pid} {ppid} {cmd}" for pid, ppid, cmd in rows)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))


def test_f06_an_agent_among_the_ancestors_refuses_approval_even_with_a_pty_and_a_clean_environment(monkeypatch, human_env):
    me = os.getpid()
    _fake_ps(monkeypatch, [(me, 500, "python -m jade permit approve"), (500, 400, "/usr/bin/python3 pty_helper.py"), (400, 300, "/bin/zsh -c run"),
                           (300, 1, "/Users/x/.local/bin/claude --dangerously-skip-permissions")])
    assert "agent process" in REAL_ANCESTOR()
    monkeypatch.setattr(util, "agent_ancestor", REAL_ANCESTOR)
    db = ledger.open()
    p = permits.request(db, "git.push", "f" * 64, risk="high")
    with pytest.raises(permits.ApprovalRefused, match="agent process"):
        permits.approve(db, p.permit_id, stdin_isatty=True)  # a pty satisfies isatty; the ancestry does not change
    assert permits.get(db, p.permit_id).state == "PENDING"


def test_f06_a_human_terminal_chain_and_the_desktop_app_are_not_agents(monkeypatch):
    me = os.getpid()
    _fake_ps(monkeypatch, [(me, 500, "python -m jade permit approve"), (500, 400, "-zsh"), (400, 300, "login -pf x"),
                           (300, 1, "/Applications/Claude.app/Contents/MacOS/Claude")])
    assert REAL_ANCESTOR() is None


def test_f06_an_unreadable_process_table_refuses(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(subprocess, "run", boom)
    assert "process table" in REAL_ANCESTOR()


def _approve_cli(monkeypatch, capsys, pid, *, answer, yes=False, ancestor=None):
    monkeypatch.setattr(cli, "human_tty_refusal", lambda *a, **k: ancestor)
    monkeypatch.setattr(util, "agent_ancestor", lambda: ancestor)
    monkeypatch.setattr(permits, "human_tty_refusal", lambda *a, **k: ancestor)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    rc = cli.main(["permit", "approve", pid, *(["-y"] if yes else [])])
    return rc, capsys.readouterr()


def test_f06_dash_y_is_not_accepted_for_a_high_permit_and_a_code_must_be_typed(monkeypatch, capsys, human_env):
    db = ledger.open()
    hooks.pretool(call(PUSH))
    p = pending(db)
    rc, out = _approve_cli(monkeypatch, capsys, p.permit_id, answer="y", yes=True)
    assert rc == 1 and "-y is not accepted" in out.err and permits.get(db, p.permit_id).state == "PENDING"
    rc, out = _approve_cli(monkeypatch, capsys, p.permit_id, answer="y")
    assert rc == 1 and "not approved" in out.out and permits.get(db, p.permit_id).state == "PENDING"
    rc, out = _approve_cli(monkeypatch, capsys, p.permit_id, answer=p.plan_hash[:6])
    assert rc == 0 and permits.get(db, p.permit_id).state == "ISSUED"


# ---- F07: the hook trusts nothing the permit row says about itself ---------------------------------------
def test_f07_a_forged_low_permit_does_not_authorize_a_critical_command(human_env):
    db = ledger.open()
    a = policy.assess("Bash", {"command": CRITICAL}, "/tmp/x")
    assert a.level == "critical"
    permits.issue(db, "kubectl.delete", a.plan_hash, risk="low")
    r = hooks.pretool(call(CRITICAL))
    assert r.exit_code == 2 and "needs a human-approved permit" in r.stderr


def test_f07_a_high_permit_never_covers_a_critical_command_with_the_same_plan_hash(human_env):
    db = ledger.open()
    a = policy.assess("Bash", {"command": CRITICAL}, "/tmp/x")
    q = permits.request(db, "kubectl.delete", a.plan_hash, risk="high")
    permits.approve(db, q.permit_id, stdin_isatty=True)  # a human approved a *high* permit for this hash
    assert permits.find_usable(db, a.plan_hash, level="critical") is None
    assert not permits.consume(db, q.permit_id, a.plan_hash, level="critical")
    assert hooks.pretool(call(CRITICAL)).exit_code == 2
    assert permits.get(db, q.permit_id).state == "ISSUED"


def test_f07_a_row_without_a_human_principal_and_tty_reference_is_not_enough_for_a_high_command(human_env):
    db = ledger.open()
    a = policy.assess("Bash", {"command": PUSH}, "/tmp/x")
    p = permits.issue(db, "git.push", a.plan_hash, risk="medium")  # risk >= the command's would need to be claimed; the human fields are missing
    db.con.execute("UPDATE permits SET risk_level='critical', doc_json=json_set(doc_json,'$.risk_level','critical') WHERE permit_id=?", (p.permit_id,))
    assert permits.try_consume(db, p.permit_id, a.plan_hash, level="high") == "a high command needs a permit a human approved from a terminal"
    assert hooks.pretool(call(PUSH)).exit_code == 2


def test_f07_the_approved_path_still_works(human_env):
    db, p = blocked_then_approved(CRITICAL)
    assert permits.get(db, p.permit_id).risk_level == "critical"
    r = hooks.pretool(call(CRITICAL))
    assert r.exit_code == 0 and "consumed" in r.stderr + r.stdout


# ---- F08: the plan hash binds the exact text -------------------------------------------------------------
@pytest.mark.parametrize("a,b", [
    ('rm -rf "/tmp/jb/my dir"', 'rm -rf "/tmp/jb/my  dir"'),
    ("rm -rf '/tmp/jb/a b'", "rm -rf '/tmp/jb/a      b'"),
    ("kubectl apply -f - <<'EOF'\nspec:\n  a: 1\nEOF", "kubectl apply -f - <<'EOF'\nspec:\na: 1\nEOF"),
    ("git push origin feat", "git push\torigin feat"),
    ("git push origin feat", "git push origin\nfeat"),
])
def test_f08_whitespace_that_changes_the_command_changes_the_hash(a, b):
    assert policy.plan_hash("Bash", a, "/tmp/x") != policy.plan_hash("Bash", b, "/tmp/x")
    assert policy.assess("Bash", {"command": a}, "/tmp/x").plan_hash != policy.assess("Bash", {"command": b}, "/tmp/x").plan_hash


def test_f08_only_the_ends_are_trimmed():
    assert policy.plan_hash("Bash", "  git push origin feat \n", "/tmp/x") == policy.plan_hash("Bash", "git push origin feat", "/tmp/x")


def test_f08_a_permit_for_one_quoted_path_does_not_authorize_another(human_env):
    db, _ = blocked_then_approved('rm -rf "/tmp/jade-b/my dir"')
    r = hooks.pretool(call('rm -rf "/tmp/jade-b/my  dir"'))
    assert r.exit_code == 2
    assert hooks.pretool(call('rm -rf "/tmp/jade-b/my dir"')).exit_code == 0


def test_f08_the_plan_hash_covers_the_resolved_cwd(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    link = tmp_path / "link"
    link.symlink_to(a)
    h1 = policy.plan_hash("Bash", PUSH, str(link))
    link.unlink()
    link.symlink_to(b)
    assert policy.plan_hash("Bash", PUSH, str(link)) != h1
    assert policy.plan_hash("Bash", PUSH, str(b)) == policy.plan_hash("Bash", PUSH, str(link))


# ---- F09: what the human reads is everything the permit covers -------------------------------------------
def test_f09_a_long_command_is_shown_in_full_and_padding_cannot_hide_a_second_command(human_env):
    cmd = "git push origin feat-a" + " " * 4100 + "; git push --force origin feat-victim"
    db = ledger.open()
    assert hooks.pretool(call(cmd)).exit_code == 2
    p = pending(db)
    screen = permits.review_text(p)
    assert "feat-victim" in screen and str(len(cmd)) in screen and p.plan_hash[:12] in screen
    assert permits.ctx_of(p)["truncated"] is False


def test_f09_a_command_too_long_to_show_cannot_be_approved(human_env, monkeypatch, capsys):
    cmd = "git push origin feat-a" + " " * (permits.MAX_SUBJECT + 10) + "; git push --force origin feat-victim"
    db = ledger.open()
    assert hooks.pretool(call(cmd)).exit_code == 2
    p = pending(db)
    assert permits.ctx_of(p)["truncated"] is True
    with pytest.raises(permits.PermitError, match="cannot be shown in full"):
        permits.review_text(p)
    with pytest.raises(permits.PermitError, match="cannot be shown in full"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    rc, out = _approve_cli(monkeypatch, capsys, p.permit_id, answer=p.plan_hash[:6])
    assert rc == 1 and "cannot be shown in full" in out.err and permits.get(db, p.permit_id).state == "PENDING"


def test_f09_control_characters_are_escaped_on_the_approval_screen(human_env):
    cmd = "git push origin feat-x\n# \x1b[2K\rgit status‮ ​\x07"
    db = ledger.open()
    assert hooks.pretool(call(cmd)).exit_code == 2
    screen = permits.review_text(pending(db))
    assert "\x1b" not in screen and "\r" not in screen and "‮" not in screen and "​" not in screen and "\x07" not in screen
    assert "\\x1b" in screen and "\\u202e" in screen and "\\x0d" in screen


def test_f09_a_scope_that_does_not_hash_to_the_permit_is_refused(human_env):
    db = ledger.open()
    hooks.pretool(call(PUSH))
    p = pending(db)
    ctx = permits.ctx_of(p)
    ctx["subject"] = "git status"
    db.con.execute("UPDATE permits SET scope=? WHERE permit_id=?", (json.dumps(ctx), p.permit_id))
    with pytest.raises(permits.PermitError, match="does not match the permit's plan hash"):
        permits.approve(db, p.permit_id, stdin_isatty=True)


def test_f09_the_cli_request_path_carries_the_same_review_context(human_env, capsys):
    assert cli.main(["permit", "request", "--command", "git push origin feat-y", "--cwd", "/tmp/x"]) == 0
    db = ledger.open()
    p = pending(db)
    assert permits.review_text(p).count("git push origin feat-y") == 1
    assert permits.ctx_of(p)["length"] == len("git push origin feat-y")


# ---- F10 / F13: expiry and the decision lifecycle ---------------------------------------------------------
def test_f10_an_expired_pending_permit_cannot_be_approved(human_env):
    db = ledger.open()
    p = permits.request(db, "git.push", "f" * 64, risk="high")
    db.con.execute("UPDATE permits SET expires_at='2020-01-01T00:00:00Z' WHERE permit_id=?", (p.permit_id,))
    with pytest.raises(permits.PermitError, match="expired"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    assert permits.get(db, p.permit_id).state == "EXPIRED"
    assert permits.find_usable(db, "f" * 64, level="high") is None


def test_f13_a_cancelled_decision_revokes_its_permit_and_nothing_can_approve_or_use_it(human_env):
    db = ledger.open()
    assert hooks.pretool(call(PUSH)).exit_code == 2
    p = pending(db)
    decide.step(db, p.decision_id, "cancel", guard="changed my mind")
    assert permits.get(db, p.permit_id).state == "REVOKED"
    with pytest.raises(permits.PermitError, match="REVOKED"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(call(PUSH)).exit_code == 2  # a fresh request, not the old permit


def test_f13_a_permit_approved_before_the_decision_is_cancelled_is_revoked_and_not_consumed(human_env):
    db, p = blocked_then_approved()
    decide.step(db, p.decision_id, "cancel")
    assert permits.get(db, p.permit_id).state == "REVOKED"
    r = hooks.pretool(call(PUSH))
    assert r.exit_code == 2 and "needs a human-approved permit" in r.stderr


def test_f13_an_expired_decision_cannot_have_its_permit_approved_or_consumed(human_env):
    db = ledger.open()
    assert hooks.pretool(call(PUSH)).exit_code == 2
    p = pending(db)
    db.con.execute("UPDATE decisions SET expires_at='2020-01-01T00:00:00Z' WHERE decision_id=?", (p.decision_id,))
    decide.sweep(db)
    assert decide.get(db, p.decision_id)["status"] == "EXPIRED"
    with pytest.raises(permits.PermitError):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    # the decision expired between approval and use
    db2, q = blocked_then_approved("git push origin feat-z")
    db2.con.execute("UPDATE decisions SET expires_at='2020-01-01T00:00:00Z' WHERE decision_id=?", (q.decision_id,))
    r = hooks.pretool(call("git push origin feat-z"))
    assert r.exit_code == 2
    assert decide.get(db2, q.decision_id)["status"] == "EXPIRED"


def test_f13_a_permit_whose_decision_status_is_not_live_is_refused_even_if_it_is_still_issued(human_env):
    db, p = blocked_then_approved()
    db.con.execute("UPDATE decisions SET status='CANCELLED' WHERE decision_id=?", (p.decision_id,))  # bypasses apply: the revoke hook never ran
    a = policy.assess("Bash", {"command": PUSH}, "/tmp/x")
    assert "its decision is CANCELLED" in permits.try_consume(db, p.permit_id, a.plan_hash, level=a.level, fingerprints=hooks.fingerprints(PUSH, "/tmp/x"),
                                                                session="sess-aaaaaaaa")
    assert permits.get(db, p.permit_id).state == "REVOKED"


# ---- F11: preconditions, policy version, cwd -------------------------------------------------------------
def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "one"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env)
    return repo, env


def test_f11_a_push_permit_is_not_used_after_head_moved(tmp_path, human_env):
    repo, env = _repo(tmp_path)
    db, p = blocked_then_approved("git push origin feat-x", cwd=str(repo))
    assert [x["kind"] for x in permits.get(db, p.permit_id).preconditions] == ["policy_version", "git_head"]
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "two"], cwd=repo, check=True, env=env)
    r = hooks.pretool(call("git push origin feat-x", cwd=str(repo)))
    assert r.exit_code == 2 and "git_head changed since approval" in r.stderr
    assert permits.get(db, p.permit_id).state == "ISSUED"  # not used up: the same state would still match it


def test_f11_the_pending_request_is_replaced_when_the_repository_moved(tmp_path, human_env):
    repo, env = _repo(tmp_path)
    db = ledger.open()
    hooks.pretool(call("git push origin feat-x", cwd=str(repo)))
    first = pending(db)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "two"], cwd=repo, check=True, env=env)
    hooks.pretool(call("git push origin feat-x", cwd=str(repo)))
    assert permits.get(db, first.permit_id).state == "REVOKED" and pending(db).permit_id != first.permit_id


def test_f11_a_changed_policy_revokes_open_permits(human_env, monkeypatch):
    db, p = blocked_then_approved()
    monkeypatch.setattr(paths, "policy_version", lambda: "0" * 12)
    r = hooks.pretool(call(PUSH))
    assert r.exit_code == 2
    assert permits.get(db, p.permit_id).state == "REVOKED"
    q = permits.request(db, "git.push", "e" * 64, risk="high")
    monkeypatch.undo()
    permits.sweep(db)
    assert permits.get(db, q.permit_id).state == "REVOKED"


def test_f11_a_permit_for_a_symlinked_cwd_is_not_used_after_the_link_is_retargeted(tmp_path, human_env):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    link = tmp_path / "link"
    link.symlink_to(a)
    db, p = blocked_then_approved(cwd=str(link))
    link.unlink()
    link.symlink_to(b)
    assert hooks.pretool(call(PUSH, cwd=str(link))).exit_code == 2
    assert permits.get(db, p.permit_id).state == "ISSUED"


# ---- G08: session and agent scope ------------------------------------------------------------------------
def test_g08_a_permit_is_used_only_by_the_session_and_agent_that_requested_it(human_env):
    db, p = blocked_then_approved(session="sess-aaaaaaaa")
    assert hooks.pretool(call(PUSH, session="sess-bbbbbbbb")).exit_code == 2     # another session
    assert hooks.pretool(call(PUSH, session="sess-aaaaaaaa", agent="sub-1")).exit_code == 2  # a subagent of the same session
    assert permits.get(db, p.permit_id).state == "ISSUED"
    r = hooks.pretool(call(PUSH, session="sess-aaaaaaaa"))
    assert r.exit_code == 0 and "consumed" in r.stderr + r.stdout


def test_g08_the_message_says_why_the_existing_permit_was_not_used(human_env):
    blocked_then_approved(session="sess-aaaaaaaa")
    r = hooks.pretool(call(PUSH, session="sess-bbbbbbbb"))
    assert "requested by another session or agent" in r.stderr


def test_g08_each_session_gets_its_own_pending_request(human_env):
    db = ledger.open()
    hooks.pretool(call(PUSH, session="sess-aaaaaaaa"))
    first = pending(db)
    hooks.pretool(call(PUSH, session="sess-aaaaaaaa"))
    assert pending(db).permit_id == first.permit_id  # the same caller asking again sees the same request
    hooks.pretool(call(PUSH, session="sess-bbbbbbbb"))
    second = pending(db)
    assert second.permit_id != first.permit_id and permits.get(db, first.permit_id).state == "PENDING"  # B does not revoke A's request (pass 2)
    assert permits.ctx_of(second)["session_id"] == "sess-bbbbbbbb"


# ---- F12: an agent cannot walk a decision to SUCCEEDED -----------------------------------------------------
def _run_cli(argv, monkeypatch, capsys, session="sess-cli-aaaa"):
    monkeypatch.setenv("JADE_SESSION_ID", session)
    monkeypatch.setattr(cli, "human_tty_refusal", lambda *a, **k: "agent")  # an agent session
    rc = cli.main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_f12_the_six_event_path_to_succeeded_is_closed(monkeypatch, capsys):
    rc, out, _ = _run_cli(["decide", "open", "--objective", "verify bypass test objective"], monkeypatch, capsys)
    did = json.loads(out)["decision_id"]
    results = [_run_cli(["decide", "event", did, e], monkeypatch, capsys)[0] for e in ("route", "fast_accept", "authorize", "execute", "observe", "verified")]
    assert results[:3] == [0, 0, 0]  # evaluation and authorization of a low-risk decision are the agent's to send
    assert results[4:] == [1, 1]     # observe and verified are never sent by an agent
    db = ledger.open()
    assert decide.get(db, did)["status"] != "SUCCEEDED"
    assert db.one("SELECT COUNT(*) AS n FROM outcomes").n == 0


def test_f12_succeeded_needs_an_observed_successful_outcome_row_even_in_process():
    db = ledger.open()
    d = decide.open_decision(db, "Run the thing and check it", command="echo hi")
    for ev in ("route", "jev_result_accept", "authorize", "execute", "observe"):
        decide.step(db, d["decision_id"], ev)
    with pytest.raises(state_machine.IllegalTransition, match="SUCCEEDED needs a recorded outcome"):
        decide.step(db, d["decision_id"], "verified")
    decide.record_outcome(db, d["decision_id"], "estimated", True, "looks fine")
    with pytest.raises(state_machine.IllegalTransition, match="SUCCEEDED needs"):
        decide.step(db, d["decision_id"], "verified")
    decide.record_outcome(db, d["decision_id"], "observed", True, "read the real state")
    assert decide.get(db, d["decision_id"])["status"] == "SUCCEEDED"


def test_f12_a_high_risk_decision_cannot_be_executed_by_an_agent(monkeypatch, capsys):
    _, out, _ = _run_cli(["decide", "open", "--objective", "push the branch somewhere", "--command", PUSH], monkeypatch, capsys)
    did = json.loads(out)["decision_id"]
    for e in ("route", "fast_accept"):
        assert _run_cli(["decide", "event", did, e], monkeypatch, capsys)[0] == 0
    for e in ("authorize", "execute"):
        assert _run_cli(["decide", "event", did, e], monkeypatch, capsys)[0] == 1
    assert decide.get(ledger.open(), did)["status"] == "ACCEPTED"


def test_f12_another_session_may_only_cancel(monkeypatch, capsys):
    _, out, _ = _run_cli(["decide", "open", "--objective", "a decision of session A"], monkeypatch, capsys, session="sess-owner-aaaa")
    did = json.loads(out)["decision_id"]
    rc, _, err = _run_cli(["decide", "event", did, "route"], monkeypatch, capsys, session="sess-other-bbbb")
    assert rc == 1 and "another session" in err
    assert _run_cli(["decide", "event", did, "route"], monkeypatch, capsys, session="sess-owner-aaaa")[0] == 0
    assert _run_cli(["decide", "event", did, "cancel"], monkeypatch, capsys, session="sess-other-bbbb")[0] == 0


def test_f12_an_agent_cannot_cancel_a_decision_whose_outcome_is_unknown_or_running(monkeypatch, capsys, human_env):
    db, p = executed("fly deploy")
    rc, _, err = _run_cli(["decide", "event", p.decision_id, "cancel"], monkeypatch, capsys)
    assert rc == 1 and "need an interactive human terminal" in err
    assert hooks.pretool(call("fly deploy")).exit_code == 2  # the SI-07 hold is still in place


def test_f12_deadline_is_refused_before_the_time_has_come(monkeypatch, capsys):
    _, out, _ = _run_cli(["decide", "open", "--objective", "something with a long life ahead"], monkeypatch, capsys)
    did = json.loads(out)["decision_id"]
    assert _run_cli(["decide", "event", did, "deadline"], monkeypatch, capsys)[0] == 1
    with pytest.raises(state_machine.IllegalTransition, match="has not passed"):
        decide.step(ledger.open(), did, "deadline")


def test_f12_outcome_from_another_session_is_refused(monkeypatch, capsys, human_env):
    db, p = executed("fly deploy", session="sess-owner-aaaa")
    rc, _, err = _run_cli(["decide", "outcome", p.decision_id, "--type", "observed", "--success", "true", "--method", "m"], monkeypatch, capsys,
                          session="sess-other-bbbb")
    assert rc == 1 and "another session" in err and decide.get(db, p.decision_id)["status"] == "EXECUTING"


# ---- F16: every non-terminal state has an exit -----------------------------------------------------------
def _revise_to_the_cap(db):
    d = decide.open_decision(db, "A decision that keeps asking for revision", command="echo hi")
    did = d["decision_id"]
    decide.step(db, did, "route")
    for _ in range(3):
        decide.step(db, did, "jev_result_abstain")
        decide.step(db, did, "add_evidence")
    decide.step(db, did, "deep_revise")
    return did


def test_f16_needs_revision_at_the_cap_can_escalate_and_expire():
    db = ledger.open()
    did = _revise_to_the_cap(db)
    assert decide.get(db, did)["status"] == "NEEDS_REVISION" and decide.get(db, did)["revision_count"] == 3
    with pytest.raises(state_machine.IllegalTransition, match="escalate instead"):
        decide.step(db, did, "revise")
    assert decide.step(db, did, "escalate").status == "WAITING_HUMAN"
    d2 = _revise_to_the_cap(db)
    db.con.execute("UPDATE decisions SET expires_at='2020-01-01T00:00:00Z' WHERE decision_id=?", (d2,))
    decide.sweep(db)
    assert decide.get(db, d2)["status"] == "EXPIRED"


def test_f16_the_sweep_ends_every_waiting_state_and_never_expires_an_unknown_outcome():
    db = ledger.open()
    ids = {}
    for st in ("CLASSIFIED", "EVALUATING", "ABSTAINED", "ACCEPTED", "AUTHORIZED", "FAILED"):
        d = decide.open_decision(db, f"A decision parked in {st}", command="echo hi")
        ids[st] = d["decision_id"]
        db.con.execute("UPDATE decisions SET status=?, expires_at='2020-01-01T00:00:00Z' WHERE decision_id=?", (st, d["decision_id"]))
    unknown = decide.open_decision(db, "An unknown outcome stays unknown", command="echo hi")["decision_id"]
    verifying = decide.open_decision(db, "A verification that never finished", command="echo hi")["decision_id"]
    db.con.execute("UPDATE decisions SET status='OUTCOME_UNKNOWN', expires_at='2020-01-01T00:00:00Z' WHERE decision_id=?", (unknown,))
    db.con.execute("UPDATE decisions SET status='VERIFYING', expires_at='2020-01-01T00:00:00Z' WHERE decision_id=?", (verifying,))
    n = decide.sweep(db)
    assert {s: status(db, i) for s, i in ids.items()} == dict.fromkeys(ids, "EXPIRED")
    assert status(db, unknown) == "OUTCOME_UNKNOWN"
    assert status(db, verifying) == "OUTCOME_UNKNOWN" and n["unknown"] == 1


def test_f16_a_crash_between_the_permit_and_its_decision_loses_nothing(human_env, monkeypatch):
    db, p = blocked_then_approved()

    def crash():
        raise RuntimeError("killed between the steps")

    monkeypatch.setattr(state_machine, "_after_event_insert", crash)
    r = hooks.pretool(call(PUSH))
    assert r.exit_code == 2  # fails closed
    assert permits.get(db, p.permit_id).state == "ISSUED" and decide.get(db, p.decision_id)["status"] == "WAITING_HUMAN"
    monkeypatch.setattr(state_machine, "_after_event_insert", lambda: None)
    r = hooks.pretool(call(PUSH))
    assert r.exit_code == 0 and decide.get(db, p.decision_id)["status"] == "EXECUTING"
    assert sqlite3.connect(db.path).execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_f16_an_outcome_is_recorded_in_one_transaction(human_env, monkeypatch):
    db, p = executed("fly deploy")
    assert hooks.pretool(call("fly deploy")).exit_code == 2  # SI-07: still executing
    monkeypatch.setattr(state_machine, "_after_event_insert", lambda: (_ for _ in ()).throw(RuntimeError("killed")))
    with pytest.raises(RuntimeError):
        decide.record_outcome(db, p.decision_id, "observed", True, "checked")
    monkeypatch.setattr(state_machine, "_after_event_insert", lambda: None)
    assert decide.get(db, p.decision_id)["status"] == "EXECUTING" and db.one("SELECT COUNT(*) AS n FROM outcomes").n == 0


def test_f12_auto_label_does_not_trust_a_succeeded_status_without_an_observed_outcome():
    from jade import evals
    from tests.acceptance.test_phase3_calibrate import _answered

    db = ledger.open()
    d = decide.open_decision(db, "Report that the deploy worked", command=None)
    _answered(db, "sA", 0.9, "deploy worked", d["decision_id"])
    db.con.execute("UPDATE decisions SET status='SUCCEEDED' WHERE decision_id=?", (d["decision_id"],))  # a stray write, no outcome row
    assert evals.auto_label(db)["observed"] == 0
    db.con.execute("INSERT INTO outcomes(outcome_id, decision_id, doc_json, ts) VALUES('out_aaaaaaaaaaaa', ?, ?, '2026-11-09T00:00:00Z')",
                   (d["decision_id"], json.dumps({"observation_type": "observed", "success": True})))
    assert evals.auto_label(db)["observed"] == 1


def test_ledger_tx_is_re_entrant_and_rolls_back_as_a_whole():
    db = ledger.open()
    with pytest.raises(RuntimeError), db.tx() as con:
        con.execute("INSERT INTO meta(k, v) VALUES('a', '1')")
        with db.tx() as con2:
            con2.execute("INSERT INTO meta(k, v) VALUES('b', '2')")
        raise RuntimeError("outer fails after the inner step succeeded")
    assert db.meta_get("a") is None and db.meta_get("b") is None
    with pytest.raises(ValueError), db.tx() as con:
        con.execute("INSERT INTO meta(k, v) VALUES('c', '3')")
        try:
            with db.tx() as con2:
                con2.execute("INSERT INTO meta(k, v) VALUES('d', '4')")
                raise ValueError("inner fails")
        except ValueError:
            pass  # the inner savepoint is rolled back, the outer carries on
        raise ValueError("then the outer fails too")
    assert db.meta_get("c") is None and db.meta_get("d") is None
