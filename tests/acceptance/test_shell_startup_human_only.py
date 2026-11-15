"""HO-shell-startup: a write to a shell start-up file or to the Claude shell snapshot is human-only (threat T31).

The agent's Bash tool runs `zsh -c` (which loads ~/.zshenv) and Claude Code sources a snapshot before each call, so a write there changes what every later
command runs. HOME and ZDOTDIR point at tmp_path: the real machine's files are never read or written.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from jade import hooks, paths, policy

CLASS = "HO-shell-startup"
REAL_SHELL_ROOTS = policy._shell_roots   # read at import, before tests/conftest.py's `isolated` fixture swaps it for one that returns ()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "userhome"
    (h / ".claude" / "shell-snapshots").mkdir(parents=True)
    (tmp_path / "etc").mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.setenv("HOME", str(h))
    for v in ("ZDOTDIR", "BASH_ENV", "ENV", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(policy, "_shell_roots", REAL_SHELL_ROOTS)   # this file opts in to the real roots
    monkeypatch.setattr(policy, "_ETC", str(tmp_path / "etc"))   # never the host's /etc/zshrc and its sourced files
    return h


def bash(cmd):
    return policy.assess("Bash", {"command": cmd}, os.environ["HOME"])


def write(path, tool="Write"):
    return policy.assess(tool, {"file_path": str(path), "content": "alias git=x", "new_string": "x", "old_string": "y"}, os.environ["HOME"])


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("name", [".zshenv", ".zshrc", ".bashrc", ".profile", ".zprofile", ".bash_profile", ".claude/shell-snapshots/snap.sh"])
def test_a_tool_write_of_a_startup_file_is_human_only(home, tool, name):
    assert CLASS in write(home / name, tool).ho_ids, name


@pytest.mark.parametrize("cmd", [
    "echo 'alias git=x' >> ~/.zshenv", "echo x | tee -a ~/.zshrc", "sed -i '' s/a/b/ ~/.zshrc", "cp x ~/.zshenv",
    "mv x ~/.claude/shell-snapshots/s.sh", "ln -sf x ~/.bashrc", "echo x > $HOME/.profile",
])
def test_a_shell_write_of_a_startup_file_is_human_only(cmd):
    assert CLASS in bash(cmd).ho_ids, cmd


def test_a_verb_that_replaces_the_snapshot_directory_is_human_only():
    assert CLASS in bash("rm -rf ~/.claude/shell-snapshots").ho_ids
    assert CLASS in bash("mv x ~/.claude").ho_ids or CLASS in bash("rm -rf ~/.claude").ho_ids


def test_zdotdir_is_followed(tmp_path, monkeypatch):
    z = tmp_path / "zd"
    z.mkdir()
    monkeypatch.setenv("ZDOTDIR", str(z))
    assert CLASS in write(z / ".zshrc").ho_ids


def test_bash_env_and_env_are_covered(tmp_path, monkeypatch):
    monkeypatch.setenv("BASH_ENV", str(tmp_path / "be.sh"))
    monkeypatch.setenv("ENV", str(tmp_path / "env.sh"))
    assert CLASS in write(tmp_path / "be.sh").ho_ids
    assert CLASS in write(tmp_path / "env.sh").ho_ids


def test_a_file_sourced_by_a_startup_file_is_covered(home):
    (home / ".cargo").mkdir()
    (home / ".zshenv").write_text('. "$HOME/.cargo/env"\nsource ~/x.sh\nsource "${HOME}/y.sh"\nsource \'$HOME/q.sh\'\n')
    assert CLASS in write(home / ".cargo" / "env").ho_ids
    assert CLASS in write(home / "x.sh").ho_ids
    assert CLASS in bash("echo x >> ~/y.sh").ho_ids
    assert CLASS not in write(home / "q.sh").ho_ids   # single quotes: the shell sources a file literally named `$HOME/q.sh`, relative


def test_a_sourced_path_built_from_another_variable_is_not_covered(home):
    (home / ".zshenv").write_text('source "$FOO/z.sh"\nsource ~/*.sh\n. $(echo q)\n')
    (home / "z.sh").write_text("")
    assert CLASS not in write(home / "z.sh").ho_ids   # documented residual (docs/05 T31)


@pytest.mark.parametrize("cmd", ["cat ~/.zshrc", "grep x ~/.zshenv", "ls ~/.claude/shell-snapshots", "cp ~/.zshrc /tmp/backup"])
def test_a_read_is_not_human_only(cmd):
    assert CLASS not in bash(cmd).ho_ids, cmd


def test_a_neighbour_file_is_not_a_startup_file(home):
    assert CLASS not in write(home / ".zshrc.bak").ho_ids
    assert CLASS not in write(home / "notes.txt").ho_ids


def test_a_fifo_startup_file_does_not_block(home):
    os.mkfifo(home / ".zshenv")
    t = time.monotonic()
    a = bash("echo x > /tmp/not-a-startup-file")
    roots = policy._shell_roots()
    assert time.monotonic() - t < 2
    assert CLASS not in a.ho_ids
    assert str(home / ".zshenv") in roots and not any(r.endswith("env") and "cargo" in r for r in roots)


def test_a_huge_startup_file_is_not_read(home):
    (home / ".zshenv").write_text("source ~/big.sh\n" + "#" * (2 << 20))
    assert CLASS not in write(home / "big.sh").ho_ids


def test_the_decision_at_medium_is_ask(home, monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    cfg = paths.config()
    cfg["level"] = "medium"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))
    ev = {"tool_name": "Write", "tool_input": {"file_path": str(home / ".zshenv"), "content": "alias git=x"}, "session_id": "sess-shellstart-0001", "cwd": str(home),
          "permission_mode": "auto"}
    assert "[jade:ask_" in hooks.pretool(ev).stderr


# ------------------------------------------------------------------ wave 2 (security review PRE-1 .. PRE-12)

@pytest.fixture
def folded(monkeypatch):
    """The comparison a case-insensitive volume needs; the default on macOS (asserted below), forced here so Linux CI runs these too."""
    monkeypatch.setattr(policy, "_case_insensitive", lambda: True)


def away(cmd):
    """The command run from a project directory outside HOME, so `~` is reached only through what the command names."""
    return policy.assess("Bash", {"command": cmd}, str(Path(os.environ["HOME"]).parent / "work"))


def test_macos_compares_paths_case_folded():
    assert policy._case_insensitive() is (sys.platform in ("darwin", "win32"))


@pytest.mark.parametrize("cmd", ["echo 'export GH_REPO=x' >> ~/.ZSHENV", "echo x >> ~/.Claude/shell-snapshots/snapshot-zsh-1.sh", "cp /tmp/x ~/.CARGO/env",
                                 "echo x > ~/.Claude/Session-Env/abc/hook-0.sh"])
def test_a_name_in_another_case_is_the_same_file(home, folded, cmd):
    (home / ".cargo").mkdir()
    (home / ".zshenv").write_text('. "$HOME/.cargo/env"\n')
    assert CLASS in away(cmd).ho_ids, cmd


def test_a_tool_write_in_another_case_is_the_same_file(home, folded):
    assert CLASS in write(home / ".ZSHRC").ho_ids
    assert CLASS in write(home / ".CLAUDE" / "Shell-Snapshots" / "s.sh", "Edit").ho_ids


def test_wiring_mod_and_settings_roots_fold_case_too(home, folded):
    assert "HO-wiring" in write(home / ".Claude" / "Hooks" / "x.sh").ho_ids
    assert "HO-wiring" in away("cp /tmp/x ~/.LOCAL/bin/JADE").ho_ids
    assert "HO-mod" in write(home / ".claude" / "Dev-Mods" / "m.js").ho_ids
    assert "HO-mod" in away("cp /tmp/m.js ~/.CLAUDE/plugins/p/x.json").ho_ids


def test_a_case_sensitive_volume_keeps_case(home, monkeypatch):
    monkeypatch.setattr(policy, "_case_insensitive", lambda: False)
    assert CLASS not in write(home / ".ZSHRC").ho_ids


@pytest.mark.parametrize("cmd", ['echo x >> "${HOME}/.zshenv"', 'echo x >> "${HOME}"/.zshrc', 'echo x >> "$HOME"/.zshrc', 'echo x >> ${HOME}/.zshrc',
                                 'echo x >> "$HOME/.zshrc"', "echo x >> $HOME/.zshrc", 'echo x | tee "${HOME}/.bashrc"'])
def test_home_in_every_quoting(cmd):
    assert CLASS in away(cmd).ho_ids, cmd


@pytest.mark.parametrize("cmd", ["echo x >> '$HOME/.zshrc'", "echo x >> '${HOME}/.zshrc'", "echo x >> '$HOME'/.zshrc"])
def test_single_quoted_home_is_literal(cmd):
    assert CLASS not in away(cmd).ho_ids, cmd   # writes `./$HOME/.zshrc` under the cwd, which is not a start-up file


def test_session_env_is_covered(home):
    (home / ".claude" / "session-env" / "abc").mkdir(parents=True)
    assert CLASS in away("echo 'export GH_REPO=x' > ~/.claude/session-env/abc/hook-0.sh").ho_ids
    assert CLASS in write(home / ".claude" / "session-env" / "abc" / "hook-0.sh").ho_ids
    assert CLASS in away("rm -rf ~/.claude/session-env").ho_ids


@pytest.mark.parametrize("cmd", [
    "cp -r /tmp/shell-snapshots ~/.claude/", "cp -R /tmp/x/. ~/.claude/", "cp -a /tmp/x ~", "cp -r /tmp/x/. ~", "cp /tmp/x/. ~/", "cp -rf /tmp/x ~/.claude",
    "cp --recursive /tmp/x ~", "cp -r -t ~ /tmp/x", "cp -rt ~ /tmp/x", "cp -r --target-directory=~ /tmp/x", "rsync -a /tmp/x/ ~/.claude/", "rsync -a /tmp/dot/ ~/", "rsync /tmp/.zshenv ~", "ditto /tmp/x ~",
    "tar -xf /tmp/a.tar -C ~", "tar xzf /tmp/a.tgz -C ~/.claude", "tar -x -f /tmp/a.tar --directory=~", "tar -xf /tmp/a.tar --directory ~",
    "cd ~ && tar -xf /tmp/a.tar", "unzip -o /tmp/a.zip -d ~", "unzip -o /tmp/a.zip -d ~/.claude", "cd ~ && unzip /tmp/a.zip", "mv /tmp/x ~",
    "cp /tmp/.zshenv ~", "install -m 644 /tmp/.zshrc ~/", "cp -r /tmp/x /", "sudo tar -xf /tmp/a.tar -C ~",
    "7z x /tmp/a.7z -o~", "cd ~ && cpio -id < /tmp/a.cpio", "cd ~ && pax -r -f /tmp/a.pax", "cd ~ && cp -r /tmp/x .",
])
def test_a_tree_copy_into_a_parent_of_a_root_is_human_only(cmd):
    assert CLASS in away(cmd).ho_ids, cmd


@pytest.mark.parametrize("cmd", ["cp -r /tmp/x ~/proj/", "rsync -a /tmp/x/ ~/proj/", "tar -xf /tmp/a.tar -C ~/proj", "unzip /tmp/a.zip -d /tmp/u",
                                 "cp /tmp/notes.txt ~", "tar -cf /tmp/a.tar -C ~ proj", "unzip -l /tmp/a.zip", "cp -r ~/.claude /tmp/backup"])
def test_a_tree_copy_elsewhere_is_not_human_only(home, cmd):
    (home / "proj").mkdir()
    assert CLASS not in away(cmd).ho_ids, cmd


@pytest.mark.parametrize("cmd", [
    "git -C ~ checkout -- .zshenv", "git --git-dir=~/.dotfiles --work-tree=~ checkout -- .zshenv", "git --work-tree=$HOME checkout -- .",
    "git --work-tree ~ checkout -- .", "GIT_WORK_TREE=~ git checkout -- .", "env GIT_WORK_TREE=$HOME git restore .", "git -C ~ reset --hard",
    "git -C ~ switch main", "git -C ~ pull", "git -C ~ merge x", "git -C ~ rebase main", "git -C ~ stash pop", "git -C ~ stash apply",
    "git -C ~ cherry-pick abc", "git -C ~ revert abc", "git -C ~ am /tmp/p.mbox", "git -C ~ apply /tmp/p.diff", "git clone /tmp/r ~/.claude",
    "git -C ~ worktree add ~/.claude/shell-snapshots", "cd ~ && git pull", "cd ~ && git stash pop", "git -C / checkout .",
    "git -C ~ checkout-index -a -f", "git -C ~ read-tree -u HEAD", "git -C ~ stash",
])
def test_git_writing_a_work_tree_that_holds_a_root_is_human_only(cmd):
    assert CLASS in away(cmd).ho_ids, cmd


@pytest.mark.parametrize("cmd", ["git -C ~ status", "git -C ~ log -1", "git -C ~/proj checkout main", "git -C ~ reset HEAD~1 --soft", "git -C ~ stash list",
                                 "cd ~/proj && git pull", "git --work-tree=~/proj checkout -- ."])
def test_git_elsewhere_or_read_only_is_not_human_only(home, cmd):
    (home / "proj").mkdir()
    assert CLASS not in away(cmd).ho_ids, cmd


def test_source_lines_anywhere_on_the_line_are_followed(home):
    h = str(home)
    (home / ".zshrc").write_text(
        f'[ -s "{h}/.bun/_bun" ] && source "{h}/.bun/_bun"\n'
        '[ -s "$HOME/.bun/b2" ] && source "$HOME/.bun/b2"\n'
        'builtin source "$HOME/herd.zsh"\n'
        'command . ~/c.sh\n'
        'if true; then source ${HOME}/t.sh; fi\n'
        'for x in 1; do . ~/d.sh; done\n'
        '{ source ~/e.sh; }\n'
        'false || source ~/f.sh\n'
        'true; . "${HOME}"/g.sh\n'
        '# source ~/commented.sh\n'
        'source "$FOO/v.sh"\n')
    for name in ("_bun", "b2"):
        assert CLASS in write(home / ".bun" / name).ho_ids, name
    for name in ("herd.zsh", "c.sh", "t.sh", "d.sh", "e.sh", "f.sh", "g.sh"):
        assert CLASS in write(home / name).ho_ids, name
    assert CLASS not in write(home / "commented.sh").ho_ids


@pytest.mark.parametrize("cmd", ["exec 3>>~/.zshenv; echo x >&3", "exec 4>~/.zshrc", "exec 5<>~/.bashrc", "exec >>~/.profile", ">>~/.zshenv", "3>>~/.zshenv echo x"])
def test_an_exec_or_bare_redirect_into_a_startup_file_is_human_only(cmd):
    assert CLASS in away(cmd).ho_ids, cmd


def test_a_zdotdir_set_in_zshenv_is_followed(home, tmp_path):
    z = home / ".config" / "zsh"
    z.mkdir(parents=True)
    (home / ".zshenv").write_text('export ZDOTDIR="$HOME/.config/zsh"\n')
    (z / ".zshrc").write_text("source ~/zd-sourced.sh\n")
    assert CLASS in write(z / ".zshrc").ho_ids
    assert CLASS in away("echo x >> ~/.config/zsh/.zprofile").ho_ids
    assert CLASS in write(home / "zd-sourced.sh").ho_ids
    (home / ".zshenv").write_text(f"ZDOTDIR={tmp_path / 'zd2'}\n")
    assert CLASS in write(tmp_path / "zd2" / ".zlogin").ho_ids


def test_claude_config_dir_adds_its_snapshot_and_session_env(home, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert CLASS in write(tmp_path / "cc" / "shell-snapshots" / "s.sh").ho_ids
    assert CLASS in write(tmp_path / "cc" / "session-env" / "x" / "hook-0.sh").ho_ids
    assert CLASS in write(home / ".claude" / "shell-snapshots" / "s.sh").ho_ids   # the default one stays


@pytest.mark.parametrize("cmd", ["source ~/.zshrc", ". ~/.zshenv", "source ~/.claude/shell-snapshots/s.sh && echo ok"])
def test_sourcing_a_startup_file_reads_it(cmd):
    assert CLASS not in away(cmd).ho_ids, cmd


def test_sourcing_with_a_redirect_into_a_root_still_counts():
    assert CLASS in away("source /tmp/x.sh >> ~/.zshrc").ho_ids
