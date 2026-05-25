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

    import grp
    import pwd
    import shlex
    try:
        pwent = pwd.getpwnam(username)
    except KeyError:
        print(f"error: user {username!r} does not exist.", file=sys.stderr)
        return 1
    groupname = grp.getgrgid(pwent.pw_gid).gr_name

    # Shell-quote bin_path so systemd parses ExecStart correctly even if
    # the binary path ever contains spaces (e.g. pipx install into a
    # path with spaces, exotic /opt layouts). Defensive — typical paths
    # are space-free.
    unit_content = UNIT_TEMPLATE_SYSTEM.format(
        user=username, group=groupname, bin=shlex.quote(str(bin_path)),
    )
    SYSTEM_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYSTEM_UNIT_PATH.write_text(unit_content, encoding="utf-8")
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


UNIT_TEMPLATE_USER = """\
[Unit]
Description=eth-validator-stats watcher
Documentation=https://github.com/Workharu/eth-validator-stats
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={bin} watch
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def install_service_user(force: bool) -> int:
    """Register a user-scope systemd unit at $XDG_CONFIG_HOME/systemd/user/.

    Returns process exit code (0 on success, 1 on user error).
    """
    if os.geteuid() == 0:
        print(
            "error: --user is meant for non-root invocation. "
            "Drop sudo, or omit --user for system scope.",
            file=sys.stderr,
        )
        return 1

    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    unit_dir = xdg / "systemd" / "user"
    unit_path = unit_dir / SERVICE_NAME

    if unit_path.exists() and not force:
        print(
            f"error: {unit_path} already exists. Re-run with --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    import shlex
    bin_path = _resolve_binary_path()
    # Shell-quote in case the binary path contains spaces — same
    # reasoning as the system-scope branch above.
    unit_content = UNIT_TEMPLATE_USER.format(bin=shlex.quote(str(bin_path)))

    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_content, encoding="utf-8")
    os.chmod(unit_path, 0o644)

    username = os.environ.get("USER", "")
    linger = subprocess.run(
        ["loginctl", "enable-linger", username] if username else ["loginctl", "enable-linger"],
        capture_output=True, text=True, check=False,
    )
    if linger.returncode != 0 and username:
        print(
            f"note: could not enable linger automatically. Run this manually "
            f"so the service survives logout: "
            f"sudo loginctl enable-linger {username}"
        )

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=True)

    print(
        f"\nInstalled at {unit_path}.\n\n"
        f"Next:\n"
        f"  eth-validator-stats init    # per-user config\n\n"
        f"Logs:   journalctl --user -u eth-validator-stats -f\n"
        f"Status: systemctl --user status eth-validator-stats\n"
    )
    return 0


def uninstall_service_system(purge: bool) -> int:
    """Remove the system-scope systemd unit and (optionally) config/state dirs."""
    if os.geteuid() != 0:
        print("error: uninstall-service needs sudo (system scope).", file=sys.stderr)
        return 1

    if SYSTEM_UNIT_PATH.exists() and is_package_owned_unit(SYSTEM_UNIT_PATH):
        print(
            "error: this unit is owned by a distro package. Use apt purge / "
            "dnf remove to uninstall.",
            file=sys.stderr,
        )
        return 1

    subprocess.run(
        ["systemctl", "disable", "--now", SERVICE_NAME],
        capture_output=True, text=True, check=False,
    )
    if SYSTEM_UNIT_PATH.exists():
        SYSTEM_UNIT_PATH.unlink()
    subprocess.run(["systemctl", "daemon-reload"], check=True)

    if purge:
        if SYSTEM_CONFIG_DIR.exists():
            shutil.rmtree(SYSTEM_CONFIG_DIR)
        if SYSTEM_STATE_DIR.exists():
            shutil.rmtree(SYSTEM_STATE_DIR)
        print("Uninstalled + purged config and state.")
    else:
        print("Uninstalled. Config and state preserved.")
    return 0


def uninstall_service_user(purge: bool) -> int:
    """Remove the user-scope systemd unit and (optionally) user config/state."""
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    unit_path = xdg / "systemd" / "user" / SERVICE_NAME

    subprocess.run(
        ["systemctl", "--user", "disable", "--now", SERVICE_NAME],
        capture_output=True, text=True, check=False,
    )
    if unit_path.exists():
        unit_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)

    if purge:
        import platformdirs
        cfg = platformdirs.user_config_path("eth-validator-stats")
        state = platformdirs.user_data_path("eth-validator-stats")
        if cfg.exists():
            shutil.rmtree(cfg)
        if state.exists():
            shutil.rmtree(state)
        print("Uninstalled + purged config and state.")
    else:
        print("Uninstalled. Config and state preserved.")
    return 0


def cmd_install_service(args) -> int:
    if args.user:
        return install_service_user(force=args.force)
    return install_service_system(run_as=args.run_as, force=args.force)


def cmd_uninstall_service(args) -> int:
    if args.user:
        return uninstall_service_user(purge=args.purge)
    return uninstall_service_system(purge=args.purge)
