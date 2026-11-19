"""V6-34: .github/workflows/ci.yml. The workflow is never run here; these tests parse it and pin what it must keep."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHA_PIN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def ci() -> dict:
    doc = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())
    if True in doc and "on" not in doc:   # YAML 1.1 reads the key `on` as the boolean True
        doc["on"] = doc.pop(True)
    return doc


def run_text(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def test_the_token_is_read_only(ci):
    assert ci["permissions"] == {"contents": "read"}
    for name, job in ci["jobs"].items():
        assert "permissions" not in job, name   # no job widens the default


def test_every_job_has_a_time_limit(ci):
    for name, job in ci["jobs"].items():
        assert 0 < job.get("timeout-minutes", 0) <= 60, name


def test_a_newer_run_cancels_an_older_one_on_a_pull_request_only(ci):
    c = ci["concurrency"]
    assert "github.ref" in c["group"] and "github.workflow" in c["group"]
    assert "pull_request" in str(c["cancel-in-progress"]) and "==" in str(c["cancel-in-progress"])   # a push to main is never cancelled


def test_every_action_is_pinned_by_a_commit_sha(ci):
    refs = [s["uses"] for j in ci["jobs"].values() for s in j["steps"] if "uses" in s]
    assert refs
    for ref in refs:
        assert SHA_PIN.match(ref), ref


def test_no_run_step_interpolates_a_github_context(ci):
    for name, job in ci["jobs"].items():
        for s in job["steps"]:
            assert "${{" not in s.get("run", ""), (name, s.get("name"))   # contexts go through env: (script injection)


def test_the_linux_job_still_runs_the_same_gates_as_before(ci):
    job = ci["jobs"]["test"]
    text = run_text(job)
    assert job["strategy"]["matrix"]["python"] == ["3.13", "3.14"]
    for part in ("uv sync --frozen", "uv run ruff check .", '-n auto --dist loadgroup -m "not timing"', "-m timing", "evals/run_cases.py", "evals/replay_policy.py"):
        assert part in text, part


def test_a_macos_job_runs_setup_hook_payloads_and_the_install_check(ci):
    job = ci["jobs"]["macos"]
    assert job["runs-on"].startswith("macos-") and job["runs-on"] != "macos-latest"   # a fixed image, not a moving one
    text = run_text(job)
    assert "tests/test_setup.py" in text and "tests/acceptance/test_hook_payloads_w40.py" in text
    assert "-n " not in text   # serial
    assert "scripts/verify_install.sh" in text
    verify = next(s for s in job["steps"] if "verify_install.sh" in s.get("run", ""))
    assert verify["env"]["UV_OFFLINE"] == "0"   # a fresh runner has no uv cache
    assert "realshell" not in text   # the real-shell oracle is an owner run (d10 D-B8)
    for f in ("tests/test_setup.py", "tests/acceptance/test_hook_payloads_w40.py", "scripts/verify_install.sh"):
        assert (ROOT / f).is_file(), f


def test_the_secret_scan_runs_on_new_commits_with_a_pinned_checked_binary(ci):
    job = ci["jobs"]["secrets"]
    assert job["steps"][0]["with"] == {"fetch-depth": 0}
    text = run_text(job)
    assert "sha256sum -c" in text and "${GITLEAKS_SHA256}" in text and "${GITLEAKS_VERSION}" in text
    assert re.fullmatch(r"[0-9a-f]{64}", job["env"]["GITLEAKS_SHA256"])
    assert re.fullmatch(r"8\.\d+\.\d+", job["env"]["GITLEAKS_VERSION"])
    assert 'sh scripts/scan_new_changes.sh "$SCAN_BASE" HEAD' in text
    base = job["env"]["SCAN_BASE"]
    assert "github.event.pull_request.base.sha" in base and "github.event.before" in base
    assert text.index("sha256sum -c") < text.index("scan_new_changes.sh")
    assert (ROOT / "scripts" / "scan_new_changes.sh").is_file()


def test_dependabot_still_updates_the_pinned_actions():
    doc = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    assert "github-actions" in {u["package-ecosystem"] for u in doc["updates"]}
