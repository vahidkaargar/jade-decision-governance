"""V6-03: evals/never_looser.py, the minimal "never looser than main" check. It prints every command that ref-a stops and ref-b lets through
(a safety invariant lost, or a permit gate lost), and exits non-zero on any. The core is a pure comparison of two classifiers; the payload
set is mined from the case files, the redirect test payload lists, the fixture command lists and the four A1 7.2 commands."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _nl():
    spec = importlib.util.spec_from_file_location("never_looser", ROOT / "evals" / "never_looser.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A1_GLUED = "cat<<<'git push origin main'|sh"


# ---- two fixture classifiers: `strict` stops every push that names main, `loose` misses the glued here-string -----------------------------------
def strict(c: str) -> dict:
    if "push" in c and "main" in c:
        return {"blocked": ["SI-08"], "level": "high", "gated": True}
    if "push" in c:
        return {"blocked": [], "level": "high", "gated": True}
    return {"blocked": [], "level": "low", "gated": False}


def loose(c: str) -> dict:
    if c == A1_GLUED:
        return {"blocked": [], "level": "low", "gated": False}
    if c == "git push origin feature":
        return {"blocked": [], "level": "medium", "gated": False}
    return strict(c)


def test_the_looser_classifier_is_reported_and_only_where_it_is_looser():
    nl = _nl()
    cmds = [A1_GLUED, "git push origin main", "git push origin feature", "ls"]
    rows = nl.compare(cmds, {c: strict(c) for c in cmds}, {c: loose(c) for c in cmds})
    assert [r["command"] for r in rows] == [A1_GLUED, "git push origin feature"]
    assert rows[0]["lost"] == ["SI-08"] and rows[0]["gate_lost"]
    assert rows[1]["lost"] == [] and rows[1]["gate_lost"]


def test_the_same_classifier_on_both_sides_reports_nothing():
    nl = _nl()
    cmds = [A1_GLUED, "git push origin main", "ls"]
    v = {c: strict(c) for c in cmds}
    assert nl.compare(cmds, v, dict(v)) == []


def test_a_stricter_ref_b_is_not_reported():
    nl = _nl()
    cmds = [A1_GLUED, "git push origin feature"]
    assert nl.compare(cmds, {c: loose(c) for c in cmds}, {c: strict(c) for c in cmds}) == []


def test_an_error_or_timeout_in_ref_b_where_ref_a_blocks_is_reported_as_such():
    nl = _nl()
    cmds = ["git push origin main", "ls"]
    a = {c: strict(c) for c in cmds}
    b = {"git push origin main": {"error": "TimeoutError"}, "ls": {"error": "ValueError"}}
    rows = nl.compare(cmds, a, b)
    assert [r["command"] for r in rows] == ["git push origin main"]
    assert rows[0]["b"] == {"error": "TimeoutError"}


def test_report_lines_are_one_json_line_per_command():
    nl = _nl()
    cmds = [A1_GLUED]
    out = nl.render(nl.compare(cmds, {A1_GLUED: strict(A1_GLUED)}, {A1_GLUED: loose(A1_GLUED)}))
    assert len(out.splitlines()) == 1 and json.loads(out.splitlines()[0])["command"] == A1_GLUED


# ---- the payload set ---------------------------------------------------------------------------------------------------------------------
def test_the_four_a1_commands_are_always_in_the_payload_set():
    nl = _nl()
    assert set(nl.A1_COMMANDS) == {A1_GLUED, "cat <<< 'git push origin :main' | sh", "git push origin feature &>log --no-verify +feature:main",
                                   "git push origin main"}


def test_harvest_reads_case_files_fixture_command_lists_and_redirect_payload_lists(tmp_path):
    nl = _nl()
    (tmp_path / "evals" / "x").mkdir(parents=True)
    (tmp_path / "evals" / "x" / "cases.jsonl").write_text(json.dumps({"id": "X1", "kind": "gate", "command": "git push origin v1"}) + "\n"
                                                          + json.dumps({"id": "X2", "kind": "gate_write", "path": "/a"}) + "\n")
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "push_cmds.json").write_text(json.dumps(["git push origin a :main"]))
    (tmp_path / "tests" / "fixtures" / "hook.json").write_text(json.dumps({"bash": {"tool_input": {"command": "git push origin b"}}}))
    (tmp_path / "tests" / "fixtures" / "redact_corpus.json").write_text(json.dumps(["not a command list"]))
    (tmp_path / "tests" / "test_redirect_fake.py").write_text(
        "import pytest\n"
        "BASES = ['git push origin c', 'git push origin d']\n"
        "def inserted(b, r):\n    w = b.split(' ')\n    return [' '.join(w[:i] + [r] + w[i:]) for i in range(len(w) + 1)]\n"
        "@pytest.mark.parametrize('command', [x for b in BASES for x in inserted(b, '&>log')])\n"
        "def test_x(command):\n    pass\n"
        "@pytest.mark.parametrize('command,want', [('git push origin e', True), pytest.param('git push origin f', False)])\n"
        "def test_y(command, want):\n    pass\n"
        "@pytest.mark.parametrize('word', ['x'])\n@pytest.mark.parametrize('op', ['>&-'])\n@pytest.mark.parametrize('base', ['git push origin g'])\n"
        "def test_z(base, op, word):\n    pass\n"
        "@pytest.mark.parametrize('plain', ['git push origin h'])\n@pytest.mark.parametrize('wrapper', [\"cat<<<'{c}'|sh\"])\n"
        "def test_w(wrapper, plain):\n    pass\n")
    got = nl.harvest_tree(tmp_path)
    cmds = set(got["commands"])
    assert {"git push origin v1", "git push origin a :main", "git push origin b", "git push origin c", "&>log git push origin d",
            "git push origin d &>log", "git push origin e", "git push origin f"} <= cmds
    assert {"git push origin g >&-", "git push>&- origin g", "git push origin g >&-x", "cat<<<'git push origin h'|sh"} <= cmds   # built from the arguments
    assert "not a command list" not in cmds
    assert got["counts"]["evals"] == 1 and got["counts"]["tests/test_redirect_fake.py"] >= 10


def test_the_script_is_labelled_minimal():
    assert "minimal" in (_nl().__doc__ or "").lower()


# ---- a real run on two trees: the checkout against itself prints nothing --------------------------------------------------------------------
def test_the_checkout_against_itself_prints_nothing_and_exits_zero(capsys):
    """Both sides run the real `policy.assess` and `hooks._on_default_branch` of this checkout in a child process with its own empty HOME."""
    nl = _nl()
    assert nl.main([str(ROOT), str(ROOT)]) == 0
    assert capsys.readouterr().out == ""


def test_a_child_refuses_to_run_another_trees_jade(tmp_path):
    """A tree with no `jade` package must not silently fall back to the installed one: that would compare the checkout with itself."""
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "evals" / "never_looser.py"), "--child", "classify", str(tmp_path), str(tmp_path)],
                       capture_output=True, text=True, timeout=120, input="[]")
    assert r.returncode != 0 and "jade" in r.stderr
