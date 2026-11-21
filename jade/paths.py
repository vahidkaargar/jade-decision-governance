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


def _refuse_real_state_under_pytest(var: str) -> None:
    """A test run that has not been given its own `JADE_HOME` / `JADE_CONFIG` must not open the human's real ledger or config. tests/conftest.py gives every test
    its own; a test file run from anywhere else (a scratch file, `pytest /tmp/x.py`) never loads that conftest, and once wrote a level and a confirmed answer into
    the live JADE state. pytest sets PYTEST_CURRENT_TEST while a test runs, so this is checked there and nowhere else."""
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get(var):
        raise RuntimeError(f"JADE refuses to use its real state while a pytest test runs without {var} set (a temporary directory per test: see tests/conftest.py)")


def home() -> Path:
    _refuse_real_state_under_pytest("JADE_HOME")
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
    _refuse_real_state_under_pytest("JADE_CONFIG")
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


LEVEL_VALUES = ("off", "low", "medium", "high")


def level() -> str | None:
    """The autonomy level the human set (docs/12): off | low | medium | high, or None when none is set. It is read from the config file only, so no
    environment variable of an agent's command can move it, and any other value (a typo, a number, a list, a planted string) counts as unset."""
    v = config().get("level")
    return v if isinstance(v, str) and v in LEVEL_VALUES else None


JEV_VALUES = ("on", "off")


def jev(recorded=None) -> str:
    """Whether a model may be asked at all (V6-28): `on` or `off`. `recorded` is the value of the last `jev` ledger event (None: no event, or `unset`; any other
    value that is not `on` or `off` is a record that cannot be believed). The rule, from the file and the record together, and failing closed:
    a bad value reads `off`; `on` needs both the key and the human's record of `on`, so deleting the key or writing `on` behind the command never raises autonomy;
    no key and no record is the default, `on` at level low or medium (an upgrade changes nothing) and `off` at every other level. It is read from the config
    file and the ledger only: no environment variable moves it."""
    cfg = config()
    if "jev" in cfg:
        v = cfg["jev"]
        if not isinstance(v, str) or v not in JEV_VALUES or v == "off":
            return "off"
        return "on" if recorded == "on" else "off"
    if recorded is not None:
        return "off"   # a record exists and the key is gone: the file was changed, not the human
    return "on" if level() in ("low", "medium") else "off"


# W-200: the one list of the variables that move the gate (SI-11). policies/safety.yaml (SI-11 description and patterns) cannot import it, so
# tests/test_gate_env_lists_w200.py fails when a pattern, the settings-file check or a report names a different set.
GATE_ENV_VARS = ("JADE_GATE_MODE", "JADE_POLICY_DIR", "JADE_CONFIG", "JADE_HOME", "JADE_APPROVER_ANCHOR", "JADE_REGISTRY_DIR", "JADE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")
HOST_SESSION_VAR = "CLAUDE_CODE_SESSION_ID"   # the host sets it in every session, so a value in the process environment is an override only when it differs from the host's own id


def is_gate_env_var(name: object) -> bool:
    return isinstance(name, str) and (name.startswith("JADE_") or name in GATE_ENV_VARS)


def gate_env_alternation() -> str:
    """The names of GATE_ENV_VARS as one regular-expression alternation, for the checks that read text. The `JADE_` names share their prefix
    (`JADE_(?:A|B)|OTHER`), which the regular-expression engine scans about three times faster than eight whole names."""
    mine = [re.escape(n[5:]) for n in GATE_ENV_VARS if n.startswith("JADE_")]
    other = [re.escape(n) for n in GATE_ENV_VARS if not n.startswith("JADE_")]
    return "|".join([f"JADE_(?:{'|'.join(mine)})", *other])


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


def gate_overrides(env: Mapping[str, str] | None = None, source: str = "environment", host_session_id: str | None = None) -> list[Override]:
    """The one list of variables that move what the gate reads: its mode, its policies, its configuration (local overlays and `gate_mode`) and its
    ledger (pins, the permit seal key, SI-07 history), and `JADE_SOURCE=test`, which changes no decision but hides what the ledger shows of them. `env` is the process environment, or the `env` block of a Claude settings file, which the host
    hands to every hook (docs/05 T19). A variable set to the value the default already gives is listed but does not weaken anything.
    `CLAUDE_CODE_SESSION_ID` is set by the host in every session: in the process environment it is listed only when `host_session_id` (the id the host
    put in the hook event) is given and differs from it; in a settings `env` block it is always listed, because the host never writes it there."""
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
    if env.get("JADE_SESSION_ID"):   # V6-18 a: names the session whose SI-07 hold `jade decide outcome` releases and whose answers `jade ack` clears; a persisted old id lifts a hold that is not its own
        add("JADE_SESSION_ID", True, "fixes the session id the CLI uses, so an old id could release an SI-07 hold or acknowledge answers of another session")
    if env.get(HOST_SESSION_VAR) and (source != "environment" or (host_session_id and env[HOST_SESSION_VAR] != host_session_id)):   # W-200
        add(HOST_SESSION_VAR, True, "fixes the session id the CLI reads when JADE_SESSION_ID is unset, so an old id could release an SI-07 hold or acknowledge answers of another session")
    if env.get("JADE_SOURCE") == "test":   # W-72: tags every row, so a persisted tag hides gated calls from the statistics and the pending-permit notice
        add("JADE_SOURCE", True, "tags every ledger row `test`, which leaves its gated calls out of `jade ledger stats` and out of the pending-permit notice")
    for var in ("JADE_REGISTRY_DIR", "JADE_CLAUDE_SETTINGS", "JADE_BIN"):
        if env.get(var):
            add(var, False, "does not change what the gate decides")
    return out
