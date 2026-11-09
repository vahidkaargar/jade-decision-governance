"""Canonicalise a shell command before any risk rule or safety invariant looks at it (threat T18, cluster A1).

`skeleton()` turns a command string into text in which the same operation always has the same spelling:

* backslash escapes, line continuations, adjacent-quote joins and `$'...'` strings are resolved;
* quoted prose is blanked, so `echo "git push"` is not a push, but a command substitution inside a double quoted
  string, a backtick span or an unquoted heredoc body IS extracted, because the shell runs it;
* the body of `sh -c`, `eval`, a heredoc / here-string / pipe feeding a shell or interpreter, and the program text of
  `python -c`, `perl -e`, `awk`, `sed 'w ...'` is exposed as commands (interpreter code is only searched for literal
  command text, never executed or parsed as that language);
* heredocs are recognised only outside quotes, so a `<<` inside a string cannot swallow the lines that follow.

`git_calls()` is the second half: every `git [global options] <subcommand>` is rewritten to `git <subcommand>` so
one regex per rule is enough, and `-c` / `--exec-path` settings that can run commands are reported as `danger`.
Nothing here executes anything. Honest limits are listed in docs/05-threat-model.md (T18).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_DEPTH = 8
MAX_NEST = 60

NULL_REDIRECT = re.compile(r"(\d*>&\d+|&>>?\s*/dev/null|\d*>>?\s*/dev/null|\d*<\s*/dev/null)")
SPLIT = re.compile(r"\n|;|&&|\|\||\||&|\$\(|`|\(|\)|\{|\}")
PREFIX = re.compile(
    r"^(?:(?:[A-Za-z_]\w*=\S*|sudo(?:\s+-[A-Za-z]+(?:\s+\S+)?)*?|command|builtin|nohup|time|exec|noglob|do|then|else|if|while|until|!"
    r"|env(?:\s+-[A-Za-z-]+)*|g?timeout(?:\s+-\S+)*\s+\d+[smhd]?|nice(?:\s+-n?\s*\d+)?|caffeinate(?:\s+-\w+)*"
    r"|xargs(?:\s+-(?:I\s*\S+|n\s*\d+|P\s*\d+|[0a-zA-Z]+))*)\s+)+")

SHELL_C = re.compile(
    r"(?:\b(?:ba|z|da|k|fi)?sh\s+(?:--?[\w-]+\s+|-[oO]\s+\w+\s+)*-\w*c|\beval|\bsu\b[^|;&]*\s-c)\s+$")
INTERP_C = re.compile(
    r"(?<![\w.-])(?:python[\d.]*|pypy\d*|perl\d*|ruby\d*|node(?:js)?|php\d*|osascript|lua\d*|deno|bun)"
    r"(?:\s+-[\w-]+(?:\s+[^\s-]\S*)?)*\s+(?:-[a-zA-Z]*[ceEr]|--eval|--print|--command)\s*$")
ALIAS_TAIL = re.compile(r"\s-c\s+alias\.[^\s=]*=$")
GIT_VALUE = re.compile(r"(?:^|[\s(;&|])(?:\S*/)?git(?:\s+\S+)*?\s+-[Cc]\s+$|--(?:git-dir|work-tree|namespace)=$|--(?:git-dir|work-tree|namespace)\s+$")

SHELL_RE = re.compile(r"^(?:(?:ba|z|da|k|a|fi|c|tc)?sh|source|\.)$")
INTERP_RE = re.compile(r"^(?:python[\d.]*|pypy\d*|perl\d*|ruby\d*|node(?:js)?|php\d*|osascript|lua\d*|deno|bun)$")
SCRIPT_CMDS = {"sed": "sed", "gsed": "sed", "awk": "awk", "gawk": "awk", "mawk": "awk", "nawk": "awk"}
SED_RISKY = re.compile(r"(?:^|[\s;{}/!0-9$])[wWe](?:\s+\S|\s*$)")
AWK_RISKY = re.compile(r"system\s*\(|getline|>|\||close\s*\(")
SCRIPT_MARK = "@script!"

HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|(\\?)([^\s;&|<>()'\"\\`$]+))")
NEXT_STAGE = re.compile(
    r"[ \t]*(?:\w+=\S*[ \t]+)*(?:(?:sudo|env|command|exec|nohup|time)(?:[ \t]+-\S+)*[ \t]+)*(?P<w>[\w./+-]+)(?P<a>(?:[ \t]+[^\s;&|()<>`$]+)*)[ \t]*(?=$|[;&|()\n<>])")
PIPE_REST = re.compile(r"\|(?!\|)")
PRODUCER = re.compile(r"^\s*(?:echo|printf|print|cat|yes)\b(?:\s+-[a-zA-Z]+)*")
ORDINARY_ESCAPE = set("_./-:=@%+,^")


# ------------------------------------------------------------------ small helpers

def _unescape_dq(body: str) -> str:
    """The text a shell hands to `sh -c "..."`: only \\$ \\` \\" \\\\ and backslash-newline are escapes inside double quotes."""
    return re.sub(r'\\([$`"\\])|\\\r?\n', lambda m: m.group(1) or "", body)


def _code(body: str) -> str:
    """Interpreter source or piped text, made searchable: quotes and list punctuation become spaces."""
    t = re.sub(r"\\\r?\n", "", body)
    return re.sub(r"""['"`,\[\]]""", " ", t)


def _match_brace(s: str, j: int) -> int:
    depth = 1
    while j < len(s) and depth:
        depth += {"{": 1, "}": -1}.get(s[j], 0)
        j += 1
    return j


def _match_arith(s: str, j: int) -> int:
    depth = 2
    while j < len(s) and depth:
        depth += {"(": 1, ")": -1}.get(s[j], 0)
        j += 1
    return j


def _ansi_c(s: str, j: int) -> tuple[int, str]:
    out: list[str] = []
    n = len(s)
    while j < n and s[j] != "'":
        c = s[j]
        if c != "\\" or j + 1 >= n:
            out.append(c)
            j += 1
            continue
        d = s[j + 1]
        m = re.compile(r"x([0-9a-fA-F]{1,2})|([0-7]{1,3})|u([0-9a-fA-F]{1,4})|U([0-9a-fA-F]{1,8})").match(s, j + 1)
        if m:
            code = int(m.group(1) or m.group(3) or m.group(4) or m.group(2), 16 if m.group(2) is None else 8)
            out.append(chr(code) if code < 0x110000 else "")
            j = m.end()
            continue
        out.append({"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "e": "\x1b", "E": "\x1b"}.get(d, d))
        j += 2
    return j + 1, "".join(out)


def _command_word(stage_text: str) -> tuple[str, list[str]]:
    bare = PREFIX.sub("", stage_text.lstrip(), count=1).strip()
    toks = bare.split()
    return (toks[0].rsplit("/", 1)[-1].lower(), toks[1:]) if toks else ("", [])


def _stdin_kind(word: str, args: list[str]) -> str | None:
    """'shell' or 'interp' when this command would run text arriving on stdin as code, else None."""
    w = word.rsplit("/", 1)[-1].lower()
    kind = "shell" if SHELL_RE.match(w) else "interp" if INTERP_RE.match(w) else None
    if kind is None:
        return None
    args = [a for a in args if not re.match(r"^(?:\d*[<>]|&>|<<)", a)]
    if any(a in ("-s", "-") for a in args):
        return kind
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a in ("-o", "+o", "-W", "-X", "-O"):
            skip = True
        elif re.fullmatch(r"-[a-zA-Z]*[ceEr]|--eval|--command", a) or not a.startswith("-"):
            return None
    return kind


def _next_stage_kind(s: str, j: int) -> str | None:
    m = NEXT_STAGE.match(s, j)
    return _stdin_kind(m.group("w"), m.group("a").split()) if m else None


def _rest_of_line_pipes_to_code(s: str, j: int) -> str | None:
    e = s.find("\n", j)
    line = s[j:len(s) if e < 0 else e]
    for m in PIPE_REST.finditer(line):
        kind = _next_stage_kind(line, m.end())
        if kind:
            return kind
    return None


# ------------------------------------------------------------------ the scanner

class _Scan:
    def __init__(self, s: str, depth: int):
        self.s, self.n, self.depth = s, len(s), depth
        self.nest = 0

    def _sub(self, text: str) -> str:
        return skeleton(text, self.depth + 1)

    def walk(self, i: int, stop: str | None) -> tuple[str, str, list[str], int]:
        """Inside double quotes (stop='"') or an unquoted heredoc body (stop=None): collect expansions.

        Returns (plain text with each expansion replaced by $X, raw text, extracted commands, index after).
        """
        s, n = self.s, self.n
        plain: list[str] = []
        subs: list[str] = []
        start = i
        while i < n and (stop is None or s[i] != stop):
            c = s[i]
            if c == "\\" and i + 1 < n:
                plain.append(s[i:i + 2])
                i += 2
            elif c == "$" and s.startswith("$((", i):
                j = _match_arith(s, i + 3)
                inner = s[i + 3:j - 2]
                if re.search(r"[A-Za-z]", inner):
                    subs.append(self._sub(inner))
                plain.append("$X")
                i = j
            elif c == "$" and s.startswith("$(", i):
                txt, i = self.run(i + 2, ")")
                subs.append(txt)
                plain.append("$X")
            elif c == "`":
                txt, i = self.run(i + 1, "`")
                subs.append(txt)
                plain.append("$X")
            elif c == "$" and s.startswith("${", i):
                j = _match_brace(s, i + 2)
                inner = s[i + 2:j - 1]
                if "$(" in inner or "`" in inner:
                    subs.append(self._sub(inner))
                plain.append("$X")
                i = j
            else:
                plain.append(c)
                i += 1
        return "".join(plain), s[start:i], subs, (i + 1 if stop is not None else i)

    def run(self, i: int, term: str | None) -> tuple[str, int]:
        """Scan from i to the unquoted terminator `term` (')' or '`') or the end. Returns (canonical text, next index)."""
        s, n = self.s, self.n
        if self.nest >= MAX_NEST:  # absurd nesting: stop parsing, keep the rest searchable as text
            return " ; " + _code(s[i:]) + " ; ", n
        self.nest += 1
        out: list[str] = []
        deferred: list[str] = []
        pending: list[tuple[str, bool, bool, str | None]] = []
        stage_at = 0
        raw_at = i
        hs_kind: str | None = None
        paren = 0

        def stage() -> str:
            return "".join(out[stage_at:])

        def end_stage(sep_i: int, pipe: bool) -> None:
            nonlocal stage_at, raw_at, hs_kind
            if pipe and _next_stage_kind(s, sep_i + 1):
                deferred.append(_code(PRODUCER.sub("", s[raw_at:sep_i])))
            stage_at, raw_at, hs_kind = len(out), sep_i + 1, None

        def literal(plain: str, raw: str, quote: str) -> None:
            nonlocal hs_kind
            tail = stage()
            body = raw if quote == "'" else _unescape_dq(raw)
            if hs_kind:
                hs_kind = None
                deferred.append(_code(body))
                out.append('""')
            elif SHELL_C.search(tail):
                deferred.append(self._sub(body))
                out.append('""')
            elif INTERP_C.search(tail):
                deferred.append(_code(body))
                out.append('""')
            elif GIT_VALUE.search(tail) or ALIAS_TAIL.search(tail):
                shell = body[1:] if ALIAS_TAIL.search(tail) and body.startswith("!") else (body.split("=!", 1)[1] if re.match(r"alias\.[^=]*=!", body) else "")
                if shell:  # `git -c alias.p='!cmd' p` runs cmd through the shell
                    deferred.append(self._sub(shell))
                out.append(re.sub(r"\s", "\x01", plain))
            else:
                kind = SCRIPT_CMDS.get(_command_word(tail)[0])
                if kind and (SED_RISKY if kind == "sed" else AWK_RISKY).search(body):
                    deferred.append(_code(body))
                    out.append(SCRIPT_MARK)
                else:
                    out.append('""' if re.search(r"\s", plain) else plain)

        while i < n:
            c = s[i]
            if term is not None and paren == 0 and c == term:
                i += 1
                break
            if c == "\\":
                if i + 1 >= n:
                    i += 1
                    continue
                d = s[i + 1]
                if d == "\n":
                    i += 2
                elif d == "\r" and s[i + 2:i + 3] == "\n":
                    i += 3
                else:
                    out.append(d if d.isalnum() or d in ORDINARY_ESCAPE else "\\" + d)
                    i += 2
            elif c == "'":
                j = s.find("'", i + 1)
                j = n if j < 0 else j
                body = s[i + 1:j]
                i = j + 1
                literal(body, body, "'")
            elif c == '"':
                plain, raw, subs, i = self.walk(i + 1, '"')
                deferred.extend(subs)
                literal(plain, raw, '"')
            elif c == "$":
                nxt = s[i + 1:i + 2]
                if s.startswith("$((", i):
                    j = _match_arith(s, i + 3)
                    inner = s[i + 3:j - 2]
                    if re.search(r"[A-Za-z]", inner):
                        deferred.append(self._sub(inner))
                    out.append("$X")
                    i = j
                elif nxt == "(":
                    txt, i = self.run(i + 2, ")")
                    deferred.append(txt)
                    out.append("$X")
                elif nxt == "'":
                    i, decoded = _ansi_c(s, i + 2)
                    literal(decoded, decoded, "'")
                elif nxt == '"':
                    i += 1
                elif nxt == "{":
                    j = _match_brace(s, i + 2)
                    inner = s[i + 2:j - 1]
                    if "$(" in inner or "`" in inner:
                        deferred.append(self._sub(inner))
                    out.append("$X")
                    i = j
                else:
                    out.append("$")
                    i += 1
            elif c == "`":
                txt, i = self.run(i + 1, "`")
                deferred.append(txt)
                out.append("$X")
            elif c == "<" and s.startswith("<(", i):  # process substitution: text a shell reads as a script
                kind = _stdin_kind(*_command_word(stage()))
                start = i + 2
                txt, i = self.run(start, ")")
                deferred.append(txt)
                if kind:
                    deferred.append(_code(PRODUCER.sub("", s[start:i - 1])))
                out.append("/dev/fd/X")
            elif c == "<" and s.startswith("<<<", i):
                hs_kind = _stdin_kind(*_command_word(stage()))
                out.append("<<<")
                i += 3
            elif c == "<" and s.startswith("<<", i) and (m := HEREDOC_OP.match(s, i)):
                quoted = m.group(2) is not None or m.group(3) is not None or bool(m.group(4))
                delim = m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(5)
                kind = _stdin_kind(*_command_word(stage())) or _rest_of_line_pipes_to_code(s, m.end())
                pending.append((delim, quoted, bool(m.group(1)), kind))
                out.append("<<HEREDOC")
                i = m.end()
            elif c == "\n":
                out.append("\n")
                i += 1
                if pending:
                    i = self._heredoc_bodies(i, pending, deferred)
                    pending = []
                end_stage(i - 1, False)
                raw_at = i
            elif c in ";(){}":
                if c == "(":
                    paren += 1
                elif c == ")" and paren:
                    paren -= 1
                out.append(c)
                end_stage(i, False)
                i += 1
            elif c == "&":
                if (i and s[i - 1] in "<>") or s[i + 1:i + 2] == ">":
                    out.append(c)
                    i += 1
                else:
                    step = 2 if s[i + 1:i + 2] == "&" else 1
                    out.append(s[i:i + step])
                    end_stage(i + step - 1, False)
                    i += step
            elif c == "|":
                if i and s[i - 1] == ">":
                    out.append(c)
                    i += 1
                elif s[i + 1:i + 2] == "|":
                    out.append("||")
                    end_stage(i + 1, False)
                    i += 2
                else:
                    step = 2 if s[i + 1:i + 2] == "&" else 1
                    out.append("|")
                    end_stage(i + step - 1, True)
                    i += step
            else:
                out.append(c)
                i += 1
        self.nest -= 1
        return "".join(out) + "".join(f" ; {d} ; " for d in deferred), i

    def _heredoc_bodies(self, i: int, pending: list, deferred: list[str]) -> int:
        s, n = self.s, self.n
        for delim, quoted, tabs, kind in pending:
            lines: list[str] = []
            while i < n:
                e = s.find("\n", i)
                e = n if e < 0 else e
                line = s[i:e]
                i = e + 1 if e < n else n
                if (line.lstrip("\t") if tabs else line).rstrip("\r") == delim:
                    break
                lines.append(line)
            body = "\n".join(lines)
            if not quoted:
                deferred.extend(_Scan(body, self.depth + 1).walk(0, None)[2])
            if kind == "shell":
                deferred.append(self._sub(body))
            elif kind == "interp":
                deferred.append(_code(body))
        return i


def skeleton(command: str, _depth: int = 0) -> str:
    """The command with quoting, escapes, heredoc bodies and expansions resolved; separators are left for SPLIT."""
    if _depth > MAX_DEPTH:
        return " ; " + _code(command) + " ; "
    text, _ = _Scan(command, _depth).run(0, None)
    return NULL_REDIRECT.sub(" ", text)


def segments(command: str) -> list[str]:
    return [re.sub(r"\s+", " ", seg).strip() for seg in SPLIT.split(skeleton(command)) if seg.strip()]


# ------------------------------------------------------------------ git

GLOBAL_NOARG = {"-p", "-P", "--paginate", "--no-pager", "--bare", "--no-replace-objects", "--literal-pathspecs", "--glob-pathspecs",
                "--noglob-pathspecs", "--icase-pathspecs", "--no-optional-locks", "--no-lazy-fetch", "--no-advice"}
GLOBAL_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env", "--attr-source"}
# Config keys that make git run a program, change where it connects, or load more config. All are `high` (R-15).
DANGEROUS_KEY = re.compile(
    r"^(?:alias\.|core\.(?:sshcommand|pager|editor|fsmonitor|hookspath|askpass|gitproxy)$|credential\.|diff\.(?:external$|.*\.(?:command|textconv)$)"
    r"|merge\..*\.driver$|sequence\.editor$|filter\..*\.(?:clean|smudge|process)$|pager\.|protocol\.|url\.|include|gpg\.|uploadpack\.|receive\."
    r"|remote\..*\.(?:pushurl|url|receivepack|uploadpack|proxy)$)")


@dataclass
class GitCall:
    """One `git [global options] <sub> <args>` found in a segment."""

    sub: str = ""
    args: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    git_dir: str | None = None
    work_tree: str | None = None
    danger: list[str] = field(default_factory=list)


def git_calls(text: str) -> tuple[str, list[GitCall]]:
    """Rewrite every `git <global options> sub` to `git sub` and describe each invocation. Pure text, no process."""
    toks = text.split(" ")
    out: list[str] = []
    calls: list[GitCall] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t != "git" and not t.endswith("/git"):
            out.append(t)
            i += 1
            continue
        call = GitCall()
        j = i + 1
        while j < len(toks):
            name, eq, val = toks[j].partition("=")
            if toks[j] in GLOBAL_NOARG:
                j += 1
            elif name in GLOBAL_VALUE:
                if not eq:
                    if j + 1 >= len(toks):
                        break
                    val, j = toks[j + 1], j + 1
                j += 1
                val = val.replace("\x01", " ")
                if name == "-C":
                    call.dirs.append(val)
                elif name == "--git-dir":
                    call.git_dir = val
                elif name == "--work-tree":
                    call.work_tree = val
                elif name in ("-c", "--config-env"):
                    key = val.split("=", 1)[0].strip().lower()
                    if not key or "$" in key or '"' in key or DANGEROUS_KEY.search(key):
                        call.danger.append(f"{name} {key or '?'}")
            elif name == "--exec-path":
                call.danger.append("--exec-path")
                j += 1
            else:
                break
        call.sub = toks[j] if j < len(toks) else ""
        call.args = [a for a in toks[j + 1:] if a]
        calls.append(call)
        out.append("git")
        i = j
    return " ".join(out), calls
