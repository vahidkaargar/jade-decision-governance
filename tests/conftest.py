import os
from pathlib import Path

import pytest

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
    for fn in (policy.reset_cache, questions.reset_cache, thresholds.escalation.cache_clear):
        fn()
    yield
