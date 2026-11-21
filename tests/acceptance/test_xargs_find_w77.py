"""W-77 (T3, R-18 residuals): a skill, command or agent instruction file created through `xargs` or `find -exec`.

Two things are pinned. (1) What main already gates: the `{}` placeholder (and `%`, `@`, `-i`, `-J`) in a destination is a creation, because a word with `{}` in it is
a glob and a path that does not exist is a new file. (2) What this item adds: the path list that reaches `xargs` on standard input. `echo ~/.claude/skills/n.md | xargs
touch` creates the file, but the path is typed in another segment; each writer-verb `xargs` segment is now also read with the instruction-file paths the rest of the command
names put in the place of its placeholder. Every true positive has a false-positive twin. Editing an existing file stays low (the decision of W-54), and `ln -sf` over an
existing file is an edit too: that is the open decision of W-39, so it is pinned as low here and not changed. All paths are synthetic and live under a throwaway HOME.
"""
import pytest

from jade import policy
from jade.util import LEVELS

EXISTING = (".claude/skills/old/SKILL.md", ".claude/skills/old.md", ".claude/commands/old.md", ".claude/agents/old.md", ".agents/old/SKILL.md")


@pytest.fixture
def home(tmp_path, monkeypatch):
    for rel in EXISTING:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# existing instruction file\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    policy.reset_cache()
    return tmp_path


def sh(cmd, cwd="/tmp/case"):
    return policy.assess("Bash", {"command": cmd}, cwd)


def is_r18(a):
    return a.rule_id == "R-18" and a.level == "high"


def gated(a):
    return LEVELS[a.level] >= LEVELS["high"] or bool(a.blocked)


# ---- (1) already gated on main: the placeholder is in the destination ------------------------------------------------------
PLACEHOLDER_CREATES = [
    "echo new | xargs -I{} tee ~/.claude/skills/{}/note.md",
    "echo new.md | xargs -I{} touch ~/.claude/skills/{}",
    "echo new.md | xargs -I{} cp /tmp/n.md ~/.claude/commands/{}",
    "echo new.md | xargs -I% cp /tmp/n.md ~/.claude/commands/%",
    "echo new.md | xargs -I {} cp /tmp/n.md ~/.claude/commands/{}",
    "echo new.md | xargs -i cp /tmp/n.md ~/.claude/commands/{}",
    "echo new.md | xargs --replace=@ cp /tmp/n.md ~/.claude/commands/@",
    "echo new.md | xargs -J% cp /tmp/n.md ~/.claude/commands/%",
    "echo new.md | xargs -0 -I{} cp /tmp/n.md ~/.claude/commands/{}",
    "printf 'new.md\\n' | xargs -I{} sh -c 'echo x > ~/.claude/skills/{}'",
    "echo a b | xargs touch -- ~/.claude/skills/new.md",
    "xargs -I{} touch ~/.claude/skills/{} < /tmp/list",
    "find /tmp/pack -name '*.md' -exec cp {} ~/.claude/skills/ \\;",
    "find /tmp/pack -name '*.md' -exec cp {} ~/.claude/skills/new.md \\;",
    "find /tmp/pack -type f -exec cp -t ~/.claude/skills/ {} +",
    "find /tmp/pack -exec install -t ~/.agents/ {} +",
    "find /tmp/pack -exec mv {} ~/.agents/ \\;",
    "find /tmp/pack -execdir cp {} ~/.agents/ \\;",
    "find /tmp/pack -ok cp {} ~/.agents/ \\;",
    "find /tmp/x -exec ln -s {} ~/.claude/skills/ \\;",
    "find /tmp -name n -exec sh -c 'echo x > ~/.claude/skills/new.md' \\;",
    "find ~/.claude/skills -maxdepth 0 -exec touch {}/n.md \\;",
]


@pytest.mark.parametrize("cmd", PLACEHOLDER_CREATES)
def test_a_placeholder_in_the_destination_is_a_creation(home, cmd):
    assert is_r18(sh(cmd)), (cmd, sh(cmd).rule_id)


# ---- (2) new: the path arrives on standard input ---------------------------------------------------------------------------
STDIN_CREATES = [
    "echo ~/.claude/skills/n.md | xargs touch",
    "echo ~/.claude/skills/n.md | xargs -0 touch",
    "echo ~/.claude/skills/n.md | xargs -I{} touch {}",
    "echo ~/.claude/skills/n.md | xargs -I % touch %",
    "echo ~/.claude/skills/n.md | xargs -n1 touch",
    "printf '%s\\n' ~/.claude/commands/n.md | xargs -n1 tee",
    "echo ~/.claude/skills | xargs -I{} touch {}/n.md",
    "echo ~/.claude/skills/ | xargs -I{} cp /tmp/x {}n.md",
    "ls -d ~/.agents | xargs -I{} cp /tmp/x {}/n.md",
    "echo ~/.claude/skills/n.md | xargs cp /tmp/x",
    "echo ~/.claude/skills/n.md | xargs -I{} sh -c 'echo x > {}'",
    "echo ~/.claude/skills/n.md | xargs -I{} bash -c 'cat /tmp/x > {}'",
    "echo ~/.claude/agents/n.md | xargs -I{} ln -s /tmp/x {}",
    "echo ~/.cursor/skills/n.md | xargs touch",
    "echo ~/.skills-archive/n.md | xargs touch",
    "xargs touch <<< ~/.claude/skills/n.md",
    "echo $HOME/.claude/skills/n.md | xargs touch",
    "echo ${HOME}/.claude/skills/n.md | xargs touch",
    "echo ~/.claude/skills/n.md | xargs --max-args 1 touch",
    "echo ~/.claude/skills/n.md | xargs --max-args=1 --no-run-if-empty touch",
    "echo \"$HOME/.claude/skills/n.md\" | xargs touch",
    "echo '~/.claude/skills/n.md' | xargs touch",
    "printf '%s\\n' \"$HOME/.claude/commands/n.md\" | xargs -n1 tee",
    "ls -d ~/.claude/skills | xargs -P 4 -n 1 -I {} touch {}/n.md",
    "ls -d ~/.claude/skills | xargs --replace=@ touch @/n.md",
    "ls -d ~/.claude/skills | xargs -J% touch %/n.md",
    "cat /tmp/list | sort | xargs -I{} touch {} ; echo ~/.claude/skills/n.md | xargs touch",
    "sudo xargs touch <<< ~/.claude/skills/n.md",
    "echo ~/.claude/skills/n.md | /usr/bin/xargs touch",
]


@pytest.mark.parametrize("cmd", STDIN_CREATES)
def test_a_path_on_standard_input_that_a_writer_creates_is_high(home, cmd):
    assert is_r18(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


def test_the_permit_binds_the_whole_command_and_memory_never_remembers_it(home):
    from jade import memory

    for cmd in ("echo ~/.claude/skills/n.md | xargs touch", "echo ~/.claude/skills/n.md | xargs -I{} sh -c 'echo x > {}'"):
        a = sh(cmd)
        assert a.seg_rules == ["R-18"] and a.seg_keys, (a.seg_rules, a.seg_keys)
        a.subject = cmd
        assert memory.eligibility(a, "Bash", {"command": cmd}, None) == "never", cmd


def test_more_pairs_than_the_cap_is_rated_whole(home):
    paths = " ".join(f"echo ~/.claude/skills/old.md{i}; " for i in range(1)) + "".join(f"echo ~/.claude/skills/n{i}.md; " for i in range(12))
    assert is_r18(sh(paths + "xargs touch"))
    assert not gated(sh(paths + "xargs grep x"))   # not a writer


@pytest.mark.parametrize("cmd", [
    "echo ~/.claude/skills/n.md | xargs -I@ sh -c 'echo x > @'",
    "echo ~/.claude/skills/n.md | xargs -I % bash -c 'touch %'",
    "echo ~/.claude/skills/n.md | xargs -I QQ zsh -c 'cp /tmp/x QQ'",
    "echo ~/.claude/skills/n.md | xargs -i sh -c 'echo x > {}'",
])
def test_a_shell_body_with_any_replace_string_is_read(home, cmd):
    """W-77 review, wave 4: the body of `sh -c` was read only for the default `{}`, so `xargs -I@ sh -c 'echo x > @'` stayed low. `-I` replaces the string
    inside every argument (BSD and GNU), so the body holds it as written."""
    a = sh(cmd)
    assert is_r18(a), (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", [
    "echo ~/.claude/skills/n.md | xargs -I@ sh -c 'echo @ > /tmp/q'",       # the path is only read out
    "echo ~/.claude/skills/n.md | xargs -I % bash -c 'cat %'",
    "echo /tmp/n.md | xargs -I@ sh -c 'echo x > @'",                         # no instruction path anywhere
    "echo ~/.claude/skills/n.md | xargs -J % sh -c 'touch %'",               # BSD -J replaces only a whole argument, never one inside the quoted body
])
def test_the_twin_of_a_shell_body_with_a_replace_string_stays_low(home, cmd):
    a = sh(cmd)
    assert not gated(a), (cmd, a.rule_id, a.level)


# ---- twins: the same shape that creates nothing -----------------------------------------------------------------------------
STDIN_TWINS = [
    "echo ~/.claude/skills/old.md | xargs touch",                       # an existing file is an edit
    "echo ~/.claude/commands/old.md | xargs -I{} cp /tmp/x {}",
    "echo ~/.claude/skills/n.md | xargs rm",                            # a removal creates nothing
    "echo ~/.claude/skills/n.md | xargs -I{} rm -f {}",
    "echo ~/.claude/skills/n.md | xargs ls -l",                         # a lister
    "echo ~/.claude/skills/n.md | xargs cat",
    "ls ~/.claude/skills | xargs grep foo",                             # not a writer
    "ls ~/.claude/skills/ | xargs -I{} cp -r {} /tmp/backup",           # the skills directory is the source
    "ls ~/.claude/skills/ | xargs -I{} cp {} /tmp/backup/{}",
    "echo ~/.claude/skills/n.md | xargs -I{} cp {} /tmp/out",
    "git -C ~/.claude/skills ls-files | xargs sed -i s/a/b/",           # an edit of files the agent keeps
    "echo /tmp/n.md | xargs touch",                                     # no instruction path anywhere
    "echo hello | xargs touch /tmp/x",
    "ls /tmp | xargs -I{} cp {} /tmp/b",
    "echo ~/.claude/skills/n.md | xargs curl -o /tmp/x",                  # the words a fetcher is fed are addresses
    "echo ~/.claude/skills/n.md | xargs echo",
    "ls ~/.claude/skills/ | xargs -I{} cp {} /tmp/backup/{}.bak",
    "echo ~/.claude/skills/n.md | xargs -I{} sh -c 'echo {} > /tmp/q'",   # the path is only read out, the file written is /tmp/q
]


@pytest.mark.parametrize("cmd", STDIN_TWINS)
def test_the_twin_that_creates_no_instruction_file_stays_low(home, cmd):
    a = sh(cmd)
    assert not gated(a), (cmd, a.rule_id, a.level)


# ---- decided elsewhere: edits of existing files and `ln -sf` over one stay low (W-54 decision, W-39 open) -------------------
EDITS = [
    "echo x | xargs -I{} cp /tmp/x ~/.claude/skills/old.md",
    "echo x | xargs -I{} ln -sf /tmp/x ~/.claude/skills/old.md",
    "ln -sf /tmp/x ~/.claude/skills/old.md",
    "find /tmp -name y -exec ln -sf {} ~/.claude/skills/old.md \\;",
    "find /tmp -name y -exec cp {} ~/.claude/skills/old.md \\;",
    "echo x | xargs -I{} cp {} ~/.claude/skills/old/SKILL.md",
]


@pytest.mark.parametrize("cmd", EDITS)
def test_an_edit_of_an_existing_instruction_file_is_not_gated_by_decision(home, cmd):
    assert not gated(sh(cmd)), cmd


# ---- not closed: pinned as the gaps they are (docs/05 T3, "Not covered") --------------------------------------------------------
KNOWN_GAPS = [
    "find ~/.claude -maxdepth 1 -name skills -exec cp /tmp/x {}/n.md \\;",   # the directory is found at run time: nothing names it
    "xargs -a /tmp/list touch",                                              # the list is a file, nothing names a path
    "cat /tmp/list | xargs touch",
    "xargs touch < /tmp/list",
    "echo $P | xargs touch",                                                 # a path in a variable
    "echo ~/.claude/skills/n.md | xargs --replace=QQ sh -c 'touch QQ'",     # GNU `--replace=s` before a shell: the segmenter does not split out the quoted body
]


@pytest.mark.parametrize("cmd", KNOWN_GAPS)
def test_known_gap_a_path_nobody_names_is_not_seen(home, cmd):
    assert not gated(sh(cmd)), cmd


@pytest.mark.timing
def test_cost_stays_small_on_a_huge_and_a_pathological_command(home):
    """Compared with origin/main in the work log of this item (run alone): the new code reads each `xargs` once, pairs at most 8 by 8 and only when a writer `xargs`
    and an instruction path are both there. The bound here is loose, because the machine is loaded."""
    import time
    huge = " | ".join([f"echo ~/.claude/skills/n{i}.md" for i in range(300)]) + " | xargs touch"
    patho = "; ".join([f"echo ~/.claude/skills/n{i}.md | xargs touch" for i in range(300)])
    plain = "ls /tmp | xargs -I{} cp {} /tmp/b; " * 200
    for cmd, want in ((huge, True), (patho, True), (plain, False)):
        t = time.perf_counter()
        a = sh(cmd)
        assert is_r18(a) == want
        assert time.perf_counter() - t < 15, cmd[:40]
