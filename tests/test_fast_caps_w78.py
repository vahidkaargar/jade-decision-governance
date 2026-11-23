"""W-78 (rest of W-53): `jade fast` caps the pattern and the file, bounds the regex in time, and does not expand `~` in file-exists."""

import os
import time

import pytest

from jade import cli, route


def _fast(*args):
    return route.fast_check(args[0], list(args[1:]))


def test_regex_still_matches_and_reports_the_offset(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("one\ntwo passed\n")
    assert _fast("regex", r"^two passed$", str(f)) == (True, "matched at offset 4")
    assert _fast("regex", "absent", str(f)) == (False, "no match")


def test_a_pattern_over_the_cap_is_refused_before_anything_is_read(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("x")
    with pytest.raises(SystemExit) as e:
        _fast("regex", "a" * (route.FAST_PATTERN_MAX + 1), str(f))
    assert "pattern" in str(e.value) and str(route.FAST_PATTERN_MAX) in str(e.value)
    assert _fast("regex", "a" * route.FAST_PATTERN_MAX, str(f))[0] is False   # exactly the cap is allowed


def test_a_file_over_the_cap_is_refused_not_cut_short(tmp_path):
    f = tmp_path / "big.txt"
    f.write_bytes(b"needle\n" + b"x" * route.FAST_FILE_MAX)   # the match sits at the start: a cut-and-search would say "true"
    with pytest.raises(SystemExit) as e:
        _fast("regex", "needle", str(f))
    assert "too large" in str(e.value)
    ok = tmp_path / "ok.txt"
    ok.write_bytes(b"x" * (route.FAST_FILE_MAX - 7) + b"needle\n")   # exactly the cap is allowed
    assert _fast("regex", "needle", str(ok))[0] is True


def test_a_file_that_is_not_a_regular_file_is_refused(tmp_path):
    with pytest.raises(SystemExit) as e:
        _fast("regex", "x", str(tmp_path))
    assert "regular file" in str(e.value)
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)   # opening a FIFO for reading would block for ever
    with pytest.raises(SystemExit) as e:
        _fast("regex", "x", str(fifo))
    assert "regular file" in str(e.value)
    with pytest.raises(SystemExit):
        _fast("regex", "x", "/dev/zero")   # would read for ever


def test_a_missing_file_is_still_a_clean_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        _fast("regex", "x", str(tmp_path / "nope"))
    assert "cannot read" in str(e.value)


@pytest.mark.timing
def test_a_catastrophic_pattern_stops_at_the_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "FAST_REGEX_TIMEOUT_S", 0.3)
    f = tmp_path / "a.txt"
    f.write_text("a" * 60 + "b")
    t = time.perf_counter()
    with pytest.raises(SystemExit) as e:
        _fast("regex", r"(a+)+$", str(f))   # exponential in the length of the a-run
    assert "did not finish" in str(e.value)
    assert time.perf_counter() - t < 5.0


def test_the_timer_and_handler_are_put_back_after_a_match_and_after_a_timeout(tmp_path, monkeypatch):
    import signal

    monkeypatch.setattr(route, "FAST_REGEX_TIMEOUT_S", 0.3)
    before = signal.getsignal(signal.SIGALRM)
    f = tmp_path / "a.txt"
    f.write_text("a" * 60 + "b")
    _fast("regex", "b", str(f))
    assert signal.getsignal(signal.SIGALRM) == before and signal.getitimer(signal.ITIMER_REAL)[0] == 0
    with pytest.raises(SystemExit):
        _fast("regex", r"(a+)+$", str(f))
    assert signal.getsignal(signal.SIGALRM) == before and signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_an_invalid_pattern_is_still_a_clean_error(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a")
    with pytest.raises(SystemExit) as e:
        _fast("regex", "(", str(f))
    assert "invalid pattern" in str(e.value)


def test_file_exists_does_not_expand_a_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "marker").write_text("x")
    ok, detail = _fast("file-exists", "~/marker")
    assert ok is False and "~/marker" in detail   # a literal path named "~" does not exist here
    assert _fast("file-exists", str(tmp_path / "marker")) == (True, "all exist")


def test_regex_does_not_expand_a_tilde_in_the_file_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "marker").write_text("x")
    with pytest.raises(SystemExit) as e:
        _fast("regex", "x", "~/marker")
    assert "cannot read" in str(e.value)


def test_the_cli_exit_codes_are_unchanged(tmp_path, capsys):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert cli.main(["fast", "regex", "hell", str(f)]) == 0
    assert cli.main(["fast", "regex", "nope", str(f)]) == 4
    assert cli.main(["fast", "file-exists", str(f)]) == 0
