"""Human-only replay (W-98b): how many recorded tool calls fall in the human-only classes, and how many of those the gate would newly ask about at a level.

Reads Claude Code transcripts (JSONL files with `tool_use` blocks), classifies every Bash call and MCP tool call with the real policy and prints COUNTS ONLY: calls per class,
how many were already high before the class, how many a level would raise to high (the new asks), and how often a class pairs with each rule. No command text, no path and
no tool input is printed or saved: a corpus of real commands holds hosts, paths and key names. MCP tool names are printed only with `--tools`. Nothing is executed and nothing
leaves the machine. Usage:

    uv run python evals/ho_replay.py [DIR_OR_FILE ...] [--since-days N] [--max-files N] [--no-overlay] [--tools]

The default input is `~/.claude/projects`. Run it before a release that changes a class and compare the new-ask count with the click budget the owner accepted.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LEVEL_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def transcript_files(inputs: list[Path], since_days: float | None, max_files: int | None) -> list[Path]:
    files: list[Path] = []
    for p in inputs:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.rglob("*.jsonl")))
    if since_days is not None:
        cutoff = time.time() - since_days * 86400
        files = [f for f in files if f.stat().st_mtime >= cutoff]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files[:max_files] if max_files else files


def calls_in(path: Path):
    """(tool, tool_input, cwd) of every tool_use block of one transcript. A line that is not JSON is skipped, never fatal."""
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            msg = row.get("message") if isinstance(row, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("name"), str) and isinstance(block.get("input"), dict):
                    yield block["name"], block["input"], row.get("cwd") or ""


def replay(files: list[Path], base_only: bool, names: bool) -> dict:
    from jade import humanonly, policy

    out = {"files": len(files), "calls": 0, "classified": 0, "errors": 0, "by_class": collections.Counter(), "already_high": collections.Counter(),
           "new_asks": collections.Counter(), "any_class": 0, "any_class_new_ask": 0, "by_rule": collections.Counter(), "tools": collections.Counter(), "ms": []}

    def one(tool: str, tool_input: dict, cwd: str) -> None:
        if tool != "Bash" and not policy.is_mcp(tool):
            return
        out["calls"] += 1
        start = time.perf_counter()
        try:
            a = policy.assess(tool, tool_input, cwd)
        except Exception:  # noqa: BLE001 - a classifier crash is a finding to count, never a reason to stop
            out["errors"] += 1
            return
        finally:
            out["ms"].append(time.perf_counter() - start)
        classes = [c for c in a.ho_ids if c in humanonly.ALL]
        if not classes:
            return
        out["classified"] += 1
        raised = LEVEL_RANK.get(a.level, 0) < LEVEL_RANK["high"]
        out["any_class"] += 1
        out["any_class_new_ask"] += raised
        for c in classes:
            out["by_class"][c] += 1
            out["already_high" if not raised else "new_asks"][c] += 1
            out["by_rule"][(c, a.rule_id)] += 1
        if names and policy.is_mcp(tool):
            out["tools"][(tool, tuple(classes))] += 1

    if base_only:
        with policy.base_only():
            for f in files:
                for t, i, c in calls_in(f):
                    one(t, i, c)
    else:
        for f in files:
            for t, i, c in calls_in(f):
                one(t, i, c)
    return out


def report(r: dict, top: int) -> str:
    n = r["calls"] or 1
    ms = sorted(r["ms"])
    lines = [f"transcripts: {r['files']}", f"Bash and MCP calls: {r['calls']}", f"classifier errors: {r['errors']}",
             f"calls in a human-only class: {r['any_class']} ({100 * r['any_class'] / n:.2f}%)",
             f"  of which a level would newly raise to high (new asks): {r['any_class_new_ask']} ({100 * r['any_class_new_ask'] / n:.2f}% of all calls)"]
    lines.append("per class (calls, already high by their rule, new asks):")
    for c in sorted(r["by_class"]):
        lines.append(f"  {c:<12}{r['by_class'][c]:>7}{r['already_high'][c]:>8}{r['new_asks'][c]:>8}")
    lines.append(f"class by rule (top {top}):")
    for (c, rule), k in r["by_rule"].most_common(top):
        lines.append(f"  {c:<12}{rule:<8}{k:>7}")
    if r["tools"]:
        lines.append(f"MCP tools by class (top {top}):")
        for (t, cl), k in r["tools"].most_common(top):
            lines.append(f"  {k:>6}  {','.join(cl)}  {t}")
    if ms:
        pct = lambda q: 1000 * ms[min(len(ms) - 1, int(q * len(ms)))]  # noqa: E731
        lines.append(f"latency ms: p50 {pct(.5):.1f}  p95 {pct(.95):.1f}  p99 {pct(.99):.1f}  max {1000 * ms[-1]:.0f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="*", type=Path, help="transcript files or directories (default: ~/.claude/projects)")
    ap.add_argument("--since-days", type=float, default=None, help="only transcripts modified in the last N days")
    ap.add_argument("--max-files", type=int, default=None, help="at most N transcripts, newest first")
    ap.add_argument("--no-overlay", action="store_true", help="classify with the shipped policy only, without the machine-local overlays")
    ap.add_argument("--tools", action="store_true", help="also print MCP tool names with their class (never a command or an input)")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)
    inputs = args.inputs or [Path.home() / ".claude" / "projects"]
    files = transcript_files(inputs, args.since_days, args.max_files)
    print(report(replay(files, args.no_overlay, args.tools), args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
