"""Static check for Python harnesses that run shell payloads in a real shell without the sandbox (docs/13, SI-13).

Standard library only, and no import of the rest of `jade`: `evals/realshell.py` loads this file by path so that its `audit` command works from any
interpreter, and the gate (jade/policy.py) uses it on the Python script a Bash command runs.

Two levels:

- `audit_source`: a file that starts a process and names a shell (a shell path constant, an argv list that starts with a shell name, `shell=True`,
  `os.system`, `os.popen`) and does not use the sandbox helper. Used by `python3 evals/realshell.py audit` and the repository ratchet test.
- `gate_findings`: the same, and the file also reads a corpus file (json, csv, lines of a file, a random sample) named in the script or on the
  command line, and starts processes many times (a pool, an executor, a map, or a start call inside a loop or comprehension). That is the shape of
  a payload runner; a build script with `shell=True`, or a test that runs one script and parses its output, does not have it. SI-13 blocks a
  command that runs such a file.

It is a syntactic check: code built to hide from it passes (docs/05 T30).
"""
from __future__ import annotations

import ast
import os

SPAWN = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput", "system", "popen", "spawn",
         "create_subprocess_exec", "create_subprocess_shell", "posix_spawn", "posix_spawnp"}
SPAWN_PREFIXES = ("execv", "execl", "spawnv", "spawnl")
SHELL_NAMES = {"bash", "zsh", "sh", "dash", "ksh", "fish", "csh", "tcsh"}
HELPER = "realshell"
SANDBOX_PROFILE_MARK = "(deny file-write*)"   # a sandbox profile that allows default and denies only a few programs (the 2026-11-15 echo runner) lacks it
# A corpus read from a file: json.load (not loads, which mostly parses a program's output), yaml, csv, the lines of a file, a random sample of rows.
LOADS = {"load", "safe_load", "readlines", "reader", "DictReader", "sample", "choices", "shuffle"}
# Many runs: a pool, an executor, or a map over the rows.
FANOUT = {"Pool", "ThreadPool", "ProcessPoolExecutor", "ThreadPoolExecutor", "map", "imap", "imap_unordered", "starmap", "map_async", "submit"}
DATA_EXT = (".json", ".jsonl", ".ndjson", ".txt", ".csv", ".tsv", ".lst")   # a corpus file, named in the script or on the command line
READ_MAX = 1 << 20   # the gate reads at most 1 MiB of a script, like the other script reads in jade/policy.py


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _scan(tree: ast.AST) -> dict:
    docs = _docstrings(tree)
    uses_helper = False
    spawns: list[int] = []
    shellish: list[tuple[int, str]] = []
    loads = fanout = looped = literal_corpus = argv = False
    spawn_names: set[str] = set()   # functions that start a process: a pool that maps one of them is a fan-out of starts

    def visit(node: ast.AST, in_loop: bool, func: str | None) -> None:
        nonlocal uses_helper, loads, fanout, looped, literal_corpus, argv
        if (isinstance(node, ast.Attribute) and node.attr == "argv") or (isinstance(node, ast.Call) and _call_name(node.func) == "ArgumentParser"):
            argv = True
        if isinstance(node, ast.Import) and any(a.name.split(".")[-1] == HELPER for a in node.names):
            uses_helper = True
        elif isinstance(node, ast.ImportFrom) and ((node.module or "").split(".")[-1] == HELPER or any(a.name == HELPER for a in node.names)):
            uses_helper = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docs and (node.value.endswith(HELPER + ".py") or SANDBOX_PROFILE_MARK in node.value):
                uses_helper = True   # loads the helper by path, or is the helper: it builds a profile that denies file writes
            elif "/" in node.value and os.path.basename(node.value) in SHELL_NAMES:
                shellish.append((node.lineno, f"names the shell {node.value!r}"))
        elif (isinstance(node, ast.List) and len(node.elts) >= 2 and isinstance(node.elts[0], ast.Constant) and isinstance(node.elts[0].value, str)
              and node.elts[0].value in SHELL_NAMES):
            shellish.append((node.lineno, f"argv list starts with {node.elts[0].value!r}"))
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in SPAWN or name.startswith(SPAWN_PREFIXES):
                spawns.append(node.lineno)
                if func:
                    spawn_names.add(func)
                if in_loop:
                    looped = True
                if name in ("system", "popen", "getoutput", "getstatusoutput", "create_subprocess_shell"):
                    shellish.append((node.lineno, f"{name}() runs a shell"))
            if name in LOADS:
                loads = True
            if name == "exec" and isinstance(node.func, ast.Name) and node.args and isinstance(node.args[0], ast.Call):
                loads = literal_corpus = True   # `exec(open("cmds.py").read())`: a corpus kept as Python source
            if name in ("open", "Path", "read_text") and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str) and node.args[0].value.lower().endswith(DATA_EXT):
                literal_corpus = True
            if name in FANOUT:
                fanout = True
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    shellish.append((node.lineno, "shell=True"))
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            for child in node.body + node.orelse:
                visit(child, True, func)
            for child in ([node.iter, node.target] if isinstance(node, (ast.For, ast.AsyncFor)) else [node.test]):
                visit(child, in_loop, func)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for child in ast.iter_child_nodes(node):
                visit(child, True, func)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.iter_child_nodes(node):
                visit(child, False, node.name)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, in_loop, func)

    visit(tree, False, None)
    return {"uses_helper": uses_helper, "spawns": spawns, "shellish": sorted(set(shellish)), "loads": loads, "repeats": fanout or looped,
            "literal_corpus": literal_corpus, "argv": argv}


def audit_source(src: str) -> list[tuple[int, str]]:
    """Findings for one Python source: (line, reason) for a file that starts a shell and does not use evals/realshell.py. Raises SyntaxError or ValueError
    for a file Python cannot parse."""
    s = _scan(ast.parse(src))
    if not s["spawns"] or not s["shellish"] or s["uses_helper"]:
        return []
    return s["shellish"]


def gate_findings(src: str, data_arg: bool = False) -> list[tuple[int, str]]:
    """`audit_source` findings for a file that also reads a corpus file and starts processes many times: a payload runner without the sandbox. The corpus
    file is named in the script (`open("corpus.json")`) or, with `data_arg`, on the command line that runs it (`python3 run.py corpus.json out.json`) and
    the script reads its arguments (`sys.argv`, argparse). A
    file Python cannot parse, or one too deep or too big to walk, has no findings: the gate does not block what it cannot read (a script-file blind spot,
    R-31)."""
    try:
        s = _scan(ast.parse(src))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    if not s["spawns"] or not s["shellish"] or s["uses_helper"] or not s["loads"] or not s["repeats"] or not ((data_arg and s["argv"]) or s["literal_corpus"]):
        return []
    return s["shellish"]


def gate_file(path: str, any_name: bool = False, data_arg: bool = False) -> list[tuple[int, str]]:
    """`gate_findings` for a file on disk: a regular file of at most READ_MAX bytes, named `*.py` unless `any_name` (a Python interpreter runs it);
    anything else has no findings."""
    try:
        if not ((any_name or path.endswith(".py")) and os.path.isfile(path) and os.path.getsize(path) <= READ_MAX):
            return []
        with open(path, encoding="utf-8", errors="replace") as fh:
            return gate_findings(fh.read(READ_MAX), data_arg)
    except OSError:
        return []
