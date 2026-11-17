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

import functools
import re
import shlex
from dataclasses import dataclass, field, replace

from . import blind, interp

MAX_DEPTH = 8
MAX_NEST = 60
BUDGET = 4_000_000     # characters the scanner may read across all nested levels; past it the rest is searched as plain text
HERE_ALLOWANCE = 6_000_000   # characters more that the here-strings of a chain may be read for: a text that the budget charged already comes back once per level
UNSAFE_MARK = "@unsafe!"   # prefix of the copy of a stage whose verb can write or run something, so rules see its resolved operands
UNSAFE_GIT = "git:unsafe-config "
TEXT_MARK = "@text!"   # prefix of a line the scanner extracted from a program (`import jade.cli`): words for the invariants to read, no operands to resolve

NULL_REDIRECT = re.compile(r"(\d*>&\d+|&>>?\s*/dev/null|\d*>>?\s*/dev/null|\d*<\s*/dev/null)")
# A redirect as the shell reads it, wherever it stands in the command: an optional descriptor that starts a word (`2>log`; in `a2>log` the 2 is part of `a2`), the
# operator (`&>`, `&>>`, `>>`, `>&`, `>|`, `>`, `<<<`, `<<HEREDOC` as the scanner spells a heredoc, `<&`, `<>`, `<`), and the target word, which may follow after blanks (`>&-main` closes a descriptor and then names the word `main`: a `-` right after `>&` or `<&` is the whole target).
REDIRECT = re.compile(r"(?P<fd>(?<!\S)\d+)?(?P<op><<HEREDOC|(?:&>>?|>>?&?)[|!]?|<<<|<&|<>|<)(?:(?<!HEREDOC)[ \t]*(?P<tgt>(?<=[<>]&)-|[^\s<>&|]*))?")   # `[|!]`: zsh `>|`, `>!`, `>>|`, `>&!`, `&>>|` ...
SPLIT = re.compile(r"\n|;|&&|\|\||(?<!>)(?<!>&)\||(?<![<>])&(?!>)|\$\(|`|\(|\)|\{|\}")  # `>&`, `>|`, `>&|` and `&>` are redirections, not separators
# Package runners that start the next word as a program: `uv run python -c ...` is `python -c ...` (its quoted code must be scanned, not read as prose).
_RUN_OPT = r"(?:(?:-w|-p|--with|--with-requirements|--with-editable|--python|--project|--directory|--package|--group|--env-file|--extra|--from|--index|--index-url)\s+\S+|--?[A-Za-z][\w-]*(?:=\S+)?)"
_RUNNER = (r"(?:uv(?:\s+tool)?\s+run|uvx|poetry\s+run|pdm\s+run|pipenv\s+run|rye\s+run|hatch\s+run|pipx\s+run|conda\s+run|npx|bunx|pnpm\s+(?:exec|dlx)"
           rf"|yarn\s+(?:exec|dlx)|npm\s+exec)(?:\s+(?:{_RUN_OPT}|--))*")
PREFIX = re.compile(
    r"^(?:(?:" + _RUNNER + r"|[A-Za-z_]\w*=\S*|sudo(?:\s+(?:-[ugphCDrtUT]\s+\S+|-[A-Za-z-]+))*|command(?!\s+-[A-Za-z]*[vV]\b)|builtin|nohup|time|exec|noglob|nocorrect|do|then|else|if|while|until|!"
    r"|env(?:\s+-[A-Za-z-]+)*|g?timeout(?:\s+-\S+)*\s+\d+[smhd]?|nice(?:\s+-n?\s*\d+)?|caffeinate(?:\s+-\w+)*"
    r"|xargs(?:\s+-(?:I\s*\S+|n\s*\d+|P\s*\d+|[0a-zA-Z]+))*)\s+)+")

ALIAS_TAIL = re.compile(r"\s-c\s+alias\.[^\s=]*=$")
GIT_VALUE = re.compile(r"(?:^|[\s(;&|])(?:\S*/)?git(?:\s+\S+)*?\s+-[Cc]\s+$|--(?:git-dir|work-tree|namespace)=$|--(?:git-dir|work-tree|namespace)\s+$")

SHELL_RE = re.compile(r"^(?:(?:ba|z|da|k|a|fi|c|tc)?sh|source|\.)$")
INTERP_RE = re.compile(r"^(?:python[\d.]*|pypy\d*|perl\d*|ruby\d*|node(?:js)?|php\d*|osascript|lua\d*|deno|bun|pwsh|powershell|swift|rscript|tclsh|julia|groovy)$")
STDIN_PATHS = {"/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
CODE_FLAG = re.compile(r"-[a-zA-Z]*[ceEr]|--eval|--print|--command")
AWK_NAMES = {"awk", "gawk", "mawk", "nawk"}
SHELL_NAME = re.compile(r"^(?:ba|z|da|k|a|fi|c|tc)?sh$")
PLAIN = re.compile(r"[^\\'\"$`<\n;(){}&|#]+")
_INERT = re.compile(r"[;&|<>(){}`]")   # characters a quoted word may hold that the splitter and the redirect check would read as syntax

HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|(\\?)([^\s;&|<>()'\"\\`$]+))")
HEREDOC_AT = re.compile(r"(?<!<)" + HEREDOC_OP.pattern + r"(?!<)")   # a heredoc operator, not the end of `<<<`
NEXT_STAGE = re.compile(
    r"[ \t]*(?:\w+=\S*[ \t]+)*(?:(?:sudo|env|command|exec|nohup|time|xargs)(?:[ \t]+-[ugphCDrtUT][ \t]+\S+|[ \t]+-\S+(?:[ \t]+\d+)?)*[ \t]+)*[\\'\"]?(?P<w>[\w./+-]+)['\"]?(?P<a>(?:[ \t]+(?!\d+[<>])[^\s;&|()<>`$]+)*)(?:[ \t]+\d+(?=[<>]))*[ \t]*(?=$|[;&|()\n<>])")
PIPE_REST = re.compile(r"\|(?!\|)")
PRODUCER = re.compile(r"^\s*(?:echo|printf|print|cat|yes)\b(?:\s+-[a-zA-Z]+)*")
PRINTER = re.compile(r"^\s*\\?(?:\S*/)?(?P<name>echo|printf|print|cat|yes)\b(?:\s+-[a-zA-Z]+)*")   # the program that prints, after `time`, `env`, `command`, `!`, `\echo`, `/bin/echo`
ORDINARY_ESCAPE = set("_./-:=@%+,^")


# ------------------------------------------------------------------ small helpers

def _unescape_dq(body: str) -> str:
    """The text a shell hands to `sh -c "..."`: only \\$ \\` \\" \\\\ and backslash-newline are escapes inside double quotes."""
    return re.sub(r'\\([$`"\\])|\\\r?\n', lambda m: m.group(1) or "", body)


_ECHO_ESCAPE = re.compile(r"\\(c.*|0[0-7]{1,3}|x[0-9A-Fa-f]{1,2}|[\\abefnrtv]|.)", re.S)


def _echo_escapes(text: str, drop_unknown: bool = False) -> str:
    """What `echo` of zsh and sh (and `printf`, `print`) prints for `text`: `\\\\` is a backslash, `\\n` a newline, `\\c` ends the output; any other escape stays as it is,
    or (`drop_unknown`, zsh `print`) loses its backslash."""
    def one(m: re.Match) -> str:
        e = m.group(1)
        if m.group(0)[1] == "c":
            return ""   # `\c` ends the output
        if len(e) > 1:
            return chr(int(e[1:], 16 if e[0] == "x" else 8))   # `\0nnn`, `\xhh`
        if e in "abefnrtv\\":
            return {"\\": "\\", "n": "\n"}.get(e, " " if e in "tvfr" else "")
        return e if drop_unknown else "\\" + e
    return _ECHO_ESCAPE.sub(one, text)


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
    return j + 1, _MARK_CHARS.sub("\x06", "".join(out))   # `$'\x0f'` is a word, not the mark of a brace (DF-6)


def _command_word(stage_text: str) -> tuple[str, list[str]]:
    bare = PREFIX.sub("", stage_text.lstrip(), count=1).strip()
    toks = bare.split()
    return (toks[0].rsplit("/", 1)[-1].lower(), toks[1:]) if toks else ("", [])


def _stdin_kind(word: str, args: list[str]) -> str | None:
    """'shell', or the language of an interpreter (`python`, `perl` ...), when this command would run text arriving on stdin as code, else None."""
    w = word.rsplit("/", 1)[-1].lower()
    if w in AWK_NAMES:   # `awk -f - <<EOF`, `awk -f /dev/stdin`: the program arrives on stdin
        return "awk" if any((a in ("-f", "--file") and k + 1 < len(args) and args[k + 1] in ("-", *STDIN_PATHS))
                            or a in ("--file=-", *(f"--file={p}" for p in STDIN_PATHS)) for k, a in enumerate(args)) else None
    kind = "shell" if SHELL_RE.match(w) else interp.language_of(w) if INTERP_RE.match(w) else None
    if kind is None:
        return None
    args = [a for a in args if not re.match(r"^(?:\d*[<>]|&>|<<)", a)]
    if kind == "shell" and any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) or a == "--noexec" for a in args):
        return None   # `sh -n` reads the text to check its syntax and runs none of it
    if any(a in ("-s", "-") or a in STDIN_PATHS for a in args):
        return kind
    skip = False
    for idx, a in enumerate(args):
        if skip:
            skip = False
        elif a in ("-o", "+o", "-W", "-X", "-O") or (a == "-l" and w == "osascript"):   # `osascript -l JavaScript` names the language, the script is on stdin
            skip = True
        elif CODE_FLAG.fullmatch(a):
            return kind if idx == len(args) - 1 or args[idx + 1:idx + 2] == ["{}"] else None  # `xargs sh -c`, `xargs -I{} sh -c {}`: the script arrives on stdin
        elif not a.startswith("-"):
            return None
    return kind


# Launchers that start the next word as a program and are not in NEXT_STAGE or PREFIX: `curl .. | doas sh`, `| busybox sh`, `| stdbuf -o0 bash`.
_STAGE_LAUNCHERS = frozenset("stdbuf doas busybox ionice setsid unbuffer chrt taskset".split())


def _peel_launchers(words: list[str]) -> list[str]:
    """`words` without the launchers in front of the program (`nice`, `timeout 5`, `uv run`, `doas -u x`, `su -c`): the program that reads the pipe is the first word left."""
    for _ in range(6):
        if not words:
            break
        peeled = PREFIX.sub("", " ".join(words), count=1).split()
        w = peeled[0].rsplit("/", 1)[-1].lower() if peeled else ""
        if w == "su":   # `su [user] -c CMD`: the command word follows -c
            peeled = peeled[peeled.index("-c") + 1:] if "-c" in peeled else []
        elif w in _STAGE_LAUNCHERS:
            peeled = peeled[1:]
            while peeled and peeled[0].startswith("-"):
                peeled = peeled[2:] if peeled[0] in ("-u", "-g", "-C", "-c", "-n", "-p") and len(peeled[0]) == 2 else peeled[1:]
        if peeled == words:
            break
        words = peeled
    return words


def _leaves_stdin(s: str, at: int) -> bool:
    """The `<` at s[at] redirects another descriptor (`sh 2<&1`) or copies stdin onto itself (`sh 0<&0`): the stage still reads the pipe."""
    m = re.search(r"(?<![^\s])(\d+)$", s[max(0, at - 6):at])
    return bool((m and m.group(1) != "0") or re.match(r"<&0(?!\d)", s[at:at + 4]))


def _next_stage_kind(s: str, j: int) -> str | None:
    """What the stage that starts at s[j] does with its stdin: the language it reads as code, or None. A stage whose stdin is redirected from a heredoc, a
    here-string or a file does not read the pipe at all (`curl .. | python3 - <<'PY'`)."""
    m = NEXT_STAGE.match(s, j)
    if not m:
        return None
    if s[m.end():m.end() + 1] == "<" and s[m.end() + 1:m.end() + 2] != "(" and not _leaves_stdin(s, m.end()):
        return None
    kind = _stdin_kind(m.group("w"), m.group("a").split())
    if kind:
        return kind
    words = _peel_launchers([m.group("w"), *m.group("a").split()])
    return _stdin_kind(words[0], words[1:]) if words else None


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


def _here_strings(raw: str) -> tuple[str, list[str]]:
    """`raw` without its here-strings, and their texts. A here-string is the word after an unquoted `<<<`, glued to it or not, and `<<<` may be glued to the program or
    to a descriptor before it: `cat <<< 'x'`, `cat<<<'x'`, `cat 0<<< 'x'`, `tee f<<<'x'`. A `<<<` inside quotes is text, and stays."""
    if "<<<" not in raw:
        return raw, []
    out: list[str] = []
    texts: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "\\":
            out.append(raw[i:i + 2])
            i += 2
        elif c == "'":
            j = raw.find("'", i + 1)
            j = n - 1 if j < 0 else j
            out.append(raw[i:j + 1])
            i = j + 1
        elif c == '"':
            j = min(_dq_end(raw, i + 1), n - 1)
            out.append(raw[i:j + 1])
            i = j + 1
        elif raw.startswith("<<<", i):
            k = i + 3
            while k < n and raw[k] in " \t":
                k += 1
            e = k
            while e < n and raw[e] not in " \t\r\n;&|()<>":
                if raw[e] == "\\":
                    e += 2
                elif raw[e] == "'":
                    e = raw.find("'", e + 1) + 1 or n
                elif raw[e] == '"':
                    e = _dq_end(raw, e + 1) + 1
                elif raw[e] == "$" and raw.startswith("$(", e):
                    e = _skip_span(raw, e + 1)
                else:
                    e += 1
            e = min(e, n)
            if words := words_of(raw[k:e]):
                texts.append(_dec(words[0]))
            d = len(out)
            while d and out[d - 1].isdigit():
                d -= 1
            if d < len(out) and (d == 0 or out[d - 1].isspace()):   # the descriptor of `0<<<`, not the digit that ends a word
                del out[d:]
            out.append(" ")
            i = e
        else:
            out.append(c)
            i += 1
    return "".join(out), texts


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


def awk_options_end(args: list[str]) -> int:
    """Index of the program word of an awk command line (the first operand that is not an option or the value of -F, -v, -W), else len(args)."""
    i = 0
    while i < len(args):
        if args[i] in ("-F", "-v", "-W"):
            i += 1
        elif not args[i].startswith("-"):
            return i
        i += 1
    return len(args)


def awk_program(args: list[str]) -> str | None:
    i = awk_options_end(args)
    return args[i] if i < len(args) else None


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
        prog = awk_program(args)
        unsafe = any(a in ("-f", "-i", "-E", "-l") or any(_long(a, n, 2) is not None for n in ("file", "include", "load", "exec")) for a in args[:awk_options_end(args)])
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
    def __init__(self, s: str, depth: int, budget: list[int], literal_end: int = 0):
        self.s, self.n, self.depth, self.budget = s, len(s), depth, budget
        self.nest = 0
        # Single quotes before this index are literal to the shell that runs the text: only the top-level text the outer shell parses. A body that is
        # re-parsed (`bash -c "..."`, `eval`, `| sh`, a here-string, a heredoc, a script the command writes) may hold text the outer shell already
        # expanded, so `'$X'` there stays a live expansion (security review PRE2-1).
        self.literal_end = literal_end if depth == 0 else 0
        self.legacy = len(budget) > 3 and budget[3]   # origin/main's reading of `"${NAME}"` (`$X`): the third reading of `_readings_top`

    def _sub(self, text: str, step: float = 1) -> str:
        """The skeleton of a nested text. A text that is read again at the same depth (the stages of a pipeline are produced once per `|`, a text is read for every
        reading of its caller) is read once: a chain of pipelines would otherwise read it as many times as it has pipes, per level."""
        seen = self.budget[2] if len(self.budget) > 2 else None
        key = (text, self.depth + step)
        if seen is not None and key in seen:
            return seen[key]
        out = skeleton(text, self.depth + step, self.budget)
        if seen is not None:
            seen[key] = out
        return out

    def _producer_program(self, raw: str) -> tuple[str, str] | None:
        """(language, program text) when a pipeline stage or substitution is `python3 -c '...'`, `perl -e`, `node -e`, `awk '...'`: a program whose
        output may be the script of a shell that reads it."""
        enc = " ".join(words_of(raw))
        rest = PREFIX.sub("", enc, count=1).split(" ")
        cmd = _dec(rest[0]).rsplit("/", 1)[-1].lower() if rest and rest[0] else ""
        if cmd in AWK_NAMES:
            prog = awk_program([_dec(a) for a in rest[1:]])
            return ("awk", prog) if prog is not None else None
        if INTERP_RE.match(cmd) and (prog := interp_code(rest[1:])) is not None:
            return interp.language_of(cmd) or "", prog
        return None

    def _produced(self, raw: str, kind: str) -> str:
        """What `echo ... | sh` hands to the reader: the echoed words, parsed as shell for a shell, searched as text otherwise. A program that prints
        (`python3 -c 'print("...")' | sh`) hands over every string it holds."""
        raw, here = _here_strings(raw)
        if kind == "shell" and (prog := self._producer_program(raw)):
            return self._interp(prog[0], prog[1], emit_all=True)
        read = (lambda t: self._sub(t)) if kind == "shell" else (lambda t: self._interp(kind, t))
        # what a stage reads on its own stdin, a here-string or a heredoc body, is what it prints (`cat <<< 'git push origin :main' | sh`); the redirect hoist takes the
        # first word of the here-string for a file, so the text is read here as it is
        # a here-string chain (`cat <<< 'cat <<< ... | sh' | sh`) reads one text per level, a part of the text that was read already, and it shrinks at each level:
        # a level costs a quarter of a nesting level, and its text is paid for from an allowance of its own (`HERE_ALLOWANCE`) when the budget is short. A heredoc body
        # is read as any text is (it comes twice when halving changes it, which a chain would square).
        def stdin_read(t: str) -> str:
            if kind != "shell":
                return read(t)
            if (short := 2 * len(t) - self.budget[0]) > 0 and len(self.budget) > 1:   # the text is charged twice below (the text, and the stage that holds it)
                grant = min(short, self.budget[1])
                self.budget[0] += grant
                self.budget[1] -= grant
            return self._sub(t, 0.25)

        stdin = " ; ".join([*(stdin_read(t) for t in dict.fromkeys(here) if t.strip()), *(read(t) for t in self._stdin_texts(raw))])
        printer = PRINTER.match(PREFIX.sub("", raw.lstrip(), count=1))
        bare = ""
        if printer is None:   # `\env echo ...`, `'command' echo ...`: the prefix words with their quotes taken off
            bare = PREFIX.sub("", " ".join(words_of(raw.lstrip())), count=1)
            printer = PRINTER.match(bare)
        if printer is None:
            text = " ".join(_dec(w) for w in words_of(PRODUCER.sub("", raw, count=1)))
            return " ; ".join(p for p in (read(text), stdin) if p)
        if bare:
            text = " ".join(_dec(w) for w in bare[printer.end():].split())
        else:
            text = " ".join(_dec(w) for w in words_of(PREFIX.sub("", raw.lstrip(), count=1)[printer.end():]))
        if kind != "shell":
            return " ; ".join(p for p in (self._interp(kind, text), stdin) if p)
        readings = [text]
        if "\\" in text and printer.group("name") in ("echo", "printf", "print"):
            # what the program prints is not what it is given: zsh and sh `echo` and `printf` read `\\` and `\n`, zsh `print` also drops the backslash of any
            # other escape (`\>` prints `>`), bash `echo` prints the text as it is. The shell that reads the output may be any of them: every reading is read.
            readings += [_echo_escapes(text), _echo_escapes(text, drop_unknown=True)]
        return " ; ".join([*(self._sub(t) for t in dict.fromkeys(readings)), *([stdin] if stdin else [])])

    @staticmethod
    def _stdin_texts(raw: str) -> list[str]:
        """The texts a stage is given on its stdin by a heredoc that the text of a substitution carries (`<(cat <<EOF ... EOF)`), as written and with the
        backslashes halved. (The here-strings come from `_here_strings`.)"""
        out: list[str] = []
        if "\n" in raw:
            pending: list[tuple[str, bool, list[str]]] = []
            for line in raw.split("\n"):
                if pending:
                    delim, dash, acc = pending[0]
                    if (line.lstrip("\t") if dash else line).rstrip("\r") == delim:
                        pending.pop(0)
                        text = "\n".join(acc)
                        out += [text, re.sub(r"\\([$`\\])|\\\r?\n", lambda x: x.group(1) or "", text)]
                    else:
                        acc.append(line)
                    continue
                for m in HEREDOC_AT.finditer(line):
                    delim = m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(5)
                    pending.append((delim, bool(m.group(1)), []))
            for _, _, acc in pending:   # a heredoc that is not closed runs to the end of the text
                out.append("\n".join(acc))
        return list(dict.fromkeys(t for t in out if t.strip()))

    def _interp(self, lang: str, body: str, emit_all: bool = False) -> str:
        """What a program in `lang` does (interp.py): its literal commands as nested shell text, its literal write targets as the destination of a
        `tee`, and the lines other rules read as written. Every other string in it is data and is dropped (`emit_all`: its output is a shell script)."""
        fx = interp.effects(lang, body, emit_all=emit_all)
        parts = [self._sub(c) for c in fx.commands]
        parts += [self._sub("tee " + shlex.quote(_enc(p))) for p in fx.writes]
        if fx.dynamic:   # an awk sink whose target the program text does not contain: the whole program is searched
            parts.append(_code(body))
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

    @staticmethod
    def _fetch_marks(code: str) -> list[str]:
        """The R-22 marker for text that a substitution fetches or decodes and a shell or interpreter then reads as its program."""
        kind = blind.subst_kind(code)
        return [blind.FETCH_MARK + kind] if kind in ("fetch", "decode") else []

    @staticmethod
    def _pipe_marks(stages: list[str]) -> list[str]:
        """Markers for a pipeline whose last stage reads its stdin as code (W-31): text fetched or decoded by an earlier stage is a program that is not on the
        command line (R-22), a file read by `cat` is one the gate cannot see (recorded, R-31)."""
        out: list[str] = []
        for raw in stages:
            word, args = _command_word(raw)
            if kind := blind.stage_kind(word, args):
                out.append(blind.FETCH_MARK + kind)
            elif word == "cat" and any(not a.startswith("-") for a in args):
                out.append(blind.BLIND_MARK + "script-file")
        return out

    def _script(self, code: str, deferred: list[str]) -> None:
        deferred.append(self._sub(code))
        if kind := blind.subst_kind(code):   # `eval "$(curl ..)"`, `sh -c "$(cat f)"`, `eval "$CMD"` (W-31)
            deferred.append(blind.FETCH_MARK + kind if kind in ("fetch", "decode") else blind.BLIND_MARK + ("eval-file" if kind == "file" else "eval-var"))
        m = PRODUCER_SUBST.fullmatch(code)
        inner = (m.group(1) or m.group(2) or "") if m else ""
        if PRODUCER.match(inner) or self._producer_program(inner):  # `eval "$(echo git push)"`: the output of an echo or a program is the script
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
                plain.append("$" + inner if not self.legacy and re.fullmatch(r"[A-Za-z_]\w*", inner) else "$X")  # `"${HOME}"` reads as $HOME, as it does unquoted
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
        braced = False   # a `{` or `}` was met: the text has several readings
        escaped = False  # an escaped operator was met: the text has two more
        c_blank = True   # reading C: nothing but blanks since the last separator of that reading
        c_open = 0       # reading C: the groups it has opened and not closed
        deferred: list[str] = []
        pending: list[tuple[str, bool, bool, str | None, bool]] = []
        cur = ""  # the current stage so far: its first and last characters are all the checks below look at
        chain: list[str] = []  # the earlier stages of the pipeline being read: any of them may feed text to a shell at its end
        fed: dict[str, tuple[int, int]] = {}   # per reader kind, how many stages of `chain` (and of `whole`) were handed to such a reader already: a reader further down reads no text that one before it did not
        raw_at = i
        whole_at = i                 # where the stage starts when a brace inside a word does not cut it (a word in every shell: `echo git push {x | sh`)
        whole: list[str] = []        # the earlier stages of the pipeline in that reading
        stop = n
        hs_kind: str | None = None
        paren = 0

        def emit(x: str) -> None:
            nonlocal cur, c_blank
            out.append(x)
            if c_blank and not x.isspace():
                c_blank = False
            cur += x
            if len(cur) > 700:
                cur = cur[:300] + cur[-300:]

        def end_stage(sep_at: int, resume: int, pipe: bool, brace: bool = False, word: bool = False) -> None:
            nonlocal cur, raw_at, hs_kind, c_blank, whole_at
            c_blank = c_blank or not brace
            raw = s[raw_at:sep_at]
            if raw.strip():
                self._stage(raw, deferred)
            if pipe:
                chain.append(raw)
                whole.append(s[whole_at:sep_at])
                if kind := _next_stage_kind(s, resume):
                    done, done_whole = fed.get(kind, (0, 0))   # `a | sh | sh | ...` hands each stage to a shell once, not once per shell
                    fed[kind] = (len(chain), len(whole))
                    deferred.extend(self._produced(r, kind) for r in chain[done:])
                    # the stages as the shell that takes a brace for a word reads them: `echo git push origin main {x | sh` pipes the push to the shell
                    deferred.extend(self._produced(r, kind) for r in dict.fromkeys(whole[done_whole:]) if r not in chain)
                    deferred.extend(self._pipe_marks(chain[done:]))
            else:
                chain.clear()
                fed.clear()
                if not word:
                    whole.clear()
            if not word:
                whole_at = resume
            cur, raw_at, hs_kind = "", resume, None

        def literal(plain: str, raw: str, quote: str) -> None:
            nonlocal hs_kind
            body = raw if quote == "'" else _unescape_dq(raw)
            tail = cur.rstrip()
            if hs_kind:
                kind, hs_kind = hs_kind, None
                deferred.append(self._sub(body) if kind == "shell" else self._interp(kind, body))
                deferred.extend(self._fetch_marks(body))   # `sh <<< "$(curl ..)"`: the here-string is fetched text that runs
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
                inert = _INERT.sub("\x06", plain)   # `awk 'NR>1'`: a quoted `>` is data, not a redirect
                literal_sq = quote == "'" and i <= self.literal_end   # `'$HOME'` at the top level is literal: it never expands
                emit('""' if re.search(r"\s", plain) else inert.replace("$", "\x06") if literal_sq else inert)

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
                    if d.isalnum() or d in ORDINARY_ESCAPE:
                        emit(d)
                    elif _INERT.fullmatch(d):
                        emit(ESCAPED + d)   # `a\&>log`: a word `a&` and a `>` in one reading, a backslash and an operator in the other (`_readings`)
                        escaped = True
                    else:
                        emit("\\" + d)
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
                    start = i + 2
                    txt, i = self.run(start, ")")
                    deferred.append(txt)
                    if hs_kind:   # `sh <<< $(curl ..)`
                        deferred.extend(self._fetch_marks("$(" + s[start:i - 1] + ")"))
                        hs_kind = None
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
                    deferred.extend(self._pipe_marks(re.split(r"\||;|&&", s[start:i - 1])))
                emit("/dev/fd/X")
            elif c == "<" and s.startswith("<<<", i):
                hs_kind = _stdin_kind(*_command_word(cur))
                emit("<<<")
                i += 3
            elif c == "<" and s.startswith("<<", i) and (m := HEREDOC_OP.match(s, i)):
                quoted = m.group(2) is not None or m.group(3) is not None or bool(m.group(4))
                delim = m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(5)
                own = _stdin_kind(*_command_word(cur))
                after = _rest_of_line_pipes_to_code(s, m.end())
                kind = own or after
                pending.append((delim, quoted, bool(m.group(1)), kind, own not in (None, "shell") and after == "shell"))   # last: a program whose output is piped to a shell
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
            elif c in "{}":
                # A group brace in one shell and a plain word in the other (`{git push origin main}`, `git push origin a{ :main`): there is no telling which one the shell
                # that runs the text will take, so a mark stands for the brace and `_brace_readings` writes the text in every reading. The mark says whether the brace
                # is a word of its own and whether reading C cuts there: a `{` where a command starts, and the `}` that closes it.
                alone = (i == 0 or s[i - 1] in " \t\n;&|()") and (i + 1 >= n or s[i + 1] in " \t\n;&|()")
                closes = c_open > 0 and (i + 1 >= n or s[i + 1] in " \t\n;&|()<>") and s[i - 1] != "{"
                cut = c_blank or (c == "}" and closes)
                # A `{` that stands alone where a command starts, and the `}` that stands alone where a command starts and closes one, are the group in bash and in zsh:
                # the text after them is another command, no reading puts a word of a command on the other side of them. They are written as they are, and a script of
                # such groups is read once, not five times (B3-5).
                clear = alone and c_blank and (c == "{" or c_open > 0)
                c_open += cut if c == "{" else -(c_open > 0 and cut)
                if clear:
                    out.append(c)
                else:
                    out.append(BRACE_MARK[c == "}", alone, cut])
                    braced = True
                cur += c
                end_stage(i, i + 1, False, brace=True, word=not clear)
                i += 1
            elif c in ";()":
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
                if i and (s[i - 1] == ">" or s[i - 2:i] == ">&"):   # `>|f`, and zsh `>&|f`, `>>&|f`
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
        text = _readings("".join(out), braced, escaped)
        return text + "".join(f" ; {d} ; " for d in dict.fromkeys(deferred)), i   # a text produced for several pipes is written once

    def _heredoc_bodies(self, i: int, pending: list, deferred: list[str]) -> int:
        s, n = self.s, self.n
        for delim, quoted, tabs, kind, prints_to_shell in pending:
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
                halved = re.sub(r"\\([$`\\])|\\\r?\n", lambda m: m.group(1) or "", body)   # the program reads what the shell wrote: `\\` is `\`, `\$` is `$`
            else:
                halved = body
            for text in dict.fromkeys([body, halved]):   # an expansion in the body is replaced before the program reads it (`\\$(true) x`): both texts are read
                if kind == "shell":
                    deferred.append(self._sub(text))
                elif kind:
                    deferred.append(self._interp(kind, text, emit_all=prints_to_shell))
        return i


@functools.lru_cache(maxsize=16)
def _skeleton_top(command: str, literal_end: int, legacy: bool = False) -> str:
    return _skeleton(command, 0, None, literal_end, legacy)


def skeleton(command: str, _depth: int = 0, _budget: list[int] | None = None, literal_end: int | None = None) -> str:
    """The command with quoting, escapes, comments, heredoc bodies and expansions resolved; separators are left for SPLIT.

    `literal_end`: single quotes before this index are literal (default: the whole text). A caller that appends text the command runs without writing
    it on the command line (a script it writes, an alias expansion) passes the length of the command, so the appended text keeps `'$X'` live.

    The top-level call is pure on a string, and one assessment asks for the same text several times (views, segments, masks),
    so it is memoised: a 2 MB command is scanned once, not once per caller."""
    if _depth == 0 and _budget is None:
        return _skeleton_top(command, len(command) if literal_end is None else literal_end)
    return _skeleton(command, _depth, _budget)


def _skeleton(command: str, _depth: int, _budget: list[int] | None, literal_end: int = 0, legacy: bool = False) -> str:
    # `legacy` rides in the budget, which every nested scan shares: origin/main's reading of a quoted `${NAME}` (`$X`, see `_Scan`) at every depth
    budget = _budget if _budget is not None else [BUDGET, HERE_ALLOWANCE, {}, legacy]
    budget[0] -= len(command)
    command = _MARK_CHARS.sub("\x06", command)   # a byte that is one of the marks stays a word
    if _depth > MAX_DEPTH or budget[0] < 0:
        return " ; " + _code(command) + " ; "
    text, _ = _Scan(command, _depth, budget, literal_end).run(0, None)
    return _hoist_redirects(expand_braces(NULL_REDIRECT.sub(" ", text)))


# A brace is a command group in one shell and a plain word in the other, and where a command starts or a word is alone the same text reads differently in zsh and bash.
# The scanner writes a mark for each brace (its kind, whether it is a word of its own, whether reading C cuts there) and the skeleton holds every reading:
#   A every brace cuts the command (groups), L every brace is a word, C a `{` cuts where a command starts and its `}` cuts too, D a brace that is a word of its own cuts and one inside a
#   word does not, E the other way round. Whichever shell runs the text, one of the readings is the one it takes, and a reading that cuts a push short is not the only one.
BRACE_MARK = {(close, alone, cut_c): chr(0x0E + 4 * close + 2 * alone + cut_c) for close in (False, True) for alone in (False, True) for cut_c in (False, True)}
_MARKS = {m: k for k, m in BRACE_MARK.items()}
ESCAPED = "\x16"   # in front of an operator the command escaped with a backslash
_MARK_CHARS = re.compile("[\x0e-\x16]")


def _reading_tables() -> list[dict[int, str]]:
    tables = []
    for name in "ALCDE":
        table = {}
        for m, (close, alone, cut_c) in _MARKS.items():
            cut = {"A": True, "L": False, "C": cut_c, "D": alone, "E": not alone}[name]
            table[ord(m)] = ("}" if close else "{") if cut else "\x06"
        tables.append(table)
    return tables


_READING_TABLES = _reading_tables()
ESCAPED_OPERATOR = re.compile(ESCAPED + ".")


def _readings(text: str, braced: bool, escaped: bool) -> str:
    """The text in every reading the shells allow: the braces (`_brace_readings`), and an operator the command escaped as a word and as syntax.

    `a\\>&x :main` is the word `a>` and the command `x :main` in a shell that reads the backslash as an escape, and `a\\`, a redirect and `:main` in one that reads
    a `\\` and the operator after it (the text a program prints and another shell reads, `echo 'a\\>x' | zsh -f`, is `a>x`). Main kept the backslash; one reading is data.

    A brace or an escape is read in its own chunk of the text (`_chunks`): the other lines of a script do not come in the readings of a line, so a long script
    with a few brace words costs a few more lines, not a few more scripts."""
    if not (braced or escaped):
        return text
    return "".join(_chunk_readings(c, braced, escaped) for c in _chunks(text))


def _chunk_readings(text: str, braced: bool, escaped: bool) -> str:
    if not _MARK_CHARS.search(text):
        return text
    tail = text[len(text.rstrip("\n")):]
    text = text[:len(text) - len(tail)]
    out = [text]
    if braced:
        out = list(dict.fromkeys(text.translate(table) for table in _READING_TABLES))
    if escaped:
        # the reading that is the one of main (every brace cuts, the backslash stays) is kept with every other reading of the braces; the escaped operators are read as data
        # in each of them, and as syntax in the first: a brace and an escaped operator in one command do not square the readings
        live = [out[0].replace(ESCAPED, "\\")]
        out = list(dict.fromkeys([*(ESCAPED_OPERATOR.sub("\x06", t) for t in out), *live]))
    first = _collapse_runs(out[0]) if len(out[0]) > DENSE else out[0]
    if len(out) == 1:
        return first + tail
    extra = list(map(_collapse_runs, out[1:]))
    if MOVES_DIRECTORY.search(text):
        # a reading that moves the directory must not move it again for the lines that follow, and each reading must start where the chunk starts: the other
        # readings are subshells, written first, and the first reading is the one that stays (`cd sub && echo a{b` then `git push` runs in sub)
        return " ; ".join([*map(_subshell, extra), first]) + tail
    return " ; ".join([first, *extra]) + tail


_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def _subshell(text: str) -> str:
    """The text as a subshell that holds it whole, whatever its parentheses do: a reading with a `)` that closes nothing (`echo a\\)b`, `case x in y)`) or a `(` that
    stays open gets the opens or closes it lacks, so the directory it moves is restored for the lines that follow, as for a balanced one."""
    depth = low = 0
    for ch in _QUOTED.sub("", text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            low = min(low, depth)
    return "( " * (1 - low) + text + " )" * (1 + depth - low)


DENSE = 600   # a chunk longer than this has the runs of one word in its first reading cut too


def _collapse_runs(text: str) -> str:
    """Four repeats of a word (or of up to eight words) say all that a long run of them does: the same segments are read again and again. A run that moves the
    directory, or counts (`cd`, `pushd`, `popd`, `-C ..`, `--git-dir`, `--work-tree`, `..`: each repeat goes one more level), is kept."""
    return RUN_OF_WORDS.sub(lambda m: m.group(0) if COUNTED.search(m.group(1)) else m.group(1) * 4, text)


MOVES_DIRECTORY = re.compile(r"(?<![^\s;&|(){}])(?:cd|pushd|popd)(?![^\s;&|(){}])")
COUNTED = re.compile(MOVES_DIRECTORY.pattern + r"|(?<![^\s;&|(){}])(?:-C|--git-dir(?:=\S*)?|--work-tree(?:=\S*)?)(?![^\s;&|(){}])|\.\.")


def _chunks(text: str) -> list[str]:
    """The lines of the text, cut where no group is open (a `(` or a backtick that is not closed) and the line is not continued (`|`, `&&`, `||` at its end):
    a reading of a chunk is written in place, so a chunk is a run of whole commands."""
    if "\n" not in text:
        return [text]
    out, cur, depth = [], [], 0
    for line in text.split("\n"):
        cur.append(line)
        depth += line.count("(") - line.count(")") + line.count("`")
        if depth <= 0 and not line.rstrip().endswith(("|", "&&", "||", "\\")):
            out.append("\n".join(cur) + "\n")
            cur, depth = [], 0
    if cur:
        out.append("\n".join(cur))
    else:
        out[-1] = out[-1][:-1]
    return out


RUN_OF_WORDS = re.compile(r"(?<!\S)((?:\S{1,40}[ \t]+){1,8}?)\1{4,}")   # a long run of one word, or of up to eight words, in a reading (`\x06 \x06 ...`, `{ a\> { a\> ...`): four of them say all that the rest do


def _redirect_word(m: re.Match) -> str:
    """One redirect in its one spelling: `&>f`, `>&f` and `>|f` are `> f`, `2>f` is `2> f`; a descriptor close or move (`>&-`, `2>&1-`) names no file and stays as written, a copy (`<&0`) goes."""
    fd, op, tgt = m.group("fd") or "", m.group("op"), m.group("tgt") or ""
    if op == "<<HEREDOC":
        return op
    if op in (">&", "<&") and re.fullmatch(r"\d+-?|-", tgt):
        # a descriptor move or close names no file. A close stays as written, as on main: `cat key 2>&-` is rated as it was, not as a plain read. A copy goes (`<&0`)
        return fd + op + tgt if tgt.endswith("-") else ""
    both = op.startswith("&")
    if op[0] in "&>":
        op = ">>" if ">>" in op else ">"   # `&>`, `>&`, `>|`, `>!`, `>>&|`, `&>>!` ...: a write, or an append, of a file
    elif op == "<&":
        op = "<"
    if op in (">", ">>", "<") and tgt:
        return f"{'' if both else fd}{op} {tgt}"   # the target is the word after the operator, as `operands` and `_absolute_operands` read it
    return fd + op + tgt


def _hoist_redirects(text: str) -> str:
    """Each simple command with its redirects taken out of the words and written after them, so that the words are what a rule reads.

    The shell reads `git push origin feature &>log :main` as the push `git push origin feature :main` with its output sent to `log`: a redirect and its target
    are not arguments, and they may stand anywhere, even in front of the program (`>log git push`). The rules look for words up to the next `&`, `|` or `;`, and
    `&>`, `>&`, `>|` hold such a character, so left in place the redirect cut every rule short. A redirect keeps its target (a write is still a write;
    `cd /etc && echo x >pf.conf` writes `/etc/pf.conf`) and moves to the end of its command, spelled `> file`; a descriptor close (`>&-`) names no file and moves as written."""
    if not REDIRECT.search(text):
        return text
    out: list[str] = []
    for k, piece in enumerate(SEG_TOKEN.split(text)):
        if k % 2 or not REDIRECT.search(piece) or piece.lstrip().startswith(TEXT_MARK):
            out.append(piece)
            continue
        out.append(_hoist_piece(piece))
        for other in _other_readings(piece):
            out.append(" ; " + _hoist_piece(other))
    return "".join(out)


GLUED_CLOSE = re.compile(r"(?P<fd>(?<!\S)\d+)?(?P<c>[<>])&-(?=[^\s<>&|])")
BANG_REDIRECT = re.compile(r"(?P<op>&>>?|>>?&?)!")


def _other_readings(piece: str) -> list[str]:
    """The pieces in which the shell that is not the one the first reading follows reads a redirect differently.

    `>&-lg`: bash closes the descriptor and `lg` is an argument, zsh writes a file `-lg`. `>! lg`, `>>! lg`, `>&! lg`: zsh writes the file `lg`, bash writes a file
    named `!` and `lg` is an argument. A gate that kept one reading would lose a word of the command, so every reading that differs is read."""
    out: list[str] = []
    if GLUED_CLOSE.search(piece):
        out.append(GLUED_CLOSE.sub(lambda m: (m.group("fd") or "") + m.group("c") + " -", piece))
    if BANG_REDIRECT.search(piece):
        out.extend(BANG_REDIRECT.sub(lambda m: m.group("op") + " !", x) for x in [piece, *out])
    return out


def _hoist_piece(piece: str) -> str:
    moved = [w for m in REDIRECT.finditer(piece) if (w := _redirect_word(m))]
    words = REDIRECT.sub(" ", piece).strip()
    # the blank in front of the piece stays: `main& >log` must not become `main&> log`, which is another operator
    return piece[:len(piece) - len(piece.lstrip())] + " ".join([words, *moved]).strip() + piece[len(piece.rstrip()):] if words or moved else piece


# `skeleton_both` is what every caller that classifies a command reads. The current reading is joined to the reading origin/main (8100ea1) gave the same text,
# kept as it was in `shellnorm_base`: each part is a command line of its own (a `cd` of one does not move the other), so no command is read as less than main read it,
# whatever the current reading drops, and the rating and the push check are the stricter of the two by construction (PR 47 review, round 6).
BIG_SEGMENT = 20_000   # characters
JOIN = "\x17"
_JOIN_CHAR = re.compile(JOIN)


_QUOTED_VAR = re.compile(r"'[^']*\$|\"[^\"]*\$\{")   # the two places the current reading differs from main's: a single-quoted `$X`, a double-quoted `${NAME}`


@functools.lru_cache(maxsize=16)
def _readings_top(command: str, literal_end: int) -> tuple[tuple[str, bool], ...]:
    """The readings of a command, each as (text, is_current): the current reading; the current reading with a single-quoted `$X` and a double-quoted `${NAME}` read as main read
    them (`literal_end` 0, `legacy`), when the command has one and the two differ; and origin/main's own reading (cut as main cut it). Every one is a command line of its own, so the rating and the push check are the stricter
    of all of them: a literal `'$B'` that is one reading's branch name stays the other readings' unknown branch (PR 46 merge review, ADV-1 and ADV-2)."""
    command = _JOIN_CHAR.sub("\x06", command)   # a byte that is the join stays a word
    out = [(_skeleton_top(command, literal_end), True)]
    if _QUOTED_VAR.search(command):
        loose = _skeleton_top(command, 0, True)
        if loose != out[0][0]:
            out.append((loose, True))
    floor = _base_skeleton(command)
    if all(floor != text for text, _ in out):
        out.append((floor, False))
    return tuple(out)


def skeleton_both(command: str, literal_end: int | None = None) -> str:
    """The text of `skeleton` and, after `JOIN`, the other readings when they differ (see `_readings_top`). `segments`, `segments_cwd` and `text_views` read it.
    `literal_end`: as in `skeleton` (the current reading; the second reading is main's, where a single-quoted `$X` is not literal)."""
    return JOIN.join(text for text, _ in _readings_top(command, len(command) if literal_end is None else literal_end))


# The one word the current `PREFIX` strips and origin/main's did not: main read `nocorrect echo git push origin main | sh <<< x` as a command named `nocorrect` and rated its
# words, and the classifier that reads the joined text strips it. The floor keeps main's reading of it (an unknown first word), so the rating is main's too.
_NOCORRECT = re.compile(r"(?<![\w./$-])nocorrect(?![\w./-])")


def _base_skeleton(command: str) -> str:
    from . import shellnorm_base

    # origin/main's own reading with its own budget (`BUDGET`, 4 000 000 characters): never cut earlier than it cut itself, so no command is read as less than main read it.
    # Its cost is main's cost too (a chain of pipelines nested six deep took 24 s there).
    return _NOCORRECT.sub("nocorrect\x06", shellnorm_base.skeleton(command))


def _parts(command: str, literal_end: int | None = None) -> list[tuple[str, re.Pattern, re.Pattern]]:
    """The readings of a command with the separators each is cut by: origin/main's is cut as origin/main cut it (`git push >&|log origin feature` is `git push >&` piped to `log` there)."""
    return [(text, SPLIT, SEG_TOKEN) if current else (text, _base().SPLIT, _base().SEG_TOKEN)
            for text, current in _readings_top(command, len(command) if literal_end is None else literal_end)]


def _base():
    from . import shellnorm_base

    return shellnorm_base


def segments(command: str) -> list[str]:
    return [re.sub(r"\s+", " ", seg).strip() for text, split, _ in _parts(command) for seg in split.split(text) if seg.strip()]


SEG_TOKEN = re.compile(r"(\n|;|&&|\|\||(?<!>)(?<!>&)\||(?<![<>])&(?!>)|\$\(|`|\(|\)|\{|\})")


def segments_cwd(command: str, cwd: str = "", literal_end: int | None = None) -> list[tuple[str, tuple[str, ...]]]:
    """Like `segments`, with the directories the segment may run in: `cd`/`pushd`/`popd` are followed, a subshell keeps its own.
    `literal_end`: as in `skeleton`.

    A command that carries substitutions is ordered by the canonical text, not by execution, so every directory the
    command passes through counts for every segment then.
    """
    rows: list[tuple[str, tuple[str, ...]]] = []
    big: set[tuple[str, tuple[str, ...]]] = set()
    for text, _, token in _parts(command, literal_end):
        for row in _segments_cwd_of(text, cwd, token):
            if len(row[0]) > BIG_SEGMENT:   # the plain text a scanner leaves when it stops reading comes out of both readings: a segment of this size is classified once (the cost of reading it twice is the cost of the command twice)
                if row in big:
                    continue
                big.add(row)
            rows.append(row)
    return rows


def _segments_cwd_of(text: str, cwd: str, token: re.Pattern) -> list[tuple[str, tuple[str, ...]]]:
    import os

    cur, prev = cwd, None
    real: dict[str, str] = {}
    resolved: set[str] = set()
    stack: list[str] = []
    dirs: list[str] = []
    ticks = False
    seen = [cwd]
    found: list[str] = []
    for k, piece in enumerate(token.split(text)):
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
                where = (t if os.path.isabs(t) else os.path.join(cur or os.getcwd(), t)).replace("\x00", "")
                if where in real:
                    new = real[where]   # the same `cd` line repeated is resolved once
                elif cur in resolved and "/" not in t and t not in (".", "..") and not os.path.islink(where):
                    new = real[where] = where   # one more plain component under a directory that is resolved already: no link to follow (the realpath of a deep path is quadratic)
                else:
                    new = real[where] = os.path.realpath(where)
                resolved.add(new)
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


_CONFIG_READS = ("--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--list", "-l", "--show-origin", "--show-scope",
                 "--name-only", "--unset", "--unset-all", "--remove-section", "--rename-section", "--edit", "-e", "--help")
_CONFIG_READ_SET = frozenset(_CONFIG_READS)
_CONFIG_VALUED = {"--file", "-f", "--blob", "--type", "--default", "--comment"}


def config_write_key(args: list[str]) -> str | None:
    """The key a `git config` command sets (`git config alias.p push`, `git config set k v`, `--add`, `--replace-all`), or None for a read, a removal and a
    single operand (`git config alias.p` prints it)."""
    a = list(args)
    if a[:1] and a[0] in ("get", "list", "unset", "edit", "rename-section", "remove-section"):
        return None
    if a[:1] == ["set"]:
        a = a[1:]
    pos: list[str] = []
    i = 0
    while i < len(a):
        x = a[i]
        if x in _CONFIG_VALUED:
            i += 2
            continue
        if x.partition("=")[0] in _CONFIG_READ_SET:   # `--get` and `--get=x`: no read flag holds an `=`, so this is `x == r or x.startswith(r + "=")` for each of them
            return None
        if x.startswith("-") and x != "-":
            i += 1
            continue
        pos.append(x)
        i += 1
    return pos[0] if len(pos) >= 2 else None


@dataclass
class GitCall:
    """One `git [global options] <sub> <args>` found in a segment."""

    sub: str = ""
    args: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    git_dir: str | None = None
    work_tree: str | None = None
    config_work_tree: str | None = None   # `-c core.worktree=D`: the work tree when no --work-tree is given
    danger: list[str] = field(default_factory=list)


_REDIRECT_ARG = re.compile(r"\d*(?:>>?|<>|<)")   # the operator of a moved redirect, with its descriptor; its target is the next word
_REDIRECT_GLUED = re.compile(r"\d*(?:<>|>&|<&)\S+")   # an operator the skeleton moved with its target glued to it (`<>log`, `>&-`, `2>&-`)


def _redirect_kind(word: str) -> int:
    """0: an ordinary word. 1: the operator of a moved redirect (its target is the next word). 2: an operator with its target glued to it."""
    if "<" not in word and ">" not in word:   # almost every word: no regex
        return 0
    if _REDIRECT_ARG.fullmatch(word):
        return 1
    return 2 if _REDIRECT_GLUED.fullmatch(word) else 0


def _without_redirects(args: list[str], kinds: list[int] | None = None) -> list[str]:
    """The arguments of a command without the redirects the skeleton moved to its end (`> log`, `>> log`, `<> log`, `>&-`, `< in`): the shell takes a redirect and its
    target out of the words, so `git push > log` is a push with no destination, not a push to the remote `>`. A quoted `>` is data (the skeleton marks it), so a bare
    one is an operator. A target that the skeleton split at an escaped space (`> log\\ x` becomes `> log\\` and `x`) is dropped whole after an operator that is a word of its
    own (`>`, `>>`, `2>`, `<`, `<>`), when the backslashes at its end are odd in number (`> log\\\\` ends in an escaped backslash: the target is complete). That split is made
    by the skeleton of origin/main only (the current skeleton moves the target to the end of the command and leaves `x` behind), so the words of the current reading keep `x`
    as a word; the other reading drops it. A glued target (`>log\\ x`) is not dropped whole. A target that the shell expands to several words in zsh (`> {a,b}`) is not handled.
    `git_calls` keeps the arguments as they stood as well, so a reading that was stricter before is still read. `kinds` is `_redirect_kind` of each word, when the
    caller has it already."""
    if kinds is None:
        kinds = [_redirect_kind(a) for a in args]
    if not any(kinds):
        return args
    out: list[str] = []
    skip = False
    for a, k in zip(args, kinds, strict=True):
        if skip:
            skip = (len(a) - len(a.rstrip("\\"))) % 2 == 1
        elif k == 1:
            skip = True
        elif k == 0:
            out.append(a)
    return out


def _config_danger(call: GitCall) -> None:
    """`git config <key> <value>` that sets an alias, pager, sshCommand ... (a program) is a danger of the call: the persistent form of `git -c key=value` (W-31)."""
    if call.sub == "config" and (key := config_write_key(call.args)) is not None and ("$" in key or DANGEROUS_KEY.search(key.lower())):
        call.danger.append(f"config {key}")


def _redirect_free_words(toks: list[str]):
    """One pass over the words of a text for `git_calls`: the words that are not empty, the number of them before each position of `toks`, the words left when
    the redirects are dropped (`_without_redirects` run once from the start), how many of those come before each word, and whether the pass is inside the target of
    a redirect before each word. From a word that is not inside a target, the words that are left are the ones `_without_redirects` leaves of the words from there on."""
    words = [t for t in toks if t]
    before = [0] * (len(toks) + 2)
    n = 0
    for k, t in enumerate(toks):
        before[k] = n
        n += bool(t)
    before[len(toks)] = before[len(toks) + 1] = n
    kept: list[str] = []
    kept_before = [0] * (len(words) + 1)
    skip_before = [False] * (len(words) + 1)
    skip = False
    for q, w in enumerate(words):
        kept_before[q] = len(kept)
        skip_before[q] = skip
        if skip:
            skip = (len(w) - len(w.rstrip("\\"))) % 2 == 1
        else:
            kind = _redirect_kind(w)
            if kind == 1:
                skip = True
            elif kind == 0:
                kept.append(w)
    kept_before[len(words)] = len(kept)
    skip_before[len(words)] = skip
    return words, before, kept, kept_before, skip_before


def git_calls(text: str) -> tuple[str, list[GitCall]]:
    """Rewrite every `git <global options> sub` to `git sub` and describe each invocation. Pure text, no process."""
    toks = text.split(" ")
    out: list[str] = []
    calls: list[GitCall] = []
    words: list[str] | None = None
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
                    if name == "-c" and key == "core.worktree" and "=" in val:
                        call.config_work_tree = val.split("=", 1)[1]
                    if not key or "$" in key or '"' in key or DANGEROUS_KEY.search(key):
                        call.danger.append(f"{name} {key or '?'}")
            elif name == "--exec-path":
                call.danger.append("--exec-path")
                j += 1
            else:
                break
        call.sub = toks[j] if j < len(toks) else ""
        if words is None:   # once per text: the words, and the words without the redirects, so that no git word reads the rest of the text again
            words, before, kept, kept_before, skip_before = _redirect_free_words(toks)
        p = before[j + 1]   # the words from the one after the subcommand on are words[p:]
        raw = words[p:]
        call.args = raw
        if skip_before[p]:   # the subcommand stands in the target of a redirect (`> git`): read this one from its own start
            call.args = _without_redirects(raw)
        elif len(kept) - kept_before[p] != len(raw):
            call.args = kept[kept_before[p]:]
        _config_danger(call)
        calls.append(call)
        if call.args != raw:   # the words as origin/main took them (a redirect target taken for a destination) are read as well: never less than origin/main read
            asis = replace(call, args=raw, dirs=list(call.dirs), danger=[d for d in call.danger if not d.startswith("config ")])
            _config_danger(asis)   # its danger is worked out from its own words, not from the reduced ones
            calls.append(asis)
        out.append("git")
        i = j
    return " ".join(out), calls
