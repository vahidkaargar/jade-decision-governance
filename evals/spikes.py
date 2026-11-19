"""V6-24 spikes: three read-only measurements that decide later work (docs/14-v6/06, section 12). They change no state of JADE.

    uv run python evals/spikes.py sp0 --out RAW.json --owner-ok        one live call: what is the raw body of Jev?
    uv run python evals/spikes.py sp1 --out NOISE.json --owner-ok      how far does Jev move for a trivial change? (6 variants x 20 calls)
    uv run python evals/spikes.py sp3 --ledger COPY.sqlite             which cheap checks would have fired on the stored verify calls? (offline)

SP-0 and SP-1 send text to openrouter.ai. The text is a fixed, harmless fixture in this file: no ledger text, no file of the owner, nothing private. They still
cost money and use the owner's key, so they run only with `--owner-ok`, and only the OWNER gives that word. An agent must not run them. They write no ledger row:
the provider is called directly, so a `jade ask` repeat key, a label or a statistic is never touched.

SP-3 reads a COPY of the ledger (copy the file first: `cp ~/.local/share/jade/jade.sqlite /tmp/copy.sqlite`). It opens the copy read-only, refuses the live ledger,
sends nothing, and prints counts and call ids, never the stored text (that text is private). The owner reads every hit by hand before any check may lower a verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jade import adapter, ask  # noqa: E402

FIXTURE_CLAIM = "The unit tests pass."
FIXTURE_EVIDENCE = "$ pytest -q\n..........................................\n42 passed in 1.31s\n"
VERIFY_QID = "q.verify_claim.supported_by_evidence"
ACCEPT_AT = 0.70   # the shipped accept band of the verify family at low risk
LONG_STRING = 40   # a string value this long in the provider body is a candidate for free text


# ---- SP-0 -----------------------------------------------------------------------------------------------
def body_shape(obj, path: str = "$") -> list[dict]:
    """Every key path of a JSON body with its type, and a flag for a long string (a free-text candidate). Values are not copied, except that a number or a short
    string is shown, so a reader sees at once whether the body holds anything but typed answers."""
    rows: list[dict] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            rows += body_shape(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            rows += body_shape(v, f"{path}[{i}]")
        if len(obj) > 5:
            rows.append({"path": f"{path}[5:]", "type": f"{len(obj) - 5} more items"})
    else:
        row = {"path": path, "type": type(obj).__name__}
        if isinstance(obj, str):
            row["length"] = len(obj)
            row["free_text_candidate"] = len(obj) >= LONG_STRING
            if len(obj) < LONG_STRING:
                row["value"] = obj
        elif isinstance(obj, int | float) and not isinstance(obj, bool):
            row["value"] = obj
        rows.append(row)
    return rows


def sp0(provider, out: Path, model: str | None = None) -> dict:
    spec = ask.verify_specs(companion=False)[0]
    res = provider.ask({"claim": FIXTURE_CLAIM, "evidence": FIXTURE_EVIDENCE}, {spec.key: spec.payload}, ask.resolve_model(model))
    out.write_text(json.dumps(res.raw, ensure_ascii=False, indent=2) if res.raw is not None else "null")
    out.chmod(0o600)
    shape = body_shape(res.raw) if res.raw is not None else []
    return {"ok": res.ok, "error_kind": res.error_kind, "resolved_model": res.resolved_model, "raw_saved_to": str(out),
            "fields": len(shape), "free_text_candidates": [r["path"] for r in shape if r.get("free_text_candidate")], "shape": shape}


# ---- SP-1 -----------------------------------------------------------------------------------------------
VARIANTS = {   # same claim and evidence, changed in a way a person would call trivial
    "base": (FIXTURE_CLAIM, FIXTURE_EVIDENCE),
    "trailing_newline": (FIXTURE_CLAIM + "\n", FIXTURE_EVIDENCE),
    "trailing_spaces": (FIXTURE_CLAIM + "  ", FIXTURE_EVIDENCE.replace("\n", "  \n")),
    "crlf": (FIXTURE_CLAIM, FIXTURE_EVIDENCE.replace("\n", "\r\n")),
    "claim_reworded": ("All of the unit tests pass.", FIXTURE_EVIDENCE),
    "evidence_one_more_line": (FIXTURE_CLAIM, FIXTURE_EVIDENCE + "done\n"),
}


def summarise(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "min": min(values), "max": max(values), "mean": round(statistics.fmean(values), 4),
            "stdev": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0, "range": round(max(values) - min(values), 4),
            "share_at_or_above_accept": round(sum(v >= ACCEPT_AT for v in values) / len(values), 3)}


def sp1(provider, n: int = 20, model: str | None = None) -> dict:
    spec = ask.verify_specs(companion=False)[0]
    result: dict = {"calls_per_variant": n, "accept_at": ACCEPT_AT, "variants": {}, "errors": 0, "cost_usd": 0.0}
    for name, (claim, evidence) in VARIANTS.items():
        values: list[float] = []
        for _ in range(n):
            res = provider.ask({"claim": claim, "evidence": evidence}, {spec.key: spec.payload}, ask.resolve_model(model))
            result["cost_usd"] += res.cost_usd or 0.0
            ans = (res.answers or {}).get(spec.key) if res.ok else None
            if isinstance(ans, dict) and isinstance(ans.get("noul"), int | float):
                values.append(float(ans["noul"]))
            else:
                result["errors"] += 1
        result["variants"][name] = summarise(values)
    result["cost_usd"] = round(result["cost_usd"], 6)
    return result


# ---- SP-3 -----------------------------------------------------------------------------------------------
CAP_MARK = "[truncated by jade: state cap 32 KiB]"
FAILURE_MARKERS = ("Traceback", "FAILED", "error:", "npm ERR!", "panic:")
SHORT_EVIDENCE = 200
_TOKEN = re.compile(r"`([^`\n]{2,80})`|(\b[0-9a-f]{7,40}\b)|((?:[\w.-]+/)+[\w.-]+)|(\b\d[\d,._]*\b)")


def claim_tokens(claim: str) -> list[str]:
    """Numbers, hex ids, paths and backticked names of a claim (C5). Language-free: a claim in any script that names 412 and `tests/x` needs both in its evidence."""
    out: list[str] = []
    for m in _TOKEN.finditer(claim or ""):
        tok = next(g for g in m.groups() if g)
        if tok not in out:
            out.append(tok)
    return out


def checks(claim: str, evidence: str, state_text: str = "") -> list[str]:
    """The names of the cheap checks that fire on one (claim, evidence). C1 short evidence, C5 a token of the claim missing from the evidence, C6 a failure marker in
    the evidence, C9 the state was cut at the cap. Evidence lines that only repeat the claim do not count for C5."""
    hits: list[str] = []
    if len((evidence or "").strip()) < SHORT_EVIDENCE:
        hits.append("C1")
    own = "\n".join(line for line in (evidence or "").splitlines() if (claim or "").strip() not in line)
    if any(tok not in own for tok in claim_tokens(claim)):
        hits.append("C5")
    if any(m in (evidence or "") for m in FAILURE_MARKERS):
        hits.append("C6")
    if CAP_MARK in (state_text or "") or CAP_MARK in (evidence or ""):
        hits.append("C9")
    return hits


def live_ledger_paths() -> set[Path]:
    home = Path(os.environ.get("JADE_HOME") or Path.home() / ".local" / "share" / "jade")
    return {(home / "jade.sqlite").resolve(), home.resolve()}


def sp3(ledger_copy: Path) -> dict:
    p = ledger_copy.expanduser().resolve()
    if p in live_ledger_paths():
        raise SystemExit("sp3 reads a COPY of the ledger, never the live file. Copy it first and pass the copy.")
    if not p.is_file():
        raise SystemExit(f"sp3: {p} is not a file")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)   # read-only: nothing can be written, migrated or locked
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT call_id, exit_code, p, state_text FROM jev_calls WHERE question_id=? AND dedup_of IS NULL AND state_text IS NOT NULL", (VERIFY_QID,)).fetchall()
    con.close()
    per_check: dict[str, dict[str, int]] = {}
    hits: list[dict] = []
    unreadable = 0
    for r in rows:
        try:
            st = json.loads(r["state_text"])
            claim, evidence = str(st.get("claim", "")), str(st.get("evidence", ""))
        except (ValueError, AttributeError):
            unreadable += 1
            continue
        fired = checks(claim, evidence, r["state_text"])
        for c in fired:
            slot = per_check.setdefault(c, {"hits": 0, "on_accepted": 0, "on_abstained": 0, "on_rejected": 0})
            slot["hits"] += 1
            if r["exit_code"] in (0, 3, 4):
                slot[{0: "on_accepted", 3: "on_abstained", 4: "on_rejected"}[r["exit_code"]]] += 1
        if fired:
            hits.append({"call_id": r["call_id"], "exit_code": r["exit_code"], "p": r["p"], "checks": fired})
    return {"verify_calls_read": len(rows), "unreadable": unreadable, "per_check": per_check, "calls_with_a_hit": len(hits),
            "hits": hits, "note": "the stored text is not printed; the owner reads each hit in the ledger copy before any check may lower a verdict"}


# ---- command line -----------------------------------------------------------------------------------------
def main(argv: list[str] | None = None, provider=None) -> int:
    ap = argparse.ArgumentParser(prog="evals/spikes.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sp0", "sp1"):
        sp = sub.add_parser(name)
        sp.add_argument("--out", required=True, type=Path)
        sp.add_argument("--owner-ok", action="store_true", help="the OWNER's word: this sends a fixed fixture text to openrouter.ai and spends the owner's key")
        sp.add_argument("--model", default=None)
    sub.choices["sp1"].add_argument("--n", type=int, default=20)
    s3 = sub.add_parser("sp3")
    s3.add_argument("--ledger", required=True, type=Path, help="a COPY of the ledger file")
    a = ap.parse_args(argv)
    if a.cmd == "sp3":
        print(json.dumps(sp3(a.ledger), indent=1))
        return 0
    if not a.owner_ok:
        print(f"{a.cmd}: refused. This run sends a fixed fixture text to openrouter.ai with the owner's key; only the owner gives that word (--owner-ok). Nothing was sent.", file=sys.stderr)
        return 2
    provider = provider or adapter.OpenRouterProvider()
    if a.cmd == "sp0":
        out = sp0(provider, a.out, a.model)
        print(json.dumps({k: v for k, v in out.items() if k != "shape"}, indent=1))
        return 0 if out["ok"] else 1
    out = sp1(provider, a.n, a.model)
    a.out.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0 if not out["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
