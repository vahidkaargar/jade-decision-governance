"""Live latency/throughput harness for the Decisions API (OpenRouter adapter path).
Measures single-question latency at parallelism 1/4/8 and batch sizes 1/3/8, reports p50/p95/max,
error counts and total cost. Small by default (about 50 calls, well under $0.01). Never sends
secrets: the state is a fixed synthetic fixture. Usage:

    uv run python evals/performance/run.py [--rounds 3] [--out evals/performance/result.json]
"""
import argparse
import concurrent.futures as cf
import getpass
import json
import statistics
import subprocess
import time
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.14"
STATE = {"claim": "The test suite passed", "evidence": "pytest: 42 passed in 3.1s", "evidence_source": "tool_output"}
Q = {"type": "noul", "instructions": "Is `claim` directly supported by `evidence`?",
     "criteria": {"true": "The evidence directly shows the claim is correct.", "false": "The evidence is missing, insufficient, or contradicts the claim."}}


def key():
    out = subprocess.run(["security", "find-generic-password", "-s", "OPENROUTER_API_KEY", "-a", getpass.getuser(), "-w"],
                         capture_output=True, text=True)
    k = out.stdout.strip()
    if not k:
        raise SystemExit("no OPENROUTER_API_KEY in Keychain")
    return k


def call(k, n_questions):
    payload = {"model": MODEL, "state": STATE, "questions": {f"q{i}": Q for i in range(n_questions)}}
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r)
        return {"ok": True, "ms": (time.perf_counter() - t) * 1000, "cost": d.get("usage", {}).get("cost", 0.0),
                "model": d.get("model"), "tokens": d.get("usage", {}).get("input_tokens")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "ms": (time.perf_counter() - t) * 1000, "status": e.code, "cost": 0.0}
    except Exception as e:  # noqa: BLE001 - harness reports, never hides
        return {"ok": False, "ms": (time.perf_counter() - t) * 1000, "error": type(e).__name__, "cost": 0.0}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def summarize(label, results):
    ok = [r["ms"] for r in results if r["ok"]]
    return {"config": label, "n": len(results), "errors": sum(1 for r in results if not r["ok"]),
            "p50_ms": round(pct(ok, 50)) if ok else None, "p95_ms": round(pct(ok, 95)) if ok else None, "p99_ms": round(pct(ok, 99)) if ok else None,
            "max_ms": round(max(ok)) if ok else None, "mean_ms": round(statistics.mean(ok)) if ok else None,
            "cost_usd": round(sum(r["cost"] for r in results), 6),
            "statuses": sorted({r.get("status") for r in results if not r["ok"] and r.get("status")})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", default="evals/performance/result.json")
    a = ap.parse_args()
    k = key()
    report = {"model_requested": MODEL, "runs": []}
    for par in (1, 4, 8):
        res = []
        for _ in range(a.rounds):
            with cf.ThreadPoolExecutor(max_workers=par) as ex:
                res += list(ex.map(lambda _: call(k, 1), range(par)))
        report["runs"].append(summarize(f"single_noul_parallel_{par}", res))
    for batch in (1, 3, 8):
        res = [call(k, batch) for _ in range(a.rounds + 1)]
        report["runs"].append(summarize(f"batch_{batch}_serial", res))
    report["resolved_model"] = next((r.get("model") for r in [call(k, 1)] if r.get("model")), None)
    report["total_cost_usd"] = round(sum(r["cost_usd"] for r in report["runs"]), 6)
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
