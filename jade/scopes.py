"""What a gated call may be generalised to (clear-questions plan, task 3).

The owner can propose that an answer covers more than the one call. This module decides how far, and nothing else: it reads the assessment of the current call,
never the ledger. A scope is a key computed from the call in front of the gate, so a call that does not fit (another project, a flag that was not allowed, a second
risky command) gets another key or none, and a standing scope can never cover more than it was confirmed for.

Three kinds, at most two offered per call:
- `kind_project`: the same kind of call (`memory.class_key` family) in one named project. Only for a call `memory.eligibility` calls `class`, and only when the
  project is known.
- `kind_any`: the same kind of call in any project. Its key is `memory.class_key`, so it is the key memory already matches.
- `merge_project`: `gh pr merge N` with the flags in MERGE_FLAGS_ALLOWED, in one named project. `memory.eligibility` calls every merge exact-only; this scope narrows
  that on purpose, and it is computed here from the segment text, never from `eligibility`. Any PR number, any allowed flag and any flag value is the same scope;
  a flag outside the list (delete, admin, auto, another repo, force, a body file) gives no scope at all. The key also holds the repository the folder points to:
  the `remote` and `url` sections of its git config file (`_repo_identity`), so `gh repo set-default` or `git remote set-url` gives another key.

Never offered: a human-only class, a blocked call, the never rules, a call the gate could not read, an inline program, and two or more risky commands in one call.
No scope covers delete, force, tag or release.

The project is the repository root the call acts in: a leading `cd PATH &&` or else the working folder, resolved with `os.path.realpath` and walked up to a `.git`
entry (a folder or a worktree file). No subprocess runs.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass

from . import memory, policy, redact, shellnorm

# `--body-file`/`-F` are not on the list: they read a local file and upload it to GitHub as the merge commit body, so a standing scope would let the agent send any file.
MERGE_FLAGS_ALLOWED = frozenset({"--squash", "-s", "--merge", "-m", "--rebase", "-r", "--subject", "-t", "--body", "-b", "--match-head-commit"})
MERGE_FLAGS_WITH_VALUE = frozenset({"--subject", "-t", "--body", "-b", "--match-head-commit"})
LABEL_MAX = 120
CONFIG_MAX = 1 << 20   # a git config file larger than this is not read: no merge scope
# What each scope allows, in simple English. The chat question and `jade review confirm` (jade/ops.py) both show this text, so they say the same thing.
COVERS: dict[str, str] = {
    "kind_project": "The same kind of command in this project. The same exact call is also remembered.",
    "kind_any": "The same kind of command in any project. Delete, force, tag and release still ask.",
    "merge_project": "Merging any pull request in this project, with the listed options only. The same exact call is also remembered.",
}
# A section header of a git config file: `[name]`, `[name "sub"]` or `[name.sub]`.
_SECTION = re.compile(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]')
_ELLIPSIS = "…"

# `cd PATH &&` at the very start. PATH is a literal: double quotes without an expansion, single quotes, or a bare word without a shell character.
_LEADING_CD = re.compile(
    r"""^\s*cd\s+(?:"(?P<dq>[^"$`\\]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s"'$`\\;&|()<>*?\[\]{}!#]+))\s*&&\s*""")
_OTHER_CD = re.compile(r"(?:^|[\s;&|(`{])(?:cd|pushd|popd)(?=[\s;&|)`}]|$)")
# Options that move a git command to another repository. The segment key drops them, so the working folder says nothing about where the call acts.
_ELSEWHERE = re.compile(r"(?:^|\s)(?:-C|--git-dir|--work-tree)(?=[\s=]|$)|(?:^|[\s;&|(])(?:GIT_DIR|GIT_WORK_TREE)=")
_DIGITS = re.compile(r"^[0-9]+$")
_EXPANDS = re.compile(r"[$`*?\[~{]")   # a word the shell may expand or split: a variable can hold `-d`, a glob or a brace can add words


@dataclass(frozen=True)
class Scope:
    kind: str     # "kind_project", "kind_any" or "merge_project"
    key: str      # what a standing answer is matched by
    label: str    # one line, at most 120 characters, secrets removed; the owner sees it in `jade knows list`
    covers: str   # simple English: what this scope would allow
    short: str    # simple English: a few words for a button or a menu line


def _real(path: str) -> str | None:
    try:
        return policy.real(path)
    except (OSError, ValueError):
        return None


def project_of(subject: str, cwd: str) -> str | None:
    """The repository root a call acts in, or None when it cannot be told from the text. Only a leading `cd PATH &&` moves the folder; a variable, a subshell, `pushd`,
    a second `cd`, a git option that names another repository, a missing folder or a path with no `.git` entry all give None."""
    if not cwd or not subject:
        return None
    base, rest = cwd, subject
    if m := _LEADING_CD.match(subject):
        path = next(v for v in (m.group("dq"), m.group("sq"), m.group("bare")) if v is not None)
        if m.group("bare") is not None and path.startswith("~"):
            if path != "~" and not path.startswith("~/"):
                return None   # `~user` is not read
            path = os.path.expanduser(path)
        if not path:
            return None
        base = path if os.path.isabs(path) else os.path.join(cwd, path)
        rest = subject[m.end():]
    skeleton = shellnorm.skeleton(rest)
    if _OTHER_CD.search(skeleton) or _ELSEWHERE.search(shellnorm.skeleton(subject)):
        return None
    here = _real(base)
    if here is None or not os.path.isdir(here):
        return None
    while True:
        if os.path.lexists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def _git_dir(project: str) -> str | None:
    """The git folder of a project: its `.git` folder, or the folder a linked worktree's `.git` file names (`gitdir: PATH`, relative to the project)."""
    dot = os.path.join(project, ".git")
    if os.path.isdir(dot):
        return dot
    if not os.path.isfile(dot):
        return None
    with open(dot, encoding="utf-8") as f:
        first = f.read(4096).splitlines()[:1]
    if not first or not first[0].startswith("gitdir:"):
        return None
    gitdir = first[0][len("gitdir:"):].strip()
    if not gitdir:
        return None
    return gitdir if os.path.isabs(gitdir) else os.path.join(project, gitdir)


def GLOBAL_CONFIGS() -> list[str]:
    """The user-level git config files, read at each call so a changed HOME or XDG_CONFIG_HOME is seen: `~/.gitconfig` and `$XDG_CONFIG_HOME/git/config`
    (default `~/.config/git/config`). `$GIT_CONFIG_GLOBAL` is not read: when it is set, `_repo_identity` gives no merge scope."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return [os.path.join(os.path.expanduser("~"), ".gitconfig"), os.path.join(xdg, "git", "config")]


# System-level files git may read on this Mac (Apple, Homebrew, Xcode). A module-level tuple so a test can replace it.
SYSTEM_CONFIGS: tuple[str, ...] = (
    "/etc/gitconfig", "/opt/homebrew/etc/gitconfig", "/usr/local/etc/gitconfig", "/opt/local/etc/gitconfig",
    "/Library/Developer/CommandLineTools/usr/share/git-core/gitconfig",
    "/Applications/Xcode.app/Contents/Developer/usr/share/git-core/gitconfig",
)
# Any of these changes which config or repository git or gh uses, in a way a file read cannot see: no merge scope.
GIT_ENV_OVERRIDES = ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG", "GIT_CONFIG_NOSYSTEM", "GIT_DIR",
                     "GIT_COMMON_DIR", "GIT_WORK_TREE", "GH_REPO", "GH_HOST", "GH_CONFIG_DIR")


def SHELL_STARTUP_FILES() -> list[str]:
    """The shell startup files the Bash tool's shell may read (zsh and bash), computed at each call. A line in one of them (`export GH_REPO=...`) changes what `gh`
    does without a trace in this process's environment, so `_repo_identity` hashes them: an edit changes the merge key. `$ZDOTDIR` (default `~`) for zsh, plus
    `$BASH_ENV` and `$ENV` when set."""
    home = os.path.expanduser("~")
    zdot = os.environ.get("ZDOTDIR") or home
    files = [os.path.join(zdot, n) for n in (".zshenv", ".zprofile", ".zshrc", ".zlogin")] + ["/etc/zshenv", "/etc/zprofile", "/etc/zshrc"]
    files += [os.path.join(home, n) for n in (".bashrc", ".bash_profile", ".bash_login", ".profile")] + ["/etc/profile", "/etc/bashrc"]
    files += [v for v in (os.environ.get("BASH_ENV"), os.environ.get("ENV")) if v]
    return files


_MISSING = object()


def _read_regular(path: str) -> bytes | object | None:
    """The bytes of a regular file (at most CONFIG_MAX). `_MISSING` when nothing is there. None for anything else that exists: a FIFO, a device, a directory, an
    unreadable file, a file over CONFIG_MAX. The file is opened without blocking and checked with fstat, so a FIFO never stalls the gate."""
    if not os.path.lexists(path):
        return _MISSING
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return _MISSING   # a dangling symlink is not a file that can change what runs
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(CONFIG_MAX + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return None if len(data) > CONFIG_MAX else data


def _shell_identity() -> list[str] | None:
    """One line `shell<TAB>PATH<TAB>SHA256` per existing startup file; None when one exists but is not a readable regular file of at most CONFIG_MAX bytes."""
    out: list[str] = []
    for path in SHELL_STARTUP_FILES():
        data = _read_regular(path)
        if data is _MISSING:
            continue
        if data is None:
            return None
        out.append(f"shell\t{path}\t{hashlib.sha256(data).hexdigest()}")
    return out


def _config_lines(path: str, keep_sections: tuple[str, ...]) -> list[str] | None:
    """The lines of the `keep_sections` of one git config file, each as `SECTION<TAB>line`. `[]` when the file does not exist. None when it exists but cannot be
    read, is not UTF-8, is over CONFIG_MAX, has a header this reader does not know, or includes another file (whose lines it would not see). No subprocess."""
    data = _read_regular(path)
    if data is _MISSING:
        return []
    if data is None:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    out: list[str] = []
    section, keep = "", False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            m = _SECTION.match(line)
            if not m:
                return None
            name = m.group(1).lower()
            base = name.split(".", 1)[0]
            if base in ("include", "includeif"):
                return None
            section = name if m.group(2) is None else f'{name} "{m.group(2)}"'
            keep = base in keep_sections
            rest = line[m.end():].strip()
            if keep and rest and rest[0] not in "#;":
                out.append(f"{section}\t{rest}")
            continue
        if keep:
            out.append(f"{section}\t{line}")
    return out


def _repo_identity(project: str) -> list[str] | None:
    """Which repository a merge in `project` acts on: every line of the `remote` and `url` sections of its git config file (remote URLs, `gh-resolved` from
    `gh repo set-default`, an `insteadOf` rewrite), plus the same lines of the global and system git config files (prefixed `global` / `system`: an `insteadOf` there
    rewrites the remote too), sorted. Files are read as plain text; no subprocess runs. A worktree's git folder names the shared folder in `commondir`, and the config
    is there. The shell startup files (SHELL_STARTUP_FILES: where `export GH_REPO=...` would hide) add one `shell` line each with the SHA-256 of the file, so any edit
    changes the key. None (so no merge scope) when an env override is set (GIT_ENV_OVERRIDES), or any file cannot be read, is not a regular file, is not UTF-8
    (config files), is too large, has a header this reader does not know, or includes another file. Other sections (a branch, core, user) do not change the result."""
    if any(os.environ.get(name) for name in GIT_ENV_OVERRIDES):
        return None
    try:
        gitdir = _git_dir(project)
        if gitdir is None:
            return None
        common = gitdir
        pointer = os.path.join(gitdir, "commondir")
        if os.path.isfile(pointer):
            with open(pointer, encoding="utf-8") as f:
                rel = f.read(4096).strip()
            if not rel:
                return None
            common = rel if os.path.isabs(rel) else os.path.join(gitdir, rel)
        config = os.path.join(common, "config")
        if not os.path.isfile(config):
            return None
        # A per-worktree config (`extensions.worktreeConfig`) can add or change a remote, and this reader does not merge it: no merge scope (fail closed).
        if os.path.lexists(os.path.join(gitdir, "config.worktree")) or os.path.lexists(os.path.join(common, "config.worktree")):
            return None
    except (OSError, ValueError):
        return None
    out = _config_lines(config, ("remote", "url"))
    if out is None:
        return None
    for label, paths in (("global", GLOBAL_CONFIGS()), ("system", SYSTEM_CONFIGS)):
        for path in paths:
            lines = _config_lines(path, ("remote", "url"))
            if lines is None:
                return None
            out.extend(f"{label}\t{line}" for line in lines)
    shell = _shell_identity()
    if shell is None:
        return None
    return sorted(out + shell)


def _merge_segment(tokens: list[str]) -> bool:
    """`gh pr merge <digits> [allowed flags]`: a flag that takes a value consumes the next token, so a number there is a value and not the PR number."""
    if len(tokens) < 4 or tokens[:3] != ["gh", "pr", "merge"] or not _DIGITS.match(tokens[3]):
        return False
    i = 4
    while i < len(tokens):
        t = tokens[i]
        if not t.startswith("-"):
            return False
        name, eq, _ = t.partition("=")
        if name not in MERGE_FLAGS_ALLOWED or (eq and name not in MERGE_FLAGS_WITH_VALUE):
            return False
        if name in MERGE_FLAGS_WITH_VALUE and not eq:
            if i + 1 >= len(tokens) or tokens[i + 1].startswith("-"):
                return False   # no value, or a value that reads as another flag: not worth guessing
            i += 1
        i += 1
    return True


def _is_plain_shell_call(a, tool: str, tool_input: dict | None) -> bool:
    """A shell call the gate read whole, that no rule keeps from a human, and that holds no inline program."""
    if not policy.runs_shell(tool, tool_input or {}):
        return False
    if a.ho_ids or a.blocked or a.blind or getattr(a, "unread", None) or a.level == "critical" or a.rule_id in memory.NEVER_RULES:
        return False
    return not memory.INLINE.search(a.subject or "")


def _simple_command_words(subject: str) -> list[str] | None:
    """The words of the call when, after a leading `cd PATH &&`, it is exactly one simple command: no separator, pipe, background `&`, group or second command of any
    kind (a gated segment is not the only one that can change what a merge does: an export, a function, a remote change is not gated). None otherwise."""
    m = _LEADING_CD.match(subject)
    skeleton = shellnorm.skeleton(subject[m.end():] if m else subject)
    if shellnorm.SPLIT.search(skeleton):
        return None
    words = skeleton.split()
    return None if not words or any(_EXPANDS.search(w) for w in words) else words


def _merge_project(a, tool: str, tool_input: dict | None, project: str | None) -> str | None:
    """The key of the merge scope, or None. The segment key is not enough: it drops `NAME=value` prefixes, `env`, `sudo` and the directory of the program, so the
    words are read from the call itself. The program must be exactly `gh`, with no prefix, and no word may be one the shell expands or splits."""
    if project is None or not _is_plain_shell_call(a, tool, tool_input):
        return None
    keys = list(getattr(a, "seg_keys", None) or [])
    words = _simple_command_words(a.subject or "")
    if len(keys) != 1 or words is None or not _merge_segment(words):
        return None
    remotes = _repo_identity(project)
    if remotes is None:
        return None   # the repository cannot be told: fail closed
    return memory.sha256(memory.canonical({"kind": "merge_project", "family": "gh pr merge N", "project": project, "remotes": remotes}))


def _kind_project_key(a, project: str) -> str:
    family = sorted({memory.normalise_segment(k) for k in a.seg_keys})
    return memory.sha256(memory.canonical({"kind": "kind_project", "family": family, "project": project}))


def _one_line(text: str) -> str:
    """One printable line: every control character (ESC, BEL, a line break) becomes a space, then each run of spaces becomes one."""
    return re.sub(r"\s+", " ", "".join(c if c.isprintable() else " " for c in text)).strip()


def _label(command: str, where: str) -> str:
    """`COMMAND in WHERE` on one line, at most LABEL_MAX characters. A path too long to fit is cut from the left (`…/tail`); the command stays visible. Every run of
    whitespace, a newline in a folder name included, becomes one space: the label is shown in the chat question and in `jade review confirm` and must not add a line."""
    command = _one_line(redact.redact_obj(command, secrets_only=True)[0])
    command = command[:LABEL_MAX // 2 + 20]
    where = _one_line(where)
    room = LABEL_MAX - len(command) - len(" in ")
    if len(where) > room:
        where = _ELLIPSIS + where[len(where) - max(room - 1, 1):] if room > 1 else _ELLIPSIS
    return _one_line(f"{command} in {where}")[:LABEL_MAX]


def _family(a) -> str:
    return " ; ".join(sorted({memory.normalise_segment(k) for k in a.seg_keys}))


def offered(a, tool: str, tool_input: dict | None, fp: dict | None, cwd: str) -> list[Scope]:
    """The scopes the owner may be offered for this call, at most two. Empty when the call may only be answered as itself."""
    cwd = cwd or getattr(a, "plan_cwd", "") or ""
    out: list[Scope] = []
    project = project_of(a.subject or "", cwd) if _is_plain_shell_call(a, tool, tool_input) else None
    if memory.eligibility(a, tool, tool_input, fp) == "class":
        family = _family(a)
        if project:
            out.append(Scope("kind_project", _kind_project_key(a, project), _label(family, project), COVERS["kind_project"], "Same command, this project"))
        out.append(Scope("kind_any", memory.class_key(a), _label(family, "any project"), COVERS["kind_any"], "Same command, any project"))
    if (key := _merge_project(a, tool, tool_input, project)) is not None:
        out.append(Scope("merge_project", key, _label("gh pr merge *", project), COVERS["merge_project"], "Merge in this project"))
    return out


def keys_for(a, tool: str, tool_input: dict | None, fp: dict | None, cwd: str) -> list[str]:
    """The keys of the scopes `offered` would return: what a lookup matches a standing scope against. A call with a new flag has another key or none."""
    return [s.key for s in offered(a, tool, tool_input, fp, cwd)]
