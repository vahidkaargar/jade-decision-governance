"""Preflight (policies/routing.yaml): suitability decides the route, risk decides the rigor."""

from __future__ import annotations

import os
import re
import signal
import stat
from dataclasses import dataclass, field
from pathlib import Path

DEEP_HINTS = re.compile(r"\b(design|architecture|architect|refactor plan|strategy for|how should (i|we)|what should (i|we)|roadmap|trade-?offs? between|best approach)\b", re.I)
PARAPHRASE = re.compile(r"^\s*(i (ran|checked|verified|confirmed|think|believe)|it (works|passes|should)|looks (good|fine)|everything (works|passes)|done\b)", re.I)


@dataclass
class RouteDecision:
    route: str
    reason: str
    checks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Limits:
    max_questions: int      # questions in one batched request (routes.JEV.max_questions_per_request)
    max_parallel: int       # batched requests in flight at once, over every process (routes.JEV.max_parallel_requests)
    deadline_s: float       # wall-clock limit of one provider exchange (routes.JEV.deadline_ms)


IMPLEMENTED = ("PF-01", "PF-02", "PF-03", "PF-04", "PF-05", "PF-06", "PF-07", "PF-08")   # the checks `preflight` runs; policy_problems keeps the file in step
FUTURE_SKEW_S = 60   # a context observed more than this far in the future has a clock or a claim that cannot be trusted


def _routing() -> dict:
    from . import policy

    return policy.routing()


def limits() -> Limits:
    """The limits policies/routing.yaml declares for the JEV route. Raises `policies.loader.RoutingError` when the file is unusable."""
    jev = _routing()["routes"]["JEV"]
    return Limits(int(jev["max_questions_per_request"]), int(jev["max_parallel_requests"]), int(jev["deadline_ms"]) / 1000)


def _to(pf: str, key: str) -> str:
    """The route a check sends the request to, as the policy file declares it (`on_true` or `on_false`)."""
    from policies.loader import RoutingError

    for p in _routing()["preflight"]:
        if p["id"] == pf:
            if key not in p:
                raise RoutingError(f"{pf} declares no {key}")
            return p[key]
    raise RoutingError(f"routing.yaml does not declare {pf}")


def policy_problems() -> list[str]:
    """What is wrong with policies/routing.yaml as the code sees it: unreadable, a check the preflight does not run, a gate the lint does not know."""
    from policies.loader import RoutingError

    from . import questions

    try:
        doc = _routing()
        problems = []
        if doc["schema_version"] != "1.0.0":
            problems.append(f"routing.yaml schema_version {doc['schema_version']!r} is not one this version reads")
        declared = [p["id"] for p in doc["preflight"]]
    except RoutingError as e:
        return [str(e)]
    problems += [f"routing.yaml declares {i}, which route.preflight does not implement" for i in declared if i not in IMPLEMENTED]
    problems += [f"route.preflight runs {i}, which routing.yaml does not declare" for i in IMPLEMENTED if i not in declared]
    problems += [f"routing.yaml lists the question gate {g!r}, which `jade question lint` does not implement" for g in doc["question_quality_gates"] if g not in questions.GATES]
    return problems


def _freshness_problem(cf) -> str | None:
    """None when `{observed_at, ttl_s}` describes a context that is still fresh; otherwise why it is not."""
    from .util import now, parse_ts

    if not isinstance(cf, dict) or "observed_at" not in cf or "ttl_s" not in cf:
        return "context_freshness needs observed_at and ttl_s"
    ttl = cf["ttl_s"]
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not 0 < ttl < float("inf"):
        return "ttl_s must be a positive number of seconds"
    try:
        seen = parse_ts(str(cf["observed_at"]))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=now().tzinfo)   # no zone given: UTC, as the ledger writes its timestamps
        age = (now() - seen).total_seconds()
    except ValueError:
        return "observed_at is not an ISO 8601 timestamp"
    if age < -FUTURE_SKEW_S:
        return "observed_at is in the future"
    if age >= ttl:
        return f"the context was observed {int(age)} s ago and is good for {int(ttl)} s"
    return None


def _repeat(question_id: str, state: dict, options, session: str | None, db):
    """The answered call PF-04 reuses, or None. Opens the ledger when none is given; a ledger that cannot be opened means no repeat can be shown."""
    from . import ask, ledger

    own = db is None
    try:
        db = db or ledger.open()
    except Exception:  # noqa: BLE001 - PF-04 is an optimisation, `jade ask` dedups on its own
        return None
    try:
        return ask.find_repeat(db, question_id, state, options=options, session=session)
    finally:
        if own:
            db.close()


def preflight(*, claim: str = "", evidence: str = "", options: list[str] | None = None, command: str | None = None, risk: str = "low",
              about_to_claim_done: bool = False, deterministic: bool = False, primitive: str | None = None,
              question_id: str | None = None, state: dict | None = None, session: str | None = None, db=None,
              context_freshness: dict | None = None) -> RouteDecision:
    """`question_id` with `state` turns PF-04 on (the ledger answers whether this exact question was already answered in the session);
    `context_freshness` turns PF-07 on. A check whose input is not given is not run, and `checks` lists only the ones that ran."""
    checks = []
    if deterministic:
        return RouteDecision(_to("PF-01", "on_true"), "PF-01: a deterministic check answers this; use `jade fast` and no model call", ["PF-01"])
    checks.append("PF-01")
    if command:
        from . import policy

        a = policy.assess("Bash", {"command": command})
        if a.blocked:
            return RouteDecision(_to("PF-02", "on_true"), f"PF-02: safety invariant {', '.join(a.blocked)} matches; blocked whatever the evidence says", checks + ["PF-02"])
        if a.level in ("high", "critical"):
            risk = a.level if risk in ("low", "medium") else risk
    checks.append("PF-02")
    primitive = primitive or ("choice" if options else "noul")
    if primitive == "noul" and (not evidence.strip() or (len(evidence) < 200 and PARAPHRASE.search(evidence))):
        return RouteDecision(_to("PF-03", "on_false"), "PF-03: no raw evidence attached (tool output, diff or document excerpt); a summary is not evidence", checks + ["PF-03"])
    checks.append("PF-03")
    if question_id and state is not None:
        prior = _repeat(question_id, state, options, session, db)
        checks.append("PF-04")
        if prior is not None:
            return RouteDecision(_to("PF-04", "on_true"), f"PF-04: repeat_without_new_evidence: {question_id} was already answered on this state in this session "
                                 f"(call {prior.call_id}); reuse that answer instead of asking again", checks)
    if options is not None and len({o.split('=')[0].strip() for o in options if o.strip()}) < 2:
        return RouteDecision(_to("PF-05", "on_false"), "PF-05: fewer than two eligible options; a one-option choice returns confidence 1.0 and means nothing", checks + ["PF-05"])
    checks.append("PF-05")
    if DEEP_HINTS.search(claim) or claim.count("?") > 1 or len(claim) > 600:
        return RouteDecision(_to("PF-06", "on_false"), "PF-06: not atomic or bounded; decompose with the reasoning model, then ask atomic questions", checks + ["PF-06"])
    checks.append("PF-06")
    if context_freshness is not None:
        why = _freshness_problem(context_freshness)
        checks.append("PF-07")
        if why:
            return RouteDecision(_to("PF-07", "on_false"), f"PF-07: stale or unverifiable context ({why}); observe it again and attach the new output", checks)
    if risk == "low" and not about_to_claim_done:
        return RouteDecision(_to("PF-08", "on_false"), "PF-08: low risk and no completion claim pending; evaluation is not worth a call yet", checks + ["PF-08"])
    return RouteDecision(_routing()["default_route"], f"default route: one batched request (risk {risk})", checks + ["PF-08"])


FAST_PATTERN_MAX = 1000          # characters in a `jade fast regex` pattern (W-78)
FAST_FILE_MAX = 1024 * 1024      # bytes in the file it searches; a larger file is refused, never cut (a cut file could say "no match" for text past the cut)
FAST_REGEX_TIMEOUT_S = 2.0       # wall-clock limit of one search; a pattern that backtracks without end stops here


class _RegexTimeout(Exception):
    pass


def _on_regex_alarm(_signum, _frame):
    raise _RegexTimeout


def _read_capped(name: str) -> str:
    """Read one regular file of at most FAST_FILE_MAX bytes. A directory, a FIFO or a device (`/dev/zero`) is refused before it can block or fill memory."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK)   # O_NONBLOCK: opening a FIFO must not wait for a writer
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise SystemExit(f"jade fast: {name} is not a regular file")
            if st.st_size > FAST_FILE_MAX:
                raise SystemExit(f"jade fast: {name} is too large ({st.st_size} bytes; the limit is {FAST_FILE_MAX})")
            data = b""
            while len(data) <= FAST_FILE_MAX:   # a file that grew since fstat is still bounded
                chunk = os.read(fd, FAST_FILE_MAX + 1 - len(data))
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(fd)
    except OSError as e:
        raise SystemExit(f"jade fast: cannot read {name} ({type(e).__name__})") from None
    if len(data) > FAST_FILE_MAX:
        raise SystemExit(f"jade fast: {name} is too large (the limit is {FAST_FILE_MAX} bytes)")
    return data.decode("utf-8", errors="replace")


def _search_with_timeout(pattern: str, text: str):
    """re.search under an interval timer. Without SIGALRM (not the main thread, or no such signal) the pattern and file caps are the only bound."""
    try:
        old = signal.signal(signal.SIGALRM, _on_regex_alarm)
    except (ValueError, AttributeError):
        return re.search(pattern, text, re.M)
    try:
        signal.setitimer(signal.ITIMER_REAL, FAST_REGEX_TIMEOUT_S)
        try:
            return re.search(pattern, text, re.M)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, old)


def fast_check(kind: str, args: list[str]) -> tuple[bool, str]:
    if kind == "exit-code":
        if len(args) != 2:
            raise SystemExit("usage: jade fast exit-code EXPECTED ACTUAL")
        return args[0].strip() == args[1].strip(), f"expected {args[0]}, actual {args[1]}"
    if kind == "file-exists":
        missing = [a for a in args if not Path(a).exists()]   # no `~` expansion: the shell does that for an unquoted path, and a quoted one is a literal name
        return not missing, "all exist" if not missing else f"missing: {', '.join(missing)}"
    if len(args) != 2:
        raise SystemExit("usage: jade fast regex PATTERN FILE")
    if len(args[0]) > FAST_PATTERN_MAX:
        raise SystemExit(f"jade fast: pattern is longer than {FAST_PATTERN_MAX} characters")
    try:
        re.compile(args[0], re.M)
    except re.error as e:
        raise SystemExit(f"jade fast: invalid pattern ({e})") from None
    text = _read_capped(args[1])
    try:
        m = _search_with_timeout(args[0], text)
    except _RegexTimeout:
        raise SystemExit(f"jade fast: the search did not finish in {FAST_REGEX_TIMEOUT_S:g} s (the pattern is too costly)") from None
    except re.error as e:
        raise SystemExit(f"jade fast: invalid pattern ({e})") from None
    return bool(m), f"matched at offset {m.start()}" if m else "no match"
