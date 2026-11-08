"""Live probe of the OpenRouter Decisions API used for the compatibility matrix (docs/00, S1-S12).

Checks: batching of three primitives in one request, which model ids resolve, single-call latency,
and how the server treats a degenerate one-option Choice. About 9 calls, well under $0.001.
The key is read from the macOS Keychain (service OPENROUTER_API_KEY) or the environment and is
never printed. The state is a fixed synthetic fixture.

    uv run python adapters/jev/probe_openrouter.py
"""

import getpass
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/alpha/decisions"
STATE = {"claim": "The test suite passed", "evidence": "pytest: 42 passed in 3.1s"}
QUESTIONS = {
    "supported": {"type": "noul", "instructions": "Is `claim` supported by `evidence`?"},
    "risk": {"type": "choice", "instructions": "Risk of acting on this evidence",
             "criteria": {"low": "routine, reversible", "high": "irreversible or production"}},
    "quality": {"type": "score", "instructions": "Evidence quality", "criteria": ["none", "weak", "direct test output"]},
}


def get_key() -> str:
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", "OPENROUTER_API_KEY", "-a", getpass.getuser(), "-w"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("no OPENROUTER_API_KEY in Keychain or environment")
    return key


def call(key: str, payload: dict):
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body, code = json.load(resp), resp.status
    except urllib.error.HTTPError as exc:
        body, code = exc.read().decode(errors="replace")[:300], exc.code
    return code, round(time.time() - start, 2), body


def main() -> None:
    key = get_key()
    single = {"q": QUESTIONS["supported"]}
    code, lat, body = call(key, {"model": "typesafe/jev-1.14", "state": STATE, "questions": QUESTIONS})
    print("BATCH3", code, lat, json.dumps(body)[:600])
    for model in ("typesafe/jev-latest", "typesafe/jev-1.14.0", "typesafe/jev-1.13"):
        code, lat, body = call(key, {"model": model, "state": STATE, "questions": single})
        print("MODEL", model, code, lat, json.dumps(body)[:200])
    lats = [call(key, {"model": "typesafe/jev-1.14", "state": STATE, "questions": single})[1] for _ in range(4)]
    print("LAT single noul x4:", lats)
    one_option = {"q": {"type": "choice", "instructions": "x", "criteria": {"only": "one"}}}
    code, lat, body = call(key, {"model": "typesafe/jev-1.14", "state": STATE, "questions": one_option})
    print("ONE_OPTION_CHOICE", code, lat, json.dumps(body)[:300])


if __name__ == "__main__":
    main()
