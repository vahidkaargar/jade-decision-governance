"""W-100d: one test walks every alternative of the R-13 pattern (policies/risk.yaml) and says where it lands (docs/12, plan v3).

R-13 is what protects JADE's own wiring. Once a chat Allow can settle a gated call, each thing R-13 names must be either live JADE wiring (a human types the signed
terminal permit; never a chat Allow) or something a chat Allow may settle (plain high: JADE's source in a checkout, agent instruction files, a ledger copy
elsewhere). The table pins the class of every alternative. A new alternative in R-13 fails `test_every_alternative_has_a_row` until someone decides which it is.
"""

import os
import re
import sys
from pathlib import Path

import pytest
import yaml

from jade import paths, policy

HOME = Path.home()
ROOT = Path(__file__).resolve().parents[2]
LIVE, PLAIN = "HO-wiring", None


def r13_pattern() -> str:
    rules = yaml.safe_load((ROOT / "policies" / "risk.yaml").read_text())["rules"]
    return next(r["match"] for r in rules if r["id"] == "R-13")


def split_top(pattern: str) -> list[str]:
    """The alternatives of a pattern at bracket depth 0 (a `|` inside a group or a character class does not split)."""
    out, depth, cur, i, in_class = [], 0, "", 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            cur += pattern[i:i + 2]
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            out.append(cur)
            cur = ""
            i += 1
            continue
        cur += c
        i += 1
    out.append(cur)
    return out


def alternatives() -> list[str]:
    outer = split_top(r13_pattern())
    inner = []
    for alt in outer:
        m = re.fullmatch(r"\(\?i:(.*)\)", alt)
        inner += split_top(m.group(1)) if m else [alt]
    return inner


W = lambda p: ("Write", {"file_path": str(p), "content": "x"})  # noqa: E731
B = lambda c: ("Bash", {"command": c})  # noqa: E731
ENV = Path(sys.prefix)
UV = HOME / ".local/share/uv/tools/jade-decision-governance"

TABLE = {
    r"\.claude/(settings[\w.-]*\.json|hooks/|CLAUDE\.md)": [
        (W(HOME / ".claude/settings.json"), LIVE), (W(HOME / ".claude/settings.local.json"), LIVE), (W(HOME / ".claude/hooks/x.sh"), LIVE),
        (W(HOME / ".claude/CLAUDE.md"), PLAIN)],
    r"\bAGENTS\.md\b": [(W(HOME / "AGENTS.md"), PLAIN), (W("/tmp/repo/AGENTS.md"), PLAIN)],
    r"\.cursor/rules": [(W(HOME / ".cursor/rules/x.mdc"), PLAIN)],
    r"\.ai/rules\.md": [(W("/tmp/repo/.ai/rules.md"), PLAIN)],
    r"policies/(?:[\w-]+\.(?:yaml|py)|local/)": [
        (W("/tmp/repo/policies/risk.yaml"), PLAIN), (W("/tmp/repo/policies/local/x.yaml"), PLAIN),
        (W(ENV / "lib/python3/site-packages/jade/policies/risk.yaml"), LIVE)],
    r"\.config/jade(?![\w.-])": [(W(HOME / ".config/jade/config.json"), LIVE), (B(f"cp /tmp/c {HOME}/.config/jade/config.json"), LIVE)],
    r"\.local/share/jade(?![\w.-])": [(W(HOME / ".local/share/jade/jade.sqlite"), LIVE), (B(f"cp /tmp/c {HOME}/.local/share/jade/x"), LIVE)],
    r"jade\.sqlite(?:-(?:wal|shm|journal))?(?![\w.])": [(W("/tmp/copy/jade.sqlite-wal"), PLAIN)],   # a copy elsewhere is not the live ledger (the live one sits under .local/share/jade)
    r"calibration/current\.json": [(W("/tmp/repo/calibration/current.json"), PLAIN), (W(HOME / ".local/share/jade/calibration/current.json"), LIVE)],
    r"uv/tools/jade(?:-decision-governance)?(?![\w-])": [(W(UV / "bin/x"), LIVE)],
    r"evals/[^/\s]+/protected/": [(W("/tmp/repo/evals/x/protected/f"), PLAIN)],
    r"\.local/bin/(?:jade|jev)(?![\w.-])": [(W(HOME / ".local/bin/jade"), LIVE), (W(HOME / ".local/bin/jev"), LIVE), (B("cp /tmp/x ~/.local/bin/jade"), LIVE)],
    r"\buv tool (?:uninstall|upgrade|reinstall)\b[^;&|]*\bjade": [
        (B("uv tool uninstall jade-decision-governance"), LIVE), (B("uv tool upgrade jade"), LIVE), (B("uv tool reinstall jade"), LIVE)],
    r"\buv tool install\b[^;&|]*\bjade": [(B("uv tool install jade-decision-governance"), LIVE), (B("uv tool install --reinstall git+https://github.com/x/jade"), LIVE)],
    r"\buv tool (?:uninstall|upgrade|reinstall)\b[^;&|]*--all\b": [(B("uv tool uninstall --all"), LIVE), (B("uv tool upgrade --all"), LIVE), (B("uv tool reinstall --all"), LIVE)],
    # The human-only CLI refuses these without a terminal (ops.HumanOnly), so a chat Allow cannot make them do anything an agent could not do anyway.
    r"\bjade (improve (promote|rollback)|ledger purge|label set)\b": [
        (B("jade improve promote c1"), PLAIN), (B("jade improve rollback"), PLAIN), (B("jade ledger purge --yes"), PLAIN), (B("jade label set a b"), PLAIN)],
}


def test_every_alternative_has_a_row():
    assert set(alternatives()) == set(TABLE), f"R-13 changed: decide the class of {set(alternatives()) ^ set(TABLE)} and update the table"


@pytest.mark.parametrize("alt", sorted(TABLE))
def test_the_alternative_lands_where_the_table_says(alt):
    for (tool, tin), want in TABLE[alt]:
        a = policy.assess(tool, tin, "/tmp")
        assert a.rule_id == "R-13" and a.level == "high", (tool, tin, a.rule_id, a.level)
        if want is None:
            assert not (set(a.ho_ids) & {"HO-wiring", "HO-checkout-install", "HO-settings-shadow", "HO-state-path"}), (tool, tin, a.ho_ids)
        else:
            assert want in a.ho_ids, (tool, tin, a.ho_ids)


def test_a_live_wiring_alternative_never_takes_a_chat_allow(monkeypatch):
    """The same table through the gate: live wiring asks for the signed permit, plain high asks in the chat (docs/12)."""
    import json

    from jade import hooks

    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    cfg = paths.config()
    cfg["level"] = "high"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))
    live = hooks.pretool({"tool_name": "Bash", "tool_input": {"command": "uv tool upgrade jade"}, "session_id": "s-walk-0001", "cwd": "/tmp", "permission_mode": "auto"})
    assert live.exit_code == 2 and "jade permit approve" in live.stderr and "[jade:ask_" not in live.stderr
    plain = hooks.pretool({"tool_name": "Write", "tool_input": {"file_path": "/tmp/repo/AGENTS.md", "content": "x"}, "session_id": "s-walk-0001", "cwd": "/tmp", "permission_mode": "auto"})
    assert plain.exit_code == 2 and "[jade:ask_" in plain.stderr


def test_the_environment_root_used_above_is_the_running_one():
    assert os.path.realpath(sys.prefix) == os.path.realpath(ENV)
