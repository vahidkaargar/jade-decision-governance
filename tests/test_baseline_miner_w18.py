"""W-18: the 78 'no_parse' baseline outputs of 31-36 characters. The miner counted every command that merely *mentioned* `jev verify`
(a commit message, an echo, a grep, a file written by heredoc) as a call, and every empty tool result as an unparseable answer. Claude Code's
placeholder for a command with no output, `(Bash completed with no output)`, is exactly 31 characters. Synthetic reproductions only: nothing
here reads a transcript, the private dataset or a ledger."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


miner = _load("mine_transcripts", "evals/baseline/mine_transcripts.py")
EMPTY = "(Bash completed with no output)"
P_LINE = "P(yes)=0.64  [cost $0.000013]"


def rows(cmd, out):
    return miner.rows_for_command("s.jsonl", "2026-11-01T00:00:00Z", cmd, out)


def test_the_empty_output_placeholder_is_in_the_reported_length_band():
    assert 31 <= len(EMPTY) <= 36


# ---- real invocations ------------------------------------------------------------------------------
REAL = [
    'jev verify "claim" -s "evidence"',
    'jev --dry-run verify "claim"',
    '~/.local/bin/jev verify "claim" -s -',
    'JEV_MODEL=x jev verify "claim"',
    'cd /tmp && jev verify "claim" -s "e"',
    'echo evidence | jev verify "claim" -s -',
    'r=$(jev verify "claim" -s "e"); echo "$r"',
    'echo "result: $(jev verify "claim" -s "e")"',
    "bash -c 'jev verify \"claim\" -s e'",
    'ssh host "jev verify claim -s e"',
    'if jev verify "claim" -s "e"; then echo ok; fi',
    'python3 ~/.local/bin/jev verify "claim" -s e',
    'uv run jev choice "q" -o a=x -o b=y',
    'jev verify "claim" -s - <<\'EOF\'\nbody\nEOF',
    'cat <<EOF | jev verify "claim" -s -\nbody\nEOF',
    'jev noul "q" -t y -f n',
    'jev score "q" -c low -c high',
]


@pytest.mark.parametrize("cmd", REAL)
def test_real_invocations_are_not_mentions(cmd):
    got = rows(cmd, P_LINE)
    assert len(got) == 1 and not got[0].get("mention")


@pytest.mark.parametrize("cmd", [c for c in REAL if " verify " in c and "ssh" not in c and "bash -c" not in c and "choice" not in c])
def test_real_invocation_result_is_parsed(cmd):
    got = rows(cmd, P_LINE)[0]
    assert got["p"] == 0.64 and "error" not in got


def test_exit_code_prefix_does_not_hide_the_result():
    got = rows('jev verify "claim" -s "e"', "Error: Exit code 4\n" + P_LINE)[0]
    assert got["p"] == 0.64


# ---- mentions --------------------------------------------------------------------------------------
MENTIONS = [
    'git commit -m "docs: jev verify was run before this"',
    "git commit -m 'note: jev verify output'",
    'echo "run jev verify on the claim"',
    "grep -n 'jev verify' docs/00.md",
    'rg "jev choice" skills',
    'cat > notes.md <<\'EOF\'\nuse jev verify "claim" -s file\nEOF',
    'cat > notes.md <<EOF\njev choice "q" -o a=1 -o b=2\nEOF',
    '# jev verify later\nls',
    'sed -n 1,20p SKILL.md  # shows jev verify',
    'python3 -c "print(\'jev verify x\')"',
    'printf "%s" "jev score q" > f',
]


@pytest.mark.parametrize("cmd", MENTIONS)
def test_mentions_are_flagged_and_never_given_a_result_or_an_error(cmd):
    got = rows(cmd, EMPTY)
    assert len(got) <= 1 and all(r.get("mention") for r in got)   # a mention is at most one flagged row, never a call
    assert all(not any(k in r for k in miner.RESULT_KEYS) for r in got)


def test_a_mention_does_not_swallow_a_real_call_in_the_same_command():
    got = rows('echo "jev verify is next"; jev verify "claim" -s "e"', P_LINE)
    assert len(got) == 1 and not got[0].get("mention") and got[0]["p"] == 0.64


def test_two_real_calls_in_one_command_are_two_rows_with_their_own_results():
    got = rows('jev verify "a" -s "e"; jev verify "b" -s "e"', "P(yes)=0.10\nP(yes)=0.90")
    assert [r["p"] for r in got] == [0.10, 0.90] and [r["question"] for r in got] == ["a", "b"]


def test_the_nested_mention_inside_a_real_calls_question_is_still_one_real_call():
    got = rows('jev verify "the grep for jev verify found nothing" -s "e"', P_LINE)
    assert len(got) == 1 and not got[0].get("mention")


# ---- non-answer outputs of a real call -------------------------------------------------------------
@pytest.mark.parametrize("out,kind", [
    (EMPTY, "empty_output"),
    ("", "empty_output"),
    ("  \n", "empty_output"),
    ("Error: Exit code 1", "exit_status_only"),
    ("Exit code 3", "exit_status_only"),
    ("Command running in background with ID: bk1x9z2 and more words after", "background_job"),
    ("something else entirely", "no_parse"),
])
def test_unparsed_real_calls_are_bucketed_by_shape(out, kind):
    got = rows('jev verify "claim" -s "e"', out)[0]
    assert got["error"] == kind and not got.get("mention")


def test_a_jev_error_line_still_wins_over_the_shape_buckets():
    got = rows('jev verify "claim" -s "e"', "Error: Exit code 1\njev: HTTP 429: slow down")[0]
    assert got["error"] == "http_error"


# ---- summary and replay ----------------------------------------------------------------------------
def test_summary_excludes_mentions_from_real_calls_and_counts_them():
    data = (rows('jev verify "a" -s "e"', P_LINE) + rows('jev verify "b" -s "e"', EMPTY)
            + rows('git commit -m "ran jev verify first"', EMPTY) + rows('echo "jev choice x"', EMPTY))
    s = miner.summarize(data, 1)
    assert s["calls_total"] == 4 and s["calls_real"] == 2 and s["calls_mention"] == 2
    assert s["with_result"]["error"] == 1 and s["error_kinds"] == {"empty_output": 1}
    assert s["by_primitive"] == {"verify": 2}


def test_mine_file_end_to_end(tmp_path):
    def line(kind, **kw):
        return json.dumps({"timestamp": "2026-11-01T00:00:00Z", "type": kw.pop("typ"), "message": {"content": [dict(type=kind, **kw)]}})
    lines = [
        line("tool_use", typ="assistant", id="1", name="Bash", input={"command": 'git commit -m "mentions jev verify here"'}),
        line("tool_result", typ="user", tool_use_id="1", content=EMPTY),
        line("tool_use", typ="assistant", id="2", name="Bash", input={"command": 'jev verify "c" -s "e"'}),
        line("tool_result", typ="user", tool_use_id="2", content=[{"type": "text", "text": P_LINE}]),
    ]
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join(lines) + "\n")
    got = miner.mine_file(str(f))
    assert [bool(r.get("mention")) for r in got] == [True, False] and got[1]["p"] == 0.64


def test_replay_policy_ignores_mentions():
    replay = _load("replay_policy", "evals/replay_policy.py").replay
    data = rows('git commit -m "ran jev verify first"', EMPTY) + rows('jev verify "c" -s "e"', EMPTY) + rows('jev verify "c" -s "e"', P_LINE)
    out = replay(data)
    assert out["provider_or_parse_error_exit5_or_6"] == 1 and out["noul_abstain"] == 1
    assert out["repeat_calls_dedup_would_return_cached"] == 1   # the two real calls repeat; the mention is not a third


# ---- wrappers with options ---------------------------------------------------------------------------
WRAPPED = [
    'sudo -u x jev verify "c"', 'timeout 30s jev verify "c"', 'env -i PATH=/bin jev verify "c"', '/usr/bin/env jev verify "c"', 'uv run --quiet jev verify "c"',
    'nice -n 5 jev verify "c"', 'caffeinate jev verify "c"', 'sudo -E jev verify "c"', 'timeout -k 5 30 jev verify "c"', 'FOO=1 env BAR=2 jev --dry-run verify "c"',
]


@pytest.mark.parametrize("cmd", WRAPPED)
def test_a_real_call_behind_a_wrapper_with_options_is_still_a_call(cmd):
    got = rows(cmd, P_LINE)
    assert len(got) == 1 and not got[0].get("mention") and got[0]["p"] == 0.64


@pytest.mark.parametrize("cmd", ['sudo -u x echo "jev verify later"', 'timeout 30s git commit -m "jev verify"', 'git commit -m "uses jev verify"', 'echo "jev verify"'])
def test_a_wrapped_mention_is_still_a_mention(cmd):
    got = rows(cmd, EMPTY)
    assert len(got) == 1 and got[0].get("mention") and not any(k in got[0] for k in miner.RESULT_KEYS)
