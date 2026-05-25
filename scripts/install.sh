#!/usr/bin/env bash
# eth-validator-stats one-liner installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Workharu/eth-validator-stats/main/scripts/install.sh | sudo bash
#
# What it does:
#   1. Detects your OS (Debian/Ubuntu, Fedora/RHEL family, macOS) and
#      architecture (amd64/arm64).
#   2. Hits the GitHub Releases API for the latest tagged release.
#   3. Picks the matching .deb / .rpm asset (or falls back to PyPI via
#      pipx on macOS or hosts without apt/dnf).
#   4. Installs it.
#   5. Tells you the next command to run.
#
# Safe to re-run: apt/dnf treat the install as an upgrade if a newer
# version is already there; pipx is forced to overwrite.

set -euo pipefail

REPO="Workharu/eth-validator-stats"
API="https://api.github.com/repos/${REPO}/releases/latest"

err()  { echo "install: $*" >&2; exit 1; }
info() { echo ">>> $*"; }

# --- 1. Platform detection ---------------------------------------------------

uname_s=$(uname -s)
uname_m=$(uname -m)

case "$uname_m" in
    x86_64|amd64)   ARCH_DEB=amd64;  ARCH_RPM=x86_64  ;;
    aarch64|arm64)  ARCH_DEB=arm64;  ARCH_RPM=aarch64 ;;
    *) err "unsupported architecture: $uname_m" ;;
esac

if [ "$uname_s" = "Linux" ]; then
    if command -v apt-get >/dev/null 2>&1; then
        FLAVOR=deb
    elif command -v dnf >/dev/null 2>&1; then
        FLAVOR=rpm
    elif command -v pipx >/dev/null 2>&1; then
        FLAVOR=pipx
    else
        err "no supported installer found (apt, dnf, or pipx). Install pipx then re-run."
    fi
elif [ "$uname_s" = "Darwin" ]; then
    command -v pipx >/dev/null 2>&1 \
        || err "macOS install requires pipx — install it first: brew install pipx"
    FLAVOR=pipx
else
    err "unsupported OS: $uname_s (Linux and macOS only for now)"
fi

info "detected: $uname_s / $uname_m   installer: $FLAVOR"

# --- 2. Sudo check for .deb / .rpm ------------------------------------------

if [ "$FLAVOR" != "pipx" ] && [ "$(id -u)" != "0" ]; then
    err "the .${FLAVOR} install path needs root. Re-run as: curl ... | sudo bash"
fi

# --- 3. Pipx path: just hit PyPI --------------------------------------------

if [ "$FLAVOR" = "pipx" ]; then
    info "installing via pipx from PyPI"
    pipx install --force eth-validator-stats
    cat <<'EOF'

Done. Next:
  eth-validator-stats init           # per-user config at ~/.config/eth-validator-stats/
  eth-validator-stats status         # see the validators table

To run as a systemd service (Linux only):
  sudo eth-validator-stats install-service
  sudo eth-validator-stats init --system
EOF
    exit 0
fi

# --- 4. Deb / Rpm path: fetch latest release, find matching asset ----------

command -v curl >/dev/null 2>&1 || err "curl is required"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

info "fetching latest release metadata"
JSON=$(curl -fsSL "$API")

# Parse fields out of the JSON without jq (which we don't require).
TAG=$(printf '%s' "$JSON" \
      | grep -oE '"tag_name":[[:space:]]*"[^"]+"' \
      | head -1 \
      | sed -E 's/.*"([^"]+)".*/\1/')
[ -n "$TAG" ] || err "could not read tag_name from $API"
info "latest release: $TAG"

case "$FLAVOR" in
    deb) ASSET_RE="eth-validator-stats_[^\"]+_${ARCH_DEB}\\.deb" ;;
    rpm) ASSET_RE="eth-validator-stats-[^\"]+\\.${ARCH_RPM}\\.rpm" ;;
esac

URL=$(printf '%s' "$JSON" \
      | grep -oE '"browser_download_url":[[:space:]]*"[^"]+"' \
      | sed -E 's/.*"([^"]+)".*/\1/' \
      | grep -E "$ASSET_RE" \
      | head -1)
[ -n "$URL" ] || err "no $FLAVOR asset matching ${ARCH_DEB}${ARCH_RPM} found in release $TAG. The asset may not have built; see https://github.com/${REPO}/releases/$TAG"

PKG="$TMP/$(basename "$URL")"
info "downloading $URL"
curl -fsSL -o "$PKG" "$URL"

# --- 5. Install --------------------------------------------------------------

case "$FLAVOR" in
    deb)
        info "installing via apt"
        apt-get update -qq
        apt-get install -y "$PKG"
        ;;
    rpm)
        info "installing via dnf"
        dnf install -y "$PKG"
        ;;
esac

cat <<EOF

Installed eth-validator-stats ${TAG}.

Next step:
  sudo eth-validator-stats init           # writes /etc/eth-validator-stats/config.yml
                                          # AND starts the systemd service

Then check it's running:
  sudo systemctl status eth-validator-stats
EOF
