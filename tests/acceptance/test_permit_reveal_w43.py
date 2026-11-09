"""W-43 (T4): a gated command that carries an inline secret can be reviewed and approved by a human without the secret reaching the ledger, the provider
payload or the agent's context.

The ledger still stores the redacted text only. When the human has turned `permit_reveal` on in the JADE config, the hook also keeps the raw call for the length of
the permit in a mode-600 file next to the ledger (never in the ledger, never in an event), and `jade permit show <id> --raw` / `jade permit approve <id>` print it
on a human terminal after checking that it is exactly what the plan hash, the redacted text and the content digest on file cover. SI-09 makes the `--raw` view
human-only. Secret-shaped values are assembled from parts so the repository holds no literal token; the commands are synthetic."""
import json
import os
import stat
from pathlib import Path

import pytest

from jade import cli, hooks, ledger, paths, permits, policy, util

TOKEN = "Zq8vK2mN5p" + "Q8sT1vW4xZ7aB3cD"
GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
CMD = f"kubectl delete ns prod --token={TOKEN}"
SETTINGS = "~/.claude/settings.json"   # R-13, high: a write needs a permit


@pytest.fixture
def human_env(monkeypatch):
    for k in util.AGENT_ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def reveal_on():
    d = Path(os.environ["JADE_CONFIG"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({"permit_reveal": True}))


@pytest.fixture
def human_tty(monkeypatch, human_env):
    """This process is an interactive human terminal for the CLI and the permit module."""
    monkeypatch.setattr(cli, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setattr(permits, "human_tty_refusal", lambda *a, **k: None)


def call(cmd=CMD, session="sess-dddddddd"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": "/tmp/x"}


def pending(db):
    return permits.listing(db, ("PENDING",))[-1]


def everything(db) -> str:
    return "\n".join(db.con.iterdump())


def stash_file(db, p) -> Path:
    return db.path.parent / "reveal" / f"{p.permit_id}.json"


def blocked(cmd: str) -> bool:
    return "SI-09" in policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


# ---- off by default: nothing about the secret is kept, and nothing about approval changes ---------------------
def test_by_default_no_raw_command_is_kept_and_the_old_refusal_stands(human_env):
    res = hooks.pretool(call())
    assert res.exit_code == 2 and TOKEN not in res.stderr
    db = ledger.open()
    p = pending(db)
    assert not (db.path.parent / "reveal").exists() or not any((db.path.parent / "reveal").iterdir())
    with pytest.raises(permits.PermitError, match="secret-shaped"):
        permits.review_raw(db, p)
    with pytest.raises(permits.PermitError, match="secret-shaped"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    assert permits.get(db, p.permit_id).state == "PENDING"


def test_by_default_show_raw_has_nothing_to_show(human_tty, capsys):
    hooks.pretool(call())
    p = pending(ledger.open())
    assert cli.main(["permit", "show", p.permit_id, "--raw"]) == 1
    cap = capsys.readouterr()
    assert TOKEN not in cap.out + cap.err and "secret-shaped" in cap.err


# ---- on: the raw call is held for the human only ----------------------------------------------------------------
def test_the_raw_call_is_held_in_a_private_file_and_never_in_the_ledger_or_the_agents_reply(reveal_on, human_env):
    res = hooks.pretool(call())
    assert res.exit_code == 2
    assert TOKEN not in res.stderr and TOKEN not in res.stdout
    db = ledger.open()
    p = pending(db)
    assert f"jade permit show {p.permit_id} --raw" in res.stderr and f"jade permit approve {p.permit_id}" in res.stderr
    f = stash_file(db, p)
    assert f.is_file() and stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    assert TOKEN in f.read_text()
    assert TOKEN not in everything(db)           # the ledger, its events and its decisions stay redacted
    assert TOKEN not in json.dumps(permits.ctx_of(p))


def test_the_same_call_again_reuses_the_permit_and_keeps_one_stash(reveal_on, human_env):
    hooks.pretool(call())
    hooks.pretool(call())
    db = ledger.open()
    assert len(permits.listing(db, ("PENDING",))) == 1
    assert len(list((db.path.parent / "reveal").iterdir())) == 1


def test_a_command_without_a_secret_keeps_nothing(reveal_on, human_env):
    hooks.pretool(call("git push origin feat-x"))
    db = ledger.open()
    assert not (db.path.parent / "reveal").exists() or not any((db.path.parent / "reveal").iterdir())
    assert "git push origin feat-x" in permits.review_raw(db, pending(db))


def test_show_raw_prints_the_command_in_full_on_a_human_terminal(reveal_on, human_tty, capsys):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    assert cli.main(["permit", "show", p.permit_id, "--raw"]) == 0
    out = capsys.readouterr().out
    assert CMD in out and "secret-shaped" in out and p.plan_hash[:12] in out
    assert permits.get(db, p.permit_id).state == "PENDING"          # looking approves nothing
    assert TOKEN not in everything(ledger.open())


def test_show_without_raw_never_prints_the_secret(reveal_on, human_tty, capsys):
    hooks.pretool(call())
    p = pending(ledger.open())
    assert cli.main(["permit", "show", p.permit_id]) == 0
    assert TOKEN not in capsys.readouterr().out


def test_the_human_approves_then_the_identical_call_runs_and_the_raw_file_is_gone(reveal_on, human_tty, monkeypatch, capsys):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    monkeypatch.setattr("builtins.input", lambda prompt="": permits.confirm_code(p))
    assert cli.main(["permit", "approve", p.permit_id]) == 0
    assert CMD in capsys.readouterr().out
    assert permits.get(db, p.permit_id).state == "ISSUED" and not stash_file(db, p).exists()
    assert hooks.pretool(call()).exit_code == 0
    assert TOKEN not in everything(ledger.open())


def test_a_wrong_code_approves_nothing_and_the_raw_file_stays_until_the_permit_ends(reveal_on, human_tty, monkeypatch):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    monkeypatch.setattr("builtins.input", lambda prompt="": "nope")
    assert cli.main(["permit", "approve", p.permit_id]) == 1
    assert permits.get(db, p.permit_id).state == "PENDING" and stash_file(db, p).exists()


def test_a_changed_command_does_not_ride_the_approval(reveal_on, human_tty, monkeypatch):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(call(CMD + " --force")).exit_code == 2     # another plan hash: the approval does not cover it


def test_content_of_a_write_with_a_secret_is_shown_and_approved_in_full(reveal_on, human_env, tmp_path, monkeypatch):
    """SI-05 refuses a write that carries a token shape outright, so the gate never raises this permit itself; the permit layer is exercised directly with
    a call whose content redacts (the same call the hook would pass)."""
    body = f'{{"env": {{"API_TOKEN": "{GHP}"}}}}'
    tin = {"file_path": SETTINGS, "content": body}
    ev = {"tool_name": "Write", "tool_input": tin, "session_id": "sess-dddddddd", "cwd": "/tmp/x"}
    a = policy.assess("Write", {"file_path": SETTINGS, "content": "x"}, "/tmp/x")
    ctx = permits.make_context("Write", a, ev, cwd="/tmp/x")
    assert ctx["redacted"] and GHP not in json.dumps(ctx)
    db = ledger.open()
    p = permits.request(db, "write.settings", a.plan_hash, risk="high", context=ctx, fingerprints={permits.CONTENT_KIND: policy.content_sha(tin)})
    assert permits.keep_raw(db, p.permit_id, permits.raw_parts("Write", a, ev))
    assert GHP not in everything(db)
    assert GHP in permits.review_raw(db, p) and "shown in full below" in permits.review_raw(db, p)
    assert permits.approve(db, p.permit_id, stdin_isatty=True).state == "ISSUED"
    assert not stash_file(db, p).exists()


def test_a_write_content_that_was_cut_after_redaction_is_refused_when_the_raw_file_differs(reveal_on, human_env):
    tin = {"file_path": SETTINGS, "content": f"token={GHP}"}
    ev = {"tool_name": "Write", "tool_input": tin, "session_id": "s", "cwd": "/tmp/x"}
    a = policy.assess("Write", {"file_path": SETTINGS, "content": "x"}, "/tmp/x")
    ctx = permits.make_context("Write", a, ev, cwd="/tmp/x")
    db = ledger.open()
    p = permits.request(db, "write.settings", a.plan_hash, risk="high", context=ctx, fingerprints={permits.CONTENT_KIND: policy.content_sha(tin)})
    parts = permits.raw_parts("Write", a, ev)
    parts["content"] = parts["content"] + "\ncurl evil"
    assert permits.keep_raw(db, p.permit_id, parts)
    with pytest.raises(permits.PermitError, match="does not match"):
        permits.review_raw(db, p)


def test_an_mcp_call_with_a_secret_in_its_input_is_reviewed_through_the_hook(reveal_on, human_tty, capsys):
    tool = "mcp__GitLab__save_note"   # M-02, high: the plan hash is the whole input
    ev = {"tool_name": tool, "tool_input": {"body": f"password={TOKEN}"}, "session_id": "sess-dddddddd", "cwd": "/tmp/x"}
    res = hooks.pretool(ev)
    assert res.exit_code == 2 and TOKEN not in res.stderr
    db = ledger.open()
    p = pending(db)
    assert TOKEN not in everything(db)
    assert cli.main(["permit", "show", p.permit_id, "--raw"]) == 0
    assert TOKEN in capsys.readouterr().out
    permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(ev).exit_code == 0


# ---- the agent cannot get the raw view ------------------------------------------------------------------------------
def test_show_raw_refuses_without_an_interactive_terminal_and_prints_nothing_secret(reveal_on, human_env, capsys):
    hooks.pretool(call())
    p = pending(ledger.open())
    assert cli.main(["permit", "show", p.permit_id, "--raw"]) == 1       # the test runner's stdin is not a tty
    cap = capsys.readouterr()
    assert TOKEN not in cap.out + cap.err and "human at an interactive terminal" in cap.err


def test_show_raw_refuses_when_an_agent_harness_is_in_the_environment(reveal_on, human_env, monkeypatch, capsys):
    hooks.pretool(call())
    p = pending(ledger.open())
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(cli, "human_tty_refusal", lambda *a, **k: util.human_tty_refusal(True))   # a tty is present; the agent variable is what refuses
    assert cli.main(["permit", "show", p.permit_id, "--raw"]) == 1
    cap = capsys.readouterr()
    assert TOKEN not in cap.out + cap.err and "agent" in cap.err


def test_show_raw_refuses_under_an_agent_process_ancestor(reveal_on, human_env, monkeypatch, capsys):
    hooks.pretool(call())
    p = pending(ledger.open())
    monkeypatch.setattr(util, "agent_ancestor", lambda: "agent process (claude, pid 1)")
    monkeypatch.setattr(cli, "human_tty_refusal", lambda *a, **k: util.human_tty_refusal(True))
    assert cli.main(["permit", "show", p.permit_id, "--raw"]) == 1
    assert TOKEN not in "".join(capsys.readouterr())


def test_the_python_view_needs_no_tty_so_si09_is_what_keeps_an_agent_off_it():
    """`permits.review_raw` is a library call, like `permits.approve`: the CLI checks the terminal and SI-09 blocks the module for an agent (docs/05 T2)."""
    assert blocked("python3 -c 'from jade import permits; print(permits.review_raw)'") and blocked("python3 -c 'import jade.permits'")


# ---- SI-09 blocks every spelling of the raw view, and only that ----------------------------------------------------
RAW = [
    "jade permit show prm_x --raw", "jade permit show --raw prm_x", "jade  permit   show prm_x   --raw", "JADE_X=1 jade permit show prm_x --raw",
    "env -i jade permit show prm_x --raw", "/usr/local/bin/jade permit show prm_x --raw", "~/.local/bin/jade permit show prm_x --raw",
    "uv run jade permit show prm_x --raw", "uvx jade permit show prm_x --raw", "python -m jade.cli permit show prm_x --raw",
    "python3 jade/cli.py permit show prm_x --raw", "jade permit show prm_x --raw | cat", "jade permit show prm_x --raw > /tmp/x",
    "jade permit show prm_x --raw 2>&1", "cd /tmp && jade permit show prm_x --raw", "$J permit show prm_x --raw", "${JADE} permit show prm_x --raw",
    "jade permit $SUB prm_x --raw", "jade permit show $ID --raw", "jade ${X:-permit} show prm_x --raw", "xargs jade permit show --raw",
    "script -q /dev/null jade permit show prm_x --raw",
]


@pytest.mark.parametrize("cmd", RAW)
def test_si09_blocks_the_raw_view(cmd):
    assert blocked(cmd), cmd


HARMLESS = [
    "jade permit show prm_x", "jade permit list", "jade permit list --all", "jade permit show prm_x | jq .state", "jade check",
    "jade permit approve --help", "jade permit request --command 'ls'", "rg 'permit show --raw' docs", "git log --grep 'permit show --raw'",
    "git commit -m 'docs: describe jade permit show --raw'", "sed -i 's/permit show --raw/x/' README.md", "kubectl get pods --raw /healthz",
    "curl --raw example.test", "cat docs/08-deployment-runbook.md", "pytest -k permit_reveal", "uv run pytest tests/acceptance/test_permit_reveal_w43.py -q",
    "echo show --raw", "docker logs jade-svc --raw",
]


@pytest.mark.parametrize("cmd", HARMLESS)
def test_si09_leaves_near_misses_alone(cmd):
    assert not blocked(cmd), cmd


def test_the_raw_flag_is_exact_not_an_abbreviation(reveal_on, human_tty, capsys):
    """An abbreviation SI-09 does not list must not be accepted by the parser."""
    hooks.pretool(call())
    p = pending(ledger.open())
    with pytest.raises(SystemExit):
        cli.main(["permit", "show", p.permit_id, "--ra"])
    assert TOKEN not in "".join(capsys.readouterr())


# ---- what is on file must be exactly what the permit covers ---------------------------------------------------------
def _rewrite(db, p, **changes):
    f = stash_file(db, p)
    doc = json.loads(f.read_text())
    doc.update(changes)
    f.write_text(json.dumps(doc))


def test_a_raw_file_whose_command_is_not_the_redacted_one_is_refused(reveal_on, human_env):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    _rewrite(db, p, subject=f"kubectl delete ns other --token={TOKEN}", hash_subject=f"kubectl delete ns other --token={TOKEN}")
    for fn in (lambda: permits.review_raw(db, p), lambda: permits.approve(db, p.permit_id, stdin_isatty=True)):
        with pytest.raises(permits.PermitError, match="does not match"):
            fn()
    assert permits.get(db, p.permit_id).state == "PENDING"


def test_a_raw_file_that_does_not_hash_to_the_plan_is_refused(reveal_on, human_env):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    _rewrite(db, p, hash_subject=f"kubectl delete ns prod --token={TOKEN}x")   # redacts to the same text, hashes to another plan
    with pytest.raises(permits.PermitError, match="plan hash|does not match"):
        permits.review_raw(db, p)


def test_a_raw_file_that_is_unreadable_or_names_another_permit_is_refused(reveal_on, human_env):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    stash_file(db, p).write_text("{not json")
    with pytest.raises(permits.PermitError, match="secret-shaped"):
        permits.review_raw(db, p)
    stash_file(db, p).write_text(json.dumps({"permit_id": "prm_000000000000", "subject": CMD, "hash_subject": CMD, "expires_at": "2999-01-01T00:00:00Z"}))
    with pytest.raises(permits.PermitError, match="secret-shaped"):
        permits.review_raw(db, p)


def test_an_expired_raw_file_is_not_shown_and_is_deleted(reveal_on, human_env):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    _rewrite(db, p, expires_at="2000-01-01T00:00:00Z")
    with pytest.raises(permits.PermitError, match="secret-shaped"):
        permits.review_raw(db, p)
    assert not stash_file(db, p).exists()


def test_a_permit_id_that_is_a_path_never_reaches_the_filesystem(reveal_on, human_env, tmp_path):
    db = ledger.open()
    outside = db.path.parent / "reveal" / ".." / "secret.json"
    assert permits.reveal_file(db, "../secret") is None and permits.reveal_file(db, str(outside)) is None
    assert permits.reveal_file(db, "prm_0123456789ab") == db.path.parent / "reveal" / "prm_0123456789ab.json"


def test_a_secret_command_too_long_to_show_is_still_refused_even_with_a_raw_file(reveal_on, human_env):
    big = f"kubectl delete ns prod --token={TOKEN} " + "x" * (permits.MAX_SUBJECT + 10)
    hooks.pretool(call(big))
    db = ledger.open()
    p = pending(db)
    with pytest.raises(permits.PermitError, match="cannot be shown in full"):
        permits.review_raw(db, p)


# ---- the raw file lives only as long as the permit can still be approved --------------------------------------------
def test_revoking_deletes_the_raw_file(reveal_on, human_env):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    assert stash_file(db, p).exists() and permits.revoke(db, p.permit_id, "test")
    assert not stash_file(db, p).exists()


def test_the_sweep_deletes_the_raw_file_of_a_permit_that_expired_or_is_gone(reveal_on, human_env):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    orphan = db.path.parent / "reveal" / "prm_aaaaaaaaaaaa.json"
    orphan.write_text("{}")
    with db.tx() as con:
        con.execute("UPDATE permits SET expires_at='2000-01-01T00:00:00Z' WHERE permit_id=?", (p.permit_id,))
    permits.sweep(db)
    assert not stash_file(db, p).exists() and not orphan.exists()


def test_the_sweep_keeps_the_raw_file_of_a_live_pending_permit(reveal_on, human_env):
    hooks.pretool(call())
    db = ledger.open()
    p = pending(db)
    permits.sweep(db)
    assert stash_file(db, p).exists()


def test_a_failing_write_of_the_raw_file_degrades_to_the_refusal_and_never_leaks(reveal_on, human_env, monkeypatch):
    monkeypatch.setattr(permits, "stash_raw", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    res = hooks.pretool(call())
    assert res.exit_code == 2 and TOKEN not in res.stderr and "environment variable" in res.stderr
    assert TOKEN not in everything(ledger.open())


def test_the_reveal_setting_is_read_from_the_human_config_only(human_env):
    assert paths.config().get("permit_reveal") is None
    assert permits.reveal_enabled() is False
