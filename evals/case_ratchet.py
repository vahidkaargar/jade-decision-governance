"""The case ratchet (V6-32): a proposed regression case must fail on the version that erred and pass on the fix.

    uv run python evals/case_ratchet.py check CASES --erred <ref> --fixed <ref>

CASES is a JSONL file of case rows, or the diff that `jade learn cases` printed (its added lines are read). A ref is a git commit-ish of this repository (taken out
with `git archive` into a temporary directory) or a directory that holds a jade tree (`.` is the working tree). Each side runs that tree's own `evals/run_cases.py`
with its own `jade`, in a child process with an empty HOME and no JADE_* or GIT_* variable (the same set-up as evals/never_looser.py).

Verdict per case:
  ready              fails on the erred version, passes on the fixed one: it can be merged as a permanent case
  not reproduced     passes on the erred version. The error did not come from a rule the case can show (it is in learned state, memory or Jev, or the rule was already
                     right). A human writes a test with that state. The case is not merged as it is.
  not fixed          fails on the fixed version
  twin ok            a human-written harmless look-alike (`twin_of`) passes on both versions
  twin fails on ...  the look-alike fails: on the erred version the case is not a fair pair, on the fix the fix blocks too much

Exit 0: every case is ready and every twin is ok. Exit 1: any other verdict. Exit 2: the check could not run (an unknown ref, a runner that crashed). A runner that
crashes is never read as a failing case. A case whose twin is `unverified` is still checked; the report says the twin is missing. Nothing is written into the
repository: JADE proposes, a human merges.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = re.compile(r"^(ok  |FAIL|gap )\s+(\S+) ")
RUN_S = 900


def _never_looser():
    spec = importlib.util.spec_from_file_location("never_looser_for_ratchet", ROOT / "evals" / "never_looser.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fail(msg: str):
    print(f"case_ratchet: could not run: {msg}", file=sys.stderr)
    raise SystemExit(2)


def read_cases(path: Path) -> list[dict]:
    """The rows of a JSONL file, or the added lines of a unified diff."""
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("--- "):
        lines = [x[1:] for x in lines if x.startswith("+") and not x.startswith("+++")]
    rows = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            _fail(f"{path}: line {n} is not a JSON case row")
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            _fail(f"{path}: line {n} is not a case row with an id")
        rows.append(row)
    return rows


def run_on(ref: str, rows: list[dict], scratch: Path) -> dict[str, str]:
    """case id -> `ok` or `FAIL`, from the ref's own runner."""
    nl = _never_looser()
    try:
        tree = nl._tree(ref, scratch)  # noqa: SLF001 - the same ref rules as the never-looser check
    except SystemExit:
        _fail(f"{ref!r} is neither a directory nor a commit of {ROOT}")
    runner = tree / "evals" / "run_cases.py"
    if not runner.is_file():
        _fail(f"{ref!r} has no evals/run_cases.py")
    home = scratch / f"home-{tree.name}"
    home.mkdir(exist_ok=True)
    cases = scratch / f"cases-{tree.name}.jsonl"
    cases.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    env = {**nl._env(home, scratch), "PYTHONPATH": str(tree)}  # noqa: SLF001 - the ref's own `jade` and `policies` come first, ahead of an editable install of another tree
    r = subprocess.run([sys.executable, str(runner), str(cases)], cwd=tree, env=env, capture_output=True, text=True, timeout=RUN_S, check=False)
    if r.returncode not in (0, 1):
        _fail(f"the case runner of {ref!r} exited {r.returncode}: {r.stderr.strip()[-400:]}")
    got = {m.group(2): m.group(1).strip() for line in r.stdout.splitlines() if (m := STATUS.match(line))}
    missing = [x["id"] for x in rows if x["id"] not in got]
    if missing:
        _fail(f"the case runner of {ref!r} printed no result for {', '.join(missing)}")
    return got


def verdicts(rows: list[dict], erred: dict[str, str], fixed: dict[str, str]) -> list[tuple[str, str]]:
    out = []
    for r in rows:
        cid = r["id"]
        if r.get("twin_of"):
            if erred[cid] != "ok":
                out.append((cid, "twin fails on the erred version: the pair is not fair"))
            elif fixed[cid] != "ok":
                out.append((cid, "twin fails on the fix: the fix blocks too much"))
            else:
                out.append((cid, "twin ok"))
        elif fixed[cid] != "ok":
            out.append((cid, "not fixed"))
        elif erred[cid] == "ok":
            out.append((cid, "not reproduced"))
        else:
            out.append((cid, "ready" + ("" if r.get("twin") == "human" else " (twin unverified)")))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check", help="run the cases on the erred and the fixed version")
    ck.add_argument("cases", type=Path)
    ck.add_argument("--erred", required=True, help="the commit or directory that made the error")
    ck.add_argument("--fixed", required=True, help="the commit or directory with the fix")
    a = ap.parse_args(argv)
    try:
        rows = read_cases(a.cases)
        if not rows:
            _fail(f"{a.cases} holds no case")
        with tempfile.TemporaryDirectory() as d:
            scratch = Path(d)
            erred, fixed = run_on(a.erred, rows, scratch), run_on(a.fixed, rows, scratch)
    except SystemExit as e:
        return int(e.code or 0)
    result = verdicts(rows, erred, fixed)
    for cid, v in result:
        print(f"{cid}  {v}")
    good = all(v == "twin ok" or v.startswith("ready") for _, v in result)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
