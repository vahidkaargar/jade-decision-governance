"""Judging, adapter, calibration and improvement (review cluster D: F15, F24-F34, F44, F45, F47). Each test reproduces a reviewed defect."""
import http.server
import json
import threading
import time

import pytest

from jade import adapter, ask, cli, compat, evals, hooks, improve, ledger, ops, paths, questions, thresholds
from jade.util import now, ts
from tests.promo_helpers import (
    Q,
    backdate_shadow,
    group_of,
    provider,
    registry_v2,
    seed,
    template_candidate,
    threshold_candidate,
)


def noul(p):
    return adapter.FixtureProvider(lambda s, q, m, p=p: {k: {"type": "noul", "noul": p} for k in q})


def ask_once(prov, db, claim="tests pass", evidence="42 passed", **kw):
    return ask.run(ask.verify_specs(companion=kw.pop("companion", False)), {"claim": claim, "evidence": evidence}, provider=prov, session=kw.pop("session", "s"), db=db, **kw)


def calibrate(tmp_path, group, accept, reject=0.3):
    cal = paths.home() / "calibration"
    cal.mkdir(exist_ok=True)
    (cal / "current.json").write_text(json.dumps({group: {"accept": accept, "reject": reject}}))


# ---- F15: the dedup anchor -------------------------------------------------------------------------------------
def test_a_failed_first_call_is_no_dedup_anchor_so_the_next_success_is_the_original(tmp_path):
    db = ledger.open(tmp_path)
    seq = iter([adapter.Result(ok=False, error_kind="UNAVAILABLE")] + [None] * 10)
    prov = adapter.FixtureProvider(lambda s, q, m: next(seq) or {k: {"type": "noul", "noul": 0.95} for k in q})
    results = [ask_once(prov, db) for _ in range(4)]
    assert [r.exit_code for r in results] == [5, 0, 0, 0]
    assert len(prov.calls) == 2                                    # call 3 and 4 are served from the cache of call 2
    assert [r.primary.cached for r in results] == [False, False, True, True]
    rows = db.all("SELECT dedup_of, exit_code FROM jev_calls ORDER BY rowid")
    assert rows[1].dedup_of is None and rows[2].dedup_of == db.all("SELECT call_id FROM jev_calls ORDER BY rowid")[1].call_id
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls WHERE p IS NOT NULL AND dedup_of IS NULL").n == 1   # the genuine answer stays visible to calibration


def test_a_row_left_evaluating_by_a_dead_process_is_closed_and_never_an_anchor(tmp_path):
    db = ledger.open(tmp_path)
    old = ts(now().replace(year=now().year - 1))
    db.begin_call(Q, "a" * 64, "s", claim_hash="c")
    db.con.execute("UPDATE jev_calls SET ts=?", (old,))
    nxt = db.begin_call(Q, "a" * 64, "s", claim_hash="c")
    assert db.get_call(nxt).dedup_of is None
    stale = db.one("SELECT state, error_kind, exit_code FROM jev_calls WHERE ts=?", (old,))
    assert (stale.state, stale.error_kind, stale.exit_code) == ("DONE", "STALE_EVALUATING", 5)


# ---- F24 / F30: a repeat is judged in the original's calibration group --------------------------------------------------
def test_a_cached_repeat_keeps_the_calibrated_verdict(tmp_path):
    db = ledger.open(tmp_path)
    group = "verify_claim/v1/jev-1.14-fixture"
    calibrate(tmp_path, group, 0.90)
    prov = noul(0.80)
    first, again = ask_once(prov, db), ask_once(prov, db)
    assert first.exit_code == 3 and first.primary.verdict.group == group
    assert again.primary.cached and again.exit_code == 3 and again.primary.verdict.group == group and again.primary.verdict.accept == 0.9
    assert len(prov.calls) == 1
    assert db.all("SELECT resolved_model FROM jev_calls ORDER BY rowid")[1].resolved_model == "typesafe/jev-1.14-fixture"   # the cached row names its group


def test_a_repeat_is_never_looser_than_the_first_answer(tmp_path):
    db = ledger.open(tmp_path)
    group = "verify_claim/v1/jev-1.14-fixture"
    calibrate(tmp_path, group, 0.90)
    prov = noul(0.80)
    assert ask_once(prov, db).exit_code == 3
    (paths.home() / "calibration" / "current.json").unlink()   # the calibration is gone, the default band would accept 0.80
    again = ask_once(prov, db)
    assert again.primary.cached and again.exit_code == 3


def test_an_answer_with_no_model_string_is_judged_at_the_strictest_calibrated_accept(tmp_path):
    db = ledger.open(tmp_path)
    calibrate(tmp_path, "verify_claim/v1/jev-1.14-20261014", 0.90)
    prov = adapter.FixtureProvider(lambda s, q, m: adapter.Result(ok=True, answers={k: {"type": "noul", "noul": 0.80} for k in q}, resolved_model=None))
    r = ask_once(prov, db)
    assert r.exit_code == 3 and r.primary.verdict.group == "verify_claim/v1/unknown" and any("no model string" in w for w in r.warnings)
    assert thresholds.bands("noul", "verify_claim", "low", "verify_claim/v1/unknown")[0] == 0.90


# ---- F27 / F28: what counts as calibration evidence ----------------------------------------------------------------------
def test_repeats_of_one_claim_are_not_two_hundred_labels(tmp_path):
    db = ledger.open(tmp_path)
    prov = noul(0.9)
    for _ in range(210):
        r = ask_once(prov, db, session="split-session")
        evals.set_label(db, r.primary.call_id, 1, "observed")
    rows = evals.labeled(db, include_protected=True)
    assert len(rows) == 1
    assert evals.propose_threshold([{**rows[0], "p": 0.9, "label": 1}] * 210) is None   # even handed the same row 210 times: one claim
    assert all(g["eligible_for_adjustment"] is False for g in evals.report(db, include_protected=True)["groups"].values())


def test_an_answer_with_no_model_string_never_feeds_calibration(tmp_path):
    db = ledger.open(tmp_path)
    r = ask_once(adapter.FixtureProvider(lambda s, q, m: adapter.Result(ok=True, answers={k: {"type": "noul", "noul": 0.9} for k in q})), db)
    evals.set_label(db, r.primary.call_id, 1, "observed")
    assert evals.labeled(db, include_protected=True) == []


def test_auto_label_joins_only_the_call_that_gated_the_decision(tmp_path):
    from jade import decide

    db = ledger.open(tmp_path)
    doc = decide.open_decision(db, "ship it")
    did = doc["decision_id"]
    specs = lambda: ask.verify_specs(companion=True)  # noqa: E731
    gate = ask.run(specs(), {"claim": "the claim", "evidence": "y"}, provider=noul(0.9), session="s", db=db, decision_id=did)
    rejected = ask.run(specs(), {"claim": "other claim", "evidence": "x"}, provider=noul(0.1), session="s", db=db, decision_id=did)   # same decision_id column, not a gate
    assert rejected.exit_code == 4 and gate.exit_code == 0
    refs = decide.get(db, did)["evaluation_refs"]
    assert refs == [gate.primary.call_id]
    db.con.execute("UPDATE decisions SET status='SUCCEEDED' WHERE decision_id=?", (did,))
    db.con.execute("INSERT INTO outcomes(outcome_id, decision_id, doc_json, ts) VALUES('o1', ?, ?, ?)", (did, json.dumps({"observation_type": "observed", "success": 1}), ts()))
    assert evals.auto_label(db)["observed"] == 1
    labelled = {r.call_id: r.label for r in db.all("SELECT call_id, label FROM labels")}
    assert labelled == {gate.primary.call_id: 1}      # not the rejected call on another claim, not the evidence_is_raw companion


# ---- F29: a 200 that does not answer the question is not an answer ---------------------------------------------------------------
@pytest.mark.parametrize("answer", [
    {"type": "score", "score": 0},                                                                                           # wrong type for a noul question
    {"type": "noul", "noul": True},                                                                                          # a bool is not a probability
    {"type": "noul", "noul": 1.5}, {"type": "noul", "noul": float("nan")}, {"type": "noul", "noul": -0.1},
    {"type": "choice", "choice": "a", "probabilities": {"a": 0.9}},                                                          # wrong type again
])
def test_a_noul_question_answered_with_anything_else_is_malformed(tmp_path, answer):
    db = ledger.open(tmp_path)
    r = ask_once(adapter.FixtureProvider(lambda s, q, m: {k: answer for k in q}), db)
    assert r.exit_code == 5 and r.primary.error_kind == "MALFORMED" and r.primary.verdict is None


@pytest.mark.parametrize("answer", [
    {"type": "choice", "choice": "z", "probabilities": {"z": 0.99, "a": 0.01}},        # an option that was not offered
    {"type": "choice", "choice": "a", "probabilities": {"a": 7.0, "b": -6.0}},        # outside [0, 1]
    {"type": "choice", "choice": "a", "probabilities": {"a": 0.7, "b": 0.9}},         # the chosen option is not the most probable
    {"type": "noul", "noul": 0.9},                                                    # not a choice
])
def test_a_choice_answer_is_held_to_what_was_offered(tmp_path, answer):
    db = ledger.open(tmp_path)
    spec = ask.adhoc_spec("choice", "Which?", options=["a=x", "b=y"])
    r = ask.run([spec], {"context": "c"}, provider=adapter.FixtureProvider(lambda s, q, m: {k: answer for k in q}), session="s", db=db)
    assert r.exit_code == 5 and r.primary.error_kind == "MALFORMED"


def test_a_response_without_the_companion_key_cannot_pass_at_risk_high(tmp_path):
    db = ledger.open(tmp_path)
    prov = adapter.FixtureProvider(lambda s, q, m: {"q": {"type": "noul", "noul": 0.97}})
    r = ask.run(ask.verify_specs(companion=True), {"claim": "c", "evidence": "e"}, provider=prov, session="s", db=db, risk="high")
    assert r.exit_code == 3 and "evidence_is_raw" in r.note
    low = ask.run(ask.verify_specs(companion=True), {"claim": "c2", "evidence": "e"}, provider=prov, session="s", db=db, risk="low")
    assert low.exit_code == 0       # at low risk the companion never gated


def test_judge_raises_on_an_unknown_or_mismatched_type():
    with pytest.raises(ValueError):
        thresholds.judge({"type": "weird"}, "verify_claim")
    with pytest.raises(ValueError):
        thresholds.judge({"type": "score", "score": 1}, "verify_claim", primitive="noul")
    assert thresholds.judge({"type": "score", "score": 1}, "evidence_quality", primitive="score").exit_code == 0


# ---- F31: "none of these" is not a yes ----------------------------------------------------------------------------------
def test_a_confident_none_of_these_is_an_abstain(tmp_path, capsys):
    ans = {"type": "choice", "choice": "none_of_these", "probabilities": {"none_of_these": 0.9, "a": 0.05, "b": 0.05}, "confidence": 0.8}
    code = cli.ask_main(["question", "q.root_cause.best_explanation", "--field", "observations=o", "-o", "a=x", "-o", "b=y"], provider=adapter.FixtureProvider(lambda s, q, m: {k: ans for k in q}))
    assert code == 3 and "no option fits" in capsys.readouterr().err
    ok = {"type": "choice", "choice": "a", "probabilities": {"none_of_these": 0.05, "a": 0.9, "b": 0.05}, "confidence": 0.8}
    assert cli.ask_main(["question", "q.root_cause.best_explanation", "--field", "observations=other", "-o", "a=x", "-o", "b=y"],
                        provider=adapter.FixtureProvider(lambda s, q, m: {k: ok for k in q})) == 0


def test_no_dead_companion_presence_config():
    assert "require_companion_presence" not in json.dumps(thresholds.escalation())


# ---- F32: every error path keeps the exit-code contract ---------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ["verify", "c", "--state-file", "/nonexistent/file", "--dry-run"],
    ["verify", "c", "--state-file", "/tmp"],
    ["verify", "c\udcff", "-s", "raw", "--dry-run"],
    ["verify", "c", "-s", "raw\udcff"],
    ["question", "q.verify_claim.supported_by_evidence", "--field", "claim=@/nonexistent", "--field", "evidence=x"],
    ["verify", "claim"],           # no evidence at all
])
def test_unreadable_or_undecodable_input_is_exit_6_and_journaled(argv, capsys):
    assert cli.ask_main(argv, provider=noul(0.9)) == 6
    assert "INVALID_QUESTION" in capsys.readouterr().err
    db = ledger.open()
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE kind='INVALID_QUESTION'").n == 1
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls WHERE state='EVALUATING'").n == 0


def test_stdin_that_is_not_text_is_exit_6(monkeypatch):
    class Bad:
        def read(self):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr("sys.stdin", Bad())
    assert cli.ask_main(["verify", "c", "-s", "-"], provider=noul(0.9)) == 6


def test_a_provider_that_raises_leaves_no_row_evaluating_and_exits_5(tmp_path):
    db = ledger.open(tmp_path)

    def boom(s, q, m):
        raise KeyError("x")

    r = ask_once(adapter.FixtureProvider(boom), db)
    assert r.exit_code == 5 and r.primary.error_kind == "INTERNAL" and r.primary.verdict is None
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls WHERE state='EVALUATING'").n == 0


@pytest.mark.parametrize("raiser", [
    lambda p, k, t: (_ for _ in ()).throw(__import__("http.client").client.IncompleteRead(b"xx", 100)),
    lambda p, k, t: (_ for _ in ()).throw(ValueError("x")),
    lambda p, k, t: (_ for _ in ()).throw(KeyError("x")),
])
def test_any_transport_failure_is_unavailable_never_a_traceback(raiser, tmp_path):
    db = ledger.open(tmp_path)
    prov = adapter.OpenRouterProvider(transport=raiser, key_fn=lambda: "k")
    r = ask_once(prov, db)
    assert r.exit_code == 5 and r.primary.error_kind == "UNAVAILABLE"


@pytest.mark.parametrize("usage", [[1], "x", {"cost": "0.1"}, {"cost": float("nan")}, {"cost": True, "input_tokens": "9"}])
def test_a_bad_usage_block_does_not_break_a_good_answer(usage, tmp_path):
    body = json.dumps({"answers": {"q": {"type": "noul", "noul": 0.9}}, "model": "typesafe/jev-1.14-20261014", "id": "r", "usage": usage})
    prov = adapter.OpenRouterProvider(transport=lambda p, k, t: (200, body, {}), key_fn=lambda: "k")
    r = ask_once(prov, ledger.open(tmp_path))
    assert r.exit_code == 0 and (r.cost_usd is None)


# ---- F33: a hard wall-clock deadline ------------------------------------------------------------------------------------------
class _Server:
    def __init__(self, handler):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/api/alpha/decisions"

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()


def _body():
    return json.dumps({"answers": {"q": {"type": "noul", "noul": 0.9}}, "model": "typesafe/jev-1.14-20261014", "id": "r"}).encode()


def test_a_slow_drip_body_cannot_hold_the_caller_past_the_deadline(monkeypatch, tmp_path):
    class Drip(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = _body()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for i in range(len(body)):
                    self.wfile.write(body[i:i + 1])
                    self.wfile.flush()
                    time.sleep(0.3)
            except OSError:
                pass

        def log_message(self, *a):
            pass

    with _Server(Drip) as url:
        monkeypatch.setattr(adapter, "URL", url)
        prov = adapter.OpenRouterProvider(key_fn=lambda: "fake-key", deadline_s=1.5)
        t0 = time.monotonic()
        r = ask_once(prov, ledger.open(tmp_path))
        elapsed = time.monotonic() - t0
    assert r.exit_code == 5 and r.primary.error_kind == "UNAVAILABLE"
    assert elapsed < 4.0


def test_a_truncated_body_is_unavailable(monkeypatch, tmp_path):
    class Short(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Length", "500")
            self.end_headers()
            self.wfile.write(b'{"answers":')
            self.wfile.flush()

        def log_message(self, *a):
            pass

    with _Server(Short) as url:
        monkeypatch.setattr(adapter, "URL", url)
        r = ask_once(adapter.OpenRouterProvider(key_fn=lambda: "fake-key", deadline_s=5), ledger.open(tmp_path))
    assert r.exit_code == 5


def test_a_normal_response_over_the_real_transport_still_works(monkeypatch, tmp_path):
    class Ok(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = _body()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    with _Server(Ok) as url:
        monkeypatch.setattr(adapter, "URL", url)
        r = ask_once(adapter.OpenRouterProvider(key_fn=lambda: "fake-key", deadline_s=5), ledger.open(tmp_path))
    assert r.exit_code == 0


def test_a_late_response_from_a_custom_transport_is_not_accepted(tmp_path):
    def slow(p, k, t):
        time.sleep(0.6)
        return 200, _body(), {}

    r = ask_once(adapter.OpenRouterProvider(transport=slow, key_fn=lambda: "k", deadline_s=0.2), ledger.open(tmp_path))
    assert r.exit_code == 5


def test_no_attempt_starts_after_the_deadline():
    calls = []
    prov = adapter.OpenRouterProvider(transport=lambda p, k, t: calls.append(t) or (429, "{}", {}), key_fn=lambda: "k", sleep=lambda s: time.sleep(0.05), deadline_s=0.02)
    res = prov.ask({}, {"q": {}}, "m")
    assert not res.ok and all(t <= 0.02 for t in calls)    # the 1 s floor is gone: no call is given more than the time that is left


# ---- F34: the shim against the original ------------------------------------------------------------------------------------------
def test_jev_model_env_is_the_default_model(monkeypatch, capsys):
    monkeypatch.setenv("JEV_MODEL", "envmodel")
    assert compat.main(["--dry-run", "choice", "Q?", "-o", "a=x", "-o", "b=y"]) == 0
    assert json.loads(capsys.readouterr().out)["model"] == "envmodel"
    assert compat.main(["--dry-run", "--model", "flagmodel", "choice", "Q?", "-o", "a=x", "-o", "b=y"]) == 0
    assert json.loads(capsys.readouterr().out)["model"] == "flagmodel"
    monkeypatch.delenv("JEV_MODEL")
    assert ask.resolve_model(None) == adapter.DEFAULT_MODEL


def test_verify_needs_evidence_like_the_registry_question(capsys):
    assert cli.ask_main(["verify", "claim", "-s", ""], prog="jev", provider=noul(0.9)) == 6
    assert "evidence" in capsys.readouterr().err


def test_noul_criteria_must_differ():
    with pytest.raises(questions.InvalidQuestion):
        questions.build_noul(None, "Q?", "same", "Same ")
    with pytest.raises(questions.InvalidQuestion):
        questions.build_noul(None, "Q?", "   ", "no")


def test_shim_payload_for_noul_choice_and_score_matches_the_legacy_script(capsys, tmp_path):
    import subprocess
    import sys

    legacy = "tests/fixtures/legacy_jev.py"
    for argv in (["noul", "Is it?", "-t", "yes", "-f", "no", "-s", "ev"], ["choice", "Q?", "-o", "a=x", "-o", "b=y", "-s", "ev"], ["score", "Rate", "-c", "bad", "-c", "good", "-s", "ev"]):
        want = json.loads(subprocess.run([sys.executable, legacy, "--dry-run", *argv], capture_output=True, text=True, check=True).stdout)
        assert compat.main(["--dry-run", *argv]) == 0
        got = json.loads(capsys.readouterr().out)
        assert got == want, argv


def test_verify_payload_differs_from_the_legacy_one_only_as_documented(capsys):
    """ADR-03: `verify` sends the registry question (claim and evidence as separate state fields, richer criteria) and, below the
    risk floor, an evidence_is_raw companion in the same request. The legacy script sent `context` and one question."""
    assert compat.main(["--dry-run", "verify", "claim", "-s", "ev"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert set(got["state"]) == {"claim", "evidence", "evidence_source"} and set(got["questions"]) == {"q", "evidence_is_raw"}


# ---- F44: `jade fast` is not a Jev call ---------------------------------------------------------------------------------------------
def test_fast_checks_write_no_jev_rows_and_never_make_the_stop_hook_block(monkeypatch, capsys):
    monkeypatch.setenv("JADE_SESSION_ID", "sess-fast")
    for _ in range(6):
        assert cli.main(["fast", "exit-code", "0", "0"]) == 0
    assert cli.main(["fast", "exit-code", "0", "2"]) == 4
    db = ledger.open()
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls").n == 0
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE kind='FAST_CHECK'").n == 7
    assert hooks.stop({"session_id": "sess-fast"}).exit_code == 0
    assert improve.patterns(db) == []
    capsys.readouterr()
    cli.main(["ledger", "stats", "--days", "7"])
    assert json.loads(capsys.readouterr().out)["calls"] == 0


def test_fast_rows_from_an_older_version_are_ignored_by_stats_stop_hook_and_miner(monkeypatch, capsys):
    monkeypatch.setenv("JADE_SESSION_ID", "sess-old")
    db = ledger.open()
    for i in range(7):
        cid = db.begin_call("fast.exit-code", "0" * 64, "sess-old", primitive="fast", task_family="fast")
        db.finish_call(cid, verdict="REJECT" if i == 6 else "ACCEPT", exit_code=4 if i == 6 else 0, p=0.0, latency_ms=0.0, cost_usd=0.0)
    assert hooks.stop({"session_id": "sess-old"}).exit_code == 0
    assert improve.patterns(db) == []
    cli.main(["ledger", "stats", "--days", "7"])
    assert json.loads(capsys.readouterr().out)["calls"] == 0


def test_ledger_stats_values_for_a_known_ledger(capsys):
    db = ledger.open()
    for i, (p, code) in enumerate([(0.9, 0), (0.5, 3), (0.1, 4), (0.95, 0)]):
        r = ask_once(noul(p), db, claim=f"claim {i}", session=f"s{i}")
        assert r.exit_code == code
    ask_once(noul(0.9), db, claim="claim 0", session="s0")                  # one cached repeat
    ask_once(adapter.FixtureProvider(lambda s, q, m: adapter.Result(ok=False, error_kind="UNAVAILABLE")), db, claim="down", session="s9")
    cli.main(["ledger", "stats", "--days", "7"])
    out = json.loads(capsys.readouterr().out)
    assert out["calls"] == 6 and out["sessions"] == 5
    assert out["exit_codes"] == {"accept_0": 3, "abstain_3": 1, "reject_4": 1, "no_answer_5_6": 1}
    assert out["repeat_share"] == round(1 / 6, 3) and out["error_share"] == round(1 / 6, 3)
    assert out["verify"] == {"n": 4, "p_mean": round((0.9 + 0.5 + 0.1 + 0.95) / 4, 3), "share_below_0.70": 0.5}


# ---- F25 / F45: promotion consumes what the system computed -----------------------------------------------------------------------
def test_promotion_pins_match_the_policy_file():
    pol = ops.promotion_policy()
    assert tuple(pol["promote"]["requires"]) == improve.REQUIRED_RESULTS
    assert tuple(pol["promote"]["auto_allowed_for"]) == improve.AUTO_PROMOTABLE
    assert pol["canary"]["traffic_fraction"] == ops.CANARY_FRACTION == ops.canary_fraction()
    assert pol["pipeline"] == ["offline_replay", "shadow", "canary", "promote"]


def test_a_caller_cannot_attach_a_result_or_its_evidence(tmp_path):
    db = ledger.open(tmp_path)
    c = improve.propose("calibration_threshold", "x", "y" * 10, path=db)
    for res in ({"audit_trail_complete": True}, {"offline_replay_pass": True}, {"protected": {"n": 99}}, {"champion": {}}, {"shadow": {"n": 500}}):
        with pytest.raises(improve.PromotionBlocked, match="computed by the evaluator"):
            improve.attach_results(db, c.candidate_id, res)
    improve.attach_results(db, c.candidate_id, {"note": "free text is fine"})


def test_the_pipeline_promotes_a_threshold_that_earned_it_and_only_once(tmp_path, human_env):
    db = ledger.open(tmp_path)
    seed(db)
    cid = threshold_candidate(db)
    assert improve.promote(db, cid, stdin_isatty=True)["promotion_decision"] == "promoted"
    with pytest.raises(improve.PromotionBlocked, match="already promoted"):
        improve.promote(db, cid, stdin_isatty=True)
    improve.rollback(db, cid, stdin_isatty=True)
    with pytest.raises(improve.PromotionBlocked, match="already rolled_back"):
        improve.promote(db, cid, stdin_isatty=True)    # a rolled-back candidate is not promoted again


def test_a_threshold_whose_replay_fails_is_blocked_whatever_else_is_true(tmp_path, human_env):
    db = ledger.open(tmp_path)
    seed(db)
    g = group_of(db)
    c = improve.propose("calibration_threshold", f"{g}: accept 0.70 -> 0.50", "y" * 10, path=db)
    res = improve.replay_threshold(db, c.candidate_id, g, 0.50)["results"]
    improve.protected_check(db, c.candidate_id, stdin_isatty=True)
    assert res["offline_replay_pass"] is False   # looser: accepts the false mid claims too, upper bound above the champion's rate? or coverage: see verdict
    with pytest.raises(improve.PromotionBlocked, match="offline_replay_pass"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)


def test_too_few_labels_cannot_pass_the_replay(tmp_path, human_env):
    db = ledger.open(tmp_path)
    seed(db, hi=20, lo=20, mid=2, hold=10, val=4)
    g = group_of(db)
    c = improve.propose("calibration_threshold", "x", "y" * 10, path=db)
    improve.replay_threshold(db, c.candidate_id, g, 0.85)
    improve.protected_check(db, c.candidate_id, stdin_isatty=True)
    with pytest.raises(improve.PromotionBlocked, match="fewer than 200"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)


def test_an_invalid_band_is_refused(tmp_path):
    db = ledger.open(tmp_path)
    c = improve.propose("calibration_threshold", "x", "y" * 10, path=db)
    for accept, reject in ((1.5, None), (0.5, 0.6), (-1, None), (float("nan"), None), (True, None)):
        with pytest.raises(ValueError):
            improve.replay_threshold(db, c.candidate_id, "g", accept, reject)


def test_the_protected_split_is_evaluated_by_a_human_only(tmp_path, agent_env):
    db = ledger.open(tmp_path)
    c = improve.propose("calibration_threshold", "x", "y" * 10, path=db)
    with pytest.raises(evals.ProtectedSetAccessDenied):
        improve.protected_check(db, c.candidate_id, stdin_isatty=True)   # an agent environment is never a human terminal


def test_holm_and_the_subgroup_test():
    assert improve.holm_rejected({"a": 0.001, "b": 0.02, "c": 0.5}, 0.05) == {"a", "b"}
    assert improve.holm_rejected({"a": 0.03, "b": 0.04}, 0.05) == set()          # 0.03 > 0.05 / 2 stops the step-down
    assert improve._binom_tail(5, 5) == pytest.approx(1 / 32)


def test_a_template_is_promoted_only_after_shadow_protected_and_canary(tmp_path, human_env, monkeypatch):
    registry_v2(tmp_path, monkeypatch)
    home = tmp_path / "home"
    db = ledger.open(home)
    seed(db)
    ops.pin_champion(home, Q, 1)
    c = improve.propose("question_template", "stricter clause", "y" * 10, path=db)
    with pytest.raises(improve.PromotionBlocked, match="shadow"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)
    improve.shadow(db, c.candidate_id, Q, 2, provider=provider(), limit=100)
    with pytest.raises(improve.PromotionBlocked, match="less than 7 days"):
        improve.start_canary(db, c.candidate_id, stdin_isatty=True)       # shadow has not run for the policy's duration
    backdate_shadow(db, c.candidate_id)
    with pytest.raises(improve.PromotionBlocked, match="canary"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)            # no canary yet
    improve.protected_check(db, c.candidate_id, provider=provider(), stdin_isatty=True)
    improve.start_canary(db, c.candidate_id, stdin_isatty=True)
    assert ops.pins_of(home, Q)["challenger"] == 2 and ops.pins_of(home, Q)["challenger_fraction"] == ops.canary_fraction()
    assert improve.promote(db, c.candidate_id, stdin_isatty=True)["promotion_decision"] == "promoted"
    assert ops.active_version(home, Q) == 2


def test_a_challenger_that_accepts_what_the_champion_rejected_is_blocked(tmp_path, human_env, monkeypatch):
    registry_v2(tmp_path, monkeypatch)
    home = tmp_path / "home"
    db = ledger.open(home)
    seed(db)
    c = improve.propose("question_template", "looser clause", "y" * 10, path=db)
    improve.shadow(db, c.candidate_id, Q, 2, provider=provider(shift=0.9), limit=100)   # lo answers (P=0.05) become 0.95: reject -> accept
    improve.protected_check(db, c.candidate_id, provider=provider(shift=0.9), stdin_isatty=True)
    backdate_shadow(db, c.candidate_id)
    improve.start_canary(db, c.candidate_id, stdin_isatty=True)
    with pytest.raises(improve.PromotionBlocked, match="no_safety_invariant_violation"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)


def test_set_challenger_cannot_exceed_the_canary_fraction(tmp_path):
    with pytest.raises(ValueError):
        ops.set_challenger(tmp_path, Q, 2, fraction=1.0)
    ops.set_challenger(tmp_path, Q, 2)
    assert ops.pins_of(tmp_path, Q)["challenger_fraction"] == ops.canary_fraction()


def test_a_canary_needs_a_human_or_the_standing_opt_in(tmp_path, agent_env, monkeypatch):
    registry_v2(tmp_path, monkeypatch)
    home = tmp_path / "home"
    db = ledger.open(home)
    seed(db)
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    improve.shadow(db, c.candidate_id, Q, 2, provider=provider(), limit=100)
    backdate_shadow(db, c.candidate_id)
    with pytest.raises(improve.PromotionBlocked, match="opt-in"):
        improve.start_canary(db, c.candidate_id)


def test_shadow_work_never_slows_ask_run(tmp_path, monkeypatch):
    """The improvement plane is not on the hot path: with every evaluator stubbed to sleep, `ask.run` is as fast as without."""
    for fn in ("shadow", "replay_threshold", "protected_check", "evaluate", "start_canary"):
        monkeypatch.setattr(improve, fn, lambda *a, **k: time.sleep(2.0))
    db = ledger.open(tmp_path)
    t0 = time.perf_counter()
    r = ask_once(noul(0.9), db)
    assert r.exit_code == 0 and time.perf_counter() - t0 < 1.0


# ---- F26 / F47: rollback --------------------------------------------------------------------------------------------------------------
def test_rollback_of_a_promoted_template_restores_the_exact_prior_champion(tmp_path, human_env, monkeypatch):
    registry_v2(tmp_path, monkeypatch)
    home = tmp_path / "home"
    db = ledger.open(home)
    seed(db)
    ops.pin_champion(home, Q, 1)
    cid = template_candidate(db, tmp_path)
    improve.promote(db, cid, stdin_isatty=True)
    assert ops.active_version(home, Q) == 2 and ops.pins_of(home, Q)["previous_champion"] == 1
    doc = improve.rollback(db, cid, stdin_isatty=True)
    assert ops.active_version(home, Q) == 1 and "previous_champion" not in ops.pins_of(home, Q) and doc["promotion_decision"] == "rolled_back"


def test_rollback_to_the_registry_default_when_there_was_no_pin(tmp_path, human_env, monkeypatch):
    registry_v2(tmp_path, monkeypatch)
    home = tmp_path / "home"
    db = ledger.open(home)
    seed(db)
    cid = template_candidate(db, tmp_path)
    improve.promote(db, cid, stdin_isatty=True)
    assert ops.active_version(home, Q) == 2
    improve.rollback(db, cid, stdin_isatty=True)
    assert ops.active_version(home, Q) is None      # None is the registry default


def test_rollback_fails_loudly_when_the_pins_no_longer_match_the_promotion(tmp_path, human_env, monkeypatch):
    registry_v2(tmp_path, monkeypatch)
    home = tmp_path / "home"
    db = ledger.open(home)
    seed(db)
    cid = template_candidate(db, tmp_path)
    improve.promote(db, cid, stdin_isatty=True)
    ops.pin_champion(home, Q, 3)       # something else moved the champion
    with pytest.raises(improve.PromotionBlocked, match="no longer match"):
        improve.rollback(db, cid, stdin_isatty=True)
    assert improve.get(db, cid)["promotion_decision"] == "promoted"


def test_ops_rollback_clears_a_canary_challenger_first(tmp_path):
    ops.pin_champion(tmp_path, Q, 1)
    ops.set_challenger(tmp_path, Q, 2)
    assert ops.rollback(tmp_path, Q) == "challenger_cleared" and ops.active_version(tmp_path, Q) == 1
    assert ops.rollback(tmp_path, Q) == "nothing"


def _promoted_threshold(tmp_path, accept=0.85):
    db = ledger.open(tmp_path / "home")
    seed(db)
    cid = threshold_candidate(db, accept)
    improve.promote(db, cid, stdin_isatty=True)
    return db, cid, group_of(db)


def test_rollback_under_freeze_from_an_agent_is_refused(tmp_path, human_env, monkeypatch):
    db, cid, g = _promoted_threshold(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")
    (tmp_path / "home" / "FREEZE").write_text("")
    with pytest.raises(improve.PromotionBlocked, match="FREEZE"):
        improve.rollback(db, cid)
    assert json.loads((tmp_path / "home" / "calibration" / "current.json").read_text())[g]["accept"] == 0.85
    monkeypatch.delenv("CLAUDECODE")
    assert improve.rollback(db, cid, stdin_isatty=True)["promotion_decision"] == "rolled_back"   # a human may recover under FREEZE


def test_an_agent_cannot_loosen_a_promoted_stricter_threshold_and_never_signs_as_human(tmp_path, human_env, monkeypatch):
    db, cid, g = _promoted_threshold(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(improve.PromotionBlocked, match="loosen"):
        improve.rollback(db, cid, actor="human:terminal")
    assert json.loads((tmp_path / "home" / "calibration" / "current.json").read_text())[g]["accept"] == 0.85
    monkeypatch.delenv("CLAUDECODE")
    doc = improve.rollback(db, cid, stdin_isatty=True)
    assert doc["audit_trail"][-1]["actor"] == "human:terminal" and g not in json.loads((tmp_path / "home" / "calibration" / "current.json").read_text())


def test_the_audit_trail_records_the_real_actor(tmp_path, human_env, monkeypatch):
    db = ledger.open(tmp_path / "home")
    seed(db)
    g = group_of(db)
    c = improve.propose("calibration_threshold", "x", "y" * 10, path=db)   # never promoted: rolling it back changes no live value
    monkeypatch.setenv("CLAUDECODE", "1")
    doc = improve.rollback(db, c.candidate_id, actor="human:terminal")
    assert doc["audit_trail"][-1]["actor"] == "runtime:agent"
    c2 = improve.propose("calibration_threshold", "x", "y" * 10, path=db)
    monkeypatch.delenv("CLAUDECODE")
    assert improve.rollback(db, c2.candidate_id, stdin_isatty=True)["audit_trail"][-1]["actor"] == "human:terminal"
    assert g


def test_rollback_restores_the_prior_promoted_value_not_the_default(tmp_path, human_env):
    home = tmp_path / "home"
    db = ledger.open(home)
    seed(db)
    g = group_of(db)
    (home / "calibration").mkdir()
    (home / "calibration" / "current.json").write_text(json.dumps({g: {"accept": 0.60, "reject": 0.2, "candidate": "imp_earlier"}}))   # an earlier promotion
    cid = threshold_candidate(db, 0.85)
    improve.promote(db, cid, stdin_isatty=True)
    cur = lambda: json.loads((home / "calibration" / "current.json").read_text())[g]  # noqa: E731
    assert cur()["accept"] == 0.85
    improve.rollback(db, cid, stdin_isatty=True)
    assert cur() == {"accept": 0.60, "reject": 0.2, "candidate": "imp_earlier"}     # the earlier promotion, not the 0.70 default


# ---- the CLI surface of the pipeline ----------------------------------------------------------------------------------------------
def test_cli_improve_protected_and_rollback_refuse_an_agent(tmp_path, agent_env, capsys):
    db = ledger.open()
    c = improve.propose("calibration_threshold", "x", "y" * 10, path=db)
    assert cli.main(["improve", "protected", c.candidate_id]) == 1
    assert "human terminal" in capsys.readouterr().err
    (paths.home() / "FREEZE").write_text("")
    assert cli.main(["improve", "rollback", c.candidate_id]) == 1
    assert "FREEZE" in capsys.readouterr().err
    assert improve.get(db, c.candidate_id)["promotion_decision"] == "pending"


def test_cli_improve_canary_needs_the_shadow_stage(tmp_path, capsys):
    db = ledger.open()
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    assert cli.main(["improve", "canary", c.candidate_id]) == 1
    assert "blocked" in capsys.readouterr().err
