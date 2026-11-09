"""Review cluster C, second pass: linear-time redaction, references are not secrets, near variants of the fixed shapes, a purge that reaches the file.

Every secret-shaped test value is assembled from parts so that the repository holds no literal token-shaped string."""
import time

import pytest

from jade import hooks, ledger, ops, permits, redact

R = "Zq8vK2mN5p" + "Q8sT1vW4xZ"                     # an unshaped secret
FAKE = "Zq8fakeSecret" + "Value12345"
PW = "Hunter2" + "Hunter2"
SLACK_C = "xo" + "xc-1234567890-1234567890-" + R
SLACK_D = "xo" + "xd-" + R * 2
HOOK_TIMEOUT_S = ops.HOOKS["PreToolUse"][2]


@pytest.fixture
def human_env(monkeypatch):
    from jade.util import AGENT_ENV

    for k in AGENT_ENV:
        monkeypatch.delenv(k, raising=False)


def call(cmd, cwd="/tmp/x", session="sess-cccccccc"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session, "cwd": cwd}


def everything(db) -> str:
    return "\n".join(db.con.iterdump())


def _timed(text):
    t0 = time.perf_counter()
    redact.redact(text)
    redact.redact(text, secrets_only=True)
    return time.perf_counter() - t0


# ---- the ReDoS: a start without an end must cost one pass, not a rescan from every start -----------------------------
# Each unit is the beginning of a pattern that has no end in the text. Repeated, it is the shape that makes a backtracking engine rescan the rest
# of the text from every start point. 150 KB of it must cost about what 150 KB of prose costs.
_UNITS = [
    "password=[a", "password: a=[a", "token : x[", 'password=\\"a', "password='a", 'password="a', "TOKEN=$(a", "token=`a", "--token $(a",
    "curl -u a", "curl --user a:", "curl -H x -u a:b", "mysql -p", "mysql -u r -p", "docker login -p ", "sshpass -p ", "redis-cli -a ", "mongosh -p ",
    "unzip -P ", "ldapsearch -w ", "htpasswd -b f ", "security find-generic-password -s x -w ", "security add-generic-password -w ", "authorization:a",
    "Authorization: Bearer ", "api key = ", "://u:a", "?key=", "-----BEGIN PRIVATE KEY-----", "x-api-key: a", "Cookie: a", "a@", "; -u a:", "| -p a ", "eyJ-", "eyJabcdefghi.a-", "sk-", "SG.aaaaaaaaaaaaaaaa-",
]


@pytest.mark.parametrize("unit", _UNITS)
def test_redaction_is_linear_on_adversarial_text(unit):
    """`redact()` runs on every gated command before the hook's timeout; a hook that overruns it is killed and the command runs."""
    n = 150_000
    for text in (unit * (n // len(unit)), ("x " + unit) * (n // (len(unit) + 2))):
        assert _timed(text) < 4, f"quadratic backtracking on {unit!r}"


def test_a_long_or_unclosed_bracket_value_is_still_redacted():
    for text in ("password=[" + "A" * 300 + "]", "password=[" + "A" * 300, "password=[" + "short-one" + "]", "password=[" + "A" * 255 + "]", "password=[" + "A" * 256 + "]"):
        out, hits = redact.redact(text)
        assert "AAAA" not in out and "short-one" not in out and hits == 1, out


def test_a_huge_gated_command_is_still_decided_well_before_the_hook_timeout(monkeypatch):
    """The reviewer's DoS: a critical command followed by 150 KB of `password=[a` took 13 s, the host killed the hook (fail open)."""
    monkeypatch.setenv("JADE_GATE_MODE", "enforce")
    t0 = time.monotonic()
    res = hooks.pretool(call("kubectl delete ns prod # " + "password=[a" * 13_636))
    assert res.exit_code == 2
    assert time.monotonic() - t0 < HOOK_TIMEOUT_S / 2


# ---- references are not secrets -------------------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "vercel --prod --token $VERCEL_TOKEN",
    'vercel --prod --token "$VERCEL_TOKEN"',
    "docker run --rm -e TOKEN_TYPE=bearer -e API_KEY=$API_KEY img && rm -rf /data",
    "terraform destroy -auto-approve -var token_url=https://x/oauth/token",
    "terraform destroy -auto-approve -var db_password=${DB_PASSWORD}",
    "git clean -fdx # secret_ref: foo",
    "kubectl delete ns prod --token $(cat /run/secrets/kube-token)",
    "API_KEY=$(op read op://vault/item/key) kubectl delete ns prod",
    "export TOKEN=$(cat ~/.tok) && git push --force origin feat-x",
    "curl -u admin:$PW https://api.example.org/x && git push --force origin feat-x",
    "mysql -u root -p$DB_PASS -e 'select 1' && rm -rf /data",
    "git push --force origin feat-x # the password_file is /etc/x",
])
def test_an_env_reference_or_a_fetch_does_not_make_a_command_unapprovable(cmd, human_env):
    """The block message tells the agent to move a secret into an environment variable: that remedy must not itself leave the command unapprovable."""
    hooks.pretool(call(cmd))
    db = ledger.open()
    p = permits.listing(db, ("PENDING",))[0]
    ctx = permits.ctx_of(p)
    assert "redacted" not in ctx, ctx["subject"]
    assert permits.approve(db, p.permit_id, stdin_isatty=True).state == "ISSUED"


@pytest.mark.parametrize("cmd", [
    "kubectl delete ns prod --token=$(echo " + FAKE + ")",
    "kubectl delete ns prod --token $(echo " + FAKE + ")",
    "TOKEN=$(cat <<< " + FAKE + ") kubectl delete ns prod",
    "curl -u admin:pre" + FAKE + "$X https://x",
    "vercel --token " + FAKE + "$X",
])
def test_a_literal_inside_a_substitution_or_beside_a_reference_is_still_a_secret(cmd):
    out, hits = redact.redact(cmd)
    assert FAKE not in out and hits >= 1, out


@pytest.mark.parametrize("text", [
    "cmd --password $PW", 'cmd --password "$PW"', "cmd --api-key $KEY", "curl -u user:$PW https://x", "curl -u user:${PW} https://x", 'curl -u "user:$PW" https://x',
    "mysql -u r -p$PW", 'mysql -u r -p"$PW"', "cmd --token $(cat tok)", "export TOKEN=$(cat f)", "TOKEN=$(pass show x) cmd", "API_KEY=$(op read op://v/i/k) cmd",
    "PASSWORD=$(cat /run/secrets/pw)", "token_type: bearer", "TOKEN_TYPE=bearer", "token_url: https://x/oauth/token", "secret_ref: foo", "password_file: /etc/x",
    "access_token_expires_in: 3600", "client_secret_path=/run/secrets/x", "tokenType: bearer", "docker login -u u --password-stdin", "git push -o ci.skip origin x",
])
def test_references_and_non_secret_settings_are_left_alone(text):
    assert redact.redact(text) == (text, 0)


def test_a_name_that_merely_resembles_a_location_is_still_a_credential():
    for text in ("SECRET_KEY_BASE=" + PW, "client_secret=" + PW, "token=" + PW, "TOKEN_ID=" + PW, "password_hash=" + PW, "pathtoken=" + PW):
        out, hits = redact.redact(text)
        assert PW not in out and hits == 1, text


# ---- near variants of the shapes the first pass fixed ---------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "curl -uadmin:" + R + " https://x", "curl --user=admin:" + R + " https://x", "curl -u admin:" + R + " https://x",
    "curl -H 'X-A: " + "b" * 300 + "' -u admin:" + R + " https://x",
    "docker login -u u -p " + R, "docker login -u u --password " + R, "podman login -p " + R, "sshpass -p " + R + " ssh h", "sshpass -p'" + R + "' ssh h",
    "redis-cli -a " + R, "redis-cli --pass " + R, "htpasswd -b f u " + R, "htpasswd -bc f u " + R, "htpasswd -nb u " + R, "mongosh -u a -p " + R,
    "unzip -P " + R + " a.zip", "ldapsearch -x -D cn=a -w " + R, "openssl enc -d -passin pass:" + R, "wget --http-password " + R + " x",
    "gpg --passphrase " + R + " -d f",
    "https://x/v1?key=" + R, "https://x/v1?a=b&key=" + R, "api key: " + R, "API key = " + R, "access token: " + R,
    SLACK_C, SLACK_D, "http://hooks.slack.com/" + "services/T01234567/B01234567/" + R, "hooks.slack.com/" + "services/T01234567/B01234567/" + R,
    "https://hooks.slack.com/" + "workflows/T01234567/A01234567/123456/" + R, "discord.com/" + "api/webhooks/123456789012345678/" + R + "abcdefgh",
    "x --token=$(echo " + R + ")",
    "$ security find-generic-password -s FOO -w 2>/dev/null\n" + R, "$ security find-generic-password -s FOO -wa mo\n" + R,
    "$ security find-generic-password -ws FOO\n" + R, "$ security find-generic-password -gw -s FOO\n" + R,
    "/usr/bin/security find-generic-password -s FOO -w\n" + R, "security find-generic-password -s FOO -w\r\n" + R,
])
def test_near_variants_of_the_fixed_shapes_are_redacted(text):
    out, hits = redact.redact(text)
    assert R not in out and hits >= 1, out
    assert redact.redact(out) == (out, 0)   # idempotent


def test_a_flag_is_a_password_flag_only_behind_its_own_command():
    for text in ("ssh -p 2222 host", "kubectl -a foo -p bar", "docker run -p 127.0.0.1:8080:80 img", "unzip -P", "git push -p", "ls -a -w 80", "tar -P -p x"):
        assert redact.redact(text) == (text, 0), text
    assert redact.redact("curl x ; ssh -u a:bcd host")[1] == 0       # a `;` ends the command: the flag belongs to ssh
    assert redact.redact("curl x\nssh -u a:bcd host")[1] == 0


def test_a_keychain_read_whose_output_is_captured_still_redacts_nothing():
    for text in ('x=$(security find-generic-password -s FOO -w)\nkeep this line', "security find-generic-password -s FOO -w | pbcopy\nkeep this line",
                 "security find-generic-password -s FOO\nkeep this line"):
        assert "keep this line" in redact.redact(text)[0], text


# ---- the purge reaches the file ---------------------------------------------------------------------------------------------
def test_purge_leaves_no_trace_of_the_cleared_text_in_the_database_file():
    """Freed pages and the write-ahead log keep old bytes; a purge that clears only the rows leaves the text recoverable from the file."""
    secret = "Zq8unshapedSecretValue" + "12345"   # no shape and no key name: redaction cannot know it, so it is stored
    for cmd in (f"kubectl delete ns prod  # token is {secret}", f"git push --force origin main # {secret}", f"rm -rf /data/{secret}"):
        hooks.pretool(call(cmd))
    db = ledger.open()
    old = "2000-01-01T00:00:00Z"
    for t, col in (("decisions", "created_at"), ("decisions", "updated_at"), ("permits", "issued_at"), ("permits", "expires_at"), ("events", "ts")):
        db.con.execute(f"UPDATE {t} SET {col}=?", (old,))
    assert secret in everything(db), "the probe must be stored first"
    assert ops.purge(db, 90) >= 3
    assert secret not in everything(db)
    files = list(db.path.parent.glob(db.path.name + "*"))
    assert files and all(secret.encode() not in f.read_bytes() for f in files), [f.name for f in files]
    db.close()


def test_a_ledger_zeroes_what_a_later_delete_removes():
    db = ledger.open()
    assert db.con.execute("PRAGMA secure_delete").fetchone()[0] == 1
    db.close()
