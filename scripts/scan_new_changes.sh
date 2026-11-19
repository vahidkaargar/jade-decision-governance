#!/bin/sh
# Scan the commits that are new in a push or a pull request for secrets, with the repository's own .gitleaks.toml (V6-34).
#
#   sh scripts/scan_new_changes.sh BASE_SHA [HEAD_REF]
#
# BASE_SHA is the commit before the change (the pull request base, or the "before" sha of a push). HEAD_REF is what to scan up to (default HEAD).
# Only the commits in BASE_SHA..HEAD_REF are read, not the whole history. If BASE_SHA is empty, all zeros, or not in this checkout (a new branch, a
# force push), the last 20 commits up to HEAD_REF are read instead. Needs the `gitleaks` program (8.19 or newer) and enough history (fetch-depth: 0).
# .gitleaks.toml allows fake credentials under tests/ and evals/ on purpose; a secret anywhere else fails the scan (exit 1, the finding is printed with the secret redacted).
# One full-history scan with no allow-list is a separate, owner-run step (docs/14-v6 section 8); this script is not that.
set -eu

fail() {
    printf 'scan_new_changes: FAIL: %s\n' "$*" >&2
    exit "${2:-2}"
}

command -v gitleaks > /dev/null 2>&1 || fail "gitleaks is not installed" 2
base=${1-}
head=${2:-HEAD}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
git rev-parse --verify --quiet "$head^{commit}" > /dev/null || fail "no such commit: $head" 2

case "$base" in
    "" | 0000000000000000000000000000000000000000) range="-n 20 $head" ;;
    *)
        if git cat-file -e "$base^{commit}" 2> /dev/null; then
            range="$base..$head"
        else
            range="-n 20 $head"
        fi
        ;;
esac
printf 'scan_new_changes: scanning %s\n' "$range"
# The range is words made of a sha, dots, -n and a number or a ref name, so it is passed through one variable and split on purpose.
# shellcheck disable=SC2086
gitleaks git --config "$ROOT/.gitleaks.toml" --log-opts="$range" --redact --no-banner --exit-code 1 --verbose "$PWD"
