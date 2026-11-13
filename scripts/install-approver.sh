#!/bin/bash
# W-90 / docs/10: HUMAN-ONLY. Creates the approver account and key, installs the root-owned trust anchor, signing helper and sudoers drop-in.
# Read it first; run it yourself in a terminal: `bash scripts/install-approver.sh` (it uses sudo and asks for your admin password).
# An agent must not run it. Undo: `bash scripts/install-approver.sh --uninstall`.
set -euo pipefail
USER_NAME="${JADE_APPROVER_USER:-jadeapprove}"
HERE="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -s)" in
  Darwin) CONF="/Library/Application Support/jade"; LIBEXEC="/usr/local/libexec"; SUDOERS="/etc/sudoers.d/jade-approver" ;;
  *) CONF="/etc/jade"; LIBEXEC="/usr/local/libexec"; SUDOERS="/etc/sudoers.d/jade-approver" ;;
esac
HELPER="$LIBEXEC/jade-approve-sign"
if [ ! -t 0 ] || [ ! -t 1 ]; then echo "install-approver: run this in an interactive terminal, yourself" >&2; exit 1; fi
if [ "${1:-}" = "--uninstall" ]; then
  echo "Removing: $CONF/allowed_signers, $CONF/approver_ed25519, $HELPER, $SUDOERS, and the account $USER_NAME. Press Enter to continue, Ctrl-C to stop."; read -r _
  sudo rm -f "$CONF/allowed_signers" "$CONF/approver_ed25519" "$HELPER" "$SUDOERS"
  if [ "$(uname -s)" = Darwin ]; then sudo sysadminctl -deleteUser "$USER_NAME" || true; else sudo userdel "$USER_NAME" || true; fi
  echo "Done. Permits are approved from a terminal again (approval_backend tty)."; exit 0
fi
echo "This will create the account '$USER_NAME' (standard user, no login), an Ed25519 approver key it alone can read, a root-owned trust anchor in '$CONF',"
echo "the helper '$HELPER' and a sudoers rule that lets YOU run that helper as '$USER_NAME' with your password every time (no NOPASSWD, no cached ticket)."
echo "After this, high and critical permits need a signature from that account (docs/10). Press Enter to continue, Ctrl-C to stop."; read -r _
if ! id "$USER_NAME" >/dev/null 2>&1; then
  if [ "$(uname -s)" = Darwin ]; then
    sudo sysadminctl -addUser "$USER_NAME" -fullName "JADE approver" -password - -shell /usr/bin/false -home /var/empty
  else
    sudo useradd --system --shell /usr/sbin/nologin --home-dir /nonexistent "$USER_NAME"
  fi
fi
sudo install -d -m 755 -o root -g wheel "$CONF" 2>/dev/null || sudo install -d -m 755 -o root -g root "$CONF"
if [ ! -f "$CONF/approver_ed25519" ]; then
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  ssh-keygen -q -t ed25519 -N '' -C jade-approver -f "$TMP/key"
  sudo install -m 600 -o "$USER_NAME" "$TMP/key" "$CONF/approver_ed25519"
  printf 'jade-approver %s\n' "$(cut -d' ' -f1,2 "$TMP/key.pub")" > "$TMP/allowed_signers"
  sudo install -m 644 -o root "$TMP/allowed_signers" "$CONF/allowed_signers"
fi
sudo install -d -m 755 -o root "$LIBEXEC"
sudo install -m 755 -o root "$HERE/jade-approve-sign" "$HELPER"
printf '%s\n' "Defaults!$HELPER timestamp_timeout=0" "$(id -un) ALL=($USER_NAME) $HELPER" | sudo tee "$SUDOERS.tmp" >/dev/null
sudo visudo -cf "$SUDOERS.tmp" >/dev/null && sudo install -m 440 -o root "$SUDOERS.tmp" "$SUDOERS"; sudo rm -f "$SUDOERS.tmp"
echo "Installed. Check: jade check   (row 'approval backend' should say signed)"
