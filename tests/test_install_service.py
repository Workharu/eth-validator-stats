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
