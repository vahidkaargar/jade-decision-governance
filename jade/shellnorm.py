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
import shlex
from dataclasses import dataclass, field

from . import interp

MAX_DEPTH = 8
MAX_NEST = 60
BUDGET = 4_000_000     # characters the scanner may read across all nested levels; past it the rest is searched as plain text
UNSAFE_MARK = "@unsafe!"   # prefix of the copy of a stage whose verb can write or run something, so rules see its resolved operands
UNSAFE_GIT = "git:unsafe-config "
TEXT_MARK = "@text!"   # prefix of a line the scanner extracted from a program (`import jade.cli`): words for the invariants to read, no operands to resolve

NULL_REDIRECT = re.compile(r"(\d*>&\d+|&>>?\s*/dev/null|\d*>>?\s*/dev/null|\d*<\s*/dev/null)")
SPLIT = re.compile(r"\n|;|&&|\|\||(?<!>)\||(?<![<>])&(?!>)|\$\(|`|\(|\)|\{|\}")  # `>&`, `>|` and `&>` are redirections, not separators
# Package runners that start the next word as a program: `uv run python -c ...` is `python -c ...` (its quoted code must be scanned, not read as prose).
_RUN_OPT = r"(?:(?:-w|-p|--with|--with-requirements|--with-editable|--python|--project|--directory|--package|--group|--env-file|--extra|--from|--index|--index-url)\s+\S+|--?[A-Za-z][\w-]*(?:=\S+)?)"
_RUNNER = (r"(?:uv(?:\s+tool)?\s+run|uvx|poetry\s+run|pdm\s+run|pipenv\s+run|rye\s+run|hatch\s+run|pipx\s+run|conda\s+run|npx|bunx|pnpm\s+(?:exec|dlx)"
           rf"|yarn\s+(?:exec|dlx)|npm\s+exec)(?:\s+(?:{_RUN_OPT}|--))*")
PREFIX = re.compile(
    r"^(?:(?:" + _RUNNER + r"|[A-Za-z_]\w*=\S*|sudo(?:\s+(?:-[ugphCDrtUT]\s+\S+|-[A-Za-z-]+))*|command|builtin|nohup|time|exec|noglob|do|then|else|if|while|until|!"
    r"|env(?:\s+-[A-Za-z-]+)*|g?timeout(?:\s+-\S+)*\s+\d+[smhd]?|nice(?:\s+-n?\s*\d+)?|caffeinate(?:\s+-\w+)*"
    r"|xargs(?:\s+-(?:I\s*\S+|n\s*\d+|P\s*\d+|[0a-zA-Z]+))*)\s+)+")

ALIAS_TAIL = re.compile(r"\s-c\s+alias\.[^\s=]*=$")
GIT_VALUE = re.compile(r"(?:^|[\s(;&|])(?:\S*/)?git(?:\s+\S+)*?\s+-[Cc]\s+$|--(?:git-dir|work-tree|namespace)=$|--(?:git-dir|work-tree|namespace)\s+$")

SHELL_RE = re.compile(r"^(?:(?:ba|z|da|k|a|fi|c|tc)?sh|source|\.)$")
INTERP_RE = re.compile(r"^(?:python[\d.]*|pypy\d*|perl\d*|ruby\d*|node(?:js)?|php\d*|osascript|lua\d*|deno|bun)$")
STDIN_PATHS = {"/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
CODE_FLAG = re.compile(r"-[a-zA-Z]*[ceEr]|--eval|--print|--command")
SHELL_NAME = re.compile(r"^(?:ba|z|da|k|a|fi|c|tc)?sh$")
PLAIN = re.compile(r"[^\\'\"$`<\n;(){}&|#]+")
_INERT = re.compile(r"[;&|<>(){}`]")   # characters a quoted word may hold that the splitter and the redirect check would read as syntax

HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|(\\?)([^\s;&|<>()'\"\\`$]+))")
NEXT_STAGE = re.compile(
    r"[ \t]*(?:\w+=\S*[ \t]+)*(?:(?:sudo|env|command|exec|nohup|time|xargs)(?:[ \t]+-[ugphCDrtUT][ \t]+\S+|[ \t]+-\S+(?:[ \t]+\d+)?)*[ \t]+)*(?P<w>[\w./+-]+)(?P<a>(?:[ \t]+[^\s;&|()<>`$]+)*)[ \t]*(?=$|[;&|()\n<>])")
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
    """'shell', or the language of an interpreter (`python`, `perl` ...), when this command would run text arriving on stdin as code, else None."""
    w = word.rsplit("/", 1)[-1].lower()
    kind = "shell" if SHELL_RE.match(w) else interp.language_of(w) if INTERP_RE.match(w) else None
    if kind is None:
        return None
    args = [a for a in args if not re.match(r"^(?:\d*[<>]|&>|<<)", a)]
    if any(a in ("-s", "-") or a in STDIN_PATHS for a in args):
        return kind
    skip = False
    for idx, a in enumerate(args):
        if skip:
            skip = False
        elif a in ("-o", "+o", "-W", "-X", "-O"):
            skip = True
        elif CODE_FLAG.fullmatch(a):
            return kind if idx == len(args) - 1 else None  # `xargs sh -c`: the script arrives on stdin
        elif not a.startswith("-"):
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


# ------------------------------------------------------------------ words: what the shell hands to a command

def _enc(text: str) -> str:
    """Whitespace inside a quoted or escaped word is kept apart from word separators."""
    return text.replace(" ", "\x01").replace("\t", "\x02").replace("\n", "\x03").replace("\r", "\x04")


def _dec(text: str) -> str:
    return text.replace("\x01", " ").replace("\x02", "\t").replace("\x03", "\n").replace("\x04", "\r")


def _skip_span(s: str, j: int) -> int:
    """s[j] is '(' of `$(`, `<(` or `>(`: index after the matching ')', quote and escape aware."""
    depth, n = 0, len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            k = s.find("'", j + 1)
            j = n if k < 0 else k + 1
            continue
        if c == '"':
            j = _dq_end(s, j + 1) + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _dq_end(s: str, j: int) -> int:
    """Index of the double quote that closes the string whose body starts at j (or len(s))."""
    n = len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
        elif c == '"':
            return j
        elif c == "$" and s.startswith("$(", j):
            j = _skip_span(s, j + 1)
        elif c == "`":
            k = j + 1
            while k < n and s[k] != "`":
                k += 2 if s[k] == "\\" else 1
            j = k + 1
        else:
            j += 1
    return n


def words_of(raw: str) -> list[str]:
    """The words of one simple command with quoting, escapes and comments resolved (nothing is expanded or run).

    A space, tab or newline that was quoted or escaped is kept as \\x01 \\x02 \\x03 so `_dec` can restore it and a
    word is never split by it. `$(..)`, backtick and `${..}` spans stay in the word as written.
    """
    out: list[str] = []
    cur: list[str] = []
    have = False
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c in " \t\r\n":
            if have:
                out.append("".join(cur))
                cur, have = [], False
            i += 1
        elif c == "\\":
            d = raw[i + 1:i + 2]
            if d == "\n":
                i += 2
            elif d == "\r" and raw[i + 2:i + 3] == "\n":
                i += 3
            else:
                cur.append(_enc(d))
                have = True
                i += 2
        elif c == "'":
            j = raw.find("'", i + 1)
            j = n if j < 0 else j
            cur.append(_enc(raw[i + 1:j]))
            have = True
            i = j + 1
        elif c == '"':
            j = _dq_end(raw, i + 1)
            cur.append(_enc(_unescape_dq(raw[i + 1:j])))
            have = True
            i = j + 1
        elif c == "$":
            nxt = raw[i + 1:i + 2]
            if nxt == "'":
                i, dec = _ansi_c(raw, i + 2)
                cur.append(_enc(dec))
            elif nxt == '"':
                i += 1
                continue
            elif nxt == "(":
                j = _skip_span(raw, i + 1)
                cur.append(_enc(raw[i:j]))
                i = j
            elif nxt == "{":
                j = _match_brace(raw, i + 2)
                cur.append(_enc(raw[i:j]))
                i = j
            else:
                cur.append("$")
                i += 1
            have = True
        elif c == "`":
            k = i + 1
            while k < len(raw) and raw[k] != "`":
                k += 2 if raw[k] == "\\" else 1
            cur.append(_enc(raw[i:k + 1]))
            have = True
            i = k + 1
        elif c in "<>" and raw[i + 1:i + 2] == "(":
            j = _skip_span(raw, i + 1)
            cur.append(_enc(raw[i:j]))
            have = True
            i = j
        elif c == "#" and not have:
            e = raw.find("\n", i)
            i = n if e < 0 else e
        else:
            m = re.compile(r"[^\\'\"$`\s<>#]+|.").match(raw, i)
            cur.append(m.group())
            have = True
            i = m.end()
    if have:
        out.append("".join(cur))
    return out


def brace_expand(word: str, cap: int = 2048) -> list[str]:
    """Bash brace expansion of one word (`m{a,}in` -> main min). Only comma lists; at most `cap` results."""
    i = word.find("{")
    while i >= 0:
        depth, j, commas = 0, i, []
        while j < len(word):
            if word[j] == "{":
                depth += 1
            elif word[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            elif word[j] == "," and depth == 1:
                commas.append(j)
            j += 1
        if j < len(word) and commas:
            pre, post = word[:i], word[j + 1:]
            cuts = [i, *commas, j]
            alts = [word[cuts[k] + 1:cuts[k + 1]] for k in range(len(cuts) - 1)]
            out: list[str] = []
            for alt in alts:
                for tail in brace_expand(post, cap):
                    for mid in brace_expand(alt, cap):
                        out.append(pre + mid + tail)
                        if len(out) >= cap:
                            return out
            return out
        i = word.find("{", i + 1)
    return [word]


BRACE_WORD = re.compile(r"[^\s;&|()<>`]*\{[^\s]*\}[^\s;&|()<>`]*")


def expand_braces(text: str) -> str:
    if "{" not in text or "," not in text:
        return text

    def one(m: re.Match) -> str:
        w = m.group()
        return " ".join(brace_expand(w)) if "," in w else w

    return BRACE_WORD.sub(one, text)


def _brace_span(s: str, i: int) -> int:
    """s[i] == '{' starting a brace-expansion word like `{a,b}`: index after its '}', or -1 when it is a command group."""
    if s[i + 1:i + 2] in ("", " ", "\t", "\n"):
        return -1
    depth, j, comma = 0, i, False
    while j < len(s):
        c = s[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1 if comma else -1
        elif c == ",":
            comma = comma or depth == 1
        elif c in " \t\n;&|<>()'\"`$\\":
            return -1
        j += 1
    return -1


# ------------------------------------------------------------------ what a stage runs or writes, by verb

CODE_ENV = {"LESSOPEN", "LESSCLOSE", "PAGER", "MANPAGER", "BROWSER", "EDITOR", "VISUAL", "BASH_ENV", "ENV", "PROMPT_COMMAND",
            "GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_EXTERNAL_DIFF", "GIT_ASKPASS",
            "SSH_ASKPASS", "GIT_PROXY_COMMAND"}
GIT_RUNS = {"GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_EXTERNAL_DIFF", "GIT_ASKPASS",
            "GIT_PROXY_COMMAND", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR"}
BENIGN_ENV = re.compile(r"(?:true|false|:|cat|less(?: -\w+)*|more|vi|vim|nvim|nano|emacs|code --wait|"
                        r"ssh(?: -[iFpl] [\w./~@-]+| -o \w+=[\w./~-]+| -[a-zA-Z]+)*)?")
GIT_EXEC_LONG = ("upload-pack", "receive-pack", "exec", "extcmd", "tree-filter", "index-filter", "msg-filter", "commit-filter",
                 "env-filter", "tag-name-filter", "open-files-in-pager")
UNIQ_VALUE = {"-f", "-s", "-w", "--skip-fields", "--skip-chars", "--check-chars"}
XXD_VALUE = {"-c", "-g", "-l", "-s", "-o", "-n", "-cols", "-groupsize", "-len", "-seek", "-offset", "-name"}
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
FIND_WRITE = {"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
SED_SAFE_CMD = set("pdDnNPgGhHxlz=")
SED_TEXT_CMD = set("aic")
SED_FILE_READ = set("rRF")


def _long(a: str, name: str, minlen: int = 3):
    """`--na`, `--name=v`: the value ('' when absent) if `a` is an unambiguous abbreviation of long option `name`, else None."""
    if not a.startswith("--"):
        return None
    key, eq, val = a[2:].partition("=")
    if len(key) >= minlen and name.startswith(key):
        return val if eq else ""
    return None


def _has_long(args: list[str], name: str, minlen: int = 3) -> bool:
    return any(_long(a, name, minlen) is not None for a in args)


def _opt_values(args: list[str], shorts: str, longs: tuple[tuple[str, int], ...]) -> list[str]:
    """Values given to `-X v`, `-Xv`, `--long=v` and `--long v` (the word after a bare long option counts)."""
    vals: list[str] = []
    for i, a in enumerate(args):
        if a == "--":
            break
        if a.startswith("--"):
            for name, minlen in longs:
                v = _long(a, name, minlen)
                if v is not None:
                    vals.append(v if "=" in a else (args[i + 1] if i + 1 < len(args) else ""))
        elif a.startswith("-") and len(a) >= 2 and a[1] in shorts:
            vals.append(a[2:] if len(a) > 2 else (args[i + 1] if i + 1 < len(args) else ""))
    return [v for v in vals if v]


def _positionals(args: list[str], valued: set[str]) -> int:
    n, skip = 0, False
    for k, a in enumerate(args):
        if skip:
            skip = False
        elif a == "--":
            return n + len(args) - k - 1
        elif a in valued:
            skip = True
        elif a == "-" or not a.startswith("-"):
            n += 1
    return n


def _delim_end(s: str, i: int, d: str, bracket: bool) -> int:
    """Index of the unescaped delimiter `d` at or after i (skipping `[...]` classes when `bracket`), or -1."""
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if bracket and c == "[":
            k = i + 1
            if s[k:k + 1] == "^":
                k += 1
            if s[k:k + 1] == "]":
                k += 1
            while k < n and s[k] != "]":
                k += 1
            i = k + 1
            continue
        if c == d:
            return i
        i += 1
    return -1


def sed_is_safe(script: str) -> bool:
    """True when a sed script uses only commands that neither write a file nor run a command (an allow-list, fail closed)."""
    i, n = 0, len(script)
    while True:
        while i < n and script[i] in " \t\n;":
            i += 1
        if i >= n:
            return True
        for _ in range(2):  # up to two addresses
            if i < n and (script[i].isdigit() or script[i] in "$+~"):
                while i < n and (script[i].isdigit() or script[i] in "$~+"):
                    i += 1
            elif i < n and script[i] in "/\\":
                d = "/"
                if script[i] == "\\":
                    d, i = script[i + 1:i + 2], i + 1
                    if not d:
                        return False
                e = _delim_end(script, i + 1, d, True)
                if e < 0:
                    return False
                i = e + 1
                while i < n and script[i] in "IM":
                    i += 1
            else:
                break
            if i < n and script[i] == ",":
                i += 1
            else:
                break
        while i < n and script[i] in " \t!":
            i += 1
        if i >= n:
            return False
        c = script[i]
        if c in "{}":
            i += 1
        elif c in SED_SAFE_CMD:
            i += 1
        elif c in "qQL":
            i += 1
            while i < n and script[i].isdigit():
                i += 1
        elif c == "#":
            e = script.find("\n", i)
            i = n if e < 0 else e
        elif c in SED_TEXT_CMD or c in SED_FILE_READ or c in ":btT":
            e = i
            while True:
                e = script.find("\n", e)
                if e < 0:
                    e = n
                    break
                if script[e - 1] == "\\" and c in SED_TEXT_CMD:
                    e += 1
                    continue
                break
            i = e
        elif c in "sy":
            d = script[i + 1:i + 2]
            if not d or d in "\\\n":
                return False
            e1 = _delim_end(script, i + 2, d, True)
            e2 = _delim_end(script, e1 + 1, d, False) if e1 >= 0 else -1
            if e2 < 0:
                return False
            i = e2 + 1
            if c == "s":
                while i < n and script[i] not in " \t\n;}":
                    if not (script[i] in "gpiImM" or script[i].isdigit()):
                        return False  # w, e and anything unknown
                    i += 1
        else:
            return False


def _sed_scripts(args: list[str]) -> tuple[list[str], bool]:
    """(program texts, edits in place or reads a script file) from sed's arguments."""
    scripts: list[str] = []
    pos: list[str] = []
    risky = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos.extend(args[i + 1:])
            break
        if a.startswith("--"):
            v = _long(a, "expression", 1)
            if v is not None:
                if "=" in a:
                    scripts.append(v)
                elif i + 1 < len(args):
                    scripts.append(args[i + 1])
                    i += 1
            elif _long(a, "in-place", 1) is not None or _long(a, "file", 2) is not None:
                risky = True
        elif a.startswith("-") and len(a) > 1:
            head, e, tail = a[1:].partition("e")
            risky = risky or bool(re.search(r"[iIf]", head))
            if e:  # `-ne 'p'`, `-es/a/b/`
                if tail:
                    scripts.append(tail)
                elif i + 1 < len(args):
                    scripts.append(args[i + 1])
                    i += 1
        else:
            pos.append(a)
        i += 1
    if not scripts and pos:
        scripts.append(pos[0])
    return scripts, risky


def _short_flag(args: list[str], letters: str) -> bool:
    """True when a short-option cluster (`-nro`) contains one of `letters`. Long options never count."""
    return any(a.startswith("-") and not a.startswith("--") and len(a) > 1 and any(ch in letters for ch in a[1:]) for a in args)


def git_effects(call: GitCall) -> tuple[bool, list[str]]:
    """(writes a file, program texts) for one git invocation: exec-capable options and subcommands, `--output`."""
    args = [_dec(a) for a in call.args]
    code: list[str] = []
    unsafe = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if a.startswith("--"):
            if _long(a, "output", 3) is not None:
                unsafe = True
            for name in GIT_EXEC_LONG:
                v = _long(a, name, 3)
                if v is not None:
                    if "=" not in a and i + 1 < len(args):
                        v, i = args[i + 1], i + 1
                    code.append(v)
                    break
        elif a.startswith("-x") and call.sub == "rebase":
            if len(a) > 2:
                code.append(a[2:])
            elif i + 1 < len(args):
                code.append(args[i + 1])
                i += 1
        elif a == "-u" and call.sub == "clone" and i + 1 < len(args):
            code.append(args[i + 1])
            i += 1
        elif a.startswith("-O") and call.sub == "grep" and len(a) > 2:
            code.append(a[2:])
        i += 1
    if call.sub == "submodule" and "foreach" in args:
        tail = [a for a in args[args.index("foreach") + 1:] if a not in ("--recursive", "-q", "--quiet")]
        code.append(" ".join(tail))
    if call.sub == "bisect" and args[:1] == ["run"]:
        code.append(" ".join(args[1:]))
    return unsafe or bool(code), [c for c in code if c.strip()]


def verb_effects(cmd: str, args: list[str]) -> tuple[bool, list[str], list[str]]:
    """(can write a file or run a program, shell commands it carries, program text to search) for a command and its decoded arguments.

    Only the verbs the `read_only` list admits need an entry: everything else is already classified by its own rules.
    """
    unsafe, code, text = False, [], []
    if cmd == "sort":
        unsafe = _short_flag(args, "o") or _has_long(args, "output", 1) or _has_long(args, "compress-program", 2)
        code = _opt_values(args, "", (("compress-program", 2),))
    elif cmd == "uniq":
        unsafe = _positionals(args, UNIQ_VALUE) >= 2
    elif cmd == "xxd":
        unsafe = any(re.fullmatch(r"-(?:[a-zA-Z]{1,3}|revert)", a) and "r" in a for a in args) or _positionals(args, XXD_VALUE) >= 2
    elif cmd in ("sed", "gsed"):
        scripts, risky = _sed_scripts(args)
        bad = [sc for sc in scripts if not sed_is_safe(sc)]
        unsafe, text = risky or bool(bad), bad
    elif cmd in ("awk", "gawk", "mawk", "nawk"):
        prog, i = None, 0
        while i < len(args):
            a = args[i]
            if a in ("-F", "-v", "-W"):
                i += 1
            elif a in ("-f", "-i", "-E", "-l") or any(_long(a, n, 2) is not None for n in ("file", "include", "load", "exec")):
                unsafe = True
            elif not a.startswith("-"):
                prog = a
                break
            i += 1
        if prog is not None:
            fx = interp.awk_effects(prog)   # the literal commands and files of its system(), print | and print > ; any other string is data
            unsafe = unsafe or bool(fx.commands or fx.writes or fx.dynamic)
            code = [*fx.commands, *("tee " + shlex.quote(_enc(w)) for w in fx.writes)]
            text = [prog] if fx.dynamic else []   # a target the program text does not contain: the whole program is searched, as before
    elif cmd == "find":
        unsafe = any(a in FIND_WRITE for a in args)
        for i, a in enumerate(args):
            if a in FIND_EXEC:
                unsafe = True
                tail = []
                for b in args[i + 1:]:
                    if b in (";", "+"):
                        break
                    tail.append(b)
                code.append(" ".join(tail))
    elif cmd in ("less", "more"):
        unsafe = _short_flag(args, "oO") or _has_long(args, "log-file", 3) or _has_long(args, "LOG-FILE", 3)
        code = [a.split("!", 1)[1] for a in args if a.startswith("+") and "!" in a]
    elif cmd == "man":
        code = _opt_values(args, "PH", (("pager", 2), ("html", 2)))
        unsafe = bool(code) or "-w" in args
    elif cmd == "bat":
        code = _opt_values(args, "", (("pager", 3),))
    elif cmd in ("rg", "ag", "ack"):
        code = _opt_values(args, "", (("pre", 3), ("pager", 3), ("hostname-bin", 3)))
    elif cmd == "fd":
        for i, a in enumerate(args):
            if a in ("-x", "-X", "--exec", "--exec-batch"):
                code.append(" ".join(args[i + 1:]))
                break
    elif cmd == "tree":
        unsafe = _short_flag(args, "o")
    elif cmd == "yq":
        unsafe = _short_flag(args, "i") or _has_long(args, "inplace", 3)
    elif cmd == "date":
        unsafe = _short_flag(args, "s") or _has_long(args, "set", 3)
    elif cmd == "file":
        unsafe = _short_flag(args, "C")
    return unsafe or bool(code) or bool(text), [c for c in code if c.strip()], [t for t in text if t.strip()]


def _positional_args(code: str, extras: list[str]) -> str:
    """`sh -c 'script' a b`: $0 is a, $1 is b, $@ and $* are b..."""
    def sub(m: re.Match) -> str:
        k = m.group(1) or m.group(2)
        if k is not None:
            return extras[int(k)] if int(k) < len(extras) else ""
        return " ".join(extras[1:])
    code = re.sub(r'"\$[@*]"|"\$\{[@*]\}"', lambda _: " ".join(extras[1:]), code) if extras else code
    return re.sub(r"\$(?:(\d)|\{(\d)\}|[@*]|\{[@*]\})", sub, code) if extras else code


def shell_script(args: list[str]) -> tuple[str, list[str]] | None:
    """The script word of `sh [options] -c script [name args...]` (decoded), or None when no script is given on the command line."""
    i = 0
    while i < len(args):
        a = _dec(args[i])
        if a in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 2
        elif a == "--" or not (a.startswith("-") or a.startswith("+")):
            return None
        elif re.fullmatch(r"-[a-zA-Z]+", a) and "c" in a:
            if i + 1 < len(args):
                return _dec(args[i + 1]), [_dec(x) for x in args[i + 2:]]
            return None
        else:
            i += 1
    return None


def interp_code(args: list[str]) -> str | None:
    """The program text of `python -c`, `perl -e`, `node -p`, `ruby -e`, `php -r` ... (decoded), else None."""
    prev_flag = False
    if args and _dec(args[0]) == "eval" and len(args) > 1:  # `deno eval '...'`
        return _dec(args[1])
    for i, raw in enumerate(args):
        a = _dec(raw)
        if CODE_FLAG.fullmatch(a):
            return _dec(args[i + 1]) if i + 1 < len(args) else None
        if a.startswith("-"):
            prev_flag = True
        elif prev_flag:
            prev_flag = False  # the value of the previous option
        else:
            return None
    return None


# ------------------------------------------------------------------ the scanner

ENV_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
EXPORTISH = {"export", "declare", "typeset", "local", "readonly"}
PRODUCER_SUBST = re.compile(r"\s*(?:\$\((.*)\)|`(.*)`)\s*", re.S)


def git_env_danger(name: str, val: str) -> bool:
    if name == "GIT_CONFIG_PARAMETERS":
        return any(DANGEROUS_KEY.search(k.lower()) for k in re.findall(r"'([^'=]+)=", val))
    if re.fullmatch(r"GIT_CONFIG_KEY_\d+", name):
        return bool(DANGEROUS_KEY.search(val.strip().lower()))
    if name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        return val.strip() != "/dev/null"
    return False


class _Scan:
    def __init__(self, s: str, depth: int, budget: list[int]):
        self.s, self.n, self.depth, self.budget = s, len(s), depth, budget
        self.nest = 0

    def _sub(self, text: str) -> str:
        return skeleton(text, self.depth + 1, self.budget)

    def _produced(self, raw: str, kind: str) -> str:
        """What `echo ... | sh` hands to the reader: the echoed words, parsed as shell for a shell, searched as text otherwise."""
        text = " ".join(_dec(w) for w in words_of(PRODUCER.sub("", raw, count=1)))
        return self._sub(text) if kind == "shell" else self._interp(kind, text)

    def _interp(self, lang: str, body: str) -> str:
        """What a program in `lang` does (interp.py): its literal commands as nested shell text, its literal write targets as the destination of a
        `tee`, and the lines other rules read as written. Every other string in it is data and is dropped."""
        fx = interp.effects(lang, body)
        parts = [self._sub(c) for c in fx.commands]
        parts += [self._sub("tee " + shlex.quote(_enc(p))) for p in fx.writes]
        return " ; ".join(parts + [f"{TEXT_MARK} {t}" for t in fx.texts])

    def _backtick(self, i: int, in_dq: bool) -> tuple[str, int]:
        """Canonical text of the backtick span opening at s[i-1], and the index after it. An escaped backtick inside nests, as in the shell."""
        s, n = self.s, self.n
        j = i
        while j < n and s[j] != "`":
            j += 2 if s[j] == "\\" else 1
        body = s[i:j]
        body = re.sub(r'\\([`\\$"])' if in_dq else r"\\([`\\$])", r"\1", body)
        return self._sub(body), min(j + 1, n)

    def _script(self, code: str, deferred: list[str]) -> None:
        deferred.append(self._sub(code))
        m = PRODUCER_SUBST.fullmatch(code)
        inner = (m.group(1) or m.group(2) or "") if m else ""
        if PRODUCER.match(inner):  # `eval "$(echo git push)"`: the output of an echo is the program
            deferred.append(self._produced(inner, "shell"))

    def _stage(self, raw: str, deferred: list[str]) -> None:
        """Effects of one simple command: shell and interpreter programs it carries, writes and exec options of its verb."""
        if self.budget[0] < 0:
            return
        self.budget[0] -= len(raw)
        ws = words_of(raw)
        if not ws:
            return
        enc = " ".join(ws)
        bare = PREFIX.sub("", enc, count=1)
        rest = bare.split(" ") if bare else []
        cmd = _dec(rest[0]).rsplit("/", 1)[-1].lower() if rest else ""
        scan_env = enc[:len(enc) - len(bare)].split(" ") + (rest[1:] if cmd in EXPORTISH else [])
        for w in scan_env:
            m = ENV_ASSIGN.match(w)
            if not m:
                continue
            name, val = m.group(1), _dec(m.group(2))
            plain = bool(BENIGN_ENV.fullmatch(val.strip()))
            if name in CODE_ENV and not plain and val.lstrip("|!").strip():
                deferred.append(self._sub(val.lstrip("|!").strip()))
            if (name in GIT_RUNS and not plain) or git_env_danger(name, val):
                deferred.append(UNSAFE_GIT + " ".join(_dec(x) for x in ws))
        if not rest:
            return
        args = rest[1:]
        adec = [_dec(a) for a in args]
        unsafe, code = False, []
        if cmd == "eval":
            self._script(" ".join(adec[1:] if adec[:1] == ["--"] else adec), deferred)
        elif SHELL_NAME.match(cmd):
            found = shell_script(args)
            if found:
                script, extras = found
                self._script(script, deferred)
                if extras and (alt := _positional_args(script, extras)) != script:
                    self._script(alt, deferred)
        elif cmd == "su":
            for k, a in enumerate(adec):
                if a in ("-c", "--command") and k + 1 < len(adec):
                    self._script(adec[k + 1], deferred)
                elif a.startswith("--command="):
                    self._script(a.split("=", 1)[1], deferred)
        elif INTERP_RE.match(cmd):
            prog = interp_code(args)
            if prog is not None:
                deferred.append(self._interp(interp.language_of(cmd) or "", prog))
        elif cmd == "git":
            for call in git_calls(bare)[1]:
                u, c = git_effects(call)
                unsafe, code = unsafe or u, code + c
        else:
            unsafe, code, text = verb_effects(cmd, adec)
            deferred.extend(_code(t) for t in text)
        deferred.extend(self._sub(c) for c in code)
        if unsafe:
            deferred.append(UNSAFE_MARK + " " + " ".join(_dec(w) for w in ws))

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
                txt, i = self._backtick(i + 1, True)
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
        if self.nest >= MAX_NEST or self.budget[0] < 0:  # absurd nesting or size: stop parsing, keep the rest searchable as text
            return " ; " + _code(s[i:]) + " ; ", n
        self.nest += 1
        out: list[str] = []
        deferred: list[str] = []
        pending: list[tuple[str, bool, bool, str | None]] = []
        cur = ""  # the current stage so far: its first and last characters are all the checks below look at
        chain: list[str] = []  # the earlier stages of the pipeline being read: any of them may feed text to a shell at its end
        raw_at = i
        stop = n
        hs_kind: str | None = None
        paren = 0

        def emit(x: str) -> None:
            nonlocal cur
            out.append(x)
            cur += x
            if len(cur) > 700:
                cur = cur[:300] + cur[-300:]

        def end_stage(sep_at: int, resume: int, pipe: bool) -> None:
            nonlocal cur, raw_at, hs_kind
            raw = s[raw_at:sep_at]
            if raw.strip():
                self._stage(raw, deferred)
            if pipe:
                chain.append(raw)
                if kind := _next_stage_kind(s, resume):
                    deferred.extend(self._produced(r, kind) for r in chain)
            else:
                chain.clear()
            cur, raw_at, hs_kind = "", resume, None

        def literal(plain: str, raw: str, quote: str) -> None:
            nonlocal hs_kind
            body = raw if quote == "'" else _unescape_dq(raw)
            tail = cur.rstrip()
            if hs_kind:
                kind, hs_kind = hs_kind, None
                deferred.append(self._sub(body) if kind == "shell" else self._interp(kind, body))
                emit('""')
            elif tail.endswith(("-c", "-C", "=", "--git-dir", "--work-tree", "--namespace")) and (
                    ALIAS_TAIL.search(cur) or GIT_VALUE.search(cur)):
                shell = body[1:] if ALIAS_TAIL.search(cur) and body.startswith("!") else (body.split("=!", 1)[1] if re.match(r"alias\.[^=]*=!", body) else "")
                if shell:  # `git -c alias.p='!cmd' p` runs cmd through the shell
                    deferred.append(self._sub(shell))
                emit(re.sub(r"\s", "\x01", plain))
            elif cur.lstrip().startswith(("cd ", "pushd ")):
                emit(re.sub(r"\s", "\x01", plain))  # keep a directory with spaces readable for cwd tracking
            else:
                emit('""' if re.search(r"\s", plain) else _INERT.sub("\x06", plain))   # `awk 'NR>1'`: a quoted `>` is data, not a redirect

        while i < n:
            c = s[i]
            if term is not None and paren == 0 and c == term:
                stop = i
                i += 1
                break
            if m := PLAIN.match(s, i):
                emit(m.group())
                i = m.end()
            elif c == "\\":
                if i + 1 >= n:
                    i += 1
                    continue
                d = s[i + 1]
                if d == "\n":
                    i += 2
                elif d == "\r" and s[i + 2:i + 3] == "\n":
                    i += 3
                else:
                    emit(d if d.isalnum() or d in ORDINARY_ESCAPE else "\\" + d)
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
                    emit("$X")
                    i = j
                elif nxt == "(":
                    txt, i = self.run(i + 2, ")")
                    deferred.append(txt)
                    emit("$X")
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
                    emit("$" + inner if re.fullmatch(r"[A-Za-z_]\w*", inner) else "$X")  # `${HOME}` stays readable as $HOME
                    i = j
                else:
                    emit("$")
                    i += 1
            elif c == "`":
                txt, i = self._backtick(i + 1, False)
                deferred.append(txt)
                emit("$X")
            elif c == "#" and (not cur or cur[-1] in " \t"):  # a comment ends at the newline, whatever a backslash says
                e = s.find("\n", i)
                i = n if e < 0 else e
            elif c == "<" and s.startswith("<(", i):  # process substitution: text a shell reads as a script
                kind = _stdin_kind(*_command_word(cur))
                start = i + 2
                txt, i = self.run(start, ")")
                deferred.append(txt)
                if kind:
                    deferred.append(self._produced(s[start:i - 1], kind))
                emit("/dev/fd/X")
            elif c == "<" and s.startswith("<<<", i):
                hs_kind = _stdin_kind(*_command_word(cur))
                emit("<<<")
                i += 3
            elif c == "<" and s.startswith("<<", i) and (m := HEREDOC_OP.match(s, i)):
                quoted = m.group(2) is not None or m.group(3) is not None or bool(m.group(4))
                delim = m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(5)
                kind = _stdin_kind(*_command_word(cur)) or _rest_of_line_pipes_to_code(s, m.end())
                pending.append((delim, quoted, bool(m.group(1)), kind))
                emit("<<HEREDOC")
                i = m.end()
            elif c == "\n":
                nl = i
                emit("\n")
                i += 1
                if pending:
                    i = self._heredoc_bodies(i, pending, deferred)
                    pending = []
                end_stage(nl, i, False)
            elif c == "{" and (j := _brace_span(s, i)) > 0:  # brace expansion word, not a command group
                emit(s[i:j])
                i = j
            elif c in ";(){}":
                if c == "(":
                    paren += 1
                elif c == ")" and paren:
                    paren -= 1
                emit(c)
                end_stage(i, i + 1, False)
                i += 1
            elif c == "&":
                if (i and s[i - 1] in "<>") or s[i + 1:i + 2] == ">":
                    emit(c)
                    i += 1
                else:
                    step = 2 if s[i + 1:i + 2] == "&" else 1
                    emit(s[i:i + step])
                    end_stage(i, i + step, False)
                    i += step
            elif c == "|":
                if i and s[i - 1] == ">":
                    emit(c)
                    i += 1
                elif s[i + 1:i + 2] == "|":
                    emit("||")
                    end_stage(i, i + 2, False)
                    i += 2
                else:
                    step = 2 if s[i + 1:i + 2] == "&" else 1
                    emit("|")
                    end_stage(i, i + step, True)
                    i += step
            else:
                emit(c)
                i += 1
        raw = s[raw_at:stop]
        if raw.strip():
            self._stage(raw, deferred)
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
                deferred.extend(_Scan(body, self.depth + 1, self.budget).walk(0, None)[2])
            if kind == "shell":
                deferred.append(self._sub(body))
            elif kind:
                deferred.append(self._interp(kind, body))
        return i


def skeleton(command: str, _depth: int = 0, _budget: list[int] | None = None) -> str:
    """The command with quoting, escapes, comments, heredoc bodies and expansions resolved; separators are left for SPLIT."""
    budget = _budget if _budget is not None else [BUDGET]
    budget[0] -= len(command)
    if _depth > MAX_DEPTH or budget[0] < 0:
        return " ; " + _code(command) + " ; "
    text, _ = _Scan(command, _depth, budget).run(0, None)
    return expand_braces(NULL_REDIRECT.sub(" ", text))


def segments(command: str) -> list[str]:
    return [re.sub(r"\s+", " ", seg).strip() for seg in SPLIT.split(skeleton(command)) if seg.strip()]


SEG_TOKEN = re.compile(r"(\n|;|&&|\|\||(?<!>)\||(?<![<>])&(?!>)|\$\(|`|\(|\)|\{|\})")


def segments_cwd(command: str, cwd: str = "") -> list[tuple[str, tuple[str, ...]]]:
    """Like `segments`, with the directories the segment may run in: `cd`/`pushd`/`popd` are followed, a subshell keeps its own.

    A command that carries substitutions is ordered by the canonical text, not by execution, so every directory the
    command passes through counts for every segment then.
    """
    import os

    text = skeleton(command)
    cur, prev = cwd, None
    stack: list[str] = []
    dirs: list[str] = []
    ticks = False
    seen = [cwd]
    found: list[str] = []
    for k, piece in enumerate(SEG_TOKEN.split(text)):
        if k % 2:
            if piece in ("(", "$("):
                stack.append(cur)
            elif piece == ")" and stack:
                cur = stack.pop()
            elif piece == "`":
                if ticks and stack:
                    cur = stack.pop()
                elif not ticks:
                    stack.append(cur)
                ticks = not ticks
            continue
        seg = re.sub(r"\s+", " ", piece).strip()
        if not seg:
            continue
        found.append(seg)
        dirs.append(cur)
        words = PREFIX.sub("", seg, count=1).split(" ")
        if words[0] in ("cd", "pushd", "popd"):
            target = next((w for w in words[1:] if not w.startswith("-") or w == "-"), None) if words[0] != "popd" else None
            new = cur
            if words[0] == "popd":
                new = prev if prev else cur
            elif target is None:
                new = os.path.expanduser("~")
            elif target == "-":
                new = prev or cur
            elif not re.search(r"[*?\[`]", os.path.expandvars(target)) and "$" not in os.path.expandvars(target):
                t = os.path.expanduser(os.path.expandvars(_dec(target)))
                new = os.path.realpath((t if os.path.isabs(t) else os.path.join(cur or os.getcwd(), t)).replace("\x00", ""))
            if new != cur:
                prev, cur = cur, new
                seen.append(cur)
    every = tuple(dict.fromkeys(seen)) if "$X" in text else None
    return [(seg, every or (d,)) for seg, d in zip(found, dirs, strict=True)]


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
        if t == "rm" or t.endswith("/rm"):  # `rm -r -f`, `rm -fr`, `rm --recursive --force` are `rm -rf`
            j = i + 1
            while j < len(toks) and toks[j].startswith("-") and toks[j] != "--":
                j += 1
            flags = toks[i + 1:j]
            short = "".join(f[1:] for f in flags if not f.startswith("--"))
            if ("r" in short or "R" in short or "--recursive" in flags) and ("f" in short or "--force" in flags):
                out.extend(["rm", "-rf"])
                i = j
                continue
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
