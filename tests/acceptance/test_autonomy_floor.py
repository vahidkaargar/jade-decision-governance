"""V6-16: level `off` is "observe plus floor". At `off` JADE still logs most rules and lets the call run, but a small floor blocks.

The floor at `off` (gate mode enforce): SI-07 (an unknown outcome is not retried), SI-08 (no push to the default branch), and a write to JADE's own live
wiring: the human-only classes HO-wiring (the running install, the hook files, `~/.claude/settings.json`, the JADE config and state), HO-checkout-install
(an install built from a JADE checkout) and HO-settings-shadow (a project settings write that turns the hooks off). SI-09 to SI-12 block as before.
Every other rule (R-13 outside the wiring, R-16 in a checkout, the human-only classes of a delete, money or a release) still only logs at `off`.

Tests only classify and call the hook; nothing runs. Test names carry the plan ids: N4 and N5, N6, N6b of docs/14-v6/05, section 9.
"""

import json
from pathlib import Path

import pytest

from jade import decide, hooks, ledger, ops, paths, permits, util

ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home()
SESSION = "sess-floor-0001"
REAL_REFUSAL = util.human_tty_refusal


def call(tool, tool_input, cwd=None):
    return {"tool_name": tool, "tool_input": tool_input, "session_id": SESSION, "cwd": cwd or str(ROOT)}


def bash(cmd, cwd=None):
    return call("Bash", {"command": cmd}, cwd)


def write(path, content="x", tool="Write"):
    return call(tool, {"file_path": str(path), "content": content})


def set_cfg(**kw):
    cfg = paths.config()
    cfg.update(kw)
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


@pytest.fixture(autouse=True)
def human(monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")


@pytest.fixture
def off():
    ops.set_level("off", stdin_isatty=True)


# ------------------------------------------------------------------ N5: SI-08 at off

@pytest.mark.parametrize("cmd", ["git push origin main", "git push origin HEAD:main", "git push origin feature +feature:main", "cd /tmp && git push origin master"])
def test_n5_a_push_to_the_default_branch_blocks_at_off(off, cmd):
    r = hooks.pretool(bash(cmd))
    assert r.exit_code == 2 and "SI-08" in r.stderr and "[advisory" not in r.stderr, (cmd, r.stderr[:200])


def test_n5_a_push_to_a_feature_branch_still_runs_at_off(off):
    assert hooks.pretool(bash("git push origin feature-x")).exit_code == 0


def test_n5_si08_in_advisory_gate_mode_stays_advisory_at_off(off, monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    r = hooks.pretool(bash("git push origin main"))
    assert r.exit_code == 0 and "[advisory, not blocked]" in r.stderr   # the owner chose advisory: only SI-09 to SI-12 are firm there


# ------------------------------------------------------------------ N5: SI-07 at off

@pytest.fixture
def unknown_outcome(human_env):
    """`fly deploy` was approved (level unset: the signed or terminal permit), started, and its result was lost."""
    db = ledger.open()
    assert hooks.pretool(bash("fly deploy")).exit_code == 2
    p = permits.listing(db, ("PENDING",))[0]
    permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(bash("fly deploy")).exit_code == 0
    decide.record_outcome(db, p.decision_id, "unknown", None, "deploy output lost")
    db.close()
    return p.decision_id


def test_n5_an_unknown_outcome_is_held_at_off(unknown_outcome):
    ops.set_level("off", stdin_isatty=True)
    r = hooks.pretool(bash("fly deploy"))
    assert r.exit_code == 2 and "SI-07" in r.stderr and unknown_outcome in r.stderr and "[advisory" not in r.stderr


def test_n5_a_different_command_is_not_held_by_si07_at_off(unknown_outcome):
    ops.set_level("off", stdin_isatty=True)
    r = hooks.pretool(bash("fly deploy --app other"))
    assert "SI-07" not in r.stderr and r.exit_code == 0   # logged only: the hold is for the exact command


# ------------------------------------------------------------------ N5: wiring writes at off

WIRING = [HOME / ".claude" / "settings.json", HOME / ".claude" / "settings.local.json", HOME / ".claude" / "hooks" / "x.sh", HOME / ".local" / "bin" / "jade"]


@pytest.mark.parametrize("target", WIRING, ids=lambda p: p.name)
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_n5_a_write_of_the_wiring_blocks_at_off(off, tool, target):
    r = hooks.pretool(write(target, tool=tool))
    assert r.exit_code == 2 and "HO-wiring" in r.stderr and "[advisory" not in r.stderr, (tool, target, r.stderr[:200])


@pytest.mark.parametrize("cmd", ["echo x > ~/.claude/settings.json", "cp /tmp/s.json ~/.claude/settings.json", "tee ~/.claude/hooks/x.sh < /tmp/x",
                                 "uv tool uninstall jade-decision-governance"])
def test_n5_a_shell_write_of_the_wiring_blocks_at_off(off, cmd):
    r = hooks.pretool(bash(cmd))
    assert r.exit_code == 2 and "HO-wiring" in r.stderr, (cmd, r.stderr[:200])


def test_n5_the_wiring_write_is_journaled_as_a_safety_block(off):
    hooks.pretool(write(HOME / ".claude" / "settings.json"))
    db = ledger.open()
    try:
        rows = db.con.execute("SELECT guard FROM events WHERE kind='safety_block'").fetchall()
    finally:
        db.close()
    assert len(rows) == 1 and "R-13" in rows[0][0]


def test_n5_a_wiring_write_in_advisory_gate_mode_stays_advisory_at_off(off, monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    assert hooks.pretool(write(HOME / ".claude" / "settings.json")).exit_code == 0


@pytest.mark.parametrize("cmd", ["cat ~/.claude/settings.json", "cp ~/.claude/settings.json /tmp/backup.json", "grep hooks ~/.claude/settings.json", "git status"])
def test_n5_a_read_of_the_wiring_is_not_blocked_at_off(off, cmd):
    assert hooks.pretool(bash(cmd)).exit_code == 0, cmd


# ------------------------------------------------------------------ N6b: what still only logs at off (documents the 1.3 fact)

def test_n6b_a_human_only_delete_is_not_raised_and_not_blocked_at_off(off):
    r = hooks.pretool(bash("kubectl delete pod web-1"))
    assert r.exit_code == 0   # at `off` an HO class is not raised to a question; only the wiring classes block


@pytest.mark.parametrize("target", [ROOT / "jade" / "hooks.py", ROOT / "AGENTS.md", ROOT / "policies" / "risk.yaml"], ids=lambda p: p.name)
def test_n6b_other_protected_writes_only_log_at_off(off, target):
    r = hooks.pretool(write(target))
    assert r.exit_code == 0 and "[advisory, not blocked]" in r.stderr, (target, r.stderr[:200])


def test_n6b_an_ordinary_high_call_only_logs_at_off(off):
    r = hooks.pretool(bash("git reset --hard HEAD~1"))
    assert r.exit_code == 0 and "[advisory, not blocked]" in r.stderr


def test_n4_the_governance_invariants_still_block_at_off(off):
    for cmd in ("jade permit approve p_1", "jade hooks uninstall", "cat permit-seal.key", "JADE_HOME=/tmp/x true"):
        assert hooks.pretool(bash(cmd)).exit_code == 2, cmd


# ------------------------------------------------------------------ the other levels do not change

@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_the_floor_is_not_looser_or_different_above_off(level):
    ops.set_level(level, stdin_isatty=True)
    assert hooks.pretool(bash("git push origin main")).exit_code == 2
    assert hooks.pretool(write(HOME / ".claude" / "settings.json")).exit_code == 2


def test_the_floor_does_not_exist_with_no_level_set_and_the_permit_gate_asks_instead():
    r = hooks.pretool(write(HOME / ".claude" / "settings.json"))
    assert r.exit_code == 2 and "permit" in r.stderr   # level unset: R-13 and the signed permit, as in 0.4.0


# ------------------------------------------------------------------ N6: `jade check` and `jade level` say it

def _rows():
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}


def test_n6_jade_level_shows_observe_plus_floor(off, capsys):
    from jade import cli

    assert cli.main(["level"]) in (0, None)
    got = capsys.readouterr()
    assert got.out.strip() == "off"   # stdout keeps the bare value for scripts
    assert "observe plus floor" in got.err


def test_n6_jade_level_for_the_other_values_is_unchanged(capsys):
    from jade import cli

    ops.set_level("high", stdin_isatty=True)
    assert cli.main(["level"]) in (0, None)
    assert capsys.readouterr().out.strip() == "high"


def test_n6_the_level_row_warns_and_names_the_floor(off):
    ok, detail = _rows()["level"]
    assert ok is None and detail.startswith("off (observe plus floor)")
    assert "SI-07" in detail and "SI-08" in detail and "wiring" in detail


def test_n6_the_what_blocks_row_lists_the_floor(off, monkeypatch):
    monkeypatch.setattr(ops, "hooks_status", lambda: dict.fromkeys(ops.HOOKS, True))
    monkeypatch.setattr(ops, "wiring_problems", lambda probe=True: [])
    ok, detail = _rows()["what blocks"]
    assert ok is None
    assert "SI-07, SI-08, SI-09, SI-10, SI-11, SI-12 block" in detail
    assert "HO-wiring" in detail and "HO-checkout-install" in detail and "HO-settings-shadow" in detail


def test_n6_jade_check_warns_when_the_level_is_off_and_the_host_does_not_prompt(off, monkeypatch):
    monkeypatch.setattr(ops, "configured_permission_mode", lambda: "bypassPermissions")
    ok, detail = _rows()["host permission prompt"]
    assert ok is None and "level off" in detail and "SI-07" in detail and "SI-08" in detail   # the floor, not "only SI-09 to SI-12"


def test_n6_set_level_off_prints_what_off_means(capsys):
    from jade import cli

    assert cli.main(["level", "off"]) in (0, None)
    got = capsys.readouterr()
    assert got.out.strip() == "level off" and "observe plus floor" in got.err


# ------------------------------------------------------------------ review fixes: script-then-run naming JADE state, and a settings write read as text

@pytest.mark.parametrize("name, body", [
    ("cfg.py", 'import os\nopen(os.path.expanduser("~/.config/jade/config.json"), "w").write("{}")\n'),
    ("db.py", 'import sqlite3\nsqlite3.connect("jade.sqlite").execute("delete from permits")\n'),
    ("cfg.sh", "echo '{}' > ~/.config/jade/config.json\n"),
])
def test_a_script_that_names_the_jade_state_blocks_when_it_runs_at_off(off, tmp_path, name, body):
    (tmp_path / name).write_text(body)
    runner = "sh" if name.endswith(".sh") else "python3"
    r = hooks.pretool(bash(f"{runner} {name}", cwd=str(tmp_path)))
    assert r.exit_code == 2 and "HO-state-path" in r.stderr and "[advisory" not in r.stderr, (name, r.stderr[:200])


def test_a_script_that_does_not_name_the_jade_state_still_runs_at_off(off, tmp_path):
    (tmp_path / "ok.py").write_text("print('hello')\n")
    assert hooks.pretool(bash("python3 ok.py", cwd=str(tmp_path))).exit_code == 0


def test_a_script_that_names_the_jade_state_stays_advisory_in_advisory_gate_mode_at_off(off, tmp_path, monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    (tmp_path / "cfg.py").write_text('open("/Users/x/.config/jade/config.json", "w")\n')
    assert hooks.pretool(bash("python3 cfg.py", cwd=str(tmp_path))).exit_code == 0


def settings_call(tmp_path, tool_input, tool="Write"):
    d = tmp_path / ".claude"
    d.mkdir(exist_ok=True)
    tin = {"file_path": str(d / "settings.local.json"), **tool_input}
    return call(tool, tin, cwd=str(tmp_path))


@pytest.mark.parametrize("content", [
    '{"\\u0064isableAllHooks": true}',
    '{"env": {"JADE_GATE_MOD\\u0045": "advisory"}}',
    '{"env": {"\\u004aADE_HOME": "/tmp/x"}}',
    '{"env": {"JADE_POLICY_DIR": "/tmp/p"}}',
    '{"\\u0068ooks": {}}',
])
def test_a_settings_write_with_escaped_keys_blocks_at_off(off, tmp_path, content):
    r = hooks.pretool(settings_call(tmp_path, {"content": content}))
    assert r.exit_code == 2 and "HO-settings-shadow" in r.stderr and "[advisory" not in r.stderr, (content, r.stderr[:200])


def test_a_settings_write_in_two_steps_blocks_at_the_second_step_at_off(off, tmp_path):
    first = settings_call(tmp_path, {"content": '{"disableAllH": true}'})
    assert hooks.pretool(first).exit_code == 0   # nothing yet: not a key the hooks know
    Path(first["tool_input"]["file_path"]).write_text('{"disableAllH": true}')
    r = hooks.pretool(settings_call(tmp_path, {"old_string": 'H"', "new_string": 'Hooks"'}, tool="Edit"))
    assert r.exit_code == 2 and "HO-settings-shadow" in r.stderr, r.stderr[:200]


def test_a_multiedit_of_a_settings_file_is_read_as_the_whole_result_at_off(off, tmp_path):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    target.write_text('{"disableAllHo": 1, "x": 0}')
    tin = {"file_path": str(target), "edits": [{"old_string": 'Ho"', "new_string": 'Hoo"'}, {"old_string": 'Hoo"', "new_string": 'Hooks"'}]}
    r = hooks.pretool(call("MultiEdit", tin, cwd=str(tmp_path)))
    assert r.exit_code == 2 and "HO-settings-shadow" in r.stderr, r.stderr[:200]


def test_an_edit_of_a_settings_file_that_cannot_be_read_blocks_at_off(off, tmp_path):
    r = hooks.pretool(settings_call(tmp_path, {"old_string": "a", "new_string": "b"}, tool="Edit"))   # no such file: the result is unknown
    assert r.exit_code == 2 and "HO-settings-shadow" in r.stderr


def test_a_harmless_settings_write_or_edit_still_runs_at_off(off, tmp_path):
    ok = '{"permissions": {"allow": ["Bash(ls)"]}}'
    first = settings_call(tmp_path, {"content": ok})
    assert hooks.pretool(first).exit_code == 0
    Path(first["tool_input"]["file_path"]).write_text(ok)
    assert hooks.pretool(settings_call(tmp_path, {"old_string": "Bash(ls)", "new_string": "Bash(pwd)"}, tool="Edit")).exit_code == 0
