"""REL-4: the install and upgrade documentation stays true to the code and keeps its promises.

The command checks live in tests/test_docs_truth.py (every `jade ...` a document names must exist). This file checks what that one cannot: the
Quick start and docs/09 cover every `jade check` row, the uninstall list covers every artefact `jade setup` creates, the install line pins a
release tag, and no document claims VALIDATED or production approval for a first release that runs in advisory mode."""
import re
import tomllib
from pathlib import Path

import pytest

from jade import ops, setup

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
DOC = (ROOT / "docs" / "09-install-and-upgrade.md").read_text()
QUICK = README.split("## Quick start (Claude Code)")[1].split("\n## ")[0]
PROVIDER_AND_CONDITIONAL_ROWS = {"hook wiring", "pretool matcher", "provider canary", "model drift"}   # rows check() adds only in some states


def _section(n: int) -> str:
    return re.split(rf"^## {n}\. ", DOC, flags=re.M)[1].split("\n## ")[0]


def test_docs09_has_the_sections_the_readme_points_at():
    heads = re.findall(r"^## (\d+)\. (.+)$", DOC, re.M)
    assert [int(n) for n, _ in heads] == list(range(1, 8))
    titles = {int(n): t.lower() for n, t in heads}
    assert "versioning" in titles[2] and "upgrade contract" in titles[3] and "release process" in titles[4]
    assert "platforms" in titles[5] and "troubleshooting" in titles[6] and "uninstall" in titles[7]
    assert "docs/09-install-and-upgrade.md" in README and "section 5" in README and "section 6" in README and "section 7" in README


def test_quick_start_walks_install_setup_check_first_use_upgrade_rollback_uninstall_report():
    for needle in ("Requirements", "uv tool install git+https://github.com/vahidkaargar/jade-decision-governance@v", "jade setup", "jade check",
                   "jev verify", "jade upgrade --check", "jade upgrade", "jade hooks install", "jade upgrade --ref vX.Y.Z",
                   "docs/08-deployment-runbook.md", "uninstall", "Report a problem", "SECURITY.md", "Python 3.13", "Keychain", "Linux", "Claude Code"):
        assert needle in QUICK, needle
    for code in ("0 accept, 3 abstain, 4 reject, 5 provider unavailable, 6 invalid question",):
        assert code in QUICK
    assert "advisory" in QUICK and "does not" in QUICK


def test_the_install_line_pins_a_release_tag_not_older_than_the_version():
    pinned = set(re.findall(r"jade-decision-governance@v(\d+\.\d+\.\d+)", README + DOC))
    assert len(pinned) == 1, pinned
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    as_tuple = lambda v: tuple(int(x) for x in v.split(".")[:3])   # noqa: E731
    assert as_tuple(pinned.pop()) >= as_tuple(version), "the documented install tag must not be older than the code"


def test_every_check_row_has_a_troubleshooting_entry():
    rows = {name for name, _ok, _detail in ops.check(live=False)} | PROVIDER_AND_CONDITIONAL_ROWS
    documented = set(re.findall(r"^\| `([a-z ]+)` \|", _section(6), re.M))
    assert rows <= documented, f"rows without a troubleshooting entry: {sorted(rows - documented)}"
    assert documented <= rows, f"troubleshooting entries for rows `jade check` does not print: {sorted(documented - rows)}"


def test_versioning_policy_names_what_is_breaking_and_how_migrations_behave():
    text = _section(2)
    for needle in ("Semantic Versioning", "exit codes", "Hook protocol", "Ledger schema", "Policy file keys", "forward-only", "idempotent",
                   "backward compatible within a minor series", "MINOR"):
        assert needle in text, needle


def test_upgrade_contract_lists_what_it_never_touches():
    text = _section(3)
    for needle in ("ledger", "overlays", "settings.json", "CLAUDE.md", "jade hooks install", "never touches", "Roll back", "jade upgrade --ref"):
        assert needle in text, needle


def test_release_process_names_the_tag_the_workflow_and_the_checksum_command():
    text = _section(4)
    for needle in ("git tag -a vX.Y.Z", "git push origin vX.Y.Z", "release.yml", "SHA256SUMS", "shasum -a 256 -c SHA256SUMS", "sha256sum -c SHA256SUMS", "verify_install.sh"):
        assert needle in text, needle
    assert (ROOT / ".github" / "workflows" / "release.yml").is_file() and (ROOT / "scripts" / "verify_install.sh").is_file()


def test_platform_section_states_the_launchd_and_linux_limits():
    text = _section(5)
    assert "launchd" in text and "no job helper" in text and "Linux" in text and "Windows" in text and "OPENROUTER_API_KEY" in text


def test_uninstall_covers_everything_setup_and_install_create():
    text = _section(7)
    name = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["name"]
    for needle in ("jade hooks uninstall", f"uv tool uninstall {name}", setup.LABEL, "~/.claude/skills/jade", "~/.agents/jade", "~/.config/jade",
                   "~/.local/share/jade", "CLAUDE.md.bak-jade", "launchctl bootout", "pre-push"):
        assert needle in text, needle
    assert text.index("jade hooks uninstall") < text.index("uv tool uninstall"), "hooks come out while jade is still installed"


@pytest.mark.parametrize("name,text", [("README Quick start", QUICK), ("docs/09", DOC)])
def test_no_claim_of_validation_or_production_approval(name, text):
    for line in text.splitlines():
        if re.search(r"\bVALIDATED\b|production[ _]approved", line, re.I):
            assert re.search(r"\bnot\b|\bNo\b|\bnothing\b|\bdecid", line, re.I), f"{name}: unqualified claim: {line.strip()[:120]}"
    assert "advisory" in text
