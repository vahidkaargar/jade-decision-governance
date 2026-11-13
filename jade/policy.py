"""Risk assessment of a proposed tool call against policies/risk.yaml and policies/safety.yaml.

`policies.loader` holds the merge rules and raw first-match classification. This module adds what a
real shell command needs: it classifies per simple command after `shellnorm` has resolved escapes, quoting,
git global options and hidden command text, ignores heredoc bodies and quoted prose that no shell would run,
and treats read-only commands as low (see the `read_only` list in risk.yaml).
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import stat as stat_mod
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache

from policies import loader

from . import blind, egress, gitalias, inert, paths, samecmd, shellnorm
from .util import LEVELS, canonical, fold, sha256

NULL_REDIRECT = shellnorm.NULL_REDIRECT
SPLIT = shellnorm.SPLIT
PREFIX = shellnorm.PREFIX
WRITE_REDIRECT = re.compile(r">>?\s*\S")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell", "Monitor")  # built-in tools that run a command line: classified like Bash
# Every built-in tool the PreToolUse matcher names (ops.pretool_matcher adds `mcp__.*`). Agent/Task need no gate: the tool calls a subagent
# makes run the same hook. CronCreate/CronDelete only schedule a prompt that later runs under the same hooks, so they are not gated either.
READ_TOOLS = ("Read", "Grep", "Glob")  # gated for one thing only: the permit seal key (SI-10, W-70); a read of another credential file is medium (R-30, W-55), recorded and not gated
GATED_BUILTIN = (*SHELL_TOOLS, *WRITE_TOOLS, "RemoteTrigger", *READ_TOOLS)
# The built-in tools whose end PostToolUse reports (ops.posttool_matcher adds `mcp__.*`): the gated tools that can run under a permit, so a permitted call is
# closed (W-40). A read is never permitted, so nothing waits for its end.
POST_TOOLS = (*SHELL_TOOLS, *WRITE_TOOLS, "RemoteTrigger")
# Built-in tools decided not to gate (W-36): subagent and scheduling tools for the reasons above, and the web tools: a URL can carry data in its query and
# the gate cannot tell that from a page request (the accepted line of docs/05 T22, W-55).
# `jade check` lists a built-in tool the ledger saw that is in neither tuple, so a new tool gets a decision.
UNGATED_BUILTIN = ("Agent", "Task", "CronCreate", "CronDelete", "WebFetch", "WebSearch")
OLD_KEYS = {"old_string", "old_str"}  # text that is being removed is never scanned as content
PATH_KEYS = ("source", "destination", "src", "dest", "dst", "from", "to", "old_path", "new_path", "dir", "directory", "folder")
UNSAFE_GIT = shellnorm.UNSAFE_GIT  # marker prefix R-15 matches; carried only in the text a rule sees
HOOKS_BYPASS = "git:hooks-bypass "  # marker prefix R-17 matches: a `git -c core.hooksPath=...` command
EGRESS_MARK = egress.MARK  # marker prefix R-21 matches: curl, wget or nc sending a file, stdin or a variable to a host that is not this machine (W-55)
RECORDED_RULES = ("R-30", "R-31", "R-32")  # rules below the permit floor whose hits the hook journals (events `secret_read`, `blind_spot`): not gated, but counted so a soak report can size them
BLIND_MARK = blind.BLIND_MARK   # marker prefix R-31 matches: `blind:<label> <command>`, a command the classifier cannot read (W-31)
FETCH_MARK = blind.FETCH_MARK   # marker prefix R-22 matches: fetched or decoded text that runs as code (W-31)
PATH_RULES = ("R-01", "R-04", "R-12", "R-13", "R-16", "R-18", "R-24")  # the rules about where a write lands: they also see relative operands made absolute
WRITE_KEYS = ("file_path", "notebook_path", "path", "filePath", "file", "filepath", "target_file", "file_name", "filename", "target")
CONTENT_KEYS = {"content", "new_string", "old_string", "new_str", "old_str", "new_source", "text", "contents", "data", "source", "patch"}


def _strings(obj, key: str = ""):
    if isinstance(obj, str):
        if key not in CONTENT_KEYS:
            yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, str(k))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, key)


def content_strings(obj, key: str = "", skip: frozenset[str] = frozenset()):
    """Every string a call would put somewhere (file content, new text, any argument), never the text it removes or the keys named in `skip`."""
    if isinstance(obj, str):
        if key not in OLD_KEYS and key.lower() not in skip:
            yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from content_strings(v, str(k), skip)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from content_strings(v, key, skip)


def write_paths(tool_input: dict) -> list[str]:
    """The path(s) a Write/Edit-style call targets: the known path keys, else every string value that is not file content."""
    known = [str(tool_input[k]) for k in WRITE_KEYS if isinstance(tool_input.get(k), str) and tool_input[k].strip()]
    if known:
        return known
    return [v for v in _strings(tool_input) if v.strip() and len(v) <= 4096]


def content_bound(tool: str, tool_input: dict) -> bool:
    """True for a call whose plan hash does not cover everything it does (W-39): a Write, Edit, MultiEdit or NotebookEdit (the hash names the target path
    only) and an MCP call that carries a command (the hash is the command only). The permit records a digest of the whole input for these; an MCP call
    with no command already hashes its whole input (`_assess_tool`), and a shell call hashes the command."""
    return tool in WRITE_TOOLS or (is_mcp(tool) and isinstance(tool_input, dict) and mcp_command(tool_input) is not None)


def _leaves(obj, path: tuple = (), depth: int = 0):
    if isinstance(obj, (dict, list, tuple)) and not obj:
        yield path, obj   # an empty container is a value too: `{}` and an absent key are different inputs
    elif isinstance(obj, dict) and depth < 32:
        for k in sorted(obj, key=str):
            yield from _leaves(obj[k], (*path, str(k)), depth + 1)
    elif isinstance(obj, (list, tuple)) and depth < 32:
        for i, v in enumerate(obj):
            yield from _leaves(v, (*path, i), depth + 1)
    else:
        yield path, obj


def content_view(tool_input) -> str:
    """Everything a content-bound call carries, as text a human can read: for each value of the input in key order a header `@@ <key path> (<n> chars)`
    and the text itself (any other value as JSON on the header line). The key path is a JSON list and the header length bounds the text, so the
    rendering is one-to-one with the input (no value can imitate the next header): the digest of this text is the digest of the call's content, and
    the approval screen shows exactly what was hashed."""
    out = []
    for path, v in _leaves(tool_input):
        head = canonical(list(path))
        out.append(f"@@ {head} ({len(v)} chars)\n{v}\n" if isinstance(v, str) else f"@@ {head} = {canonical(v)}\n")
    return "".join(out)


def content_sha(tool_input) -> str:
    return sha256(content_view(tool_input))


def tool_subject(tool: str, tool_input: dict) -> str:
    """The text a gate decision is about: the command, or the target path(s)."""
    if tool in WRITE_TOOLS:
        return "\n".join(write_paths(tool_input))
    if tool in READ_TOOLS:
        paths_, globs = read_targets(tool, tool_input)
        return "\n".join([*paths_, *globs])
    if is_mcp(tool):
        return mcp_command(tool_input) or ""
    return str(tool_input.get("command") or "")


def read_targets(tool: str, tool_input: dict) -> tuple[list[str], list[str]]:
    """(paths, globs) a Read/Grep/Glob call names. A Grep `pattern` is a regular expression over file content and is never a target (a search for the
    text `permit-seal.key` in the docs reads no key); the Glob `pattern` and the Grep `glob` are path patterns."""
    def text(k: str) -> list[str]:
        v = tool_input.get(k) if isinstance(tool_input, dict) else None
        return [v] if isinstance(v, str) and v.strip() and len(v) <= 4096 else []

    paths_ = [x for k in WRITE_KEYS for x in text(k)]
    globs = text("pattern") if tool == "Glob" else text("glob") if tool == "Grep" else []
    return paths_, globs


def seal_key_dirs() -> tuple[str, ...]:
    """The directories that may hold the permit seal key, symlinks resolved: the JADE home in force and the default `~/.local/share/jade` (a call that
    names the default stays covered when `JADE_HOME` points elsewhere, as SI-10 does for a command). Unlike `paths.home()` this creates nothing: a Read
    must not change the disk."""
    default = real(os.path.expanduser("~/.local/share/jade"))
    return tuple(dict.fromkeys((real(os.path.expanduser(os.environ.get("JADE_HOME") or "~/.local/share/jade")), default)))


_DATA_FIRMLINK = "/System/Volumes/Data"


def _dir_spelling(p: str) -> str:
    """A directory path as the default macOS volume (APFS, case-insensitive, `/System/Volumes/Data` firmlinked to `/`) treats it: realpath folds neither."""
    if p == _DATA_FIRMLINK or p.startswith(_DATA_FIRMLINK + "/"):
        p = p[len(_DATA_FIRMLINK):] or "/"
    return p.casefold()


def is_seal_key_dir(path: str) -> bool:
    """True when `path` (already resolved with `real`) is one of the directories that may hold the seal key, under any case or firmlink spelling."""
    return _dir_spelling(path) in {_dir_spelling(d) for d in seal_key_dirs()}


def is_mcp(tool: str) -> bool:
    return tool.startswith("mcp__")


def is_gated(tool: str) -> bool:
    """Tools the gate assesses at all; everything else returns before any classification."""
    return tool in GATED_BUILTIN or is_mcp(tool)


def runs_shell(tool: str, tool_input: dict) -> bool:
    return tool in SHELL_TOOLS or (is_mcp(tool) and mcp_command(tool_input) is not None)


def mcp_command(tool_input: dict) -> str | None:
    for key in risk().get("mcp_command_keys", ["command", "cmd", "script"]):
        if isinstance(tool_input.get(key), str) and tool_input[key].strip():
            return tool_input[key]
    return None


@dataclass
class Assessment:
    level: str
    rule_id: str
    rationale: str
    environment: str = "local"
    reversibility: str = "reversible"
    blast_radius: str = "file"
    data_sensitivity: str = "none"
    blocked: list[str] = field(default_factory=list)
    plan_hash: str = ""
    subject: str = ""
    plan_subject: str = ""  # exactly what plan_hash covers besides the tool: `subject` is what a human reads, this is what was hashed
    plan_cwd: str = ""
    hold_key: str = ""  # identity of the command for SI-07: the canonical gated segments, independent of quoting, separators, prefixes and cwd
    blind: list[str] = field(default_factory=list)  # R-31 only: the labels of what the gate could not read (`script-file`, `eval-var` ...); never the command text
    recorded: bool = False  # R-30 and R-31: a read-only command or a Read/Grep/Glob call named a credential file (a write that names one is R-30 too and is not counted)
    ho_ids: list[str] = field(default_factory=list)   # docs/12: the human-only classes the call falls in (HO_* below). Never changes `level` or `rule_id`
    elevated: bool = False   # set by the hook, not here: a level was set and a human-only class raised the call to high

    @property
    def human_only(self) -> bool:
        return bool(self.ho_ids)


_BASE_ONLY = False  # see base_only()


@lru_cache(maxsize=2)
def _risk(with_overlay: bool) -> dict:
    r = loader.load_risk(overlay=paths.overlay("risk") if with_overlay else None, base=paths.policy_dir() / "risk.yaml")
    r["_read_only"] = [re.compile(p) for p in r.get("read_only", [])]
    r["_inert"] = [re.compile(p) for p in r.get("inert_for_files", [])]
    return r


@lru_cache(maxsize=2)
def _safety(with_overlay: bool) -> dict:
    return loader.load_safety(overlay=paths.overlay("safety") if with_overlay else None, base=paths.policy_dir() / "safety.yaml")


def risk() -> dict:
    return _risk(not _BASE_ONLY)


def safety() -> dict:
    return _safety(not _BASE_ONLY)


@lru_cache(maxsize=1)
def _routing() -> dict:
    return loader.load_routing(paths.policy_dir() / "routing.yaml")


def routing() -> dict:
    """policies/routing.yaml, shipped file only (no overlay). Raises `loader.RoutingError` when it cannot be read or is malformed."""
    return _routing()


def reset_cache() -> None:
    _risk.cache_clear()
    _safety.cache_clear()
    _routing.cache_clear()
    _hidden.cache_clear()


@contextmanager
def base_only():
    """Use the shipped policy files only, without the machine-local overlays. The hook's degraded mode when an overlay fails to load:
    the overlay can only add rules, so the shipped ones are a sound floor to classify against."""
    global _BASE_ONLY
    prev, _BASE_ONLY = _BASE_ONLY, True
    try:
        yield
    finally:
        _BASE_ONLY = prev


skeleton = shellnorm.skeleton
segments = shellnorm.segments
segments_cwd = shellnorm.segments_cwd


def _analyse(seg: str) -> tuple[str, list[shellnorm.GitCall]]:
    """The segment without launcher prefixes (sudo, env, timeout ...) and with git global options removed."""
    return shellnorm.git_calls(PREFIX.sub("", seg, count=1).strip())


def _bare(seg: str) -> str:
    return _analyse(seg)[0]


def git_calls(seg: str) -> list[shellnorm.GitCall]:
    return _analyse(seg)[1]


def _pathfold(text: str) -> str:
    """Lexical path clean-up (`//`, `/./`, `x/../`) so a protected path matches however it is spelled."""
    t = re.sub(r"/{2,}", "/", text)
    t = re.sub(r"/(?:\./)+", "/", t)
    while (u := re.sub(r"/[^/\s]+/\.\./", "/", t)) != t:
        t = u
    return t


def real(path: str) -> str:
    """`os.path.realpath` of a path as a shell reads it: a NUL byte is dropped (bash ignores it in a command), so the classifier never
    raises ValueError on one."""
    return os.path.realpath(path.replace("\x00", ""))


def normalise_path(path: str, cwd: str = "") -> str:
    """Absolute, symlink-resolved (deepest existing ancestor), `~`-expanded form of a write target."""
    if not path:
        return ""
    p = os.path.expanduser(path.replace("\x00", ""))
    if not os.path.isabs(p):
        try:
            p = os.path.join(cwd or os.getcwd(), p)
        except OSError:
            return ""
    return re.sub(r"/{2,}", "/", real(p))


def is_read_only(seg: str) -> bool:
    if WRITE_REDIRECT.search(seg):
        return False
    bare, calls = _analyse(seg)
    if any(c.danger for c in calls):
        return False
    bare = system_bare(bare)
    return any(p.search(bare) for p in risk()["_read_only"])


def resolved_cwd(cwd: str = "") -> str:
    """The directory a command will run in, symlinks resolved: a permit for `/tmp/link` stops matching when the link is retargeted."""
    return real(os.path.expanduser(cwd)) if cwd else ""


def plan_hash(tool: str, subject: str, cwd: str = "") -> str:
    """Hash of the exact thing a permit authorizes: tool, the command or path as written, and the resolved working directory.

    Whitespace is never folded: inside quotes (`rm -rf "my  dir"`), in a heredoc body (YAML, Python) and a newline versus a space it changes what
    runs, and the human approved the text they saw. Only a space, tab or newline at the ends is trimmed, which no shell reads as part of a word:
    a no-break space, an ideographic space, a carriage return, a vertical tab or a control character at the end is part of the last word."""
    return sha256(canonical({"tool": tool, "subject": subject.strip(" \t\n"), "cwd": resolved_cwd(cwd)}))


def _seal(a: Assessment, tool: str, subject: str, cwd: str, shown: str) -> None:
    a.plan_hash, a.subject, a.plan_subject, a.plan_cwd = plan_hash(tool, subject, cwd), shown, subject, cwd


def _jade_package(path: str) -> str:
    """`path` with every directory named `jade` that is not JADE's package spelled `jade~`, for R-16 only.

    The checkout of this project is itself called `jade`, so a relative operand (`*.php`, `setup.py`, `.`) made absolute under that directory
    reads like `.../jade/*.php`: a substring coincidence, not a write to jade/*.py. A directory named `jade` counts as the package when it holds
    `__init__.py` and `hooks.py`, or when it does not exist (a path being created is judged as written, fail closed)."""
    orig = path.split("/")
    parts = list(orig)
    for k, comp in enumerate(orig):
        if comp.lower() == "jade":
            d = "/".join(orig[:k + 1]) or "/"
            if os.path.isdir(d) and not (os.path.exists(os.path.join(d, "__init__.py")) and os.path.exists(os.path.join(d, "hooks.py"))):
                parts[k] = "jade~"
    return "/".join(parts)


# Options whose value is a name (a project, a container, a package) and never a path the command writes.
NAME_OPTIONS = frozenset({"--name", "--project-name", "--package", "--app-name", "--title", "--namespace", "--label", "--hostname", "--user"})


_SPELLED_ANCHOR = re.compile(r"^(?:\$HOME|\$\{HOME\}|\$PWD|\$\{PWD\}|\$\(pwd\))(?=/|$)")
_ATTACHED_VALUE = re.compile(r"^-[A-Za-z](?=[/~$.])(.+)$")   # `-o/dir`, `-C$HOME/x`, `-t/dir`: a short option with its value attached
_KEYED_VALUE = re.compile(r"^(?:of|if)=(.*)$")                # dd's `of=/path`
URL_START = re.compile(r"^[A-Za-z][\w+.-]*://")


def _operand_value(tok: str) -> str:
    """The path a word of a command stands for: the value of `--opt=value`, of an attached `-o/dir` and of dd's `of=`, `$HOME` as `~` and `$PWD` or
    `$(pwd)` as `.` (the directory the command runs in). Anything else comes back as it was, an option without a value as the empty string."""
    if len(tok) > 1 and tok[0] == '"' and tok[-1] == '"' and _SPELLED_ANCHOR.match(tok[1:-1]):
        tok = tok[1:-1]
    if tok.startswith("-"):
        m = None if tok.startswith("--") else _ATTACHED_VALUE.match(tok)
        tok = m.group(1) if m else tok.partition("=")[2]
    elif m := _KEYED_VALUE.match(tok):
        tok = m.group(1)
    return _SPELLED_ANCHOR.sub(lambda a: "~" if "HOME" in a.group(0) else ".", tok, count=1)


def _absolute_operands(seg: str, dirs: tuple[str, ...], package: bool = False) -> str:
    """The operands of a segment as absolute, symlink-resolved paths, for every directory the segment may run in.

    Catches `cd /etc && sort -o pf.conf in`, `sort -o ../../etc/pf.conf in` and a path reached through a symlink. A path that is an existing
    directory ends in `/` (`cp x ~/.claude/hooks` writes into it); `package` reads it as R-16 does (`_jade_package`).
    """
    out: list[str] = []
    if seg.startswith(shellnorm.TEXT_MARK):
        return ""
    toks = seg.split(" ")[:64]
    for n, tok in enumerate(toks):
        if n == 0:
            continue  # the program being run is not something the segment writes (`~/.local/bin/jade check`)
        if n > 1 and toks[n - 1] in NAME_OPTIONS:
            continue  # `uv init --name jade`: a name, not a path
        tok = _operand_value(tok.replace("\x01", " "))
        if not tok or tok.startswith(("$", "@", '"')) or tok in ("&&", "||") or "git:unsafe" in tok or URL_START.match(tok):
            continue
        for d in dirs:
            if d or os.path.isabs(os.path.expanduser(tok)):
                if p := normalise_path(tok, d):
                    if not p.endswith("/") and os.path.isdir(p):
                        p += "/"
                    if p != tok:
                        out.append(_jade_package(p) if package else p)
    return " ".join(dict.fromkeys(out))


def _seen_by(rule: dict, text: str, files: str | None) -> str:
    """The text a rule looks at: a `files_only` rule sees the write targets of the segment (`files`) when they are known, else the whole text."""
    return files if rule.get("files_only") and files is not None else text


URL_WORD = re.compile(r"^[A-Za-z][\w+.-]*:/")   # a word that STARTS with an address, `https://host/x` or, after path folding, `https:/host/x`: not a file. A `name:/` further inside a word is a directory called `name:`
GLOB_OR_EXPANSION = re.compile(r"[$`*?\[\]{}]")
# Verbs that only remove, retarget or re-mode what exists: they never create the file a `creates_only` rule is about.
NON_CREATING = frozenset({"rm", "rmdir", "unlink", "shred", "chmod", "chown", "chgrp", "chflags", "xattr"})


def _creates(rule: dict, text: str, dirs: tuple[str, ...] = ("",), whole: bool = False) -> bool:
    """Does the call create something the `creates_only` rule names? `text` is what the call writes (a path when `whole`, else the words of a segment).

    A word that matches the rule and resolves, in every directory the call may run in, to an existing regular file (a symlink to one counts) is an
    edit. Everything else is a creation: a path that does not exist (a dangling symlink too), an existing directory (the call puts something into
    it), a glob, a variable, a relative path with no known directory. A rule that matched but whose match no single word explains is a creation."""
    seen = urls = False
    for tok in [text] if whole else text.split(" "):
        tok = _operand_value(re.sub(r"^[<>|&]+", "", tok.replace("\x01", " ").strip("\"'")))
        if not tok or not rule["_re"].search(tok):
            continue
        if URL_WORD.search(tok):
            urls = True
            continue
        seen = True
        if GLOB_OR_EXPANSION.search(tok):
            return True
        for d in dirs or ("",):
            if not d and not os.path.isabs(os.path.expanduser(tok)):
                return True
            p = normalise_path(tok, d)
            if not (p and os.path.isfile(p)):
                return True
    return not (seen or urls)


def _is_checkout(d: str) -> bool:
    """A directory that holds JADE's own package (`jade/__init__.py` and `jade/hooks.py`): the checkout of this project, whatever it is called."""
    return os.path.isfile(os.path.join(d, "jade", "__init__.py")) and os.path.isfile(os.path.join(d, "jade", "hooks.py"))


def _in_checkout(rule: dict, text: str, dirs: tuple[str, ...] = ("",), whole: bool = False) -> bool:
    """Does a word the `checkout_only` rule names (R-24: `pyproject.toml`, `uv.lock`) resolve, in a directory the call may run in, to a file of JADE's checkout?

    Every project has a `pyproject.toml`, so the name alone is no finding; the file of the project the gate protects is. A relative word with no known
    directory is not resolved (nothing says whose file it is), and a glob or variable is never matched by the rule's own pattern."""
    for tok in [text] if whole else text.split(" "):
        tok = _operand_value(re.sub(r"^[<>|&]+", "", tok.replace("\x01", " ").strip("\"'")))
        if not tok or not rule["_re"].search(tok) or URL_WORD.search(tok):
            continue
        for d in dirs or ("",):
            if not d and not os.path.isabs(os.path.expanduser(tok)):
                continue
            if (p := normalise_path(tok, d)) and _is_checkout(os.path.dirname(p)):
                return True
    return False


def _hit(rule: dict, text: str, dirs: tuple[str, ...] = ("",), whole: bool = False) -> bool:
    return bool(rule["_re"].search(text)) and (not rule.get("creates_only") or _creates(rule, text, dirs, whole)) \
        and (not rule.get("checkout_only") or _in_checkout(rule, text, dirs, whole))


def _path_rule(text: str, files: str | None = None, alt: dict[str, str] | None = None, dirs: tuple[str, ...] = ("",), whole: bool = False) -> dict | None:
    """The first path rule that matches. `alt` gives a rule its own `files` text (R-16 reads the operands without the directories that only look
    like JADE's package, see `_jade_package`; R-18 reads what the command creates, see `creation_view`)."""
    if not text and not alt:
        return None
    return next((r for r in risk()["rules"] if r["id"] in PATH_RULES and (t := alt[r["id"]] if alt and r["id"] in alt else _seen_by(r, text, files))
                 and _hit(r, t, dirs, whole)), None)


def _program_basename(text: str) -> str:
    """The segment with the directory of the program it runs taken off (`~/.local/bin/jade check` is `jade check`): running a protected file is not
    writing it. Launcher prefixes (sudo, env, VAR=x, uv run) are left alone."""
    head = m.end() if (m := PREFIX.match(text)) else 0
    word, sep, rest = text[head:].partition(" ")
    return text[:head] + word.rsplit("/", 1)[-1] + sep + rest if "/" in word and not word.startswith(("-", ">")) else text


def _first_match(text: str, files: str | None = None, alt: dict[str, str] | None = None, dirs: tuple[str, ...] = ("",), whole: bool = False) -> dict:
    for r in risk()["rules"]:
        if (t := alt[r["id"]] if alt and r["id"] in alt else _seen_by(r, text, files)) and _hit(r, t, dirs, whole):
            return r
    return risk()["rules"][-1]


COPY_VERBS = {"cp", "rsync", "ln", "install", "scp"}
REDIRECT_TARGET = re.compile(r">>?[|&]?\s*(\S+)")
SYSTEM_BIN = re.compile(r"^/(?:usr/|opt/homebrew/|usr/local/)?s?bin/")
# Options that name a file the program writes (ruff --output-file, pytest --junitxml, mypy --html-report, pylint --output, -o of bandit and pytest):
# the one place an otherwise read-only command (`inert_for_files`) writes an operand-shaped path.
OUTPUT_OPTION = re.compile(r"^(?:-o|--[\w-]*(?:output|report|cache-dir|junit|log-?file|result|basetemp|outfile|html|xml|json)[\w-]*)(?:=(.*))?$")


def system_bare(bare: str) -> str:
    """The command with a system directory taken off its program (`/usr/bin/grep` is `grep`): a program in a user directory is unknown and stays so."""
    return SYSTEM_BIN.sub("", bare, count=1)


def _output_option_values(words: list[str]) -> list[str]:
    out = []
    for i, w in enumerate(words):
        if m := OUTPUT_OPTION.match(w):
            if m.group(1) is not None:
                out.append(m.group(1))
            elif i + 1 < len(words) and not words[i + 1].startswith("-"):
                out.append(words[i + 1])
        elif re.match(r"^-o\S", w):
            out.append(w[2:])
    return out


REDIRECT_OP = re.compile(r"^(?:\d*|&)>>?[|&]?$|^\d*<$")                      # `>`, `2>>`, `&>`, `<`: the target is the next word
REDIRECT_WORD = re.compile(r"^(?:(?:\d*|&)>>?[|&]?\S+|\d*>&\d*|\d*<\S+)$")       # `2>/tmp/err`, `>&2`, `</dev/null`: a redirect, not an operand


def operands(words: list[str]) -> list[str]:
    """The operands of a command's words: no option, no redirect and no redirect target (`cp a b 2>/tmp/err` copies to `b`, not to `/tmp/err`)."""
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
        elif REDIRECT_OP.match(w):
            skip = True
        elif not w or w.startswith("-") or REDIRECT_WORD.match(w):
            continue
        else:
            out.append(w)
    return out


def files_view(bare: str) -> str | None:
    """What a command writes, for the rules that care about files and not about mentions (R-13, R-16).

    A string is the destinations that are known (empty: none), None means it may write any operand, so the whole segment counts. Known:

    * a command that only reads its operands (`inert_for_files`, and every `read_only` verb) writes what it redirects into, and an inert
      command also what its output options name (`ruff check --output-file jade/x.py`): `git show X > jade/hooks.py`, `cat a > policies/risk.yaml`;
      the text it prints or the file it is given (a `cat > f <<EOF` body, a `printf` message) is data;
    * cp, rsync, ln, install and scp: the destination; tar -c: the archive."""
    bare = system_bare(bare)
    words = bare.split(" ")
    verb = words[0].rsplit("/", 1)[-1]
    redirects = " ".join(REDIRECT_TARGET.findall(bare))
    if any(p.search(bare) for p in risk()["_inert"]):
        return " ".join([redirects, *_output_option_values(words[1:])]).strip()
    if any(p.search(bare) for p in risk()["_read_only"]) and not any(c.danger for c in _analyse(bare)[1]):
        return redirects
    ops = operands(words[1:])
    if verb in COPY_VERBS and ops and not any(w in ("-t", "--target-directory") or w.startswith("--target-directory=") or (w.startswith("-") and not w.startswith("--") and "t" in w[1:]) for w in words[1:]):
        return f"{ops[-1]} {redirects}".strip()
    if verb == "tar" and len(words) > 2:
        flags = words[1]
        if (re.fullmatch(r"-?[a-zA-Z]*c[a-zA-Z]*", flags) and "f" in flags) or "--create" in words[1:]:  # creating an archive: only the archive is written
            archive = words[2] if re.fullmatch(r"-?[a-zA-Z]*f[a-zA-Z]*", flags) and not words[2].startswith("-") else None
            if archive:
                return f"{archive} {redirects}".strip()
    return None


# Verbs that put a file at an operand they are given or at a name they derive from one: a bare relative word is resolved against the directory
# the command runs in only for these. Any other verb names a skills directory only by naming a path to it (`npm test` run inside one creates nothing).
WRITER_VERBS = frozenset({"touch", "cp", "mv", "install", "ln", "rsync", "scp", "tee", "mkdir", "mkfifo", "curl", "wget", "tar", "unzip", "7z", "7za", "7zz",
                          "ditto", "dd", "truncate", "zip"})
# Verbs that only list or show what they are given.
LISTERS = frozenset({"eza", "exa", "lsd", "ls", "tree", "bat", "batcat", "less", "more", "stat", "file", "du", "wc", "head", "tail", "nl"})
# `git <sub>` that puts files at a new place: every other verb works in a repository that is already there (an edit of files the agent keeps).
CREATING_GIT = frozenset({"clone", "init", "worktree", "submodule"})
_LISTING_UNZIP = re.compile(r"^-[A-Za-z]*[ltpvzZ][A-Za-z]*$")


def _is_target_dir_option(w: str) -> bool:
    return w == "--target-directory" or w.startswith("--target-directory=") or (w.startswith("-") and not w.startswith("--") and "t" in w[1:])


def _skills_rule_re() -> re.Pattern:
    return next(r for r in risk()["rules"] if r["id"] == "R-18")["_re"]


def _relocates(src: str, dirs: tuple[str, ...]) -> bool:
    """`mv` of something that already is a skill, command or agent: an existing path the rule names. It moves what is there and writes no new text."""
    if GLOB_OR_EXPANSION.search(src):
        return False
    rx = _skills_rule_re()
    for d in dirs or ("",):
        if not d and not os.path.isabs(os.path.expanduser(src)):
            return False
        p = normalise_path(src, d)
        if not (p and os.path.exists(p)):
            return False
        if not rx.search(p + "/" if os.path.isdir(p) else p):
            return False
    return True


def _writes_into_cwd(verb: str, words: list[str], ops: list[str]) -> bool:
    """A command that puts files in the directory it runs in without naming it: `tar -x`, `unzip`, `curl -O`, `wget`, `7z x`, `ln -s target`."""
    opts = [w for w in words[1:] if w.startswith("-")]
    if verb == "tar":
        extract = any(re.fullmatch(r"-?[A-Za-z]*x[A-Za-z]*", w) for w in words[1:2]) or any(w in ("-x", "--extract", "--get") for w in opts) or \
            any(re.fullmatch(r"-[A-Za-z]*x[A-Za-z]*", w) for w in opts)
        return extract and not any(w in ("-C", "--directory") or w.startswith(("--directory=", "-C")) for w in words[1:])
    if verb == "unzip":
        return not any(_LISTING_UNZIP.match(w) or w == "-d" or w.startswith("-d") for w in opts)
    if verb == "curl":
        return any(w in ("--remote-name", "--remote-name-all") or re.fullmatch(r"-[A-Za-z]*O[A-Za-z]*", w) for w in opts)
    if verb == "wget":
        return not any(w in ("--spider", "--output-document", "--directory-prefix") or w.startswith(("--output-document=", "--directory-prefix=", "-O", "-P"))
                       or re.fullmatch(r"-[A-Za-z]+[OP].*", w) for w in opts)
    if verb in ("7z", "7za", "7zz"):
        return len(words) > 2 and words[1] in ("x", "e") and not any(w.startswith("-o") for w in opts)
    if verb == "ln":
        return len(ops) == 1
    return False


def creation_view(bare: str, view: str | None, calls: list[shellnorm.GitCall], dirs: tuple[str, ...] = ("",)) -> str | None:
    """What a command CREATES, for the `creates_only` rule (R-18): `files_view` narrowed by what the verb does with its operands.

    A removal creates nothing (`rm -r ~/.claude/skills/old/`), neither does a lister or a git verb that works in the repository that is there (`git -C
    ~/.agents/x pull`). `mv` creates only at its destination (the last operand, or the `-t` directory), and nothing when every source is a skill
    that already exists (retiring or renaming one). `git clone` creates its destination, or a directory under the current one when it names none, so
    the current directory and every `-C` directory count. `tar -x`, `unzip`, `curl -O`, `wget`, `7z x` and `ln -s target` put files in the directory
    they run in, which counts. A verb that is not in WRITER_VERBS names a skills directory only by naming a path to it: a bare word is not resolved
    against the current directory (`npm test` inside a skill is not a creation). Anything else is `files_view` (None: it may write any operand)."""
    words = system_bare(bare).split(" ")
    if words[0].rsplit("/", 1)[-1] == "rtk" and len(words) > 1:
        words = words[1:]   # `rtk ls x` is `ls x`
    verb = words[0].rsplit("/", 1)[-1]
    ops = operands(words[1:])
    redirects = " ".join(REDIRECT_TARGET.findall(bare))
    if verb in NON_CREATING or verb in LISTERS:
        return redirects
    if verb == "zip":
        return f"{ops[0]} {redirects}".strip() if ops else redirects   # the archive is written, what it holds is read
    if verb == "mv" and ops and not any(_is_target_dir_option(w) for w in words[1:]):
        if len(ops) > 1 and all(_relocates(s, dirs) for s in ops[:-1]):
            return redirects
        return f"{ops[-1]} {redirects}".strip()
    clones = [c for c in calls if c.sub == "clone"]
    if clones:
        return " ".join([*(w for w in ops if w != "clone"), ".", *(d for c in clones for d in c.dirs), redirects]).strip()
    if verb == "git" and calls and not any(c.sub in CREATING_GIT for c in calls):
        return redirects
    if verb == "unzip" and any(_LISTING_UNZIP.match(w) for w in words[1:]):
        return redirects   # -l, -t, -p, -v, -z: it lists, tests or prints
    if _writes_into_cwd(verb, words, ops):
        return " ".join([*([view] if view is not None else [*ops, redirects]), "."]).strip()
    if view is None and verb not in WRITER_VERBS:
        rx = _skills_rule_re()
        named = [w for w in words[1:] if w and not REDIRECT_WORD.match(w) and not REDIRECT_OP.match(w)
                 and (rx.search(w) or _operand_value(w.strip("\"'")).startswith(("/", "~", "$", "@")))]
        return " ".join([*named, redirects]).strip()
    return view


def _raise_to(a: Assessment, other: Assessment) -> None:
    """Let the more severe of two assessments of the same call decide the level, keep both sets of blocks."""
    for inv in other.blocked:
        if inv not in a.blocked:
            a.blocked.append(inv)
    if LEVELS[other.level] > LEVELS[a.level]:
        a.level, a.rule_id, a.rationale, a.environment = other.level, other.rule_id, other.rationale, other.environment
        a.reversibility, a.blast_radius, a.data_sensitivity = other.reversibility, other.blast_radius, other.data_sensitivity


_QUOTES = re.compile(r"[\x27\x22\\]")
_EXPANSION = re.compile(r"\$\{[^}]*\}|\$\([^)]*\)\)?|`[^`]*`|\$[A-Za-z_]\w*")


def text_views(text: str) -> list[str]:
    """Every reading of one text that a `match_on: raw` pattern is tried on (the single choke point for spelling tricks): the folded text; the
    text with every quote and backslash removed (`'JADE_HOME'=x`, `JADE_HO""ME=x`); that with every expansion deleted (`JADE_HO${E}ME=x`, an
    expansion that expands to nothing); and the shell's own decoding (`$'\\x4aADE_HOME'`, braces) from `shellnorm.skeleton`. The unquoted views keep
    the words apart, so a pattern that needs a boundary still sees one."""
    f = fold(text.replace("\x00", ""))
    stripped = _QUOTES.sub("", f)
    views = [f, stripped, _EXPANSION.sub("", stripped)]
    try:
        views.append(shellnorm.skeleton(f))
    except Exception:  # noqa: BLE001, S110 - a text the decoder cannot read is still matched in its other readings
        pass
    return list(dict.fromkeys(views))


_GLOB_WORD_SPLIT = re.compile(r"[\s;&|()<>\x27\x22`=:,]+")
_GLOB_CLASS = re.compile(r"\[[^\]]*\]")
MIN_GLOB_LITERALS = 6   # `*.key`, `p*`, `*` and `*seal*` are ordinary arguments; `permit-s*` and `*seal.key` name one file in practice (a word with no `-` or `.` needs two more)


def _mentions_state_home(views: list[str]) -> bool:
    """The command names JADE's state directory (the default `~/.local/share/jade`, a `$JADE_HOME` reference or the directory `JADE_HOME` holds here)."""
    marks = ["share/jade", "JADE_HOME", os.environ.get("JADE_HOME") or ""]
    return any(m and m in v for v in views for m in marks)


def _names_by_glob(names: list[str], views: list[str], relaxed: bool | None = None) -> bool:
    """True when a word of the command is a shell glob that would match one of `names` (a file an invariant protects) and holds at least
    `MIN_GLOB_LITERALS` literal characters: `cat ~/.local/share/jade/permit-s*`, `*seal.key`, `permit-seal.k?y`, `permit-[s]eal.key`. Brace lists are already
    expanded in one of the views. Only the last path component is matched; a directory glob (`*/permit-seal.key`) already contains the name.

    A command that also names JADE's state directory (`cat ~/.local/share/jade/*.key`, `cd $JADE_HOME && cat p*`) is the place the threshold is for: there a glob that
    matches the file counts whatever its length, because the directory says which `*` is meant (`jade ledger stats` is the allowed way to read that directory)."""
    if relaxed is None:
        relaxed = _mentions_state_home(views)
    for view in views:
        for word in _GLOB_WORD_SPLIT.split(view):
            comp = word.rsplit("/", 1)[-1]
            if not comp or len(comp) > 200 or not re.search(r"[*?\[]", comp):
                continue
            literal = re.sub(r"[*?\[\]]", "", _GLOB_CLASS.sub("", comp))
            if not relaxed and (len(literal) < MIN_GLOB_LITERALS or not re.search(r"[-.]", literal) and len(literal) < MIN_GLOB_LITERALS + 2):   # `**permit**` in prose is not a file
                continue
            if any(fnmatch.fnmatchcase(n, comp) for n in names):
                return True
    return False


def _glob_prefix(pattern: str) -> str:
    """The leading directory part of a glob that holds no wildcard: `/a/b/*.key` -> `/a/b`, `**/x` -> ``, `a/b/c` -> `a/b`."""
    comps = pattern.split("/")
    idx = next((i for i, c in enumerate(comps) if re.search(r"[*?\[{]", c)), len(comps) - 1)
    return "/".join(comps[:idx]) or ("/" if pattern.startswith("/") else "")


def _grep_glob_keeps_the_key(names: list[str], globs: list[str]) -> bool:
    """Whether a Grep `glob` filter leaves the key file in the search. Like ripgrep -g: a word that starts with `!` excludes what it matches, any other
    word restricts the search to what it matches; with no filter, or only exclusions that miss the key, the key is searched."""
    words = [w for g in globs for w in g.split()]
    neg = [w[1:] for w in words if w.startswith("!") and len(w) > 1]
    pos = [w for w in words if not w.startswith("!")]
    if any(_names_by_glob(names, text_views(w), relaxed=True) for w in neg):
        return False
    return any(_names_by_glob(names, text_views(w), relaxed=True) for w in pos) if pos else True


def _secret_read(seg: str) -> dict | None:
    """The `reads: true` rule (R-30) when a read-only command names a credential file. A command that may write is classified by `classify_segment`, where
    the same rule sits among the others; this is the one place a command that only reads is looked at for what it reads."""
    rule = next((r for r in risk()["rules"] if r.get("reads")), None)
    if rule is None:
        return None
    bare = _bare(seg)
    return rule if any(rule["_re"].search(t) for t in dict.fromkeys((seg, bare, _pathfold(bare)))) else None


_CREDENTIAL_DIR = re.compile(r"(?i)(?:^|/)\.(?:aws|docker|kube|ssh|open-vault)/*$|(?:^|/)\.config/gh/*$")   # a directory a recursive Grep would read credentials from


def _secret_path(tool: str, tool_input: dict, cwd: str) -> dict | None:
    """R-30 for a Read, Grep or Glob call: a path, a glob or a Glob pattern that names a credential file, also after `~`, `..` and symlinks are resolved."""
    rule = next((r for r in risk()["rules"] if r.get("reads")), None)
    if rule is None:
        return None
    targets, globs = read_targets(tool, tool_input)
    base = [t for t in targets if t.strip()]
    texts = [*base, *globs, *(f"{b.rstrip('/')}/{g}" for b in base for g in globs)]
    resolved = [n for t in base if (n := normalise_path(t, cwd))]
    texts += resolved
    if tool == "Grep" and any(_CREDENTIAL_DIR.search(t) for t in [*base, *resolved]):
        return rule   # a Grep rooted at the credential directory itself (`~/.aws`) reads every file in it; one rooted at an ancestor stays accepted (T22)
    return rule if any(rule["_re"].search(v) for t in texts for v in dict.fromkeys((t, _pathfold(t)))) else None


def _assess_read(tool: str, tool_input: dict, cwd: str) -> Assessment:
    """Read, Grep and Glob are gated for the permit seal key only (SI-10, W-70): the call is blocked when it names the key file, resolves to it (a symlink or a
    hard link), searches the directory that holds it without a filter that leaves it out, or holds a glob that would match its name (six or more literal
    characters anywhere, any length when the search directory is the one that holds the key). Every other read, the other files of the JADE home included
    (ledger, logs, pins), and every read of a repository's own files stays low: `jade ledger stats` is the way to read the ledger. A path or glob that names
    another credential file (`.env`, `~/.aws/credentials`, a private key, `*.pem`) is medium (R-30, W-55): the hook journals it and does not gate it."""
    a = Assessment(level="low", rule_id="R-READ", rationale="Read-only tool call; only the permit seal key is blocked (SI-10) and a credential file is recorded (R-30)", blast_radius="file")
    subject = tool_subject(tool, tool_input)
    _seal(a, tool, subject, cwd, subject)
    inv = next((i for i in safety()["invariants"] if i["id"] == "SI-10"), None)
    if inv is None or not _reads_the_seal_key(inv, tool, tool_input, cwd):
        if rule := _secret_path(tool, tool_input, cwd):
            _raise_to(a, _from_rule(rule, "local"))
            a.recorded = True
        return a
    a.level, a.rule_id, a.rationale = "critical", "SI-10", "Read of the permit seal key"
    a.reversibility, a.blast_radius, a.data_sensitivity = "irreversible", "service", "secret"
    a.blocked.append("SI-10")
    a.hold_key = a.plan_hash
    return a


def _reads_the_seal_key(inv: dict, tool: str, tool_input: dict, cwd: str) -> bool:
    targets, globs = read_targets(tool, tool_input)
    names = list(inv.get("block_globs", []))
    rx = inv.get("_re")
    # the file name in the text of a path or a glob (not in a Grep pattern), also behind quoting and `..` segments
    if rx is not None and any(rx.search(v) for t in (*targets, *globs) for v in text_views(_pathfold(t))):
        return True
    keydirs = seal_key_dirs()
    keys = [os.path.join(d, n) for d in keydirs for n in names[:1]]
    roots = [normalise_path(t, cwd) for t in targets] or [normalise_path(cwd, cwd) if cwd else real(os.getcwd())]
    for n in filter(None, roots):
        if n in keys:
            return True
        for key in keys:
            try:
                if os.path.exists(n) and os.path.exists(key) and os.path.samefile(n, key):   # a hard link, or a symlink the resolution missed
                    return True
            except (OSError, ValueError):
                pass
    for root in filter(None, roots):
        if tool == "Grep" and is_seal_key_dir(root) and _grep_glob_keeps_the_key(names, globs):
            return True   # a recursive search of the directory that holds the key, with no file filter that leaves it out
        for g in globs:
            prefix = _glob_prefix(g)
            where = normalise_path(prefix, root) if prefix else root   # the directory the pattern searches: the key's own directory relaxes the threshold
            if _names_by_glob(names, text_views(g), relaxed=is_seal_key_dir(where)):
                return True
    return False


def _scan_content(a: Assessment, tool_input: dict, read_only: bool = False, cwd: str = "") -> None:
    """Invariants marked `scan_content` (SI-02, SI-05) apply to the text a non-shell call carries: file content, new text, any argument.

    The text is folded first (`util.fold`). An invariant with `scan_read_only: false` skips a tool whose name only reads, and one with `scan_skip_keys`
    skips those keys (a page URL or a search term is not a bind address)."""
    invs = [i for i in safety()["invariants"] if i.get("scan_content") and "_re" in i and i["id"] not in a.blocked
            and not (read_only and i.get("scan_read_only") is False)]
    targets = None
    for inv in invs:
        skip = frozenset(str(k).lower() for k in inv.get("scan_skip_keys", []))
        if "_paths_re" in inv:   # only a write into one of these files counts (SI-11: a shell profile or an environment file)
            if targets is None:
                targets = [x for p in dict.fromkeys([*write_paths(tool_input), *_path_args(tool_input)]) for x in (p, normalise_path(p, cwd)) if x]
            if not any(inv["_paths_re"].search(t) for t in targets):
                continue
            rx = inv.get("_cre") or inv["_re"]
            if any(rx.search(v) for t in content_strings(tool_input, "", skip) for v in text_views(t)):
                a.blocked.append(inv["id"])
            continue
        if any(inv["_re"].search(fold(t)) for t in content_strings(tool_input, "", skip)):
            a.blocked.append(inv["id"])


_DIR_REPLACING_VERBS = frozenset({"rm", "rmdir", "mv", "unlink", "shred", "srm"})


def _config_roots() -> tuple[str, ...]:
    """The real paths of the config directory in force and of the default one (`~/.config/jade`): SI-12 guards both."""
    return tuple(dict.fromkeys(os.path.realpath(str(p)) for p in (paths.config_dir(), os.path.expanduser("~/.config/jade"))))


def _si12_hit(path: str, base: str, roots: tuple[str, ...], parent_too: bool = False) -> bool:
    """True when `path` (relative to `base`) is a config directory, a path inside one, or, for a verb that replaces a directory, a parent of one."""
    if not path or "\x00" in path:
        return False
    real = os.path.realpath(os.path.join(base or os.getcwd(), os.path.expanduser(path)))
    return any(real == r or real.startswith(r + os.sep) or (parent_too and r.startswith(real.rstrip(os.sep) + os.sep)) for r in roots)


HO_WIRING, HO_INSTALL, HO_SETTINGS, HO_STATE, HO_MOD, HO_R17, HO_TERMINAL = (
    "HO-wiring", "HO-checkout-install", "HO-settings-shadow", "HO-state-path", "HO-mod", "HO-R17", "HO-terminal")
_STATE_LITERAL = re.compile(r"\.config/jade(?![\w.-])|\.local/share/jade(?![\w.-])|jade\.sqlite")
_INSTALL_CMD = re.compile(r"\b(?:uv\s+tool\s+(?:install|upgrade|reinstall)|uv\s+pip\s+install|pip3?\s+install|pipx\s+(?:install|upgrade|reinstall)|python3?\s+-m\s+pip\s+install)\b")
_UV_TOOL_JADE = re.compile(r"\buv\s+tool\s+(?:uninstall|upgrade|reinstall|install)\b[^;&|]*\bjade")   # R-13: changes or removes the installed JADE tool itself, whatever it installs from
_SETTINGS_FILE = re.compile(r"(?:^|/)\.claude/settings(?:\.local)?\.json$")
_SETTINGS_DISABLE = re.compile(r"disableAllHooks|allowManagedHooksOnly|JADE_(?:GATE_MODE|POLICY_DIR|CONFIG|HOME|REGISTRY_DIR)|\"hooks\"\s*:\s*(?:\{\s*\}|null)", re.I)
TERMINAL_TOOLS = frozenset({"mcp__terminal__run_in_terminal"})


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def _wiring_roots() -> tuple[str, ...]:
    """The real paths of what the running JADE is made of and reads: its environment, the uv tool directory, the launchers, the hook files, the state and
    config directories, and the shipped policy and registry directories unless they sit in a checkout (a checkout is plain high work, docs/12 decision 8)."""
    out = [os.path.expanduser(p) for p in ("~/.local/share/uv/tools/jade-decision-governance", "~/.local/share/uv/tools/jade", "~/.local/bin/jade", "~/.local/bin/jev",
                                           "~/.claude/settings.json", "~/.claude/settings.local.json", "~/.claude/hooks", "~/.local/share/jade")]
    prefix = _real(sys.prefix)
    if len([x for x in prefix.split(os.sep) if x]) >= 3:   # not /usr or /usr/local of a system Python
        out.append(prefix)
    out.append(os.environ.get("JADE_HOME") or os.path.expanduser("~/.local/share/jade"))
    for d in (paths.shipped_policy_dir(), paths.registry_dir()):
        if not _is_checkout(os.path.dirname(_real(str(d)))):
            out.append(str(d))
    out.extend(_config_roots())
    return tuple(dict.fromkeys(_real(p) for p in out))


def _mod_roots() -> tuple[str, ...]:
    return tuple(_real(p) for p in ("~/.claude/dev-mods", "~/.claude/plugins", "~/.claude/mods"))


def _written_text(tool_input: dict) -> str:
    """The new text a Write, Edit, MultiEdit or NotebookEdit call carries ('' when it carries none the gate can read)."""
    parts = [str(tool_input[k]) for k in ("content", "new_string", "new_source") if isinstance(tool_input.get(k), str)]
    for e in tool_input.get("edits") or []:
        if isinstance(e, dict) and isinstance(e.get("new_string"), str):
            parts.append(e["new_string"])
    return "\n".join(parts).strip()


_SCRIPT_READ_MAX = 1 << 20


def _script_names_state(f: str, dirs: tuple[str, ...], cwd: str) -> bool:
    """Does the script file a command runs (at most 1 MiB, a regular file, resolved in a directory the call may run in) name the JADE state paths?"""
    for d in (dirs or ("",)):
        p = os.path.realpath(os.path.join(d or cwd or os.getcwd(), os.path.expanduser(f)))
        try:
            if os.path.isfile(p) and os.path.getsize(p) <= _SCRIPT_READ_MAX:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    if _STATE_LITERAL.search(fh.read(_SCRIPT_READ_MAX)):
                        return True
        except OSError:
            continue
    return False


def _ho_add(a: Assessment, ident: str) -> None:
    if ident not in a.ho_ids:
        a.ho_ids.append(ident)


def _ho_tool_paths(a: Assessment, tool_input: dict, subjects: list[str], cwd: str) -> None:
    """Human-only classes of a Write/Edit-style or MCP call, from the paths it names."""
    wroots, mroots = _wiring_roots(), _mod_roots()
    text = None
    for s in subjects:
        if not s.strip():
            continue
        if _si12_hit(s, cwd, wroots):
            _ho_add(a, HO_WIRING)
        if _si12_hit(s, cwd, mroots):
            _ho_add(a, HO_MOD)
        if _SETTINGS_FILE.search(os.path.realpath(os.path.join(cwd or os.getcwd(), os.path.expanduser(s)))):
            text = _written_text(tool_input) if text is None else text
            if not text or _SETTINGS_DISABLE.search(text):   # unreadable text counts: it could be anything
                _ho_add(a, HO_SETTINGS)


def _ho_segment(a: Assessment, seg: str, dirs: tuple[str, ...], cwd: str, wroots: tuple[str, ...], mroots: tuple[str, ...]) -> None:
    """Human-only classes of one shell segment that is not read-only."""
    toks, parent_too = _segment_tokens(seg)
    bare = _bare(seg)
    install = bool(_INSTALL_CMD.search(bare))
    if _UV_TOOL_JADE.search(bare):
        _ho_add(a, HO_WIRING)
    for tok in toks:
        for d in (dirs or ("",)):
            base = d or cwd
            if _si12_hit(tok, base, wroots, parent_too):
                _ho_add(a, HO_WIRING)
            if _si12_hit(tok, base, mroots, parent_too):
                _ho_add(a, HO_MOD)
            real = os.path.realpath(os.path.join(base or os.getcwd(), os.path.expanduser(tok))) if tok else ""
            if real and _SETTINGS_FILE.search(real):
                _ho_add(a, HO_SETTINGS)   # a shell write of a settings file: the text is not the gate's to read
            if install and real and os.path.isdir(real) and _is_checkout(real):
                _ho_add(a, HO_INSTALL)


def _si12_tool_paths(a: Assessment, subjects: list[str], cwd: str) -> None:
    if paths.level() is None or "SI-12" in a.blocked:
        return
    roots = _config_roots()
    if any(_si12_hit(s, cwd, roots) for s in subjects):
        a.blocked.append("SI-12")


def _segment_tokens(seg: str) -> tuple[list[str], bool]:
    """The words of a shell segment that may be paths it writes (the write view of the segment, or the whole segment when it may write any operand), cleaned
    of quotes and of an `--opt=` prefix, and whether its verb replaces a directory (rm, mv, find -delete ...): a parent of the operand then counts too."""
    bare = _bare(seg)
    words = bare.split(" ")
    verb = words[0].rsplit("/", 1)[-1]
    view = files_view(bare)
    text = bare if view is None else view
    parent_too = verb in _DIR_REPLACING_VERBS or (verb == "find" and "-delete" in words)
    toks = []
    for tok in text.split(" "):
        tok = tok.rpartition("=")[2] if "=" in tok and not tok.startswith("=") else tok
        tok = tok.strip("'\"`,;()")
        if tok and not tok.startswith("-") and tok not in (">", ">>"):
            toks.append(tok)
    return toks, parent_too


def _si12_segment(seg: str, dirs: tuple[str, ...], cwd: str, roots: tuple[str, ...]) -> bool:
    """SI-12 for one shell segment that is not read-only: does it write the config directory (or replace it, or a parent of it)?"""
    toks, parent_too = _segment_tokens(seg)
    return any(_si12_hit(tok, d or cwd, roots, parent_too) for tok in toks for d in (dirs or ("",)))


def _assess_write(tool: str, tool_input: dict, cwd: str) -> Assessment:
    subjects = write_paths(tool_input) or [""]
    best_rule, envs = None, []
    for subject in subjects:
        norm = normalise_path(subject, cwd)
        cands = [subject, *([norm] if norm and norm != subject else [])]
        for c in cands:
            r = _first_match(c, dirs=(cwd,), whole=True)
            if best_rule is None or LEVELS[r["level"]] > LEVELS[best_rule["level"]]:
                best_rule = r
            envs.append(loader.environment_of(risk(), c))
    env = "production" if "production" in envs else "staging" if "staging" in envs else "local"
    a = _from_rule(best_rule, env)
    if env == "production" and LEVELS[a.level] < LEVELS["high"]:
        a.level, a.rule_id, a.rationale = "high", "R-ENV", "Write under a production path"
    subject = "\n".join(subjects)
    # A relative path names a different file in each directory, so the permit binds the working directory too; an absolute or `~` path does not depend on it.
    relative = any(x.strip() and not os.path.isabs(os.path.expanduser(x)) for x in subjects)
    _seal(a, tool, subject, cwd if relative else "", subject)
    _si12_tool_paths(a, [x for x in subjects if x.strip()], cwd)
    _ho_tool_paths(a, tool_input, subjects, cwd)
    return a


def snake(name: str) -> str:
    """`setSetting` -> `set_setting`, `SetSetting` -> `set_setting`: the verb heuristics were written for snake and kebab names.
    A single capitalised word (`Move`, `Shell`) is left as it is."""
    if not re.search(r"[a-z0-9][A-Z]", name):
        return name
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _tool_rule(tool: str, command_decides: bool) -> dict:
    """First `mcp_tools` rule that names the tool (as spelled, or with a camelCase name read as snake_case); a `generic` name heuristic is
    skipped when a command string decides."""
    head, sep, last = tool.rpartition("__")
    forms = list(dict.fromkeys((tool, head + sep + snake(last))))
    for m in risk().get("mcp_tools", []):
        if any(re.search(m["match"], f) for f in forms) and not (command_decides and m.get("generic")):
            return m
    return {"id": "R-99", "level": "low", "rationale": "Tool with no mutation rule", "blast_radius": "service"}


def tool_coverage(tool: str) -> str | None:
    """None when a decision covers the tool: a built-in tool the gate names or is decided not to gate, or an MCP tool a specific `mcp_tools` rule names.
    Otherwise why not: the id of the generic rule that classifies an MCP tool by its name alone (M-07 gated by a verb in the name, M-99 medium and
    not gated), or `ungated` for a built-in tool nothing names. The tool name is all this reads."""
    if not is_mcp(tool):
        return None if tool in GATED_BUILTIN or tool in UNGATED_BUILTIN else "ungated"
    rule = _tool_rule(tool, command_decides=False)
    return rule["id"] if rule.get("generic") else None


def _rule_assessment(rule: dict) -> Assessment:
    return Assessment(level=rule["level"], rule_id=rule["id"], rationale=rule["rationale"], environment="production" if rule["level"] == "critical" else "local",
                      reversibility=rule.get("reversibility", "reversible"), blast_radius=rule.get("blast_radius", "service"))


def _path_args(tool_input: dict) -> list[str]:
    out = [str(tool_input[k]) for k in (*WRITE_KEYS, *PATH_KEYS) if isinstance(tool_input.get(k), str) and tool_input[k].strip()]
    return [p for p in out if len(p) <= 4096]


def _shell_input_strings(tool: str, tool_input: dict) -> list[str]:
    """Arguments the tool types or pipes into a shell or another tool (risk.yaml mcp_shell_inputs)."""
    out: list[str] = []
    for m in risk().get("mcp_shell_inputs", []):
        if re.search(m["match"], tool):
            for key in m.get("keys", []):
                vals = tool_input if key == "*" else {key: tool_input.get(key)}
                out.extend(t for t in content_strings(vals) if t.strip() and len(t) <= 16384)
    return out


def _assess_tool(tool: str, tool_input: dict, cwd: str) -> Assessment:
    """MCP tools and the built-in tools that carry no command line: classified by name, by the command they carry, by the paths they
    write and by the text they type into a shell; the most severe reading decides."""
    cmd = mcp_command(tool_input) if is_mcp(tool) else None
    rule = _tool_rule(tool, command_decides=cmd is not None)
    if rule["id"] == "M-99" and isinstance(tool_input.get("action"), str) and re.fullmatch(r"[\w-]{1,40}", tool_input["action"]):
        # one tool, many verbs (`herd processes {action: start}`): the action is the name's verb
        by_action = _tool_rule(f"{tool}__{tool_input['action']}", command_decides=cmd is not None)
        rule = rule if by_action["id"] == "M-99" else by_action
    if cmd is not None:
        a = _assess_shell(tool, cmd, cwd)
        if not rule.get("generic"):
            _raise_to(a, _rule_assessment(rule))
    else:
        a = _rule_assessment(rule)
        subject = canonical(tool_input)
        _seal(a, tool, subject, "", f"{tool} {subject}")
    if rule.get("paths"):
        _si12_tool_paths(a, list(_path_args(tool_input)), cwd)
        _ho_tool_paths(a, tool_input, list(_path_args(tool_input)), cwd)
        for p in _path_args(tool_input):
            norm = normalise_path(p, cwd)
            hit = next((r for r in (_path_rule(p, dirs=(cwd,), whole=True), _path_rule(norm, dirs=(cwd,), whole=True)) if r), None)
            if hit:
                _raise_to(a, _from_rule(hit, loader.environment_of(risk(), norm or p)))
            if loader.environment_of(risk(), norm or p) == "production":
                _raise_to(a, Assessment(level="high", rule_id="R-ENV", rationale="Write under a production path", environment="production"))
    for text in _shell_input_strings(tool, tool_input):
        _raise_to(a, _assess_shell(tool, text, cwd))
    return a


IDENTITY_FILE = re.compile(r"(?<![\w-])(?:-i|-oIdentityFile=?|-o IdentityFile[= ]?|IdentityFile[= ])\s*(\S+)")
REMOTE_SHELL_VERBS = ("ssh", "scp", "sftp")


def _use_identity(seg: str) -> str:
    """An ssh, scp or sftp segment with the file named by `-i` (or `-o IdentityFile=`) replaced by `$KEY`: the program reads that key to log in
    and never prints it, so a key path there is not a key mutation (R-04). A key named anywhere else on the line (`scp ~/.ssh/id_rsa host:`,
    `cat key`) is untouched, and the remote-shell rule R-14 still rates the call itself."""
    bare = PREFIX.sub("", seg, count=1).strip()
    return IDENTITY_FILE.sub("-i $KEY", seg) if bare.split(" ", 1)[0].rsplit("/", 1)[-1] in REMOTE_SHELL_VERBS else seg


def classify_segment(seg: str, dirs: tuple[str, ...], proxied: bool = False, marks: tuple[str, ...] = ()) -> tuple[dict, str]:
    """The rule one simple command (a segment of `shellnorm.segments_cwd`) matches, and the command with launcher prefixes and git options gone.

    The command is looked at as written and with its operands made absolute for every directory it may run in; a `files_only` rule (R-13, R-16)
    reads only what the command writes (`files_view`). `marks` are marker prefixes (`blind:script-file `, `fetch:exec `) that the caller found for this segment and that rules
    read together with its text."""
    real_bare = _bare(seg)
    seg = _use_identity(seg)
    bare, calls = _analyse(seg)
    hooks_off = any("core.hookspath" in d for c in calls for d in c.danger)   # `git -c core.hooksPath=...` turns the pre-push guard off for this command (R-17)
    texts = [*([HOOKS_BYPASS + bare] if hooks_off else []), seg, bare, _pathfold(bare), *([UNSAFE_GIT + bare] if any(c.danger for c in calls) else []),
             *([EGRESS_MARK + bare] if egress.sends_out(bare, proxied or bool(egress.PROXY_ENV.search(seg))) else []), *(m + bare for m in marks)]
    view = files_view(bare)  # what a `files_only` rule may look at: None = the whole segment
    cview = creation_view(bare, view, calls, tuple(dirs) or ("",))  # what a `creates_only` rule may look at
    seg_best = None
    dirs_t = tuple(dirs) or ("",)
    pairs = [(t, view if view is not None else _program_basename(t)) for t in dict.fromkeys(texts)]
    if view:
        pairs.append((bare, _pathfold(view)))
    for text, files in pairs:
        alt = {"R-18": cview if cview is not None else files} if cview != view else None
        rule = _first_match(text, files, alt, dirs_t)
        if seg_best is None or LEVELS[rule["level"]] > LEVELS[seg_best["level"]]:
            seg_best = rule
    abs_ops = _absolute_operands(seg, dirs)
    abs_files = abs_ops if view is None else _absolute_operands("x " + view, dirs) if view else ""
    abs_pkg = _absolute_operands(seg if view is None else "x " + view, dirs, package=True) if view != "" else ""
    abs_create = abs_files if cview == view else abs_ops if cview is None else _absolute_operands("x " + cview, dirs) if cview else ""
    if (rule := _path_rule(abs_ops, abs_files, {"R-16": abs_pkg, "R-18": abs_create}, dirs_t)) and LEVELS[rule["level"]] > LEVELS[seg_best["level"]]:
        seg_best = rule
    return seg_best, real_bare


def _call_dir(call: shellnorm.GitCall, dirs: tuple[str, ...], cwd: str) -> str | None:
    """The directory a git call runs in: the directory of the segment, then each `-C` in turn. None when one is written with a variable or a substitution."""
    d = (dirs[0] if dirs else "") or cwd
    for c in call.dirs:
        if re.search(r"[$`\x06]", c):
            return None
        c = os.path.expanduser(c)
        d = c if os.path.isabs(c) else os.path.join(d or os.getcwd(), c)
    return d


def hidden(text: str, cwd: str = "") -> tuple[list[str], frozenset[str]]:
    """(commands, files) the text runs without writing them on the command line (W-31): the expansion of a git alias that a config file defines, and the text of a
    script that the same command writes and then runs. Each is returned as a command for the classifier (and for SI-08) to read as if it had been typed; `files` are the
    scripts whose text was read, so running one of them is not a blind spot. A command with no non-built-in git subcommand starts no process."""
    extra, known = _hidden(text, cwd, os.environ.get("HOME", ""), os.environ.get("XDG_CONFIG_HOME", ""))
    return [*extra, *samecmd.extras(text, cwd)], known   # not cached: it reads the patch files the command names, as they are now (W-37)


@lru_cache(maxsize=8)
def _hidden(text: str, cwd: str, _home: str, _xdg: str) -> tuple[tuple[str, ...], frozenset[str]]:
    """`hidden`, once per command: the classifier and the SI-08 check both ask, and the git lookups behind it are bounded by the hook's time budget, not doubled by it.
    The two environment values are in the key because they decide which config git reads."""
    extra, known = blind.written_scripts(text)
    extra = list(extra)
    if "git" in text:
        aliases = gitalias.Aliases()
        for seg, dirs in shellnorm.segments_cwd(text, cwd):
            if is_read_only(seg):
                continue
            for call in git_calls(seg):
                if not gitalias.candidate(call.sub) or (d := _call_dir(call, dirs, cwd)) is None:
                    continue
                if (expansion := aliases.expand(call.sub, call.args, d)) is not None:
                    extra.append(f"cd {shlex.quote(d)} && {expansion}" if d else expansion)
    return tuple(extra), known


def _computed_write(bare: str) -> bool:
    """A segment that writes a path relative to the working directory: a redirect, the destination of `cp`, an output option, an in-place edit, a writer verb."""
    view = files_view(bare)
    if view is not None:
        return blind.relative_words(view)
    words = bare.split(" ")
    verb = words[0].rsplit("/", 1)[-1]
    if verb == "git":
        return False
    if verb in WRITER_VERBS and verb not in ("curl", "wget"):
        return blind.relative_words(" ".join(operands(words[1:])))
    return shellnorm.verb_effects(verb, words[1:])[0] and blind.relative_words(" ".join(operands(words[1:])))


def _assess_shell(tool: str, subject: str, cwd: str) -> Assessment:
    best: dict | None = None
    active: list[str] = []
    keys: list[str] = []
    gated_keys: list[str] = []
    text_in = subject.replace("\x00", "")  # a shell ignores a NUL byte in a command; the plan hash still covers the text as written
    secret_read = False
    proxied = bool(egress.PROXY_ENV.search(text_in))   # a proxy variable anywhere in the command routes a send in any segment (`export https_proxy=...; curl ...`)
    extra, known = hidden(text_in, cwd)
    scan = text_in + "".join("\n" + e for e in extra)   # what the command runs that it does not write: git aliases, a script it writes and runs (W-31)
    fetched: set[str] = set()   # files a curl or wget of this command writes
    computed = False   # a `cd` to a directory only the running shell knows came before this segment
    labels: list[str] = []
    si12_roots = _config_roots() if paths.level() is not None else None   # docs/12: SI-12 is active once a level is set
    si12 = False
    ho = Assessment(level="low", rule_id="", rationale="")   # collects the human-only classes of the segments
    wroots, mroots = _wiring_roots(), _mod_roots()
    for seg, dirs in shellnorm.segments_cwd(scan, cwd):
        if seg.startswith(BLIND_MARK):
            labels.append(seg[len(BLIND_MARK):].split(" ", 1)[0])
        cd = blind.cd_kind(PREFIX.sub("", seg, count=1).strip().split(" "))
        if cd:
            computed = cd == "computed"
        if is_read_only(seg):
            if sr := _secret_read(seg):
                secret_read = True
            if sr and (best is None or LEVELS[sr["level"]] > LEVELS[best["level"]]):
                best = sr   # a plain read of a credential file: medium, recorded and not gated (R-30, W-55)
            continue
        active.append(seg)
        words = _bare(seg).split(" ")
        marks: list[str] = []
        if blind.stage_kind(words[0], words[1:]) == "fetch" or words[0].rsplit("/", 1)[-1] in ("curl", "wget"):
            fetched.update(blind.downloads(words))   # a later segment that runs one of them runs fetched text (R-22)
        elif fetched and blind.runs_file(words, fetched):
            marks.append(FETCH_MARK)
        elif fetched:
            fetched.update(blind.copies_of(words, fetched))   # `mv a b; bash b`: the copy is the fetched file under another name
        seg_labels = [lab for lab in (blind.script_label(words, known), "computed-cwd" if computed and _computed_write(_bare(seg)) else None) if lab]
        labels.extend(seg_labels)
        marks.extend(f"{BLIND_MARK}{lab} " for lab in seg_labels)
        seg_best, bare = classify_segment(seg, dirs, proxied, tuple(marks))
        if best is None or LEVELS[seg_best["level"]] > LEVELS[best["level"]]:
            best = seg_best
        if si12_roots and not si12 and _si12_segment(seg, dirs, cwd, si12_roots):
            si12 = True
        _ho_segment(ho, seg, dirs, cwd, wroots, mroots)
        if HO_STATE not in ho.ho_ids and (sf := blind.script_file(words)) and _script_names_state(sf, dirs, cwd):
            _ho_add(ho, HO_STATE)
        key = _segment_key(bare)
        keys.append(key)
        if LEVELS[seg_best["level"]] >= LEVELS[permit_floor()]:
            gated_keys.append(key)
    best = best or risk()["rules"][-1]
    if labels and best["level"] == "low":   # R-31 is low, so it never wins a level comparison: nothing above low matched, and what the gate could not read is the finding
        best = next(r for r in risk()["rules"] if r["id"] == "R-31")
    a = _from_rule(best, loader.environment_of(risk(), text_in))
    a.recorded = secret_read and bool(best.get("reads"))
    if best["id"] in ("R-31", "R-32"):   # nothing higher matched: what the gate could not read is counted, by label and never by text (W-31)
        a.recorded = True
        a.blind = sorted(dict.fromkeys(labels))
    views: list[str] | None = None
    for inv in safety()["invariants"]:
        if "_re" not in inv:
            continue
        raw = inv.get("match_on") == "raw"
        src = _scratch_prefix_stripped(text_in) if inv["id"] == "SI-11" else text_in   # W-48
        if src is not text_in and src != text_in:
            inv_views = text_views(src)
        else:
            if views is None and (raw or "_qre" in inv):
                views = text_views(text_in)
            inv_views = views
        haystacks, qviews = (inv_views if raw else active + [_bare(s) for s in active]), inv_views
        if inv.get("inert_data"):   # W-71: a name in a position that only holds data (a grep pattern, a commit message, an echo argument) is not a mention
            masked = _masked(src, cwd)
            if masked != src:
                qviews = text_views(masked)
                haystacks = qviews if raw else _active_of(masked)
        # `block_quoted`: the same invariant, also read in the quoted text the segmenter blanks (an alias whose value is the whole command, `env -S "..."`)
        if any(inv["_re"].search(h) for h in haystacks) or (raw and inv.get("block_globs") and _names_by_glob(inv["block_globs"], haystacks)) \
                or ("_qre" in inv and any(inv["_qre"].search(v) for v in qviews)):
            a.blocked.append(inv["id"])
    if si12 and "SI-12" not in a.blocked:
        a.blocked.append("SI-12")
    a.ho_ids = list(ho.ho_ids)
    if HO_STATE not in a.ho_ids and any(_STATE_LITERAL.search(e) for e in extra):   # a script the command runs names the JADE state paths: what it does there is not the gate's to read
        _ho_add(a, HO_STATE)
    if a.rule_id == "R-17":
        _ho_add(a, HO_R17)
    _seal(a, tool, subject, cwd, subject)
    a.hold_key = sha256(canonical({"shell": sorted(set(gated_keys or keys))}))
    return a


# Destinations of a heredoc that a shell, a tool or the human's habit runs or sources whatever the file is called: a hidden file (`~/.zsh_exports`,
# `~/.aliases`, `~/.tmux.conf`), a script by extension, anything in a `bin` directory, the Claude settings files. SI-11's `scan_content_paths` names the
# profiles and environment files; this adds what a name cannot list. The body of such a heredoc stays visible to SI-09, SI-10 and SI-11.
_SOURCED_DEST = re.compile(r"(?i)(?:^|/)\.[^/]+$|\.(?:sh|bash|zsh|ksh|csh|fish|env|rc|profile)$|/s?bin/[^/]+$|(?:^|/)\.claude/settings[\w.-]*\.json$|(?:^|/)direnvrc$")
_CD_WORD = re.compile(r"(?:^|[\s;&|(])(?:cd|pushd)(?:\s|$)")


def _plain_destination(d: str, cwd: str, text: str, rx: re.Pattern | None) -> bool:
    """True when a heredoc written to `d` is data: an ordinary file. False for the files above, for a file that is executable or has another hard link
    (a hard link to a profile is the profile; `normalise_path` resolves symlinks only), and for a relative name in a command that also `cd`s (where it
    lands is not known here: `cd ~/.config/environment.d && cat > 10-x.conf`)."""
    if not d:
        return False
    if not os.path.isabs(os.path.expanduser(d)) and _CD_WORD.search(text):
        return False
    p = normalise_path(d, cwd)
    if (rx and rx.search(p)) or _SOURCED_DEST.search(p):
        return False
    try:
        st = os.stat(p)
    except OSError:
        return True
    return not (stat_mod.S_ISREG(st.st_mode) and (st.st_nlink > 1 or st.st_mode & 0o111))


def _masked(text: str, cwd: str) -> str:
    """The command with the data positions blanked (inert.py). A heredoc written to a file that is not plain data (`_plain_destination`: a shell
    profile, an environment file, a plist, a hidden or executable file, a hard link, a name after a `cd`) stays whole: a line such a file runs at the
    next login is an effect whichever name it holds."""
    rx = next((i["_paths_re"] for i in safety()["invariants"] if i["id"] == "SI-11" and "_paths_re" in i), None)
    return inert.mask(text, lambda d: _plain_destination(d, cwd, text, rx))


def _active_of(text: str) -> list[str]:
    """The segments of a command that are not read-only, and each without its launcher prefixes: what a pattern that is not `match_on: raw` reads."""
    segs = [seg for seg, _ in shellnorm.segments_cwd(text) if not is_read_only(seg)]
    return segs + [_bare(s) for s in segs]


def _segment_key(bare: str) -> str:
    """One simple command with launcher prefixes and git global options gone (the caller passes `_bare`), the program named without its directory,
    whitespace collapsed: `/usr/local/bin/fly  deploy` and `FOO=1 fly deploy` are the same command."""
    words = bare.split(" ")
    words[0] = words[0].rsplit("/", 1)[-1]
    return " ".join(w for w in words if w)


# W-48: the one harmless shape SI-11 used to block. A prefix assignment of JADE_HOME or JADE_CONFIG that points at a scratch directory (`$(mktemp -d)`, a path under
# /tmp, /private/tmp or /var/folders, or empty) in front of a test or lint run or a `jade`/`jev` call reaches only that command's children, never the hook process, and
# is what the skill tells an agent to type for a synthetic payload. JADE_GATE_MODE and JADE_POLICY_DIR are never exempt, nor is `export`, `env`, a real path, a `..`
# segment, a variable, or a program outside the list; SI-09 still sees `jade permit approve` and the other human-only commands in the same command.
_SCRATCH_VALUE = r"""(?:""|''|"?\$\(mktemp -d(?: [^)\n;&|]*)?\)"?|(?:/private)?/(?:tmp|var/folders)/[\w./+-]*|)"""
_SCRATCH_PREFIX = re.compile(
    r"(?P<lead>(?:\A|[;&|\n])[ \t]*)(?P<asg>(?:(?:JADE_(?:HOME|CONFIG)=" + _SCRATCH_VALUE + r"|JADE_SOURCE=\w+)[ \t]+)+)"
    r"(?P<prog>(?:uv[ \t]+run[ \t]+(?:-\S+[ \t]+)*)?(?:python3?[ \t]+-m[ \t]+)?(?:pytest|ruff|jade|jev)(?![\w./-]))")


def _scratch_prefix_stripped(text: str) -> str:
    """`text` with the exempt scratch prefix assignments removed; the rest, SI-11's own pattern included, still reads the result."""
    def drop(m: re.Match) -> str:
        return m.group(0) if ".." in m.group("asg") else m.group("lead") + m.group("prog")
    return _SCRATCH_PREFIX.sub(drop, text)


def assess(tool: str, tool_input: dict, cwd: str = "") -> Assessment:
    """Classify one tool call. Raises on malformed policy; callers decide how to fail."""
    if tool in WRITE_TOOLS:
        a = _assess_write(tool, tool_input, cwd)
    elif tool in READ_TOOLS:
        return _assess_read(tool, tool_input, cwd)
    elif is_mcp(tool) or tool == "RemoteTrigger":
        a = _assess_tool(tool, tool_input, cwd)
    else:
        return _assess_shell(tool, str(tool_input.get("command") or ""), cwd)
    a.hold_key = a.hold_key or a.plan_hash
    if tool in TERMINAL_TOOLS:
        _ho_add(a, HO_TERMINAL)   # a script typed into the human's terminal panel runs outside any sandbox; the gate cannot read what it does
    _scan_content(a, tool_input, read_only=is_mcp(tool) and mcp_command(tool_input) is None and _tool_rule(tool, False).get("id") in ("M-06", "M-06a", "M-06b"), cwd=cwd)
    return a


def _from_rule(rule: dict, env: str) -> Assessment:
    return Assessment(level=rule["level"], rule_id=rule["id"], rationale=rule.get("rationale", ""), environment=env,
                      reversibility=rule.get("reversibility", "reversible"), blast_radius=rule.get("blast_radius", "file"),
                      data_sensitivity=rule.get("data_sensitivity", "none"))


def permit_floor() -> str:
    return risk().get("permit_required_at", "high")


def human_floor() -> str:
    """The level from which a permit must carry a human principal and a tty approval reference: `human_approval_required_at`, and never
    above `high` (a high or critical command always needs a human, whatever the file says)."""
    floor = risk().get("human_approval_required_at", "high")
    return floor if floor in ("low", "medium") else "high"
