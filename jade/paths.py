"""Where JADE keeps state, configuration and shipped data. Everything is overridable by env for tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

PKG = Path(__file__).resolve().parent


def home() -> Path:
    p = Path(os.environ.get("JADE_HOME") or Path.home() / ".local" / "share" / "jade")
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except OSError:
        pass
    return p


def row_source() -> str | None:
    """`test` when the process runs under `JADE_SOURCE=test` (a verification run that has to use the live hook or ledger), else None. Every row such a
    process writes carries the tag: `jade ledger stats`, the miner, the calibration labels and the SessionStart notice leave tagged rows out, and
    `jade ledger purge --test-rows` removes them (W-72). The tag changes nothing the gate decides, and any other value is ignored."""
    return "test" if os.environ.get("JADE_SOURCE") == "test" else None


def config_dir() -> Path:
    return Path(os.environ.get("JADE_CONFIG") or Path.home() / ".config" / "jade")


def _data(name: str) -> Path:
    shipped = PKG / f"_{name}"
    return shipped if shipped.is_dir() else PKG.parent / name


def schema_dir() -> Path:
    return _data("schemas")


def registry_dir() -> Path:
    return Path(os.environ.get("JADE_REGISTRY_DIR") or _data("registry"))


def docs_dir() -> Path:
    return _data("docs")


def shipped_policy_dir() -> Path:
    import policies

    return Path(policies.__file__).resolve().parent


def policy_dir() -> Path:
    if os.environ.get("JADE_POLICY_DIR"):
        return Path(os.environ["JADE_POLICY_DIR"])
    return shipped_policy_dir()


def overlay(kind: str) -> Path | None:
    """Machine-local overlay (never shipped): ~/.config/jade/local/<kind>.local.yaml."""
    p = config_dir() / "local" / f"{kind}.local.yaml"
    return p if p.exists() else None


def checkout_overlay_dir() -> Path:
    """`policies/local/` of the checkout (or installed package) the code runs from. Nothing reads it: `jade check` lists the `*.local.yaml` files
    there so an edit in the wrong place is not silent (W-56)."""
    return shipped_policy_dir() / "local"


def git_commit(directory: Path) -> str | None:
    """Short HEAD commit of the git checkout at `directory`, or None (not a checkout, no git, a slow or failing git). Never raises."""
    if not (Path(directory) / ".git").exists():
        return None
    try:
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")}   # inside a git hook these name another repository
        r = subprocess.run(["git", "-C", str(directory), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, timeout=5, check=False, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{12}", sha) else None


_COMMIT: list[str | None] = []


def code_commit() -> str | None:
    """Commit of the checkout this code runs from (None for an installed package: site-packages has no .git). Read once per process."""
    if not _COMMIT:
        _COMMIT.append(git_commit(PKG.parent))
    return _COMMIT[0]


def code_version() -> str:
    """`<jade.__version__>` plus `+<commit>` when run from a checkout. Part of policy_version: a code change moves it like a policy change does (W-50)."""
    import jade

    commit = code_commit()
    return f"{jade.__version__}+{commit}" if commit else jade.__version__


def checkout_dir() -> Path | None:
    """The checkout a human names as the source of truth for `jade check`'s staleness test: env JADE_CHECKOUT, else config key `checkout`."""
    named = os.environ.get("JADE_CHECKOUT") or config().get("checkout")
    return Path(str(named)).expanduser() if named else None


def policy_version() -> str:
    h = hashlib.sha256()
    h.update(b"code:" + code_version().encode() + b"\n")
    files = sorted(policy_dir().glob("*.yaml")) + [p for p in (overlay("risk"), overlay("safety")) if p]
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def config() -> dict:
    p = config_dir() / "config.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


GATE_MODES = ("enforce", "advisory", "off")


def gate_mode() -> str:
    """enforce (default) | advisory | off. Env wins so a human can launch an unattended agent in advisory mode."""
    mode = os.environ.get("JADE_GATE_MODE") or config().get("gate_mode") or "enforce"
    return mode if mode in GATE_MODES else "enforce"


class Override(NamedTuple):
    var: str
    value: str
    weakens: bool   # can make the gate decide less than the shipped rules and the default ledger would
    why: str
    source: str


def _same(a: str, b: Path) -> bool:
    try:
        return Path(a).expanduser().resolve() == b.resolve()
    except (OSError, RuntimeError):
        return False


def gate_overrides(env: Mapping[str, str] | None = None, source: str = "environment") -> list[Override]:
    """The one list of variables that move what the gate reads: its mode, its policies, its configuration (local overlays and `gate_mode`) and its
    ledger (pins, the permit seal key, SI-07 history), and `JADE_SOURCE=test`, which changes no decision but hides what the ledger shows of them. `env` is the process environment, or the `env` block of a Claude settings file, which the host
    hands to every hook (docs/05 T19). A variable set to the value the default already gives is listed but does not weaken anything."""
    env = os.environ if env is None else env
    out: list[Override] = []

    def add(var: str, weakens: bool, why: str) -> None:
        out.append(Override(var, str(env[var]), weakens, why, source))

    if env.get("JADE_GATE_MODE"):
        mode = env["JADE_GATE_MODE"]
        add("JADE_GATE_MODE", mode in GATE_MODES and mode != "enforce", f"gate mode {mode if mode in GATE_MODES else 'enforce (invalid value ignored)'}")
    if env.get("JADE_POLICY_DIR"):
        add("JADE_POLICY_DIR", not _same(env["JADE_POLICY_DIR"], shipped_policy_dir()), "reads policies from another directory than the shipped one")
    if env.get("JADE_CONFIG"):
        add("JADE_CONFIG", not _same(env["JADE_CONFIG"], Path.home() / ".config" / "jade"), "reads gate_mode and local overlays from another directory")
    if env.get("JADE_HOME"):
        add("JADE_HOME", not _same(env["JADE_HOME"], Path.home() / ".local" / "share" / "jade"), "uses another ledger, pins and permit seal key")
    if env.get("JADE_APPROVER_ANCHOR"):   # W-89: where the approver public key is read from; the default path is the only one a root-owned file protects
        from . import signing
        add("JADE_APPROVER_ANCHOR", not _same(env["JADE_APPROVER_ANCHOR"], signing.DEFAULT_ANCHOR), "reads the approver public key from another file than the root-owned default")
    if env.get("JADE_SOURCE") == "test":   # W-72: tags every row, so a persisted tag hides gated calls from the statistics and the pending-permit notice
        add("JADE_SOURCE", True, "tags every ledger row `test`, which leaves its gated calls out of `jade ledger stats` and out of the pending-permit notice")
    for var in ("JADE_REGISTRY_DIR", "JADE_CLAUDE_SETTINGS", "JADE_BIN"):
        if env.get(var):
            add(var, False, "does not change what the gate decides")
    return out
