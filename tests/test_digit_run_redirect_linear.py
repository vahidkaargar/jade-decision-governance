"""W-125: a long run of digits before a redirect (`echo 111...1>x`) took quadratic time in the gate (200000 digits: 84 s, also on 0.6.1).

V6-08 (WT W-104) made `NULL_REDIRECT` start a descriptor only at the start of a run of digits, which fixed it; its timing case
(`evals/performance/timing.jsonl`, P01) has no redirect after the digits. These cases put each redirect shape after the run, so a change
that brings the quadratic cost back for one shape fails here. They time CPU, not the wall clock: on a loaded machine the wall clock of the
same call varied 2 to 10 times between runs, and the CPU time did not. The serial `timing` job runs them.
"""

from __future__ import annotations

import re
import time

import pytest

from jade import policy

# what follows the digits: a file write, an append, a read, a clobber, a read-write, both streams, a descriptor close, a here-string,
# a heredoc, the null device and a descriptor copy; then a run in the target of a push and a run after `;`
SHAPES = {
    "write": ("echo ", ">x"), "write_spaced": ("echo ", " > x"), "append": ("echo ", ">>x"), "read": ("echo ", "<x"),
    "clobber": ("echo ", ">|x"), "read_write": ("echo ", "<>x"), "both": ("echo ", "&>x"), "close": ("echo ", ">&-"),
    "here_string": ("echo ", "<<<x"), "heredoc": ("echo ", "<<E\nx\nE"), "null": ("echo ", ">/dev/null"), "copy": ("echo ", ">&2"),
    "push_target": ("git push origin ", ">x"), "after_semicolon": ("echo x;", ">x"),
}
SMALL, SCALE, BUDGET_S, MAX_RATIO = 4000, 8, 1.0, 24


def _cpu(cmd: str) -> float:
    """CPU seconds of one `policy.assess`, the best of three, with the per-text caches cleared so the work is timed, not a cache hit."""
    best = float("inf")
    for _ in range(3):
        for mod in (policy, policy.shellnorm):
            for fn in vars(mod).values():
                if callable(getattr(fn, "cache_clear", None)):
                    fn.cache_clear()
        policy.assess("Bash", {"command": "echo warm"}, cwd="/tmp/case")
        t = time.process_time()
        policy.assess("Bash", {"command": cmd}, cwd="/tmp/case")
        best = min(best, time.process_time() - t)
    return best


def _problems(shape: str) -> list[str]:
    pre, suf = SHAPES[shape]
    small, large = _cpu(pre + "1" * SMALL + suf), _cpu(pre + "1" * (SCALE * SMALL) + suf)
    problems = []
    if large > BUDGET_S:
        problems.append(f"{SCALE * SMALL} digits took {large:.2f} s CPU, budget {BUDGET_S} s")
    if large > MAX_RATIO * max(small, 1e-3):
        problems.append(f"time ratio {large / max(small, 1e-3):.1f} for {SCALE} times the digits, at most {MAX_RATIO}")
    return problems


@pytest.mark.timing
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_digit_run_before_a_redirect_classifies_in_linear_time(shape):
    """Eight times the digits take at most 24 times the CPU (linear about 8, quadratic about 64), and 32000 digits fit one second."""
    assert _problems(shape) == []


@pytest.mark.timing
def test_the_case_fails_with_the_old_pattern(monkeypatch):
    """The case bites: with the pattern of 0.6.1 put back, a digit run before `>x` is quadratic again and the case fails."""
    from jade import shellnorm, shellnorm_base

    old = re.compile(shellnorm.NULL_REDIRECT_OLD)
    monkeypatch.setattr(shellnorm_base, "NULL_REDIRECT", old)
    monkeypatch.setattr(shellnorm, "NULL_REDIRECT", old)
    problems = _problems("write")
    assert any(p.startswith("time ratio") for p in problems), problems
