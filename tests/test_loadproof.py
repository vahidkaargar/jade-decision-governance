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


CMD = "echo " + "'a' " * 200 + "; git push origin main"


def test_a_retry_is_as_cold_as_the_first_try_so_a_slow_scan_cannot_pass_from_the_cache(monkeypatch):
    """Review of W-156: the shell scan is memoised (`_skeleton_top`, `_skeleton_both_top`, `policy._hidden`), so a retry read the first try's result and
    a slowdown that failed the cold first try passed the warm second one. Every try must run the scan itself: here the underlying `_skeleton` is counted,
    the first try fails (as a slow one would), and the second try must still call it."""
    from jade import policy, shellnorm

    real = shellnorm._skeleton
    calls = []

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(shellnorm, "_skeleton", counted)
    per_try = []

    @retry_under_load
    def body():
        before = len(calls)
        a = policy.assess("Bash", {"command": CMD}, "")
        per_try.append(len(calls) - before)
        assert "SI-08" in a.blocked
        assert len(per_try) >= 2, "slow, as a real slowdown is on the cold first try"

    body()
    assert len(per_try) == 2 and per_try[0] > 0, per_try
    assert per_try[1] > 0, f"the second try read the scan from the cache: {per_try}"


def test_the_memo_caches_are_empty_at_the_start_of_every_try():
    from jade import policy, shellnorm

    sizes = []

    @retry_under_load(tries=3)
    def body():
        sizes.append((shellnorm._skeleton_top.cache_info().currsize, shellnorm._skeleton_both_top.cache_info().currsize, policy._hidden.cache_info().currsize))
        policy.assess("Bash", {"command": CMD}, "")
        raise AssertionError("always slow")

    with pytest.raises(AssertionError):
        body()
    assert sizes == [(0, 0, 0)] * 3, sizes


def test_every_lru_cache_of_jade_is_cleared_before_a_try_or_left_warm_by_a_written_decision():
    """A new memo cache in `jade` would make a retry warm again without anyone noticing: it must be put in MEMO_CACHES (cleared before every try)
    or in NOT_CLEARED (left warm, with the reason in tests/loadproof.py)."""
    import importlib
    import pkgutil

    import jade
    from tests import loadproof

    found = set()
    for info in pkgutil.walk_packages(jade.__path__, "jade."):
        mod = importlib.import_module(info.name)
        for name, obj in vars(mod).items():
            if callable(getattr(obj, "cache_clear", None)) and callable(getattr(obj, "cache_info", None)) and getattr(obj, "__module__", None) == info.name:
                found.add((info.name, name))
    listed = set(loadproof.MEMO_CACHES) | set(loadproof.NOT_CLEARED)
    assert found - listed == set(), f"lru_cache functions not in MEMO_CACHES or NOT_CLEARED: {sorted(found - listed)}"
    assert listed - found == set(), f"listed but not an lru_cache any more: {sorted(listed - found)}"


def test_the_decorated_signature_is_kept_for_pytest_collection():
    import inspect

    @retry_under_load
    def body(monkeypatch, repo):
        pass

    assert list(inspect.signature(body).parameters) == ["monkeypatch", "repo"]
