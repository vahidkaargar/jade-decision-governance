"""Installation and health (review cluster E: F37 F38 F39 F40, G05). Every test uses a temp settings path and a stub `jade` that runs this checkout."""
import json
import os
import plistlib
import stat
import subprocess

import pytest

from jade import hooks, ledger, ops, paths, policy


def rows_of(**kw):
    return {n: (ok, d) for n, ok, d in ops.check(live=False, **kw)}


def install_hooks():
    ops.hooks_install(stdin_isatty=True)


# ---- F37: check exits non-zero on dead wiring, and probes the live path ---------------------------------------------
def test_healthy_wiring_passes_check_and_the_probe_runs_each_hook(human_env, jade_stub, capsys):
    install_hooks()
    rows = rows_of()
    assert all(ok for ok, _ in rows.values()), rows
    assert "decided" in rows["hook wiring"][1]
    assert ops.print_check(ops.check(live=False)) == 0


def test_dead_hook_binary_fails_check(human_env, jade_stub, monkeypatch):
    monkeypatch.setenv("JADE_BIN", "/nonexistent/dead/jade")
    install_hooks()
    ok, detail = rows_of()["hook wiring"]
    assert not ok and "/nonexistent/dead/jade" in detail
    assert ops.print_check(ops.check(live=False)) == 1


def test_a_binary_that_exists_but_is_not_executable_fails_check(human_env, jade_stub):
    install_hooks()
    (jade_stub / "jade").chmod(0o644)
    assert not rows_of()["hook wiring"][0]


def test_disable_all_hooks_fails_check_in_settings_and_in_local_settings(human_env, jade_stub, tmp_path):
    install_hooks()
    s = tmp_path / "settings.json"
    data = json.loads(s.read_text())
    s.write_text(json.dumps({**data, "disableAllHooks": True}))
    ok, detail = rows_of()["hook wiring"]
    assert not ok and "disableAllHooks" in detail
    s.write_text(json.dumps(data))
    assert rows_of()["hook wiring"][0]
    (tmp_path / "settings.local.json").write_text(json.dumps({"disableAllHooks": True}))
    assert not rows_of()["hook wiring"][0]


def test_a_command_that_only_mentions_the_hook_is_not_installed(tmp_path):
    spoof = {"hooks": {e: [{"hooks": [{"type": "command", "command": f"echo jade hook {sub}"}]}] for e, sub in
                       (("PreToolUse", "pretool"), ("PostToolUse", "posttool"), ("Stop", "stop"), ("SessionStart", "session-start"))}}
    (tmp_path / "settings.json").write_text(json.dumps(spoof))
    assert not any(ops.hooks_status().values())
    assert ops.parse_hook_command("echo see my-jade hook notes") is None
    assert ops.parse_hook_command("/opt/x/jade hook pretool").sub == "pretool"


def test_a_hook_that_does_nothing_fails_the_live_probe(human_env, jade_stub, monkeypatch):
    install_hooks()
    (jade_stub / "jade").write_text("#!/bin/sh\ncat >/dev/null\nexit 0\n")
    ok, detail = rows_of()["hook wiring"]
    assert not ok and "SI-09" in detail and "no decision" in detail


def test_a_hook_that_crashes_fails_the_live_probe(human_env, jade_stub):
    install_hooks()
    (jade_stub / "jade").write_text("#!/bin/sh\necho 'Traceback (most recent call last)' >&2\nexit 1\n")
    assert not rows_of()["hook wiring"][0]


def test_wrong_event_or_matcher_fails_check(human_env, jade_stub, tmp_path):
    install_hooks()
    s = tmp_path / "settings.json"
    data = json.loads(s.read_text())
    data["hooks"]["PostToolUse"][0]["matcher"] = "Read"
    s.write_text(json.dumps(data))
    ok, detail = rows_of()["hook wiring"]
    assert not ok and "PostToolUse" in detail


def test_gate_off_fails_check_and_advisory_passes(human_env, jade_stub, monkeypatch):
    install_hooks()
    ops.set_gate_mode("off", stdin_isatty=True)
    ok, detail = rows_of()["gate mode"]
    assert not ok and "inert" in detail
    ops.set_gate_mode("advisory", stdin_isatty=True)
    assert rows_of()["gate mode"][0]
    ops.set_gate_mode("enforce", stdin_isatty=True)
    assert rows_of()["gate mode"] == (True, "enforce")


def test_gate_mode_env_that_disagrees_with_the_config_fails_check(human_env, jade_stub, monkeypatch):
    install_hooks()
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    ok, detail = rows_of()["gate mode"]
    assert not ok and "overrides the configured enforce" in detail
    monkeypatch.setenv("JADE_GATE_MODE", "off")
    assert not rows_of()["gate mode"][0]


def test_bypass_permissions_with_a_gate_that_never_blocks_fails_check(human_env, jade_stub, tmp_path):
    install_hooks()
    s = tmp_path / "settings.json"
    data = json.loads(s.read_text())
    s.write_text(json.dumps({**data, "permissions": {"defaultMode": "bypassPermissions"}}))
    ok, detail = rows_of()["host permission prompt"]
    assert ok and "bypassPermissions" in detail   # enforce: the note only
    ops.set_gate_mode("advisory", stdin_isatty=True)
    ok, detail = rows_of()["host permission prompt"]
    assert not ok and "nothing stops a call" in detail


def test_unloaded_maintain_plist_fails_check(human_env, jade_stub, tmp_path, monkeypatch):
    install_hooks()
    d = tmp_path / "LaunchAgents"
    d.mkdir()
    (d / "com.example.jade-maintain.plist").write_text(ops.launchd_plist("com.example.jade-maintain"))
    monkeypatch.setattr(ops, "_launchctl_loaded", lambda label: False)
    ok, detail = rows_of()["maintain job"]
    assert not ok and "not loaded" in detail
    monkeypatch.setattr(ops, "_launchctl_loaded", lambda label: True)
    assert rows_of()["maintain job"][0]
    assert ops.maintain_jobs()[0][1] == "com.example.jade-maintain"


# ---- F38: launchd gets a PATH and HOME, and the shim check does not depend on PATH ----------------------------------
def test_plist_carries_path_and_home_and_escapes_values(jade_stub, monkeypatch):
    plist = plistlib.loads(ops.launchd_plist("com.example.a&b<c>").encode())
    env = plist["EnvironmentVariables"]
    assert plist["Label"] == "com.example.a&b<c>" and env["HOME"] == str(ops.Path.home())
    assert env["PATH"].split(":")[0] == str(jade_stub) and "/usr/bin" in env["PATH"].split(":")


def test_jev_shim_is_found_beside_jade_under_launchds_minimal_path(human_env, jade_stub, monkeypatch):
    install_hooks()
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    ok, detail = rows_of()["jev shim"]
    assert ok and detail == str(jade_stub / "jev")
    (jade_stub / "jev").unlink()
    assert not rows_of()["jev shim"][0]


def test_plist_with_a_space_in_the_binary_path_stays_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("JADE_BIN", str(tmp_path / "a b" / "jade"))
    plist = plistlib.loads(ops.launchd_plist("x").encode())
    assert plist["ProgramArguments"][0] == str(tmp_path / "a b" / "jade")


# ---- F39: settings.json writes -------------------------------------------------------------------------------------
def test_install_keeps_a_symlink_and_its_target_mode(human_env, jade_stub, tmp_path, monkeypatch):
    target = tmp_path / "dotfiles" / "settings.json"
    target.parent.mkdir()
    target.write_text('{"model": "opus"}')
    target.chmod(0o600)
    link = tmp_path / "linked.json"
    link.symlink_to(target)
    monkeypatch.setenv("JADE_CLAUDE_SETTINGS", str(link))
    install_hooks()
    assert link.is_symlink() and stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "jade" in target.read_text() and json.loads(target.read_text())["model"] == "opus"
    assert all(ops.hooks_status().values())
    ops.hooks_uninstall(stdin_isatty=True)
    assert link.is_symlink() and stat.S_IMODE(target.stat().st_mode) == 0o600 and json.loads(target.read_text()) == {"model": "opus", "hooks": {}}


def test_a_new_settings_file_is_private(human_env, jade_stub, tmp_path):
    install_hooks()
    assert stat.S_IMODE((tmp_path / "settings.json").stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".settings.json.jade-*"))   # no temp file left behind


def test_two_installs_in_one_second_keep_the_original_in_a_backup(human_env, jade_stub, tmp_path):
    s = tmp_path / "settings.json"
    s.write_text('{"model": "opus"}')
    b1 = ops.hooks_install(stdin_isatty=True)
    b2 = ops.hooks_install(stdin_isatty=True)
    assert b1 != b2 and json.loads(b1.read_text()) == {"model": "opus"} and "hooks" in json.loads(b2.read_text())
    assert len(list(tmp_path.glob("settings.json.bak-jade-*"))) == 2


def test_uninstall_is_atomic_safe_on_a_missing_file_and_refuses_invalid_json(human_env, jade_stub, tmp_path):
    assert ops.hooks_uninstall(stdin_isatty=True) == 0 and not (tmp_path / "settings.json").exists()
    bad = tmp_path / "settings.json"
    bad.write_text("{not json")
    for fn in (lambda: ops.hooks_uninstall(stdin_isatty=True), lambda: ops.hooks_install(stdin_isatty=True)):
        with pytest.raises(ops.WiringError):
            fn()
    assert bad.read_text() == "{not json"


def test_uninstall_writes_through_a_temp_file_then_rename(human_env, jade_stub, tmp_path, monkeypatch):
    install_hooks()
    calls = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append((os.path.dirname(a), os.path.basename(b))), real(a, b))[1])
    assert ops.hooks_uninstall(stdin_isatty=True) == 5
    assert calls == [(str(tmp_path.resolve()), "settings.json")]
    assert len(list(tmp_path.glob("settings.json.bak-jade-*"))) == 1   # the uninstall's: the install had no file to back up


def test_a_foreign_hook_that_mentions_jade_survives_install_and_uninstall(human_env, jade_stub, tmp_path):
    foreign = "echo see my-jade hook notes"
    other = "/opt/tools/not-jade hook pretool"
    (tmp_path / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": foreign}, {"type": "command", "command": other}]}]}}))
    install_hooks()
    install_hooks()
    cmds = lambda: [h["command"] for g in json.loads((tmp_path / "settings.json").read_text())["hooks"]["PreToolUse"] for h in g["hooks"]]  # noqa: E731
    assert foreign in cmds() and other in cmds()
    assert ops.hooks_uninstall(stdin_isatty=True) == 5
    assert cmds() == [foreign, other]


def test_a_binary_path_with_a_space_is_quoted_and_runs(human_env, tmp_path, monkeypatch):
    import sys

    d = tmp_path / "with space"
    d.mkdir()
    (d / "jade").write_text(f'#!/bin/sh\nexec "{sys.executable}" -m jade.cli "$@"\n')
    (d / "jade").chmod(0o755)
    monkeypatch.setenv("JADE_BIN", str(d / "jade"))
    install_hooks()
    cmd = json.loads((tmp_path / "settings.json").read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert cmd.startswith("'") and ops.parse_hook_command(cmd).binary == str(d / "jade")
    assert ops.wiring_problems() == []
    # an install of the older kind (unquoted) is recognised and reported, not silently half working
    legacy = f"{d / 'jade'} hook stop"
    assert ops.parse_hook_command(legacy).shell_safe is False


# ---- F40: claude-md apply -------------------------------------------------------------------------------------------
def apply(tmp_path, text):
    md = tmp_path / "CLAUDE.md"
    md.write_text(text)
    ops.claude_md_apply(md, stdin_isatty=True)
    return md.read_text()


def test_claude_md_keeps_a_following_level_one_heading(human_env, tmp_path):
    out = apply(tmp_path, ops.JEV_HEADING + "\n- old jev\n\n# Global agent rules\nIMPORTANT user rules\n\n## Learned\nL\n")
    assert "IMPORTANT user rules" in out and "# Global agent rules" in out and "## Learned\nL" in out and "old jev" not in out


def test_claude_md_ignores_a_jev_heading_inside_a_code_fence(human_env, tmp_path):
    text = "# Notes\n```md\n" + ops.JEV_HEADING + "\nexample only\n```\n\n" + ops.JEV_HEADING + "\n- real old\n\n## Next\nkeep\n"
    out = apply(tmp_path, text)
    assert "example only" in out and "real old" not in out and "## Next\nkeep" in out and out.count("```") == 2


def test_claude_md_does_not_end_the_section_at_a_fenced_heading_or_a_deeper_one(human_env, tmp_path):
    text = ops.JEV_HEADING + "\n### Sub\nsub text\n```\n## not a heading\n# nor this\n```\nmore\n\n## Next\nkeep\n"
    out = apply(tmp_path, text)
    assert "sub text" not in out and "not a heading" not in out and "more\n" not in out and "## Next\nkeep" in out


def test_claude_md_jev_section_last_in_file_is_replaced_and_idempotent(human_env, tmp_path):
    first = apply(tmp_path, "# Top\nkeep\n\n" + ops.JEV_HEADING + "\n- old\n")
    assert first.startswith("# Top\nkeep\n\n") and "old" not in first
    assert apply(tmp_path, first) == first


def test_claude_md_refuses_a_duplicate_heading_and_writes_nothing(human_env, tmp_path):
    md = tmp_path / "CLAUDE.md"
    text = ops.JEV_HEADING + "\na\n\n## X\n\n" + ops.JEV_HEADING + "\nb\n"
    md.write_text(text)
    with pytest.raises(ValueError, match="2 times"):
        ops.claude_md_apply(md, stdin_isatty=True)
    assert md.read_text() == text and not list(tmp_path.glob("CLAUDE.md.bak-jade-*"))


def test_claude_md_apply_keeps_symlink_mode_and_both_backups(human_env, tmp_path):
    real = tmp_path / "real.md"
    real.write_text(ops.JEV_HEADING + "\nold\n")
    real.chmod(0o640)
    link = tmp_path / "CLAUDE.md"
    link.symlink_to(real)
    b1 = ops.claude_md_apply(link, stdin_isatty=True)
    b2 = ops.claude_md_apply(link, stdin_isatty=True)
    assert link.is_symlink() and stat.S_IMODE(real.stat().st_mode) == 0o640 and b1 != b2
    assert b1.read_text().strip().endswith("old")


# ---- G05: the gate configuration surface -----------------------------------------------------------------------------
def test_check_reports_every_weakening_override(human_env, jade_stub, monkeypatch, tmp_path):
    install_hooks()
    monkeypatch.setenv("JADE_ACK_OVERRIDES", "")
    ok, detail = rows_of()["gate overrides"]
    assert not ok and "JADE_HOME" in detail and "JADE_CONFIG" in detail
    monkeypatch.setenv("JADE_POLICY_DIR", str(tmp_path / "empty-policies"))
    assert "JADE_POLICY_DIR" in rows_of()["gate overrides"][1]
    monkeypatch.setenv("JADE_ACK_OVERRIDES", "JADE_HOME,JADE_CONFIG,JADE_POLICY_DIR,JADE_APPROVER_ANCHOR")
    ok, detail = rows_of()["gate overrides"]
    assert ok and "acknowledged" in detail


def test_a_policy_dir_that_is_the_shipped_one_does_not_weaken(monkeypatch):
    monkeypatch.setenv("JADE_POLICY_DIR", str(paths.shipped_policy_dir()))
    (o,) = [o for o in paths.gate_overrides() if o.var == "JADE_POLICY_DIR"]
    assert not o.weakens


def test_an_override_in_the_claude_settings_env_block_fails_check_and_cannot_be_acknowledged(human_env, jade_stub, tmp_path, monkeypatch):
    install_hooks()
    s = tmp_path / "settings.json"
    data = json.loads(s.read_text())
    s.write_text(json.dumps({**data, "env": {"JADE_POLICY_DIR": "/nonexistent/policies", "JADE_GATE_MODE": "off", "OTHER": "x"}}))
    monkeypatch.setenv("JADE_ACK_OVERRIDES", "JADE_HOME,JADE_CONFIG,JADE_POLICY_DIR,JADE_GATE_MODE,JADE_APPROVER_ANCHOR")
    ok, detail = rows_of()["gate overrides"]
    assert not ok and "JADE_POLICY_DIR" in detail and "JADE_GATE_MODE" in detail and "OTHER" not in detail


def test_session_start_shows_and_journals_a_weakening_override_once_per_session(monkeypatch, tmp_path):
    monkeypatch.setenv("JADE_POLICY_DIR", str(tmp_path / "p"))
    monkeypatch.setenv("JADE_GATE_MODE", "advisory")
    ev = {"session_id": "sess-override-1"}
    out = hooks.session_start(ev).stdout
    assert "gate override in force" in out and "JADE_POLICY_DIR" in out and "JADE_GATE_MODE=advisory" in out
    hooks.session_start(ev)
    assert ledger.open().one("SELECT COUNT(*) AS n FROM events WHERE kind='gate_override'").n == 1


def test_session_start_is_quiet_without_a_weakening_override(monkeypatch):
    monkeypatch.setattr(paths, "gate_overrides", lambda *a, **k: [])   # the test run itself redirects JADE_HOME and JADE_CONFIG
    assert "gate override" not in hooks.session_start({"session_id": "sess-clean-1"}).stdout


@pytest.mark.parametrize("cmd", ["JADE_POLICY_DIR=/tmp/empty git status", "export JADE_CONFIG=/tmp/x", "env JADE_HOME=/tmp/x jade ask", "launchctl setenv JADE_POLICY_DIR /tmp/x",
                                 "echo 'export JADE_GATE_MODE=off' >> ~/.zshrc"])
def test_agent_commands_that_set_gate_variables_are_blocked(cmd):
    assert "SI-11" in policy.assess("Bash", {"command": cmd}).blocked, cmd


def test_probe_leaves_the_real_ledger_alone(human_env, jade_stub, tmp_path):
    install_hooks()
    before = ledger.open().one("SELECT COUNT(*) AS n FROM events").n
    ops.wiring_problems()
    assert ledger.open().one("SELECT COUNT(*) AS n FROM events").n == before
    assert not subprocess.run(["pgrep", "-f", "jade-check-probe"], capture_output=True).stdout


def test_cli_reports_wiring_errors_without_a_traceback(human_env, jade_stub, tmp_path, capsys, monkeypatch):
    from jade import cli

    monkeypatch.setattr("jade.util.is_human_tty", lambda *a, **k: True)
    monkeypatch.setattr(ops, "is_human_tty", lambda *a, **k: True)
    (tmp_path / "settings.json").write_text("{broken")
    assert cli.main(["hooks", "uninstall"]) == 1
    assert "not valid JSON" in capsys.readouterr().err
