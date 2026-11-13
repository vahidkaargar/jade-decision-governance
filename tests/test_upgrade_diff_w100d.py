"""W-100d: `jade upgrade` shows the files that change, then needs a typed `yes`.

The upgrade replaces the code that decides every gate call, so the human reads which files under jade/, policies/, registry/ and scripts/ changed since the installed
commit (GitHub's compare answer, reduced to a path and a status) and types `yes`. No test touches the network: `upgrade._http_get` and `upgrade._run` are replaced.
"""

import json

import pytest

from jade import cli, ops, upgrade, util

BASE = "a1b2c3d4e5f6"
TAG = "v0.5.0"
COMPARE = f"https://api.github.com/repos/vahidkaargar/jade-decision-governance/compare/{BASE}...{TAG}"


def compare(files, **extra):
    return json.dumps({"files": files, **extra}).encode()


def f(name, status="modified", add=1, dele=0):
    return {"filename": name, "status": status, "additions": add, "deletions": dele}


@pytest.fixture
def web(monkeypatch):
    pages = {}

    def get(url, timeout=upgrade.HTTP_TIMEOUT_S, limit=upgrade.MAX_BYTES):
        if url not in pages:
            raise AssertionError(f"unexpected URL {url}")
        v = pages[url]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(upgrade, "_http_get", get)
    monkeypatch.setattr(upgrade, "_direct_url_commit", lambda: BASE)
    monkeypatch.setattr(upgrade.paths, "code_commit", lambda: None)
    return pages


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout=600, capture=False):
        self.calls.append(list(argv))
        return (0, "ok    hooks installed: all five\n") if argv[0] != "uv" else (0, "")


@pytest.fixture
def runner(monkeypatch):
    r = Runner()
    monkeypatch.setenv("JADE_BIN", "/opt/jade-new/bin/jade")
    monkeypatch.setattr(upgrade, "_run", r)
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)
    return r


# ------------------------------------------------------------------ the file list

def test_the_list_keeps_the_four_directories_and_says_how_each_changed(web):
    web[COMPARE] = compare([f("jade/hooks.py", add=12, dele=3), f("policies/risk.yaml"), f("registry/x.json", "added"), f("scripts/s.sh", "removed"),
                            f("README.md"), f("tests/test_x.py"), f("docs/09.md")])
    lines, note = upgrade.changed_files(TAG)
    assert lines == ["modified  jade/hooks.py (+12 -3)", "modified  policies/risk.yaml (+1 -0)", "added     registry/x.json (+1 -0)", "removed   scripts/s.sh (+1 -0)"]
    assert "3 other file" in note


def test_a_path_with_odd_characters_is_dropped_not_printed(web):
    web[COMPARE] = compare([f("jade/ok.py"), f("jade/\x1b[31mred.py"), f("jade/a b.py"), {"filename": 5}, "x", f("jade/new\nline.py")])
    lines, _ = upgrade.changed_files(TAG)
    assert lines == ["modified  jade/ok.py (+1 -0)"]


def test_a_long_list_is_cut_and_says_so(web):
    web[COMPARE] = compare([f(f"jade/m{i}.py") for i in range(130)])
    lines, note = upgrade.changed_files(TAG)
    assert len(lines) == upgrade.MAX_FILES and "30 more" in note


def test_github_lists_at_most_300_files_and_the_note_says_when_that_is_all_it_gave(web):
    web[COMPARE] = compare([f(f"jade/m{i}.py") for i in range(300)], total_commits=900)
    _, note = upgrade.changed_files(TAG)
    assert "300" in note and "may be more" in note


def test_an_unknown_installed_commit_means_no_list(monkeypatch):
    monkeypatch.setattr(upgrade, "_direct_url_commit", lambda: None)
    monkeypatch.setattr(upgrade.paths, "code_commit", lambda: None)
    monkeypatch.setattr(upgrade, "_http_get", lambda *a, **k: pytest.fail("no commit: nothing to ask GitHub"))
    lines, note = upgrade.changed_files(TAG)
    assert lines == [] and "not known" in note


@pytest.mark.parametrize("err", [upgrade.FetchError("offline", "x"), upgrade.FetchError("rate_limited", "HTTP 403"), upgrade.FetchError("not_found", "HTTP 404")])
def test_a_fetch_failure_is_a_note_and_never_raises(web, err):
    web[COMPARE] = err
    lines, note = upgrade.changed_files(TAG)
    assert lines == [] and err.kind in note


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"files": "x"}', b"{}"])
def test_a_garbled_answer_is_a_note(web, body):
    web[COMPARE] = body
    lines, note = upgrade.changed_files(TAG)
    assert lines == [] and note


def test_an_installed_commit_that_is_not_hex_is_never_put_in_the_url(monkeypatch):
    monkeypatch.setattr(upgrade, "_direct_url_commit", lambda: "../../etc")
    monkeypatch.setattr(upgrade.paths, "code_commit", lambda: None)
    monkeypatch.setattr(upgrade, "_http_get", lambda *a, **k: pytest.fail("a commit that is not hex is not used"))
    lines, note = upgrade.changed_files(TAG)
    assert lines == [] and note


# ------------------------------------------------------------------ the typed yes

def test_apply_asks_before_it_installs_and_stops_on_anything_but_yes(runner, web):
    web[COMPARE] = compare([f("jade/hooks.py")])
    said = []
    res = upgrade.apply(TAG, say=said.append, confirm=lambda: False)
    assert not res.ok and "not confirmed" in res.error and runner.calls == []
    assert any("jade/hooks.py" in s for s in said)


def test_apply_installs_after_a_yes(runner, web):
    web[COMPARE] = compare([f("jade/hooks.py")])
    res = upgrade.apply(TAG, say=lambda s: None, confirm=lambda: True)
    assert res.ok and runner.calls[0][0] == "uv"


def test_apply_without_a_confirm_asks_nothing_and_lists_nothing(runner, monkeypatch):
    monkeypatch.setattr(upgrade, "changed_files", lambda ref: pytest.fail("a caller with no confirm gets no listing"))
    assert upgrade.apply(TAG).ok


def test_the_list_is_printed_even_when_it_is_unavailable_and_the_yes_is_still_needed(runner, web):
    web[COMPARE] = upgrade.FetchError("offline", "x")
    said = []
    res = upgrade.apply(TAG, say=said.append, confirm=lambda: False)
    assert not res.ok and runner.calls == [] and any("cannot list" in s or "offline" in s for s in said)


# ------------------------------------------------------------------ the command

def test_the_command_needs_a_typed_yes(runner, web, monkeypatch, capsys):
    web[COMPARE] = compare([f("policies/safety.yaml")])
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert cli.main(["upgrade", "--ref", TAG]) == 0
    out = capsys.readouterr().out
    assert "policies/safety.yaml" in out and runner.calls[0][0] == "uv"


@pytest.mark.parametrize("typed", ["no", "", "y", "YES", "yes please"])
def test_anything_but_the_word_yes_stops_the_upgrade(runner, web, monkeypatch, capsys, typed):
    web[COMPARE] = compare([f("jade/hooks.py")])
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)
    assert cli.main(["upgrade", "--ref", TAG]) == 1
    assert runner.calls == [] and "not confirmed" in capsys.readouterr().err


def test_end_of_input_stops_the_upgrade(runner, web, monkeypatch, capsys):
    web[COMPARE] = compare([f("jade/hooks.py")])

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["upgrade", "--ref", TAG]) == 1 and runner.calls == []


def test_check_mode_lists_nothing_and_needs_no_yes(monkeypatch):
    monkeypatch.setattr(upgrade, "changed_files", lambda ref: pytest.fail("--check only looks"))
    monkeypatch.setattr(upgrade, "check", lambda: upgrade.CheckResult(True, "0.4.0", "0.4.0", "v0.4.0", "u", False))
    assert cli.main(["upgrade", "--check"]) == 0


def test_a_human_is_still_required(monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: "no terminal")
    monkeypatch.setattr(upgrade, "changed_files", lambda ref: pytest.fail("an agent gets no listing"))
    with pytest.raises(ops.HumanOnly):
        upgrade.apply(TAG, stdin_isatty=False, confirm=lambda: True)
