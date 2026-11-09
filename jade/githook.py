"""The structural half of invariant SI-08 (never push to the default branch): a standalone pre-push hook and a check that a repository runs it.

Reading shell can never be complete (here-string quote escapes, redirections before the command, wrappers: docs/05 T18). git itself knows the one
thing that matters, the remote ref about to be updated, and tells a pre-push hook on stdin. `jade git-hook script` prints a hook that reads those
lines and refuses a push whose remote ref is the remote's default branch, in every spelling of the refspec (`HEAD:main`, `+main`, `refs/heads/main`,
a delete `:main`). The hook is plain POSIX sh with no dependency on jade at run time. Installing it is a human step (docs/08 section 2.7): it
changes a repository's hooks or the global git configuration, which an agent session must not do."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

MARKER = "jade-pre-push-guard"
VERSION = 1

HOOK_SCRIPT = f"""#!/bin/sh
# {MARKER} v{VERSION}: refuses a push to the default branch of the remote (JADE invariant SI-08).
# Standalone: needs only git and a POSIX sh. Printed by `jade git-hook script`; not installed by jade.
# git runs this with the remote name and URL as arguments and one line per ref on stdin:
#   <local ref> <local sha> <remote ref> <remote sha>
# The remote ref is what the push really updates, whatever the refspec looked like (HEAD:main, +main, refs/heads/main, a delete).
# Protected: main and master always, the branch refs/remotes/<remote>/HEAD points at (the remote's default branch) when it is known, init.defaultBranch,
# and every branch named by `git config --add jade.protectedBranch <name>` (for a default branch with another name and no remote-tracking HEAD:
# `git remote set-head <remote> --auto` records it, or name it once with jade.protectedBranch).
# A human who means it uses `git push --no-verify`; JADE rates that command high so an agent needs a permit for it.

remote=$1
lower() {{ printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }}

# one name per line: what is protected besides main and master
default=$(git config --get-all jade.protectedBranch 2>/dev/null)
init_default=$(git config --get init.defaultBranch 2>/dev/null)
[ -n "$init_default" ] && default=$default'
'$init_default
case $remote in
  ''|*[!A-Za-z0-9._/-]*) ;;   # a URL or an odd name: no remote-tracking HEAD to read, the other names still count
  *)
    head_ref=$(git symbolic-ref -q "refs/remotes/$remote/HEAD" 2>/dev/null) && default=$default'
'${{head_ref#refs/remotes/$remote/}}
    ;;
esac
is_protected() {{
  [ "$1" = main ] || [ "$1" = master ] && return 0
  while IFS= read -r p; do
    [ -n "$p" ] && [ "$1" = "$(lower "$p")" ] && return 0
  done <<PROTECTED_EOF
$default
PROTECTED_EOF
  return 1
}}

input=$(cat)
refused=
action=push
while read -r local_ref local_sha remote_ref remote_sha; do
  case $remote_ref in
    refs/heads/*) ;;
    *) continue ;;
  esac
  branch=${{remote_ref#refs/heads/}}
  name=$(lower "$branch")
  if is_protected "$name"; then
    refused=$branch
    case $local_sha in
      *[!0]*) ;;
      *) action=delete ;;
    esac
    break
  fi
done <<PUSH_REFS_EOF
$input
PUSH_REFS_EOF

if [ -n "$refused" ]; then
  echo "jade pre-push guard: refusing to $action the default branch '$refused' of '$remote'." >&2
  echo "jade pre-push guard: push a feature branch and open a pull request. A human who really means it runs git push --no-verify." >&2
  exit 1
fi

# Installed through core.hooksPath this file replaces the repository's own .git/hooks/pre-push: run that one too when it is a different file.
common=$(git rev-parse --git-common-dir 2>/dev/null)
repo_hook=$common/hooks/pre-push
if [ -n "$common" ] && [ -x "$repo_hook" ] && ! cmp -s "$0" "$repo_hook" 2>/dev/null; then
  printf '%s\\n' "$input" | "$repo_hook" "$@"
  exit $?
fi
exit 0
"""


def script() -> str:
    return HOOK_SCRIPT


def _git(repo: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10, check=False)
    return r.returncode, r.stdout.strip()


def check(repo: str | os.PathLike) -> tuple[bool, list[str]]:
    """(guarded, report lines): does the pre-push hook git would run for this repository carry the JADE guard?

    The directory is what git itself reports for `hooks` (`git rev-parse --git-path hooks`), so a `core.hooksPath` from the repository, the global
    or the system configuration is honoured, including the one husky sets. Read-only: nothing is installed or changed."""
    repo = Path(repo).expanduser()
    lines: list[str] = []
    try:
        code, _ = _git(repo, "rev-parse", "--git-dir")
        if code != 0:
            return False, [f"not a git repository: {repo}"]
        code, top = _git(repo, "rev-parse", "--show-toplevel")
        base = Path(top) if code == 0 and top else repo   # hooks run from the top of the work tree (a bare repository: its own directory)
        code, hooks_dir = _git(base, "rev-parse", "--git-path", "hooks")
    except (OSError, subprocess.SubprocessError) as e:
        return False, [f"git could not be run ({type(e).__name__})"]
    if code != 0 or not hooks_dir:
        return False, ["the hooks directory could not be resolved"]
    hooks = Path(hooks_dir) if os.path.isabs(hooks_dir) else base / hooks_dir
    try:
        _, origin = _git(base, "config", "--show-origin", "--get", "core.hooksPath")
    except (OSError, subprocess.SubprocessError):
        origin = ""
    lines.append(f"hooks directory: {hooks}" + (f" (core.hooksPath, {origin.split(chr(9))[0]})" if origin else " (default)"))
    hook = hooks / "pre-push"
    if not hook.is_file():
        lines.append("pre-push: missing, a push to the default branch is not stopped by git (docs/08 section 2.7)")
        return False, lines
    try:
        text = hook.read_text(errors="replace")
    except OSError as e:
        return False, [*lines, f"pre-push: cannot be read ({type(e).__name__})"]
    if MARKER not in text:
        lines.append("pre-push: present but it is not the JADE guard (another hook manager or a custom hook); a push to the default branch is not stopped by it")
        return False, lines
    if not os.access(hook, os.X_OK):
        lines.append("pre-push: carries the JADE guard but is not executable, git skips it (chmod +x)")
        return False, lines
    same = hashlib.sha256(text.encode()).digest() == hashlib.sha256(HOOK_SCRIPT.encode()).digest()
    lines.append(f"pre-push: JADE guard v{VERSION} installed" + ("" if same else " (differs from `jade git-hook script`: an older or edited copy)"))
    lines.extend(_default_branch_lines(base))
    return True, lines


def _default_branch_lines(base: Path) -> list[str]:
    """What the guard protects in this repository besides main and master, and a warning when a remote's default branch is not known to it."""
    try:
        _, remotes = _git(base, "remote")
        _, extra = _git(base, "config", "--get-all", "jade.protectedBranch")
        _, init = _git(base, "config", "--get", "init.defaultBranch")
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    named = [n for n in dict.fromkeys([*extra.splitlines(), init]) if n]
    if named:
        out.append("also protected by name (jade.protectedBranch, init.defaultBranch): " + ", ".join(named))
    for remote in remotes.split():
        try:
            code, ref = _git(base, "symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD")
        except (OSError, subprocess.SubprocessError):
            continue
        if code == 0 and ref:
            out.append(f"remote {remote}: default branch {ref.rsplit('/', 1)[-1]} (refs/remotes/{remote}/HEAD) is protected")
        else:
            out.append(f"remote {remote}: default branch not recorded, only main, master" + (" and the names above" if named else "") + f" are protected; if it has another name run `git remote set-head {remote} --auto` or `git config --add jade.protectedBranch <name>`")
    return out
