"""W-156: make a time-bound test survive a busy machine, without making it blind to a real slowdown.

A test that asserts "this took less than N seconds" can fail only because other processes had the CPU. `retry_under_load` runs the test body again
(up to `tries` times, three by default) when it fails with an AssertionError, and lets the last failure through. Before every try it clears JADE's
memo caches (`clear_memo_caches`): the shell scan, the policy files, the question registry and the escalation table. Without that, the second try
reads what the first one computed and costs about 3x less CPU, so a real slowdown that fails the cold first try could pass the warm retry (review
of W-156). With it, every try does the same work, a real regression (a quadratic scan) is slow on every try and still fails, and a busy machine is
rarely busy three times in a row. A test that passes the first time costs nothing extra.

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

# The lru_cache functions of `jade` that `clear_memo_caches` clears, as (module, name). `tests/test_loadproof.py` fails when a module gains an
# lru_cache that is in neither this list nor NOT_CLEARED, so a new memo cannot make a retry warm without a decision.
MEMO_CACHES = (
    ("jade.shellnorm", "_skeleton_top"),
    ("jade.shellnorm", "_skeleton_both_top"),
    ("jade.shellnorm_base", "_skeleton_top"),
    ("jade.memory", "_quoted_hash"),
    ("jade.humanonly", "_split_cached"),
    ("jade.policy", "_risk"),        # policy.reset_cache clears these four
    ("jade.policy", "_safety"),
    ("jade.policy", "_routing"),
    ("jade.policy", "_hidden"),
    ("jade.policy", "_command_words"),   # W-153 (lane D): pure memos of one command text; cleared so a retry reads the command as cold as the first try
    ("jade.policy", "_gh_ref_write"),
    ("jade.policy", "_shell_dequoted"),
    ("jade.askplain", "_assignments"),   # W-154 (lane H): a pure memo of one command's variable assignments
    ("jade.questions", "_scan"),     # questions.reset_cache
    ("jade.thresholds", "escalation"),
)
# Left warm on purpose: the JSON schema registry and validators are loaded once per process and no time-bound test measures them; clearing them
# would add load cost to every first try that is not there today.
NOT_CLEARED = (
    ("jade.schemas", "_registry"),
    ("jade.schemas", "validator"),
)


def clear_memo_caches() -> None:
    """Clear every cache of MEMO_CACHES, so the next run of a body is as cold as the first one (the `isolated` fixture clears the policy caches once per test)."""
    import importlib

    for mod, name in MEMO_CACHES:
        getattr(importlib.import_module(mod), name).cache_clear()


def retry_under_load(fn=None, *, tries: int = DEFAULT_TRIES):
    """Re-run the test body on AssertionError, up to `tries` times in all; raise the last failure. JADE's memo caches are cleared before every try."""
    if tries < 1:
        raise ValueError("tries must be at least 1")

    def wrap(f):
        @functools.wraps(f)
        def run(*args, **kwargs):
            for attempt in range(tries):
                clear_memo_caches()
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
