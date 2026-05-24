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

# 0. Clean previous output
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# 1. Stage source tree under build/deb/
SRC_STAGING="$BUILD_DIR/eth-validator-stats-$VERSION"
mkdir -p "$SRC_STAGING"
cp -r "$REPO_ROOT/src" "$REPO_ROOT/pyproject.toml" "$REPO_ROOT/README.md" \
      "$REPO_ROOT/LICENSE" "$SRC_STAGING/"
cp -r "$DEB_DIR/debian" "$SRC_STAGING/"

# 2. Pre-stage the runtime tree under debian/tmp inside the source staging.
TMP_TREE="$SRC_STAGING/debian/tmp"
mkdir -p "$TMP_TREE/opt/eth-validator-stats" \
         "$TMP_TREE/usr/bin" \
         "$TMP_TREE/lib/systemd/system" \
         "$TMP_TREE/etc/eth-validator-stats" \
         "$TMP_TREE/var/lib/eth-validator-stats"

# 2a. Build the bundled venv at the FINAL runtime path so shebangs resolve.
python3.11 -m venv "$TMP_TREE/opt/eth-validator-stats/venv"
"$TMP_TREE/opt/eth-validator-stats/venv/bin/pip" install --no-cache-dir "$SRC_STAGING"

# 2b. Rewrite shebangs that point at TMP_TREE back to /opt/eth-validator-stats.
find "$TMP_TREE/opt/eth-validator-stats/venv/bin" -type f \
    -exec sed -i "s|#!$TMP_TREE/opt/eth-validator-stats|#!/opt/eth-validator-stats|g" {} +

# 2c. Strip __pycache__ to keep the package small.
find "$TMP_TREE/opt/eth-validator-stats/venv" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$TMP_TREE/opt/eth-validator-stats/venv" -name '*.pyc' -delete

# 2d. Symlink /usr/bin/eth-validator-stats → venv entrypoint.
ln -sf /opt/eth-validator-stats/venv/bin/eth-validator-stats \
       "$TMP_TREE/usr/bin/eth-validator-stats"

# 2e. Render the systemd unit by composing Phase 1's template with the system-mode fields.
cat > "$TMP_TREE/lib/systemd/system/eth-validator-stats.service" <<'EOF'
[Unit]
Description=eth-validator-stats watcher
Documentation=https://github.com/Workharu/eth-validator-stats
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/eth-validator-stats/config.yml

[Service]
Type=simple
User=eth-validator-stats
Group=eth-validator-stats
ExecStart=/opt/eth-validator-stats/venv/bin/eth-validator-stats watch
Environment="ETH_VALIDATOR_STATS_CONFIG=/etc/eth-validator-stats/config.yml"
Environment="ETH_VALIDATOR_STATS_STATE=/var/lib/eth-validator-stats/state.json"
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 3. Run debuild
cd "$SRC_STAGING"
dpkg-buildpackage -us -uc -b

# 4. Move .deb to dist/
mv "$BUILD_DIR/eth-validator-stats_${VERSION}-1_all.deb" "$DIST_DIR/"

echo ">>> built $DIST_DIR/eth-validator-stats_${VERSION}-1_all.deb"
