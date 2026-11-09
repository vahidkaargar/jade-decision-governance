"""REL-3: scripts/verify_install.sh builds the wheel and sdist, installs the wheel into an isolated tool directory and runs it. The script is
the single source of the check, the release workflow calls it too; this test runs it (offline) and skips where uv is missing."""
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# A CI runner has an empty uv cache: let the script download there (UV_OFFLINE=0). Locally it stays offline unless the caller says otherwise.
_NET = {"UV_OFFLINE": "0"} if os.environ.get("CI") else {}

SCRIPT = ROOT / "scripts" / "verify_install.sh"
SH = shutil.which("sh")


def test_the_script_exists_is_posix_sh_and_parses():
    text = SCRIPT.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert "bash" not in text.splitlines()[0]
    assert subprocess.run([SH, "-n", str(SCRIPT)], capture_output=True, check=False).returncode == 0


def test_the_script_cannot_touch_the_real_home_or_the_network():
    text = SCRIPT.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for needle in ("UV_TOOL_DIR=", "UV_TOOL_BIN_DIR=", "HOME=", "JADE_HOME=", "JADE_CONFIG=", "UV_OFFLINE", "mktemp -d"):
        assert needle in text, needle
    assert "jade hooks install" not in code and "JADE\" hooks" not in code and "setup --yes" not in code and "launchctl" not in code and "claude-md apply" not in code
    assert '"$JADE" setup --status' in code and "setup\n" not in code


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed; the release workflow runs scripts/verify_install.sh")
    keep = tmp_path_factory.mktemp("dist")
    env = {**_NET, **os.environ, "VERIFY_INSTALL_KEEP_DIST": str(keep)}
    r = subprocess.run([SH, str(SCRIPT)], capture_output=True, text=True, timeout=600, env=env, cwd=ROOT, check=False)
    return r, keep


def test_the_script_passes_on_this_tree(dist):
    r, _ = dist
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "verify_install: ok" in r.stdout


def test_the_script_reports_each_step(dist):
    out = dist[0].stdout
    for step in ("build", "install", "jade --version", "jade check", "jade setup --status", "contents"):
        assert step in out, step


def test_the_wheel_and_sdist_hold_what_an_install_without_a_checkout_needs(dist):
    _, keep = dist
    (whl,) = keep.glob("*.whl")
    (sdist,) = keep.glob("*.tar.gz")
    with zipfile.ZipFile(whl) as z:
        names = set(z.namelist())
    for must in ("policies/loader.py", "policies/risk.yaml", "policies/safety.yaml", "policies/routing.yaml", "policies/escalation.yaml", "policies/promotion.yaml",
                 "jade/_skills/jade/SKILL.md", "jade/_docs/claude-md-jev-section.md", "jade/_schemas/permit.schema.json", "jade/_registry/q.verify_claim.supported_by_evidence.json"):
        assert must in names, must
    assert {p.name for p in (ROOT / "registry").glob("*.json")} <= {Path(n).name for n in names if n.startswith("jade/_registry/")}
    assert {p.name for p in (ROOT / "schemas").glob("*.json")} <= {Path(n).name for n in names if n.startswith("jade/_schemas/")}
    with tarfile.open(sdist) as t:
        members = {m.split("/", 1)[1] for m in t.getnames() if "/" in m}
    for must in ("pyproject.toml", "uv.lock", "scripts/verify_install.sh", ".github/workflows/release.yml", "skills/jade/SKILL.md", "docs/claude-md-jev-section.md",
                 "tests/fixtures/ledger_0_2_2.sql", "LICENSE", "README.md", "CHANGELOG.md"):
        assert must in members, must


def test_no_private_overlay_or_baseline_dataset_is_ever_packaged(dist):
    _, keep = dist
    (whl,) = keep.glob("*.whl")
    (sdist,) = keep.glob("*.tar.gz")
    with zipfile.ZipFile(whl) as z:
        every = z.namelist()
    with tarfile.open(sdist) as t:
        every += t.getnames()
    for n in every:
        name = Path(n).name
        assert not (name.endswith(".local.yaml") and not name.endswith(".example.yaml")), n
        assert not (name.startswith("dataset-") and name.endswith(".jsonl")), n
        assert not name.endswith(".sqlite"), n


def run_script(**env):
    return subprocess.run([SH, str(SCRIPT)], capture_output=True, text=True, timeout=600, env={**_NET, **os.environ, **env}, cwd=ROOT, check=False)


def test_a_prebuilt_dist_directory_is_verified_instead_of_building(dist):
    _, keep = dist
    r = run_script(VERIFY_INSTALL_DIST=str(keep))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "verify_install: ok" in r.stdout


def test_a_dist_directory_without_a_wheel_fails_with_one_line(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    r = run_script(VERIFY_INSTALL_DIST=str(tmp_path))
    assert r.returncode == 1
    assert r.stderr.strip().startswith("verify_install: FAIL:") and len(r.stderr.strip().splitlines()) == 1


def test_a_wheel_without_the_shipped_data_fails(dist, tmp_path):
    _, keep = dist
    (whl,) = keep.glob("*.whl")
    shutil.copy(next(keep.glob("*.tar.gz")), tmp_path)
    with zipfile.ZipFile(whl) as src, zipfile.ZipFile(tmp_path / whl.name, "w") as out:
        for item in src.infolist():
            if item.filename != "jade/_skills/jade/SKILL.md":
                out.writestr(item, src.read(item.filename))
    r = run_script(VERIFY_INSTALL_DIST=str(tmp_path))
    assert r.returncode == 1
    assert "the wheel lacks jade/_skills/jade/SKILL.md" in r.stderr


def _wheel_without(dist, tmp_path, drop: str):
    _, keep = dist
    (whl,) = keep.glob("*.whl")
    shutil.copy(next(keep.glob("*.tar.gz")), tmp_path)
    with zipfile.ZipFile(whl) as src, zipfile.ZipFile(tmp_path / whl.name, "w") as out:
        for item in src.infolist():
            if item.filename != drop:
                out.writestr(item, src.read(item.filename))
    return run_script(VERIFY_INSTALL_DIST=str(tmp_path))


@pytest.mark.parametrize("member", ["jade/permits.py", "jade/githook.py", "jade/improve.py", "jade/_registry/q.verify_claim.evidence_is_raw.json",
                                    "jade/_registry/q.risk.irreversible.json", "jade/_schemas/decision.schema.json", "jade/_schemas/examples/decision.json",
                                    "jade/_schemas/_defs.schema.json", "policies/escalation.yaml"])
def test_a_wheel_missing_any_file_of_the_source_tree_fails(dist, tmp_path, member):
    """The wheel is compared with the tracked files of the checkout, not with a short list: a module or registry entry that no verified command imports
    still has to be there (`jev verify` needs q.verify_claim.evidence_is_raw, `jade permit` needs permits.py)."""
    r = _wheel_without(dist, tmp_path, member)
    assert r.returncode == 1, r.stdout[-1500:]
    assert f"the wheel lacks {member}" in r.stderr and len(r.stderr.strip().splitlines()) == 1


def test_the_setup_status_rows_are_checked_for_the_platform_they_run_on():
    """launchd is macOS only: off macOS the maintenance job row is `n/a`, not `missing`, and the script has to accept that (it runs on ubuntu in CI)."""
    code = "\n".join(line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#"))
    assert "n/a" in code and "sys.platform" in code
    from jade import setup
    out: list[str] = []
    real = setup._platform
    setup._platform = lambda: "linux"
    try:
        setup.status(out.append)
    finally:
        setup._platform = real
    assert any(line.startswith("n/a      maintenance job plist: ") for line in out)
    assert not any(line.startswith("missing  maintenance job plist") for line in out)
