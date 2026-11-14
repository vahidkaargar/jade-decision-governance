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
  a flag outside the list (delete, admin, auto, another repo, force) gives no scope at all.

Never offered: a human-only class, a blocked call, the never rules, a call the gate could not read, an inline program, and two or more risky commands in one call.
No scope covers delete, force, tag or release.

The project is the repository root the call acts in: a leading `cd PATH &&` or else the working folder, resolved with `os.path.realpath` and walked up to a `.git`
entry (a folder or a worktree file). No subprocess runs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import memory, policy, redact, shellnorm

MERGE_FLAGS_ALLOWED = frozenset({"--squash", "-s", "--merge", "-m", "--rebase", "-r", "--subject", "-t", "--body", "-b", "--body-file", "-F", "--match-head-commit"})
MERGE_FLAGS_WITH_VALUE = frozenset({"--subject", "-t", "--body", "-b", "--body-file", "-F", "--match-head-commit"})
LABEL_MAX = 120
_ELLIPSIS = "…"

# `cd PATH &&` at the very start. PATH is a literal: double quotes without an expansion, single quotes, or a bare word without a shell character.
_LEADING_CD = re.compile(
    r"""^\s*cd\s+(?:"(?P<dq>[^"$`\\]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s"'$`\\;&|()<>*?\[\]{}!#]+))\s*&&\s*""")
_OTHER_CD = re.compile(r"(?:^|[\s;&|(`{])(?:cd|pushd|popd)(?=[\s;&|)`}]|$)")
# Options that move a git command to another repository. The segment key drops them, so the working folder says nothing about where the call acts.
_ELSEWHERE = re.compile(r"(?:^|\s)(?:-C|--git-dir|--work-tree)(?=[\s=]|$)|(?:^|[\s;&|(])(?:GIT_DIR|GIT_WORK_TREE)=")
_DIGITS = re.compile(r"^[0-9]+$")


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
    return not memory._INLINE.search(a.subject or "")


def _merge_project(a, tool: str, tool_input: dict | None, project: str | None) -> str | None:
    if project is None or not _is_plain_shell_call(a, tool, tool_input):
        return None
    keys = list(getattr(a, "seg_keys", None) or [])
    if len(keys) != 1 or not _merge_segment(keys[0].split(" ")):
        return None
    return memory.sha256(memory.canonical({"kind": "merge_project", "family": "gh pr merge N", "project": project}))


def _kind_project_key(a, project: str) -> str:
    family = sorted({memory.normalise_segment(k) for k in a.seg_keys})
    return memory.sha256(memory.canonical({"kind": "kind_project", "family": family, "project": project}))


def _label(command: str, where: str) -> str:
    """`COMMAND in WHERE` on one line, at most LABEL_MAX characters. A path too long to fit is cut from the left (`…/tail`); the command stays visible."""
    command = re.sub(r"\s+", " ", redact.redact_obj(command, secrets_only=True)[0]).strip()
    command = command[:LABEL_MAX // 2 + 20]
    room = LABEL_MAX - len(command) - len(" in ")
    if len(where) > room:
        where = _ELLIPSIS + where[len(where) - max(room - 1, 1):] if room > 1 else _ELLIPSIS
    return (f"{command} in {where}")[:LABEL_MAX]


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
            out.append(Scope("kind_project", _kind_project_key(a, project), _label(family, project),
                             "The same kind of command in this project only. Other projects still ask.", "Same command, this project"))
        out.append(Scope("kind_any", memory.class_key(a), _label(family, "any project"),
                         "The same kind of command in any project. Delete, force, tag and release still ask.", "Same command, any project"))
    if (key := _merge_project(a, tool, tool_input, project)) is not None:
        out.append(Scope("merge_project", key, _label("gh pr merge *", project),
                         "Merging any pull request in this project, with the safe options only. Everything else still asks.", "Merge in this project"))
    return out


def keys_for(a, tool: str, tool_input: dict | None, fp: dict | None, cwd: str) -> list[str]:
    """The keys of the scopes `offered` would return: what a lookup matches a standing scope against. A call with a new flag has another key or none."""
    return [s.key for s in offered(a, tool, tool_input, fp, cwd)]
