"""Replay the regression and adversarial case files through the real jade code paths (fixture provider,
isolated home, no network). Exit non-zero on any mismatch.

    uv run python evals/run_cases.py [evals/regression/cases.jsonl evals/adversarial/cases.jsonl]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = [ROOT / "evals" / "regression" / "cases.jsonl", ROOT / "evals" / "adversarial" / "cases.jsonl"]


def run_case(c: dict) -> list[str]:
    from jade import adapter, ask, hooks, ledger, questions

    problems: list[str] = []
    kind = c["kind"]
    if kind in ("verify", "repeat", "secret"):
        p = c.get("p", 0.9)
        prov = adapter.FixtureProvider(lambda s, q, m, p=p: {k: {"type": "noul", "noul": p} for k in q})
        claim = c.get("claim", "tests pass")
        secret = "".join(c.get("secret_parts", []))
        evidence = c.get("evidence") or (f"log line with key {secret} inside" if kind == "secret" else "pytest: 42 passed in 3.1s")
        specs = lambda: ask.verify_specs(companion=False)  # noqa: E731
        r = ask.run(specs(), {"claim": claim, "evidence": evidence}, provider=prov, session="case-session", risk=c.get("risk", "low"))
        if kind == "repeat":
            ask.run(specs(), {"claim": claim, "evidence": evidence}, provider=prov, session="case-session", risk=c.get("risk", "low"))
            if len(prov.calls) != c["expect_provider_calls"]:
                problems.append(f"provider called {len(prov.calls)} times, expected {c['expect_provider_calls']}")
        if "expect_exit" in c and r.exit_code != c["expect_exit"]:
            problems.append(f"exit {r.exit_code}, expected {c['expect_exit']}")
        if "expect_permits" in c and ledger.open().one("SELECT COUNT(*) AS n FROM permits").n != c["expect_permits"]:
            problems.append("a permit exists that should not")
        if kind == "secret" and secret in json.dumps(prov.calls):
            problems.append("secret reached the provider payload")
        if "expect_state_under_bytes" in c and len(json.dumps(prov.calls[0]["state"]).encode()) >= c["expect_state_under_bytes"]:
            problems.append("state not capped")
        if "authorized" in "\n".join(ask.explain(r)).lower():
            problems.append("guidance text contains the word authorized")
    elif kind == "choice":
        try:
            questions.build_choice(None, c["options"], "Which?")
            problems.append("degenerate choice was accepted")
        except questions.InvalidQuestion:
            pass
    elif kind == "provider_error":
        res = adapter.Result(ok=False, error_kind=c["error_kind"], http_status=c.get("http_status"))
        r = ask.run(ask.verify_specs(companion=False), {"claim": "c", "evidence": "e"}, provider=adapter.FixtureProvider(lambda s, q, m: res), session="case-session")
        if r.exit_code != c["expect_exit"]:
            problems.append(f"exit {r.exit_code}, expected {c['expect_exit']}")
    elif kind in ("gate", "gate_write", "gate_mcp"):
        ev = {"tool_name": "Bash", "tool_input": {"command": c.get("command", "")}, "session_id": "case-session", "cwd": "/tmp/case"}
        if kind == "gate_mcp":
            ev = {"tool_name": c["tool"], "tool_input": c["input"], "session_id": "case-session", "cwd": "/tmp/case"}
        if kind == "gate_write":
            tin = {c.get("key", "file_path"): c["path"]}
            if c.get("content_parts"):  # file content assembled from parts so that no case file holds a token-shaped literal
                tin["content"] = "".join(c["content_parts"])
            ev = {"tool_name": c.get("tool", "Write"), "tool_input": tin, "session_id": "case-session", "cwd": "/tmp/case"}
        if kind == "gate_mcp" and c.get("content_parts"):
            ev["tool_input"] = {**ev["tool_input"], "content": "".join(c["content_parts"])}
        ev.update(c.get("event", {}))  # extra host fields: permission_mode, agent_id
        res = hooks.pretool(ev)
        want = 2 if c["expect"] == "block" else 0
        if res.exit_code != want:
            problems.append(f"gate exit {res.exit_code}, expected {want} ({res.stderr[:80]!r})")
        for inv in c.get("expect_blocked", []):
            if inv not in res.stderr:
                problems.append(f"expected invariant {inv} in the block message")
    else:
        problems.append(f"unknown case kind {kind}")
    return problems


def main(files: list[str]) -> int:
    failures = total = 0
    for f in files or DEFAULT:
        for line in Path(f).read_text().splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            with tempfile.TemporaryDirectory() as d:
                os.environ.update(JADE_HOME=d, JADE_CONFIG=d, JADE_GATE_MODE="enforce")
                for k in ("CLAUDECODE", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT"):
                    os.environ.pop(k, None)
                from jade import policy, questions, thresholds

                for fn in (policy.reset_cache, questions.reset_cache, thresholds.escalation.cache_clear):
                    fn()
                problems = run_case(c)
            total += 1
            failures += bool(problems)
            print(f"{'FAIL' if problems else 'ok  '}  {c['id']} {c['class']}" + ("".join(f"\n      {p}" for p in problems)))
    print(f"{total - failures}/{total} cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
