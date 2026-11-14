"""Plain first lines for the question the human answers (clear questions, step 1).

The chat question (`askchat.question_for`) and the macOS dialog (`askdialog.text_for`) start with these lines: what will happen, why it is risky, and what
else is in the call. The full command comes after them. Every text here comes from the gate (the rule, the canonical gated segments), never from the agent.
The owner reads simple English: short sentences, one idea each, at most 20 words.
"""

from __future__ import annotations

import re

from . import policy, shellnorm

# One plain sentence per rule id of policies/risk.yaml (tests check that none is missing and that none is longer than 20 words).
RISK_TEXT: dict[str, str] = {
    "R-01": "This changes the firewall, and only you can decide that.",
    "R-02": "This does a destructive action with admin rights on a boot service or a disk.",
    "R-03": "This changes a live deployment, and you cannot fully undo it.",
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
    "R-20": "This rewrites or drops local git state, and the reflog can undo it.",
    "R-21": "This sends a file or data to another machine, so the data leaves this Mac.",
    "R-22": "This runs text that is downloaded or decoded while it runs, so nobody can read it first.",
    "R-23": "This edits a git config file, which can make git run a program later.",
    "R-24": "This changes the dependencies or the build of JADE itself.",
    "R-30": "This reads a file that holds a credential, and it is only recorded.",
    "R-31": "This runs a script or text that the gate cannot read, and it is only recorded.",
    "R-32": "This gives a script or a computed folder to a command that normally only reads.",
    "R-90": "This is a routine commit, checkout, install or download, and you can undo it.",
    "R-99": "This only reads, edits a local file, or runs a test or build.",
    "M-01": "This changes a live cluster through a tool, and you cannot fully undo it.",
    "M-02": "This changes a repository or a pipeline through a tool, for example a merge or a commit.",
    "M-03": "This deletes, moves, writes or stops files or processes through a tool.",
    "M-04": "This changes agent permissions, settings, schedules or connectors, or starts another session.",
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
    (r"^gh pr merge\s+\S*/pull/(\d+)\b", r"Merge pull request \1."),
    # Only flags that take no value, or `--name=value`, may come before the number: in `--body 5 940` the 5 is the body, so that call says "Merge a pull request."
    (r"^gh pr merge(?:\s+(?:--squash|-s|--merge|-m|--rebase|-r|--delete-branch|-d|--admin|--auto|--disable-auto|--[\w-]+=\S*))*\s+(\d+)(?:\s|$)",
     r"Merge pull request \1."),
    (r"^gh pr merge\s+([A-Za-z][\w./-]*)(?:\s|$)", r"Merge the pull request of branch \1."),
    (r"^gh pr merge(?:\s+-[-\w=]+)*\s*$", "Merge the pull request of this branch."),
    (r"^gh pr merge\b", "Merge a pull request."),
    (r"^glab mr merge(?:\s+-[-\w=]+)*\s+(\d+)(?:\s|$)", r"Merge merge request \1."),
    (r"^glab mr merge(?:\s+-[-\w=]+)*\s*$", "Merge the merge request of this branch."),
    (r"^glab mr merge\b", "Merge a merge request."),
    (r"^gh release create\b", "Create a release on GitHub."),
    (r"^gh release merge\b", "Merge a release on GitHub."),
    (r"^npm publish\b", "Publish a package to npm."),
    (r"^cargo publish\b", "Publish a crate."),
    (r"^twine upload\b", "Upload a package to PyPI."),
    (r"^docker push\b", "Push a Docker image."),
    (r"^composio execute\b", "Run an action in a connected app."),
    (r"^git push\b[^\n]*\s(?:--force\S*|-f)(?:\s|$)", "Force push to a remote."),
    (r"^git push\b[^\n]*\s(?:--delete|-d)(?:\s|$)", "Delete a branch or tag on a remote."),
    (r"^git push\b[^\n]*\s--tags?(?:\s|$)", "Push tags to a remote."),
    (r"^git push\b[^\n]*\s(?:main|master)(?:\s|$)", "Push to the main branch."),
    (r"^git push\b", "Push to a remote."),
    (r"^git reset\b[^\n]*--ha", "Throw away local changes and move the branch."),
    (r"^git clean\b", "Delete files that git does not track."),
    (r"^git branch\b[^\n]*\s-[a-zA-Z]*[dD]\b", "Delete a local branch."),
    (r"^rm\s+-[a-zA-Z]*r[a-zA-Z]*f|^rm\s+-[a-zA-Z]*f[a-zA-Z]*r", "Delete files and folders for good."),
    (r"^git:hooks-bypass |^git:unsafe-config |^git config\b", "Change git settings."),
    (r"^(?:pfctl|iptables|nft|ufw)\b", "Change the firewall."),
    (r"^sudo\b", "Run a command with admin rights."),
    (r"^(?:launchctl|systemctl|brew services)\b", "Change a service on this machine."),
    (r"^docker(?: compose)? (?:up|down|rm)\b", "Start, stop or remove containers."),
    (r"^(?:kubectl|helm|terraform|ansible-playbook|fly deploy|vercel)\b", "Change a live deployment."),
    (r"^(?:ssh|scp|sftp|rsync)\b", "Connect to another machine."),
    (r"^egress:upload ", "Send data to a network host."),
    (r"^fetch:exec ", "Run text that was downloaded."),
    (r"^gh gist\b", "Publish a gist on GitHub."),
    (r"^crontab\b", "Change the scheduled jobs of this user."),
)]
FALLBACK_CHARS = 60
SHELL_WORDS = frozenset({"do", "done", "then", "else", "elif", "fi", "esac"})
_NONPIPE_SPLIT = re.compile(r"\n|;|&&|\|\||(?<![<>])&(?!>)|\$\(|`|\(|\)|\{|\}")   # shellnorm.SPLIT without the single pipe: a pipeline is one command


def _render(key: str) -> str:
    for rx, text in SEGMENT_TEXT:
        if m := rx.search(key):
            return m.expand(text)
    return "Run: " + key[:FALLBACK_CHARS]


def what_line(a, tool: str) -> str:
    """One plain sentence for what will happen. A shell call is read from its gated segments, any other tool from its name."""
    keys = list(getattr(a, "seg_keys", None) or [])
    if keys:
        return " ".join(dict.fromkeys(_render(k) for k in keys))
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        return f"Use {parts[1]}: {parts[-1].replace('_', ' ')}." if len(parts) > 2 else f"Use the tool {tool}."
    if tool in ("Write", "MultiEdit", "NotebookEdit"):
        return "Write a file."
    if tool == "Edit":
        return "Edit a file."
    return "Run a shell command." if tool in ("Bash", "PowerShell") else f"Use the tool {tool}."


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
