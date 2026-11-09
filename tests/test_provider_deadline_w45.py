"""W-45: the provider deadline is end to end. DNS resolution and the TCP connect count against it, not only the headers and the body.

No real network: the resolver and the connect are stubbed to block, and the stalling server is a socket on 127.0.0.1."""
import socket
import threading
import time

import pytest

from jade import adapter, ledger
from tests.acceptance.test_judging_and_improvement import ask_once

DEADLINE = 1.0
BOUND = DEADLINE + 1.0     # the deadline, the thread hand-off and a scheduling margin; far below the 4 s the stub would otherwise block for
PAYLOAD = {"model": "m", "state": {}, "questions": {}}


@pytest.fixture
def release():
    """Lets a blocked stub (and so the abandoned worker thread) finish when the test is over."""
    ev = threading.Event()
    yield ev
    ev.set()


@pytest.fixture
def stalled_resolver(monkeypatch, release):
    def blocked(*a, **kw):
        release.wait(4.0)
        raise socket.gaierror(socket.EAI_AGAIN, "stub resolver gave up")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(adapter, "URL", "http://provider.invalid/api/alpha/decisions")


@pytest.fixture
def stalled_connect(monkeypatch, release):
    def blocked(*a, **kw):
        release.wait(4.0)
        raise OSError("stub connect gave up")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(adapter, "URL", "http://provider.invalid/api/alpha/decisions")


class _Silent:
    """Accepts TCP connections and never reads or answers."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.held, self.stop = [], threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        self.srv.settimeout(0.2)
        while not self.stop.is_set():
            try:
                self.held.append(self.srv.accept()[0])
            except OSError:
                pass

    @property
    def port(self):
        return self.srv.getsockname()[1]

    def close(self):
        self.stop.set()
        for s in self.held + [self.srv]:
            try:
                s.close()
            except OSError:
                pass


def _timed(fn):
    t0 = time.monotonic()
    try:
        return fn(), time.monotonic() - t0
    except BaseException as e:  # noqa: BLE001 - the test inspects the exception
        return e, time.monotonic() - t0


def test_a_resolver_that_hangs_cannot_hold_the_transport_past_the_deadline(stalled_resolver):
    out, elapsed = _timed(lambda: adapter.http_transport(PAYLOAD, "fake-key", DEADLINE))
    assert isinstance(out, TimeoutError) and elapsed < BOUND


def test_a_connect_that_hangs_cannot_hold_the_transport_past_the_deadline(stalled_connect):
    out, elapsed = _timed(lambda: adapter.http_transport(PAYLOAD, "fake-key", DEADLINE))
    assert isinstance(out, TimeoutError) and elapsed < BOUND


def test_a_hung_resolver_is_unavailable_exit_5_and_inside_the_provider_deadline(stalled_resolver, tmp_path):
    prov = adapter.OpenRouterProvider(key_fn=lambda: "fake-key", deadline_s=DEADLINE)
    r, elapsed = _timed(lambda: ask_once(prov, ledger.open(tmp_path)))
    assert r.exit_code == 5 and r.primary.error_kind == "UNAVAILABLE"
    assert elapsed < BOUND + 0.5      # no second attempt starts after the deadline


def test_a_hung_connect_is_unavailable_exit_5_and_inside_the_provider_deadline(stalled_connect, tmp_path):
    prov = adapter.OpenRouterProvider(key_fn=lambda: "fake-key", deadline_s=DEADLINE)
    r, elapsed = _timed(lambda: ask_once(prov, ledger.open(tmp_path)))
    assert r.exit_code == 5 and r.primary.error_kind == "UNAVAILABLE"
    assert elapsed < BOUND + 0.5


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_a_server_that_accepts_and_stalls_is_cut_at_the_deadline(monkeypatch, tmp_path, scheme):
    srv = _Silent()
    try:
        monkeypatch.setattr(adapter, "URL", f"{scheme}://127.0.0.1:{srv.port}/api/alpha/decisions")   # https: the TLS handshake is what stalls
        prov = adapter.OpenRouterProvider(key_fn=lambda: "fake-key", deadline_s=DEADLINE)
        r, elapsed = _timed(lambda: ask_once(prov, ledger.open(tmp_path)))
    finally:
        srv.close()
    assert r.exit_code == 5 and r.primary.error_kind == "UNAVAILABLE"
    assert elapsed < BOUND + 0.5


def test_a_hung_resolver_behind_a_proxy_url_is_cut_too(monkeypatch, release):
    def blocked(*a, **kw):
        release.wait(4.0)
        raise socket.gaierror(socket.EAI_AGAIN, "stub")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    out, elapsed = _timed(lambda: adapter.http_transport(PAYLOAD, "fake-key", DEADLINE))
    assert isinstance(out, TimeoutError) and elapsed < BOUND


def test_a_transport_failure_before_the_deadline_keeps_its_own_error(monkeypatch):
    """The worker thread hands the real exception back; only a missed deadline is a TimeoutError."""
    def refused(*a, **kw):
        raise ConnectionRefusedError(61, "stub refused")

    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(adapter, "URL", "http://provider.invalid/api/alpha/decisions")
    out, elapsed = _timed(lambda: adapter.http_transport(PAYLOAD, "fake-key", 5.0))
    assert isinstance(out, ConnectionRefusedError) and elapsed < 2.0


def test_an_abandoned_worker_never_delivers_a_late_answer(stalled_resolver, release):
    """After the deadline the caller has its TimeoutError; when the stub finally returns, nothing is raised into or returned to anyone."""
    out, _ = _timed(lambda: adapter.http_transport(PAYLOAD, "fake-key", 0.3))
    assert isinstance(out, TimeoutError)
    release.set()
    time.sleep(0.2)
    leftovers = [t for t in threading.enumerate() if t.name.startswith("jade-provider-exchange") and t.is_alive()]
    assert leftovers == []
