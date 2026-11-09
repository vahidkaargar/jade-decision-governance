"""Mine Claude Code transcripts for `jev` invocations and build a baseline.

Pairs each Bash tool_use whose command runs `jev verify|noul|choice|score` with its tool_result and
the next assistant text. Writes the per-call dataset (contains project text: keep it private, it is
gitignored) to --dataset and prints a counts-only summary (safe to publish) to stdout.

    uv run python evals/baseline/mine_transcripts.py --dataset evals/baseline/dataset-YYYY-MM-DD.jsonl > summary.json

Heuristics are regex based. Treat the acceptance numbers as bounds, not labels.
"""

import argparse
import collections
import glob
import json
import os
import re
import statistics
import sys

CMD = re.compile(r"\bjev\b(?:\s+--?[a-z-]+(?:\s+\S+)?)*\s+(?P<prim>verify|noul|choice|score)\b\s*(?P<rest>.*)", re.S)
CMD_HEAD = re.compile(r"\bjev\b(?:\s+--?[a-z-]+(?:\s+\S+)?)*\s+(?P<prim>verify|noul|choice|score)\s+")
IS_JEV = re.compile(r"\bjev\s+(--\S+\s+)*(verify|noul|choice|score)\b")
P_RE = re.compile(r"P\(yes\)=([01](?:\.[0-9]+)?)")
CH_RE = re.compile(r"choice=(\S+) confidence=([01](?:\.[0-9]+)?)")
SC_RE = re.compile(r"score=([0-9]+(?:\.[0-9]+)?) confidence=([01](?:\.[0-9]+)?)")
ERR_RE = re.compile(r"^jev: (.{0,80})", re.M)
QUOTED = re.compile(r"\s*(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)')")
REPORTED = re.compile(r"P\(yes\)|jev|probabilit|confidence", re.I)
DONE = re.compile(r"\b(done|verified|complete|fixed|passes|passed)\b", re.I)
RESULT_KEYS = ("p", "choice", "score", "error")
# Claude Code's stand-ins for a Bash result that carries no jev output. The first is exactly 31 characters, the lower end of the 31-36
# character "no_parse" band of the first baseline (W-18).
EMPTY_RE = re.compile(r"\A\s*(?:\(Bash completed with no output\))?\s*\Z")
EXIT_ONLY_RE = re.compile(r"\A\s*(?:Error: )?Exit code \d+\s*\Z")
BACKGROUND_RE = re.compile(r"\A\s*Command running in background")
# `jev` in command position: after a separator or a control keyword, optional env assignments and wrappers, optional path. Matched on the
# command with quoted text, heredoc bodies and comments blanked out, so a commit message or an echo that names `jev verify` is not a call.
SEP = r"(?:^|[;&|(`{\n]|\$\()[ \t]*"
_NOT_JEV = r"(?!(?:jev|verify|noul|choice|score)\b)"
_OPTS = rf"(?:--?[A-Za-z][\w-]*(?:=\S+)?(?:\s+{_NOT_JEV}[\w@./:=-]+)?\s+)*"   # options of a wrapper, each with at most one value (`sudo -u x`, `nice -n 5`, `uv run --quiet`)
WRAP = (r"(?:(?:(?:ba|z|da)?sh\s+(?:-\w+\s+)*-\w*c|eval|ssh\s+\S+)\s+|(?:then|do|else|elif|if|while|until|!|python3?(?:\s+-[IuB])*|perl\s+-e\s+\S+)\s+|"
        rf"(?:\S*/)?(?:command|exec|time|nohup|env|sudo|doas|nice|caffeinate|xargs|uv\s+run|uvx)\s+{_OPTS}|"
        rf"(?:\S*/)?g?timeout\s+{_OPTS}\d+[smhd]?\s+|"
        r"\w+=\S*\s+)*")
COMMAND_POS = re.compile(SEP + WRAP + r"(?:\S*/)?(?P<jev>jev)\b(?=(?:\s+--?[a-z-]+(?:\s+\S+)?)*\s+(?:verify|noul|choice|score)\b)", re.S)
SHELL_ARG = re.compile(r"(?:\b(?:ba|z|da)?sh\s+(?:-\w+\s+)*-\w*c|\beval|\bssh\s+\S+)\s*$")
HEREDOC = re.compile(r"<<-?[ \t]*(?:'([A-Za-z_]\w*)'|\"([A-Za-z_]\w*)\"|([A-Za-z_]\w*))")


def mask_literals(cmd: str) -> str:
    """The command with quoted text, heredoc bodies and comments replaced by spaces, same length so offsets still line up. Text that a shell
    would run stays visible: `$(...)` and backticks inside double quotes, and the argument of `bash -c`, `eval` and `ssh host`."""
    out, i, n = [], 0, len(cmd)
    pending: list[str] = []   # heredoc terminators waiting for the end of this line

    def blank(text: str) -> str:
        return "".join(c if c == "\n" else " " for c in text)

    while i < n:
        c = cmd[i]
        if c == "\n" and pending:
            out.append(c)
            i += 1
            for tag in pending:
                j = i
                while j < n:
                    end = cmd.find("\n", j)
                    end = n if end < 0 else end
                    if cmd[j:end].strip() == tag:
                        break
                    j = end + 1
                out.append(blank(cmd[i:j]))
                i = j
            pending = []
            continue
        if c == "\\" and i + 1 < n:
            out.append(c + cmd[i + 1])
            i += 2
            continue
        if c == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            j = cmd.find("\n", i)
            j = n if j < 0 else j
            out.append(blank(cmd[i:j]))
            i = j
            continue
        if c == "<" and cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = HEREDOC.match(cmd, i)
            if m:
                pending.append(m.group(1) or m.group(2) or m.group(3))
                out.append(cmd[i:m.end()])
                i = m.end()
                continue
        if c in "'\"":
            j, depth, shown = i + 1, 0, [" "]
            while j < n:
                d = cmd[j]
                if c == '"' and d == "\\" and j + 1 < n:
                    shown.append("  ")
                    j += 2
                    continue
                if c == '"' and d == "$" and cmd.startswith("$(", j):
                    depth += 1
                    shown.append("$(")
                    j += 2
                    continue
                if c == '"' and depth and d == ")":
                    depth -= 1
                    shown.append(d)
                elif c == '"' and (depth or d == "`"):
                    shown.append(d)
                elif d == c and not depth:
                    break
                else:
                    shown.append(d if d == "\n" else " ")
                j += 1
            body = cmd[i + 1:j]
            if SHELL_ARG.search("".join(out)):
                out.append(" " + body + " ")   # the shell runs this text
            else:
                out.append("".join(shown)[: len(body) + 1].ljust(len(body) + 1) + " ")
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def real_call_starts(cmd: str) -> set[int]:
    """Offsets of the `jev` words that are run, not merely written."""
    return {m.start("jev") for m in COMMAND_POS.finditer(mask_literals(cmd))}


def error_kind(text: str) -> str:
    """Bucket a CLI error line without keeping any of its text."""
    if "unrecognized arguments" in text or "needs at least" in text or "option must be" in text:
        return "cli_usage_error"
    if "timed out" in text or "request failed" in text:
        return "network_error"
    if "no key" in text:
        return "no_key"
    if text.startswith("HTTP"):
        return "http_error"
    return "other_error"


def shape_kind(out: str) -> str:
    """Bucket an output that holds no result and no `jev:` error line, by shape only."""
    if EMPTY_RE.match(out):
        return "empty_output"
    if EXIT_ONLY_RE.match(out):
        return "exit_status_only"
    if BACKGROUND_RE.match(out):
        return "background_job"
    return "no_parse"


def band(p: float) -> str:
    for limit, name in ((0.3, "<0.3"), (0.5, "0.3-0.5"), (0.7, "0.5-0.7"), (0.85, "0.7-0.85")):
        if p < limit:
            return name
    return ">=0.85"


def result_text(content) -> str:
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
    return content or ""


def rows_for_command(session: str, ts, cmd: str, out: str) -> list[dict]:
    """One row per `jev` call the command runs. A command that only writes the words (a commit message, an echo, a grep, a heredoc) gets a
    single row flagged `mention`, which carries no result: its output belongs to some other program (W-18)."""
    real = real_call_starts(cmd)
    heads = [m for m in CMD_HEAD.finditer(cmd) if m.start() in real]
    mention = not heads
    if mention:
        first = CMD.search(cmd)
        heads = [first] if first else []
    new = []
    for m in heads:
        q = QUOTED.match(cmd, m.end()) if not mention else QUOTED.match(m.group("rest"))
        new.append({
            "session": session, "ts": ts, "primitive": m.group("prim"), "dry_run": "--dry-run" in cmd,
            "cmd_len": len(cmd), "state_stdin": " -s - " in cmd or "-s -\n" in cmd,
            "question": ((q.group(1) or q.group(2) or "")[:200] if q else ""), "out_len": len(out),
            **({"mention": True} if mention else {}),
        })
    new_real = [] if mention else new
    results = [("p", float(p)) for p in P_RE.findall(out)]
    results += [("choice", list(c)) for c in CH_RE.findall(out)] + [("score", list(s)) for s in SC_RE.findall(out)]
    for row, (key, value) in zip(new_real, results, strict=False):
        row[key] = value
    errs = ERR_RE.findall(out)
    for row in new_real:
        if not any(k in row for k in RESULT_KEYS):
            row["error"] = error_kind(errs[0]) if errs else ("other_error" if "jev:" in out else shape_kind(out))
    return new


def mine_file(path: str) -> list[dict]:
    session = os.path.basename(path)
    rows, pending, awaiting = [], {}, []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            content = (obj.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            texts = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                kind = c.get("type")
                if kind == "tool_use" and c.get("name") == "Bash":
                    cmd = (c.get("input") or {}).get("command", "")
                    if IS_JEV.search(cmd):
                        pending[c.get("id")] = (obj.get("timestamp"), cmd)
                elif kind == "tool_result" and c.get("tool_use_id") in pending:
                    ts, cmd = pending.pop(c["tool_use_id"])
                    new = rows_for_command(session, ts, cmd, result_text(c.get("content")))
                    rows += new
                    awaiting += new
                elif kind == "text" and obj.get("type") == "assistant":
                    texts.append(c.get("text", ""))
            if texts and awaiting:
                joined = "\n".join(texts)
                for row in awaiting:
                    row["reported_in_next_text"] = bool(REPORTED.search(joined))
                    row["next_text_done_claim"] = bool(DONE.search(joined))
                awaiting = []
    return rows


def summarize(rows: list[dict], n_files: int) -> dict:
    real = [r for r in rows if not r["dry_run"] and not r.get("mention")]
    ps = sorted(r["p"] for r in real if "p" in r)
    low = [r for r in real if "p" in r and r["p"] < 0.7]
    with_p = [r for r in real if "p" in r and "reported_in_next_text" in r]
    repeats = collections.Counter((r["session"], r["primitive"], r["question"]) for r in real if r["question"])
    per_file = collections.Counter(r["session"] for r in real)
    stamps = [r["ts"] for r in real if r["ts"]]
    return {
        "transcript_files": n_files,
        "files_with_real_calls": len(per_file),
        "calls_total": len(rows),
        "calls_dry_run": sum(r["dry_run"] and not r.get("mention") for r in rows),
        "calls_mention": sum(bool(r.get("mention")) for r in rows),
        "calls_real": len(real),
        "calls_per_file": {"median": statistics.median(per_file.values()) if per_file else 0, "max": max(per_file.values(), default=0)},
        "by_primitive": dict(collections.Counter(r["primitive"] for r in real)),
        "with_result": {k: sum(k in r for r in real) for k in RESULT_KEYS},
        "error_kinds": dict(collections.Counter(r["error"] for r in real if "error" in r)),
        "p_stats": {"n": len(ps), "mean": round(statistics.mean(ps), 3) if ps else None,
                    "median": statistics.median(ps) if ps else None,
                    "p10": ps[len(ps) // 10] if ps else None, "p90": ps[9 * len(ps) // 10] if ps else None},
        "p_bands": dict(collections.Counter(band(p) for p in ps)),
        "low_p_lt_0.7": {"n": len(low), "followed_by_done_claim": sum(bool(r.get("next_text_done_claim")) for r in low),
                         "reported_in_next_text": sum(bool(r.get("reported_in_next_text")) for r in low)},
        "p_reported_in_next_text": {"n": len(with_p), "yes": sum(r["reported_in_next_text"] for r in with_p)},
        "repeat_questions": {"distinct_repeated": sum(1 for v in repeats.values() if v > 1),
                             "calls_in_repeats": sum(v for v in repeats.values() if v > 1)},
        "state_via_stdin": sum(r["state_stdin"] for r in real),
        "calls_per_day": dict(sorted(collections.Counter((r["ts"] or "")[:10] for r in real).items())),
        "first_ts": min(stamps, default=None),
        "last_ts": max(stamps, default=None),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--dataset", default=os.devnull, help="where to write the private per-call dataset")
    args = ap.parse_args()
    files = glob.glob(os.path.join(args.root, "**", "*.jsonl"), recursive=True)
    rows = []
    for path in files:
        try:
            rows += mine_file(path)
        except OSError as exc:
            print(f"skip {path}: {exc}", file=sys.stderr)
    with open(args.dataset, "w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(summarize(rows, len(files)), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
