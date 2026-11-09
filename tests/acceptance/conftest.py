import pytest


@pytest.fixture
def agent_env(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")


@pytest.fixture
def human_env(monkeypatch):
    for k in ("CLAUDECODE", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CURSOR_AGENT", "CODEX_SANDBOX"):
        monkeypatch.delenv(k, raising=False)
