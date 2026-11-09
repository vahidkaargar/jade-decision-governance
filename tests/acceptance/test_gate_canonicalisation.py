"""Cluster A1: one canonicalisation step in front of every classifier rule (findings F00-F05, F35).

Each group reproduces a verified bypass of the PreToolUse gate. The commands are only classified, never run,
except for the throwaway git repositories under tmp_path used to resolve the current branch.
"""

import os
import subprocess

import pytest

from jade import hooks, policy
from jade.util import LEVELS

SESSION = "s" * 8


def assess(cmd: str, cwd: str = ""):
    return policy.assess("Bash", {"command": cmd}, cwd)


def at_least(cmd: str, level: str = "high") -> bool:
    return LEVELS[assess(cmd).level] >= LEVELS[level]


def pre(cmd: str, cwd: str = "/nonexistent-case-dir", tool: str = "Bash"):
    return hooks.pretool({"tool_name": tool, "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": cwd})


def repo(path, branch: str):
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    return str(path)


# ---------------------------------------------------------------- F00 / F35: git global options

GLOBAL_OPTION_PUSHES = [
    "git -C /some/repo push origin main",
    "git -C . push origin main",
    "git -c user.name=x push --force origin main",
    "git -c a=b push origin main",
    "git --no-pager push origin main",
    "git -P push origin main",
    "git --git-dir=.git push origin main",
    "git --git-dir .git --work-tree . push origin main",
    "git --namespace=n -C . push origin main",
    "git -C a -C b push origin main",
    "/usr/bin/git -C . push origin main",
    "timeout 5 git -C . push origin main",
    "env -i git -C . push origin main",
    "sudo -E git -C . push origin main",
    "git -C 'a dir' push origin main",
    "git -c 'user.name=A B' push origin main",
]


@pytest.mark.parametrize("cmd", GLOBAL_OPTION_PUSHES)
def test_f00_global_options_do_not_hide_a_push_to_the_default_branch(cmd):
    a = assess(cmd)
    assert "SI-08" in a.blocked, (cmd, a)
    r = pre(cmd)
    assert r.exit_code == 2 and "safety invariant" in r.stderr


@pytest.mark.parametrize("cmd,rule", [
    ("git -C . push origin feat", "R-10"),
    ("git -c a=b push origin feat", "R-10"),
    ("git --no-pager push origin feat", "R-10"),
    ("git -C . push --force origin feat", "R-10"),
    ("git -c user.name=x push --force origin feat", "R-10"),
    ("git -C . reset --hard", "R-11"),
    ("git -C . reset --hard HEAD~1", "R-11"),
    ("git -C . clean -fd", "R-11"),
    ("git -C . branch -D x", "R-11"),
    ("git --no-pager -C . reset --hard", "R-11"),
    ("git reset HEAD~1 --hard", "R-11"),
    ("git reset -q --hard", "R-11"),
    ("git push origin feat --force", "R-10"),
    ("git branch -d x -D", "R-11"),
    ("git clean -df", "R-11"),
    ("timeout 5 git -C . reset --hard", "R-11"),
])
def test_f00_global_options_do_not_hide_high_risk_git(cmd, rule):
    a = assess(cmd)
    assert a.level == "high" and a.rule_id == rule, (cmd, a)
    assert pre(cmd).exit_code == 2


@pytest.mark.parametrize("cmd", [
    "git -c alias.p='!id' p",
    "git -c alias.p=!id p",
    "git -c 'alias.p=!id x' p",
    "git -c core.sshCommand=id fetch",
    "git -c core.sshcommand=id ls-remote origin",
    "git -c core.pager=id log",
    "git -c core.editor=id commit",
    "git -c core.fsmonitor=id status",
    "git -c core.hooksPath=/x commit -m m",
    "git -c credential.helper='!id' fetch",
    "git -c diff.external=id diff",
    "git -c url.x.insteadOf=y fetch",
    "git --exec-path=/x status",
    "git --exec-path status",
    "git -C . -c alias.s=!id s",
    "git -c $KEY=v status",
    "git --config-env=alias.p=VAR p",
])
def test_f00_config_that_can_run_commands_is_high(cmd):
    a = assess(cmd)
    assert a.level == "high" and a.rule_id == "R-15", (cmd, a)
    assert pre(cmd).exit_code == 2


@pytest.mark.parametrize("cmd", [
    "git -C /x status",
    "git -C /x diff --stat",
    "git --no-pager log --oneline",
    "git -C . -c color.ui=never diff",
    "git -c user.name=x commit -m hello",
    "git -c 'user.name=A B' commit -m hello",
    "git --git-dir=.git rev-parse HEAD",
    "git -C 'a dir' status",
    "git -c core.quotepath=false status",
    "git -c diff.algorithm=patience diff",
])
def test_f00_harmless_git_with_global_options_stays_low(cmd):
    a = assess(cmd)
    assert a.level == "low" and not a.blocked, (cmd, a)
    assert pre(cmd).exit_code == 0


def test_f00_plain_forms_keep_their_old_verdicts():
    assert "SI-08" in assess("git push origin main").blocked
    assert assess("git push origin feat").rule_id == "R-10"
    assert assess("git reset --hard").rule_id == "R-11"
    assert assess("git commit -m 'git push later'").level == "low"
    assert assess("git branch -d old").level == "low"
    assert assess("git clean -n").level == "low"


def test_f00_bare_push_with_relative_dash_c_resolves_against_event_cwd(tmp_path):
    r = repo(tmp_path / "r", "main")
    for cmd in ("git -C . push", "git -c a=b push", "git --no-pager push", "git -C ../r push"):
        res = pre(cmd, cwd=r)
        assert res.exit_code == 2 and "safety invariant" in res.stderr, cmd


# ---------------------------------------------------------------- F01: read-only verbs that write

NOT_READ_ONLY = [
    ("sort -o ~/.claude/settings.json in.txt", "R-13"),
    ("sort -ro ~/.claude/settings.json in.txt", "R-13"),
    ("sort --output=/etc/pf.conf in.txt", "R-01"),
    ("sort -o /etc/pf.conf in.txt", "R-01"),
    ("uniq in.txt ~/.claude/settings.json", "R-13"),
    ("uniq -c in.txt /etc/pf.conf", "R-01"),
    ("sed -n 'w /etc/pf.conf' in.txt", "R-01"),
    ("sed 's/a/b/w /etc/pf.conf' in.txt", "R-01"),
    ("sed -n '/x/w ~/.claude/settings.json' in.txt", "R-13"),
    ("sed --in-place s/a/b/ ~/.claude/settings.json", "R-13"),
    ("awk '{print > \"/etc/pf.conf\"}' in.txt", "R-01"),
    ("awk '{print | \"tee /etc/pf.conf\"}' in.txt", "R-01"),
    ("gawk -i inplace '{print}' ~/.claude/settings.json", "R-13"),
    ("git diff --output=/etc/pf.conf", "R-01"),
    ("git log --output ~/.claude/settings.json", "R-13"),
    ("git show --output=~/.claude/settings.json HEAD", "R-13"),
    ("find . -fprint /etc/pf.conf", "R-01"),
    ("find . -fls ~/.claude/settings.json", "R-13"),
    ("find . -fprintf /etc/pf.conf x", "R-01"),
    ("xxd -r in.txt /etc/pf.conf", "R-01"),
    ("xxd in.txt ~/.claude/settings.json", "R-13"),
    ("yq -i '.a=1' ~/.claude/settings.json", "R-13"),
    ("tree -o ~/.claude/CLAUDE.md", "R-13"),
    ("less -o ~/.claude/CLAUDE.md f", "R-13"),
    ("tee ~/.claude/settings.json", "R-13"),
    ("dd of=/etc/pf.conf if=x", "R-01"),
]


@pytest.mark.parametrize("cmd,rule", NOT_READ_ONLY)
def test_f01_no_read_only_entry_can_write(cmd, rule):
    a = assess(cmd)
    assert a.rule_id == rule and LEVELS[a.level] >= LEVELS["high"], (cmd, a)
    assert pre(cmd).exit_code == 2


@pytest.mark.parametrize("cmd", [
    "awk 'BEGIN{system(\"git push origin main\")}'",
    "sed '1e git push origin main' f",
    "sed -n 's/a/b/e' f; git push origin main",
    "fd -x git push origin main",
    "find . -exec git push origin main ;",
    "find . -okdir git push origin main ;",
])
def test_f01_programs_inside_read_only_verbs_are_classified(cmd):
    a = assess(cmd)
    assert "SI-08" in a.blocked and a.level == "high", (cmd, a)


@pytest.mark.parametrize("cmd", [
    "sort in.txt", "sort -u -k2,3 in.txt", "sort -r in.txt | uniq -c | head", "uniq in.txt", "uniq -c in.txt", "uniq -f 1 in.txt",
    "sed -n 5p f", "sed -n '/git push/p' log.txt", "sed 's/a/b/' f", "sed -E 's/(we)/\\1/' f", "sed -n '/pfctl/p' ~/.claude/settings.json",
    "awk '{print $1}' f", "awk '/pfctl/' notes.md", "awk '{s+=$1} END{print s}' f",
    "find . -name '*.py'", "find . -type f -print", "git diff --stat", "git log --oneline -5", "git show HEAD",
    "xxd f", "xxd -l 16 f", "grep -rn 'git push' docs/", "cat ~/.claude/settings.json", "jq . ~/.claude/settings.json",
    "yq .a ~/.claude/settings.json", "tree -L 2", "less f", "date -u", "ls -la ~/.claude", "wc -l f",
])
def test_f01_genuinely_read_only_commands_stay_low(cmd):
    a = assess(cmd)
    assert a.level == "low" and not a.blocked, (cmd, a)
    assert policy.is_read_only(policy.segments(cmd)[0]), cmd


# ---------------------------------------------------------------- F02: hidden command text

HIDDEN_PUSHES = [
    'echo "$(git push origin main)"',
    'echo "`git push origin main`"',
    'echo "x $(echo "y" ; git push origin main) z"',
    'X="$(git push origin main)"',
    "cat <<EOF\n$(git push origin main)\nEOF",
    "cat <<EOF\nhello `git push origin main`\nEOF",
    "sh <<EOF\ngit push origin main\nEOF",
    "bash -s <<'X'\ngit push --force origin main\nX",
    "sudo bash <<'X'\ngit push origin main\nX",
    "cat <<'EOF' | bash\ngit push origin main\nEOF",
    "echo 'git push origin main' | bash",
    "echo git push origin main | sh",
    "printf '%s\\n' 'git push origin main' | sh",
    "echo 'git push origin main' | sudo bash -s",
    "bash <<< 'git push origin main'",
    "zsh <<< \"git push origin main\"",
    "python3 -c \"import os; os.system('git push origin main')\"",
    "python3 -c 'import subprocess; subprocess.run([\"git\", \"push\", \"origin\", \"main\"])'",
    "python3 - <<'PY'\nimport os\nos.system('git push origin main')\nPY",
    "perl -e 'system(\"git push origin main\")'",
    "ruby -e 'system(\"git push origin main\")'",
    "node -e \"require('child_process').execSync('git push origin main')\"",
    "echo '<<X'\ngit push origin main",
    "echo \"<<X\"; git push origin main",
    "echo a <<< b; git push origin main",
    "bash -lc 'git push origin main'",
    "bash --login -c 'git push origin main'",
    "echo $((1<<3)); git push origin main",
]


@pytest.mark.parametrize("cmd", HIDDEN_PUSHES)
def test_f02_literal_pushes_the_shell_will_run_are_classified_like_direct_ones(cmd):
    a = assess(cmd)
    assert "SI-08" in a.blocked, (cmd, a, policy.segments(cmd))
    assert pre(cmd).exit_code == 2


@pytest.mark.parametrize("cmd,rule", [
    ('echo "$(rm -rf ~/Documents)"', "R-11"),
    ("cat <<EOF\n$(rm -rf ~/Documents)\nEOF", "R-11"),
    ("echo 'rm -rf ~/Documents' | bash", "R-11"),
    ("sh <<EOF\nsudo pfctl -d\nEOF", "R-01"),
    ("python3 -c \"import os; os.system('rm -rf ~/Documents')\"", "R-11"),
    ('echo "$(git push origin feat)"', "R-10"),
])
def test_f02_other_hidden_commands_keep_their_level(cmd, rule):
    a = assess(cmd)
    assert a.rule_id == rule and a.level in ("high", "critical"), (cmd, a)


@pytest.mark.parametrize("cmd", [
    'echo "to deploy run: kubectl apply -f x.yaml and git push"',
    "cat > notes.md <<'EOF'\nsudo pfctl -d\ngit push origin main\nEOF",
    "cat > notes.md <<EOF\nsudo pfctl -d\ngit push origin main\nEOF",
    "cat > notes.md <<EOF\nthe date is $(date)\nEOF",
    "git commit -m \"$(cat <<'EOF'\nfix: don't push (git push) to main\n\nBody with 'quotes' and $(not run)\nEOF\n)\"",
    'echo "$(date) $(whoami)"',
    "echo '$(git push origin main)'",
    "echo \\$HOME",
    "grep -rn 'git push' docs/ | head",
    "python3 -c \"print('hello')\"",
    "python3 - <<'PY'\nprint('hello')\nPY",
    "python3 script.py <<EOF\ngit push origin main\nEOF",
    "bash script.sh <<EOF\ngit push origin main\nEOF",
    "echo hi | cat",
    "echo 'git push origin main' | cat",
    "echo 'git push origin main' | wc -c",
    "cat file | python3 script.py",
    "bash <<< 'echo hello'",
    "echo $((1<<3))",
    "git commit -m 'git push later'",
    "cat <<EOF > out.txt\ngit push origin main\nEOF",
])
def test_f02_quoted_prose_and_data_stay_inert(cmd):
    a = assess(cmd)
    assert a.level == "low" and not a.blocked, (cmd, a, policy.segments(cmd))


def test_f02_heredoc_marker_inside_quotes_does_not_swallow_following_lines():
    cmd = "echo \"<<EOF\"\ngit push origin feat\nEOF"
    assert assess(cmd).rule_id == "R-10"


# ---------------------------------------------------------------- F03: escapes and continuations

ESCAPED = [
    "g\\it push origin main",
    "git pu\\sh origin main",
    "git\\\n push origin main",
    "git push \\\n origin main",
    "git push origin \\\n main",
    "git push origin \\\r\n main",
    "g'i't push origin main",
    'g"i"t push origin main',
    "'git' 'push' origin main",
    "git ''push origin main",
    "git push origin ma\\in",
    "git push origin 'ma'in",
    "$'\\x67it' push origin main",
    "$'g\\151t' push origin main",
    "git -\\C . push origin main",
    "git\\\n -C . \\\n push origin main",
]


@pytest.mark.parametrize("cmd", ESCAPED)
def test_f03_escapes_quotes_and_continuations_are_resolved_before_matching(cmd):
    a = assess(cmd)
    assert "SI-08" in a.blocked, (cmd, a, policy.segments(cmd))


@pytest.mark.parametrize("cmd", ["git re\\set --hard", "git reset \\\n --hard", "g\\it reset --hard", "git reset \\\r\n --hard"])
def test_f03_escaped_hard_reset_is_high(cmd):
    a = assess(cmd)
    assert a.rule_id == "R-11", (cmd, a)


def test_f03_benign_escapes_and_trailing_backslash():
    assert assess("echo a\\ b").level == "low"
    assert assess("git commit -m hello\\ git\\ push").level == "low"
    assert at_least("git push origin main\\", "high")
    assert assess("ls \\\n -la").level == "low"


# ---------------------------------------------------------------- F04: push refspecs and the current branch

MAIN_ONLY_REFSPECS = [
    "git push origin HEAD",
    "git push -u origin HEAD",
    "git push --set-upstream origin HEAD",
    "git push --force origin HEAD",
    "git push --force-with-lease origin HEAD",
    "git push --force-with-lease=main origin HEAD",
    "git push origin +HEAD",
    "git push origin @",
    "git push -o x origin",
    "git push -o ci.skip origin HEAD",
    "git push --push-option=x origin HEAD",
    "git push --push-option x origin HEAD",
    "git push -uo x origin HEAD",
    "git push --repo origin HEAD",
    "git push --repo=origin",
    "git push",
    "git push origin",
    "git push --tags",
    "git -C . push origin HEAD",
    "git push origin $BRANCH",
    "git push origin $(git branch --show-current)",
    "git push origin ${BRANCH}",
    'git push origin "$BRANCH"',
    "git push origin refs/heads/*:refs/heads/*",
]

ANYWHERE_REFSPECS = [
    "git push origin main",
    "git push origin master",
    "git push origin +main",
    "git push origin refs/heads/main",
    "git push origin heads/main",
    "git push origin HEAD:main",
    "git push origin HEAD:refs/heads/main",
    "git push origin feat:refs/heads/main",
    "git push origin feat:heads/master",
    "git push origin :main",
    "git push --delete origin main",
    "git push -f origin feat:main",
    "git push --force-with-lease=main:abc123 origin HEAD:main",
    "git push --all origin",
    "git push --mirror",
    "git push --all",
    "git push origin Main",
    "git push origin refs/heads/*:refs/heads/*",
]


def _is_si08(res) -> bool:
    return res.exit_code == 2 and "safety invariant" in res.stderr and "SI-08" in res.stderr


@pytest.mark.parametrize("cmd", MAIN_ONLY_REFSPECS)
def test_f04_pushes_that_resolve_to_the_current_main_branch_are_blocked(tmp_path, cmd):
    cwd = repo(tmp_path / "r", "main")
    assert _is_si08(pre(cmd, cwd=cwd)), (cmd, pre(cmd, cwd=cwd).stderr)


@pytest.mark.parametrize("cmd", ANYWHERE_REFSPECS)
@pytest.mark.parametrize("branch", ["main", "feat/x"])
def test_f04_static_default_branch_refspecs_are_blocked_on_any_branch(tmp_path, cmd, branch):
    cwd = repo(tmp_path / "r", branch)
    assert _is_si08(pre(cmd, cwd=cwd)), (cmd, branch, pre(cmd, cwd=cwd).stderr)


@pytest.mark.parametrize("cmd", [
    "git push origin HEAD",
    "git push -u origin HEAD",
    "git push origin feat/x",
    "git push origin HEAD:feat/y",
    "git push origin HEAD:refs/heads/feat/y",
    "git push origin main:feat/y",
    "git push origin feat/x:feat/z",
    "git push -o ci.skip origin HEAD",
    "git push --force-with-lease=feat/x origin HEAD",
    "git push --tags origin",
    "git push origin feature/main",
    "git push origin v1.0",
    "git push",
    "git push origin $BRANCH",
])
def test_f04_feature_branch_pushes_still_need_a_permit_but_are_not_invariant_blocks(tmp_path, cmd):
    cwd = repo(tmp_path / "r", "feat/x")
    res = pre(cmd, cwd=cwd)
    assert res.exit_code == 2 and "safety invariant" not in res.stderr and "needs a human-approved permit" in res.stderr, (cmd, res.stderr)


def test_f04_detached_head_and_missing_repo_do_not_crash(tmp_path):
    cwd = repo(tmp_path / "r", "main")
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    subprocess.run(["git", "-C", cwd, "checkout", "-q", "--detach"], check=True)
    res = pre("git push origin HEAD", cwd=cwd)
    assert res.exit_code == 2 and "safety invariant" not in res.stderr
    assert pre("git push origin HEAD", cwd=str(tmp_path / "missing")).exit_code == 2


def test_f04_remote_default_branch_name_is_resolved(tmp_path):
    cwd = repo(tmp_path / "r", "feat/x")
    subprocess.run(["git", "-C", cwd, "update-ref", "refs/remotes/origin/trunk", "HEAD"], check=False)
    subprocess.run(["git", "-C", cwd, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"], check=True)
    assert _is_si08(pre("git push origin HEAD:trunk", cwd=cwd))
    assert not _is_si08(pre("git push origin HEAD:other", cwd=cwd))


def test_f04_mcp_shell_tool_gets_the_same_resolution(tmp_path):
    cwd = repo(tmp_path / "r", "main")
    res = pre("git -C . push origin HEAD", cwd=cwd, tool="mcp__MacOS-MCP__Shell")
    assert _is_si08(res)


# ---------------------------------------------------------------- F05: write-tool path normalisation

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def write(path: str, cwd: str = "", tool: str = "Write"):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return policy.assess(tool, {key: path}, cwd)


def test_f05_every_spelling_of_a_protected_path_is_high(fake_home, tmp_path):
    h = str(fake_home)
    (tmp_path / "lnk").symlink_to(fake_home / ".claude")
    spellings = [
        h + "/.claude/settings.json",
        h + "/.claude/./settings.json",
        h + "/.claude//settings.json",
        h + "//.claude/settings.json",
        h + "/.claude/../.claude/settings.json",
        h + "/.Claude/Settings.json",
        h + "/.CLAUDE/SETTINGS.JSON",
        "~/.claude/settings.json",
        "~/.claude/./settings.json",
        "~/.CLAUDE/Settings.JSON",
        str(tmp_path / "lnk" / "settings.json"),
        str(tmp_path / "lnk" / ".." / "home" / ".claude" / "settings.json"),
        h + "/.claude/CLAUDE.md",
        h + "/.claude/hooks/x.sh",
        h + "/.claude/settings.local.json",
    ]
    for p in spellings:
        for tool in ("Write", "Edit", "MultiEdit"):
            a = write(p, tool=tool)
            assert a.level == "high" and a.rule_id == "R-13", (p, tool, a)
    assert write(str(tmp_path / "lnk" / "settings.json"), tool="NotebookEdit").rule_id == "R-13"


def test_f05_relative_paths_resolve_against_the_event_cwd(tmp_path):
    root = tmp_path / "work" / "jade"
    (root / "policies").mkdir(parents=True)
    (root / "sub").mkdir()
    for rel in ("policies/safety.yaml", "./policies/safety.yaml", "sub/../policies/safety.yaml", "policies//Safety.yaml", "POLICIES/SAFETY.YAML"):
        a = write(rel, cwd=str(root))
        assert a.rule_id == "R-13", (rel, a)
    assert write("../policies/safety.yaml", cwd=str(root / "sub")).rule_id == "R-13"
    assert write("safety.yaml", cwd=str(root / "policies")).rule_id == "R-13"
    assert write("notes.md", cwd=str(root)).level == "low"


@pytest.mark.parametrize("path", [
    "/work/jade/policies/./safety.yaml", "/work/jade/policies/Safety.yaml", "/work/jade/policies/promotion.yaml",
    "/work/jade/policies/risk.yaml", "/work/jade/policies/local/risk.local.yaml", "/work/jade/policies/loader.py",
    "/work/jade/jade/permits.py", "/work/jade/jade/hooks.py", "/work/jade/jade/ledger.py", "/work/jade/jade/policy.py",
    "/work/jade/jade/shellnorm.py", "/work/jade/jade/decide.py", "/work/jade/jade/state_machine.py",
    "/work/jade/evals/x/protected/a.json", "/work/jade/evals/calibration/protected/deep/b.jsonl",
    "/work/jade/AGENTS.md", "/work/proj/agents.md", "/Users/u/.config/jade/policy.yaml", "/Users/u/.local/share/jade/jade.sqlite",
])
def test_f05_protected_targets_are_high(path):
    assert write(path).rule_id == "R-13", path


@pytest.mark.parametrize("path", [
    "/work/jade/jade/cli.py", "/work/jade/tests/test_x.py", "/work/jade/evals/run_cases.py", "/work/jade/evals/regression/cases.jsonl",
    "/work/jade/docs/05-threat-model.md", "/tmp/notes.txt", "/work/jade/README.md",
    "/work/other/jade/permitsx.py", "/work/jade/jade/hooks.pyc.txt",
])
def test_f05_ordinary_files_stay_low(path):
    assert write(path).level == "low", path


def test_f05_bash_spellings_of_protected_paths(fake_home):
    for cmd in ("sed -i s/a/b/ ~/.claude/./settings.json", "cp x ~/.claude//settings.json", "echo x > ~/.Claude/Settings.json",
                "cp x ~/.CLAUDE/hooks/h.sh", "echo x >> policies//safety.yaml", "echo x > ./policies/./Safety.yaml"):
        assert assess(cmd).rule_id == "R-13", cmd
    assert assess("sudo PFCTL -f /ETC/PF.CONF").level == "critical"
    assert assess("cat ~/.claude/settings.json").level == "low"
    assert assess("git branch -d x").level == "low"  # case-folding is limited to path rules


def test_f05_relative_path_without_cwd_never_raises(fake_home):
    # No cwd given and a relative path: resolved against the process cwd, never raises.
    assert write("settings.json").level in ("low", "high")
    assert os.path.isabs(policy.normalise_path("x", ""))


# ---------------------------------------------------------------- hidden text in other carriers

@pytest.mark.parametrize("cmd", [
    "bash <(echo 'git push origin main')",
    "source <(echo 'git push origin main')",
    "git -c \"alias.p=!git push origin main\" p",
    "git -c alias.p='!git push origin main' p",
    "git -c alias.p=\"!git push origin main\" p",
    "git -c alias.p='!git -C . push origin main' p",
])
def test_hidden_text_in_process_substitution_and_aliases(cmd):
    a = assess(cmd)
    assert "SI-08" in a.blocked, (cmd, a, policy.segments(cmd))


def test_process_substitution_and_pipes_into_non_shells_stay_inert():
    assert assess("diff <(sort a) <(sort b)").level == "low"
    assert assess("cat <(echo git push origin main)").level == "low"


def test_absurd_nesting_degrades_to_text_search_instead_of_crashing():
    cmd = "echo " + "$(" * 400 + "git push origin main" + ")" * 400
    assert "SI-08" in assess(cmd).blocked
    assert assess("x" * 50000).level == "low"
