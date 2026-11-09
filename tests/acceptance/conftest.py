import pytest


@pytest.fixture
def agent_env(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")


@pytest.fixture
def human_env(monkeypatch):
    from jade.util import AGENT_ENV

    for k in AGENT_ENV:
        monkeypatch.delenv(k, raising=False)
