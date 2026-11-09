"""Where JADE keeps state, configuration and shipped data. Everything is overridable by env for tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

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


def policy_dir() -> Path:
    if os.environ.get("JADE_POLICY_DIR"):
        return Path(os.environ["JADE_POLICY_DIR"])
    import policies

    return Path(policies.__file__).resolve().parent


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


def gate_mode() -> str:
    """enforce (default) | advisory | off. Env wins so a human can launch an unattended agent in advisory mode."""
    mode = os.environ.get("JADE_GATE_MODE") or config().get("gate_mode") or "enforce"
    return mode if mode in ("enforce", "advisory", "off") else "enforce"
