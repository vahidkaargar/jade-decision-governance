"""Replay a policy table (V6-32): before a human looks at a rule that `jade learn policy` calls `review`, replay recorded calls against the idea.

    uv run python evals/learn_replay.py TABLE.json [TRANSCRIPT_DIR_OR_FILE ...] [--commands FILE] [--since-days N] [--max-files N] [--no-overlay] [--json]

TABLE.json is the output of `jade learn policy --json`. For each row with advice `review` the candidate is one step: the rule at the next lower level. The corpus is
the Bash and MCP calls of Claude Code transcripts (the same input as evals/ho_replay.py, default ~/.claude/projects) and, with --commands, a file of commands
(evals/gate_replay.py). Every call is classified with the real policy. A rule decides a call by its rule id, so a candidate can only move the calls that rule decides;
the classifier is not run again with a changed rule, the candidate level is applied to those calls (`gate_replay.compare` prints the moves).

The human-only check is counted call by call, never as one total: two opposite moves must not cancel to zero (the aggregate counters of ho_replay.py would). It is
conservative: it counts every human-only call the rule decides, although at a level (`jade level`) the hook raises such a call to high anyway. One such call withdraws the
candidate (`blocked_by_replay`). A candidate with no call in the corpus is `not_replayed`: an empty replay proves nothing.

Output: counts, rule ids and levels. No command text, no path, no tool input. Nothing is executed and nothing leaves the machine. Exit 0: every `review` row was replayed
with zero human-only flips, or there was none. Exit 1: a candidate was withdrawn. Exit 2: a `review` row could not be replayed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

import gate_replay  # noqa: E402
import ho_replay  # noqa: E402


def corpus_items(inputs: list[Path], since_days: float | None = None, max_files: int | None = None, commands: Path | None = None) -> list[dict]:
    """The calls to replay, as {tool, input, cwd}: Bash and MCP calls of the transcripts, and the commands of `commands`."""
    from jade import policy

    items: list[dict] = []
    for f in ho_replay.transcript_files(list(inputs), since_days, max_files):
        for tool, tool_input, cwd in ho_replay.calls_in(f):
            if tool == "Bash" or policy.is_mcp(tool):
                items.append({"tool": tool, "input": tool_input, "cwd": cwd})
    if commands is not None:
        items += [{"tool": c["tool"], "input": {"command": c["command"]}, "cwd": c["cwd"]} for c in gate_replay.load_commands(commands)]
    return items


def classify_corpus(items: list[dict], base_only: bool = False) -> list[dict]:
    """{rule, level, ho} per call, from the real policy. A call the classifier raises on is level `error` with no rule: it is counted, never dropped."""
    from jade import humanonly, policy

    out: list[dict] = []

    def one(it: dict) -> None:
        try:
            a = policy.assess(it["tool"], it["input"], it["cwd"])
        except Exception:  # noqa: BLE001 - a classifier crash is a finding to count
            out.append({"rule": None, "level": "error", "ho": []})
            return
        out.append({"rule": a.rule_id, "level": a.level, "ho": [c for c in a.ho_ids if c in humanonly.ALL]})

    if base_only:
        with policy.base_only():
            for it in items:
                one(it)
    else:
        for it in items:
            one(it)
    return out


def replay_table(table: list[dict], corpus: list[dict]) -> list[dict]:
    """The table with each `review` row replayed: a `replay` block (calls, calls the rule decides, candidate level, human-only flips, the moves) and an advice that stays
    `review` only when the replay found nothing against it."""
    out = []
    for row in table:
        if row.get("advice") != "review":
            out.append(row)
            continue
        rule, level = row["rule"], row.get("level")
        replay: dict
        if not corpus:
            out.append({**row, "advice": "not_replayed", "replay": {"reason": "no calls in the corpus: nothing was replayed"}})
            continue
        order = gate_replay.LEVEL_ORDER
        if level not in order or order.index(level) == 0:
            out.append({**row, "advice": "not_replayed", "replay": {"reason": f"the rule is at the lowest level ({level}): there is no lower level to try"}})
            continue
        cand = order[order.index(level) - 1]
        before = [(c["level"], c["rule"]) for c in corpus]
        after = [(cand if c["rule"] == rule else c["level"], c["rule"]) for c in corpus]
        decided = sum(1 for c in corpus if c["rule"] == rule)
        flips = sum(1 for c in corpus if c["rule"] == rule and c["ho"])   # call by call: opposite moves never cancel
        replay = {"calls": len(corpus), "decided_by_rule": decided, "candidate_level": cand, "human_only_flips": flips, "moves": gate_replay.compare(before, after),
                  "errors": sum(1 for c in corpus if c["level"] == "error")}
        advice = "blocked_by_replay" if flips else "not_replayed" if not decided else "review"
        if not decided:
            replay["reason"] = "no call of the corpus is decided by this rule"
        out.append({**row, "advice": advice, "replay": replay})
    return out


def render(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        if "replay" not in r:
            continue
        p = r["replay"]
        head = f"rule {r['rule']} ({r.get('level')}): advice {r['advice']}"
        if "reason" in p and "calls" not in p:
            lines.append(f"{head}. {p['reason']}")
            continue
        lines.append(f"{head}. Corpus {p['calls']} call(s), {p['decided_by_rule']} decided by this rule; candidate level {p['candidate_level']}; "
                     f"human-only calls this rule decides: {p['human_only_flips']}; classifier errors {p['errors']}.")
        lines += [f"    {x}" for x in p["moves"].splitlines()]
        if p.get("reason"):
            lines.append(f"    {p['reason']}")
    return "\n".join(lines) if lines else "No row has advice `review`: nothing to replay."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("table", type=Path, help="the output of `jade learn policy --json`")
    ap.add_argument("inputs", nargs="*", type=Path, help="transcript files or directories (default: ~/.claude/projects)")
    ap.add_argument("--commands", type=Path, help="also replay the commands of this file (a JSON array or JSONL, see evals/gate_replay.py)")
    ap.add_argument("--since-days", type=float, default=None)
    ap.add_argument("--max-files", type=int, default=None)
    ap.add_argument("--no-overlay", action="store_true", help="classify with the shipped policy only")
    ap.add_argument("--json", action="store_true", help="print the replayed table as one JSON line")
    a = ap.parse_args(argv)
    table = json.loads(a.table.read_text(encoding="utf-8"))
    if not isinstance(table, list) or not all(isinstance(r, dict) and "rule" in r and "advice" in r for r in table):
        print("learn_replay: the table must be the JSON list that `jade learn policy --json` prints", file=sys.stderr)
        return 2
    if not any(r["advice"] == "review" for r in table):
        print(json.dumps(table, sort_keys=True, separators=(",", ":")) if a.json else render(table))
        return 0
    inputs = a.inputs or [Path.home() / ".claude" / "projects"]
    corpus = classify_corpus(corpus_items(inputs, a.since_days, a.max_files, a.commands), a.no_overlay)
    rows = replay_table(table, corpus)
    print(json.dumps(rows, sort_keys=True, separators=(",", ":")) if a.json else render(rows))
    if any(r["advice"] == "blocked_by_replay" for r in rows):
        return 1
    return 2 if any(r["advice"] == "not_replayed" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
