#!/bin/bash
# W-90 / docs/10: HUMAN-ONLY. Creates the approver service account and key, then installs the signing helper, the sudoers rule and, last, the trust anchor.
# Read it first; run it yourself in a terminal: `bash scripts/install-approver.sh` (it uses sudo and asks for your admin password).
# An agent must not run it. Undo: `bash scripts/install-approver.sh --uninstall`.
# Untested on a real system by the agent that wrote it; the account creation (dscl) and the sudoers rule are the parts to read twice.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin   # never a directory the agent's user can write to
USER_NAME="${JADE_APPROVER_USER:-_jadeapprove}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ME="$(id -un)"
case "$(uname -s)" in
  Darwin) CONF="/Library/Application Support/jade"; HELPER_DIR="/Library/PrivilegedHelperTools" ;;
  *) CONF="/etc/jade"; HELPER_DIR="/usr/lib/jade" ;;
esac
HELPER="$HELPER_DIR/jade-approve-sign"
SUDOERS="/etc/sudoers.d/jade-approver"
if [ ! -t 0 ] || [ ! -t 1 ]; then echo "install-approver: run this in an interactive terminal, yourself" >&2; exit 1; fi
if [ "$(id -u)" = 0 ]; then echo "install-approver: run it as yourself, not as root (the sudoers rule is for your user)" >&2; exit 1; fi

if [ "${1:-}" = "--uninstall" ]; then
  echo "Removing: $CONF/allowed_signers, $CONF/approver_ed25519, $HELPER, $SUDOERS, the account $USER_NAME, and an approval_backend entry in your jade config. Enter to continue, Ctrl-C to stop."; read -r _
  sudo rm -f "$CONF/allowed_signers" "$CONF/allowed_signers.tmp" "$CONF/approver_ed25519" "$HELPER" "$SUDOERS" "$SUDOERS.tmp"
  if id "$USER_NAME" >/dev/null 2>&1; then
    if [ "$(uname -s)" = Darwin ]; then sudo dscl . -delete "/Users/$USER_NAME" || echo "install-approver: could not delete the account $USER_NAME; remove it with: sudo dscl . -delete /Users/$USER_NAME" >&2
    else sudo userdel "$USER_NAME" || true; fi
  fi
  CFG="${JADE_CONFIG:-$HOME/.config/jade}/config.json"
  if [ -f "$CFG" ] && [ -x /usr/bin/python3 ]; then
    /usr/bin/python3 - "$CFG" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
if d.pop("approval_backend", None) is not None:
    json.dump(d, open(p, "w"), indent=1)
    print("removed approval_backend from", p)
PY
  fi
  sudo -k
  echo "Done. Permits are approved from a terminal again (approval backend tty). Run: jade check"; exit 0
fi

RULE_DEFAULTS="Defaults!$HELPER timestamp_timeout=0"
RULE_DENY="$ME ALL=($USER_NAME) !ALL"
RULE_ALLOW="$ME ALL=($USER_NAME) $HELPER"
echo "== The helper that will be installed ROOT-OWNED and run as '$USER_NAME' (read it; it comes from this checkout, which an agent could have edited):"
echo "== $HERE/jade-approve-sign   sha256: $(shasum -a 256 < "$HERE/jade-approve-sign" | cut -d' ' -f1)"
cat "$HERE/jade-approve-sign"
echo "== The sudoers rule (/etc/sudoers.d/jade-approver):"
printf '   %s\n   %s\n   %s\n' "$RULE_DEFAULTS" "$RULE_DENY" "$RULE_ALLOW"
if git -C "$HERE" rev-parse --git-dir >/dev/null 2>&1 && [ -n "$(git -C "$HERE" status --porcelain -- "$HERE/jade-approve-sign" "$HERE/install-approver.sh")" ]; then
  echo "WARNING: these scripts differ from the last commit of this checkout." >&2
fi
echo "== This will also create the service account '$USER_NAME' (hidden, no login, no shell), an Ed25519 key only that account can read, and a root-owned trust anchor in '$CONF'."
echo "== After this, high and critical permits need a signature from that account (docs/10)."
printf 'Type INSTALL to continue: '; read -r answer
[ "$answer" = "INSTALL" ] || { echo "cancelled; nothing was changed"; exit 1; }

if ! id "$USER_NAME" >/dev/null 2>&1; then
  if [ "$(uname -s)" = Darwin ]; then
    uid=399
    while dscl . -list /Users UniqueID | awk '{print $2}' | grep -qx "$uid"; do uid=$((uid - 1)); [ "$uid" -ge 200 ] || { echo "install-approver: no free uid in 200-399" >&2; exit 1; }; done
    sudo dscl . -create "/Users/$USER_NAME"
    for kv in "UserShell /usr/bin/false" "RealName JADE-approver" "UniqueID $uid" "PrimaryGroupID 1" "NFSHomeDirectory /var/empty" "IsHidden 1" "Password *"; do
      # shellcheck disable=SC2086  # key and value are two words on purpose
      sudo dscl . -create "/Users/$USER_NAME" $kv
    done
  else
    sudo useradd --system --shell /usr/sbin/nologin --home-dir /nonexistent "$USER_NAME"
  fi
fi
id "$USER_NAME" >/dev/null || { echo "install-approver: the account '$USER_NAME' does not exist; nothing else was installed" >&2; exit 1; }

sudo install -d -m 755 -o root "$CONF" "$HELPER_DIR"
if [ ! -f "$CONF/approver_ed25519" ]; then
  # The private key is made by the approver account itself, inside a directory only that account can enter: it never exists where your own user can read it.
  KEYDIR="$(sudo mktemp -d /var/tmp/jade-approver.XXXXXXXX)"   # /var/tmp is enterable by every account; root's own $TMPDIR (a private /var/folders path on macOS) is not, so the approver could not write its key there
  sudo chown "$USER_NAME" "$KEYDIR"; sudo chmod 700 "$KEYDIR"
  sudo -u "$USER_NAME" /usr/bin/ssh-keygen -q -t ed25519 -N '' -C jade-approver -f "$KEYDIR/key"
  sudo install -m 600 -o "$USER_NAME" "$KEYDIR/key" "$CONF/approver_ed25519"
  sudo rm -rf "$KEYDIR"
fi
sudo install -m 755 -o root "$HERE/jade-approve-sign" "$HELPER"
printf '%s\n' "$RULE_DEFAULTS" "$RULE_DENY" "$RULE_ALLOW" | sudo tee "$SUDOERS.tmp" >/dev/null
if ! sudo visudo -cf "$SUDOERS.tmp" >/dev/null; then
  sudo rm -f "$SUDOERS.tmp"
  echo "install-approver: the sudoers rule did not validate, so it was not installed; the trust anchor was NOT written, so nothing changed for permits (re-run, or --uninstall)" >&2; exit 1
fi
sudo install -m 440 -o root "$SUDOERS.tmp" "$SUDOERS"; sudo rm -f "$SUDOERS.tmp"
# Last: the anchor turns the signed backend on. It is made from the key every time, so a re-run after a partial install still ends with a complete one.
PUB="$(sudo -u "$USER_NAME" /usr/bin/ssh-keygen -y -f "$CONF/approver_ed25519" | cut -d' ' -f1,2)"
printf 'jade-approver %s\n' "$PUB" | sudo tee "$CONF/allowed_signers.tmp" >/dev/null
sudo install -m 644 -o root "$CONF/allowed_signers.tmp" "$CONF/allowed_signers"; sudo rm -f "$CONF/allowed_signers.tmp"
sudo -k   # drop any cached sudo ticket
echo "Installed. Now run: jade check   (row 'approval backend' should say signed)"
echo "Then approve a real permit once and try to use it twice: the second use must be refused (docs/10 section 6)."
