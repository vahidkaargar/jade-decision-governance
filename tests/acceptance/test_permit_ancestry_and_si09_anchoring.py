"""Round H, part 1: the ancestor check of permit approval works on the real desktop layout (a path with a space), SI-09 is anchored to the spellings of
the approving invocation, the permit seal key fails closed, and SI-10 sees a glob of the key file's name.

The process tree is synthetic (`util.read_process_table` is replaced); nothing here reads or changes the machine's real processes.
"""

import os
import subprocess

import pytest

from jade import ledger, permits, policy, util

REAL_ANCESTOR = util.agent_ancestor  # tests/conftest.py replaces util.agent_ancestor for every test; this is the real one
APP_SUPPORT = "/Users/x/Library/Application Support/Claude/claude-code/2.1.317/91184d3d9ba3"
AGENT_BIN = f"{APP_SUPPORT}/claude.app/Contents/MacOS/claude"
DISCLAIMER = "/Applications/Claude.app/Contents/Helpers/disclaimer"
DESKTOP = "/Applications/Claude.app/Contents/MacOS/Claude"


def tree(monkeypatch, chain):
    """chain: executables from this process upward as (exe, args). This process is pid 1000 and the last entry is a child of launchd."""
    me = os.getpid()
    pids = [me, *range(5000, 5000 + len(chain) - 1)]
    table = {}
    for i, (exe, args) in enumerate(chain):
        table[pids[i]] = util.Proc(pids[i + 1] if i + 1 < len(chain) else 1, exe, args)
    monkeypatch.setattr(util, "read_process_table", lambda: table)
    return table


ME = ("/usr/bin/python3", "-m jade permit approve")


# ---- the process table is read without splitting a path on its spaces ---------------------------------------------
def _fake_ps(monkeypatch, comm_rows, command_rows):
    def run(argv, **k):
        out = comm_rows if "pid=,ppid=,comm=" in argv[-1] else command_rows
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", run)


def test_the_table_keeps_a_path_with_spaces_whole_and_separates_the_arguments(monkeypatch):
    me = os.getpid()
    comm = f"{me} 700 /bin/zsh\n700 600 {AGENT_BIN}\n600 1 {DISCLAIMER}\n"
    command = f"{me} 700 /bin/zsh -c run\n700 600 {AGENT_BIN} --output-format stream-json --verbose\n600 1 {DISCLAIMER} --pgroup -- x\n"
    _fake_ps(monkeypatch, comm, command)
    table = util.read_process_table()
    assert table[700] == util.Proc(600, AGENT_BIN, "--output-format stream-json --verbose")
    assert table[600].exe == DISCLAIMER and table[me].ppid == 700
    assert "agent process (claude, pid 700)" in REAL_ANCESTOR()


def test_a_command_whose_argv0_differs_from_the_executable_still_parses(monkeypatch):
    """macOS prints the real path in comm; a symlink or a bare name in argv[0] is dropped from the arguments."""
    me = os.getpid()
    comm = f"{me} 700 /bin/zsh\n700 1 /opt/homebrew/bin/node\n"
    command = f"{me} 700 -zsh\n700 1 node /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/cli.js --resume\n"
    _fake_ps(monkeypatch, comm, command)
    table = util.read_process_table()
    assert table[700].args.startswith("/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/cli.js")
    assert "agent process" in REAL_ANCESTOR()


def test_linux_style_truncated_comm_takes_the_executable_from_the_command_line(monkeypatch):
    me = os.getpid()
    comm = f"{me} 700 python3\n700 1 cursor-agent\n"
    command = f"{me} 700 python3 -m jade\n700 1 /usr/local/bin/cursor-agent --x\n"
    _fake_ps(monkeypatch, comm, command)
    assert util.read_process_table()[700] == util.Proc(1, "/usr/local/bin/cursor-agent", "--x")


def test_an_unreadable_table_and_one_that_lacks_this_process_both_refuse(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(subprocess, "run", boom)
    assert "process table could not be read" in REAL_ANCESTOR()
    monkeypatch.setattr(util, "read_process_table", lambda: {4242: util.Proc(1, "/bin/zsh")})
    assert "does not list this process" in REAL_ANCESTOR()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="not a table\n", stderr=""))
    assert REAL_ANCESTOR()   # nothing parsed: this process is missing, so the answer is a refusal


# ---- recognising the agent by executable name and bundle path ------------------------------------------------------
@pytest.mark.parametrize("agent", [
    (AGENT_BIN, "--output-format stream-json"),                                       # the desktop app's agent, a path with a space
    (f"{APP_SUPPORT}/claude", "--resume"),
    ("/Users/x/Library/Application Support/Claude/claude-code/2.2.0/aa/anything-else", ""),   # whatever binary the app unpacks there
    (DISCLAIMER, "--pgroup -- claude"),                                               # the app's launcher of the agent
    ("/Users/x/.local/bin/claude", "--dangerously-skip-permissions"),                  # the CLI
    ("/Users/x/.local/share/claude/versions/2.1.5", ""),
    ("/opt/homebrew/bin/node", "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/cli.js"),
    ("/opt/homebrew/bin/node", "/Users/x/My Projects/node_modules/@anthropic-ai/claude-agent-sdk/cli.js --print"),   # the agent SDK, a script path with a space
    ("/Users/x/app/node_modules/@anthropic-ai/claude-agent-sdk/vendor/claude", ""),
    ("/usr/local/bin/codex", "exec"), ("/usr/bin/bun", "/x/node_modules/@openai/codex/bin/codex.js"), ("/usr/local/bin/cursor-agent", ""),
    ("/usr/local/bin/aider", "--model x"), ("/usr/local/bin/opencode", ""), ("/usr/local/bin/gemini", ""), ("/usr/local/bin/goose", "session"),
    ("/usr/bin/python3", "/Users/x/Library/Application Support/Claude/claude-code/2.1.317/a/claude --x"),
    ("/opt/homebrew/bin/npx", "@anthropic-ai/claude-code"),
])
def test_an_agent_ancestor_is_found_whatever_the_path_looks_like(monkeypatch, agent):
    tree(monkeypatch, [ME, ("/bin/zsh", "-c run"), agent, ("/sbin/launchd", "")])
    assert "agent process" in REAL_ANCESTOR()


@pytest.mark.parametrize("agent", [(AGENT_BIN, ""), (DISCLAIMER, ""), ("/Users/x/.local/bin/claude", "")])
def test_the_agent_is_found_far_up_the_tree_and_the_reason_names_it(monkeypatch, agent):
    chain = [ME, ("/bin/zsh", ""), ("/usr/bin/script", "-q"), ("/bin/bash", ""), ("/usr/bin/env", "x"), agent, ("/Applications/Claude.app/Contents/MacOS/Claude", "")]
    tree(monkeypatch, chain)
    assert "it runs under an agent process" in REAL_ANCESTOR()


@pytest.mark.parametrize("human", [
    ("/Applications/Claude.app/Contents/MacOS/Claude", ""),                                   # the desktop app itself: its terminal panel is the human's
    ("/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper", "--type=utility"),
    ("/Users/x/Library/Application Support/Code/bin/code", ""),                                # a path with a space that is not an agent
    ("/usr/bin/vim", "/Users/x/Library/Application Support/Claude/notes.md"),
    ("/usr/bin/vim", "/Users/x/src/claude-code/notes.md"), ("/usr/local/bin/pytest", "/src/claude-code/tests -q"),
    ("/usr/bin/python3", "-m http.server --directory /Users/x/claude-code/"), ("/usr/bin/tail", "-f /var/log/claude-code/agent.log"),
    ("/usr/bin/login", "-pf x"), ("/usr/bin/tmux", "new"), ("/usr/bin/ssh", "host"),
])
def test_a_human_terminal_chain_is_not_an_agent(monkeypatch, human):
    tree(monkeypatch, [ME, ("-zsh", ""), human, ("/sbin/launchd", "")])
    assert REAL_ANCESTOR() is None


def test_the_agent_check_ignores_this_process_itself(monkeypatch):
    """`python -m jade ...` run by a human, even from a checkout whose path names claude, is not an agent."""
    tree(monkeypatch, [("/usr/bin/python3", "/Users/x/claude/jade/cli.py permit approve"), ("-zsh", ""), ("/usr/bin/login", "")])
    assert REAL_ANCESTOR() is None


def test_approval_is_refused_under_the_desktop_agent_with_a_pty_and_a_clean_environment(monkeypatch, human_env):
    tree(monkeypatch, [ME, ("/usr/bin/python3", "pty_helper.py"), ("/bin/zsh", "-c run"), (AGENT_BIN, "--output-format stream-json"), (DISCLAIMER, ""),
                       (DESKTOP, "")])
    monkeypatch.setattr(util, "agent_ancestor", REAL_ANCESTOR)
    db = ledger.open()
    p = permits.request(db, "git.push", "f" * 64, risk="high")
    with pytest.raises(permits.ApprovalRefused, match="agent process"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    assert permits.get(db, p.permit_id).state == "PENDING"


def test_the_real_table_of_this_machine_parses_with_a_path_that_holds_spaces():
    table = util.read_process_table()
    assert os.getpid() in table and all(isinstance(p, util.Proc) and isinstance(p.exe, str) for p in table.values())
    assert 1 in table or os.getpid() in table


# ---- SI-09 is anchored to the approving invocation ----------------------------------------------------------------
def si09(cmd: str) -> bool:
    return "SI-09" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


HARMLESS = [
    "gh pr review --approve", "gh pr review 12 --approve -b ok", "gh pr review $N --approve", "gh pr review --approve $URL && gh pr merge 12",
    "ACTION=approve ./deploy.sh", "ACTION=approve make release", "export ACTION=approve", "kubectl certificate approve $CSR",
    "kubectl --kubeconfig $KC certificate approve csr-1", "curl -X POST https://example.test/api/approve -d \"$BODY\"",
    "jade permit approve --help", "jade permit approve -h", "jade permit approve prm_x --help", "uv run jade permit approve --help",
    "python -m jade.cli check", "python3 -m jade.cli permit list", "python -m jade.cli ledger stats", "python -m jade.cli permit approve --help",
    "pytest -k jade", "pytest -k 'jade and approve'", "uv run pytest tests -k 'permit or approve'", "pytest tests/test_permits.py -k permit_approve",
    "jade check", "jade permit list", "jade permit show prm_x", "jade claude-md show", "jade hooks status", "jade gate status", "jade git-hook check .",
    "sed -i 's/permit approve/x/' README.md", "git log --grep 'permit approve'", "rg 'permit approve' docs", "git commit -m 'jade: approve the permit flow'",
    "echo $HOME approve", "docker exec jade-svc approve", "ln -s /opt/x/jade /usr/local/bin/jade2",
]


@pytest.mark.parametrize("cmd", HARMLESS)
def test_si09_does_not_block_harmless_commands_near_jade_approve_or_a_variable(cmd):
    assert not si09(cmd)


APPROVING = [
    "jade permit approve prm_x", "jade  permit   approve prm_x", "JADE_X=1 jade permit approve prm_x", "env -i jade permit approve prm_x",
    "/Users/x/.local/bin/jade permit approve prm_x", "~/.local/bin/jade permit approve prm_x", "uv run jade permit approve prm_x", "uvx jade permit approve prm_x",
    "python -m jade permit approve prm_x", "python3 -m jade.cli permit approve prm_x", "python jade/cli.py permit approve prm_x",
    "sh -c 'jade permit approve prm_x'", "bash -c \"jade permit approve prm_x\"", "nohup jade permit approve prm_x &", "sudo jade permit approve prm_x",
    "time jade permit approve prm_x", "command jade permit approve prm_x", "(jade permit approve prm_x)", "{ jade permit approve prm_x; }",
    "cd /x && jade permit approve prm_x", "echo y | jade permit approve prm_x", "jade permit approve prm_x </dev/null", "jade --home /x permit approve prm_x",
    "script -q /dev/null jade permit approve prm_x", "xargs jade permit approve < ids",
    "jade 'permit' 'approve' prm_x", "jade permit \"app\"\"rove\" prm_x", "jade permit app\\rove prm_x", "jade ${X:-permit} approve prm_x",
    "$J permit approve prm_x", "$J $P approve prm_x", "$J ${P:-permit} approve prm_x", "j=jade; $j permit approve prm_x",
    "ap=approve; jade permit $ap prm_x", "A='approve'; $J permit $A prm_x", "x=approve jade permit $x prm_x", "export A=approve; $J permit $A prm_x",
    "jade permit ${X:-approve} prm_x", "jade permit $(echo approve) prm_x", "jade permit `echo approve` prm_x",
    "alias ja=jade", "f() { jade permit approve \"$1\"; }", "ln -s ~/.local/bin/jade /tmp/j", "ja permit approve prm_x",
    "jade hooks install", "jade hooks uninstall", "jade gate off", "jade gate advisory", "jade gate enforce", "jade claude-md apply", "$JADE hooks install",
    "python -m jade.cli hooks install", "python jade/cli.py gate off", "python -m jade.cli $ACTION", "python -m jade.cli permit $A prm_x",
    "python3 -c \"import jade.cli as c; c.main(['permit','approve','prm_x'])\"", "python3 -c \"from jade.cli import main; main()\"",
    "python3 -c \"from jade import permits\"", "python3 -c \"import jade.permits as p\"", "python -c \"import runpy; runpy.run_module('jade.cli')\"",
]


@pytest.mark.parametrize("cmd", APPROVING)
def test_si09_still_blocks_every_spelling_of_the_approving_invocation(cmd):
    assert si09(cmd)


def test_si09_help_exemption_is_scoped_to_one_segment():
    assert si09("jade permit approve --help; jade permit approve prm_x")
    assert si09("jade permit approve prm_x && jade check --help")


# ---- the permit seal key ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("content", [b"", b"short", b"x" * 31])
def test_an_empty_or_short_seal_key_fails_closed_with_a_clear_error(content, human_env):
    db = ledger.open()
    p = permits.request(db, "git.push", "f" * 64, risk="high")
    key = db.path.parent / permits.SEAL_KEY_FILE
    key.write_bytes(content)
    with pytest.raises(permits.PermitError, match=rf"seal key .*permit-seal.key is {len(content)} bytes"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    assert permits.get(db, p.permit_id).state == "PENDING"
    assert key.read_bytes() == content   # never silently replaced: every permit sealed with the old key would fail
    with pytest.raises(permits.PermitError, match="seal key"):
        permits.issue(db, "git.push", "e" * 64)


def test_a_short_key_never_verifies_a_permit_and_never_raises_a_type_error(human_env):
    db = ledger.open()
    p = permits.request(db, "git.push", "f" * 64, risk="high")
    permits.approve(db, p.permit_id, stdin_isatty=True)
    (db.path.parent / permits.SEAL_KEY_FILE).write_bytes(b"")
    assert permits.find_usable(db, "f" * 64, level="high") is None


def test_a_missing_key_is_made_whole_and_leaves_no_temp_file(human_env):
    db = ledger.open()
    p = permits.request(db, "git.push", "f" * 64, risk="high")
    permits.approve(db, p.permit_id, stdin_isatty=True)
    key = db.path.parent / permits.SEAL_KEY_FILE
    assert len(key.read_bytes()) == 32 and oct(key.stat().st_mode & 0o777) == "0o600"
    assert [f.name for f in db.path.parent.iterdir() if f.name.endswith(".tmp")] == []


# ---- SI-10 also sees a glob of the key file's name ----------------------------------------------------------------
def si10(cmd: str) -> bool:
    return "SI-10" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


@pytest.mark.parametrize("cmd", [
    "cat ~/.local/share/jade/permit-seal.key", "cat ~/.local/share/jade/permit-\"se\"al.key", "cat $JADE_HOME/permit-s*", "cat ~/.local/share/jade/*seal.key",
    "cat ~/.local/share/jade/permit-seal.k?y", "cat ~/.local/share/jade/permit-[s]eal.key", "cat ~/.local/share/jade/permit-{seal,x}.key",
    "xxd ~/.local/share/jade/permit-*", "cp ~/.local/share/jade/permit-s* /tmp/x",
])
def test_si10_blocks_the_literal_name_and_a_glob_that_names_the_file(cmd):
    assert si10(cmd)


@pytest.mark.parametrize("cmd", [
    "ls ~/.local/share/jade/*", "rm tmp/*.key", "ls *seal*", "cat notes/*.md", "cat **permit**", "echo '**permit**'", "ls permit*", "ls src/*.py", "[ -f x ]",
    "cat ~/.local/share/jade/permits.log", "cat docs/permit-lifecycle.md",
])
def test_si10_leaves_ordinary_globs_and_prose_alone(cmd):
    assert not si10(cmd)
