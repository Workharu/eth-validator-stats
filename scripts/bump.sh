#!/usr/bin/env bash
# Bump the project version across every file that pins it.
#
# Usage:   scripts/bump.sh <new-version>
# Example: scripts/bump.sh 0.3.2
#
# Updates: pyproject.toml, packaging/rpm/eth-validator-stats.spec (Version:
#          field only), README.md (install-snippet filenames), uv.lock.
# Skips:   the %changelog section in the rpm spec and debian/changelog —
#          those need a human-written release notes entry. The script
#          reminds you afterwards.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <new-version>" >&2
    echo "example: $0 0.3.2" >&2
    exit 64
fi

NEW=$1
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Semver-ish: MAJOR.MINOR.PATCH, digits only.
if ! [[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: bad version format: $NEW (expected X.Y.Z, digits only)" >&2
    exit 64
fi

CURRENT=$(grep -E '^version = ' pyproject.toml | head -n1 | sed -E 's/.*"([^"]+)".*/\1/')
if [[ -z "$CURRENT" ]]; then
    echo "ERROR: could not read current version from pyproject.toml" >&2
    exit 1
fi

if [[ "$CURRENT" == "$NEW" ]]; then
    echo "already at $NEW; nothing to do"
    exit 0
fi

echo ">>> bumping $CURRENT -> $NEW"

# Refuse to clobber uncommitted changes in the version-bearing files.
DIRTY=$(git status --porcelain -- \
    pyproject.toml \
    packaging/rpm/eth-validator-stats.spec \
    packaging/deb/debian/changelog \
    README.md \
    uv.lock 2>/dev/null || true)
if [[ -n "$DIRTY" ]]; then
    echo "ERROR: refusing to bump — these tracked files have uncommitted changes:" >&2
    echo "$DIRTY" >&2
    echo "Commit or stash them first." >&2
    exit 1
fi

# pyproject.toml — the version line at the [project] level.
sed -i "s/^version = \"$CURRENT\"$/version = \"$NEW\"/" pyproject.toml

# RPM spec Version: field. The %changelog history is intentionally left
# alone — that's a release-notes entry the human writes.
sed -i "s/^Version:[[:space:]]*$CURRENT$/Version:        $NEW/" \
    packaging/rpm/eth-validator-stats.spec

# README install snippets. Replace eth-validator-stats_<CURRENT>-1_ for .deb
# and eth-validator-stats-<CURRENT>-1. for .rpm, both arches.
sed -i "s/eth-validator-stats_${CURRENT}-1_/eth-validator-stats_${NEW}-1_/g" README.md
sed -i "s/eth-validator-stats-${CURRENT}-1\\./eth-validator-stats-${NEW}-1./g" README.md

# Refresh the lockfile so its project-version line matches the new bump.
uv sync >/dev/null

# Confirm no $CURRENT references slipped past the regex anchors.
LEFTOVERS=$(git grep -n -F "$CURRENT" -- \
    pyproject.toml \
    packaging/rpm/eth-validator-stats.spec \
    README.md \
    uv.lock 2>/dev/null || true)

cat <<EOF

>>> bumped to $NEW. Files modified:
$(git diff --stat --no-color pyproject.toml \
    packaging/rpm/eth-validator-stats.spec \
    README.md uv.lock | sed 's/^/    /')

EOF

if [[ -n "$LEFTOVERS" ]]; then
    echo "WARNING: $CURRENT still appears in some files — check manually:"
    echo "$LEFTOVERS" | sed 's/^/    /'
    echo
fi

cat <<EOF
Still TODO (release-notes work, by hand):
  packaging/deb/debian/changelog            — prepend an entry for $NEW-1
  packaging/rpm/eth-validator-stats.spec    — prepend a %changelog entry for $NEW-1

After writing the changelog entries:
  git add -p && git commit -m "chore(release): bump to $NEW"
  git tag -a v$NEW -m "v$NEW"
  git push origin main && git push origin v$NEW
EOF
