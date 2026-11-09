"""W-53: every key `policies/routing.yaml` declares is read by code that acts on it, or is listed here as prose the code does not read.

The limits (`max_questions_per_request`, `max_parallel_requests`), the routes the preflight returns, PF-04 (a repeat) and PF-07 (stale context),
the dedup window and the question quality gates are read at run time. The registry is checked against the declared limits by `jade question lint`.
"""

from __future__ import annotations

import json
import shutil
from datetime import timedelta

import pytest
import yaml

from jade import adapter, ask, cli, ledger, ops, paths, policy, questions, route
from jade.util import new_id, now, sha256, ts

REAL = paths.shipped_policy_dir()
REGISTRY = paths.registry_dir()

# Keys the code does not read, with the reason. A key that is neither read nor listed here fails the truth test below; a listed key that
# the file no longer declares fails it too, so this list cannot go stale.
PROSE_ONLY = {
    "preflight[].description": "one-line explanation of the check, for the reader",
    "preflight[].check": "name of the check, for the reader; the code keys on the PF id",
    "routes.FAST.description": "prose",
    "routes.FAST.requires": "the route's contract, as prose",
    "routes.JEV.description": "prose",
    "routes.JEV.requires": "the route's contract, as prose",
    "routes.DEEP.description": "prose",
    "routes.ESCALATE.description": "prose",
    "dedup.key": "how the ledger keys a repeat, as prose (ledger.begin_call is the implementation)",
    "dedup.scope": "session, as prose (ledger.begin_call is the implementation)",
    "dedup.repeat_without_new_evidence": "what a repeat returns, as prose (ask._run is the implementation)",
}


# ---- helpers ----------------------------------------------------------------------------------------------------
def policy_dir(tmp_path, monkeypatch, edit=None):
    d = tmp_path / "policies"
    shutil.copytree(REAL, d, ignore=shutil.ignore_patterns("__pycache__", "local"), dirs_exist_ok=True)
    if edit:
        doc = yaml.safe_load((d / "routing.yaml").read_text())
        edit(doc)
        (d / "routing.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    monkeypatch.setenv("JADE_POLICY_DIR", str(d))
    policy.reset_cache()
    return d


def registry_dir(tmp_path, monkeypatch):
    d = tmp_path / "registry"
    shutil.copytree(REGISTRY, d)
    monkeypatch.setenv("JADE_REGISTRY_DIR", str(d))
    questions.reset_cache()
    return d


def fake(prob=0.9):
    return adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": prob} for k in q})


def adhoc_specs(n):
    out = []
    for i in range(n):
        s = ask.adhoc_spec("noul", f"Does `claim` hold for case {i}?", true="yes", false="no")
        s.key = f"q{i}"
        out.append(s)
    return out


def in_flight(db, n, *, age_s=0):
    """n separate batches (distinct session and state) left EVALUATING, as n concurrent `jade ask` processes would."""
    for _ in range(n):
        tag = new_id("x")
        cid = db.begin_call("q.verify_claim.supported_by_evidence", sha256(tag), f"other-{tag}", primitive="noul", task_family="verify_claim")
        if age_s:
            db.con.execute("UPDATE jev_calls SET ts=? WHERE call_id=?", (ts(now() - timedelta(seconds=age_s)), cid))
    db.con.commit()


# ---- the truth test: no declared key that nothing reads -----------------------------------------------------------
class Tracked:
    """Wraps the parsed routing policy and records the path of every leaf a consumer reads."""

    def __init__(self, data, path, seen):
        self._d, self._p, self._seen = data, path, seen

    def _wrap(self, v, p):
        if isinstance(v, (dict, list)):
            return Tracked(v, p, self._seen)
        self._seen.add(p)
        return v

    def __getitem__(self, k):
        if isinstance(self._d, list):
            return self._wrap(self._d[k], self._p)
        return self._wrap(self._d[k], f"{self._p}.{k}" if self._p else k)

    def get(self, k, default=None):
        return self[k] if k in self._d else default

    def __contains__(self, k):
        return k in self._d

    def __iter__(self):
        if isinstance(self._d, list):
            for v in self._d:
                yield self._wrap(v, self._p + "[]" if isinstance(v, dict) else self._p)
        else:
            yield from self._d

    def __len__(self):
        return len(self._d)

    def keys(self):
        return self._d.keys()

    def items(self):
        return ((k, self[k]) for k in self._d)

    def values(self):
        return (self[k] for k in self._d)


def leaves(doc, path=""):
    if isinstance(doc, dict):
        for k, v in doc.items():
            yield from leaves(v, f"{path}.{k}" if path else k)
    elif isinstance(doc, list) and doc and all(isinstance(x, dict) for x in doc):
        for x in doc:
            yield from leaves(x, path + "[]")
    else:
        yield path


def test_every_key_in_routing_yaml_is_read_by_code_or_declared_prose(monkeypatch):
    doc = yaml.safe_load((REAL / "routing.yaml").read_text())
    seen: set[str] = set()
    monkeypatch.setattr(policy, "routing", lambda: Tracked(doc, "", seen))

    # exercise every consumer: the preflight (each check), the batch limit, the in-flight limit, the dedup window, the lint gates, the policy check
    db = ledger.open()
    route.preflight(claim="x", deterministic=True)
    route.preflight(claim="x", command="terraform destroy -auto-approve")
    route.preflight(claim="done", evidence="")
    route.preflight(claim="x", evidence="raw " * 80, question_id="q.verify_claim.supported_by_evidence", state={"claim": "x", "evidence": "raw " * 80}, db=db)
    route.preflight(claim="x", options=["a=1"], evidence="raw")
    route.preflight(claim="design the architecture", evidence="raw " * 80)
    route.preflight(claim="x", evidence="raw " * 80, context_freshness={"observed_at": ts(), "ttl_s": 60})
    route.preflight(claim="x", evidence="raw " * 80, risk="low")
    route.preflight(claim="x", evidence="raw " * 80, about_to_claim_done=True)
    ask.run(ask.verify_specs(), {"claim": "tests pass", "evidence": "42 passed"}, provider=fake(), session="s")
    ask.run(ask.verify_specs(), {"claim": "tests pass", "evidence": "42 passed"}, provider=fake(), session="s")
    questions.lint(questions.get("q.verify_claim.supported_by_evidence"))
    route.policy_problems()
    db.close()

    unread = sorted(set(leaves(doc)) - seen - set(PROSE_ONLY))
    assert not unread, f"policies/routing.yaml declares {unread}, which no code reads: read it where the check runs, delete it, or list it in PROSE_ONLY with the reason"
    stale = sorted(k for k in PROSE_ONLY if k not in set(leaves(doc)))
    assert not stale, f"PROSE_ONLY names keys the file no longer declares: {stale}"
    read_but_prose = sorted(k for k in PROSE_ONLY if k in seen)
    assert not read_but_prose, f"{read_but_prose} are read by code now: take them off PROSE_ONLY"


def test_the_dead_cache_block_is_gone_and_no_document_describes_it():
    doc = yaml.safe_load((REAL / "routing.yaml").read_text())
    assert "cache" not in doc
    for rel in ("docs/05-threat-model.md", "docs/03-routing-and-question-design.md"):
        text = (REAL.parent / rel).read_text()
        assert "`cache:`" not in text and "never_cached" not in text, rel


def test_every_declared_preflight_check_and_gate_has_an_implementation():
    assert route.policy_problems() == []
    declared = {p["id"] for p in policy.routing()["preflight"]}
    assert declared == set(route.IMPLEMENTED) and len(declared) == 8
    assert set(policy.routing()["question_quality_gates"]) == set(questions.GATES)


def test_a_policy_that_names_a_check_or_gate_nothing_implements_is_reported(tmp_path, monkeypatch):
    def edit(doc):
        doc["preflight"].append({"id": "PF-09", "check": "x", "description": "x", "on_false": "FAST"})
        doc["question_quality_gates"].append("be_kind")
    policy_dir(tmp_path, monkeypatch, edit)
    problems = route.policy_problems()
    assert any("PF-09" in p for p in problems) and any("be_kind" in p for p in problems)
    rows = {n: (ok, d) for n, ok, d in ops.check(live=False)}
    assert rows["routing policy"][0] is False and "PF-09" in rows["routing policy"][1]


# ---- routes come from the file ------------------------------------------------------------------------------------
def test_the_route_a_check_returns_is_the_one_the_policy_declares(tmp_path, monkeypatch):
    assert route.preflight(claim="design the architecture of the ledger", evidence="raw " * 80).route == "DEEP"
    policy_dir(tmp_path, monkeypatch, lambda d: next(p for p in d["preflight"] if p["id"] == "PF-06").update(on_false="ESCALATE"))
    assert route.preflight(claim="design the architecture of the ledger", evidence="raw " * 80).route == "ESCALATE"
    policy_dir(tmp_path, monkeypatch, lambda d: d.update(default_route="DEEP"))
    assert route.preflight(claim="tests pass", evidence="42 passed\n" * 30, about_to_claim_done=True).route == "DEEP"


# ---- max_questions_per_request ------------------------------------------------------------------------------------
def test_a_batch_over_max_questions_per_request_is_refused_before_anything_is_sent():
    prov = fake()
    limit = route.limits().max_questions
    assert limit == 8
    assert ask.run(adhoc_specs(limit), {"claim": "c"}, provider=prov, session="s").exit_code == 0
    r = ask.run(adhoc_specs(limit + 1), {"claim": "c"}, provider=prov, session="s2")
    assert r.exit_code == 6 and "at most 8" in r.note and len(prov.calls) == 1
    assert ledger.open().one("SELECT COUNT(*) AS n FROM jev_calls WHERE session_id='s2'").n == 0


def test_the_batch_limit_is_read_from_the_policy_file(tmp_path, monkeypatch):
    policy_dir(tmp_path, monkeypatch, lambda d: d["routes"]["JEV"].update(max_questions_per_request=1))
    prov = fake()
    assert ask.run(adhoc_specs(2), {"claim": "c"}, provider=prov, session="s").exit_code == 6 and not prov.calls
    assert ask.run(adhoc_specs(1), {"claim": "c"}, provider=prov, session="s").exit_code == 0


def test_a_dry_run_refuses_an_over_limit_batch_too(tmp_path, monkeypatch, capsys):
    policy_dir(tmp_path, monkeypatch, lambda d: d["routes"]["JEV"].update(max_questions_per_request=1))
    assert cli.ask_main(["--dry-run", "verify", "tests pass", "-s", "42 passed"]) == 6     # the claim plus its evidence companion are two questions
    assert "at most 1" in capsys.readouterr().err


def test_an_unreadable_or_malformed_routing_policy_fails_closed(tmp_path, monkeypatch):
    d = policy_dir(tmp_path, monkeypatch)
    (d / "routing.yaml").write_text("schema_version: 1.0.0\nroutes: {JEV: {max_questions_per_request: 0}}\npreflight: []\n")
    policy.reset_cache()
    prov = fake()
    r = ask.run(ask.verify_specs(), {"claim": "c", "evidence": "e"}, provider=prov, session="s")
    assert r.exit_code == 6 and "routing policy" in r.note and not prov.calls
    (d / "routing.yaml").unlink()
    policy.reset_cache()
    assert ask.run(ask.verify_specs(), {"claim": "c", "evidence": "e"}, provider=prov, session="s").exit_code == 6 and not prov.calls


# ---- max_parallel_requests ----------------------------------------------------------------------------------------
def test_a_call_while_max_parallel_requests_are_in_flight_is_not_made():
    db = ledger.open()
    limit = route.limits().max_parallel
    assert limit == 4
    in_flight(db, limit - 1)
    prov = fake()
    assert ask.run(ask.verify_specs(), {"claim": "a", "evidence": "e"}, provider=prov, session="s1", db=db).exit_code == 0
    in_flight(db, 1)
    r = ask.run(ask.verify_specs(), {"claim": "b", "evidence": "e"}, provider=prov, session="s2", db=db)
    assert r.exit_code == 5 and r.primary.error_kind == "TOO_MANY_IN_FLIGHT" and len(prov.calls) == 1
    assert "in flight" in "\n".join(ask.explain(r))
    rows = db.all("SELECT state, exit_code, error_kind FROM jev_calls WHERE session_id='s2'")
    assert rows and all(x.state == "DONE" and x.exit_code == 5 and x.error_kind == "TOO_MANY_IN_FLIGHT" for x in rows)   # no row is left EVALUATING


def test_calls_that_ended_or_whose_process_died_do_not_count_as_in_flight():
    db = ledger.open()
    in_flight(db, 6, age_s=route.limits().deadline_s * 2 + 60)      # older than any live exchange can be
    prov = fake()
    assert ask.run(ask.verify_specs(), {"claim": "a", "evidence": "e"}, provider=prov, session="s1", db=db).exit_code == 0


def test_a_batch_is_one_request_however_many_questions_it_holds():
    db = ledger.open()
    in_flight(db, 2)
    first = db.one("SELECT session_id, state_hash FROM jev_calls LIMIT 1")
    db.begin_call("q.verify_claim.evidence_is_raw", first.state_hash, first.session_id, primitive="noul", task_family="evidence_quality")   # same batch, second question
    in_flight(db, 1)
    # 3 batches in flight (limit 4): this call goes through
    assert ask.run(ask.verify_specs(), {"claim": "a", "evidence": "e"}, provider=fake(), session="s1", db=db).exit_code == 0


def test_a_repeat_served_from_the_ledger_needs_no_request_slot():
    db = ledger.open()
    prov = fake()
    assert ask.run(ask.verify_specs(companion=False), {"claim": "a", "evidence": "e"}, provider=prov, session="s1", db=db).exit_code == 0
    in_flight(db, 4)
    r = ask.run(ask.verify_specs(companion=False), {"claim": "a", "evidence": "e"}, provider=prov, session="s1", db=db)
    assert r.exit_code == 0 and r.primary.cached and len(prov.calls) == 1


def test_the_parallel_limit_is_read_from_the_policy_file(tmp_path, monkeypatch):
    policy_dir(tmp_path, monkeypatch, lambda d: d["routes"]["JEV"].update(max_parallel_requests=1))
    db = ledger.open()
    in_flight(db, 1)
    assert ask.run(ask.verify_specs(), {"claim": "a", "evidence": "e"}, provider=fake(), session="s1", db=db).exit_code == 5


# ---- PF-04: a repeat ----------------------------------------------------------------------------------------------
STATE = {"claim": "tests pass", "evidence": "42 passed in 3.1s\n" * 20, "evidence_source": "inline"}
QID = "q.verify_claim.supported_by_evidence"


def test_pf04_a_question_already_answered_in_the_session_routes_to_reuse(monkeypatch):
    monkeypatch.setenv("JADE_SESSION_ID", "sess-pf04")
    db = ledger.open()
    first = route.preflight(claim="tests pass", evidence=STATE["evidence"], question_id=QID, state=STATE, db=db, about_to_claim_done=True)
    assert first.route == "JEV" and "PF-04" not in first.reason
    ask.run(ask.verify_specs(companion=False), dict(STATE), provider=fake(), session="sess-pf04", db=db)
    again = route.preflight(claim="tests pass", evidence=STATE["evidence"], question_id=QID, state=STATE, db=db, about_to_claim_done=True)
    assert again.route == "REUSE" and again.reason.startswith("PF-04") and "repeat_without_new_evidence" in again.reason and "PF-04" in again.checks


def test_pf04_new_evidence_another_session_or_an_old_answer_is_not_a_repeat(monkeypatch):
    monkeypatch.setenv("JADE_SESSION_ID", "sess-pf04")
    db = ledger.open()
    ask.run(ask.verify_specs(companion=False), dict(STATE), provider=fake(), session="sess-pf04", db=db)
    kw = dict(claim="tests pass", evidence=STATE["evidence"], question_id=QID, db=db, about_to_claim_done=True)
    assert route.preflight(state={**STATE, "evidence": STATE["evidence"] + "one more line\n"}, **kw).route == "JEV"
    assert route.preflight(state=STATE, session="another-session", **kw).route == "JEV"
    db.con.execute("UPDATE jev_calls SET ts=?", (ts(now() - timedelta(seconds=ledger.dedup_ttl_s() + 5)),))
    db.con.commit()
    assert route.preflight(state=STATE, **kw).route == "JEV"


def test_pf04_an_answer_that_was_an_error_is_no_repeat(monkeypatch):
    monkeypatch.setenv("JADE_SESSION_ID", "sess-pf04")
    db = ledger.open()
    down = adapter.FixtureProvider(lambda s, q, m: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ask.run(ask.verify_specs(companion=False), dict(STATE), provider=down, session="sess-pf04", db=db).exit_code == 5
    assert route.preflight(claim="tests pass", evidence=STATE["evidence"], question_id=QID, state=STATE, db=db, about_to_claim_done=True).route == "JEV"


def test_pf04_asks_nothing_when_no_question_is_named():
    assert "PF-04" not in route.preflight(claim="tests pass", evidence=STATE["evidence"], about_to_claim_done=True).checks


def test_pf04_an_unknown_question_id_is_an_invalid_question():
    with pytest.raises(questions.InvalidQuestion):
        route.preflight(claim="x", evidence="raw " * 80, question_id="q.nope", state={"claim": "x"})


def test_the_cli_route_command_reports_reuse(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("JADE_SESSION_ID", "sess-cli")
    ask.run(ask.verify_specs(companion=False), dict(STATE), provider=fake(), session="sess-cli")
    ev = tmp_path / "ev.txt"
    ev.write_text(STATE["evidence"])
    argv = ["route", "--claim", "tests pass", "--evidence-file", str(ev), "--done", "--question-id", QID,
            "--field", "claim=tests pass", "--field", f"evidence=@{ev}", "--field", "evidence_source=inline"]
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["route"] == "REUSE"
    assert cli.main(["route", "--claim", "x", "--evidence", STATE["evidence"], "--question-id", "q.nope"]) == 6


# ---- PF-07: stale context -----------------------------------------------------------------------------------------
EV = "raw output line\n" * 20


def fresh(observed_ago_s, ttl_s):
    return {"observed_at": ts(now() - timedelta(seconds=observed_ago_s)), "ttl_s": ttl_s}


def test_pf07_context_older_than_its_ttl_needs_new_evidence():
    d = route.preflight(claim="x", evidence=EV, about_to_claim_done=True, context_freshness=fresh(3601, 3600))
    assert d.route == "NEEDS_EVIDENCE" and d.reason.startswith("PF-07") and "PF-07" in d.checks
    assert route.preflight(claim="x", evidence=EV, about_to_claim_done=True, context_freshness=fresh(60, 3600)).route == "JEV"


@pytest.mark.parametrize("bad", [{"observed_at": "yesterday", "ttl_s": 60}, {"observed_at": ts()}, {"ttl_s": 60}, {"observed_at": ts(), "ttl_s": 0},
                                 {"observed_at": ts(), "ttl_s": -5}, {"observed_at": ts(), "ttl_s": "60"}, {"observed_at": ts(), "ttl_s": True},
                                 {"observed_at": ts(now() + timedelta(hours=2)), "ttl_s": 86400}, "now", 5])
def test_pf07_a_freshness_claim_that_cannot_be_checked_is_not_fresh(bad):
    d = route.preflight(claim="x", evidence=EV, about_to_claim_done=True, context_freshness=bad)
    assert d.route == "NEEDS_EVIDENCE" and d.reason.startswith("PF-07")


def test_pf07_naive_and_offset_timestamps_are_understood():
    stamp = (now() - timedelta(seconds=30)).replace(tzinfo=None).isoformat(timespec="seconds")      # no zone: read as UTC, as the ledger writes them
    assert route.preflight(claim="x", evidence=EV, about_to_claim_done=True, context_freshness={"observed_at": stamp, "ttl_s": 60}).route == "JEV"
    assert route.preflight(claim="x", evidence=EV, about_to_claim_done=True, context_freshness={"observed_at": stamp, "ttl_s": 10}).route == "NEEDS_EVIDENCE"


def test_pf07_the_cli_flags(capsys):
    assert cli.main(["route", "--claim", "x", "--evidence", EV, "--done", "--observed-at", ts(now() - timedelta(hours=3)), "--ttl-s", "3600"]) == 0
    assert json.loads(capsys.readouterr().out)["route"] == "NEEDS_EVIDENCE"
    assert cli.main(["route", "--claim", "x", "--evidence", EV, "--done", "--observed-at", ts()]) == 2      # one without the other


def test_a_context_that_is_not_described_is_not_checked():
    assert "PF-07" not in route.preflight(claim="x", evidence=EV, about_to_claim_done=True).checks


# ---- the dedup window ---------------------------------------------------------------------------------------------
def test_the_dedup_window_is_read_from_the_policy_file(tmp_path, monkeypatch):
    assert ledger.dedup_ttl_s() == 3600 == ledger.DEDUP_TTL_S
    policy_dir(tmp_path, monkeypatch, lambda d: d["dedup"].update(ttl_s=120))
    assert ledger.dedup_ttl_s() == 120
    prov = fake()
    run = lambda: ask.run(ask.verify_specs(companion=False), {"claim": "a", "evidence": "e"}, provider=prov, session="s")   # noqa: E731
    run()
    db = ledger.open()
    db.con.execute("UPDATE jev_calls SET ts=?", (ts(now() - timedelta(seconds=300)),))
    db.con.commit()
    assert not run().primary.cached and len(prov.calls) == 2


def test_the_provider_deadline_the_policy_declares_is_the_one_the_adapter_enforces():
    assert route.limits().deadline_s == adapter.DEADLINE_S == 20.0


# ---- the registry against the declared limits and the schema -------------------------------------------------------
def entry(**kw):
    e = json.loads((REGISTRY / "q.risk.irreversible.json").read_text())
    e.update(kw)
    return e


def test_the_shipped_registry_is_clean():
    assert questions.validate_registry() == {}
    assert cli.main(["question", "lint"]) == 0


def test_a_family_with_more_batch_eligible_questions_than_one_request_holds_is_reported(tmp_path, monkeypatch):
    reg = registry_dir(tmp_path, monkeypatch)
    for i in range(route.limits().max_questions):          # the three shipped risk_triage questions plus eight more: eleven in one family
        (reg / f"q.risk.extra{i}.json").write_text(json.dumps(entry(question_id=f"q.risk.extra{i}")))
    questions.reset_cache()
    problems = questions.validate_registry()
    assert any("risk_triage" in k and "max_questions_per_request" in p for k, ps in problems.items() for p in ps)
    assert cli.main(["question", "lint"]) == 1


def test_a_registry_entry_that_breaks_the_schema_is_reported_and_never_served(tmp_path, monkeypatch, capsys):
    reg = registry_dir(tmp_path, monkeypatch)
    bad = entry(task_family="not_a_family")
    (reg / "q.risk.irreversible.json").write_text(json.dumps(bad))
    questions.reset_cache()
    assert any("schema" in p for p in questions.validate_registry()["q.risk.irreversible@1"])
    with pytest.raises(questions.InvalidQuestion, match="schema"):
        questions.get("q.risk.irreversible")
    assert cli.main(["question", "lint"]) == 1
    rows = {n: (ok, d) for n, ok, d in ops.check(live=False)}
    assert rows["question registry"][0] is False and "q.risk.irreversible" in rows["question registry"][1]


def test_two_files_holding_the_same_question_and_version_are_reported(tmp_path, monkeypatch):
    reg = registry_dir(tmp_path, monkeypatch)
    (reg / "q.risk.irreversible.copy.json").write_text((REGISTRY / "q.risk.irreversible.json").read_text())
    questions.reset_cache()
    assert any("more than one file" in p for ps in questions.validate_registry().values() for p in ps)


def test_a_dependency_on_a_question_that_does_not_exist_is_reported(tmp_path, monkeypatch):
    reg = registry_dir(tmp_path, monkeypatch)
    (reg / "q.risk.irreversible.json").write_text(json.dumps(entry(dependencies=["q.ghost"], batch_eligibility=False)))
    questions.reset_cache()
    assert any("q.ghost" in p for p in questions.validate_registry()["q.risk.irreversible@1"])


def test_the_registry_that_is_not_json_is_reported_not_a_crash(tmp_path, monkeypatch):
    reg = registry_dir(tmp_path, monkeypatch)
    (reg / "q.broken.json").write_text("{not json")
    questions.reset_cache()
    assert any("q.broken.json" in k for k in questions.validate_registry())
    assert cli.main(["question", "lint"]) == 1


def test_the_lint_enforces_exactly_the_gates_the_policy_lists(tmp_path, monkeypatch):
    leading = entry()
    leading["template"] = {**leading["template"], "instructions": "Obviously, is `claim` irreversible?"}
    assert any("no_leading_language" in p for p in questions.lint(leading))
    policy_dir(tmp_path, monkeypatch, lambda d: d["question_quality_gates"].remove("no_leading_language"))
    assert not any("no_leading_language" in p for p in questions.lint(leading))
