"""The ask pipeline: build -> redact -> journal (write-ahead) -> dedup -> one batched provider call -> judge -> exit code.

Exit codes (ADR-05): 0 accept band, 3 abstain band, 4 reject band, 5 provider unavailable, 6 invalid question.
Only 0 is a yes. Nothing here creates or touches a permit: a probability is never an authorization (SI-01).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

from policies.loader import RoutingError

from . import adapter, ledger, ops, paths, questions, redact, route, thresholds
from .util import LEVELS, canonical, normalise_obj, session_id, sha256, state_language

STATE_CAP = 32 * 1024
DAILY_BUDGET = 500
# V6-24 (H16): `jade ask --no-cache` (a human or a test run) may ask the provider again for the same claim and evidence this many times in all, the first answer
# included. After that the stored answer comes back and no provider call is made. The number is a first guess: SP-1 (evals/spikes.py) measures the noise.
NO_CACHE_SAMPLES = 3


@dataclass
class Spec:
    key: str
    question_id: str
    primitive: str
    family: str
    payload: dict
    version: int = 1
    gates: bool = True          # False: recorded and reported, never changes the exit code on its own
    no_match: str | None = None  # a choice question's no-match option: selecting it means no option fits (ABSTAIN)


@dataclass
class Item:
    spec: Spec
    call_id: str | None = None
    answer: dict | None = None
    verdict: thresholds.Verdict | None = None
    exit_code: int = 5
    error_kind: str | None = None
    cached: bool = False


@dataclass
class AskResult:
    items: list[Item]
    exit_code: int
    note: str = ""
    cost_usd: float | None = None
    resolved_model: str | None = None
    raw: dict | None = None
    redactions: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def primary(self) -> Item:
        return self.items[0]


def cap_state(state: dict) -> tuple[dict, bool]:
    """Keep the canonical state under 32 KiB by truncating the longest string field."""
    cut = False
    state = dict(state)
    for _ in range(8):
        size = len(canonical(state).encode())
        if size <= STATE_CAP:
            break
        k = max((k for k, v in state.items() if isinstance(v, str)), key=lambda k: len(state[k]), default=None)
        if k is None:
            break
        keep = max(256, len(state[k]) - (size - STATE_CAP) - 64)
        state[k] = state[k][:keep] + "\n[truncated by jade: state cap 32 KiB]"
        cut = True
    return state, cut


def resolve_model(model: str | None = None) -> str:
    """--model, then the JEV_MODEL environment variable (the legacy script's default), then config.json, then the built-in default."""
    return model or os.environ.get("JEV_MODEL", "").strip() or paths.config().get("model") or adapter.DEFAULT_MODEL


def check_text(specs: list[Spec], state: dict) -> None:
    """What makes a request unsendable before any text is touched, all of it exit 6: more questions than `routes.JEV.max_questions_per_request` lets one
    request hold, a routing policy that cannot be read (the limits are not guessed), and text that cannot be encoded as UTF-8 (a lone surrogate from
    undecodable argv or stdin) and so cannot be sent or journaled."""
    try:
        limit = route.limits().max_questions
    except RoutingError as e:
        raise questions.InvalidQuestion(f"the routing policy cannot be used ({e}); nothing was sent") from None
    if len(specs) > limit:
        raise questions.InvalidQuestion(f"{len(specs)} questions in one request; policies/routing.yaml allows at most {limit} (max_questions_per_request). Split the batch")
    try:
        canonical(state).encode()
        for s in specs:
            canonical(s.payload).encode()
    except UnicodeError:
        raise questions.InvalidQuestion("the text is not valid UTF-8 (undecodable bytes in an argument, file or stdin)") from None


def prepare(specs: list[Spec], state: dict) -> tuple[dict, int, bool, bool]:
    """Everything that happens to a call's text before it can leave the machine: redaction of the state and of every question payload (in
    place on the specs), the 32 KiB cap, and the residual-secret check. Returns (state, redactions, cut, residual). `run` and `--dry-run` both
    use it, so a dry run shows exactly what a real call would send."""
    owners = tuple(e.lower() for e in paths.config().get("owner_emails", []))
    state, hits = redact.redact_obj(state, owners)
    for s in specs:
        s.payload, n = redact.redact_obj(s.payload, owners)
        hits += n
    state, cut = cap_state(state)
    leaked = redact.residual(canonical(state)) or any(redact.residual(canonical(s.payload)) for s in specs)
    return state, hits, cut, leaked


def run(specs: list[Spec], state: dict, *, risk: str = "low", session: str | None = None, decision_id: str | None = None,
        provider=None, model: str | None = None, db: ledger.Ledger | None = None, use_cache: bool = True, resample_cap: int | None = None) -> AskResult:
    """`use_cache=False` asks the provider even for a repeat. `resample_cap` (only the `--no-cache` path sets it) bounds those fresh samples per claim and
    evidence, journals each one, and makes a fresh answer never looser than the first one on the same text. The human proxy sets neither."""
    session = session or session_id()
    model = resolve_model(model)
    warnings: list[str] = []
    items = [Item(s) for s in specs]
    try:
        check_text(specs, state)
    except questions.InvalidQuestion as e:
        for it in items:
            it.exit_code, it.error_kind = 6, "INVALID_QUESTION"
        return AskResult(items, 6, str(e), warnings=warnings)
    state, hits, cut, leaked = prepare(specs, state)
    if cut:
        warnings.append("state truncated to 32 KiB")
    if leaked:
        for it in items:
            it.exit_code, it.error_kind = 6, "RESIDUAL_SECRET"
        return AskResult(items, 6, "a secret-shaped string survived redaction; nothing was sent", redactions=hits, warnings=warnings)

    own_db = db is None
    try:
        db = db or ledger.open()
    except Exception as e:  # noqa: BLE001 - journaling failure must not turn into a silent yes or a crash
        db = None
        warnings.append(f"ledger unavailable ({type(e).__name__}); this call is not journaled")
    try:
        return _run(specs, items, state, hits, warnings, risk=risk, session=session, decision_id=decision_id, provider=provider, model=model,
                    db=db, use_cache=use_cache, resample_cap=resample_cap)
    except Exception as e:  # noqa: BLE001 - fail closed: whatever broke, no row stays EVALUATING and the answer is exit 5, never a yes or a traceback
        for it in items:
            it.answer, it.verdict, it.cached, it.exit_code, it.error_kind = None, None, False, 5, "INTERNAL"
            if db and it.call_id:
                try:
                    db.finish_call(it.call_id, error_kind="INTERNAL", exit_code=5, state="DONE")
                except Exception:  # noqa: BLE001, S110 - the ledger may be what failed
                    pass
        return AskResult(items, 5, f"internal error ({type(e).__name__}); no answer was obtained", redactions=hits, warnings=warnings)
    finally:
        if db and own_db:
            db.close()


def _run(specs: list[Spec], items: list[Item], state: dict, hits: int, warnings: list[str], *, risk: str, session: str, decision_id: str | None,
         provider, model: str, db: ledger.Ledger | None, use_cache: bool, resample_cap: int | None = None) -> AskResult:
    state_text, state_hash = canonical(state), hash_state(state)
    origs: dict[int, object] = {}       # the stored answers that are served again
    anchors: dict[int, object] = {}     # every answered original a repeat points at, served or not
    priors: dict[int, object] = {}      # the first provider answer of ANY source (live or test tag): it holds a fresh sample to the first verdict and counts the cap
    reasons: dict[int, str] = {}        # why a call is a repeat (DI-3a): exact, whitespace, no_cache, cap_hit
    if db and decision_id:
        risk, why = effective_risk(db, decision_id, risk)   # DI-4: the stricter of the caller's risk and the decision's
        if why:
            warnings.append(why)
        capped = revision_cap_result(db, decision_id, items, hits, warnings)   # DI-5: no provider call once the revisions are used up
        if capped is not None:
            return capped
    if db:
        for i, it in enumerate(items):
            s = it.spec
            it.call_id = db.begin_call(s.question_id, state_hash, session, version=s.version, claim_hash=claim_hash(s, state),
                                       primitive=s.primitive, task_family=s.family, request_model=model, decision_id=decision_id,
                                       state_bytes=len(state_text.encode()), state_text=state_text, risk=risk, redactions=hits, language=state_language(state),
                                       question_text=str(s.payload.get("instructions", ""))[:2000] + (" | " + str(state.get("claim", ""))[:500] if state.get("claim") else ""))
            row = db.get_call(it.call_id)
            if use_cache or resample_cap is not None:   # the human proxy path (neither) stays unconstrained
                prior = db.find_first_answer(s.question_id, s.version, state_hash, claim_hash(s, state), session)
                if prior is not None and prior.call_id != it.call_id and prior.answers_json and adapter.check_answer(json.loads(prior.answers_json), s.payload) is None:
                    priors[i] = prior
            if row.dedup_of:
                orig = db.get_call(row.dedup_of)
                if orig and orig.state == "DONE" and orig.answers_json and orig.exit_code in (0, 3, 4):
                    answer = json.loads(orig.answers_json)
                    if adapter.check_answer(answer, s.payload) is None:   # a stored answer is held to the same shape as a live one
                        anchors[i] = orig
                        if use_cache:
                            it.answer, it.cached, origs[i] = answer, True, orig
                            reasons[i] = "exact" if (orig.state_text or "") == state_text[:ledger.STATE_CAP] else "whitespace"
                        elif resample_cap is not None:
                            if samples_of(db, priors.get(i) or orig) >= resample_cap:
                                it.answer, it.cached, origs[i], reasons[i] = answer, True, orig, "cap_hit"
                                warnings.append(f"--no-cache cap reached ({resample_cap} provider answers for this claim and evidence in this session); the stored answer is returned and nothing was sent")
                            else:
                                reasons[i] = "no_cache"
            elif i in priors and resample_cap is not None and not use_cache:
                # No anchor of this source, but the first answer is from another source (a run under `JADE_SOURCE=test` next to a live one): the same cap counts it.
                if samples_of(db, priors[i]) >= resample_cap:
                    it.answer, it.cached, origs[i], reasons[i] = json.loads(priors[i].answers_json), True, priors[i], "cap_hit"
                    warnings.append(f"--no-cache cap reached ({resample_cap} provider answers for this claim and evidence in this session); the stored answer is returned and nothing was sent")
                else:
                    reasons[i] = "no_cache"
        today = db.one("SELECT COUNT(*) AS n FROM jev_calls WHERE ts >= strftime('%Y-%m-%dT00:00:00Z','now')").n
        if today > DAILY_BUDGET:
            warnings.append(f"daily call budget exceeded ({today} > {DAILY_BUDGET})")
    result = AskResult(items, 5, redactions=hits, warnings=warnings)
    todo = [it for it in items if not it.cached]
    res = None
    busy, cap = 0, None
    if todo and db:
        # `max_parallel_requests`: one batched request is one slot, counted over every process through the ledger (this call's own rows excluded). A
        # call that finds the slots taken is not made: exit 5, no answer, never a yes. The count is a soft limit, two calls starting together can both pass.
        cap = route.limits()
        busy = db.in_flight_batches(cap.deadline_s * 2, exclude=tuple(it.call_id for it in items if it.call_id))
    if todo and cap and busy >= cap.max_parallel:
        res = adapter.Result(ok=False, error_kind="TOO_MANY_IN_FLIGHT", detail=f"{busy} requests in flight")
        result.note = f"{busy} requests already in flight (limit {cap.max_parallel}, max_parallel_requests); nothing was sent. Retry in a few seconds"
    elif todo:
        res = (provider or adapter.OpenRouterProvider()).ask(state, {it.spec.key: it.spec.payload for it in todo}, model)
        result.cost_usd, result.resolved_model, result.raw = res.cost_usd, res.resolved_model, res.raw
        if res.ok and not res.resolved_model:
            warnings.append("the provider response carries no model string; judged at the strictest calibrated accept of its family")
    for i, it in enumerate(items):
        s = it.spec
        orig = origs.get(i)
        # A fresh answer is held to the first non-yes answer of this claim and evidence, from any source: the test tag does not start the claim over.
        cands = [c for c in (orig, anchors.get(i) if resample_cap is not None else None, priors.get(i)) if c is not None]
        first = next((c for c in cands if c.exit_code in (3, 4)), cands[0] if cands else None)
        if orig is not None:
            resolved = orig.resolved_model   # a repeat is judged in the group of the call that produced the answer
        else:
            problem = None
            if not res.ok or s.key not in res.answers:
                problem = res.error_kind or "MALFORMED"
            elif adapter.check_answer(res.answers[s.key], s.payload):
                problem = "MALFORMED"
            if problem:
                it.answer, it.error_kind = None, problem
                it.exit_code = 6 if (res.error_kind == "REJECTED" and res.http_status in (400, 404, 422)) else 5
                if db and it.call_id:
                    db.finish_call(it.call_id, error_kind=it.error_kind, exit_code=it.exit_code, latency_ms=res.latency_ms, state="DONE")
                continue
            it.answer = res.answers[s.key]
            resolved = res.resolved_model
        try:
            it.verdict = thresholds.judge(it.answer, s.family, risk, s.version, resolved, primitive=s.primitive, no_match=s.no_match)
        except ValueError:
            it.answer, it.error_kind, it.exit_code, it.cached = None, "MALFORMED", 5, False
            if db and it.call_id:
                db.finish_call(it.call_id, error_kind="MALFORMED", exit_code=5, state="DONE")
            continue
        if first is not None and it.verdict.exit_code == 0 and first.exit_code in (3, 4):
            # A repeat is never looser than the first answer: a changed calibration or a lower risk cannot turn an abstain into a yes, and neither can a fresh sample.
            v = it.verdict
            it.verdict = thresholds.Verdict(first.verdict or "ABSTAIN", first.exit_code, v.value, v.accept, v.reject, v.group, f"repeat of an answered call: the first verdict ({first.verdict}) stands")
        it.exit_code = it.verdict.exit_code
        if db and it.call_id:
            db.finish_call(it.call_id, answers_json=it.answer, p=it.verdict.value, verdict=it.verdict.verdict, exit_code=it.exit_code,
                           resolved_model=resolved, provider=(res.provider if res else None) if not it.cached else "cache",
                           provider_request_id=res.request_id if (res and not it.cached) else None,
                           latency_ms=res.latency_ms if (res and not it.cached) else 0.0, input_tokens=res.input_tokens if (res and not it.cached) else None,
                           output_tokens=res.output_tokens if (res and not it.cached) else None,
                           cost_usd=(res.cost_usd or 0.0) / max(1, len(todo)) if (res and not it.cached) else 0.0)
            if i == 0 and i in reasons:   # one event for the ask, not one for each question in its batch
                journal_repeat(db, it.call_id, reasons[i], first_call=(first.call_id if first is not None else None))
    primary = items[0]
    result.exit_code = primary.exit_code
    if primary.cached:
        result.note = "repeat_without_new_evidence: same question and state already answered in this session; cached answer returned"
    # A companion can only make the outcome more cautious, and only when the stakes are above low. Its answer missing is not a pass.
    if primary.exit_code == 0 and LEVELS.get(risk, 0) >= LEVELS["medium"]:
        for it in items[1:]:
            if not it.spec.gates:
                continue
            if it.verdict is None:
                result.exit_code = 3
                result.note = f"{it.spec.question_id}: no usable answer ({it.error_kind}); the evidence check could not be made"
            elif it.verdict.verdict == "REJECT":
                result.exit_code = 3
                result.note = f"{it.spec.question_id} rejected (P={it.verdict.value:.2f}): the evidence looks like a summary; attach raw output"
    if res and res.ok and res.resolved_model:
        try:
            ops.check_drift(None, res.resolved_model, ops.expected_models())
        except Exception as e:  # noqa: BLE001 - drift bookkeeping is informational
            warnings.append(f"drift check skipped ({type(e).__name__})")
    try:
        ops.check_question_drift(db=db)
    except Exception as e:  # noqa: BLE001 - drift bookkeeping is informational
        warnings.append(f"question drift check skipped ({type(e).__name__})")
    if db and decision_id:
        from . import decide, state_machine

        try:
            decide.link_evaluation(db, decision_id, primary.call_id, {0: "ACCEPT", 3: "ABSTAIN", 4: "REJECT"}.get(result.exit_code, "ABSTAIN"),
                                   error=result.exit_code in (5, 6))
        except state_machine.IllegalTransition as e:
            warnings.append(f"decision {decision_id} not advanced: {e}")
    return result


def open_verify_decision(claim: str) -> str | None:
    """V6-23: the decision a `jade ask verify` call with no `--decision` is attached to, so that its answer is linked and labels can join it later (165 decisions had
    no call attached, and verify calls had no decision). It is always a new decision of this session: a decision the caller did not name is never advanced, and a
    decision of another session is never touched. Returns None when the ledger cannot be opened or written: the call then runs, unlinked, as before."""
    from . import decide

    try:
        db = ledger.open()
        try:
            return decide.open_decision(db, f"Verify claim: {claim}", route="JEV")["decision_id"]
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - linking is a bonus: it never stops the question
        return None


def hash_state(state: dict) -> str:
    """The repeat key of a state (DI-3a): the hash of its text with colour codes, CRLF and trailing space and blank lines left out (`util.normalise_text`) and nothing
    else. A text with none of those hashes as it always did, so stored anchors still match. What is sent and stored is the original text."""
    return sha256(canonical(normalise_obj(state)))


def claim_hash(spec: Spec, state: dict) -> str:
    return sha256(canonical(normalise_obj({"q": spec.payload, "claim": state.get("claim")})))


def effective_risk(db: ledger.Ledger, decision_id: str, risk: str) -> tuple[str, str]:
    """DI-4: the stricter of the risk the caller gave (`--risk`, default low) and the risk recorded for the decision it names. Returns (risk, a sentence when the
    decision raised it). An unknown decision or one with no readable level leaves the risk as given. The link only binds when `--decision` is given."""
    row = db.one("SELECT risk_level FROM decisions WHERE decision_id=?", (decision_id,))
    level = row.risk_level if row else None
    if level in LEVELS and LEVELS[level] > LEVELS.get(risk, 0):
        return level, f"risk raised from {risk} to {level}: decision {decision_id} is rated {level}, so the answer is judged at {level}"
    return risk, ""


def samples_of(db: ledger.Ledger, anchor) -> int:
    """How many times the provider answered this claim on this evidence: the original (`anchor`, an answered ledger row) and every fresh sample after it with the same
    key, from any source (a `JADE_SOURCE=test` run does not get a second cap), not the answers served from the ledger. It reads by the index `calls_session` (session, question, state hash), as the repeat lookup does."""
    return int(db.one("SELECT COUNT(*) AS n FROM jev_calls WHERE session_id=? AND question_id=? AND state_hash=? AND COALESCE(claim_hash,'')=COALESCE(?, '') AND ts>=? "
                      "AND state='DONE' AND exit_code IN (0,3,4) AND COALESCE(provider,'')<>'cache'",
                      (anchor.session_id, anchor.question_id, anchor.state_hash, anchor.claim_hash, anchor.ts)).n)


def journal_repeat(db: ledger.Ledger, entity_id: str, reason: str, **detail) -> None:
    """V6-24: one `ask_repeat` event for each ask that was a repeat (`exact`, `whitespace`, `no_cache`) or that was stopped (`cap_hit`), so `jade ledger stats` can
    split `repeat_share` by reason. Never raises: a journal failure must not change an answer."""
    try:
        db.event(entity_id, "ask_repeat", guard=reason, detail={k: v for k, v in detail.items() if v is not None})
    except Exception:  # noqa: BLE001, S110 - bookkeeping only
        pass


def revision_cap_result(db: ledger.Ledger, decision_id: str, items: list[Item], hits: int, warnings: list[str]) -> AskResult | None:
    """DI-5: a decision that waits for evidence (ABSTAINED or NEEDS_EVIDENCE) with every revision used cannot take another one, so asking the provider again can
    only buy a sample. No call is made and none is journaled as a call: the result is an abstain (exit 3, not a yes) that says to escalate or report the claim as
    unverified. Anything else returns None and `run` goes on."""
    from . import state_machine

    row = db.one("SELECT status, revision_count, doc_json FROM decisions WHERE decision_id=?", (decision_id,))
    if row is None or row.status not in ("ABSTAINED", "NEEDS_EVIDENCE") or row.revision_count < state_machine.MAX_REVISIONS:
        return None
    refs = json.loads(row.doc_json).get("evaluation_refs") or []
    last = db.get_call(refs[-1]) if refs else None
    note = (f"revision cap: decision {decision_id} used all {state_machine.MAX_REVISIONS} revisions and is still {row.status}; nothing was sent. "
            "Escalate to the human, or report the claim as unverified")
    for it in items:
        it.exit_code, it.error_kind = 3, "REVISION_CAP"
    p = items[0]
    p.verdict = thresholds.Verdict("ABSTAIN", 3, last.p if last else None, None, None, "", f"the last verdict stands ({row.status})")
    if last and last.answers_json and last.question_id == p.spec.question_id:
        try:
            p.answer = json.loads(last.answers_json)
        except ValueError:
            p.answer = None
    journal_repeat(db, decision_id, "cap_hit", revision_count=row.revision_count, status=row.status)
    return AskResult(items, 3, note, redactions=hits, warnings=warnings)


def find_repeat(db: ledger.Ledger, question_id: str, state: dict, *, options=None, session: str | None = None):
    """The answered ledger row that asking this registry question on this state now would repeat, or None (PF-04). Hashes the state exactly as `run`
    does, after the same redaction and cap, so a hit here is a hit there."""
    spec = registry_spec(question_id, options=options)
    prepared, _, _, _ = prepare([spec], dict(state))
    return db.find_repeat(spec.question_id, spec.version, hash_state(prepared), claim_hash(spec, prepared), session or session_id())


# ---- rendering -------------------------------------------------------------------------------------
def render(ans: dict) -> str:
    """Exactly the legacy `jev` line for one answer."""
    t = ans.get("type")
    if t == "noul":
        return f"P(yes)={ans['noul']:.2f}"
    if t == "choice":
        probs = " ".join(f"{k}={v:.2f}" for k, v in sorted(ans.get("probabilities", {}).items(), key=lambda kv: -kv[1]))
        return f"choice={ans['choice']} confidence={ans.get('confidence', 0):.2f} | {probs}"
    if t == "score":
        near = ans.get("legend", {}).get(str(round(ans["score"])), "")
        return f"score={ans['score']:.2f} confidence={ans.get('confidence', 0):.2f} (~ {near})"
    return json.dumps(ans, ensure_ascii=False)


def explain(result: AskResult) -> list[str]:
    """Guidance lines for stderr. Never contains the word 'authorized': an answer is not a permission."""
    out = [f"jade: warning: {w}" for w in result.warnings]
    it = result.primary
    cid = f" call={it.call_id}" if it.call_id else ""
    if result.exit_code == 3 and it.error_kind == "REVISION_CAP":
        out.append(f"jade: ABSTAIN, exit 3 ({result.note}). Not a yes: do not ask again; more evidence will not be sent.")
    elif result.exit_code == 3:
        why = result.note or (it.verdict.reason if it.verdict else "")
        out.append(f"jade: ABSTAIN, exit 3 ({why}). Not a yes: add raw evidence and re-ask, or tell the user it is unverified.{cid}")
    elif result.exit_code == 4:
        out.append(f"jade: REJECT, exit 4 ({it.verdict.reason if it.verdict else ''}). The claim is not supported by this evidence.{cid}")
    elif result.exit_code == 5:
        out.append(f"jade: PROVIDER_UNAVAILABLE, exit 5 ({it.error_kind}{': ' + result.note if result.note and it.error_kind == 'TOO_MANY_IN_FLIGHT' else ''}). "
                   f"No answer was obtained; this is not a yes.{cid}")
    elif result.exit_code == 6:
        out.append(f"jade: INVALID_QUESTION, exit 6 ({result.note or it.error_kind}). Fix the question; this is not a yes.{cid}")
    elif result.note:
        out.append(f"jade: note: {result.note}")
    for extra in result.items[1:]:
        if extra.verdict is not None and extra.verdict.verdict != "ACCEPT":
            out.append(f"jade: {extra.spec.question_id}: {render(extra.answer)} ({extra.verdict.verdict.lower()})")
    return out


def emit(result: AskResult, *, raw: bool = False, as_json: bool = False) -> int:
    it = result.primary
    if as_json:
        print(json.dumps({"exit_code": result.exit_code, "note": result.note, "resolved_model": result.resolved_model, "cost_usd": result.cost_usd,
                          "items": [{"question_id": i.spec.question_id, "call_id": i.call_id, "answer": i.answer, "cached": i.cached,
                                     "verdict": i.verdict.verdict if i.verdict else None, "exit_code": i.exit_code, "error_kind": i.error_kind}
                                    for i in result.items]}, ensure_ascii=False, indent=1))
    elif raw and result.raw is not None:
        print(json.dumps(result.raw, ensure_ascii=False, indent=2))
    elif it.answer is not None:
        cost = "" if (it.cached or result.cost_usd is None) else f"  [cost ${result.cost_usd:.6f}]"
        print(render(it.answer) + cost)
    for line in explain(result):
        print(line, file=sys.stderr)
    return result.exit_code


# ---- spec builders ---------------------------------------------------------------------------------
def verify_specs(companion: bool = True) -> list[Spec]:
    main = questions.get("q.verify_claim.supported_by_evidence")
    specs = [Spec("q", main["question_id"], "noul", main["task_family"], questions.build_noul(main["question_id"]), main["version"])]
    if companion:
        c = questions.get("q.verify_claim.evidence_is_raw")
        specs.append(Spec("evidence_is_raw", c["question_id"], "noul", c["task_family"], questions.build_noul(c["question_id"]), c["version"]))
    return specs


def registry_spec(question_id: str, *, options=None, instructions=None, levels=None, true=None, false=None) -> Spec:
    e = questions.get(question_id)
    prim = e["primitive"]
    if prim == "choice":
        payload = questions.build_choice(question_id, options, instructions)
    elif prim == "noul":
        payload = questions.build_noul(question_id, instructions, true, false)
    else:
        payload = questions.build_score(question_id, instructions, levels)
    return Spec("q", question_id, prim, e["task_family"], payload, e["version"], no_match=e.get("no_match_option") if prim == "choice" else None)


def adhoc_spec(primitive: str, text: str, *, options=None, levels=None, true=None, false=None) -> Spec:
    qid, family = questions.ADHOC[primitive]
    if primitive == "choice":
        payload = questions.build_choice(None, options, text)
    elif primitive == "noul":
        payload = questions.build_noul(None, text, true, false)
    else:
        payload = questions.build_score(None, text, levels)
    return Spec("q", qid, primitive, family, payload, 1)


def check_required(question_id: str, state: dict) -> None:
    need = questions.get(question_id).get("input_schema", {}).get("required", [])
    missing = [k for k in need if not str(state.get(k, "")).strip()]
    if missing:
        raise questions.InvalidQuestion(f"{question_id} needs state field(s): {', '.join(missing)}")
