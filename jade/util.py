"""Small shared helpers: ids, time, canonical JSON, hashing, human-terminal detection."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta

AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CURSOR_AGENT", "CODEX_SANDBOX")
LEVELS = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def now() -> datetime:
    return datetime.now(UTC)


def ts(dt: datetime | None = None) -> str:
    return (dt or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_in(seconds: float) -> str:
    return ts(now() + timedelta(seconds=seconds))


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def canonical(obj) -> str:
    """Canonical JSON: sorted keys, no insignificant whitespace (docs/02 section 4)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def user() -> str:
    return re.sub(r"[^a-z0-9._-]", "-", getpass.getuser().lower()) or "user"


def agent_env_present() -> bool:
    return any(os.environ.get(k) for k in AGENT_ENV)


def is_human_tty(stdin_isatty: bool | None = None) -> bool:
    """True only for an interactive terminal that is not an agent harness (ADR-05, threat T2)."""
    if agent_env_present():
        return False
    if stdin_isatty is None:
        try:
            stdin_isatty = sys.stdin.isatty() and sys.stdout.isatty()
        except (AttributeError, ValueError):
            stdin_isatty = False
    return bool(stdin_isatty)


def session_id() -> str:
    """Session identity for dedup and Stop-hook reminders. Falls back to a per-directory, per-day id."""
    for key in ("JADE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        if os.environ.get(key):
            return os.environ[key]
    return f"cwd-{sha256(os.getcwd())[:12]}-{now():%Y%m%d}"


_INVISIBLE = dict.fromkeys([*range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2065), 0xFEFF, 0x00AD, 0x180E, 0x034F], None)
_DASHES = {**dict.fromkeys(range(0x2010, 0x2016), "-"), 0x2212: "-", 0xFE58: "-", 0xFE63: "-", 0xFF0D: "-"}


def fold(text: str) -> str:
    """The text as a pattern should see it: NFKC, no zero-width or soft-hyphen characters, every Unicode dash read as `-`.

    A secret or bind address written with a non-breaking hyphen or an invisible character is still that secret to the program that reads it."""
    if text.isascii():
        return text
    import unicodedata

    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE).translate(_DASHES)


def at_least(level: str, floor: str) -> bool:
    return LEVELS[level] >= LEVELS[floor]


ARABIC = range(0x0600, 0x0700)


def state_language(state: dict) -> str:
    """Language tag of the free text in a state object (claim, evidence, context), ignoring field names and sources."""
    return language(" ".join(str(v) for k, v in state.items() if k != "evidence_source" and isinstance(v, str)))


def language(text: str | None) -> str:
    """Script-based slice tag: fa (mostly Arabic script, covers Persian), en (Latin only), mixed, or unknown."""
    letters = [c for c in (text or "") if c.isalpha()]
    if len(letters) < 8:
        return "unknown"
    share = sum(1 for c in letters if ord(c) in ARABIC) / len(letters)
    return "fa" if share >= 0.6 else "en" if share <= 0.05 else "mixed"
