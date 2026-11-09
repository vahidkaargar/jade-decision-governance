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
from typing import NamedTuple

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
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()   # a lone surrogate hashes as itself, not as the `?` of another text


def user() -> str:
    return re.sub(r"[^a-z0-9._-]", "-", getpass.getuser().lower()) or "user"


def agent_env_present() -> bool:
    return any(os.environ.get(k) for k in AGENT_ENV)


# Programs that run an agent. A process is looked at by its executable (the `comm` column of `ps`, which holds the whole path, spaces
# included: the desktop app keeps its agent under `~/Library/Application Support/Claude/claude-code/<version>/.../claude.app/Contents/MacOS/claude`)
# and, for an interpreter, by the script it runs. Case-sensitive on purpose: the Claude desktop app itself is `Claude` (Electron), the agent
# CLI is `claude`, and a human's own terminal panel inside the app must not look like an agent. The agent binary the app starts, and its
# `disclaimer` launcher, are agents. Only the executable and the script an interpreter runs are looked at, never the whole command line:
# `vim ~/claude-code/notes` or `pytest /src/claude-code/tests` above a human's shell must not make that human an agent.
_AGENT_PROGRAMS = frozenset({"claude", "claude-code", "claude-agent-sdk", "codex", "cursor-agent", "aider", "goose", "opencode", "gemini"})
_INTERPRETERS = re.compile(r"^(?:node|nodejs|bun|deno|npx|bunx|pnpx|python[\d.]*|uv|uvx)$")
# A path that is an agent whatever its file name: the per-version directory the desktop app unpacks its agent into, the app's helper that
# launches it, the npm packages and the native-install directory of the CLI, the agent SDK and its bundled binary.
_AGENT_ENTRY = re.compile(r"@anthropic-ai/claude-(?:code|agent-sdk)|claude-agent-sdk|/\.?claude(?:-code)?/versions/|/Application Support/Claude/claude-code/"
                          r"|/Claude\.app/Contents/Helpers/disclaimer$|@openai/codex|cursor-agent")


class Proc(NamedTuple):
    """One row of the process table: the parent, the executable as `ps` prints it (the whole path, spaces and all) and the rest of the command line."""

    ppid: int
    exe: str
    args: str = ""


def _agent_word(proc: Proc) -> str | None:
    """The name of the agent this process is, or None."""
    exe = proc.exe.strip()
    if not exe:
        return None
    prog = os.path.basename(exe)
    if prog in _AGENT_PROGRAMS or _AGENT_ENTRY.search(exe):
        return prog or "unnamed"
    if _INTERPRETERS.match(prog):
        script = next((w for w in proc.args.split() if not w.startswith("-")), "")
        if os.path.basename(script) in _AGENT_PROGRAMS or _AGENT_ENTRY.search(proc.args):
            return os.path.basename(script) or prog
    return None


def _ps_rows(column: str) -> dict[int, str]:
    import subprocess

    out = subprocess.run(["ps", "-axo", f"pid=,ppid=,{column}="], capture_output=True, text=True, timeout=3, check=True).stdout
    rows: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)   # the text of the last column is everything after the second number, never split on a space
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rows[int(parts[0])] = f"{parts[1]} {parts[2].strip() if len(parts) > 2 else ''}"
    return rows


def read_process_table() -> dict[int, Proc]:
    """pid -> Proc for every process, from two `ps` reads: `comm` is the executable path whole (on macOS, where a path may hold spaces) and
    `command` adds the arguments; the arguments are what follows the executable in the command line. Raises OSError or SubprocessError when
    `ps` cannot be read. Tests replace this function with a synthetic tree."""
    comm, command = _ps_rows("comm"), _ps_rows("command")
    table: dict[int, Proc] = {}
    for pid, row in comm.items():
        ppid, _, exe = row.partition(" ")
        cmd = command.get(pid, "").partition(" ")[2]
        argv0, _, rest = cmd.partition(" ")
        if exe and cmd.startswith(exe):
            args = cmd[len(exe):].strip()
        elif exe.startswith("/"):   # macOS: comm is the real path, argv[0] is whatever the caller typed (a symlink, a bare name)
            args = rest.strip()
        elif os.path.basename(argv0).startswith(exe[:15]):   # Linux: comm is the program name cut to 15 characters, argv[0] has the path
            exe, args = argv0, rest.strip()
        else:
            args = rest.strip()
        table[pid] = Proc(int(ppid), exe, args)
    return table


def agent_ancestor(table: dict[int, Proc] | None = None, pid: int | None = None) -> str | None:
    """Why the process tree above this one looks like an agent harness, or None when it does not.

    An environment check is defeated by an agent that clears its variables, a tty check by an agent that allocates its own pty; neither
    changes who the parent processes are. This is a heuristic with a known hole (a process that double-forks and is reparented to launchd
    leaves no ancestry, docs/05 T2). When the process table cannot be read, or does not list this process, the answer is "refuse", never
    "allow". `table` and `pid` let a test walk a synthetic tree."""
    import subprocess

    me = os.getpid() if pid is None else pid
    if table is None:
        try:
            table = read_process_table()
        except (OSError, subprocess.SubprocessError, ValueError):
            return "the process table could not be read"
    if me not in table:
        return "the process table does not list this process"
    return _agent_above(table, me)


def _agent_above(table: dict[int, Proc], me: int) -> str | None:
    """The agent process among the ancestors of `me` (not `me` itself), as a reason, or None."""
    seen: set[int] = set()
    cur = me
    while cur in table and cur not in seen and cur > 1:
        seen.add(cur)
        proc = table[cur]
        if cur != me and (name := _agent_word(proc)):
            return f"it runs under an agent process ({name}, pid {cur})"
        cur = proc.ppid
    return None


_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ksh93", "mksh", "fish", "tcsh", "csh", "nu", "elvish", "xonsh", "ash"})


def _interactive_shell(proc: Proc) -> str | None:
    """None when `proc` is a shell that reads commands from a terminal (a login shell is `-zsh`; `zsh`, `zsh -l`, `bash --login -i`), else why it is not.
    A shell given a command string (`-c`, also in a cluster: `-lc`, `-ic`), `--command` or a script file is a program, not a person typing."""
    exe = proc.exe.strip()
    if not exe:
        return "its program is unknown"
    name = os.path.basename(exe).lstrip("-")
    if name not in _SHELLS:
        return f"it is {name or 'unnamed'}, not an interactive shell"
    skip = False
    for word in proc.args.split():
        if skip:   # the value of an option that takes one (`bash --init-file F`, `zsh -o vi`): not a script
            skip = False
            continue
        if word in ("--init-file", "--rcfile", "-o", "+o", "-O", "+O"):
            skip = True
            continue
        if word == "--command" or (word.startswith("-") and not word.startswith("--") and "c" in word[1:]):
            return f"it is {name} run with a command string"
        if not word.startswith(("-", "+")):
            return f"it is {name} run on a script"
    return None


def _login_shell_below(table: dict[int, Proc], leader: int, me: int) -> bool:
    """True when the session leader is `login` (Terminal.app and iTerm start `login -pf user` and it keeps the session) and the process below it on the way to `me` is an
    interactive shell: the person's shell is a child of the leader, not the leader itself."""
    cur, seen = me, set()
    while cur in table and cur not in seen and cur > 1:
        seen.add(cur)
        if table[cur].ppid == leader:
            return _interactive_shell(table[cur]) is None
        cur = table[cur].ppid
    return False


def tty_session_refusal(table: dict[int, Proc] | None = None, pid: int | None = None, sid: int | None = None, ctty: bool | None = None) -> str | None:
    """None when this process sits in a terminal session a person could be typing in, else the reason (W-37, docs/05 T2 and T19).

    An agent that allocates its own pty (`pty.spawn`, `script`, `expect`) makes `isatty` true and, by clearing its environment, passes the variable check;
    what it cannot make is a session led by an interactive shell: the process it spawns is the leader of the new session, and that is `jade` itself, `python`,
    `script` or `sh -c "..."`. Checked here: stdin is the controlling terminal of this process's session (`os.tcgetpgrp` raises when it is not: a pty slave that
    was handed down without `setsid`), the session leader is in the process table and is an interactive shell, and the ancestry of the leader is no agent
    either (a process that double-forks away from its parent keeps its session, so the leader's parents still show the harness). Not covered: an agent that
    starts its own interactive shell on a pty it drives and detaches from its parent, docs/05 T2. `table`, `pid`, `sid` and `ctty` let a test give a synthetic tree."""
    import subprocess

    me = os.getpid() if pid is None else pid
    if ctty is None:
        try:
            os.tcgetpgrp(sys.stdin.fileno())
            ctty = True
        except (OSError, ValueError, AttributeError):
            ctty = False
    if not ctty:
        return "stdin is not the controlling terminal of this process's session"
    if sid is None:
        try:
            sid = os.getsid(me)
        except OSError:
            return "the session of this process could not be read"
    if table is None:
        try:
            table = read_process_table()
        except (OSError, subprocess.SubprocessError, ValueError):
            return "the process table could not be read"
    leader = table.get(sid)
    if leader is None:
        return f"the leader of its session (pid {sid}) is not in the process table"
    if (why := _interactive_shell(leader)) and not (os.path.basename(leader.exe.strip()).lstrip("-") == "login" and _login_shell_below(table, sid, me)):
        return f"the leader of its session (pid {sid}) is not an interactive shell: {why}"
    if name := _agent_above(table, sid):
        return f"the leader of its session: {name}"
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
    return agent_ancestor() or tty_session_refusal()


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
