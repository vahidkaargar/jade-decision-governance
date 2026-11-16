"""Round-6 review of PR 47: the gate is never weaker than origin/main (8100ea1), by construction.

Six rounds of fixes, shape by shape, kept finding commands that origin/main stopped and this branch let through. The gate now reads every command in two readings
(`shellnorm.skeleton_both`): the current one and the one origin/main gave, kept as it was in `jade/shellnorm_base.py`. The segments of both are classified, so the rating
is the stricter of the two and the push check finds a push that either reading shows.

* R6-LEAD-1, R6-RS-1: a leading here-string into an interpreter that prints (`<<<'git push origin main' awk 1 | sh`).
* R6-DP-1: a leading input redirect before a printer (`<&- echo git push origin main | sh`).
* R6-DP-2: a non-printer here-string inside a command substitution (`eval "$(sed p<<<'git push origin main')"`).
* R6-CL-1: a here-string producer in a subshell, a brace group, an `if` or a `for` body, piped to a shell.
* R6-DP-6, R6-DP-7: a heredoc into a shell the splitter does not know; zsh multios.
* R6-BENIGN-1, R6-DP-4, R6-RS-3, R6-CL-2: text that a here-string hands to a program that does not run it is data (`grep -c push <<< 'git push origin main' | wc -l`).
* R6-PERF-1, R6-CL-3, R6-DP-3, R6-DP-5, R6-PERF-2, R6-RS-5: cost of those paths.
"""

import hashlib
import re
import time
from pathlib import Path

import pytest

from jade import hooks, policy, shellnorm
from tests.test_redirect_anywhere import si08
from tests.test_redirect_zsh_and_braces import confirm_real_push

PUSH = "git push origin main"
BASE_FILE = Path(shellnorm.__file__).with_name("shellnorm_base.py")
BASE_SHA256 = "69c0043da8d770690723bd2cd1c05d8db096e19625c8c4cbdd8a1189661401b8"   # `git show 8100ea1:jade/shellnorm.py | shasum -a 256`

# ---------------------------------------------------------------- the floor is origin/main's reading, unchanged


def test_the_floor_is_the_file_of_origin_main_unchanged():
    body = BASE_FILE.read_text().split("\n", 2)[2]   # two comment lines say what the copy is for
    assert hashlib.sha256(body.encode()).hexdigest() == BASE_SHA256


def test_a_command_the_two_readings_spell_alike_is_read_once():
    assert shellnorm.JOIN not in shellnorm.skeleton_both("echo hello && ls -l")


def test_the_two_readings_are_joined_and_each_is_a_command_line_of_its_own(tmp_path):
    (tmp_path / "sub").mkdir()
    text = shellnorm.skeleton_both("cd sub && echo a\\)b ; git push origin feature")
    cur, base = text.split(shellnorm.JOIN)
    assert cur == shellnorm.skeleton("cd sub && echo a\\)b ; git push origin feature")
    assert "git push" in base
    root = str(tmp_path.resolve())
    rows = shellnorm.segments_cwd("cd sub && echo a\\)b ; git push origin feature", root)
    last_cd = max(i for i, (seg, _) in enumerate(rows) if seg == "cd sub")   # the cd of the second reading starts where the command starts
    assert rows[last_cd][1] == (root,)
    assert rows[-1] == ("git push origin feature", (str(tmp_path.resolve() / "sub"),))   # and the push after it is in sub, not in sub/sub


def test_the_join_byte_in_a_command_is_a_word():
    command = "echo a\x17b ; git push origin main"
    assert si08(command)
    assert shellnorm.skeleton_both(command).count(shellnorm.JOIN) <= 1


def test_a_push_that_one_reading_leaves_open_does_not_hide_one_that_the_other_resolves(tmp_path, monkeypatch):
    """`_on_default_branch` answers True for a push found on main even when an earlier push of the command names a repository it cannot resolve."""
    import subprocess
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    command = 'git -C "$R" push origin feature ; git -C ' + str(repo) + " push origin main"
    assert hooks._on_default_branch(str(tmp_path), command)


REDIRECT_PREFIXES = ["&>log", ">&|log", "0<in", "3>log", ">!log", ">log", "2>log", "<in", ">>log", "&>>log", ">|log", "2>&1", "<&-"]


@pytest.fixture
def repo_on_main(tmp_path):
    import subprocess
    r = tmp_path / "repo"
    r.mkdir()
    for argv in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                 ["remote", "add", "origin", "https://example.invalid/x.git"]):
        subprocess.run(["git", "-C", str(r), *argv], check=True)
    return r


@pytest.mark.parametrize("redirect", REDIRECT_PREFIXES)
@pytest.mark.parametrize("tpl", ["{r} git push --force", "git push {r} --force", "git push --force {r}", "{r} git push", "git push {r}", "env {r} git push"])
def test_a_redirect_anywhere_in_a_bare_push_on_main_still_stops_it(redirect, tpl, repo_on_main):
    """CR6-ADD-1: the current-branch half of SI-08 (a push with no destination, on main) was lost when a redirect stood anywhere in the push."""
    command = tpl.format(r=redirect)
    assert hooks._on_default_branch(str(repo_on_main), command), command


# ---------------------------------------------------------------- the criticals of round 6 are blocked

CRITICAL = [
    "<<<'git push origin main' awk 1 | sh",
    "<<<'git push origin :main' awk 1 | sh",
    "<<< 'git push origin main' awk '{print}' | sh",
    "0<<<'git push origin main' awk '{print $0}' | sh",
    "<<<\"git push origin main\" perl -pe '' | sh",
    "<<<'git push origin main' ruby -pe '' | sh",
    "<<<'git push origin main' python3 -c 'import sys;print(sys.stdin.read())' | sh",
    "<<<'git push origin main' gawk 1 | bash",
    "<<<'git push origin main' nawk 1 | zsh",
    "env <<<'git push origin main' awk 1 | sh",
    "x=1 <<<'git push origin main' awk 1 | sh",
    "LC_ALL=C <<<'git push origin main' awk 1 | sh",
    "bash <(<<<'git push origin main' awk 1)",
    "<&- echo git push origin main | sh",
    "0<&- echo git push origin main | sh",
    "<&0 echo git push origin main | sh",
    "0<&0 echo git push origin main | bash",
    "2<&- echo git push origin main | sh",
    "<&0 echo git push origin main | zsh",
    "<&- printf '%s\\n' 'git push origin main' | sh",
    "eval \"$(sed p<<<'git push origin main')\"",
    "sh -c \"$(sed p<<<'git push origin main')\"",
    "bash -c \"$(sed -n p<<<'git push origin main')\"",
    "$(sed 1p<<<'git push origin main')",
    "eval $(sort -u<<<'git push origin main')",
    "eval \"`uniq <<< 'git push origin :main'`\"",
    "(sed p<<<'git push origin main') | sh",
    "{ sed p<<<'git push origin main'; } | sh",
    "if true; then sed p<<<'git push origin main'; fi | sh",
    "for i in 1; do sed p<<<'git push origin main'; done | sh",
    "(uniq <<< 'git push origin :main') | sh",
    "(sort -u<<<'git push origin HEAD:main') | sh",
    "2>&1 sh <<'E' | sort -u<<<'git push origin HEAD:main'\ngit push origin main\nE",
    "2>/dev/null env sh <<'E' | uniq <<< 'git push origin :main'\ngit push origin main\nE",
    "(sh) <<'E' | sed p<<<'git push origin main'\ngit push origin main\nE",
    "nocorrect echo git push origin main | sh <<< x",
    "nocorrect echo git push origin :main | sh <<< x",
    "cat<<<'git push origin main'|sh 2>&1",
    "sed p<<<'git push origin main'|sh 2>&1",
]


@pytest.mark.parametrize("command", CRITICAL)
def test_a_round_6_critical_is_blocked(command):
    assert si08(command), command
    assert policy.assess("Bash", {"command": command}, cwd="/tmp").level in ("high", "critical"), command


@pytest.mark.parametrize("command", [
    "<<<'git push origin main' awk 1 | sh",
    "<&- echo git push origin main | sh",
    "eval \"$(sed p<<<'git push origin main')\"",
    "(sed p<<<'git push origin main') | sh",
    "nocorrect echo git push origin main | sh <<< x",
])
def test_a_round_6_critical_really_pushes(command, tmp_path):
    confirm_real_push(command, tmp_path)


# ---------------------------------------------------------------- text a here-string hands to a program that does not run it is data

DATA = [
    "git commit -F - <<< 'Fix: never git push origin main by hand' | tail -1",
    "grep -c push <<< 'git push origin main' | wc -l",
    "tr a-z A-Z <<< 'git push origin :main' | pbcopy",
    "grep -c push <<< 'git push origin :main' | xargs echo",
    "sed 's/x/y/' <<< 'rm -rf ~' | tee note.txt",
    "sed p <<< 'rm -rf build' | tee log.txt",
    "awk '{print $1}' <<< 'git push origin main' | head -1",
    "wc -w <<< 'git push origin main' | cat",
    "jq -R . <<< 'git push origin main' | less",
    "sort <<< 'git push origin main' | cat > out.txt",
    "base64 <<< 'git push origin main' | md5",
]


@pytest.mark.parametrize("command", DATA)
def test_a_here_string_into_a_program_that_runs_nothing_is_not_a_push(command):
    a = policy.assess("Bash", {"command": command}, cwd="/tmp")
    assert not si08(command), command
    assert a.level == "low", (command, a.level, a.rule_id)


# ---------------------------------------------------------------- cost


def _cost(command: str) -> float:
    shellnorm._skeleton_top.cache_clear()
    shellnorm._skeleton_both_top.cache_clear()
    t = time.process_time()
    policy.assess("Bash", {"command": command}, cwd="/tmp")
    try:
        hooks._on_default_branch("/tmp", command)
    except Exception:   # noqa: BLE001, S110 - git is not asked here
        pass
    return time.process_time() - t


def test_a_here_string_into_a_reader_that_runs_nothing_is_read_once():
    small = _cost("git commit -F - <<< '" + "the quick brown fox; (x) {y} " * 100 + "' | tail -1")
    big = _cost("git commit -F - <<< '" + "the quick brown fox; (x) {y} " * 3500 + "' | tail -1")   # 100 kB
    assert big < 1.0, big
    assert big < max(small, 0.005) * 100, (small, big)   # 35 times the text, nowhere near 35 squared


def test_a_long_pipeline_of_here_string_stages_is_linear():
    def pipe(n: int) -> str:
        return " | ".join(f'grep -v x{i} <<< "$v"' for i in range(n))
    small, big = _cost(pipe(100)), _cost(pipe(400))
    assert big < 1.0, big
    assert big < max(small, 0.01) * 12, (small, big)   # 4 times the stages


def test_a_long_pipeline_into_shells_with_a_descriptor_redirect_is_linear():
    small, big = _cost("a | sh 2>&1 " * 500), _cost("a | sh 2>&1 " * 2000)
    assert big < 2.0, big
    assert big < max(small, 0.02) * 12, (small, big)


def test_nested_here_strings_in_substitutions_cost_the_same_at_every_depth():
    def nest(depth: int, inner: str) -> str:
        for _ in range(depth):
            inner = f'sed p <<< "$({inner})" | wc -l'
        return inner
    assert _cost(nest(16, "echo hi")) < 1.0
    deep = nest(30, PUSH)   # past the depth the scanner reads, the rest is searched as plain text: a push in it still stops the command
    assert _cost(deep) < 1.0
    assert si08(deep)


def test_many_cd_lines_with_an_unbalanced_paren_cost_a_few_times_the_plain_ones(tmp_path):
    (tmp_path / "sub").mkdir()
    odd = "cd sub || exit ; : \\) a{b\n" * 150 + "git push"
    plain = "cd sub || exit ; : a b\n" * 150 + "git push"
    t_odd = min(_cost_in(odd, tmp_path) for _ in range(2))
    t_plain = min(_cost_in(plain, tmp_path) for _ in range(2))
    assert t_odd < max(t_plain, 0.05) * 8, (t_plain, t_odd)


def _cost_in(command: str, cwd) -> float:
    shellnorm._skeleton_top.cache_clear()
    shellnorm._skeleton_both_top.cache_clear()
    t = time.process_time()
    policy.assess("Bash", {"command": command}, cwd=str(cwd))
    return time.process_time() - t


# ---------------------------------------------------------------- the CHANGELOG says no more than the code does


def test_the_changelog_does_not_claim_what_round_6_found_false():
    text = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text()
    assert not re.search(r"for any pipe, whatever follows it", text)
    assert not re.search(r"`cat<<<'\.\.\.'\|sh 2>&1` was caught only by the printer path", text)
