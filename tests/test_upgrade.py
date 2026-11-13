"""REL-1: `jade --version`, `jade version` and the human-run `jade upgrade`. No test touches the network or the real tool install: the HTTP layer
(`upgrade._http_get`) and the subprocess layer (`upgrade._run`, `subprocess.run`) are replaced."""

import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

import jade
from jade import cli, ops, paths, upgrade, util

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com/repos/vahidkaargar/jade-decision-governance/releases/latest"
RAW = "https://raw.githubusercontent.com/vahidkaargar/jade-decision-governance/{tag}/CHANGELOG.md"
CHANGELOG = """# Changelog

## [Unreleased]

### Added
- something not released

## [0.4.0] - 2026-12-01
text

## [0.3.1] - 2026-11-20

## [0.3.0] - 2026-11-15

## [0.2.2] - 2026-11-09

## [0.2.1] - 2026-11-01
"""


def release(tag="v0.3.1"):
    return json.dumps({"tag_name": tag, "html_url": "https://evil.example/x", "name": "ignored"}).encode()


@pytest.fixture
def web(monkeypatch):
    """A fake GitHub: {url: bytes | Exception}. Any other URL fails the test (no network)."""
    pages: dict = {}
    seen: list[str] = []

    def get(url, timeout=upgrade.HTTP_TIMEOUT_S, limit=upgrade.MAX_BYTES):
        seen.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected URL {url}")
        v = pages[url]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(upgrade, "_http_get", get)
    pages["seen"] = seen   # type: ignore[assignment]
    return pages


@pytest.fixture
def human(monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


@pytest.fixture
def no_state(tmp_path):
    """The ledger and the configuration are never touched by an upgrade: neither directory may exist afterwards."""
    yield
    assert not (tmp_path / "home").exists(), "upgrade created or opened the JADE home"
    assert not (tmp_path / "config").exists(), "upgrade created the JADE config directory"


# ---- version reporting ---------------------------------------------------------------------------------------------
def test_version_info_has_code_commit_schema_and_policy_version(monkeypatch, no_state):
    monkeypatch.setattr(paths, "code_commit", lambda: "abc123def456")
    v = upgrade.version_info()
    assert v["version"] == jade.__version__ and v["commit"] == "abc123def456" and v["commit_source"] == "checkout"
    assert v["schema_version"] == upgrade.LEDGER_SCHEMA_VERSION == "1.0.0"
    assert v["policy_version"] == paths.policy_version() and len(v["policy_version"]) == 12


def test_the_ledger_schema_version_is_the_one_every_shipped_record_carries():
    for f in sorted((ROOT / "schemas" / "examples").glob("*.json")):
        doc = json.loads(f.read_text())
        if "schema_version" in doc:
            assert doc["schema_version"] == upgrade.LEDGER_SCHEMA_VERSION, f.name


def test_commit_is_unknown_for_an_installed_package_without_provenance(monkeypatch):
    monkeypatch.setattr(paths, "code_commit", lambda: None)
    monkeypatch.setattr(upgrade, "_direct_url_commit", lambda: None)
    v = upgrade.version_info()
    assert v["commit"] is None and v["commit_source"] is None
    assert "commit: unknown" in upgrade.version_text(v)


def test_a_git_install_reports_the_commit_uv_recorded(monkeypatch):
    class Dist:
        def read_text(self, name):
            assert name == "direct_url.json"
            return json.dumps({"url": "https://github.com/x/y", "vcs_info": {"vcs": "git", "commit_id": "0123456789abcdef0123456789abcdef01234567"}})

    monkeypatch.setattr(paths, "code_commit", lambda: None)
    monkeypatch.setattr(upgrade.importlib.metadata, "distribution", lambda name: Dist())
    v = upgrade.version_info()
    assert v["commit"] == "0123456789ab" and v["commit_source"] == "install record"


@pytest.mark.parametrize("text", [None, "", "not json", "[]", "{}", '{"vcs_info": 1}', '{"vcs_info": {"commit_id": "zz"}}', '{"vcs_info": {"commit_id": "abc"}}',
                                  json.dumps({"vcs_info": {"commit_id": "g" * 40}})])
def test_a_missing_or_malformed_install_record_is_no_commit_and_never_raises(monkeypatch, text):
    class Dist:
        def read_text(self, name):
            return text

    monkeypatch.setattr(upgrade.importlib.metadata, "distribution", lambda name: Dist())
    assert upgrade._direct_url_commit() is None


def test_a_package_that_is_not_installed_is_no_commit(monkeypatch):
    def boom(name):
        raise upgrade.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(upgrade.importlib.metadata, "distribution", boom)
    assert upgrade._direct_url_commit() is None


def test_jade_version_command_prints_all_four_facts(monkeypatch, capsys, no_state):
    monkeypatch.setattr(paths, "code_commit", lambda: "abc123def456")
    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert f"jade {jade.__version__}" in out and "abc123def456" in out and "1.0.0" in out and paths.policy_version() in out


def test_jade_version_json_is_machine_readable(monkeypatch, capsys, no_state):
    monkeypatch.setattr(paths, "code_commit", lambda: "abc123def456")
    assert cli.main(["version", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["version"] == jade.__version__ and doc["commit"] == "abc123def456" and doc["schema_version"] == "1.0.0" and doc["policy_version"] == paths.policy_version()


def test_dash_dash_version_prints_the_same_report_and_exits_zero(monkeypatch, capsys, no_state):
    monkeypatch.setattr(paths, "code_commit", lambda: "abc123def456")
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out == upgrade.version_text(upgrade.version_info())
    assert out.startswith(f"jade {jade.__version__}\n")


def test_the_version_report_survives_a_broken_policy_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("JADE_POLICY_DIR", str(tmp_path / "missing"))
    v = upgrade.version_info()
    assert v["version"] == jade.__version__ and isinstance(v["policy_version"], str)


# ---- the HTTP layer ------------------------------------------------------------------------------------------------
class FakeResp:
    def __init__(self, body=b"{}", status=200):
        self.body, self.status = body, status

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, result):
        self.result, self.calls = result, []

    def open(self, req, timeout=None):
        self.calls.append((req.full_url, timeout, dict(req.header_items())))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def patch_opener(monkeypatch, result):
    op = FakeOpener(result)
    monkeypatch.setattr(upgrade.urllib.request, "build_opener", lambda *h: op)
    return op


def test_http_get_uses_https_a_five_second_timeout_and_no_credentials(monkeypatch):
    op = patch_opener(monkeypatch, FakeResp(b"hello"))
    assert upgrade._http_get(API) == b"hello"
    url, timeout, headers = op.calls[0]
    assert url == API and timeout == 5 and upgrade.HTTP_TIMEOUT_S == 5
    assert not {k.lower() for k in headers} & {"authorization", "cookie"}


@pytest.mark.parametrize("url", ["http://api.github.com/x", "file:///etc/passwd", "ftp://github.com/x", "//github.com/x", "github.com/x"])
def test_http_get_refuses_anything_but_https(monkeypatch, url):
    op = patch_opener(monkeypatch, FakeResp())
    with pytest.raises(upgrade.FetchError) as e:
        upgrade._http_get(url)
    assert e.value.kind == "bad_url" and not op.calls


@pytest.mark.parametrize("err,kind", [
    (urllib.error.URLError("no route"), "offline"),
    (TimeoutError("slow"), "timeout"),
    (OSError("reset"), "offline"),
    (urllib.error.HTTPError(API, 404, "nf", {}, io.BytesIO(b"")), "not_found"),
    (urllib.error.HTTPError(API, 403, "rate", {}, io.BytesIO(b"")), "rate_limited"),
    (urllib.error.HTTPError(API, 429, "rate", {}, io.BytesIO(b"")), "rate_limited"),
    (urllib.error.HTTPError(API, 500, "boom", {}, io.BytesIO(b"")), "http"),
])
def test_http_get_turns_every_failure_into_a_typed_error(monkeypatch, err, kind):
    patch_opener(monkeypatch, err)
    with pytest.raises(upgrade.FetchError) as e:
        upgrade._http_get(API)
    assert e.value.kind == kind


def test_a_url_error_wrapping_a_timeout_is_a_timeout(monkeypatch):
    patch_opener(monkeypatch, urllib.error.URLError(TimeoutError("t")))
    with pytest.raises(upgrade.FetchError) as e:
        upgrade._http_get(API)
    assert e.value.kind == "timeout"


def test_http_get_caps_the_size_of_what_it_reads(monkeypatch):
    patch_opener(monkeypatch, FakeResp(b"x" * 5000))
    with pytest.raises(upgrade.FetchError) as e:
        upgrade._http_get(API, limit=1000)
    assert e.value.kind == "too_large"


def test_redirects_must_stay_on_https_and_on_github():
    h = upgrade._SafeRedirect()
    req = upgrade.urllib.request.Request(API)
    ok = h.redirect_request(req, None, 301, "moved", {}, "https://api.github.com/repositories/1/releases/latest")
    assert ok is not None
    ok = h.redirect_request(req, None, 302, "moved", {}, "https://raw.githubusercontent.com/x/y/v1/CHANGELOG.md")
    assert ok is not None
    for target in ("http://api.github.com/x", "https://evil.example/x", "https://github.com.evil.example/x", "file:///etc/passwd", "https://api.github.com.evil.io/x"):
        with pytest.raises(urllib.error.URLError):
            h.redirect_request(req, None, 302, "moved", {}, target)


# ---- latest release ------------------------------------------------------------------------------------------------
def test_latest_release_reads_the_tag_and_ignores_every_other_field(web):
    web[API] = release("v0.3.1")
    r = upgrade.latest_release()
    assert r.ok and r.tag == "v0.3.1" and r.version == "0.3.1" and r.error_kind is None
    assert r.url == "https://github.com/vahidkaargar/jade-decision-governance/releases/tag/v0.3.1"   # built from the tag, not taken from the response


@pytest.mark.parametrize("tag", ["latest", "v1.2", "v1.2.3-rc1", "v1.2.3\x1b[31m", "v1.2.3; rm -rf /", "$(id)", "", "../v1.2.3", None, 7, "v1.2.3 "])
def test_a_tag_that_is_not_a_plain_version_is_rejected(web, tag):
    web[API] = json.dumps({"tag_name": tag}).encode()
    r = upgrade.latest_release()
    assert not r.ok and r.error_kind == "bad_response" and r.tag is None


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b"null", b"{}", b'{"tag_name": {"a": 1}}', b"\xff\xfe"])
def test_a_garbled_response_is_a_typed_failure(web, body):
    web[API] = body
    r = upgrade.latest_release()
    assert not r.ok and r.error_kind == "bad_response"


def test_offline_is_a_typed_failure_not_an_exception(web):
    web[API] = upgrade.FetchError("offline", "no route to host")
    r = upgrade.latest_release()
    assert not r.ok and r.error_kind == "offline" and "no route" in r.detail


def test_no_release_published_is_reported_as_such(web):
    web[API] = upgrade.FetchError("not_found", "HTTP 404")
    r = upgrade.latest_release()
    assert not r.ok and r.error_kind == "no_release"


def test_latest_release_never_raises_whatever_the_http_layer_does(web):
    web[API] = RuntimeError("boom")
    r = upgrade.latest_release()
    assert not r.ok and r.error_kind == "unexpected"


# ---- jade upgrade --check ------------------------------------------------------------------------------------------
def test_check_reports_installed_latest_and_the_headings_in_between(web, monkeypatch):
    monkeypatch.setattr(jade, "__version__", "0.2.2")
    web[API] = release("v0.3.1")
    web[RAW.format(tag="v0.3.1")] = CHANGELOG.encode()
    r = upgrade.check()
    assert r.ok and r.installed == "0.2.2" and r.latest == "0.3.1" and r.update_available
    assert r.changes == ["0.3.1 - 2026-11-20", "0.3.0 - 2026-11-15"]   # newest first, 0.4.0 is newer than the release, 0.2.2 is installed, Unreleased is not a release


def test_check_up_to_date_lists_nothing(web, monkeypatch):
    monkeypatch.setattr(jade, "__version__", "0.3.1")
    web[API] = release("v0.3.1")
    r = upgrade.check()
    assert r.ok and not r.update_available and r.changes == [] and RAW.format(tag="v0.3.1") not in web["seen"]


def test_check_when_installed_is_newer_than_the_release_is_not_an_update(web, monkeypatch):
    monkeypatch.setattr(jade, "__version__", "0.9.0")
    web[API] = release("v0.3.1")
    r = upgrade.check()
    assert r.ok and not r.update_available


def test_a_changelog_that_cannot_be_fetched_does_not_fail_the_check(web, monkeypatch):
    monkeypatch.setattr(jade, "__version__", "0.2.2")
    web[API] = release("v0.3.1")
    web[RAW.format(tag="v0.3.1")] = upgrade.FetchError("offline", "x")
    r = upgrade.check()
    assert r.ok and r.update_available and r.changes == [] and r.changes_error == "offline"


def test_changelog_headings_are_reduced_to_a_version_and_a_date(web, monkeypatch):
    monkeypatch.setattr(jade, "__version__", "0.2.2")
    web[API] = release("v0.3.1")
    web[RAW.format(tag="v0.3.1")] = b"## [0.3.0] - 2026-11-15\n## [0.2.5] - \x1b[31mred\x1b[0m rm -rf\n## [0.2.4] - 2026-11-14 extra words\n"
    r = upgrade.check()
    assert r.changes == ["0.3.0 - 2026-11-15", "0.2.5", "0.2.4 - 2026-11-14"]
    assert all("\x1b" not in c and "rm" not in c and "extra" not in c for c in r.changes)


def test_check_failure_is_typed(web):
    web[API] = upgrade.FetchError("offline", "no route")
    r = upgrade.check()
    assert not r.ok and r.error_kind == "offline" and r.update_available is False


def test_cli_check_prints_the_report_and_needs_no_terminal(web, monkeypatch, capsys, no_state):
    monkeypatch.setattr(jade, "__version__", "0.2.2")
    monkeypatch.setattr(paths, "code_commit", lambda: "abc123def456")
    web[API] = release("v0.3.1")
    web[RAW.format(tag="v0.3.1")] = CHANGELOG.encode()
    assert cli.main(["upgrade", "--check"]) == 0   # pytest's stdin is not a terminal, and this is read-only
    out = capsys.readouterr().out
    assert "installed: 0.2.2" in out and "latest:    0.3.1" in out and "## [0.3.1] - 2026-11-20" in out and "## [0.3.0] - 2026-11-15" in out
    assert "jade upgrade" in out and "evil.example" not in out


def test_cli_check_offline_exits_one_with_a_message(web, capsys, no_state):
    web[API] = upgrade.FetchError("offline", "no route to host")
    assert cli.main(["upgrade", "--check"]) == 1
    cap = capsys.readouterr()
    assert "offline" in cap.err and "Traceback" not in cap.err + cap.out


def test_cli_check_and_ref_are_exclusive():
    with pytest.raises(SystemExit) as e:
        cli.main(["upgrade", "--check", "--ref", "v0.3.0"])
    assert e.value.code == 2


# ---- refs ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ref", ["v0.3.0", "0.3.0", "v10.20.30", "1.2.3", "a" * 40, "0123456789abcdef0123456789abcdef01234567"])
def test_valid_refs(ref):
    assert upgrade.validate_ref(ref) == (f"v{ref}" if ref[0].isdigit() and len(ref) != 40 else ref)


def test_a_bare_version_is_read_as_the_release_tag():
    """Release tags are vX.Y.Z (the release workflow runs on v*): `--ref 0.3.0` names the tag v0.3.0, it is not passed to uv as a ref that does not exist."""
    assert upgrade.validate_ref("0.3.0") == "v0.3.0" and upgrade.validate_ref("v0.3.0") == "v0.3.0"
    assert upgrade.install_argv("0.3.0")[-1].endswith("@v0.3.0")
    assert upgrade.validate_ref("1234567890123456789012345678901234567890") == "1234567890123456789012345678901234567890"   # a 40-digit commit is not a version


@pytest.mark.parametrize("ref", ["main", "master", "HEAD", "v1", "v1.2", "v1.2.3.4", "v1.2.3-rc1", "v1.2.3\n", " v1.2.3", "v1.2.3 ", "$(id)", "v1.2.3;id",
                                 "--upgrade", "-e", "../x", "A" * 40, "a" * 39, "a" * 41, "g" * 40, "", "v1.2.x", "vv1.2.3", "ｖ1.2.3", "v1.2.3@evil", "evil@v1.2.3"])
def test_invalid_refs(ref):
    with pytest.raises(upgrade.UpgradeError):
        upgrade.validate_ref(ref)


def test_the_install_command_is_a_fixed_argv_list_to_the_constant_repository():
    argv = upgrade.install_argv("v0.3.0")
    assert argv == ["uv", "tool", "install", "--reinstall", "git+https://github.com/vahidkaargar/jade-decision-governance@v0.3.0"]
    assert upgrade.REPO_URL == "https://github.com/vahidkaargar/jade-decision-governance" and upgrade.REPO_URL.startswith("https://")
    with pytest.raises(upgrade.UpgradeError):
        upgrade.install_argv("main")


def test_run_never_uses_a_shell(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, "out", "err")

    monkeypatch.setattr(upgrade.subprocess, "run", fake)
    rc, out = upgrade._run(["uv", "tool", "install", "x"], timeout=30)
    assert rc == 0 and isinstance(seen["argv"], list) and not seen["kw"].get("shell") and seen["kw"]["timeout"] == 30


# ---- jade upgrade (human only) -------------------------------------------------------------------------------------
class Runner:
    """Replaces upgrade._run: records argv, answers per program."""

    def __init__(self, install=(0, ""), check=(0, "ok    hooks installed: all four\nok    pretool matcher: covers every gated tool\n")):
        self.calls: list[list[str]] = []
        self.install, self.check = install, check

    def __call__(self, argv, timeout=600, capture=False):
        self.calls.append(list(argv))
        out = self.install if argv[0] == "uv" else self.check
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def runner(monkeypatch, human):
    r = Runner()
    monkeypatch.setenv("JADE_BIN", "/opt/jade-new/bin/jade")
    monkeypatch.setattr(upgrade, "_run", r)
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(upgrade, "changed_files", lambda ref: ([], "stubbed: no network in a test"))   # W-100d: the command lists the changed files, then needs a typed yes
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    return r


def test_apply_refuses_without_a_terminal_before_doing_anything(monkeypatch, no_state):
    called = []
    monkeypatch.setattr(upgrade, "_run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(upgrade, "_http_get", lambda *a, **k: called.append(a))
    with pytest.raises(ops.HumanOnly, match="human"):
        upgrade.apply("v0.3.0", stdin_isatty=False)
    assert not called


def test_apply_refuses_for_an_agent_ancestor_even_on_a_terminal(monkeypatch, no_state):
    called = []
    monkeypatch.setattr(upgrade, "_run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(util, "agent_env_present", lambda: False)
    monkeypatch.setattr(util, "agent_ancestor", lambda: "an agent process (claude) is an ancestor")
    with pytest.raises(ops.HumanOnly, match="agent"):
        upgrade.apply("v0.3.0", stdin_isatty=True)
    assert not called


def test_apply_refuses_when_an_agent_environment_is_present(monkeypatch, no_state):
    called = []
    monkeypatch.setattr(upgrade, "_run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(util, "agent_env_present", lambda: True)
    with pytest.raises(ops.HumanOnly):
        upgrade.apply("v0.3.0", stdin_isatty=True)
    assert not called


def test_cli_upgrade_refuses_in_this_non_interactive_run(monkeypatch, capsys, no_state):
    called = []
    monkeypatch.setattr(upgrade, "_run", lambda *a, **k: called.append(a))
    assert cli.main(["upgrade", "--ref", "v0.3.0"]) == 1
    assert "human" in capsys.readouterr().err and not called


def test_apply_with_a_ref_installs_that_ref_and_runs_jade_check_from_the_new_install(runner, web, no_state):
    res = upgrade.apply("v0.3.0")
    assert res.ok and res.ref == "v0.3.0" and res.install_rc == 0 and res.check_rc == 0
    assert runner.calls[0] == ["uv", "tool", "install", "--reinstall", "git+https://github.com/vahidkaargar/jade-decision-governance@v0.3.0"]
    assert runner.calls[1] == ["/opt/jade-new/bin/jade", "check", "--offline"]
    assert len(runner.calls) == 2 and web["seen"] == []   # a named ref needs no network call of ours
    assert not res.reinstall_hooks


@pytest.mark.parametrize("row", ["hooks installed", "hook wiring", "pretool matcher"])
def test_a_failing_hook_row_tells_the_human_to_re_run_hooks_install(runner, row, no_state):
    runner.check = (1, f"ok    ledger writable: x\nFAIL  {row}: missing: run `jade hooks install` in a terminal\njade check: 1 failed ({row})\n")
    res = upgrade.apply("0.3.0")
    assert res.ref == "v0.3.0" and runner.calls[0][-1].endswith("@v0.3.0")
    assert res.ok and res.reinstall_hooks and res.failed_rows == [row]
    assert any("re-run `jade hooks install`" in s for s in res.next_steps) and any("new Claude Code session" in s for s in res.next_steps)


def test_a_failing_row_that_is_not_about_hooks_is_reported_but_does_not_ask_for_a_reinstall(runner, no_state):
    runner.check = (1, "FAIL  ledger writable: boom\nok    hooks installed: all four\n")
    res = upgrade.apply("v0.3.0")
    assert res.ok and not res.reinstall_hooks and res.failed_rows == ["ledger writable"] and res.check_rc == 1
    assert any("ledger writable" in s for s in res.next_steps)


def test_a_clean_check_says_no_hook_change_is_needed(runner, no_state):
    res = upgrade.apply("v0.3.0")
    assert not res.reinstall_hooks and any("no change" in s for s in res.next_steps)


def test_a_failed_install_is_reported_and_jade_check_is_not_run(runner, no_state):
    runner.install = (2, "error: no such tag")
    res = upgrade.apply("v9.9.9")
    assert not res.ok and res.install_rc == 2 and res.check_rc is None and len(runner.calls) == 1 and "uv" in (res.error or "")


@pytest.mark.parametrize("exc,word", [(FileNotFoundError("uv"), "uv"), (subprocess.TimeoutExpired(["uv"], 600), "timed out"), (PermissionError("no"), "uv")])
def test_an_install_that_cannot_run_is_a_typed_failure(runner, exc, word, no_state):
    runner.install = exc
    res = upgrade.apply("v0.3.0")
    assert not res.ok and word in (res.error or "") and res.check_rc is None


def test_a_missing_uv_is_reported_before_anything_runs(runner, monkeypatch, no_state):
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: None)
    res = upgrade.apply("v0.3.0")
    assert not res.ok and "uv" in res.error and not runner.calls


def test_a_check_that_cannot_run_after_a_good_install_is_reported(runner, no_state):
    runner.check = FileNotFoundError("jade")
    res = upgrade.apply("v0.3.0")
    assert res.install_rc == 0 and res.check_rc is None and not res.ok and "jade check" in res.error and any("jade check" in s for s in res.next_steps)


def test_apply_rejects_a_bad_ref_before_running_anything(runner, no_state):
    with pytest.raises(upgrade.UpgradeError):
        upgrade.apply("main")
    assert not runner.calls


def test_apply_without_a_ref_installs_the_latest_release(runner, web, monkeypatch, no_state):
    monkeypatch.setattr(jade, "__version__", "0.2.2")
    web[API] = release("v0.3.1")
    res = upgrade.apply(None)
    assert res.ok and res.ref == "v0.3.1" and runner.calls[0][-1].endswith("@v0.3.1")


def test_apply_without_a_ref_is_a_no_op_when_already_on_the_latest(runner, web, monkeypatch, no_state):
    monkeypatch.setattr(jade, "__version__", "0.3.1")
    web[API] = release("v0.3.1")
    res = upgrade.apply(None)
    assert res.ok and res.install_rc is None and not runner.calls and "up to date" in " ".join(res.next_steps)


def test_apply_without_a_ref_and_without_a_network_installs_nothing(runner, web, no_state):
    web[API] = upgrade.FetchError("offline", "no route")
    res = upgrade.apply(None)
    assert not res.ok and "--ref" in res.error and not runner.calls


def test_an_older_ref_is_allowed_as_a_rollback_and_says_so(runner, monkeypatch, no_state):
    monkeypatch.setattr(jade, "__version__", "0.3.1")
    res = upgrade.apply("v0.2.2")
    assert res.ok and any("older" in s for s in res.next_steps) and runner.calls


def test_cli_upgrade_runs_and_prints_the_next_steps(runner, capsys, no_state):
    runner.check = (1, "FAIL  pretool matcher: x\n")
    assert cli.main(["upgrade", "--ref", "v0.3.0"]) == 0
    out = capsys.readouterr().out
    assert "v0.3.0" in out and "jade hooks install" in out


def test_cli_upgrade_exit_code_is_one_when_the_install_fails(runner, capsys, no_state):
    runner.install = (1, "")
    assert cli.main(["upgrade", "--ref", "v0.3.0"]) == 1
    assert "jade:" in capsys.readouterr().err


def test_cli_upgrade_rejects_a_bad_ref_with_a_message_and_runs_nothing(runner, capsys, no_state):
    assert cli.main(["upgrade", "--ref", "main"]) == 1
    assert "ref" in capsys.readouterr().err and not runner.calls


def test_upgrade_module_never_opens_the_ledger_or_reads_the_config():
    src = (ROOT / "jade" / "upgrade.py").read_text()
    for needle in ("import ledger", "ledger.open", "set_gate_mode", "atomic_write", "config_dir", "paths.home", "paths.config(", "shell=True", "os.system", "eval(", "exec("):
        assert needle not in src, needle
