"""W-30: a challenger version of a choice or a score question is shadowed like a noul one, behind the same barriers.

The shadow run compares a challenger version to the champion on the stored state of recent champion calls. A choice shadow reports the
verdict agreement, the option the two pick and the champion's "no option fits" answers the challenger accepts; a score shadow reports how far
the two scores are apart. Promotion stays human: labels, the protected split and the canary's false-accept trigger exist for noul answers only,
so a choice or score candidate cannot pass the protected stage, start a canary or be promoted by code; a human applies it by hand.
"""

from __future__ import annotations

import json
import shutil

import pytest

from jade import adapter, ask, improve, ledger, ops, paths, questions

QC = "q.strategy.best_option"
QS = "q.evidence.quality"
QN = "q.verify_claim.supported_by_evidence"
V2_TEXT = "Which option best achieves `goal` under `constraints`? An option that breaks a constraint never wins."
OPTIONS = ["alpha=first approach", "beta=second approach"]


def registry_with_v2(tmp_path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    shutil.copytree(paths.registry_dir(), reg)
    for qid in (QC, QS, QN):
        q = json.loads((reg / f"{qid}.json").read_text())
        q["version"] = 2
        q["template"]["instructions"] = V2_TEXT if qid == QC else f"v2 of {qid}: stricter reading"
        (reg / f"{qid}.v2.json").write_text(json.dumps(q))
    monkeypatch.setenv("JADE_REGISTRY_DIR", str(reg))
    questions.reset_cache()


def choice_answer(pick: str, top: float = 0.8, offered=("alpha", "beta", "none_of_these")) -> dict:
    rest = (1 - top) / (len(offered) - 1)
    return {"type": "choice", "choice": pick, "probabilities": {o: (top if o == pick else rest) for o in offered}}


def seed_choice(db, n: int = 12, pick=lambda i: "alpha") -> None:
    for i in range(n):
        prov = adapter.FixtureProvider(lambda s, q, m, i=i: {"q": choice_answer(pick(i), 0.8 if pick(i) != "none_of_these" else 0.9)})
        ask.run([ask.registry_spec(QC, options=OPTIONS)], {"goal": f"goal {i}", "constraints": "none"}, provider=prov, session=f"c{i}", db=db)


def seed_score(db, n: int = 12) -> None:
    prov = adapter.FixtureProvider(lambda s, q, m: {"q": {"type": "score", "score": 0.5}})
    for i in range(n):
        ask.run([ask.registry_spec(QS)], {"claim": f"claim {i}", "evidence": f"raw {i}"}, provider=prov, session=f"e{i}", db=db)


def db_with_candidate(tmp_path, monkeypatch):
    registry_with_v2(tmp_path, monkeypatch)
    db = ledger.open(tmp_path / "home")
    return db


# ---- choice -----------------------------------------------------------------------------------------------------
def test_choice_shadow_compares_verdict_and_option_and_writes_no_live_rows(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed_choice(db)
    seen = []

    def challenger(s, q, m):
        seen.append(q["q"])
        i = int(s["goal"].split()[1])
        return {"q": choice_answer("beta" if i == 3 else "alpha")}

    cand = improve.propose("question_template", "stricter clause", "y" * 10, path=db)
    before = db.one("SELECT COUNT(*) AS n FROM jev_calls").n
    out = improve.shadow(db, cand.candidate_id, QC, 2, provider=adapter.FixtureProvider(challenger), limit=50)
    assert out["primitive"] == "choice" and out["n"] == 12 and out["errors"] == 0
    assert out["agreement"] == round(11 / 12, 3) and out["option_agreement"] == round(11 / 12, 3)
    assert out["accepted_option_changes"] == 1 and out["reject_to_accept"] == 0
    assert out["champion_accept_rate"] == 1.0 and out["challenger_accept_rate"] == 1.0
    assert len(out["disagreements"]) == 1 and out["disagreements"][0]["challenger_choice"] == "beta"
    assert db.one("SELECT COUNT(*) AS n FROM jev_calls").n == before   # a shadow never writes live call rows
    # the challenger is asked its own version's instructions, over the options the champion call offered (the no-match option is added by the builder)
    assert {p["instructions"] for p in seen} == {V2_TEXT}
    assert all(set(p["criteria"]) == {"alpha", "beta", "none_of_these"} for p in seen)
    assert improve.get(db, cand.candidate_id)["results"]["shadow"]["primitive"] == "choice"


def test_a_challenger_that_accepts_an_option_the_champion_found_no_fit_for_is_unsafe(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed_choice(db, pick=lambda i: "none_of_these" if i < 4 else "alpha")
    cand = improve.propose("question_template", "looser clause", "y" * 10, path=db)
    prov = adapter.FixtureProvider(lambda s, q, m: {"q": choice_answer("alpha", 0.9)})
    out = improve.shadow(db, cand.candidate_id, QC, 2, provider=prov, limit=50)
    assert out["reject_to_accept"] == 4 and out["agreement"] == round(8 / 12, 3)
    ok, why = improve.safety_verdict(improve.get(db, cand.candidate_id), improve.get(db, cand.candidate_id)["results"])
    assert not ok and "rejected" in why


def test_choice_rows_the_challenger_cannot_be_asked_are_skipped_not_counted_as_errors(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed_choice(db, n=3)
    db.con.execute("UPDATE jev_calls SET answers_json=? WHERE session_id='c0'", (json.dumps(choice_answer("alpha", 1.0, offered=("alpha", "none_of_these"))),))
    db.con.execute("UPDATE jev_calls SET answers_json=NULL WHERE session_id='c1'")
    cand = improve.propose("question_template", "x", "y" * 10, path=db)
    out = improve.shadow(db, cand.candidate_id, QC, 2, provider=adapter.FixtureProvider(lambda s, q, m: {"q": choice_answer("alpha")}), limit=50)
    assert out["n"] == 1 and out["errors"] == 0 and out["skipped"] == 2   # one option is no choice; no stored answer says nothing about the options


def test_shadow_counts_a_malformed_choice_answer_as_an_error(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed_choice(db, n=4)
    cand = improve.propose("question_template", "x", "y" * 10, path=db)
    prov = adapter.FixtureProvider(lambda s, q, m: {"q": choice_answer("gamma", offered=("alpha", "beta", "gamma"))})   # an option that was not offered
    out = improve.shadow(db, cand.candidate_id, QC, 2, provider=prov, limit=50)
    assert out["n"] == 0 and out["errors"] == 4


# ---- score ------------------------------------------------------------------------------------------------------
def test_score_shadow_reports_how_far_the_scores_are_apart(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed_score(db)
    seen = []

    def challenger(s, q, m):
        seen.append(q["q"])
        return {"q": {"type": "score", "score": 0.9 if s["claim"] == "claim 2" else 0.52}}

    cand = improve.propose("question_template", "x", "y" * 10, path=db)
    out = improve.shadow(db, cand.candidate_id, QS, 2, provider=adapter.FixtureProvider(challenger), limit=50)
    assert out["primitive"] == "score" and out["n"] == 12 and out["errors"] == 0
    assert out["agreement"] == round(11 / 12, 3) and out["max_abs_score_diff"] == 0.4
    assert out["mean_abs_score_diff"] == pytest.approx((11 * 0.02 + 0.4) / 12, abs=1e-3)
    assert out["reject_to_accept"] == 0 and out["champion_accept_rate"] is None   # a score ranks, it never gates
    assert {p["instructions"] for p in seen} == {f"v2 of {QS}: stricter reading"}
    assert all(p["criteria"] == questions.get(QS, 1)["template"]["criteria"] for p in seen)


# ---- noul keeps working, and asks the challenger's own text -----------------------------------------------------
def test_noul_shadow_asks_the_challenger_version_not_the_active_one(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    ok = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    for i in range(3):
        ask.run(ask.verify_specs(companion=False), {"claim": f"claim {i}", "evidence": f"raw {i}"}, provider=ok, session=f"n{i}", db=db)
    seen = []
    cand = improve.propose("question_template", "x", "y" * 10, path=db)
    improve.shadow(db, cand.candidate_id, QN, 2, provider=adapter.FixtureProvider(lambda s, q, m: seen.append(q["q"]) or {"q": {"type": "noul", "noul": 0.9}}), limit=10)
    assert {p["instructions"] for p in seen} == {f"v2 of {QN}: stricter reading"}


def test_shadow_replays_only_calls_of_the_primitive_asked(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed_choice(db, n=2)
    db.con.execute("UPDATE jev_calls SET primitive='noul' WHERE session_id='c0'")
    cand = improve.propose("question_template", "x", "y" * 10, path=db)
    out = improve.shadow(db, cand.candidate_id, QC, 2, provider=adapter.FixtureProvider(lambda s, q, m: {"q": choice_answer("alpha")}), limit=50)
    assert out["n"] == 1


# ---- the barriers -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("qid,seed,answer", [
    (QC, seed_choice, lambda s, q, m: {"q": choice_answer("alpha")}),
    (QS, seed_score, lambda s, q, m: {"q": {"type": "score", "score": 0.5}}),
])
def test_a_choice_or_score_candidate_cannot_pass_protected_canary_or_promote(tmp_path, monkeypatch, human_env, qid, seed, answer):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed(db)
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    improve.shadow(db, c.candidate_id, qid, 2, provider=adapter.FixtureProvider(answer), limit=50)
    from tests.promo_helpers import backdate_shadow

    backdate_shadow(db, c.candidate_id)
    with pytest.raises(improve.PromotionBlocked, match="noul"):
        improve.protected_check(db, c.candidate_id, provider=adapter.FixtureProvider(answer), stdin_isatty=True)
    with pytest.raises(improve.PromotionBlocked, match="noul"):
        improve.start_canary(db, c.candidate_id, stdin_isatty=True)
    with pytest.raises(improve.PromotionBlocked, match="protected_test_non_inferior|canary_ok"):
        improve.promote(db, c.candidate_id, stdin_isatty=True)
    assert "challenger" not in ops.pins_of(tmp_path / "home", qid)
    assert improve.get(db, c.candidate_id)["promotion_decision"] not in ("canary", "promoted")


def test_the_shared_shadow_barriers_apply_to_a_choice_shadow(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed_choice(db)
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    improve.shadow(db, c.candidate_id, QC, 2, provider=adapter.FixtureProvider(lambda s, q, m: {"q": choice_answer("alpha")}), limit=50)
    res = improve.get(db, c.candidate_id)["results"]
    ok, why = improve.shadow_verdict(res, improve.policy())
    assert not ok and "fewer than 100" in why   # 12 live samples
    verdicts = improve.evaluate(db, improve.get(db, c.candidate_id), tmp_path / "home")
    assert verdicts["protected_test_non_inferior"][0] is False and verdicts["canary_ok"][0] is False
    assert "noul" in verdicts["protected_test_non_inferior"][1] and "noul" in verdicts["canary_ok"][1]


def test_a_noul_shadow_still_reaches_the_canary(tmp_path, monkeypatch, human_env):
    """The new barrier is for choice and score only; the noul pipeline of the existing tests is untouched."""
    from tests.promo_helpers import Q, backdate_shadow, provider, registry_v2, seed

    registry_v2(tmp_path, monkeypatch)
    db = ledger.open(tmp_path / "home")
    seed(db)
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    improve.shadow(db, c.candidate_id, Q, 2, provider=provider(), limit=100)
    improve.protected_check(db, c.candidate_id, provider=provider(), stdin_isatty=True)
    backdate_shadow(db, c.candidate_id)
    assert improve.start_canary(db, c.candidate_id, stdin_isatty=True)["promotion_decision"] == "canary"


def test_choice_shadow_respects_the_kill_switch_and_the_canary(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    seed_choice(db, n=3)
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    (tmp_path / "home" / "FREEZE").write_text("")
    with pytest.raises(improve.PromotionBlocked, match="FREEZE"):
        improve.shadow(db, c.candidate_id, QC, 2, provider=adapter.FixtureProvider(lambda s, q, m: {"q": choice_answer("alpha")}))


def test_a_primitive_other_than_noul_choice_score_is_refused(tmp_path, monkeypatch):
    db = db_with_candidate(tmp_path, monkeypatch)
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    monkeypatch.setattr(questions, "get", lambda qid, version=None: {"primitive": "ranking", "task_family": "x"})
    with pytest.raises(improve.PromotionBlocked, match="noul, choice and score"):
        improve.shadow(db, c.candidate_id, QC, 2, provider=adapter.FixtureProvider(lambda *a: {}))


def test_the_protected_check_of_a_noul_template_asks_the_challenger_text(tmp_path, monkeypatch, human_env):
    from tests import promo_helpers as ph

    db = db_with_candidate(tmp_path, monkeypatch)
    ph.seed(db, hold=20, val=0)
    c = improve.propose("question_template", "x", "y" * 10, path=db)
    improve.shadow(db, c.candidate_id, QN, 2, provider=ph.provider(), limit=100)
    seen = []
    improve.protected_check(db, c.candidate_id, provider=adapter.FixtureProvider(lambda s, q, m: seen.append(q["q"]) or {"q": {"type": "noul", "noul": 0.9}}), stdin_isatty=True)
    assert seen and {p["instructions"] for p in seen} == {f"v2 of {QN}: stricter reading"}
