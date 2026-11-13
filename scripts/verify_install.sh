#!/bin/sh
# Build the wheel and sdist, install the wheel into an isolated uv tool directory and run it, the way a user without a checkout would.
#
#   sh scripts/verify_install.sh
#
# No network by default (UV_OFFLINE=1: the build backend and the dependencies must be in uv's cache; set UV_OFFLINE=0 on a runner that has to download them).
# Nothing outside a temporary directory is read or written: HOME, UV_TOOL_DIR, UV_TOOL_BIN_DIR, JADE_HOME and JADE_CONFIG all point into it, and the
# script never runs `jade hooks install`, `jade setup` without --status, or anything that edits ~/.claude, a LaunchAgent or the keychain.
# VERIFY_INSTALL_DIST=<dir> verifies the one wheel and one sdist already in <dir> instead of building them (the release workflow verifies the files it publishes).
# VERIFY_INSTALL_KEEP_DIST=<dir> copies the wheel and sdist that were verified there.
# Exit 0 only when every step gave the expected result; the first unexpected result prints one line starting `verify_install: FAIL` and exits 1.
set -eu

fail() {
    printf 'verify_install: FAIL: %s\n' "$*" >&2
    exit 1
}
step() { printf 'verify_install: %s\n' "$*"; }

ROOT=$(cd "$(dirname "$0")/.." && pwd)
UV=$(command -v uv) || fail "uv is not installed"

# uv's own caches and managed Pythons live under the real HOME: resolve them before HOME moves.
UV_CACHE_DIR=${UV_CACHE_DIR:-$("$UV" cache dir)}
UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$("$UV" python dir)}
export UV_CACHE_DIR UV_PYTHON_INSTALL_DIR
UV_OFFLINE=${UV_OFFLINE:-1}
export UV_OFFLINE
OFFLINE=""
[ "$UV_OFFLINE" = 0 ] || OFFLINE="--offline"

WORK=$(mktemp -d) || fail "mktemp failed"
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
HOME="$WORK/home"
UV_TOOL_DIR="$WORK/tools"
UV_TOOL_BIN_DIR="$WORK/bin"
JADE_HOME="$WORK/jade-home"
JADE_CONFIG="$WORK/jade-config"
JADE_ACK_OVERRIDES="JADE_HOME,JADE_CONFIG"   # `jade check` warns about both on purpose; they are what keeps this run off the real ledger
mkdir -p "$HOME" "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR" "$JADE_HOME" "$JADE_CONFIG" "$WORK/dist" "$WORK/cwd"
export HOME UV_TOOL_DIR UV_TOOL_BIN_DIR JADE_HOME JADE_CONFIG JADE_ACK_OVERRIDES
unset JADE_GATE_MODE JADE_POLICY_DIR JADE_APPROVER_ANCHOR JADE_REGISTRY_DIR JADE_CHECKOUT JADE_SESSION_ID JADE_CLAUDE_SETTINGS JADE_LAUNCH_AGENTS JADE_MANAGED_SETTINGS \
    OPENROUTER_API_KEY NINEROUTER_KEY XDG_CONFIG_HOME XDG_DATA_HOME || true
PATH="$UV_TOOL_BIN_DIR:$PATH"
export PATH
if [ -n "${VERIFY_INSTALL_DIST:-}" ]; then   # absolute before the cd below
    VERIFY_INSTALL_DIST=$(cd "$VERIFY_INSTALL_DIST" 2>/dev/null && pwd) || fail "VERIFY_INSTALL_DIST is not a directory"
fi
if [ -n "${VERIFY_INSTALL_KEEP_DIST:-}" ]; then
    mkdir -p "$VERIFY_INSTALL_KEEP_DIST" && VERIFY_INSTALL_KEEP_DIST=$(cd "$VERIFY_INSTALL_KEEP_DIST" && pwd) || fail "cannot create VERIFY_INSTALL_KEEP_DIST"
fi
cd "$WORK/cwd"   # not the checkout: nothing here may shadow the installed package

VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -n 1)
[ -n "$VERSION" ] || fail "no version in pyproject.toml"

# 1. build ------------------------------------------------------------------------------------------------------
if [ -n "${VERIFY_INSTALL_DIST:-}" ]; then
    cp "$VERIFY_INSTALL_DIST"/*.whl "$VERIFY_INSTALL_DIST"/*.tar.gz "$WORK/dist/" 2>/dev/null || fail "VERIFY_INSTALL_DIST needs a wheel and an sdist"
else
    # shellcheck disable=SC2086 # $OFFLINE is empty or one flag
    "$UV" build $OFFLINE --no-config --out-dir "$WORK/dist" "$ROOT" >"$WORK/build.log" 2>&1 || { cat "$WORK/build.log" >&2; fail "uv build failed (offline needs the build backend in uv's cache; UV_OFFLINE=0 allows the network)"; }
fi
WHEEL=$(ls "$WORK"/dist/*.whl 2>/dev/null) || fail "no wheel was built"
SDIST=$(ls "$WORK"/dist/*.tar.gz 2>/dev/null) || fail "no sdist was built"
[ "$(printf '%s\n' "$WHEEL" | wc -l | tr -d ' ')" = 1 ] && [ "$(printf '%s\n' "$SDIST" | wc -l | tr -d ' ')" = 1 ] || fail "expected exactly one wheel and one sdist"
case "$WHEEL" in *"-$VERSION-"*) ;; *) fail "wheel $WHEEL does not carry version $VERSION" ;; esac
step "build ok: $(basename "$WHEEL"), $(basename "$SDIST")"

# 2. install ----------------------------------------------------------------------------------------------------
# shellcheck disable=SC2086
"$UV" tool install $OFFLINE --no-config "$WHEEL" >"$WORK/install.log" 2>&1 || { cat "$WORK/install.log" >&2; fail "uv tool install failed (offline needs the dependencies in uv's cache)"; }
for exe in jade jev; do
    [ -x "$UV_TOOL_BIN_DIR/$exe" ] || fail "the wheel did not install an executable named $exe"
done
JADE="$UV_TOOL_BIN_DIR/jade"
PY="$UV_TOOL_DIR/jade-decision-governance/bin/python"
[ -x "$PY" ] || fail "no interpreter in the tool environment"
step "install ok"

# 3. contents: what an install without a checkout needs is in both archives ------------------------------------------
"$PY" -I -c 'import sys, zipfile; print("\n".join(zipfile.ZipFile(sys.argv[1]).namelist()))' "$WHEEL" >"$WORK/wheel.list"
"$PY" -I -c 'import sys, tarfile; print("\n".join(n.split("/", 1)[1] for n in tarfile.open(sys.argv[1]).getnames() if "/" in n))' "$SDIST" >"$WORK/sdist.list"
for member in policies/loader.py policies/risk.yaml policies/safety.yaml policies/routing.yaml policies/escalation.yaml policies/promotion.yaml \
    jade/_skills/jade/SKILL.md jade/_docs/claude-md-jev-section.md jade/_schemas/permit.schema.json jade/_schemas/improvement.schema.json \
    jade/_registry/q.verify_claim.supported_by_evidence.json; do
    grep -Fxq -- "$member" "$WORK/wheel.list" || fail "the wheel lacks $member"
done
for member in pyproject.toml uv.lock LICENSE README.md CHANGELOG.md skills/jade/SKILL.md docs/claude-md-jev-section.md registry/q.verify_claim.supported_by_evidence.json \
    schemas/permit.schema.json policies/risk.yaml scripts/verify_install.sh; do
    grep -Fxq -- "$member" "$WORK/sdist.list" || fail "the sdist lacks $member"
done
# the wheel holds every file of the source tree that the package ships: not a short list, because the modules and data a verified command never loads
# (permits.py, githook.py, one registry entry) would otherwise be lost without a failure
"$PY" -I - "$ROOT" "$WORK/wheel.list" >"$WORK/tree.missing" 2>"$WORK/tree.err" <<'PYEOF' || { cat "$WORK/tree.err" >&2; fail "could not compare the wheel with the source tree"; }
import pathlib, subprocess, sys

root, have = pathlib.Path(sys.argv[1]), set(pathlib.Path(sys.argv[2]).read_text().split("\n"))
try:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"], capture_output=True, check=True).stdout
    tracked = [n for n in out.decode().split("\0") if n]
except (OSError, subprocess.CalledProcessError):   # an sdist unpacked without git: every file of the shipped directories
    tracked = [p.relative_to(root).as_posix() for d in ("jade", "policies", "registry", "schemas", "skills") for p in (root / d).rglob("*") if p.is_file()]
for name in sorted(tracked):
    if "__pycache__" in name or name.endswith(".pyc") or not (root / name).is_file():
        continue
    for src, dst in (("jade/", "jade/"), ("policies/", "policies/"), ("registry/", "jade/_registry/"), ("schemas/", "jade/_schemas/"), ("skills/", "jade/_skills/")):
        if name.startswith(src):
            member = dst + name[len(src):]
            break
    else:
        if name != "docs/claude-md-jev-section.md":
            continue
        member = "jade/_docs/claude-md-jev-section.md"
    if member not in have:
        print(member)
PYEOF
if [ -s "$WORK/tree.missing" ]; then
    fail "the wheel lacks $(head -n 1 "$WORK/tree.missing")$([ "$(wc -l <"$WORK/tree.missing" | tr -d ' ')" -gt 1 ] && printf ' (and %s more)' "$(($(wc -l <"$WORK/tree.missing" | tr -d ' ') - 1))")"
fi
# a private overlay, a baseline dataset or a ledger must never be packaged
if grep -E '(^|/)(dataset-[^/]*\.jsonl|[^/]*\.sqlite)$|\.local\.yaml$' "$WORK/wheel.list" "$WORK/sdist.list" | grep -v '\.example\.yaml$' >"$WORK/leak.list"; then
    fail "a private file is packaged: $(head -n 3 "$WORK/leak.list" | tr '\n' ' ')"
fi
step "contents ok: $(wc -l <"$WORK/wheel.list" | tr -d ' ') wheel members, $(wc -l <"$WORK/sdist.list" | tr -d ' ') sdist members"

# 4. jade --version -------------------------------------------------------------------------------------------------
"$JADE" --version >"$WORK/version.out" 2>&1 || fail "jade --version exited non-zero: $(cat "$WORK/version.out")"
[ "$(head -n 1 "$WORK/version.out")" = "jade $VERSION" ] || fail "jade --version printed '$(head -n 1 "$WORK/version.out")', expected 'jade $VERSION'"
grep -q '^commit: unknown$' "$WORK/version.out" || fail "an installed package must report commit: unknown"
step "jade --version ok: $VERSION"

# 5. jade check (offline): only the hooks row may fail on a machine that has not run `jade hooks install` ----------------
set +e
"$JADE" check --offline >"$WORK/check.out" 2>"$WORK/check.err"
rc=$?
set -e
[ "$rc" = 1 ] || { cat "$WORK/check.out" "$WORK/check.err" >&2; fail "jade check exited $rc, expected 1 (the hooks are not installed in a fresh HOME)"; }
sed -n 's/^FAIL  \([^:]*\): .*/\1/p' "$WORK/check.out" >"$WORK/check.failed"
[ "$(cat "$WORK/check.failed")" = "hooks installed" ] || { cat "$WORK/check.out" >&2; fail "jade check failed rows: $(tr '\n' ',' <"$WORK/check.failed"), expected only 'hooks installed'"; }
for row in "ledger writable" "policies load" "ledger integrity" "code version" "question registry" "jev shim" "maintain job"; do
    grep -q "^ok    $row: " "$WORK/check.out" || { cat "$WORK/check.out" >&2; fail "jade check has no ok row '$row'"; }
done
grep -q 'installed package (no git checkout)' "$WORK/check.out" || fail "jade check did not recognise an installed package"
step "jade check ok: the only failing row is 'hooks installed'"

# 6. jade setup --status: read-only, reports what is missing ---------------------------------------------------------------
set +e
"$JADE" setup --status >"$WORK/status.out" 2>"$WORK/status.err"
rc=$?
set -e
[ "$rc" = 0 ] || [ "$rc" = 1 ] || { cat "$WORK/status.out" "$WORK/status.err" >&2; fail "jade setup --status exited $rc"; }
grep -q 'Traceback' "$WORK/status.out" "$WORK/status.err" && fail "jade setup --status crashed"
for row in "Claude Code hooks" "skill file" "skill link"; do
    grep -q "^missing  $row: " "$WORK/status.out" || { cat "$WORK/status.out" >&2; fail "jade setup --status does not report '$row' as missing"; }
done
# launchd exists on macOS only: elsewhere the row is `n/a`, not `missing`
if [ "$("$PY" -I -c 'import sys; print(sys.platform)')" = darwin ]; then plist_tag="missing"; else plist_tag="n/a    "; fi   # the tags are padded to seven characters
grep -q "^$plist_tag  maintenance job plist: " "$WORK/status.out" || { cat "$WORK/status.out" >&2; fail "jade setup --status does not report 'maintenance job plist' as $plist_tag"; }
for made in .claude .agents Library; do
    [ ! -e "$HOME/$made" ] || fail "jade setup --status wrote $made into HOME"
done
step "jade setup --status ok"

# 7. the shipped data is read from the installed package -----------------------------------------------------------------
"$JADE" claude-md show >"$WORK/section.out" 2>&1 || fail "jade claude-md show failed: $(head -n 3 "$WORK/section.out")"
[ -s "$WORK/section.out" ] || fail "jade claude-md show printed nothing"
"$PY" -I -c 'import sys; from jade import setup; sys.exit(0 if setup.skill_source().is_file() else 1)' || fail "the packaged skill file is not found by jade.setup.skill_source()"
"$JADE" question lint >"$WORK/lint.out" 2>&1 || fail "jade question lint failed: $(head -n 3 "$WORK/lint.out")"
"$UV_TOOL_BIN_DIR/jev" --help >/dev/null 2>&1 || fail "jev --help failed"
step "shipped data ok"

if [ -n "${VERIFY_INSTALL_KEEP_DIST:-}" ]; then
    cp "$WORK"/dist/*.whl "$WORK"/dist/*.tar.gz "$VERIFY_INSTALL_KEEP_DIST"/
fi
step "ok"
