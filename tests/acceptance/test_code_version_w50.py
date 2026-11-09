"""W-50: the running code's version and commit are in `jade check` and in policy_version; `jade check` warns when the installed tool is older than a named checkout."""
import json

import jade
from jade import ops, paths


def _rows():
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}


def _checkout(tmp_path, version):
    d = tmp_path / "checkout"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(f'[project]\nname = "jade-decision-governance"\nversion = "{version}"\n')
    return d


def test_code_version_is_the_package_version_plus_the_commit_when_there_is_one(monkeypatch):
    monkeypatch.setattr(paths, "code_commit", lambda: None)
    assert paths.code_version() == jade.__version__
    monkeypatch.setattr(paths, "code_commit", lambda: "abc123def456")
    assert paths.code_version() == f"{jade.__version__}+abc123def456"


def test_policy_version_changes_with_the_code_version(monkeypatch):
    monkeypatch.setattr(paths, "code_commit", lambda: None)
    before = paths.policy_version()
    assert paths.policy_version() == before and len(before) == 12
    monkeypatch.setattr(paths, "code_commit", lambda: "abc123def456")
    assert paths.policy_version() != before
    monkeypatch.setattr(paths, "code_commit", lambda: "fff123def456")
    assert paths.policy_version() != before
    monkeypatch.setattr(jade, "__version__", "9.9.9")
    monkeypatch.setattr(paths, "code_commit", lambda: None)
    assert paths.policy_version() != before


def test_code_commit_is_none_outside_a_checkout_and_a_hex_sha_inside_one(tmp_path):
    assert paths.git_commit(tmp_path) is None
    sha = paths.code_commit()   # this test suite runs from the checkout; an sdist has no .git and gets None
    assert sha is None or (len(sha) == 12 and all(c in "0123456789abcdef" for c in sha))


def test_check_shows_version_and_commit(monkeypatch):
    monkeypatch.setattr(paths, "code_commit", lambda: "abc123def456")
    ok, detail = _rows()["code version"]
    assert ok and jade.__version__ in detail and "abc123def456" in detail
    monkeypatch.setattr(paths, "code_commit", lambda: None)
    ok, detail = _rows()["code version"]
    assert ok and jade.__version__ in detail and "no git checkout" in detail


def test_the_policies_row_carries_the_policy_version_that_now_includes_the_code(monkeypatch):
    monkeypatch.setattr(paths, "code_commit", lambda: None)
    assert f"version {paths.policy_version()}" in _rows()["policies load"][1]


def test_without_a_named_checkout_the_row_is_informational(monkeypatch):
    monkeypatch.delenv("JADE_CHECKOUT", raising=False)
    ok, detail = _rows()["code version"]
    assert ok and "JADE_CHECKOUT" in detail


def test_installed_older_than_the_named_checkout_fails_the_row(monkeypatch, tmp_path):
    monkeypatch.setenv("JADE_CHECKOUT", str(_checkout(tmp_path, "99.0.0")))
    ok, detail = _rows()["code version"]
    assert not ok and "99.0.0" in detail and jade.__version__ in detail and "uv tool install" in detail
    assert ops.print_check(ops.check(live=False)) == 1


def test_the_checkout_can_be_named_in_config(monkeypatch, tmp_path):
    monkeypatch.delenv("JADE_CHECKOUT", raising=False)
    cfg = paths.config_dir()
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"checkout": str(_checkout(tmp_path, "99.0.0"))}))
    assert not _rows()["code version"][0]


def test_same_or_newer_than_the_checkout_is_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("JADE_CHECKOUT", str(_checkout(tmp_path, jade.__version__)))
    ok, detail = _rows()["code version"]
    assert ok and "matches" in detail
    monkeypatch.setenv("JADE_CHECKOUT", str(_checkout(tmp_path / "x", "0.0.1")))
    assert _rows()["code version"][0]


def test_an_unreadable_checkout_is_informational_not_a_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("JADE_CHECKOUT", str(tmp_path / "nope"))
    ok, detail = _rows()["code version"]
    assert ok and "cannot read" in detail
    d = tmp_path / "bad"
    d.mkdir()
    (d / "pyproject.toml").write_text("not = [toml")
    monkeypatch.setenv("JADE_CHECKOUT", str(d))
    assert _rows()["code version"][0]
    (d / "pyproject.toml").write_text('[project]\nversion = "weird"\n')
    assert _rows()["code version"][0]
