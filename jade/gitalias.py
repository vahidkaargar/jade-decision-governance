"""Git aliases that a config file defines (W-31, docs/05 T18).

`git p` is `git push origin main` when the user's or the repository's config says `alias.p = push origin main`, and the command line does not. The gate reads the
alias table with one `git config --get-regexp ^alias\\.` call and classifies the expansion as if it had been typed, for the risk rules and for SI-08.

* Only a word that is not a built-in git command is looked up: git ignores an alias that hides one (`alias.push = status` never runs), so `git status`, `git log`, `git commit` ...
  never start a process here.
* The call reads config and runs nothing from it. The agent's `GIT_*` environment is dropped (`GIT_CONFIG_GLOBAL`, `GIT_DIR` ... cannot steer it); the system and global
  config are read, because that is where aliases live. One lookup per directory, at most two directories per command, 2 s each (a third directory is answered from the aliases the first two
  returned, not with none); a lookup that fails or times out gives no aliases (nothing is invented), the same answer as a config without any.
* The expansion is followed through other aliases, at most `DEPTH` levels. A `!` alias is a shell command: its body is classified as one, with the arguments appended.

Setting an alias is not read here: `git config alias.x ...` is R-15 (jade/shellnorm.py `config_write_key`).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess

TIMEOUT_S = 2.0   # one config read; hooks.GIT_TIMEOUT_S is the same bound
MAX_DIRS = 2
DEPTH = 5

# `git --list-cmds=builtins` of git 2.54. A command that is not here (`submodule`, `filter-branch`, `lfs`) may be hidden by an alias, so it is looked up.
BUILTIN = frozenset("""
add am annotate apply archive backfill bisect blame branch bugreport bundle cat-file check-attr check-ignore check-mailmap check-ref-format checkout
checkout--worker checkout-index cherry cherry-pick clean clone column commit commit-graph commit-tree config count-objects credential credential-cache
credential-cache--daemon credential-store describe diagnose diff diff-files diff-index diff-pairs diff-tree difftool fast-export fast-import fetch fetch-pack
fmt-merge-msg for-each-ref for-each-repo format-patch fsck fsck-objects fsmonitor--daemon gc get-tar-commit-id grep hash-object help history hook index-pack
init init-db interpret-trailers last-modified log ls-files ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base merge-file merge-index merge-ours
merge-recursive merge-recursive-ours merge-recursive-theirs merge-subtree merge-tree mktag mktree multi-pack-index mv name-rev notes pack-objects
pack-redundant pack-refs patch-id pickaxe prune prune-packed pull push range-diff read-tree rebase receive-pack reflog refs remote remote-ext remote-fd
repack replace replay repo rerere reset restore rev-list rev-parse revert rm send-pack shortlog show show-branch show-index show-ref sparse-checkout stage
stash status stripspace submodule--helper switch symbolic-ref tag unpack-file unpack-objects update-index update-ref update-server-info upload-archive
upload-archive--writer upload-pack var verify-commit verify-pack verify-tag version whatchanged worktree write-tree
""".split())

_NAME = re.compile(r"[A-Za-z][\w.-]*")
_PLAIN = re.compile(r"[\w@%+=:,./^~$-]+")


def candidate(sub: str) -> bool:
    """A subcommand word an alias could be: it is a plain name and no built-in command."""
    return bool(sub) and _NAME.fullmatch(sub) is not None and sub.lower() not in BUILTIN


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(LC_ALL="C", GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def table(directory: str) -> dict[str, str]:
    """`{alias name (lower case): expansion}` for a directory, {} when git cannot say."""
    d = os.path.expanduser(directory) if directory else ""
    try:
        out = subprocess.run(["git", "--no-pager", "config", "--get-regexp", r"^alias\."], capture_output=True, text=True, timeout=TIMEOUT_S,
                             cwd=d if d and os.path.isdir(d) else None, env=_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if out.returncode != 0:
        return {}
    found: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if line.startswith("alias."):
            name, _, value = line[6:].partition(" ")
            found[name.lower()] = value.strip()
    return found


class Aliases:
    """Lookups for one command: each directory is read once."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, str]] = {}

    def get(self, directory: str) -> dict[str, str]:
        if directory not in self._tables:
            if len(self._tables) >= MAX_DIRS:
                # Past the bound no process is started, but "no aliases" would let two decoy directories hide any alias: the aliases already read (the user's own
                # config is in every one of them) stand in, which can only add a match, never lose one.
                merged: dict[str, str] = {}
                for t in self._tables.values():
                    merged.update(t)
                return merged
            self._tables[directory] = table(directory)
        return self._tables[directory]

    def expand(self, sub: str, args: list[str], directory: str) -> str | None:
        """The command an alias stands for (`git <expansion> <args>` or the shell text of a `!` alias), or None when `sub` is no alias."""
        t = self.get(directory)
        seen: set[str] = set()
        words = [sub, *args]
        for _ in range(DEPTH):
            name = words[0].lower()
            value = t.get(name)
            if value is None or name in seen or name in BUILTIN:
                break
            seen.add(name)
            if value.startswith("!"):
                return " ".join([value[1:].strip(), *words[1:]]).strip()
            try:
                expansion = shlex.split(value)
            except ValueError:
                expansion = value.split()
            if not expansion:
                break
            words = [*expansion, *words[1:]]
            if expansion[0].startswith("-"):
                break   # global options first (`-c k=v push`): git_calls reads them
        if not seen:
            return None
        return "git " + " ".join(w if _PLAIN.fullmatch(w) else shlex.quote(w) for w in words)
