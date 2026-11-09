"""The ask pipeline: build -> redact -> journal (write-ahead) -> dedup -> one batched provider call -> judge -> exit code.

Exit codes (ADR-05): 0 accept band, 3 abstain band, 4 reject band, 5 provider unavailable, 6 invalid question.
Only 0 is a yes. Nothing here creates or touches a permit: a probability is never an authorization (SI-01).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

from . import adapter, ledger, ops, paths, questions, redact, thresholds
from .util import LEVELS, canonical, session_id, sha256, state_language

STATE_CAP = 32 * 1024
DAILY_BUDGET = 500


@dataclass
class Spec:
    key: str
    question_id: str
    primitive: str
    family: str
    payload: dict
    version: int = 1
    gates: bool = True          # False: recorded and reported, never changes the exit code on its own


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


def run(specs: list[Spec], state: dict, *, risk: str = "low", session: str | None = None, decision_id: str | None = None,
        provider=None, model: str | None = None, db: ledger.Ledger | None = None, use_cache: bool = True) -> AskResult:
    cfg = paths.config()
    owners = tuple(e.lower() for e in cfg.get("owner_emails", []))
    session = session or session_id()
    model = model or cfg.get("model") or adapter.DEFAULT_MODEL
    warnings: list[str] = []
    state, hits = redact.redact_obj(state, owners)
    for s in specs:
        s.payload, n = redact.redact_obj(s.payload, owners)
        hits += n
    state, cut = cap_state(state)
    if cut:
        warnings.append("state truncated to 32 KiB")
    items = [Item(s) for s in specs]
    if redact.residual(canonical(state)) or any(redact.residual(canonical(s.payload)) for s in specs):
        for it in items:
            it.exit_code, it.error_kind = 6, "RESIDUAL_SECRET"
        return AskResult(items, 6, "a secret-shaped string survived redaction; nothing was sent", redactions=hits, warnings=warnings)

    own_db = db is None
    try:
        db = db or ledger.open()
    except Exception as e:  # noqa: BLE001 - journaling failure must not turn into a silent yes or a crash
        db = None
        warnings.append(f"ledger unavailable ({type(e).__name__}); this call is not journaled")
    state_text, state_hash = canonical(state), sha256(canonical(state))
    try:
        if db:
            for it in items:
                s = it.spec
                it.call_id = db.begin_call(s.question_id, state_hash, session, version=s.version, claim_hash=sha256(canonical({"q": s.payload, "claim": state.get("claim")})),
                                           primitive=s.primitive, task_family=s.family, request_model=model, decision_id=decision_id,
                                           state_bytes=len(state_text.encode()), state_text=state_text, risk=risk, redactions=hits, language=state_language(state),
                                           question_text=str(s.payload.get("instructions", ""))[:2000] + (" | " + str(state.get("claim", ""))[:500] if state.get("claim") else ""))
                row = db.get_call(it.call_id)
                if use_cache and row.dedup_of:
                    orig = db.get_call(row.dedup_of)
                    if orig and orig.state == "DONE" and orig.answers_json and orig.exit_code in (0, 3, 4):
                        it.answer, it.cached = json.loads(orig.answers_json), True
            today = db.one("SELECT COUNT(*) AS n FROM jev_calls WHERE ts >= strftime('%Y-%m-%dT00:00:00Z','now')").n
            if today > DAILY_BUDGET:
                warnings.append(f"daily call budget exceeded ({today} > {DAILY_BUDGET})")
        result = AskResult(items, 5, redactions=hits, warnings=warnings)
        todo = [it for it in items if not it.cached]
        res = None
        if todo:
            res = (provider or adapter.OpenRouterProvider()).ask(state, {it.spec.key: it.spec.payload for it in todo}, model)
            result.cost_usd, result.resolved_model, result.raw = res.cost_usd, res.resolved_model, res.raw
        for it in items:
            s = it.spec
            if not it.cached:
                if not res.ok or s.key not in res.answers:
                    it.error_kind = res.error_kind or "MALFORMED"
                    it.exit_code = 6 if (res.error_kind == "REJECTED" and res.http_status in (400, 404, 422)) else 5
                    if db and it.call_id:
                        db.finish_call(it.call_id, error_kind=it.error_kind, exit_code=it.exit_code, latency_ms=res.latency_ms, state="DONE")
                    continue
                it.answer = res.answers[s.key]
            resolved = res.resolved_model if (res and res.ok) else None
            it.verdict = thresholds.judge(it.answer, s.family, risk, s.version, resolved)
            it.exit_code = it.verdict.exit_code
            if db and it.call_id:
                db.finish_call(it.call_id, answers_json=it.answer, p=it.verdict.value, verdict=it.verdict.verdict, exit_code=it.exit_code,
                               resolved_model=resolved, provider=res.provider if res else "cache", provider_request_id=res.request_id if res else None,
                               latency_ms=res.latency_ms if (res and not it.cached) else 0.0, input_tokens=res.input_tokens if res else None,
                               output_tokens=res.output_tokens if res else None,
                               cost_usd=(res.cost_usd or 0.0) / max(1, len(todo)) if (res and not it.cached) else 0.0)
        primary = items[0]
        result.exit_code = primary.exit_code
        if primary.cached:
            result.note = "repeat_without_new_evidence: same question and state already answered in this session; cached answer returned"
        # A companion can only make the outcome more cautious, and only when the stakes are above low.
        if primary.exit_code == 0 and LEVELS.get(risk, 0) >= LEVELS["medium"]:
            for it in items[1:]:
                if it.spec.gates and it.verdict and it.verdict.verdict == "REJECT":
                    result.exit_code = 3
                    result.note = f"{it.spec.question_id} rejected (P={it.verdict.value:.2f}): the evidence looks like a summary; attach raw output"
        if res and res.ok and res.resolved_model:
            try:
                ops.check_drift(None, res.resolved_model, ops.expected_models())
            except Exception as e:  # noqa: BLE001 - drift bookkeeping is informational
                warnings.append(f"drift check skipped ({type(e).__name__})")
        if db and decision_id:
            from . import decide, state_machine

            try:
                decide.link_evaluation(db, decision_id, primary.call_id, {0: "ACCEPT", 3: "ABSTAIN", 4: "REJECT"}.get(result.exit_code, "ABSTAIN"),
                                       error=result.exit_code in (5, 6))
            except state_machine.IllegalTransition as e:
                warnings.append(f"decision {decision_id} not advanced: {e}")
        return result
    finally:
        if db and own_db:
            db.close()


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
    if result.exit_code == 3:
        why = result.note or (it.verdict.reason if it.verdict else "")
        out.append(f"jade: ABSTAIN, exit 3 ({why}). Not a yes: add raw evidence and re-ask, or tell the user it is unverified.{cid}")
    elif result.exit_code == 4:
        out.append(f"jade: REJECT, exit 4 ({it.verdict.reason if it.verdict else ''}). The claim is not supported by this evidence.{cid}")
    elif result.exit_code == 5:
        out.append(f"jade: PROVIDER_UNAVAILABLE, exit 5 ({it.error_kind}). No answer was obtained; this is not a yes.{cid}")
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
    return Spec("q", question_id, prim, e["task_family"], payload, e["version"])


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
