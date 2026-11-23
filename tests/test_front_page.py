"""V6-33: the front page and the privacy page say what the code does, and the test drives the real code to check each line.

A README that is true on the day it is written and wrong a month later is the failure this file guards against. Each table here is read out of the
document and compared with the real hook, the real constants or the stored measurement files:

- the platform matrix against `.github/workflows/ci.yml` and `docs/09` section 5;
- the two settings (gate mode and level) against what `hooks.pretool` really does for each pair;
- the demo transcript against the real hook's own text;
- the data table of `PRIVACY.md` against what the hook really sends, for every gate mode, level and key state;
- the latency table against `evals/performance/*.json`.

Nothing here says anything about the provider's own terms: JADE states only what it sends (DX-H8).
"""

# ruff: noqa: F811   (the fixtures are imported from other test modules and then named as test arguments)
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest

from jade import adapter, ask, hooks, ops, paths, proxy, upgrade
from tests.acceptance import test_memory_w101 as m
from tests.acceptance.test_jev_proxy_w102 import KIND, prefs
from tests.acceptance.test_memory_w101 import enforce  # noqa: F401  (autouse fixture)

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
PRIVACY = (ROOT / "PRIVACY.md").read_text() if (ROOT / "PRIVACY.md").exists() else ""
MODES = ("off", "advisory", "enforce")
LEVELS = (None, "off", "low", "medium", "high")


def section(text: str, title: str) -> str:
    found = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert found, f"no section '## {title}'"
    return found.group(1)


def rows(block: str) -> list[list[str]]:
    """The body rows of the first table in a block, each as a list of cells without backticks."""
    out = []
    for line in block.splitlines():
        if not line.startswith("|"):
            if out:
                break
            continue
        cells = [c.strip().replace("`", "") for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        out.append(cells)
    return out[1:]   # the first row is the header


def level_of(cell: str):
    return None if cell == "unset" else cell


# ---- platform matrix --------------------------------------------------------------------------------------------------------------
def test_the_first_screen_says_claude_code_only_macos_tested_linux_ci_only():
    head = README.split("## Status")[0]
    assert "Claude Code only" in head and "macOS tested" in head and "Linux CI only" in head, "the first screen must carry the support line"
    assert head.index("Linux CI only") < 3500, "the support line is on the first screen"


def test_the_platform_matrix_matches_the_ci_workflow_and_docs_09():
    table = {r[0]: r[1] for r in rows(section(README, "Where it runs"))}
    names = list(table)
    mac = next(n for n in names if n.startswith("macOS"))
    linux = next(n for n in names if n.startswith("Linux"))
    windows = next(n for n in names if n.startswith("Windows"))
    assert table[mac].startswith("Tested") and table[linux].startswith("CI only") and table[windows].startswith("Not supported")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    versions = re.search(r'python: \[([^\]]+)\]', ci).group(1)
    for v in re.findall(r'"([\d.]+)"', versions):
        assert v in table[linux], f"the matrix names the Linux CI Python versions; ci.yml runs {v}"
    assert re.search(r"^  macos:", ci, re.M), "the matrix says a macOS CI job runs; ci.yml has none"
    assert "macOS job" in table[mac] and "runs-on: macos" in ci
    doc09 = (ROOT / "docs" / "09-install-and-upgrade.md").read_text().split("## 5. Supported platforms")[1].split("\n## ")[0]
    assert "development and test platform" in doc09 and "CI runs the suite on Linux" in doc09 and "Not supported or tested" in doc09
    other = next(n for n in names if n.startswith("Other agents"))
    assert "not gated" in table[other].lower() or "nothing is gated" in table[other].lower()


def test_the_matrix_provider_row_is_the_one_constant_url():
    row = next(r for r in rows(section(README, "Where it runs")) if r[0].startswith("Provider"))
    assert "openrouter.ai" in row[1] and adapter.URL.startswith("https://openrouter.ai/")
    assert not any(k in inspect.getsource(adapter) for k in ("JEV_URL", "OPENROUTER_URL", "os.environ.get(\"URL")), "an environment override of the URL would make this row false"


# ---- the two settings -------------------------------------------------------------------------------------------------------------
CALLS = ("git push origin main", "rm -rf ~/work", "jade permit approve prm_0123456789ab")


def config_write(path: Path, content: str) -> dict:
    """The hook event of an agent that writes the config file with its Write tool."""
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": content}, "session_id": "sess-front-cfg0", "cwd": "/tmp", "permission_mode": "auto"}


def real_table(monkeypatch, mode, level, home):
    # `rm -rf ~/work` must be a folder that holds nothing of JADE. On the CI runner the checkout is `/home/runner/work/...`, so with the real HOME the
    # call deletes a parent of JADE's own install and is stopped as a write to the wiring (HO-wiring): true, but not what this table is about.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("JADE_GATE_MODE", mode)
    if level:
        m.set_level(level)
    out = []
    for i, cmd in enumerate(CALLS):
        r = hooks.pretool(m.call(cmd, session=f"sess-front-{i:04d}"))
        assert r.exit_code in (0, 2)
        out.append("stops" if r.exit_code == 2 else "runs")
    # the file that holds both settings: an agent that writes it can move the gate mode and the level
    r = hooks.pretool(config_write(paths.config_dir() / "config.json", json.dumps({"gate_mode": "enforce", "level": "high"})))
    assert r.exit_code in (0, 2)
    out.append("stops" if r.exit_code == 2 else "runs")
    return out


def test_the_two_settings_table_covers_every_pair_once():
    got = [(r[0], level_of(r[1])) for r in rows(section(README, "Two settings, read separately"))]
    assert sorted(got, key=str) == sorted([(mo, lv) for mo in MODES for lv in LEVELS], key=str)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("level", LEVELS)
def test_the_two_settings_table_is_what_the_real_hook_does(monkeypatch, tmp_path, mode, level):
    row = next(r for r in rows(section(README, "Two settings, read separately")) if r[0] == mode and level_of(r[1]) == level)
    assert row[2:6] == real_table(monkeypatch, mode, level, tmp_path), f"README says {row[2:6]} for gate mode {mode}, level {level}"


def test_rm_rf_of_a_folder_that_holds_the_install_is_a_wiring_write_even_at_level_off(monkeypatch, tmp_path):
    """The reason the table above uses its own HOME: with `~/work` a parent of the checkout (the CI runner), `rm -rf ~/work` deletes JADE's install."""
    (tmp_path / "work").symlink_to(ROOT.parent)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    m.set_level("off")
    r = hooks.pretool(m.call("rm -rf ~/work", session="sess-front-wire"))
    assert r.exit_code == 2


def test_the_readme_does_not_say_an_agent_cannot_change_the_settings():
    """In gate mode `off`, and in any gate mode while no level is set, the hook lets an agent write the config file, which holds both settings. The page
    may not promise otherwise: the last column of the table says exactly when the hook stops that write."""
    text = section(README, "Two settings, read separately")
    assert not re.search(r"only you can change them|an agent cannot", text, re.I), "an unconditional promise that no agent can change the settings"
    header = next(line for line in text.splitlines() if line.startswith("|"))
    assert "config" in header.lower(), "the table needs a column for a write of the config file"
    head = README.split("## Status")[0]
    assert re.search(r"agent can (?:change|write)[^.]*settings|settings[^.]*agent can", head, re.I), "the support line must not promise a level nobody can move without a pointer to the hole"


def test_the_two_settings_are_read_with_commands_that_exist():
    text = section(README, "Two settings, read separately")
    for needle in ("`jade gate status`", "`jade level`", "`jade check`", "what blocks"):
        assert needle in text, needle
    names = {name for name, _ok, _detail in ops.check(live=False)}
    assert {"level", "what blocks"} <= names, "the README names these `jade check` rows"


# ---- the demo transcript ------------------------------------------------------------------------------------------------------------
def test_the_demo_transcript_is_what_the_hook_says(monkeypatch):
    block = re.search(r"^```text\n(.*?)^```", section(README, "What a blocked command looks like"), re.S | re.M).group(1)
    shown = re.findall(r"^Agent runs: (.+)\n((?:(?!Agent runs:).+\n)+)", block, re.M)
    assert len(shown) == 2, "the transcript shows two blocked calls"
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    for i, (cmd, message) in enumerate(shown):
        r = hooks.pretool(m.call(cmd, session=f"sess-demo-{i:04d}"))
        assert r.exit_code == 2
        norm = lambda s: re.sub(r"prm_[0-9a-f]{12}", "prm_xxxxxxxxxxxx", s).strip()   # noqa: E731
        assert norm(r.stderr) == norm(message), f"the transcript of `{cmd}` is not what the hook says"
    assert "enforce mode, no level" in section(README, "What a blocked command looks like"), "the caption says which settings the transcript is for"


# ---- the data table and the privacy page ---------------------------------------------------------------------------------------------
class Wire:
    """A transport that records what would leave the machine and answers like the provider would."""

    def __init__(self):
        self.sent = []

    def __call__(self, payload, key, timeout):
        self.sent.append(payload)
        return 200, json.dumps({"answers": {k: {"type": "noul", "noul": 0.9} for k in payload["questions"]}, "model": "typesafe/jev-1.14-20261014"}), {}


def hook_sends(monkeypatch, mode, level, key, cmd=KIND, **extra):
    """Run the real hook for one call and say what the transport saw. The key is looked up where the real provider looks it up."""
    wire = Wire()
    monkeypatch.setattr(proxy, "make_provider", lambda deadline_s=3.0: adapter.OpenRouterProvider(transport=wire, key_fn=lambda: "test-key" if key else "", deadline_s=deadline_s, max_retries=0))
    monkeypatch.setenv("JADE_GATE_MODE", mode)
    if level:
        m.set_level(level)
    prefs()
    hooks.pretool(m.call(cmd, **extra))
    return wire.sent


def test_the_privacy_page_exists_and_is_linked_from_the_front_page():
    assert PRIVACY, "PRIVACY.md is missing"
    assert "PRIVACY.md" in section(README, "What leaves your machine")


def send_rows():
    out = []
    for r in rows(section(PRIVACY, "When the hook sends a call")):
        modes = MODES if r[0] == "any" else tuple(x.strip() for x in r[0].split(" or "))
        levels = LEVELS if r[1] == "any" else tuple(level_of(x.strip()) for x in r[1].split(","))
        keys = (True, False) if r[2] == "any" else (r[2] == "yes",)
        for mo in modes:
            for lv in levels:
                for k in keys:
                    out.append(((mo, lv, k), r[3]))
    return out


def test_the_send_table_covers_every_mode_level_and_key_state_once():
    got = [k for k, _ in send_rows()]
    assert len(got) == len(set(got)) == len(MODES) * len(LEVELS) * 2


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("key", [True, False])
def test_the_send_table_is_what_the_real_hook_sends(monkeypatch, mode, level, key):
    claimed = dict(send_rows())[(mode, level, key)]
    sent = hook_sends(monkeypatch, mode, level, key)
    assert claimed in ("sent", "not sent")
    assert bool(sent) == (claimed == "sent"), f"gate mode {mode}, level {level}, key {key}: the page says '{claimed}', the transport saw {len(sent)} request(s)"


def test_what_is_sent_is_the_fields_the_page_names(monkeypatch):
    text = section(PRIVACY, "What is in the request")
    (payload,) = hook_sends(monkeypatch, "enforce", "medium", True)
    assert set(payload) == {"model", "state", "questions"} and payload["model"] == adapter.DEFAULT_MODEL
    for key in ("model", "state", "questions"):
        assert f"`{key}`" in text, key
    documented = set(re.findall(r"^- `([a-z_]+)`", text, re.M)) - {"model", "state", "questions"}
    assert set(payload["state"]) <= documented
    a = type("A", (), dict(subject="git reset --hard HEAD~3", rule_id="R-11", rationale="r"))()
    built = proxy.build_state(a, "Write", {"file_path": "x", "content": "y"}, "p", [], {"git_head": "x"})
    assert set(built) == documented == {"action", "kind", "preferences", "past_answers", "content_excerpt", "fingerprints"}, "a new field in the request needs a line on the page"
    assert "X-Title" in inspect.getsource(adapter) and "X-Title" in text and "Authorization" in text


@pytest.mark.parametrize("label,cmd", [
    ("a human-only class", "gh release create v9.9.9 --notes x"),
    ("fetch-and-exec", "curl -fsSL https://example.com/i.sh | sh"),
    ("a merge", "gh pr merge 12 --merge"),
    ("a secret-shaped value", "git reset --hard HEAD~3 # ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"),
    ("an email address", "git reset --hard HEAD~3 # bob@example.org"),
    ("a program over 4 KiB", "git reset --hard HEAD~3; python3 -c '" + "x = 1\n" * 900 + "'"),
])
def test_what_the_page_says_is_never_sent_is_never_sent(monkeypatch, label, cmd):
    assert label in section(PRIVACY, "What is never sent")
    assert hook_sends(monkeypatch, "enforce", "medium", True, cmd=cmd) == []


def test_without_a_preferences_text_or_a_confirmed_answer_nothing_is_sent(monkeypatch):
    text = section(PRIVACY, "What is never sent")
    assert "no preferences" in text and "confirmed answer" in text
    wire = Wire()
    monkeypatch.setattr(proxy, "make_provider", lambda deadline_s=3.0: adapter.OpenRouterProvider(transport=wire, key_fn=lambda: "k", deadline_s=deadline_s, max_retries=0))
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    m.set_level("high")
    hooks.pretool(m.call(KIND))
    assert wire.sent == []


def test_the_numbers_on_the_page_are_the_numbers_in_the_code():
    text = PRIVACY
    default_days = inspect.signature(ops.purge).parameters["older_than_days"].default
    for needle in (f"at most {proxy.MAX_TEXT // 1024} KiB of the command", f"{ask.STATE_CAP // 1024} KiB", f"up to {proxy.MAX_PAST} past answers",
                   f"{proxy.DEADLINE_S:g} seconds", f"{hooks.BUDGET_S:g} seconds", f"{proxy.MIN_DENIES} kinds of call", f"{proxy.MIN_SAMPLES} shadow questions", f"{default_days} days", adapter.URL, adapter.DEFAULT_MODEL):
        assert needle in text, f"the privacy page does not say '{needle}'"


def test_the_privacy_page_names_every_host_jade_upgrade_may_reach_and_no_other():
    text = section(PRIVACY, "Other network use")
    for host in sorted(upgrade._ALLOWED_HOSTS):
        assert host in text, host
    hosts = set(re.findall(r"`([a-z0-9.*-]+\.[a-z]{2,})`", text))
    assert hosts <= upgrade._ALLOWED_HOSTS | {"*.githubusercontent.com", "openrouter.ai"}, hosts - upgrade._ALLOWED_HOSTS


def test_the_page_does_not_speak_for_the_provider():
    """JADE states what it sends. What OpenRouter or TypeSafe keep, log or train on is in their terms (DX-H8)."""
    assert "does not control" in PRIVACY or "not controlled" in PRIVACY
    for line in re.split(r"(?<=[.!?])\s+|\n", PRIVACY):
        if re.search(r"openrouter|typesafe|provider", line, re.I) and re.search(r"\b(do(es)? not|never|will not|won't)\b[^.]{0,40}\b(store|retain|keep|log|train)", line, re.I):
            assert re.search(r"\b(cannot|can't|does not control|not controlled|no promise|read their)", line, re.I), f"a promise about the provider: {line}"
    assert not re.search(r"https?://(?:www\.)?(?:openrouter\.ai|typesafe\.ai)/\S*(?:terms|privacy)", PRIVACY, re.I), "no terms URL from memory"


def test_the_page_says_what_retention_does_not_clear():
    text = section(PRIVACY, "How long it stays on your machine")
    assert "--ask-text" in text and "chat question" in text
    assert inspect.signature(ops.purge_report).parameters["ask_text"].default is False
    assert "jade maintain" in text and "jade ledger purge" in text


def test_the_page_says_what_the_other_provider_commands_send():
    text = section(PRIVACY, "Other commands that call the provider")
    for needle in ("jev", "jade ask", "jade check", "jade improve", "fixed text"):
        assert needle in text, needle


# ---- the folder: where the repository path and the working folder go -------------------------------------------------------------------
FOLDER = "customer-acme-secret-project"


def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / FOLDER
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    for argv in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", *argv], cwd=repo, env=env, check=True, capture_output=True)
    return repo


def test_a_git_push_sends_the_repository_path_and_the_page_says_so(monkeypatch, tmp_path):
    repo = git_repo(tmp_path)
    (payload,) = hook_sends(monkeypatch, "enforce", "medium", True, cmd="git push origin v1", cwd=str(repo))   # V6-17: a push of `v1.0` is a release (HO-release), a human-only call that is never sent; `v1` is a plain high call
    heads = payload["state"]["fingerprints"]["git_head"]
    assert heads.startswith(str(repo.resolve())), "the fingerprint of a push holds the absolute repository path"
    text = section(PRIVACY, "What is in the request")
    fingerprints = next(line for line in text.splitlines() if line.startswith("- `fingerprints`"))
    assert "repository path" in fingerprints and "absolute" in fingerprints, "the page must say the absolute path of the repository is sent"
    assert not re.search(r"your folder (?:is|are) not part", text, re.I), "the page promised that the folder is not sent"


def test_a_call_that_is_not_a_push_does_not_send_the_folder(monkeypatch, tmp_path):
    repo = git_repo(tmp_path)
    (payload,) = hook_sends(monkeypatch, "enforce", "medium", True, cwd=str(repo))
    assert FOLDER not in json.dumps(payload), "outside a push the folder is not in the request"
    assert "Your level is not part of it" in PRIVACY


def test_the_purge_keeps_the_working_folder_and_the_repository_path_and_the_page_says_so(monkeypatch, tmp_path):
    repo = git_repo(tmp_path)
    r = hooks.pretool(m.call("git push origin v1", cwd=str(repo)))
    assert r.exit_code == 2, "enforce mode, no level: the push waits for a permit"
    d = m.db()
    try:
        with d.tx() as con:
            con.execute("UPDATE permits SET state='EXPIRED'")
        ops.purge_report(d, -1, ask_text=True)   # everything the purge can clear, as old as it can be
        permits = [dict(x) for x in d.con.execute("SELECT * FROM permits").fetchall()]
    finally:
        d.con.close()
    where = {col for row in permits for col, val in row.items() if FOLDER in str(val)}
    assert where == {"scope", "doc_json"}, f"the purge keeps the folder name in {where}"
    text = section(PRIVACY, "How long it stays on your machine")
    assert "working folder" in text and "repository path" in text and "not cleared" in text, "the retention section must say these two paths stay"
    assert not re.search(r"removes? (?:all )?project text", text), "the purge does not remove all project text"


# ---- the daily job ------------------------------------------------------------------------------------------------------------------
def test_the_daily_job_calls_the_provider_and_the_page_says_so(monkeypatch):
    """`jade setup` installs a job that runs `jade maintain` with no `--offline`, and `maintain` then runs the live check: two fixed-text calls, with no one at
    the keyboard. The page may not say that the person starts every provider call."""
    import plistlib

    from jade import cli

    assert plistlib.loads(ops.launchd_plist("x").encode())["ProgramArguments"][1:] == ["maintain"], "the installed job passes no --offline"
    for fmt in ("cron", "systemd"):
        assert "--offline" not in ops.maintain_schedule(fmt)
    seen = []
    monkeypatch.setattr(ops, "check", lambda live=False: seen.append(live) or [])
    cli._maintain(type("Args", (), {"offline": False})())
    assert seen == [True], "`jade maintain` without --offline runs the live check"
    text = section(PRIVACY, "Other commands that call the provider")
    assert "daily job" in text and "no one" in text.lower() and "You run these yourself" not in text
    assert "daily job" in PRIVACY.split("## When the hook sends a call")[0], "the short version names the daily job"
    assert "jade maintain" in section(README, "What leaves your machine"), "the front page table names the daily job"


# ---- speed -------------------------------------------------------------------------------------------------------------------------
def _measure(source: str):
    name, _, path = source.partition(": ")
    doc = json.loads((ROOT / "evals" / "performance" / name).read_text())
    if "runs" in doc:
        return next(r for r in doc["runs"] if r["config"] == path)
    for part in path.split("/"):
        doc = doc[part]
    return doc


def subsection(text: str, title: str) -> str:
    """A `### title` block of a document, up to the next heading of level 2 or 3."""
    found = re.search(rf"^### {re.escape(title)}[^\n]*\n(.*?)(?=^##+ |\Z)", text, re.S | re.M)
    assert found, f"no section '### {title}'"
    return found.group(1)


DOC04 = (ROOT / "docs" / "04-calibration-and-evaluation.md").read_text()
DOC09 = (ROOT / "docs" / "09-install-and-upgrade.md").read_text()


def test_the_latency_table_is_what_the_stored_measurements_say():
    """The table moved from the README front page to docs/04 section 4d; the README map still points at docs/04 for it."""
    assert "how fast the hook is" in README
    text = subsection(DOC04, "4d. How fast")
    table = rows(text)
    assert len(table) >= 4
    for what, n, p50, p95, date, source in (r[:6] for r in table):
        got = _measure(source)
        assert int(n) == (got.get("n") or got["runs"]), what
        assert float(p50.replace(" ms", "")) == pytest.approx(got["p50_ms"], abs=0.5), what
        assert float(p95.replace(" ms", "")) == pytest.approx(got["p95_ms"], abs=0.5), what
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) and date in source, what
    assert "synthetic" in text and "not measured again" in text, "the page says these are old, synthetic numbers"
    assert f"{proxy.DEADLINE_S:g} seconds" in text and f"{hooks.BUDGET_S:g} seconds" in text


# ---- the trust root ----------------------------------------------------------------------------------------------------------------
def test_the_trust_root_is_stated_and_matches_what_jade_upgrade_does():
    """The text moved from the README front page to docs/09 section 4; the README Upgrade step points there."""
    assert "what you trust when you install a release: `docs/09-install-and-upgrade.md`" in README
    text = subsection(DOC09.split("## 4. Release process")[1].split("\n## ")[0], "Which release you are running")
    assert upgrade.REPO in text or "github.com/vahidkaargar/jade-decision-governance" in text
    assert "does not check" in text and "signature" in text and "SHA256SUMS" in text
    src = inspect.getsource(upgrade).lower()
    for word in ("gpg", "cosign", "minisign", "attestation", "signature"):
        assert word not in src, f"jade/upgrade.py now mentions '{word}': the trust-root text says it checks no signature, so check both"
    assert "uv" in text and upgrade.install_argv("v1.2.3")[:3] == ["uv", "tool", "install"]


# ---- public hygiene ----------------------------------------------------------------------------------------------------------------
OWNER = "mobo" + "gift"


def tracked(*dirs: str) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", *dirs], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return out


def test_fixtures_and_cases_carry_neutral_labels():
    bad = []
    for rel in tracked("tests", "evals", "policies", "schemas", "registry"):
        p = ROOT / rel
        if p.suffix in {".py", ".json", ".jsonl", ".yaml", ".yml", ".md", ".sql", ".txt", ".sh"} and OWNER in p.read_text(errors="replace"):
            bad.append(rel)
    assert not bad, f"the maintainer's account name is in public fixtures: {bad}"


def test_the_public_pages_do_not_name_the_authors_machine():
    pages = ["README.md", "PRIVACY.md", "CONTRIBUTING.md", "SECURITY.md", *sorted(f"docs/{p.name}" for p in (ROOT / "docs").glob("[01]*.md") if not p.name.startswith("08"))]
    for rel in pages:
        text = (ROOT / rel).read_text() if (ROOT / rel).exists() else ""
        for phrase in ("Mac mini", "reference machine"):
            assert phrase not in text, f"{rel} names '{phrase}': that text lives in docs/08-deployment-runbook.md"
    assert "Mac mini" in (ROOT / "docs" / "08-deployment-runbook.md").read_text(), "the text moved to docs/08, it was not deleted"
