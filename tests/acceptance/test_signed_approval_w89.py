"""W-38 / W-89: the signed approval backend (docs/10-out-of-band-permit-approval.md).

A throw-away Ed25519 key pair plays the approver account; its public half is the trust anchor in a temporary directory that the test user owns (the real anchor is
root-owned: `signing.TRUSTED_UID`). `signing.helper_command` is replaced by a wrapper that runs the real `scripts/jade-approve-sign` with the right code, so
no sudo, no terminal and no network are involved. Every forged shape must stay refused by the hook at use.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from jade import hooks, ledger, ops, paths, permits, signing
from jade.util import ts, ts_in

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "jade-approve-sign"
PUSH = "git push origin v1-feat-x"


def keygen(d: Path, name: str) -> Path:
    key = d / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "jade-approver", "-f", str(key)], check=True)
    return key


def write_anchor(d: Path, pub: Path) -> Path:
    anchor = d / "allowed_signers"
    anchor.write_text(f"jade-approver {' '.join(pub.read_text().split()[:2])}\n")
    anchor.chmod(0o644)
    return anchor


@pytest.fixture
def approver(tmp_path, monkeypatch, human_env):
    d = tmp_path / "approver"
    d.mkdir(mode=0o755)
    key = keygen(d, "approver_key")
    anchor = write_anchor(d, Path(f"{key}.pub"))
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(anchor))
    monkeypatch.setattr(signing, "TRUSTED_UID", os.getuid())
    wrapper = d / "wrapper.sh"
    wrapper.write_text(
        '#!/bin/bash\nmsg="$(cat)"\ncode="$(printf \'%s\' "$msg" | shasum -a 256 | cut -c1-6 | tr a-f A-F)"\nprintf \'%s\\n\' "$code" > "$0.tty"\n'
        f'printf \'%s\' "$msg" | JADE_APPROVER_KEY="{key}" JADE_APPROVER_TTY="$0.tty" JADE_APPROVER_TTY_OUT=/dev/null bash "{HELPER}"\n')
    wrapper.chmod(0o755)
    monkeypatch.setattr(signing, "helper_command", lambda: [str(wrapper)])
    return SimpleKeys(d, key, anchor, wrapper)


class SimpleKeys:
    def __init__(self, d, key, anchor, wrapper):
        self.dir, self.key, self.anchor, self.wrapper = d, key, anchor, wrapper


def call(cmd, session="sess-aaaaaaaa"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": "/tmp/x"}


def blocked(cmd=PUSH):
    db = ledger.open()
    assert hooks.pretool(call(cmd)).exit_code == 2
    return db, permits.listing(db, ("PENDING",))[-1]


def retry(cmd=PUSH) -> int:
    return hooks.pretool(call(cmd)).exit_code


def row_doc(db, pid):
    r = db.one("SELECT * FROM permits WHERE permit_id=?", (pid,))
    return r, json.loads(r.doc_json)


def put_doc(db, pid, doc, reseal=True):
    r = db.one("SELECT * FROM permits WHERE permit_id=?", (pid,))
    doc.pop("seal", None)
    if reseal:   # an attacker who read the seal key (W-41) can re-seal: the signature must still decide
        doc["seal"] = permits._seal_of(permits._seal_key(db, create=True), r, doc)
    with db.tx() as con:
        con.execute("UPDATE permits SET doc_json=? WHERE permit_id=?", (json.dumps(doc), pid))


# ---- the happy path -----------------------------------------------------------------------------------
def test_a_signed_approval_is_used_once(approver):
    db, p = blocked()
    issued = permits.approve(db, p.permit_id, stdin_isatty=True)
    _, doc = row_doc(db, p.permit_id)
    assert issued.state == "ISSUED" and doc["approval_reference"].startswith("sig:") and doc["approval_signature"].startswith("-----BEGIN SSH SIGNATURE-----")
    assert retry() == 0
    assert retry() == 2   # single use


def test_the_backend_is_signed_only_with_an_anchor_or_the_config(approver, monkeypatch, tmp_path):
    assert signing.backend() == "signed"
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(tmp_path / "nothing-here"))
    assert signing.backend() == "tty"
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "config.json").write_text(json.dumps({"approval_backend": "signed"}))
    assert signing.backend() == "signed"   # configured but no anchor: high and critical permits cannot be used (fail closed)


# ---- an approval that is not a signature from the approver ---------------------------------------------
def test_a_tty_approval_alone_does_not_count_when_the_helper_cannot_sign(approver, monkeypatch):
    monkeypatch.setattr(signing, "helper_command", lambda: ["/usr/bin/false"])
    db, p = blocked()
    with pytest.raises(permits.PermitError, match="did not sign"):
        permits.approve(db, p.permit_id, stdin_isatty=True)
    assert permits.get(db, p.permit_id).state == "PENDING" and retry() == 2


def test_a_row_flipped_to_issued_with_sql_and_resealed_is_refused(approver):
    db, p = blocked()
    r, doc = row_doc(db, p.permit_id)
    doc.update(state="ISSUED", principal="human:attacker", approval_reference="sig:attacker@" + ts(), issued_at=ts(), expires_at=ts_in(1800))
    put_doc(db, p.permit_id, doc)
    with db.tx() as con:
        con.execute("UPDATE permits SET state='ISSUED', issued_at=?, expires_at=? WHERE permit_id=?", (doc["issued_at"], doc["expires_at"], p.permit_id))
    put_doc(db, p.permit_id, doc)
    assert retry() == 2


def test_a_tty_style_reference_is_refused_while_the_backend_is_signed(approver):
    db, p = blocked()
    r, doc = row_doc(db, p.permit_id)
    doc.update(state="ISSUED", principal="human:x", approval_reference="tty:x@" + ts(), issued_at=ts(), expires_at=ts_in(1800))
    with db.tx() as con:
        con.execute("UPDATE permits SET state='ISSUED', issued_at=?, expires_at=? WHERE permit_id=?", (doc["issued_at"], doc["expires_at"], p.permit_id))
    put_doc(db, p.permit_id, doc)
    assert retry() == 2


def forge_with_key(db, p, key: Path):
    """The attacker signs the right message with a key of their own."""
    r, doc = row_doc(db, p.permit_id)
    until = ts_in(1800)
    doc.update(state="ISSUED", principal="human:x", approval_reference="sig:x@" + ts(), issued_at=ts(), expires_at=until, approval_valid_until=until)
    msg = permits._approval_message(r, doc, until)
    sig = subprocess.run(["/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key), "-n", signing.NAMESPACE], input=msg.encode(), capture_output=True, check=True).stdout.decode()
    doc["approval_signature"] = sig
    with db.tx() as con:
        con.execute("UPDATE permits SET state='ISSUED', issued_at=?, expires_at=? WHERE permit_id=?", (doc["issued_at"], doc["expires_at"], p.permit_id))
    put_doc(db, p.permit_id, doc)


def test_a_signature_by_another_key_is_refused(approver, tmp_path):
    db, p = blocked()
    forge_with_key(db, p, keygen(tmp_path, "attacker"))
    assert retry() == 2


def test_the_same_forgery_with_the_real_key_would_pass(approver):
    """Control for the test above: the forging helper is right, only the key differs."""
    db, p = blocked()
    forge_with_key(db, p, approver.key)
    assert retry() == 0


def test_a_signature_replayed_on_another_permit_is_refused(approver):
    db, a = blocked(PUSH)
    permits.approve(db, a.permit_id, stdin_isatty=True)
    _, doc_a = row_doc(db, a.permit_id)
    assert hooks.pretool(call("git push origin v1-feat-y")).exit_code == 2
    b = permits.listing(db, ("PENDING",))[-1]
    r, doc = row_doc(db, b.permit_id)
    doc.update(state="ISSUED", principal=doc_a["principal"], approval_reference=doc_a["approval_reference"], approval_signature=doc_a["approval_signature"],
               approval_valid_until=doc_a["approval_valid_until"], issued_at=ts(), expires_at=doc_a["approval_valid_until"])
    with db.tx() as con:
        con.execute("UPDATE permits SET state='ISSUED', issued_at=?, expires_at=? WHERE permit_id=?", (doc["issued_at"], doc["expires_at"], b.permit_id))
    put_doc(db, b.permit_id, doc)
    assert hooks.pretool(call("git push origin v1-feat-y")).exit_code == 2


def test_an_edited_field_of_a_signed_permit_is_refused_even_when_resealed(approver):
    db, p = blocked()
    permits.approve(db, p.permit_id, stdin_isatty=True)
    r, doc = row_doc(db, p.permit_id)
    doc["preconditions"] = [{"kind": "custom", "fingerprint": "attacker"}]
    put_doc(db, p.permit_id, doc)
    assert retry() == 2


def test_a_signature_past_its_window_is_refused(approver):
    db, p = blocked()
    permits.approve(db, p.permit_id, stdin_isatty=True)
    r, doc = row_doc(db, p.permit_id)
    assert permits._signature_refusal(r, doc) is None
    doc["approval_valid_until"] = "2020-01-01T00:00:00Z"
    assert "validity window" in permits._signature_refusal(r, doc)


# ---- the trust anchor ---------------------------------------------------------------------------------
def test_an_anchor_that_the_user_can_write_is_not_trusted(approver):
    approver.anchor.chmod(0o666)
    state, why = signing.anchor_state()
    assert state == "untrusted" and "writable" in why and signing.signed_backend()   # signed and unusable: fail closed
    db, p = blocked()
    with pytest.raises(permits.PermitError):
        permits.approve(db, p.permit_id, stdin_isatty=True)


def test_an_anchor_owned_by_someone_else_or_a_symlink_is_not_trusted(approver, monkeypatch, tmp_path):
    monkeypatch.setattr(signing, "TRUSTED_UID", os.getuid() + 1)
    assert signing.anchor_state()[0] == "untrusted"
    monkeypatch.setattr(signing, "TRUSTED_UID", os.getuid())
    link = tmp_path / "link"
    link.symlink_to(approver.anchor)
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(link))
    assert signing.anchor_state()[0] == "untrusted"


def test_a_missing_ssh_keygen_or_a_hung_verify_fails_closed(approver, monkeypatch):
    db, p = blocked()
    permits.approve(db, p.permit_id, stdin_isatty=True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("ssh-keygen", 5)))
    assert signing.verify(signing.message(permit_id="prm_" + "0" * 12, plan_hash="a" * 64, preconditions=[], policy_version="b" * 12, risk="high",
                                          principal="human:x", valid_until=ts_in(60), nonce="c" * 32), "-----BEGIN SSH SIGNATURE-----\nx\n") is not None


def test_a_message_that_is_not_a_permit_approval_is_never_signed_or_verified(approver):
    assert signing.verify("anything else", "-----BEGIN SSH SIGNATURE-----\nx\n") is not None
    with pytest.raises(RuntimeError, match="not a permit approval"):
        signing.sign_with_helper("hello")


# ---- the helper script ---------------------------------------------------------------------------------
def run_helper(approver, msg: str, typed: str):
    tty = approver.dir / "tty"
    tty.write_text(typed + "\n")
    env = {**os.environ, "JADE_APPROVER_KEY": str(approver.key), "JADE_APPROVER_TTY": str(tty), "JADE_APPROVER_TTY_OUT": "/dev/null"}
    return subprocess.run(["bash", str(HELPER)], input=msg, capture_output=True, text=True, env=env, check=False)


def good_message(approver) -> str:
    return signing.message(permit_id="prm_" + "1" * 12, plan_hash="a" * 64, preconditions=[], policy_version="b" * 12, risk="high", principal="human:x",
                           valid_until=ts_in(600), nonce="c" * 32)


def test_the_helper_signs_only_after_the_right_code(approver):
    msg = good_message(approver)
    ok = run_helper(approver, msg, signing.code_of(msg))
    assert ok.returncode == 0 and ok.stdout.startswith("-----BEGIN SSH SIGNATURE-----") and signing.verify(msg, ok.stdout) is None
    bad = run_helper(approver, msg, "WRONG1")
    assert bad.returncode == 3 and bad.stdout == ""
    other = run_helper(approver, "rm -rf /", "XXXXXX")
    assert other.returncode == 2 and other.stdout == ""


# ---- the enforce pre-flight ---------------------------------------------------------------------------
def journal_unprompted_calls(n=3):
    db = ledger.open()
    for i in range(n):
        db.event(f"call{i}", "gated", guard="R-10 high", detail={"tool": "Bash", "permission_mode": "bypassPermissions"})
    db.close()


def test_enforce_is_refused_without_the_signed_backend_when_the_host_never_asked(tmp_path, monkeypatch, human_env):
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(tmp_path / "nothing-here"))
    journal_unprompted_calls()
    with pytest.raises(ops.HumanOnly, match="signed approval backend"):
        ops.set_gate_mode("enforce", stdin_isatty=True)
    assert paths.config().get("gate_mode") != "enforce"
    ops.set_gate_mode("enforce", stdin_isatty=True, allow_unsigned=True)
    assert paths.config()["gate_mode"] == "enforce"
    db = ledger.open()
    assert db.one("SELECT count(*) AS n FROM events WHERE kind='enforce_unsigned_override'").n == 1


def test_enforce_is_allowed_with_the_signed_backend(approver):
    journal_unprompted_calls()
    ops.set_gate_mode("enforce", stdin_isatty=True)
    assert paths.config()["gate_mode"] == "enforce"


def test_enforce_is_allowed_when_every_call_had_a_host_prompt(tmp_path, monkeypatch, human_env):
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(tmp_path / "nothing-here"))
    ops.set_gate_mode("enforce", stdin_isatty=True)
    assert paths.config()["gate_mode"] == "enforce"


# ---- the check row ------------------------------------------------------------------------------------
def test_the_check_row_reports_each_state(approver, tmp_path, monkeypatch):
    assert ops._approval_row()[1] and ops._approval_row()[2].startswith("signed")
    approver.anchor.chmod(0o666)
    ok, detail = ops._approval_row()[1:]
    assert not ok and "not usable" in detail
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(tmp_path / "nothing-here"))
    ok, detail = ops._approval_row()[1:]
    assert ok and detail.startswith("tty")


def test_the_new_variable_is_a_reported_override_and_an_si11_name(approver):
    names = [o.var for o in paths.gate_overrides({"JADE_APPROVER_ANCHOR": "/tmp/x"})]
    assert names == ["JADE_APPROVER_ANCHOR"] and paths.gate_overrides({"JADE_APPROVER_ANCHOR": "/tmp/x"})[0].weakens
    from jade import policy
    assert "SI-11" in policy.assess("Bash", {"command": "export JADE_APPROVER_ANCHOR=/tmp/mine"}, "/tmp/case").blocked
    assert "SI-11" in policy.assess("Bash", {"command": "JADE_APPROVER_ANCHOR=/tmp/mine uv run pytest"}, "/tmp/case").blocked


# ---- review findings (install review, 2026-11-13) -------------------------------------------------------
def test_a_fake_ssh_keygen_first_in_path_cannot_make_a_forgery_verify(approver, tmp_path, monkeypatch):
    """The user's own bin directories come first in PATH on a typical setup. Verification runs the root-owned program by absolute path."""
    attacker = keygen(tmp_path, "attacker2")   # made before PATH is poisoned
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ssh-keygen").write_text("#!/bin/sh\nexit 0\n")
    (fake / "ssh-keygen").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    db, p = blocked()
    forge_with_key(db, p, attacker)
    assert retry() == 2


def test_the_program_paths_are_absolute_and_root_owned_locations():
    assert signing.SSH_KEYGEN.startswith("/usr/bin/") and signing.SUDO.startswith("/usr/bin/")
    assert signing.helper_command()[0] == signing.SUDO


def test_the_scripts_pin_their_own_path():
    for name in ("jade-approve-sign", "install-approver.sh"):
        text = (HELPER.parent / name).read_text()
        assert "export PATH=/usr/bin:/bin" in text, name
    install = (HELPER.parent / "install-approver.sh").read_text()
    assert "visudo -cf" in install and "exit 1" in install.split("visudo -cf", 1)[1].split("sudo install -m 440", 1)[0]   # a failed validation stops the script
    assert "mktemp -d /var/tmp/" in install and "sudo -u \"$USER_NAME\" /usr/bin/ssh-keygen" in install   # the private key is made by the approver, never in the user's own temp


# ---- review findings, second batch ------------------------------------------------------------------------
def test_an_installed_default_anchor_cannot_be_downgraded_by_the_environment(approver, monkeypatch, tmp_path):
    """A missing override target used to read as "no anchor", which is terminal approval again. With a default anchor present the override is ignored."""
    monkeypatch.setattr(signing, "DEFAULT_ANCHOR", approver.anchor)
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(tmp_path / "nothing-here"))
    assert signing.anchor_path() == approver.anchor and signing.backend() == "signed" and signing.anchor_state()[0] == "ok"
    monkeypatch.setenv("JADE_APPROVER_ANCHOR", str(tmp_path / "other"))   # and an override pointing at an anchor of the agent's own is ignored too
    assert signing.anchor_path() == approver.anchor


def test_a_directory_above_the_anchor_that_others_can_write_is_not_trusted(approver):
    approver.dir.chmod(0o775)
    state, why = signing.anchor_state()
    assert state == "untrusted" and "directories" in why
    approver.dir.chmod(0o755)
    assert signing.anchor_state()[0] == "ok"
    approver.dir.parent.chmod(0o777)   # no sticky bit: someone could rename the directory and put their own file there
    try:
        assert signing.anchor_state()[0] == "untrusted"
    finally:
        approver.dir.parent.chmod(0o700)
    approver.dir.parent.chmod(0o1777)   # sticky, like /tmp on Linux (mode 1777): others cannot rename entries they do not own
    try:
        assert signing.anchor_state()[0] == "ok"
    finally:
        approver.dir.parent.chmod(0o700)


def test_the_real_helper_must_be_installed_root_owned(approver, monkeypatch, tmp_path):
    monkeypatch.setattr(signing, "helper_command", lambda: [signing.SUDO, "-u", signing.APPROVER_USER, str(signing.HELPER)])
    monkeypatch.setattr(signing, "HELPER", tmp_path / "missing-helper")
    msg = good_message(approver)
    with pytest.raises(RuntimeError, match="not installed"):
        signing.sign_with_helper(msg)
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o777)
    monkeypatch.setattr(signing, "HELPER", helper)
    with pytest.raises(RuntimeError, match="root-owned|writable|cannot be used"):
        signing.sign_with_helper(msg)


def test_the_helper_ignores_its_test_overrides_when_run_through_sudo(approver):
    msg = good_message(approver)
    tty = approver.dir / "tty"
    tty.write_text(signing.code_of(msg) + "\n")
    env = {**os.environ, "SUDO_USER": "someone", "JADE_APPROVER_KEY": str(approver.key), "JADE_APPROVER_TTY": str(tty), "JADE_APPROVER_TTY_OUT": "/dev/null"}
    r = subprocess.run(["bash", str(HELPER)], input=msg, capture_output=True, text=True, env=env, check=False, stdin=None)
    assert r.returncode != 0 and "BEGIN SSH SIGNATURE" not in r.stdout   # the override key and the fake terminal are not used: no key, no tty, no signature


def test_the_installer_orders_its_steps_so_a_failure_never_leaves_a_half_install():
    text = (HELPER.parent / "install-approver.sh").read_text()
    anchor_install = text.index('sudo install -m 644 -o root "$CONF/allowed_signers.tmp"')
    assert text.index("visudo -cf") < anchor_install and text.index('sudo install -m 755 -o root "$HERE/jade-approve-sign"') < anchor_install   # the anchor is the last step
    assert 'RULE_DENY="$ME ALL=($USER_NAME) !ALL"' in text and "Type INSTALL to continue" in text and "dscl . -create" in text
    assert text.count("approval_backend") >= 1   # uninstall also removes a configured signed backend


def test_the_installer_makes_the_approvers_key_in_a_directory_the_approver_can_enter():
    """The first real run failed with 'Saving key ... Permission denied': a bare `sudo mktemp -d` is under root's private $TMPDIR (macOS /var/folders/zz/...), which the approver
    account cannot traverse. The key directory must be made under a world-traversable absolute path."""
    install = (HELPER.parent / "install-approver.sh").read_text()
    line = next(ln for ln in install.splitlines() if "KEYDIR=" in ln and "mktemp" in ln)
    assert "mktemp -d /var/tmp/" in line, line


def test_the_installer_derives_the_public_key_as_root_after_the_sudoers_rule_is_in_place():
    """The second real run failed: 'user is not allowed to execute ssh-keygen -y as _jadeapprove'. From the sudoers install on, the user may run only the helper as the
    approver, so every later step that needs the key must run as root."""
    install = (HELPER.parent / "install-approver.sh").read_text()
    after = install.split('sudo install -m 440 -o root "$SUDOERS.tmp"', 1)[1]
    line = next(ln for ln in after.splitlines() if "ssh-keygen -y" in ln)
    assert "sudo -u" not in line and "sudo /usr/bin/ssh-keygen -y" in line, line
