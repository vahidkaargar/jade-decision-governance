"""evals/realshell_oracle.py (V6-22): the real-shell oracle snapshot and gate G4.

The snapshot run needs macOS sandbox-exec and is an owner run before a release. The check (G4) is pure: it reads a snapshot file and replays the
classifier, so it runs on every platform. Every payload that runs in a real shell here is a push to a stub, and it runs through `evals/realshell.py`
only. No test runs a destructive payload. No test writes to evals/oracle/ or touches the real ledger.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "evals" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


oracle = _load("realshell_oracle")
realshell = _load("realshell")

HAVE_SANDBOX = sys.platform == "darwin" and os.access(realshell.SANDBOX_EXEC, os.X_OK)
needs_sandbox = pytest.mark.skipif(not HAVE_SANDBOX, reason="NOT VERIFIED HERE: macOS sandbox-exec is missing, so the driver run is untested on this "
                                                              "platform (the check of a snapshot file still ran)")


# ---- the truth: what the shell reached, read from the recorded call (independent of the classifier) -----------------------------------------
@pytest.mark.parametrize("call, expected", [
    (["git", "push", "origin", "main"], True),
    (["git", "push", "origin", "master"], True),
    (["git", "push", "origin", "HEAD:main"], True),
    (["git", "push", "origin", "HEAD:refs/heads/main"], True),
    (["git", "push", "origin", "HEAD:heads/main"], True),
    (["git", "push", "origin", "+feature:main"], True),
    (["git", "push", "origin", ":main"], True),                        # a delete of main is a push to main
    (["git", "push", "--delete", "origin", "main"], True),
    (["git", "push", "--force", "-u", "origin", "main"], True),
    (["git", "push", "-fu", "origin", "main"], True),
    (["git", "push", "--all", "origin"], True),
    (["git", "push", "--mirror", "origin"], True),
    (["git", "push", "origin", "refs/heads/*:refs/heads/*"], True),
    (["git", "push", "origin", ":"], True),                            # every matching branch, main included
    (["git", "-C", "my repo", "push", "origin", "main"], True),
    (["git", "-c", "push.default=current", "push", "origin", "main"], True),
    (["git", "--no-pager", "--git-dir", "x/.git", "push", "origin", "main"], True),
    (["git", "push", "--repo", "origin", "main"], True),               # with --repo the first word is already a refspec
    (["git", "push", "-o", "ci.skip", "origin", "main"], True),        # the value of -o is not a remote
    (["git", "push", "origin", "feature", "main"], True),
    (["git", "send-pack", "origin", "HEAD:refs/heads/main"], True),
    (["git", "push", "origin", "feature"], False),
    (["git", "push", "origin", "feature:feature-2"], False),
    (["git", "push", "origin", "refs/heads/mainline"], False),
    (["git", "push", "origin", "main-fix"], False),
    (["git", "push", "origin", "v1.0"], False),
    (["git", "push", "origin", "tag", "main"], False),                 # `tag main` pushes the tag named main
    (["git", "push", "--tags", "origin"], False),
    (["git", "push", "-o", "main", "origin", "feature"], False),       # `main` is the value of -o
    (["git", "status"], False),
    (["git", "pull", "origin", "main"], False),
    (["git", "fetch", "origin", "main"], False),
    (["gh", "pr", "merge", "5"], False),                               # not a push: the oracle claims only the SI-08 family
    (["rm", "-rf", "x"], False),
    (["git", "push"], None),                                           # depends on the current branch and the config
    (["git", "push", "origin"], None),
    (["git", "push", "origin", "HEAD"], None),
    (["git", "push", "origin", "@"], None),
    (["git"], False),
    ([], False),
])
def test_what_one_recorded_call_reached(call, expected):
    assert oracle.reaches_default_push(call) is expected


def test_one_entry_over_two_shells_true_beats_unknown_beats_false():
    ok = {"rc": 0, "timed_out": False, "calls": [["git", "push", "origin", "main"]]}
    none = {"rc": 0, "timed_out": False, "calls": []}
    amb = {"rc": 0, "timed_out": False, "calls": [["git", "push"]]}
    slow = {"rc": None, "timed_out": True, "calls": []}
    other = {"rc": 0, "timed_out": False, "calls": [["git", "push", "origin", "feature"]]}
    assert oracle.entry_reach({"bash": none, "zsh": ok}) is True       # one shell is enough: the payload is a bypass if either shell takes it
    assert oracle.entry_reach({"bash": amb, "zsh": other}) is None
    assert oracle.entry_reach({"bash": other, "zsh": none}) is False
    assert oracle.entry_reach({"bash": slow, "zsh": none}) is None     # a timeout is no evidence of safety
    assert oracle.entry_reach({"bash": slow, "zsh": ok}) is True       # a call recorded before the timeout still counts


# ---- the corpus ---------------------------------------------------------------------------------------------------------------------------
def test_default_corpus_is_the_si08_family_chosen_by_row_metadata():
    corpus = oracle.default_corpus()
    ids = {i for e in corpus for i in e["ids"]}
    assert {"A56", "R24", "A132", "A243", "A244", "A245", "KG56"} <= ids       # SI-08 rows, plus the pushes V6-08 closed (A243 to A245, were KG01 to KG03) and the open KG56
    assert "KG04" not in ids and "KG17" not in ids and "A04" not in ids  # a merge by API, a wrapper that no stub sees, another invariant
    assert len({e["cmd"] for e in corpus}) == len(corpus)
    assert all(isinstance(e["cmd"], str) and e["cmd"] for e in corpus)


def test_corpus_hash_is_stable_and_changes_with_one_command():
    a = [{"ids": ["x"], "cmd": "git push origin main"}, {"ids": ["y"], "cmd": "git push origin feature"}]
    assert oracle.corpus_hash(a) == oracle.corpus_hash(list(reversed(a)))
    b = [a[0], {"ids": ["y"], "cmd": "git push origin feature2"}]
    assert oracle.corpus_hash(a) != oracle.corpus_hash(b)


def test_a_case_row_with_oracle_si08_joins_the_corpus_and_a_seeded_row_does_not(tmp_path):
    f = tmp_path / "cases.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"id": "N1", "kind": "gate", "command": "git push origin main", "expect": "block", "oracle": "si08"},
        {"id": "N2", "kind": "gate", "command": "git push origin dev", "expect": "allow"},
        {"id": "N3", "kind": "gate", "command": "git push origin main", "expect": "block", "oracle": "si08", "seed": {"a": "b"}},
        {"id": "N4", "kind": "gate_write", "path": "/x", "oracle": "si08"},
        {"id": "N5", "kind": "gate", "command": "git push --all", "expect": "block", "expect_blocked": ["SI-08"]},
        {"id": "N6", "kind": "gate", "command": "git push origin main", "expect": "block", "expect_blocked": ["SI-08"]},
    ]) + "\n")
    corpus = oracle.default_corpus([f])
    assert [(e["cmd"], e["ids"]) for e in corpus] == [("git push origin main", ["N1", "N6"]), ("git push --all", ["N5"])]


def test_added_payloads_get_ids_and_a_duplicate_command_is_merged(tmp_path):
    f = tmp_path / "round.jsonl"
    f.write_text('"git push origin main"\n{"cmd": "git push origin :main", "id": "r1"}\n{"command": "git push origin dev"}\n')
    base = [{"ids": ["A1"], "cmd": "git push origin main"}]
    merged = oracle.add_payloads(base, f)
    assert {e["cmd"]: e["ids"] for e in merged} == {"git push origin main": ["A1"], "git push origin :main": ["r1"], "git push origin dev": ["add-003"]}


# ---- the snapshot file ----------------------------------------------------------------------------------------------------------------------
def _res(*calls, rc=0, timed_out=False):
    return {"rc": rc, "timed_out": timed_out, "calls": [list(c) for c in calls]}


def _snap(entries, shells=None, corpus=None):
    entries = [{"id": i, "ids": [i], "cmd": c, "results": r} for i, c, r in entries]
    return oracle.build_snapshot(entries, shells or {"bash": "3.2.57", "zsh": "5.9"}, corpus if corpus is not None else [{"ids": e["ids"], "cmd": e["cmd"]} for e in entries])


def test_the_snapshot_keeps_calls_only_and_hides_host_paths():
    root = "/private/var/folders/xx/jade-realshell-abc"
    raw = [{"cmd": "git -C $HOME push origin main", "results": {
        "bash": {"shell": "bash", "rc": 0, "timed_out": False, "stdout": "secret out", "stderr": "secret err",
                 "calls": [["git", "-C", f"{root}/cases/0-bash-k3j2/home", "push", "origin", "main"]]}}}]
    entries = oracle.entries_from_run(raw, [{"ids": ["A1"], "cmd": "git -C $HOME push origin main"}], root)
    text = oracle.dump_snapshot(oracle.build_snapshot(entries, {"bash": "3.2.57"}, [{"ids": ["A1"], "cmd": "git -C $HOME push origin main"}]))
    assert "secret" not in text and "stdout" not in text and root not in text and "k3j2" not in text
    assert "<CASE>/home" in text
    assert oracle.entry_reach(entries[0]["results"]) is True


def test_a_rerun_with_the_same_results_is_byte_equal():
    a = _snap([("A1", "git push origin main", {"bash": _res(("git", "push", "origin", "main")), "zsh": _res(("git", "push", "origin", "main"))})])
    b = _snap([("A1", "git push origin main", {"zsh": _res(("git", "push", "origin", "main")), "bash": _res(("git", "push", "origin", "main"))})])
    assert oracle.dump_snapshot(a) == oracle.dump_snapshot(b)
    assert oracle.dump_snapshot(a).endswith("\n")


def test_the_file_name_holds_the_shell_versions():
    assert oracle.snapshot_name({"bash": "3.2.57(1)-release", "zsh": "5.9"}) == "realshell-bash3.2.57-zsh5.9.json"
    assert oracle.snapshot_name({"zsh": "5.9", "bash": "5.2.15"}) == "realshell-bash5.2.15-zsh5.9.json"


def test_a_shell_that_gave_no_version_stops_the_run():
    with pytest.raises(oracle.OracleError):
        oracle.snapshot_name({"bash": "", "zsh": "5.9"})


# ---- G4: the check ---------------------------------------------------------------------------------------------------------------------------
BLOCK = {"action": "block", "invariants": ["SI-08"]}
ALLOW = {"action": "allow", "invariants": []}
ASK = {"action": "ask", "invariants": []}
PUSH = ("git", "push", "origin", "main")


def classifier(table):
    def classify(cmds):
        return {c: table[c] for c in cmds}
    return classify


def snapshot_of(rows):
    """rows: (id, cmd, reached) -> a snapshot whose real shell did or did not reach a push to main."""
    entries = []
    for i, cmd, reached in rows:
        r = _res(PUSH) if reached is True else _res(["git", "push"]) if reached is None else _res(["git", "status"])
        entries.append((i, cmd, {"bash": r, "zsh": r}))
    return _snap(entries)


def gate(result, name):
    return next(g for g in result["gates"] if g["gate"] == name)


def run(snap, table, gaps=None, ceiling=None, corpus=None, **kw):
    corpus = corpus if corpus is not None else [{"ids": e["ids"], "cmd": e["cmd"]} for e in snap["entries"]]
    return oracle.check_snapshot(snap, corpus, gaps or {}, classifier(table), ceiling, **kw)


def test_every_push_that_the_shell_reaches_is_blocked_by_si08_is_a_pass():
    snap = snapshot_of([("A1", "git push origin main", True), ("A2", "git push origin dev", False)])
    r = run(snap, {"git push origin main": BLOCK, "git push origin dev": ALLOW})
    assert {g["state"] for g in r["gates"]} == {"pass"}
    assert r["exit"] == 0


def test_a_push_that_the_shell_reaches_and_the_classifier_allows_is_a_bypass_and_fails():
    snap = snapshot_of([("A1", "git push origin main", True), ("A2", "g=git; $g push origin main", True)])
    r = run(snap, {"git push origin main": BLOCK, "g=git; $g push origin main": ALLOW})
    g = gate(r, "G4 SI-08 mismatch")
    assert g["state"] == "fail" and "A2" in g["detail"] and "A1" not in g["detail"]
    assert r["exit"] == 1


def test_an_ask_or_another_invariant_is_not_si08_and_counts_apart_from_an_allow():
    snap = snapshot_of([("A1", "c1", True), ("A2", "c2", True), ("A3", "c3", True), ("A4", "c4", True)])
    r = run(snap, {"c1": ASK, "c2": ALLOW, "c3": {"action": "block", "invariants": ["SI-09"]}, "c4": {"action": "error", "invariants": []}})
    g = gate(r, "G4 SI-08 mismatch")
    assert g["state"] == "fail"
    for word in ("allowed 1", "asked 1", "another invariant 1", "error 1"):
        assert word in g["detail"], g["detail"]


def test_a_known_gap_is_exempt_but_is_not_a_pass_without_a_ceiling_and_must_not_grow():
    snap = snapshot_of([("KG03", "g=git; $g push origin main", True), ("A1", "git push origin main", True)])
    table = {"g=git; $g push origin main": ALLOW, "git push origin main": BLOCK}
    gaps = {"g=git; $g push origin main": "KG03"}
    r = run(snap, table, gaps)
    assert gate(r, "G4 SI-08 mismatch")["state"] == "pass"             # the only mismatch is a known gap
    assert gate(r, "G4 known gaps")["state"] == "not-enough-data" and r["exit"] == 0
    assert gate(run(snap, table, gaps, ceiling=1), "G4 known gaps")["state"] == "pass"
    over = gate(run(snap, table, gaps, ceiling=0), "G4 known gaps")
    assert over["state"] == "fail" and "ceiling" in over["detail"]


def test_a_gap_that_is_now_blocked_must_lose_its_marker():
    snap = snapshot_of([("KG03", "g=git; $g push origin main", True)])
    r = run(snap, {"g=git; $g push origin main": BLOCK}, {"g=git; $g push origin main": "KG03"}, ceiling=1)
    g = gate(r, "G4 known gaps")
    assert g["state"] == "fail" and "KG03" in g["detail"] and "marker" in g["detail"]


def test_a_command_of_the_current_corpus_missing_from_the_snapshot_is_stale_and_fails():
    snap = snapshot_of([("A1", "git push origin main", True)])
    corpus = [{"ids": ["A1"], "cmd": "git push origin main"}, {"ids": ["A9"], "cmd": "git push origin +main"}]
    r = run(snap, {"git push origin main": BLOCK}, corpus=corpus)
    g = gate(r, "G4 fresh")
    assert g["state"] == "fail" and "A9" in g["detail"] and r["exit"] == 1


def test_a_snapshot_with_more_payloads_than_the_corpus_is_still_fresh():
    snap = snapshot_of([("A1", "git push origin main", True), ("add-001", "git push origin :main", True)])
    r = run(snap, {"git push origin main": BLOCK, "git push origin :main": BLOCK}, corpus=[{"ids": ["A1"], "cmd": "git push origin main"}])
    assert gate(r, "G4 fresh")["state"] == "pass" and "1 extra" in gate(r, "G4 fresh")["detail"]


def test_when_no_payload_reached_a_push_there_is_nothing_to_compare_and_it_is_not_a_pass():
    snap = snapshot_of([("A1", "echo hi", False)])
    r = run(snap, {"echo hi": ALLOW})
    assert gate(r, "G4 reach")["state"] == "not-enough-data"
    assert gate(r, "G4 SI-08 mismatch")["state"] == "not-enough-data"
    assert r["exit"] == 0


def test_a_payload_with_no_evidence_is_counted_not_judged_and_never_a_pass_for_itself():
    snap = snapshot_of([("A1", "git push origin main", True), ("A2", "git push", None)])
    r = run(snap, {"git push origin main": BLOCK, "git push": ALLOW})
    g = gate(r, "G4 reach")
    assert "1 not judged" in g["detail"]
    assert gate(r, "G4 SI-08 mismatch")["state"] == "pass"             # the allow of the unjudged one is not held against the classifier


def test_the_new_gap_rate_per_100_payloads_is_printed():
    snap = snapshot_of([(f"A{i}", f"git push origin main #{i}", True) for i in range(4)])
    table = {f"git push origin main #{i}": (ALLOW if i == 0 else BLOCK) for i in range(4)}
    g = gate(run(snap, table), "G4 SI-08 mismatch")
    assert "new gaps 1 of 4 payloads (25.0 per 100)" in g["detail"]


def test_the_report_is_text_with_ids_only(capsys):
    snap = snapshot_of([("A1", "git push origin main --secret-looking-arg", True)])
    r = run(snap, {"git push origin main --secret-looking-arg": ALLOW})
    text = oracle.render(r)
    assert "A1" in text and "secret-looking-arg" not in text and "G4" in text


# ---- the folder, the command line, the exit codes ----------------------------------------------------------------------------------------
def write_snap(d, name, snap):
    d.mkdir(exist_ok=True)
    (d / name).write_text(oracle.dump_snapshot(snap))


def test_no_snapshot_in_the_folder_is_not_enough_data_and_exit_0(tmp_path, capsys):
    assert oracle.main(["check", "--dir", str(tmp_path / "nothing")]) == 0
    out = capsys.readouterr().out
    assert "not-enough-data" in out and "no snapshot" in out and "G4 is not claimed" in out


def test_an_unreadable_or_foreign_file_exits_2_and_is_never_a_pass(tmp_path, capsys):
    (tmp_path / "realshell-bash1-zsh1.json").write_text("{not json")
    assert oracle.main(["check", "--dir", str(tmp_path)]) == 2
    (tmp_path / "realshell-bash1-zsh1.json").write_text(json.dumps({"format": 99}))
    assert oracle.main(["check", "--dir", str(tmp_path)]) == 2
    assert "cannot" in capsys.readouterr().err


def test_check_through_the_real_hook_finds_a_known_gap_and_passes_a_blocked_push():
    """The classifier side is the real hook in a child process (decision_replay), not a fake. KG58 is a real gap on this tree (V6-08 closed the plain variable form); a plain push is blocked by SI-08."""
    cmds = ["git push origin main", "v=push; git $v origin main", "git push origin feature-x"]
    snap = snapshot_of([("A56", cmds[0], True), ("KG58", cmds[1], True), ("A99", cmds[2], False)])
    out = oracle.classify_with_hook(cmds)
    assert out[cmds[0]]["action"] == "block" and "SI-08" in out[cmds[0]]["invariants"]
    assert "SI-08" not in out[cmds[1]]["invariants"]
    assert "SI-08" not in out[cmds[2]]["invariants"]
    corpus = [{"ids": e["ids"], "cmd": e["cmd"]} for e in snap["entries"]]
    r = oracle.check_snapshot(snap, corpus, {}, oracle.classify_with_hook, None)
    g = gate(r, "G4 SI-08 mismatch")
    assert r["exit"] == 1 and g["state"] == "fail" and "KG58" in g["detail"] and "A56" not in g["detail"]
    r = oracle.check_snapshot(snap, corpus, {cmds[1]: "KG58"}, oracle.classify_with_hook, 1)
    assert r["exit"] == 0 and gate(r, "G4 known gaps")["state"] == "pass"


def test_the_committed_snapshots_pass_g4_in_ci():
    """G4 in CI: each snapshot committed under evals/oracle/ is replayed against the classifier of this tree. With none committed (the owner run is not
    done yet) the gate says not-enough-data and this test still passes: G4 is then not claimed."""
    d = ROOT / "evals" / "oracle"
    assert oracle.main(["check", "--dir", str(d)]) == 0


# ---- stop-loss ---------------------------------------------------------------------------------------------------------------------------------
def rounds(tmp_path, rows):
    f = tmp_path / "rounds.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


def test_stoploss_with_no_rounds_is_not_enough_data(tmp_path, capsys):
    assert oracle.main(["stoploss", "--file", str(tmp_path / "none.jsonl")]) == 0
    assert "not-enough-data" in capsys.readouterr().out


def test_stoploss_prints_the_rate_and_says_stop_only_after_two_independent_rounds_with_none(tmp_path, capsys):
    f = rounds(tmp_path, [{"round": "R1", "payloads": 40, "new_gaps": 3, "independent": True},
                          {"round": "R2", "payloads": 50, "new_gaps": 0, "independent": True}])
    assert oracle.main(["stoploss", "--file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "R1" in out and "7.5 per 100" in out and "R2" in out and "0.0 per 100" in out
    assert "keep going" in out and "stop adding" not in out
    f = rounds(tmp_path, [{"round": "R1", "payloads": 40, "new_gaps": 3, "independent": True},
                          {"round": "R2", "payloads": 50, "new_gaps": 0, "independent": True},
                          {"round": "R3", "payloads": 60, "new_gaps": 0, "independent": True}])
    oracle.main(["stoploss", "--file", str(f)])
    assert "stop adding parser waves" in capsys.readouterr().out


def test_stoploss_does_not_count_a_round_that_was_not_independent(tmp_path, capsys):
    f = rounds(tmp_path, [{"round": "R1", "payloads": 40, "new_gaps": 0, "independent": True},
                          {"round": "R2", "payloads": 50, "new_gaps": 0, "independent": False}])
    oracle.main(["stoploss", "--file", str(f)])
    assert "keep going" in capsys.readouterr().out


@pytest.mark.parametrize("row", [{"round": "R", "payloads": 0, "new_gaps": 0, "independent": True},
                                 {"round": "R", "payloads": 10, "new_gaps": -1, "independent": True},
                                 {"round": "R", "payloads": 10, "new_gaps": 11, "independent": True},
                                 {"round": "R", "payloads": "10", "new_gaps": 0, "independent": True},
                                 {"payloads": 10, "new_gaps": 0, "independent": True}])
def test_stoploss_rejects_a_bad_row_with_exit_2(tmp_path, row):
    assert oracle.main(["stoploss", "--file", str(rounds(tmp_path, [row]))]) == 2


# ---- the driver (macOS only): real shells, through the helper only ---------------------------------------------------------------------------
@needs_sandbox
def test_the_driver_records_what_two_real_shells_reach_for_a_few_stubbed_pushes(tmp_path):
    corpus = [{"ids": ["T1"], "cmd": "git push origin main"},
              {"ids": ["T2"], "cmd": "g=git; $g push origin main"},
              {"ids": ["T3"], "cmd": "git push origin feature"},
              {"ids": ["T4"], "cmd": "git -C \"$HOME\" push origin HEAD:refs/heads/main"}]
    with realshell.Sandbox(deny_read=tmp_path / "secret") as sb:
        versions = oracle.shell_versions(sb, ("bash", "zsh"), timeout=30)
        raw = sb.run_corpus([e["cmd"] for e in corpus], shells=("bash", "zsh"), jobs=2, timeout=30)
        entries = oracle.entries_from_run(raw, corpus, sb.root)
    assert all(versions.values())
    reach = {e["id"]: oracle.entry_reach(e["results"]) for e in entries}
    assert reach == {"T1": True, "T2": True, "T3": False, "T4": True}
    snap = oracle.build_snapshot(entries, versions, corpus)
    write_snap(tmp_path / "out", oracle.snapshot_name(versions), snap)
    text = (tmp_path / "out" / oracle.snapshot_name(versions)).read_text()
    assert "jade-realshell-" not in text and str(tmp_path) not in text
    # the snapshot is replayable with a classifier that blocks pushes to main: pass; and with one that allows everything: fail
    blocks = oracle.check_snapshot(snap, corpus, {}, classifier({e["cmd"]: BLOCK for e in corpus}), None)
    assert blocks["exit"] == 0
    allows = oracle.check_snapshot(snap, corpus, {}, classifier({e["cmd"]: ALLOW for e in corpus}), None)
    assert allows["exit"] == 1


# ---- review fixes (V6-22): empty words, and a run that timed out ------------------------------------------------------------------------------
def test_the_log_parser_keeps_an_empty_shell_word():
    """`git -C "" push origin main` reaches git with an empty word. The old parser dropped it, so -C swallowed `push` and the truth said False."""
    sep, end = "\x1f", "\x1e"
    log = sep.join(["git", "-C", "", "push", "origin", "main"]) + sep + end + sep.join(["git", "push", "-o", "", "origin", "main"]) + sep + end
    assert realshell._parse_log(log) == [["git", "-C", "", "push", "origin", "main"], ["git", "push", "-o", "", "origin", "main"]]
    assert realshell._parse_log("git" + sep + end) == [["git"]]                        # a call with no argument: no phantom empty word
    assert realshell._parse_log("git" + sep + "push" + sep + "" + sep + end) == [["git", "push", ""]]   # an empty LAST word is a word


@pytest.mark.parametrize("call", [
    ["git", "-C", "", "push", "origin", "main"],
    ["git", "-C", ".", "-C", "", "push", "origin", "main"],
    ["git", "push", "-o", "", "origin", "main"],
])
def test_an_empty_word_in_a_recorded_call_does_not_hide_the_push(call):
    assert oracle.reaches_default_push(call) is True


def test_an_empty_word_is_kept_in_the_snapshot_file():
    cmd = 'git -C "" push origin main'
    raw = [{"cmd": cmd, "results": {"bash": {"shell": "bash", "rc": 0, "timed_out": False, "stdout": "", "stderr": "",
                                              "calls": [["git", "-C", "", "push", "origin", "main"]]}}}]
    entries = oracle.entries_from_run(raw, [{"ids": ["A1"], "cmd": cmd}], "/x")
    assert entries[0]["results"]["bash"]["calls"] == [["git", "-C", "", "push", "origin", "main"]]
    assert oracle.entry_reach(entries[0]["results"]) is True


def test_the_empty_word_payload_is_a_known_gap_row_of_the_oracle_corpus():
    rows = {e["cmd"]: e["ids"] for e in oracle.default_corpus()}
    assert 'git -C "" push origin main' in rows, 'add it to evals/known_gaps/cases.jsonl with "oracle": "si08"'
    assert 'git -C "" push origin main' in oracle.known_gap_rows()


@needs_sandbox
def test_the_driver_sees_an_empty_word_in_a_real_shell():
    with realshell.Sandbox() as sb:
        raw = sb.run_corpus(['git -C "" push origin main', "git push -o '' origin main"], shells=("bash", "zsh"), jobs=2, timeout=30)
    for item in raw:
        for res in item["results"].values():
            assert "" in res["calls"][0], res["calls"]
        assert oracle.entry_reach(item["results"]) is True


def test_the_default_timeout_is_long_enough_for_the_python3_shim():
    """/usr/bin/python3 on macOS starts xcodebuild inside the sandbox: about 25 s. With 5 s every interpreter payload was cut off and counted not judged."""
    assert oracle.DEFAULT_TIMEOUT >= 60
    assert oracle.build_parser().parse_args(["snapshot"]).timeout == oracle.DEFAULT_TIMEOUT


def _slow(*calls):
    return _res(*calls, rc=None, timed_out=True)


def test_a_payload_that_timed_out_in_every_shell_fails_g4():
    """No evidence at all for that payload: G4 may not pass on it."""
    py = "python3 <<'E'\nimport os\nos.system('git push origin main')\nE"
    snap = _snap([("A1", "git push origin main", {"bash": _res(PUSH), "zsh": _res(PUSH)}),
                  ("A2", py, {"bash": _slow(), "zsh": _slow()})])
    r = run(snap, {"git push origin main": BLOCK, py: ALLOW})
    g = gate(r, "G4 timeouts")
    assert g["state"] == "fail" and "A2" in g["detail"] and "A1" not in g["detail"]
    assert r["exit"] == 1


def test_a_timeout_in_every_shell_fails_g4_even_when_nothing_else_reached_a_push():
    snap = _snap([("A1", "sleep 99", {"bash": _slow(), "zsh": _slow()})])
    r = run(snap, {"sleep 99": ALLOW})
    assert gate(r, "G4 timeouts")["state"] == "fail" and r["exit"] == 1


def test_a_timeout_in_one_shell_only_is_not_the_all_shells_failure_but_is_counted_not_judged():
    snap = _snap([("A1", "c1", {"bash": _slow(), "zsh": _res(["git", "status"])})] +
                 [(f"B{i}", f"git push origin main #{i}", {"bash": _res(PUSH), "zsh": _res(PUSH)}) for i in range(20)])
    table = {f"git push origin main #{i}": BLOCK for i in range(20)} | {"c1": ALLOW}
    r = run(snap, table)
    assert gate(r, "G4 timeouts")["state"] == "pass"
    assert "1 not judged" in gate(r, "G4 reach")["detail"]


def test_too_many_not_judged_payloads_fail_g4_and_a_few_do_not():
    rows = [(f"B{i}", f"git push origin main #{i}", True) for i in range(18)] + [("U1", "git push", None), ("U2", "git push #2", None)]
    table = {c: BLOCK for _, c, _ in rows}
    r = run(snapshot_of(rows), table)                                   # 2 of 20 = 10 per cent: at the limit, still a pass
    assert gate(r, "G4 judged")["state"] == "pass" and r["exit"] == 0
    rows.append(("U3", "git push #3", None))
    table["git push #3"] = ALLOW
    r = run(snapshot_of(rows), table)                                   # 3 of 21: above the limit
    g = gate(r, "G4 judged")
    assert g["state"] == "fail" and "3 of 21" in g["detail"] and r["exit"] == 1
    assert gate(run(snapshot_of(rows), table, max_unjudged=0.2), "G4 judged")["state"] == "pass"   # the owner can state another limit


def test_the_snapshot_command_refuses_to_run_without_a_sandbox_backend(monkeypatch, tmp_path, capsys):
    """No flag skips the sandbox: where sandbox-exec is missing the command exits 2 before any payload runs."""
    monkeypatch.setattr(realshell, "SANDBOX_EXEC", str(tmp_path / "no-such-sandbox-exec"))
    assert oracle.main(["snapshot", "--out", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()
    assert "nothing ran" in capsys.readouterr().err.lower()


def test_dry_run_prints_the_corpus_and_runs_nothing(tmp_path, capsys):
    assert oracle.main(["snapshot", "--dry-run", "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "payloads" in out and "hash" in out
    assert not (tmp_path / "out").exists()
