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


REQUIRE = "JADE_REQUIRE_APPROVE_MODULES"


def approve_modules_turned_off(doc: dict) -> list[str]:
    """W-201: every place in a parsed workflow where JADE_REQUIRE_APPROVE_MODULES is not "1": a missing workflow-level value, or any workflow, job or step
    env that sets it to something else. GitHub merges env workflow, then job, then step, so a step-level value wins for that step."""
    bad = []
    if str((doc.get("env") or {}).get(REQUIRE)) != "1":
        bad.append("workflow")
    for name, job in doc["jobs"].items():
        if REQUIRE in (job.get("env") or {}) and str(job["env"][REQUIRE]) != "1":
            bad.append(name)
        for i, step in enumerate(job.get("steps") or [], 1):
            if REQUIRE in (step.get("env") or {}) and str(step["env"][REQUIRE]) != "1":
                bad.append(f"{name} step {i}")
    return bad


def test_the_checker_sees_every_place_that_turns_the_switch_off():
    def doc(top=None, job=None, step=None):
        s = {"run": "uv run pytest -q"} | ({"env": {REQUIRE: step}} if step is not None else {})
        j = {"steps": [{"uses": "actions/checkout@x"}, s]} | ({"env": {REQUIRE: job}} if job is not None else {})
        return ({"env": {REQUIRE: top}} if top is not None else {}) | {"jobs": {"test": j}}
    assert approve_modules_turned_off(doc(top="1")) == []
    assert approve_modules_turned_off(doc()) == ["workflow"]
    assert approve_modules_turned_off(doc(top="0")) == ["workflow"]
    assert approve_modules_turned_off(doc(top="1", job="0")) == ["test"]
    assert approve_modules_turned_off(doc(top="1", step="0")) == ["test step 2"]
    assert approve_modules_turned_off(doc(top="1", step=0)) == ["test step 2"]       # unquoted YAML 0
    assert approve_modules_turned_off(doc(top="1", step=False)) == ["test step 2"]   # unquoted YAML false
    assert approve_modules_turned_off(doc(top="1", step="")) == ["test step 2"]


def test_ci_fails_instead_of_skipping_the_w93_modules(ci):
    # W-201: tests/conftest.py skips the subprocess approval modules where the real approver anchor exists; on a runner that must be an error, for every job.
    assert ci["env"][REQUIRE] == "1"
    assert approve_modules_turned_off(ci) == []   # no job and no step turns it off
    assert "-k " not in run_text(ci["jobs"]["test"])   # the parallel step runs every module, so the listed ones are among them


def test_the_release_test_job_also_fails_instead_of_skipping_the_w93_modules():
    # W-201: the release workflow runs the same suite before a tag is published, so a skip there must stop the run too.
    rel = yaml.safe_load((WORKFLOWS / "release.yml").read_text())
    assert approve_modules_turned_off(rel) == []
    assert "uv run pytest -q" in run_text(rel["jobs"]["test"]) and "-k " not in run_text(rel["jobs"]["test"])


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


def test_the_secret_scan_job_runs_its_own_tests_with_the_pinned_binary(ci):
    # tests/test_secret_scan.py skips without gitleaks, and no other job installs it: this job must run it, and a missing binary must fail there, not skip.
    job = ci["jobs"]["secrets"]
    steps = job["steps"]
    step = next(s for s in steps if "tests/test_secret_scan.py" in s.get("run", ""))
    assert "pytest" in step["run"]
    assert step["env"]["JADE_REQUIRE_GITLEAKS"] == "1"
    names = [s.get("name", "") for s in steps]
    assert names.index("Install gitleaks (pinned, checked)") < steps.index(step)
    assert any("uv sync --frozen" in s.get("run", "") for s in steps[: steps.index(step)])


def test_dependabot_still_updates_the_pinned_actions():
    doc = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    assert "github-actions" in {u["package-ecosystem"] for u in doc["updates"]}
