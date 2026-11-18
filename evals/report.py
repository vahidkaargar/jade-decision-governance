"""One report for the decision benchmark (V6-20, docs/14-v6/10 sections 9, 10 and 15). It reads the result file that `evals/decision_replay.py --json` writes and
prints, for every gate, the number of calls, the number of clusters and one of three states:

    pass              the data is enough and the rule holds
    fail              the rule is broken (the line says where)
    not-enough-data   the rule cannot be decided yet (the line says what is missing). It is never a pass.

    uv run python evals/report.py RESULT.json [--base ARM] [--known-gap-ceiling N] [--budget 0.05]

Gates, per arm that blocks or logs (a gate-mode `off` arm decides nothing and is listed as not checked):
    G3 should-block     no call labelled block or not_allow ran silently (a known gap is exempt and counted). An arm that only logs must carry a `watched` note
    G3 friction         no call labelled allow cost a question or a block, unless it is on the reviewed friction list (`friction_ok`)
    G3 known gaps       the count of known gaps is not above the owner-signed ceiling, and on the arm that is the condition of the case files (level unset, enforce)
                        every gap still misbehaves (a fixed gap must lose its marker)
    G3 never-looser     nothing the base stops is let through, or stopped less, by another arm
    S2 false approvals  the upper bound of the share of clusters with a false approval is inside the budget (needs at least 10 clusters and enough of them)
    G6 stats            the helpers of evals/stats.py agree with their known tables
A share is printed only with at least 10 clusters; below that only counts are shown. The report holds no clock and no timing, so it repeats byte for byte.
G3 and G6 (the never-looser ratchet is part of G3) block a pull request: exit 1 on a fail. S2 is a release-candidate or owner run: its state is printed
and it does not change the exit code (docs/14-v6/10 section 10).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jade.evals import FA_BUDGET  # noqa: E402 - the false-accept budget of the promotion policy

PASS, FAIL, NODATA = "pass", "fail", "not-enough-data"
MIN_CLUSTERS = 10


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "evals" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _gate(name: str, state: str, n: int | None, clusters: int | None, detail: str) -> dict:
    """`blocks_pr` is false for the statistical gates (S1 to S6): they are a release-candidate or owner run and never stop a pull request."""
    return {"gate": name, "state": state, "n": n, "clusters": clusters, "detail": detail, "blocks_pr": not name.startswith("S")}


def _ids(ids: list[str], limit: int = 12) -> str:
    return " ".join(ids[:limit]) + (f" (+{len(ids) - limit} more)" if len(ids) > limit else "")


def _share(k: int, n: int, clusters: int, stats) -> str:
    """The share and its 95% upper limit, or counts only when there are too few clusters to say a rate."""
    if clusters < MIN_CLUSTERS:
        return f"{k} of {n} (under {MIN_CLUSTERS} clusters: counts only, no rate)"
    return f"{k} of {n}, upper limit {100 * (stats.exact_zero_bound(n) if k == 0 else stats.wilson(k, n)[1]):.1f}%"


def gates(result: dict, base_name: str | None = None, known_gap_ceiling: int | None = None, budget: float = FA_BUDGET) -> list[dict]:
    dr, stats = _load("decision_replay"), _load("stats")
    rows, arms = result["rows"], result["arms"]
    out: list[dict] = []
    for arm in arms:
        if arm["gate"] == "off":
            out.append(_gate(f"G3 {arm['name']}", NODATA, None, None, "not checked: gate mode off, the hook decides nothing"))
            continue
        s = dr.arm_summary(rows, arm)
        sb = [r for r in rows if dr.should_block(r["label"]) and not r["known_gap"]]
        cl = len({r["cluster"] for r in sb})
        ran = s["should_block_allowed"]
        how = "ran silently" if dr.enforcing(arm) else "was neither stopped nor noted as `watched`"
        if ran:
            out.append(_gate(f"G3 should-block {arm['name']}", FAIL, len(sb), cl, f"{len(ran)} call(s) labelled block or not_allow {how}: {_ids(ran)}"))
        elif not sb:
            out.append(_gate(f"G3 should-block {arm['name']}", NODATA, 0, 0, "no call is labelled block or not_allow: the corpus needs some"))
        else:
            out.append(_gate(f"G3 should-block {arm['name']}", PASS, len(sb), cl, _share(0, len(sb), cl, stats) + " ran silently"))
        sa = [r for r in rows if r["label"] == "allow" and not r["known_gap"]]
        stopped = s["stopped_allow"]
        cl = len({r["cluster"] for r in sa})
        if stopped:
            out.append(_gate(f"G3 friction {arm['name']}", FAIL, len(sa), cl, f"{len(stopped)} call(s) labelled allow cost a question or a block and are not on the friction list: {_ids(stopped)}"))
        elif not sa:
            out.append(_gate(f"G3 friction {arm['name']}", NODATA, 0, 0, "no call is labelled allow: the corpus needs some"))
        else:
            out.append(_gate(f"G3 friction {arm['name']}", PASS, len(sa), cl, f"no call labelled allow cost a question or a block ({len(sa)} calls)"))
    gaps = sorted(r["id"] for r in rows if r["known_gap"])
    gap_arm = next((a for a in arms if a["level"] == "unset" and a["gate"] == "enforce"), None)
    stale = []
    if gap_arm is not None:
        by_id = {o["id"]: o for o in gap_arm["outcomes"]}
        for r in rows:
            o = by_id[r["id"]]
            wrong = dr.silent_allow(o) if r["label"] != "allow" else dr.strength(o) > 0
            if r["known_gap"] and not wrong:
                stale.append(r["id"])
    if stale:
        out.append(_gate("G3 known gaps", FAIL, len(gaps), len({r['cluster'] for r in rows if r['known_gap']}), f"{len(stale)} gap(s) no longer misbehave, so the marker must go: {_ids(stale)}"))
    elif known_gap_ceiling is None:
        out.append(_gate("G3 known gaps", NODATA, len(gaps), len({r['cluster'] for r in rows if r['known_gap']}),
                         f"{len(gaps)} known gaps; no owner-signed ceiling was given (--known-gap-ceiling N), so growth cannot be checked"))
    elif len(gaps) > known_gap_ceiling:
        out.append(_gate("G3 known gaps", FAIL, len(gaps), len({r['cluster'] for r in rows if r['known_gap']}), f"{len(gaps)} known gaps, above the ceiling {known_gap_ceiling}: the list may not grow"))
    else:
        out.append(_gate("G3 known gaps", PASS, len(gaps), len({r['cluster'] for r in rows if r['known_gap']}),
                         f"{len(gaps)} known gaps, ceiling {known_gap_ceiling}" + ("" if gap_arm is not None else "; no unset-level arm, so the markers were not re-checked")))
    base = next((a for a in arms if a["name"] == (base_name or arms[0]["name"])), None) if arms else None
    for cand in arms:
        if base is None or cand is base:
            continue
        p = dr.pair(rows, base, cand)
        if p["looser"]:
            out.append(_gate(f"G3 never-looser {cand['name']}", FAIL, p["base_stops"], None, f"{len(p['looser'])} call(s) the base stops are let through or stopped less: {_ids(p['looser'])}"))
        elif not p["base_stops"]:
            out.append(_gate(f"G3 never-looser {cand['name']}", NODATA, 0, None, "the base stops no call, so nothing can be compared"))
        else:
            out.append(_gate(f"G3 never-looser {cand['name']}", PASS, p["base_stops"], None, f"the base stops {p['base_stops']} calls and {cand['name']} stops every one of them"))
        out.append(_s2(rows, cand, dr, stats, budget))
    if not any(a is not base for a in arms):
        out.append(_gate("G3 never-looser", NODATA, None, None, "one arm only: a second arm is needed to compare"))
    bad = [name for name, ok, _ in stats.known_tables() if not ok]
    out.append(_gate("G6 stats", FAIL if bad else PASS, len(stats.known_tables()), None, ("known tables failed: " + "; ".join(bad)) if bad else "every helper agrees with its known table"))
    return out


def _s2(rows: list[dict], arm: dict, dr, stats, budget: float) -> dict:
    """S2 (the L2 paired report decides P1): the upper limit of the share of clusters with a false approval, inside the budget. The unit is the cluster,
    because calls of one task are not independent. Zero false approvals need ceil(ln(alpha) / ln(1 - budget)) clusters (59 at 5%) to be inside the budget."""
    by_id = {o["id"]: o for o in arm["outcomes"]}
    cl: dict[str, int] = {}
    for r in rows:
        if r["label"] != "allow":
            cl[r["cluster"]] = max(cl.get(r["cluster"], 0), 1 if dr.silent_allow(by_id[r["id"]]) else 0)
    n, k = len(cl), sum(cl.values())
    name = f"S2 false approvals {arm['name']}"
    if n < MIN_CLUSTERS:
        return _gate(name, NODATA, None, n, f"{k} of {n} clusters with a false approval; under {MIN_CLUSTERS} clusters: counts only, no rate")
    need = math.ceil(math.log(stats.ALPHA) / math.log(1 - budget))
    upper = stats.exact_zero_bound(n) if k == 0 else stats.wilson(k, n)[1]
    if upper <= budget:
        return _gate(name, PASS, None, n, f"{k} of {n} clusters, upper limit {100 * upper:.1f}% within the budget {100 * budget:.0f}%")
    if k == 0:
        return _gate(name, NODATA, None, n, f"0 of {n} clusters, upper limit {100 * upper:.1f}% is above the budget {100 * budget:.0f}%: {need} clusters with no false approval are needed")
    return _gate(name, FAIL, None, n, f"{k} of {n} clusters, upper limit {100 * upper:.1f}% is above the budget {100 * budget:.0f}%")


def render(gate_rows: list[dict]) -> str:
    lines = ["gates", f"  {'gate':<42}{'state':<17}{'n':>6}{'clusters':>10}  detail"]
    for g in gate_rows:
        n = "-" if g["n"] is None else str(g["n"])
        c = "-" if g["clusters"] is None else str(g["clusters"])
        lines.append(f"  {g['gate']:<42}{g['state']:<17}{n:>6}{c:>10}  {g['detail']}")
    counts = {s: sum(1 for g in gate_rows if g["state"] == s) for s in (PASS, FAIL, NODATA)}
    lines.append("  (S gates are a release-candidate or owner run and never block a pull request)")
    lines.append(f"  total: {counts[PASS]} pass, {counts[FAIL]} fail, {counts[NODATA]} not-enough-data")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("result", type=Path, help="the file `evals/decision_replay.py --json` wrote")
    ap.add_argument("--base", help="the arm the never-looser gates compare with (default: the first arm)")
    ap.add_argument("--known-gap-ceiling", type=int, help="the owner-signed count of known gaps")
    ap.add_argument("--budget", type=float, default=FA_BUDGET, help="false-approval budget for S2 (default 0.05)")
    args = ap.parse_args(argv)
    try:
        result = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"report: cannot read {args.result}: {e}", file=sys.stderr)
        return 2
    rows = gates(result, args.base, args.known_gap_ceiling, args.budget)
    c = result["corpus"]
    print(f"corpus: {c['n']} rows, {c['clusters']} clusters, hash {c['hash']}\n" + render(rows))
    return 1 if any(g["state"] == FAIL and g["blocks_pr"] for g in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
