"""W-99 (docs/12 plan v3, PR-4): the level, SI-12 and the SI-09 / SI-11 additions.

The level (`off | low | medium | high`, or unset) is read from the config file only: no environment variable sets it, and a bad value means unset.
Only a human terminal changes it (`jade level`). Until a level is set JADE behaves like 0.4.0. Once one is set, the governance invariants
(SI-09 to SI-12) block in advisory gate mode too, and SI-12 (the agent never writes the JADE config directory) applies at every level, `off`
included.
"""

import json
from pathlib import Path

import pytest

from jade import hooks, ledger, ops, paths, policy, util

ROOT = Path(__file__).resolve().parents[2]
SESSION = "sess-w99-0001"
REAL_REFUSAL = util.human_tty_refusal


def call(tool, tool_input):
    return {"tool_name": tool, "tool_input": tool_input, "session_id": SESSION, "cwd": str(ROOT)}


def bash(cmd):
    return call("Bash", {"command": cmd})


def set_cfg(**kw):
    cfg = paths.config()
    cfg.update(kw)
    (paths.config_dir()).mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


@pytest.fixture(autouse=True)
def human(monkeypatch):
    """The ancestry check of a human terminal sees the agent that runs this suite; the refusal paths are tested with stdin_isatty=False."""
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


@pytest.fixture
def cfgdir():
    return paths.config_dir()


# ------------------------------------------------------------------ the value

def test_level_is_unset_by_default():
    assert paths.level() is None


@pytest.mark.parametrize("v", ["off", "low", "medium", "high"])
def test_level_reads_the_config(v):
    set_cfg(level=v)
    assert paths.level() == v


@pytest.mark.parametrize("v", ["HIGH", "extreme", "", None, 3, ["high"]])
def test_a_bad_level_means_unset(v):
    set_cfg(level=v)
    assert paths.level() is None


def test_no_environment_variable_sets_the_level(monkeypatch):
    for name in ("JADE_LEVEL", "JADE_GATE_LEVEL", "JADE_AUTONOMY", "JADE_LEVELS"):
        monkeypatch.setenv(name, "off")
    assert paths.level() is None


# ------------------------------------------------------------------ `jade level`

def test_set_level_refuses_a_non_human_terminal(monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", REAL_REFUSAL)
    with pytest.raises(ops.HumanOnly):
        ops.set_level("high", stdin_isatty=False)
    assert paths.level() is None


def test_set_level_writes_the_config_and_journals_an_event():
    ops.set_level("high", stdin_isatty=True)
    assert paths.level() == "high"
    db = ledger.open(timeout=5)
    try:
        rows = db.con.execute("SELECT kind, guard FROM events WHERE kind='level'").fetchall()
    finally:
        db.close()
    assert len(rows) == 1


def test_unset_removes_the_key_and_off_is_a_level():
    ops.set_level("high", stdin_isatty=True)
    ops.set_level("unset", stdin_isatty=True)
    assert paths.level() is None and "level" not in paths.config()
    ops.set_level("off", stdin_isatty=True)
    assert paths.level() == "off"


@pytest.mark.parametrize("v", ["extreme", "", "HIGH", "Medium"])
def test_only_the_five_levels_are_accepted(v):
    with pytest.raises(ValueError):
        ops.set_level(v, stdin_isatty=True)


@pytest.mark.parametrize("v", ["unset", "off", "low", "medium", "high"])
def test_each_of_the_five_levels_can_be_set(v):
    ops.set_level(v, stdin_isatty=True)
    assert paths.level() == (None if v == "unset" else v)


def test_high_is_refused_when_the_config_holds_a_level_no_jade_level_recorded():
    set_cfg(level="off")   # written behind JADE's back: no `level` event
    with pytest.raises(ops.HumanOnly):
        ops.set_level("high", stdin_isatty=True)
    ops.set_level("unset", stdin_isatty=True)   # the human can always clear it
    ops.set_level("high", stdin_isatty=True)
    assert paths.level() == "high"


def test_the_level_row_of_jade_check_says_what_is_in_force():
    name, ok, detail = ops._level_row()
    assert name == "level" and ok and "unset" in detail
    ops.set_level("high", stdin_isatty=True)
    name, ok, detail = ops._level_row()
    assert ok and "high" in detail
    set_cfg(level="off")   # not recorded by a `jade level` event
    name, ok, detail = ops._level_row()
    assert not ok and "no `jade level`" in detail


# ------------------------------------------------------------------ SI-12: the config directory

def sim_paths(cfg):
    return [f"{cfg}/config.json", f"{cfg}/knowledge.md", f"{cfg}/local/risk.local.yaml"]


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_si12_blocks_a_tool_write_into_the_config_dir(tool, cfgdir):
    set_cfg(level="high")
    for p in sim_paths(cfgdir):
        a = policy.assess(tool, {"file_path": p, "content": "x", "new_string": "x", "old_string": "y"}, str(ROOT))
        assert "SI-12" in a.blocked, (tool, p)


def test_si12_blocks_a_symlinked_route_into_the_config_dir(cfgdir, tmp_path):
    set_cfg(level="high")
    link = tmp_path / "alias"
    link.symlink_to(cfgdir)
    a = policy.assess("Write", {"file_path": str(link / "config.json"), "content": "x"}, str(ROOT))
    assert "SI-12" in a.blocked


@pytest.mark.parametrize("cmd", [
    "echo x > {c}/config.json", "echo x >> {c}/knowledge.md", "printf x | tee {c}/config.json", "cp /tmp/x {c}/config.json", "cp -r /tmp/d {c}",
    "mv /tmp/x {c}/config.json", "mv {c} /tmp/elsewhere", "ln -sfn /tmp/x {c}", "rm -rf {c}", "rm -rf {c}/local", "install -m 600 /tmp/x {c}/config.json",
    "sed -i '' s/a/b/ {c}/config.json", "rsync -a /tmp/d/ {c}/", "python3 -c \"open('{c}/config.json','w').write('x')\"", "dd if=/dev/zero of={c}/config.json",
    "truncate -s 0 {c}/config.json", "cd {c} && echo x > config.json", "rm -rf {p}", "mv {p} {p}.old",
])
def test_si12_blocks_a_shell_write_into_the_config_dir(cmd, cfgdir):
    set_cfg(level="high")
    a = policy.assess("Bash", {"command": cmd.format(c=cfgdir, p=cfgdir.parent)}, str(ROOT))
    assert "SI-12" in a.blocked, cmd


@pytest.mark.parametrize("cmd", [
    "cat {c}/config.json", "ls {c}", "ls -la {c}/local", "grep -n level {c}/config.json", "cp {c}/config.json /tmp/copy.json", "head -3 {c}/knowledge.md",
    "cd {c}", "git -C {c} status", "jade check", "echo hello", "cp /tmp/x {p}/", "mkdir -p {p}",
])
def test_si12_leaves_reads_and_neighbours_alone(cmd, cfgdir):
    set_cfg(level="high")
    a = policy.assess("Bash", {"command": cmd.format(c=cfgdir, p=cfgdir.parent)}, str(ROOT))
    assert "SI-12" not in a.blocked, cmd


def test_si12_applies_when_the_level_is_off_and_not_when_it_is_unset(cfgdir):
    cmd = {"command": f"echo x > {cfgdir}/config.json"}
    assert "SI-12" not in policy.assess("Bash", cmd, str(ROOT)).blocked   # unset: 0.4.0 behaviour (R-13 gates the real ~/.config/jade)
    set_cfg(level="off")
    assert "SI-12" in policy.assess("Bash", cmd, str(ROOT)).blocked


def test_a_pretool_write_to_the_config_dir_exits_2_even_in_advisory_mode(monkeypatch, cfgdir):
    set_cfg(level="off")
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool(bash(f"echo x > {cfgdir}/config.json"))
    assert r.exit_code == 2 and "SI-12" in (r.stderr or "")


# ------------------------------------------------------------------ SI-09 and SI-11 additions

BLOCKED_COMMANDS = [
    "jade level high", "jade level off", "jade level unset", "jade level medium", "jade  level  low", "~/.local/bin/jade level high",
    "uv run jade level high", "python -m jade.cli level high", "$J level high", "jade -- level high",
    "jade knows edit", "jade knows rm k1", "jade knows allow x", "jade knows deny x",
    "jade review mark d1 right", "jade review confirm", "jade review confirm --all",
    "jade hook pretool", "jade hook posttool", "echo '{}' | jade hook posttool", "$J hook posttool", "python -m jade.cli hook posttool",
    "python3 -c 'from jade import hooks'", "python3 -c 'import jade.hooks'", "python3 -c 'import jade.proxy'", "python3 -c 'from jade import memory'",
    "JADE_REGISTRY_DIR=/tmp/x jade check",
]


@pytest.mark.parametrize("cmd", BLOCKED_COMMANDS)
def test_governance_commands_are_si09_or_si11(cmd):
    a = policy.assess("Bash", {"command": cmd}, str(ROOT))
    assert set(a.blocked) & {"SI-09", "SI-11"}, cmd


ALLOWED_COMMANDS = [
    "jade level", "jade level --help", "jade review", "jade review --since 24h", "jade knows list", "jade knows --help", "jade check",
    "echo 'run jade level high yourself'", "git commit -m 'document jade hook posttool'", "grep -rn 'jade level' docs/", "pytest -k level",
    "cat jade/hooks.py", "git log --oneline -- jade/hooks.py",
]


@pytest.mark.parametrize("cmd", ALLOWED_COMMANDS)
def test_reading_commands_and_mentions_are_not_blocked(cmd):
    a = policy.assess("Bash", {"command": cmd}, str(ROOT))
    assert not (set(a.blocked) & {"SI-09", "SI-11", "SI-12"}), (cmd, a.blocked)


# ------------------------------------------------------------------ the hook: governance invariants are never advisory once a level is set

def test_si09_is_advisory_while_no_level_is_set(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool(bash("jade level high"))
    assert r.exit_code == 0


@pytest.mark.parametrize("level", ["off", "high"])
def test_si09_blocks_in_advisory_mode_once_a_level_is_set(monkeypatch, level):
    set_cfg(level=level)
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool(bash("jade level unset"))
    assert r.exit_code == 2 and "SI-09" in (r.stderr or "")


def test_si11_blocks_in_advisory_mode_once_a_level_is_set(monkeypatch):
    set_cfg(level="off")
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    assert hooks.pretool(bash("JADE_GATE_MODE=off jade check")).exit_code == 2


def test_other_invariants_stay_advisory_at_level_off(monkeypatch):
    set_cfg(level="off")
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool(bash("git push origin main"))
    assert r.exit_code == 0 and "advisory" in (r.stderr or "")


def test_an_enforced_invariant_block_is_unchanged_at_a_level():
    set_cfg(level="high")
    assert hooks.pretool(bash("git push origin main")).exit_code == 2
