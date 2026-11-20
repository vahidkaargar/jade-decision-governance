"""W-156: `tests.loadproof.retry_under_load` retries a failed assertion a few times and never hides a failure that is always there."""
import pytest

from tests.loadproof import retry_under_load


def _counter():
    calls = []
    return calls, lambda: calls.append(1)


def test_a_test_that_fails_twice_and_then_passes_passes():
    calls, tick = _counter()

    @retry_under_load
    def body():
        tick()
        assert len(calls) >= 3, "slow, as on a busy machine"
        return "ok"

    assert body() == "ok"
    assert len(calls) == 3


def test_a_failure_that_is_always_there_is_raised_after_the_last_try():
    calls, tick = _counter()

    @retry_under_load(tries=4)
    def body():
        tick()
        raise AssertionError(f"slow on try {len(calls)}")

    with pytest.raises(AssertionError, match="slow on try 4"):
        body()
    assert len(calls) == 4


def test_a_test_that_passes_at_once_runs_once():
    calls, tick = _counter()

    @retry_under_load
    def body():
        tick()

    body()
    assert len(calls) == 1


def test_an_error_that_is_not_an_assertion_is_not_retried():
    calls, tick = _counter()

    @retry_under_load
    def body():
        tick()
        raise RuntimeError("a real bug in the code under test")

    with pytest.raises(RuntimeError):
        body()
    assert len(calls) == 1


def test_tries_below_one_is_refused():
    with pytest.raises(ValueError):
        retry_under_load(tries=0)


@pytest.mark.parametrize("n", [1, 2])
def test_pytest_still_sees_the_fixtures_and_parameters_of_the_wrapped_test(n, tmp_path):
    seen = []

    @retry_under_load
    def inner(x, tmp):
        seen.append((x, tmp))

    inner(n, tmp_path)
    assert seen == [(n, tmp_path)]


def test_the_decorated_signature_is_kept_for_pytest_collection():
    import inspect

    @retry_under_load
    def body(monkeypatch, repo):
        pass

    assert list(inspect.signature(body).parameters) == ["monkeypatch", "repo"]
