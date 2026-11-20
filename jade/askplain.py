"""Plain first lines for the question the human answers (clear questions, step 1).

The chat question (`askchat.question_for`) and the macOS dialog (`askdialog.text_for`) start with these lines: what will happen, why it is risky, and what
else is in the call. The full command comes after them. Every text here comes from the gate (the rule, the canonical gated segments), never from the agent.
The owner reads simple English: short sentences, one idea each, at most 20 words.
"""

from __future__ import annotations

import dataclasses
import re

from . import policy, shellnorm
from .util import show_safely

# One plain sentence per rule id of policies/risk.yaml (tests check that none is missing and that none is longer than 20 words).
RISK_TEXT: dict[str, str] = {
    "R-01": "This changes the firewall, and only you can decide that.",
    "R-02": "This does a destructive action with admin rights on a service that starts with the Mac, or on a disk.",
    "R-03": "This changes a live server or app, and you cannot fully undo it.",
    "R-04": "This touches a key or a secret store, and you cannot undo it.",
    "R-10": "This changes something outside this Mac, such as a merge, a release, a publish or a push.",
    "R-11": "This destroys local history or deletes files, and you cannot undo it.",
    "R-12": "This starts, stops or changes a service on this Mac.",
    "R-13": "This changes the rules, hooks, settings or installed code that guard the agent.",
    "R-14": "This opens a remote shell or copies files to or from another machine.",
    "R-15": "This changes git settings that can run a program or change where git connects.",
    "R-16": "This changes the code of JADE itself, which is the gate.",
    "R-17": "This switches off the guard that stops a push to the main branch.",
    "R-18": "This creates a new skill or agent instruction file that you have not seen.",
    "R-19": "This pushes a named branch or opens a pull request, and it is only recorded.",
    "R-20": "This rewrites or drops local git history, and git can usually bring it back.",
    "R-21": "This sends a file or data to another machine, so the data leaves this Mac.",
    "R-22": "This runs text that is downloaded or decoded while it runs, so nobody can read it first.",
    "R-23": "This edits a git config file, which can make git run a program later.",
    "R-24": "This changes the packages or the build of JADE itself.",
    "R-25": "This deletes the root of the disk and turns off the safety check of rm, and you cannot undo it.",
    "R-26": "This deletes files with the safety check of rm turned off, and you cannot undo it.",
    "R-30": "This reads a file that holds a password or key, and it is only recorded.",
    "R-31": "This runs a script or text that the gate cannot read, and it is only recorded.",
    "R-32": "This gives a script or a computed folder to a command that normally only reads.",
    "R-90": "This is a routine commit, checkout, install or download, and you can undo it.",
    "R-99": "This only reads, edits a local file, or runs a test or build.",
    "M-01": "This changes a live group of servers through a tool, and you cannot fully undo it.",
    "M-02": "This changes a repository or a build job through a tool, for example a merge or a commit.",
    "M-03": "This deletes, moves, writes or stops files or processes through a tool.",
    "M-04": "This changes agent permissions, settings, schedules or links to other apps, or starts another session.",
    "M-05": "This runs another tool or code by name, so the gate cannot see what it runs.",
    "M-06a": "This only changes how the app looks.",
    "M-06b": "This only updates the task list or the progress notes of the app.",
    "M-06": "This tool only reads.",
    "M-07": "This tool has a name that says it changes, runs or sends something.",
    "M-08": "This runs a script in a web page, uploads a file or drives the browser, and it is only recorded.",
    "M-99": "This tool has no rule yet, and it is only recorded.",
}
GENERIC_RISK = "This call needs your decision."


GENERIC_HO = "A rule that only you can decide matches this call."
# What a human-only class (jade/humanonly.py, jade/policy.py HO_*) lets the call do, as the end of "This call ...".
HO_TEXT: dict[str, str] = {
    "HO-delete": "can delete data",
    "HO-money": "can move money",
    "HO-release": "can publish or release something",
    "HO-wiring": "can change how JADE is wired in",
    "HO-checkout-install": "can change the JADE tool that is installed",
    "HO-settings-shadow": "can switch off the hooks in a settings file",
    "HO-state-path": "can touch the state files of JADE",
    "HO-mod": "can change a mod of the app",
    "HO-R17": "can switch off the guard on the main branch",
    "HO-terminal": "can run a script in your terminal",
    "HO-shell-startup": "can change your shell start-up files and so what every command runs",
}


def _mild(rule_id: str) -> bool:
    """Is the rule's own sentence a low or medium one (reads, local edits, recorded only)? Read from the policy, so a local overlay rule counts too."""
    doc = policy.risk()
    for r in (*doc.get("rules", ()), *doc.get("mcp_tools", ())):
        if r.get("id") == rule_id:
            return r.get("level") in ("low", "medium")
    return False


def risk_line(rule_id: str, ho_ids=()) -> str:
    """The plain sentence of one rule, or a generic one when the rule is not known (a rule of a local overlay).
    A call that a human-only class holds but a low or medium rule rated says what the class lets it do: the rule's own sentence ("only reads") would be false."""
    if ho_ids and _mild(rule_id):
        known = [HO_TEXT[i] for i in ho_ids if i in HO_TEXT]
        if len(known) != len(ho_ids):
            return GENERIC_HO
        return f"This call {' and '.join(known)}, and only you can decide that."
    return RISK_TEXT.get(rule_id, GENERIC_RISK)


# What a gated command does, as one sentence. The first pattern that matches the canonical key of a gated segment wins. `{1}` style groups are filled with `m.expand`.
_Render = tuple[re.Pattern, str]
SEGMENT_TEXT: list[_Render] = [(re.compile(p), t) for p, t in (
    (r"^gh pr merge\b[^\n]*\s(?:-R\S*|--repo(?:=\S*)?)(?:\s|$)", "Merge a pull request."),
    (r"^gh pr merge\s+\S*/pull/(\d+)\b", r"Merge pull request \1."),
    # Only flags that take no value, or `--name=value`, may come before the number: in `--body 5 940` the 5 is the body, so that call says "Merge a pull request."
    (r"^gh pr merge(?:\s+(?:--squash|-s|--merge|-m|--rebase|-r|--delete-branch|-d|--admin|--auto|--disable-auto|--[\w-]+=\S*))*\s+(\d+)(?:\s|$)",
     r"Merge pull request \1."),
    (r"^gh pr merge\s+([A-Za-z][\w./-]*)(?:\s|$)", r"Merge the pull request of branch \1."),
    (r"^gh pr merge(?:\s+-[-\w=]+)*\s*$", "Merge the pull request of this branch."),
    (r"^gh pr merge\b", "Merge a pull request."),
    (r"^glab mr merge\b[^\n]*\s(?:-R\S*|--repo(?:=\S*)?)(?:\s|$)", "Merge a merge request."),
    (r"^glab mr merge(?:\s+(?:--squash|-s|--rebase|-r|--remove-source-branch|-d|--yes|-y|--auto-merge|--[\w-]+=\S*))*\s+(\d+)(?:\s|$)", r"Merge merge request \1."),
    (r"^glab mr merge(?:\s+-[-\w=]+)*\s*$", "Merge the merge request of this branch."),
    (r"^glab mr merge\b", "Merge a merge request."),
    (r"^gh release create\b", "Create a release on GitHub."),
    (r"^gh release merge\b", "Merge a release on GitHub."),
    (r"^npm publish\b", "Publish a package to npm."),
    (r"^cargo publish\b", "Publish a crate."),
    (r"^twine upload\b", "Upload a package to PyPI."),
    (r"^docker push\b", "Push a Docker image."),
    (r"^composio execute\b", "Run an action in a connected app."),
    (r"^git reset\b[^\n]*--ha", "Throw away local changes and move the branch."),
    (r"^git clean\b", "Delete files that git does not track."),
    (r"^git branch\b[^\n]*\s-[a-zA-Z]*[dD]\b", "Delete a local branch."),
    (r"^rm\s+-[a-zA-Z]*r[a-zA-Z]*f|^rm\s+-[a-zA-Z]*f[a-zA-Z]*r", "Delete files and folders for good."),
    (r"^git:hooks-bypass |^git:unsafe-config |^git config\b", "Change git settings."),
    (r"^(?:pfctl|iptables|nft|ufw)\b", "Change the firewall."),
    (r"^sudo\b", "Run a command with admin rights."),
    (r"^(?:launchctl|systemctl|brew services)\b", "Change a service on this machine."),
    (r"^docker(?: compose)? (?:up|down|rm)\b", "Start, stop or remove containers."),
    (r"^(?:kubectl|helm|terraform|ansible-playbook|fly deploy|vercel)\b", "Change a live server or app."),
    (r"^(?:ssh|scp|sftp|rsync)\b", "Connect to another machine."),
    (r"^egress:upload ", "Send data to a network host."),
    (r"^fetch:exec ", "Run text that was downloaded."),
    (r"^gh gist\b", "Publish a gist on GitHub."),
    (r"^crontab\b", "Change the scheduled jobs of this user."),
)]
FALLBACK_CHARS = 60
SHELL_WORDS = frozenset({"do", "done", "then", "else", "elif", "fi", "esac"})
_NONPIPE_SPLIT = re.compile(r"\n|;|&&|\|\||(?<![<>])&(?!>)|\$\(|`|\(|\)|\{|\}")   # shellnorm.SPLIT without the single pipe: a pipeline is one command


_NAMED = re.compile(r"[\w./-]+")
_CURRENT = frozenset({"HEAD", "@"})
_PUSH_PLAIN_FLAGS = frozenset({"-u", "--set-upstream", "-v", "--verbose", "-q", "--quiet", "-n", "--dry-run", "--porcelain", "--atomic", "--signed", "--no-verify", "--progress", "--ipv4", "-4", "--ipv6", "-6"})
_PUSH_LETTERS = frozenset("uvqnfd46")
_REMOTE = re.compile(r"[A-Za-z0-9._-]+")
_REFSPEC = re.compile(r"\+?[\w./@~*:-]*")
_WORST_PUSH = "This push can force push, delete branches and tags, and include the main branch."
_SEG_SPLIT = re.compile(r"\n|;|&&|\|\||\|&?|(?<![<>])&(?!>)|\$\(|`|\(|\)|\{|\}")   # shellnorm.SPLIT and the single pipe: every command of the call
_SHELL_SPECIAL = re.compile(r"['\"\\$*?\[]")   # quotes, escapes, variables and glob characters: the shell changes the word before git sees it
_LEAD_CD = re.compile(r"cd [\w./~@%+,:=-]+ ?&& ?([^\s;&|(){}`].*)", re.S)   # `cd PATH && ` only: a plain path word, one `&&`, then the command
_DANGEROUS = ("--force", "--delete", "--mirror", "--all", "--branches", "--prune", "--tags", "--follow-tags")


_MAIN = ("main", "master")
_MANY = "every matching branch to a remote, which can include the main branch"
_CUR_SUFFIX = " It can be the main branch."


def _abbr(name: str, full: str) -> bool:
    """Git accepts a long flag cut short (`--del`). A name of three or more characters (`--` and a letter) that starts a flag counts as every dangerous flag it starts, so `--m` is `--mirror` and `--f` is force and follow-tags. No other option git knows is a prefix of these, so an exact `--atomic` or `--signed` is not touched, even when git would call it ambiguous: the line then names the worse one."""
    return len(name) >= 3 and full.startswith(name)


def _branch(dst: str) -> str:
    dst = dst.removeprefix("refs/heads/")
    return dst.removeprefix("heads/")


def _push_line(key: str) -> str:
    """One plain sentence for a `git push` key. Every refspec is read. When the line cannot be sure, it names the worst outcome. Order of clauses: force, delete, main branch, tags, named branch."""
    force = flag_delete = tags = tags_only = mirror = prune = every = False
    positional: list[str] = []
    unknown = False   # anything the lines below do not read: the worst line is shown
    for tok in key.split()[2:]:
        tok = tok.strip("'\"")
        if tok == "--" or not tok:
            unknown = True
        elif tok.startswith("--"):
            name = tok.split("=", 1)[0]
            dangerous = name.startswith("--force") or any(_abbr(name, d) for d in _DANGEROUS) or name == "--tag"
            if not dangerous and (name not in _PUSH_PLAIN_FLAGS or "=" in tok):
                unknown = True
            force |= _abbr(name, "--force") or name.startswith("--force")
            flag_delete |= _abbr(name, "--delete")
            tags_only |= _abbr(name, "--tags") or name == "--tag"
            tags |= tags_only or _abbr(name, "--follow-tags")
            mirror |= _abbr(name, "--mirror")
            prune |= _abbr(name, "--prune")
            every |= _abbr(name, "--all") or _abbr(name, "--branches")
        elif tok.startswith("-") and len(tok) > 1:
            letters = tok[1:]
            unknown |= not set(letters) <= _PUSH_LETTERS
            force |= "f" in letters
            flag_delete |= "d" in letters
        else:
            positional.append(tok)
    unknown |= bool(positional) and (not _REMOTE.fullmatch(positional[0]) or set(positional[0]) == {"."})   # `.` and `..` are repositories, not remote names
    unknown |= "tag" in positional[1:]   # `tag v1` pushes a tag: not read here
    unknown |= not all(_REFSPEC.fullmatch(t) for t in positional[1:])
    if unknown:
        return _WORST_PUSH
    if mirror:
        return "Push and delete to match every branch and tag."
    pushed: list[str] = []      # destinations that are written
    del_dests: list[str] = []   # destinations that are deleted
    n_tag_delete = 0
    colon_single = False
    cur = not positional[1:] and not tags_only and not flag_delete and not every
    for spec in positional[1:]:
        plus = spec.startswith("+")
        spec = spec.lstrip("+")
        src, colon, dst = spec.partition(":")
        if not colon:
            dst = spec
        force |= plus
        if "*" in spec or (colon and not src and not dst):
            every = True      # a wildcard, or `:` alone (matching branches): not a delete
            continue
        is_delete = flag_delete or (bool(colon) and not src)
        is_tag = dst.startswith("refs/tags/")
        dst = dst[len("refs/tags/"):] if is_tag else _branch(dst)
        colon_single = bool(colon)
        if is_delete:
            del_dests.append(dst)
            n_tag_delete += is_tag
        else:
            pushed.append(dst)
            cur |= dst in _CURRENT
    cur &= not every
    pushed = [d for d in pushed if d not in _CURRENT]
    main_push = any(d in _MAIN for d in pushed)
    main_del = any(d in _MAIN for d in del_dests)
    dele = flag_delete or bool(del_dests)
    prune_text = "delete remote branches that are gone here"
    only_one = len(pushed) == 1 and not del_dests and not cur and bool(_NAMED.fullmatch(pushed[0]))
    if dele and not (force or prune or tags or every or cur or pushed):
        if main_del:
            return "Delete the main branch on a remote." if len(del_dests) == 1 else "Delete branches on a remote, including the main branch."
        if len(del_dests) > 1:
            return "Delete branches on a remote."
        if not flag_delete and del_dests and _NAMED.fullmatch(del_dests[0]):
            return f"Delete the remote {'tag' if n_tag_delete else 'branch'} {del_dests[0]}."
        return "Delete a branch or tag on a remote." if flag_delete else "Delete branches on a remote."
    if main_push and only_one and colon_single and not (force or dele or prune or tags or every):
        return f"Push to {pushed[0]}."
    if prune and not (force or dele or tags or every or main_push):
        return "Push and delete remote branches that are gone here." + (_CUR_SUFFIX if cur else "")
    clauses: list[str] = []
    delete_clause = "delete branches on a remote" + (", including the main branch" if main_del else "")
    if force:
        if every:
            clauses.append("force push " + _MANY)
            if dele:
                clauses.append(delete_clause)
        elif dele or prune:
            clauses.append("force push and " + (delete_clause if dele else prune_text) + (", including the main branch" if main_push and not main_del else ""))
        elif only_one:
            clauses.append(f"force push to {pushed[0]}")
        else:
            where = "the current branch to a remote" if cur and not pushed else "to a remote"
            clauses.append(f"force push {where}" + (", including the main branch" if main_push else ""))
        if prune and (every or dele):
            clauses.append(prune_text)
        if tags:
            clauses.append("push tags to a remote")
    else:
        if dele:
            clauses.append(delete_clause)
        if prune:
            clauses.append(prune_text)
        if every:
            clauses.append("push " + _MANY)
        elif main_push:
            clauses.append("push to the main branch")
        if tags:
            clauses.append("push tags to a remote")
        if not (every or main_push):
            if only_one:
                clauses.append(f"push to {pushed[0]}")
            elif pushed:
                clauses.append("push to a remote")
            elif cur:
                clauses.append("push the current branch to a remote")
            elif not clauses:
                clauses.append("push to a remote")
    text = ", ".join(clauses[:-1]) + (" and " if len(clauses) > 1 else "") + clauses[-1]
    text = text[0].upper() + text[1:] + "."
    if cur and not (main_push or every):
        text += _CUR_SUFFIX
    if len(text.split()) > 20:   # too long to read: keep the worst parts
        parts = [w for w, on in (("force push", force), ("delete branches", dele or prune), ("push tags", tags)) if on]
        text = "This push can " + (", ".join(parts[:-1]) + " and " if len(parts) > 1 else "") + (parts[-1] if parts else "push") + ", and can include the main branch."
    return text


_MERGE_KEY = re.compile(r"^(?:gh pr|glab mr) merge\b")
_SHORT_FLAGS = re.compile(r"-[A-Za-z]+")
_NOT_PLAIN = re.compile(r"[$`*?\[~\x06]")


def _merge_notes(key: str) -> list[str]:
    """What a merge flag lets the call do besides merging, with the flag as written. The first line of the question never says less than the call can do (V6-05): `--admin` skips the
    branch rules, `--delete-branch` (`-d`, also inside a cluster such as `-sd`) deletes the branch of the pull request, `--auto` merges later, when the checks pass. A word that is
    not plain (`$X`, a glob) can be any flag, so the line says that."""
    words = key.split()
    gh = words[0] == "gh"
    flags = {w.split("=", 1)[0] for w in words[3:] if w.startswith("-")}
    letters = "".join(w[1:] for w in words[3:] if _SHORT_FLAGS.fullmatch(w))
    notes = []
    if gh and "--admin" in flags:
        notes.append("skip the branch rules (--admin)")
    if gh and ("--delete-branch" in flags or "d" in letters):
        notes.append("delete its branch (" + ("--delete-branch" if "--delete-branch" in flags else "-d") + ")")
    if not gh and ("--remove-source-branch" in flags or "d" in letters):
        notes.append("delete its branch (" + ("--remove-source-branch" if "--remove-source-branch" in flags else "-d") + ")")
    if gh and "--auto" in flags:
        notes.append("wait for the checks, then merge it (--auto)")
    if not gh:   # glab: `--auto-merge` and `--when-pipeline-succeeds` merge later, when the pipeline passes (a `=false` value turns it off, so it is not a note)
        for name in ("--auto-merge", "--when-pipeline-succeeds"):
            if any(w == name or (w.startswith(name + "=") and w.split("=", 1)[1].lower() != "false") for w in words[3:]):
                notes.append(f"wait for the pipeline, then merge it ({name})")
                break
    if _NOT_PLAIN.search(key):
        notes.append("maybe more, because some words are not plain")
    return notes


def _merge_line(key: str, line: str) -> str:
    notes = _merge_notes(key)
    if not notes:
        return line
    head = line[:-1] if line.endswith(".") else line
    if len(notes) == 1:
        return f"{head} and {notes[0]}."
    if len(notes) == 2:
        return f"{head}, {notes[0]} and {notes[1]}."
    return f"{head}, " + ", ".join(notes[:-1]) + f", and {notes[-1]}."


# V6-26: the line of every other family names what a flag lets the call do besides its base line, with the flag as written. A word that is not plain (`$X`, a glob) can be any
# flag, so the line says that. Each rule reads the words of the canonical key and returns (tails, ands): a tail goes straight after the base sentence ("for every repository"),
# an "and" clause is joined with "and" ("and delete the volumes"). A flag a rule does not know changes nothing here: only a flag that is known to widen the call is named.
_UNPLAIN = re.compile(r"[$`*?\[\x06]")   # like _NOT_PLAIN, but `~` is a path here, not a flag
_Rule = tuple[list[str], list[str]]


def _flags(words: list[str]) -> set[str]:
    return {w.split("=", 1)[0] for w in words if w.startswith("-")}


def _letter(words: list[str], letter: str) -> bool:
    """Is `letter` inside a cluster of short flags (`-fdx`)? A long flag (`--delete`) never counts."""
    return any(_SHORT_FLAGS.fullmatch(w) and letter in w[1:] for w in words)


def _clean(words: list[str]) -> _Rule:
    ands = []
    if _letter(words, "d"):
        ands.append("also delete folders (-d)")
    if _letter(words, "x"):
        ands.append("also delete files that git ignores (-x)")
    if _letter(words, "X"):
        ands.append("also delete files that git ignores (-X)")
    return [], ands


def _config(words: list[str]) -> _Rule:
    flags = _flags(words)
    return [f"for {who} ({flag})" for flag, who in (("--global", "every repository of this user"), ("--system", "every user of this Mac")) if flag in flags], []


def _rsync(words: list[str]) -> _Rule:
    flags = _flags(words)
    ands = []
    if (dels := sorted(f for f in flags if f.startswith("--del") and f != "--delay-updates")):
        ands.append(f"delete files at the target that are not in the source ({dels[0]})")
    if "--remove-source-files" in flags:
        ands.append("delete the source files after the copy (--remove-source-files)")
    return [], ands


def _docker_rm(words: list[str]) -> _Rule:
    if len(words) < 3 or not ({"down", "rm"} & set(words[1:3])):   # `docker compose up`, `docker run`: no volume flag is read
        return [], []
    flags = _flags(words)
    ands = []
    if "--volumes" in flags or _letter(words, "v"):
        ands.append("delete the volumes, which hold data (" + ("--volumes" if "--volumes" in flags else "-v") + ")")
    if "--rmi" in flags:
        ands.append("delete images (--rmi)")
    if "--remove-orphans" in flags:
        ands.append("remove other containers of the project (--remove-orphans)")
    return [], ands


def _live(words: list[str]) -> _Rule:
    """kubectl, helm, terraform and the other tools that change a live server or app."""
    flags = _flags(words)
    ands = []
    if "--all" in flags:
        ands.append("act on every matching object (--all)")
    if "-A" in flags or "--all-namespaces" in flags:
        ands.append("act in every namespace (" + ("--all-namespaces" if "--all-namespaces" in flags else "-A") + ")")
    if "--force" in flags:
        ands.append("force it (--force)")
    if "--prune" in flags:
        ands.append("delete objects that are no longer in the files (--prune)")
    if "-auto-approve" in flags or "--auto-approve" in flags:
        ands.append("not ask you again (" + ("-auto-approve" if "-auto-approve" in flags else "--auto-approve") + ")")
    return [], ands


def _docker_push(words: list[str]) -> _Rule:
    flags = _flags(words)
    return [], (["push every tag of the image (" + ("--all-tags" if "--all-tags" in flags else "-a") + ")"] if "--all-tags" in flags or "-a" in flags else [])


def _crontab(words: list[str]) -> _Rule:
    return [], (["delete all of them (-r)"] if _letter(words, "r") else [])


def _gist(words: list[str]) -> _Rule:
    return [], (["let anyone read it (--public)"] if "--public" in _flags(words) else [])


def _npm(words: list[str]) -> _Rule:
    public = "--access=public" in words or any(a == "--access" and b == "public" for a, b in zip(words, words[1:], strict=False))
    return [], (["let anyone read the package (--access public)"] if public else [])


_SUDO = r"(?:sudo(?: -\S+)*\s+)?"
_WIDEN: list[tuple[re.Pattern, object]] = [(re.compile(p), f) for p, f in (
    (r"^" + _SUDO + r"git clean\b", _clean),
    (r"^" + _SUDO + r"git config\b", _config),
    (r"^" + _SUDO + r"rsync\b", _rsync),
    (r"^" + _SUDO + r"docker(?: compose)? (?:up|down|rm)\b", _docker_rm),
    (r"^" + _SUDO + r"(?:kubectl|helm|terraform|ansible-playbook|fly deploy|vercel)\b", _live),
    (r"^" + _SUDO + r"docker push\b", _docker_push),
    (r"^" + _SUDO + r"crontab\b", _crontab),
    (r"^" + _SUDO + r"gh gist\b", _gist),
    (r"^" + _SUDO + r"npm publish\b", _npm),
)]


def _compose(head: str, tails: list[str], ands: list[str]) -> str:
    text = head[:-1] if head.endswith(".") else head
    text += "".join(" " + t for t in tails)
    if len(ands) == 1:
        text += f" and {ands[0]}"
    elif len(ands) == 2:
        text += f", {ands[0]} and {ands[1]}"
    elif ands:
        text += ", " + ", ".join(ands[:-1]) + f", and {ands[-1]}"
    return text + "."


def _widen_line(key: str, line: str) -> str:
    """The base line of a family, and what a flag of the call lets it do besides that (V6-26). Nothing is added to a plain call."""
    words = key.split()
    for rx, rule in _WIDEN:
        if rx.match(key):
            tails, ands = rule(words)
            if _UNPLAIN.search(key):
                ands.append("maybe more, because some words are not plain")
            return _compose(line, tails, ands) if tails or ands else line
    return line


def _branch_line(key: str, line: str) -> str:
    """`git branch -D a b c` deletes three branches, not one."""
    words = key.split()
    names = [w for w in words[2:] if not w.startswith("-")]
    if len(names) >= 2:
        line = f"Delete {len(names)} local branches."
    return line[:-1] + ", maybe more, because some words are not plain." if _UNPLAIN.search(key) else line


def _render(key: str) -> str:
    if key == "git push" or key.startswith("git push "):
        return _push_line(key)
    for rx, text in SEGMENT_TEXT:
        if m := rx.search(key):
            line = m.expand(text)
            if _MERGE_KEY.match(key):
                return _merge_line(key, line)
            if key.startswith("git branch "):
                return _branch_line(key, line)
            return _widen_line(key, line)
    return "Run: " + show_safely(key[:FALLBACK_CHARS], one_line=True)


def _plain_push_call(subject: str) -> bool:
    """True only when the call is one `git push` command, at most after a leading `cd PATH`, written in plain words: no quote, escape, `$` or glob
    character, no prefix before `git push`, single spaces. Anything else can change what git does before or while it reads the key (a quoted `"git" -c`,
    a `*` the shell expands, a `git config` in the same call), so its push line is the worst one."""
    subject = subject.strip(" ")
    if m := _LEAD_CD.fullmatch(subject):
        subject = m.group(1)
    pieces = [p.strip(" ") for p in _SEG_SPLIT.split(subject) if p.strip(" ")]
    if len(pieces) != 1:
        return False
    seg = pieces[0]
    words = seg.split()
    return words[:2] == ["git", "push"] and seg == " ".join(words) and not _SHELL_SPECIAL.search(seg) and not any(w.startswith("~") for w in words)


def what_line(a, tool: str) -> str:
    """One plain sentence for what will happen. A shell call is read from its gated segments, any other tool from its name."""
    keys = list(getattr(a, "seg_keys", None) or [])
    if keys:
        plain = _plain_push_call(getattr(a, "subject", "") or "")
        return " ".join(dict.fromkeys(_WORST_PUSH if not plain and (k == "git push" or k.startswith("git push ")) else _render(k) for k in keys))
    if tool.startswith("mcp__"):
        parts = show_safely(tool, one_line=True).split("__")
        return f"Use {parts[1]}: {parts[-1].replace('_', ' ')}." if len(parts) > 2 else f"Use the tool {show_safely(tool, one_line=True)}."
    if tool in ("Write", "MultiEdit", "NotebookEdit"):
        return "Write a file."
    if tool == "Edit":
        return "Edit a file."
    return "Run a shell command." if tool in ("Bash", "PowerShell") else f"Use the tool {show_safely(tool, one_line=True)}."


def other_count(a) -> int:
    """How many commands of a shell call are not gated. A pipeline counts once (`gh pr merge N | tail -1` is the merge), `cd` and the words of a shell loop are not commands."""
    keys = set(getattr(a, "seg_keys", None) or [])
    if not keys or not getattr(a, "subject", ""):
        return 0
    n = 0
    for piece in _NONPIPE_SPLIT.split(shellnorm.skeleton(a.subject)):
        stages = [re.sub(r"\s+", " ", s).strip() for s in re.split(r"(?<!>)\|", piece)]
        stages = [s for s in stages if s]
        if not stages:
            continue
        bare = [policy._segment_key(policy._bare(s)) for s in stages]
        first = bare[0].split(" ")[0] if bare[0] else ""
        if any(b in keys for b in bare) or first in ("cd", "pushd", "popd") or (len(stages) == 1 and first in SHELL_WORDS):
            continue
        n += 1
    return max(n, 0)


def header(a, tool: str) -> list[str]:
    """The lines every question starts with, in this order: what, why it is risky, what else is in the call, and a hint when risky commands are joined."""
    lines = [what_line(a, tool), risk_line(a.rule_id, a.ho_ids)]
    n = other_count(a)
    if n:
        lines.append(f"Also in this call: {n} other command that JADE rates as low risk." if n == 1 else f"Also in this call: {n} other commands that JADE rates as low risk.")
    risky = len(getattr(a, "seg_keys", None) or [])
    if risky >= 2:
        lines.append(f"This call joins {risky} risky commands. Ask the agent to send them one by one.")
    return lines


# ---- V6-26: one builder for the three places a human answers (chat question, macOS dialog, terminal permit screen) ----------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Channel:
    """What one place of answering does with an answer and with none. The caller reads the numbers from its own constants when it asks (`askchat.channel()`,
    `askdialog.channel()`, `permits.channel(ttl_s)`), so a changed constant changes the text and no sentence promises a time that is no longer true."""
    name: str                    # "chat", "dialog" or "permit"
    wait_s: int                  # how long the question waits for an answer
    allow_s: int | None = None   # how long an Allow can be used (the chat grant; the permit's own time)
    deny_s: int | None = None    # after a Deny the same call is not asked again for this long
    again_s: int | None = None   # after no answer the same call is not shown again for this long (the dialog)
    deny_cap: int | None = None  # this many Denies of the same call end asking for good (the chat)


@dataclasses.dataclass
class Parts:
    """The pieces of one question. `fields` are the five that policies/escalation.yaml promises. Each channel puts `head`, `where` and `closing` in its own order."""
    fields: dict
    head: list[str]
    where: list[str]
    closing: list[str]
    options: list[dict]

    @property
    def lines(self) -> list[str]:
        return [*self.where, *self.closing]


def duration(seconds: int) -> str:
    """600 -> '10 minutes', 90 -> '90 seconds', 3600 -> '1 hour'."""
    n = int(seconds)
    if n >= 3600 and n % 3600 == 0:
        k, unit = n // 3600, "hour"
    elif n >= 60 and n % 60 == 0:
        k, unit = n // 60, "minute"
    else:
        k, unit = n, "second"
    return f"{k} {unit}" + ("" if k == 1 else "s")


def _no_answer(ch: Channel) -> str:
    if ch.name == "dialog":
        again = f" It is not shown again for {duration(ch.again_s)}." if ch.again_s else ""
        return (f"If you do not answer in {duration(ch.wait_s)}, the dialog closes and the call stays blocked. "
                f"It then needs a permit that you approve in a terminal.{again}")
    if ch.name == "permit":
        return f"If you do not approve it, the call stays blocked. This request expires after {duration(ch.wait_s)}."
    return f"If you do not answer, the call stays blocked. This question expires after {duration(ch.wait_s)}."


def _deny(ch: Channel) -> str:
    if ch.name == "dialog":
        return "Deny: the call does not run." + (f" It is not asked again for {duration(ch.deny_s)}." if ch.deny_s else "")
    if ch.name == "permit":
        return "Type n to refuse this request now. It is revoked and the agent is told when it asks again. A refusal is not a Deny. Type deny to record a Deny."
    text = "Deny: the call does not run and the agent is told."
    if ch.deny_s:
        text += f" The same call is not asked again for {duration(ch.deny_s)}."
    if ch.deny_cap:
        text += f" After {ch.deny_cap} Denies of the same call it is not asked again."
    return text


def _allow(ch: Channel) -> str:
    if ch.name == "permit":
        return f"Approving allows exactly this call once within {duration(ch.allow_s or ch.wait_s)}."
    if ch.name == "dialog":
        return "Allow this one call? Single use, this session only."
    return ("Allow this one call? It covers exactly this text, this tool, this folder and any run settings shown above. "
            f"Single use, {duration(ch.allow_s or ch.wait_s)}, this session only. A changed text, prefix or folder asks again.")


def build(a, tool: str, ch: Channel, *, folder: str, who: str) -> Parts:
    """The text that every channel shares: the plain first lines, where the call runs and who asks, what Allow, Deny and no answer do, and the five fields of
    policies/escalation.yaml (W-83). It states facts only: no advice, no history of earlier answers, no claim that a call can be undone. `folder` and `who` are written out
    safely here. The command itself is not built here: each channel shows it in its own way."""
    from . import thresholds

    folder = show_safely(folder, one_line=True) if folder else "(not given)"   # one line: a folder name with a line break must not forge an "Asked by" or "Allow" line
    who = show_safely(who, one_line=True)[:80]
    head = header(a, tool)
    where = [f"Folder: {folder}", f"Asked by: {who}"]
    if ch.name == "permit":
        options = [{"label": "Allow", "description": f"Approve it: the call can run once within {duration(ch.allow_s or ch.wait_s)}."},
                   {"label": "Deny", "description": "Refuse it: type n to revoke the request, or deny to record a Deny."}]
    else:
        options = [{"label": "Allow", "description": "Run this one call once. It is not remembered."},
                   {"label": "Deny", "description": "Do not run it. The agent is told you said no." + (f" It is not asked again for {duration(ch.deny_s)}." if ch.deny_s else "")}]
    fields = {"context": "\n".join(where), "impact": "\n".join(head), "options": options,
              "recommended_default": "With no answer, the call stays blocked.", "consequence_of_no_answer": _no_answer(ch)}
    missing = [k for k in thresholds.escalation().get("human_question", {}).get("each_question_includes", []) if k not in fields]
    if missing:
        raise ValueError("policies/escalation.yaml asks every question to include " + ", ".join(missing) + ", which the question builder does not make")
    closing = [_allow(ch), _deny(ch), fields["consequence_of_no_answer"]]
    return Parts(fields=fields, head=head, where=where, closing=closing, options=options)
