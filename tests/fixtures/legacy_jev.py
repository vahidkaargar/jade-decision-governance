#!/usr/bin/env python3
"""jev - ask TypeSafe Jev (via OpenRouter Decisions API) a typed question.

Subcommands:
  verify  CLAIM            yes/no probability that CLAIM holds given the state
  noul    QUESTION  -t T -f F   yes/no probability with custom criteria
  choice  QUESTION  -o name=desc ...   pick one option
  score   QUESTION  -c level ...       position on an ordered scale

State (the evidence Jev reasons over) comes from -s TEXT, or -s - for stdin.
Key: macOS Keychain service OPENROUTER_API_KEY (account $USER), else env
OPENROUTER_API_KEY. The key is never printed. State text is sent to
openrouter.ai, so never include secrets.
"""
import argparse, getpass, json, os, subprocess, sys, urllib.error, urllib.request

URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.14")


def get_key():
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "OPENROUTER_API_KEY",
             "-a", getpass.getuser(), "-w"],
            capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def read_state(arg):
    if arg == "-":
        return sys.stdin.read()
    return arg or ""


def build_question(a):
    if a.cmd == "verify":
        return {"type": "noul", "instructions": f"Is this claim correct and supported by the state? {a.text}",
                "criteria": {"true": "The claim is correct and supported by the evidence in the state.",
                             "false": "The claim is wrong, unsupported, or contradicted by the state."}}
    if a.cmd == "noul":
        return {"type": "noul", "instructions": a.text,
                "criteria": {"true": a.true, "false": a.false}}
    if a.cmd == "choice":
        crit = {}
        for o in a.option:
            name, sep, desc = o.partition("=")
            if not sep or not name.strip():
                sys.exit(f"jev: option must be name=description, got: {o!r}")
            crit[name.strip()] = desc.strip()
        if len(crit) < 2:
            sys.exit("jev: choice needs at least two -o options")
        return {"type": "choice", "instructions": a.text, "criteria": crit}
    if a.cmd == "score":
        if len(a.level) < 2:
            sys.exit("jev: score needs at least two -c levels, lowest first")
        return {"type": "score", "instructions": a.text, "criteria": a.level}


def render(ans):
    t = ans.get("type")
    if t == "noul":
        return f"P(yes)={ans['noul']:.2f}"
    if t == "choice":
        probs = " ".join(f"{k}={v:.2f}" for k, v in
                         sorted(ans.get("probabilities", {}).items(), key=lambda kv: -kv[1]))
        return f"choice={ans['choice']} confidence={ans.get('confidence', 0):.2f} | {probs}"
    if t == "score":
        legend = ans.get("legend", {})
        near = legend.get(str(round(ans["score"])), "")
        return f"score={ans['score']:.2f} confidence={ans.get('confidence', 0):.2f} (~ {near})"
    return json.dumps(ans, ensure_ascii=False)


def main():
    p = argparse.ArgumentParser(prog="jev", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--raw", action="store_true", help="print full JSON response")
    p.add_argument("--dry-run", action="store_true", help="print request payload, send nothing")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("verify", "noul", "choice", "score"):
        sp = sub.add_parser(name)
        sp.add_argument("text", help="claim or question")
        sp.add_argument("-s", "--state", default="", help="evidence/context text, or - for stdin")
    sub.choices["noul"].add_argument("-t", "--true", required=True, help="when answer is yes")
    sub.choices["noul"].add_argument("-f", "--false", required=True, help="when answer is no")
    sub.choices["choice"].add_argument("-o", "--option", action="append", default=[], help="name=description")
    sub.choices["score"].add_argument("-c", "--level", action="append", default=[], help="ordered level, lowest first")
    a = p.parse_args()

    payload = {"model": a.model, "state": {"context": read_state(a.state)},
               "questions": {"q": build_question(a)}}
    if a.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    key = get_key()
    if not key:
        sys.exit("jev: no key. Run: security add-generic-password -s OPENROUTER_API_KEY -a \"$USER\" -w")
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"jev: HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")
    except (urllib.error.URLError, TimeoutError) as e:
        sys.exit(f"jev: request failed: {e}")
    if a.raw:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    ans = data.get("answers", {}).get("q")
    if ans is None:
        sys.exit(f"jev: unexpected response: {json.dumps(data, ensure_ascii=False)[:400]}")
    cost = data.get("usage", {}).get("cost")
    print(render(ans) + (f"  [cost ${cost:.6f}]" if cost is not None else ""))


if __name__ == "__main__":
    main()
