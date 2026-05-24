#!/usr/bin/env bash
# Build the eth-validator-stats .rpm package.
#
# Output: dist/eth-validator-stats-<version>-1.<dist>.noarch.rpm
#
# Requires: rpm-build, python3.11, python3.11-devel, systemd-rpm-macros, tar.
# Run from the repo root, intended for Fedora 40+ build host.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RPM_DIR="$REPO_ROOT/packaging/rpm"
BUILD_DIR="$REPO_ROOT/build/rpm"
DIST_DIR="$REPO_ROOT/dist"
VERSION="$(grep -E '^version = ' "$REPO_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"

echo ">>> building eth-validator-stats $VERSION .rpm"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS} "$DIST_DIR"

# Stage source under SOURCES/eth-validator-stats-<version>/
STAGE="eth-validator-stats-$VERSION"
mkdir -p "$BUILD_DIR/SOURCES/$STAGE"
cp -r "$REPO_ROOT/src" "$REPO_ROOT/pyproject.toml" "$REPO_ROOT/README.md" \
      "$REPO_ROOT/LICENSE" "$BUILD_DIR/SOURCES/$STAGE/"
# uv.lock is not needed at build time but ship it for reference
cp "$REPO_ROOT/uv.lock" "$BUILD_DIR/SOURCES/$STAGE/" || true

# Pack source tarball
(cd "$BUILD_DIR/SOURCES" && tar czf "$STAGE.tar.gz" "$STAGE")
rm -rf "$BUILD_DIR/SOURCES/$STAGE"

# Copy spec
cp "$RPM_DIR/eth-validator-stats.spec" "$BUILD_DIR/SPECS/"

# Build
rpmbuild --define "_topdir $BUILD_DIR" \
         -bb "$BUILD_DIR/SPECS/eth-validator-stats.spec"

# Move resulting .rpm
mv "$BUILD_DIR"/RPMS/noarch/eth-validator-stats-*.rpm "$DIST_DIR/"

echo ">>> built $(ls $DIST_DIR/eth-validator-stats-*.rpm)"
