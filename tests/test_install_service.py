"""Tests for the install-service subcommand and helpers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from eth_validator_stats import _install_service as svc


def test_is_package_owned_unit_returns_false_for_nonexistent_path(tmp_path: Path):
    assert svc.is_package_owned_unit(tmp_path / "does-not-exist.service") is False


def test_is_package_owned_unit_returns_true_when_dpkg_query_finds_owner(
    tmp_path: Path, monkeypatch
):
    unit = tmp_path / "fake.service"
    unit.write_text("[Unit]\n")

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "dpkg-query":
            return subprocess.CompletedProcess(cmd, 0, stdout="some-pkg: " + str(unit), stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    assert svc.is_package_owned_unit(unit) is True


def test_is_package_owned_unit_returns_true_when_rpm_qf_finds_owner(
    tmp_path: Path, monkeypatch
):
    unit = tmp_path / "fake.service"
    unit.write_text("[Unit]\n")

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "dpkg-query":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
        if cmd[0] == "rpm":
            return subprocess.CompletedProcess(cmd, 0, stdout="some-pkg-0.1-1", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    assert svc.is_package_owned_unit(unit) is True


def test_is_package_owned_unit_returns_false_when_no_pkg_owns_path(
    tmp_path: Path, monkeypatch
):
    unit = tmp_path / "fake.service"
    unit.write_text("[Unit]\n")

    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    assert svc.is_package_owned_unit(unit) is False


def test_resolve_binary_path_returns_resolved_argv0(tmp_path: Path, monkeypatch):
    fake_bin = tmp_path / "eth-validator-stats"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(fake_bin), "install-service"])
    assert svc._resolve_binary_path() == fake_bin.resolve(strict=True)


def _stub_pwd_grp(monkeypatch, username: str = "alice", uid: int = 1001, gid: int = 1001, group: str = "alice"):
    """Stub pwd.getpwnam(username) and grp.getgrgid(gid) for tests."""
    import pwd, grp

    class _Pw:
        pw_name = username
        pw_uid = uid
        pw_gid = gid

    class _Gr:
        gr_name = group
        gr_gid = gid

    def fake_getpwnam(name: str):
        if name == username:
            return _Pw()
        raise KeyError(name)

    def fake_getgrgid(gid_arg: int):
        if gid_arg == gid:
            return _Gr()
        raise KeyError(gid_arg)

    monkeypatch.setattr(pwd, "getpwnam", fake_getpwnam)
    monkeypatch.setattr(grp, "getgrgid", fake_getgrgid)


def test_install_service_system_refuses_without_sudo(monkeypatch, capsys):
    monkeypatch.setattr(svc.os, "geteuid", lambda: 1000)
    rc = svc.install_service_system(run_as=None, force=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "sudo" in err.lower() or "--user" in err


def test_install_service_system_refuses_if_package_owned_unit_exists(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(svc.os, "geteuid", lambda: 0)
    fake_unit = tmp_path / svc.SERVICE_NAME
    fake_unit.write_text("[Unit]\n")
    monkeypatch.setattr(svc, "SYSTEM_UNIT_PATH", fake_unit)
    monkeypatch.setattr(svc, "is_package_owned_unit", lambda p: True)
    rc = svc.install_service_system(run_as=None, force=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "apt" in err.lower() or "dnf" in err.lower() or "package" in err.lower()


def test_install_service_system_requires_run_as_or_sudo_user(monkeypatch, capsys):
    monkeypatch.setattr(svc.os, "geteuid", lambda: 0)
    monkeypatch.setattr(svc, "is_package_owned_unit", lambda p: False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    rc = svc.install_service_system(run_as=None, force=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "--run-as" in err or "user" in err.lower()


def test_install_service_system_writes_unit_with_resolved_bin_and_sudo_user(
    monkeypatch, tmp_path
):
    # Filesystem setup.
    fake_bin = tmp_path / "eth-validator-stats"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    fake_unit = tmp_path / svc.SERVICE_NAME
    fake_config = tmp_path / "etc" / "eth-validator-stats"
    fake_state = tmp_path / "var" / "lib" / "eth-validator-stats"

    # Patch module-level constants.
    monkeypatch.setattr(svc, "SYSTEM_UNIT_PATH", fake_unit)
    monkeypatch.setattr(svc, "SYSTEM_CONFIG_DIR", fake_config)
    monkeypatch.setattr(svc, "SYSTEM_STATE_DIR", fake_state)

    # Stub system calls.
    monkeypatch.setattr(svc.os, "geteuid", lambda: 0)
    monkeypatch.setattr(svc, "is_package_owned_unit", lambda p: False)
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(sys, "argv", [str(fake_bin), "install-service"])
    _stub_pwd_grp(monkeypatch)

    chown_calls: list = []
    chmod_calls: list = []
    monkeypatch.setattr(svc.shutil, "chown", lambda p, user, group: chown_calls.append((p, user, group)))
    monkeypatch.setattr(svc.os, "chmod", lambda p, mode: chmod_calls.append((p, mode)))

    systemctl_calls: list = []
    def fake_run(cmd, *args, **kwargs):
        systemctl_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(svc.subprocess, "run", fake_run)

    rc = svc.install_service_system(run_as=None, force=False)

    assert rc == 0
    written = fake_unit.read_text()
    assert "User=alice" in written
    assert "Group=alice" in written
    assert f"ExecStart={fake_bin.resolve()} watch" in written
    assert "ETH_VALIDATOR_STATS_CONFIG=/etc/eth-validator-stats/config.yml" in written

    # Dirs created + chowned + chmoded.
    assert fake_config.exists()
    assert fake_state.exists()
    assert (fake_config, "alice", "alice") in chown_calls
    assert (fake_state, "alice", "alice") in chown_calls
    assert (fake_config, 0o750) in chmod_calls

    # systemctl daemon-reload + enable were called.
    cmd_strs = [" ".join(c) for c in systemctl_calls]
    assert any("daemon-reload" in s for s in cmd_strs)
    assert any("enable" in s and "eth-validator-stats" in s for s in cmd_strs)


def test_install_service_system_run_as_overrides_sudo_user(monkeypatch, tmp_path):
    fake_bin = tmp_path / "eth-validator-stats"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    fake_unit = tmp_path / svc.SERVICE_NAME
    monkeypatch.setattr(svc, "SYSTEM_UNIT_PATH", fake_unit)
    monkeypatch.setattr(svc, "SYSTEM_CONFIG_DIR", tmp_path / "etc")
    monkeypatch.setattr(svc, "SYSTEM_STATE_DIR", tmp_path / "var")
    monkeypatch.setattr(svc.os, "geteuid", lambda: 0)
    monkeypatch.setattr(svc, "is_package_owned_unit", lambda p: False)
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(sys, "argv", [str(fake_bin), "install-service"])
    _stub_pwd_grp(monkeypatch, username="bob", uid=1002, gid=1002, group="bob")
    monkeypatch.setattr(svc.shutil, "chown", lambda *a, **k: None)
    monkeypatch.setattr(svc.os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0))

    rc = svc.install_service_system(run_as="bob", force=False)
    assert rc == 0
    written = fake_unit.read_text()
    assert "User=bob" in written
    assert "User=alice" not in written


def test_install_service_user_refuses_with_sudo(monkeypatch, capsys):
    monkeypatch.setattr(svc.os, "geteuid", lambda: 0)
    rc = svc.install_service_user(force=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "--user" in err or "sudo" in err.lower()


def test_install_service_user_writes_unit_to_xdg_path(monkeypatch, tmp_path):
    fake_bin = tmp_path / "eth-validator-stats"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(fake_bin), "install-service", "--user"])
    monkeypatch.setattr(svc.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("USER", "alice")

    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(svc.subprocess, "run", fake_run)

    rc = svc.install_service_user(force=False)
    assert rc == 0

    unit_path = tmp_path / "config" / "systemd" / "user" / svc.SERVICE_NAME
    assert unit_path.exists()
    content = unit_path.read_text()
    assert "User=" not in content   # user-scope unit must NOT set User=
    assert "Group=" not in content
    assert f"ExecStart={fake_bin.resolve()} watch" in content


def test_install_service_user_refuses_if_unit_exists_without_force(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(svc.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    unit_path = tmp_path / "config" / "systemd" / "user" / svc.SERVICE_NAME
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text("[Unit]\nexisting\n")

    rc = svc.install_service_user(force=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "--force" in err
