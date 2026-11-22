"""W-200: every list of gate environment variables agrees. paths.GATE_ENV_VARS is the one list; SI-11 (command and file-write patterns, description),
the settings-file check, `paths.gate_overrides`, the settings `env` readers and the hook-command check all have to cover each name in it."""
from __future__ import annotations

import json
import re

import pytest

from jade import hooks, ops, paths, policy

NAMES = list(getattr(paths, "GATE_ENV_VARS", ()))
SESSION_VARS = ("JADE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")
HOST_ID = "11111111-aaaa-bbbb-cccc-222222222222"


def _si11():
    return next(i for i in policy.safety()["invariants"] if i["id"] == "SI-11")


def test_the_one_list_exists_and_holds_every_variable_si11_names():
    assert NAMES, "paths.GATE_ENV_VARS is missing"
    assert len(set(NAMES)) == len(NAMES)
    for n in ("JADE_GATE_MODE", "JADE_POLICY_DIR", "JADE_CONFIG", "JADE_HOME", "JADE_APPROVER_ANCHOR", "JADE_REGISTRY_DIR", "JADE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        assert n in NAMES, n


def test_si11_description_and_every_pattern_name_the_same_set_as_the_one_list():
    inv = _si11()
    for n in NAMES:
        assert n in inv["description"], ("description", n)
    for key in ("block", "content_block"):
        found = set()
        for alt in re.findall(r"JADE_\(\?:([A-Z_|]+)\)", inv[key]):
            found |= {"JADE_" + x for x in alt.split("|")}
        if "CLAUDE_CODE_SESSION_ID" in inv[key]:
            found.add("CLAUDE_CODE_SESSION_ID")
        assert found == set(NAMES), (key, sorted(found ^ set(NAMES)))


@pytest.mark.parametrize("name", NAMES)
def test_si11_blocks_a_command_and_a_profile_write_for_each_listed_name(name):
    assert "SI-11" in policy.assess("Bash", {"command": f"export {name}=x"}, "/nonexistent-w200").blocked
    assert "SI-11" in policy.assess("Write", {"file_path": "/Users/x/.zshrc", "content": f"export {name}=x\n"}, "/nonexistent-w200").blocked


@pytest.mark.parametrize("name", NAMES)
def test_a_settings_file_that_names_a_listed_variable_counts_as_moving_jade(name):
    assert policy._settings_disables(json.dumps({"env": {name: "x"}}))
    assert policy._settings_disables('{"env": {"' + "".join(f"\\u{ord(c):04x}" for c in name) + '": "x"}}')


@pytest.mark.parametrize("name", NAMES)
def test_gate_overrides_in_a_settings_env_block_lists_each_listed_name(name):
    got = [o.var for o in paths.gate_overrides({name: "/tmp/elsewhere"}, source="settings.json")]
    assert got == [name]


@pytest.mark.parametrize("name", [n for n in NAMES if n != "JADE_REGISTRY_DIR"])
def test_the_settings_env_reader_and_the_shadow_scan_see_each_weakening_name(name, tmp_path, monkeypatch):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"env": {name: "/tmp/elsewhere" if name not in SESSION_VARS and name != "JADE_GATE_MODE" else ("off" if name == "JADE_GATE_MODE" else "sess-old")}}))
    monkeypatch.setenv("JADE_CLAUDE_SETTINGS", str(s))
    assert name in [o.var for o in ops.settings_env_overrides()]
    assert any(f"env.{name}=" in line for line in ops._shadow_scan(s))


@pytest.mark.parametrize("name", NAMES)
def test_a_hook_command_that_sets_a_listed_name_is_a_gate_assignment(name):
    assert ops._GATE_ASSIGN.search(f"{name}=x jade hook pretool")


def test_the_report_row_names_every_listed_variable_when_none_is_set(monkeypatch):
    monkeypatch.setattr(paths, "gate_overrides", lambda *a, **k: [])   # the test run itself redirects JADE_HOME and JADE_CONFIG
    monkeypatch.setattr(ops, "settings_env_overrides", lambda: [])
    ok, detail = ops._overrides_row()[1:]
    assert ok
    for n in NAMES:
        assert n in detail, n


def test_a_host_session_id_is_not_an_override_but_a_different_one_in_the_process_is(monkeypatch):
    env = {"CLAUDE_CODE_SESSION_ID": HOST_ID}
    assert paths.gate_overrides(env) == []                                          # no host id known: the value the host sets in every session never shows
    assert paths.gate_overrides(env, host_session_id=HOST_ID) == []                  # equal to the host's id: not an override
    (o,) = paths.gate_overrides({"CLAUDE_CODE_SESSION_ID": "sess-old"}, host_session_id=HOST_ID)
    assert o.var == "CLAUDE_CODE_SESSION_ID" and o.weakens
    assert paths.gate_overrides({"CLAUDE_CODE_SESSION_ID": ""}, host_session_id=HOST_ID) == []


def test_a_settings_env_block_that_sets_the_host_session_id_always_shows_even_when_it_equals_the_host_id():
    (o,) = paths.gate_overrides({"CLAUDE_CODE_SESSION_ID": HOST_ID}, source="settings.json", host_session_id=HOST_ID)
    assert o.weakens


def test_the_session_start_notice_names_a_session_id_that_differs_from_the_event_and_stays_silent_for_the_host_one(monkeypatch, tmp_path):
    def mentions(event_id):
        return any("CLAUDE_CODE_SESSION_ID" in line for line in hooks._override_notice(_NoDb(), {"session_id": event_id}))   # the test run's own JADE_HOME and JADE_CONFIG always show

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", HOST_ID)
    assert not mentions(HOST_ID)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-old")
    assert mentions(HOST_ID)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert not mentions(HOST_ID)


class _NoDb:
    def event(self, *a, **k):
        return None


@pytest.mark.parametrize("name", NAMES)
def test_a_project_settings_write_that_sets_a_listed_variable_is_human_only(name, tmp_path):
    f = tmp_path / "proj" / ".claude" / "settings.json"
    a = policy.assess("Write", {"file_path": str(f), "content": json.dumps({"env": {name: "x"}})}, str(tmp_path))
    assert "HO-settings-shadow" in a.ho_ids, name
