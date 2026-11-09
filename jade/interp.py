"""What a program in a non-shell language DOES, read from its source without running it (threat T18, task G).

The gate used to search the text of `python3 - <<EOF`, `python -c`, `perl -e`, `node -e` ... for command words, so a script that only
mentions `systemctl start`, a firewall or `git -c` in a docstring, a comment or a message was rated like the command it names. A program
is not a shell command line. Only two things in it matter to the gate, and this module extracts exactly those:

* the command lines it hands to a shell or a program (`subprocess.run([...])`, `os.system("...")`, `system`, `exec`, backticks, `qx`,
  `child_process`, `proc_open`, `do shell script` ...), returned as shell text for the caller to classify like any nested command;
* the paths it writes, deletes, renames or creates (`open(p, "w")`, `Path.write_text`, `fs.writeFileSync`, `file_put_contents` ...), which the
  caller classifies as the targets of a write.

Every other string is data and is ignored. Python is parsed with `ast` (string constants are folded through simple assignments, f-strings,
`%`, `.format`, `os.path.join` and `Path /`, an unknown part becomes `$X`); the other languages are read with literal-argument patterns. A
command or path built at run time from input the source does not contain stays invisible: the honest limit of docs/05 T18. Nothing here
executes or imports the code. The shell interpreters (sh, bash, zsh ...) are not handled here: their bodies stay fully scanned.
"""

from __future__ import annotations

import ast
import re
import shlex
import textwrap
import warnings
from dataclasses import dataclass, field

UNKNOWN = "$X"
MAX_SOURCE = 2_000_000   # characters parsed per program; a longer one is cut (the tail is data to the gate, not code)
MAX_DEPTH = 4            # exec("...") inside exec("...")
MAX_ITEMS = 400          # extracted commands plus paths per program: more is not a script, it is a dump

LANGS = (
    (re.compile(r"^(?:python[\d.]*|pypy\d*)$"), "python"),
    (re.compile(r"^perl\d*$"), "perl"),
    (re.compile(r"^ruby\d*$"), "ruby"),
    (re.compile(r"^(?:node(?:js)?|deno|bun)$"), "js"),
    (re.compile(r"^php\d*$"), "php"),
    (re.compile(r"^osascript$"), "applescript"),
    (re.compile(r"^lua\d*$"), "lua"),
)


def language_of(word: str) -> str | None:
    w = word.rsplit("/", 1)[-1].lower()
    return next((lang for rx, lang in LANGS if rx.match(w)), None)


@dataclass
class Effects:
    commands: list[str] = field(default_factory=list)  # shell text the program runs
    writes: list[str] = field(default_factory=list)    # paths it writes, removes, renames or creates
    texts: list[str] = field(default_factory=list)     # words other rules read as written (an `import jade.cli` line)
    dynamic: bool = False                              # awk only: a sink whose target the program text does not contain
    shell_fed: bool = False                            # a shell is started without a script of its own: every string the program holds may be its input

    def add_cmd(self, c: str | None) -> None:
        if c and c.replace(UNKNOWN, "").strip(" '\"") and len(self.commands) + len(self.writes) < MAX_ITEMS:
            self.commands.append(c)

    def add_write(self, p: str | None) -> None:
        if p and p.replace(UNKNOWN, "").strip("/ .~'\"") and len(self.commands) + len(self.writes) < MAX_ITEMS and p not in self.writes:
            self.writes.append(p)

    def merge(self, other: Effects) -> None:
        for c in other.commands:
            self.add_cmd(c)
        for p in other.writes:
            self.add_write(p)
        self.texts += [t for t in other.texts if t not in self.texts]
        self.dynamic = self.dynamic or other.dynamic
        self.shell_fed = self.shell_fed or other.shell_fed


def word(s: str) -> str:
    """One shell word: quoted when it needs it, an unknown part left as a bare `$X`."""
    return s if s == UNKNOWN else shlex.quote(s) if s else "''"


def argv_text(items: list[str]) -> str:
    return " ".join(word(i) for i in items)


def effects(lang: str, source: str, depth: int = 0) -> Effects:
    source = source[:MAX_SOURCE]
    if lang == "python":
        fx = _python(source, depth)
        if fx is not None:
            return fx
    return _generic(lang, source)


# ------------------------------------------------------------------ python

EXEC_SHELL = {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput", "commands.getoutput", "commands.getstatusoutput",
              "asyncio.create_subprocess_shell", "pexpect.run", "pexpect.spawn", "pty.spawn", "os.popen2", "os.popen3", "os.popen4"}
EXEC_AUTO = {"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen",
             "asyncio.create_subprocess_exec", "invoke.run"}
EXEC_ARGV_FROM = {"os.execv": 1, "os.execve": 1, "os.execvp": 1, "os.execvpe": 1, "os.posix_spawn": 1, "os.posix_spawnp": 1,
                  "os.spawnv": 2, "os.spawnve": 2, "os.spawnvp": 2, "os.spawnvpe": 2}
EXEC_VARARGS_FROM = {"os.execl": 1, "os.execle": 1, "os.execlp": 1, "os.execlpe": 1, "os.spawnl": 2, "os.spawnle": 2, "os.spawnlp": 2, "os.spawnlpe": 2}
WRITE_FIRST = {"os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.mkdir", "os.makedirs", "os.chmod", "os.chown", "os.lchown", "os.truncate",
               "os.utime", "os.chflags", "os.mkfifo", "os.mknod", "shutil.rmtree", "shutil.chown", "logging.FileHandler", "logging.handlers.RotatingFileHandler",
               "logging.handlers.TimedRotatingFileHandler", "logging.handlers.WatchedFileHandler", "sqlite3.connect", "shutil.make_archive",
               "numpy.save", "numpy.savez", "numpy.savez_compressed", "numpy.savetxt", "plistlib.dump_file", "os.lchmod"}
WRITE_BOTH = {"os.rename", "os.replace", "os.renames", "shutil.move"}
WRITE_SECOND = {"os.link", "os.symlink", "shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree", "shutil.copymode", "shutil.copystat",
                "shutil.unpack_archive", "shutil.copyfileobj"}
OPEN_FUNCS = {"open", "io.open", "codecs.open", "gzip.open", "bz2.open", "lzma.open", "tarfile.open", "tarfile.TarFile", "zipfile.ZipFile",
              "tarfile.TarFile.open"}
PATH_WRITE_METHODS = {"write_text", "write_bytes", "touch", "unlink", "rmdir", "mkdir", "chmod", "lchmod", "symlink_to", "hardlink_to", "link_to"}
PATH_MOVE_METHODS = {"rename", "replace"}
OBJ_PATH_METHODS = {"to_csv", "to_json", "to_excel", "to_parquet", "to_pickle", "to_feather", "to_html", "to_markdown", "to_latex", "savefig", "extractall"}
MODULE_LOADERS = {"__import__", "importlib.import_module", "runpy.run_module", "importlib.util.find_spec"}
HOME_NAMES = {"HOME", "USERPROFILE"}
PATH_CTORS = {"pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "pathlib.PurePosixPath", "Path"}
TRANSPARENT = {"os.path.expanduser", "os.path.expandvars", "os.path.abspath", "os.path.realpath", "os.path.normpath", "os.fspath", "str", "os.path.normcase",
               "pathlib.Path.expanduser", "bytes.decode"}


class _Py:
    def __init__(self, depth: int):
        self.fx = Effects()
        self.depth = depth
        self.alias: dict[str, str] = {}   # local name -> dotted module path
        self.vars: dict[str, ast.AST] = {}  # local name -> the expression assigned to it last
        self.lists: dict[str, list[ast.AST]] = {}
        self.funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}   # functions defined in the program, inlined at a call
        self.inlined = 0
        self.nesting = 0

    # ---- names
    def qual(self, n: ast.AST) -> str | None:
        if isinstance(n, ast.Name):
            return self.alias.get(n.id, n.id)
        if isinstance(n, ast.Attribute):
            base = self.qual(n.value)
            return f"{base}.{n.attr}" if base else None
        if isinstance(n, ast.Call):
            q = self.qual(n.func)
            if q in MODULE_LOADERS and n.args and (s := self.ev(n.args[0])):
                return s
            if q == "getattr" and len(n.args) >= 2 and (base := self.qual(n.args[0])) and (s := self.ev(n.args[1])):
                return f"{base}.{s}"
        return None

    # ---- values
    def ev(self, n: ast.AST | None, d: int = 0) -> str | None:
        """The string an expression evaluates to, with `$X` for a part the source does not contain, or None when nothing is known."""
        if n is None or d > 12:
            return None
        if isinstance(n, ast.Constant):
            v = n.value
            return v if isinstance(v, str) else v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if isinstance(n, ast.JoinedStr):
            return "".join(p.value if isinstance(p, ast.Constant) else (self.ev(p.value, d + 1) or UNKNOWN) if isinstance(p, ast.FormattedValue) else UNKNOWN
                           for p in n.values)
        if isinstance(n, ast.Name):
            if n.id in self.vars:
                return self.ev(self.vars[n.id], d + 1)
            return None
        if isinstance(n, ast.BinOp):
            left, right = self.ev(n.left, d + 1), self.ev(n.right, d + 1)
            if isinstance(n.op, ast.Add):
                return None if left is None and right is None else (UNKNOWN if left is None else left) + (UNKNOWN if right is None else right)
            if isinstance(n.op, ast.Div):
                return _join_path([left if left is not None else UNKNOWN, right if right is not None else UNKNOWN])
            if isinstance(n.op, ast.Mod) and left is not None:
                args = [self.ev(e, d + 1) or UNKNOWN for e in n.right.elts] if isinstance(n.right, ast.Tuple) else [self.ev(n.right, d + 1) or UNKNOWN]
                it = iter(args)
                return re.sub(r"%[-#0 +]*\d*(?:\.\d+)?[sdifrxXeEgGc]", lambda _m: next(it, UNKNOWN), left)
            return None
        if isinstance(n, ast.IfExp):
            return self.ev(n.body, d + 1) or self.ev(n.orelse, d + 1)
        if isinstance(n, ast.Subscript):
            if self.qual(n.value) == "os.environ" and self.ev(n.slice, d + 1) in HOME_NAMES:
                return "~"
            return None
        if isinstance(n, ast.Attribute):
            if n.attr == "parent":
                v = self.ev(n.value, d + 1)
                return v.rsplit("/", 1)[0] or "/" if v and "/" in v else "." if v else None
            return None
        if isinstance(n, ast.Call):
            return self._call_value(n, d)
        return None

    def _call_value(self, n: ast.Call, d: int) -> str | None:
        q = self.qual(n.func)
        args = [self.ev(a, d + 1) for a in n.args]
        if q in PATH_CTORS or q in ("os.path.join", "posixpath.join"):
            return _join_path([a if a is not None else UNKNOWN for a in args]) if args else "."
        if q in ("pathlib.Path.home", "Path.home", "os.path.expanduser") and not args:
            return "~"
        if q in ("pathlib.Path.cwd", "Path.cwd", "os.getcwd"):
            return "."
        if q in TRANSPARENT and args:
            return args[0]
        if q in ("os.getenv", "os.environ.get") and args and args[0] in HOME_NAMES:
            return "~"
        if isinstance(n.func, ast.Attribute):
            meth, recv = n.func.attr, n.func.value
            r = self.ev(recv, d + 1)
            if meth in ("expanduser", "resolve", "absolute", "strip", "rstrip", "lstrip", "lower", "upper", "decode", "as_posix") and r is not None:
                return r
            if meth in ("joinpath", "with_name") and r is not None:
                return _join_path([r, *[a if a is not None else UNKNOWN for a in args]])
            if meth == "format" and r is not None:
                return _format(r, args, {k.arg: self.ev(k.value, d + 1) for k in n.keywords if k.arg})
            if meth == "replace" and r is not None and len(args) >= 2 and args[0] is not None and args[1] is not None and not self._is_path_call(recv):
                return r.replace(args[0], args[1])
            if meth == "join" and r is not None and n.args:
                items = self.items(n.args[0])
                if items is not None:
                    return r.join(self.ev(i, d + 1) or UNKNOWN for i in items)
        return None

    def _is_path_call(self, recv: ast.AST) -> bool:
        return isinstance(recv, ast.Call) and self.qual(recv.func) in PATH_CTORS

    def items(self, n: ast.AST) -> list[ast.AST] | None:
        if isinstance(n, (ast.List, ast.Tuple)):
            return list(n.elts)
        if isinstance(n, ast.Name) and n.id in self.lists:
            return self.lists[n.id]
        return None

    def argv(self, n: ast.AST) -> str | None:
        items = self.items(n)
        if items is None:
            return None
        return argv_text([(self.ev(i) if not isinstance(i, ast.Starred) else None) or UNKNOWN for i in items])

    def command(self, n: ast.AST | None) -> str | None:
        """A command argument: a list is an argv, a string is shell text."""
        if n is None:
            return None
        if (a := self.argv(n)) is not None:
            return a
        return self.ev(n)

    def path(self, n: ast.AST | None) -> str | None:
        return self.ev(n) if n is not None else None

    # ---- the walk, in source order
    def walk(self, n: ast.AST) -> None:
        if isinstance(n, ast.Import):
            for a in n.names:
                self.alias[(a.asname or a.name.split(".")[0])] = a.name if a.asname else a.name.split(".")[0]
                self._jade_import(a.name)
            return
        if isinstance(n, ast.ImportFrom):
            mod = ("." * n.level) + (n.module or "")
            for a in n.names:
                self.alias[a.asname or a.name] = f"{mod}.{a.name}" if mod else a.name
            self._jade_import(mod, f"from {mod} import " + ", ".join(a.name for a in n.names))
            return
        if isinstance(n, ast.Assign):
            self.walk(n.value)
            for t in n.targets:
                self._bind(t, n.value)
            return
        if isinstance(n, ast.AnnAssign):
            if n.value is not None:
                self.walk(n.value)
                self._bind(n.target, n.value)
            return
        if isinstance(n, ast.AugAssign):
            self.walk(n.value)
            if isinstance(n.target, ast.Name):
                if n.target.id in self.lists and isinstance(n.op, ast.Add):
                    self.lists[n.target.id] = [*self.lists[n.target.id], *(self.items(n.value) or [n.value])]
                elif isinstance(n.op, ast.Add) and n.target.id in self.vars:
                    self.vars[n.target.id] = ast.BinOp(left=self.vars[n.target.id], op=ast.Add(), right=n.value)
                else:
                    self.vars.pop(n.target.id, None)
            return
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.funcs[n.name] = n
            saved = self._save()
            for a in (*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs, n.args.vararg, n.args.kwarg):
                if a is not None:
                    self.vars.pop(a.arg, None)
                    self.lists.pop(a.arg, None)
            for c in n.body:
                self.walk(c)
            self._restore(saved)
            return
        if isinstance(n, (ast.For, ast.AsyncFor)):
            self.walk(n.iter)
            items = self.items(n.iter) if not isinstance(n.iter, ast.Set) else list(n.iter.elts)
            if items is not None and 0 < len(items) <= 50 and self.nesting < 3:
                self.nesting += 1
                for item in items:   # a loop over a literal list runs its body once per element
                    self._bind_const(n.target, item)
                    for c in n.body:
                        self.walk(c)
                self.nesting -= 1
            else:
                self._forget(n.target)
                for c in n.body:
                    self.walk(c)
            for c in n.orelse:
                self.walk(c)
            return
        if isinstance(n, ast.Call):
            for c in ast.iter_child_nodes(n):
                self.walk(c)
            self._call(n)
            return
        if isinstance(n, ast.Attribute) and (q := self.qual(n)) and q.startswith("jade."):
            self.fx.texts.append(q)   # `import jade` then `jade.cli.main()`: the module is named by the attribute chain
        for c in ast.iter_child_nodes(n):
            self.walk(c)

    def _save(self) -> tuple[dict, dict]:
        return dict(self.vars), dict(self.lists)

    def _restore(self, saved: tuple[dict, dict]) -> None:
        self.vars, self.lists = saved

    def _const(self, node: ast.AST) -> ast.AST:
        """The value of an expression at a call or loop step, frozen so that it keeps its meaning inside the callee (`$X` when unknown)."""
        if (items := self.items(node)) is not None:
            return ast.List(elts=[self._const(i) for i in items], ctx=ast.Load())
        v = self.ev(node)
        return ast.Constant(UNKNOWN if v is None else v)

    def _bind_const(self, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            c = self._const(value)
            self.vars[target.id] = c
            if isinstance(c, ast.List):
                self.lists[target.id] = list(c.elts)
            else:
                self.lists.pop(target.id, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            vals = self.items(value)
            for k, t in enumerate(target.elts):
                if vals is not None and k < len(vals) and not isinstance(t, ast.Starred):
                    self._bind_const(t, vals[k])
                else:
                    self._forget(t)
        else:
            self._forget(target)

    def _inline(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, n: ast.Call) -> None:
        """Walk the body of a program-defined function with its parameters bound to what this call passes (`def sub(path, old, new): ...` called as
        `sub("jade/ask.py", ...)` writes jade/ask.py)."""
        a = fn.args
        params = [*a.posonlyargs, *a.args]
        defaults = dict(zip([p.arg for p in params][len(params) - len(a.defaults):], a.defaults, strict=False)) if a.defaults else {}
        defaults.update({p.arg: d for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is not None})
        given: dict[str, ast.AST] = {}
        for p, arg in zip(params, n.args, strict=False):
            if isinstance(arg, ast.Starred):
                break
            given[p.arg] = arg
        given.update({k.arg: k.value for k in n.keywords if k.arg})
        saved = self._save()
        bound = {name: self._const(node) for name, node in given.items()}
        bound.update({name: self._const(d) for name, d in defaults.items() if name not in bound})
        for p in (*params, *a.kwonlyargs):
            self.vars.pop(p.arg, None)
            self.lists.pop(p.arg, None)
        for name, c in bound.items():
            self.vars[name] = c
            if isinstance(c, ast.List):
                self.lists[name] = list(c.elts)
        self.nesting += 1
        self.inlined += 1
        for c in fn.body:
            self.walk(c)
        self.nesting -= 1
        self._restore(saved)

    def _forget(self, t: ast.AST) -> None:
        for x in ast.walk(t):
            if isinstance(x, ast.Name):
                self.vars.pop(x.id, None)
                self.lists.pop(x.id, None)

    def _bind(self, t: ast.AST, value: ast.AST) -> None:
        if not isinstance(t, ast.Name):
            self._forget(t)
            return
        if isinstance(value, (ast.Name, ast.Attribute)) and (q := self.qual(value)) and not (isinstance(value, ast.Name) and value.id in self.vars):
            self.alias[t.id] = q   # sp = subprocess, run = subprocess.run
        self.vars[t.id] = value
        if isinstance(value, (ast.List, ast.Tuple)):
            self.lists[t.id] = list(value.elts)
        else:
            self.lists.pop(t.id, None)

    def _jade_import(self, mod: str, line: str | None = None) -> None:
        if "jade" in mod.lower():
            self.fx.texts.append(line or f"import {mod}")

    # ---- sinks
    def _shell_sink(self, c: str | None) -> None:
        if c and _bare_shell(c):
            self.fx.shell_fed = True

    def _call(self, n: ast.Call) -> None:
        fx = self.fx
        q = self.qual(n.func) or ""
        kw = {k.arg: k.value for k in n.keywords if k.arg}
        a = n.args
        if q in EXEC_SHELL or q in EXEC_AUTO:
            c = self.command(a[0] if a else kw.get("args") or kw.get("cmd") or kw.get("command"))
            if c is not None and q == "pexpect.spawn" and len(a) > 1 and (extra := self.argv(a[1])):
                c = f"{c} {extra}"
            fx.add_cmd(c)
            self._shell_sink(c)
        elif q in EXEC_ARGV_FROM and len(a) > EXEC_ARGV_FROM[q]:
            c = self.argv(a[EXEC_ARGV_FROM[q]]) or (argv_text([self.ev(a[0]) or UNKNOWN]) if a else None)
            fx.add_cmd(c)
            self._shell_sink(c)
        elif q in EXEC_VARARGS_FROM:
            c = argv_text([self.ev(x) or UNKNOWN for x in a[EXEC_VARARGS_FROM[q]:]])
            fx.add_cmd(c)
            self._shell_sink(c)
        elif q.startswith("sh.") and q.count(".") == 1:
            fx.add_cmd(argv_text([q[3:], *[self.ev(x) or UNKNOWN for x in a]]))
        elif q in OPEN_FUNCS:
            mode_n = a[1] if len(a) > 1 else kw.get("mode")
            if mode_n is not None and ((m := self.ev(mode_n)) is None or set(m) & set("wax+")):  # a mode the source does not contain counts as a write
                fx.add_write(self.path(a[0] if a else kw.get("file") or kw.get("name") or kw.get("filename")))
        elif q == "os.open":
            flags = ast.dump(a[1]) if len(a) > 1 else ast.dump(kw["flags"]) if "flags" in kw else ""
            if re.search(r"O_(?:WRONLY|RDWR|CREAT|APPEND|TRUNC)", flags):
                fx.add_write(self.path(a[0] if a else None))
        elif q in WRITE_FIRST:
            fx.add_write(self.path(a[0] if a else kw.get("filename") or kw.get("path") or kw.get("base_name")))
        elif q == "logging.basicConfig" and "filename" in kw:
            fx.add_write(self.path(kw["filename"]))
        elif q in WRITE_BOTH:
            for x in a[:2]:
                fx.add_write(self.path(x))
        elif q in WRITE_SECOND:
            fx.add_write(self.path(a[1]) if len(a) > 1 else None)
        elif q in ("exec", "eval") and a and self.depth < MAX_DEPTH and (s := self.ev(a[0])) is not None:
            fx.merge(effects("python", s, self.depth + 1))
        elif q in MODULE_LOADERS and a and (s := self.ev(a[0])):
            self._jade_import(s)
        elif isinstance(n.func, ast.Name) and n.func.id in self.funcs and self.nesting < 3 and self.inlined < 200:
            self._inline(self.funcs[n.func.id], n)
        elif isinstance(n.func, ast.Attribute):
            meth, recv = n.func.attr, n.func.value
            if meth in PATH_WRITE_METHODS:
                fx.add_write(self.path(recv))
            elif meth in PATH_MOVE_METHODS and len(a) == 1:   # Path.rename(target): str.replace takes two arguments
                fx.add_write(self.path(recv))
                fx.add_write(self.path(a[0]))
            elif meth == "open" and (mode_n := a[0] if a else kw.get("mode")) is not None and set(self.ev(mode_n) or "") & set("wax+"):
                fx.add_write(self.path(recv))
            elif meth in OBJ_PATH_METHODS and (a or kw):
                fx.add_write(self.path(a[0] if a else next(iter(kw.values()))))
            elif meth in ("append", "extend") and isinstance(recv, ast.Name) and recv.id in self.lists and a:
                self.lists[recv.id] = [*self.lists[recv.id], *((self.items(a[0]) or []) if meth == "extend" else [a[0]])]

    def finish(self, tree: ast.AST) -> None:
        """Strings a started shell may read as its input (`Popen(["sh"]).communicate(b"...")`): with no way to tell which, all of them count."""
        if self.fx.shell_fed:
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes)):
                    text = n.value if isinstance(n.value, str) else n.value.decode("utf-8", "replace")
                    if len(text.split()) > 1:
                        self.fx.add_cmd(text)


SHELL_PROGRAMS = {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "ash", "busybox"}


def _bare_shell(cmd: str) -> bool:
    """A command line that starts a shell with no script of its own (`bash`, `sudo sh -s`): its commands arrive some other way."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    head = [t.rsplit("/", 1)[-1] for t in toks[:4]]
    if not any(h in SHELL_PROGRAMS for h in head):
        return False
    return not any(t == "-c" or (t.startswith("-") and not t.startswith("--") and "c" in t[1:]) for t in toks)


def _format(template: str, args: list[str | None], kw: dict[str, str | None]) -> str:
    it = iter(args)

    def sub(m: re.Match) -> str:
        key = m.group(1)
        v = next(it, None) if key == "" else (args[int(key)] if int(key) < len(args) else None) if key.isdigit() else kw.get(key)
        return UNKNOWN if v is None else v

    return re.sub(r"\{([^{}:!]*)[^{}]*\}", sub, template)


def _join_path(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if not out or p.startswith(("/", "~")):
            out = p
        else:
            out = out.rstrip("/") + "/" + p
    return out


def _python(source: str, depth: int) -> Effects | None:
    tree = None
    for text in (source, textwrap.dedent(source)):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")   # an escape such as `\\``: a SyntaxWarning of the program being read, not ours
                tree = ast.parse(text)
            break
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            continue
    if tree is None:
        return None
    p = _Py(depth)
    try:
        p.walk(tree)
        p.finish(tree)
    except RecursionError:
        return None
    return p.fx


# ------------------------------------------------------------------ the other languages: literal arguments of known calls

LIT = r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`)"""
LIT_RE = re.compile(LIT, re.S)
SEP_RE = re.compile(r"""[\s,\[\](){}=>]*(?:(?:cmd|args|command|file|path)\s*[:=]>?\s*)?[\s,\[\](){}=>]*""")
CAT_RE = re.compile(r"\s*(?:\.\.|\.|\+|<<)\s*")
ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "", "a": "", "e": ""}


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: ESC.get(m.group(1), m.group(1)), s, flags=re.S)


IDENT_RE = re.compile(r"[$@]?[A-Za-z_]\w*")
ASSIGN_RE = re.compile(r"(?:\b(?:const|let|var|my|our|local)\s+)?([$@]?[A-Za-z_]\w*)\s*=(?!=)\s*" + LIT)


def _consts(src: str) -> dict[str, str]:
    """`const p = "jade/x.py"`, `my $p = '...'`, `$p = "..."`, `local p = "..."`: names bound to a string literal anywhere in the program (the last wins)."""
    return {m.group(1): _unescape(next(g for g in m.groups()[1:] if g is not None)) for m in ASSIGN_RE.finditer(src)}


def _literal_at(src: str, j: int, consts: dict[str, str]) -> tuple[str, int] | None:
    if lit := LIT_RE.match(src, j):
        return _unescape(next(g for g in lit.groups() if g is not None)), lit.end()
    if (m := IDENT_RE.match(src, j)) and m.group() in consts and not src.startswith("(", m.end()):
        return consts[m.group()], m.end()
    return None


def _args_after(src: str, pos: int, consts: dict[str, str] | None = None) -> list[str]:
    """The literal arguments that follow a call head: strings separated by commas and brackets are separate arguments, strings joined with
    `.` or `+` are one. A name bound to a string literal elsewhere in the program counts as that literal. Stops at the first thing that is not
    a literal (an unknown variable, a call, a statement end)."""
    consts = consts or {}
    args: list[str] = []
    i = pos
    cur: str | None = None
    for _ in range(40):
        m = SEP_RE.match(src, i)
        j = m.end() if m else i
        got = _literal_at(src, j, consts)
        if not got:
            break
        text, end = got
        sep_seen = bool(src[i:j].strip()) or cur is None
        if cur is not None and not sep_seen:
            cur += text
        else:
            if cur is not None:
                args.append(cur)
            cur = text
        i = end
        cm = CAT_RE.match(src, i)
        while cm:  # `"a" . "b"`: one argument; a non-literal after the operator ends the arguments
            nxt = _literal_at(src, cm.end(), consts)
            if not nxt:
                if cur is not None:
                    cur += UNKNOWN
                    args.append(cur)
                    cur = None
                return args
            cur += nxt[0]
            i = nxt[1]
            cm = CAT_RE.match(src, i)
    if cur is not None:
        args.append(cur)
    return args


def _cmd_from(args: list[str]) -> str | None:
    if not args:
        return None
    return args[0] if len(args) == 1 else argv_text(args)


def _spec(heads: str, mode: str) -> tuple[re.Pattern, str]:
    return re.compile(heads), mode


# mode: run = the arguments are a command (one string = shell text, several = argv); first / last / all = the literal arguments that are paths
# written; open = (path, mode) written when the mode opens for writing; open3 = perl's open(FH, ...) forms
_GENERIC: dict[str, list[tuple[re.Pattern, str]]] = {
    "python": [
        _spec(r"\b(?:os\.(?:system|popen)|subprocess\.(?:getoutput|getstatusoutput|run|call|check_call|check_output|Popen)|pty\.spawn|Popen)\s*\(", "run"),
        _spec(r"\bos\.(?:remove|unlink|rmdir|makedirs|mkdir|chmod|chown|truncate)\s*\(|\bshutil\.rmtree\s*\(", "first"),
        _spec(r"\b(?:os\.(?:rename|replace)|shutil\.move)\s*\(", "all"),
        _spec(r"\bshutil\.copy(?:2|file|tree)?\s*\(", "last"),
        _spec(r"\bopen\s*\(", "open"),
    ],
    "perl": [
        _spec(r"\b(?:system|exec)\b\s*\(?\s*", "run"),
        _spec(r"\bopen\s*\(?\s*(?:my\s+)?[\w$@*]+\s*,\s*", "open3"),
        _spec(r"\b(?:unlink|mkdir|rmdir|chmod|chown|utime|truncate|remove_tree|rmtree|make_path|mkpath)\b\s*\(?\s*(?:[\w$]+\s*,\s*)?", "first"),
        _spec(r"\b(?:rename|move|mv)\s*\(\s*", "all"),
        _spec(r"\b(?:copy|cp|symlink|link)\s*\(\s*", "last"),
    ],
    "ruby": [
        _spec(r"\b(?:system|exec|spawn)\b\s*\(?\s*|\b(?:IO\.popen|Open3\.(?:capture2e?|capture3|popen[23]e?|pipeline\w*)|Kernel\.(?:system|exec|spawn))\s*\(?\s*", "run"),
        _spec(r"\bFile\.(?:open|new)\s*\(?\s*", "open"),
        _spec(r"\b(?:File|IO)\.(?:write|binwrite|delete|unlink|chmod|chown|truncate|utime)\s*\(?\s*|\bFileUtils\.(?:rm|rm_f|rm_r|rm_rf|remove\w*|mkdir|mkdir_p|touch|chmod|chown|rmdir)\s*\(?\s*", "first"),
        _spec(r"\b(?:File\.rename|FileUtils\.mv)\s*\(?\s*", "all"),
        _spec(r"\b(?:FileUtils\.(?:cp|cp_r|ln|ln_s|ln_sf|install|copy\w*)|File\.(?:symlink|link))\s*\(?\s*", "last"),
    ],
    "js": [
        _spec(r"\b(?:exec|execSync|execFile|execFileSync|spawn|spawnSync|fork|execa|execaSync|execaCommand|Command)\s*\(\s*|\bBun\.(?:spawn|spawnSync)\s*\(\s*|\bDeno\.run\s*\(\s*", "run"),
        _spec(r"\b(?:writeFile|writeFileSync|appendFile|appendFileSync|createWriteStream|unlink|unlinkSync|rm|rmSync|rmdir|rmdirSync|mkdir|mkdirSync|truncate|truncateSync|chmod|chmodSync|chown|chownSync|writeTextFile|writeTextFileSync|writeFile|remove|removeSync)\s*\(\s*|\bBun\.write\s*\(\s*", "first"),
        _spec(r"\b(?:rename|renameSync)\s*\(\s*", "all"),
        _spec(r"\b(?:copyFile|copyFileSync|cp|cpSync|symlink|symlinkSync|link|linkSync)\s*\(\s*", "last"),
        _spec(r"\b(?:open|openSync)\s*\(\s*", "open"),
    ],
    "php": [
        _spec(r"\b(?:system|exec|shell_exec|passthru|popen|proc_open|pcntl_exec)\s*\(\s*", "run"),
        _spec(r"\bfopen\s*\(\s*", "open"),
        _spec(r"\b(?:file_put_contents|unlink|mkdir|rmdir|touch|chmod|chown|move_uploaded_file)\s*\(\s*", "first"),
        _spec(r"\brename\s*\(\s*", "all"),
        _spec(r"\b(?:copy|symlink|link)\s*\(\s*", "last"),
    ],
    "applescript": [
        _spec(r"\bdo\s+shell\s+script\s+", "run"),
        _spec(r"\bdo\s+script\s+", "run"),
        _spec(r"\bopen\s+for\s+access\s+(?:POSIX\s+file\s+)?", "first"),
    ],
    "lua": [
        _spec(r"\b(?:os\.execute|io\.popen)\s*\(?\s*", "run"),
        _spec(r"\bio\.open\s*\(\s*", "open"),
        _spec(r"\bos\.(?:remove)\s*\(\s*", "first"),
        _spec(r"\bos\.rename\s*\(\s*", "all"),
    ],
}
_BACKTICK = {"perl": re.compile(r"`((?:[^`\\]|\\.)*)`|\bqx\s*(?:\{([^}]*)\}|\(([^)]*)\)|\[([^\]]*)\]|/([^/]*)/|'([^']*)'|\"([^\"]*)\")", re.S),
             "ruby": re.compile(r"`((?:[^`\\]|\\.)*)`|%x\s*(?:\{([^}]*)\}|\(([^)]*)\)|\[([^\]]*)\])", re.S),
             "php": re.compile(r"`((?:[^`\\]|\\.)*)`", re.S)}
_JS_TAG = re.compile(r"\$\s*`((?:[^`\\]|\\.)*)`", re.S)
_WRITE_MODE = re.compile(r"[wax+]")


def _generic(lang: str, src: str) -> Effects:
    fx = Effects()
    consts = _consts(src)
    if rx := _BACKTICK.get(lang):
        for m in rx.finditer(src):
            fx.add_cmd(_unescape(next(g for g in m.groups() if g is not None)))
    if lang == "js":
        for m in _JS_TAG.finditer(src):
            fx.add_cmd(_unescape(m.group(1)))
    for rx, mode in _GENERIC.get(lang, ()):
        for m in rx.finditer(src):
            args = _args_after(src, m.end(), consts)
            if not args:
                continue
            if mode == "run":
                c = _cmd_from(args)
                fx.add_cmd(c)
                if c and _bare_shell(c):
                    fx.shell_fed = True
            elif mode == "first":
                fx.add_write(args[0])
            elif mode == "all":
                for p in args[:2]:
                    fx.add_write(p)
            elif mode == "last":
                if len(args) >= 2:
                    fx.add_write(args[1])
            elif mode == "open":
                if len(args) >= 2 and _WRITE_MODE.search(args[1]):
                    fx.add_write(args[0])
            elif mode == "open3":   # perl: open(FH, MODE, PATH), open(FH, "| cmd"), open(FH, ">path")
                if len(args) >= 2:
                    if args[0].strip() in ("|-", "-|"):
                        fx.add_cmd(argv_text(args[1:]))
                    elif ">" in args[0] or args[0].strip().startswith("+<"):
                        fx.add_write(args[1])
                else:
                    t = args[0].strip()
                    if t.startswith("|") or t.endswith("|"):
                        fx.add_cmd(t.strip("|"))
                    elif t.startswith((">", "+<", "+>")):
                        fx.add_write(t.lstrip("+<>").strip())
    if fx.shell_fed:
        for m in LIT_RE.finditer(src):
            text = _unescape(next(g for g in m.groups() if g is not None))
            if len(text.split()) > 1:
                fx.add_cmd(text)
    return fx


# ------------------------------------------------------------------ awk

AWK_LIT = r'"((?:[^"\\]|\\.)*)"'
AWK_RISKY = re.compile(r"\bsystem\s*\(|\bgetline\b|\bprintf?\b[^;}\n]*(?<![=!<>])(?:>>?|(?<!\|)\|(?!\|))")


def awk_effects(prog: str) -> Effects:
    """The commands and files an awk program uses, from its literal arguments. `fx.dynamic` is set when a risky construct names something the
    program text does not contain (`print > $1".txt"`, `system(cmd)`): the caller then treats the whole call as one that may write any operand."""
    fx = Effects()
    risky = AWK_RISKY.findall(prog)
    if not risky:
        return fx
    found = 0
    for m in re.finditer(r"\bsystem\s*\(\s*" + AWK_LIT + r"\s*\)", prog):
        fx.add_cmd(_unescape(m.group(1)))
        found += 1
    for m in re.finditer(r"(?<!\|)\|(?!\|)\s*" + AWK_LIT, prog):
        fx.add_cmd(_unescape(m.group(1)))
        found += 1
    for m in re.finditer(AWK_LIT + r"\s*\|\s*getline\b", prog):
        fx.add_cmd(_unescape(m.group(1)))
        found += 1
    for m in re.finditer(r"\bprintf?\b[^;}\n]*?(?<![=!<>])>>?\s*" + AWK_LIT, prog):
        fx.add_write(_unescape(m.group(1)))
        found += 1
    sinks = len(re.findall(r"\bsystem\s*\(", prog)) + len(re.findall(r"\bprintf?\b[^;}\n]*(?<![=!<>])(?:>>?|(?<!\|)\|(?!\|))", prog)) + len(re.findall(r"\|\s*getline\b", prog))
    fx.dynamic = sinks > found
    return fx
