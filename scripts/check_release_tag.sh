#!/bin/sh
# The release rules that the release workflow enforces, run on the tagged commit before anything is built or published.
#
#   sh scripts/check_release_tag.sh vX.Y.Z
#
# Run it in a checkout that has the full history and a remote branch origin/main (the workflow uses fetch-depth 0 and fetches main).
# It fails (exit 1, one line on stderr) unless:
#   1. the argument is a version tag (vX.Y.Z, also a prerelease spelling such as vX.Y.Zrc1);
#   2. the checkout is not shallow, so the ancestry answer in step 3 can be trusted;
#   3. the checked-out commit is an ancestor of origin/main (a tag on a side branch is refused);
#   4. CHANGELOG.md has a section "## [X.Y.Z]" with at least one line of text under it.
# It does not run the tests: the release workflow runs them in the next job. It never writes anything and never reads the network.
set -eu

fail() {
    printf 'check_release_tag: FAIL: %s\n' "$*" >&2
    exit 1
}

tag=${1-}
case "$tag" in
    v[0-9]*) ;;
    *) fail "'$tag' is not a version tag (expected vX.Y.Z)" ;;
esac
# Plain characters only: the version goes into an awk pattern and into messages.
printf '%s\n' "$tag" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*$' || fail "'$tag' is not a version tag (expected vX.Y.Z)"
version=${tag#v}

[ "$(git rev-parse --is-shallow-repository)" = false ] || fail "the checkout is shallow; fetch the full history (fetch-depth: 0)"
git rev-parse --verify --quiet refs/remotes/origin/main > /dev/null || fail "no origin/main in this checkout; fetch it before the check"
git merge-base --is-ancestor HEAD refs/remotes/origin/main || fail "$tag is not on origin/main: the tagged commit is not an ancestor of origin/main (a release is cut only from merged main)"
printf 'check_release_tag: %s is on origin/main\n' "$tag"

[ -f CHANGELOG.md ] || fail "CHANGELOG.md is missing"
grep -Fq "## [$version]" CHANGELOG.md || fail "CHANGELOG.md has no section for $version"
body=$(awk -v v="$version" 'index($0, "## [" v "]") == 1 { on = 1; next } on && /^## \[/ { exit } on { print }' CHANGELOG.md)
[ -n "$(printf '%s' "$body" | tr -d '[:space:]')" ] || fail "the CHANGELOG.md section for $version is empty"
printf 'check_release_tag: CHANGELOG.md has a section for %s\n' "$version"
