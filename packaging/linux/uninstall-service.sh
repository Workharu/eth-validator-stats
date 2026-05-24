#!/usr/bin/env bash
# Uninstall the eth-validator-stats systemd unit.
#
# Usage: ./uninstall-service.sh [--system]
#
# Tries the matching scope. Does not modify linger state.

set -euo pipefail

SERVICE_NAME="eth-validator-stats.service"

scope="user"
if [[ "${1:-}" == "--system" ]]; then
    scope="system"
fi

if [[ "$scope" == "user" ]]; then
    unit_file="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE_NAME"
    systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$unit_file"
    systemctl --user daemon-reload
    echo "removed $unit_file"
else
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "error: --system requires sudo." >&2
        exit 1
    fi
    unit_file="/etc/systemd/system/$SERVICE_NAME"
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$unit_file"
    systemctl daemon-reload
    echo "removed $unit_file"
fi
