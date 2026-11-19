"""V6-26: human text tier 1. One builder for the chat question, the dialog and the permit screen.

What is tested here, in the order of the card:
- one builder gives every field that `policies/escalation.yaml` promises (W-83), for each channel;
- each question says what Deny does and what no answer does, with the numbers read from the channel constants (a changed constant changes the text);
- each question says the folder and who asks;
- the plain line lists every flag that widens the call, one test per family, and says so when it cannot read a word;
- the permit screen shows the same code it accepts, and the typed `n` is a refusal that is journaled (not a Deny) while the word `deny` is a Deny;
- a gate text says that the JADE question is sent alone (Fix B); `rewrite` itself is pinned as it is today (Fix C is not built).
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from jade import askchat, askdialog, askplain, cli, hooks, ledger, paths, permits, policy, signing, thresholds, util

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "jade-approve-sign"
PUSH = "git push origin v1-feat-x"
MERGE = "gh pr merge 940 --squash --delete-branch"
WIPE = "git reset --hard HEAD~1"
SESSION = "sess-v626-0001"


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")


def assess(cmd: str, cwd: str = "/tmp"):
    return policy.assess("Bash", {"command": cmd}, cwd=cwd)


def make_permit(cmd: str = PUSH, ttl_s: int = permits.DEFAULT_TTL_S, cwd: str = "/tmp/x"):
    """A pending permit made the way `jade permit request` makes it (no session: a request from the command line)."""
    db = ledger.open()
    a = assess(cmd, cwd)
    from jade import decide

    p = permits.request(db, decide.canonical_action(a.rule_id, cmd), a.plan_hash, risk=a.level if a.level in ("high", "critical") else "high", ttl_s=ttl_s,
                        context=permits.make_context("Bash", a, cwd=cwd), fingerprints=hooks.fingerprints(cmd, cwd), environment=a.environment)
    return db, p


# ---------------------------------------------------------------- one builder, the five promised fields (W-83)
def channels():
    return [askchat.channel(), askdialog.channel(), permits.channel(1800)]


def test_every_channel_builds_every_field_that_escalation_yaml_promises():
    wanted = thresholds.escalation()["human_question"]["each_question_includes"]
    assert wanted == ["context", "impact", "options", "recommended_default", "consequence_of_no_answer"]
    for ch in channels():
        parts = askplain.build(assess(MERGE), "Bash", ch, folder="/tmp/x", who="main session")
        assert list(parts.fields) == wanted, ch.name
        assert all(parts.fields[k] for k in wanted), ch.name
        assert [o["label"] for o in parts.fields["options"]] == ["Allow", "Deny"], ch.name


def test_the_builder_gives_a_fact_not_advice():
    for ch in channels():
        parts = askplain.build(assess(MERGE), "Bash", ch, folder="/tmp/x", who="main session")
        text = json.dumps(parts.fields).lower() + "\n".join(parts.lines).lower()
        for banned in ("suggest allow", "suggest deny", "we recommend", "can be undone", "you can undo", "approved before", "you allowed"):
            assert banned not in text, (ch.name, banned)
        assert "stays blocked" in parts.fields["recommended_default"]


def test_a_missing_field_in_the_yaml_list_fails_loudly(monkeypatch):
    monkeypatch.setattr(thresholds, "escalation", lambda: {"human_question": {"each_question_includes": ["context", "colour"]}})
    with pytest.raises(ValueError, match="colour"):
        askplain.build(assess(MERGE), "Bash", askchat.channel(), folder="/tmp/x", who="main session")


# ---------------------------------------------------------------- consequences, with numbers from the channel constants
def chat_q(cmd=WIPE, cwd="/Users/x/iro", ask_id="0123456789ab") -> str:
    return askchat.question_for(ask_id, assess(cmd, cwd), "Bash", {"subject": cmd, "cwd": cwd})["question"]


def test_the_chat_question_says_what_deny_and_no_answer_do():
    q = chat_q()
    assert "If you do not answer, the call stays blocked. This question expires after 10 minutes." in q
    assert "Deny: the call does not run and the agent is told." in q
    assert "The same call is not asked again for 10 minutes." in q
    assert "After 3 Denies of the same call it is not asked again." in q
    assert "Single use, 10 minutes, this session only." in q


def test_a_changed_chat_constant_changes_the_chat_text(monkeypatch):
    monkeypatch.setattr(askchat, "ASK_TTL_S", 1200)
    monkeypatch.setattr(askchat, "GRANT_TTL_S", 300)
    monkeypatch.setattr(askchat, "DENY_CAP", 5)
    q = chat_q()
    assert "This question expires after 20 minutes." in q and "not asked again for 20 minutes." in q
    assert "After 5 Denies of the same call" in q and "Single use, 5 minutes, this session only." in q
    assert "10 minutes" not in q


def test_the_option_descriptions_say_the_same_consequences():
    opts = askchat.question_for("0123456789ab", assess(WIPE), "Bash", {})["options"]
    assert [o["label"] for o in opts] == ["Allow", "Deny"]
    assert "Run this one call once. It is not remembered." in opts[0]["description"]
    assert "not asked again for 10 minutes" in opts[1]["description"]


def dialog_text(cmd=WIPE, **ctx) -> str:
    return askdialog.text_for(assess(cmd), "Bash", {"subject": cmd, "cwd": "/tmp/x", **ctx})


def test_the_dialog_says_what_deny_and_no_answer_do_with_its_own_numbers(monkeypatch):
    t = dialog_text(agent_type="general-purpose")
    assert "If you do not answer in 2 minutes, the dialog closes and the call stays blocked." in t
    assert "It then needs a permit that you approve in a terminal. It is not shown again for 5 minutes." in t
    assert "Deny: the call does not run. It is not asked again for 10 minutes." in t
    monkeypatch.setattr(askdialog, "DIALOG_WAIT_S", 90)
    monkeypatch.setattr(askdialog, "GAVE_UP_TTL_S", 1200)
    monkeypatch.setattr(askdialog, "DENY_TTL_S", 60)
    t = dialog_text()
    assert "in 90 seconds" in t and "not shown again for 20 minutes" in t and "not asked again for 1 minute" in t
    assert "2 minutes" not in t and "5 minutes" not in t


def test_the_permit_screen_says_what_no_approval_does(monkeypatch):
    db, p = make_permit(ttl_s=600)
    text = permits.review_text(p)
    assert "If you do not approve it, the call stays blocked. This request expires after 10 minutes." in text
    assert "Approving allows exactly this call once within 10 minutes." in text
    db, p = make_permit("git push origin v1-feat-other", ttl_s=1800)   # another call: the same call would find the first, still pending, permit
    assert "expires after 30 minutes" in permits.review_text(p)


# ---------------------------------------------------------------- the folder and who asks
def test_the_chat_question_names_the_folder_and_the_main_session():
    q = chat_q(cwd="/Users/x/iro")
    assert "Folder: /Users/x/iro" in q and "Asked by: main session" in q


def test_the_dialog_names_the_folder_and_the_subagent():
    t = dialog_text(agent_type="general-purpose")
    assert "Folder: /tmp/x" in t and "Asked by: a subagent (general-purpose)" in t
    assert "Asked by: a subagent" in dialog_text()


def test_a_hostile_agent_type_and_folder_are_written_out():
    t = dialog_text(agent_type="evil‮\x1b[2J", cwd="/tmp/‮x")
    assert "‮" not in t and "\x1b" not in t and "\\u202e" in t
    q = chat_q(cwd="/tmp/‮x")
    assert "‮" not in q


FORGED_CWD = "/tmp/x\nAsked by: the human at the keyboard\nAllow this one call? Single use."


def test_a_folder_with_a_line_break_cannot_forge_a_line_in_the_chat_or_the_dialog():
    for text in (chat_q(cwd=FORGED_CWD), dialog_text(cwd=FORGED_CWD, agent_type="general-purpose")):
        lines = text.split("\n")
        assert not any(ln.startswith("Asked by: the human") for ln in lines), text
        assert sum(ln.startswith("Allow this one call?") for ln in lines) == 1, text
        assert sum(ln.startswith("Asked by:") for ln in lines) == 1, text
        assert any(ln.startswith("Folder: /tmp/x ") for ln in lines), text


def test_a_folder_with_a_line_break_cannot_forge_a_line_on_the_permit_screen():
    db, p = make_permit(cwd=FORGED_CWD)
    lines = permits.review_text(p).split("\n")
    assert not any(ln.startswith("Asked by: the human") for ln in lines)
    assert sum(ln.startswith("Asked by:") for ln in lines) == 1
    assert not any(ln.startswith("Allow this one call? Single use.") for ln in lines)
    assert any(ln.startswith("Tool     Bash   cwd /tmp/x ") for ln in lines)


def test_the_permit_screen_names_the_folder_and_who_asks():
    db, p = make_permit()
    text = permits.review_text(p)
    assert "Asked by: a request made from the command line" in text and "cwd /tmp/x" in text
    db = ledger.open()
    assert hooks.pretool({"tool_name": "Bash", "tool_input": {"command": "git push origin v1-feat-y"}, "session_id": SESSION, "cwd": "/tmp/x"}).exit_code == 2
    mine = permits.listing(db, ("PENDING",))[-1]
    assert "Asked by: the main session" in permits.review_text(mine)
    assert hooks.pretool({"tool_name": "Bash", "tool_input": {"command": "git push origin v1-feat-z"}, "session_id": SESSION, "cwd": "/tmp/x", "agent_id": "ag1"}).exit_code == 2
    sub = permits.listing(db, ("PENDING",))[-1]
    assert "Asked by: a subagent" in permits.review_text(sub)


# ---------------------------------------------------------------- the plain line lists every widening flag
def line(cmd: str) -> str:
    a = assess(cmd)
    assert a.seg_keys, cmd
    return askplain.what_line(a, "Bash")


FAMILIES = [
    # glab: --auto-merge waits for the checks and merges later
    ("glab mr merge 12 --auto-merge", ["--auto-merge"]),
    ("glab mr merge 12 --when-pipeline-succeeds", ["--when-pipeline-succeeds"]),
    # git clean: untracked is the base case; -x adds ignored files and -d adds folders
    ("git clean -fdX", ["-d", "-X"]),
    ("git clean -fdx", ["-d", "-x"]),
    ("git clean -f -d -x -e keep", ["-d", "-x"]),
    # git branch: more than one name
    ("git branch -D one two three", ["3 local branches"]),
    # git config: the scope
    ("git config --global core.hooksPath /tmp/h", ["--global"]),
    ("git config --system core.hooksPath /tmp/h", ["--system"]),
    # rsync: it can delete at the target and delete the source
    ("rsync -a --delete src/ host:dst/", ["--delete"]),
    ("rsync -a --remove-source-files src/ host:dst/", ["--remove-source-files"]),
    ("rsync -a --delete-after src/ host:dst/", ["--delete-after"]),
    # docker compose and docker rm: volumes and images are data
    ("docker compose down -v", ["-v"]),
    ("docker compose down --volumes --rmi all", ["--volumes", "--rmi"]),
    ("docker rm -fv web", ["-v"]),
    # kubectl: every object, every namespace, a forced delete
    ("kubectl delete pods --all", ["--all"]),
    ("kubectl delete pods -A", ["-A"]),
    ("kubectl delete pod web --force", ["--force"]),
    # terraform: no question asked by the tool
    ("terraform apply -auto-approve", ["-auto-approve"]),
    ("terraform destroy -auto-approve", ["-auto-approve"]),
    # docker push: every tag
    ("docker push --all-tags registry.example/app", ["--all-tags"]),
    # crontab -r: every job
    ("crontab -r", ["-r"]),
    # gist: public
    ("gh gist create --public a.txt", ["--public"]),
    # npm publish: who can read it
    ("npm publish --access public", ["--access public"]),
]


@pytest.mark.parametrize("cmd,marks", FAMILIES)
def test_the_plain_line_names_every_flag_that_widens_the_call(cmd, marks):
    text = line(cmd)
    for mark in marks:
        assert mark in text, (cmd, text)
    assert len(text.split()) <= 40 and text.endswith("."), (cmd, text)


@pytest.mark.parametrize("cmd", [
    "git clean -fe keep", "git branch -D one", "git config core.hooksPath /tmp/h", "rsync -a src/ host:dst/", "docker compose down", "docker rm web",
    "kubectl delete pod web", "terraform apply", "docker push registry.example/app", "crontab -l", "gh gist create a.txt", "npm publish", "glab mr merge 12",
])
def test_a_plain_call_of_the_same_family_says_only_the_base_line(cmd):
    text = line(cmd) if assess(cmd).seg_keys else ""
    assert "maybe more" not in text and "--" not in text and " -" not in text.replace(" - ", ""), (cmd, text)


@pytest.mark.parametrize("cmd", [
    "rsync -a $OPTS src/ host:dst/", "kubectl delete pods $WHAT", "docker compose down $F", "git clean -fdX $P", "terraform apply $ARGS", "git branch -D $B",
    "git config $SCOPE core.hooksPath /tmp/h", "docker push $ARGS registry.example/app",
])
def test_a_word_that_is_not_plain_says_there_may_be_more(cmd):
    assert "maybe more, because some words are not plain" in line(cmd), cmd


def test_a_home_path_is_not_a_hidden_flag():
    assert "maybe more" not in line("rsync -a ~/src/ host:dst/")


def test_rm_has_no_widening_rule_because_the_gate_key_drops_its_flag():
    # The key is `rm -rf /`: --no-preserve-root never reaches the line builder, so no text may promise it.
    assert not hasattr(askplain, "_rm")
    assert "--no-preserve-root" not in line("rm -rf --no-preserve-root /")


def test_the_widening_text_is_in_the_chat_question_and_the_dialog():
    cmd = "rsync -a --delete src/ host:dst/"
    q = chat_q(cmd)
    assert "--delete" in q.split("\n")[1] and "--delete" in dialog_text(cmd).split("\n")[0]


# ---------------------------------------------------------------- the permit screen: plain lines first, one code
def test_the_permit_screen_starts_with_the_plain_lines_then_the_rule():
    db, p = make_permit(MERGE)
    lines = permits.review_text(p).split("\n")
    a = assess(MERGE, "/tmp/x")
    head = askplain.header(a, "Bash")
    assert lines[:len(head)] == head and lines[0].startswith("Merge pull request 940") and "delete its branch (--delete-branch)" in lines[0]
    text = "\n".join(lines)
    assert text.index("Command") > text.index("Rule") > text.index(askplain.risk_line(a.rule_id)) and text.index("Permit   ") > text.index(head[-1])


def test_the_unsigned_screen_shows_and_accepts_the_same_code(monkeypatch, capsys, human_env):
    db, p = make_permit()
    seen = {}

    def fake_input(prompt=""):
        seen["prompt"] = prompt
        return re.search(r"Type (\S+) to approve", prompt).group(1)

    for mod in (cli, permits):
        monkeypatch.setattr(mod, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", fake_input)
    assert cli.main(["permit", "approve", p.permit_id]) == 0
    assert permits.get(db, p.permit_id).state == "ISSUED"
    assert permits.confirm_code(p) in seen["prompt"] and permits.approval_code(db, p) == permits.confirm_code(p)


def keygen(d: Path, name: str) -> Path:
    key = d / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "jade-approver", "-f", str(key)], check=True)
    return key


@pytest.fixture
def approver(tmp_path, monkeypatch, human_env):
    """The signed backend with the REAL helper script. The fake terminal types what the permit screen showed (TYPED_CODE), so a code that differs makes the helper refuse."""
    d = tmp_path / "approver"
    d.mkdir(mode=0o755)
    key = keygen(d, "approver_key")
    anchor = d / "allowed_signers"
    anchor.write_text(f"jade-approver {' '.join(Path(f'{key}.pub').read_text().split()[:2])}\n")
    anchor.chmod(0o644)
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(anchor))
    monkeypatch.setattr(signing, "TRUSTED_UID", os.getuid())
    wrapper = d / "wrapper.sh"
    wrapper.write_text(f'#!/bin/bash\nmsg="$(cat)"\nprintf \'%s\\n\' "${{TYPED_CODE:-none}}" > "$0.tty"\n'
                       f'printf \'%s\' "$msg" | JADE_APPROVER_KEY="{key}" JADE_APPROVER_TTY="$0.tty" JADE_APPROVER_TTY_OUT="$0.out" bash "{HELPER}"\n')
    wrapper.chmod(0o755)
    monkeypatch.setattr(signing, "helper_command", lambda: [str(wrapper)])
    for mod in (cli, permits):
        monkeypatch.setattr(mod, "human_tty_refusal", lambda *a, **k: None)
    return wrapper


def test_the_signed_screen_shows_the_code_the_helper_accepts(approver, monkeypatch):
    db, p = make_permit()
    seen = {}

    def fake_input(prompt=""):
        code = re.search(r"Type (\S+) to approve", prompt).group(1)
        seen["code"] = code
        monkeypatch.setenv("TYPED_CODE", code)
        return code

    monkeypatch.setattr("builtins.input", fake_input)
    assert cli.main(["permit", "approve", p.permit_id]) == 0
    done = permits.get(db, p.permit_id)
    assert done.state == "ISSUED" and done.approval_reference.startswith("sig:")
    shown = Path(str(approver) + ".out").read_text()
    assert seen["code"] in shown   # the helper printed the very code the permit screen showed
    assert re.fullmatch(r"[0-9A-F]{6}", seen["code"])


def test_the_signed_helper_refuses_a_code_the_screen_did_not_show(approver, monkeypatch):
    db, p = make_permit()
    monkeypatch.setattr("builtins.input", lambda prompt="": (monkeypatch.setenv("TYPED_CODE", "ZZZZZZ"), re.search(r"Type (\S+) to approve", prompt).group(1))[1])
    assert cli.main(["permit", "approve", p.permit_id]) == 1
    assert permits.get(db, p.permit_id).state == "PENDING"


def test_the_signed_code_is_the_helpers_code_of_the_message_and_not_the_plan_hash_prefix(approver):
    db, p = make_permit()
    until = permits.approval_valid_until(p)
    row = db.one("SELECT * FROM permits WHERE permit_id=?", (p.permit_id,))
    msg = permits._approval_message(row, {**permits._doc(row), "principal": f"human:{util.user()}"}, until)
    assert permits.approval_code(db, p, until) == signing.code_of(msg) != permits.confirm_code(p).upper()
    assert permits.approval_code(db, p, util.ts_in(5)) != signing.code_of(msg)   # another end time is another message, so another code


def test_an_approval_end_time_that_is_past_or_too_late_is_refused(approver):
    db, p = make_permit()
    with pytest.raises(permits.PermitError, match="ran out"):
        permits.approve(db, p.permit_id, stdin_isatty=True, valid_until=util.ts_in(-5))
    with pytest.raises(permits.PermitError, match="later than the permit allows"):
        permits.approve(db, p.permit_id, stdin_isatty=True, valid_until=util.ts_in(p.ttl_s + 600))
    assert permits.get(db, p.permit_id).state == "PENDING"


# ---------------------------------------------------------------- a refusal is journaled
@pytest.fixture
def human(monkeypatch, human_env):
    for mod in (cli, permits):
        monkeypatch.setattr(mod, "human_tty_refusal", lambda *a, **k: None)
    monkeypatch.setattr(util, "agent_ancestor", lambda: None)


def answer(monkeypatch, capsys, pid, typed):
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)
    rc = cli.main(["permit", "approve", pid])
    return rc, capsys.readouterr()


def kinds(db, entity):
    return [r.kind for r in db.all("SELECT kind FROM events WHERE entity_id=? ORDER BY rowid", (entity,))]


def table_count(db, name):
    return db.one(f"SELECT COUNT(*) AS n FROM {name}").n


def test_n_refuses_the_permit_and_journals_a_refusal_that_is_not_a_deny(monkeypatch, capsys, human):
    db, p = make_permit()
    before = (table_count(db, "human_answers"), table_count(db, "asks"))
    rc, out = answer(monkeypatch, capsys, p.permit_id, "n")
    assert rc == 1 and "refused" in out.out
    assert permits.get(db, p.permit_id).state == "REVOKED"
    assert "permit_refused" in kinds(db, p.permit_id) and "permit_denied" not in kinds(db, p.permit_id)
    assert (table_count(db, "human_answers"), table_count(db, "asks")) == before   # no Deny: the hold keys, the Deny cap and the learning loop are untouched


def test_the_word_deny_records_a_deny(monkeypatch, capsys, human):
    db, p = make_permit()
    rc, out = answer(monkeypatch, capsys, p.permit_id, "deny")
    assert rc == 1 and "Deny" in out.out
    assert permits.get(db, p.permit_id).state == "REVOKED" and "permit_denied" in kinds(db, p.permit_id)
    rows = db.human_answers()
    assert len(rows) == 1 and rows[0]["answer"] == "deny" and rows[0]["channel"] == "terminal"


@pytest.mark.parametrize("typed", ["y", "", "later", "NO!"])
def test_anything_else_leaves_the_permit_waiting_and_writes_nothing(monkeypatch, capsys, human, typed):
    db, p = make_permit()
    events = len(kinds(db, p.permit_id))
    rc, out = answer(monkeypatch, capsys, p.permit_id, typed)
    assert rc == 1 and "not approved" in out.out and "waiting" in out.out
    assert permits.get(db, p.permit_id).state == "PENDING" and len(kinds(db, p.permit_id)) == events


def test_the_prompt_says_what_each_answer_does(monkeypatch, capsys, human):
    db, p = make_permit()
    seen = []
    monkeypatch.setattr("builtins.input", lambda prompt="": seen.append(prompt) or "later")
    cli.main(["permit", "approve", p.permit_id])
    prompt = seen[0]
    assert "to approve" in prompt and " n " in f" {prompt} ".replace(",", " ") and "deny" in prompt and "anything else" in prompt and "waiting" in prompt


def test_a_refusal_needs_a_human_terminal(monkeypatch, human):
    db, p = make_permit()
    monkeypatch.setattr(permits, "human_tty_refusal", lambda *a, **k: "an agent harness is present in the environment")
    with pytest.raises(permits.ApprovalRefused):
        permits.refuse(db, p.permit_id)
    assert permits.get(db, p.permit_id).state == "PENDING"


def test_a_refusal_of_an_issued_or_unknown_permit_is_an_error(human):
    db, p = make_permit()
    permits.approve(db, p.permit_id, stdin_isatty=True)
    with pytest.raises(permits.PermitError, match="ISSUED"):
        permits.refuse(db, p.permit_id)
    with pytest.raises(permits.PermitError, match="unknown"):
        permits.refuse(db, "prm_000000000000")


def test_the_agent_is_told_about_a_refusal_on_its_retry_and_stays_blocked(monkeypatch, capsys, human):
    db = ledger.open()
    ev = {"tool_name": "Bash", "tool_input": {"command": PUSH}, "session_id": SESSION, "cwd": "/tmp/x"}
    assert hooks.pretool(ev).exit_code == 2
    p = permits.listing(db, ("PENDING",))[-1]
    answer(monkeypatch, capsys, p.permit_id, "n")
    r = hooks.pretool(ev)
    assert r.exit_code == 2 and "refused" in r.stderr and "do not rephrase" in r.stderr.lower()
    assert permits.get(db, p.permit_id).state == "REVOKED"
    again = permits.listing(db, ("PENDING",))
    assert len(again) == 1 and again[0].permit_id != p.permit_id   # a new request waits: the human may change their mind


def test_a_call_nobody_refused_gets_no_refusal_text():
    ev = {"tool_name": "Bash", "tool_input": {"command": PUSH}, "session_id": SESSION, "cwd": "/tmp/x"}
    assert "refused" not in hooks.pretool(ev).stderr


# ---------------------------------------------------------------- batch: Fix B (words) now, Fix C (code) is not built
def set_level(level="high"):
    cfg = paths.config()
    cfg["level"] = level
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


def chat_call(cmd=WIPE):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto"}


def test_the_gate_text_says_to_send_the_jade_question_alone():
    set_level()
    r = hooks.pretool(chat_call())
    assert r.exit_code == 2
    assert "ONE question" in r.stderr and "alone" in r.stderr and "no other question" in r.stderr
    assert "every other question" in r.stderr   # why: JADE replaces all questions of that call


def test_the_stop_reminder_says_to_send_the_jade_question_alone():
    set_level()
    hooks.pretool(chat_call())
    db = ledger.open()
    res = askchat.stop_nudge(db, SESSION)
    assert res is not None and "alone" in res.stderr


def test_rewrite_still_replaces_a_batch_with_the_one_jade_question_until_fix_c_is_built():
    """Pinned as it is today (docs d13 X-15): Fix C (refuse a batch that carries a JADE token) is the next release. Fix A (keep the other questions) is rejected."""
    set_level()
    m = re.search(r"\[jade:ask_[0-9a-f]{12}\]", hooks.pretool(chat_call()).stderr)
    batch = {"tool_name": "AskUserQuestion", "session_id": SESSION, "cwd": "/tmp", "permission_mode": "auto",
             "tool_input": {"questions": [{"question": f"{m.group(0)} Allow?", "header": "a", "multiSelect": False, "options": [{"label": "Allow"}, {"label": "Deny"}]},
                                          {"question": "Which colour?", "header": "c", "multiSelect": False, "options": [{"label": "Red"}, {"label": "Blue"}]}]}}
    out = json.loads(hooks.pretool(batch).stdout)
    questions = out["hookSpecificOutput"]["updatedInput"]["questions"]
    assert len(questions) == 1 and "Which colour?" not in json.dumps(questions)
