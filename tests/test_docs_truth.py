"""Docs truth (review F46): every `jade ...` command a document, skill, example or work item tells the reader to run
must exist in the command tree. A fragment (a command with arguments left out) passes; an unknown command, an unknown
flag, a value of the wrong type or a choice that does not exist fails."""
import argparse
import contextlib
import io
import json
import re
import shlex
from pathlib import Path

import pytest

from jade import cli

ROOT = Path(__file__).resolve().parents[1]
YAML = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "policies").rglob("*.yaml"))   # their header comments name commands too
DOCS = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "BACKLOG.md", "skills/jade/SKILL.md", "docs/claude-md-jev-section.md",
        *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("0*.md"))]
STOP = ("|", "...", "\u2192", "/", "then", "and", "does")
FRAGMENT = ("the following arguments are required", "expected one argument", "expected at least one argument")


def _spans(text: str):
    """Inline code spans and the lines of fenced blocks."""
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            yield line.strip()
            yield from re.findall(r"`([^`\n]+)`", line)   # a command quoted inline in a line of a diagram (`...; from FAILED, `jade decide event ...``)
        else:
            yield from re.findall(r"`([^`\n]+)`", line)


def _commands(span: str):
    """Yield the argv (without the program) of each `jade ...` command in a span."""
    span = span.replace("\\|", "|")
    for part in re.split(r"\s*(?:&&|\|\||;)\s*", span):
        part = re.sub(r"^\$ ", "", part.strip())
        part = re.sub(r"^(?:uv run(?: --quiet)? )", "", part)
        if not re.match(r"jade( |$)", part):
            continue
        part = part.split(" # ")[0].split(" > ")[0]
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        argv = []
        for w in words[1:]:
            if w in STOP or w[:1] in "({[":
                break
            last = w[-1:] in ",:" and len(w) > 1   # prose after the command: `jade check: probes ...`
            w = w.rstrip(",:")
            w = re.sub(r"<[^>]*>", "x", w)
            if "|" in w and not w.startswith("-"):   # an alternation such as yes|no: check the first spelling
                w = w.split("|")[0]
            argv.append(w)
            if last:
                break
        if argv and not any(re.match(r"\$[A-Za-z_{]", w) for w in argv):   # `jade permit $ap` is a bypass spelling, not a command
            yield argv


def _options(parser) -> set[str]:
    return {o for a in parser._actions for o in a.option_strings}


def _unknown_flag(parser, argv: list[str]) -> str | None:
    """The first `--flag` in argv that neither the parser nor the subcommand it reaches defines (before any subcommand: any subcommand's flag
    counts, as in `jade ask --state-file`). argparse reports a missing argument before an unknown flag, so a fragment such as `jade ask --compat`
    would otherwise pass."""
    known, flags = _options(parser), []
    for tok in argv:
        sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        if tok.startswith("-") and tok != "-":
            flags.append(tok.split("=")[0])
        elif sub is not None and tok in sub.choices:
            parser = sub.choices[tok]
            known |= _options(parser)
    sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if sub is not None:   # no subcommand reached at this level: a flag of any of them is a fragment of a real command
        for sp in sub.choices.values():
            known |= _options(sp)
    for flag in flags:
        if flag not in known and not (flag.startswith("--") and any(k.startswith(flag) for k in known)):
            return flag
    return None


def _check(argv: list[str]) -> str | None:
    if not argv or argv[0] in ("hook", "--version"):
        return None
    err = io.StringIO()
    parser = cli.ask_parser("jade ask") if argv[0] == "ask" else cli.build_parser()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        try:
            parser.parse_args(argv[1:] if argv[0] == "ask" else argv)
            return None
        except SystemExit:
            pass
    msg = err.getvalue().strip().splitlines()[-1] if err.getvalue().strip() else "?"
    if any(f in msg for f in FRAGMENT):
        bad = _unknown_flag(parser, argv[1:] if argv[0] == "ask" else argv)
        return f"unrecognized flag {bad}" if bad else None
    return msg


def _cases():
    seen = set()
    for rel in [*DOCS, *YAML]:
        p = ROOT / rel
        if p.exists():
            for span in _spans(p.read_text()):
                for argv in _commands(span):
                    if (rel, tuple(argv)) not in seen:
                        seen.add((rel, tuple(argv)))
                        yield pytest.param(rel, argv, id=f"{rel}::jade {' '.join(argv)}")
    for line in (ROOT / "work-items.jsonl").read_text().splitlines():
        w = json.loads(line)
        for field in ("next_action", "title", "rationale", "impact"):
            for span in _spans(w.get(field, "")) if "`" in w.get(field, "") else [w.get(field, "")]:
                for argv in _commands(span):
                    if (w["id"], tuple(argv)) not in seen:
                        seen.add((w["id"], tuple(argv)))
                        yield pytest.param(w["id"], argv, id=f"{w['id']}::jade {' '.join(argv)}")


@pytest.mark.parametrize("where,argv", list(_cases()))
def test_documented_command_exists(where, argv):
    assert _check(argv) is None, f"{where}: `jade {' '.join(argv)}` is not a command the CLI accepts: {_check(argv)}"


def test_extractor_finds_the_commands_it_should():
    got = [a for rel in ("README.md", "docs/08-deployment-runbook.md") for s in _spans((ROOT / rel).read_text()) for a in _commands(s)]
    assert ["gate", "advisory"] in got and ["hooks", "install"] in got and len(got) >= 10


@pytest.mark.parametrize("argv", [["permit", "issue", "--action", "x"], ["outcome", "record"], ["decide", "answer"], ["eval", "replay", "x"],
                                  ["ledger", "stats", "--since", "2026-01-01"], ["ledger", "purge", "--older-than", "soon"],
                                  ["ask", "--compat"], ["ask", "verify", "--compat"], ["decide", "classify"], ["permit", "--nonsense"]])
def test_checker_rejects_what_does_not_exist(argv):
    assert _check(argv) is not None


@pytest.mark.parametrize("argv", [["permit", "approve"], ["gate", "advisory"], ["ledger", "stats", "--days", "14"], ["ask", "verify", "claim"],
                                  ["ledger", "purge", "--older-than", "90d"], ["ledger", "purge", "--older-than", "90"],
                                  ["ask", "--state-file"], ["ask", "verify", "--no-companion"], ["decide", "open", "--command"]])
def test_checker_accepts_what_exists(argv):
    assert _check(argv) is None


# ---- docs/05: every threat row has evidence that exists (review G07) ---------------------------------------------------------
def _threat_rows_and_evidence():
    text = (ROOT / "docs/05-threat-model.md").read_text()
    head, _, evidence = text.partition("## 5. Evidence per threat row")
    rows = re.findall(r"^\| (T\d+) \|", head, re.M)
    table = {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(r"^\| (T\d+) \| ([^|]+) \| (.+) \|$", evidence, re.M)}
    return rows, table


def test_every_threat_row_has_evidence_that_exists():
    rows, table = _threat_rows_and_evidence()
    assert len(rows) >= 22 and set(rows) == set(table), f"rows without evidence or evidence without a row: {set(rows) ^ set(table)}"
    cases = {json.loads(line)["id"] for f in ("regression", "adversarial") for line in (ROOT / "evals" / f / "cases.jsonl").read_text().splitlines() if line.strip()}
    work_ids = {json.loads(line)["id"] for line in (ROOT / "work-items.jsonl").read_text().splitlines() if line.strip()}
    for row, (standing, cited) in table.items():
        assert re.match(r"(tested|accepted)\b", standing), (row, standing)
        for w in re.findall(r"W-\d+", standing):
            assert w in work_ids, f"{row}: {w} is not a work item"
        if standing.startswith("accepted"):
            assert re.search(r"W-\d+", standing), f"{row}: an accepted risk names its work item"
        ids = [t.strip() for t in re.sub(r"`[^`]*`", "", cited).split(",") if t.strip()]
        tests = re.findall(r"`([^`]+)`", cited)
        assert ids or tests, f"{row}: no evidence cited"
        for i in ids:
            assert i in cases, f"{row}: case {i} does not exist"
        for t in tests:
            file, _, name = t.partition("::")
            path = ROOT / file
            assert path.exists() and re.search(rf"^def {re.escape(name)}\b", path.read_text(), re.M), f"{row}: {t} does not exist"


def test_the_work_items_named_in_the_docs_exist():
    work_ids = {json.loads(line)["id"] for line in (ROOT / "work-items.jsonl").read_text().splitlines() if line.strip()}
    for rel in DOCS + ["policies/routing.yaml", "policies/escalation.yaml", "policies/promotion.yaml"]:
        p = ROOT / rel
        if p.exists() and rel != "BACKLOG.md":
            for w in set(re.findall(r"\bW-\d{2}\b", p.read_text())):
                assert w in work_ids, f"{rel} names {w}, which is not in work-items.jsonl"


def test_the_version_is_one_number_everywhere():
    import tomllib

    import jade

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert jade.__version__ == version
    lock = (ROOT / "uv.lock").read_text()
    assert re.search(rf'name = "jade-decision-governance"\nversion = "{re.escape(version)}"', lock)
    top = re.search(r"^## \[(\d[^\]]*)\]", (ROOT / "CHANGELOG.md").read_text(), re.M).group(1)
    assert top == version, "the newest CHANGELOG section is the released version"
    assert f"`jade` {version}" in (ROOT / "README.md").read_text()


def test_inline_commands_in_a_fenced_diagram_line_are_extracted():
    text = "```\njade decide outcome <id> ...  \u2192 VERIFYING; from FAILED, `jade decide event <id> compensate|rollback`\n```\n"
    cmds = [a for span in _spans(text) for a in _commands(span)]
    assert ["decide", "event", "x", "compensate"] in cmds
    assert _check(["decide", "event", "x", "compensate"]) is None
    assert any(_check(a) for a in [["decide", "answer"]])   # the same line naming a command that does not exist would fail the docs test


def test_policy_yaml_headers_are_scanned_for_commands():
    assert "policies/risk.yaml" in YAML
    assert any(c.values[0].startswith("policies/") for c in _cases()) or not any(_commands(sp) for rel in YAML for sp in _spans((ROOT / rel).read_text()))
