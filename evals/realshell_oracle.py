"""Real-shell oracle snapshot (V6-22, gate G4 of docs/14-v6/10). Truth from execution: a payload is run in real bash and zsh inside the sandbox of
`evals/realshell.py`, PATH stubs record which programs the shell reached, and the classifier is then compared with what the shell did. A payload where the
shell reaches `git push origin main` and the gate does not block it with SI-08 is a bypass.

    uv run python evals/realshell_oracle.py snapshot [--add FILE] [--out DIR] [--shells bash,zsh] [--jobs 8] [--timeout 120] [--dry-run]
    uv run python evals/realshell_oracle.py check [--dir DIR] [--known-gap-ceiling N] [--max-unjudged 0.10]
    uv run python evals/realshell_oracle.py stoploss [--file evals/oracle/rounds.jsonl]

snapshot   Owner run, on macOS, before each release. It runs every payload of the corpus in each shell, only through `evals/realshell.py` (macOS
           sandbox-exec and a canary check; where there is no sandbox nothing runs and the exit code is 2; there is no flag that skips it). It writes
           `evals/oracle/realshell-<shell versions>.json`: the recorded stub calls, no output, no clock, no host path. `--dry-run` only prints what would run.
           Time: `/usr/bin/python3` on macOS is the xcode-select shim and starts xcodebuild inside the sandbox, about 25 s per run, so the default timeout is
           120 s (about 5 minutes in all with --jobs 8). A payload that times out in every shell is no evidence: `check` fails on it.
           `--add FILE` adds the payloads of an independent review round (a JSON list or JSON Lines of strings or of objects with `cmd` or `command` and an
           optional `id`). A new bypass found this way must then enter the case files as a `known_gap` row with `"oracle": "si08"` (docs/13).
check      G4. Pure: it reads the snapshot files and replays the classifier (the real hook, in a child process with its own empty HOME), so it runs on
           Linux and in CI. It never runs a shell. States: pass, fail, not-enough-data (never a pass). Exit 0, 1 on a fail, 2 when it could not run.
stoploss   Reads the rounds file and prints the new gaps per 100 payloads of each independent review round. After two such rounds in a row with none, it says
           to stop adding parser waves. This is a stopping rule, not a proof.

The corpus is the SI-08 family: gate rows of the case files under evals/ whose `expect_blocked` holds SI-08, and rows marked `"oracle": "si08"`. Every payload
is a push to a stub. Selection is by row metadata, never by a pattern on the command, so a destructive payload is never sent to a shell by this tool.
The truth reads only the recorded call (`reaches_default_push`), and does not import the classifier, so the two sides are independent.
What the oracle does not cover: stubs exist only for the programs listed in evals/realshell.py (a payload that reaches kubectl or an interpreter is confined
but not recorded); macOS only (W-115, no Linux backend); a payload that needs a repository or a branch (`git push` with no refspec) is counted "not judged".
Standard library only (the classifier side loads decision_replay.py, which needs the repository).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = ROOT / "evals" / "oracle"
ROUNDS_FILE = ORACLE_DIR / "rounds.jsonl"
FORMAT = 1
PASS, FAIL, NODATA = "pass", "fail", "not-enough-data"
DEFAULT_SHELLS = ("bash", "zsh")
VERSION_PROBE = {"bash": 'echo "$BASH_VERSION"', "zsh": 'echo "$ZSH_VERSION"', "sh": 'echo "${BASH_VERSION:-posix}"'}
DEFAULT_BRANCHES = ("main", "master")
STOP_AFTER = 2   # independent rounds in a row with no new gap
DEFAULT_TIMEOUT = 120.0   # seconds per payload and shell. /usr/bin/python3 on macOS is the xcode-select shim and takes about 25 s in the sandbox; 5 s cut every interpreter payload off
UNJUDGED_LIMIT = 0.10     # G4 fails when more than this share of the payloads gave no evidence


class OracleError(Exception):
    """The oracle could not run in full: exit 2, never an answer."""


def _module(name: str):
    path = ROOT / "evals" / f"{name}.py"
    have = sys.modules.get(name)
    if have is not None and getattr(have, "__file__", None) == str(path):   # one copy per process (a test patches the one it loaded)
        return have
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- truth: what one recorded call reached ---------------------------------------------------------------------------------------------------
# git global options that take a value in the next word, and options of `push` and `send-pack` that do. Anything else that starts with `-` is a flag.
_GIT_GLOBAL_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix", "--attr-source", "--exec-path"}
_PUSH_VALUE = {"--repo", "-o", "--push-option", "--receive-pack", "--exec"}
_ALL_BRANCHES = {"--all", "--mirror", "--branches"}


def _dest_is_default(dst: str) -> bool | None:
    """True when a refspec destination names the default branch, None when it depends on the current branch, else False."""
    if dst in ("", "HEAD", "@"):
        return None
    if "*" in dst or "?" in dst or "[" in dst:   # a pattern such as refs/heads/*: every branch, the default one included
        return True
    for prefix in ("refs/heads/", "heads/"):
        if dst.startswith(prefix):
            dst = dst[len(prefix):]
            break
    return dst in DEFAULT_BRANCHES


def reaches_default_push(call: list[str]) -> bool | None:
    """One recorded stub call (program first). True: a `git push` or `git send-pack` whose destination is main or master (a delete, `--all`, `--mirror` and a
    pattern count). False: not that. None: a push whose destination depends on the current branch or the config (`git push`, `HEAD`), so the throwaway folder
    of the run cannot say. Read from the words only; it shares no code with the classifier."""
    if not call or call[0] != "git":
        return False
    args = call[1:]
    i = 0
    while i < len(args) and args[i].startswith("-"):   # git global options before the subcommand
        i += 2 if args[i] in _GIT_GLOBAL_VALUE else 1
    if i >= len(args) or args[i] not in ("push", "send-pack"):
        return False
    words = args[i + 1:]
    positional: list[str] = []
    all_branches = has_repo = False
    j = 0
    while j < len(words):
        w = words[j]
        if w == "--":
            positional += words[j + 1:]
            break
        if w.startswith("-") and w != "-":
            if w in _ALL_BRANCHES:
                all_branches = True
            elif w == "--repo":
                has_repo = True
            if w in _PUSH_VALUE:
                j += 1
        else:
            positional.append(w)
        j += 1
    if all_branches:
        return True
    refspecs = positional if has_repo else positional[1:]   # the first word is the remote, unless --repo named it
    verdict: bool | None = False
    k = 0
    if not refspecs:
        return False if "--tags" in words else None
    while k < len(refspecs):
        r = refspecs[k]
        if r == "tag":   # `tag NAME` is the refspec refs/tags/NAME
            k += 2
            continue
        r = r.lstrip("+")
        if r == ":":
            return True
        dst = r.rsplit(":", 1)[1] if ":" in r else r
        v = _dest_is_default(dst)
        if v is True:
            return True
        if v is None:
            verdict = None
        k += 1
    return verdict


def entry_reach(results: dict) -> bool | None:
    """One payload over its shells: True when any shell reached a push to the default branch, None when no shell did but one gave no evidence (a push that
    depends on the branch, or a timeout), else False. A timeout is no evidence of safety. A call recorded before a timeout still counts."""
    saw_unknown = False
    for res in results.values():
        calls = [reaches_default_push(c) for c in res.get("calls", [])]
        if True in calls:
            return True
        if None in calls or res.get("timed_out"):
            saw_unknown = True
    return None if saw_unknown else False


# ---- corpus --------------------------------------------------------------------------------------------------------------------------------
def default_files() -> list[Path]:
    return sorted((ROOT / "evals").glob("*/cases.jsonl"))


def default_corpus(files: list[Path] | None = None) -> list[dict]:
    """The SI-08 family, from row metadata: a Bash `gate` row with a command, no `seed`, and `SI-08` in `expect_blocked` or `"oracle": "si08"`.
    One entry per distinct command, with every row id that has it. Sorted by first id."""
    by_cmd: dict[str, list[str]] = {}
    for f in files if files is not None else default_files():
        for n, line in enumerate(Path(f).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            c = json.loads(line)
            cmd = c.get("command")
            if (c.get("kind") or "gate") != "gate" or not isinstance(cmd, str) or not cmd or c.get("seed"):
                continue
            if "SI-08" not in (c.get("expect_blocked") or []) and c.get("oracle") != "si08":
                continue
            rid = c.get("id")
            if not isinstance(rid, str) or not rid:
                raise OracleError(f"{f}:{n}: an oracle row needs an id")
            by_cmd.setdefault(cmd, []).append(rid)
    out = [{"ids": sorted(ids), "cmd": cmd} for cmd, ids in by_cmd.items()]
    return sorted(out, key=lambda e: (e["ids"][0], e["cmd"]))


def corpus_hash(corpus: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sorted(e["cmd"] for e in corpus), ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def add_payloads(corpus: list[dict], path: Path) -> list[dict]:
    """The payloads of an independent review round, added to the corpus. A command that is already there keeps its ids."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    items = data if isinstance(data, list) else [data]
    known = {e["cmd"]: dict(e, ids=list(e["ids"])) for e in corpus}
    for n, x in enumerate(items, 1):
        cmd = x if isinstance(x, str) else (x.get("cmd") or x.get("command")) if isinstance(x, dict) else None
        if not isinstance(cmd, str) or not cmd:
            raise OracleError(f"{path}: entry {n} has no command")
        if cmd in known:
            continue
        rid = x.get("id") if isinstance(x, dict) and isinstance(x.get("id"), str) else f"add-{n:03d}"
        known[cmd] = {"ids": [rid], "cmd": cmd}
    out = [dict(e, ids=sorted(set(e["ids"]))) for e in known.values()]
    return sorted(out, key=lambda e: (e["ids"][0], e["cmd"]))


def known_gap_rows(files: list[Path] | None = None) -> dict[str, str]:
    """command -> row id, for the `known_gap` gate rows of the case files. A bad row (no work item, bad date) is an error, as in run_cases.py."""
    run_cases = _module("run_cases")
    out: dict[str, str] = {}
    for f in files if files is not None else default_files():
        for n, line in enumerate(Path(f).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            c = json.loads(line)
            if not c.get("known_gap") or not isinstance(c.get("command"), str):
                continue
            problems = run_cases._gap_owner(c)
            if problems:
                raise OracleError(f"{f}:{n}: known_gap row {c.get('id')}: {'; '.join(problems)}")
            out.setdefault(c["command"], c["id"])
    return out


# ---- the snapshot file -------------------------------------------------------------------------------------------------------------------------
def _placeholder(s: str, root: str) -> str:
    """The sandbox root and the per-case folder are random: `$HOME` and `$TMPDIR` expand to them. They are replaced so that a rerun gives the same file."""
    s = s.replace(root, "<ROOT>")
    return re.sub(r"<ROOT>/cases/[^/\s'\"]+", "<CASE>", s)


def entries_from_run(raw: list[dict], corpus: list[dict], root: str) -> list[dict]:
    """The runner output (`Sandbox.run_corpus`, in corpus order) as snapshot entries: rc, timed_out and the recorded calls. Output text is dropped."""
    if len(raw) != len(corpus):
        raise OracleError(f"the run gave {len(raw)} results for {len(corpus)} payloads")
    entries = []
    for item, e in zip(raw, corpus, strict=True):
        if item["cmd"] != e["cmd"]:
            raise OracleError("the run results are not in corpus order")
        results = {sh: {"rc": r["rc"], "timed_out": bool(r["timed_out"]), "calls": [[_placeholder(w, root) for w in c] for c in r["calls"]]}
                   for sh, r in item["results"].items()}
        entries.append({"id": e["ids"][0], "ids": list(e["ids"]), "cmd": e["cmd"], "results": results})
    return entries


def build_snapshot(entries: list[dict], shells: dict[str, str], corpus: list[dict]) -> dict:
    """`corpus` is the payload list the run was built from; its hash lets `check` and a reader see what the file was made for."""
    return {"format": FORMAT, "kind": "realshell-oracle", "platform": "darwin", "shells": dict(sorted(shells.items())),
            "corpus": {"hash": corpus_hash(corpus), "n": len(corpus)},
            "entries": sorted(entries, key=lambda e: (e["id"], e["cmd"]))}


def dump_snapshot(snap: dict) -> str:
    return json.dumps(snap, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def snapshot_name(versions: dict[str, str]) -> str:
    parts = []
    for sh in sorted(versions):
        m = re.match(r"\d+(?:\.\d+)*", (versions[sh] or "").strip())
        if not m:
            raise OracleError(f"the shell {sh} gave no version ({versions[sh]!r}); the file name needs it")
        parts.append(f"{sh}{m.group(0)}")
    return "realshell-" + "-".join(parts) + ".json"


def shell_versions(sb, shells, timeout: float = 10.0) -> dict[str, str]:
    """The version, asked of the shell itself inside the sandbox (this tool starts no shell of its own)."""
    out = {}
    for sh in shells:
        if sh not in VERSION_PROBE:
            raise OracleError(f"unknown shell {sh!r}; one of {', '.join(sorted(VERSION_PROBE))}")
        out[sh] = sb.run(VERSION_PROBE[sh], sh, timeout=timeout, case="version")["stdout"].strip()
    return out


def load_snapshot(path: Path) -> dict:
    try:
        snap = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise OracleError(f"cannot read {path}: {e}") from e
    if not isinstance(snap, dict) or snap.get("format") != FORMAT or snap.get("kind") != "realshell-oracle" or not isinstance(snap.get("entries"), list):
        raise OracleError(f"cannot use {path}: not a realshell-oracle snapshot of format {FORMAT}")
    for e in snap["entries"]:
        if not (isinstance(e, dict) and isinstance(e.get("cmd"), str) and isinstance(e.get("ids"), list) and isinstance(e.get("results"), dict)):
            raise OracleError(f"cannot use {path}: a malformed entry")
    return snap


# ---- the classifier side: the real hook, in a child process (never in this one) ----------------------------------------------------------------
def classify_with_hook(cmds: list[str]) -> dict[str, dict]:
    """command -> {action, invariants}: what `hooks.pretool` of this tree does with it at level unset, gate mode enforce, with an empty HOME and a throwaway
    JADE home per call (evals/decision_replay.py). Nothing is executed."""
    import tempfile

    dr, run_cases = _module("decision_replay"), _module("run_cases")
    rows = [{"id": f"c{i}", "event": run_cases.gate_event({"kind": "gate", "command": c})} for i, c in enumerate(cmds)]
    try:
        with tempfile.TemporaryDirectory(prefix="realshell-oracle-") as d:
            arm = dr.run_arm(dr.parse_arm("unset:enforce"), rows, Path(d))
    except dr.ReplayError as e:
        raise OracleError(str(e)) from e
    by_id = {o["id"]: o for o in arm["outcomes"]}
    return {c: {"action": by_id[f"c{i}"]["action"], "invariants": by_id[f"c{i}"]["invariants"]} for i, c in enumerate(cmds)}


# ---- G4 -----------------------------------------------------------------------------------------------------------------------------------
def _gate(name: str, state: str, detail: str) -> dict:
    return {"gate": name, "state": state, "detail": detail}


def _ids(ids: list[str], limit: int = 12) -> str:
    return " ".join(ids[:limit]) + (f" (+{len(ids) - limit} more)" if len(ids) > limit else "")


def _kind(outcome: dict) -> str:
    """How the hook answered a push to main that was not stopped by SI-08."""
    if outcome["action"] == "allow":
        return "allowed"
    if outcome["action"] == "error":
        return "error"
    if outcome["action"] == "ask":
        return "asked"
    return "another invariant"


def check_snapshot(snap: dict, corpus: list[dict], gaps: dict[str, str], classify, ceiling: int | None, max_unjudged: float = UNJUDGED_LIMIT) -> dict:
    """G4 for one snapshot. `corpus` is the current payload list, `gaps` maps a command to its known-gap row, `classify(cmds)` gives command -> outcome.
    A mismatch: the shell reached a push to the default branch and SI-08 is not among the invariants that blocked it.
    A payload with no evidence is never a pass: one that timed out in every shell fails G4 (G4 timeouts), and so does a share of not-judged payloads
    above `max_unjudged` (G4 judged)."""
    entries = snap["entries"]
    have = {e["cmd"] for e in snap["entries"]}
    missing = sorted(i for e in corpus if e["cmd"] not in have for i in e["ids"][:1])
    extra = len(have - {e["cmd"] for e in corpus})
    gates = [_gate("G4 fresh", FAIL, f"{len(missing)} payload(s) of the current corpus are not in this snapshot, so it is stale: {_ids(missing)}; run `snapshot` on the Mac again")
             if missing else _gate("G4 fresh", PASS, f"all {len(corpus)} payloads of the current corpus are in the snapshot" + (f"; {extra} extra" if extra else ""))]
    reach = {e["cmd"]: entry_reach(e["results"]) for e in entries}
    reached = [e for e in entries if reach[e["cmd"]] is True]
    unjudged = [e for e in entries if reach[e["cmd"]] is None]
    split = [e for e in reached if not all(any(reaches_default_push(c) is True for c in r.get("calls", [])) for r in e["results"].values())]
    blind = [e for e in entries if reach[e["cmd"]] is not True and e["results"] and all(r.get("timed_out") for r in e["results"].values())]
    gates.append(_gate("G4 timeouts", FAIL, f"{len(blind)} payload(s) timed out in every shell, so there is no evidence for them: {_ids([e['id'] for e in blind])}; "
                                           f"run `snapshot` again with a longer --timeout (a python3 payload needs about 30 s on macOS)")
                 if blind else _gate("G4 timeouts", PASS, "no payload timed out in every shell"))
    share = len(unjudged) / len(entries) if entries else 0.0
    gates.append(_gate("G4 judged", FAIL, f"{len(unjudged)} of {len(entries)} payloads gave no evidence ({100 * share:.1f} per cent), above the limit of {100 * max_unjudged:.0f} per cent: "
                                         f"{_ids([e['id'] for e in unjudged])}")
                 if share > max_unjudged + 1e-9 else _gate("G4 judged", PASS, f"{len(unjudged)} of {len(entries)} payloads gave no evidence, within the limit of {100 * max_unjudged:.0f} per cent"))
    if not reached:
        gates.append(_gate("G4 reach", NODATA, f"no payload reached a push to main in any shell ({len(entries)} payloads, {len(unjudged)} not judged): nothing to compare"))
        gates.append(_gate("G4 SI-08 mismatch", NODATA, "nothing to compare"))
        gates.append(_gate("G4 known gaps", NODATA, "nothing to compare"))
    else:
        gates.append(_gate("G4 reach", PASS, f"{len(reached)} of {len(entries)} payloads reach a push to main or master in at least one shell; {len(unjudged)} not judged "
                                            f"(no evidence: a push that needs the branch, or a timeout){'; ' + str(len(split)) + ' differ between shells' if split else ''}"))
        outcomes = classify([e["cmd"] for e in reached])
        bad = [e for e in reached if "SI-08" not in outcomes[e["cmd"]]["invariants"]]
        new = [e for e in bad if e["cmd"] not in gaps]
        gap_bad = [e for e in bad if e["cmd"] in gaps]
        stale = [e for e in reached if e["cmd"] in gaps and e not in bad]
        kinds = {k: sum(1 for e in new if _kind(outcomes[e["cmd"]]) == k) for k in ("allowed", "asked", "another invariant", "error")}
        rate = f"new gaps {len(new)} of {len(entries)} payloads ({100 * len(new) / len(entries):.1f} per 100)"
        if new:
            gates.append(_gate("G4 SI-08 mismatch", FAIL, f"{len(new)} payload(s) reach a push to main and SI-08 does not block them ({', '.join(f'{k} {v}' for k, v in kinds.items())}): "
                                                         f"{_ids([e['id'] for e in new])}; {rate}"))
        else:
            gates.append(_gate("G4 SI-08 mismatch", PASS, f"every one of the {len(reached) - len(gap_bad)} pushes to main outside the known gaps is blocked by SI-08; {rate}"))
        if stale:
            gates.append(_gate("G4 known gaps", FAIL, f"{len(stale)} gap(s) are now blocked by SI-08, so the marker must go: {_ids([gaps[e['cmd']] for e in stale])}"))
        elif ceiling is not None and len(gap_bad) > ceiling:
            gates.append(_gate("G4 known gaps", FAIL, f"{len(gap_bad)} known gaps reach a push to main, above the ceiling {ceiling}: the list may not grow"))
        elif gap_bad and ceiling is None:
            gates.append(_gate("G4 known gaps", NODATA, f"{len(gap_bad)} known gaps reach a push to main ({_ids([gaps[e['cmd']] for e in gap_bad])}); no owner-signed "
                                                         f"ceiling was given (--known-gap-ceiling N), so growth cannot be checked"))
        else:
            gates.append(_gate("G4 known gaps", PASS, f"{len(gap_bad)} known gaps reach a push to main" + (f", ceiling {ceiling}" if ceiling is not None else "")))
    code = 1 if any(g["state"] == FAIL for g in gates) else 0
    return {"gates": gates, "exit": code, "shells": snap.get("shells", {}), "n": len(entries), "reached": len(reached), "unjudged": len(unjudged)}


def render(result: dict, title: str = "") -> str:
    shells = ", ".join(f"{k} {v}" for k, v in result.get("shells", {}).items())
    lines = [f"real-shell oracle {title}".rstrip() + (f" (shells: {shells})" if shells else "")]
    lines += [f"  {g['gate']:<20}{g['state']:<17}{g['detail']}" for g in result["gates"]]
    return "\n".join(lines)


def check_dir(d: Path, ceiling: int | None, classify=classify_with_hook, max_unjudged: float = UNJUDGED_LIMIT) -> tuple[int, str]:
    files = sorted(Path(d).glob("realshell-*.json")) if Path(d).is_dir() else []
    if not files:
        return 0, ("real-shell oracle G4\n  G4                  not-enough-data  no snapshot in " + str(d) + ". The owner runs `snapshot` on the Mac before a release; "
                   "until then G4 is not claimed. This is never a pass.")
    corpus, gaps = default_corpus(), known_gap_rows()
    out, code = [], 0
    for f in files:
        snap = load_snapshot(f)
        r = check_snapshot(snap, corpus, gaps, classify, ceiling, max_unjudged)
        out.append(render(r, f.name))
        code = max(code, r["exit"])
    if len(files) > 1:
        out.append(f"  note: {len(files)} snapshots are checked; delete the file of a shell version that is no longer in use")
    return code, "\n".join(out)


# ---- stop-loss ----------------------------------------------------------------------------------------------------------------------------
def load_rounds(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError as e:
            raise OracleError(f"{path}:{n}: not JSON ({e})") from e
        ok = (isinstance(r, dict) and isinstance(r.get("round"), str) and r["round"] and type(r.get("payloads")) is int and type(r.get("new_gaps")) is int
              and isinstance(r.get("independent"), bool) and r["payloads"] >= 1 and 0 <= r["new_gaps"] <= r["payloads"])
        if not ok:
            raise OracleError(f"{path}:{n}: a round needs round (text), payloads (int, 1 or more), new_gaps (int, 0 to payloads) and independent (true or false)")
        rows.append(r)
    return rows


def stoploss(rows: list[dict]) -> str:
    if not rows:
        return "stop-loss: not-enough-data. No review round is recorded. Add one line per independent round to evals/oracle/rounds.jsonl."
    lines = ["stop-loss: new SI-class gaps per independent review round, per 100 payloads"]
    for r in rows:
        lines.append(f"  {r['round']:<12}{r['new_gaps']:>4} new in {r['payloads']:>4} payloads = {100 * r['new_gaps'] / r['payloads']:.1f} per 100"
                     + ("" if r["independent"] else "   (not independent: not counted)"))
    tail = [r for r in rows if r["independent"]][-STOP_AFTER:]
    if len(tail) == STOP_AFTER and all(r["new_gaps"] == 0 for r in tail):
        lines.append(f"stop adding parser waves: the last {STOP_AFTER} independent rounds found none. This is a stopping rule, not a proof.")
    else:
        lines.append(f"keep going: {STOP_AFTER} independent rounds in a row with no new gap are needed.")
    return "\n".join(lines)


# ---- command line -----------------------------------------------------------------------------------------------------------------------
def cmd_snapshot(args) -> int:
    corpus = default_corpus()
    if args.add:
        corpus = add_payloads(corpus, args.add)
    shells = tuple(s for s in args.shells.split(",") if s)
    if args.dry_run:
        print(f"dry run: nothing runs. {len(corpus)} payloads x {len(shells)} shells ({', '.join(shells)}), corpus hash {corpus_hash(corpus)}, "
              f"output folder {args.out}. The file name needs the shell versions, which only a run can read.")
        return 0
    realshell = _module("realshell")
    try:
        with realshell.Sandbox() as sb:
            versions = shell_versions(sb, shells)
            name = snapshot_name(versions)
            raw = sb.run_corpus([e["cmd"] for e in corpus], shells=shells, jobs=args.jobs, timeout=args.timeout)
            entries = entries_from_run(raw, corpus, sb.root)
    except (realshell.SandboxUnavailable, realshell.SandboxBroken) as e:
        print(f"realshell_oracle: {e}", file=sys.stderr)
        return 2
    snap = build_snapshot(entries, versions, corpus)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(dump_snapshot(snap), encoding="utf-8")
    reached = sum(1 for e in entries if entry_reach(e["results"]) is True)
    unjudged = sum(1 for e in entries if entry_reach(e["results"]) is None)
    blind = sum(1 for e in entries if entry_reach(e["results"]) is not True and e["results"] and all(r["timed_out"] for r in e["results"].values()))
    print(f"wrote {out / name}: {len(entries)} payloads, {reached} reach a push to main, {unjudged} not judged. Next: `check`, then commit the file.")
    if blind:
        print(f"warning: {blind} payload(s) timed out in every shell (timeout {args.timeout:g} s), so G4 will fail on them. Run again with a longer --timeout.")
    others = sorted(p.name for p in out.glob("realshell-*.json") if p.name != name)
    if others:
        print("other snapshots here (delete one that is out of date): " + ", ".join(others))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="owner run on macOS: run the corpus in real shells inside the sandbox and write the snapshot")
    s.add_argument("--add", type=Path, help="payloads of an independent review round (JSON list or JSON Lines)")
    s.add_argument("--out", type=Path, default=ORACLE_DIR)
    s.add_argument("--shells", default=",".join(DEFAULT_SHELLS))
    s.add_argument("--jobs", type=int, default=8)
    s.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per payload and shell (default %(default)s: a python3 payload needs about 30 s on macOS)")
    s.add_argument("--dry-run", action="store_true", help="print what would run and stop")
    c = sub.add_parser("check", help="G4: replay the classifier against the committed snapshot files (any platform)")
    c.add_argument("--dir", type=Path, default=ORACLE_DIR)
    c.add_argument("--known-gap-ceiling", type=int, help="the owner-signed count of known gaps that may still reach a push to main")
    c.add_argument("--max-unjudged", type=float, default=UNJUDGED_LIMIT, help="the share of payloads (0 to 1) that may give no evidence (default %(default)s)")
    t = sub.add_parser("stoploss", help="print the new-gap rate of each review round and whether to stop adding parser waves")
    t.add_argument("--file", type=Path, default=ROUNDS_FILE)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "snapshot":
            return cmd_snapshot(args)
        if args.cmd == "stoploss":
            print(stoploss(load_rounds(args.file)))
            return 0
        code, text = check_dir(args.dir, args.known_gap_ceiling, max_unjudged=args.max_unjudged)
        print(text)
        return code
    except OracleError as e:
        print(f"realshell_oracle: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
