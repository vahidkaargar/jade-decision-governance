"""W-156: make a time-bound test survive a busy machine, without making it blind to a real slowdown.

A test that asserts "this took less than N seconds" can fail only because other processes had the CPU. `retry_under_load` runs the test body again
(up to `tries` times, three by default) when it fails with an AssertionError, and lets the last failure through. A real regression (a quadratic scan) is
slow on every try, so it still fails; a busy machine is rarely busy three times in a row. A test that passes the first time costs nothing extra.

Use it under `@pytest.mark.timing` (the marker keeps CI running the test serially):

    @pytest.mark.timing
    @retry_under_load
    def test_x(): ...

or `@retry_under_load(tries=5)`. Only AssertionError is retried: an exception from the code under test is a real failure on the first try.
`cpu_seconds()` is the clock to prefer: CPU time of this process does not grow when other processes take the machine's time.
"""
import functools
import time

DEFAULT_TRIES = 3


def retry_under_load(fn=None, *, tries: int = DEFAULT_TRIES):
    """Re-run the test body on AssertionError, up to `tries` times in all; raise the last failure."""
    if tries < 1:
        raise ValueError("tries must be at least 1")

    def wrap(f):
        @functools.wraps(f)
        def run(*args, **kwargs):
            for attempt in range(tries):
                try:
                    return f(*args, **kwargs)
                except AssertionError:
                    if attempt == tries - 1:
                        raise
        return run

    return wrap(fn) if fn is not None else wrap


def cpu_seconds() -> float:
    """CPU time of this process (user plus system): other processes taking the CPU do not move it."""
    return time.process_time()
