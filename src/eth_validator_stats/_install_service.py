"""Linux-only install-service / uninstall-service implementation.

This module is imported lazily by `cli.py` only when the install-service or
uninstall-service subcommand is invoked, so non-Linux users (or users running
`--help`) don't pay the cost of importing pwd/grp.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = "eth-validator-stats.service"
SYSTEM_UNIT_PATH = Path("/etc/systemd/system") / SERVICE_NAME
SYSTEM_CONFIG_DIR = Path("/etc/eth-validator-stats")
SYSTEM_STATE_DIR = Path("/var/lib/eth-validator-stats")


def is_package_owned_unit(path: Path) -> bool:
    """Return True if `path` is owned by an installed apt or dnf package."""
    if not path.exists():
        return False
    r = subprocess.run(
        ["dpkg-query", "-S", str(path)],
        capture_output=True, text=True, check=False,
    )
    if r.returncode == 0:
        return True
    r = subprocess.run(
        ["rpm", "-qf", str(path)],
        capture_output=True, text=True, check=False,
    )
    return r.returncode == 0


def _resolve_binary_path() -> Path:
    """Return the absolute, symlink-resolved path of the running entrypoint."""
    return Path(sys.argv[0]).resolve(strict=True)
