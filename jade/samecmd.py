"""What a command changes through something it only names or creates itself (W-37, docs/05 T19), returned as commands the classifier reads as if they were typed.

* A patch applied by `patch`, `git apply` or `git am` writes the files its headers name, not the files on the command line. When the patch is a heredoc or a
  here-string of the command, a file the same command writes, a file on disk (`< p`, `-i p`, `git apply p`, `cat p | patch`) or the text of an `echo`/`printf`,
  the headers (`--- a/x`, `+++ b/x`, `diff --git`, `rename to`, `Index:`, context-diff `***`) are read, the strip level, `-d`/`--directory`/`-C` are
  applied, and each target comes back as `(cd DIR && cat > TARGET)`, which every path rule already knows how to read. A patch that cannot be read (a pipe from
  `git diff`, `curl` or any other program, a variable, a glob, a file that does not exist or that another command of the line writes, a file over 1 MiB)
  comes back as `blind:patch-body patch`: R-31, recorded and never gated (the evidence for the human's decision, W-31, W-37).
* A link made by one command and used by the next (`ln -s ~/.claude c && echo x > c/settings.json`): the path is read through the link, in the directory the
  later command runs in. Symbolic and hard links, `-t`, a link into a directory, chains of links. A write that comes before the link exists is not through it.
* `uv add|remove|lock|version`, and `uv sync` with `--upgrade`, `-U` or `--upgrade-package` (they relock), which rewrite `pyproject.toml` and `uv.lock` of the project they run in (R-24 decides whether that project is JADE's checkout).

Text matching on what the shell reads, plus one read of a patch file the command names. Nothing is run. A command this module cannot read exactly gives no
extra (the other readers of the classifier stay in force) except a patch it cannot read, which is recorded.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

from . import blind, inert, shellnorm

BLIND_LABEL = "patch-body"
MAX_PATCH = 1_048_576   # a patch file larger than this is not read: it is recorded
MAX_PATHS = 400         # header paths (before strip variants) read from all the patches of one command
MAX_HITS = 24           # patch invocations read in one command
MAX_DEPTH = 3           # `sh -c` and `$(...)` nest this deep
DYN = "\x00"            # a word that holds an expansion: its value is not known here

_UNWRAP_PLAIN = frozenset({"command", "builtin", "nohup", "time", "exec", "noglob", "{", "!", "then", "do", "else", "elif", "if", "while", "until", "xargs", "caffeinate"})
_SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash"})
_QUICK_PATCH = re.compile(r"\b(?:g?patch|apply|am)\b")
_QUICK_LN = re.compile(r"\bln\b")
_QUICK_UV = re.compile(r"\buv\b")
_OTHER_VAR = re.compile(r"\$(?!HOME(?![\w])|\{HOME\})|`")
_DYNAMIC = re.compile(r"[$`*?\[\]{}\x00]")
_REDIRECT_WORD = re.compile(r"^(?:\d*>>?[|&]?\S*|&>>?\S*|\d*>&\S*|\d*<\S*)$")


def _base(w: str) -> str:
    return w.rsplit("/", 1)[-1]


def _quote(p: str) -> str:
    return shlex.quote(p.replace("\x00", ""))


# ------------------------------------------------------------------ words

def _unwrap(words: list[str]) -> list[str]:
    """The words of a command without the launchers in front of it (`sudo`, `env A=b`, `nohup`, `timeout 5`, `nice -n 5`, `{`, `then`, `xargs -I{}` ...)."""
    i = 0
    while i < len(words):
        w = _base(words[i])
        if w in _UNWRAP_PLAIN:
            i += 1
            while w == "xargs" and i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-I", "-n", "-P", "-L", "-s", "-d", "-E") else 1
        elif w == "sudo":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T") else 1
        elif w == "env":
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.match(r"^[A-Za-z_]\w*=", words[i])):
                i += 2 if words[i] in ("-u", "-C", "-S", "--unset", "--chdir") else 1
        elif w in ("timeout", "gtimeout"):
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-s", "-k", "--signal", "--kill-after") else 1
            i += 1   # the duration
        elif w == "nice":
            i += 1
            if i < len(words) and words[i] == "-n":
                i += 2
            elif i < len(words) and re.match(r"^-\d+$", words[i]):
                i += 1
        elif re.match(r"^[A-Za-z_]\w*=", words[i]):
            i += 1
        else:
            break
    return words[i:]


# ------------------------------------------------------------------ patch headers

def _unquote(s: str) -> str | None:
    """A file name as a patch writes it: a C-quoted `"a/x\\ty"` is decoded, anything else is cut at the first tab (the timestamp). None for a name with a control character."""
    s = s.strip()
    if s.startswith('"'):
        end = 1
        out = bytearray()
        while end < len(s) and s[end] != '"':
            if s[end] == "\\" and end + 1 < len(s):
                end += 1
                c = s[end]
                if c in "01234567":
                    j = end
                    while j < len(s) and j < end + 3 and s[j] in "01234567":
                        j += 1
                    out.append(int(s[end:j], 8) & 0xFF)
                    end = j - 1
                else:
                    out += {"t": b"\t", "n": b"\n", "\\": b"\\", '"': b'"', "a": b"\a", "b": b"\b", "f": b"\f", "r": b"\r", "v": b"\v"}.get(c, c.encode())
            else:
                out += s[end].encode()
            end += 1
        name = out.decode("utf-8", "replace")
    else:
        name = s.split("\t", 1)[0]
        name = re.sub(r"\s+\d{4}-\d\d-\d\d[ T]\d\d:\d\d.*$", "", name).rstrip()
    if not name or name == "/dev/null" or re.search(r"[\x00-\x1f]", name):
        return None
    return name


def _git_header(rest: str) -> list[str]:
    """The two names of `diff --git a/X b/Y` (the rest of the line)."""
    rest = rest.strip()
    if rest.startswith('"'):
        names = []
        for m in re.finditer(r'"(?:[^"\\]|\\.)*"|\S+', rest):
            if (n := _unquote(m.group(0))):
                names.append(n)
        return names
    for m in re.finditer(r" (?=b/)", rest):
        left, right = rest[:m.start()], rest[m.end():]
        if left.startswith("a/") and left[2:] == right[2:]:
            return [left, right]
    return [t for t in rest.split(" ") if t]


_HUNK_MARK = re.compile(r"^\*\*\* \d+(?:,\d+)? \*{4}$")


def header_paths(body: str) -> list[str]:
    """Every file name a patch (unified, context or git, a mail with a patch in it) names, as written, `/dev/null` dropped. Hunk lines that look like headers
    are read as headers too: a patch of a patch is gated, never missed."""
    out: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        if line.startswith("diff --git "):
            out += _git_header(line[11:])
        elif m := re.match(r"^(?:rename|copy) (?:from|to) (.+)$", line):
            if (n := _unquote(m.group(1))):
                out.append(n)
        elif line.startswith(("--- ", "+++ ")) or (line.startswith("*** ") and not _HUNK_MARK.match(line) and not line.startswith("*** ***")):
            if (n := _unquote(line[4:])):
                out.append(n)
        elif line.startswith("Index: "):
            if (n := _unquote(line[7:])):
                out.append(n)
        seen.update(out)
        if len(seen) > MAX_PATHS:   # one past the bound, so the caller can tell that some were left unread
            break
    return list(dict.fromkeys(out))


def _levels(path: str, strip: int | None, default: str) -> list[str]:
    parts = path.split("/")
    if strip is not None:
        lv = [strip]
    elif default == "git":
        lv = [0, 1]
    else:
        lv = list(range(0, min(len(parts), 9)))
    out = []
    for n in lv:
        if n >= 0 and (rest := "/".join(parts[n:]) or parts[-1]):   # a name with fewer slashes than the level is kept by its last component
            out.append(rest)
    return out


# ------------------------------------------------------------------ the commands that apply a patch

@dataclass
class Spec:
    kind: str                                   # patch | apply | am
    strip: int | None = None
    rel: list[str] = field(default_factory=list)   # `-d dir`, `--directory=dir`, `git -C dir`: where the patch lands, in order
    files: list[str] = field(default_factory=list)   # patch files named
    targets: list[str] = field(default_factory=list)   # files the command writes by name (patch's original file, `-o out`)
    stdin: bool = False                         # an explicit `-`
    writes: bool = True
    no_body: bool = False                       # `git am --continue`: the patch is already stored


_PATCH_VALUE_SHORT = set("pdioBDFgrVxYz")
_PATCH_VALUE_LONG = {"--strip", "--directory", "--input", "--output", "--reject-file", "--prefix", "--suffix", "--basename-prefix", "--basename-suffix",
                     "--version-control", "--read-only", "--quoting-style", "--remove-empty-files", "--posix"}
_GIT_APPLY_VALUE = {"--directory", "--include", "--exclude", "--whitespace", "--build-fake-ancestor", "--root"}
_AM_VALUE = {"--directory", "--include", "--exclude", "--whitespace", "--patch-format", "--committer-date-is-author-date", "--resolvemsg", "--empty"}
_AM_ACTIONS = {"--abort", "--quit", "--show-current-patch", "--skip", "--continue", "--resolved", "--retry", "-r", "--allow-empty"}


def _opt_value(words: list[str], i: int, name: str) -> tuple[str | None, int]:
    w = words[i]
    if w.startswith(name + "="):
        return w.split("=", 1)[1], 0
    if w == name and i + 1 < len(words):
        return words[i + 1], 1
    return None, 0


def _parse_patch(args: list[str]) -> Spec:
    sp = Spec("patch")
    ops: list[str] = []
    out: str | None = None
    inp: str | None = None
    i, only_ops = 0, False
    while i < len(args):
        a = args[i]
        used = 0
        if only_ops or not a.startswith("-") or a == "-":
            if a == "-":
                sp.stdin = True
            elif not _REDIRECT_WORD.match(a):
                ops.append(a)
            elif a in ("<", ">", ">>") or re.fullmatch(r"\d*>>?|&>>?", a):
                used = 1   # the target of a redirect
        elif a == "--":
            only_ops = True
        elif a.startswith("--"):
            name = a.split("=", 1)[0]
            if name in ("--dry-run", "--check"):
                sp.writes = False
            v, used = _opt_value(args, i, name) if name in _PATCH_VALUE_LONG else (None, 0)
            if name == "--strip" and v and v.lstrip("-").isdigit():
                sp.strip = int(v)
            elif name == "--directory" and v is not None:
                sp.rel.append(v)
            elif name == "--input" and v is not None:
                inp = v
            elif name == "--output" and v is not None:
                out = v
        else:
            k = 1
            while k < len(a):
                c = a[k]
                if c == "C":
                    sp.writes = False
                if c in _PATCH_VALUE_SHORT:
                    v = a[k + 1:] or (args[i + 1] if i + 1 < len(args) else None)
                    used = 0 if a[k + 1:] else 1
                    if c == "p" and v is not None and v.lstrip("-").isdigit():
                        sp.strip = int(v)
                    elif c == "d" and v is not None:
                        sp.rel.append(v)
                    elif c == "i" and v is not None:
                        inp = v
                    elif c == "o" and v is not None:
                        out = v
                    break
                k += 1
        i += 1 + used
    if inp is not None:
        sp.files.append(inp)
        sp.targets += ops[:1]
    else:
        if len(ops) >= 2:
            sp.files.append(ops[1])
        sp.targets += ops[:1]
    if out is not None:
        sp.targets.append(out)
    return sp


def _parse_git(kind: str, args: list[str]) -> Spec:
    sp = Spec(kind)
    flags: set[str] = set()
    values = _GIT_APPLY_VALUE if kind == "apply" else _AM_VALUE
    i = 0
    only_ops = False
    while i < len(args):
        a = args[i]
        used = 0
        if only_ops or not a.startswith("-") or a == "-":
            if a == "-":
                sp.stdin = True
            elif not _REDIRECT_WORD.match(a):
                sp.files.append(a)
            elif a in ("<", ">", ">>") or re.fullmatch(r"\d*>>?|&>>?", a):
                used = 1
        elif a == "--":
            only_ops = True
        elif a.startswith("--"):
            name = a.split("=", 1)[0]
            flags.add(name)
            if name in values:
                v, used = _opt_value(args, i, name)
                if name == "--directory" and v is not None:
                    sp.rel.append(v)
                elif name == "--build-fake-ancestor" and v is not None:
                    sp.targets.append(v)
        elif m := re.match(r"^-p(-?\d+)$", a):
            sp.strip = int(m.group(1))
        elif a == "-p" and i + 1 < len(args) and args[i + 1].lstrip("-").isdigit():
            sp.strip, used = int(args[i + 1]), 1
        elif a in ("-h",):
            flags.add("--help")
        else:
            flags.update({f"-{c}" for c in a[1:]})
        i += 1 + used
    if kind == "apply":
        nowrite = {"--check", "--stat", "--numstat", "--summary", "--help"} & flags
        sp.writes = not ((nowrite and "--apply" not in flags) or ("--cached" in flags and "--index" not in flags))
    else:
        sp.no_body = bool(_AM_ACTIONS & flags)
        sp.writes = "--help" not in flags and not sp.no_body
    return sp


def _spec(words: list[str]) -> Spec | None:
    """The patch-applying command these words are, or None. `words` have launchers removed; a word with an expansion in it is `DYN`."""
    if not words or words[0] == DYN:
        return None
    prog = _base(words[0])
    if prog in ("patch", "gpatch"):
        return _parse_patch(words[1:])
    if prog != "git":
        return None
    rel: list[str] = []
    i = 1
    while i < len(words):
        w = words[i]
        name, eq, val = w.partition("=")
        if w in shellnorm.GLOBAL_NOARG:
            i += 1
        elif name in shellnorm.GLOBAL_VALUE:
            if not eq:
                val, i = (words[i + 1] if i + 1 < len(words) else ""), i + 1
            if name == "-C":
                rel.append(val)
            i += 1
        elif w.startswith("-"):
            i += 1   # an option git does not take as a global one: stop reading it as one, the sub-command is the next word
        else:
            break
    if i >= len(words) or words[i] not in ("apply", "am"):
        return None
    sp = _parse_git(words[i], words[i + 1:])
    sp.rel = rel + sp.rel
    return sp


# ------------------------------------------------------------------ where the patch text comes from

@dataclass
class Hit:
    paths: list[str] = field(default_factory=list)   # candidate targets, relative to each directory the command may run in (or absolute)
    blind: bool = False
    dirs: tuple[str, ...] | None = None              # the directories of this one invocation (segment fallback); None = every directory of the command


_HOME_VAR = re.compile(r"\$HOME(?![\w])|\$\{HOME\}")


def _expand(w: str) -> str:
    """`~` and `$HOME` read as the shell reads them; any other variable stays in the word, which then counts as dynamic."""
    home = os.environ.get("HOME", "")
    return os.path.expanduser(_HOME_VAR.sub(lambda _m: home, w)) if home or w.startswith("~") else w


def _abs(d: str, w: str) -> str:
    return os.path.normpath(os.path.join(d or os.getcwd(), _expand(w)))


def _is_dynamic(w: str) -> bool:
    return not w or bool(_DYNAMIC.search(w))


def _read(path: str) -> str | None:
    try:
        st = os.stat(path)
        if not os.path.isfile(path) or st.st_size > MAX_PATCH:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(MAX_PATCH + 1)
    except (OSError, ValueError):
        return None


def _file_bodies(word: str, dirs: tuple[str, ...], rel: list[str], written: dict[str, str | None]) -> list[str] | None:
    """The text of the patch file `word` names, for every directory it may be relative to; None when it cannot be known (a variable or glob, a file the command
    rewrites by another route, a file that is not there, over 1 MiB)."""
    if _is_dynamic(word):
        return None
    bodies: list[str] = []
    for d in dirs or ("",):
        for base in dict.fromkeys([d, os.path.join(d or os.getcwd(), *rel)] if rel else [d]):
            p = _abs(base, word)
            if p in written:
                if written[p] is None:
                    return None
                bodies.append(written[p] or "")
            elif (t := _read(p)) is not None:
                bodies.append(t)
    return bodies or None


_ECHO_FORMATS = {"%s\\n", "%s\n", "%s"}


def _echo_text(words: list[str]) -> str | None:
    parts = [w for w in words if not w.startswith("-") and w not in _ECHO_FORMATS]
    if any(DYN in w for w in parts):
        return None
    return "\n".join(parts).replace("\\n", "\n").replace("\\t", "\t")


def _command_words(c: inert.Cmd) -> list[str]:
    _, rest = inert._lead(c)
    return _unwrap([w.text if w.text is not None else DYN for w in rest])


_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`|<\(([^()]*)\)|>\(([^()]*)\)")


def _substitutions(raw: str) -> list[str]:
    return [next(g for g in m.groups() if g is not None) for m in _SUBST.finditer(raw)]


def _writes_of(c: inert.Cmd, text: str, words: list[str], dirs: tuple[str, ...], written: dict[str, str | None]) -> None:
    """Record the files this command writes: a heredoc into `cat`/`tee` and an `echo`/`printf` text are known, everything else that writes a file makes it unknown."""
    prog = _base(words[0]) if words and words[0] != DYN else ""
    dests = [r.target.text for r in c.redirects if r.op in (">", ">>", ">|", "&>", "&>>") and r.target is not None]
    known: str | None = None
    if prog in ("cat", "tee") and len(c.heredocs) == 1 and c.heredocs[0].body and not (prog == "cat" and len(words) != 1):
        known = text[c.heredocs[0].body[0]:c.heredocs[0].body[1]]
        if prog == "tee":
            dests += [w for w in words[1:] if not w.startswith("-")]
    elif prog in ("echo", "printf") and dests:
        known = _echo_text(words[1:])
    elif prog == "tee":
        dests += [w for w in words[1:] if not w.startswith("-")]
    elif prog in ("cp", "mv", "install", "rsync", "ln", "scp") and len(ops := [w for w in words[1:] if not w.startswith("-")]) >= 2:
        dests.append(ops[-1])
    elif prog in ("curl", "wget"):
        dests += blind.downloads(words)
    elif prog == "dd":
        dests += [w[3:] for w in words[1:] if w.startswith("of=")]
    append = any(r.op in (">>", "&>>") for r in c.redirects)
    for w in dests:
        if w is None or _is_dynamic(w) or w.startswith("/dev/"):
            continue
        for d in dirs or ("",):
            written[_abs(d, w)] = known if (known is not None and not append) else None


def _sources(c: inert.Cmd, k: int, cmds: list[inert.Cmd], text: str, sp: Spec, dirs: tuple[str, ...], written: dict[str, str | None]) -> tuple[list[str], bool]:
    """(texts, blind): every patch text this invocation reads, and whether some source could not be read."""
    bodies: list[str] = []
    blind_ = False
    have = False
    for h in c.heredocs:
        if h.body:
            bodies.append(text[h.body[0]:h.body[1]][:MAX_PATCH])
            have = True
    for r in c.redirects:
        if r.op == "<":
            have = True
            got = _file_bodies(r.target.text, dirs, sp.rel, written) if r.target is not None and r.target.text is not None else None
            bodies += got or []
            blind_ = blind_ or got is None
        elif r.op == "<<<":
            have = True
            if r.target is not None and r.target.text is not None:
                bodies.append(r.target.text)
            else:
                blind_ = True
    for f in sp.files:
        have = True
        got = _file_bodies(f, dirs, sp.rel, written)
        bodies += got or []
        blind_ = blind_ or got is None
    if not have and k > 0 and cmds[k - 1].op_after == "|":
        prod = cmds[k - 1]
        pw = _command_words(prod)
        prog = _base(pw[0]) if pw and pw[0] != DYN else ""
        if prog == "cat":
            got_any = False
            for h in prod.heredocs:
                if h.body:
                    bodies.append(text[h.body[0]:h.body[1]][:MAX_PATCH])
                    got_any = True
            for r in prod.redirects:
                if r.op == "<":
                    got_any = True
                    got = _file_bodies(r.target.text, dirs, [], written) if r.target is not None and r.target.text is not None else None
                    bodies += got or []
                    blind_ = blind_ or got is None
            for w in pw[1:]:
                if w != "-" and not w.startswith("-"):
                    got_any = True
                    got = _file_bodies(w, dirs, [], written)
                    bodies += got or []
                    blind_ = blind_ or got is None
            blind_ = blind_ or not got_any
        elif prog in ("echo", "printf") and (t := _echo_text(pw[1:])) is not None:
            bodies.append(t)
        else:
            blind_ = True   # `git diff | git apply`, `curl .. | patch`: the text exists only at run time
    return bodies, blind_


def _hit(sp: Spec, bodies: list[str], blind_: bool) -> Hit:
    paths: list[str] = []
    for body in bodies:
        named = header_paths(body)
        blind_ = blind_ or len(named) > MAX_PATHS   # the headers past the bound are not read: recorded, never silent
        for p in named[:MAX_PATHS]:
            paths += _levels(p, sp.strip, "git" if sp.kind != "patch" else "patch")
    paths += sp.targets
    rel = os.path.join(*sp.rel) if sp.rel else ""
    out = []
    for p in dict.fromkeys(paths):
        if DYN in p:
            continue
        out.append(os.path.normpath(os.path.join(rel, os.path.expanduser(p))) if rel else p)
    return Hit(list(dict.fromkeys(out))[:MAX_PATHS], blind_)


_HEREDOC_OPEN = re.compile(r"<<(-?)[ \t]*(?:'([^']*)'|\"([^\"]*)\"|\\?([\w.-]+))")


def _flatten_groups(text: str) -> tuple[str, list[str]]:
    """(text, substitutions): `text` with every unquoted `(`, `)`, `{` and `}` that is not part of a word or a substitution read as `;`, so
    `(cd d && patch <<EOF ... EOF)` is the commands the exact scanner reads; each `$( )`, `<( )` and `>( )` is replaced by a word and returned apart.
    Quotes, comments and heredoc bodies are copied as they are. Only the analysis reads the result: it is never run."""
    subs: list[str] = []
    out: list[str] = []
    n, i = len(text), 0
    pending: list[tuple[str, bool]] = []
    while i < n:
        ch = text[i]
        nxt = text[i + 1:i + 2]
        if ch == "\\":
            out.append(text[i:i + 2])
            i += 2
        elif ch == "'" or ch == '"':
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if ch == '"' and text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif ch == "#" and (i == 0 or text[i - 1] in " \t\n;&|("):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(text[i:j])
            i = j
        elif ch == "<" and text.startswith("<<", i) and not text.startswith("<<<", i) and (m := _HEREDOC_OPEN.match(text, i)):
            pending.append((m.group(2) or m.group(3) or m.group(4), m.group(1) == "-"))
            out.append(m.group(0))
            i = m.end()
        elif ch == "\n" and pending:
            out.append("\n")
            i += 1
            for delim, dash in pending:
                while i < n:
                    j = text.find("\n", i)
                    end = n if j < 0 else j + 1
                    line = text[i:end].rstrip("\n")
                    out.append(text[i:end])
                    i = end
                    if (line.lstrip("\t") if dash else line) == delim:
                        break
            pending = []
        elif nxt == "(" and ch in "$<>":
            depth, j = 0, i + 1
            while j < n:
                depth += (text[j] == "(") - (text[j] == ")")
                j += 1
                if depth == 0:
                    break
            subs.append(text[i + 2:j - 1])   # read on its own by the caller: a heredoc inside a substitution is a command line of its own
            out.append("__SUBSTITUTION__")
            i = j
        elif ch in "{}" and (i == 0 or text[i - 1] in " \t\n;&|(") and (nxt == "" or nxt in " \t\n;&|)"):
            out.append(" ; ")
            i += 1
        elif ch in "()":
            out.append(" ; ")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), subs


def _scan(text: str, cwd: str, dirs: tuple[str, ...], depth: int = 0) -> list[Hit]:
    if depth > MAX_DEPTH:
        return []
    try:
        cmds = inert._parse(text)
    except Exception:  # noqa: BLE001 - a text the scanner does not read exactly: groups and subshells are flattened, else it is read segment by segment
        try:
            text, subs = _flatten_groups(text)
            cmds = inert._parse(text)
        except Exception:  # noqa: BLE001
            return _scan_segments(text, cwd)
        hits_in_subs = [h for inner in subs for h in _scan(inner, cwd, dirs, depth + 1)]
    else:
        hits_in_subs = []
    hits: list[Hit] = list(hits_in_subs)
    written: dict[str, str | None] = {}
    for k, c in enumerate(cmds):
        lead, rest = inert._lead(c)
        for w in [*lead, *rest, *(r.target for r in c.redirects if r.target is not None)]:
            if w.text is None or "$(" in w.raw or "`" in w.raw:
                for inner in _substitutions(w.raw):
                    hits += _scan(inner, cwd, dirs, depth + 1)
        words = _command_words(c)
        if not words:
            continue
        prog = _base(words[0]) if words[0] != DYN else ""
        if prog in _SHELL_NAMES or prog == "eval":
            inner = ""
            if prog == "eval":
                inner = " ".join(words[1:])
            else:
                for j, a in enumerate(words[1:], 1):
                    if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a) and j + 1 < len(words):
                        inner = words[j + 1]
                        break
            if inner and DYN not in inner:
                hits += _scan(inner, cwd, dirs, depth + 1)
            for h in c.heredocs:
                if h.body:
                    hits += _scan(text[h.body[0]:h.body[1]][:MAX_PATCH], cwd, dirs, depth + 1)
        if (sp := _spec(words)) is not None and sp.writes and not sp.no_body:
            bodies, blind_ = _sources(c, k, cmds, text, sp, dirs, written)
            hits.append(_hit(sp, bodies, blind_))
        elif sp is not None and sp.writes and sp.no_body:
            pass
        _writes_of(c, text, words, dirs, written)
        if len(hits) >= MAX_HITS:
            hits.append(Hit([], True))   # the commands past the bound are not read: recorded, never silent
            break
    return hits


def _seg_words(seg: str, keep: bool = False) -> list[str]:
    """The words of one segment (`shellnorm.segments_cwd`), launchers removed. A word that holds an expansion is `DYN` unless `keep`."""
    seg = shellnorm.PREFIX.sub("", seg, count=1).strip()
    return [w.replace("\x01", " ") if keep or not _OTHER_VAR.search(w) else DYN for w in seg.split(" ") if w]


def _scan_segments(text: str, cwd: str) -> list[Hit]:
    """The fallback when the command is not read exactly (a subshell, a group): every simple command that applies a patch, with the file it names, or recorded."""
    hits: list[Hit] = []
    for seg, dirs in shellnorm.segments_cwd(text, cwd):
        if seg.startswith((shellnorm.TEXT_MARK, blind.BLIND_MARK, blind.FETCH_MARK)):
            continue
        words = _seg_words(seg)
        redirected: list[str] = []
        kept: list[str] = []
        skip = False
        unreadable = False
        for i, w in enumerate(words):
            if skip:
                skip = False
            elif w in ("<<<", "<<", "<<-") or w.startswith("<<"):
                unreadable = True
            elif w == "<" and i + 1 < len(words):
                redirected.append(words[i + 1])
                skip = True
            elif w.startswith("<") and len(w) > 1 and not w.startswith("<("):
                redirected.append(w[1:])
            else:
                kept.append(w)
        if (sp := _spec(_unwrap(kept))) is None or not sp.writes or sp.no_body:
            continue
        bodies: list[str] = []
        blind_ = unreadable
        for f in [*redirected, *sp.files]:
            got = _file_bodies(f, tuple(dirs) or ("",), sp.rel, {})
            bodies += got or []
            blind_ = blind_ or got is None
        if not (redirected or sp.files):
            blind_ = True
        h = _hit(sp, bodies, blind_)
        h.dirs = tuple(dirs) or ("",)
        hits.append(h)
    return hits


def _all_dirs(text: str, cwd: str) -> tuple[str, ...]:
    ds = [cwd]
    try:
        for _, d in shellnorm.segments_cwd(text, cwd):
            ds.extend(d)
    except Exception:  # noqa: BLE001
        pass
    return tuple(dict.fromkeys(ds))[:6]


def _in(d: str, body: str) -> str:
    return f"(cd {_quote(d)} && {body})" if d else f"({body})"


def patch_extras(text: str, cwd: str) -> list[str]:
    if not _QUICK_PATCH.search(text):
        return []
    dirs = _all_dirs(text, cwd)
    out: list[str] = []
    blind_ = False
    for h in _scan(text, cwd, dirs):
        blind_ = blind_ or h.blind
        if h.paths:
            target = " ".join(f"> {_quote(p)}" for p in h.paths)
            out += [_in(d, f"cat {target}") for d in (h.dirs or dirs)]
    if blind_:
        out.append(f"{blind.BLIND_MARK}{BLIND_LABEL} patch")
    return out


# ------------------------------------------------------------------ links made by the same command

_REDIRECT_PREFIX = re.compile(r"^(\d*>>?\|?|&>>?|<)(.+)$")
_DD_OPERAND = re.compile(r"^(of=)(.+)$")
_REDIRECT_OPERATOR = re.compile(r"(?:\d*>>?\|?|&>>?|<|\d*>&\d*)")


def _canon(d: str, w: str) -> str:
    """An absolute path for word `w` run in `d`: `~` and `$HOME` expanded, the directory above it with its symlinks resolved, the last component left alone."""
    p = _abs(d, w)
    return os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p)) if p != "/" else p


def _through(links: dict[str, str], p: str) -> str:
    """`p` read through the links the command has made so far (the longest matching link first, chains followed)."""
    for _ in range(8):
        for src in sorted(links, key=len, reverse=True):
            if p == src or p.startswith(src + "/"):
                p = links[src] + p[len(src):]
                break
        else:
            return p
    return p


def _ln_pairs(words: list[str], d: str, links: dict[str, str]) -> list[tuple[str, str]]:
    """(link, target) pairs `ln` makes in directory `d`, both absolute; the target of a symbolic link is read as the link's own directory reads it."""
    args = words[1:]
    symbolic, tdir, ops = False, None, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            ops += args[i + 1:]
            break
        if a in ("-t", "--target-directory") and i + 1 < len(args):
            tdir, i = args[i + 1], i + 1
        elif a.startswith("--target-directory="):
            tdir = a.split("=", 1)[1]
        elif a == "--symbolic" or (a.startswith("-") and not a.startswith("--") and "s" in a[1:]):
            symbolic = True
        elif a.startswith("-") and a != "-":
            pass
        else:
            ops.append(a)
        i += 1
    if not ops or any(_is_dynamic(_expand(o)) for o in ops) or (tdir is not None and _is_dynamic(_expand(tdir))):
        return []
    pairs: list[tuple[str, str]] = []

    def make(src: str, link_word: str, into_dir: bool) -> None:
        link = _canon(d, link_word)
        link = os.path.join(_through(links, os.path.dirname(link)), os.path.basename(link))
        if into_dir:
            link = os.path.join(link, os.path.basename(src.rstrip("/")))
        target = _expand(src)
        target = os.path.normpath(target if os.path.isabs(target) else os.path.join(os.path.dirname(link) if symbolic else (d or os.getcwd()), target))
        pairs.append((link, os.path.realpath(_through(links, target))))

    if tdir is not None:
        for s in ops:
            make(s, tdir, True)
    elif len(ops) == 1:
        make(ops[0], d or os.getcwd(), True)
    else:
        last = ops[-1]
        into = len(ops) > 2 or last.endswith("/") or os.path.isdir(_through(links, _canon(d, last)))
        for s in ops[:-1]:
            make(s, last, into)
    return pairs


def _quote_word(w: str) -> str:
    """A word of a rewritten command: a redirect operator stays as it is, a path is quoted."""
    if _REDIRECT_OPERATOR.fullmatch(w):
        return w
    if m := _REDIRECT_PREFIX.match(w):
        return m.group(1) + _quote(m.group(2))
    if m := _DD_OPERAND.match(w):
        return m.group(1) + _quote(m.group(2))
    return _quote(w)


def link_extras(text: str, cwd: str) -> list[str]:
    """Each later command that names a path through a link this command made, rewritten to name the path the link points at, run in the directory it ran in."""
    if not _QUICK_LN.search(text):
        return []
    links: dict[str, str] = {}
    out: list[str] = []
    for seg, dirs in shellnorm.segments_cwd(text, cwd):
        if seg.startswith((shellnorm.TEXT_MARK, blind.BLIND_MARK, blind.FETCH_MARK)):
            continue
        words = _unwrap(_seg_words(seg, keep=True))
        if not words or _base(words[0]) in ("cd", "pushd", "popd"):
            continue
        is_ln = _base(words[0]) == "ln"
        for d in (dirs or ("",)) if links else ():   # a later `ln -sf x c/settings.json` writes through an earlier link too
            new = list(words)
            changed = False
            for n, w in enumerate(words):
                if n == 0 or _REDIRECT_OPERATOR.fullmatch(w):
                    continue
                prefix, val = "", w
                if m := _REDIRECT_PREFIX.match(w):
                    prefix, val = m.group(1), m.group(2)
                elif w.startswith("--") and "=" in w:
                    prefix, val = w.split("=", 1)[0] + "=", w.split("=", 1)[1]
                elif m := _DD_OPERAND.match(w):   # `dd of=c/settings.json`
                    prefix, val = m.group(1), m.group(2)
                elif w.startswith("-"):
                    continue
                if not val or val.startswith("-") or _is_dynamic(_expand(val)):
                    continue
                p = _canon(d, val)
                if (q := _through(links, p)) != p:
                    new[n] = prefix + q
                    changed = True
            if changed:
                out.append(_in(d, " ".join(_quote_word(x) for x in new)))
        if is_ln:
            for d in dirs or ("",):
                for link, target in _ln_pairs(words, d, links):
                    links[link] = target
    return out


# ------------------------------------------------------------------ uv add / remove / lock / version

_UV_NOWRITE = {"--dry-run", "--check", "--locked", "--help", "-h", "--short"}


def uv_extras(text: str, cwd: str) -> list[str]:
    if not _QUICK_UV.search(text):
        return []
    out: list[str] = []
    for seg, dirs in shellnorm.segments_cwd(text, cwd):
        words = _unwrap(_seg_words(seg))
        if len(words) < 2 or words[0] == DYN or _base(words[0]) != "uv":
            continue
        args = words[1:]
        extra_dir = ""
        for i, a in enumerate(args):
            if a in ("--project", "--directory", "-C") and i + 1 < len(args):
                extra_dir = args[i + 1]
            elif a.startswith(("--project=", "--directory=")):
                extra_dir = a.split("=", 1)[1]
        j = 0
        while j < len(args) and args[j].startswith("-"):
            j += 2 if args[j] in ("--project", "--directory", "-C", "--python", "-p", "--config-file", "--cache-dir", "--env-file") else 1
        if j >= len(args):
            continue
        sub, rest = args[j], args[j + 1:]
        upgrades = sub == "sync" and any(a in ("-U", "--upgrade") or a.startswith("--upgrade-package") or a == "-P" for a in rest) \
            and not any(a in ("--frozen", "--locked", "--no-sync") for a in rest)   # relocks, so it rewrites uv.lock
        if not (sub in ("add", "remove", "lock", "version") or upgrades) or any(a in _UV_NOWRITE for a in rest):
            continue
        if sub == "version" and not ("--bump" in rest or any(not a.startswith("-") for a in rest)):
            continue
        for d in dirs or ("",):
            where = os.path.join(d or os.getcwd(), os.path.expanduser(extra_dir)) if extra_dir and not _is_dynamic(extra_dir) else d
            out.append(_in(where, "cat > pyproject.toml > uv.lock"))
    return out


def extras(text: str, cwd: str = "") -> list[str]:
    """Commands the classifier reads in addition to the text of the command (see the module doc). One reader failing never hides another."""
    out: list[str] = []
    for fn in (patch_extras, link_extras, uv_extras):
        try:
            out += fn(text, cwd)
        except Exception:  # noqa: BLE001 - the other readers of the classifier stay in force
            continue
    return out
