import pytest


@pytest.fixture
def agent_env(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")


@pytest.fixture
def human_env(monkeypatch):
    from jade.util import AGENT_ENV

    for k in AGENT_ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def jade_stub(tmp_path, monkeypatch):
    """A `jade` and a `jev` in a throwaway bin directory that run this checkout (never the installed tool), with JADE_BIN and PATH pointing at them."""
    import sys

    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    jade = bin_dir / "jade"
    jade.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m jade.cli "$@"\n')
    jev = bin_dir / "jev"
    jev.write_text("#!/bin/sh\n# jade shim\nexec jade ask \"$@\"\n")
    for f in (jade, jev):
        f.chmod(0o755)
    monkeypatch.setenv("JADE_BIN", str(jade))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    # the test run itself redirects JADE_HOME and JADE_CONFIG: say so, as a human would for a deliberate override
    monkeypatch.setenv("JADE_ACK_OVERRIDES", "JADE_HOME,JADE_CONFIG,JADE_CLAUDE_SETTINGS,JADE_BIN,JADE_APPROVER_ANCHOR")
    return bin_dir
