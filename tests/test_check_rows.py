"""V6-10: `jade check` and `jade hooks install` say what is true, at level `off` and in every gate mode and level pair, and say "unknown" for what JADE
cannot see. A row is (name, ok, detail) where ok is True (ok), False (FAIL, the exit code is non-zero, as before) or None (WARN: not a failure, but not
safe either, or not known). Only rule ids and levels are printed, never a command. No default and no blocking behaviour changes here (that is V6-16)."""

import json
import subprocess

import pytest

import jade
from jade import cli, hooks, ops, paths, policy, signing, util

MODES = paths.GATE_MODES
LEVELS = (None, "off", "low", "medium", "high")
NEW_ROWS = ("what blocks", "host mode bypass", "version vs main", "pre-push guard", "remote branch protection")
GOV = sorted(hooks.GOVERNANCE_INVARIANTS)


@pytest.fixture(autouse=True)
def human(monkeypatch):
    """`jade level` and `jade gate` need a human terminal; this suite runs under an agent."""
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


def invariant_ids() -> list[str]:
    return sorted(i["id"] for i in policy.safety()["invariants"])


def setup_pair(mode: str, level: str | None) -> None:
    if level is not None:
        ops.set_level(level, stdin_isatty=True)   # a recorded level: a raw config write would be the "not recorded" FAIL row
    ops.set_gate_mode(mode, stdin_isatty=True)


def rows() -> dict[str, tuple]:
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}


def blocks(mode: str, sid: str) -> bool:
    """What the hook really does with a call that hits invariant `sid` (gate mode off: pretool returns before any verdict)."""
    return mode != "off" and hooks._verdict(mode, "x", [sid]).exit_code == 2


def risk_gated(mode: str, level: str | None) -> bool:
    """pretool hands a high-risk call to `_gate` in advisory mode at level off, else in the gate mode."""
    return mode != "off" and ("advisory" if level == "off" else mode) == "enforce"


# ------------------------------------------------------------------ the table over every gate mode and level pair

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("level", LEVELS)
def test_what_blocks_row_matches_the_hook_for_every_pair(mode, level):
    setup_pair(mode, level)
    ok, detail = rows()["what blocks"]
    ids = invariant_ids()
    firm = [s for s in ids if blocks(mode, s)]
    soft = [s for s in ids if not blocks(mode, s)]
    gated = risk_gated(mode, level)
    if not soft and gated:
        assert ok is True, detail
        assert "every safety invariant" in detail and "risk" in detail
    else:
        assert ok is None, detail   # never "ok" when anything only logs, never FAIL (the exit code does not move)
        for s in soft:
            assert s in detail, (s, detail)
        if not firm:
            assert "nothing blocks" in detail
        else:
            assert ", ".join(firm) + " block" in detail
        if not gated:
            assert "risk rule" in detail and ("not even logged" if mode == "off" else "only log") in detail


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("level", LEVELS)
def test_no_new_row_fails_and_the_exit_code_only_counts_fail_rows(mode, level, capsys):
    setup_pair(mode, level)
    got = ops.check(live=False)
    by = {n: ok for n, ok, _ in got}
    for n in NEW_ROWS:
        assert by[n] is not False, n
    assert by["gate mode"] is (False if mode == "off" else (True if mode == "enforce" else None))   # off fails as on main; advisory is a warning now
    assert by["level"] is not False   # a recorded level never fails
    failed = [n for n, ok, _ in got if ok is False]
    assert ops.print_check(got) == (1 if failed else 0)
    out = capsys.readouterr().out
    for n, ok, _ in got:
        prefix = {True: "ok  ", False: "FAIL", None: "WARN"}[ok]
        assert f"{prefix}  {n}: " in out


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("level", LEVELS)
def test_the_unknown_rows_say_unknown_in_every_pair(mode, level):
    setup_pair(mode, level)
    r = rows()
    for n in ("pre-push guard", "remote branch protection"):
        ok, detail = r[n]
        assert ok is None and detail.startswith("unknown: owner confirms"), (n, detail)


# ------------------------------------------------------------------ one test per row, both states

def test_level_off_row_is_a_warning_with_the_advisory_text():
    setup_pair("enforce", "off")
    ok, detail = rows()["what blocks"]
    assert ok is None
    soft = [s for s in invariant_ids() if s not in hooks.GOVERNANCE_INVARIANTS]
    assert detail.startswith("advisory: " + ", ".join(soft) + ", R-13, R-16 and all risk rules only log; " + ", ".join(GOV) + " block"), detail
    assert "SI-07" in soft and "SI-08" in soft
    ok, detail = rows()["level"]
    assert ok is None and detail.startswith("off: JADE only watches")


def test_level_high_row_is_ok():
    setup_pair("enforce", "high")
    assert rows()["level"] == (True, "high")
    ok, detail = rows()["what blocks"]
    assert ok is True and ", ".join(invariant_ids()) in detail


def test_level_unset_row_stays_ok():
    ok, detail = rows()["level"]
    assert ok is True and "unset" in detail


def test_gate_mode_off_row_fails_and_does_not_say_every_hook_is_inert():
    setup_pair("off", None)
    ok, detail = rows()["gate mode"]
    assert ok is False
    assert "every JADE hook is inert" not in detail
    assert "PreToolUse" in detail and "Stop" in detail and "PostToolUse" in detail and "still record" in detail


def test_gate_mode_advisory_row_is_a_warning_and_enforce_is_ok():
    setup_pair("advisory", None)
    ok, detail = rows()["gate mode"]
    assert ok is None and detail.startswith("advisory")
    setup_pair("enforce", None)
    assert rows()["gate mode"] == (True, "enforce")


def test_hooks_installed_row_counts_from_hooks(monkeypatch):
    monkeypatch.setattr(ops, "hooks_status", lambda: dict.fromkeys(ops.HOOKS, True))
    monkeypatch.setattr(ops, "wiring_problems", lambda probe=True: [])
    ok, detail = rows()["hooks installed"]
    assert ok is True and detail == f"all {len(ops.HOOKS)}: {', '.join(ops.HOOKS)}"
    assert "four" not in detail


def test_hooks_installed_row_fails_when_one_is_missing(monkeypatch):
    st = dict.fromkeys(ops.HOOKS, True)
    st["Stop"] = False
    monkeypatch.setattr(ops, "hooks_status", lambda: st)
    monkeypatch.setattr(ops, "wiring_problems", lambda probe=True: [])
    ok, detail = rows()["hooks installed"]
    assert ok is False and "Stop" in detail


@pytest.mark.parametrize("seen,no_prompt,ok,start", [
    (10, 10, None, "JADE is the only gate"),
    (10, 4, None, "JADE is the only gate for 4 of 10"),
    (10, 0, True, "the host prompted"),
    (0, 0, None, "unknown"),
])
def test_host_mode_bypass_row(monkeypatch, seen, no_prompt, ok, start):
    monkeypatch.setattr(ops, "no_prompt_gated_calls", lambda days=7: (seen, no_prompt))
    monkeypatch.setattr(ops, "configured_permission_mode", lambda: None)
    got_ok, detail = rows()["host mode bypass"]
    assert got_ok is ok and detail.startswith(start), detail


def test_host_mode_bypass_row_with_a_host_that_never_asks(monkeypatch):
    monkeypatch.setattr(ops, "no_prompt_gated_calls", lambda days=7: (0, 0))
    monkeypatch.setattr(ops, "configured_permission_mode", lambda: "bypassPermissions")
    ok, detail = rows()["host mode bypass"]
    assert ok is None and detail.startswith("JADE is the only gate") and "bypassPermissions" in detail


def test_version_vs_main_is_unknown_without_a_checkout():
    ok, detail = rows()["version vs main"]
    assert ok is None and detail.startswith("unknown") and jade.__version__ in detail


@pytest.mark.parametrize("main,ok,word", [("999.0.0", None, "older than origin/main 999.0.0"), (jade.__version__, True, "same as origin/main"),
                                          ("0.0.1", True, "newer than origin/main 0.0.1")])
def test_version_vs_main_compares_with_origin_main(monkeypatch, tmp_path, main, ok, word):
    monkeypatch.setenv("JADE_CHECKOUT", str(tmp_path))
    monkeypatch.setattr(ops, "main_version", lambda co: main)
    got_ok, detail = rows()["version vs main"]
    assert got_ok is ok and word in detail, detail


def test_version_vs_main_is_unknown_when_origin_main_is_not_there(monkeypatch, tmp_path):
    monkeypatch.setenv("JADE_CHECKOUT", str(tmp_path))
    ok, detail = rows()["version vs main"]
    assert ok is None and detail.startswith("unknown") and "origin/main" in detail


def test_main_version_reads_origin_main_from_a_real_checkout(tmp_path):
    co = tmp_path / "co"
    co.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-C", str(co)]
    subprocess.run([*git, "init", "-q"], check=True)
    (co / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "4.5.6"\n')
    subprocess.run([*git, "add", "pyproject.toml"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    assert ops.main_version(co) is None   # no origin/main yet
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    assert ops.main_version(co) == "4.5.6"


def test_maintain_row_warns_when_no_job_is_installed():
    ok, detail = rows()["maintain job"]
    assert ok is None and detail.startswith("not installed") and "do not run" in detail


def test_maintain_row_is_ok_when_the_job_is_loaded(monkeypatch, tmp_path):
    exe = tmp_path / "jade"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(ops, "maintain_jobs", lambda: [(tmp_path / "x.plist", "com.example.jade-maintain", [str(exe), "maintain"])])
    monkeypatch.setattr(ops, "_launchctl_loaded", lambda label: True)
    assert rows()["maintain job"] == (True, "com.example.jade-maintain loaded")
    monkeypatch.setattr(ops, "_launchctl_loaded", lambda label: False)
    assert rows()["maintain job"][0] is False


def test_host_permission_prompt_row_at_level_off_with_a_host_that_never_asks(monkeypatch):
    monkeypatch.setattr(ops, "configured_permission_mode", lambda: "bypassPermissions")
    setup_pair("enforce", "off")
    ok, detail = rows()["host permission prompt"]
    assert ok is None and "level off" in detail
    setup_pair("enforce", "high")
    assert rows()["host permission prompt"][0] is True
    setup_pair("advisory", "high")
    assert rows()["host permission prompt"][0] is False   # as on main


def test_approval_backend_row(monkeypatch):
    ok, detail = rows()["approval backend"]
    assert ok is None and detail.startswith("tty")   # a terminal approval a same-user process can imitate is not "ok"
    monkeypatch.setattr(signing, "anchor_state", lambda: ("ok", ""))
    setup_pair("enforce", "high")
    ok, detail = rows()["approval backend"]
    assert ok is True and detail.startswith("signed")
    setup_pair("enforce", "off")
    ok, detail = rows()["approval backend"]
    assert ok is None and detail.startswith("signed") and "level off" in detail


# ------------------------------------------------------------------ jade hooks install

def test_install_summary_names_gate_mode_what_blocks_and_backend_and_writes_nothing():
    setup_pair("enforce", "off")
    before = sorted(p.name for p in paths.home().iterdir()) if paths.home().exists() else []
    lines = ops.install_summary()
    assert lines[0] == "gate mode: enforce"
    assert lines[1].startswith("what blocks: advisory: ")
    assert lines[2].startswith("approval backend: tty")
    after = sorted(p.name for p in paths.home().iterdir()) if paths.home().exists() else []
    assert before == after


def test_jade_hooks_install_prints_the_summary(capsys):
    setup_pair("advisory", None)
    assert cli.main(["hooks", "install"]) in (0, None)
    out = capsys.readouterr().out
    assert "hooks installed in" in out
    assert "gate mode: advisory" in out and "what blocks: advisory gate mode, no level set: nothing blocks" in out and "approval backend: " in out
    assert json.loads(ops.settings_path().read_text())["hooks"].keys() >= set(ops.HOOKS)
