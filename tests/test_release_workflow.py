"""REL-3: .github/workflows/release.yml. The workflow is never run here; these tests parse it and pin what it must do."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    doc = yaml.safe_load((WORKFLOWS / name).read_text())
    if True in doc and "on" not in doc:   # YAML 1.1 reads the key `on` as the boolean True
        doc["on"] = doc.pop(True)
    return doc


@pytest.fixture(scope="module")
def wf():
    return load("release.yml")


def steps(job: dict) -> list[dict]:
    return job["steps"]


def run_text(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in steps(job))


def uses(doc: dict) -> set[str]:
    return {s["uses"] for job in doc["jobs"].values() for s in steps(job) if "uses" in s}


def test_it_runs_on_a_version_tag_push_and_nothing_else(wf):
    on = wf["on"]
    assert set(on) == {"push"}
    assert on["push"] == {"tags": ["v*"]}


def test_the_default_token_is_read_only_and_only_the_release_job_can_write(wf):
    assert wf["permissions"] == {"contents": "read"}
    writers = [n for n, j in wf["jobs"].items() if (j.get("permissions") or {}).get("contents") == "write"]
    assert writers == ["release"]
    for n, j in wf["jobs"].items():
        assert set(j.get("permissions") or {}) <= {"contents"}, n


def test_the_release_job_waits_for_the_test_job(wf):
    assert wf["jobs"]["release"]["needs"] in ("test", ["test"])


def test_the_test_job_runs_ruff_and_the_suite_like_ci(wf):
    text = run_text(wf["jobs"]["test"])
    assert "uv sync --frozen" in text
    assert "uv run ruff check ." in text
    assert "uv run pytest -q" in text


def test_the_release_job_builds_checksums_and_publishes_with_gh(wf):
    job = wf["jobs"]["release"]
    text = run_text(job)
    assert "uv build" in text
    assert "SHA256SUMS" in text and ("sha256sum" in text or "shasum -a 256" in text)
    assert "gh release create" in text and "gh release upload" in text   # create, or update when the release already exists
    assert "--clobber" in text
    for asset in ("*.whl", "*.tar.gz", "SHA256SUMS"):
        assert asset in text, asset
    assert "GH_TOKEN" in str(job["steps"]) and "github.token" in str(job["steps"])


def test_the_built_wheel_is_installed_and_verified_before_it_is_published(wf):
    text = run_text(wf["jobs"]["release"])
    assert "scripts/verify_install.sh" in text
    assert text.index("verify_install.sh") < text.index("gh release")


def test_the_tag_must_match_the_package_version(wf):
    text = run_text(wf["jobs"]["release"])
    assert "GITHUB_REF_NAME" in text and "__version__" in text


def test_only_actions_already_used_by_ci_are_referenced_the_same_way(wf):
    ci = uses(load("ci.yml"))
    assert uses(wf) <= ci, uses(wf) - ci


def test_no_job_interpolates_a_github_context_into_a_shell_script(wf):
    for n, j in wf["jobs"].items():
        for s in steps(j):
            assert "${{" not in s.get("run", ""), (n, s.get("name"))   # contexts go through env: (script injection)


def test_every_job_has_a_timeout(wf):
    assert all("timeout-minutes" in j for j in wf["jobs"].values())
