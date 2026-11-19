"""V6-28 (docs/14-v6 d05 section 3.3, d04 ADR-v6-03 point 6): the `jev` key.

Autonomy is `(level, jev)`. `jev` says whether a model may be asked at all. It is `on` or `off`. The effective value follows one rule, written once:

- a bad value (`maybe`, `ON`, a number) reads `off`;
- the value comes from the last `jev` ledger event and the config file together: `on` only when both say `on`, so deleting the key, or an edit that says `on`
  after the human said `off`, never raises autonomy (N29, N30, N31);
- with no key and no event (an upgrade, a fresh config) the default is `on` at level `low` or `medium` (nothing changes) and `off` at every other level (at `high`
  this stops the shadow call that sends command text to the model);
- no environment variable moves it (N33); only a human terminal changes it (SI-09, N28).

The ledger rule has the same strength as the one for `level`: the ledger is a same-user file.
"""

import json
from pathlib import Path

import pytest

from jade import cli, ledger, ops, paths, policy, util

ROOT = Path(__file__).resolve().parents[2]
REAL_REFUSAL = util.human_tty_refusal
MISSING = object()


@pytest.fixture(autouse=True)
def human(monkeypatch):
    """The ancestry check of a human terminal sees the agent that runs this suite; the refusal paths are tested with stdin_isatty=False."""
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


def set_cfg(**kw):
    cfg = paths.config()
    for k, v in kw.items():
        if v is MISSING:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


def record(value, kind="jev"):
    """An event as `jade jev` writes it (the key stays out of the event when `value` is MISSING)."""
    db = ledger.open(timeout=5)
    try:
        db.event("jev", kind, guard="test", actor="human:test", detail=None if value is MISSING else {"jev": value})
    finally:
        db.close()


# ------------------------------------------------------------------ the effective value: one table

# (level, config key, last event, effective). MISSING for the key means no key; MISSING for the event means no event.
TABLE = [
    # the default: no key and no event
    ("medium", MISSING, MISSING, "on"), ("low", MISSING, MISSING, "on"),
    ("high", MISSING, MISSING, "off"), ("off", MISSING, MISSING, "off"), (None, MISSING, MISSING, "off"),
    # an explicit key the human wrote with the command
    ("medium", "on", "on", "on"), ("high", "on", "on", "on"), ("low", "on", "on", "on"),
    ("medium", "off", "off", "off"), ("high", "off", "off", "off"),
    # `off` is safe even when no command recorded it (a new install writes it so)
    ("medium", "off", MISSING, "off"), ("low", "off", MISSING, "off"),
    # `on` that no command recorded is not believed
    ("medium", "on", MISSING, "off"), ("high", "on", MISSING, "off"),
    # N29: the last event says off, the key is deleted
    ("medium", MISSING, "off", "off"), ("low", MISSING, "off", "off"),
    # the last event says on, the key is deleted: a changed file, not the human
    ("medium", MISSING, "on", "off"),
    # N30: the config says on, the last event says off
    ("medium", "on", "off", "off"), ("low", "on", "off", "off"),
    # the config says off, the last event says on: the file wins, the tighter value
    ("medium", "off", "on", "off"),
    # N31: a bad value reads off, at any level, with any event
    ("medium", "maybe", MISSING, "off"), ("medium", "maybe", "on", "off"), ("medium", "ON", "on", "off"), ("low", True, "on", "off"), ("medium", 1, MISSING, "off"),
    ("medium", ["on"], MISSING, "off"), ("medium", None, MISSING, "off"), ("medium", "", MISSING, "off"),
    ("medium", "on", "maybe", "off"), ("medium", MISSING, "maybe", "off"),
    # `jade jev unset` records no value: the default applies again
    ("medium", MISSING, None, "on"), ("high", MISSING, None, "off"),
    # an event with no value in it is not a human command
    ("medium", MISSING, "no-value", "off"),
]


def _name(v):
    return "missing" if v is MISSING else repr(v)


@pytest.mark.parametrize("level, key, event, expected", TABLE, ids=[f"{_name(lv)}-key={_name(k)}-event={_name(e)}" for lv, k, e, _ in TABLE])
def test_the_effective_value_follows_the_table(level, key, event, expected):
    set_cfg(level=MISSING if level is None else level, jev=key)
    if event == "no-value":
        record(MISSING)
    elif event is not MISSING:
        record(event)
    assert ops.jev_effective() == expected


def test_n31_a_misspelt_level_reads_unset_and_a_bad_jev_reads_off():
    set_cfg(level="Hgih", jev="maybe")
    assert paths.level() is None and ops.jev_effective() == "off"


def test_n33_no_environment_variable_moves_the_key(monkeypatch):
    for name in ("JADE_JEV", "JADE_JEV_MODE", "JADE_AUTONOMY", "JADE_PROXY"):
        monkeypatch.setenv(name, "on")
    set_cfg(level="high")
    assert ops.jev_effective() == "off"
    set_cfg(level="medium", jev="off")
    record("off")
    for name in ("JADE_JEV", "JADE_JEV_MODE"):
        monkeypatch.setenv(name, "on")
    assert ops.jev_effective() == "off"
    source = "\n".join(p.read_text() for p in (ROOT / "jade").glob("*.py"))
    assert "JADE_JEV" not in source, "an environment override for `jev` was added: a variable in an agent's command must not move it"


def test_an_unreadable_ledger_never_turns_it_on(monkeypatch):
    set_cfg(level="medium", jev="on")
    record("on")
    assert ops.jev_effective() == "on"

    def broken(*a, **k):
        raise OSError("no ledger")

    monkeypatch.setattr(ledger, "open", broken)
    assert ops.jev_effective() == "off"   # `on` needs the human's record, which cannot be read


# ------------------------------------------------------------------ `jade jev`

def test_set_jev_refuses_a_non_human_terminal(monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", REAL_REFUSAL)
    with pytest.raises(ops.HumanOnly):
        ops.set_jev("on", stdin_isatty=False)
    assert "jev" not in paths.config()


def test_set_jev_writes_the_key_and_journals_an_event_first():
    set_cfg(level="high")
    ops.set_jev("on", stdin_isatty=True)
    assert paths.config()["jev"] == "on" and ops.jev_effective() == "on"
    db = ledger.open(timeout=5)
    try:
        rows = db.con.execute("SELECT kind, actor, detail_json FROM events WHERE kind='jev'").fetchall()
    finally:
        db.close()
    assert len(rows) == 1 and json.loads(rows[0]["detail_json"]) == {"jev": "on"} and rows[0]["actor"].startswith("human:")
    ops.set_jev("off", stdin_isatty=True)
    assert ops.jev_effective() == "off"


def test_unset_removes_the_key_and_the_default_applies_again():
    set_cfg(level="medium")
    ops.set_jev("off", stdin_isatty=True)
    assert ops.jev_effective() == "off"
    ops.set_jev("unset", stdin_isatty=True)
    assert "jev" not in paths.config() and ops.jev_effective() == "on"
    set_cfg(level="high")
    assert ops.jev_effective() == "off"


@pytest.mark.parametrize("v", ["maybe", "", "ON", "true", "1"])
def test_only_on_off_and_unset_are_accepted(v):
    with pytest.raises(ValueError):
        ops.set_jev(v, stdin_isatty=True)


def test_set_jev_is_refused_when_the_key_says_on_and_no_command_recorded_it():
    set_cfg(level="medium", jev="on")   # written some other way
    with pytest.raises(ops.HumanOnly):
        ops.set_jev("on", stdin_isatty=True)
    ops.set_jev("off", stdin_isatty=True)   # the safe value is always allowed
    assert ops.jev_effective() == "off"
    ops.set_jev("on", stdin_isatty=True)
    assert ops.jev_effective() == "on"


def test_set_jev_accepts_an_unrecorded_off_key_a_new_install_wrote():
    set_cfg(level="medium", jev="off")
    ops.set_jev("on", stdin_isatty=True)
    assert ops.jev_effective() == "on"


def test_the_cli_shows_and_sets_the_key(capsys):
    set_cfg(level="medium")
    assert cli.main(["jev"]) in (0, None)
    got = capsys.readouterr()
    assert got.out.strip() == "on" and "default" in got.err
    assert cli.main(["jev", "off"]) in (0, None)
    assert capsys.readouterr().out.strip() == "jev off"
    assert cli.main(["jev"]) in (0, None)
    assert capsys.readouterr().out.strip() == "off"
    assert cli.main(["jev", "unset"]) in (0, None)
    assert paths.config().get("jev") is None


def test_the_cli_refuses_a_non_human_terminal_with_a_message(monkeypatch, capsys):
    monkeypatch.setattr(util, "human_tty_refusal", REAL_REFUSAL)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert cli.main(["jev", "on"]) == 1
    assert "jev" not in paths.config()


# ------------------------------------------------------------------ labels (display only, never stored)

@pytest.mark.parametrize("level, jev, label", [
    ("high", "off", "MANUAL"), ("medium", "off", "ASSISTED"), ("medium", "on", "BALANCED"), ("low", "on", "FAST"),
    ("high", "on", "custom"), ("low", "off", "custom"), ("off", "off", "observe"), ("off", "on", "observe"), (None, "off", "legacy"), (None, "on", "legacy"),
])
def test_the_label_is_derived_from_the_pair(level, jev, label):
    assert ops.autonomy_label(level, jev) == label


def test_the_label_is_shown_on_stderr_and_never_stored(capsys):
    ops.set_level("medium", stdin_isatty=True)
    ops.set_jev("off", stdin_isatty=True)
    assert cli.main(["level"]) in (0, None)
    got = capsys.readouterr()
    assert got.out.strip() == "medium"   # stdout keeps the bare value for scripts
    assert "ASSISTED" in got.err
    assert "label" not in paths.config() and "ASSISTED" not in json.dumps(paths.config())


# ------------------------------------------------------------------ `jade check`

def rows():
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}


def test_the_check_row_says_the_value_and_where_it_comes_from():
    set_cfg(level="medium")
    ok, detail = rows()["jev"]
    assert ok is not False and detail.startswith("on") and "default" in detail
    ops.set_jev("off", stdin_isatty=True)
    ok, detail = rows()["jev"]
    assert ok is True and detail.startswith("off") and "last `jade jev` event" in detail


def test_the_check_row_fails_when_the_file_does_not_match_the_record():
    set_cfg(level="medium")
    ops.set_jev("off", stdin_isatty=True)
    set_cfg(jev=MISSING)
    ok, detail = rows()["jev"]
    assert ok is False and "off" in detail and "jade jev unset" in detail
    set_cfg(jev="maybe")
    ok, detail = rows()["jev"]
    assert ok is False and detail.startswith("off") and "not on or off" in detail


# ------------------------------------------------------------------ N28: only a human terminal changes it

BLOCKED = [
    "jade jev on", "jade jev off", "jade jev unset", "jade  jev  on", "~/.local/bin/jade jev off", "uv run jade jev on", "python -m jade.cli jev on",
    "$J jev on", "jade -- jev on", "python3 -c 'import jade.proxy'", "python3 -c 'from jade import proxy'", "python3 -c 'from jade import memory'",
    "python3 -c 'import jade.ops'",
]
ALLOWED = [
    "jade jev", "jade jev --help", "jade level", "echo 'run jade jev on yourself'", "grep -rn 'jade jev' docs/", "pytest -k jev", "git log --oneline -- jade/proxy.py",
    "cat jade/proxy.py", "jev verify --claim x --evidence y",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_n28_the_jev_verb_and_the_modules_are_si09(cmd):
    a = policy.assess("Bash", {"command": cmd}, str(ROOT))
    assert "SI-09" in a.blocked, cmd


@pytest.mark.parametrize("cmd", ALLOWED)
def test_n28_reading_the_value_and_naming_the_verb_is_not_blocked(cmd):
    a = policy.assess("Bash", {"command": cmd}, str(ROOT))
    assert "SI-09" not in a.blocked, (cmd, a.blocked)
