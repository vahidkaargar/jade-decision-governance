"""W-82 (R-13 residual): a copy into a protected DIRECTORY, a glob or a variable.

R-13 read only the literal protected path a command wrote. `cp /tmp/e/settings.json ~/.claude/` writes `~/.claude/settings.json` (the hook wiring), but the words say
`~/.claude/`, so it was low. Pinned here, one true positive and its false-positive twin each:

1. a directory destination (a trailing slash, `.`, an existing directory, `-t DIR`, the directory the command runs in): the path a copy lands at is the directory plus the
   TYPED name of each source, and that path is read like a typed one (`cp`, `mv`, `rsync`, `install`, `ln`);
2. a glob destination: the paths it matches now, as the shell expands it;
3. a variable that an earlier part of the same command set to a literal (R-13 only, never another rule).

What is NOT decided here, and so stays as it was (UNDECIDED_STAYS_LOW): a source whose name the gate cannot know (a glob, a variable, a tree, an archive) copied into a
directory made for protected files. Whether `cp *.json ~/.claude/` or `tar -xf a.tar -C ~/.claude` should be high weighs false positives (`cp -r ~/.claude ~/backup/`,
`cp $f ~/.config/`) and is the owner's call (the `next_action` of W-82). Each is pinned low so that a decision flips it on purpose.

All paths are synthetic and live under a throwaway HOME.
"""
import time

import pytest

from jade import policy
from jade.util import LEVELS


@pytest.fixture
def home(tmp_path, monkeypatch):
    for rel in (".claude/hooks", ".claude/projects", ".config/jade", ".local/bin", ".ssh", "proj", "proj/policies", "proj/.vscode", "docs", "src/pack/.claude"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude/settings.json").write_text("{}")
    (tmp_path / "src/pack/.claude/settings.json").write_text("{}")
    (tmp_path / "src/pack/notes.md").write_text("n")
    monkeypatch.setenv("HOME", str(tmp_path))
    policy.reset_cache()
    return tmp_path


def sh(cmd, cwd="/tmp/case"):
    return policy.assess("Bash", {"command": cmd}, cwd)


def is_r13(a):
    return a.rule_id == "R-13" and a.level == "high"


def gated(a):
    return LEVELS[a.level] >= LEVELS["high"] or bool(a.blocked)


# ---- (1) a directory destination and the typed name of each source ------------------------------------------------------------
DIR_DEST = [
    "cp /tmp/e/settings.json ~/.claude/",
    "cp settings.json ~/.claude/",
    "cp /tmp/e/settings.json ~/.claude",                      # an existing directory
    "cp /tmp/e/settings.local.json ~/.claude/",
    "cp /tmp/e/{settings,x}.json ~/.claude/",                  # a brace list is typed names: the shell reads them as words
    "cp /tmp/e/settings.json $HOME/.claude/",
    "cp /tmp/e/settings.json ${HOME}/.claude/",
    "cp /tmp/e/settings.json ~/.claude//",
    "cp /tmp/e/settings.json ~/.claude/../.claude/",
    "cp /tmp/e/settings.json ~/.claude/./",
    "cp /tmp/e/CLAUDE.md ~/.claude/",
    "cp /tmp/e/CLAUDE.md ~/proj/",
    "cp /tmp/e/CLAUDE.md ~/proj",
    "cp /tmp/e/AGENTS.md ~/proj/",
    "cp /tmp/e/.mcp.json ~/proj/",
    "cp /tmp/e/tasks.json ~/proj/.vscode/",
    "cp /tmp/e/settings.json ~/proj/.vscode",
    "cp /tmp/e/risk.yaml ~/proj/policies/",
    "cp /tmp/e/authorized_keys ~/.ssh/",
    "cp /tmp/e/jade ~/.local/bin/",
    "cp /tmp/e/jev ~/.local/bin",
    "cp /tmp/e/jade ~/.config/",
    "cp /tmp/e/x.py ~/.config/jade/",
    "cp -r /tmp/e/hooks ~/.claude/",                          # a tree called hooks
    "cp -r /tmp/e/pack ~/proj/.claude/hooks",                 # the copy becomes the hooks directory
    "cp -a /tmp/e/settings.json /tmp/e/other.txt ~/.claude/",
    "mv /tmp/e/settings.json ~/.claude/",
    "ln -s /tmp/e/settings.json ~/.claude/",
    "ln -sf /tmp/e/settings.json ~/.claude",
    "install /tmp/e/settings.json ~/.claude/",
    "install -m 644 /tmp/e/settings.json ~/.claude/",
    "rsync -a /tmp/e/settings.json ~/.claude/",
    "rsync -av /tmp/e/CLAUDE.md ~/proj/",
    "cp -t ~/.claude/ /tmp/e/settings.json",
    "cp -t ~/.claude /tmp/e/settings.json",
    "cp --target-directory=~/.claude /tmp/e/settings.json",
    "cp --target-directory ~/.claude /tmp/e/settings.json",
    "install -t ~/.claude /tmp/e/settings.json",
    "mv -t ~/.claude/ /tmp/e/settings.json",
    "sudo cp /tmp/e/settings.json ~/.claude/",
    "cp /tmp/e/settings.json ~/.claude/ 2>/dev/null",
    "cp /tmp/e/settings.json ~/.claude/ && echo ok",
    "true; cp /tmp/e/settings.json ~/.claude/",
]


@pytest.mark.parametrize("cmd", DIR_DEST)
def test_a_copy_into_a_protected_directory_is_r13(home, cmd):
    assert is_r13(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


@pytest.mark.parametrize("cmd", [
    "cd ~/.claude && cp /tmp/e/settings.json .",
    "cd ~/.claude; cp /tmp/e/settings.json ./",
    "cd ~/proj && cp /tmp/e/CLAUDE.md .",
    "cd ~/.claude/projects && cp /tmp/e/settings.json ..",
])
def test_the_directory_the_command_runs_in_counts(home, cmd):
    assert is_r13(sh(cmd)), (cmd, sh(cmd).rule_id)


def test_cwd_given_to_the_hook_counts(home):
    assert is_r13(sh("cp /tmp/e/settings.json .", str(home / ".claude")))
    assert is_r13(sh("cp /tmp/e/CLAUDE.md .", str(home / "proj")))
    assert is_r13(sh("mv /tmp/e/settings.json .vscode", str(home / "proj")))
    assert not gated(sh("cp /tmp/e/notes.md .", str(home / ".claude")))
    assert not gated(sh("mv notes.txt .vscode", str(home / "proj")))


# ---- (2) a glob destination -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "cp /tmp/e/x ~/.claude/set*",
    "cp /tmp/e/x ~/.claude/*.json",
    "cp /tmp/e/x ~/.claude/s*.json",
    "cp /tmp/e/x ~/.claude/settings.jso?",
    "cp /tmp/e/x ~/.claude/[s]ettings.json",
    "cp /tmp/e/x ~/.c*/settings.json",
    "cp /tmp/e/x ~/.cl*/hooks",
    "cp /tmp/e/x ~/.claude/hook*",
    "cp /tmp/e/settings.json ~/.cl*/",
    "cp /tmp/e/settings.json ~/.cl*",
    "mv /tmp/e/x ~/.claude/*",
    "rsync -a /tmp/e/x ~/.claude/set*",
])
def test_a_glob_destination_is_what_it_matches_now(home, cmd):
    assert is_r13(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


# ---- (3) a variable an earlier part of the command set ----------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "D=~/.claude; cp /tmp/e/settings.json $D/",
    "D=~/.claude; cp /tmp/e/settings.json \"$D\"",
    "D=~/.claude; cp /tmp/e/settings.json ${D}",
    "D=~/.claude && cp /tmp/e/settings.json $D",
    "D=~/.claude/settings.json; cp /tmp/e/x $D",
    "D=~/.claude; cp /tmp/e/x $D/settings.json",
    "export D=$HOME/.claude && cp /tmp/e/settings.json ${D}/",
    "export D=\"$HOME/.claude\"; cp /tmp/e/settings.json \"$D/\"",
    "A=~; B=$A/.claude; cp /tmp/e/settings.json $B/",
    "D=~/proj; cp /tmp/e/CLAUDE.md $D/",
    "F=settings.json; cp /tmp/e/$F ~/.claude/",
    "F=settings.json; D=~/.claude; cp /tmp/e/$F $D",
    "D=~/.claude; mkdir -p /tmp/x; cp /tmp/e/settings.json $D/",
    "D=~/.claude; false && D=/tmp/o; cp /tmp/e/settings.json $D/",      # a branch may not have run: every kept value is read
    "D=/tmp/o; D=~/.claude; cp /tmp/e/settings.json $D/",
    "D=/tmp/o; if x; then D=~/.claude; fi; cp /tmp/e/settings.json $D/",
])
def test_a_variable_set_in_the_same_command_is_read(home, cmd):
    assert is_r13(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


def test_the_last_values_are_kept_not_the_first(home):
    pad = "; ".join(f"D=/tmp/p{i}" for i in range(20))
    assert is_r13(sh(f"D=~/.claude; {pad}; D=~/.claude; cp /tmp/e/settings.json $D/"))
    assert not gated(sh(f"D=~/.claude; {pad}; cp /tmp/e/settings.json $D/"))   # the real value is the last one: a place that is not protected


@pytest.mark.timing
def test_a_value_that_grows_is_bounded(home):
    steps = ["A=aaaaaaaaaaaaaaaa"]
    prev = "A"
    for i in range(1, 16):
        steps.append(f"B{i}=" + f"${prev}" * 8)
        prev = f"B{i}"
    t = time.perf_counter()
    a = sh("; ".join(steps) + f"; cp /tmp/e/x ${prev}/ ; D=~/.claude; cp /tmp/e/settings.json $D/")
    assert is_r13(a)   # the growing variable is dropped, the one after it is still read
    assert time.perf_counter() - t < 20


# ---- the false-positive twins: all stay as low as main rated them -----------------------------------------------------------
STAYS_LOW = [
    "cp /tmp/e/x ~/.claude/",                                  # a name no protected pattern holds
    "cp /tmp/e/notes.md ~/.claude/",
    "cp /tmp/e/x ~/.claude",
    "cp /tmp/e/x ~/.claude/projects/",
    "cp -r /tmp/x ~/.claude",                                  # a tree called x, into a directory that is there: `~/.claude/x`, no trailing slash
    "cp -rf /tmp/x ~/.claude",
    "cp -r /tmp/x ~/.claude/",
    "mv x ~/.claude",
    "rsync -a /tmp/x ~/.claude",
    "cp -r /tmp/e/ ~/proj/",
    "cp /tmp/e/settings.json /tmp/",                           # a name a protected path holds, in a directory that is not one
    "cp /tmp/e/settings.json ~/docs/",
    "cp /tmp/e/settings.json ~/proj/",
    "cp /tmp/e/settings.json ~/proj/b.json",
    "cp /tmp/e/CLAUDE.md.bak ~/proj/",
    "cp /tmp/e/MYCLAUDE.md ~/proj/",
    "cp ~/.claude/settings.json /tmp/",                        # a read of a protected file
    "cp ~/.claude/settings.json /tmp/b",
    "cp ~/.claude/settings.json ~/docs/",
    "cp /tmp/a /tmp/b",
    "cp -r ~/src/pack ~/proj/",
    "cp ~/src/pack/notes.md ~/.claude/",
    "cp /tmp/e/x ~/.claude/zzz*",                              # a glob that matches nothing the rule names
    "cp /tmp/e/x ~/proj/*.md",
    "cp /tmp/e/x ~/*/settings.json",                           # `*` never matches a name that starts with a dot, in bash and zsh alike
    "cp /tmp/e/x /*/*/zzzzz*",
    "cd /tmp && cp /tmp/e/settings.json .",
    "cd ~/.claude/projects && cp /tmp/e/x .",
    "D=/tmp/o; cp /tmp/e/settings.json $D/",
    "D=~/.claude; cp /tmp/e/x $D/",
    "D=~/.claude; cp /tmp/e/x $D/y.txt",
    "D=~/.claude; ls $D/",
    "ls ~/.claude/",
    "cat ~/.claude/settings.json",
    "echo x > /tmp/f",
    "rm ~/.claude/projects/old",
    "tar -cf out.tar -C ~/.claude .",                          # create: the archive is written, the directory is read
    "tar -tf a.tar -C ~/.claude",
    "unzip -l a.zip -d ~/.claude",
]


@pytest.mark.parametrize("cmd", STAYS_LOW)
def test_the_false_positive_twin_stays_low(home, cmd):
    a = sh(cmd)
    assert not gated(a), (cmd, a.rule_id, a.level)


# What the owner has not decided (see the docstring): a name that is not typed. Low as on main.
UNDECIDED_STAYS_LOW = [
    "cp *.json ~/.claude/",
    "cp s* ~/.claude/",
    "cp * ~/.claude/",
    "cp /tmp/e/* ~/.claude/",
    "cp $F ~/.claude/",
    "cp $(ls /tmp/e) ~/.claude/",
    "cp -r /tmp/e/. ~/.claude/",
    "rsync -a /tmp/e/ ~/.claude/",
    "cp -r /tmp/e/.claude ~/proj/",
    "cp -r ~/.claude ~/docs/",
    "mv /tmp/e/.claude ~/proj/",
    "cp -r ~/src/pack/.claude/ ~/.claude/",
    "tar -xf a.tar -C ~/.claude",
    "tar -C ~/.claude -xf a.tar",
    "tar xf a.tar -C ~/.claude",
    "unzip a.zip -d ~/.claude",
    "cd ~/.claude && tar xf a.tar",
    "D=~/.claude; cp *.json $D/",
    "D=~/.claude; tar -xf a.tar -C $D",
]


@pytest.mark.parametrize("cmd", UNDECIDED_STAYS_LOW)
def test_an_untyped_name_is_left_as_main_rated_it(home, cmd):
    assert not gated(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


def test_a_dir_dest_reading_only_adds_never_lowers(home):
    """A command that was already high stays at least that high, whatever the rest says."""
    for cmd in ("cp x ~/.claude/settings.json", "cp x ~/.claude/hooks/", "cp /tmp/e/settings.json ~/.claude/ && git push --force",
                "cp /tmp/e/x ~/.claude/ ; rm -rf ~/work"):
        assert gated(sh(cmd)), cmd


def test_it_reads_one_rule_only(home):
    """The directory reading is R-13's: the other path rules (R-16 and R-18) keep what they had."""
    assert sh("cp *.py jade/").rule_id != "R-13"
    assert sh("cp /tmp/e/n.md ~/.claude/skills/").rule_id == "R-18"


def test_a_pile_of_operands_hides_nothing_and_adds_nothing(home):
    srcs = " ".join(f"/tmp/e/f{i}.txt" for i in range(400))
    assert not gated(sh(f"cp {srcs} ~/.claude/"))
    assert is_r13(sh(f"cp {srcs} /tmp/e/settings.json ~/.claude/"))   # padding in front must not push the name out of view
    assert not gated(sh("cp " + " ".join(f"s{i}*" for i in range(400)) + " /tmp/x/"))


def test_a_glob_with_a_hit_past_many_matches_is_still_read(home):
    for i in range(300):
        (home / "docs" / f"a{i}.txt").write_text("x")
    (home / ".claude" / "zz.txt").write_text("x")
    assert is_r13(sh("cp /tmp/e/x ~/.claude/*"))
    assert not gated(sh("cp /tmp/e/x ~/docs/*"))
