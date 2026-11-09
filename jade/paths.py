"""Where JADE keeps state, configuration and shipped data. Everything is overridable by env for tests."""

from __future__ import annotations

import hashlib
import json
import os
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


def policy_version() -> str:
    h = hashlib.sha256()
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
    ledger (pins, the permit seal key, SI-07 history). `env` is the process environment, or the `env` block of a Claude settings file, which the host
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
    for var in ("JADE_REGISTRY_DIR", "JADE_CLAUDE_SETTINGS", "JADE_BIN"):
        if env.get(var):
            add(var, False, "does not change what the gate decides")
    return out
