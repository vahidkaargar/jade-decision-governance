"""REL-2: `jade setup`, the human-only, idempotent first-run wizard.

Every test runs with a temp HOME and a temp JADE_HOME (conftest), a scripted terminal and a fake launchd; nothing touches the real ~/.claude,
~/.config/jade, ~/.local/share/jade, Keychain or launchctl."""

import json
import os
import plistlib
from pathlib import Path

import pytest

from jade import cli, ledger, ops, paths, policy, setup, util

ROOT = Path(__file__).resolve().parents[1]
OLD_CLAUDE_MD = f"# Notes\n\nkeep me\n\n{ops.JEV_HEADING}\n\nold text of the section\n\n## Other\n\nafter\n"
OK_ROWS = [("ledger", True, "ok"), ("hooks installed", True, "four")]
BAD_ROWS = [("ledger", True, "ok"), ("hook wiring", False, "dead")]


@pytest.fixture
def human(monkeypatch):
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fresh macOS-like machine: temp HOME with a ~/.claude/CLAUDE.md that carries the old Jev section, a key in the environment, a fake
    launchd (`loaded` holds the labels it has bootstrapped) and a fake `jade check`."""
    home = tmp_path / "hm"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("JADE_BIN", str(home / ".local" / "bin" / "jade"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-NEVER-PRINT-ME")
    (home / ".claude" / "CLAUDE.md").write_text(OLD_CLAUDE_MD)
    m = type("M", (), {})()
    m.home, m.loaded, m.bootstrapped, m.rows, m.check_args = home, set(), [], OK_ROWS, []
    monkeypatch.setattr(setup, "_platform", lambda: "darwin")
    monkeypatch.setattr(setup, "_keychain_has_key", lambda: False)
    monkeypatch.setattr(ops, "_launchctl_loaded", lambda label: label in m.loaded)

    def bootstrap(plist, label):
        m.bootstrapped.append(label)
        m.loaded.add(label)
        return True, ""

    monkeypatch.setattr(setup, "_launchctl_bootstrap", bootstrap)

    def fake_check(live=True, provider=None):
        m.check_args.append(live)
        return m.rows

    monkeypatch.setattr(ops, "check", fake_check)
    return m


class Term:
    """A scripted terminal: answers in order, records the prompts, fails the test on a prompt nobody expected."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return self.answers.pop(0)


def run(term=None, **kw):
    out: list[str] = []
    rep = setup.run(stdin_isatty=True, input_fn=term or Term(), say=out.append, **kw)
    return rep, "\n".join(out)


def snapshot(m, tmp_path):
    """Contents of every file a setup run may write, and the names of all files under HOME and the config directory."""
    files = {}
    for root in (m.home, tmp_path / "config", tmp_path / "settings.json", tmp_path / "LaunchAgents"):
        for p in ([root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []):
            files[str(p)] = p.read_bytes() if p.is_file() and not p.is_symlink() else os.readlink(p) if p.is_symlink() else None
    return files


def no_ledger_state(tmp_path):
    assert not (tmp_path / "home").exists(), "the JADE home was created"
    assert not (tmp_path / "config").exists(), "the configuration directory was created"


# ---- fresh machine -------------------------------------------------------------------------------------------------------------------
def test_a_fresh_machine_gets_every_step_after_one_confirmation_each(human, machine, tmp_path):
    term = Term(*["y"] * 7)
    rep, out = run(term)
    assert rep.rc == 0, out
    assert {o.key: o.outcome for o in rep.outcomes if o.key != "check"} == {
        "prereq": "ok", "gate": "applied", "hooks": "applied", "claude-md": "applied", "skill-file": "applied", "skill-link": "applied",
        "launchd-plist": "applied", "launchd-load": "applied"}
    assert len(term.prompts) == 7 and not term.answers
    assert paths.config()["gate_mode"] == "advisory"
    assert all(ops.hooks_status().values()) and ops.pretool_matcher_gaps() == []
    md = (machine.home / ".claude" / "CLAUDE.md").read_text()
    assert ops.claude_md_section().rstrip("\n") in md and "old text of the section" not in md and "keep me" in md and "## Other" in md
    skill = machine.home / ".agents" / "jade" / "SKILL.md"
    assert skill.read_text() == (ROOT / "skills" / "jade" / "SKILL.md").read_text()
    link = machine.home / ".claude" / "skills" / "jade"
    assert link.is_symlink() and os.path.realpath(link) == os.path.realpath(skill.parent)
    plist = ops.launch_agents_dir() / "com.jade.maintain.plist"
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"][1:] == ["maintain"]
    assert machine.bootstrapped == ["com.jade.maintain"]
    assert machine.check_args == [False]
    assert len(list((machine.home / ".claude").glob("*.bak-jade-*"))) == 1   # CLAUDE.md backed up; settings.json did not exist


def test_every_prompt_is_preceded_by_the_exact_change_and_its_undo(human, machine):
    term = Term(*["y"] * 7)
    rep, out = run(term)
    assert out.count("change:") == 7 and out.count("undo:") == 7 and all("Apply this change? [y/N]" in p for p in term.prompts)
    assert '"gate_mode": "advisory"' in out and "jade gate enforce" in out
    assert "jade hooks uninstall" in out and "PreToolUse" in out and "SessionStart" in out
    assert ".bak-jade-" in out and "launchctl bootout" in out and "launchctl bootstrap" in out


def test_the_key_value_is_never_printed_and_never_read(human, machine, monkeypatch):
    rep, out = run(Term(*["y"] * 7))
    assert "sk-test-NEVER-PRINT-ME" not in out
    assert "OPENROUTER_API_KEY" in out   # named, as present in the environment


def test_yes_skips_the_prompts_but_changes_the_same_things(human, machine):
    rep, out = run(Term(), yes=True)   # a Term with no answers fails on any prompt
    assert rep.rc == 0 and [o.outcome for o in rep.outcomes if o.key in ("gate", "hooks", "claude-md", "skill-file", "skill-link", "launchd-plist", "launchd-load")] == ["applied"] * 7
    assert out.count("change:") == 7   # still shown first


def test_the_summary_says_advisory_is_the_start_and_where_it_is_documented(human, machine):
    rep, out = run(Term(*["y"] * 7))
    tail = out[out.index("Summary"):]
    assert "advisory" in tail and "enforce" in tail and "NOT set" in tail and "docs/08" in tail and "new Claude Code session" in tail


def test_every_step_records_a_setup_event_in_the_ledger_meta(human, machine):
    rep, _ = run(Term(*["y"] * 7))
    db = ledger.open()
    for key in ("prereq", "gate", "hooks", "claude-md", "skill-file", "skill-link", "launchd-plist", "launchd-load", "check"):
        rec = json.loads(db.meta_get(f"setup.{key}"))
        assert rec["outcome"] and rec["ts"] and rec["version"]
    last = json.loads(db.meta_get("setup.last_run"))
    assert last["outcomes"]["gate"] == "applied"
    assert "sk-test" not in json.dumps(last)
    db.close()


# ---- second run, partial state -------------------------------------------------------------------------------------------------------
def test_a_second_run_skips_everything_and_changes_nothing(human, machine, tmp_path):
    run(Term(*["y"] * 7))
    before = snapshot(machine, tmp_path)
    rep, out = run(Term())   # no prompt may appear
    assert rep.rc == 0
    outcomes = {o.key: o.outcome for o in rep.outcomes}
    assert {k: v for k, v in outcomes.items() if k not in ("prereq", "check")} == dict.fromkeys(
        ("gate", "hooks", "claude-md", "skill-file", "skill-link", "launchd-plist", "launchd-load"), "already_done")
    assert snapshot(machine, tmp_path) == before   # no file, link or backup changed
    assert machine.bootstrapped == ["com.jade.maintain"]   # not bootstrapped again
    assert "already done" in out


def test_partial_state_applies_only_what_is_missing(human, machine):
    ops.set_gate_mode("advisory", stdin_isatty=True)
    ops.hooks_install(stdin_isatty=True)
    term = Term(*["y"] * 5)
    rep, _ = run(term)
    out = {o.key: o.outcome for o in rep.outcomes}
    assert out["gate"] == out["hooks"] == "already_done"
    assert out["claude-md"] == out["skill-file"] == out["skill-link"] == out["launchd-plist"] == out["launchd-load"] == "applied"
    assert len(term.prompts) == 5


def test_a_configured_enforce_mode_is_not_lowered_to_advisory(human, machine):
    ops.set_gate_mode("enforce", stdin_isatty=True)
    rep, out = run(Term(*["y"] * 6))
    assert {o.key: o.outcome for o in rep.outcomes}["gate"] == "already_done" and paths.config()["gate_mode"] == "enforce"


def test_gate_mode_off_is_offered_advisory(human, machine):
    ops.set_gate_mode("off", stdin_isatty=True)
    rep, _ = run(Term(*["y"] * 7))
    assert paths.config()["gate_mode"] == "advisory"


def test_a_hook_install_with_an_old_matcher_is_not_already_done(human, machine, tmp_path):
    ops.hooks_install(stdin_isatty=True)
    p = ops.settings_path()
    doc = json.loads(p.read_text())
    doc["hooks"]["PreToolUse"][0]["matcher"] = "Bash"
    p.write_text(json.dumps(doc))
    assert setup.state_of("hooks").kind == "todo"
    rep, _ = run(Term(*["y"] * 7))
    assert ops.pretool_matcher_gaps() == []


def test_a_claude_md_that_already_has_the_shipped_section_is_done(human, machine):
    (machine.home / ".claude" / "CLAUDE.md").write_text(f"# Notes\n\n{ops.claude_md_section()}")
    assert setup.state_of("claude-md").kind == "done"


# ---- declined ------------------------------------------------------------------------------------------------------------------------
def test_a_declined_step_changes_nothing_and_the_rest_continue(human, machine, tmp_path):
    term = Term("y", "n", "", "y", "y", "y", "y")   # gate y, hooks n, claude-md <enter>, skill y y, plist y, load y
    rep, out = run(term)
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["hooks"] == o["claude-md"] == "declined" and o["gate"] == o["skill-file"] == "applied"
    assert rep.rc == 0
    assert not ops.settings_path().exists() and not any(ops.hooks_status().values())
    assert (machine.home / ".claude" / "CLAUDE.md").read_text() == OLD_CLAUDE_MD
    tail = out[out.index("Summary"):]
    assert "hooks" in tail and "not active" in tail.lower()


def test_a_declined_plist_means_launchctl_is_not_offered(human, machine):
    term = Term("y", "y", "y", "y", "y", "n")
    rep, out = run(term)
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["launchd-plist"] == "declined" and o["launchd-load"] == "skipped" and machine.bootstrapped == []
    assert not (ops.launch_agents_dir() / "com.jade.maintain.plist").exists()


def test_an_end_of_input_declines(human, machine):
    def eof(prompt=""):
        raise EOFError

    out: list[str] = []
    rep = setup.run(stdin_isatty=True, input_fn=eof, say=out.append)
    assert all(o.outcome != "applied" for o in rep.outcomes) and not ops.settings_path().exists()


# ---- refusals ------------------------------------------------------------------------------------------------------------------------
def test_without_a_terminal_it_refuses_before_touching_anything(machine, tmp_path):
    with pytest.raises(ops.HumanOnly, match="terminal"):
        setup.run(stdin_isatty=False, input_fn=Term(), say=lambda s: None)
    no_ledger_state(tmp_path)
    assert (machine.home / ".claude" / "CLAUDE.md").read_text() == OLD_CLAUDE_MD and not (machine.home / ".agents").exists()


def test_yes_does_not_waive_the_terminal(machine, tmp_path):
    with pytest.raises(ops.HumanOnly):
        setup.run(yes=True, stdin_isatty=False, input_fn=Term(), say=lambda s: None)
    no_ledger_state(tmp_path)


def test_an_agent_ancestor_is_refused(machine, monkeypatch, tmp_path):
    for k in util.AGENT_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(util, "agent_ancestor", lambda: "it runs under an agent process (claude, pid 7)")
    with pytest.raises(ops.HumanOnly, match="agent process"):
        setup.run(yes=True, stdin_isatty=True, input_fn=Term(), say=lambda s: None)
    no_ledger_state(tmp_path)


def test_an_agent_environment_is_refused(machine, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(ops.HumanOnly, match="agent harness"):
        setup.run(yes=True, stdin_isatty=True, input_fn=Term(), say=lambda s: None)
    no_ledger_state(tmp_path)


def test_the_cli_refuses_without_a_terminal_and_exits_1(machine, capsys, tmp_path):
    assert cli.main(["setup"]) == 1   # pytest's stdin is not a terminal
    assert "jade:" in capsys.readouterr().err
    assert cli.main(["setup", "--yes"]) == 1
    no_ledger_state(tmp_path)


# ---- status --------------------------------------------------------------------------------------------------------------------------
def test_status_reports_what_is_missing_and_changes_nothing(machine, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")   # read-only: an agent may run it
    before = snapshot(machine, tmp_path)
    assert cli.main(["setup", "--status"]) == 1
    out = capsys.readouterr().out
    for word in ("gate", "hooks", "CLAUDE.md", "skill", "maintenance", "missing"):
        assert word in out
    assert "sk-test-NEVER-PRINT-ME" not in out
    assert snapshot(machine, tmp_path) == before
    no_ledger_state(tmp_path)
    assert not (machine.home / ".agents").exists() and not (machine.home / ".claude" / "skills").exists()


def test_status_after_setup_is_all_done_and_exits_zero(human, machine, capsys):
    run(Term(*["y"] * 7))
    capsys.readouterr()
    assert cli.main(["setup", "--status"]) == 0
    out = capsys.readouterr().out
    assert "missing" not in out.lower() and out.count("done") >= 7


# ---- prerequisites -------------------------------------------------------------------------------------------------------------------
def test_a_missing_key_says_how_to_add_it_and_does_not_fail_setup(human, machine, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    rep, out = run(Term(*["y"] * 7))
    assert {o.key: o.outcome for o in rep.outcomes}["prereq"] == "missing" and rep.rc == 0
    assert "OPENROUTER_API_KEY" in out and "security add-generic-password" in out and "Keychain" in out


def test_the_keychain_probe_asks_for_attributes_only(monkeypatch):
    calls = []

    class R:
        returncode = 0
        stdout = b"keychain: secret-looking-attributes"

    monkeypatch.setattr(setup, "_platform", lambda: "darwin")
    monkeypatch.setattr(setup.shutil, "which", lambda n: "/usr/bin/security")
    monkeypatch.setattr(setup.subprocess, "run", lambda argv, **kw: calls.append(argv) or R())
    assert setup._keychain_has_key() is True
    assert calls[0][1] == "find-generic-password"
    assert "-w" not in calls[0] and "-g" not in calls[0] and "OPENROUTER_API_KEY" in calls[0]


def test_the_keychain_probe_is_false_off_macos_or_without_security(monkeypatch):
    monkeypatch.setattr(setup, "_platform", lambda: "linux")
    assert setup._keychain_has_key() is False
    monkeypatch.setattr(setup, "_platform", lambda: "darwin")
    monkeypatch.setattr(setup.shutil, "which", lambda n: None)
    assert setup._keychain_has_key() is False


def test_a_key_in_the_keychain_counts(human, machine, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr(setup, "_keychain_has_key", lambda: True)
    rep, out = run(Term(*["y"] * 7))
    assert {o.key: o.outcome for o in rep.outcomes}["prereq"] == "ok" and "Keychain" in out


# ---- skill ---------------------------------------------------------------------------------------------------------------------------
def test_missing_skill_data_is_reported_and_nothing_is_created(human, machine, monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "skill_source", lambda: tmp_path / "nowhere" / "SKILL.md")
    rep, out = run(Term(*["y"] * 5))
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["skill-file"] == "blocked" and o["skill-link"] in ("blocked", "skipped") and rep.rc == 1
    assert not (machine.home / ".agents").exists() and not (machine.home / ".claude" / "skills").exists()
    assert "reinstall" in out.lower()
    assert o["gate"] == o["hooks"] == o["claude-md"] == "applied"   # the other steps still ran


def test_an_older_skill_file_is_replaced_after_a_backup(human, machine):
    """A release that improves the skill must reach installed users: a different SKILL.md is shown as a change, backed up and replaced on a yes."""
    d = machine.home / ".agents" / "jade"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("my own version\n")
    rep, out = run(Term(*["y"] * 7))
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["skill-file"] == "applied" and rep.rc == 0
    assert (d / "SKILL.md").read_text() == setup.skill_source().read_text()
    (backup,) = d.glob("SKILL.md.bak-jade-*")
    assert backup.read_text() == "my own version\n"
    assert "differs" in out and "backup" in out
    assert (machine.home / ".claude" / "skills" / "jade").is_symlink()


def test_a_declined_skill_replacement_changes_nothing(human, machine):
    d = machine.home / ".agents" / "jade"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("my own version\n")
    rep, _ = run(Term("y", "y", "y", "n", "y", "y", "y"))
    assert {x.key: x.outcome for x in rep.outcomes}["skill-file"] == "declined"
    assert (d / "SKILL.md").read_text() == "my own version\n" and not list(d.glob("*.bak-jade-*"))


def test_a_skill_file_that_is_a_link_is_never_replaced(human, machine, tmp_path):
    d = machine.home / ".agents" / "jade"
    d.mkdir(parents=True)
    other = tmp_path / "elsewhere.md"
    other.write_text("not ours\n")
    (d / "SKILL.md").symlink_to(other)
    rep, out = run(Term(*["y"] * 5))
    assert {x.key: x.outcome for x in rep.outcomes}["skill-file"] == "blocked" and rep.rc == 1
    assert other.read_text() == "not ours\n" and (d / "SKILL.md").is_symlink()


@pytest.mark.parametrize("kind", ["directory", "file", "other-link", "dangling-link"])
def test_an_existing_different_path_at_the_link_is_never_replaced(human, machine, kind):
    link = machine.home / ".claude" / "skills" / "jade"
    link.parent.mkdir(parents=True)
    if kind == "directory":
        link.mkdir()
        (link / "keep").write_text("x")
    elif kind == "file":
        link.write_text("x")
    elif kind == "other-link":
        (machine.home / "elsewhere").mkdir()
        link.symlink_to(machine.home / "elsewhere")
    else:
        link.symlink_to(machine.home / "nowhere")
    before = os.readlink(link) if link.is_symlink() else None
    rep, out = run(Term(*["y"] * 6))
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["skill-link"] == "blocked" and rep.rc == 1
    assert (os.readlink(link) if link.is_symlink() else None) == before
    if kind == "directory":
        assert (link / "keep").read_text() == "x"


def test_an_existing_correct_link_and_file_are_already_done(human, machine):
    d = machine.home / ".agents" / "jade"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text((ROOT / "skills" / "jade" / "SKILL.md").read_text())
    (machine.home / ".claude" / "skills").mkdir()
    (machine.home / ".claude" / "skills" / "jade").symlink_to(d)
    assert setup.state_of("skill-file").kind == "done" and setup.state_of("skill-link").kind == "done"


def test_the_skill_source_is_the_packaged_copy_of_the_checkout_skill():
    assert setup.skill_source().read_text() == (ROOT / "skills" / "jade" / "SKILL.md").read_text()


# ---- CLAUDE.md -----------------------------------------------------------------------------------------------------------------------
def test_no_claude_md_is_not_applicable_and_not_an_error(human, machine):
    (machine.home / ".claude" / "CLAUDE.md").unlink()
    rep, out = run(Term(*["y"] * 6))
    assert {o.key: o.outcome for o in rep.outcomes}["claude-md"] == "skipped" and rep.rc == 0
    assert "claude-md show" in out
    assert not (machine.home / ".claude" / "CLAUDE.md").exists()


def test_a_claude_md_without_the_section_is_not_applicable(human, machine):
    (machine.home / ".claude" / "CLAUDE.md").write_text("# nothing here\n")
    rep, _ = run(Term(*["y"] * 6))
    assert {o.key: o.outcome for o in rep.outcomes}["claude-md"] == "skipped" and rep.rc == 0


def test_a_claude_md_with_the_section_twice_is_refused_untouched(human, machine):
    text = OLD_CLAUDE_MD + f"\n{ops.JEV_HEADING}\n\nsecond\n"
    (machine.home / ".claude" / "CLAUDE.md").write_text(text)
    rep, out = run(Term(*["y"] * 6))
    assert {o.key: o.outcome for o in rep.outcomes}["claude-md"] == "blocked" and rep.rc == 1
    assert (machine.home / ".claude" / "CLAUDE.md").read_text() == text


# ---- launchd and platform ------------------------------------------------------------------------------------------------------------
def test_off_macos_the_launchd_steps_are_skipped(human, machine, monkeypatch):
    monkeypatch.setattr(setup, "_platform", lambda: "linux")
    rep, out = run(Term(*["y"] * 5))
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["launchd-plist"] == o["launchd-load"] == "skipped" and rep.rc == 0
    assert not ops.launch_agents_dir().exists() and machine.bootstrapped == []
    assert "macOS" in out


def test_a_plist_at_our_path_that_is_not_a_maintain_job_is_not_overwritten(human, machine):
    d = ops.launch_agents_dir()
    d.mkdir(parents=True)
    (d / "com.jade.maintain.plist").write_text("not a plist")
    rep, _ = run(Term(*["y"] * 5))
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["launchd-plist"] == "blocked" and rep.rc == 1 and (d / "com.jade.maintain.plist").read_text() == "not a plist"


def test_a_failed_bootstrap_is_reported_and_fails_the_run(human, machine, monkeypatch):
    monkeypatch.setattr(setup, "_launchctl_bootstrap", lambda plist, label: (False, "Bootstrap failed: 5"))
    rep, out = run(Term(*["y"] * 7))
    assert {o.key: o.outcome for o in rep.outcomes}["launchd-load"] == "failed" and rep.rc == 1 and "Bootstrap failed" in out


def test_launchctl_bootstrap_runs_launchctl_with_an_argument_list(monkeypatch, tmp_path):
    calls = []

    class R:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(setup.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or R())
    ok, _ = setup._launchctl_bootstrap(tmp_path / "x.plist", "com.jade.maintain")
    argv, kw = calls[0]
    assert ok and argv == ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(tmp_path / "x.plist")] and not kw.get("shell")


# ---- failures and the final check ----------------------------------------------------------------------------------------------------
def test_a_failing_final_check_fails_the_run(human, machine):
    machine.rows = BAD_ROWS
    rep, out = run(Term(*["y"] * 7))
    assert rep.rc == 1 and {o.key: o.outcome for o in rep.outcomes}["check"] == "check_failed"


def test_an_unreadable_settings_file_fails_that_step_only(human, machine):
    ops.settings_path().write_text("{ not json")
    rep, out = run(Term(*["y"] * 7))
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["hooks"] == "failed" and o["gate"] == "applied" and o["skill-file"] == "applied" and rep.rc == 1
    assert ops.settings_path().read_text() == "{ not json"


def test_a_ledger_that_cannot_be_opened_does_not_stop_the_setup(human, machine, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "open", boom)
    rep, out = run(Term(*["y"] * 7))
    assert rep.rc == 0 and "could not record" in out and paths.config()["gate_mode"] == "advisory"


# ---- SI-09 ---------------------------------------------------------------------------------------------------------------------------
def si09(cmd: str) -> bool:
    return "SI-09" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


@pytest.mark.parametrize("cmd", ["jade setup", "jade setup --yes", "python3 -c 'import jade.setup as s; s.run(yes=True)'",
                                 "python3 -c 'from jade import setup; setup.run(yes=True)'", "python -m jade.cli setup --yes"])
def test_si09_blocks_setup_and_the_module(cmd):
    assert si09(cmd)


@pytest.mark.parametrize("cmd", ["jade setup --status", "jade setup --help", "uv run jade setup --status", "python -m jade.cli setup --status", "make setup",
                                 "cat jade/setup.py", "ls jade setup.py"])
def test_si09_lets_the_read_only_status_and_harmless_twins_through(cmd):
    assert not si09(cmd)


def test_si09_still_blocks_status_mixed_with_a_real_run():
    assert si09("jade setup --status; jade setup --yes")


# ---- packaging -----------------------------------------------------------------------------------------------------------------------
def test_the_wheel_ships_the_skill_next_to_the_other_packaged_data():
    import tomllib

    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]["skills"] == "jade/_skills"


# ---- a configuration file that is not usable is never rewritten ---------------------------------------------------------------------
@pytest.mark.parametrize("text", ['{"gate_mode": "advisory", "secret_overlay_dir": "/x",}', '["a"]', "null", "", "\xff"])
def test_an_unreadable_or_non_object_config_json_is_refused_and_left_alone(human, machine, capsys, text):
    cfg = paths.config_dir() / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_bytes(text.encode("latin-1"))
    before = cfg.read_bytes()
    rep, out = run(Term(*["y"] * 7))
    o = {x.key: x.outcome for x in rep.outcomes}
    assert o["gate"] == "blocked" and rep.rc == 1
    assert cfg.read_bytes() == before and not list(cfg.parent.glob("config.json.*"))
    assert "config.json" in out and "refused" in out
    assert setup.status() == 1   # read-only status reports it instead of crashing
    assert "REFUSED" in capsys.readouterr().out


def test_a_valid_config_json_keeps_its_other_keys_when_the_gate_is_set(human, machine):
    cfg = paths.config_dir() / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('{"secret_overlay_dir": "/x"}')
    rep, _ = run(Term(*["y"] * 7))
    assert {x.key: x.outcome for x in rep.outcomes}["gate"] == "applied"
    assert json.loads(cfg.read_text()) == {"secret_overlay_dir": "/x", "gate_mode": "advisory"}
