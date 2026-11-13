"""Review cluster B, second pass: what a verifier found after the first fix (F06, F07 and six new defects).

Each test reproduces one thing against the real hook, CLI or module with an isolated home (tests/conftest.py) and no network."""
import json
import os
import subprocess
import time

import pytest

from jade import cli, hooks, ledger, permits, policy, util

REAL_ANCESTOR = util.agent_ancestor  # tests/conftest.py replaces util.agent_ancestor for every test; this is the real one
PUSH = "git push origin v1-feat-x"
CRITICAL = "kubectl delete namespace production"


def call(cmd, cwd="/tmp/x", session="sess-aaaaaaaa", agent=None):
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": cwd}
    if agent:
        ev["agent_id"] = agent
    return ev


def pending(db):
    return permits.listing(db, ("PENDING",))[-1]


def approved(cmd=PUSH, **kw):
    db = ledger.open()
    assert hooks.pretool(call(cmd, **kw)).exit_code == 2
    p = pending(db)
    permits.approve(db, p.permit_id, stdin_isatty=True)
    return db, p


def _fake_ps(monkeypatch, rows):
    """A synthetic process tree: rows of (pid, ppid, command) or (pid, ppid, executable, arguments); a command is split at its first space."""
    table = {}
    for row in rows:
        pid, ppid, *rest = row
        exe, args = (rest[0], rest[1]) if len(rest) == 2 else (rest[0].partition(" ")[0], rest[0].partition(" ")[2])
        table[pid] = util.Proc(ppid, exe, args)
    monkeypatch.setattr(util, "read_process_table", lambda: table)


# ---- F06: the ancestor check matches the program, not any word on the command line --------------------------------
@pytest.mark.parametrize("ancestor", [
    "vim /Users/x/src/claude-code/notes.md", "node /Users/x/claude-code-fork/build.js", "pytest /src/claude-code/tests -q",
    "/bin/zsh -c cd ~/claude-code && ./run.sh", "python3 -m http.server --directory /Users/x/claude-code/", "tail -f /var/log/claude-code/agent.log",
])
def test_f06_a_human_shell_under_a_path_that_names_claude_code_is_not_an_agent(monkeypatch, ancestor):
    me = os.getpid()
    _fake_ps(monkeypatch, [(me, 500, "python -m jade permit approve"), (500, 400, "-zsh"), (400, 300, ancestor), (300, 1, "/sbin/launchd")])
    assert REAL_ANCESTOR() is None


@pytest.mark.parametrize("ancestor", [
    "node /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/cli.js --resume", "/Users/x/.local/bin/claude", "bun /x/node_modules/@openai/codex/bin/codex.js",
    "/Users/x/.local/share/claude/versions/2.1.5 --dangerously-skip-permissions", "/usr/local/bin/cursor-agent", "npx @anthropic-ai/claude-code",
])
def test_f06_the_programs_that_run_an_agent_are_still_found(monkeypatch, ancestor):
    me = os.getpid()
    _fake_ps(monkeypatch, [(me, 500, "python -m jade permit approve"), (500, 400, "-zsh"), (400, 1, ancestor)])
    assert "agent process" in REAL_ANCESTOR()


def test_f06_the_real_process_table_parses_and_never_raises():
    why = REAL_ANCESTOR()  # no fake: the real `ps -axo pid=,ppid=,command=` output of this machine
    assert why is None or isinstance(why, str)
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, check=True).stdout
    assert any(line.split(None, 2)[0] == str(os.getpid()) for line in out.splitlines() if line.strip())


@pytest.mark.parametrize("cmd", ["ap=approve; jade permit $ap prm_x", "A='approve'; $J permit $A prm_x", "x=approve jade permit $x"])
def test_f06_a_variable_set_to_approve_is_blocked(cmd):
    assert "SI-09" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


@pytest.mark.parametrize("cmd", [
    "pytest /Users/x/Documents/projects/jade/tests -k approve", "docker exec jade-svc approve", "ln -s /opt/x/jade /usr/local/bin/jade2",
    "uv run pytest tests/acceptance -k 'approve or permit'", "git commit -m 'jade: approve flow'", "grep approve jade/permits.py", "mode=install make",
])
def test_f06_si09_does_not_block_harmless_commands_that_only_sit_near_jade_or_approve(cmd):
    assert "SI-09" not in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


@pytest.mark.parametrize("cmd", ["ln -s ~/.local/bin/jade /tmp/j", "ln -s /Users/x/.local/share/uv/tools/jade-decision-governance/bin/jade /tmp/j", "jade --json permit approve prm_x", "jade$IFS permit approve x"])
def test_f06_si09_still_blocks_wrappers_of_the_installed_launcher(cmd):
    assert "SI-09" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


def _approve_cli(monkeypatch, capsys, pid, *, answer, yes=False):
    monkeypatch.setattr(cli, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setattr(permits, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    rc = cli.main(["permit", "approve", pid, *(["-y"] if yes else [])])
    return rc, capsys.readouterr()


def test_f06_dash_y_is_refused_at_every_risk_level_and_the_help_says_so(monkeypatch, capsys):
    db = ledger.open()
    p = permits.request(db, "git.push", "f" * 64, risk="medium")  # request() does not cap the level: a lower permit floor could make one
    rc, out = _approve_cli(monkeypatch, capsys, p.permit_id, answer="y", yes=True)
    assert rc == 1 and "-y is not accepted" in out.err and permits.get(db, p.permit_id).state == "PENDING"
    with pytest.raises(SystemExit):
        cli.main(["permit", "approve", "--help"])
    assert "skip the confirmation" not in capsys.readouterr().out


def test_f06_human_approval_required_at_is_read_by_the_code(monkeypatch):
    db = ledger.open()
    row = None
    p = permits.issue(db, "act.medium", "a" * 64, risk="medium")  # an agent-issued permit, sealed
    row = db.one("SELECT * FROM permits WHERE permit_id=?", (p.permit_id,))
    doc = json.loads(row.doc_json)
    assert policy.human_floor() == "high"
    assert permits._static_refusal(db, row, doc, "medium") is None  # default: a medium command needs no human
    real = policy.risk()
    monkeypatch.setattr(policy, "risk", lambda: {**real, "human_approval_required_at": "medium"})
    assert policy.human_floor() == "medium"
    assert "human approved" in permits._static_refusal(db, row, doc, "medium")
    monkeypatch.setattr(policy, "risk", lambda: {**real, "human_approval_required_at": "critical"})
    assert policy.human_floor() == "high"  # never above high


def test_f06_the_residual_is_documented_with_the_verified_variant():
    text = open(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "05-threat-model.md")).read()
    assert "setsid" in text and "double fork" in text and "W-38" in text and "permit-seal.key" in text


# ---- F07: a permit row edited with SQL after issue is refused ------------------------------------------------------
def _forge(db, permit_id, **fields):
    row = db.one("SELECT * FROM permits WHERE permit_id=?", (permit_id,))
    doc = json.loads(row.doc_json)
    doc.update(principal=f"human:{util.user()}", approval_reference=f"tty:{util.user()}@{util.ts()}", risk_level="critical", **fields)
    db.con.execute("UPDATE permits SET risk_level='critical', doc_json=? WHERE permit_id=?", (json.dumps(doc), permit_id))


def test_f07_a_low_permit_rewritten_by_sql_as_a_human_critical_approval_is_refused(human_env):
    db = ledger.open()
    a = policy.assess("Bash", {"command": CRITICAL}, "/tmp/x")
    p = permits.issue(db, "kubectl.delete", a.plan_hash, risk="low")
    _forge(db, p.permit_id)
    r = hooks.pretool(call(CRITICAL))
    assert r.exit_code == 2 and permits.get(db, p.permit_id).state == "ISSUED"
    row = db.one("SELECT * FROM permits WHERE permit_id=?", (p.permit_id,))
    assert "seal" in permits._static_refusal(db, row, json.loads(row.doc_json), "critical")  # the reason the hook ignores it


def test_f07_a_forged_row_with_no_seal_at_all_is_refused(human_env):
    db = ledger.open()
    a = policy.assess("Bash", {"command": CRITICAL}, "/tmp/x")
    p = permits.issue(db, "kubectl.delete", a.plan_hash, risk="low")
    _forge(db, p.permit_id)
    row = db.one("SELECT doc_json FROM permits WHERE permit_id=?", (p.permit_id,))
    doc = json.loads(row.doc_json)
    doc.pop("seal")
    db.con.execute("UPDATE permits SET doc_json=? WHERE permit_id=?", (json.dumps(doc), p.permit_id))
    assert hooks.pretool(call(CRITICAL)).exit_code == 2


@pytest.mark.parametrize("sql", [
    "UPDATE permits SET expires_at='2099-01-01T00:00:00Z' WHERE permit_id=?",
    "UPDATE permits SET plan_hash='{h}' WHERE permit_id=?",
    "UPDATE permits SET scope='{{}}' WHERE permit_id=?",
    "UPDATE permits SET decision_id='dec_forged' WHERE permit_id=?",
])
def test_f07_editing_an_approved_permit_row_breaks_its_seal(human_env, sql):
    db, p = approved()
    other = policy.assess("Bash", {"command": "git push origin v1-feat-y"}, "/tmp/x")
    db.con.execute(sql.format(h=other.plan_hash), (p.permit_id,))
    cmd = "git push origin v1-feat-y" if "plan_hash" in sql else PUSH
    r = hooks.pretool(call(cmd))
    assert r.exit_code == 2 and permits.get(db, p.permit_id).state in ("ISSUED", "REVOKED")


def test_f07_a_consumed_permit_set_back_to_issued_is_not_used_twice(human_env):
    db, p = approved()
    assert hooks.pretool(call(PUSH)).exit_code == 0
    db.con.execute("UPDATE permits SET state='ISSUED', uses=0 WHERE permit_id=?", (p.permit_id,))
    r = hooks.pretool(call(PUSH))
    assert r.exit_code == 2 and "already used" in r.stderr


def test_f07_without_the_seal_key_no_permit_is_used_and_the_hook_does_not_make_one(human_env):
    db, p = approved()
    key = ledger.open().path.parent / permits.SEAL_KEY_FILE
    assert key.exists() and oct(key.stat().st_mode & 0o777) == "0o600"
    key.unlink()
    r = hooks.pretool(call(PUSH))
    assert r.exit_code == 2 and not key.exists()
    assert permits.get(db, p.permit_id).state == "ISSUED"


def test_f07_an_honest_approval_is_used_once(human_env):
    db, p = approved()
    assert hooks.pretool(call(PUSH)).exit_code == 0
    assert hooks.pretool(call(PUSH)).exit_code == 2


@pytest.mark.parametrize("cmd", ["cat ~/.local/share/jade/permit-seal.key", "head -c 8 permit-seal.key", "python3 -c \"open('permit-seal.key','rb')\"",
                                 "cp $JADE_HOME/permit-seal.key /tmp/k"])
def test_f07_naming_the_seal_key_is_blocked(cmd):
    assert "SI-10" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked
    r = hooks.pretool(call(cmd))
    assert r.exit_code == 2 and "SI-10" in r.stderr


def test_f07_docs_state_what_the_seal_does_not_stop():
    text = open(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "05-threat-model.md")).read()
    assert "seal" in text and "same OS user" in text


# ---- new: find_usable chooses by caller ---------------------------------------------------------------------------
def test_g08_an_older_permit_of_this_session_is_used_when_another_session_has_a_newer_one(human_env):
    db = ledger.open()
    assert hooks.pretool(call(PUSH, session="sess-aaaaaaaa")).exit_code == 2
    a = pending(db)
    permits.approve(db, a.permit_id, stdin_isatty=True)
    time.sleep(1.1)  # issued_at has a resolution of one second
    assert hooks.pretool(call(PUSH, session="sess-bbbbbbbb")).exit_code == 2
    b = [x for x in permits.listing(db, ("PENDING",)) if x.permit_id != a.permit_id][-1]
    permits.approve(db, b.permit_id, stdin_isatty=True)
    assert permits.get(db, b.permit_id).issued_at > permits.get(db, a.permit_id).issued_at
    r = hooks.pretool(call(PUSH, session="sess-aaaaaaaa"))
    assert r.exit_code == 0 and a.permit_id in r.stderr
    assert permits.get(db, a.permit_id).state == "CONSUMED" and permits.get(db, b.permit_id).state == "ISSUED"
    r = hooks.pretool(call(PUSH, session="sess-bbbbbbbb"))
    assert r.exit_code == 0 and permits.get(db, b.permit_id).state == "CONSUMED"


# ---- new: two sessions do not revoke each other's pending request ---------------------------------------------------
def test_g08_two_sessions_retrying_do_not_invalidate_each_others_pending_permit(human_env):
    db = ledger.open()
    hooks.pretool(call(PUSH, session="sess-aaaaaaaa"))
    first = pending(db)
    for _ in range(3):  # both agents retry between human approvals
        hooks.pretool(call(PUSH, session="sess-bbbbbbbb"))
        hooks.pretool(call(PUSH, session="sess-aaaaaaaa"))
    assert permits.get(db, first.permit_id).state == "PENDING"
    assert len(permits.listing(db, ("PENDING",))) == 2
    permits.approve(db, first.permit_id, stdin_isatty=True)  # the id the human was shown is still valid
    assert hooks.pretool(call(PUSH, session="sess-aaaaaaaa")).exit_code == 0


def test_g08_a_pending_permit_whose_policy_or_precondition_changed_is_still_superseded(human_env, monkeypatch):
    db = ledger.open()
    hooks.pretool(call(PUSH))
    first = pending(db)
    monkeypatch.setattr(paths_mod(), "policy_version", lambda: "0" * 12)
    hooks.pretool(call(PUSH))
    assert permits.get(db, first.permit_id).state == "REVOKED"


def paths_mod():
    from jade import paths

    return paths


# ---- new: plan_hash trims only what a shell does not read ---------------------------------------------------------
@pytest.mark.parametrize("tail", [" ", "　", "\r", "\x0b", "\x1f", "\x0c", " "])
def test_plan_hash_does_not_cover_a_command_with_a_non_shell_whitespace_character_appended(tail):
    base = policy.assess("Bash", {"command": "rm -rf /x/y"}, "/tmp").plan_hash
    assert policy.assess("Bash", {"command": "rm -rf /x/y" + tail}, "/tmp").plan_hash != base
    assert policy.assess("Bash", {"command": tail + "rm -rf /x/y"}, "/tmp").plan_hash != base


@pytest.mark.parametrize("tail", [" ", "\t", "\n", "  \n"])
def test_plan_hash_still_trims_a_space_tab_and_newline_at_the_ends(tail):
    base = policy.assess("Bash", {"command": "rm -rf /x/y"}, "/tmp").plan_hash
    assert policy.assess("Bash", {"command": "rm -rf /x/y" + tail}, "/tmp").plan_hash == base
    assert policy.assess("Bash", {"command": tail + "rm -rf /x/y"}, "/tmp").plan_hash == base


# ---- new: a NUL byte in a command ------------------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", ["git push origin v1-feat\x00x", "rm -rf /etc/x\x00y", "cd /tmp\x00x && ls", "echo a\x00b", "kubectl delete\x00 namespace production"])
def test_a_nul_byte_in_a_command_never_raises_in_the_classifier(cmd):
    a = policy.assess("Bash", {"command": cmd}, "/tmp")
    assert a.level in ("low", "medium", "high", "critical")
    assert policy.assess("Bash", {"command": cmd.replace("\x00", "")}, "/tmp").level == a.level


def test_a_nul_byte_does_not_hide_the_risk_and_the_hook_does_not_fail_closed_on_it(human_env):
    r = hooks.pretool(call("git push origin v1-feat\x00x"))
    assert r.exit_code == 2 and "Failing closed" not in r.stderr and "needs a human-approved permit" in r.stderr
    assert hooks.pretool(call("echo a\x00b")).exit_code == 0


def test_permit_request_with_a_nul_byte_in_the_command_does_not_crash(capsys):
    assert cli.main(["permit", "request", "--command", "git push origin v1-feat\x00x", "--cwd", "/tmp/x"]) == 0
    p = pending(ledger.open())
    assert "\\x00" in permits.review_text(p)  # the approval screen writes the NUL out
