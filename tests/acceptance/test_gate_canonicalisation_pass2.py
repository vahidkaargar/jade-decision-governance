"""Cluster A1, second pass: variants the verifier found after the first fix (F01-F03 and new defects N1-N7).

Every command is only classified, never run, except the throwaway git repositories under tmp_path.
"""

import json
import shlex
import subprocess
import time

import pytest

from jade import hooks, policy
from jade.util import LEVELS

SESSION = "s" * 8


def assess(cmd: str, cwd: str = ""):
    return policy.assess("Bash", {"command": cmd}, cwd)


def pre(cmd: str, cwd: str = "/nonexistent-case-dir", tool: str = "Bash"):
    return hooks.pretool({"tool_name": tool, "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": cwd})


def repo(path, branch: str):
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    return str(path)


def is_si08(res) -> bool:
    return res.exit_code == 2 and "safety invariant" in res.stderr and "SI-08" in res.stderr


# ---------------------------------------------------------------- F01: write operands the first pass could not see

@pytest.mark.parametrize("cmd,rule", [
    ("sort --out=/etc/pf.conf in", "R-01"),
    ("sort --outp=/etc/pf.conf in", "R-01"),
    ("sort --output /etc/pf.conf in", "R-01"),
    ("sort --o=/etc/pf.conf in", "R-01"),
    ("sort -nro /etc/pf.conf in", "R-01"),
    ("uniq -f 1 in /etc/pf.conf", "R-01"),
    ("uniq -s 2 in /etc/pf.conf", "R-01"),
    ("uniq -w 3 in ~/.claude/settings.json", "R-13"),
    ("uniq -f1 in /etc/pf.conf", "R-01"),
    ("uniq -c -f 1 -s 2 in /etc/pf.conf", "R-01"),
    ("uniq - /etc/pf.conf", "R-01"),
    ("xxd -c 8 in /etc/pf.conf", "R-01"),
    ("xxd -g 1 in /etc/pf.conf", "R-01"),
    ("xxd -s 3 in /etc/pf.conf", "R-01"),
    ("xxd -s -4 in /etc/pf.conf", "R-01"),
    ("xxd - /etc/pf.conf", "R-01"),
    ("xxd -l 4 -c 2 in ~/.claude/settings.json", "R-13"),
    ("sed 'w/etc/pf.conf' in", "R-01"),
    ("sed w/etc/pf.conf in", "R-01"),
    ('sed -n "w/etc/pf.conf" in', "R-01"),
    ("sed -e 'w /etc/pf.conf' in", "R-01"),
    ("sed --expression='w /etc/pf.conf' in", "R-01"),
    ("sed 'W /etc/pf.conf' in", "R-01"),
    ("sed 's/a/b/w/etc/pf.conf' in", "R-01"),
    ("sed -n '1w ~/.claude/settings.json' in", "R-13"),
    ("cat in | less --log-file=/etc/pf.conf", "R-01"),
    ("cat in | less --LOG-FILE /etc/pf.conf", "R-01"),
    ("cat in | less -o /etc/pf.conf", "R-01"),
    ("cd /etc && sort -o pf.conf in", "R-01"),
    ("cd /etc; sort -o pf.conf in", "R-01"),
    ("cd ~/.claude && sort -o settings.json in", "R-13"),
    ("cd ~/.claude && tee settings.json", "R-13"),
    ("cd /etc && cp x pf.conf", "R-01"),
    ("cd /etc && echo x > pf.conf", "R-01"),
    ("cd /etc && sed -i s/a/b/ pf.conf", "R-01"),
    ("cd ~ && cd .claude && sort -o settings.json in", "R-13"),
    ("pushd ~/.claude && sort -o settings.json in", "R-13"),
    ("sort -o ../../../../etc/pf.conf in", "R-01"),
])
def test_f01_write_operands_behind_option_values_and_abbreviations(cmd, rule):
    a = assess(cmd, "/work/proj/sub")
    assert a.rule_id == rule and LEVELS[a.level] >= LEVELS["high"], (cmd, a, policy.segments(cmd))
    assert pre(cmd).exit_code == 2


@pytest.mark.parametrize("cmd", [
    "less '+!git push origin main' in",
    "less +'!git push origin main' in",
    "less \"+!git push origin main\" in",
    "man -P 'git push origin main' ls",
    "man --pager='git push origin main' ls",
    "man --pager 'git push origin main' ls",
    "rg --pre 'git push origin main' x",
    "rg --pre='git push origin main' x",
    "bat --pager='git push origin main' in",
    "bat --pager \"git push origin main\" in",
    "sort --compress-program='git push origin main' in",
    "sort --compress-program 'git push origin main' in",
    "sort --compress='git push origin main' in",
    "git fetch --upload-pack='git push origin main' origin",
    "git ls-remote --upload-pack 'git push origin main' origin",
    "git push --receive-pack='git push origin main' origin feat",
    "git rebase --exec 'git push origin main' HEAD~1",
    "git rebase -x 'git push origin main' HEAD~1",
    "git submodule foreach 'git push origin main'",
    "git submodule foreach --recursive 'git push origin main'",
    "git bisect run sh -c 'git push origin main'",
    "git filter-branch --tree-filter 'git push origin main' HEAD",
    "git difftool --extcmd='git push origin main'",
    "git grep -O'git push origin main' x",
    "LESSOPEN='|git push origin main' less in",
    "PAGER='git push origin main' man ls",
    "GIT_PAGER='git push origin main' git log",
    "GIT_EXTERNAL_DIFF='git push origin main' git diff",
    "fd -x 'git push origin main'",
    "find . -exec 'git' push origin main ';'",
])
def test_f01_exec_options_on_read_only_verbs_expose_their_command(cmd):
    a = assess(cmd)
    assert "SI-08" in a.blocked, (cmd, a, policy.segments(cmd))
    assert pre(cmd).exit_code == 2


@pytest.mark.parametrize("cmd", [
    "rg 'git push' docs/", "rg --pre-glob '*.gz' x", "bat --style=plain in", "man ls", "less in", "less +G in", "less +/git\\ push in",
    "sort --compress-program=gzip in", "git fetch origin", "git fetch --prune origin",
    "git submodule foreach 'git status'", "git grep -n 'git push' docs", "git ls-remote origin",
    "GIT_TERMINAL_PROMPT=0 git fetch", "GIT_DIR=.git git status", "LESS=-R less in",
    "sed -n '/error/p' f", "sed -n '1,5p' f", "sed -n '/^## /{p;q}' f", "sed 's/\\(a\\)/\\1/g' f", "sed 's|a|b|g' f", "sed -e 's/a/b/' -e 's/c/d/' f", "sed '/x/d' f",
    "sed -n '$p' f", "sed 5q f", "sed 's/w/e/' f", "sed 's/e/w/g' f", "sed -n '/w/p' f", "sed 'y/abc/xyz/' f", "sed -n 's/.*\\(foo\\).*/\\1/p' f",
    "awk '$1 == \"a\" {print $2}' f", "uniq -f 1 in", "uniq -s 2 in", "uniq -c in", "xxd -c 8 in", "xxd -s 3 in", "xxd -g 1 in", "xxd -l 4 -c 2 in",
    "sort -t, -k2,2 in", "sort -nr in", "sort -o", "cd /tmp && ls", "cd /etc && cat pf.conf", "cd ~/.claude && cat settings.json",
    "cd ~/.claude && ls", "(cd /etc && ls); sort -u in",
])
def test_f01_harmless_neighbours_stay_low(cmd):
    a = assess(cmd)
    if cmd == "sort -o":
        return  # a bare -o with no operand is invalid; only checked that it does not raise
    assert a.level == "low" and not a.blocked, (cmd, a, policy.segments(cmd))


@pytest.mark.parametrize("cmd", [
    "echo 'git push origin main' | xargs -0 sh -c",
    "echo 'git push origin main' | xargs sh -c",
    "echo 'git push origin main' | source /dev/stdin",
    "echo 'git push origin main' | . /dev/stdin",
    "echo 'git push origin main' | bash /dev/stdin",
    "echo 'git push origin main' | sh /dev/fd/0",
    "echo 'git push origin main' | bash -",
    "echo 'echo \"git push origin main\" | bash' | bash",
    "echo \"echo 'git push origin main' | sh\" | sh",
    "echo 'echo '\"'\"'echo \"git push origin main\" | bash'\"'\"' | bash' | bash",
    "printf '%s\\n' 'echo git push origin main | sh' | sh",
])
def test_f01_pipes_into_stdin_readers_are_classified(cmd):
    a = assess(cmd)
    assert "SI-08" in a.blocked, (cmd, a, policy.segments(cmd))


def test_f01_cd_target_is_tracked_for_head_resolution(tmp_path):
    on_main = repo(tmp_path / "m", "main")
    on_feat = repo(tmp_path / "f", "feat/x")
    assert is_si08(pre(f"cd {on_main} && git push origin HEAD", cwd=on_feat))
    assert is_si08(pre(f"cd {on_main}; git push", cwd=on_feat))
    assert is_si08(pre(f"cd '{on_main}' && git push -u origin HEAD", cwd=on_feat))
    assert is_si08(pre(f"pushd {on_main} && git push", cwd=on_feat))
    assert is_si08(pre("cd ../m && git push", cwd=on_feat))
    assert not is_si08(pre(f"cd {on_feat} && git push origin HEAD", cwd=on_main))
    assert not is_si08(pre(f"(cd {on_feat} && git push origin HEAD)", cwd=on_main))
    # a subshell's cd does not leak out of it
    assert is_si08(pre(f"(cd {on_feat} && true); git push origin HEAD", cwd=on_main))
    assert pre(f"cd {on_feat} && git push origin HEAD", cwd=on_main).exit_code == 2  # still needs a permit


# ---------------------------------------------------------------- F02: eval / sh -c built from several words

def _nest(cmd: str, depth: int) -> str:
    for _ in range(depth):
        cmd = "sh -c " + shlex.quote(cmd)
    return cmd


EVAL_FORMS = [
    'eval "git" push origin main',
    "eval git' push' origin main",
    "eval 'git' push origin main",
    "eval \"git push\" origin main",
    "eval git\\ push origin main",
    "eval -- git push origin main",
    'sh -c "git"\' push origin main\'',
    "sh -c git\\ push\\ origin\\ main",
    "sh -c 'git push origin main' x",
    "sh -c 'sh -c '\"'\"'git push origin main'\"'\"''",
    "sh -c \"sh -c 'git push origin main'\"",
    "bash -c \"bash -c \\\"git push origin main\\\"\"",
    "env FOO=1 sh -c \"git\"' push origin main'",
    "sudo -u root bash -c \"git\"' push origin main'",
    "find . -exec sh -c 'git push origin main' \\;",
    "xargs -I{} sh -c 'git push origin main'",
    "timeout 5 sh -c 'git'\" push\"' origin main'",
    "nohup bash -lc \"git\"' push origin main'",
    "sh -c '$0 push origin main' git",
    "sh -c '$0 $1 origin main' git push",
    "sh -c 'echo ok; \"$@\"' _ git push origin main",
    "su root -c 'git push origin main'",
    "su -c \"git\"' push origin main' root",
    "eval \"$(echo git push origin main)\"",
    "bash -c 'eval \"git\" push origin main'",
    "eval eval git push origin main",
    "eval 'eval \"git push origin main\"'",
    _nest("git push origin main", 1),
    _nest("git push origin main", 3),
    _nest("git push origin main", 6),
    _nest("git push --force origin main", 4),
    "FOO=bar eval \"git\" push origin main",
]


@pytest.mark.parametrize("cmd", EVAL_FORMS)
def test_f02_shell_code_is_the_joined_words_not_only_the_first_quoted_one(cmd):
    a = assess(cmd)
    assert "SI-08" in a.blocked, (cmd, a, policy.segments(cmd))
    assert pre(cmd).exit_code == 2


@pytest.mark.parametrize("cmd,rule", [
    ("eval 'rm' -rf ~/Documents", "R-11"),
    ('eval "git reset" --hard', "R-11"),
    ("sh -c 'rm'\" -rf ~/Documents\"", "R-11"),
    ("sh -c rm\\ -rf\\ ~/Documents", "R-11"),
    (_nest("rm -rf ~/Documents", 3), "R-11"),
    (_nest("git reset --hard", 3), "R-11"),
    ("eval \"sudo\" pfctl -d", "R-01"),
    ("git rebase --exec 'rm -rf ~/Documents' HEAD~1", "R-11"),
    ("echo 'echo \"rm -rf ~/Documents\" | bash' | bash", "R-11"),
    ("echo 'rm -rf ~/Documents' | xargs -0 sh -c", "R-11"),
])
def test_f02_other_dangerous_commands_in_joined_words(cmd, rule):
    a = assess(cmd)
    assert a.rule_id == rule and LEVELS[a.level] >= LEVELS["high"], (cmd, a, policy.segments(cmd))


@pytest.mark.parametrize("cmd", [
    "sh -c 'echo hello'", 'eval "echo" hello', "bash -c \"ls\"' -la'", 'echo "eval git push"', "sh -c 'git status'", "eval 'ls' -la",
    "sh -c \"git\"' status'", "echo 'sh -c \"git push origin main\"'", "echo sh -c", "ls eval", "grep -r 'eval' src/",
    "sh -c 'echo $0' x", "bash script.sh", "sh -c 'git log --oneline' | head", "echo eval git push | wc -c",
])
def test_f02_harmless_shell_wrappers_stay_low(cmd):
    a = assess(cmd)
    assert a.level == "low" and not a.blocked, (cmd, a, policy.segments(cmd))


# ---------------------------------------------------------------- F03: comments end at the newline; brace expansion

@pytest.mark.parametrize("cmd,rule", [
    ("echo hi #\\\ngit push origin main", "R-10"),
    ("git status #\\\ngit push origin main", "R-10"),
    ("true # x\\\ngit reset --hard", "R-11"),
    ("echo a # c\\\nrm -rf ~/Documents", "R-11"),
    ("echo hi # c\\\r\ngit push origin main", "R-10"),
    ("echo hi # c \\\\\ngit push origin main", "R-10"),
    ("ls #\\\n\\\ngit push origin main", "R-10"),
    ("echo $(true #\\\ngit push origin main\n)", "R-10"),
    ("git push origin m{a,}in", "R-10"),
    ("git push origin ma{in,}", "R-10"),
    ("git push origin mai{n,}", "R-10"),
    ("git push origin {main,}", "R-10"),
    ("git push origin {ma,}{in,}", "R-10"),
    ("git push origin {x,main}", "R-10"),
    ("git push origin m{a,b}in", "R-10"),
    ("git push origin HEAD:m{a,}in", "R-10"),
    ("git push origin main{,}", "R-10"),
    ("git push origin {feat,main}", "R-10"),
    ("{ git push origin main; }", "R-10"),
    ("git reset {--hard,--soft}", "R-11"),
])
def test_f03_comment_then_newline_and_brace_expansion_cannot_hide_commands(cmd, rule):
    a = assess(cmd)
    assert a.rule_id == rule, (cmd, a, policy.segments(cmd))
    if rule == "R-10":
        assert "SI-08" in a.blocked, (cmd, a, policy.segments(cmd))


@pytest.mark.parametrize("cmd", [
    "echo hi # git push origin main",
    "git status # then git push origin main",
    "echo 'a # git push origin main'",
    'echo "a # git push origin main"',
    "echo a#b", "echo $#", "echo ${#x}", "echo $(( 1 ))", "echo a # b\nls",
    "{ echo a; }", "echo {a,b}", "echo file{1,2}.txt", "echo ${x:-{a,b}}", "git status; { ls; }",
    "git push origin feat/{a,b}", "echo \"m{a,}in\"",
])
def test_f03_comments_and_braces_that_are_not_commands_stay_as_before(cmd):
    a = assess(cmd)
    if cmd.startswith("git push origin feat/"):
        assert a.rule_id == "R-10" and not a.blocked
    else:
        assert a.level == "low" and not a.blocked, (cmd, a, policy.segments(cmd))


# ---------------------------------------------------------------- N1: classifier cost

def _timed(cmd: str) -> float:
    t = time.perf_counter()
    assess(cmd)
    return time.perf_counter() - t


def test_n1_many_quoted_words_cost_is_linear_not_quadratic():
    small = _timed("echo " + "'a' " * 5000)
    big = _timed("echo " + "'a' " * 40000)
    assert big < 5.0, big
    assert big < max(small, 0.02) * 40, (small, big)  # 8x the words, nowhere near 64x the time
    assert _timed("echo " + '"a" ' * 40000) < 5.0
    assert _timed("echo " + "x " * 400000) < 5.0
    assert _timed("echo " + "$(true) " * 20000) < 10.0


def test_n1_nested_shell_quoting_is_bounded_and_still_found():
    assert _timed(_nest("git push origin main", 10)) < 5.0
    assert "SI-08" in assess(_nest("git push origin main", 7)).blocked
    assert _timed(_nest("git push origin main", 12)) < 10.0  # past the work budget: degraded text search, never a hang


def test_n1_work_budget_exhaustion_degrades_to_text_search_not_open():
    cmd = "echo " + "'a' " * 300000 + "; git push origin main"
    t = time.perf_counter()
    a = assess(cmd)
    assert time.perf_counter() - t < 10.0
    assert "SI-08" in a.blocked


# ---------------------------------------------------------------- N7: remaining low-severity gaps

@pytest.mark.parametrize("cmd,rule", [
    ("git clean -f -d", "R-11"),
    ("git clean -d -f", "R-11"),
    ("git clean --force -d", "R-11"),
    ("git clean -x -f -d", "R-11"),
    ("git clean --force -fd", "R-11"),
    ("git branch --delete --force x", "R-11"),
    ("git branch -d -f x", "R-11"),
    ("git branch -f -d x", "R-11"),
    ("git branch -vD x", "R-11"),
    ("git branch --force --delete x", "R-11"),
    ("git reset --har", "R-11"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0='!id' git p", "R-15"),
    ("GIT_SSH_COMMAND=id git fetch", "R-15"),
    ("GIT_SSH=id git fetch", "R-15"),
    ("GIT_PAGER=id git log", "R-15"),
    ("GIT_EDITOR=id git commit", "R-15"),
    ("GIT_ASKPASS=id git fetch", "R-15"),
    ("GIT_EXEC_PATH=/x git status", "R-15"),
    ("GIT_CONFIG_PARAMETERS=\"'core.pager=id'\" git log", "R-15"),
    ("export GIT_SSH_COMMAND=id", "R-15"),
    ("env GIT_SSH_COMMAND=id git fetch", "R-15"),
    ("git -c core.pager=id log", "R-15"),
])
def test_n7_destructive_git_spellings_and_env_var_config(cmd, rule):
    a = assess(cmd)
    assert a.rule_id == rule and LEVELS[a.level] >= LEVELS["high"], (cmd, a, policy.segments(cmd))
    assert pre(cmd).exit_code == 2


@pytest.mark.parametrize("cmd", [
    "git clean -n -d", "git clean -d", "git clean -f", "git branch -d x", "git branch -f x HEAD", "git branch --delete x", "git branch -m a b",
    "git reset --soft HEAD~1", "GIT_TERMINAL_PROMPT=0 git status", "GIT_AUTHOR_NAME=a git commit -m x", "GIT_DIR=.git git log",
])
def test_n7_harmless_git_stays_as_before(cmd):
    a = assess(cmd)
    assert LEVELS[a.level] <= LEVELS["medium"] and a.rule_id not in ("R-11", "R-15"), (cmd, a)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.mark.parametrize("key", ["file_path", "path", "filePath", "file", "filepath", "target_file", "notebook_path", "file_name"])
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_n7_write_tools_with_any_path_key_are_classified(fake_home, key, tool):
    a = policy.assess(tool, {key: str(fake_home / ".claude" / "settings.json"), "content": "x"}, "")
    assert a.rule_id == "R-13" and a.level == "high", (key, tool, a)
    res = hooks.pretool({"tool_name": tool, "tool_input": {key: "~/.claude/settings.json", "content": "x"}, "session_id": SESSION, "cwd": ""})
    assert res.exit_code == 2


def test_n7_write_tool_with_unknown_key_falls_back_to_every_string_value(fake_home):
    a = policy.assess("Write", {"destination": str(fake_home / ".claude" / "settings.json"), "content": "x"}, "")
    assert a.rule_id == "R-13", a
    a = policy.assess("Edit", {"edits": [{"path": "~/.claude/settings.json"}]}, "")
    assert a.rule_id == "R-13", a
    # prose in the content of a harmless file is not a path
    a = policy.assess("Write", {"file_path": "/tmp/notes.md", "content": "edit ~/.claude/settings.json by hand"}, "")
    assert a.level == "low", a
    a = policy.assess("Edit", {"file_path": "/tmp/n.md", "old_string": "~/.claude/settings.json", "new_string": "/etc/pf.conf"}, "")
    assert a.level == "low", a


@pytest.mark.parametrize("path", [
    "~/library/launchagents/x.plist", "/Users/u/LIBRARY/LAUNCHAGENTS/x.plist", "/Library/launchagents/x.plist",
])
def test_n7_launch_agent_paths_are_case_insensitive(path):
    assert policy.assess("Write", {"file_path": path}, "").rule_id == "R-12"
    assert assess(f"cp x {path}").rule_id == "R-12"


def test_n7_production_paths_are_case_insensitive():
    a = policy.assess("Write", {"file_path": "/library/launchdaemons/x.plist"}, "")
    assert a.level == "high" and a.environment == "production"


def test_n7_push_tags_on_main_stays_blocked_and_other_pushes_unchanged(tmp_path):
    cwd = repo(tmp_path / "r", "main")
    assert is_si08(pre("git push --tags origin", cwd=cwd))
    assert json.dumps(assess("git push origin feat/x").blocked) == "[]"


def test_f01_rebase_exec_classifies_the_command_it_runs():
    a = assess("git rebase --exec 'make test' HEAD~1")
    assert a.rule_id == "R-20" and not a.blocked, a  # a rebase is medium; `make test` adds nothing


# ---------------------------------------------------------------- found while probing the second fix

@pytest.mark.parametrize("cmd", [
    "echo \"`echo \\`git push origin main\\``\"",
    "echo `echo \\`git push origin main\\``",
    "echo `echo \\`echo \\\\\\`git push origin main\\\\\\`\\``",
    "eval `echo git push origin main`",
    "eval $(echo git push origin main)",
    "bash -c \"$(echo 'git push origin main')\"",
    "eval \"$(printf 'git push origin %s' main)\"",
    "echo git push origin main | tee /dev/stdout | sh",
    "echo git push origin main | cat | cat | bash",
    "echo git push origin main | sudo -u root sh",
    "echo git push origin main | sudo -E bash -s",
    "sudo -u root bash -c \"git\"' push origin main'",
    "sudo -E -u root sh -c 'git push origin main'",
    "sh -c '\"$@\"' _ git push origin main",
    "sh -c 'exec \"$@\"' _ git push origin main",
    "cd $HOME/.claude && git push origin main",
])
def test_probe_more_ways_to_hand_code_to_a_shell(cmd):
    assert "SI-08" in assess(cmd).blocked, (cmd, policy.segments(cmd))


@pytest.mark.parametrize("cmd", [
    "rm -r -f ~/Documents", "rm -fr ~/Documents", "rm -Rf ~/Documents", "rm -rfv ~/Documents", "rm --recursive --force ~/Documents",
    "rm -r --force ~/Documents", "rm -f -R ~/Documents", "/bin/rm -r -f ~/Documents", "r\\m -fr ~/Documents", "command rm -fr ~/Documents",
])
def test_probe_rm_flag_spellings_are_rm_rf(cmd):
    assert assess(cmd).rule_id == "R-11", cmd


@pytest.mark.parametrize("cmd", ["rm -r build", "rm -f x.log", "rm -rf /tmp/scratch", "rm -fr /private/tmp/x", "rm -r -f ./node_modules", "rm file", "rm -rf $TMPDIR/x"])
def test_probe_rm_without_both_flags_or_with_scratch_targets_is_unchanged(cmd):
    assert assess(cmd).level == "low", cmd


def test_probe_cd_to_a_computed_directory_is_a_documented_limit():
    # No way to know where `cd $NOPE/x` lands; the relative operand stays unresolved and is classified as written.
    assert assess("cd $NOPE/x && sort -o settings.json in").level == "low"


def test_n1_huge_command_is_not_masked_and_cost_stays_bounded():
    from jade import inert

    big = "cat <<'EOF'\n" + "x " * 150_000 + "\nEOF"
    assert len(big) > inert.MASK_LIMIT
    assert inert.mask(big) == big  # too large to scan: matched whole, the stricter answer
    assert _timed(big) < 5.0
