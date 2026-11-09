"""Gate replay: classify a file of recorded shell commands with the real risk policy and report how the gate would have rated them.

Nothing is executed and nothing leaves the machine. The commands themselves are never printed: a corpus of real commands holds hosts, paths and
key names, so the report shows only counts, shares, rule ids and latency. Usage:

    uv run python evals/gate_replay.py commands.jsonl [--cwd DIR] [--exclude-rules R-14,R-10] [--top 15] [--no-overlay]
                                       [--save levels.json] [--compare before.json]

Input: a JSON array or a JSONL file. Each item is a command string or an object with `command` (and optionally `cwd` and `tool`, default Bash).
`--exclude-rules` names the rules a human already decided to keep high (ssh, git push ...): the share of high and critical commands outside
them is the number to drive down. `--save` writes the per-command (level, rule) list, still without any command text; `--compare` reads such a
file from an earlier run on the same input and prints the level transitions.
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

DEFAULT_EXCLUDE = ("R-14", "R-10")  # ssh/scp/sftp/rsync to a remote, and push/publish/composio execute: decided high by the user
LEVEL_ORDER = ("low", "medium", "high", "critical")


def load_commands(path: Path) -> list[dict]:
    """Items of a JSON array, or of a JSONL file, as {command, cwd, tool}."""
    text = path.read_text()
    try:
        data = json.loads(text)
        items = data if isinstance(data, list) else [data]
    except ValueError:
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
    out = []
    for it in items:
        if isinstance(it, str):
            out.append({"command": it})
        elif isinstance(it, dict) and isinstance(it.get("command"), str):
            out.append({"command": it["command"], "cwd": it.get("cwd") or "", "tool": it.get("tool") or "Bash"})
        else:
            raise SystemExit("every item must be a command string or an object with a string `command`")
    return out


def percentile(sorted_vals: list[float], q: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))] if sorted_vals else 0.0


def replay(items: list[dict], cwd: str = "", base_only: bool = False) -> tuple[list[tuple[str, str]], list[float]]:
    """(level, rule id) per item and the seconds each classification took. A command the classifier raises on is level `error`."""
    from jade import policy

    results: list[tuple[str, str]] = []
    times: list[float] = []

    def one(it: dict) -> None:
        start = time.perf_counter()
        try:
            a = policy.assess(it.get("tool") or "Bash", {"command": it["command"]}, it.get("cwd") or cwd)
            results.append((a.level, a.rule_id))
        except Exception as exc:  # noqa: BLE001 - a classifier crash is a finding, never a reason to stop the replay
            results.append(("error", type(exc).__name__))
        times.append(time.perf_counter() - start)

    if base_only:
        with policy.base_only():
            for it in items:
                one(it)
    else:
        for it in items:
            one(it)
    return results, times


def summary(results: list[tuple[str, str]], times: list[float], exclude: tuple[str, ...], top: int) -> str:
    n = len(results) or 1
    levels = collections.Counter(lv for lv, _ in results)
    rules = collections.Counter(results)
    lines = [f"commands: {len(results)}", "level share:"]
    for lv in (*LEVEL_ORDER, "error"):
        if levels[lv]:
            lines.append(f"  {lv:<9}{levels[lv]:>6}  {100 * levels[lv] / n:5.1f}%")
    flagged = [(lv, r) for lv, r in results if lv in ("high", "critical")]
    outside = [(lv, r) for lv, r in flagged if r not in exclude]
    lines.append(f"high+critical: {len(flagged)} ({100 * len(flagged) / n:.1f}%)")
    lines.append(f"high+critical outside {','.join(exclude) or '-'}: {len(outside)} ({100 * len(outside) / n:.1f}%)")
    lines.append(f"top rules (level, rule, count), high and critical first, of {len(rules)} distinct:")
    key = lambda kv: (-LEVEL_ORDER.index(kv[0][0]) if kv[0][0] in LEVEL_ORDER else 1, -kv[1])  # noqa: E731
    for (lv, r), c in sorted(rules.items(), key=key)[:top]:
        lines.append(f"  {lv:<9}{r:<8}{c:>6}{'   (excluded)' if r in exclude and lv in ('high', 'critical') else ''}")
    ts = sorted(times)
    lines.append("latency ms: " + "  ".join(f"{name} {1000 * percentile(ts, q):.1f}" for name, q in (("p50", .5), ("p90", .9), ("p95", .95), ("p99", .99)))
                 + f"  max {1000 * (ts[-1] if ts else 0):.0f}")
    return "\n".join(lines)


def compare(before: list, after: list[tuple[str, str]]) -> str:
    if len(before) != len(after):
        return f"cannot compare: {len(before)} saved results against {len(after)} commands"
    moves = collections.Counter((b[0], b[1], a[0], a[1]) for b, a in zip(before, after, strict=True) if tuple(b) != a)
    if not moves:
        return "no command changed level or rule"
    lines = [f"changed: {sum(moves.values())}", "  from (level rule) -> to (level rule), count:"]
    lines += [f"  {b0:<9}{b1:<7}-> {a0:<9}{a1:<7}{c:>5}" for (b0, b1, a0, a1), c in moves.most_common(25)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("--cwd", default="", help="working directory every command is classified in (default: the item's own `cwd`, else none)")
    ap.add_argument("--exclude-rules", default=",".join(DEFAULT_EXCLUDE), help="comma list of rule ids decided by a human; reported separately")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--no-overlay", action="store_true", help="classify with the shipped policy only, without the machine-local overlays")
    ap.add_argument("--save", type=Path, help="write the per-command [level, rule] list (no command text) for a later --compare")
    ap.add_argument("--compare", type=Path, help="a --save file from an earlier run on the same input")
    args = ap.parse_args(argv)
    items = load_commands(args.file)
    results, times = replay(items, args.cwd, base_only=args.no_overlay)
    exclude = tuple(r for r in args.exclude_rules.split(",") if r)
    print(summary(results, times, exclude, args.top))
    if args.compare:
        print(compare(json.loads(args.compare.read_text()), results))
    if args.save:
        args.save.write_text(json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
