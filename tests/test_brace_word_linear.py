"""W-152: the pattern that finds a brace word (`m{a,}in`) took cubic time on a long word with `{` and no `}` after it.

`expand_braces` now finds the word by reading each whitespace-separated token once. It must give the same text as the old pattern for every input,
and a long word must cost about what a long word of plain letters costs.
"""

from __future__ import annotations

import itertools
import random
import re
import time

import pytest

from jade import policy, shellnorm

OLD = re.compile(shellnorm.BRACE_WORD_OLD)


def old_expand(text: str) -> str:
    """`expand_braces` as it was on origin/main: the same body with the old pattern."""
    if "{" not in text or "," not in text:
        return text

    def one(m: re.Match) -> str:
        w = m.group()
        return " ".join(shellnorm.brace_expand(w)) if "," in w else w

    return OLD.sub(one, text)


FIXED = [
    "m{a,}in", "git push origin ma{in,x} now", "{a,b}", "{a,b} {c,d}", "a{b,c}d{e,f}g", "{,}", "x{,", "x},{", "}{,}", "{ ,}", "a{b, c}",
    "a;{b,c}", "a{b;c,d}e", "a{b,c}e;f}g", "x&{a,b}&y", "a|b{c,d}|e", "(a{b,c})", "`a{b,c}`", "$(a{b,c})", "<{a,b}", ">{a,b}", "{a,b}>x", "{a,b};{c,d}",
    "a{b,c;d}", "a{b,c;d}e;f", "{{a,b},c}", "{a,{b,c}}", "{a,b}}", "}{a,b}", "a}{b,c", "a}b{c,d}", ";}{a,b}", "{;a,b}", "{a,b;}", "{a,b}\n{c,d}", "{a,\nb}",
    "x {a,b}\ty{c,d}  z", "a{b,c}" * 3, "{" * 5 + "," + "}" * 5, "{" * 5 + ",", "a{" * 5 + ",", "}" + "{" * 5 + ";", "{,}" * 4, "a {b,c}", "a {b,c}d",
]


@pytest.mark.parametrize("text", FIXED)
def test_the_same_text_as_the_old_pattern(text):
    assert shellnorm.expand_braces(text) == old_expand(text)


def test_the_same_text_as_the_old_pattern_on_every_short_string():
    alphabet = "{},;a "
    for n in range(1, 8):
        for chars in itertools.product(alphabet, repeat=n):
            text = "".join(chars)
            assert shellnorm.expand_braces(text) == old_expand(text), text


def test_the_same_text_as_the_old_pattern_on_random_strings():
    rng = random.Random(152)
    alphabet = list("{{{}},,,;&|()<>`a\n\t  x")
    for i in range(9000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 24 if i < 6000 else 70)))
        assert shellnorm.expand_braces(text) == old_expand(text), repr(text)


def test_the_frozen_copy_reads_with_the_linear_function():
    """`skeleton_both` joins the frozen copy's reading; its `expand_braces` is the linear one too, so the long word does not cost cubic time there."""
    base = shellnorm._base()
    assert base.expand_braces is shellnorm.expand_braces
    assert "main" in base.skeleton("m{a,}in x")


SHAPES = {
    "open_braces_then_comma": ("", "{", ","),
    "letter_and_open_brace": ("", "a{", ","),
    "close_then_open_braces": ("}", "{", ";"),
    "open_braces_after_a_word": ("a,", "{", ""),
}


@pytest.mark.timing
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_long_word_with_braces_costs_linear_time(shape):
    """Eight times the word takes at most 24 times as long (linear work gives about 8, cubic 512), and 40000 braces fit one second."""
    head, unit, tail = SHAPES[shape]

    def took(n: int) -> float:
        text = "echo " + head + unit * n + tail
        best = float("inf")
        for _ in range(2):
            t = time.perf_counter()
            shellnorm.expand_braces(text)
            best = min(best, time.perf_counter() - t)
        return best

    small, large = took(5000), took(40000)
    assert large < 1.0
    assert large <= 24 * max(small, 1e-3)


@pytest.mark.timing
def test_the_gate_classifies_a_long_brace_word_inside_the_hook_budget():
    policy.assess("Bash", {"command": "echo warm"}, cwd="/tmp")
    t = time.perf_counter()
    policy.assess("Bash", {"command": "git push origin feature " + "{" * 1000 + ","}, cwd="/tmp")
    # origin/main: 10.5 s for 1000 braces (cubic) and over a minute for 2000; now about 0.6 s. What is left is `_brace_span`, which reads to the end of the word at every `{` (quadratic).
    assert time.perf_counter() - t < 4.0
