#!/usr/bin/env bash
# Build the eth-validator-stats .deb package.
#
# Output: dist/eth-validator-stats_<version>_all.deb
#
# Requires: debhelper-compat 13, python3.11, python3.11-venv, devscripts.
# Run from the repo root.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEB_DIR="$REPO_ROOT/packaging/deb"
BUILD_DIR="$REPO_ROOT/build/deb"
DIST_DIR="$REPO_ROOT/dist"
VERSION="$(grep -E '^version = ' "$REPO_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"

echo ">>> building eth-validator-stats $VERSION .deb"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# Stage source tree under build/deb/eth-validator-stats-<version>/
SRC_STAGING="$BUILD_DIR/eth-validator-stats-$VERSION"
mkdir -p "$SRC_STAGING"
cp -r "$REPO_ROOT/src" "$REPO_ROOT/pyproject.toml" "$REPO_ROOT/README.md" \
      "$REPO_ROOT/LICENSE" "$SRC_STAGING/"
cp -r "$DEB_DIR/debian" "$SRC_STAGING/"

# All staging happens inside debian/rules' override_dh_auto_install target.
cd "$SRC_STAGING"
dpkg-buildpackage -us -uc -b

# debian/control declares `Architecture: any` (the package bundles an
# arch-specific Python via python-build-standalone), so dpkg writes
# eth-validator-stats_<version>-1_<arch>.deb. Glob to catch whichever arch
# the build host produced; the matching dbgsym package gets `.ddeb`
# extension so it's not picked up here.
mv "$BUILD_DIR/eth-validator-stats_${VERSION}-1_"*.deb "$DIST_DIR/"

echo ">>> built $(ls "$DIST_DIR"/eth-validator-stats_${VERSION}-1_*.deb)"
