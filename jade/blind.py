"""Command text that the classifier cannot read on the command line, and what can still be said about it (W-31, docs/05 T18).

Three kinds of answer:

* Fetched or decoded text run as code (`curl | sh`, `sh -c "$(curl ..)"`, `bash <(curl ..)`, `base64 -d | sh`, a file the same command downloads and then runs).
  The program is not on the command line and never will be, so the shape is rated: `fetch:exec` (R-22, high). The helpers here only recognise the shape;
  jade/shellnorm.py (pipes, substitutions) and jade/policy.py (a download and its later run) place the marker.
* A script written and run by the same command (`cat > x.sh <<EOF ... EOF; bash x.sh`). The text is on the command line after all, so `written_scripts` returns it as a
  command for the classifier to read, as if it were fed to the interpreter by a heredoc.
* What is still unreadable and is only recorded, never gated (R-31, medium, event `blind_spot`): a script file run by name, `sed -f`/`awk -f`, `eval "$(cat f)"`,
  `eval "$VAR"`, a relative write after `cd $VAR`. Whether these should be high is a human decision (W-31, W-33); the event is the evidence for it.

Everything is a text match on already canonicalised words. Nothing is run and no file is read.
"""

from __future__ import annotations

import os
import re

from . import inert

FETCH_MARK = "fetch:exec "   # prefix R-22 matches; carried only in the text a rule sees
BLIND_MARK = "blind:"   # prefix R-31 matches: `blind:<label> <command>`
LABELS = ("script-file", "script-option", "eval-file", "eval-var", "computed-cwd", "patch-body")

FETCHERS = frozenset({"curl", "wget", "fetch", "aria2c", "http", "https", "xh", "lwp-request", "nc", "ncat", "netcat"})
_DECOMPRESS = frozenset({"uudecode", "gunzip", "gzcat", "zcat", "bzcat", "xzcat", "unxz", "bunzip2", "lzcat"})
SHELLS = re.compile(r"^(?:(?:ba|z|da|k|a|fi|c|tc)?sh|source|\.)$")
INTERPS = re.compile(r"^(?:python[\d.]*|pypy\d*|perl\d*|ruby\d*|node(?:js)?|php\d*|lua\d*|deno|bun|pwsh|powershell|swift|rscript|tclsh|julia|groovy)$")
_CODE = re.compile(r"-[a-zA-Z]*[cemEpr]|--eval|--print|--command")   # an option whose value (or whose use) is a program on the command line, not a file
_VALUE_OPTS = {"-W", "-X", "--require", "--import", "--loader", "--experimental-loader", "--env-file"}
_SCRIPT_EXT = re.compile(r"\.(?:sh|bash|zsh|py|pl|rb|js|mjs|cjs|ts|php|lua)$", re.I)
_SYSTEM_DIR = re.compile(r"^/(?:usr/|opt/homebrew/|usr/local/)?s?bin/")
_REDIRECT_IN = re.compile(r"^(?:\d*>|&>|<<|<\()")

_FETCH_SUBST = re.compile(r"(?:\$\(|`)\s*(?:\S*/)?(?:" + "|".join(sorted(FETCHERS)) + r")\b")
_DECODE_SUBST = re.compile(r"(?:\$\(|`)[^)`]*\b(?:base64\s+(?:-d|-D|--decode)|xxd\s+-r|uudecode|g?zcat|gunzip|bzcat|xzcat)\b")
_FILE_SUBST = re.compile(r"(?:\$\(|`)\s*(?:cat\s+(?!<<)[^\s<|;&)`]|<\s*[^\s<(])")
_VAR_ONLY = re.compile(r'^\s*"?\$\{?[A-Za-z_]\w*\}?"?\s*$')


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1].lower()


def stage_kind(word: str, args: list[str]) -> str | None:
    """'fetch' for a program that brings text in from the network, 'decode' for one that turns bytes into text, else None: a stage whose output is not
    on the command line, and which becomes a program when a shell or interpreter reads it."""
    w = _base(word)
    if w in FETCHERS:
        return "fetch"
    if w == "base64" and any(a in ("-d", "-D", "--decode") or re.fullmatch(r"--dec\w*", a) for a in args):
        return "decode"
    if w == "xxd" and any(re.fullmatch(r"-[a-z]*r[a-z]*", a) for a in args):
        return "decode"
    if w in _DECOMPRESS or (w == "gzip" and any(re.fullmatch(r"-[a-z]*d[a-z]*", a) for a in args)):
        return "decode"
    if w == "openssl" and "-d" in args and args[:1] and args[0] in ("enc", "base64"):
        return "decode"
    return None


def subst_kind(code: str) -> str | None:
    """What the script word of `eval` or `sh -c` holds: 'fetch' or 'decode' (a substitution that brings text in), 'file' (`$(cat f)`, `$(< f)`), 'var'
    (the word is just a variable), else None. `eval "$(ssh-agent -s)"` and `eval "$(brew shellenv)"` are none of these."""
    if _FETCH_SUBST.search(code):
        return "fetch"
    if _DECODE_SUBST.search(code):
        return "decode"
    if _FILE_SUBST.search(code):
        return "file"
    if _VAR_ONLY.match(code):
        return "var"
    return None


# ---------------------------------------------------------------- download, then run

def _cluster(letters: str, rest: list[str], value_letter: str) -> tuple[str | None, int]:
    """The value of a short option cluster whose letter `value_letter` takes one (`-sSLo f`, `-ofile`) and how many following words it used."""
    k = letters.find(value_letter)
    if k < 0:
        return None, 0
    if k + 1 < len(letters):
        return letters[k + 1:], 0
    return (rest[0], 1) if rest else (None, 0)


def downloads(words: list[str]) -> list[str]:
    """The file names a `curl`, `wget` or the like writes (`-o f`, `-O`, `wget URL`, a redirect), as written. A name that holds a variable or is `-` is dropped."""
    if not words or _base(words[0]) not in ("curl", "wget", "fetch", "aria2c", "http", "https", "xh"):
        return []
    w, args = _base(words[0]), words[1:]
    names: list[str] = []
    urls: list[str] = []
    remote_name = False
    i = 0
    while i < len(args):
        a = args[i]
        used = 0
        if a in (">", ">>") and i + 1 < len(args):
            names.append(args[i + 1])
            used = 1
        elif re.match(r"^>>?[^>&]", a):
            names.append(a.lstrip(">"))
        elif a in ("-o", "--output", "--output-document") and i + 1 < len(args):
            names.append(args[i + 1])
            used = 1
        elif a.startswith(("--output=", "--output-document=")):
            names.append(a.split("=", 1)[1])
        elif a in ("--remote-name", "--remote-name-all"):
            remote_name = True
        elif a.startswith("-") and not a.startswith("--"):
            letters = a[1:]
            if w == "curl":
                remote_name = remote_name or "O" in letters
                v, used = _cluster(letters, args[i + 1:], "o")
                if v:
                    names.append(v)
            elif w == "wget":
                v, used = _cluster(letters, args[i + 1:], "O")
                if v:
                    names.append(v)
        elif re.match(r"^[a-z][a-z0-9+.-]*://", a):
            urls.append(a)
        i += 1 + used
    if remote_name or (w == "wget" and not names):
        names.extend(u.split("#", 1)[0].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] for u in urls)
    return [n for n in dict.fromkeys(names) if n and n != "-" and not re.search(r'[$`"\x06]', n) and not n.startswith("/dev/")]


def copies_of(words: list[str], names: set[str] | frozenset[str]) -> list[str]:
    """The destination of `cp`, `mv`, `install` or `ln` when its source is one of `names` (a file this command fetched): a copy of fetched text is fetched text."""
    if not words or _base(words[0]) not in ("cp", "mv", "install", "ln", "rsync") or not names:
        return []
    ops = [a for a in words[1:] if a and not a.startswith("-")]
    have = {os.path.basename(n) for n in names}
    if len(ops) < 2 or not any(os.path.basename(o) in have for o in ops[:-1]) or re.search(r'[$`"\x06]', ops[-1]):
        return []
    return [ops[-1]]


def _first_positional(args: list[str], kind: str) -> str | None:
    """The file a shell or interpreter runs (`bash -x f`, `python3 -u f`), or None when it runs a program given on the command line, a module, or stdin."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return args[i + 1] if i + 1 < len(args) else None
        if kind == "shell":
            if a in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                i += 2
                continue
            if re.fullmatch(r"-[a-zA-Z]+", a) and ("c" in a or "s" in a or "n" in a):   # -n: syntax check only, nothing runs
                return None
            if a.startswith(("-", "+")):
                i += 1
                continue
        else:
            if a in _VALUE_OPTS:
                i += 2
                continue
            if _CODE.fullmatch(a):
                return None
            if a.startswith("-"):
                i += 1
                continue
        if a == "<" and i + 1 < len(args):
            return None if args[i + 1].startswith("/dev/") else args[i + 1]
        if re.fullmatch(r"\d*>>?|&>>?", a):   # a redirect operator: the next word is its target, not a script
            i += 2
            continue
        if _REDIRECT_IN.match(a):
            i += 1
            continue
        if a.startswith("<") and len(a) > 1:
            return None if a[1:].startswith("/dev/") else a[1:]
        return None if a.startswith("/dev/") else a
    return None


def _runner_kind(w: str) -> str | None:
    return "shell" if SHELLS.match(w) else "interp" if INTERPS.match(w) else None


def runs_file(words: list[str], names: set[str] | frozenset[str]) -> bool:
    """True when the command runs one of `names` (by base name) as a script: `sh x`, `python3 ./x`, `source x`, `./x`, `/tmp/x`."""
    if not words or not names:
        return False
    w = _base(words[0])
    targets = {os.path.basename(n) for n in names}
    if (kind := _runner_kind(w)) is not None:
        f = _first_positional(words[1:], kind)
        return bool(f) and os.path.basename(f) in targets
    return "/" in words[0] and os.path.basename(words[0]) in targets


# ---------------------------------------------------------------- recorded blind spots (R-31)

def _script_path(f: str) -> bool:
    return bool(f) and not f.startswith("-") and bool(re.search(r"[./]", f))


def script_label(words: list[str], known: frozenset[str] = frozenset()) -> str | None:
    """The blind-spot label of one simple command (launcher prefixes and git options already removed), or None. `known`: files this same command wrote, whose
    text the classifier has read, so running one is not a blind spot."""
    if not words or not words[0]:
        return None
    w, args = _base(words[0]), words[1:]

    def unknown(f: str | None) -> bool:
        return bool(f) and os.path.basename(f) not in {os.path.basename(k) for k in known} and not f.startswith("/dev/")

    if (kind := _runner_kind(w)) is not None:
        f = _first_positional(args, kind)
        if f and (kind == "shell" or _script_path(f)) and unknown(f):
            return "script-file"
        return None
    if w == "sed" or w in ("awk", "gawk", "mawk", "nawk"):
        for i, a in enumerate(args):
            if a.startswith("--file="):
                v = a.split("=", 1)[1]
            elif a == "--file" or (a == "-f" if w != "sed" else re.fullmatch(r"-[a-zA-Z]*f", a)):
                v = args[i + 1] if i + 1 < len(args) else ""
            else:
                continue
            if v and v != "-" and not v.startswith("/dev/"):
                return "script-option"
        return None
    first = words[0]
    if first.startswith(("./", "../")) or (first.startswith(("/", "~/")) and not _SYSTEM_DIR.match(first) and _SCRIPT_EXT.search(first)):
        return "script-file" if unknown(first) else None
    return None


def cd_kind(words: list[str]) -> str | None:
    """'computed' when `cd`/`pushd` names a directory only the running shell knows (`cd $NOPE/x`, a glob, a substitution), 'literal' for a written one."""
    if not words or words[0] not in ("cd", "pushd"):
        return None
    target = next((x for x in words[1:] if not x.startswith("-") or x == "-"), None)
    if target is None or target == "-":
        return None
    t = os.path.expandvars(target)
    return "computed" if "$" in t or re.search(r"[*?\[`]", t) else "literal"


def relative_words(text: str) -> bool:
    """A word of `text` that is a path relative to the working directory."""
    return any(w and not w.startswith(("/", "~", "$", "-", "@", '"', "\x06")) and "://" not in w for w in text.split())


# ---------------------------------------------------------------- a script written and run by the same command

MAX_TOTAL = 4_000_000   # characters of script text handed back; the classifier's own scan budget is of the same size
MAX_BODY = 200_000
_ECHO = {"echo", "printf"}


def _decode_escapes(s: str) -> str:
    return s.replace("\\n", "\n").replace("\\t", "\t")


def _norm(p: str) -> str:
    return os.path.normpath(os.path.expanduser(p))


def written_scripts(text: str) -> tuple[list[str], frozenset[str]]:
    """(commands, files): for each file this command writes with a heredoc (`cat > f <<EOF`, `tee f <<EOF`) or with `echo`/`printf` text and then runs
    (`bash f`, `python3 f`, `source f`, `. f`, `./f`), the command `<interpreter> <<'EOF' ... EOF` that feeds that text to the interpreter, which the classifier already reads;
    and the names of those files. The text of a file that is written and never run stays data. A command this scanner cannot read exactly gives nothing."""
    if "\n" not in text and not re.search(r"\b(?:echo|printf)\b", text):
        return [], frozenset()
    try:
        cmds = inert._parse(text)
    except Exception:  # noqa: BLE001 - a text the scanner does not read exactly is left to the other readers
        return [], frozenset()
    written: list[tuple[int, str, str, bool, bool]] = []   # (index of the writing command, normalised path, body, appends, ends the line)
    for k, c in enumerate(cmds):
        _, rest = inert._lead(c)
        prog = inert._prog(rest)
        reds = [r for r in c.redirects if r.op in (">", ">>") and r.target is not None and r.target.text and not re.search(r"[*?\[\]{}]", r.target.text)]
        dests = [(r.target.text, r.op == ">>") for r in reds]
        body, own_nl = None, False
        if prog in ("cat", "tee") and len(c.heredocs) == 1 and (h := c.heredocs[0]).body and not h.dynamic:
            body = text[h.body[0]:h.body[1]]
            if prog == "tee":
                append = any(w.text in ("-a", "--append") for w in rest[1:])
                dests += [(w.text, append) for w in rest[1:] if w.text and not w.text.startswith("-")]
            elif len(rest) != 1:
                continue
        elif prog in _ECHO and dests:
            flags = [w.text for w in rest[1:] if w.text is not None and w.text.startswith("-")]
            parts = [w.text for w in rest[1:] if w.text is not None and not w.text.startswith("-")]
            body = _decode_escapes("\n".join(p for p in parts if p not in ("%s\\n", "%s\n", "%s")))
            own_nl = (prog == "echo" and not any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", f) for f in flags)) or any(p in ("%s\\n", "%s\n") for p in parts)
        if body is None:
            continue
        for d, appends in ((d, a) for d, a in dests if not d.startswith("/dev/")):
            written.append((k, _norm(d), body[:MAX_BODY], appends, own_nl))
    if not written:
        return [], frozenset()
    # Where each command runs a file as a script: path -> [(index, runner word, runner kind)]
    runs: dict[str, list[tuple[int, str, str | None]]] = {}
    for k, c in enumerate(cmds):
        _, rest = inert._lead(c)
        if not rest or rest[0].text is None:
            continue
        first, args = rest[0].text, [w.text or "" for w in rest[1:]]
        w = _base(first)
        if (kind := _runner_kind(w)) is not None:
            f = _first_positional(args, kind)
            if f:
                runs.setdefault(_norm(f), []).append((k, w if kind == "interp" or w not in (".", "source") else "bash", kind))
        elif "/" in first:
            runs.setdefault(_norm(first), []).append((k, "", None))
    out: list[str] = []
    files: set[str] = set()
    total = 0
    for path in dict.fromkeys(p for _, p, _, _, _ in written):
        writes = [wr for wr in written if wr[1] == path]
        for idx, runner, kind in runs.get(path, []):
            # the file as it is when this command runs it: an overwrite starts it again, an append (a script built in several writes) adds to it
            body = ""
            for k, _, part, appends, nl in writes:
                if k < idx:
                    body = (body if appends else "") + part + ("\n" if nl else "")
            if not any(k < idx for k, *_ in writes):
                continue   # run before it is written: not this command's script
            if kind is None:
                runner = "python3" if re.match(r"\s*#!.*\bpython", body) else "node" if re.match(r"\s*#!.*\bnode", body) else "bash"
            delim = "JADE_EOF"
            while delim in body:
                delim += "_"
            total += len(body)
            if total > MAX_TOTAL:
                return out, frozenset(files)   # the rest stays unread, and so unmarked as known: it is recorded as a blind spot (R-31)
            out.append(f"{runner} <<'{delim}'\n{body}\n{delim}")
            files.add(path)
    return out, frozenset(files)
