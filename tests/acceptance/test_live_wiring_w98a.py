"""W-98a (docs/12 plan v3, PR-3a): the human-only classes `policy.assess` reports in `ho_ids`.

Live JADE wiring (the running install, launchers, hook files, state and config directories), host-config mods, an install built from a JADE checkout,
a project settings file that disables JADE, JADE state paths named in a script, R-17 and a script typed into the terminal panel are human-only: no
memory and no Jev may answer them. This PR only reports the class. It never changes `level` or `rule_id`; the hook uses the fields from PR-5a on.
Edits of JADE's own source in a checkout stay plain high work (docs/12 decision 8).
"""

import sys
from pathlib import Path

import pytest

from jade import paths, policy

ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home()


def ho(tool, tool_input, cwd=None):
    return policy.assess(tool, tool_input, cwd or str(ROOT))


def bash(cmd, cwd=None):
    return ho("Bash", {"command": cmd}, cwd)


def write(path, content="x", tool="Write"):
    return ho(tool, {"file_path": str(path), "content": content, "new_string": content, "old_string": "y"})


# ------------------------------------------------------------------ the fields

def test_a_plain_call_has_no_class_and_is_not_human_only():
    for cmd in ("git status", "ls -la", "echo hello", "pytest -q"):
        a = bash(cmd)
        assert a.ho_ids == [] and not a.human_only and not a.elevated, cmd


def test_the_class_never_changes_level_or_rule_id():
    a = write(HOME / ".claude" / "settings.json")
    assert a.human_only and (a.level, a.rule_id) == ("high", "R-13")   # the rule that rated it before this field existed
    a = bash("cp /tmp/x ~/.local/bin/jade")
    assert a.human_only and (a.level, a.rule_id) == ("high", "R-13")
    a = ho("mcp__terminal__run_in_terminal", {"command": "ls", "cwd": str(ROOT)})
    assert a.human_only
    assert bash("echo x > /tmp/not-jade.txt").ho_ids == []


# ------------------------------------------------------------------ live wiring: tool writes

@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_a_write_of_the_config_state_or_hook_files_is_live_wiring(tool):
    for target in (paths.config_dir() / "config.json", paths.config_dir() / "local" / "risk.local.yaml", paths.home() / "jade.sqlite",
                   HOME / ".local" / "bin" / "jade", HOME / ".claude" / "settings.json", HOME / ".claude" / "hooks" / "x.sh",
                   Path(sys.prefix) / "lib" / "x.py"):
        assert "HO-wiring" in write(target, tool=tool).ho_ids, (tool, target)


def test_a_symlink_into_the_config_dir_is_live_wiring(tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(paths.config_dir())
    assert "HO-wiring" in write(link / "config.json").ho_ids


def test_edits_of_jades_own_source_in_a_checkout_are_plain_high_not_human_only():
    for target in (ROOT / "jade" / "hooks.py", ROOT / "policies" / "risk.yaml", ROOT / "registry" / "x.json", ROOT / "tests" / "x.py"):
        a = write(target)
        assert a.ho_ids == [], target


# ------------------------------------------------------------------ live wiring: shell

@pytest.mark.parametrize("cmd", [
    "cp /tmp/x ~/.local/bin/jade", "echo x > ~/.local/bin/jev", "mv /tmp/x ~/.claude/settings.json", "rm -rf ~/.claude/hooks",
    "echo x >> {cfg}/knowledge.md", "cp -r /tmp/d {cfg}", "ln -sfn /tmp/x {cfg}", "sed -i '' s/a/b/ {cfg}/config.json", "tee {home}/jade.sqlite",
])
def test_a_shell_write_of_live_wiring_is_reported(cmd):
    a = bash(cmd.format(cfg=paths.config_dir(), home=paths.home()))
    assert "HO-wiring" in a.ho_ids, cmd


@pytest.mark.parametrize("cmd", [
    "cat ~/.local/bin/jade", "ls ~/.claude/hooks", "cp ~/.claude/settings.json /tmp/backup.json", "grep -n hooks ~/.claude/settings.json",
    "git add jade/hooks.py", "cp /tmp/x /tmp/y", "echo x > {cfg}.notes", "jade check",
])
def test_reads_and_neighbours_are_not_live_wiring(cmd):
    a = bash(cmd.format(cfg=paths.config_dir()))
    assert "HO-wiring" not in a.ho_ids, cmd


# ------------------------------------------------------------------ mods

def test_a_write_under_the_mod_directories_is_host_config():
    for sub in ("dev-mods/m.js", "plugins/p/x.json", "mods/m.js"):
        assert "HO-mod" in write(HOME / ".claude" / sub).ho_ids, sub
    assert "HO-mod" in bash("cp /tmp/m.js ~/.claude/dev-mods/m.js").ho_ids
    assert "HO-mod" not in bash("ls ~/.claude/dev-mods").ho_ids
    assert write(HOME / ".claude" / "skills" / "x" / "SKILL.md").ho_ids == []


# ------------------------------------------------------------------ install from a checkout

@pytest.mark.parametrize("cmd", [
    f"uv tool install {ROOT}", f"uv tool upgrade --reinstall {ROOT}", f"uv pip install -e {ROOT}", f"pip install {ROOT}", f"pipx install {ROOT}",
    f"python3 -m pip install {ROOT}",
])
def test_an_install_built_from_a_jade_checkout_is_reported(cmd):
    assert "HO-checkout-install" in bash(cmd).ho_ids, cmd


def test_a_relative_checkout_operand_is_resolved_in_the_directory_of_the_call():
    assert "HO-checkout-install" in bash("uv tool install .", cwd=str(ROOT)).ho_ids
    assert "HO-checkout-install" in bash("pip install ..", cwd=str(ROOT / "jade")).ho_ids


@pytest.mark.parametrize("cmd", ["uv tool install ruff", "pip install requests", "uv pip install -r requirements.txt", "uv sync", "uv tool list", "pip list"])
def test_other_installs_are_not_reported(cmd):
    assert "HO-checkout-install" not in bash(cmd, cwd=str(ROOT)).ho_ids, cmd


def test_an_install_from_another_project_directory_is_not_reported(tmp_path):
    (tmp_path / "proj").mkdir()
    assert "HO-checkout-install" not in bash(f"uv tool install {tmp_path / 'proj'}").ho_ids


# ------------------------------------------------------------------ project settings that disable JADE

def test_a_project_settings_write_that_disables_or_hides_its_text_is_reported(tmp_path):
    f = tmp_path / "proj" / ".claude" / "settings.json"
    assert "HO-settings-shadow" in write(f, '{"disableAllHooks": true}').ho_ids
    assert "HO-settings-shadow" in write(f, '{"env": {"JADE_GATE_MODE": "off"}}').ho_ids
    assert "HO-settings-shadow" in write(f, "").ho_ids   # nothing readable: counts
    assert "HO-settings-shadow" in write(tmp_path / "proj" / ".claude" / "settings.local.json", '{"hooks": {}}').ho_ids


def test_a_project_settings_write_that_only_sets_permissions_is_not_reported(tmp_path):
    f = tmp_path / "proj" / ".claude" / "settings.json"
    assert "HO-settings-shadow" not in write(f, '{"permissions": {"allow": ["Bash(ls:*)"]}}').ho_ids
    assert write(tmp_path / "proj" / "notes.json", '{"disableAllHooks": true}').ho_ids == []


def test_a_shell_write_of_a_settings_file_is_reported(tmp_path):
    assert "HO-settings-shadow" in bash(f"echo '{{}}' > {tmp_path}/.claude/settings.json").ho_ids
    assert "HO-settings-shadow" not in bash(f"cat {tmp_path}/.claude/settings.json").ho_ids


# ------------------------------------------------------------------ state paths named in a script

def test_a_script_that_names_the_state_paths_is_reported(tmp_path):
    s = tmp_path / "go.sh"
    s.write_text("#!/bin/sh\nrm -f ~/.local/share/jade/jade.sqlite\n")
    assert "HO-state-path" in bash(f"bash {s}").ho_ids
    t = tmp_path / "ok.sh"
    t.write_text("#!/bin/sh\necho hello\n")
    assert "HO-state-path" not in bash(f"bash {t}").ho_ids


# ------------------------------------------------------------------ R-17 and the terminal panel

def test_r17_is_human_only_and_keeps_its_rule_id():
    a = bash("git config core.hooksPath /tmp/hooks")
    assert a.rule_id == "R-17" and "HO-R17" in a.ho_ids and a.level == "high"
    assert "HO-R17" not in bash("git config --get core.hooksPath").ho_ids


def test_a_script_typed_into_the_terminal_panel_is_human_only():
    a = ho("mcp__terminal__run_in_terminal", {"command": "bash /tmp/x.sh", "cwd": str(ROOT)})
    assert "HO-terminal" in a.ho_ids
    other = ho("mcp__terminal__read_terminal", {"tab_id": "1"})
    assert "HO-terminal" not in other.ho_ids
