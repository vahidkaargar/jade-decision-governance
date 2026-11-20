"""V6-10: `jade check` and `jade hooks install` say what is true, at level `off` and in every gate mode and level pair, and say "unknown" for what JADE
cannot see. A row is (name, ok, detail) where ok is True (ok), False (FAIL, the exit code is non-zero, as before) or None (WARN: not a failure, but not
safe either, or not known). Only rule ids and levels are printed, never a command. No default and no blocking behaviour changes here. (V6-16 then made level `off` "observe plus floor": SI-07, SI-08 and the wiring classes block; the table
below reads that from `hooks._verdict`.)"""

import json
import subprocess

import pytest

import jade
from jade import cli, hooks, ops, paths, policy, proxy, signing, util
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_jev_proxy_w102 import KIND, Prov, owner_answers, prefs

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


def setup_pair(mode: str, level: str | None, again: bool = False) -> None:
    """`again`: the pair is set after a gated call already ran in this test (the W-142 probes), and enforce then asks for the signed backend or the journaled override."""
    ops.set_level("unset" if level is None else level, stdin_isatty=True)   # a recorded level (an unset one too, W-142: the same test sets several pairs): a raw config write would be the "not recorded" FAIL row
    ops.set_gate_mode(mode, stdin_isatty=True, allow_unsigned=again)


def rows() -> dict[str, tuple]:
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}


def blocks(mode: str, sid: str) -> bool:
    """What the hook really does with a call that hits invariant `sid` (gate mode off: pretool returns before any verdict)."""
    return mode != "off" and hooks._verdict(mode, "x", [sid]).exit_code == 2


def risk_gated(mode: str, level: str | None) -> bool:
    """Is a plain high-risk call held for a human at this pair? Asked of the real `hooks.pretool` (W-142), never worked out again here: the call is
    `m.CMD` with no session id, so neither `memory.step` nor `proxy.step` can let it run, and an exit 2 with a pending permit is the hold."""
    setup_pair(mode, level, again=True)
    a = policy.assess("Bash", {"command": m.CMD}, "/tmp")
    assert a.level == "high" and not a.blocked and not a.ho_ids, (a.level, a.blocked, a.ho_ids)   # the probe call is a plain high call: no invariant decides it
    res = hooks.pretool({"tool_name": "Bash", "tool_input": {"command": m.CMD}, "cwd": "/tmp", "permission_mode": "auto"})
    return res.exit_code == 2 and "needs a human-approved permit" in res.stderr


def no_human_paths(mode: str, level: str | None, monkeypatch) -> list[str]:
    """Who really lets a gated high call run with no human at this pair (W-142): the real `hooks.pretool` is called, never a copy of `_gate`. First (at
    enforce, level medium) a confirmed answer is stored for `m.CMD` and the deny gate and a fixture Jev that says yes are set up for `KIND`; then the pair
    is set and each call is made in a session of its own. `memory` and `Jev` are named when the hook answered with the words of that branch (exit 0)."""
    setup_pair("enforce", "medium", again=True)
    m.ask_and_allow(m.CMD)   # first: while Jev is not set up, the owner is asked
    m.confirm_all()
    monkeypatch.setattr(proxy, "make_provider", Prov(p=0.95))
    prefs()
    owner_answers()
    setup_pair(mode, level, again=True)
    out = []
    by_memory = hooks.pretool(m.call(m.CMD, "sess-w142-memory"))
    if by_memory.exit_code == 0 and "allowed from your confirmed answer" in by_memory.stderr:
        out.append("memory")
    by_jev = hooks.pretool(m.call(KIND, "sess-w142-jev"))
    if by_jev.exit_code == 0 and "Jev judged this" in by_jev.stderr:
        out.append("Jev")
    return out


@pytest.fixture
def wired(monkeypatch):
    """The hooks are installed and wired: the `what blocks` row then reads only the gate mode and the level."""
    monkeypatch.setattr(ops, "hooks_status", lambda: dict.fromkeys(ops.HOOKS, True))
    monkeypatch.setattr(ops, "wiring_problems", lambda probe=True: [])


# ------------------------------------------------------------------ W-142: the probes ask the real hook, so each has a positive twin

@pytest.mark.parametrize("level", ["low", "medium"])
def test_the_probe_sees_both_ways_to_run_with_no_human_at_enforce_and_low_or_medium(level, wired, monkeypatch):
    """The positive twin: a probe that always answered "none" would make the table below pass for the wrong reason."""
    assert no_human_paths("enforce", level, monkeypatch) == ["memory", "Jev"]


@pytest.mark.parametrize("mode,level", [("enforce", "high"), ("enforce", None), ("enforce", "off"), ("advisory", "medium"), ("off", "medium")])
def test_the_probe_sees_no_way_to_run_with_no_human_where_the_hook_has_none(mode, level, wired, monkeypatch):
    assert no_human_paths(mode, level, monkeypatch) == []


def test_the_hold_probe_sees_a_hold_at_enforce_and_none_in_advisory_or_at_level_off(wired):
    assert risk_gated("enforce", None) is True
    assert risk_gated("enforce", "high") is True
    assert risk_gated("enforce", "off") is False
    assert risk_gated("advisory", "high") is False
    assert risk_gated("off", "high") is False


# ------------------------------------------------------------------ the table over every gate mode and level pair

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("level", LEVELS)
def test_what_blocks_row_matches_the_hook_for_every_pair(mode, level, wired, monkeypatch):
    gated = risk_gated(mode, level)   # the real hook, asked first: it sets the pair itself and leaves it set
    bypass = no_human_paths(mode, level, monkeypatch)
    ok, detail = rows()["what blocks"]
    ids = invariant_ids()
    if level is None and mode != "off":   # policy.py runs the SI-12 checks only once a level is set: the row must say so, not list it as active
        assert "SI-12 is active only once a level is set" in detail, detail
        ids = [s for s in ids if s != "SI-12"]
    firm = [s for s in ids if blocks(mode, s)]
    soft = [s for s in ids if not blocks(mode, s)]
    if not soft and gated and bypass:   # the invariants block, but a high call may run with no human: never "ok" (V6-10 F1)
        assert ok is None, detail
        assert ", ".join(ids) + ") blocks" in detail
        assert "held for a human approval" not in detail
        assert "without a human" in detail, detail
        assert ("confirmed answer" in detail) == ("memory" in bypass) and ("Jev" in detail) == ("Jev" in bypass), (bypass, detail)   # the row names each path the real hook has, and only those
    elif not soft and gated:
        assert ok is True, detail
        assert ", ".join(ids) + ") blocks" in detail and "held for a human approval" in detail
        assert "Jev" not in detail or "decides nothing" in detail
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

def test_level_off_row_is_a_warning_with_the_advisory_text(wired):
    setup_pair("enforce", "off")
    ok, detail = rows()["what blocks"]
    assert ok is None
    floor = sorted(hooks.OFF_FLOOR_INVARIANTS)   # V6-16: observe plus floor
    soft = [s for s in invariant_ids() if s not in hooks.GOVERNANCE_INVARIANTS and s not in floor]
    assert detail.startswith("advisory: " + ", ".join(soft) + " and every risk rule (R-13 and R-16 too) only log; " + ", ".join(sorted(set(GOV) | set(floor))) + " block"), detail
    assert floor == ["SI-07", "SI-08"] and "SI-07" not in soft and "SI-08" not in soft
    ok, detail = rows()["level"]
    assert ok is None and detail.startswith("off (observe plus floor)")


def test_level_high_row_is_ok(wired):
    setup_pair("enforce", "high")
    assert rows()["level"] == (True, "high")
    ok, detail = rows()["what blocks"]
    assert ok is True and ", ".join(invariant_ids()) in detail


@pytest.mark.parametrize("level", ["low", "medium"])
def test_what_blocks_at_low_and_medium_names_memory_and_jev(level, wired):
    """memory.step and proxy.step let a call run with no human at these levels (docs/12): the row says so and is not ok."""
    setup_pair("enforce", level)
    ok, detail = rows()["what blocks"]
    assert ok is None, detail
    assert "held for a human approval" not in detail
    assert "confirmed answer" in detail and "`jade knows list`" in detail
    assert "Jev" in detail and "plain high" in detail
    assert "critical" in detail


def test_what_blocks_at_high_says_jev_decides_nothing(wired):
    setup_pair("enforce", "high")
    ok, detail = rows()["what blocks"]
    assert ok is True and "held for a human approval" in detail and "confirmed answer" not in detail


def test_what_blocks_says_nothing_blocks_when_the_pretool_hook_is_missing():
    """A fresh HOME: no hook is installed, so nothing blocks whatever the gate mode and level say (V6-10 F1)."""
    setup_pair("enforce", "high")
    assert rows()["hooks installed"][0] is False
    ok, detail = rows()["what blocks"]
    assert ok is None, detail
    assert detail.startswith("nothing blocks: the PreToolUse hook is not installed"), detail
    assert "blocks;" not in detail and "jade hooks install" in detail


def _pretool_out_of_date(monkeypatch, gaps):
    """The PreToolUse entry is installed but `hooks_status` calls it out of date, with `gaps` as `pretool_matcher_gaps` reports them (W-141)."""
    monkeypatch.setattr(ops, "hooks_status", lambda: {**dict.fromkeys(ops.HOOKS, True), "PreToolUse": False})
    monkeypatch.setattr(ops, "pretool_matcher_gaps", lambda: gaps)
    monkeypatch.setattr(ops, "wiring_problems", lambda probe=True: [])


def test_what_blocks_names_the_tools_a_matcher_gap_leaves_unchecked(monkeypatch):
    """W-141: a matcher that misses some tools still runs for the others, so the row must not say "nothing blocks". It names the uncovered tools and still says
    what blocks for the covered ones."""
    _pretool_out_of_date(monkeypatch, ["Read", "Grep", "Glob", "AskUserQuestion"])
    setup_pair("enforce", "high")
    ok, detail = rows()["what blocks"]
    assert ok is None, detail
    assert not detail.startswith("nothing blocks"), detail
    assert "does not cover Read, Grep, Glob, AskUserQuestion" in detail, detail
    assert "no check at all" in detail and "jade hooks install" in detail, detail
    assert "every other gated tool (Bash, " in detail and ", every MCP tool) is checked as follows: every safety invariant (" in detail, detail
    assert "held for a human approval" in detail, detail


def test_what_blocks_says_every_mcp_tool_when_the_matcher_misses_mcp(monkeypatch):
    _pretool_out_of_date(monkeypatch, ["mcp__server__tool"])
    setup_pair("enforce", "high")
    ok, detail = rows()["what blocks"]
    assert "does not cover every MCP tool" in detail and "mcp__server__tool" not in detail, detail


def test_what_blocks_says_the_hook_still_runs_when_only_its_timeout_or_failure_setting_is_old(monkeypatch):
    """W-141: no matcher gap, but the entry is out of date (a short timeout, or no onFailure): the hook still runs, so "nothing blocks" would be false too."""
    _pretool_out_of_date(monkeypatch, [])
    setup_pair("enforce", "high")
    ok, detail = rows()["what blocks"]
    assert ok is None, detail
    assert not detail.startswith("nothing blocks"), detail
    assert "out of date" in detail and "still runs" in detail and "crashed or hung gate may let a call run" in detail, detail
    assert "jade hooks install" in detail and "every safety invariant (" in detail, detail


def test_what_blocks_in_the_gap_case_keeps_the_gate_mode_words(monkeypatch):
    _pretool_out_of_date(monkeypatch, ["Read"])
    setup_pair("advisory", "high")
    ok, detail = rows()["what blocks"]
    assert ok is None and "does not cover Read" in detail and "advisory gate mode" in detail, detail


def test_what_blocks_in_the_gap_case_is_unknown_when_the_wiring_has_problems(monkeypatch):
    _pretool_out_of_date(monkeypatch, ["Read"])
    monkeypatch.setattr(ops, "wiring_problems", lambda probe=True: ["disableAllHooks is true in x: the host runs no hook at all"])
    setup_pair("enforce", "high")
    ok, detail = rows()["what blocks"]
    assert ok is None and detail.startswith("unknown: the hook wiring has problems"), detail
    assert "does not cover Read" in detail, detail


def _install_with_pretool_matchers(matchers):
    """A real settings file (nothing mocked): `jade hooks install`, then the JADE PreToolUse group once per matcher (review of W-141)."""
    ops.hooks_install(stdin_isatty=True)
    p = ops.settings_path()
    data = json.loads(p.read_text())
    group = data["hooks"]["PreToolUse"][0]
    data["hooks"]["PreToolUse"] = [{**group, "matcher": mt} for mt in matchers]
    p.write_text(json.dumps(data))


def test_an_invalid_pretool_matcher_covers_nothing_and_the_row_names_every_tool_it_misses():
    """Review of W-141: with matchers `Bash` and `(` (not a valid regex) only Bash is checked. `pretool_matcher_gaps` stopped at the first tool on
    `re.error`, so the row named one tool and said "every other gated tool is checked", which was false for Write, Edit, every MCP tool and the rest."""
    setup_pair("enforce", "high")
    _install_with_pretool_matchers(["Bash", "("])
    gaps = ops.pretool_matcher_gaps()
    assert "Bash" not in gaps and {"Write", "Edit", "Monitor", "AskUserQuestion", "mcp__server__tool"} <= set(gaps), gaps
    assert gaps == [t for t in (*policy.GATED_BUILTIN, "AskUserQuestion", "mcp__server__tool") if t != "Bash"], gaps
    ok, detail = rows()["what blocks"]
    assert ok is None, detail
    covered_list = detail.split("does not cover ", 1)[1].split(":", 1)[0]
    assert "Bash" not in covered_list and "Write" in covered_list and "Edit" in covered_list and "every MCP tool" in covered_list, detail
    assert "every other gated tool (Bash) is checked as follows: every safety invariant (" in detail, detail


def test_a_pretool_matcher_that_covers_no_gated_tool_means_nothing_blocks():
    """Every matcher invalid (or none that matches a gated tool): no gated call is checked, so the row says "nothing blocks", never "every other tool"."""
    setup_pair("enforce", "high")
    _install_with_pretool_matchers(["("])
    assert ops.pretool_matcher_gaps() == [*policy.GATED_BUILTIN, "AskUserQuestion", "mcp__server__tool"]
    ok, detail = rows()["what blocks"]
    assert ok is None, detail
    assert detail.startswith("nothing blocks: the PreToolUse matcher covers no gated tool"), detail
    assert "every other gated tool" not in detail and "blocks;" not in detail and "jade hooks install" in detail, detail


def test_what_blocks_is_unknown_when_the_hook_wiring_has_problems(monkeypatch):
    monkeypatch.setattr(ops, "hooks_status", lambda: dict.fromkeys(ops.HOOKS, True))
    monkeypatch.setattr(ops, "wiring_problems", lambda probe=True: ["disableAllHooks is true in x: the host runs no hook at all"])
    setup_pair("enforce", "high")
    ok, detail = rows()["what blocks"]
    assert ok is None and detail.startswith("unknown: the hook wiring has problems"), detail


def test_what_blocks_is_unknown_when_the_policy_does_not_load(monkeypatch, wired):
    setup_pair("enforce", "high")

    def boom():
        raise ValueError("bad policy")

    monkeypatch.setattr(policy, "safety", boom)
    ok, detail = ops.blocking_summary()
    assert ok is None, detail
    assert detail.startswith("unknown: the safety policy does not load"), detail
    assert "every safety invariant" not in detail and "fails closed" in detail
    assert ops.blocking_summary("off", "high")[1].startswith("gate mode off: nothing blocks")   # still true with no policy


def test_maintain_does_not_count_warn_rows_as_failures(monkeypatch, capsys):
    """V6-10 F3: a WARN row (None) never makes the daily job exit 1 or appear in check_failed."""
    got = [("ledger writable", True, "x"), ("what blocks", None, "advisory: ..."), ("level", None, "off: ..."), ("version vs main", None, "unknown: ...")]
    monkeypatch.setattr(ops, "check", lambda live=True, provider=None: got)
    assert cli.main(["maintain", "--offline"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["check_failed"] == []
    got.append(("hooks installed", False, "missing"))
    assert cli.main(["maintain", "--offline"]) == 1
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["check_failed"] == ["hooks installed"]


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


@pytest.mark.parametrize("main,ok,word", [("999.0.0", None, "older than origin/main 999.0.0"), ("0.0.1", True, "newer than origin/main 0.0.1")])
def test_version_vs_main_compares_with_origin_main(monkeypatch, tmp_path, main, ok, word):
    monkeypatch.setenv("JADE_CHECKOUT", str(tmp_path))
    monkeypatch.setattr(ops, "main_version", lambda co: main)
    got_ok, detail = rows()["version vs main"]
    assert got_ok is ok and word in detail, detail


SHA_A, SHA_B = "aaaaaaaaaaaa", "bbbbbbbbbbbb"


@pytest.mark.parametrize("running,main_sha,behind,ok,word", [
    (SHA_A, SHA_A, None, True, "same as origin/main"),
    (SHA_A, SHA_B, 3, None, "same version string, 3 commit(s) behind origin/main"),
    (SHA_A, SHA_B, 0, None, "same version string, but a different commit"),
    (SHA_A, SHA_B, None, None, "same version string, but a different commit"),
    (None, SHA_B, None, None, "unknown: installed package"),
    (SHA_A, None, None, None, "unknown"),
])
def test_version_vs_main_with_the_same_version_compares_commits(monkeypatch, tmp_path, running, main_sha, behind, ok, word):
    """V6-10 F2: 0.6.1 is both the tag and main three commits later; an equal version string is not proof the same code runs."""
    monkeypatch.setenv("JADE_CHECKOUT", str(tmp_path))
    monkeypatch.setattr(ops, "main_version", lambda co: jade.__version__)
    monkeypatch.setattr(paths, "code_commit", lambda: running)
    monkeypatch.setattr(ops, "main_commit", lambda co: main_sha)
    monkeypatch.setattr(ops, "commits_behind_main", lambda co, sha: behind)
    got_ok, detail = rows()["version vs main"]
    assert got_ok is ok and word in detail, detail
    if ok is not True:
        assert "same as origin/main" not in detail


def test_main_commit_and_commits_behind_from_a_real_checkout(tmp_path):
    co = tmp_path / "co"
    co.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-C", str(co)]
    subprocess.run([*git, "init", "-q"], check=True)
    (co / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "4.5.6"\n')
    subprocess.run([*git, "add", "pyproject.toml"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    first = paths.git_commit(co)
    assert ops.main_commit(co) is None
    for i in range(2):
        (co / "f").write_text(str(i))
        subprocess.run([*git, "add", "f"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", f"c{i}"], check=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    assert ops.main_commit(co) == paths.git_commit(co)
    assert ops.commits_behind_main(co, first) == 2
    assert ops.commits_behind_main(co, ops.main_commit(co)) == 0
    assert ops.commits_behind_main(co, "0123456789ab") is None   # not in this repository


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

def test_setup_hooks_text_counts_from_hooks(monkeypatch):
    from jade import setup

    monkeypatch.setattr(ops, "hooks_status", lambda: dict.fromkeys(ops.HOOKS, True))
    monkeypatch.setattr(ops, "pretool_matcher_gaps", lambda: [])
    st = setup._hooks_state()
    assert st.detail == f"all {len(ops.HOOKS)} JADE hooks installed", st
    assert "four" not in setup._hooks_change() and f"add {len(ops.HOOKS)} JADE hook entries" in setup._hooks_change()


def test_install_summary_rows_name_gate_mode_what_blocks_and_backend_and_write_nothing():
    setup_pair("enforce", "off")
    before = sorted(p.name for p in paths.home().iterdir()) if paths.home().exists() else []
    rows_ = ops.install_summary_rows()
    assert [r[0] for r in rows_] == ["gate mode", "what blocks", "approval backend"]   # the row names `jade check` uses
    (_, ok0, text0), (_, ok1, text1), (_, ok2, text2) = rows_
    assert text0 == "enforce" and ok0 is True
    assert text1.startswith("advisory: ") and ok1 is None   # level off: a WARN, as in `jade check`
    assert text2.startswith("tty") and ok2 is None
    after = sorted(p.name for p in paths.home().iterdir()) if paths.home().exists() else []
    assert before == after


def test_install_summary_row_state_follows_the_gate_and_the_backend(monkeypatch):
    setup_pair("advisory", None)
    by = {n: (ok, d) for n, ok, d in ops.install_summary_rows()}
    assert by["gate mode"][0] is None and by["gate mode"][1].startswith("advisory")
    monkeypatch.setattr(signing, "anchor_state", lambda: ("untrusted", "bad anchor"))
    by = {n: (ok, d) for n, ok, d in ops.install_summary_rows()}
    assert by["approval backend"][0] is False and "bad anchor" in by["approval backend"][1]


def test_install_summary_rows_render_like_check_rows():
    rows_ = [("gate mode", True, "enforce"), ("what blocks", None, "x"), ("approval backend", False, "y")]
    assert ops.install_summary_lines(rows_) == ["ok    gate mode: enforce", "WARN  what blocks: x", "FAIL  approval backend: y"]


def test_jade_hooks_install_prints_the_summary(capsys):
    setup_pair("advisory", None)
    assert cli.main(["hooks", "install"]) in (0, None)
    out = capsys.readouterr().out
    assert "hooks installed in" in out
    assert "WARN  gate mode: advisory" in out and "what blocks: advisory gate mode, no level set: nothing blocks" in out and "approval backend: " in out
    assert json.loads(ops.settings_path().read_text())["hooks"].keys() >= set(ops.HOOKS)
