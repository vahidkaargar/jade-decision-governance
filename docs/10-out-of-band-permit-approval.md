# 10 — Out-of-band permit approval (W-38 design)

Status: **design approved 2026-11-13 (option B); implemented in W-89 (agent), not yet installed on any machine; install is W-90 (human).** Decisions taken by the maintainer: option B; `jade gate enforce` refuses to start without the signed backend unless an explicit, journaled override is given (section 5, point 4); ledger write path (a), section 5 point 2, is the recommended default and is used unless the maintainer says otherwise.

## 1. Problem

Today a high or critical permit is approved by `jade permit approve` after a terminal check (`jade/permits.py` `approve`, `human_tty_refusal`): a real tty, no agent ancestor process, a typed code, no `-y`. SI-09 blocks the command text. These raise the cost of a mistake or an injected instruction. They are not a boundary against a process that runs as the same OS user, because that process can:

- allocate its own pty, clear the agent environment variables and detach, so the tty check passes (W-38);
- read the permit seal key (`permit-seal.key`, mode 600, same user) and re-seal a forged row (W-41; SI-10 only blocks command text that names the file);
- run in `bypassPermissions`, `auto`, headless or unattended subagent mode, where JADE's gate is the only gate: the 2026-11-13 `jade check` reports 2301 of 2312 gated calls in the last 7 days with no host prompt (W-34).

Enforce mode is only as strong as the approval. This is why W-38 is a precondition of `jade gate enforce` for these sessions.

## 2. Requirements

1. An approval cannot be produced by any process that runs as the agent's OS user, whatever it can execute, allocate or read.
2. The check at use (the PreToolUse hook) needs only public material, so no agent-readable secret decides a permit.
3. The approval binds the exact action: permit id, plan hash, content digest, policy version, expiry and a nonce. Single use stays; the existing 30-minute TTL stays.
4. Fail closed: with the signed backend on, a high or critical permit without a valid signature is never usable, whatever the ledger row says.
5. No new network listener, no new Python dependency, no Apple developer identity.
6. A human-only setup that is reversible by the human alone.

## 3. Options, with what was measured on this Mac (macOS 26.6.2, Mac16,10, 2026-11-13)

| | Option | Verdict | Evidence |
|--|--------|---------|----------|
| A | Secure Enclave or Keychain key with user presence, signed by a small helper | **Not feasible here** | A non-permanent Secure Enclave key can be created by an ad-hoc signed Swift binary, but it dies with the process. A permanent one fails with `OSStatus -34018 (failed to add key to keychain)`: `security find-identity -v -p codesigning` lists 0 identities and there is no provisioning profile. `LAContext` reports `deviceOwnerAuthentication` available and biometrics unavailable (`Biometric accessory is not paired`), so the prompt would be the account password. |
| B | Separate OS user holds an Ed25519 private key; approval is a signature made through `sudo -u`, which asks the human for their own login password | **Feasible, recommended** | `sudo -n true` answers `a password is required` (no cached ticket, no NOPASSWD rule). `ssh-keygen -Y sign` and `-Y verify` (OpenSSH 10.3) round-trip: a good signature verifies in 6 ms; a changed message, and a changed namespace, fail with exit 255. No new dependency. |
| C | FIDO2 security key (`ed25519-sk`, touch required) | Strongest, optional upgrade | Needs hardware this repository cannot assume and an `ssh-keygen` with the security-key provider (Apple's build was not probed for it: **unverified**). Uses the same verifier as B: only the key type in `allowed_signers` changes. |
| D | Approval from a second device | Rejected | Needs a channel from the phone to the Mac: a listener (excluded by the architecture: only nginx is LAN-facing) or a relay service (new trust and a new outage mode). |
| E | Keep the tty check and harden it | Rejected as a boundary | Every added check is something a same-user process can reproduce. Cluster B already did the useful part. |

## 4. Recommended design (B, with C as a drop-in)

### 4.1 Parts

- **Approver account**: a service account (a hidden service account `_jadeapprove`, UID 200-399, shell `/usr/bin/false`, password `*`, no admin rights; created with `dscl` by the install script) that owns an Ed25519 key pair in its own home, mode 600. The private key never exists in the agent's user.
- **Trust anchor**: an `allowed_signers` file holding only the public key, installed root-owned and not writable by the agent's user (suggested `/Library/Application Support/jade/allowed_signers`, `root:wheel`, 0644). The OS enforces this; R-13 and SI-09 only add a block message. The hook refuses to use a trust anchor that the agent's user can write.
- **Signing helper** (`scripts/jade-approve-sign`, installed root-owned at `/Library/PrivilegedHelperTools/jade-approve-sign` (macOS) or `/usr/lib/jade/jade-approve-sign`, run as `_jadeapprove`): receives the approval message on stdin, shows it on `/dev/tty`, asks for the 6-character code derived from that message (`signing.code_of`), and signs only a message of the permit-approval shape. It never reads the ledger (the approver account cannot, and need not). `jade permit approve` starts it as `/usr/bin/sudo -u _jadeapprove <helper>` (absolute paths: a `sudo` or `ssh-keygen` found through PATH could be the agent's); sudo asks for the human's own login password. The sudoers drop-in sets `timestamp_timeout=0` for the helper, denies every other command as the approver account (`!ALL`, so an admin rule cannot read the key), and has no `NOPASSWD`.
- **Approval message** (one canonical line, UTF-8): `jade-permit-approval v1 | permit <id> | plan <plan_hash> | content <content_digest> | policy <policy_version> | principal human:<user> | expires <rfc3339> | nonce <random>`. Namespace for `ssh-keygen -Y`: `jade-permit-approval`.
- **Ledger**: the permit document gains `approval_signature` (the armored signature) and `approval_valid_until`; `approval_reference` may now start with `sig:`. The message is rebuilt from the row at every check, so it is not stored. `schemas/permit.schema.json` changes are additive. An approval from a terminal stays valid for permits below high.
- **Hook at consume** (`permits.try_consume`, beside the existing seal check): with `approval_backend: signed` in the human's config, a permit at high or critical needs `ssh-keygen -Y verify -f <allowed_signers> -I jade-approver -n jade-permit-approval -s <sig>` to succeed on a message that matches the current plan hash, content digest, policy version, expiry and an unused nonce. The verify call gets a hard timeout, and any failure, timeout or missing file blocks.

### 4.2 Flow

1. The gate blocks a high call and creates a PENDING permit (unchanged).
2. The agent reports the permit id. The human runs `jade permit approve prm_xxx` in their own terminal: the existing review and typed code, then sudo asks for their login password and the helper shows the message and asks for its code.
3. `jade permit approve` verifies the returned signature itself, then issues the permit with `approval_reference` `sig:<user>@<time>`.
4. The agent retries the command. The hook rebuilds the message, verifies the signature against the root-owned anchor and consumes the permit once.

### 4.3 What this closes and what it does not

| Item | Effect |
|------|--------|
| W-38 (forged human approval by pty and double fork) | Closed for the signed backend: no agent-side process holds the private key, and the sudo prompt needs a password the agent does not have. |
| W-41 (seal key readable, a re-sealed forged row) | Closed for approvals: the seal stops deciding a high or critical permit, the signature does. The HMAC stays for integrity of low-risk rows. |
| W-84 (reveal file readable) | Not closed. Moving the raw call to a file readable only by the approver account is possible but is a separate change. |
| W-81 (`jade ack` session id self-asserted) | Not closed. Different identity problem. |
| An agent that types the human's password (computer-use, a keylogger, a password in the vault) | **Residual.** Mitigation: the prompt is on `/dev/tty` through `sudo`, no `NOPASSWD`, and the account password is not stored in Open Vault. State it in docs/05 T19. |
| An agent that edits the hook configuration or the installed code | Unchanged: R-13, R-16, SI-09 and the root-owned trust anchor. |
| A trojaned `jade`, `sudo` or shell startup file (user-writable) that relays the sudo prompt and keeps the password | **Residual, not fixable inside one OS user.** The helper's display cannot be trusted to the human by a program the agent can replace, and a captured password lets the agent run the helper itself. Mitigation: option C (a FIDO2 key, touch per approval) removes the password from the scheme: W-91. |
| The ledger can be edited by the same user: `UPDATE permits SET state='ISSUED', uses=0` plus deleting the `permit_consumed` event re-arms a consumed permit | **Residual.** The signature still binds the exact command and expires within the 30-minute window, so this is a replay of what the human approved, not a new action. "Used once" is therefore tamper-evident, not tamper-proof, until the consumption record lives outside the user's reach: W-92. |
| An admin rule (`%admin ALL=(ALL) ALL`) lets the human run any command as the approver, including `cat` of the key | Closed by the `!ALL` line in the drop-in. A live root sudo ticket still defeats everything the human can do as root: the install script ends with `sudo -k`. |
| Install from an agent-edited checkout | The script prints the helper (and its sha256) and the exact sudoers rule and needs `INSTALL` typed; it warns when the scripts differ from the last commit. Reading them is the control. |

### 4.4 Independent review (2026-11-13)

An independent adversarial review of the verifier, the helper and the install script found: the downgrade through a missing `JADE_APPROVER_ANCHOR` target (fixed: an installed default anchor always wins), a helper path and anchor whose parent directories were not checked (fixed: every ancestor must be root-owned and not group- or other-writable unless sticky; the helper moved to `/Library/PrivilegedHelperTools`), test overrides live in the production helper (fixed: ignored when run through sudo), an install order that could leave an anchor without a working helper (fixed: the anchor is written last, from the key, every run), the admin rule reaching the key (fixed: `!ALL`), the install-from-checkout trust problem (mitigated: the script shows what it installs), and a macOS account made with `sysadminctl` that would be an ordinary login account (changed to a hidden `dscl` service account, **untested**). Two findings are residual and recorded as W-91 and W-92 (table above). Every fix has a test in `tests/acceptance/test_signed_approval_w89.py` (28 cases); the PATH one was checked to fail without the fix.

## 5. Open points that need the maintainer

1. **Create the approver account** (needs the admin password; the agent cannot, and should not): is a dedicated standard user acceptable on the Mac mini? The cost is one `sysadminctl` call and one sudoers drop-in, both in an install script the human reads and runs.
2. **Ledger write path (resolved in the implementation).** The approver account never touches the ledger: `jade permit approve` pipes the message to the helper and receives the signature on stdout, so no `attach` command exists and the ledger stays owned by one user. The signature is self-authenticating: the hook checks it, not who stored it.
3. **Hardware key.** If you own a FIDO2 key and want touch-per-approval, say so: option C replaces the key, not the design.
4. **Rollout rule (decided 2026-11-13).** `jade gate enforce` refuses to start while the backend is `tty` and the last 7 days show gated calls with no host prompt, unless the human passes `--allow-unsigned`, which is journaled as event `enforce_unsigned_override`.

## 6. Implementation plan

| Id | Owner | Work | Done when |
|----|-------|------|-----------|
| W-89 | agent | **Done in this branch:** `jade/signing.py`, schema fields, the check at use in `permits._static_refusal`, signing inside `permits.approve`, the `approval backend` check row, the enforce pre-flight and `--allow-unsigned`, `scripts/jade-approve-sign`, `scripts/install-approver.sh`, `tests/acceptance/test_signed_approval_w89.py` (20 cases: forged row with and without the seal key, another key, replay on another permit, edited field, expired window, writable or foreign-owned or symlinked anchor, hung verify, helper code and shape, enforce pre-flight, check row, the new SI-11 variable). | Full suite green; with no anchor and no config key nothing changes. |
| W-90 | human | Read and run `scripts/install-approver.sh` (written in W-89, never run by an agent and **not run even once by the agent: untested on a real system**): creates the account and the key, installs the root-owned trust anchor, helper and sudoers drop-in; then `jade check` shows the new row ok. | A signed approval from a real sudo prompt is consumed once and a replay is refused. |
| — | human | Switch `approval_backend` to `signed`; then `jade gate enforce` (docs/08 section 2.2). The 14-day enforce soak starts here. | |

Rollback: `bash scripts/install-approver.sh --uninstall` (removes the anchor, key, helper, sudoers rule and the account), and `jade gate advisory`. Without the anchor the backend is `tty` again; permits that were approved with a signature are then refused (their reference is `sig:`) and must be requested again. An agent cannot remove the root-owned anchor, so it cannot downgrade the backend.

## 7. Probe commands (reproducible)

```bash
sudo -n true                                   # "a password is required"
ssh-keygen -q -t ed25519 -N '' -C jade-approver -f k
ssh-keygen -Y sign   -f k -n jade-permit-approval msg
ssh-keygen -Y verify -f allowed -I jade-approver -n jade-permit-approval -s msg.sig < msg   # exit 0; a changed msg or namespace: exit 255
swiftc probe.swift && ./probe                  # LAContext availability; Secure Enclave key: permanent fails with -34018
security find-identity -v -p codesigning       # 0 valid identities
```
