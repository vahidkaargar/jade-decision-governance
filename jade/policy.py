"""Risk assessment of a proposed tool call against policies/risk.yaml and policies/safety.yaml.

`policies.loader` holds the merge rules and raw first-match classification. This module adds what a
real shell command needs: it classifies per simple command after `shellnorm` has resolved escapes, quoting,
git global options and hidden command text, ignores heredoc bodies and quoted prose that no shell would run,
and treats read-only commands as low (see the `read_only` list in risk.yaml).
"""

from __future__ import annotations

import fnmatch
import glob
import os
import re
import shlex
import stat as stat_mod
import sys
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache

from policies import loader

from . import blind, egress, gitalias, harness_audit, humanonly, inert, paths, samecmd, shellnorm
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
GH_MERGE_MARK = "gh:merge-api "   # marker prefix R-10 matches: a `gh api` call that merges a pull request, or whose query or route the gate cannot read (V6-08, N20); the segment text has the quoted query blanked
_GH_MERGE_MUTATION = re.compile(r"\b(?:mergePullRequest|enablePullRequestAutoMerge|mergeBranch)\b")
# A GraphQL call whose query is not text on the command line: a file (`-F query=@q.graphql`; gh reads it), stdin (`--input`), a variable or a substitution (`query="$Q"`), or no query
# field at all. The gate cannot see a merge there, so the call is asked about like a merge (V6-08 review, N20). A query in single quotes, or in double quotes without a `$` or a
# backtick, is text the gate reads: `gh api graphql -f query='query { viewer { login } }'` stays a read.
_GH_QUERY_UNREAD = re.compile(
    r"""(?<![\w-])--input(?![\w-])|\bquery=["']?@|\bquery=(?:\$|`)|\bquery="(?:[^"\\$`]|\\.)*[$`]""")
_GH_QUERY_FIELD = re.compile(r"\bquery=")
# The last part of a pull request route is a variable or a substitution (`pulls/3/$m`, `pulls/3/$(echo merge)`): a merge the literal route rule cannot see.
_GH_ROUTE_UNREAD = re.compile(r"(?<![\w-])pulls/[^\s/]+/[^\s/]*[$`]")


_GH_API_WORDS = re.compile(r"(?<![\w.-])(?:gh|glab)\s+api(?![\w.-])")
_GH_REST_MERGE = re.compile(r"(?<![\w-])(?:pulls|merge_requests)/[^\s/]+/merge(?![\w-])|(?<![\w-])repos/[^\s/]+/[^\s/]+/merges(?![\w-])")   # the routes risk.yaml R-10 rates by name


def gh_api_merge_in(text: str) -> bool:
    """Does the text of a call hold a `gh api` call that merges a pull request, or whose query or route the gate cannot read (`_gh_api_merges`)? Read from the plain text of
    the call, so it errs towards yes. Memory keeps such a call as an exact answer at most and Jev never judges it, as for `gh pr merge` (integration of V6-08 and V6-06b)."""
    return bool(_GH_API_WORDS.search(text)) and (bool(_GH_REST_MERGE.search(text)) or _gh_api_merges(text.split(), text))


def _gh_api_merges(words: list[str], text: str) -> bool:
    """`words` is a `gh api` call: it merges a pull request, or its query or route is one the gate cannot read (so it is asked about)."""
    if _GH_MERGE_MUTATION.search(text) or _GH_ROUTE_UNREAD.search(text):
        return True
    if any(w.rstrip("/").rsplit("/", 1)[-1] == "graphql" for w in words[2:]):
        return bool(_GH_QUERY_UNREAD.search(text)) or not _GH_QUERY_FIELD.search(text)
    return False
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
    seg_keys: list[str] = field(default_factory=list)    # shell only: the canonical key of each gated segment (what memory generalises over, jade/memory.py)
    seg_rules: list[str] = field(default_factory=list)   # shell only: the rule each gated segment matched
    unread: list[str] = field(default_factory=list)      # shell only: the labels of what the gate could not read in ANY segment, also when a higher rule won (`blind` is only set when R-31 wins)

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
    return _realpath(path.replace("\x00", ""))


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


def _r13() -> dict:
    return next(r for r in risk()["rules"] if r["id"] == "R-13")


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
        views.extend(shellnorm.skeleton_both(f).split(shellnorm.JOIN))
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
    return _seal_key_reached(inv, tool, targets, globs, cwd)


# Argument names of an MCP file reader or file search (Desktop Commander: `path`, `paths`, `pattern`, `filePattern`, `searchType`; other servers: the same
# ideas under other spellings). V6-18 c, N22 (W-73): SI-10 reads these like the Read, Grep and Glob tools do.
_MCP_PATH_KEYS = frozenset({*WRITE_KEYS, *PATH_KEYS, "paths", "files", "file_paths", "filepaths", "uri", "root", "cwd"})
_MCP_GLOB_KEYS = frozenset({"glob", "filepattern", "file_pattern", "include"})
_MCP_MAX_TARGETS = 256
_MCP_MAX_STRING = 4096


def _mcp_strings(v) -> list[str]:
    """The non-empty strings of a value that is a string or a list of strings (one level of list), short enough to be a path. A list or a string over the
    limits is cut here and counted as reaching the key by `mcp_targets_overflow`."""
    items = v if isinstance(v, (list, tuple)) else [v]
    return [x for x in items[:_MCP_MAX_TARGETS] if isinstance(x, str) and x.strip() and len(x) <= _MCP_MAX_STRING]


def mcp_read_targets(tool: str, tool_input: dict) -> tuple[str, list[str], list[str]]:
    """(kind, paths, globs) an MCP call with no command names. `kind` is "Grep" for a content search (a recursive search of a directory reads every file in
    it) and "Read" otherwise. A `pattern` is a path glob, except in a content search, where it is a regular expression over file content and never a target
    (as the Grep tool: a search for the text `permit-seal.key` in the docs reads no key)."""
    if not isinstance(tool_input, dict):
        return "Read", [], []
    content = str(tool_input.get("searchType", "")).lower() == "content" or "grep" in tool.lower()
    paths_, globs = [], []
    for k, v in tool_input.items():
        lk = str(k).lower()
        if lk in _MCP_PATH_KEYS:
            paths_ += _mcp_strings(v)
        elif lk in _MCP_GLOB_KEYS or (lk == "pattern" and not content):
            globs += _mcp_strings(v)
    return ("Grep" if content else "Read"), paths_, globs


def mcp_targets_overflow(tool: str, tool_input: dict) -> bool:
    """Does a path or glob argument of an MCP call hold more than `_MCP_MAX_TARGETS` entries, or a string longer than a path can be? `mcp_read_targets` keeps
    only the first entries, so the key could hide behind decoys (`read_multiple_files` with 256 other paths first). Such a call counts as reaching the key."""
    if not isinstance(tool_input, dict):
        return False
    content = str(tool_input.get("searchType", "")).lower() == "content" or "grep" in tool.lower()
    for k, v in tool_input.items():
        lk = str(k).lower()
        if lk in _MCP_PATH_KEYS or lk in _MCP_GLOB_KEYS or (lk == "pattern" and not content):
            items = v if isinstance(v, (list, tuple)) else [v]
            if len(items) > _MCP_MAX_TARGETS or any(isinstance(x, str) and len(x) > _MCP_MAX_STRING for x in items):
                return True
    return False


def _mcp_reads_the_seal_key(tool: str, tool_input: dict, cwd: str) -> bool:
    inv = next((i for i in safety()["invariants"] if i["id"] == "SI-10"), None)
    if inv is None:
        return False
    if mcp_targets_overflow(tool, tool_input):   # fail closed: what the scan below would leave out could be the key
        return True
    kind, targets, globs = mcp_read_targets(tool, tool_input)
    return (bool(targets) or bool(globs)) and _seal_key_reached(inv, kind, targets, globs, cwd)


def _seal_key_reached(inv: dict, tool: str, targets: list[str], globs: list[str], cwd: str) -> bool:
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
    return tuple(dict.fromkeys(_realpath(str(p)) for p in (paths.config_dir(), os.path.expanduser("~/.config/jade"))))


def _expand_home(p: str) -> str:
    """`~`, `$HOME` and `${HOME}` at the start of a path word, which the shell expands before the command sees them."""
    home = os.path.expanduser("~")
    for var in ("${HOME}", "$HOME"):
        if p == var or p.startswith(var + "/"):
            return home + p[len(var):]
    return os.path.expanduser(p)


def _case_insensitive() -> bool:
    """Whether paths are compared case-folded. A macOS volume (APFS and HFS+ by default) and a Windows one ignore case, so `~/.ZSHENV` is `~/.zshenv`;
    folding on a case-sensitive Mac volume only adds matches, the safe direction (security review PRE-1)."""
    return sys.platform in ("darwin", "win32")


def _path_key(path: str, base: str) -> str:
    """The form every root check compares: the real path of `path` (relative to `base`, with `~`, `$HOME` and `${HOME}` expanded), case-folded where
    the volume ignores case. The roots go through `_fold` too."""
    return _fold(_realpath(os.path.join(base or os.getcwd(), _expand_home(path))))


_DATA_FIRMLINK_RE = re.compile(r"/system/volumes/data(?=/|$)", re.I)


def _fold(p: str) -> str:
    """The comparison form of a real path, used on both sides of every root check: the macOS data-volume firmlink prefix removed
    (`/System/Volumes/Data/Users/x` is `/Users/x`, the same inode, and realpath does not resolve it: PRE2-2), Unicode NFC (APFS ignores NFC/NFD:
    PRE2-9), case-folded where the volume ignores case. Each step only adds matches on another system."""
    if _DATA_FIRMLINK_RE.match(p):
        p = p[20:] or "/"
    if not p.isascii():
        p = unicodedata.normalize("NFC", p)
    if _case_insensitive():
        p = p.casefold()
        if not p.isascii():
            p = unicodedata.normalize("NFC", p)
    return p


def _si12_hit(path: str, base: str, roots: tuple[str, ...], parent_too: bool = False) -> bool:
    """True when `path` (relative to `base`) is a config directory, a path inside one, or, for a verb that replaces a directory, a parent of one.
    Used by every root check (SI-12, wiring, mod, shell start-up), so all of them compare paths the same way (`_path_key`)."""
    if not path or "\x00" in path:
        return False
    real = _path_key(path, base)
    for r in roots:
        r = _fold(r)
        if real == r or real.startswith(r + os.sep) or (parent_too and r.startswith(real.rstrip(os.sep) + os.sep)):
            return True
    return False


HO_WIRING, HO_INSTALL, HO_SETTINGS, HO_STATE, HO_MOD, HO_R17, HO_TERMINAL, HO_SHELL = (
    "HO-wiring", "HO-checkout-install", "HO-settings-shadow", "HO-state-path", "HO-mod", "HO-R17", "HO-terminal", "HO-shell-startup")
HO_DELETE, HO_MONEY, HO_RELEASE = humanonly.HO_DELETE, humanonly.HO_MONEY, humanonly.HO_RELEASE   # W-98b: jade/humanonly.py holds the patterns and their corpus
_STATE_LITERAL = re.compile(r"\.config/jade(?![\w.-])|\.local/share/jade(?![\w.-])|jade\.sqlite")
_INSTALL_CMD = re.compile(r"\b(?:uv\s+tool\s+(?:install|upgrade|reinstall)|uv\s+pip\s+install|pip3?\s+install|pipx\s+(?:install|upgrade|reinstall)|python3?\s+-m\s+pip\s+install)\b")
_UV_TOOL_JADE = re.compile(r"\buv\s+tool\s+(?:uninstall|upgrade|reinstall|install)\b[^;&|]*\bjade|\buv\s+tool\s+(?:uninstall|upgrade|reinstall)\b[^;&|]*--all\b")   # R-13: changes or removes the installed JADE tool itself, whatever it installs from
_SETTINGS_FILE = re.compile(r"(?:^|/)\.claude/settings(?:\.local)?\.json$")
_SETTINGS_DISABLE = re.compile(r"disableAllHooks|allowManagedHooksOnly|JADE_(?:GATE_MODE|POLICY_DIR|CONFIG|HOME|REGISTRY_DIR|SESSION_ID)|CLAUDE_CODE_SESSION_ID|\"hooks\"\s*:\s*(?:\{\s*\}|null)", re.I)
TERMINAL_TOOLS = frozenset({"mcp__terminal__run_in_terminal"})


def _real(p: str) -> str:
    return _realpath(os.path.expanduser(p))


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


_SHELL_STARTUP_READ_MAX = 1 << 20
_ETC = "/etc"   # the system-wide start-up files live here; the tests point it at a temporary directory
_ZSH_FILES = (".zshenv", ".zprofile", ".zshrc", ".zlogin", ".zlogout")
_SH_WORD = r"""(?:"[^"\n]*"|'[^'\n]*'|\\.|[^\s;&|<>()"'\\])+"""
_SOURCE_WORD = re.compile(r"(?<![\w.-])(?:source|\.)[ \t]+(" + _SH_WORD + ")")
_SOURCE_KEYWORDS = frozenset({"then", "do", "else", "if", "elif", "while", "until"})
_ZDOTDIR_SET = re.compile(r"(?:^|[;&|{(]|\bexport\b)[ \t]*ZDOTDIR=(" + _SH_WORD + ")", re.M)
_HOME_VAR = re.compile(r"\$(?:HOME(?![A-Za-z0-9_])|\{HOME\})")


def _read_startup_text(path: str) -> str:
    """The text of a start-up file when it is a regular file of at most 1 MiB; '' otherwise. Never blocks on a FIFO or a device."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode) or st.st_size > _SHELL_STARTUP_READ_MAX:
            return ""
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(_SHELL_STARTUP_READ_MAX).decode("utf-8", "replace")
    except OSError:
        return ""
    finally:
        os.close(fd)


def _shell_word(raw: str) -> str | None:
    """One shell word as the shell expands it, when that needs nothing but HOME: quotes removed, a leading unquoted `~` and `$HOME` or `${HOME}` outside
    single quotes expanded, a single-quoted part kept literal. None when anything else would expand (another variable, `~user`, a command substitution,
    an unquoted glob)."""
    home, out, i, n = os.path.expanduser("~"), [], 0, len(raw)
    if raw.startswith("~"):
        if n > 1 and raw[1] != "/":
            return None
        out.append(home)
        i = 1
    while i < n:
        c = raw[i]
        if c == "'":
            j = raw.find("'", i + 1)
            if j < 0:
                return None
            out.append(raw[i + 1:j])
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and raw[j] != '"':
                d = raw[j]
                if d == "\\" and j + 1 < n and raw[j + 1] in '$`"\\':
                    out.append(raw[j + 1])
                    j += 2
                elif d == "$" and (m := _HOME_VAR.match(raw, j)):
                    out.append(home)
                    j = m.end()
                elif d in "$`":
                    return None
                else:
                    out.append(d)
                    j += 1
            if j >= n:
                return None
            i = j + 1
        elif c == "\\":
            if i + 1 >= n:
                return None
            out.append(raw[i + 1])
            i += 2
        elif c == "$" and (m := _HOME_VAR.match(raw, i)):
            out.append(home)
            i = m.end()
        elif c in "$`*?[":
            return None
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _sourced_path(raw: str) -> str | None:
    """A literal absolute path a start-up file names (`_shell_word`); None for a variable, a glob, a command substitution or a relative path."""
    p = _shell_word(raw)
    if not p or "\x00" in p or not os.path.isabs(p):
        return None
    return p


def _sourced_files(text: str) -> list[str]:
    """Every literal path a start-up file sources, wherever on a line the `source` or `.` command stands (comment lines skipped)."""
    out = []
    for m in _SOURCE_WORD.finditer(text):
        lead = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if _starts_command(lead) and (sp := _sourced_path(m.group(1))):
            out.append(sp)
    return out


def _starts_command(lead: str) -> bool:
    """Whether a command starts after `lead` (the text before it on its line): nothing, `;`, `&&`, `||`, `|`, `{`, `(`, `!` or a keyword, then any
    `builtin` or `command` words. A comment line never does."""
    if lead.lstrip().startswith("#"):
        return False
    words = lead.split()
    while words and words[-1] in ("builtin", "command"):
        words.pop()
    if not words:
        return True
    last = words[-1]
    return last[-1] in ";&|{(!" or last in _SOURCE_KEYWORDS or last.rsplit(";", 1)[-1] in _SOURCE_KEYWORDS


def _shell_roots() -> tuple[str, ...]:
    """The real paths of what the agent's shell loads: the shell start-up files (zsh, bash, system-wide, $BASH_ENV, $ENV, and the zsh files of a
    `ZDOTDIR` that ~/.zshenv sets to a literal path), the Claude shell snapshot and session-env directories (also under $CLAUDE_CONFIG_DIR), and every
    file those start-up files source by a literal path, one level deep (a variable or a glob is not followed: docs/05 T32). Computed per call."""
    zd = os.path.expanduser(os.environ.get("ZDOTDIR") or "~")
    startup = [os.path.join(zd, n) for n in _ZSH_FILES]
    startup += [os.path.expanduser("~/" + n) for n in (".bashrc", ".bash_profile", ".bash_login", ".profile", ".bash_logout")]
    startup += [os.path.join(_ETC, n) for n in ("zshenv", "zprofile", "zshrc", "zlogin", "zlogout", "profile", "bashrc")]
    startup += [os.environ[v] for v in ("BASH_ENV", "ENV") if os.environ.get(v)]
    texts = {startup[0]: _read_startup_text(startup[0])}
    for m in _ZDOTDIR_SET.finditer(texts[startup[0]]):   # PRE-8: the interactive shell that makes the snapshot reads the zsh files there
        if d := _sourced_path(m.group(1)):
            startup += [os.path.join(d, n) for n in _ZSH_FILES]
    out = [_real(p) for p in startup]
    claude = [os.path.expanduser("~/.claude"), *([os.environ["CLAUDE_CONFIG_DIR"]] if os.environ.get("CLAUDE_CONFIG_DIR") else [])]
    out += [_real(os.path.join(c, n)) for c in claude for n in ("shell-snapshots", "session-env")]   # session-env: Claude Code runs the scripts there
    for f in dict.fromkeys(startup):
        text = texts[f] if f in texts else _read_startup_text(f)
        out += [_real(sp) for sp in _sourced_files(text)]
    return tuple(dict.fromkeys(out))


_real_shell_roots = _shell_roots   # tests/conftest.py swaps `_shell_roots` for one that returns (); the case runner (evals/run_cases.py) puts this one back for a case that sets a level


def _written_text(tool_input: dict) -> str:
    """The new text a Write, Edit, MultiEdit or NotebookEdit call carries ('' when it carries none the gate can read)."""
    parts = [str(tool_input[k]) for k in ("content", "new_string", "new_source") if isinstance(tool_input.get(k), str)]
    for e in tool_input.get("edits") or []:
        if isinstance(e, dict) and isinstance(e.get("new_string"), str):
            parts.append(e["new_string"])
    return "\n".join(parts).strip()


_SCRIPT_READ_MAX = 1 << 20
_JSON_U_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _settings_disables(text: str | None) -> bool:
    """Does the text of a settings file turn the JADE hooks off or move JADE (`disableAllHooks`, `JADE_GATE_MODE` ...)? JSON lets a key be written with `\\uXXXX`
    escapes, so the text is read both as written and with the escapes decoded. None (the result could not be worked out) counts as yes."""
    if text is None:
        return True
    return bool(_SETTINGS_DISABLE.search(text) or _SETTINGS_DISABLE.search(_JSON_U_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)))


def _settings_result(tool_input: dict, subject: str, cwd: str) -> str | None:
    """The whole text a Write, Edit or MultiEdit leaves in the settings file `subject`, or None when the gate cannot work it out (a call that carries no text, a file
    it cannot read or that is over 1 MiB, an `old_string` that is not in the file). An Edit only carries a piece, so a key split over two calls (`disableAllH`, then
    `H"` to `Hooks"`) shows only in the whole result."""
    content = tool_input.get("content")
    if isinstance(content, str):
        return content
    edits = [e for e in (tool_input.get("edits") or []) if isinstance(e, dict)]
    if isinstance(tool_input.get("old_string"), str) and isinstance(tool_input.get("new_string"), str):
        edits.append(tool_input)
    if not edits:
        return None
    path = _realpath(os.path.join(cwd or os.getcwd(), _expand_home(subject)))
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > _SCRIPT_READ_MAX:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(_SCRIPT_READ_MAX)
    except (OSError, ValueError):
        return None
    for e in edits:
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or not old or old not in text:
            return None
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def _script_names_state(f: str, dirs: tuple[str, ...], cwd: str) -> bool:
    """Does the script file a command runs (at most 1 MiB, a regular file, resolved in a directory the call may run in) name the JADE state paths?"""
    for d in (dirs or ("",)):
        p = _realpath(os.path.join(d or cwd or os.getcwd(), os.path.expanduser(f)))
        try:
            if os.path.isfile(p) and os.path.getsize(p) <= _SCRIPT_READ_MAX:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    if _STATE_LITERAL.search(fh.read(_SCRIPT_READ_MAX)):
                        return True
        except OSError:
            continue
    return False


def _script_ho(f: str, dirs: tuple[str, ...], cwd: str) -> set[str]:
    """The delete, money and release classes of the commands in the script file a command runs (at most 1 MiB, a regular file, resolved in a directory the call may run in).
    A script in a language the shell segmenter does not read is not understood here (docs/05)."""
    out: set[str] = set()
    for d in (dirs or ("",)):
        p = _realpath(os.path.join(d or cwd or os.getcwd(), os.path.expanduser(f)))
        try:
            if not (os.path.isfile(p) and os.path.getsize(p) <= _SCRIPT_READ_MAX):
                continue
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read(_SCRIPT_READ_MAX)
        except OSError:
            continue
        for seg, sdirs in shellnorm.segments_cwd(text, os.path.dirname(p)):
            if not is_read_only(seg):
                out |= humanonly.shell_classes(_bare(seg), sdirs or (os.path.dirname(p),), os.path.dirname(p))
    return out


def _si13_words(words: list[str], f: str, dirs: tuple[str, ...], cwd: str) -> bool:
    """SI-13 for one simple command that runs the script file `f`: is it a Python payload runner that starts real shells without evals/realshell.py
    (jade/harness_audit.py `gate_findings`)? A shell script is read one level deep for the Python runners it starts (`batch.sh` looping over `run.py`)."""
    data_arg = any(w.lower().endswith(harness_audit.DATA_EXT) for w in words[1:] if w != f)
    python = blind._base(words[0]).startswith("python") or (words[0] in ("uv", "uvx"))
    for d in (dirs or ("",)):
        p = os.path.realpath(os.path.join(d or cwd or os.getcwd(), os.path.expanduser(f)))
        if (python or p.endswith(".py")) and harness_audit.gate_file(p, any_name=python, data_arg=data_arg):
            return True
        if python or p.endswith(".py"):
            continue
        try:
            if not (os.path.isfile(p) and os.path.getsize(p) <= _SCRIPT_READ_MAX):
                continue
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read(_SCRIPT_READ_MAX)
        except OSError:
            continue
        here = os.path.dirname(p)
        for seg, sdirs in shellnorm.segments_cwd(text, here):
            inner = _bare(seg).split(" ")
            sf = blind.script_file(inner)
            if sf and (sf.endswith(".py") or blind._base(inner[0]).startswith("python")):
                inner_data = any(w.lower().endswith(harness_audit.DATA_EXT) or "$" in w for w in inner[1:] if w != sf)
                for sd in (sdirs or (here,)):
                    q = os.path.realpath(os.path.join(sd or here, os.path.expanduser(sf)))
                    if harness_audit.gate_file(q, any_name=True, data_arg=inner_data):
                        return True
    return False


def _ho_add(a: Assessment, ident: str) -> None:
    if ident not in a.ho_ids:
        a.ho_ids.append(ident)


def _ho_tool_paths(a: Assessment, tool_input: dict, subjects: list[str], cwd: str) -> None:
    """Human-only classes of a Write/Edit-style or MCP call, from the paths it names."""
    wroots, mroots, sroots = _wiring_roots(), _mod_roots(), _shell_roots()
    text = None
    for s in subjects:
        if not s.strip():
            continue
        if _si12_hit(s, cwd, wroots):
            _ho_add(a, HO_WIRING)
        if _si12_hit(s, cwd, mroots):
            _ho_add(a, HO_MOD)
        if _si12_hit(s, cwd, sroots):
            _ho_add(a, HO_SHELL)
        if _SETTINGS_FILE.search(_path_key(s, cwd)):
            text = _written_text(tool_input) if text is None else text
            if not text or _settings_disables(text) or _settings_disables(_settings_result(tool_input, s, cwd)):   # unreadable text counts: it could be anything
                _ho_add(a, HO_SETTINGS)


def _ho_segment(a: Assessment, seg: str, dirs: tuple[str, ...], cwd: str, wroots: tuple[str, ...], mroots: tuple[str, ...], sroots: tuple[str, ...],
                git_work_tree: str | None = None) -> None:
    """Human-only classes of one shell segment that is not read-only. `git_work_tree`: a GIT_WORK_TREE an earlier segment of the call set."""
    toks, parent_too = _segment_tokens(seg)
    bare, calls = _analyse(seg)
    links = _hardlink_sources(bare)
    into = _into_targets(bare)
    _ho_shell_segment(a, seg, bare, calls, toks, parent_too, dirs, cwd, sroots, links, into, git_work_tree)
    for d in (dirs or ("",)):
        base = d or cwd
        # PRE2-3: a hard link is a second name of the same file, whichever side is the protected one. PRE2-8: a file copied into a directory under its
        # own name (`cp /tmp/settings.json ~/.claude/`) is an exact file; the whole-tree check is the shell roots' only.
        checks = [(src, True) for src in links] + [(path, False) for path, _, exact, _ in into if exact]
        for path, parent in checks:
            if _si12_hit(path, base, wroots, parent):
                _ho_add(a, HO_WIRING)
            if _si12_hit(path, base, mroots, parent):
                _ho_add(a, HO_MOD)
            if path and "\x00" not in path and _SETTINGS_FILE.search(_path_key(path, base)):
                _ho_add(a, HO_SETTINGS)
    install = bool(_INSTALL_CMD.search(bare))
    if _UV_TOOL_JADE.search(bare):
        _ho_add(a, HO_WIRING)
    for ident in sorted(humanonly.shell_classes(bare, dirs, cwd, raw=seg)):
        _ho_add(a, ident)
    for tok in toks:
        for d in (dirs or ("",)):
            base = d or cwd
            if _si12_hit(tok, base, wroots, parent_too):
                _ho_add(a, HO_WIRING)
            if _si12_hit(tok, base, mroots, parent_too):
                _ho_add(a, HO_MOD)
            real = _realpath(os.path.join(base or os.getcwd(), os.path.expanduser(tok))) if tok and "\x00" not in tok else ""
            if real and _SETTINGS_FILE.search(_fold(real)):
                _ho_add(a, HO_SETTINGS)   # a shell write of a settings file: the text is not the gate's to read
            if install and real and os.path.isdir(real) and _is_checkout(real):
                _ho_add(a, HO_INSTALL)


_TREE_CP_FLAG = re.compile(r"^-[A-Za-z]*[rRa][A-Za-z]*$")
_TAR_EXTRACT = re.compile(r"^-[A-Za-z]*x[A-Za-z]*$")
_GIT_TREE_WRITERS = frozenset({"checkout", "restore", "switch", "pull", "merge", "rebase", "cherry-pick", "revert", "am", "apply", "stash", "reset", "clone",
                               "worktree", "read-tree", "checkout-index"})
_GIT_WORK_TREE_ENV = re.compile(r"(?:^|\s)GIT_WORK_TREE=(\S*)")
_HARDLINK_CP = re.compile(r"^-[A-Za-z]*l[A-Za-z]*$")
_SYMLINK_LN = re.compile(r"^-[A-Za-z]*s[A-Za-z]*$")
_NO_TARGET_DIR = re.compile(r"^-[A-Za-z]*T[A-Za-z]*$")
# rsync options whose value is the next word (rsync reads options anywhere on the line, also after the operands: PRE2-11)
_RSYNC_VALUE = frozenset({"-e", "--rsh", "--exclude", "--include", "--exclude-from", "--include-from", "--files-from", "-f", "--filter", "--rsync-path",
                          "-T", "--temp-dir", "--compare-dest", "--copy-dest", "--link-dest", "--backup-dir", "--suffix", "--chmod", "--chown", "--usermap",
                          "--groupmap", "-B", "--block-size", "--timeout", "--contimeout", "--log-file", "--log-file-format", "--out-format", "--password-file",
                          "--bwlimit", "--max-size", "--min-size", "--partial-dir", "--port", "--sockopts", "--skip-compress", "--modify-window", "--iconv",
                          "--max-delete", "--write-batch", "--read-batch", "--only-write-batch", "-M", "--remote-option", "--info", "--debug",
                          "--checksum-choice", "--cc", "--compress-choice", "--zc", "--compress-level", "--zl", "--stop-after", "--stop-at", "--address",
                          "--protocol", "--outbuf", "--early-input", "--max-alloc", "--checksum-seed"})
_RSYNC_VALUE_CLUSTER = re.compile(r"^-[A-Za-z]*[efTBM]$")
_SOURCE_VERBS = frozenset({"source", "."})


def _word(w: str) -> str:
    return w.strip("'\"`,;()")


def _option_value(args: list[str], names: tuple[str, ...], long: str = "") -> str | None:
    """The value of the last `-X V`, `-XV` or `--long=V` among `args` (None when none is given)."""
    val = None
    for k, w in enumerate(args):
        if w in names and k + 1 < len(args):
            val = args[k + 1]
        elif long and w.startswith(long + "="):
            val = w.split("=", 1)[1]
        elif any(w.startswith(n) and len(w) > len(n) and not n.startswith("--") for n in names):
            val = w[2:]
    return val


def _target_dir(args: list[str]) -> str | None:
    """The directory of `-t D`, `-tD`, `-rt D` or `--target-directory[=]D` (cp, install, ln)."""
    for k, w in enumerate(args):
        if w.startswith("--target-directory="):
            return w.split("=", 1)[1]
        if _is_target_dir_option(w):
            rest = "" if w == "--target-directory" else w[w.index("t", 1) + 1:]
            return rest or (args[k + 1] if k + 1 < len(args) else None)
    return None


def _rsync_operands(args: list[str]) -> list[str]:
    """The operands of an rsync command line, wherever its options stand (`rsync -a src/ ~ --exclude foo`: `foo` is an option value, PRE2-11)."""
    out, skip, ended = [], False, False
    for w in args:
        if skip:
            skip = False
        elif ended:
            out.append(w)
        elif w == "--":
            ended = True
        elif REDIRECT_OP.match(w) or w in _RSYNC_VALUE or _RSYNC_VALUE_CLUSTER.match(w):
            skip = True
        elif w and not w.startswith("-") and not REDIRECT_WORD.match(w):
            out.append(w)
    return out


def _hardlink_sources(bare: str) -> list[str]:
    """The operands of a hard-link command (`ln` without `-s`, `cp -l`/`--link`/`-al`, `link`): each is a second name of the other (PRE2-3)."""
    words = [_word(w) for w in bare.split(" ") if w]
    if not words:
        return []
    verb, args = words[0].rsplit("/", 1)[-1], words[1:]
    if verb == "link" or (verb == "ln" and not any(w == "--symbolic" or _SYMLINK_LN.match(w) for w in args)) or (
            verb in ("cp", "gcp") and any(w == "--link" or _HARDLINK_CP.match(w) for w in args)):
        return operands(args)
    return []


def _into_targets(bare: str) -> list[tuple[str, bool, bool, bool]]:
    """Where a copy or unpack verb writes, as (path, parent_too, exact, unless_dir):

    * a tree copied under its own name (`cp -r /tmp/proj ~`, `rsync -a /tmp/proj ~/`) writes `dest/proj` only: that path, with parent_too (a source named
      `.claude` brings `.claude/shell-snapshots/...`), and `dest` itself only when it is not a directory yet (the copy becomes `dest`: unless_dir);
    * a contents copy (`src/`, `src/.`, `.`, `cp -T`, ditto) and an unpack (tar, unzip, 7z, cpio, pax) write into `dest`: `dest` with parent_too;
    * a file copied into a directory writes `dest/name`. `exact` marks a `dest/name` path: it is the exact name that lands (PRE2-8, PRE2-12)."""
    words = [_word(w) for w in bare.split(" ") if w]
    if not words:
        return []
    verb, args = words[0].rsplit("/", 1)[-1], words[1:]
    if verb in ("cp", "gcp", "install", "ln", "rsync", "ditto"):
        tdir = _target_dir(args) if verb not in ("rsync", "ditto") else None
        ops = _rsync_operands(args) if verb == "rsync" else operands(args)
        if tdir:
            dest, srcs = tdir, [o for o in ops if o != tdir]
        elif len(ops) >= 2:
            dest, srcs = ops[-1], ops[:-1]
        else:
            return []
        if verb == "rsync" and re.match(r"^[^/]*:", dest):
            return []   # a remote destination
        if verb == "ditto" or (verb in ("cp", "gcp") and any(w == "--no-target-directory" or _NO_TARGET_DIR.match(w) for w in args)):
            return [(dest, True, False, False)]   # ditto copies the contents of each source; `cp -T`: dest is the copy itself
        copyish = verb in ("cp", "gcp", "rsync")
        recursive = verb == "rsync" or (verb in ("cp", "gcp") and any(_TREE_CP_FLAG.match(w) or w in ("--recursive", "--archive") for w in args))
        out: list[tuple[str, bool, bool, bool]] = []
        for x in srcs:
            contents = copyish and (x in (".", "..") or x.endswith(("/", "/.")))   # BSD cp and rsync copy what is inside
            name = os.path.basename(x.rstrip("/"))
            if contents:
                out.append((dest, True, False, False))
                continue
            if name not in ("", ".", ".."):
                out.append((os.path.join(_expand_home(dest), name), recursive, True, False))
            if recursive:
                out.append((dest, True, False, True))
        return list(dict.fromkeys(out))
    if verb in ("tar", "gtar", "bsdtar"):
        first = args[0] if args else ""
        if not (any(w in ("--extract", "--get") or _TAR_EXTRACT.match(w) for w in args) or re.fullmatch(r"[A-Za-z]*x[A-Za-z]*", first)):
            return []
        return [(_option_value(args, ("-C", "--directory", "--cd"), "--directory") or ".", True, False, False)]
    if verb == "unzip":
        if any(_LISTING_UNZIP.match(w) for w in args):
            return []
        return [(_option_value(args, ("-d",)) or ".", True, False, False)]
    if verb in ("7z", "7za", "7zz") and args[:1] and args[0] in ("x", "e"):
        return [(next((w[2:] for w in args if w.startswith("-o") and len(w) > 2), "."), True, False, False)]
    if (verb == "cpio" and any(re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*", w) for w in args)) or (verb == "pax" and any(re.fullmatch(r"-[A-Za-z]*r[A-Za-z]*", w) for w in args)):
        return [(".", True, False, False)]
    return []


# W-82: where a copy lands when the words name a DIRECTORY or a glob, so that R-13 (which reads path text) still sees the protected file it writes.
# `cp /tmp/e/settings.json ~/.claude/` writes `~/.claude/settings.json`, but the words only say `~/.claude/`. The path is the directory and the name of the source, which
# is typed: the same reading the human-only classes make of an exact file copied under its own name (`_into_targets`). Only a name that is typed counts here. A source whose
# name is not known (a glob, a variable the command did not set to a literal, a tree's content, an archive) is NOT guessed at: it is read by `unseen_into_dedicated` below,
# which is high only when the directory it lands in is one made for protected files (the owner's decision, W-82, docs/05 T19).
_DIR_DEST_VERBS = frozenset({"cp", "gcp", "mv", "install", "ln", "rsync", "ditto"})
_GLOB_CHARS = re.compile(r"[*?\[]")
DIR_GLOB_CAP = 4096          # matches of a glob destination that are examined
DIR_GLOB_COMPONENTS = 3      # a glob destination with more wildcard components than this is not expanded (the walk is the cost)
DIR_GLOB_WALKS = 16          # different glob destinations walked in one command; one past that is read as written (`cp s1* /usr/*/*/; cp s2* /usr/*/*/; ...`)
_GLOBS: dict[str, list[str]] | None = None   # set for the length of one `assess` only: the glob destinations walked so far
_DESTS: dict[tuple[str, tuple[str, ...]], list[tuple[str, bool]]] | None = None   # set for the length of one `assess` only: `_dest_bases` answers
DIR_GLOB_SPEND = 20000       # glob-destination matches handed out in one command; past that a glob destination is read as written
_GLOB_SPENT: list[int] | None = None   # set for the length of one `assess` only: the matches handed out so far


def _glob_matches(pattern: str) -> list[str]:
    """The paths a glob destination stands for now, as the shell expands it (`*` and `?` do not match a leading dot). Bounded: `DIR_GLOB_CAP` matches, and no pattern with
    `**` or many wildcard components, whose walk could cost more than the command."""
    if "**" in pattern or sum(1 for c in pattern.split("/") if _GLOB_CHARS.search(c)) > DIR_GLOB_COMPONENTS:
        return []
    memo = _GLOBS
    if memo is not None:   # inside one `assess`: a pattern is walked once, and at most DIR_GLOB_WALKS different patterns are walked at all
        if pattern in memo:
            return memo[pattern]
        if len(memo) >= DIR_GLOB_WALKS:
            return []
    out: list[str] = []
    try:
        for m in glob.iglob(pattern):
            out.append(m)
            if len(out) >= DIR_GLOB_CAP:
                break
    except (OSError, ValueError, re.error):
        pass
    if memo is not None:
        memo[pattern] = out
    return out


def _dest_bases(base: str, dirs: tuple[str, ...]) -> list[tuple[str, bool]]:
    """The destination `base` as (path, is_a_directory) for every directory the command may run in: absolute with symlinks resolved, a glob as what it matches now. A path
    is a directory when it ends in `/`, is `.` or `..`, or exists as one. A relative path with no directory to resolve it in is read as written. Inside one `assess` the
    answer for a (base, dirs) is kept: a long command that names one glob destination many times resolves its matches once."""
    memo = _DESTS
    if memo is None:
        return _dest_bases_now(base, dirs)
    key = (base, dirs)
    if key not in memo:
        memo[key] = _dest_bases_now(base, dirs)
    got = memo[key]
    if _GLOB_SPENT is not None and _GLOB_CHARS.search(base):   # a glob destination: every match is read by the caller, so the matches handed out in one command are bounded too
        if _GLOB_SPENT[0] >= DIR_GLOB_SPEND:
            return [(_pathfold(_expand_home(base.strip("\"'"))), base.endswith("/"))]   # read as written, as a pattern that is not expanded
        _GLOB_SPENT[0] += len(got)
    return got


def _dest_bases_now(base: str, dirs: tuple[str, ...]) -> list[tuple[str, bool]]:
    base = _expand_home(base.strip("\"'"))
    if not base or "\x00" in base:
        return []
    textual = base.endswith("/") or base.rstrip("/").rsplit("/", 1)[-1] in (".", "..")
    out: list[tuple[str, bool]] = []
    for d in dirs or ("",):
        if not d and not os.path.isabs(base):
            continue
        pat = base if os.path.isabs(base) else os.path.join(d, base)
        for p in (_glob_matches(pat) if _GLOB_CHARS.search(pat) else [pat]):
            q = normalise_path(p)
            out.append((q, textual or os.path.isdir(q)))
    return list(dict.fromkeys(out)) or [(_pathfold(base), textual)]


def dir_dest_paths(bare: str, dirs: tuple[str, ...] = ("",)):
    """The paths a copy, move, link or sync writes when it names a directory (W-82), one at a time: R-13 reads each as if it had been typed.

    Built on `_into_targets`, with `mv` as `cp -r`. A file copied into a directory is `dir/name` (and `dir/name/` for what a tree called so holds). A tree copied to a name
    that is not a directory yet becomes that directory: `dir/`. A destination that is a glob is every path it matches now: a file it matches is written itself, a directory
    holds the copy. A name that is not typed (a glob or variable source, a contents copy, an archive) gives nothing. It only produces paths; the caller takes a rating
    from them only when it is higher than what the command already has."""
    words = system_bare(bare).split(" ")
    verb = words[0].rsplit("/", 1)[-1]
    if verb not in _DIR_DEST_VERBS:
        return
    if verb == "mv":
        words = ["cp", "-r", *words[1:]]
    for path, _parent_too, exact, unless_dir in _into_targets(" ".join(words)):
        if exact:
            base, _, name = path.rpartition("/")
            if not name or GLOB_OR_EXPANSION.search(name) or "\x01" in name:
                continue
        elif unless_dir:
            base, name = path, ""
        else:
            continue
        for q, is_dir in _dest_bases(base or "/", dirs):
            head = q.rstrip("/")
            if exact and not is_dir:   # a file that is there is written itself (a glob destination that matched one)
                yield q
            elif exact:
                yield f"{head}/{name}"
                yield f"{head}/{name}/"
            elif not is_dir:   # a tree copied to a name that is not a directory yet becomes that directory
                yield f"{head}/"


def dir_dest_hit(rule: dict, bare: str, dirs: tuple[str, ...]) -> bool:
    """Does R-13's pattern match a path `dir_dest_paths` gives for this command?"""
    rx = rule["_re"]
    return any(rx.search(p) for p in dir_dest_paths(bare, dirs))


# W-82, owner decision 2026-11-20: a name the gate cannot see (a glob or variable source, the contents of a tree, an archive) copied, moved, linked, synced or unpacked
# into a directory MADE for protected files is R-13 high. Which file lands is not in the words, and in these directories most of what can land is protected: the hook
# settings, the hooks, the instruction files, `authorized_keys`, the policy files. A directory is dedicated by its own name (the last component, case-folded as R-13
# reads paths), wherever it is. Not `~/.local/bin`, not `~/.config`, not an ordinary project directory, not a directory inside a dedicated one (`~/.claude/projects/`):
# there a name that is not typed stays as it was.
DEDICATED_DIRS = frozenset({".claude", ".vscode", ".cursor", ".codex", ".ssh", "policies", ".ai"})


def _dedicated(path: str) -> bool:
    return os.path.basename(path.rstrip("/")).casefold() in DEDICATED_DIRS


def unseen_into_dedicated(bare: str, dirs: tuple[str, ...] = ("",)) -> bool:
    """Does this copy, move, link, sync or unpack put a name that is not typed into a dedicated directory (W-82)? Built on `_into_targets`, with `mv` as `cp -r`:

    * a source whose name is a glob, a variable or a substitution lands in the destination directory;
    * a tree copied under its typed name holds contents that are not typed: it lands in `dest/name` when `dest` is a directory, and becomes `dest` when it is not;
    * a contents copy (`src/`, `src/.`, `cp -T`, ditto) and an unpack (tar, unzip, 7z, cpio, pax) write unseen names into `dest`.
    The destination is resolved as `dir_dest_paths` resolves it (home, the directories the command may run in, symlinks, a glob destination as what it matches now).
    The command an `xargs` or a `find -exec` runs is read too: what they feed it (the replace string, or the words `xargs` appends) is a name that is not typed."""
    words = system_bare(bare).split(" ")
    inner: str | None = None
    if (xi := _xargs_inner(" ".join(words))) is not None:
        cmd, repl = xi
        inner = _put(cmd, repl, _BRACES) if repl else f"{cmd} {_BRACES}"
    elif words[0].rsplit("/", 1)[-1] == "find" and (at := next((i for i, w in enumerate(words) if w in shellnorm.FIND_EXEC), None)) is not None:
        tail = words[at + 1:]
        inner = " ".join(tail[:next((i for i, w in enumerate(tail) if w in (";", "+", "\\;", "\\", "\x06")), len(tail))])
    if inner is not None:   # the inner command without its own launcher and system directory (`xargs sudo cp`, `-exec /bin/cp`)
        words = system_bare(_bare(inner)).split(" ")
    if not words or not words[0]:
        return False
    verb = words[0].rsplit("/", 1)[-1]
    if verb == "mv":
        words = ["cp", "-r", *words[1:]]
    seen: dict[str, list[tuple[str, bool]]] = {}   # one destination for many sources: resolved (and a glob walked) once

    def bases(p: str) -> list[tuple[str, bool]]:
        if p not in seen:
            seen[p] = _dest_bases(p, dirs)
        return seen[p]

    for path, parent_too, exact, unless_dir in _into_targets(" ".join(words)):
        if exact:
            base, _, name = path.rpartition("/")
            if not name or GLOB_OR_EXPANSION.search(name) or "\x01" in name or "\x06" in name:   # the name is not typed: it lands in `base`
                if _dedicated(base) or any(_dedicated(q) for q, _is_dir in bases(base or "/")):   # the name as written, or where its symlinks lead
                    return True
            elif parent_too and _dedicated(name) and any(is_dir for _q, is_dir in bases(base or "/")):   # a tree called `.claude` copied into a directory
                return True
        elif unless_dir:
            if any(not is_dir and (_dedicated(path) or _dedicated(q)) for q, is_dir in bases(path)):   # the tree becomes `dest`
                return True
        elif _dedicated(path) or any(_dedicated(q) for q, _is_dir in bases(path)):
            return True
    return False


def _git_tree_targets(seg: str, calls: list[shellnorm.GitCall], carried: str | None = None) -> list[tuple[str, bool]]:
    """The work trees a git call writes (`-C D`, `--work-tree D`, `-c core.worktree=D`, `GIT_WORK_TREE=D` in the segment or set by an earlier one
    (`carried`), else the directory it runs in), and the directory a clone or a new worktree fills: a work tree that is home, or holds a start-up file, is
    replaced file by file (`git --work-tree=~ checkout -- .`)."""
    env = m.group(1) if (m := _GIT_WORK_TREE_ENV.search(seg)) else carried
    out = []
    for call in calls:
        sub, args = call.sub, call.args
        if sub not in _GIT_TREE_WRITERS:
            continue
        ops = [a for a in args if not a.startswith("-")]
        if (sub == "reset" and not any(a in ("--hard", "--merge", "--keep") for a in args)) or (sub == "stash" and ops[:1] and ops[0] in ("list", "show", "create", "store")):
            continue
        if sub == "apply" and any(a in ("--cached", "--check", "--stat", "--numstat", "--summary") for a in args):
            continue
        base = "."
        for d in call.dirs:
            base = os.path.join(base, _expand_home(_word(d)))
        wt = call.work_tree or call.config_work_tree or env
        if sub == "clone":
            dest = ops[-1] if len(ops) >= 2 else (os.path.basename(ops[0].rstrip("/")).removesuffix(".git") if ops else "")
            out.append((os.path.join(base, _expand_home(_word(dest))), True))
        elif sub == "worktree":
            if ops[:1] == ["add"] and len(ops) >= 2:
                out.append((os.path.join(base, _expand_home(_word(ops[1]))), True))
        else:
            out.append((os.path.join(base, _expand_home(_word(wt))) if wt else base, True))
    return out


def _ho_shell_segment(a: Assessment, seg: str, bare: str, calls: list[shellnorm.GitCall], toks: list[str], parent_too: bool, dirs: tuple[str, ...], cwd: str,
                      sroots: tuple[str, ...], links: list[str], into: list[tuple[str, bool, bool, bool]], git_work_tree: str | None) -> None:
    """HO-shell-startup for one shell segment: the paths it writes, the files it hard-links, the trees it copies or unpacks, the git work trees it writes.
    `source F` reads F: only its redirects count (PRE-10)."""
    if not sroots:
        return
    if bare.split(" ", 1)[0] in _SOURCE_VERBS:
        toks = [_word(t) for t in REDIRECT_TARGET.findall(bare)]
    targets = [(t, parent_too, False) for t in toks] + [(s, True, False) for s in links] + [(p, par, ud) for p, par, _, ud in into]
    targets += [(p, par, False) for p, par in _git_tree_targets(seg, calls, git_work_tree)]
    for path, parent, unless_dir in targets:
        for d in dirs or ("",):
            base = d or cwd
            if unless_dir and os.path.isdir(os.path.join(base or os.getcwd(), _expand_home(path))):
                continue   # `cp -r src dest` into a directory that exists writes `dest/src`, counted on its own
            if _si12_hit(path, base, sroots, parent):
                _ho_add(a, HO_SHELL)
                return


def _si12_tool_paths(a: Assessment, subjects: list[str], cwd: str) -> None:
    if paths.level() is None or "SI-12" in a.blocked:
        return
    roots = _config_roots()
    if any(_si12_hit(s, cwd, roots) for s in subjects):
        a.blocked.append("SI-12")


_REDIRECT_PREFIX = re.compile(r"^(?:\d+|&)?(?:<>|>>?)[|&]?")


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
        tok = _REDIRECT_PREFIX.sub("", tok.strip("'\"`,;()"))   # `3>>F`, `&>F`, `>|F`: the file after the operator (PRE-7)
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
    if cmd is None:   # a tool that carries no command is classed by what its name does; one that carries a command was read as that command above
        for ident in sorted(humanonly.tool_classes(tool) | (humanonly.tool_classes(f"{tool}__{tool_input['action']}") if isinstance(tool_input.get("action"), str)
                                                              and re.fullmatch(r"[\w-]{1,40}", tool_input["action"]) else set())):
            _ho_add(a, ident)
    if is_mcp(tool) and _mcp_reads_the_seal_key(tool, tool_input, cwd):   # V6-18 c: an MCP file reader or search that can reach the permit seal key (SI-10)
        _raise_to(a, Assessment(level="critical", rule_id="SI-10", rationale="MCP call that can read the permit seal key", reversibility="irreversible",
                                blast_radius="service", data_sensitivity="secret", blocked=["SI-10"]))
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
    r13 = _r13()   # W-82: a copy into a directory, a glob or a variable-held destination: R-13 reads the paths it writes (adds a reading, never lowers one)
    if LEVELS[seg_best["level"]] < LEVELS[r13["level"]] and (dir_dest_hit(r13, bare, dirs_t) or unseen_into_dedicated(bare, dirs_t)
                                                              or ("xargs" in seg and unseen_into_dedicated(seg, dirs_t))):   # `bare` has lost the launcher `xargs`
        seg_best = r13
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


_BRACES = "\x06\x06"   # how the segmenter spells the default `{}` of `xargs -I{}`, `xargs -i` and `find -exec ... {}`
_STDIN_PATH = re.compile(r"[\w./~$@+,:=%{}-]+")
_XARGS_VALUE_OPTS = frozenset({"-n", "-L", "-P", "-s", "-d", "-E", "-a", "-S", "-R", "--arg-file", "--max-args", "--max-lines", "--max-procs", "--max-chars", "--delimiter",
                               "--eof"})   # options whose value is the next word when it is not attached
_XARGS_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_XARGS_FETCHERS = frozenset({"curl", "wget"})
XARGS_STDIN_CAP = 8   # xargs segments, and instruction paths named elsewhere, that are paired one by one; above that a writer `xargs` is rated R-18 whole


def _xargs_inner(seg: str) -> tuple[str, str | None] | None:
    """`xargs [options] command ...` as written in one segment: the command it runs (the words after its options) and its replace string
    (`{}` is the segmenter's `\\x06\\x06`; `-I s`, `-Is`, `-J s`, `--replace=s`, `-i[s]`), None when there is none. None when the segment is not an `xargs`."""
    words = seg.split(" ")
    start = next((i for i, w in enumerate(words[:7]) if w.rsplit("/", 1)[-1] == "xargs"), None)
    if start is None:
        return None
    i, repl = start + 1, None
    while i < len(words) and words[i].startswith("-") and words[i] not in ("-", "--"):
        o = words[i]
        if o.startswith("--replace"):
            repl = o.partition("=")[2] or _BRACES
        elif o[:2] in ("-I", "-J"):
            if o[2:]:
                repl = o[2:]
            elif i + 1 < len(words):
                i, repl = i + 1, words[i + 1]
        elif o[:2] == "-i":
            repl = o[2:] or _BRACES
        elif o in _XARGS_VALUE_OPTS:
            i += 1
        i += 1
    if i < len(words) and words[i] == "--":
        i += 1
    inner = " ".join(w for w in words[i:] if w)
    return (inner, repl) if inner else None


def _put(text: str, repl: str, path: str) -> str:
    """`text` with the replace string at the start of a word (or after `>`, `<`, `=`) made `path`. One in the middle of a word (`/tmp/backup/{}`) is a name that was listed,
    never a whole path, and stays as it is."""
    return re.sub(r"(?<![^\s<>=])" + re.escape(repl), lambda _: path, text)


def _verb(command: str) -> str:
    return system_bare(_bare(command)).split(" ")[0].rsplit("/", 1)[-1]


def _xargs_stdin_readings(rows: list[tuple[str, tuple[str, ...]]]) -> tuple[list[tuple[str, tuple[str, ...], str]], str | None]:
    """The commands an `xargs` runs when the instruction-file paths the rest of the command names (an `echo`, a `printf`, an `ls -d`, a `find`) are what reaches
    it on standard input (W-77, R-18): `echo ~/.claude/skills/n.md | xargs touch` is `touch ~/.claude/skills/n.md`. Returns (segment, directories, the real `xargs` segment)
    for each reading, and the first writer `xargs` segment when the pairing was cut at the cap (`XARGS_STDIN_CAP`), else None. Only a reading is added, the segment itself is
    classified as before.

    A path word is one the instruction-file pattern of R-18 matches (a directory without its slash too), taken from every segment that is not an `xargs`
    and holds no replace string. A writer verb (`WRITER_VERBS`) gets the path in the place of the replace string, or after its last word when it has none. A shell
    (`xargs -I{} sh -c 'echo x > {}'`) is read through the segment of its quoted body that holds the `{}`, the segmenter makes it one. Whole-command scope: the segmenter does not
    say which segment feeds which, so a path named in another pipeline counts too (the safe direction)."""
    xs = [(seg, dirs, got) for seg, dirs in rows if (got := _xargs_inner(seg))]
    if not xs:
        return [], None
    rx = _skills_rule_re()
    holders = [seg for seg, _ in rows if _BRACES in seg and not _xargs_inner(seg)]
    paths_: list[str] = []
    for seg, _ in rows:
        if _xargs_inner(seg) or _BRACES in seg:
            continue
        for w in _bare(seg).split(" "):
            w = w.strip("\"'")
            if w and w not in paths_ and _STDIN_PATH.fullmatch(w) and rx.search(w if w.endswith("/") else w + "/"):
                paths_.append(w)
    if not paths_:
        return [], None
    writer = next((seg for seg, _, (inner, _) in xs if _verb(inner) in WRITER_VERBS or _verb(inner) in _XARGS_SHELLS), None)
    out: list[tuple[str, tuple[str, ...], str]] = []
    for seg, dirs, (inner, repl) in xs[:XARGS_STDIN_CAP]:
        verb = _verb(inner)
        for p in paths_[:XARGS_STDIN_CAP]:
            if verb in WRITER_VERBS and (repl and repl in inner or verb not in _XARGS_FETCHERS):   # the words a fetcher is fed are addresses, never the file it writes
                out.append((_put(inner, repl, p) if repl and repl in inner else f"{inner} {p}", dirs, seg))
            if repl == _BRACES and verb in _XARGS_SHELLS:
                out.extend((_put(h, _BRACES, p), dirs, seg) for h in holders)
    return out, writer if len(xs) > XARGS_STDIN_CAP or len(paths_) > XARGS_STDIN_CAP else None


def _carried_work_tree(seg: str, cur: str | None) -> str | None:
    """GIT_WORK_TREE after a segment that sets it on its own (`export GIT_WORK_TREE=~`, `GIT_WORK_TREE=~`) or unsets it; any other segment leaves it.
    A plain assignment counts too, although only an exported one reaches git: the safe direction."""
    words = seg.split(" ")
    first = words[0]
    if first == "unset" and "GIT_WORK_TREE" in words[1:]:
        return None
    if first in shellnorm.EXPORTISH or all(shellnorm.ENV_ASSIGN.match(w) for w in words if w):
        if m := _GIT_WORK_TREE_ENV.search(seg):
            return m.group(1)
    return cur


_VAR_USE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")
_VAR_VALUE = re.compile(r"[\w./+:=@%,~${}-]*")   # a plain literal, or one built from variables an earlier part set: no substitution, no space, no glob
ENV_VALUES = 4   # values kept for one variable: the last that were set (`D=~/.claude; false && D=/tmp/o; cp x $D/` is read with each)
ENV_NAMES = 32   # variables kept per command
ENV_LENGTH = 400   # a value longer than this is not kept: `B=$A$A$A; C=$B$B$B` grows by a factor at every step


def _subst_vars(text: str, env: dict[str, list[str]], k: int = 0) -> str:
    """`text` with each `$NAME` or `${NAME}` that an earlier part of the command set to a literal written as that value (W-82): the k-th of its values from the last one, or
    the first it has. One that is not known stays."""
    def value(m: re.Match) -> str:
        vals = env.get(m.group(1) or m.group(2))
        return m.group(0) if not vals else vals[-1 - k] if k < len(vals) else vals[0]
    return _VAR_USE.sub(value, text)


def _subst_readings(text: str, env: dict[str, list[str]]) -> list[str]:
    """The command written with the last value of each variable, then with the one before it, and so on: every reading that differs from the text itself."""
    names = {g for m in _VAR_USE.finditer(text) for g in m.groups() if g in env}
    rounds = max((len(env[n]) for n in names), default=0)
    return list(dict.fromkeys(r for k in range(rounds) if (r := _subst_vars(text, env, k)) != text))


def _note_assignments(seg: str, env: dict[str, list[str]]) -> None:
    """A segment that is only `NAME=value` words (or `export NAME=value`) gives a variable a value. A value that is a substitution, has a space or a glob, is too long, or
    is not a plain literal is not kept. Every plain value is kept (the last `ENV_VALUES`), and neither an unknown value nor `unset` forgets one: a branch may or may not
    have run, and the reading is only ever added."""
    words = [w for w in seg.split(" ") if w]
    while words and words[0] in ("then", "do", "else", "if", "while", "until", "!", "time"):   # `if x; then D=~/.claude; fi`
        words = words[1:]
    if words and words[0] in shellnorm.EXPORTISH:
        words = [w for w in words[1:] if not w.startswith("-")]
    if not words or not all(shellnorm.ENV_ASSIGN.match(w) for w in words):
        return
    for w in words:
        name, val = shellnorm.ENV_ASSIGN.match(w).groups()
        if not _VAR_VALUE.fullmatch(val) or len(val) > ENV_LENGTH or (name not in env and len(env) >= ENV_NAMES):
            continue
        val = _subst_vars(val, env)
        if len(val) <= ENV_LENGTH:
            vals = env.setdefault(name, [])
            if val in vals:
                vals.remove(val)
            vals.append(val)
            del vals[:-ENV_VALUES]


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
    gated_rules: list[str] = []
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
    si13 = False   # a Python payload runner that starts real shells without the sandbox (docs/13)
    ho = Assessment(level="low", rule_id="", rationale="")   # collects the human-only classes of the segments
    sql_client = False   # a segment runs a database client: its statement is quoted text, so the raw command is read for it below
    wroots, mroots, sroots = _wiring_roots(), _mod_roots(), _shell_roots()
    git_wt: str | None = None   # GIT_WORK_TREE an earlier segment exported or assigned (PRE2-6)
    rows: list[tuple[str, tuple[str, ...]]] = []   # every segment, read-only ones too: what an `xargs` is fed is read from them (W-77)
    subst_seen: dict[tuple, dict] = {}
    env: dict[str, list[str]] = {}   # `D=~/.claude` earlier in this command: a later `$D` is read as its value too (W-82)
    for seg, dirs in shellnorm.segments_cwd(scan, cwd, literal_end=len(text_in)):   # a script the command writes keeps `'$X'` live (PRE2-1)
        rows.append((seg, dirs))
        _note_assignments(seg, env)
        git_wt = _carried_work_tree(seg, git_wt)
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
        if len(words) > 1 and words[0].rsplit("/", 1)[-1] == "gh" and words[1] == "api" and _gh_api_merges(words, text_in):
            marks.append(GH_MERGE_MARK)   # the query is quoted text, so the whole command is read, as for a SQL client below
        seg_labels = [lab for lab in (blind.script_label(words, known), "computed-cwd" if computed and _computed_write(_bare(seg)) else None) if lab]
        labels.extend(seg_labels)
        marks.extend(f"{BLIND_MARK}{lab} " for lab in seg_labels)
        seg_best, bare = classify_segment(seg, dirs, proxied, tuple(marks))
        if env and "$" in seg and LEVELS[seg_best["level"]] < LEVELS[_r13()["level"]]:   # W-82: the command as the shell will run it; only R-13 is taken from this reading
            for known in _subst_readings(seg, env):
                key = (known, dirs, tuple(marks))   # the same command with the same value comes back many times in a long text: classified once
                if key not in subst_seen:
                    subst_seen[key] = classify_segment(known, dirs, proxied, tuple(marks))[0]
                if subst_seen[key]["id"] == "R-13":
                    seg_best = subst_seen[key]
                    break
        if best is None or LEVELS[seg_best["level"]] > LEVELS[best["level"]]:
            best = seg_best
        if si12_roots and not si12 and _si12_segment(seg, dirs, cwd, si12_roots):
            si12 = True
        _ho_segment(ho, seg, dirs, cwd, wroots, mroots, sroots, git_wt)
        sql_client = sql_client or humanonly.is_sql_client(_bare(seg))
        if sf := blind.script_file(words):
            if not si13 and _si13_words(words, sf, dirs, cwd):
                si13 = True
            if HO_STATE not in ho.ho_ids and _script_names_state(sf, dirs, cwd):
                _ho_add(ho, HO_STATE)
            for ident in sorted(_script_ho(sf, dirs, cwd)):
                _ho_add(ho, ident)
        key = _segment_key(bare)
        keys.append(key)
        if LEVELS[seg_best["level"]] >= LEVELS[permit_floor()]:
            gated_keys.append(key)
            gated_rules.append(seg_best["id"])
    if "xargs" in scan:   # W-77: the instruction-file paths another segment names are what a writer `xargs` is fed (R-18); only ever adds a reading
        readings, cut = _xargs_stdin_readings(rows)
        found = [(classify_segment(xseg, xdirs, proxied)[0], real) for xseg, xdirs, real in readings]
        if cut:   # more pairs than the cap: a writer `xargs` next to instruction paths is rated whole
            found.append((next(r for r in risk()["rules"] if r["id"] == "R-18"), cut))
        for r, real in found:
            if best is None or LEVELS[r["level"]] > LEVELS[best["level"]]:
                best = r
            if LEVELS[r["level"]] >= LEVELS[permit_floor()]:
                gated_keys.append(_segment_key(_bare(real)))
                gated_rules.append(r["id"])
    best = best or risk()["rules"][-1]
    if labels and best["level"] == "low":   # R-31 is low, so it never wins a level comparison: nothing above low matched, and what the gate could not read is the finding
        best = next(r for r in risk()["rules"] if r["id"] == "R-31")
    a = _from_rule(best, loader.environment_of(risk(), text_in))
    a.recorded = secret_read and bool(best.get("reads"))
    a.unread = sorted(dict.fromkeys(labels))
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
    if si13 and "SI-13" not in a.blocked:
        a.blocked.append("SI-13")
    if sql_client and humanonly.sql_destroys(text_in):
        _ho_add(ho, HO_DELETE)
    a.ho_ids = list(ho.ho_ids)
    if HO_STATE not in a.ho_ids and any(_STATE_LITERAL.search(e) for e in extra):   # a script the command runs names the JADE state paths: what it does there is not the gate's to read
        _ho_add(a, HO_STATE)
    if a.rule_id == "R-17" or "R-17" in gated_rules:   # V6-17 review: also a second command (`ssh host 'git config core.hooksPath x'`, `a; git config core.hooksPath x`), not only the top rule
        _ho_add(a, HO_R17)
    _seal(a, tool, subject, cwd, subject)
    a.hold_key = sha256(canonical({"shell": sorted(set(gated_keys or keys))}))
    a.seg_keys, a.seg_rules = sorted(set(gated_keys)), sorted(set(gated_rules))
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


_REALPATHS: dict[str, str] | None = None   # set for the length of one `assess` only


def _realpath(path: str) -> str:
    """`os.path.realpath`, remembered for the length of one `assess`: a command with many `cd` segments asks for the same paths in every check (the cost of a deep path is quadratic)."""
    if _REALPATHS is None:
        return os.path.realpath(path)
    got = _REALPATHS.get(path)
    if got is None:
        got = _REALPATHS[path] = os.path.realpath(path)
    return got


def assess(tool: str, tool_input: dict, cwd: str = "") -> Assessment:
    """Classify one tool call. Raises on malformed policy; callers decide how to fail."""
    global _REALPATHS, _GLOBS, _DESTS, _GLOB_SPENT
    outer, _REALPATHS = _REALPATHS, {} if _REALPATHS is None else _REALPATHS
    outer_globs, _GLOBS = _GLOBS, {} if _GLOBS is None else _GLOBS
    outer_dests, _DESTS = _DESTS, {} if _DESTS is None else _DESTS
    outer_spent, _GLOB_SPENT = _GLOB_SPENT, [0] if _GLOB_SPENT is None else _GLOB_SPENT
    try:
        return _assess(tool, tool_input, cwd)
    finally:
        _REALPATHS, _GLOBS, _DESTS, _GLOB_SPENT = outer, outer_globs, outer_dests, outer_spent


def _assess(tool: str, tool_input: dict, cwd: str) -> Assessment:
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
