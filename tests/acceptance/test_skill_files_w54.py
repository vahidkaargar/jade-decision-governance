"""W-54 (T3): creating a skill, command or agent instruction file is high (R-18); editing an existing one, reading and mentioning stay low.

The decision is the human's (2026-11-09): the agent edits its own skills routinely, so only a NEW file needs a permit. `creates_only` makes
the rule look at what a call creates: a target that is an existing regular file is an edit. Every true positive has a false-positive twin.
All paths are synthetic and live under a throwaway HOME.
"""
import pytest

from jade import hooks, policy
from jade.util import LEVELS

EXISTING = (".claude/skills/old/SKILL.md", ".claude/commands/old.md", ".claude/agents/old.md", ".agents/old/SKILL.md", ".cursor/skills/old/SKILL.md")


@pytest.fixture
def home(tmp_path, monkeypatch):
    for rel in EXISTING:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# existing instruction file\n")
    (tmp_path / ".claude/skills/emptydir").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    policy.reset_cache()
    return tmp_path


def sh(cmd, cwd="/tmp/case"):
    return policy.assess("Bash", {"command": cmd}, cwd)


def wr(tool, path, cwd="/tmp/case"):
    return policy.assess(tool, {"file_path": path, "content": "x"}, cwd)


def gated(a):
    return LEVELS[a.level] >= LEVELS["high"] or bool(a.blocked)


def is_r18(a):
    return a.rule_id == "R-18" and a.level == "high"


# ---- creation through the file tools ---------------------------------------------------------------------------------
NEW_PATHS = ["~/.claude/skills/new/SKILL.md", "~/.claude/commands/new.md", "~/.claude/agents/new.md", "~/.agents/new/SKILL.md",
             "~/.cursor/skills/new/SKILL.md", "~/.skills-archive/new/SKILL.md", "~/.claude/skills/old/scripts/helper.py",
             "~/.claude/skills/Old/NEW.md", "~/.CLAUDE/Commands/new.md", "~/.claude/skills/new skill/SKILL.md"]


@pytest.mark.parametrize("path", NEW_PATHS)
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_creating_an_instruction_file_with_a_file_tool_is_high(home, tool, path):
    a = wr(tool, path)
    assert is_r18(a), (tool, path, a.rule_id)
    assert "instruction" in a.rationale.lower()


def test_a_relative_path_is_resolved_against_the_cwd(home):
    assert is_r18(wr("Write", "skills/new/SKILL.md", str(home / ".claude")))
    assert not gated(wr("Write", "skills/old/SKILL.md", str(home / ".claude")))


@pytest.mark.parametrize("rel", EXISTING)
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_editing_an_existing_instruction_file_stays_low(home, tool, rel):
    a = wr(tool, f"~/{rel}")
    assert a.level == "low" and a.rule_id != "R-18", (tool, rel, a.rule_id)


def test_a_symlink_to_an_existing_file_counts_as_existing(home):
    (home / ".claude/skills/alias.md").symlink_to(home / ".claude/commands/old.md")
    assert not gated(wr("Write", "~/.claude/skills/alias.md"))


def test_a_dangling_symlink_is_a_new_file(home):
    (home / ".claude/skills/dangling.md").symlink_to(home / "nowhere.md")
    assert is_r18(wr("Write", "~/.claude/skills/dangling.md"))


def test_other_claude_files_and_lookalike_names_are_not_matched(home):
    for p in ("~/.claude/projects/x/memory/note.md", "~/.claude/skillset/x.md", "~/.claude/skills-notes/x.md", "/tmp/work/skills/x.md",
              "~/Documents/my.agents/x.md", "~/docs/agents/x.md", "~/.agentsfile", "~/.claude/commands.md", "~/notes/.claude-skills/x.md"):
        assert not is_r18(wr("Write", p)), p


def test_claude_md_and_settings_are_still_r13(home):
    assert wr("Write", "~/.claude/CLAUDE.md").rule_id == "R-13"
    assert wr("Write", "~/.claude/settings.json").rule_id == "R-13"


# ---- creation through Bash ---------------------------------------------------------------------------------------------
CREATES = [
    "cp /tmp/n.md ~/.claude/skills/new/SKILL.md",
    "cp -r /tmp/pack ~/.claude/skills/",                       # into an existing directory: new content under it
    "cp /tmp/n.md ~/.claude/skills/emptydir/SKILL.md",
    "mv /tmp/n.md ~/.claude/commands/new.md",
    "install -m 644 /tmp/n.md ~/.claude/skills/new/SKILL.md",
    "ln -s /tmp/pack ~/.claude/skills/new",
    "ln -s /tmp/n.md ~/.agents/new.md",
    "rsync -a /tmp/pack/ ~/.agents/pack/",
    "cat > ~/.claude/commands/new.md <<'EOF'\nbody\nEOF",
    "printf 'x' > ~/.agents/new/SKILL.md",
    "echo x >> ~/.claude/agents/new.md",
    "echo x | tee ~/.claude/agents/new.md",
    "echo x | tee -a ~/.claude/agents/new.md",
    "touch ~/.claude/commands/new.md",
    "echo x > $HOME/.claude/commands/new.md",
    "echo x > \"$HOME/.claude/commands/new.md\"",
    "echo x > ${HOME}/.claude/commands/new.md",
    "git clone https://example.org/x.git ~/.claude/skills/x",
    "git clone --depth 1 -b main https://example.org/x.git ~/.agents/x",
    "git clone https://example.org/x.git $HOME/.claude/skills/x",
    "cd ~/.claude/skills && git clone https://example.org/x.git",
    "git -C ~/.claude/skills clone https://example.org/x.git",
    "curl -o ~/.claude/skills/new/SKILL.md https://example.org/s",
    "curl -fsSL https://example.org/s -o ~/.claude/commands/new.md",
    "wget -O ~/.claude/commands/new.md https://example.org/s",
    "tar -xzf /tmp/a.tgz -C ~/.claude/skills/",
    "unzip /tmp/a.zip -d ~/.agents/",
    "cd ~/.claude/commands && echo x > new.md",
    "cd ~/.claude/skills && cp /tmp/n.md new.md",
    "cd ~/.claude && mkdir -p skills/n && cp /tmp/n.md skills/n/SKILL.md",
    "cd /tmp && cp n.md ~/.claude/agents/new.md",
    "/bin/cp /tmp/n.md ~/.claude/agents/new.md",
    "sudo cp /tmp/n.md ~/.claude/agents/new.md",
    "ls && cp /tmp/n.md ~/.claude/agents/new.md",
    "cp /tmp/n.md ~/.claude/skills//new/../new.md",
]


@pytest.mark.parametrize("cmd", CREATES)
def test_bash_that_creates_an_instruction_file_is_high(home, cmd):
    a = sh(cmd, str(home))
    assert is_r18(a), (cmd, a.rule_id, a.level)


def test_a_python_program_that_writes_a_new_skill_file_is_high(home):
    body = f"open('{home}/.claude/commands/new.md', 'w').write('x')"
    assert is_r18(sh(f"python3 - <<'EOF'\n{body}\nEOF", str(home)))


def test_a_python_program_that_edits_an_existing_skill_file_is_low(home):
    body = f"open('{home}/.claude/commands/old.md', 'a').write('x')"
    assert not gated(sh(f"python3 - <<'EOF'\n{body}\nEOF", str(home)))


# ---- the false-positive twins: edit, read, grep, ls, mention ------------------------------------------------------------
STAYS_LOW = [
    "sed -i '' s/a/b/ ~/.claude/skills/old/SKILL.md",
    "cat >> ~/.claude/skills/old/SKILL.md <<'EOF'\nmore\nEOF",
    "cat > ~/.claude/commands/old.md <<'EOF'\nreplacement\nEOF",
    "printf x >> ~/.claude/commands/old.md",
    "echo x | tee -a ~/.agents/old/SKILL.md",
    "cp /tmp/n.md ~/.claude/skills/old/SKILL.md",              # overwrites an existing file
    "cd ~/.claude/skills/old && cp /tmp/n.md SKILL.md",
    "cd ~/.claude/commands && echo x >> old.md",
    "touch ~/.claude/commands/old.md",
    "rm ~/.claude/skills/old/SKILL.md",                         # deletion is not creation
    "rm -r ~/.claude/skills/old/",
    "rm ~/.claude/commands/never-existed.md",
    "chmod +x ~/.claude/skills/old/SKILL.md",
    "cat ~/.claude/skills/new/SKILL.md",
    "head -20 ~/.agents/new/SKILL.md",
    "grep -rn foo ~/.claude/skills",
    "grep -n skills ~/.claude/skills/old/SKILL.md",
    "rg foo ~/.claude/commands",
    "ls ~/.claude/skills",
    "ls -la ~/.agents/new",
    "find ~/.claude/skills -name '*.md'",
    "diff ~/.claude/skills/old/SKILL.md /tmp/b.md",
    "wc -l ~/.claude/commands/old.md",
    "cd ~/.claude/skills",
    "cp ~/.claude/skills/old/SKILL.md /tmp/backup.md",          # the destination is elsewhere
    "cp -r ~/.claude/skills/old /tmp/backup",
    "mv ~/.claude/skills/old/SKILL.md /tmp/removed.md",         # a rename out of the directory creates nothing in it
    "mv /tmp/n.md ~/.claude/skills/old/SKILL.md",               # replaces an existing file
    "mkdir -p ~/.claude/skills/new",                            # a directory is not an instruction file
    "git add ~/.claude/skills/new/SKILL.md",
    "git commit -m 'add ~/.claude/skills/new/SKILL.md'",
    "git status ~/.claude/skills",
    "git clone https://example.org/x.git /tmp/work/x",
    "git clone https://example.org/x.git ~/Documents/projects/x",
    "git log -p -- ~/.claude/skills/old/SKILL.md",
    "echo 'cp /tmp/n.md ~/.claude/skills/new/SKILL.md' > /tmp/notes.md",
    "echo 'skills live in ~/.claude/skills/ and ~/.agents/' >> /tmp/notes.md",
    "cat > /tmp/notes.md <<'EOF'\ncp /tmp/n.md ~/.claude/skills/new/SKILL.md\nwrite ~/.agents/x/SKILL.md\nEOF",
    "python3 - <<'EOF'\nprint('would write ~/.claude/commands/new.md')\nEOF",
    "curl -o /tmp/n.md https://example.org/s",
    "curl -s https://example.org/s?path=~/.claude/skills/new",
    "skillfind 'open vault password'",
    "jade check",
    "uv run pytest -q tests/test_skills_catalog.py",
]


@pytest.mark.parametrize("cmd", STAYS_LOW)
def test_edit_read_grep_ls_and_mention_stay_low(home, cmd):
    a = sh(cmd, str(home))
    assert a.rule_id != "R-18" and not gated(a), (cmd, a.rule_id, a.level, a.blocked)


def test_a_quoted_path_in_a_commit_message_or_heredoc_is_data(home):
    for cmd in ("git commit -m \"document the skills in ~/.claude/skills/new/SKILL.md\"",
                "git commit -F - <<'EOF'\nadd ~/.claude/commands/new.md\nEOF",
                "echo done > /tmp/log <<'EOF'\n~/.agents/new/SKILL.md\nEOF"):
        assert not gated(sh(cmd, str(home))), cmd


# ---- end to end through the hook ------------------------------------------------------------------------------------------
def ev(tool, tin):
    return {"tool_name": tool, "tool_input": tin, "session_id": "sess-54545454", "cwd": "/tmp/case"}


def test_the_hook_blocks_a_new_skill_and_passes_an_edit_of_an_existing_one(home):
    assert hooks.pretool(ev("Write", {"file_path": str(home / ".claude/skills/n/SKILL.md"), "content": "x"})).exit_code == 2
    assert hooks.pretool(ev("Write", {"file_path": str(home / ".claude/skills/old/SKILL.md"), "content": "x"})).exit_code == 0
    assert hooks.pretool(ev("Edit", {"file_path": str(home / ".claude/commands/old.md"), "old_string": "a", "new_string": "b"})).exit_code == 0
    assert hooks.pretool(ev("Bash", {"command": f"cp /tmp/n.md {home}/.agents/n.md"})).exit_code == 2
    assert hooks.pretool(ev("Bash", {"command": f"cat {home}/.agents/old/SKILL.md"})).exit_code == 0
    assert hooks.pretool(ev("Bash", {"command": f"git clone https://example.org/x.git {home}/.claude/skills/x"})).exit_code == 2


# ---- the rule itself -------------------------------------------------------------------------------------------------------
def test_the_rule_is_shipped_as_creates_only_and_files_only():
    r = next(r for r in policy.risk()["rules"] if r["id"] == "R-18")
    assert r["level"] == "high" and r["files_only"] is True and r["creates_only"] is True
    assert "R-18" in policy.PATH_RULES


def test_an_mcp_tool_that_writes_a_new_skill_path_is_high_and_an_existing_one_is_not_raised_by_r18(home):
    new = policy.assess("mcp__x__write_file", {"path": str(home / ".claude/skills/n/SKILL.md"), "content": "x"}, "/tmp/case")
    assert is_r18(new) or new.rule_id == "M-03"   # M-03 (high) already names every file write through an MCP server
    assert LEVELS[new.level] >= LEVELS["high"]


# ---- review round 2: over-gating, redirects, option spellings, URLs and the cwd -----------------------------------------
def agent_cwd(home):
    return str(home / ".agents/old")


GIT_AND_ARCHIVE_STAY_LOW = [
    "git -C ~/.agents/old pull --ff-only",
    "git -C ~/.agents/old checkout main",
    "git -C ~/.agents/old stash",
    "git -C ~/.agents/old status",
    "git -C ~/.agents/old diff",
    "git -C ~/.agents/old log",
    "mv ~/.claude/skills/old ~/.skills-archive/old",           # retiring a skill moves what exists; it writes no new instruction text
    "mv ~/.claude/skills/old ~/.claude/skills/old.disabled",
    "mv ~/.claude/commands/old.md ~/.claude/commands/old.md.off",
    "eza -la ~/.claude/skills",                                # a lister names a directory and writes nothing
    "zip -r /tmp/a.zip ~/.claude/skills/old",                  # the archive is the destination, the skill is only read
    "dd if=/tmp/a of=~/.claude/skills/old/SKILL.md",           # an existing file is an edit
]


@pytest.mark.parametrize("cmd", GIT_AND_ARCHIVE_STAY_LOW)
def test_git_verbs_archiving_and_listers_in_a_skill_directory_stay_low(home, cmd):
    a = sh(cmd, str(home))
    assert a.rule_id != "R-18" and not gated(a), (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", [
    "mv /tmp/n.md ~/.skills-archive/n.md",                      # a file from elsewhere is new content
    "mv /tmp/n.md ~/.claude/skills/old.disabled",
    "git -C ~/.agents clone https://example.org/x.git",
    "git clone https://example.org/x.git ~/.agents/x",
    "git -C ~/.agents/old worktree add ~/.agents/wt",
    "zip /tmp/a.zip ~/.claude/skills/old && cp /tmp/a.zip ~/.claude/skills/new.zip",
])
def test_the_creating_twins_stay_high(home, cmd):
    assert is_r18(sh(cmd, str(home))), cmd


ORDINARY_IN_A_SKILL_DIRECTORY = [
    "npm test", "npm install", "make lint", "pytest -q", "./scripts/run.sh", "bash scripts/check.sh", "python3 x.py", "uv run python x.py",
    "sed -i '' 's/a/b/' SKILL.md", "perl -pi -e 's/a/b/' SKILL.md",
    "git checkout -- SKILL.md", "git restore SKILL.md", "git pull --ff-only", "git checkout main", "git stash", "git mv SKILL.md SKILL2.md",
    "git commit -am x", "cat SKILL.md",
]


@pytest.mark.parametrize("cmd", ORDINARY_IN_A_SKILL_DIRECTORY)
def test_ordinary_commands_run_from_inside_a_skill_directory_stay_low(home, cmd):
    a = sh(cmd, agent_cwd(home))
    assert a.rule_id != "R-18" and not gated(a), (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", [
    "touch new.md", "cp /tmp/n.md new.md", "mv /tmp/n.md new.md", "echo x > new.md", "echo x >> new.md", "echo x | tee new.md",
    "cat > new.md <<'EOF'\nbody\nEOF", "install -m 644 /tmp/n.md new.md", "ln -s /tmp/n.md new.md", "git clone https://example.org/x.git",
    "curl -o new.md https://example.org/s", "wget -O new.md https://example.org/s", "mkdir -p sub && touch sub/SKILL.md",
])
def test_a_command_that_creates_a_file_in_the_skill_directory_it_runs_in_is_still_high(home, cmd):
    assert is_r18(sh(cmd, agent_cwd(home))), cmd


def test_an_existing_file_named_relative_to_the_skill_directory_is_still_an_edit(home):
    for cmd in ("cp /tmp/n.md SKILL.md", "touch SKILL.md", "echo x >> SKILL.md", "cat > SKILL.md <<'EOF'\nx\nEOF"):
        assert not gated(sh(cmd, agent_cwd(home))), cmd


REDIRECTS = ["2>/tmp/err", "2>>/tmp/err", "2> err.log", "> /tmp/log", ">> /tmp/log", "&>/tmp/log", "2>&1", "2>/dev/null", "</dev/null"]


@pytest.mark.parametrize("redirect", REDIRECTS)
@pytest.mark.parametrize("verb", ["cp /tmp/n.md", "mv /tmp/n.md", "ln -s /tmp/n.md", "install /tmp/n.md", "rsync /tmp/n.md"])
def test_a_trailing_redirect_does_not_hide_the_destination(home, verb, redirect):
    assert is_r18(sh(f"{verb} ~/.claude/skills/new.md {redirect}", str(home))), (verb, redirect)


@pytest.mark.parametrize("redirect", ["2>/tmp/err", "> /tmp/log", "&>/tmp/log"])
def test_a_trailing_redirect_does_not_hide_a_protected_destination_either(home, redirect):
    assert sh(f"cp /tmp/x ~/.claude/CLAUDE.md {redirect}", str(home)).rule_id == "R-13"
    assert not gated(sh(f"cp /tmp/n.md ~/.claude/skills/old/SKILL.md {redirect}", str(home)))


@pytest.mark.parametrize("cmd", [
    "mv -vt ~/.claude/skills/ /tmp/n.md", "mv -ft ~/.claude/skills /tmp/n.md", "mv -t ~/.claude/skills /tmp/n.md", "mv -t$HOME/.claude/skills /tmp/n.md",
    "mv --target-directory ~/.claude/skills /tmp/n.md", "mv --target-directory=$HOME/.claude/skills /tmp/n.md", "cp -vt ~/.claude/skills/ /tmp/n.md",
    "ln -sft ~/.claude/skills/ /tmp/n.md", "install -vt ~/.claude/skills/ /tmp/n.md",
])
def test_a_clustered_or_attached_target_directory_option_is_a_creation(home, cmd):
    assert is_r18(sh(cmd, str(home))), cmd


def test_a_directory_in_an_attached_option_needs_no_trailing_slash(home):
    h = str(home)
    for cmd in (f"7z x /tmp/a.7z -o{h}/.claude/skills", "7z x /tmp/a.7z -o$HOME/.claude/skills", f"unzip /tmp/a.zip -d{h}/.claude/skills",
                f"tar -xzf /tmp/a.tgz -C{h}/.claude/skills", "tar -xzf /tmp/a.tgz -C$HOME/.claude/skills", "tar -xzf /tmp/a.tgz -C $HOME/.claude/skills",
                f"wget -P{h}/.claude/skills https://example.org/s", f"cp /tmp/n.md -t{h}/.claude/skills", "cp /tmp/n.md $HOME/.claude/skills",
                "unzip /tmp/a.zip -d ${HOME}/.agents"):
        assert is_r18(sh(cmd, "/tmp/case")), cmd


def test_the_attached_option_twins_stay_low(home):
    h = str(home)
    for cmd in (f"unzip /tmp/a.zip -d{h}/tmp-out", f"tar -xzf /tmp/a.tgz -C{h}/Documents", "tar -tzf /tmp/a.tgz", "unzip -l /tmp/a.zip", "ls $HOME/.claude/skills"):
        assert not gated(sh(cmd, "/tmp/case")), cmd


@pytest.mark.parametrize("cmd", [
    "tar -xzf /tmp/a.tgz", "tar xf /tmp/a.tar", "unzip /tmp/a.zip", "curl -O https://example.org/SKILL.md", "curl -OL https://example.org/SKILL.md",
    "curl -JO https://example.org/SKILL.md", "curl -fsSLO https://example.org/SKILL.md", "wget https://example.org/SKILL.md", "wget -q https://example.org/SKILL.md",
    "wget -qO- https://example.org/s | tar -x", "7z x /tmp/a.7z", "ln -s /tmp/n.md", "cp /tmp/n.md $PWD/new.md", "cp /tmp/n.md ${PWD}/new.md",
    "echo x > $PWD/new.md",
])
def test_a_command_that_writes_into_the_cwd_without_naming_it_is_a_creation(home, cmd):
    assert is_r18(sh(cmd, str(home / ".claude/skills"))), cmd
    assert is_r18(sh(cmd, str(home / ".agents"))), cmd


def test_the_same_cwd_creators_after_a_cd_are_a_creation(home):
    for cmd in ("cd ~/.claude/skills && unzip /tmp/a.zip", "cd ~/.claude/skills && curl -O https://example.org/s", "cd ~/.claude/skills && wget https://example.org/s"):
        assert is_r18(sh(cmd, "/tmp/case")), cmd


@pytest.mark.parametrize("cmd", [
    "curl https://example.org/s", "curl -o /tmp/n.md https://example.org/s", "curl -sS https://example.org/s -o /tmp/n.md", "wget -O /tmp/n.md https://example.org/s",
    "wget -qO- https://example.org/s", "tar -tzf /tmp/a.tgz", "tar -xzf /tmp/a.tgz -C /tmp/x", "tar -czf /tmp/a.tgz .", "unzip -l /tmp/a.zip",
    "unzip -d /tmp/x /tmp/a.zip", "unzip -p /tmp/a.zip x", "ln -s /tmp/n.md /tmp/link",
])
def test_the_cwd_creator_twins_that_write_elsewhere_stay_low(home, cmd):
    assert not gated(sh(cmd, str(home / ".claude/skills"))), cmd


def test_a_directory_whose_name_ends_in_a_colon_is_not_a_url(home):
    for tool in ("Write", "Edit", "MultiEdit"):
        assert is_r18(wr(tool, "~/.claude/skills/newskill:/SKILL.md")), tool
    for cmd in ("echo hi > ~/.claude/skills/a:/SKILL.md", "touch ~/.claude/skills/a:/SKILL.md", "cp /tmp/a ~/.claude/skills/a:/SKILL.md",
                "echo hi | tee ~/.claude/skills/https:/x/SKILL.md", "cp /tmp/a ~/.claude/skills/https://x/SKILL.md"):
        assert is_r18(sh(cmd, str(home))), cmd
    assert is_r18(sh("echo hi > a:/SKILL.md", str(home / ".claude/skills")))


def test_a_real_url_that_holds_a_skills_path_is_still_not_a_creation(home):
    assert not gated(sh("git clone https://example.org/.agents/x.git /tmp/work/x", str(home)))
    for cmd in ("curl -s https://example.org/.claude/skills/x/SKILL.md", "curl -o /tmp/n.md https://example.org/.claude/skills/x.md",
                "wget -O /tmp/n.md https://example.org/.agents/x"):
        assert not gated(sh(cmd, str(home))), cmd
        assert not gated(sh(cmd, str(home / ".agents"))), cmd
