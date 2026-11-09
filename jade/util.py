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

AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CURSOR_AGENT", "CODEX_SANDBOX", "AI_AGENT")
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


# Programs that run an agent: the executable's name, or a Node/Python entry point that names one. Case-sensitive on purpose: the Claude
# desktop app is `Claude`, the agent CLI is `claude`, and a human's own terminal panel inside the app must not look like an agent.
_AGENT_PROGRAMS = frozenset({"claude", "claude-code", "codex", "cursor-agent", "aider"})
_AGENT_ENTRY = re.compile(r"@anthropic-ai/claude-code|/claude-code/|/@openai/codex|cursor-agent")


def agent_ancestor() -> str | None:
    """Why the process tree above this one looks like an agent harness, or None when it does not.

    An environment check is defeated by an agent that clears its variables, a tty check by an agent that allocates its own pty; neither
    changes who the parent processes are. This is a heuristic with a known hole (a process that double-forks and is reparented to launchd
    leaves no ancestry, docs/05 T2). When the process table cannot be read the answer is "refuse", never "allow"."""
    import subprocess

    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=3, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return "the process table could not be read"
    table: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    pid, seen = os.getpid(), set()
    while pid in table and pid not in seen and pid > 1:
        seen.add(pid)
        ppid, cmd = table[pid]
        words = cmd.split()
        prog = os.path.basename(words[0]) if words else ""
        if pid != os.getpid() and (prog in _AGENT_PROGRAMS or _AGENT_ENTRY.search(cmd)):
            return f"it runs under an agent process ({prog or 'unnamed'}, pid {pid})"
        pid = ppid
    return None


def human_tty_refusal(stdin_isatty: bool | None = None) -> str | None:
    """None for an interactive terminal that is not an agent harness, else the reason (ADR-05, threat T2). A heuristic for "not obviously an
    agent": an agent that allocates a pty, removes its environment and detaches from its parent passes it (docs/05 T2, W-38)."""
    if agent_env_present():
        return "an agent harness is present in the environment"
    if stdin_isatty is None:
        try:
            stdin_isatty = sys.stdin.isatty() and sys.stdout.isatty()
        except (AttributeError, ValueError):
            stdin_isatty = False
    if not stdin_isatty:
        return "stdin and stdout are not an interactive terminal"
    return agent_ancestor()


def is_human_tty(stdin_isatty: bool | None = None) -> bool:
    return human_tty_refusal(stdin_isatty) is None


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
