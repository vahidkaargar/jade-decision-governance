"""W-98b: `evals/ho_replay.py` counts the calls of recorded transcripts that fall in the human-only classes and prints counts only."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_HOST = "customer-secret-host.example"


def line(tool, tool_input, cwd="/tmp/proj"):
    return json.dumps({"cwd": cwd, "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": tool, "input": tool_input}]}})


def make(tmp_path):
    f = tmp_path / "s1.jsonl"
    rows = [
        line("Bash", {"command": "rm -rf ./data"}),                                              # delete, already high by R-11
        line("Bash", {"command": "stripe refunds create --charge ch_1 --live"}),                 # money, low by its rule: a new ask
        line("Bash", {"command": f"curl -X POST https://api.stripe.com/v1/refunds -d a={SECRET_HOST}"}),   # money
        line("Bash", {"command": "npm publish"}),                                                # release, already high (R-10 or none)
        line("Bash", {"command": "ls -la"}),
        line("Bash", {"command": "rm -rf node_modules"}),
        line("mcp__stripe__create_refund", {"amount": 5}),
        line("mcp__stripe__list_charges", {}),
        line("Read", {"file_path": "/tmp/x"}),                                                   # not a Bash or MCP call: not counted
        "not json at all",
        json.dumps({"message": {"content": "text"}}),
    ]
    f.write_text("\n".join(rows) + "\n")
    return f


def run(*args):
    return subprocess.run([sys.executable, str(ROOT / "evals" / "ho_replay.py"), *map(str, args)], capture_output=True, text=True, cwd=ROOT)


def test_counts_classes_and_new_asks_and_prints_no_command_text(tmp_path):
    f = make(tmp_path)
    r = run(f, "--no-overlay")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "Bash and MCP calls: 8" in out
    assert "classifier errors: 0" in out
    assert "HO-delete" in out and "HO-money" in out and "HO-release" in out
    for leaked in (SECRET_HOST, "ch_1", "./data", "api.stripe.com", "stripe refunds", "rm -rf"):
        assert leaked not in out
    assert "mcp__stripe" not in out   # tool names only with --tools


def test_tools_flag_prints_mcp_tool_names_but_never_an_input(tmp_path):
    out = run(make(tmp_path), "--no-overlay", "--tools").stdout
    assert "mcp__stripe__create_refund" in out and "mcp__stripe__list_charges" not in out
    assert "amount" not in out


def test_a_directory_and_a_missing_input_are_fine(tmp_path):
    make(tmp_path)
    assert "transcripts: 1" in run(tmp_path, "--no-overlay").stdout
    assert run(tmp_path / "nope", "--no-overlay").returncode == 0


def test_max_files_and_empty_input(tmp_path):
    (tmp_path / "empty.jsonl").write_text("")
    r = run(tmp_path, "--no-overlay", "--max-files", "1")
    assert r.returncode == 0 and "Bash and MCP calls: 0" in r.stdout
