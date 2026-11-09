"""W-57: the wheel ships copies of schemas/, registry/ and docs/claude-md-jev-section.md; they must equal their sources byte for byte.

The wheel is built into a temporary directory with `uv build --wheel --offline` (no network: the build backend comes from uv's cache)."""
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".DS_Store"}


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed; CI builds the wheel (docs/08)")
    out = tmp_path_factory.mktemp("wheel")
    r = subprocess.run([uv, "build", "--wheel", "--offline", "--out-dir", str(out), str(ROOT)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    built = list(out.glob("*.whl"))
    assert len(built) == 1, built
    with zipfile.ZipFile(built[0]) as z:
        yield {n: z.read(n) for n in z.namelist()}


def _mapping() -> dict[str, str]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]


def _source_files(src: str) -> dict[str, bytes]:
    p = ROOT / src
    if p.is_file():
        return {"": p.read_bytes()}
    return {f.relative_to(p).as_posix(): f.read_bytes() for f in sorted(p.rglob("*"))
            if f.is_file() and "__pycache__" not in f.parts and f.name not in IGNORED}


def _shipped(wheel: dict[str, bytes], dest: str) -> dict[str, bytes]:
    if dest in wheel:
        return {"": wheel[dest]}
    return {n[len(dest) + 1:]: b for n, b in wheel.items() if n.startswith(dest + "/") and Path(n).name not in IGNORED}


def test_the_force_include_map_covers_the_three_packaged_copies():
    assert set(_mapping().values()) >= {"jade/_schemas", "jade/_registry", "jade/_docs/claude-md-jev-section.md"}


@pytest.mark.parametrize("src, dest", sorted(_mapping().items()))
def test_a_packaged_copy_equals_its_source_byte_for_byte(wheel, src, dest):
    want, have = _source_files(src), _shipped(wheel, dest)
    assert want, f"{src} is empty or missing"
    assert sorted(have) == sorted(want), f"{dest}: the wheel and {src} list different files"
    assert [k for k in want if have[k] != want[k]] == [], f"{dest} differs from {src}"


def test_the_wheel_has_the_directories_the_runtime_reads(wheel):
    from jade import paths  # paths._data reads these names when it runs from an installed wheel

    for name in ("jade/_schemas/permit.schema.json", "jade/_registry", "jade/_docs/claude-md-jev-section.md"):
        assert any(n == name or n.startswith(name + "/") for n in wheel), name
    assert paths.schema_dir().name in ("schemas", "_schemas")
