"""Cluster A2, second pass: the variants a verifier found after the first fix.

Own code as a protected target (R-16, TTY gate in util.py), calling the human-only code from Python (SI-09), launchctl remove/stop/kill,
camelCase and `browser_`-prefixed MCP names, the `action` argument of a one-tool-many-verbs server, interface-only tools, SI-02/SI-05 regex
gaps and the narrowed content scan, and SI-07 keyed by the canonical command. Commands are only classified, never run. Secrets and bind
addresses are assembled from parts so that the repository holds no token-shaped or wildcard-bind literal.
"""

import json
from pathlib import Path

import pytest

from jade import decide, hooks, ledger, permits, policy
from jade.util import LEVELS, fold

SESSION = "sess-a2-0003"
ROOT = Path(__file__).resolve().parents[2]
WILD = "0.0" + ".0.0"
OLLAMA = "114" + "34"
ALT = "80" + "80"


def sec(*parts):
    return "".join(parts)


def bash(cmd, **extra):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": str(ROOT), **extra}


def call(tool, tool_input, **extra):
    return {"tool_name": tool, "tool_input": tool_input, "session_id": SESSION, "cwd": str(ROOT), **extra}


def level(cmd, cwd=None):
    return policy.assess("Bash", {"command": cmd}, cwd if cwd is not None else str(ROOT))


# ---------------------------------------------------------------- G00: the TTY gate and the rest of the package are protected

MODULES = ["util", "cli", "redact", "improve", "evals", "adapter", "compat", "ask", "route", "thresholds", "work", "__init__", "permits", "hooks"]


@pytest.mark.parametrize("module", MODULES)
def test_every_module_of_the_package_is_a_protected_write_target(module):
    path = str(ROOT / "jade" / f"{module}.py")
    for tool, key in (("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
        a = policy.assess(tool, {key: path, "content": "x", "new_string": "x"}, str(ROOT))
        assert LEVELS[a.level] >= LEVELS["high"], (tool, module, a)
        assert hooks.pretool(call(tool, {key: path, "content": "x"})).exit_code == 2


JADE_CODE_WRITES = [
    "sed -i '' s/is_human_tty/x/ jade/util.py", "cd {root} && sed -i '' s/x/y/ jade/util.py", "cd {root} && sed -i '' s/x/y/ jade/cli.py",
    "perl -pi -e 's/a/b/' jade/util.py", "echo x > jade/util.py", "echo x >> {root}/jade/improve.py", "cp /tmp/x jade/util.py", "cp /tmp/x {root}/jade/evals.py",
    "mv /tmp/x jade/cli.py", "mv jade/util.py /tmp/gone", "rm jade/util.py", "touch jade/ops.py", "truncate -s 0 jade/util.py", "tee jade/improve.py < /tmp/x",
    "install -m 644 /tmp/x jade/redact.py", "dd if=/tmp/x of=jade/util.py", "ln -sf /tmp/x jade/util.py", "chmod 000 jade/util.py",
    "python3 -c \"open('jade/util.py','w').write('x')\"", "cd {root}/jade && sed -i '' s/x/y/ util.py", "cd jade && echo x > util.py",
    "git checkout HEAD -- jade/util.py", "git mv jade/hooks.py jade/hooks2.py", "ruff check --fix jade/util.py", "ruff format jade/util.py",
    "tar xzf /tmp/x.tgz jade/util.py", "patch jade/util.py /tmp/p", "git apply --include=jade/util.py /tmp/p",
    "uv run python -c \"import pathlib; pathlib.Path('jade/util.py').write_text('x')\"", "sed -i '' s/x/y/ ./jade//util.py", "sed -i '' s/x/y/ jade/../jade/util.py",
    "sed -i '' s/x/y/ JADE/UTIL.PY",
]


@pytest.mark.parametrize("cmd", JADE_CODE_WRITES)
def test_bash_that_can_write_jade_code_needs_a_permit(cmd):
    cmd = cmd.format(root=ROOT)
    a = level(cmd)
    assert LEVELS[a.level] >= LEVELS["high"], a
    assert hooks.pretool(bash(cmd)).exit_code == 2


JADE_CODE_READS = [
    "git add jade/hooks.py", "git add -A jade/util.py", "git diff jade/hooks.py", "git commit -m x jade/util.py", "git restore --staged jade/hooks.py",
    "ruff check jade/policy.py", "uv run ruff check jade/hooks.py", "uv run --quiet ruff check jade/util.py", "ruff format --check jade/util.py",
    "python -m pytest jade/hooks.py", "python3 -m mypy jade/policy.py", "python -m py_compile jade/hooks.py", "uv run pytest -q jade/util.py",
    "cp jade/hooks.py /tmp/hooks.bak", "cp -a jade/util.py ~/Desktop/", "rsync -a jade/util.py /tmp/backup/", "tar czf /tmp/x.tgz jade/hooks.py",
    "open jade/hooks.py", "code jade/hooks.py", "cat jade/util.py", "grep -n is_human_tty jade/util.py", "wc -l jade/*.py", "ls jade/", "uv run ruff check .",
    "uv run pytest -q", "cd {root} && git add jade/util.py && git status",
]


@pytest.mark.parametrize("cmd", JADE_CODE_READS)
def test_commands_that_only_read_jade_code_stay_low(cmd):
    a = level(cmd.format(root=ROOT))
    assert a.level == "low", a


def test_a_copy_is_judged_by_its_destination():
    assert level("cp jade/hooks.py /tmp/b").level == "low"
    assert level("cp /tmp/b jade/hooks.py").rule_id == "R-16"
    assert level("cp -t jade/ /tmp/b").rule_id == "R-16"  # a target directory: the destination is not the last word, so the whole command counts
    assert level("cd jade && cp /tmp/b util.py").rule_id == "R-16"


PROGRAMMATIC = [
    "python3 -c \"from jade import cli; cli.main(['permit','approve','prm_x'])\"", "uv run python -c 'from jade.cli import main; main()'",
    "python3 -c \"import jade.permits as p; p.approve\"", "python -m jade.cli permit list", "python jade/cli.py check", "python3 {root}/jade/cli.py check",
    "python3 -c \"from jade import ledger; ledger.open().con.execute('update permits set state=1')\"", "python3 -c \"import jade.improve as i\"",
    "python3 -c \"from jade import evals, cli\"", "python3 - <<'EOF'\nfrom jade import cli\ncli.main(['hooks','uninstall'])\nEOF",
]


@pytest.mark.parametrize("cmd", PROGRAMMATIC)
def test_calling_the_human_only_code_from_python_is_blocked(cmd):
    cmd = cmd.format(root=ROOT)
    a = level(cmd)
    assert "SI-09" in a.blocked, a
    r = hooks.pretool(bash(cmd))
    assert r.exit_code == 2 and "SI-09" in r.stderr


@pytest.mark.parametrize("cmd", [
    "uv run jade check", "uv run python evals/run_cases.py", "uv run pytest -q tests/", "python3 -c \"from jade import redact; print(redact.redact('x'))\"",
    "python3 -c \"import json\"", "grep -rn 'from jade import cli' tests/", "cat jade/cli.py",
])
def test_ordinary_python_and_the_cli_stay_allowed(cmd):
    a = level(cmd)
    assert not a.blocked and a.level == "low", a


@pytest.mark.parametrize("sub", ["remove", "stop", "kill", "kickstart", "submit"])
def test_launchctl_cannot_silence_the_maintain_agent_by_any_verb(sub):
    cmd = f"launchctl {sub} com.mobogift.jade-maintain"
    assert LEVELS[level(cmd).level] >= LEVELS["high"]
    assert hooks.pretool(bash(cmd)).exit_code == 2
    assert level("launchctl list").level == "low"


def test_residual_risk_is_written_down():
    doc = (ROOT / "docs" / "05-threat-model.md").read_text()
    for phrase in ("patch -p1", "git apply", "pty", "symlink"):
        assert phrase in doc, phrase


# ---------------------------------------------------------------- G01: names the first pass missed

@pytest.mark.parametrize("tool,tin", [
    ("mcp__playwright-cdp__browser_run_code_unsafe", {"code": "x"}), ("mcp__playwright-cdp__run_code_unsafe", {"code": "x"}),
    ("mcp__x__setSetting", {"k": "v"}), ("mcp__x__setPermissionMode", {"mode": "x"}), ("mcp__x__createTask", {"p": "x"}), ("mcp__x__executeCommand", {"a": 1}),
    ("mcp__x__SetSetting", {"k": "v"}), ("mcp__x__deleteEverything", {}), ("mcp__x__writeFile", {"path": "/tmp/a"}),
    ("mcp__herd__processes", {"action": "start", "name": "queue"}), ("mcp__herd__workspaces", {"action": "create"}), ("mcp__herd__services", {"action": "stop"}),
])
def test_camel_case_prefixed_and_action_named_tools_need_a_permit(tool, tin):
    a = policy.assess(tool, tin, str(ROOT))
    assert LEVELS[a.level] >= LEVELS["high"], a
    assert hooks.pretool(call(tool, tin)).exit_code == 2


@pytest.mark.parametrize("tool,tin", [
    ("mcp__playwright-cdp__browser_run_code_unsafe", {"code": "x"}), ("mcp__x__setSetting", {"k": "v"}), ("mcp__x__executeCommand", {"a": 1}),
    ("mcp__x__browser_run_code", {"code": "x"}),
])
def test_the_degraded_failsafe_agrees_on_the_new_names(tool, tin):
    assert hooks.failsafe_reason(call(tool, tin)) is not None


@pytest.mark.parametrize("tool,tin", [
    ("mcp__x__getThing", {}), ("mcp__x__listItems", {}), ("mcp__x__searchDocs", {"q": "x"}), ("mcp__herd__logs", {"action": "read"}),
    ("mcp__herd__site", {"action": "info"}), ("mcp__herd__mail", {"action": "wait"}), ("mcp__MacOS-MCP__Snapshot", {}),
])
def test_camel_case_read_names_and_read_actions_stay_ungated(tool, tin):
    a = policy.assess(tool, tin, str(ROOT))
    assert LEVELS[a.level] < LEVELS["high"], a
    assert hooks.pretool(call(tool, tin)).exit_code == 0


@pytest.mark.parametrize("tool", [
    "mcp__ccd_sidebar__set_pinned", "mcp__ccd_sidebar__create_group", "mcp__ccd_sidebar__move_sessions", "mcp__ccd_session_mgmt__set_session_title",
    "mcp__ccd_view__set_transcript_view", "mcp__ccd_window__set_sidebar_collapsed", "mcp__ccd_pr__set_monitor", "mcp__terminal__stop_terminal_tab",
])
def test_interface_only_tools_are_not_gated(tool):
    a = policy.assess(tool, {"x": 1}, str(ROOT))
    assert a.level == "low" and a.rule_id == "M-06a", a
    assert hooks.pretool(call(tool, {"x": 1})).exit_code == 0


@pytest.mark.parametrize("tool", [
    "mcp__ccd_pr__set_auto_merge", "mcp__ccd_session_mgmt__set_session_permission_mode", "mcp__ccd_session_mgmt__delete_session",
    "mcp__ccd_session_mgmt__send_message", "mcp__ccd_settings__set_setting",
])
def test_governance_tools_next_to_the_interface_ones_stay_gated(tool):
    assert LEVELS[policy.assess(tool, {"x": 1}, str(ROOT)).level] >= LEVELS["high"]


@pytest.mark.parametrize("tool", ["mcp__claude-in-chrome__javascript_tool", "mcp__playwright-cdp__browser_evaluate", "mcp__playwright-cdp__browser_file_upload"])
def test_page_scripts_and_uploads_are_recorded_not_gated_by_decision(tool):
    a = policy.assess(tool, {"x": 1}, str(ROOT))
    assert a.rule_id == "M-08" and a.level == "medium", a


# ---------------------------------------------------------------- G02: regex gaps on content, and calls that are not a bind

def _bind_variants():
    return [
        "listen *:" + ALT + ";", "listen [::]:" + OLLAMA + ";", "listen " + WILD + ";", "host: " + WILD, '{"host": "' + WILD + '"}', "bind " + WILD,
        "OLLAMA_HOST = " + WILD, "OLLAMA_HOST=" + WILD + ":" + OLLAMA, "python -m http.server --bind " + WILD, "gunicorn -b " + WILD + ":8000 app:a",
        'ports:\n  - "' + OLLAMA + ":" + OLLAMA + '"', "ports: [" + OLLAMA + ":" + OLLAMA + "]", "server: --host=" + WILD, "bind_address: " + WILD, "HOST=*",
        "listen " + WILD + ":" + OLLAMA + ";",
    ]


@pytest.mark.parametrize("n", range(16))
def test_si02_variants_are_blocked_on_content(n):
    text = _bind_variants()[n]
    r = hooks.pretool(call("Write", {"file_path": "/tmp/a2/conf", "content": text}))
    assert r.exit_code == 2 and "SI-02" in r.stderr, text


@pytest.mark.parametrize("text", [
    "listen 127.0.0.1:" + OLLAMA + ";", "host: 127.0.0.1", 'ports:\n  - "127.0.0.1:' + OLLAMA + ":" + OLLAMA + '"', "bind 127.0.0.1", "ghost: " + WILD.replace(".", "-"),
    "listen " + OLLAMA + ";", "host: localhost", "ls *.py",
])
def test_si02_loopback_and_unrelated_text_pass(text):
    assert hooks.pretool(call("Write", {"file_path": "/tmp/a2/conf", "content": text})).exit_code == 0, text


@pytest.mark.parametrize("key", [
    sec("sk-proj-", "Hh8Gg7Ff6Ee5Dd4Cc3Bb2Aa1Zz", "-Yy9Xx8"), sec("sk-ant-api03-", "Hh8Gg7Ff6Ee5Dd4Cc3Bb2Aa1Zz-Yy9Xx8"), sec("sk-", "Hh8Gg7Ff6Ee5Dd4Cc3Bb2Aa1Zz"),
    sec("sk", "‑", "proj", "‐", "Hh8Gg7Ff6Ee5Dd4Cc3Bb2Aa1Zz"), sec("sk-", "Hh8Gg7Ff6Ee5", "​", "Dd4Cc3Bb2Aa1Zz"), sec("gh", "s_", "A" * 36),
    sec("github", "_pat_", "A" * 40), sec("ghp", "_", "B" * 36),
])
def test_si05_key_shapes_and_unicode_disguises_are_blocked(key):
    r = hooks.pretool(call("Write", {"file_path": "/tmp/a2/.env", "content": "K=" + key}))
    assert r.exit_code == 2 and "SI-05" in r.stderr, key
    r = hooks.pretool(bash("echo " + key + " > /tmp/a2/k"))
    assert r.exit_code == 2 and "SI-05" in r.stderr, key


@pytest.mark.parametrize("text", [sec("sk", "-learn-is-a-python-library"), "task-description-and-so-on-for-the-risk-assessment", "disk-usage-report-for-all-volumes"])
def test_si05_ordinary_hyphenated_words_pass(text):
    assert hooks.pretool(call("Write", {"file_path": "/tmp/a2/notes.md", "content": text})).exit_code == 0


def test_fold_reads_what_a_program_reads():
    assert fold("sk‑a​b") == "sk-ab" and fold("plain") == "plain"


@pytest.mark.parametrize("tool,tin", [
    ("mcp__claude-in-chrome__navigate", {"url": "http://" + WILD + ":3000/"}), ("mcp__Claude_Browser__navigate", {"url": "http://" + WILD + ":3000"}),
    ("mcp__playwright-cdp__browser_navigate", {"url": "http://" + WILD + ":5173"}),
    ("mcp__context7__query-docs", {"query": "how do I set OLLAMA_HOST=" + WILD + " safely"}),
    ("mcp__skills_rules__smart_search", {"query": "OLLAMA_HOST=" + WILD}), ("mcp__x__list_things", {"q": "--host " + WILD}),
])
def test_a_url_or_a_search_term_is_not_a_bind(tool, tin):
    r = hooks.pretool(call(tool, tin))
    assert r.exit_code == 0, (tool, r.stderr)


def test_a_bind_in_the_body_of_a_write_class_tool_is_still_blocked():
    r = hooks.pretool(call("mcp__x__create_note", {"title": "t", "body": "OLLAMA_HOST=" + WILD}))
    assert r.exit_code == 2 and "SI-02" in r.stderr
    r = hooks.pretool(call("mcp__Desktop_Commander__write_file", {"path": "/tmp/a2/x", "content": "listen " + WILD + ";"}))
    assert r.exit_code == 2 and "SI-02" in r.stderr


def test_a_secret_is_blocked_in_every_tool_call_by_decision():
    """A key typed into a search or any other argument leaves the machine; SI-05 is not narrowed the way SI-02 is."""
    r = hooks.pretool(call("mcp__x__list_things", {"q": sec("sk-", "Hh8Gg7Ff6Ee5Dd4Cc3Bb2Aa1Zz")}))
    assert r.exit_code == 2 and "SI-05" in r.stderr


# ---------------------------------------------------------------- G06: SI-07 holds the command, not its spelling

HELD = ["fly deploy", "fly  deploy", "fly \"deploy\"", "fly deploy;", "fly deploy && true", "true && fly deploy", "FOO=1 fly deploy", "sh -c \"fly deploy\"",
        "command fly deploy", "/usr/local/bin/fly deploy", "fly dep\\loy", "fly 'deploy'", "env FOO=1 fly deploy", "(fly deploy)", "fly deploy\n"]


@pytest.fixture
def unknown_outcome(human_env):
    """`fly deploy` was approved and started, and then its result was lost."""
    db = ledger.open()
    assert hooks.pretool(bash("fly deploy")).exit_code == 2
    p = permits.listing(db, ("PENDING",))[0]
    permits.approve(db, p.permit_id, stdin_isatty=True)
    assert hooks.pretool(bash("fly deploy")).exit_code == 0
    decide.record_outcome(db, p.decision_id, "unknown", None, "deploy output lost")
    assert decide.get(db, p.decision_id)["status"] == "OUTCOME_UNKNOWN"
    n = db.one("SELECT COUNT(*) AS n FROM permits").n
    db.close()
    return p.decision_id, n


@pytest.mark.parametrize("cmd", HELD)
def test_si07_holds_every_spelling_of_the_command(unknown_outcome, cmd):
    did, n = unknown_outcome
    r = hooks.pretool(bash(cmd))
    assert r.exit_code == 2 and "SI-07" in r.stderr and did in r.stderr, (cmd, r.stderr[:200])
    db = ledger.open()
    assert db.one("SELECT COUNT(*) AS n FROM permits").n == n and db.one("SELECT COUNT(*) AS n FROM decisions").n == 1, cmd
    db.close()


def test_si07_holds_the_command_from_another_directory(unknown_outcome):
    did, _ = unknown_outcome
    r = hooks.pretool(bash("fly deploy", cwd="/tmp/some-other-dir"))
    assert r.exit_code == 2 and "SI-07" in r.stderr and did in r.stderr


def test_si07_does_not_hold_a_different_command(unknown_outcome):
    r = hooks.pretool(bash("fly deploy --app other"))
    assert "SI-07" not in r.stderr  # a different argument list is a different command: it raises its own permit
    r = hooks.pretool(bash("kubectl apply -f x.yaml"))
    assert "SI-07" not in r.stderr


def test_si07_still_works_for_a_decision_made_before_the_hold_key_existed(unknown_outcome):
    did, _ = unknown_outcome
    db = ledger.open()
    db.con.execute("UPDATE decisions SET hold_key=NULL WHERE decision_id=?", (did,))
    db.close()
    r = hooks.pretool(bash("fly deploy"))
    assert r.exit_code == 2 and "SI-07" in r.stderr


def test_hold_key_is_the_same_for_every_spelling():
    keys = {level(c).hold_key for c in HELD}
    assert len(keys) == 1
    assert level("fly deploy --app x").hold_key not in keys


def test_the_decision_row_carries_the_hold_key(unknown_outcome):
    db = ledger.open()
    row = db.one("SELECT hold_key, plan_hash FROM decisions")
    assert row.hold_key == level("fly deploy").hold_key and row.hold_key != row.plan_hash
    json.loads(db.one("SELECT doc_json FROM decisions").doc_json)  # the schema-validated document is unchanged
    db.close()
