"""Mask the places of a shell command where a name is only data (W-71).

SI-10 (the permit seal key), SI-11 (the gate variables) and SI-09 (the human-only subcommands) are text matches (`match_on: raw`): they read the whole
command, quoted text and heredoc bodies included, because a program body or a quoted argument of an unknown program may run what it holds. That also
blocked a grep for the name, a commit message, an `echo` and a note written with `cat > file <<'EOF'`.

`mask(text)` returns the same command with the text of those positions replaced by `""` (a heredoc body by nothing), and nothing else. A position is
masked only when the program is one that never runs or reads its text there AND the command parses exactly:

* `echo` and `printf` (not `-v`): every argument, when the command is not redirected or piped on;
* `grep`, `egrep`, `fgrep`, `rg`, `ag`, `ack`, `git grep`: the pattern, when every option before it is known to take no file and no program;
* `git commit|tag|merge|stash|notes|revert|cherry-pick`: the `-m`/`--message` value, a message from `$(cat <<'EOF' ... EOF)`, the body of `-F -`;
  `git log|shortlog|rev-list`: `--grep`, `-S`, `-G`; `gh pr|issue|release ...`: `--title`/`--body`;
* `pytest` (also `python -m pytest`, `uv run pytest`): the `-k`/`-m` expression and a `--cov=` value;
* `cat` or `tee` writing a heredoc body to a literal file (or to the terminal): the body, when no other part of the command runs anything (a file
  written here and run by the next segment is read in full), the delimiter is quoted or the body holds no `$(` or backtick, and the file is not one
  the caller refuses (`dest_ok`: a shell profile, an environment file, a LaunchAgent plist for SI-11).

Everything else is kept, so a file operand, a redirect target, a program body, `bash <<EOF`, a piped producer and every env assignment still read as
they did. A command the scanner cannot parse exactly (a substitution other than the commit-message form, a subshell, a function, an alias, an
unterminated quote or heredoc, `eval`, a `PATH` change) is returned unchanged: when in doubt nothing is masked. The program must be a bare word
(`./echo` and `/tmp/grep` are not `echo` and `grep`), and a leading `NAME=value` disables the masks of that segment.

Honest limit: this reads the command text only. A name hidden from the text altogether is out of scope here (docs/05 T19), and the Write tool's own
content scan is a separate path.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

_OPS_END = (None, ";", "&&", "||", "\n")   # what may follow a command whose output goes nowhere (not `|`, not `&`)
_REDIR = re.compile(r"(\d*)(<<<|<<-?|<>|>>|>&|<&|&>>|&>|>\||>|<)")
_NAME_START = re.compile(r"[A-Za-z_]")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_CAT_SUBST = re.compile(r"\$\(\s*cat\s+<<(-?)\s*(['\"])([A-Za-z_]\w*)\2[ \t]*\n")
_BAIL_PROGS = {"alias", "unalias", "function", "enable", "hash", "eval", "builtin", "command", "exec", "trap", "shopt", "setopt", "set", "source", ".",
               "coproc", "select", "time", "nohup", "env", "sudo", "xargs", "watch", "nice"}   # these change what a later word means or run their arguments: nothing in the line is masked
_PATHVARS = {"PATH", "BASH_ENV", "ENV", "ZDOTDIR", "IFS", "SHELLOPTS", "BASHOPTS", "CDPATH", "GLOBIGNORE"}
# commands that cannot run a file: a heredoc written to a file stays masked only when every command in the line is one of these (or a passive git verb)
_PASSIVE = {"echo", "printf", "cat", "tee", "grep", "egrep", "fgrep", "rg", "ag", "ack", "ls", "head", "tail", "wc", "mkdir", "touch", "cd", "pwd", "true",
            "false", "test", "[", "diff", "stat", "file", "date", "sort", "uniq", "cut", "tr", "basename", "dirname", "realpath", "readlink", "which",
            "rm"}
# `commit` is not here: it runs .git/hooks/pre-commit and commit-msg, so a file written next to it may be run by it
_GIT_PASSIVE = {"add", "status", "diff", "log", "show", "grep", "rm", "mv", "tag", "branch", "stash", "ls-files", "rev-parse"}
# options of the passive commands that run a program: `git grep -O<cmd>`, `rg --pre cmd`, `ag --pager cmd`, `sort --compress-program=cmd`
_RUNS_PROGRAM = re.compile(r"^(?:-[A-Za-z]*O.*|--(?:pre|pre-glob|pager|open-files-in-pager|compress-program|use-compress-program|to-command|exec)(?:=.*)?)$")
_GREP = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
_G_FLAGS = re.compile(r"^-[rRnilLcvwxFEPHhsIaoqzZUbTVSu]+$")
_G_NUM = re.compile(r"^-(?:[ABCmj]\d+|\d+)$")
_G_NUM_SEP = re.compile(r"^-[ABCmj]$")
_G_E = re.compile(r"^-[rRnilLcvwxFEPHhsIaoqzZUbTVSu]*e$")
_G_E_ATTACHED = re.compile(r"^-e.+$")
_G_LONG_FLAGS = {"--recursive", "--ignore-case", "--line-number", "--files-with-matches", "--count", "--invert-match", "--word-regexp", "--fixed-strings",
                 "--extended-regexp", "--perl-regexp", "--no-filename", "--with-filename", "--quiet", "--silent", "--text", "--only-matching", "--null",
                 "--no-messages", "--smart-case", "--hidden", "--no-ignore", "--follow", "--files-without-match", "--line-regexp", "--no-heading",
                 "--heading", "--no-line-number", "--no-index", "--cached", "--untracked", "--line-buffered", "--dereference-recursive", "--basic-regexp"}
_G_LONG_VALUED = ("color", "colour", "include", "exclude", "exclude-dir", "max-count", "context", "after-context", "before-context", "glob", "type",
                  "threads", "max-depth", "binary-files", "directories", "devices")
_MSG_SUBS = {"commit", "tag", "merge", "stash", "notes", "revert", "cherry-pick"}
_LOG_SUBS = {"log", "shortlog", "rev-list"}
# `uv run` options that take no value; an option in neither set is not understood, and then nothing after it is masked
_UV_FLAGS = {"--no-project", "--isolated", "--frozen", "--locked", "--offline", "--no-sync", "--quiet", "-q", "--verbose", "-v", "--all-extras", "--all-packages",
             "--dev", "--no-dev", "--active", "--no-cache", "-n", "--no-config", "--exact", "--no-editable", "--no-progress", "--native-tls",
             "--no-python-downloads", "--refresh", "--reinstall", "--upgrade", "-U", "--no-env-file", "--no-managed-python", "--managed-python",
             "--all-groups", "--only-dev", "--no-default-groups", "--no-binary", "--no-build", "--compile-bytecode"}
_UV_VALUED = {"--with", "-w", "--python", "-p", "--project", "--directory", "--group", "--extra", "--package", "--env-file", "--with-requirements",
              "--with-editable", "--index", "--index-url", "--from", "--config-file", "--cache-dir", "--refresh-package", "--no-binary-package"}


@dataclass
class Word:
    start: int
    end: int
    text: str | None   # the word as a shell reads it, None when it holds an expansion (or is a commit-message substitution)
    data: bool = False   # a `"$(cat <<'EOF' ... EOF)"` that only holds text
    raw: str = ""   # the word as written, quotes and expansions included


@dataclass
class Redirect:
    op: str
    target: Word | None


@dataclass
class Heredoc:
    delim: str
    quoted: bool
    dash: bool = False   # `<<-`: leading tabs of the delimiter line are ignored
    body: tuple[int, int] | None = None   # span of the lines between the operator's line and the delimiter line
    dynamic: bool = False


@dataclass
class Cmd:
    words: list[Word] = field(default_factory=list)
    redirects: list[Redirect] = field(default_factory=list)
    heredocs: list[Heredoc] = field(default_factory=list)
    op_after: str | None = None


class _Bail(Exception):
    """The text is not something this scanner reads exactly."""


MASK_LIMIT = 200_000   # characters; an ordinary command is a few hundred

_LAST_ARG = re.compile(r"\$_(?!\w)|!\$")   # `echo KEY && cat $_`: the shell hands the last word of the previous command to the next one


def _parse(text: str) -> list[Cmd]:
    if _LAST_ARG.search(text):
        raise _Bail
    n = len(text)
    i = 0
    cmds: list[Cmd] = []
    cur = Cmd()
    pending: list[Heredoc] = []

    def end(op: str | None) -> None:
        nonlocal cur
        cur.op_after = op
        if cur.words or cur.redirects or cur.heredocs:
            cmds.append(cur)
        cur = Cmd()

    def read_body(pos: int) -> int:
        """Consume the bodies of the heredocs that opened on the line that ended at `pos - 1`; return the index after the last delimiter line."""
        for h in pending:
            start = pos
            while True:
                if pos >= n:
                    raise _Bail
                nl = text.find("\n", pos)
                line = text[pos:] if nl < 0 else text[pos:nl]
                strip = line.lstrip("\t") if h.dash else line
                if strip == h.delim:
                    h.body = (start, pos)
                    pos = n if nl < 0 else nl + 1
                    break
                if nl < 0:
                    raise _Bail
                pos = nl + 1
            body = text[h.body[0]:h.body[1]]
            h.dynamic = not h.quoted and ("$(" in body or "`" in body)
        pending.clear()
        return pos

    def word(pos: int) -> tuple[Word, int]:
        start, out, literal, data = pos, [], True, False
        while pos < n:
            c = text[pos]
            if c in " \t\n;&|<>()":
                if c in "<>" and pos + 1 < n and text[pos + 1] == "(":
                    raise _Bail
                break
            if c == "'":
                j = text.find("'", pos + 1)
                if j < 0:
                    raise _Bail
                out.append(text[pos + 1:j])
                pos = j + 1
            elif c == '"':
                pos += 1
                while True:
                    if pos >= n:
                        raise _Bail
                    d = text[pos]
                    if d == '"':
                        pos += 1
                        break
                    if d == "\\":
                        if pos + 1 >= n:
                            raise _Bail
                        nxt = text[pos + 1]
                        if nxt != "\n":
                            out.append(nxt if nxt in '$`"\\' else "\\" + nxt)
                        pos += 2
                    elif d == "`":
                        raise _Bail
                    elif d == "$":
                        if text.startswith("$(", pos):
                            m = _CAT_SUBST.match(text, pos)
                            if not m:
                                raise _Bail
                            tabs, _, delim = m.group(1) == "-", m.group(2), m.group(3)
                            q = m.end()
                            while True:   # the delimiter line, exactly (leading tabs only for `<<-`), then `)`
                                if q >= n:
                                    raise _Bail
                                nl = text.find("\n", q)
                                line = text[q:] if nl < 0 else text[q:nl]
                                if (line.lstrip("\t") if tabs else line) == delim:
                                    q = n if nl < 0 else nl + 1
                                    break
                                if nl < 0:
                                    raise _Bail
                                q = nl + 1
                            while q < n and text[q] in " \t\n":
                                q += 1
                            if q >= n or text[q] != ")":
                                raise _Bail
                            pos, literal, data = q + 1, False, True
                        elif text.startswith("${", pos):
                            raise _Bail
                        else:
                            literal = False
                            pos += 1
                    else:
                        out.append(d)
                        pos += 1
            elif c == "\\":
                if pos + 1 >= n:
                    raise _Bail
                if text[pos + 1] != "\n":
                    out.append(text[pos + 1])
                pos += 2
            elif c == "`":
                raise _Bail
            elif c == "$":
                if text.startswith(("$(", "${", "$'", '$"'), pos):
                    raise _Bail
                literal = False
                out.append(c)
                pos += 1
            elif c in "{}" and pos == start and (pos + 1 >= n or text[pos + 1] in " \t\n;&|"):
                raise _Bail   # a `{ ...; }` group
            else:
                out.append(c)
                pos += 1
        if data:
            return Word(start, pos, None, True, text[start:pos]), pos
        return Word(start, pos, "".join(out) if literal else None, False, text[start:pos]), pos

    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
        elif c == "\n":
            end("\n")
            i = read_body(i + 1) if pending else i + 1
        elif c == ";":
            if text.startswith(";;", i):
                raise _Bail
            end(";")
            i += 1
        elif c == "&" and not text.startswith("&>", i):
            op = "&&" if text.startswith("&&", i) else "&"
            end(op)
            i += len(op)
        elif c == "|":
            op = "||" if text.startswith("||", i) else "|"
            end(op)
            i += len(op)
        elif c in "()":
            raise _Bail
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;&|"):
            raise _Bail   # comments are read as text; a `#` in a command is rare and not worth a guess
        else:
            m = _REDIR.match(text, i) if (c in "<>&" or c.isdigit()) else None
            if m and (c in "<>&" or (m.start(2) == m.end(1) and m.end(1) > i)):
                op = m.group(2)
                i = m.end()
                if op in ("<<", "<<-"):
                    while i < n and text[i] in " \t":
                        i += 1
                    w, j = word(i)
                    if w.text is None or not w.text:
                        raise _Bail
                    raw = text[w.start:w.end]
                    h = Heredoc(delim=w.text, quoted=any(q in raw for q in "'\"\\"), dash=op == "<<-")
                    cur.heredocs.append(h)
                    pending.append(h)
                    i = j
                    continue
                while i < n and text[i] in " \t":
                    i += 1
                if op in (">&", "<&") and i < n and (text[i].isdigit() or text[i] == "-"):
                    w, i = word(i)
                    cur.redirects.append(Redirect(op, None))
                    continue
                if i >= n or text[i] in "\n;&|<>()":
                    raise _Bail
                w, i = word(i)
                cur.redirects.append(Redirect(op, w))
            else:
                w, i = word(i)
                cur.words.append(w)
    if pending:
        raise _Bail
    end(None)
    return cmds


Span = tuple[int, int, bool]   # start, end, and whether it is a heredoc body (replaced by nothing) rather than a word (replaced by `""`)


def _w(word: Word) -> Span:
    return (word.start, word.end, False)


def _lead(cmd: Cmd) -> tuple[list[Word], list[Word]]:
    """The `NAME=value` words that start a command, and the words after them."""
    k = 0
    while k < len(cmd.words) and _ASSIGN.match(cmd.words[k].raw):
        k += 1
    return cmd.words[:k], cmd.words[k:]


def _prog(words: list[Word]) -> str | None:
    """The program of a command when it is a bare word (a path-qualified or computed name is not `echo` or `grep`)."""
    if not words or not words[0].text or "/" in words[0].text:
        return None
    return words[0].text


def _stdin_body(cmd: Cmd) -> Span | None:
    """The body of the one heredoc of a command whose input is data (a commit message read with `-F -`)."""
    if len(cmd.heredocs) == 1 and cmd.heredocs[0].body and not cmd.heredocs[0].dynamic and cmd.op_after in _OPS_END:
        return (*cmd.heredocs[0].body, True)
    return None


def _heredoc_mask(cmd: Cmd, words: list[Word], dest_ok: Callable[[str], bool] | None) -> list[Span]:
    """The body of a `cat` or `tee` that only shows it or writes it to a literal file."""
    body = _stdin_body(cmd)
    if body is None:
        return []
    prog = _prog(words)
    dests: list[str] = []
    if prog == "cat" and len(words) == 1:
        for r in cmd.redirects:
            t = r.target.text if r.target else None
            if r.op not in (">", ">>") or not t or re.search(r"[*?\[\]{}]", t):
                return []
            dests.append(t)
        if len(dests) > 1:
            return []
    elif prog == "tee" and not cmd.redirects:
        args = [w.text for w in words[1:]]
        if any(a is None or (a.startswith("-") and a != "-a") or re.search(r"[*?\[\]{}]", a) for a in args):
            return []
        dests = [a for a in args if a != "-a"]
    else:
        return []
    if dest_ok is not None and not all(dest_ok(d) for d in dests):
        return []
    return [body]


def _git_masks(cmd: Cmd, words: list[Word]) -> list[Span]:
    if len(words) < 2 or words[1].text is None:
        return []
    sub, rest, spans = words[1].text, words[2:], []
    if sub in _MSG_SUBS:
        if sub == "notes" and rest and rest[0].text in ("add", "append", "edit"):
            rest = rest[1:]
        k = 0
        while k < len(rest):
            t = rest[k].text
            nxt = rest[k + 1] if k + 1 < len(rest) else None
            if t is None:
                k += 1
            elif (t == "--message" or re.fullmatch(r"-[A-Za-z]*m", t)) and nxt is not None and (nxt.text is not None or nxt.data):
                spans.append(_w(nxt))
                k += 2
            else:
                if t.startswith("--message=") or (t.startswith("-m") and len(t) > 2):
                    spans.append(_w(rest[k]))
                elif ((t in ("-F", "--file") and nxt is not None and nxt.text == "-") or t == "--file=-") and (body := _stdin_body(cmd)):
                    spans.append(body)
                k += 1
        return spans
    if sub in _LOG_SUBS:
        k = 0
        while k < len(rest):
            t = rest[k].text
            nxt = rest[k + 1] if k + 1 < len(rest) else None
            if t in ("--grep", "-S", "-G") and nxt is not None and nxt.text is not None:
                spans.append(_w(nxt))
                k += 2
                continue
            if t is not None and (t.startswith("--grep=") or (t[:2] in ("-S", "-G") and len(t) > 2)):
                spans.append(_w(rest[k]))
            k += 1
        return spans
    return _grep_masks(rest) if sub == "grep" else []


def _grep_masks(args: list[Word]) -> list[Span]:
    """The pattern of a grep-like command; nothing when an option before it could read a file, run a program or take a value of unknown meaning."""
    spans: list[Span] = []
    pattern_given = False
    k = 0
    while k < len(args):
        w = args[k]
        t = w.text
        nxt = args[k + 1] if k + 1 < len(args) else None
        if t is None:
            return spans if pattern_given else []
        if t == "--":
            if not pattern_given and nxt is not None and nxt.text is not None:
                spans.append(_w(nxt))
            return spans
        if t.startswith("--"):
            if t in _G_LONG_FLAGS or ("=" in t and t[2:].split("=", 1)[0] in _G_LONG_VALUED):
                k += 1
            elif t.startswith("--regexp="):
                spans.append(_w(w))
                pattern_given = True
                k += 1
            elif t == "--regexp" and nxt is not None and nxt.text is not None:
                spans.append(_w(nxt))
                pattern_given = True
                k += 2
            else:
                return []
        elif t.startswith("-") and len(t) > 1:
            if _G_FLAGS.match(t) or _G_NUM.match(t):
                k += 1
            elif _G_NUM_SEP.match(t) and nxt is not None and (nxt.text or "").isdigit():
                k += 2
            elif _G_E.match(t) and nxt is not None and nxt.text is not None:
                spans.append(_w(nxt))
                pattern_given = True
                k += 2
            elif _G_E_ATTACHED.match(t):
                spans.append(_w(w))
                pattern_given = True
                k += 1
            else:
                return []   # -f, -g, -A with a non-number, an unknown letter: it may name a file or run something
        else:
            if not pattern_given:
                spans.append(_w(w))
                pattern_given = True
            k += 1
    return spans


def _pytest_masks(words: list[Word]) -> list[Span]:
    t = [w.text for w in words]
    k = 0
    if t and t[0] == "uv":
        if len(t) < 2 or t[1] != "run":
            return []
        k = 2
        while k < len(t) and t[k] is not None and t[k].startswith("-"):
            if t[k] in _UV_VALUED:
                k += 2
            elif t[k] in _UV_FLAGS or (t[k].startswith("--") and "=" in t[k]):
                k += 1
            else:
                return []   # an option this scanner does not know may take a value: the next word is not known to be the program
    if k >= len(t) or t[k] is None:
        return []
    if t[k] in ("pytest", "py.test"):
        k += 1
    elif re.fullmatch(r"python(?:3(?:\.\d+)?)?", t[k]) and k + 2 < len(t) and t[k + 1] == "-m" and t[k + 2] in ("pytest", "py.test"):
        k += 3
    else:
        return []
    spans: list[Span] = []
    while k < len(t):
        a = t[k]
        if a is None:
            k += 1
        elif re.fullmatch(r"-[A-Za-z]*[km]", a) and k + 1 < len(t) and t[k + 1] is not None:
            spans.append(_w(words[k + 1]))
            k += 2
        else:
            if a.startswith("--cov=") or (a[:2] in ("-k", "-m") and len(a) > 2 and not a.startswith("--")):
                spans.append(_w(words[k]))
            k += 1
    return spans


def _gh_masks(cmd: Cmd, words: list[Word]) -> list[Span]:
    t = [w.text for w in words]
    if len(t) < 3 or t[1] not in ("pr", "issue", "release") or t[2] not in ("create", "edit", "comment", "close", "reopen", "review", "merge", "ready"):
        return []
    spans: list[Span] = []
    k = 3
    while k < len(t):
        a = t[k]
        nxt = words[k + 1] if k + 1 < len(t) else None
        if a in ("--title", "--body", "-t", "-b", "--notes", "-n") and nxt is not None and (nxt.text is not None or nxt.data):
            spans.append(_w(nxt))
            k += 2
            continue
        if a is not None and a.startswith(("--title=", "--body=", "--notes=")):
            spans.append(_w(words[k]))
        elif a in ("--body-file", "-F") and nxt is not None and nxt.text == "-" and (body := _stdin_body(cmd)):
            spans.append(body)
        k += 1
    return spans


def _args_masks(cmd: Cmd, words: list[Word]) -> list[Span]:
    """`echo` and `printf`: every argument, when the output is not redirected, piped on or fed from a heredoc."""
    if cmd.redirects or cmd.heredocs or cmd.op_after not in _OPS_END:
        return []
    if _prog(words) == "printf" and any(w.text == "-v" for w in words[1:]):
        return []
    return [_w(w) for w in words[1:] if w.text is not None]


def _writes_ok(cmd: Cmd, words: list[Word], prog: str | None, dest_ok: Callable[[str], bool] | None) -> bool:
    """Every file a command of the line writes is a literal path the caller accepts (a `cat x >> ~/.zshrc` next to a masked heredoc is not quiet)."""
    targets = [r.target for r in cmd.redirects if r.op not in ("<", "<<<") and r.target is not None]
    if prog == "tee":
        targets += [w for w in words[1:] if w.text != "-a"]
    return all(t.text is not None and (dest_ok is None or dest_ok(t.text)) for t in targets)


def _spans(cmds: list[Cmd], dest_ok: Callable[[str], bool] | None) -> list[Span]:
    split = [_lead(c) for c in cmds]
    progs = [_prog(rest) for _, rest in split]
    for (lead, _), c, p in zip(split, cmds, progs, strict=True):
        if any(w.raw.split("=", 1)[0].rstrip("+") in _PATHVARS for w in lead) or p in _BAIL_PROGS:
            return []
        if p in ("export", "declare", "typeset", "readonly", "local") and any(w.raw.split("=", 1)[0].rstrip("+") in _PATHVARS for w in c.words[1:]):
            return []
    # a heredoc written to a file is read in full when the same line could run any file
    quiet = all(not (lead or rest) or (not lead and (p in _PASSIVE or (p == "git" and len(rest) > 1 and rest[1].text in _GIT_PASSIVE))
                                       and not any(w.text and _RUNS_PROGRAM.match(w.text) for w in rest[1:]))
                for (lead, rest), p in zip(split, progs, strict=True)) and all(
        _writes_ok(c, rest, p, dest_ok) for c, (_, rest), p in zip(cmds, split, progs, strict=True))
    spans: list[Span] = []
    for c, (lead, rest), p in zip(cmds, split, progs, strict=True):
        if lead or p is None:
            continue
        if p in ("echo", "printf"):
            spans += _args_masks(c, rest)
        elif p in _GREP:
            spans += _grep_masks(rest[1:])
        elif p == "git":
            spans += _git_masks(c, rest)
        elif p == "gh":
            spans += _gh_masks(c, rest)
        elif p in ("pytest", "py.test", "uv") or re.fullmatch(r"python(?:3(?:\.\d+)?)?", p):
            spans += _pytest_masks(rest)
        elif p in ("cat", "tee") and c.heredocs:
            if p == "cat" and not c.redirects:
                spans += _heredoc_mask(c, rest, None)   # shown on the terminal, written nowhere
            elif quiet:
                spans += _heredoc_mask(c, rest, dest_ok)
    return spans


def mask(text: str, dest_ok: Callable[[str], bool] | None = None) -> str:
    """The command with its data positions blanked (see the module docstring), or the command itself when nothing is masked or it cannot be read
    exactly. `dest_ok(path)` is False for a file a heredoc must not be written to unseen (SI-11: a shell profile, an environment file, a plist)."""
    if len(text) > MASK_LIMIT:
        return text   # past this size the scan is not worth its cost; the whole text is matched, which is the stricter answer
    try:
        spans = _spans(_parse(text), dest_ok)
    except Exception:  # noqa: BLE001 - a text this scanner cannot read is matched whole, which is the stricter answer
        return text
    if not spans:
        return text
    out: list[str] = []
    last = 0
    for start, stop, body in sorted(set(spans)):
        if start < last:
            continue
        out.append(text[last:start])
        out.append("" if body else '""')
        last = stop
    out.append(text[last:])
    return "".join(out)
