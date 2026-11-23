"""W-82 (R-13 residual): a copy into a protected DIRECTORY, a glob or a variable.

R-13 read only the literal protected path a command wrote. `cp /tmp/e/settings.json ~/.claude/` writes `~/.claude/settings.json` (the hook wiring), but the words say
`~/.claude/`, so it was low. Pinned here, one true positive and its false-positive twin each:

1. a directory destination (a trailing slash, `.`, an existing directory, `-t DIR`, the directory the command runs in): the path a copy lands at is the directory plus the
   TYPED name of each source, and that path is read like a typed one (`cp`, `mv`, `rsync`, `install`, `ln`);
2. a glob destination: the paths it matches now, as the shell expands it;
3. a variable that an earlier part of the same command set to a literal (R-13 only, never another rule).

4. a source whose name the gate cannot know (a glob, a variable, a tree's contents, an archive, what `xargs` or `find -exec` feeds) copied into a directory made for
   protected files (`.claude`, `.vscode`, `.cursor`, `.codex`, `.ssh`, `policies`, `.ai`): R-13 high, by the owner's decision of 2026-11-20. The same copy into
   `~/.local/bin`, `~/.config`, an ordinary project directory or a directory inside a dedicated one stays as main rated it. Accepted false positive: the backup
   `cp -r ~/.claude ~/docs/` (it does write `~/docs/.claude/settings.json`, which typed is R-13 on main).

All paths are synthetic and live under a throwaway HOME.
"""
import glob
import os
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


@pytest.mark.parametrize("cmd", [
    "mkdir -p ~/newproj/.claude && cp /tmp/e/settings.json ~/newproj/.claude/",
    "mkdir -p ~/np && cp /tmp/e/CLAUDE.md ~/np/",
    "mkdir -p ~/newproj/.claude && mv /tmp/e/settings.json ~/newproj/.claude/",
    "mkdir -p ~/newproj/.claude && install -m 644 /tmp/e/settings.json ~/newproj/.claude/",
    "mkdir -p ~/newproj/.claude && cp -t ~/newproj/.claude /tmp/e/settings.json",
    "mkdir -p ~/newproj/.claude && cp /tmp/e/x.txt /tmp/e/settings.json ~/newproj/.claude/",
    "cp /tmp/e/settings.json ~/newproj/.claude/",
])
def test_a_typed_name_into_a_directory_that_does_not_exist_yet_is_read(home, cmd):
    """A destination that ends in `/` (or a `-t DIR`) is a directory even when it is not there yet (the same command makes it): the typed name lands in it, as
    `cp *.json ~/newproj/.claude/` (a name the gate cannot see) is already read."""
    assert not (home / "newproj").exists() and not (home / "np").exists()
    assert is_r13(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


@pytest.mark.parametrize("cmd", [
    "cp /tmp/e/x ~/.claude/set*/",
    "cp /tmp/e/x ~/.claude/set*",
    "cp /tmp/e/x ~/.claude/*/",
    "cp /tmp/e/settings.json ~/.cl*/",
    "cp -t ~/.cl*/ /tmp/e/settings.json",
])
def test_a_glob_destination_typed_with_a_slash_is_still_what_it_matches(home, cmd):
    """The `/` put back for a directory that is not there yet must not change what a glob destination matches: `~/.claude/set*/` still stands for the file
    `settings.json` it matches now, as without the `/`."""
    assert is_r13(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


@pytest.mark.parametrize("cmd", [
    "mkdir -p ~/np && cp /tmp/e/x.txt ~/np/",
    "mkdir -p ~/newproj/.claude && cp /tmp/e/x.txt ~/newproj/.claude/",
    "cp /tmp/e/settings.json ~/newproj/conf/",
    "cp /tmp/e/settings.json ~/newproj/.claude",   # no `/` and not there: the copy is a file named `.claude`
])
def test_the_twin_of_a_typed_name_into_a_new_directory_stays_low(home, cmd):
    assert not gated(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


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


# ---- (4) a name the gate cannot see, into a DEDICATED protected directory (owner decision 2026-11-20) -------------------------
# A glob or variable source, the contents of a tree, an archive: the names that land are not in the words. When the directory they land in is one made for protected
# files (its own name is `.claude`, `.vscode`, `.cursor`, `.codex`, `.ssh`, `policies` or `.ai`), the copy is R-13 high. Not `~/.local/bin`, not `~/.config`, not an
# ordinary project directory: there a name that is not typed stays as main rated it.
UNSEEN_INTO_DEDICATED = [
    "cp *.json ~/.claude/",
    "cp s* ~/.claude/",
    "cp * ~/.claude/",
    "cp /tmp/e/* ~/.claude/",
    "cp $F ~/.claude/",
    "cp \"$F\" ~/.claude",
    "cp $(ls /tmp/e) ~/.claude/",
    "cp -r /tmp/e/. ~/.claude/",
    "rsync -a /tmp/e/ ~/.claude/",
    "cp -r /tmp/e/.claude ~/proj/",                         # the tree becomes ~/proj/.claude: what is in it is not typed
    "cp -r ~/.claude ~/docs/",                              # the backup twin: it writes ~/docs/.claude/settings.json, as `cp x ~/docs/.claude/settings.json` (R-13 on main)
    "mv /tmp/e/.claude ~/proj/",
    "cp -r ~/src/pack/.claude/ ~/.claude/",
    "cp -r /tmp/e/cfg ~/proj/.cursor",                      # a tree copied to a name that is not there yet becomes that directory
    "tar -xf a.tar -C ~/.claude",
    "tar -C ~/.claude -xf a.tar",
    "tar xf a.tar -C ~/.claude",
    "tar -xzf a.tgz -C ~/.ssh",
    "curl -s https://example.invalid/a.tgz | tar xz -C ~/.claude",
    "unzip a.zip -d ~/.claude",
    "unzip -o a.zip -d ~/proj/.vscode",
    "cd ~/.claude && tar xf a.tar",
    "cd ~/proj/policies && unzip a.zip",
    "D=~/.claude; cp *.json $D/",
    "D=~/.claude; tar -xf a.tar -C $D",
    "cp *.json ~/proj/.vscode/",
    "cp *.yaml ~/proj/policies/",
    "cp *.toml ~/proj/.codex/",
    "cp /tmp/e/* ~/proj/.ai/",
    "cp /tmp/e/* ~/.ssh/",
    "cp -t ~/.claude *.json",
    "cp -t ~/proj/.vscode /tmp/e/*",
    "install -m 600 /tmp/e/* ~/.ssh/",
    "ln -s /tmp/e/* ~/.claude/",
    "mv /tmp/e/* ~/.claude/",
    "rsync -a /tmp/e/* ~/.claude",
    "ditto /tmp/e ~/.claude",
    "cp *.json ~/.CLAUDE/",                                 # the file system is case-insensitive on macOS, as R-13 reads it
    "cp /tmp/e/* ~/.cl*/",                                  # a glob destination: what it matches now
    "for f in *.json; do cp \"$f\" ~/.claude/; done",
    "find /tmp/e -name '*.json' -exec cp {} ~/.claude/ \\;",
    "find /tmp/e -type f -exec cp {} ~/.ssh/ +",
    "ls /tmp/e | xargs cp -t ~/.claude",
    "ls /tmp/e | xargs -I{} cp {} ~/.ssh/",
    "ls /tmp/e | xargs -I % cp % ~/proj/.vscode/",
    "find /tmp/e -name '*.json' -print0 | xargs -0 -I{} cp {} ~/.claude/",
    "sudo cp /tmp/e/* ~/.claude/",
    "true; cp /tmp/e/* ~/.claude/ 2>/dev/null",
]


@pytest.mark.parametrize("cmd", UNSEEN_INTO_DEDICATED)
def test_an_unseen_name_into_a_dedicated_directory_is_r13(home, cmd):
    assert is_r13(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


@pytest.mark.parametrize("cmd", [
    "cp -r /tmp/e/.claude newdir/",
    "mkdir -p newdir && cp -r /tmp/e/.claude newdir/",
    "cp -r /tmp/e/.claude -t newdir",
    "cp -r /tmp/e/.claude --target-directory=newdir",
    "mv /tmp/e/.claude newdir/",
    "cp -r ~/.claude ~/backup_zz/",                         # the accepted backup false positive, now the same whether the directory is there or not
    "cp -r /tmp/e ~/proj/.claude/",                          # not there yet: the tree becomes ~/proj/.claude
    "mkdir -p ~/proj/.cursor && cp -r /tmp/e ~/proj/.cursor/",
])
def test_a_tree_into_a_directory_typed_as_one_but_not_there_yet_is_read(home, cmd):
    """A destination that ends in `/` or is given with `-t` is a directory by its words, also for the tree reading: a tree called `.claude` copied into it is
    `newdir/.claude` (W-82 review, wave 4: `sub/` that exists was high, `newdir/` low). A tree copied to a typed directory that is not there yet may also become it."""
    assert not (home / "proj" / "newdir").exists() and not (home / "backup_zz").exists()
    a = sh(cmd, str(home / "proj"))
    assert is_r13(a), (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", [
    "cp -r /tmp/e/.claude newdir",                           # no `/`, not there: the copy is newdir itself, an ordinary name
    "cp -r /tmp/e/cfg newdir/",
    "cp -r /tmp/e/cfg -t newdir",
    "cp -r /tmp/e ~/proj/newdir/",
    "mkdir -p ~/.claude/x && cp -r /tmp/e ~/.claude/x/",     # a directory inside a dedicated one
])
def test_the_twin_of_a_tree_into_a_new_directory_stays_low(home, cmd):
    a = sh(cmd, str(home / "proj"))
    assert not gated(a), (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", [
    "ln -s /tmp/e ~/proj/.claude",                           # the link IS a dedicated directory, and what is in it is not typed
    "ln -sf /tmp/e ~/proj/.claude",
    "ln -sfn /tmp/e ~/proj/.claude",
    "ln -s /tmp/e/.claude ~/proj/",                          # a link called .claude, made in ~/proj
    "ln -s /tmp/e/.claude ~/proj",
    "ln -s /tmp/e/.claude -t ~/proj",
    "cd ~/proj && ln -s /tmp/e/.claude",                     # one operand: the link is made in the directory the command runs in
    "ln -s /tmp/e/.claude newdir/",
    "ln -sT /tmp/e ~/proj/.cursor",
    "sudo ln -s /tmp/e ~/proj/.codex",
])
def test_a_symlink_that_makes_a_dedicated_directory_of_a_tree_is_r13(home, cmd):
    """`ln -s /tmp/e ~/proj/.claude` makes a dedicated directory whose contents the gate cannot see, as `cp -r /tmp/e ~/proj/.claude` does (W-82 review, wave 4)."""
    a = sh(cmd, str(home / "proj"))
    assert is_r13(a), (cmd, a.rule_id, a.level)


def test_a_symlink_that_replaces_a_dedicated_link_is_r13(home, tmp_path_factory):
    old = tmp_path_factory.mktemp("old")
    (home / "proj" / ".claude").symlink_to(old)
    assert is_r13(sh("ln -sfn /tmp/e ~/proj/.claude"))   # -n: the link itself is replaced, not followed
    assert is_r13(sh("ln -sfh /tmp/e ~/proj/.claude"))


@pytest.mark.parametrize("cmd", [
    "ln -s /tmp/x ~/.claude/",                               # a link called x inside ~/.claude: a typed name no protected path holds
    "ln -s /tmp/x ~/.claude",
    "ln -s ~/.claude ~/proj/cfg",                            # a link to the dedicated directory, under an ordinary name
    "ln -s /tmp/e ~/proj/x",
    "ln -s /opt/tool/bin/tool ~/.local/bin/",
    "ln -s /tmp/e ~/proj/.vscode",                           # ~/proj/.vscode is a real directory: the link is ~/proj/.vscode/e
    "ln -sfn /tmp/e ~/proj/.vscode",                         # -n changes nothing for a real directory
    "cd ~/proj && ln -s /tmp/e/x",
])
def test_the_twin_of_a_symlink_into_a_dedicated_directory_stays_low(home, cmd):
    a = sh(cmd, str(home / "proj"))
    assert not gated(a), (cmd, a.rule_id, a.level)


def test_the_hook_cwd_inside_a_dedicated_directory_counts(home):
    assert is_r13(sh("cp /tmp/e/* .", str(home / ".claude")))
    assert is_r13(sh("tar xf /tmp/a.tar", str(home / ".claude")))
    assert is_r13(sh("unzip /tmp/a.zip", str(home / "proj/.vscode")))
    assert not gated(sh("cp /tmp/e/* .", str(home / "proj")))
    assert not gated(sh("tar xf /tmp/a.tar", str(home / "docs")))


# The owner's exclusions and the near names: a name that is not typed stays low outside the dedicated directories.
UNSEEN_ELSEWHERE_STAYS_LOW = [
    "cp jad* ~/.local/bin/",
    "cp /tmp/e/* ~/.local/bin",
    "cp *.json ~/.config/",
    "cp $f ~/.config/",
    "cp -r /tmp/e/. ~/.config/",
    "tar -xf a.tar -C ~/.config",
    "cp * ~/proj/",
    "cp -r /tmp/e/. ~/proj/",
    "rsync -a /tmp/e/ ~/proj/",
    "tar -xf a.tar -C ~/proj",
    "unzip a.zip -d ~/docs",
    "cp *.json ~/.claude/projects/",                         # a directory inside one: no protected file is a direct child of it
    "cp -r /tmp/e/. ~/.claude/projects/",
    "tar -xf a.tar -C ~/.claude/projects",
    "cp *.json ~/.claude-old/",                             # a near name is not the directory
    "cp *.json ~/claude/",
    "cp *.yaml ~/proj/policies-old/",
    "cp -r /tmp/x ~/.claude",                                # a typed tree into a directory that is there: it lands at ~/.claude/x
    "mv x ~/.claude",
    "cp ~/.claude/* /tmp/",                                  # a read of a dedicated directory
    "cp -r ~/.claude/. /tmp/b/",
    "tar -cf out.tar -C ~/.claude .",
    "tar -tf a.tar -C ~/.claude",
    "unzip -l a.zip -d ~/.claude",
    "D=/tmp/o; cp *.json $D/",
    "ls ~/.claude/*",
    "ls /tmp/e | xargs cp -t ~/proj",
    "ls /tmp/e | xargs -I{} cp {} ~/.local/bin/",
    "find /tmp/e -type f -exec cp {} ~/docs/ \\;",
    "find ~/.claude -name '*.json' -exec cat {} \\;",
    "find ~/.claude -type f | xargs grep x",
]


@pytest.mark.parametrize("cmd", UNSEEN_ELSEWHERE_STAYS_LOW)
def test_an_unseen_name_elsewhere_stays_low(home, cmd):
    a = sh(cmd)
    assert not gated(a), (cmd, a.rule_id, a.level)


@pytest.mark.timing
def test_one_glob_destination_named_many_times_is_walked_once(home):
    for i in range(300):
        (home / "docs" / f"d{i}").mkdir()
    cmd = "; ".join(f"cp s{i}* ~/docs/*/" for i in range(400)) + "; " + "; ".join(f"cp a{i} ~/docs/*/" for i in range(400))
    t = time.process_time()
    a = sh(cmd)
    assert not gated(a)
    assert time.process_time() - t < 20   # each segment walked and read all 300 matches again before: minutes on a loaded machine
    assert is_r13(sh("cp /tmp/e/* ~/.cl*/"))   # a fresh command has its own budget


@pytest.mark.parametrize("cmd", [
    "7z x a.7z -o~/.claude",
    "cd ~/.claude && cpio -i < a.cpio",
    "cd ~/.ssh && pax -r -f a.pax",
    "ls /tmp/e | xargs -a list cp -t ~/.claude",
    "ls /tmp/e | xargs sudo cp -t ~/.claude",
    "find /tmp/e -type f -exec sudo cp {} ~/.claude/ \\;",
    "find /tmp/e -type f -exec /bin/cp {} ~/.ssh/ \\;",
])
def test_the_other_unpackers_and_launchers(home, cmd):
    assert is_r13(sh(cmd)), (cmd, sh(cmd).rule_id, sh(cmd).level)


def test_a_symlink_named_like_a_dedicated_directory_counts_both_ways(home, tmp_path_factory):
    other = tmp_path_factory.mktemp("dots") / "claude-config"
    other.mkdir()
    (home / "proj" / ".cursor").symlink_to(other)                 # the name the command writes is dedicated, the directory it points at is not
    assert is_r13(sh("cp *.json ~/proj/.cursor/"))
    (home / "proj" / "cfg").symlink_to(home / ".claude")            # the name is ordinary, the directory it points at is dedicated
    assert is_r13(sh("cp *.json ~/proj/cfg/"))


def test_the_glob_budget_spends_only_on_glob_destinations(home):
    for i in range(300):
        (home / "docs" / f"d{i}").mkdir()
    pad = "; ".join(f"cp a ~/docs/*/z{i}" for i in range(80))   # many matches: the budget is spent
    assert is_r13(sh(f"{pad}; cp *.json ~/.claude/"))
    assert is_r13(sh(f"{pad}; cp /tmp/e/settings.json ~/.claude/"))
    assert is_r13(sh(f"{pad}; cd ~/.claude && tar xf a.tar"))
    # The documented limit: once the budget is spent, a glob destination is read as written (as before W-82).
    assert not gated(sh(f"{pad}; cp /tmp/e/x ~/.cl*/settings.json"))


@pytest.mark.parametrize("cmd", [
    "D=/tmp/x; cp a $D/b; bash x",
    "D=/tmp/r; cp a $D/b; ./r",
    "D=/tmp/q; cp a $D/b; sh b",
    "D=/tmp/q; cp a $D/b; bash x",
])
def test_the_variable_reading_keeps_the_scripts_the_command_wrote(home, cmd):
    """The reading of `$D` must not stand in for the scripts the command wrote: a later script with a one-letter name that the reading happens to contain is
    still a script the gate cannot read (R-31, low), so the blind-spot record is kept."""
    a = sh(cmd)
    assert (a.level, a.rule_id) == ("low", "R-31"), (cmd, a.level, a.rule_id)


def test_a_variable_reading_is_read_for_r13_only(home, monkeypatch):
    """Only R-13 is taken from the reading of a variable, so the reading is not classified by every rule again: with four values per variable that made a long
    command cost 3.5 times main's CPU time (W-82 review, wave 4). Each segment is classified once, and the reading still finds R-13."""
    calls = [0]
    real = policy.classify_segment

    def counted(*args, **kwargs):
        calls[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(policy, "classify_segment", counted)
    pad = "D=/tmp/a; D=/tmp/b; D=/tmp/c; D=/tmp/d"

    def segments(cmd):   # what the scanner hands the classifier: since W-191 it holds a stage once for each value of a variable in its words
        return len(list(policy.shellnorm.segments_cwd(cmd, "/tmp/case", literal_end=len(cmd))))

    sh(f"{pad}; cp /tmp/e/x /tmp/z/y1; cp /tmp/e/x /tmp/z/y2")
    once, calls[0] = calls[0], 0   # every segment classified once, with no variable to read
    assert once == segments(f"{pad}; cp /tmp/e/x /tmp/z/y1; cp /tmp/e/x /tmp/z/y2")
    cmd = f"{pad}; cp /tmp/e/x $D/y1; cp /tmp/e/x $D/y2"
    a = sh(cmd)
    assert not gated(a) and calls[0] == segments(cmd), (calls[0], segments(cmd))   # the R-13 reading adds no classification of its own
    calls[0] = 0
    cmd = f"{pad}; D=~/.claude; cp /tmp/e/settings.json $D/; cp /tmp/e/x $D/y"
    assert is_r13(sh(cmd))
    assert calls[0] == segments(cmd), (calls[0], segments(cmd))


def test_a_variable_with_a_known_value_is_still_an_unseen_name(home):
    """Accepted false positive: the raw words `$F` are not typed, and the gate takes the higher of its readings, so a value the command set does not lower it."""
    assert is_r13(sh("F=notes.md; cp /tmp/e/$F ~/.claude/"))


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


# ---- the walk of a glob destination is bounded by the work it does, not only by the matches it returns (W-82 review, wave 4) ------
def _counting_scandir(monkeypatch):
    """Count every directory entry any glob walk reads through `os.scandir` (glob.iglob uses it too)."""
    seen = [0]
    real = os.scandir

    class Counted:
        def __init__(self, it):
            self.it = it

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.it.close()

        def __iter__(self):
            for e in self.it:
                seen[0] += 1
                yield e

        def close(self):
            self.it.close()

    monkeypatch.setattr(os, "scandir", lambda p=".": Counted(real(p)))
    return seen


def _wide_tree(root, n=10):
    for a in range(n):
        for b in range(n):
            for c in range(n):
                (root / f"a{a}" / f"b{b}" / f"c{c}").mkdir(parents=True)


def test_a_glob_destination_that_matches_nothing_does_not_walk_the_whole_tree(home, monkeypatch):
    """Three wildcard components and a literal tail that matches nothing: the walk visits every directory of the tree (main assesses it in 0.01 s, the walk
    took 3 s of CPU and 50 s of wall time under load, past the hook's budget). The entries it reads are bounded per command."""
    _wide_tree(home / "big")
    cmd = "; ".join(f"cp s* ~/big/*/*/*/n{i}/" for i in range(16))
    seen = _counting_scandir(monkeypatch)
    a = sh(cmd)
    assert not gated(a), (a.rule_id, a.level)
    assert seen[0] <= policy.DIR_GLOB_ENTRIES, seen[0]


def test_a_spent_walk_keeps_what_it_found_and_reads_the_rest_as_written(home, monkeypatch):
    _wide_tree(home / "big")
    monkeypatch.setattr(policy, "DIR_GLOB_ENTRIES", 50)
    assert is_r13(sh("cp /tmp/e/settings.json ~/.cl*/"))   # a small walk is whole
    assert not gated(sh("cp s* ~/big/*/*/*/n0/; cp /tmp/e/x ~/.cl*/settings.json"))   # the documented limit: past the budget, read as written


@pytest.mark.parametrize("pattern", [
    "{r}/*", "{r}/*/", "{r}/.*", "{r}/d*/f*", "{r}/d?/", "{r}/[d]1/f1", "{r}/*/f1", "{r}/*/*/", "{r}/d1/*", "{r}/link*/f*", "{r}/nope*/x", "{r}/d1/f1/",
    "{r}/*/.h*", "{r}/[!d]*", "{r}/d1", "{r}/*/nofile",
])
def test_the_bounded_walk_matches_what_the_shell_glob_matches(tmp_path, pattern):
    """The walk is written by hand to count its work; it must stand for the same paths as `glob.glob` (a leading dot only for a pattern that starts with one,
    a trailing `/` for directories only, symlinks to directories followed)."""
    r = tmp_path / "t"
    for d in ("d1", "d2", ".hid"):
        (r / d).mkdir(parents=True)
        (r / d / "f1").write_text("x")
        (r / d / ".h1").write_text("x")
    (r / "e1").write_text("x")
    (r / "link1").symlink_to(r / "d1")
    p = pattern.format(r=r)
    assert sorted(policy._glob_matches(p)) == sorted(glob.glob(p)), p


# ---- a glob source into a glob destination that matches a protected FILE (wave 8 fix) ------------------------------------------
@pytest.mark.parametrize("cmd", [
    "cp -R /tmp/x* ~/.claude/settings.js*", "cp -a /tmp/x* ~/.claude/settings.js*", "mv /tmp/x* ~/.claude/settings.js*", "rsync -a /tmp/x* ~/.claude/settings.js*",
    "cp -R /tmp/x* ~/proj/CLAUDE.m*", "mv /tmp/x? ~/proj/CLAUDE.m*", "mv /tmp/x[0-9] ~/proj/CLAUDE.m*", "mv /tmp/x* ~/proj/CLAUDE.*", "cp -r /tmp/x* ~/proj/CLAUDE.m*",
    "cp -R /tmp/x* ~/.claude/s*", "cp -R /tmp/x* ~/.claude/*", "cp ~ ~/proj/CLAUDE.m*", "cp -R /tmp/new.md ~/proj/CLAUDE.m*", "cp -R /tmp/x* ~/proj/CLAUDE.md",
])
def test_a_glob_source_into_a_glob_destination_that_matches_a_file_is_r13(home, cmd):
    (home / "proj" / "CLAUDE.md").write_text("x")
    assert is_r13(sh(cmd, str(home))), (cmd, sh(cmd, str(home)).rule_id, sh(cmd, str(home)).level)   # origin/main rates all of these high


@pytest.mark.parametrize("cmd", ["cp -R /tmp/x* ~/proj/NOTES.m*", "mv /tmp/x* ~/docs/*", "cp -R /tmp/x* ~/docs/"])
def test_a_glob_source_into_a_glob_destination_with_no_protected_match_stays_low(home, cmd):
    (home / "proj" / "CLAUDE.md").write_text("x")
    assert not gated(sh(cmd, str(home))), (cmd, sh(cmd, str(home)).rule_id)
