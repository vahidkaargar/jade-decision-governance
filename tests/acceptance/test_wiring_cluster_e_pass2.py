"""Cluster E, second pass: the variants a verifier found after the first fix.

SI-11 read through quoting and name construction, SI-11 on the content a Write/Edit puts into a shell profile or an environment file, the
setext heading that follows the Jev section, and hook entries whose program is only *called* jade. Commands are classified, never run.
"""
import json

import pytest

from jade import hooks, ops, policy


def blocked(cmd):
    return policy.assess("Bash", {"command": cmd}).blocked


# ---- G05: spelling variants of the assignment ------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "export 'JADE_POLICY_DIR'=/tmp/e",
    'export "JADE_POLICY_DIR"=/tmp/e',
    'env "JADE_HOME"=/tmp/e jade check',
    'JADE_POLICY_""DIR=/tmp/e jade check',
    "JADE_POLICY_''DIR=/tmp/e jade check",
    "JADE_POLICY_\\DIR=/tmp/e jade check",
    "J=JADE_POLICY_DIR; env $J=/tmp/e jade check",
    'J=JADE_POLICY_DIR; export "$J=/tmp/e"',
    "J=JADE_HOME; export ${J}=/tmp/e",
    "export JADE_POLI${X}CY_DIR=/tmp/e",
    "export JADE_POLI$(true)CY_DIR=/tmp/e",
    "export $'\\x4aADE_HOME'=/tmp/e",
    "export JADE_HOME+=/x",
    "printf -v JADE_HOME %s /tmp/e",
    "read JADE_CONFIG <<< /tmp/e",
    "echo \"export 'JADE_POLICY_DIR'=/tmp/e\" >> ~/.zshrc",
    "echo 'export JADE_POLICY_\"\"DIR=/tmp/e' >> ~/.zprofile",
    "printf '%s\\n' 'export JADE_CONFIG=/tmp/e' | tee -a ~/.zshenv",
    "launchctl setenv 'JADE_HOME' /tmp/x",
])
def test_si11_reads_through_quoting_and_name_construction(cmd):
    assert "SI-11" in blocked(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "grep -n JADE_HOME README.md",
    "cat jade/paths.py",
    "uv run pytest -q",
    "git status",
    "echo $HOME=x",
    "env | grep -c JADE",
    "printenv JADE_HOME",
    "J=other; env $J=/tmp/e true",
])
def test_si11_does_not_block_commands_that_never_assign_a_gate_variable(cmd):
    assert "SI-11" not in blocked(cmd), cmd


def test_si11_through_the_hook_is_exit_two(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    for cmd in ("export 'JADE_POLICY_DIR'=/tmp/e", "J=JADE_POLICY_DIR; env $J=/tmp/e jade check"):
        r = hooks.pretool({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "sess-e2-1", "cwd": "/tmp"})
        assert r.exit_code == 2 and "SI-11" in r.stdout + r.stderr, cmd


# ---- G05: the Write/Edit tools put the assignment into a profile ------------------------------------------------------
HOME_PATHS = ["~/.zshrc", "/Users/x/.zprofile", "/Users/x/.zshenv", "/Users/x/.bashrc", "/Users/x/.bash_profile", "/Users/x/.profile",
              "/Users/x/.config/fish/config.fish", "/Users/x/Library/LaunchAgents/com.example.env.plist", "/etc/zshenv", "/Users/x/proj/.envrc",
              "/Users/x/proj/.env", "/Users/x/.config/environment.d/jade.conf"]


@pytest.mark.parametrize("path", HOME_PATHS)
@pytest.mark.parametrize("tool,key", [("Write", "content"), ("Edit", "new_string"), ("NotebookEdit", "new_source")])
def test_writing_a_gate_variable_into_a_profile_or_env_file_is_blocked(path, tool, key):
    body = "export PATH=/x:$PATH\nexport JADE_POLICY_DIR=/tmp/e\n"
    inp = {"file_path": path, key: body} if tool != "NotebookEdit" else {"notebook_path": path, key: body}
    assert "SI-11" in policy.assess(tool, inp).blocked, (tool, path)


def test_multiedit_and_json_and_plist_spellings_into_a_profile_are_blocked():
    edits = {"file_path": "/Users/x/.zshrc", "edits": [{"old_string": "a", "new_string": "export 'JADE_CONFIG'=/tmp/e"}]}
    assert "SI-11" in policy.assess("MultiEdit", edits).blocked
    plist = "<dict><key>EnvironmentVariables</key><dict><key>JADE_HOME</key><string>/tmp/e</string></dict></dict>"
    assert "SI-11" in policy.assess("Write", {"file_path": "/Users/x/Library/LaunchAgents/a.plist", "content": plist}).blocked
    js = '{"env": {"JADE_GATE_MODE": "off"}}'
    assert "SI-11" in policy.assess("Write", {"file_path": "/Users/x/proj/.envrc", "content": js}).blocked


def test_the_same_text_elsewhere_and_other_profile_edits_stay_allowed():
    assert "SI-11" not in policy.assess("Write", {"file_path": "/Users/x/proj/docs/notes.md", "content": "set JADE_POLICY_DIR=/tmp/e to test"}).blocked
    assert "SI-11" not in policy.assess("Write", {"file_path": "/Users/x/proj/tests/conftest.py", "content": 'os.environ["JADE_HOME"] = "x"\nJADE_HOME=1'}).blocked
    assert "SI-11" not in policy.assess("Edit", {"file_path": "/Users/x/.zshrc", "old_string": "export JADE_HOME=/old", "new_string": "export PATH=/x:$PATH"}).blocked
    assert "SI-11" not in policy.assess("Write", {"file_path": "/Users/x/.zshrc", "content": "export EDITOR=vim\n"}).blocked


def test_write_to_a_profile_through_the_hook_is_exit_two(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    r = hooks.pretool({"tool_name": "Write", "tool_input": {"file_path": "/Users/x/.zshrc", "content": "export JADE_POLICY_DIR=/tmp/e\n"},
                       "session_id": "sess-e2-2", "cwd": "/tmp"})
    assert r.exit_code == 2


def test_a_relative_profile_path_after_a_cwd_is_read_too():
    a = policy.assess("Write", {"file_path": ".zshrc", "content": "export JADE_HOME=/tmp/e"}, "/Users/x")
    assert "SI-11" in a.blocked


# ---- F40: setext headings end the Jev section -------------------------------------------------------------------------
def apply(tmp_path, text):
    md = tmp_path / "CLAUDE.md"
    md.write_text(text)
    ops.claude_md_apply(md, stdin_isatty=True)
    return md.read_text()


@pytest.mark.parametrize("under", ["============", "-----", "="])
def test_a_setext_heading_after_the_jev_section_is_kept(human_env, tmp_path, under):
    out = apply(tmp_path, ops.JEV_HEADING + f"\n- old jev\n\nGlobal rules\n{under}\nIMPORTANT\n\n## B\nb\n")
    assert "Global rules" in out and "IMPORTANT" in out and "## B\nb" in out and "old jev" not in out


def test_a_multi_line_setext_heading_is_kept_whole(human_env, tmp_path):
    out = apply(tmp_path, ops.JEV_HEADING + "\n- old\n\nFirst line\nsecond line\n-----\nbody\n")
    assert "First line\nsecond line\n-----\nbody" in out and "old" not in out


def test_a_thematic_break_or_list_underline_is_not_a_heading(human_env, tmp_path):
    text = ops.JEV_HEADING + "\n- old jev\n  more of it\n---\nstill jev\n\npara\n\n---\nafter break\n\n## B\nb\n"
    out = apply(tmp_path, text)
    assert "still jev" not in out and "after break" not in out and "## B\nb" in out


def test_a_setext_jev_heading_is_found(human_env, tmp_path):
    out = apply(tmp_path, "Jev: verify outputs and decide (global, all sessions)\n-------\n- old\n\n## B\nb\n")
    assert "old" not in out and "## B\nb" in out and out.count("verify outputs and decide") == 1


def test_setext_looking_lines_inside_a_fence_are_not_headings():
    heads = list(ops.markdown_headings("```\nTitle\n=====\n```\nReal\n----\n"))
    assert [(i, lvl, t) for i, lvl, t in heads] == [(4, 2, "Real")]


# ---- hooks install/uninstall: only JADE's own program is JADE's ------------------------------------------------------
def settings_with(tmp_path, *cmds):
    (tmp_path / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": c} for c in cmds]}]}}))


def pre_cmds(tmp_path):
    return [h["command"] for g in json.loads((tmp_path / "settings.json").read_text())["hooks"].get("PreToolUse", []) for h in g["hooks"]]


@pytest.mark.parametrize("foreign", ["/opt/other/jade hook pretool", "/usr/local/bin/jade hook pretool", "'/opt/with space/jade' hook pretool"])
def test_a_different_program_called_jade_survives_install_and_uninstall(human_env, jade_stub, tmp_path, foreign):
    settings_with(tmp_path, foreign)
    assert not ops.hooks_status()["PreToolUse"]
    ops.hooks_install(stdin_isatty=True)
    assert foreign in pre_cmds(tmp_path) and ops.hooks_status()["PreToolUse"]
    ops.hooks_uninstall(stdin_isatty=True)
    assert pre_cmds(tmp_path) == [foreign]


def test_jade_own_launchers_are_still_recognised(human_env, jade_stub, tmp_path, monkeypatch):
    mine = str(jade_stub / "jade")
    uvtool = "/Users/x/.local/share/uv/tools/jade-decision-governance/bin/jade"
    settings_with(tmp_path, f"{mine} hook pretool", f"{uvtool} hook pretool", "jade hook pretool")
    assert ops.hooks_uninstall(stdin_isatty=True) == 3 and pre_cmds(tmp_path) == []


def test_a_bare_jade_that_resolves_to_another_program_is_not_ours(human_env, jade_stub, tmp_path, monkeypatch):
    other = tmp_path / "otherbin"
    other.mkdir()
    (other / "jade").write_text("#!/bin/sh\necho some other tool\n")
    (other / "jade").chmod(0o755)
    monkeypatch.setenv("PATH", f"{other}:/usr/bin:/bin")
    settings_with(tmp_path, "jade hook pretool")
    assert ops.hooks_uninstall(stdin_isatty=True) == 0 and pre_cmds(tmp_path) == ["jade hook pretool"]


def test_an_existing_foreign_binary_named_jade_is_not_ours(human_env, jade_stub, tmp_path):
    d = tmp_path / "elsewhere"
    d.mkdir()
    (d / "jade").write_text("#!/bin/sh\necho other\n")
    (d / "jade").chmod(0o755)
    settings_with(tmp_path, f"{d / 'jade'} hook pretool")
    assert ops.hooks_uninstall(stdin_isatty=True) == 0


def test_another_jade_binary_that_is_really_this_package_is_ours(human_env, jade_stub, tmp_path):
    import sys

    d = tmp_path / "venvbin"
    d.mkdir()
    (d / "jade").write_text(f'#!/bin/sh\nexec "{sys.executable}" -m jade.cli "$@"\n')
    (d / "jade").chmod(0o755)
    settings_with(tmp_path, f"{d / 'jade'} hook pretool")
    assert ops.hooks_uninstall(stdin_isatty=True) == 1


def test_names_that_only_contain_jade_stay_foreign(human_env, jade_stub, tmp_path):
    settings_with(tmp_path, "/usr/local/bin/my-jade hook stop", "/opt/tools/jade-lint hook pretool")
    assert ops.hooks_uninstall(stdin_isatty=True) == 0
