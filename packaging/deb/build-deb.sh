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

mv "$BUILD_DIR/eth-validator-stats_${VERSION}-1_all.deb" "$DIST_DIR/"

echo ">>> built $DIST_DIR/eth-validator-stats_${VERSION}-1_all.deb"
