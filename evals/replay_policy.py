"""Policy replay: apply the proposed JADE thresholds (policies/escalation.yaml) to recorded Jev
calls and report what the runtime *would* have done. Input: a dataset produced by
evals/baseline/mine_transcripts.py (or, later, a ledger export). No network. Usage:

    uv run python evals/replay_policy.py evals/baseline/dataset-2026-11-08.jsonl
"""
import collections
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ESC = yaml.safe_load((ROOT / "policies" / "escalation.yaml").read_text())
CODES = ESC["exit_codes"]


def classify_noul(p: float, family: str = "verify_claim", risk: str = "low") -> str:
    fam = ESC["families"][family]
    accept = fam["noul"]["accept"]
    reject = fam["noul"]["reject"]
    if risk in fam.get("by_risk", {}):
        accept = fam["by_risk"][risk]["accept"]
    if p >= accept:
        return "accept"
    if p <= reject:
        return "reject"
    return "abstain"


def classify_choice_conf(conf: float, family: str = "strategy_choice") -> str:
    # The mined dataset has only `confidence`; top-p is recoverable only with the option count,
    # which the transcript does not carry. Treat confidence as a conservative proxy and flag it.
    thr = ESC["families"][family]["choice"]["accept_top_p"]
    return "accept" if conf >= thr else "abstain"


def replay(rows):
    out = collections.Counter()
    per_session_abstain = collections.defaultdict(int)
    repeats = collections.Counter()
    for r in rows:
        if r.get("dry_run") or r.get("mention"):   # a mention is text that names jev, not a call (W-18)
            continue
        key = (r["session"], r["primitive"], r.get("question", ""))
        repeats[key] += 1
        if "p" in r:
            v = classify_noul(float(r["p"]))
            out[f"noul_{v}"] += 1
            if v != "accept":
                per_session_abstain[r["session"]] += 1
                if r.get("next_text_done_claim"):
                    out["would_have_flagged_done_claim"] += 1
        elif "choice" in r:
            name, conf = r["choice"]
            v = classify_choice_conf(float(conf))
            out[f"choice_{v}"] += 1
        elif "error" in r:
            out["provider_or_parse_error_exit5_or_6"] += 1
    out["repeat_calls_dedup_would_return_cached"] = sum(v - 1 for v in repeats.values() if v > 1)
    out["sessions_with_at_least_one_non_accept"] = len(per_session_abstain)
    return out


def main(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    res = replay(rows)
    total = sum(v for k, v in res.items() if k.startswith(("noul_", "choice_", "provider")))
    print(json.dumps({"input": str(path), "calls_considered": total, "exit_codes": CODES,
                      "thresholds": {"verify_claim": ESC["families"]["verify_claim"]["noul"],
                                     "choice_proxy_accept_conf": ESC["families"]["strategy_choice"]["choice"]["accept_top_p"]},
                      "result": dict(sorted(res.items()))}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "evals/baseline/dataset-2026-11-08.jsonl")
