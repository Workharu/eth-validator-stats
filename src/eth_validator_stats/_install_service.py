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


UNIT_TEMPLATE_SYSTEM = """\
[Unit]
Description=eth-validator-stats watcher
Documentation=https://github.com/Workharu/eth-validator-stats
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
ExecStart={bin} watch
Environment="ETH_VALIDATOR_STATS_CONFIG=/etc/eth-validator-stats/config.yml"
Environment="ETH_VALIDATOR_STATS_STATE=/var/lib/eth-validator-stats/state.json"
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def install_service_system(run_as: str | None, force: bool) -> int:
    """Register a system-scope systemd unit and prep config/state dirs.

    Returns process exit code (0 on success, 1 on user error).
    """
    if os.geteuid() != 0:
        print(
            "error: install-service needs sudo (system scope). "
            "Pass --user to register a user-scope unit without sudo.",
            file=sys.stderr,
        )
        return 1

    if SYSTEM_UNIT_PATH.exists() and is_package_owned_unit(SYSTEM_UNIT_PATH) and not force:
        print(
            "error: an eth-validator-stats unit installed by the distro package "
            "is already present. Either: (a) remove the package first "
            "(apt purge / dnf remove), then re-run; or (b) re-run with --force "
            "to overwrite it (not recommended).",
            file=sys.stderr,
        )
        return 1

    bin_path = _resolve_binary_path()

    username = run_as or os.environ.get("SUDO_USER")
    if not username:
        print(
            "error: cannot determine the user to run the service as. "
            "Pass --run-as <username>.",
            file=sys.stderr,
        )
        return 1

    import pwd, grp
    try:
        pwent = pwd.getpwnam(username)
    except KeyError:
        print(f"error: user {username!r} does not exist.", file=sys.stderr)
        return 1
    groupname = grp.getgrgid(pwent.pw_gid).gr_name

    unit_content = UNIT_TEMPLATE_SYSTEM.format(
        user=username, group=groupname, bin=bin_path,
    )
    SYSTEM_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYSTEM_UNIT_PATH.write_text(unit_content)
    os.chmod(SYSTEM_UNIT_PATH, 0o644)

    for d in (SYSTEM_CONFIG_DIR, SYSTEM_STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)
        shutil.chown(d, user=username, group=groupname)
        os.chmod(d, 0o750)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", SERVICE_NAME], check=True)

    print(
        "\nInstalled.\n\n"
        "Next:\n"
        "  sudo eth-validator-stats init --system\n"
        "  sudo systemctl start eth-validator-stats\n\n"
        "Logs:   journalctl -u eth-validator-stats -f\n"
        "Status: systemctl status eth-validator-stats\n"
    )
    return 0
