"""V6-03: evals/never_looser.py, the minimal "never looser than main" check. It prints every command that ref-a stops and ref-b lets through
(a safety invariant lost, or a permit gate lost), and exits non-zero on any. The core is a pure comparison of two classifiers; the payload
set is mined from the case files, the redirect test payload lists, the fixture command lists and the four A1 7.2 commands."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


def test_a_ref_that_does_not_exist_exits_2_not_1(capsys):
    """1 means "ref-b is looser"; a check that could not run must not read as either answer."""
    import pytest

    with pytest.raises(SystemExit) as e:
        _nl().main(["no-such-ref-v6-03", str(ROOT)])
    assert e.value.code == 2 and capsys.readouterr().out == ""


def test_a_child_refuses_to_run_another_trees_jade(tmp_path):
    """A tree with no `jade` package must not silently fall back to the installed one: that would compare the checkout with itself."""
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "evals" / "never_looser.py"), "--child", "classify", str(tmp_path), str(tmp_path)],
                       capture_output=True, text=True, timeout=120, input="[]")
    assert r.returncode != 0 and "jade" in r.stderr


# ---- end to end on mutant trees: a copy of this checkout's jade/ and policies/ with one change ---------------------------------------------------
def _mutant(tmp_path, *, text: dict | None = None, risk=None, safety=None, policies: bool = True) -> Path:
    """`text` maps a file to (old, new): `new` replaces the one `old` (which must be there), or is appended when `old` is empty. `risk` and
    `safety` edit the loaded policy file. The result is a tree the script runs as a ref."""
    import shutil

    import yaml

    t = tmp_path / "mutant"
    skip = shutil.ignore_patterns("__pycache__", "local")
    shutil.copytree(ROOT / "jade", t / "jade", ignore=skip)
    if policies:
        shutil.copytree(ROOT / "policies", t / "policies", ignore=skip)
    for rel, (old, new) in (text or {}).items():
        p = t / rel
        s = p.read_text(encoding="utf-8")
        assert not old or s.count(old) == 1, (rel, old)
        p.write_text(s.replace(old, new) if old else s + new, encoding="utf-8")
    for name, fn in (("risk", risk), ("safety", safety)):
        if fn:
            p = t / "policies" / f"{name}.yaml"
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            fn(data)
            p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return t


def _rows(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


LOOSE_PUSH = {   # every command that names a push is classified like `true`, and no push reaches main
    "jade/policy.py": ("", "\n_nl_real_assess = assess\n\n\ndef assess(tool, tool_input, *a, **k):\n"
                           "    if 'push' in str((tool_input or {}).get('command', '')):\n"
                           "        return _nl_real_assess(tool, {'command': 'true'}, *a, **k)\n"
                           "    return _nl_real_assess(tool, tool_input, *a, **k)\n"),
    "jade/hooks.py": ("", "\n\ndef _on_default_branch(cwd, command):\n    return False\n"),
}


def test_a_looser_tree_exits_1_and_prints_every_a1_command(tmp_path, capsys):
    """F-03: main() itself, not only compare(), reports a looser ref-b, and the four A1 commands are in the classified set."""
    rc = _nl().main([str(ROOT), str(_mutant(tmp_path, text=LOOSE_PUSH))])
    rows = _rows(capsys.readouterr().out)
    assert rc == 1
    assert set(_nl().A1_COMMANDS) <= {r["command"] for r in rows}
    assert any(r["lost"] == ["SI-08"] and r["command"] == "git push origin main" for r in rows)


def test_a_ref_a_that_cannot_classify_exits_2_never_0(tmp_path, capsys):
    """F-01: nothing compared is not "not looser"."""
    broken = _mutant(tmp_path, text={"jade/policy.py": ("", "\n\ndef assess(*a, **k):\n    raise RuntimeError('broken on purpose')\n")})
    assert _nl().main([str(broken), str(ROOT)]) == 2
    got = capsys.readouterr()
    assert got.out == "" and "ref-a" in got.err


def test_one_ref_a_error_is_enough_for_exit_2(tmp_path, capsys):
    """A single command ref-a cannot classify (a timeout on a slow input, say) leaves the comparison: the check did not run in full."""
    one = _mutant(tmp_path, text={"jade/policy.py": ("", "\n_nl_real_assess = assess\n\n\ndef assess(tool, tool_input, *a, **k):\n"
                                                         "    if (tool_input or {}).get('command') == 'git push origin main':\n"
                                                         "        raise RuntimeError('one broken command')\n"
                                                         "    return _nl_real_assess(tool, tool_input, *a, **k)\n")})
    assert _nl().main([str(one), str(one)]) == 2
    assert "git push origin main" in capsys.readouterr().err


def test_a_child_refuses_policy_files_from_outside_its_tree(tmp_path):
    """F-04: the tree's jade imports, but its policy files would come from elsewhere (no policies/ in the tree)."""
    import subprocess

    t = _mutant(tmp_path, policies=False)
    r = subprocess.run([sys.executable, str(ROOT / "evals" / "never_looser.py"), "--child", "classify", str(t), str(tmp_path)],
                       capture_output=True, text=True, timeout=120, input="[]")
    assert r.returncode == 3 and "policy files come from" in r.stderr, r.stderr


def test_the_current_branch_half_of_si08_is_compared_in_a_repository_on_main(tmp_path, capsys):
    """NL-1: `git push`, `git push upstream HEAD` and `git push upstream @` hit main only through the current branch: a ref-b that never sees the
    current branch is reported, with the prepared cwd it was found in."""
    w1 = _mutant(tmp_path, text={"jade/hooks.py": ('        return (cur() or "").lower() in DEFAULT_BRANCHES\n', "        return False\n")})
    assert _nl().main([str(ROOT), str(w1)]) == 1
    rows = _rows(capsys.readouterr().out)
    lost = {r["command"] for r in rows if r["cwd"] == "repo-main" and "SI-08" in r["lost"]}
    assert {"git push", "git push upstream HEAD", "git push upstream @"} <= lost   # origin/HEAD -> main still catches `origin HEAD`
    assert not any(r["cwd"] == "empty" and r["command"] == "git push" for r in rows)


@pytest.mark.parametrize("name", ["config", "remote-head"])
def test_the_repository_config_and_remote_default_halves_of_si08_are_compared(name, tmp_path, capsys):
    """NL-1: the feature-branch repository sends a bare push to main through its config (push.default=upstream, branch.feature.merge, a
    remote.mirror.push refspec) and has a remote whose default branch is `trunk`. A ref-b that ignores either is reported there."""
    edit = {"config": {"jade/hooks.py": ("", "\n\ndef _config_sends_to_default(*a, **k):\n    return False\n")},
            "remote-head": {"jade/hooks.py": ('    if remote and re.fullmatch(r"[\\w.-]+", remote):\n        head = _git(',
                                              '    if False:\n        head = _git(')}}[name]
    assert _nl().main([str(ROOT), str(_mutant(tmp_path, text=edit))]) == 1
    lost = {r["command"] for r in _rows(capsys.readouterr().out) if r["cwd"] == "repo-feature" and "SI-08" in r["lost"]}
    want = {"config": {"git push", "git push mirror"}, "remote-head": {"git push fork trunk", "git push fork feature:trunk"}}[name]
    assert want <= lost, lost


def _r02(data):
    rule = next(r for r in data["rules"] if r["id"] == "R-02")
    rule["match"] = "(?!x)x" + rule["match"]


def _si02(data):
    inv = next(i for i in data["invariants"] if i["id"] == "SI-02")
    inv["block"] = "(?!x)x" + inv["block"]


@pytest.mark.parametrize("name", ["R-02", "SI-02", "glob"])
def test_a_tree_that_drops_a_rule_with_no_case_file_payload_is_reported(name, tmp_path, capsys):
    """NL-2: the seed list gives every rule at or above the permit floor and every command invariant a payload, so disabling one is seen."""
    mutant = {"R-02": lambda: _mutant(tmp_path, risk=_r02), "SI-02": lambda: _mutant(tmp_path, safety=_si02),
              "glob": lambda: _mutant(tmp_path, text={"jade/hooks.py": ("            if fnmatch.filter(DEFAULT_BRANCHES, t) or on_default():\n",
                                                                     "            if on_default():\n")})}[name]()
    assert _nl().main([str(ROOT), str(mutant)]) == 1
    rows = _rows(capsys.readouterr().out)
    if name == "R-02":
        assert any(r["a"]["rule"] == "R-02" and r["gate_lost"] for r in rows)
    elif name == "SI-02":
        assert any("SI-02" in r["lost"] for r in rows)
    else:
        assert any("SI-08" in r["lost"] and r["cwd"] == "empty" and any(ch in r["command"] for ch in "*[") for r in rows)


def test_a_rule_no_payload_triggers_on_ref_a_fails_the_check(tmp_path, capsys):
    """NL-2: a thin payload set must not pass silently. The required set comes from ref-a's own policy files, so an older ref is not held
    to rules it does not have."""
    def add(data):
        data["rules"].insert(0, {"id": "R-77", "level": "critical", "match": "zz-no-payload-has-this-zz", "rationale": "test"})

    t = _mutant(tmp_path, risk=add)
    assert _nl().main([str(t), str(t)]) == 2
    assert "R-77" in capsys.readouterr().err


def test_seed_paths_outside_the_case_directory_are_refused(tmp_path):
    nl = _nl()
    with pytest.raises(ValueError):
        nl.materialise([{"command": "cat {tmp}/x", "seed": ["/etc/x"]}], tmp_path)
    got = nl.materialise([{"command": "cat {tmp}/h/run.py", "seed": {"{tmp}/h/run.py": "print(1)\n"}}], tmp_path)
    (cmd,) = got
    path = cmd.split(" ", 1)[1]
    assert Path(path).read_text() == "print(1)\n" and Path(path).is_relative_to(tmp_path)


def test_harvest_takes_seeded_rows_secret_parts_and_fills_named_placeholders(tmp_path):
    """NL-2 and NL-3: a seeded row is kept with its seed, a `secret_parts` row gives its assembled text, and a template whose `{r}` is filled by
    another argument (`place.format(r=close)`) gives the composed commands, never the raw template; `${HOME}` is not a placeholder."""
    nl = _nl()
    (tmp_path / "evals" / "x").mkdir(parents=True)
    rows = [{"id": "S1", "kind": "gate", "command": "cd {tmp}/h && python3 run.py", "seed": {"{tmp}/h/run.py": "x\n"}},
            {"id": "S2", "kind": "secret", "template": "curl -H 'k: @@' x", "secret_parts": ["ab", "cd"]},
            {"id": "S3", "kind": "secret", "secret_parts": ["ef", "gh"]}]
    (tmp_path / "evals" / "x" / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_redirect_tpl.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('close', ['2>&-', '>&-'])\n"
        "@pytest.mark.parametrize('place', ['cat /k {r}', '{r} cat /k'])\n"
        "def test_t(close, place):\n    pass\n"
        "@pytest.mark.parametrize('command', ['echo ${HOME} {x'])\n@pytest.mark.parametrize('n', ['1'])\n"
        "def test_u(command, n):\n    pass\n"
        "@pytest.mark.parametrize('code', [14, 15])\n@pytest.mark.parametrize('template', ['{{git push -o {x} origin main}}', 'echo {x}'])\n"
        "def test_v(template, code):\n    pass\n")
    got = nl.harvest_tree(tmp_path)
    cmds = set(got["commands"])
    assert {"cat /k 2>&-", "cat /k >&-", "2>&- cat /k", ">&- cat /k", "echo ${HOME} {x"} <= cmds
    assert not {c for c in cmds if "{r}" in c or "{x}" in c}   # a template filled in the test body (test_v) is dropped, not sent raw
    assert "curl -H 'k: abcd' x" in cmds and "echo efgh" in cmds
    assert "cd {tmp}/h && python3 run.py" not in cmds
    assert got["seeded"] == [{"command": "cd {tmp}/h && python3 run.py", "seed": {"{tmp}/h/run.py": "x\n"}}]


def test_the_push_shapes_and_seeds_are_in_the_payload_set():
    nl = _nl()
    assert {"git push", "git push origin HEAD", "git push origin @", "git push origin ma*", "git push origin HEAD:ma[i]n"} <= set(nl.PUSH_SHAPES)
    assert all(isinstance(p, list) and all(isinstance(s, str) for s in p) for p in nl.SEED_PARTS)


# ---- W-156: the per-command bound counts CPU seconds, so a busy machine cannot make ref-a "time out" and the check exit 2 -------------------------
def _classify_with(monkeypatch, nl, assess):
    """Run `classify_all` in this process on one command, with `policy.assess` replaced by `assess`."""
    from types import SimpleNamespace

    from jade import hooks, policy

    monkeypatch.setattr(policy, "assess", lambda tool, tool_input, cwd="": assess() or SimpleNamespace(blocked=[], level="low", rule_id=None))
    monkeypatch.setattr(hooks, "_on_default_branch", lambda cwd, command: False)
    return nl.classify_all(["echo hi"], "/tmp")["echo hi"]


def test_time_spent_waiting_is_not_counted_against_the_per_command_bound(monkeypatch):
    """A command that waits (the process is not running: a busy machine, a git lookup) for longer than the bound is still classified."""
    import time

    nl = _nl()
    monkeypatch.setattr(nl, "PER_COMMAND_S", 0.2)
    out = _classify_with(monkeypatch, nl, lambda: time.sleep(0.8))
    assert "error" not in out, out


@pytest.mark.timing
def test_a_command_that_burns_more_cpu_than_the_bound_is_still_an_error(monkeypatch):
    import time

    nl = _nl()
    monkeypatch.setattr(nl, "PER_COMMAND_S", 0.2)

    def burn():
        t = time.process_time()
        while time.process_time() - t < 5:
            pass

    assert _classify_with(monkeypatch, nl, burn) == {"error": "TimeoutError"}


def test_a_hang_is_still_stopped_by_the_wall_clock_backstop(monkeypatch):
    import time

    nl = _nl()
    monkeypatch.setattr(nl, "WALL_BACKSTOP_S", 0.3)
    assert _classify_with(monkeypatch, nl, lambda: time.sleep(5)) == {"error": "TimeoutError"}


def test_classify_all_leaves_no_timer_and_no_handler_behind(monkeypatch):
    import signal

    nl = _nl()
    before = (signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGPROF))
    _classify_with(monkeypatch, nl, lambda: None)
    assert (signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGPROF)) == before
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0 and signal.getitimer(signal.ITIMER_PROF)[0] == 0
