"""W-122 (owner decision 2026-11-20: no SI-10 change): a known limit, pinned.

SI-10 reads a glob of any length as a mention of the permit seal key when the same command names JADE's state folder (`share/jade`, `JADE_HOME`):
`cat ~/.local/share/jade/*.key` must stay blocked. W-71 blanks the body of a heredoc that `cat`/`tee` writes to a literal file, so a test or note
written that way passes. But when the body is read whole (a program body, a line that also runs something, a destination the scanner cannot fix),
a body that names the state folder and holds a glob word that matches the key name (`glob('*')`, `2 * 3`) is blocked too, although it reads nothing.
That false positive is accepted; the remedy is the Write tool, whose content SI-10 does not read. If this file starts failing because a case below
is no longer blocked, SI-10 changed: that needs the owner's word and a new differential, not an edit of this test.
"""
import pytest

from jade import policy

STATE = "~/.local/share/" + "jade"          # spelled in two parts so this file's own text is not one long literal of the folder
BODY = f"D = '{STATE}'\nfor p in d.glob('*'):\n    x = 2 * 3"


def si10(tool: str, tool_input: dict, cwd: str = "/tmp/case") -> bool:
    return "SI-10" in policy.assess(tool, tool_input, cwd).blocked


def bash(cmd: str, cwd: str = "/tmp/case") -> bool:
    return si10("Bash", {"command": cmd}, cwd)


def hd(prefix: str, body: str = BODY, after: str = "") -> str:
    return f"{prefix} <<'EOF'\n{body}\nEOF" + (f"\n{after}" if after else "")


# ------------------------------------------------------------------ the accepted false positive: the body is read whole
BLOCKED = [
    hd("python3 -"),                                              # a program body
    hd("uv run python -"),
    f"cat <<'EOF' | python3\n{BODY}\nEOF",                       # a piped program
    hd("cat > tests/t.py", after="uv run pytest tests/t.py"),     # the same line runs something
    hd("cat > tests/t.py", after="git add tests/t.py && git commit -m t"),
    hd("cat > tests/t.sh", after="bash tests/t.sh"),
    hd("cd /tmp && cat > t.py"),                                  # a relative destination after `cd`
    hd('cat > "$F"'),                                             # a destination the scanner cannot fix
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_a_whole_read_heredoc_that_names_the_state_folder_and_a_glob_is_blocked(cmd):
    assert bash(cmd), cmd


def test_the_real_effect_it_stands_for_stays_blocked():
    assert bash(f"cat {STATE}/*.key")
    assert bash(f"python3 - <<'EOF'\nimport glob\nprint([open(p).read() for p in glob.glob('{STATE}/*')])\nEOF")


# ------------------------------------------------------------------ the twins: each takes away one half of the pair, or reads the body as data
@pytest.mark.parametrize("cmd", [
    hd("python3 -", BODY.replace(STATE, "/tmp/data")),                           # no state folder
    hd("python3 -", f"D = '{STATE}'\nprint(D, 'a*b')"),                          # a glob word that cannot match the key name
    hd("python3 -", f"D = '{STATE}'\nprint(D)"),                                 # no glob at all
    hd("cat > tests/t.py"),                                                      # W-71: a heredoc to a literal file, nothing else on the line
    hd("cat >> tests/t.py"),
    hd("tee tests/t.py"),
])
def test_the_twins_are_not_blocked(cmd):
    assert not bash(cmd), cmd


def test_the_write_tool_is_the_remedy():
    assert not si10("Write", {"file_path": "/tmp/case/tests/t.py", "content": BODY})
    assert not si10("Edit", {"file_path": "/tmp/case/tests/t.py", "old_string": "x", "new_string": BODY})
