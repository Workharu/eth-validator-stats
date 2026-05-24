#!/usr/bin/env bash
# Manage the project version across every file that pins it.
#
# Usage:
#   scripts/bump.sh <new-version>   # bump to <new-version>
#   scripts/bump.sh --verify        # check that all pinned files agree
#
# Bump mode:
#   Updates pyproject.toml, packaging/rpm/eth-validator-stats.spec (Version:
#   field), README.md install-snippet filenames, and uv.lock. Also prepends
#   a stub changelog entry to debian/changelog and to the rpm spec's
#   %changelog section. The stub body is `TODO: release notes for <ver>` —
#   edit it to your actual release notes before committing. The script
#   uses your `git config user.name` / `user.email` for the entry author.
#
# Verify mode:
#   Reads the version from each pinned file (pyproject.toml, uv.lock, rpm
#   spec Version:, debian/changelog top entry, README install snippets) and
#   exits 0 if they all agree, 1 with a per-file table otherwise. CI runs
#   this on every push so drift is caught before a tag is cut.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <new-version>     # bump" >&2
    echo "       $0 --verify          # check pinned files agree" >&2
    exit 64
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Read the version declared by each file that pins it. Echoed to stdout
# as "label\tvalue" lines so verify_versions can present them as a table.
read_versions() {
    local PY UVL SPEC DEB README_DEB README_RPM
    PY=$(grep -E '^version = ' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
    UVL=$(grep -A1 '^name = "eth-validator-stats"$' uv.lock \
            | grep -E '^version = ' | head -1 \
            | sed -E 's/version = "([^"]+)"/\1/')
    SPEC=$(grep -E '^Version:' packaging/rpm/eth-validator-stats.spec \
            | head -1 | awk '{print $2}')
    DEB=$(head -1 packaging/deb/debian/changelog \
            | sed -E 's/^eth-validator-stats \(([^)-]+)-[0-9]+\).*/\1/')
    README_DEB=$(grep -oE 'eth-validator-stats_[0-9.]+-1_amd64\.deb' README.md \
            | head -1 \
            | sed -E 's/eth-validator-stats_([0-9.]+)-1_amd64\.deb/\1/')
    README_RPM=$(grep -oE 'eth-validator-stats-[0-9.]+-1\.fc[0-9]+\.x86_64\.rpm' README.md \
            | head -1 \
            | sed -E 's/eth-validator-stats-([0-9.]+)-1\.fc[0-9]+\.x86_64\.rpm/\1/')
    printf "pyproject.toml\t%s\n" "$PY"
    printf "uv.lock\t%s\n" "$UVL"
    printf "rpm spec Version:\t%s\n" "$SPEC"
    printf "deb changelog top entry\t%s\n" "$DEB"
    printf "README .deb snippet\t%s\n" "$README_DEB"
    printf "README .rpm snippet\t%s\n" "$README_RPM"
}

verify_versions() {
    local table
    table=$(read_versions)
    echo "Version references found:"
    while IFS=$'\t' read -r label value; do
        printf "  %-28s %s\n" "$label:" "${value:-<missing>}"
    done <<< "$table"

    # Reference value = pyproject.toml's version.
    local ref drift
    ref=$(awk -F'\t' '$1 == "pyproject.toml" { print $2 }' <<< "$table")
    drift=0
    while IFS=$'\t' read -r label value; do
        if [[ "$value" != "$ref" ]]; then drift=1; fi
    done <<< "$table"

    echo
    if [[ "$drift" -eq 0 && -n "$ref" ]]; then
        echo "OK: all version references agree on $ref"
        return 0
    else
        echo "ERROR: version drift detected. Run \`scripts/bump.sh $ref\` to resync (or pass the actual intended version)." >&2
        return 1
    fi
}

if [[ "$1" == "--verify" ]]; then
    verify_versions
    exit $?
fi

NEW=$1

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

# Resolve author identity for the changelog stub. Falls back to the same
# value the existing changelog entries use so the format stays consistent
# even on a machine where git config user.name is unset.
AUTHOR_NAME=$(git config user.name 2>/dev/null || true)
AUTHOR_EMAIL=$(git config user.email 2>/dev/null || true)
: "${AUTHOR_NAME:=Workharu}"
: "${AUTHOR_EMAIL:=Workharu@users.noreply.github.com}"

DEB_DATE=$(date -u "+%a, %d %b %Y %H:%M:%S +0000")
RPM_DATE=$(date -u "+%a %b %d %Y")

# pyproject.toml — the version line at the [project] level.
sed -i "s/^version = \"$CURRENT\"$/version = \"$NEW\"/" pyproject.toml

# RPM spec Version: field. The %changelog history is appended to below.
sed -i "s/^Version:[[:space:]]*$CURRENT$/Version:        $NEW/" \
    packaging/rpm/eth-validator-stats.spec

# README install snippets. Replace eth-validator-stats_<CURRENT>-1_ for .deb
# and eth-validator-stats-<CURRENT>-1. for .rpm, both arches.
sed -i "s/eth-validator-stats_${CURRENT}-1_/eth-validator-stats_${NEW}-1_/g" README.md
sed -i "s/eth-validator-stats-${CURRENT}-1\\./eth-validator-stats-${NEW}-1./g" README.md

# Prepend a stub entry to debian/changelog. The TODO body is a placeholder —
# edit it before committing. Trailing newlines on the stub are deliberate
# so the previous entry is properly separated.
DEB_STUB="eth-validator-stats ($NEW-1) unstable; urgency=medium

  * TODO: release notes for $NEW (edit me before committing)

 -- $AUTHOR_NAME <$AUTHOR_EMAIL>  $DEB_DATE
"
{ printf '%s\n' "$DEB_STUB"; cat packaging/deb/debian/changelog; } \
    > packaging/deb/debian/changelog.tmp
mv packaging/deb/debian/changelog.tmp packaging/deb/debian/changelog

# Prepend a stub entry to the rpm spec %changelog section. Insert directly
# under the "%changelog" line, before the existing top entry.
RPM_STUB="* $RPM_DATE $AUTHOR_NAME <$AUTHOR_EMAIL> - $NEW-1
- TODO: release notes for $NEW (edit me before committing)
"
awk -v stub="$RPM_STUB" '
    /^%changelog$/ { print; print stub; next }
    { print }
' packaging/rpm/eth-validator-stats.spec > packaging/rpm/eth-validator-stats.spec.tmp
mv packaging/rpm/eth-validator-stats.spec.tmp packaging/rpm/eth-validator-stats.spec

# Refresh the lockfile so its project-version line matches the new bump.
uv sync >/dev/null

# Confirm no $CURRENT references slipped past the regex anchors. The
# existing %changelog / debian/changelog history entries will legitimately
# still reference $CURRENT — that's the previous-release entry — so
# filter them out of the leftover scan.
LEFTOVERS=$(git grep -n -F "$CURRENT" -- \
    pyproject.toml \
    packaging/rpm/eth-validator-stats.spec \
    packaging/deb/debian/changelog \
    README.md \
    uv.lock 2>/dev/null \
    | grep -vE "(changelog|\.spec).*${CURRENT}-1" \
    || true)

cat <<EOF

>>> bumped to $NEW. Files modified:
$(git diff --stat --no-color pyproject.toml \
    packaging/rpm/eth-validator-stats.spec \
    packaging/deb/debian/changelog \
    README.md uv.lock | sed 's/^/    /')

EOF

if [[ -n "$LEFTOVERS" ]]; then
    echo "WARNING: $CURRENT still appears in some files — check manually:"
    echo "$LEFTOVERS" | sed 's/^/    /'
    echo
fi

cat <<EOF
Stub changelog entries were prepended in:
  packaging/deb/debian/changelog
  packaging/rpm/eth-validator-stats.spec  (%changelog section)

The body of each stub is "TODO: release notes for $NEW" — edit both to
your actual release notes before committing. Then:

  ./scripts/bump.sh --verify
  git add -p
  git commit -m "chore(release): bump to $NEW"
  git tag -a v$NEW -m "v$NEW"
  git push origin main && git push origin v$NEW
EOF
