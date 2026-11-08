import pytest
from jade import hooks, permits, state_machine  # noqa: F401


@pytest.mark.parametrize("src,event,dst", [
    ("EVALUATING", "jev_result_accept", "ACCEPTED"),
    ("EVALUATING", "jev_error", "NEEDS_EVIDENCE"),
    ("ACCEPTED", "authorize_without_permit_high_risk", "WAITING_HUMAN"),
    ("VERIFYING", "unknown", "OUTCOME_UNKNOWN"),
])
def test_state_machine_transitions(src, event, dst):
    assert state_machine.next_state(src, event, risk="high") == dst


@pytest.mark.parametrize("src,event", [("EVALUATING", "execute"), ("ACCEPTED", "execute"), ("OUTCOME_UNKNOWN", "retry")])
def test_forbidden_edges(src, event):
    with pytest.raises(state_machine.IllegalTransition):
        state_machine.next_state(src, event, risk="low")


def test_permit_single_use_and_ttl(tmp_path):
    p = permits.issue(tmp_path, action="git.push", plan_hash="f" * 64, ttl_s=1800, risk="low")
    assert permits.consume(tmp_path, p.permit_id, plan_hash="f" * 64)
    assert not permits.consume(tmp_path, p.permit_id, plan_hash="f" * 64)


def test_permit_plan_hash_mismatch_blocks(tmp_path):
    p = permits.issue(tmp_path, action="git.push", plan_hash="f" * 64, ttl_s=1800, risk="low")
    assert not permits.consume(tmp_path, p.permit_id, plan_hash="e" * 64)


def test_approve_refuses_non_tty(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    p = permits.request(tmp_path, action="deploy.prod", plan_hash="f" * 64, risk="critical")
    with pytest.raises(permits.ApprovalRefused):
        permits.approve(tmp_path, p.permit_id, stdin_isatty=False)


def test_hook_fails_closed_on_internal_error_for_high_risk(monkeypatch):
    monkeypatch.setattr(hooks, "_classify", lambda cmd: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hooks.pretool({"tool_name": "Bash", "tool_input": {"command": "git push origin x"}}).exit_code == 2


def test_safety_invariants_block_regardless_of_permit(tmp_path):
    p = permits.issue(tmp_path, action="shell", plan_hash="f" * 64, ttl_s=1800, risk="low")
    r = hooks.pretool({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}, permit=p)
    assert r.exit_code == 2 and "SI-08" in r.stderr


def test_degenerate_choice_rejected():
    from jade import questions
    with pytest.raises(questions.InvalidQuestion):
        questions.build_choice("q.strategy.best_option", options={"only": "one"})


def test_duplicate_delivery_idempotent(tmp_path):
    ev = {"decision_id": "dec_" + "0" * 12, "event": "observe", "idempotency_key": "k1"}
    assert state_machine.apply(tmp_path, ev).applied and not state_machine.apply(tmp_path, ev).applied
