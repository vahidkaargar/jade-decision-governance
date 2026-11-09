"""Phase 5 acceptance (docs/06): rollback drill, drift canary, retention job, launchd job, end-to-end flow."""
import json
import plistlib
import time

from jade import adapter, ask, cli, ledger, ops

Q = "q.verify_claim.supported_by_evidence"


def test_rollback_drill_restores_champion(tmp_path):
    ops.pin_champion(tmp_path, Q, 1)
    ops.set_challenger(tmp_path, Q, 2)
    assert ops.active_version(tmp_path, Q) == 2
    t0 = time.perf_counter()
    ops.rollback(tmp_path, Q)
    elapsed = time.perf_counter() - t0
    assert ops.active_version(tmp_path, Q) == 1 and elapsed < 1.0  # the drill is one pointer flip


def test_promote_challenger_keeps_previous_champion_for_rollback(tmp_path):
    ops.pin_champion(tmp_path, Q, 1)
    ops.set_challenger(tmp_path, Q, 2)
    assert ops.promote_challenger(tmp_path, Q) == 2
    assert ops.active_version(tmp_path, Q) == 2
    assert json.loads((tmp_path / "pins.json").read_text())[Q]["previous_champion"] == 1


def test_drift_canary_fires_on_model_string_change(tmp_path):
    exp = ["typesafe/jev-1.14-20261014"]
    assert ops.check_drift(tmp_path, resolved_model="typesafe/jev-1.14-20261014", expected=exp) is None
    d = ops.check_drift(tmp_path, resolved_model="typesafe/jev-1.15-20270101", expected=exp)
    assert d.kind == "MODEL_DRIFT" and d.first_seen
    assert ops.check_drift(tmp_path, resolved_model="typesafe/jev-1.15-20270101", expected=exp).first_seen is False  # one work item per new string
    db = ledger.open(tmp_path)
    assert db.one("SELECT COUNT(*) AS n FROM work_items WHERE id LIKE 'RT-%'").n == 1
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE kind='MODEL_DRIFT'").n == 1


def test_model_upgrade_starts_a_new_group_at_default_thresholds_and_is_not_frozen():
    """Decision Q6: follow upgrades. A new snapshot is answered normally, journaled as drift, judged with default bands."""
    prov = adapter.FixtureProvider(lambda s, q, m: adapter.Result(ok=True, answers={k: {"type": "noul", "noul": 0.75} for k in q}, resolved_model="typesafe/jev-1.15-20270101", latency_ms=1.0))
    r = ask.run(ask.verify_specs(companion=False), {"claim": "c", "evidence": "e"}, provider=prov, session="s")
    assert r.exit_code == 0 and r.primary.verdict.group == "verify_claim/v1/jev-1.15-20270101"
    assert ledger.open().one("SELECT COUNT(*) AS n FROM events WHERE kind='MODEL_DRIFT'").n == 1


def test_maintain_job_runs_offline_and_reports_failures(capsys, human_env):
    code = cli.main(["maintain", "--offline"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1 and "hooks installed" in out["check_failed"] and {"decisions", "purged", "mined"} <= set(out)  # unwired hooks make the job non-zero
    ops.hooks_install(stdin_isatty=True)
    ops.hooks_uninstall(stdin_isatty=True)


def test_launchd_plist_is_valid_and_runs_maintain():
    plist = plistlib.loads(ops.launchd_plist("com.example.jade").encode())
    assert plist["Label"] == "com.example.jade" and plist["ProgramArguments"][1:] == ["maintain"] and "StartCalendarInterval" in plist


def test_end_to_end_blocked_push_approved_then_verified(human_env):
    """A whole governed action: the gate blocks, a human approves, the command runs once, the outcome is observed."""
    from jade import decide, hooks, permits

    db = ledger.open()
    ev = {"tool_name": "Bash", "tool_input": {"command": "git push origin feat/jade"}, "session_id": "sess-e2e-0001", "cwd": "/tmp/repo"}
    assert hooks.pretool(ev).exit_code == 2
    p = permits.listing(db, ("PENDING",))[0]
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.99} for k in q})
    ask.run(ask.verify_specs(), {"claim": "safe to push", "evidence": "Jev would say yes"}, provider=prov, session="sess-e2e-0001", risk="high")
    assert hooks.pretool(ev).exit_code == 2  # a confident answer is still not a permit
    permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(ev).exit_code == 0
    assert hooks.pretool(ev).exit_code == 2  # single use
    hooks.posttool({**ev, "tool_response": {"is_error": False}})
    status = decide.get(db, p.decision_id)["status"]
    assert status in ("VERIFYING", "SUCCEEDED")
    kinds = [e.kind for e in db.all("SELECT kind FROM events WHERE entity_id=? ORDER BY id", (p.decision_id,))]
    assert kinds[:2] == ["open", "classify"] and "execute" in kinds
