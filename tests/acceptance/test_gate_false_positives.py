"""Task G: fewer false positives of the command gate, none of the protection lost.

Every class of false positive the replay of real commands showed has a test here, next to its true-positive twin (the same words, but a
command that really does the thing). The commands are synthetic. The classes:

1. text inside a heredoc body or an inline program of a NON-shell interpreter (python, perl, ruby, node, php, awk, osascript, lua): only the literal
   commands it runs and the literal paths it writes count, every other string is data. A shell interpreter's body stays fully scanned.
2. a file written by `cat > f <<EOF` or `printf ... > f`: the destination is what counts, not the text.
3. R-16 / R-13 firing on reads, fetches, greps, git read verbs and substring coincidences (the checkout of this project is itself a directory named
   `jade`, so every relative operand became `.../jade/<operand>`), while a directory destination without a trailing slash and the redirect target
   or output option of a read-only command are protected.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jade import interp, policy
from jade.util import LEVELS

ROOT = Path(__file__).resolve().parents[2]
PUSH = "git push origin main"


def level(cmd, cwd="/tmp/case"):
    return policy.assess("Bash", {"command": cmd}, cwd)


def heredoc(code, prog="python3 -"):
    return f"{prog} <<'EOF'\n{code}\nEOF"


def gated(a):
    return bool(a.blocked) or LEVELS[a.level] >= LEVELS["high"]


@pytest.fixture
def checkout(tmp_path):
    """A checkout whose root directory is itself called `jade`, with the package inside, as on the machine this runs on."""
    root = tmp_path / "work" / "jade"
    (root / "jade").mkdir(parents=True)
    for name in ("__init__", "hooks", "policy", "util"):
        (root / "jade" / f"{name}.py").write_text("")
    (root / "skills" / "jade").mkdir(parents=True)
    (root / "tests").mkdir()
    return root


# ------------------------------------------------------------------ 1. the interpreter's body: what it does, not what it says

PROSE = "\"\"\"Notes: systemctl start x; pfctl -d; git push origin main; ssh host; launchctl load y; git -c core.pager=less log; rm -rf /; jade permit approve\"\"\""
PROSE_PROGRAMS = {
    "python-heredoc": heredoc(f"{PROSE}\nprint('hook jade firewall systemctl start x')"),
    "python-c": "python3 -c \"print('sudo pfctl -d; git push origin main; systemctl start x')\"",
    "python-comment": heredoc("# systemctl start x, firewall, git push origin main\nx = 1"),
    "python-message": heredoc("msg = 'git push origin main'\nprint(msg)"),
    "python-reads": heredoc("import json, os\nprint(open('/etc/pf.conf').read())\nd = json.load(open(os.path.expanduser('~/.claude/settings.json')))"),
    "perl": "perl -e 'print \"git push origin main; sudo pfctl -d\\n\"'",
    "ruby": "ruby -e 'puts \"git push origin main\"'",
    "node": "node -e \"console.log('git push origin main; systemctl start x')\"",
    "php": "php -r 'echo \"git push origin main\";'",
    "applescript": "osascript -e 'display dialog \"git push origin main\"'",
    "lua": "lua -e 'print(\"git push origin main\")'",
    "awk-comparison": "awk 'NR>1 && $1 > 5' ~/.claude/settings.json",
    "awk-print": "awk '{print $1, \"git push origin main\"}' in.txt",
    "piped-python": "echo 'print(\"git push origin main\")' | python3 -",
}


@pytest.mark.parametrize("name", PROSE_PROGRAMS)
def test_text_a_program_only_mentions_is_data(name):
    a = level(PROSE_PROGRAMS[name])
    assert a.level == "low" and not a.blocked, (name, a.rule_id, a.blocked)


def test_a_shell_body_stays_fully_scanned():
    for cmd in (heredoc(PUSH, "sh -"), heredoc(PUSH, "bash"), f"bash -c '{PUSH}'", f"sh <<'EOF'\n{PUSH}\nEOF", f"zsh -c \"{PUSH}\""):
        assert gated(level(cmd)), cmd
    assert level(heredoc("systemctl start x", "bash")).rule_id == "R-12"


EXEC_TWINS = {   # the same words as PROSE, but the program runs them
    "python subprocess list": (heredoc("import subprocess\nsubprocess.run(['git', 'push', 'origin', 'main'])"), "SI-08"),
    "python subprocess shell": (heredoc(f"import subprocess\nsubprocess.run('{PUSH}', shell=True)"), "SI-08"),
    "python os.system": (heredoc(f"import os\nos.system('{PUSH}')"), "SI-08"),
    "python -c os.system": (f"python3 -c \"import os; os.system('{PUSH}')\"", "SI-08"),
    "python alias import": (heredoc(f"from os import system as s\ns('{PUSH}')"), "SI-08"),
    "python getattr": (heredoc(f"import os\ngetattr(os, 'system')('{PUSH}')"), "SI-08"),
    "python __import__": (f"python3 -c \"__import__('os').system('{PUSH}')\"", "SI-08"),
    "python built from pieces": (heredoc("import os\nc = 'git push'\nos.system(c + ' origin ' + 'main')"), "SI-08"),
    "python percent format": (heredoc("import os\nos.system('git push origin %s' % 'main')"), "SI-08"),
    "python exec of a string": (heredoc(f"exec(\"import os\\nos.system('{PUSH}')\")"), "SI-08"),
    "python execvp": (heredoc("import os\nos.execvp('git', ['git', 'push', 'origin', 'main'])"), "SI-08"),
    "python popen": (heredoc(f"import subprocess as sp\nsp.Popen('{PUSH}', shell=True)"), "SI-08"),
    "python function wrapper": (heredoc(f"import os\ndef run(c):\n    os.system(c)\nrun('{PUSH}')"), "SI-08"),
    "python loop over commands": (heredoc(f"import os\nfor c in ['echo a', '{PUSH}']:\n    os.system(c)"), "SI-08"),
    "python started shell reads its input": (heredoc(f"import subprocess\nsubprocess.run(['sh'], input=b'{PUSH}')"), "SI-08"),
    "python syntax error falls back to patterns": (heredoc(f"import os\nos.system('{PUSH}'\n"), "SI-08"),
    "perl system list": ("perl -e 'system(\"git\", \"push\", \"origin\", \"main\")'", "SI-08"),
    "perl exec": (f"perl -e 'exec \"{PUSH}\"'", "SI-08"),
    "perl backticks": (f"perl -e '`{PUSH}`'", "SI-08"),
    "ruby system": (f"ruby -e 'system(\"{PUSH}\")'", "SI-08"),
    "ruby backticks": (f"ruby -e '`{PUSH}`'", "SI-08"),
    "node execSync": (f"node -e \"require('child_process').execSync('{PUSH}')\"", "SI-08"),
    "node spawn argv": ("node -e \"require('child_process').spawn('git', ['push', 'origin', 'main'])\"", "SI-08"),
    "bun shell": (f"bun -e 'await $`{PUSH}`'", "SI-08"),
    "deno eval": ("deno eval 'Deno.run({cmd: [\"git\", \"push\", \"origin\", \"main\"]})'", "SI-08"),
    "php shell_exec": (f"php -r 'shell_exec(\"{PUSH}\");'", "SI-08"),
    "osascript do shell script": (f"osascript -e 'do shell script \"{PUSH}\"'", "SI-08"),
    "lua os.execute": (f"lua -e 'os.execute(\"{PUSH}\")'", "SI-08"),
    "awk system": (f"awk 'BEGIN{{system(\"{PUSH}\")}}'", "SI-08"),
    "awk system of a variable": (f"awk 'BEGIN{{c=\"{PUSH}\"; system(c)}}'", "SI-08"),
    "python piped": (f"echo \"import os; os.system('{PUSH}')\" | python3 -", "SI-08"),
    "python here-string": (f"python3 <<< \"import os; os.system('{PUSH}')\"", "SI-08"),
    "python under uv run": (f"uv run python - <<'EOF'\nimport os\nos.system('{PUSH}')\nEOF", "SI-08"),
    "python from a pipe into a heredoc consumer": (f"cat <<'EOF' | python3\nimport os\nos.system('{PUSH}')\nEOF", "SI-08"),
}


@pytest.mark.parametrize("name", EXEC_TWINS)
def test_a_program_that_really_runs_the_command_is_blocked_as_before(name):
    cmd, inv = EXEC_TWINS[name]
    assert inv in level(cmd).blocked, name


RULE_TWINS = [   # (command a program runs or writes, the rule that must still rate it)
    (heredoc("import os\nos.system('systemctl start x')"), "R-12"),
    (heredoc("import subprocess\nsubprocess.run(['systemctl', 'start', 'x'])"), "R-12"),
    (heredoc("import os\nos.system('rm -rf /')"), "R-11"),
    (heredoc("import subprocess\nsubprocess.run(['sudo', 'pfctl', '-d'])"), "R-01"),
    (heredoc("import subprocess\nsubprocess.run(['git', '-c', 'core.pager=less', 'log'])"), "R-15"),
    ("perl -e 'system(\"sudo pfctl -d\")'", "R-01"),
    ("awk '{print > \"/etc/pf.conf\"}' in.txt", "R-01"),
    ("awk '{print | \"tee /etc/pf.conf\"}' in.txt", "R-01"),
    (heredoc("open('/Users/x/.claude/settings.json', 'w').write('{}')"), "R-13"),
    (heredoc("import os\nopen(os.path.expanduser('~/.claude/settings.json'), 'a').write('x')"), "R-13"),
    (heredoc("from pathlib import Path\nPath.home().joinpath('.claude', 'settings.json').write_text('x')"), "R-13"),
    (heredoc("from pathlib import Path\n(Path.home() / '.claude' / 'settings.json').write_text('x')"), "R-13"),
    (heredoc("import shutil\nshutil.copy('x', '/Users/x/.claude/settings.json')"), "R-13"),
    (heredoc("import os\nos.rename('/tmp/x', '/Users/x/.claude/settings.json')"), "R-13"),
    (heredoc("p = '/Users/x/.claude/settings.json'\nopen(p, 'w').write('x')"), "R-13"),
    (heredoc("def edit(path, a, b):\n    s = open(path).read()\n    open(path, 'w').write(s.replace(a, b))\nedit('policies/risk.yaml', 'a', 'b')"), "R-13"),
    (heredoc("for p in ['notes.txt', 'policies/safety.yaml']:\n    open(p, 'w').write('x')"), "R-13"),
    (heredoc("open('jade/util.py', 'w').write('x')"), "R-16"),
    (heredoc("import pathlib\npathlib.Path('jade/util.py').write_text('x')"), "R-16"),
    (heredoc("import sqlite3\nsqlite3.connect('/Users/x/.local/share/jade/jade.sqlite').execute('delete from permits')"), "R-13"),
    ("node -e \"require('fs').writeFileSync('/etc/pf.conf', 'x')\"", "R-01"),
    ("node -e \"const p = '/Users/x/.claude/settings.json'; require('fs').writeFileSync(p, 'x')\"", "R-13"),
    ("perl -e 'open(my $fh, \">\", \"/etc/pf.conf\"); print $fh 1'", "R-01"),
    ("ruby -e 'File.write(\"/etc/pf.conf\", \"x\")'", "R-01"),
    ("php -r 'file_put_contents(\"/etc/pf.conf\", \"x\");'", "R-01"),
]


@pytest.mark.parametrize("cmd,rule", RULE_TWINS)
def test_what_a_program_really_does_is_rated_by_the_same_rules(cmd, rule):
    a = level(cmd)
    assert a.rule_id == rule and LEVELS[a.level] >= LEVELS["high"], (a.rule_id, a.level)


@pytest.mark.parametrize("cmd", [
    "python3 -c \"from jade import cli; cli.main(['permit', 'approve', 'x'])\"", heredoc("import jade.cli"), heredoc("from jade import evals, cli"),
    heredoc("import importlib\nimportlib.import_module('jade.permits')"), heredoc("import jade\njade.cli.main()"),
    "python3 -c \"import pty; pty.spawn(['jade', 'permit', 'approve', 'prm_x', '-y'])\"",
])
def test_calling_the_human_only_code_from_python_stays_blocked(cmd):
    assert "SI-09" in level(cmd).blocked, cmd


def test_raw_text_invariants_still_read_a_whole_program():
    secret = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2"
    assert "SI-05" in level(heredoc(f"print('{secret}')")).blocked
    assert "SI-10" in level(heredoc("print(open('permit-seal.key').read())")).blocked


# ------------------------------------------------------------------ the interpreter reader itself

def test_python_values_are_folded_through_assignments_and_calls():
    fx = interp.effects("python", "import os, pathlib\nb = os.path.expanduser('~')\nd = pathlib.Path(b) / '.claude'\nopen(f'{d}/settings.json', 'w')\n"
                                  "os.system('git ' + 'status')\nsubprocess.run(['git', 'log', '--oneline'])")
    assert fx.writes == ["~/.claude/settings.json"] and fx.commands == ["git status", "git log --oneline"]


def test_python_unknown_parts_become_x_and_other_strings_are_ignored():
    fx = interp.effects("python", "import subprocess, sys\nsubprocess.run(['git', 'push', 'origin', sys.argv[1]])\nprint('rm -rf /')\nx = 'systemctl start y'")
    assert fx.commands == ["git push origin $X"] and not fx.writes


def test_python_functions_and_literal_loops_are_followed():
    src = "def w(p, text='x'):\n    open(p, 'w').write(text)\nw('a.txt')\nfor name in ('b.txt', 'c.txt'):\n    w(name)\n"
    assert interp.effects("python", src).writes == ["a.txt", "b.txt", "c.txt"]


def test_python_reads_are_not_writes():
    src = "import json, shutil, os\njson.load(open('a'))\nopen('b', 'r').read()\nshutil.copy('c', '/tmp/d')\nos.path.exists('e')\nopen('f', mode='rb')"
    assert interp.effects("python", src).writes == ["/tmp/d"]


def test_python_started_shell_reads_every_string_as_input():
    fx = interp.effects("python", "import subprocess\nsubprocess.Popen(['bash']).communicate(b'echo a b')\nnote = 'one'")
    assert fx.shell_fed and "echo a b" in fx.commands and "one" not in fx.commands


def test_other_languages_use_literal_arguments_only():
    assert interp.effects("perl", "system('git', 'push'); print 'rm -rf /';").commands == ["git push"]
    assert interp.effects("ruby", "system('git push'); puts 'rm -rf /'").commands == ["git push"]
    assert interp.effects("js", "exec('git push'); console.log('rm -rf /')").commands == ["git push"]
    assert interp.effects("js", "fs.writeFileSync('/tmp/a', 'x'); fs.readFileSync('/tmp/b')").writes == ["/tmp/a"]
    assert interp.effects("php", "file_put_contents('/tmp/a', 'x'); echo 'rm -rf /';").writes == ["/tmp/a"]


def test_awk_literal_sinks_and_dynamic_ones():
    fx = interp.awk_effects("{print > \"/tmp/a\"; print $1 | \"sort\"; system(\"ls\")}")
    assert fx.writes == ["/tmp/a"] and sorted(fx.commands) == ["ls", "sort"] and not fx.dynamic
    assert interp.awk_effects("{print > $1\".txt\"}").dynamic
    assert not interp.awk_effects("NR > 1 && $2 > 5 {print $1}").dynamic


def test_language_names():
    for word, lang in (("python3.13", "python"), ("/usr/bin/perl", "perl"), ("nodejs", "js"), ("bun", "js"), ("osascript", "applescript"), ("php8", "php")):
        assert interp.language_of(word) == lang
    assert interp.language_of("bash") is None


# ------------------------------------------------------------------ 2. a file written by a heredoc or printf: the destination counts

def test_file_content_is_data_and_the_destination_is_what_counts():
    for body in ("systemctl start x", "pfctl -d", "policies/risk.yaml jade/util.py ~/.claude/settings.json"):
        assert level(f"cat > notes.md <<'EOF'\n{body}\nEOF").level == "low", body
        assert level(f"cat >> notes.md <<EOF\n{body}\nEOF").level == "low", body
    assert level("printf 'policies/local/*.yaml\\n!policies/local/*.example.yaml\\n' > .gitignore").level == "low"
    assert level("echo 'AGENTS.md jade/hooks.py' >> notes.txt").level == "low"
    assert level("cat > policies/risk.yaml <<'EOF'\nx\nEOF").rule_id == "R-13"
    assert level("cat > ~/.claude/settings.json <<'EOF'\n{}\nEOF").rule_id == "R-13"
    assert level("cat > jade/util.py <<'EOF'\nx\nEOF").rule_id == "R-16"
    assert level("echo x >& ~/.claude/settings.json").rule_id == "R-13"
    assert level("printf x &> ~/.claude/settings.json").rule_id == "R-13"
    assert level("echo x >| ~/.claude/settings.json").rule_id == "R-13"


# ------------------------------------------------------------------ 3. R-16 and R-13 fire on writes, not on mentions

READS_AND_COINCIDENCES = [
    "git add AGENTS.md", "git add jade/hooks.py CLAUDE.md", "git commit -m x AGENTS.md", "git diff AGENTS.md", "git status policies/", "git check-ignore policies/local/risk.local.yaml",
    "cp ~/.claude/settings.json /tmp/b", "cp -p ~/.local/bin/jev /tmp/jev.orig", "rsync -a ~/.claude/settings.json /tmp/backup/", "tar czf /tmp/x.tgz ~/.claude/settings.json",
    "grep -n a AGENTS.md", "/usr/bin/grep -n a AGENTS.md", "/bin/cat policies/risk.yaml", "head -3 .ai/rules.md",
    "for f in settings.json settings.local.json; do echo $f; done", "f=~/.claude/settings.json", "case $x in *.php) echo php;; esac",
    "mkdir -p policies/local", "mkdir -p ~/.config/jade/local",
    "~/.local/bin/jade check", "uv run jade work list", "git fetch origin '+refs/heads/*:refs/remotes/origin/*'", "git branch -a --list '*retire*'",
    "chmod 644 *.php *.txt", "ls *.py", "git log --oneline -- jade/",
]


@pytest.mark.parametrize("cmd", READS_AND_COINCIDENCES)
def test_reads_and_substring_coincidences_stay_low(cmd, checkout):
    for cwd in (str(checkout), "/tmp/case"):
        a = level(cmd, cwd)
        assert a.level == "low" and not a.blocked, (cmd, cwd, a.rule_id)


def test_the_directory_that_holds_the_checkout_is_not_the_package(checkout):
    root = str(checkout)
    for cmd in ("cp /tmp/x .", "cp /tmp/x setup.py", "cp /tmp/x *.php", "cp /tmp/x ..", "touch hooks.py", "echo x > util.py", "sed -i '' s/a/b/ hooks.py",
                "cp /tmp/x skills/jade", "rm -f tests/x.py", "python3 conftest.py", "uv init --bare"):
        assert level(cmd, root).level == "low", cmd
    assert level("cp /tmp/x jade/hooks.py", root).rule_id == "R-16"
    assert level("cd jade && cp /tmp/x util.py", root).rule_id == "R-16"
    assert level(f"cp /tmp/x {root}/jade/hooks.py", "/tmp/case").rule_id == "R-16"
    assert level("echo x > hooks.py", str(checkout / "jade")).rule_id == "R-16"   # the cwd is the package itself


@pytest.mark.parametrize("cmd", [
    "cp /tmp/hooks.py jade", "cp /tmp/hooks.py ./jade", "mv /tmp/hooks.py jade", "install -t jade /tmp/hooks.py", "cp -t jade x", "cp --target-directory=jade x",
    "rsync -a x jade", "tar xf a.tar -C jade", "tar -C jade -xf a.tar", "unzip a.zip -d jade", "ditto a jade", "cd jade && cp /tmp/hooks.py .",
    "cd jade && cp /tmp/hooks.py ./", "cp /tmp/hooks.py {root}/jade",
])
def test_a_directory_destination_spelled_without_a_trailing_slash_is_gated(cmd, checkout):
    a = level(cmd.format(root=checkout), str(checkout))
    assert a.rule_id == "R-16" and LEVELS[a.level] >= LEVELS["high"], (cmd, a.rule_id)


@pytest.mark.parametrize("cmd", [
    "git show HEAD:evil.py > jade/hooks.py", "git diff > jade/hooks.py", "git log -p >> jade/hooks.py", "pytest > jade/hooks.py", "uv run ruff check . > jade/hooks.py",
    "git show HEAD:evil.py > {root}/jade/hooks.py", "cat a.py > jade/hooks.py", "echo x > jade/hooks.py", "git diff 2> jade/hooks.py",
])
def test_the_redirect_target_of_a_read_only_command_is_a_write(cmd, checkout):
    a = level(cmd.format(root=checkout), str(checkout))
    assert a.rule_id == "R-16" and LEVELS[a.level] >= LEVELS["high"], (cmd, a.rule_id)


@pytest.mark.parametrize("cmd", [
    "ruff check --output-file=jade/hooks.py .", "ruff check --output-file jade/hooks.py .", "pytest --junitxml=jade/hooks.py", "pytest --junit-xml jade/hooks.py",
    "flake8 --output-file jade/x.py", "mypy --html-report jade/ .", "pylint --output=jade/x.py jade", "bandit -o jade/x.py -r .",
    "uv run pytest --junitxml jade/x.py", "ruff check --cache-dir jade/_c/ .",
])
def test_the_output_option_of_a_checker_is_a_write_target(cmd, checkout):
    a = level(cmd, str(checkout))
    assert a.rule_id == "R-16" and LEVELS[a.level] >= LEVELS["high"], (cmd, a.rule_id)


@pytest.mark.parametrize("cmd", [
    "ruff check jade/policy.py", "ruff check --output-format=json jade", "pytest jade/ -q", "pytest --junitxml=/tmp/r.xml jade", "mypy --html-report /tmp/m jade",
    "flake8 --output-file /tmp/f.txt jade/", "bandit -o /tmp/b.txt -r jade",
])
def test_a_checker_that_writes_its_report_elsewhere_stays_low(cmd, checkout):
    assert level(cmd, str(checkout)).level == "low", cmd


@pytest.mark.parametrize("cmd,rule", [
    ("cp /tmp/x ~/.claude/settings.json", "R-13"), ("mv ~/.claude/settings.json /tmp/x", "R-13"), ("rm ~/.claude/settings.json", "R-13"),
    ("sed -i '' s/a/b/ AGENTS.md", "R-13"), ("tee .ai/rules.md", "R-13"), ("cp /tmp/x ~/.claude/hooks", "R-13"), ("ln -sf /tmp/x ~/.local/bin/jade", "R-13"),
    ("cat /tmp/x > ~/.local/bin/jev", "R-13"), ("sqlite3 ~/.local/share/jade/jade.sqlite 'delete from permits'", "R-13"), ("jade ledger purge", "R-13"),
    ("uv tool install --force .", None), ("chmod 000 ~/.config/jade", "R-13"), ("python3 ~/.claude/hooks/x.py", "R-13"),
])
def test_writes_to_protected_files_are_still_gated(cmd, rule, tmp_path, monkeypatch):
    (tmp_path / "home" / ".claude" / "hooks").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    a = level(cmd, str(tmp_path))
    if rule:
        assert a.rule_id == rule and LEVELS[a.level] >= LEVELS["high"], (cmd, a.rule_id)


def test_assignments_and_loops_are_not_commands_but_their_expansions_are():
    assert level("x=$(git push origin main)").blocked == ["SI-08"]
    assert level("ap=approve; jade permit $ap prm_x").blocked
    assert level("for f in a b; do git push origin main; done").blocked == ["SI-08"]
    assert level("case $x in a) rm -rf ~/Documents;; esac").rule_id == "R-11"


def test_a_quoted_syntax_character_is_data():
    assert level("grep -E 'a|b' ~/.claude/settings.json").level == "low"
    assert level("echo 'a;git push origin main'").level == "low"
    assert level("git commit -m 'fix;git push origin main'").level == "low"
    assert level("echo a; git push origin main").blocked == ["SI-08"]


# ------------------------------------------------------------------ the replay script

def test_gate_replay_reports_levels_and_rules_without_printing_a_command(tmp_path):
    secretish = "uniquemarker-7f3a"
    items = [f"ls {secretish}", f"echo {secretish}", "git push origin feature/x", "ssh host uptime", heredoc("print('systemctl start x')"),
             {"command": "git status", "cwd": "/tmp"}, {"command": "rm -rf ~/gone", "tool": "Bash"}]
    jsonl = tmp_path / "c.jsonl"
    jsonl.write_text("\n".join(json.dumps(i) for i in items) + "\n")
    as_array = tmp_path / "c.json"
    as_array.write_text(json.dumps(items))
    outputs = []
    for f in (jsonl, as_array):
        run = subprocess.run([sys.executable, str(ROOT / "evals" / "gate_replay.py"), str(f), "--no-overlay", "--save", str(tmp_path / f"{f.stem}.levels")],
                             capture_output=True, text=True, check=True, cwd=ROOT)
        outputs.append(run.stdout)
        assert secretish not in run.stdout and "systemctl" not in run.stdout and "git push" not in run.stdout
    assert outputs[0].split("latency")[0] == outputs[1].split("latency")[0]
    out = outputs[0]
    assert "commands: 7" in out and "high+critical: 3" in out and "outside R-14,R-10: 1" in out and "p95" in out and "R-14" in out and "R-10" in out
    saved = json.loads((tmp_path / "c.levels").read_text())
    assert len(saved) == 7 and saved[2] == ["high", "R-10"] and saved[4] == ["low", "R-99"] and secretish not in json.dumps(saved)
    again = subprocess.run([sys.executable, str(ROOT / "evals" / "gate_replay.py"), str(jsonl), "--no-overlay", "--compare", str(tmp_path / "c.levels")],
                           capture_output=True, text=True, check=True, cwd=ROOT).stdout
    assert "no command changed level or rule" in again


def test_gate_replay_rejects_an_item_that_is_not_a_command(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"cmd": "ls"}]))
    run = subprocess.run([sys.executable, str(ROOT / "evals" / "gate_replay.py"), str(bad)], capture_output=True, text=True, cwd=ROOT)
    assert run.returncode != 0 and "string `command`" in run.stderr
