import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own JADE home and config, no overlay, no agent environment, and cold caches."""
    monkeypatch.setenv("JADE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JADE_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("JADE_CLAUDE_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("JADE_LAUNCH_AGENTS", str(tmp_path / "LaunchAgents"))   # never the real ~/Library/LaunchAgents
    monkeypatch.delenv("JADE_GATE_MODE", raising=False)
    monkeypatch.delenv("JADE_POLICY_DIR", raising=False)
    monkeypatch.delenv("JADE_ACK_OVERRIDES", raising=False)
    monkeypatch.delenv("JADE_SESSION_ID", raising=False)
    from jade import policy, questions, thresholds, util

    monkeypatch.setattr(util, "agent_ancestor", lambda: None)  # the test run itself may sit under an agent; tests of the real walk patch `ps`
    for fn in (policy.reset_cache, questions.reset_cache, thresholds.escalation.cache_clear):
        fn()
    yield
