"""V6-34: a test that measures elapsed time must carry `@pytest.mark.timing`.

CI runs the `timing` tests one after the other, after the parallel run, so load from other workers cannot fail a wall-clock bound
(pyproject.toml, `markers`). A test that reads a clock and is not marked runs in the parallel run and can fail at random.
This test reads the test files (it runs none of them) and lists every unmarked test that reads `time.monotonic`, `time.perf_counter`
or `time.process_time`, directly or through a helper in the same file. `time.time()` is left out: tests use it for timestamps.
"""
import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
CLOCKS = {"monotonic", "perf_counter", "monotonic_ns", "perf_counter_ns", "process_time"}


def _reads_clock(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr in CLOCKS and isinstance(n.value, ast.Name) and n.value.id == "time"
        for n in ast.walk(node)
    )


def _unmarked_clock_tests(source: str) -> list[str]:
    tree = ast.parse(source)
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in stmt.targets) and "timing" in ast.unparse(stmt.value):
            return []   # the whole module is marked
    helpers = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("test_") and _reads_clock(n)
    }
    found = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.FunctionDef) and n.name.startswith("test_")):
            continue
        if any("timing" in ast.unparse(d) for d in n.decorator_list):
            continue
        calls_helper = any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in helpers for c in ast.walk(n))
        if _reads_clock(n) or calls_helper:
            found.append(n.name)
    return found


def test_the_scan_finds_an_unmarked_clock_test_and_skips_a_marked_one():
    unmarked = "import time\n\ndef test_a():\n    t = time.monotonic()\n    assert time.monotonic() - t < 1\n"
    marked = "import time, pytest\n\n@pytest.mark.timing\ndef test_a():\n    t = time.perf_counter()\n"
    via_helper = "import time\n\ndef _cost():\n    return time.process_time()\n\ndef test_b():\n    assert _cost() < 1\n"
    module_marked = "import pytest\npytestmark = pytest.mark.timing\n" + unmarked
    stamp_only = "import time\n\ndef test_c():\n    assert time.time() > 0\n"
    assert _unmarked_clock_tests(unmarked) == ["test_a"]
    assert _unmarked_clock_tests(marked) == []
    assert _unmarked_clock_tests(via_helper) == ["test_b"]
    assert _unmarked_clock_tests(module_marked) == []
    assert _unmarked_clock_tests(stamp_only) == []


def test_every_test_that_measures_elapsed_time_is_marked_timing():
    bad = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        names = _unmarked_clock_tests(path.read_text())
        if names:
            bad[str(path.relative_to(TESTS))] = names
    assert not bad, f"add @pytest.mark.timing to: {bad}"
