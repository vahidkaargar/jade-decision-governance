"""Jev transport behind a provider interface (ADR-02). First implementation: OpenRouter Decisions API.

Errors are typed and never look like an answer: REJECTED (4xx), RATE_LIMITED (429), UNAVAILABLE
(5xx, 529, network, timeout), MALFORMED (200 without usable answers), NO_KEY. Retries: 429/529 only,
at most 2, exponential with jitter, total deadline 20 s.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .util import user

URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.14"
RETRYABLE = {"RATE_LIMITED", "OVERLOADED"}
MAX_RETRIES = 2
DEADLINE_S = 20.0


@dataclass
class Result:
    ok: bool
    answers: dict = field(default_factory=dict)
    resolved_model: str | None = None
    request_id: str | None = None
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    error_kind: str | None = None
    http_status: int | None = None
    detail: str = ""
    attempts: int = 1
    raw: dict | None = None


def _valid_answer(a) -> bool:
    if not isinstance(a, dict):
        return False
    t = a.get("type")
    if t == "noul":
        return isinstance(a.get("noul"), int | float) and 0 <= a["noul"] <= 1
    if t == "choice":
        return isinstance(a.get("choice"), str) and isinstance(a.get("probabilities"), dict)
    if t == "score":
        return isinstance(a.get("score"), int | float)
    return False


def parse_response(status: int | None, body, exception: str | None = None) -> Result:
    """Turn one HTTP exchange (or transport exception) into a typed Result."""
    if exception:
        return Result(ok=False, error_kind="UNAVAILABLE", detail=f"transport: {exception}")
    if isinstance(body, str | bytes):
        try:
            body = json.loads(body)
        except ValueError:
            body = {"_unparsed": str(body)[:200]}
    body = body if isinstance(body, dict) else {}
    detail = ""
    if isinstance(body.get("error"), dict):
        detail = str(body["error"].get("message", ""))[:300]
    if status == 429:
        return Result(ok=False, error_kind="RATE_LIMITED", http_status=status, detail=detail)
    if status is None or status >= 500 or status == 408:
        return Result(ok=False, error_kind="UNAVAILABLE", http_status=status, detail=detail)
    if status >= 400:
        return Result(ok=False, error_kind="REJECTED", http_status=status, detail=detail)
    answers = body.get("answers")
    if not isinstance(answers, dict) or not answers or not all(_valid_answer(a) for a in answers.values()):
        return Result(ok=False, error_kind="MALFORMED", http_status=status, detail="response has no usable answers", raw=body)
    usage = body.get("usage") or {}
    return Result(ok=True, answers=answers, resolved_model=body.get("model"), request_id=body.get("id"),
                  provider=body.get("provider"), input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                  cost_usd=usage.get("cost"), http_status=status, raw=body)


def parse_fixture(path: str | Path) -> Result:
    fx = json.loads(Path(path).read_text())
    return parse_response(fx.get("status"), fx.get("body"), fx.get("exception"))


def get_key() -> str:
    """Keychain item OPENROUTER_API_KEY, else the environment variable. The value is never logged."""
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", "OPENROUTER_API_KEY", "-a", os.environ.get("USER") or user(), "-w"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def http_transport(payload: dict, key: str, timeout: float):
    """Returns (status, body_text, headers). Raises OSError/TimeoutError on transport failure."""
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "X-Title": "jade-decision-governance"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URL
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, transport=http_transport, key_fn=get_key, sleep=time.sleep, deadline_s: float = DEADLINE_S):
        self.transport, self.key_fn, self.sleep, self.deadline_s = transport, key_fn, sleep, deadline_s

    def ask(self, state: dict, questions: dict, model: str = DEFAULT_MODEL) -> Result:
        key = self.key_fn()
        if not key:
            return Result(ok=False, error_kind="NO_KEY", detail="no OPENROUTER_API_KEY in Keychain or environment")
        payload = {"model": model, "state": state, "questions": questions}
        start = time.monotonic()
        attempt, result = 0, None
        while True:
            attempt += 1
            remaining = self.deadline_s - (time.monotonic() - start)
            t0 = time.monotonic()
            try:
                status, body, headers = self.transport(payload, key, max(1.0, remaining))
                result = parse_response(status, body)
            except (OSError, TimeoutError, ValueError) as e:
                status, headers = None, {}
                result = parse_response(None, None, type(e).__name__)
            result.latency_ms = (time.monotonic() - t0) * 1000
            result.attempts = attempt
            retryable = result.error_kind == "RATE_LIMITED" or status == 529
            if result.ok or not retryable or attempt > MAX_RETRIES:
                return result
            wait = min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25)  # noqa: S311 - jitter, not security
            retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after")
            if retry_after and str(retry_after).isdigit():
                wait = max(wait, float(retry_after))
            if time.monotonic() - start + wait >= self.deadline_s:
                return result
            self.sleep(wait)


class FixtureProvider:
    """Deterministic provider for tests and offline replay: answers come from a callable or a queue."""

    name = "fixture"

    def __init__(self, responder):
        self.responder, self.calls = responder, []

    def ask(self, state: dict, questions: dict, model: str = DEFAULT_MODEL) -> Result:
        self.calls.append({"state": state, "questions": questions, "model": model})
        r = self.responder(state, questions, model)
        return r if isinstance(r, Result) else Result(ok=True, answers=r, resolved_model=model + "-fixture", latency_ms=1.0, cost_usd=0.0)
