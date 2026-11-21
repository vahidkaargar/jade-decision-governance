import os
from pathlib import Path

import pytest

SUBPROCESS_APPROVE_MODULES = {   # W-93: these run the hook or CLI in a subprocess and approve high permits; a subprocess cannot be patched, so an installed real anchor would flip them to the signed backend
    "test_gate_failure_modes", "test_hardening1_review_fixes", "test_phase2_enforce", "test_signed_approval_w89", "test_gate_canonicalisation",
    "test_permit_ancestry_and_si09_anchoring", "test_git_untrusted_repo_w60", "test_hook_payloads_w40", "test_gate_false_positives",
    "test_permits_lifecycle_pass2", "test_permits_lifecycle"}


def real_anchor_installed() -> bool:
    from jade import signing

    return os.path.lexists(signing.DEFAULT_ANCHOR)


def _refuse_to_skip() -> None:
    if os.environ.get("JADE_REQUIRE_APPROVE_MODULES") == "1" and real_anchor_installed():   # W-201: CI sets this; a skipped module there would be a silent hole, so the run stops
        raise pytest.UsageError("W-201: JADE_REQUIRE_APPROVE_MODULES=1 and the real approver trust anchor is installed, so the W-93 modules would be skipped; run on a host without the anchor")


def pytest_sessionstart(session):
    """W-201: stop at the start of the session, in the main process. Under pytest-xdist (CI runs `-n auto`) a UsageError raised while a worker collects
    comes out as an INTERNALERROR with exit code 3 and the message is lost; raised here it ends the run before any worker starts, with exit code 4 and the message."""
    _refuse_to_skip()


def pytest_collection_modifyitems(config, items):
    """W-93: on a machine where the approver is installed (W-90) the default anchor always wins over the test override, so the subprocess-based approval tests
    are skipped there; CI and any machine without the anchor run them. In-process tests are covered by the `isolated` fixture below."""
    if not real_anchor_installed():
        return
    _refuse_to_skip()   # also here: a conftest that is loaded late does not get pytest_sessionstart
    skip = pytest.mark.skip(reason="W-93: the real approver trust anchor is installed; this module approves permits in a subprocess that would use it (run it in CI or after --uninstall)")
    for item in items:
        if item.module.__name__.rsplit(".", 1)[-1] in SUBPROCESS_APPROVE_MODULES:
            item.add_marker(skip)


LIVE_HOME = (Path.home() / ".local" / "share" / "jade").resolve()   # read once, before any test moves HOME (W-72)


def _is_live(p) -> bool:
    try:
        q = Path(p).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return q == LIVE_HOME or LIVE_HOME in q.parents


@pytest.fixture(autouse=True)
def never_the_live_ledger(monkeypatch):
    """W-72: a test that reaches the real JADE home (no JADE_HOME, or one that names it) fails before anything is created, opened or migrated.
    The check runs ahead of `paths.home()` and `Ledger()`, so not even the directory mode or the schema of the live ledger is touched."""
    from jade import ledger, paths

    real_home, real_init = paths.home, ledger.Ledger.__init__

    def refuse(what: str):
        pytest.fail(f"W-72: a test reached the live JADE home ({what}); set JADE_HOME to a temporary directory (tests/conftest.py does by default)", pytrace=False)

    def guarded_home():
        if _is_live(os.environ.get("JADE_HOME") or LIVE_HOME):
            refuse("paths.home()")
        return real_home()

    def guarded_init(self, path, *a, **kw):
        if _is_live(path):
            refuse(str(path))
        return real_init(self, path, *a, **kw)

    monkeypatch.setattr(paths, "home", guarded_home)
    monkeypatch.setattr(ledger.Ledger, "__init__", guarded_init)
    yield


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own JADE home and config, no overlay, no agent environment, and cold caches."""
    monkeypatch.setenv("JADE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JADE_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("JADE_CLAUDE_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("JADE_LAUNCH_AGENTS", str(tmp_path / "LaunchAgents"))   # never the real ~/Library/LaunchAgents
    monkeypatch.setenv("JADE_MANAGED_SETTINGS", str(tmp_path / "managed-settings.json"))   # never the real /Library/Application Support/ClaudeCode
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(tmp_path / "no-approver-anchor"))   # W-89: never the real root-owned trust anchor, so a machine that has one still runs the tty-approval tests
    from jade import signing as _signing

    monkeypatch.setattr(_signing, "DEFAULT_ANCHOR", tmp_path / "no-default-anchor")   # W-93: in-process tests never see a real installed anchor
    from jade import askdialog as _askdialog

    monkeypatch.setattr(_askdialog, "available", lambda: False)   # W-100c: no test ever shows a real macOS dialog; the dialog tests switch it on and stub `run_dialog`
    monkeypatch.delenv("JADE_GATE_MODE", raising=False)
    monkeypatch.delenv("JADE_SOURCE", raising=False)
    monkeypatch.delenv("JADE_POLICY_DIR", raising=False)
    monkeypatch.delenv("JADE_CHECKOUT", raising=False)
    monkeypatch.delenv("JADE_ACK_OVERRIDES", raising=False)
    monkeypatch.delenv("JADE_SESSION_ID", raising=False)
    from jade import paths, policy, questions, thresholds, util

    monkeypatch.setattr(paths, "checkout_overlay_dir", lambda: tmp_path / "checkout-local")   # never this checkout's private policies/local (W-56)
    monkeypatch.setattr(util, "agent_ancestor", lambda: None)  # the test run itself may sit under an agent; tests of the real walk patch `ps`
    monkeypatch.setattr(util, "tty_session_refusal", lambda *a, **k: None)  # nor is the test run led by an interactive shell on a terminal (W-37); tests of the real check keep the original
    monkeypatch.setattr(policy, "_shell_roots", lambda: ())   # PRE-12: no test reads the real ~/.zshrc, /etc/zshrc or what they source; test_shell_startup_human_only opts in with a temporary HOME
    for fn in (policy.reset_cache, questions.reset_cache, thresholds.escalation.cache_clear):
        fn()
    yield
