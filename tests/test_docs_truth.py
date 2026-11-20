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
DOCS = ["README.md", "PRIVACY.md", "CONTRIBUTING.md", "SECURITY.md", "BACKLOG.md", "skills/jade/SKILL.md", "docs/claude-md-jev-section.md",
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
            for w in set(re.findall(r"\bW-\d{2}\b", p.read_text())) - {f"W-{n}" for n in range(96, 100)}:   # W-96..W-99: historic feature ids, no rows (tests/test_ids_unique.py)
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


# ---- V6-11: a number or a state a document states is the one the code has ------------------------------------------------------
NUMBER = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
COUNTED_DOCS = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "skills/jade/SKILL.md", "docs/08-deployment-runbook.md", "docs/09-install-and-upgrade.md",
                "docs/12-jev-decides-phase1.md"]   # not the roadmap (docs/06), which states what each phase planned at the time


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def _numbers_before(pattern: str, rel: str):
    """(number, matched text) for each `<number word or digits> <pattern>` in a document."""
    for m in re.finditer(rf"\b(\d+|{'|'.join(NUMBER)})(?:[- ])(?:installed )?(?:{pattern})", _read(rel), re.I):
        word = m.group(1).lower()
        yield (int(word) if word.isdigit() else NUMBER[word]), m.group(0)


def test_the_hook_count_a_document_states_is_len_ops_hooks():
    from jade import ops

    checked = 0
    for rel in COUNTED_DOCS:
        for n, text in _numbers_before(r"hooks\b|hook entries", rel):
            checked += 1
            assert n == len(ops.HOOKS), f"{rel}: '{text}' but `jade hooks install` writes {len(ops.HOOKS)} ({', '.join(ops.HOOKS)})"
    assert checked >= 3, "the pattern found too few statements: it no longer reads the documents"


def test_the_counts_the_readme_states_are_the_counts_of_the_files():
    from jade import questions

    text = _read("README.md")
    schemas = [p for p in (ROOT / "schemas").glob("*.schema.json") if not p.name.startswith("_")]
    for pattern, got, what in ((r"JSON Schema contracts", len(schemas), "schemas/*.schema.json"), (r"policy files", len(list((ROOT / "policies").glob("*.yaml"))), "policies/*.yaml"),
                               (r"entry question registry", len(questions.entries()), "questions in the registry")):
        found = [(n, t) for n, t in _numbers_before(pattern, "README.md")]
        assert found, f"README no longer states a count for {pattern}"
        for n, t in found:
            assert n == got, f"README says '{t}' but {what} has {got}"
    assert text.count("-entry question registry") == 1


def test_the_readme_says_which_harness_and_platform_are_supported_and_claims_no_other():
    text = _read("README.md")
    assert re.search(r"Claude Code is the only (?:agent )?harness (?:JADE )?(?:is )?wired", text), "README must say Claude Code is the only wired harness"
    assert "macOS is the tested platform" in text
    for rel in ("README.md", "docs/00-inventory-and-gaps.md"):
        assert "harness-neutral" not in _read(rel), f"{rel} calls the CLI gate harness-neutral; no other harness is wired (V6-11, CT-06)"


def test_the_readme_does_not_state_what_only_the_live_machine_can_say():
    """A README is read by someone else's machine. Whether the maintenance job is loaded, the ruleset is on or the version is current is what `jade check` reads."""
    text = _read("README.md")
    for claim in ("maintenance job is loaded", "branch ruleset protect", "four hooks are installed", "reference machine: advisory from"):
        assert claim not in text, f"README states '{claim}': say what `jade check` shows instead"
    assert "jade check" in text.split("## Status", 1)[1].split("## Why", 1)[0]


def test_approval_times_are_stated_with_both_ways_to_approve():
    for rel in ("README.md", "docs/08-deployment-runbook.md"):
        text = _read(rel)
        assert "30 minutes" in text and "10 minutes" in text, f"{rel} must say the signed permit lasts 30 minutes and a chat or dialog answer 10 (docs/12)"


def test_docs_12_memory_never_covers_what_the_code_never_remembers():
    """The section 9 table and the section 10 owner page say the same as `memory.eligibility`: critical calls, R-21, R-22, R-13 and R-18 are asked every time."""
    from jade import memory

    doc = _read("docs/12-jev-decides-phase1.md")
    sec9 = doc.split("## 9.", 1)[1].split("## 10.", 1)[0]
    sec10 = doc.split("## 10.", 1)[1].split("## 11.", 1)[0]
    never_row = next(line for line in sec9.splitlines() if line.startswith("|") and "never: asked every time" in line)
    for rule in sorted(memory.NEVER_RULES):
        assert rule in never_row, f"docs/12 section 9 does not list {rule} as never remembered"
    assert "critical" in never_row.lower(), "docs/12 section 9 must list a critical call as never remembered"
    asked = re.search(r"Four classes are asked[^\n]*", sec10).group(0)
    assert not re.search(r"\bmerges\b[^.]*\bare asked", asked), "a merge is remembered as that exact call (section 9); section 10 must not say it is always asked"
    assert "critical" in asked.lower() and "R-13" in asked and "R-22" in asked, "section 10 names the calls that are asked every time, by rule"


def test_waiting_dependency_is_reserved_in_the_docs_while_no_edge_enters_it():
    from jade import state_machine

    entered = [(src, ev) for (src, ev), dst in state_machine.T.items() if dst == "WAITING_DEPENDENCY"]
    text = _read("docs/02-contracts-and-state-machine.md")
    if entered:
        assert "reserved, not entered" not in text.split("Resumable side states", 1)[1].split("\n", 1)[0], f"an edge enters WAITING_DEPENDENCY now: {entered}"
    else:
        line = text.split("Resumable side states", 1)[1].split("\n", 1)[0]
        assert "WAITING_DEPENDENCY" in line and "reserved, not entered" in line, "no edge enters WAITING_DEPENDENCY: docs/02 must say it is reserved, not entered"


def test_the_threat_model_does_not_say_the_failure_hook_is_missing():
    """PostToolUseFailure is registered (ops.HOOKS) and closes a failed call as an observed failure; T20 and W-85 once said it was not."""
    from jade import ops

    assert "PostToolUseFailure" in ops.HOOKS
    t20 = next(line for line in _read("docs/05-threat-model.md").splitlines() if line.startswith("| T20 | Permit"))
    assert "which JADE does not register" not in t20 and "until a human re-runs `jade hooks install` the installed PostToolUse matcher is still" not in t20
    items = {json.loads(line)["id"]: json.loads(line) for line in _read("work-items.jsonl").splitlines() if line.strip()}
    assert items["W-85"]["status"] == "done", "W-85 asked for the failure event to be registered; it is (hooks.posttool_failure, W-100d)"
    assert items["W-83"]["status"] == "done", "W-83: the unread escalation.yaml keys are deleted and tests/test_escalation_policy_keys.py keeps them out"


def test_a_document_that_says_the_question_shows_a_recommended_default_is_not_true_of_the_code():
    for rel in ("docs/03-routing-and-question-design.md", "docs/07-self-improvement-and-work-management.md"):
        for line in _read(rel).splitlines():
            if "recommended default" in line.lower() and "consequence of no answer" in line.lower():
                assert "not shown" in line.lower() or "does not show" in line.lower(), f"{rel}: promises a recommended default and the cost of no answer; the question shows neither: {line[:100]}"


def test_no_document_promises_a_recommended_default_or_an_abstention_budget_the_code_does_not_have():
    """The question shows no recommended default, and no code tracks an abstention budget (T14 said both). Each mention must be a denial (\"no\", \"not\" ... the phrase)."""
    root = Path(__file__).resolve().parent.parent
    denial = re.compile(r"\b(no|not|never|without|neither|nor|none)\b[^.;|]{0,60}(recommended default|abstention budget|abstain budget)", re.I)
    for rel in ("README.md", *(f"docs/{p.name}" for p in sorted((root / "docs").glob("0*.md")))):
        for line in _read(rel).splitlines():
            for m in re.finditer(r"recommended default|abstention budget|abstain budget", line, re.I):
                head = line[max(0, m.start() - 70): m.end()]
                assert denial.search(head) or re.search(r"(does not|not) show a recommended default", line, re.I), f"{rel}: promises {m.group(0)!r}, which the code does not have: {line[:120]}"


def test_the_ask_text_purge_waits_in_the_backlog_for_the_owner():
    """`jade ledger purge --ask-text` is built and off (V6-11). The owner's decision must be an open row, in both files, or nothing will ask for it."""
    rows = [json.loads(line) for line in _read("work-items.jsonl").splitlines() if line.strip()]
    mine = [w for w in rows if "--ask-text" in w["title"] + w["next_action"]]
    assert mine, "no work item names the --ask-text purge: the owner's decision is only in the CHANGELOG"
    assert all(w["owner"] == "human" and w["status"] == "open" for w in mine)
    assert any("DX-H2" in w["evidence_refs"] and "docs/05 T15" in w["evidence_refs"] for w in mine)
    backlog = _read("BACKLOG.md")
    for w in mine:
        assert re.search(rf"^\| {w['id']} \|", backlog, re.M), f"{w['id']} missing from BACKLOG.md"


def test_docs_02_terminal_states_are_the_ones_the_state_machine_calls_terminal():
    """W-166 (V6-11): docs/02 listed FAILED and OUTCOME_UNKNOWN as terminal, but both have exits in `state_machine.T`."""
    from jade import state_machine

    line = _read("docs/02-contracts-and-state-machine.md").split("Resumable side states", 1)[1].split("\n", 1)[0]
    listed = line.split("Terminal", 1)[1].split(". ", 1)[0]
    named = set(re.findall(r"`([A-Z_]+)`", listed))
    assert named == set(state_machine.TERMINAL), f"docs/02 terminal list {sorted(named)} != state_machine.TERMINAL {sorted(state_machine.TERMINAL)}"
    for state in ("FAILED", "OUTCOME_UNKNOWN"):
        assert any(src == state for (src, _ev) in state_machine.T), f"{state} has no exit in T: it would be terminal"
        assert state not in state_machine.TERMINAL


# ---- W-171 (d11 checklist 4): the README names no fact that is true of one machine on one day ---------------------------------
ISO_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
LIVE_STATE = (r"\bsince 20\d\d-\d\d-\d\d", r"\bearliest 20\d\d-\d\d-\d\d", r"\b(?:has|have|had|is|are) (?:been |now )?loaded\b", r"\bnot yet (?:been )?run\b",
              r"\bon the author's machine\b", r"\bin the author's machine\b", r"\bcurrently\b", r"\bright now\b", r"\bas of (?:today|now)\b")
# The Status table rows that may hold a date, and which dates: a decision of the maintainer is a fact of the project, not of a machine.
STATUS_DATES_ALLOWED = {"DESIGN_READY": {"2026-11-09"}}


def _readme_status_rows() -> dict[str, str]:
    section = _read("README.md").split("## Status", 1)[1].split("\n## ", 1)[0]
    return {m.group(1): m.group(2) for m in re.finditer(r"^\| ([A-Z_]+(?: \([a-z]+\))?) \| (.+) \|$", section, re.M)}


def test_the_readme_names_no_live_state_word_or_machine_fact():
    """The README is read on someone else's machine. A date of enforce, a loaded job or a second install is a fact of one machine on one day: `jade check` says it."""
    text = _read("README.md")
    for pattern in LIVE_STATE:
        m = re.search(pattern, text, re.I)
        assert not m, f"README says {m.group(0)!r}, a fact of one machine on one day: say what `jade check` shows instead (W-171)"


def test_the_readme_status_table_holds_a_date_only_where_a_row_is_allowed_one():
    rows = _readme_status_rows()
    assert set(rows) >= {"DESIGN_READY", "IMPLEMENTED", "LIVE (enforce)", "VALIDATED", "PRODUCTION_APPROVED"}, f"the Status table changed its rows: {sorted(rows)}"
    for stage, state in rows.items():
        extra = set(ISO_DATE.findall(state)) - STATUS_DATES_ALLOWED.get(stage, set())
        assert not extra, f"README Status row {stage} holds the date {sorted(extra)}: only {STATUS_DATES_ALLOWED} may (W-171)"
    assert "jade check" in rows["LIVE (enforce)"], "the LIVE row must send the reader to `jade check`"


def test_the_readme_architecture_decision_count_is_the_count_of_adrs():
    adrs = len(re.findall(r"^## ADR-\d+", _read("docs/01-adrs.md"), re.M))
    found = [(n, t) for n, t in _numbers_before(r"architecture decisions", "README.md")]
    assert found, "README no longer states a count of architecture decisions"
    for n, t in found:
        assert n == adrs, f"README says '{t}' but docs/01-adrs.md has {adrs} ADRs (W-171)"


@pytest.mark.parametrize("line", ["| LIVE (enforce) | Run on the author's machine, since 2026-11-13.", "the daily job has been loaded on this Mac", "earliest 2026-11-27 (14 days)",
                                  "The installer has not yet been run on a second machine", "it is currently on"])
def test_the_live_state_patterns_catch_what_the_readme_used_to_say(line):
    assert any(re.search(p, line, re.I) for p in LIVE_STATE)
