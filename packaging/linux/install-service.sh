#!/usr/bin/env bash
# Install eth-validator-stats as a systemd user service.
#
# Usage: ./install-service.sh [--system]
#   (no flag)  install as systemd --user unit (default; no sudo required)
#   --system   install at /etc/systemd/system (sudo required)
#
# The script fills @INSTALL_DIR@ in the template using the repo root
# (resolved relative to this script's location: repo_root = $(dirname $0)/../..).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE="$SCRIPT_DIR/systemd/eth-validator-stats.service.template"
SERVICE_NAME="eth-validator-stats.service"

scope="user"
if [[ "${1:-}" == "--system" ]]; then
    scope="system"
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "error: template not found at $TEMPLATE" >&2
    exit 1
fi

VENV_EXEC="$REPO_ROOT/.venv/bin/eth-validator-stats"
if [[ ! -x "$VENV_EXEC" ]]; then
    echo "error: $VENV_EXEC not found or not executable." >&2
    echo "       run 'uv sync' from the repo root first." >&2
    exit 1
fi

render_unit() {
    sed "s|@INSTALL_DIR@|$REPO_ROOT|g" "$TEMPLATE"
}

if [[ "$scope" == "user" ]]; then
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$unit_dir"
    render_unit > "$unit_dir/$SERVICE_NAME"
    echo "wrote $unit_dir/$SERVICE_NAME"

    # Try to enable linger so the service runs after logout. Polkit on
    # some distros blocks non-root linger; fall back to a printed hint.
    if ! loginctl enable-linger "$USER" 2>/dev/null; then
        echo
        echo "note: could not enable linger automatically."
        echo "      Run this manually so the service survives logout:"
        echo "        sudo loginctl enable-linger \"$USER\""
        echo
    fi

    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE_NAME"
    echo
    echo "installed as systemd --user unit. verify with:"
    echo "  systemctl --user status $SERVICE_NAME"
    echo "  journalctl --user -u $SERVICE_NAME -f"
else
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "error: --system requires sudo." >&2
        exit 1
    fi
    unit_dir="/etc/systemd/system"
    render_unit \
      | sed "/^\[Service\]/a User=${SUDO_USER:-root}" \
      > "$unit_dir/$SERVICE_NAME"
    echo "wrote $unit_dir/$SERVICE_NAME"
    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"
    echo
    echo "installed as systemd system unit. verify with:"
    echo "  systemctl status $SERVICE_NAME"
    echo "  journalctl -u $SERVICE_NAME -f"
fi
