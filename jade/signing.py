"""W-38 / W-89: out-of-band permit approval (docs/10-out-of-band-permit-approval.md).

A high or critical permit is approved by a signature that only a separate OS user can make (the approver account, reached with `sudo -u` and a password
prompt). The hook verifies it with a public key from a root-owned file, so nothing the agent's own user can read or write decides an approval.

The signed backend is on when the trust anchor exists (an agent cannot remove a root-owned file, so it cannot downgrade) or when the human's config says
`"approval_backend": "signed"`. The anchor path is never read from the config. The one environment variable that moves it (`JADE_APPROVER_ANCHOR`, for the test suite) is a gate
override like `JADE_HOME`: SI-11 blocks an agent from setting it, `jade check` reports it as weakening, and a file the agent's user owns fails the ownership check anyway.

Signing and verifying use `ssh-keygen -Y` (OpenSSH 8.1 or newer, shipped with macOS and every Linux with openssh-client): no dependency is added.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import paths
from .util import canonical

NAMESPACE = "jade-permit-approval"
IDENTITY = "jade-approver"
APPROVER_USER = "_jadeapprove"
DEFAULT_ANCHOR = Path("/Library/Application Support/jade/allowed_signers" if sys.platform == "darwin" else "/etc/jade/allowed_signers")
ANCHOR_ENV = "JADE_APPROVER_ANCHOR"   # one of the gate-override variables (paths.gate_overrides, SI-11): the test suite points it at a temporary file; a value in the host's
                                      # environment is reported by `jade check` and is a weakening override unless it names the default path
HELPER = Path("/Library/PrivilegedHelperTools/jade-approve-sign" if sys.platform == "darwin" else "/usr/lib/jade/jade-approve-sign")
# Absolute, root-owned programs only. A bare name is looked up in PATH, whose first entries are the agent user's own bin directories on a typical setup: a
# `ssh-keygen` placed there that exits 0 would make every forged approval verify.
SSH_KEYGEN = "/usr/bin/ssh-keygen"
SUDO = "/usr/bin/sudo"
TRUSTED_UID = 0                  # the trust anchor and its directory must belong to this user (root); tests set it to their own uid
VERIFY_TIMEOUT_S = 5
SIGN_TIMEOUT_S = 300


def anchor_path() -> Path:
    """The default root-owned anchor wins whenever it exists: neither the environment nor the config can move the check away from an installed anchor (a missing
    override target would otherwise read as "no anchor", which is a downgrade to terminal approval). The override is honoured only while no default anchor exists,
    which is the test suite's case."""
    if os.path.lexists(DEFAULT_ANCHOR):
        return DEFAULT_ANCHOR
    return Path(os.environ[ANCHOR_ENV]) if os.environ.get(ANCHOR_ENV) else DEFAULT_ANCHOR


def chain_refusal(path: Path) -> str | None:
    """Why the directories above `path` cannot be trusted, or None. Every ancestor must belong to root (or TRUSTED_UID) and must not be writable by group or
    others, except a sticky directory such as /tmp: whoever can rename a directory on the way can put their own file at the path."""
    try:
        d = Path(os.path.realpath(path.parent))
        while True:
            st = os.stat(d)
            if st.st_uid not in (0, TRUSTED_UID):
                return f"{d} is owned by uid {st.st_uid}"
            if st.st_mode & 0o020 or (st.st_mode & 0o002 and not st.st_mode & 0o1000):
                return f"{d} is writable by group or others (mode {st.st_mode & 0o7777:o})"
            if d == d.parent:
                return None
            d = d.parent
    except OSError as e:
        return f"{path} cannot be inspected ({e.strerror})"

MESSAGE_RE = re.compile(r"^jade-permit-approval v1 \| permit prm_[0-9a-f]{12} \| plan [0-9a-f]{64} \| pre [0-9a-f]{64} \| policy [0-9a-f]{7,40} \| risk (?:high|critical) "
                        r"\| principal human:[a-z0-9._-]+ \| valid_until [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z \| nonce [0-9a-f]{32}$")


def message(*, permit_id: str, plan_hash: str, preconditions, policy_version: str, risk: str, principal: str, valid_until: str, nonce: str) -> str:
    """The one line a human signs. It names everything that decides what the permit covers: the permit, the command's plan hash, the preconditions (which
    carry the content digest of a Write, Edit or MCP call), the policy, the risk, who approved, until when, and the permit's own nonce."""
    pre = hashlib.sha256(canonical(preconditions or []).encode()).hexdigest()
    return (f"jade-permit-approval v1 | permit {permit_id} | plan {plan_hash} | pre {pre} | policy {policy_version} | risk {risk} "
            f"| principal {principal} | valid_until {valid_until} | nonce {nonce}")


def code_of(msg: str) -> str:
    """The 6-character code shown with the message and typed again at the helper: derived from the message, so a message swapped in between is a different code."""
    return hashlib.sha256(msg.encode()).hexdigest()[:6].upper()


def anchor_state() -> tuple[str, str]:
    """("absent", why) | ("ok", "") | ("untrusted", why). Untrusted: a symlink, not a regular file, not owned by TRUSTED_UID, or writable by anyone else, or the
    same for its directory."""
    try:
        st = os.lstat(anchor_path())
    except FileNotFoundError:
        return "absent", f"{anchor_path()} does not exist"
    except OSError as e:
        return "untrusted", f"{anchor_path()} cannot be inspected ({e.strerror})"
    if not stat.S_ISREG(st.st_mode):
        return "untrusted", f"{anchor_path()} is not a regular file"
    if st.st_uid != TRUSTED_UID:
        return "untrusted", f"the trust anchor file is owned by uid {st.st_uid}, not {TRUSTED_UID}"
    if st.st_mode & 0o022:
        return "untrusted", f"the trust anchor file is writable by group or others (mode {st.st_mode & 0o777:o})"
    if (why := chain_refusal(anchor_path())):
        return "untrusted", f"the trust anchor's directories cannot be trusted: {why}"
    if not os.access(anchor_path(), os.R_OK):
        return "untrusted", f"{anchor_path()} is not readable by this user"
    return "ok", ""


def signed_backend() -> bool:
    """True when high and critical permits need a signature: the anchor exists, trusted or not (an untrusted one fails closed), or the config asks for it."""
    return anchor_state()[0] != "absent" or paths.config().get("approval_backend") == "signed"


def backend() -> str:
    return "signed" if signed_backend() else "tty"


def verify(msg: str, signature: str) -> str | None:
    """None when `signature` is a good signature by the approver key over exactly `msg`; otherwise why not. Fails closed on every error."""
    state, why = anchor_state()
    if state != "ok":
        return f"the approver trust anchor cannot be used: {why}"
    if not MESSAGE_RE.match(msg) or not isinstance(signature, str) or not signature.strip():
        return "the approval message or signature is malformed"
    sig_file = None
    try:
        fd, name = tempfile.mkstemp(prefix="jade-sig-")
        sig_file = Path(name)
        with os.fdopen(fd, "w") as fh:
            fh.write(signature if signature.endswith("\n") else signature + "\n")
        r = subprocess.run([SSH_KEYGEN, "-Y", "verify", "-f", str(anchor_path()), "-I", IDENTITY, "-n", NAMESPACE, "-s", str(sig_file)],
                           input=msg.encode(), capture_output=True, timeout=VERIFY_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return f"the signature could not be checked ({type(e).__name__})"
    finally:
        if sig_file is not None:
            sig_file.unlink(missing_ok=True)
    return None if r.returncode == 0 else "the approval signature does not verify against the approver key"


def helper_refusal() -> str | None:
    try:
        st = os.lstat(HELPER)
    except OSError as e:
        return f"it is not installed ({e.strerror}); run scripts/install-approver.sh (docs/10)"
    if not stat.S_ISREG(st.st_mode):
        return "it is not a regular file"
    if st.st_uid != 0 or st.st_mode & 0o022:
        return "it is not root-owned or is writable by group or others"
    return chain_refusal(HELPER)


def helper_command() -> list[str]:
    """What runs the approver's signing helper: `sudo -u <approver> <helper>`, which asks the human for their login password on /dev/tty."""
    return [SUDO, "-u", APPROVER_USER, str(HELPER)]


def sign_with_helper(msg: str) -> str:
    """Ask the approver account to sign `msg` (human terminal: sudo asks for the account password, the helper shows the message and asks for its code).
    Returns the armored signature, or raises RuntimeError."""
    if not MESSAGE_RE.match(msg):
        raise RuntimeError("refusing to sign a message that is not a permit approval")
    cmd = helper_command()
    if cmd[0] == SUDO:   # the real helper: it runs as another user through sudo, so it and every directory above it must belong to root
        why = helper_refusal()
        if why:
            raise RuntimeError(f"the approver helper {HELPER} cannot be used: {why}; nothing was signed")
    try:
        r = subprocess.run(cmd, input=msg.encode(), stdout=subprocess.PIPE, timeout=SIGN_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"the approver helper could not run ({type(e).__name__})") from e
    if r.returncode != 0 or b"BEGIN SSH SIGNATURE" not in r.stdout:
        raise RuntimeError(f"the approver helper did not sign (exit {r.returncode}); no approval was made")
    return r.stdout.decode()
