"""Risk assessment of a proposed tool call against policies/risk.yaml and policies/safety.yaml.

`policies.loader` holds the merge rules and raw first-match classification. This module adds what a
real shell command needs: it classifies per simple command after `shellnorm` has resolved escapes, quoting,
git global options and hidden command text, ignores heredoc bodies and quoted prose that no shell would run,
and treats read-only commands as low (see the `read_only` list in risk.yaml).
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache

from policies import loader

from . import paths, shellnorm
from .util import LEVELS, canonical, fold, sha256

NULL_REDIRECT = shellnorm.NULL_REDIRECT
SPLIT = shellnorm.SPLIT
PREFIX = shellnorm.PREFIX
WRITE_REDIRECT = re.compile(r">>?\s*\S")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell", "Monitor")  # built-in tools that run a command line: classified like Bash
# Every built-in tool the PreToolUse matcher names (ops.pretool_matcher adds `mcp__.*`). Agent/Task need no gate: the tool calls a subagent
# makes run the same hook. CronCreate/CronDelete only schedule a prompt that later runs under the same hooks, so they are not gated either.
GATED_BUILTIN = (*SHELL_TOOLS, *WRITE_TOOLS, "RemoteTrigger")
OLD_KEYS = {"old_string", "old_str"}  # text that is being removed is never scanned as content
PATH_KEYS = ("source", "destination", "src", "dest", "dst", "from", "to", "old_path", "new_path", "dir", "directory", "folder")
UNSAFE_GIT = shellnorm.UNSAFE_GIT  # marker prefix R-15 matches; carried only in the text a rule sees
PATH_RULES = ("R-01", "R-04", "R-12", "R-13", "R-16")  # the rules about where a write lands: they also see relative operands made absolute
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


def tool_subject(tool: str, tool_input: dict) -> str:
    """The text a gate decision is about: the command, or the target path(s)."""
    if tool in WRITE_TOOLS:
        return "\n".join(write_paths(tool_input))
    if is_mcp(tool):
        return mcp_command(tool_input) or ""
    return str(tool_input.get("command") or "")


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


def reset_cache() -> None:
    _risk.cache_clear()
    _safety.cache_clear()


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
        if tok.startswith("-"):
            tok = tok.partition("=")[2]
        tok = tok.replace("\x01", " ")
        if not tok or tok.startswith(("$", "@", '"')) or tok in ("&&", "||") or "git:unsafe" in tok:
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


def _path_rule(text: str, files: str | None = None, alt: dict[str, str] | None = None) -> dict | None:
    """The first path rule that matches. `alt` gives a rule its own `files` text (R-16 reads the operands without the directories that only look
    like JADE's package, see `_jade_package`)."""
    if not text and not alt:
        return None
    return next((r for r in risk()["rules"] if r["id"] in PATH_RULES and (t := alt[r["id"]] if alt and r["id"] in alt else _seen_by(r, text, files))
                 and r["_re"].search(t)), None)


def _program_basename(text: str) -> str:
    """The segment with the directory of the program it runs taken off (`~/.local/bin/jade check` is `jade check`): running a protected file is not
    writing it. Launcher prefixes (sudo, env, VAR=x, uv run) are left alone."""
    head = m.end() if (m := PREFIX.match(text)) else 0
    word, sep, rest = text[head:].partition(" ")
    return text[:head] + word.rsplit("/", 1)[-1] + sep + rest if "/" in word and not word.startswith(("-", ">")) else text


def _first_match(text: str, files: str | None = None) -> dict:
    for r in risk()["rules"]:
        if (t := _seen_by(r, text, files)) and r["_re"].search(t):
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
    ops = [w for w in words[1:] if w and not w.startswith("-") and not w.startswith(">")]
    if verb in COPY_VERBS and ops and not any(w in ("-t", "--target-directory") or w.startswith("--target-directory=") or (w.startswith("-") and not w.startswith("--") and "t" in w[1:]) for w in words[1:]):
        return f"{ops[-1]} {redirects}".strip()
    if verb == "tar" and len(words) > 2:
        flags = words[1]
        if (re.fullmatch(r"-?[a-zA-Z]*c[a-zA-Z]*", flags) and "f" in flags) or "--create" in words[1:]:  # creating an archive: only the archive is written
            archive = words[2] if re.fullmatch(r"-?[a-zA-Z]*f[a-zA-Z]*", flags) and not words[2].startswith("-") else None
            if archive:
                return f"{archive} {redirects}".strip()
    return None


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


def _assess_write(tool: str, tool_input: dict, cwd: str) -> Assessment:
    subjects = write_paths(tool_input) or [""]
    best_rule, envs = None, []
    for subject in subjects:
        norm = normalise_path(subject, cwd)
        cands = [subject, *([norm] if norm and norm != subject else [])]
        for c in cands:
            r = _first_match(c)
            if best_rule is None or LEVELS[r["level"]] > LEVELS[best_rule["level"]]:
                best_rule = r
            envs.append(loader.environment_of(risk(), c))
    env = "production" if "production" in envs else "staging" if "staging" in envs else "local"
    a = _from_rule(best_rule, env)
    if env == "production" and LEVELS[a.level] < LEVELS["high"]:
        a.level, a.rule_id, a.rationale = "high", "R-ENV", "Write under a production path"
    subject = "\n".join(subjects)
    _seal(a, tool, subject, "", subject)
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
        for p in _path_args(tool_input):
            norm = normalise_path(p, cwd)
            hit = next((r for r in (_path_rule(p), _path_rule(norm)) if r), None)
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


def classify_segment(seg: str, dirs: tuple[str, ...]) -> tuple[dict, str]:
    """The rule one simple command (a segment of `shellnorm.segments_cwd`) matches, and the command with launcher prefixes and git options gone.

    The command is looked at as written and with its operands made absolute for every directory it may run in; a `files_only` rule (R-13, R-16)
    reads only what the command writes (`files_view`)."""
    real_bare = _bare(seg)
    seg = _use_identity(seg)
    bare, calls = _analyse(seg)
    texts = [seg, bare, _pathfold(bare), *([UNSAFE_GIT + bare] if any(c.danger for c in calls) else [])]
    view = files_view(bare)  # what a `files_only` rule may look at: None = the whole segment
    seg_best = None
    pairs = [(t, view if view is not None else _program_basename(t)) for t in dict.fromkeys(texts)]
    if view:
        pairs.append((bare, _pathfold(view)))
    for text, files in pairs:
        rule = _first_match(text, files)
        if seg_best is None or LEVELS[rule["level"]] > LEVELS[seg_best["level"]]:
            seg_best = rule
    abs_ops = _absolute_operands(seg, dirs)
    abs_files = abs_ops if view is None else _absolute_operands("x " + view, dirs) if view else ""
    abs_pkg = _absolute_operands(seg if view is None else "x " + view, dirs, package=True) if view != "" else ""
    if (rule := _path_rule(abs_ops, abs_files, {"R-16": abs_pkg})) and LEVELS[rule["level"]] > LEVELS[seg_best["level"]]:
        seg_best = rule
    return seg_best, real_bare


def _assess_shell(tool: str, subject: str, cwd: str) -> Assessment:
    best: dict | None = None
    active: list[str] = []
    keys: list[str] = []
    gated_keys: list[str] = []
    text_in = subject.replace("\x00", "")  # a shell ignores a NUL byte in a command; the plan hash still covers the text as written
    for seg, dirs in shellnorm.segments_cwd(text_in, cwd):
        if is_read_only(seg):
            continue
        active.append(seg)
        seg_best, bare = classify_segment(seg, dirs)
        if best is None or LEVELS[seg_best["level"]] > LEVELS[best["level"]]:
            best = seg_best
        key = _segment_key(bare)
        keys.append(key)
        if LEVELS[seg_best["level"]] >= LEVELS[permit_floor()]:
            gated_keys.append(key)
    best = best or risk()["rules"][-1]
    a = _from_rule(best, loader.environment_of(risk(), text_in))
    for inv in safety()["invariants"]:
        if "_re" not in inv:
            continue
        haystacks = text_views(text_in) if inv.get("match_on") == "raw" else active + [_bare(s) for s in active]
        if any(inv["_re"].search(h) for h in haystacks):
            a.blocked.append(inv["id"])
    _seal(a, tool, subject, cwd, subject)
    a.hold_key = sha256(canonical({"shell": sorted(set(gated_keys or keys))}))
    return a


def _segment_key(bare: str) -> str:
    """One simple command with launcher prefixes and git global options gone (the caller passes `_bare`), the program named without its directory,
    whitespace collapsed: `/usr/local/bin/fly  deploy` and `FOO=1 fly deploy` are the same command."""
    words = bare.split(" ")
    words[0] = words[0].rsplit("/", 1)[-1]
    return " ".join(w for w in words if w)


def assess(tool: str, tool_input: dict, cwd: str = "") -> Assessment:
    """Classify one tool call. Raises on malformed policy; callers decide how to fail."""
    if tool in WRITE_TOOLS:
        a = _assess_write(tool, tool_input, cwd)
    elif is_mcp(tool) or tool == "RemoteTrigger":
        a = _assess_tool(tool, tool_input, cwd)
    else:
        return _assess_shell(tool, str(tool_input.get("command") or ""), cwd)
    a.hold_key = a.hold_key or a.plan_hash
    _scan_content(a, tool_input, read_only=is_mcp(tool) and mcp_command(tool_input) is None and _tool_rule(tool, False).get("id") in ("M-06", "M-06a"), cwd=cwd)
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
