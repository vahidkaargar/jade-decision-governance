"""Jev transport behind a provider interface (ADR-02). First implementation: OpenRouter Decisions API.

Errors are typed and never look like an answer: REJECTED (4xx), RATE_LIMITED (429), UNAVAILABLE
(5xx, 529, network, timeout), MALFORMED (200 without usable answers), NO_KEY. Retries: 429/529 only,
at most 2, exponential with jitter, total deadline 20 s.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import random
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

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


def _num(x) -> bool:
    """A finite real number. A bool is an int in Python and is not a probability."""
    return isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x)


def _prob(x) -> bool:
    return _num(x) and 0 <= x <= 1


def _valid_answer(a) -> bool:
    """Shape of one answer, whatever was asked: probabilities are finite numbers in [0, 1], a bool is no number, a choice is one of
    its own probabilities and no other option is more probable (the API's `choice` is the argmax)."""
    if not isinstance(a, dict):
        return False
    t = a.get("type")
    if "confidence" in a and not _prob(a["confidence"]):
        return False
    if t == "noul":
        return _prob(a.get("noul"))
    if t == "choice":
        probs = a.get("probabilities")
        if not isinstance(a.get("choice"), str) or not isinstance(probs, dict) or a["choice"] not in probs:
            return False
        if not all(isinstance(k, str) and _prob(v) for k, v in probs.items()):
            return False
        return probs[a["choice"]] >= max(probs.values()) - 1e-9
    if t == "score":
        return _num(a.get("score"))
    return False


def check_answer(answer, payload: dict) -> str | None:
    """Why `answer` does not answer the question `payload`, or None when it does. The answer's own `type` is never trusted to pick the
    judgement: a noul question answered as a score, a choice that was not offered, or a probability outside [0, 1] is MALFORMED (ADR-05)."""
    prim = payload.get("type")
    if not _valid_answer(answer):
        return "the answer is malformed (type, range or shape)"
    if answer["type"] != prim:
        return f"asked a {prim} question, got a {answer['type']} answer"
    if prim == "choice":
        offered = set(payload.get("criteria") or {})
        if answer["choice"] not in offered or set(answer["probabilities"]) - offered:
            return "the answer names an option that was not offered"
    return None


def parse_response(status: int | None, body, exception: str | None = None) -> Result:
    """Turn one HTTP exchange (or transport exception) into a typed Result."""
    if exception:
        return Result(ok=False, error_kind="UNAVAILABLE", detail=f"transport: {exception}")
    if isinstance(body, str | bytes):
        try:
            body = json.loads(body)
        except (ValueError, RecursionError):
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
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    tok = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None  # noqa: E731
    cost = usage.get("cost")
    text = lambda v: v if isinstance(v, str) and v.strip() else None  # noqa: E731
    return Result(ok=True, answers=answers, resolved_model=text(body.get("model")), request_id=text(body.get("id")),
                  provider=text(body.get("provider")), input_tokens=tok(usage.get("input_tokens")), output_tokens=tok(usage.get("output_tokens")),
                  cost_usd=float(cost) if _num(cost) and cost >= 0 else None, http_status=status, raw=body)


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


MAX_BODY = 4 * 1024 * 1024


def _proxy_for(parts):
    """The proxy the environment names for this URL (`https_proxy`/`http_proxy`, `no_proxy`), or None. Only an `http://` proxy is spoken."""
    host = parts.hostname or ""
    proxies = urllib.request.getproxies()
    raw = proxies.get(parts.scheme)
    if not raw or urllib.request.proxy_bypass(host):
        return None
    p = urlsplit(raw if "://" in raw else "http://" + raw)
    if p.scheme != "http" or not p.hostname:
        raise OSError(f"unsupported proxy URL scheme {p.scheme!r}: only http:// proxies are supported")
    return p


GRACE_S = 0.1   # how long past the deadline the caller waits for the worker to report its own timeout before it abandons the worker


def http_transport(payload: dict, key: str, timeout: float):
    """Returns (status, body_text, headers). Raises OSError/TimeoutError/HTTPException on transport failure.

    `timeout` is a wall-clock limit for the whole exchange (DNS, connect, TLS, request, headers and body), not for one socket operation.
    The exchange runs in a daemon worker thread and the caller waits for it with a monotonic deadline: Python cannot interrupt `getaddrinfo`
    or a connect that is waiting on a dropped SYN, so at the deadline the caller abandons the worker and raises TimeoutError. The abandoned
    worker ends when its resolver or connect returns (the socket timeout bounds the connect) and sends nothing once the deadline has passed.
    Inside the worker a timer also shuts the socket at the deadline, so a server that trickles a byte at a time cannot hold it either."""
    deadline = time.monotonic() + max(0.0, timeout)
    expired = threading.Event()
    box: dict = {}
    done = threading.Event()
    holder: list = []   # the connection, once the worker has made one, so an abandoned worker's socket can be closed

    def work() -> None:
        try:
            box["result"] = _exchange(payload, key, deadline, expired, holder)
        except BaseException as e:  # noqa: BLE001 - handed back to the caller unchanged
            box["error"] = e
        finally:
            done.set()

    threading.Thread(target=work, name="jade-provider-exchange", daemon=True).start()
    if not done.wait(max(0.0, deadline - time.monotonic()) + GRACE_S):
        expired.set()
        for conn in holder:
            try:
                conn.close()
            except OSError:
                pass
        raise TimeoutError("deadline reached")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _exchange(payload: dict, key: str, deadline: float, expired: threading.Event, holder: list):
    timeout = max(0.0, deadline - time.monotonic())
    parts = urlsplit(URL)
    https = parts.scheme == "https"
    target, headers = parts.path or "/", {}
    proxy = _proxy_for(parts)
    if proxy is None:
        conn = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(parts.hostname, parts.port, timeout=max(0.05, timeout))
    else:
        # urllib honoured HTTP(S)_PROXY and NO_PROXY; so does this. https goes through a CONNECT tunnel (TLS still ends at the provider),
        # plain http as an absolute-URI request. A proxy URL that cannot be used is a transport failure, never a silent direct connection.
        conn = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(proxy.hostname, proxy.port or 80, timeout=max(0.05, timeout))
        auth = {}
        if proxy.username is not None:
            import base64
            from urllib.parse import unquote

            cred = f"{unquote(proxy.username)}:{unquote(proxy.password or '')}".encode()
            auth = {"Proxy-Authorization": "Basic " + base64.b64encode(cred).decode()}
        if https:
            conn.set_tunnel(parts.hostname, parts.port or 443, headers=auth)   # TCP to the proxy, CONNECT, then TLS to the provider
        else:
            target, headers = URL, auth
    holder.append(conn)

    def cut() -> None:
        expired.set()
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(2)
            except OSError:
                pass

    timer = threading.Timer(max(0.0, timeout), cut)
    timer.daemon = True
    timer.start()
    try:
        conn.connect()   # DNS, TCP, tunnel and TLS happen here; a worker that outlives the deadline must not send the request afterwards
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("deadline reached")
        conn.request("POST", target, body=json.dumps(payload).encode(),
                     headers={**headers, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "jade-decision-governance"})
        resp = conn.getresponse()
        chunks, size = [], 0
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or expired.is_set():
                raise TimeoutError("deadline reached")
            if conn.sock is not None:
                conn.sock.settimeout(left)
            chunk = resp.read1(8192)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BODY:
                raise OSError("response body too large")
            chunks.append(chunk)
        if expired.is_set():
            raise TimeoutError("deadline reached")
        return resp.status, b"".join(chunks).decode("utf-8", "replace"), dict(resp.getheaders())
    except OSError:
        if expired.is_set():
            raise TimeoutError("deadline reached") from None
        raise
    finally:
        timer.cancel()
        conn.close()


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, transport=http_transport, key_fn=get_key, sleep=time.sleep, deadline_s: float = DEADLINE_S, max_retries: int = MAX_RETRIES):
        self.transport, self.key_fn, self.sleep, self.deadline_s, self.max_retries = transport, key_fn, sleep, deadline_s, max_retries

    def ask(self, state: dict, questions: dict, model: str = DEFAULT_MODEL) -> Result:
        start = time.monotonic()   # the deadline covers the key lookup too (a slow Keychain no longer adds to a full exchange)
        key = self.key_fn()
        if not key:
            return Result(ok=False, error_kind="NO_KEY", detail="no OPENROUTER_API_KEY in Keychain or environment")
        payload = {"model": model, "state": state, "questions": questions}
        attempt, result = 0, None
        while True:
            attempt += 1
            remaining = self.deadline_s - (time.monotonic() - start)
            if remaining <= 0:
                return result or Result(ok=False, error_kind="UNAVAILABLE", detail="deadline reached before a request could start", attempts=attempt)
            t0 = time.monotonic()
            status, headers = None, {}
            try:
                status, body, headers = self.transport(payload, key, remaining)
                result = parse_response(status, body)
            except Exception as e:  # noqa: BLE001 - every failure of the exchange is a typed result, never a traceback and never a yes
                kind = "MALFORMED" if status is not None else "UNAVAILABLE"
                result = Result(ok=False, error_kind=kind, http_status=status, detail=f"{'response' if status is not None else 'transport'}: {type(e).__name__}")
            result.latency_ms = (time.monotonic() - t0) * 1000
            result.attempts = attempt
            if result.ok and time.monotonic() - start > self.deadline_s + 0.25:   # a late answer is not accepted
                result = Result(ok=False, error_kind="UNAVAILABLE", detail="response arrived after the deadline", latency_ms=result.latency_ms, attempts=attempt)
            retryable = result.error_kind == "RATE_LIMITED" or status == 529
            if result.ok or not retryable or attempt > self.max_retries:
                return result
            wait = min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25)  # noqa: S311 - jitter, not security
            retry_after = {str(k).lower(): v for k, v in (headers or {}).items()}.get("retry-after")
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
