"""V6-24: decision hygiene (Tier A of docs/14-v6/06). Every change here only turns a "yes" into "not yes", records more, or reuses a stored answer for the
same claim. DI-3a (whitespace repeats), DI-4 (the stricter risk), DI-5 (the revision cap), `--no-cache` (journalled, capped, human and test only, H16) and
H17 (only `jade ask` writes a `jev_result_*` event). All tests use a fixture provider and the temporary ledger of tests/conftest.py."""
import pytest

from jade import adapter, ask, cli, decide, ledger, state_machine, util

PUSH = "git push origin v1-feat-x"   # a plain high-risk call: its decision is rated high


def provider(p=0.9, companion=None):
    """A provider that answers every question with P(yes)=p; `companion` (or the same p) answers evidence_is_raw. A list gives one p per call."""
    seq = list(p) if isinstance(p, list | tuple) else None

    def responder(state, questions, model):
        value = seq.pop(0) if seq else p
        return {k: {"type": "noul", "noul": (companion if (k == "evidence_is_raw" and companion is not None) else value)} for k in questions}
    return adapter.FixtureProvider(responder)


def ask_(prov, claim="tests pass", evidence="42 passed", *, decision=None, risk="low", use_cache=True, companion=False, session="sess-hygiene1"):
    return ask.run(ask.verify_specs(companion=companion), {"claim": claim, "evidence": evidence}, provider=prov, session=session, decision_id=decision,
                   risk=risk, use_cache=use_cache)


def reasons(db):
    return [r.guard for r in db.all("SELECT guard FROM events WHERE kind='ask_repeat' ORDER BY id")]


# ---- DI-4: the stricter of --risk and the decision's risk -------------------------------------------------------------
def test_di4_a_linked_high_decision_beats_risk_low():
    db = ledger.open()
    d = decide.open_decision(db, "push the feature branch", command=PUSH)
    assert d["risk"]["level"] == "high"
    prov = provider(0.75)
    assert ask_(prov, session="sess-a").exit_code == 0                                      # P=0.75 is an accept at low
    linked = ask_(prov, claim="tests pass on the branch", decision=d["decision_id"], risk="low", session="sess-b")
    assert linked.exit_code == 3                                                            # the same answer at high (accept 0.85) is an abstain
    assert db.get_call(linked.primary.call_id).risk == "high"                               # the call row says which band judged it
    assert any("high" in w for w in linked.warnings)                                        # and the agent is told why it was not --risk low


def test_di4_a_linked_medium_decision_makes_the_evidence_companion_count():
    db = ledger.open()
    d = decide.open_decision(db, "read the report and say it is done", raise_risk_to="medium")
    prov = provider(0.9, companion=0.05)                                                    # a clear "this is only a summary"
    assert ask_(prov, companion=True, session="sess-a").exit_code == 0                      # at low the companion is ignored
    r = ask_(prov, claim="the report is done", companion=True, decision=d["decision_id"], risk="low", session="sess-b")
    assert r.exit_code == 3 and "evidence_is_raw" in r.note


def test_di4_a_lower_decision_risk_never_lowers_the_risk_the_caller_asked_for():
    db = ledger.open()
    d = decide.open_decision(db, "say hello", raise_risk_to="low")
    r = ask_(provider(0.75), decision=d["decision_id"], risk="high")
    assert r.exit_code == 3 and db.get_call(r.primary.call_id).risk == "high"


def test_di4_an_unknown_decision_leaves_the_risk_as_given():
    r = ask_(provider(0.75), decision="dec_nope", risk="low")
    assert r.exit_code == 0 and any("not advanced" in w for w in r.warnings)


# ---- DI-3a: whitespace repeats reuse the stored verdict; a reworded claim is a new call ------------------------------
def test_di3a_a_reworded_claim_stays_a_new_provider_call():
    prov = provider(0.9)
    ask_(prov, claim="tests pass")
    ask_(prov, claim="all tests pass")
    assert len(prov.calls) == 2


@pytest.mark.parametrize("first,again", [
    (("tests pass", "42 passed"), ("tests pass\n", "42 passed")),
    (("tests pass", "42 passed"), ("tests pass  ", "42 passed")),
    (("tests pass", "42 passed"), ("tests pass", "42 passed\n\n")),
    (("tests pass", "42 passed"), ("tests pass", "42 passed   \n")),
    (("tests pass", "line one\nline two"), ("tests pass", "line one  \r\nline two\r\n")),
    (("tests pass", "42 passed"), ("tests pass", "\x1b[32m42 passed\x1b[0m"))])
def test_di3a_a_whitespace_only_repeat_returns_the_stored_verdict_with_no_provider_call(first, again):
    prov = provider(0.9)
    one = ask_(prov, claim=first[0], evidence=first[1])
    two = ask_(prov, claim=again[0], evidence=again[1])
    assert len(prov.calls) == 1 and two.primary.cached and two.exit_code == one.exit_code == 0
    assert reasons(ledger.open()) == ["whitespace"]


def test_di3a_an_identical_repeat_is_journalled_as_exact():
    prov = provider(0.9)
    ask_(prov)
    assert ask_(prov).primary.cached
    assert reasons(ledger.open()) == ["exact"]


def test_di3a_a_whitespace_repeat_is_never_looser_than_the_first_answer():
    prov = provider([0.5, 0.9])
    assert ask_(prov, evidence="42 passed").exit_code == 3
    again = ask_(prov, evidence="42 passed  \n")
    assert again.exit_code == 3 and len(prov.calls) == 1


def test_di3a_only_whitespace_is_normalised_never_case_or_digits():
    prov = provider(0.9)
    ask_(prov, claim="42 tests pass")
    ask_(prov, claim="42 Tests pass")
    ask_(prov, claim="43 tests pass")
    assert len(prov.calls) == 3


def test_di3a_the_text_that_is_sent_is_what_the_agent_wrote():
    prov = provider(0.9)
    ask_(prov, evidence="42 passed  \n")
    assert prov.calls[0]["state"]["evidence"] == "42 passed  \n"


def test_normalise_text_rules():
    n = util.normalise_text
    assert n("a  \nb\t\n\n") == "a\nb"
    assert n("a\r\nb") == "a\nb"
    assert n("\x1b[1;31mred\x1b[0m") == "red"
    assert n("a\rb") == "a\rb"                  # a lone CR is not a line ending: nothing but whitespace is touched
    assert n("  lead") == "  lead"              # leading space can carry meaning (indented output)
    assert n("Ab") == "Ab" and n("1") == "1"


# ---- --no-cache: journalled, capped, human and test only (H16) --------------------------------------------------------
def run_cli(argv, prov, monkeypatch, human=False):
    monkeypatch.setattr(cli, "is_human_tty", lambda *a, **k: human)
    monkeypatch.setenv("JADE_SESSION_ID", "sess-hygiene1")
    return cli.ask_main(argv, provider=prov)


def test_h16_an_agent_cannot_resample_with_no_cache(monkeypatch, capsys):
    prov = provider([0.5, 0.9])
    argv = ["verify", "tests pass", "-s", "42 passed", "--no-cache"]
    assert run_cli(argv, prov, monkeypatch) == 3
    assert run_cli(argv, prov, monkeypatch) == 3                  # the stored answer comes back: the flag did nothing
    assert len(prov.calls) == 1
    assert "--no-cache" in capsys.readouterr().err                # and the agent is told so
    assert reasons(ledger.open()) == ["exact"]


def test_h16_a_human_may_resample_but_the_answer_is_never_looser(monkeypatch):
    prov = provider([0.5, 0.9])
    argv = ["verify", "tests pass", "-s", "42 passed", "--no-cache"]
    assert run_cli(argv, prov, monkeypatch, human=True) == 3
    assert run_cli(argv, prov, monkeypatch, human=True) == 3      # the provider said 0.9; the first non-yes stands
    assert len(prov.calls) == 2
    db = ledger.open()
    assert reasons(db) == ["no_cache"]
    second = db.one("SELECT p, verdict, exit_code FROM jev_calls WHERE dedup_of IS NOT NULL")
    assert second.exit_code == 3 and second.verdict == "ABSTAIN"


def test_h16_a_test_run_may_resample(monkeypatch):
    monkeypatch.setenv("JADE_SOURCE", "test")
    prov = provider(0.9)
    argv = ["verify", "tests pass", "-s", "42 passed", "--no-cache"]
    assert run_cli(argv, prov, monkeypatch) == 0 and run_cli(argv, prov, monkeypatch) == 0
    assert len(prov.calls) == 2


def test_h16_the_test_tag_does_not_hide_the_first_answer_from_the_never_looser_rule(monkeypatch):
    """Review finding: an anchor is per source, so `JADE_SOURCE=test` found none, took a fresh sample and said yes after a live abstain."""
    prov = provider([0.5, 0.9])
    argv = ["verify", "tests pass", "-s", "42 passed"]
    assert run_cli(argv, prov, monkeypatch) == 3                  # live: abstain
    monkeypatch.setenv("JADE_SOURCE", "test")
    assert run_cli(argv, prov, monkeypatch) == 3                  # test tag, new provider answer 0.9: the first non-yes stands
    assert len(prov.calls) == 2                                   # the sample is still drawn, it is only held to the first answer
    monkeypatch.delenv("JADE_SOURCE")
    assert run_cli(argv, prov, monkeypatch) == 3                  # back in live mode: the stored live answer


def test_h16_a_live_ask_after_a_test_abstain_is_held_to_it_too(monkeypatch):
    prov = provider([0.5, 0.9])
    argv = ["verify", "tests pass", "-s", "42 passed"]
    monkeypatch.setenv("JADE_SOURCE", "test")
    assert run_cli(argv, prov, monkeypatch) == 3
    monkeypatch.delenv("JADE_SOURCE")
    assert run_cli(argv, prov, monkeypatch) == 3                  # a fresh live sample cannot turn the first abstain into a yes
    assert len(prov.calls) == 2


def test_h16_the_test_tag_does_not_give_a_second_cap(monkeypatch, capsys):
    """Review finding: live calls and test calls each had their own cap, so the tag bought 3 more provider answers."""
    prov = provider([0.5, 0.9, 0.9, 0.9, 0.9, 0.9])
    argv = ["verify", "tests pass", "-s", "42 passed", "--no-cache"]
    assert run_cli(argv, prov, monkeypatch) == 3                  # live (agent: the flag is ignored), the first answer
    monkeypatch.setenv("JADE_SOURCE", "test")
    for _ in range(ask.NO_CACHE_SAMPLES + 2):
        assert run_cli(argv, prov, monkeypatch) == 3
    assert len(prov.calls) == ask.NO_CACHE_SAMPLES                # the live answer counts: first call plus the resamples the cap lets through
    assert "cap" in capsys.readouterr().err


def test_h16_no_cache_is_capped_and_after_the_cap_there_is_no_provider_call(monkeypatch, capsys):
    prov = provider(0.9)
    argv = ["verify", "tests pass", "-s", "42 passed", "--no-cache"]
    for _ in range(ask.NO_CACHE_SAMPLES + 3):
        assert run_cli(argv, prov, monkeypatch, human=True) == 0
    assert len(prov.calls) == ask.NO_CACHE_SAMPLES                # the first call plus the resamples the cap lets through
    assert "cap" in capsys.readouterr().err
    assert reasons(ledger.open()).count("cap_hit") == 3


def test_h16_the_cap_is_per_claim_and_evidence(monkeypatch):
    prov = provider(0.9)
    for ev in ("a passed", "b passed"):
        for _ in range(ask.NO_CACHE_SAMPLES):
            run_cli(["verify", "tests pass", "-s", ev, "--no-cache"], prov, monkeypatch, human=True)
    assert len(prov.calls) == 2 * ask.NO_CACHE_SAMPLES


def test_the_human_proxy_path_keeps_its_fresh_sample_without_the_cap():
    prov = provider(0.9)
    spec = ask.registry_spec("q.human_proxy.would_allow")
    for _ in range(ask.NO_CACHE_SAMPLES + 2):
        ask.run([spec], {"command": "git push origin x", "rule": "R-1"}, provider=prov, session="sess-proxy", use_cache=False)
    assert len(prov.calls) == ask.NO_CACHE_SAMPLES + 2


# ---- DI-5: the revision cap stops the provider call; a cached re-ask uses no revision --------------------------------
def test_di5_a_cached_re_ask_uses_no_revision():
    db = ledger.open()
    d = decide.open_decision(db, "check the thing", command="echo hi")
    did = d["decision_id"]
    prov = provider(0.5)
    for _ in range(6):
        r = ask_(prov, decision=did)
        assert r.exit_code == 3
    assert len(prov.calls) == 1
    doc = db.one("SELECT status, revision_count FROM decisions WHERE decision_id=?", (did,))
    assert (doc.status, doc.revision_count) == ("ABSTAINED", 0)
    assert reasons(db) == ["exact"] * 5


def test_di5_at_the_cap_there_is_no_provider_call_and_the_decision_stays():
    db = ledger.open()
    d = decide.open_decision(db, "check the thing", command="echo hi")
    did = d["decision_id"]
    prov = provider(0.5)
    for i in range(1 + state_machine.MAX_REVISIONS):          # one call, then three revisions, each with new evidence
        assert ask_(prov, evidence=f"run {i}: 42 passed", decision=did).exit_code == 3
    assert len(prov.calls) == 1 + state_machine.MAX_REVISIONS
    row = db.one("SELECT status, revision_count FROM decisions WHERE decision_id=?", (did,))
    assert (row.status, row.revision_count) == ("ABSTAINED", state_machine.MAX_REVISIONS)
    for i in range(3):
        r = ask_(prov, evidence=f"later run {i}: 42 passed", decision=did)
        assert r.exit_code == 3 and "escalate" in r.note.lower() and "unverified" in r.note
        assert r.primary.call_id is None
    assert len(prov.calls) == 1 + state_machine.MAX_REVISIONS         # zero further provider calls
    assert db.one("SELECT status FROM decisions WHERE decision_id=?", (did,)).status == "ABSTAINED"
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls").n == 1 + state_machine.MAX_REVISIONS   # and no row: nothing was asked
    assert reasons(db).count("cap_hit") == 3


def test_di5_the_cap_does_not_touch_a_decision_below_it_or_another_state():
    db = ledger.open()
    d = decide.open_decision(db, "check the thing", command="echo hi")
    prov = provider(0.9)
    assert ask_(prov, decision=d["decision_id"]).exit_code == 0
    assert db.one("SELECT status FROM decisions WHERE decision_id=?", (d["decision_id"],)).status == "ACCEPTED"


def test_di5_a_cached_repeat_from_another_decision_still_moves_the_decision():
    db = ledger.open()
    a = decide.open_decision(db, "first", command="echo hi")
    b = decide.open_decision(db, "second", command="echo hi")
    prov = provider(0.9)
    ask_(prov, decision=a["decision_id"])
    r = ask_(prov, decision=b["decision_id"])
    assert r.primary.cached and db.one("SELECT status FROM decisions WHERE decision_id=?", (b["decision_id"],)).status == "ACCEPTED"


# ---- H17: only `jade ask` writes a jev_result_* event --------------------------------------------------------------------
def test_h17_an_agent_cannot_send_a_jev_result_through_the_cli(monkeypatch, capsys):
    monkeypatch.setenv("JADE_SESSION_ID", "sess-hygiene1")
    db = ledger.open()
    d = decide.open_decision(db, "check the thing", command="echo hi", session="sess-hygiene1")
    assert cli.main(["decide", "event", d["decision_id"], "route"]) == 0
    for ev in ("jev_result_accept", "jev_result_abstain", "jev_result_reject"):
        assert cli.main(["decide", "event", d["decision_id"], ev]) == 1
        assert not state_machine.agent_may(ev, "EVALUATING", "low")
    assert "jade ask" in capsys.readouterr().err
    assert db.one("SELECT status FROM decisions WHERE decision_id=?", (d["decision_id"],)).status == "EVALUATING"


def test_h17_an_agent_actor_without_a_matching_call_raises_illegal_transition():
    db = ledger.open()
    d = decide.open_decision(db, "check the thing", command="echo hi")
    decide.step(db, d["decision_id"], "route")
    with pytest.raises(state_machine.IllegalTransition, match="matching"):
        decide.step(db, d["decision_id"], "jev_result_accept", actor="agent")
    with pytest.raises(state_machine.IllegalTransition, match="matching"):
        decide.step(db, d["decision_id"], "jev_result_accept", actor="agent", call_id="call_nope")
    assert db.one("SELECT status FROM decisions WHERE decision_id=?", (d["decision_id"],)).status == "EVALUATING"


def test_h17_a_call_that_does_not_say_yes_cannot_carry_an_accept():
    db = ledger.open()
    d = decide.open_decision(db, "check the thing", command="echo hi")
    r = ask_(provider(0.5), decision=d["decision_id"])          # an abstain, linked: the decision is ABSTAINED
    other = decide.open_decision(db, "another", command="echo hi")
    decide.step(db, other["decision_id"], "route")
    with pytest.raises(state_machine.IllegalTransition, match="matching"):
        decide.step(db, other["decision_id"], "jev_result_accept", call_id=r.primary.call_id)   # an abstain call, and another decision's call
    decide.step(db, d["decision_id"], "add_evidence")
    with pytest.raises(state_machine.IllegalTransition, match="matching"):
        decide.step(db, d["decision_id"], "jev_result_accept", call_id=r.primary.call_id)       # the call is an abstain: no accept


def test_h17_the_ask_path_still_walks_a_decision_to_accepted():
    db = ledger.open()
    d = decide.open_decision(db, "check the thing", command="echo hi")
    r = ask_(provider(0.9), decision=d["decision_id"])
    assert r.exit_code == 0
    assert decide.get(db, d["decision_id"])["status"] == "ACCEPTED"
    assert decide.get(db, d["decision_id"])["evaluation_refs"] == [r.primary.call_id]


def test_h17_a_companion_that_lowers_an_accept_records_an_abstain_on_an_accepted_call():
    db = ledger.open()
    d = decide.open_decision(db, "read the report", raise_risk_to="medium")
    r = ask_(provider(0.9, companion=0.05), companion=True, decision=d["decision_id"])
    assert r.exit_code == 3
    assert decide.get(db, d["decision_id"])["status"] == "ABSTAINED"     # a stricter event than the call's own verdict is allowed


def test_the_state_machine_marks_exactly_the_jev_result_events_as_ask_only():
    assert state_machine.JEV_RESULT_EVENTS == {"jev_result_accept", "jev_result_abstain", "jev_result_reject"}
    for ev in state_machine.JEV_RESULT_EVENTS:
        assert not state_machine.agent_may(ev, "EVALUATING", "low")
    assert state_machine.agent_may("jev_error", "EVALUATING", "low")
    assert state_machine.agent_may("add_evidence", "ABSTAINED", "low")
