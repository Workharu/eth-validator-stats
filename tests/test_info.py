from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from eth_validator_stats import cli


def make_handler(*, support_liveness: bool = True, version: str = "TestClient/v1.0"):
    """A minimal beacon-API mock covering everything `info` probes."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/eth/v1/node/version":
            return httpx.Response(200, json={"data": {"version": version}})
        if path == "/eth/v1/beacon/genesis":
            return httpx.Response(200, json={"data": {"genesis_time": "1606824023"}})
        if path == "/eth/v1/config/spec":
            return httpx.Response(200, json={"data": {"SECONDS_PER_SLOT": "12", "SLOTS_PER_EPOCH": "32"}})
        if path == "/eth/v1/beacon/headers/head":
            return httpx.Response(200, json={"data": {"header": {"message": {"slot": "1024"}}}})
        if path == "/eth/v1/beacon/states/head/validators":
            return httpx.Response(
                200,
                json={"data": [{"index": "1", "balance": "32000000000", "status": "active_ongoing", "validator": {"pubkey": "0x01"}}]},
            )
        if path.startswith("/eth/v1/validator/liveness/"):
            if not support_liveness:
                return httpx.Response(404)
            return httpx.Response(200, json={"data": [{"index": "1", "is_live": True}]})
        return httpx.Response(404)

    return handler


@pytest.fixture
def temp_config(tmp_path: Path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'beacon_node_url = "http://test-node"\n'
        '[[validators]]\n'
        'index = 1\n'
        'label = "v1"\n'
    )
    state_path = tmp_path / "state.json"
    env = {
        "ETH_VALIDATOR_STATS_CONFIG": str(cfg_path),
        "ETH_VALIDATOR_STATS_STATE": str(state_path),
    }
    # Clear inherited overrides that would change URL or auth
    for k in ("BEACON_NODE_URL", "BEACON_NODE_AUTH_TOKEN"):
        if k in os.environ:
            env[k] = ""
    with patch.dict(os.environ, env, clear=False):
        for k in ("BEACON_NODE_URL", "BEACON_NODE_AUTH_TOKEN"):
            if k in os.environ and not env.get(k):
                del os.environ[k]
        yield cfg_path, state_path


def _patch_client(handler):
    """Patch BeaconClient so it routes through the mock transport."""
    original = cli.BeaconClient

    def factory(base_url, *, auth_token=None, **kwargs):
        return original(base_url, auth_token=auth_token, transport=httpx.MockTransport(handler), **kwargs)

    return patch.object(cli, "BeaconClient", factory)


def test_info_all_endpoints_ok(temp_config, capsys):
    with _patch_client(make_handler(support_liveness=True, version="Prysm/v4.2.1")):
        rc = cli.main(["info"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Prysm/v4.2.1" in out
    assert "/eth/v1/node/version" in out
    assert "OK" in out
    # Note: rich color codes may insert characters; check key substrings, not full lines
    assert "liveness" in out


def test_info_reports_unsupported_liveness(temp_config, capsys):
    with _patch_client(make_handler(support_liveness=False, version="OldClient/v0.1")):
        rc = cli.main(["info"])
    out = capsys.readouterr().out
    # Non-zero because at least one probe didn't return OK
    assert rc == 1
    assert "UNSUPPORTED" in out
    assert "OldClient/v0.1" in out


def test_info_auth_status_displayed(tmp_path, capsys):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'beacon_node_url = "http://test-node"\n'
        'beacon_auth_token = "supersecret"\n'
        '[[validators]]\n'
        'index = 1\n'
    )
    state_path = tmp_path / "state.json"
    with patch.dict(
        os.environ,
        {
            "ETH_VALIDATOR_STATS_CONFIG": str(cfg_path),
            "ETH_VALIDATOR_STATS_STATE": str(state_path),
        },
        clear=False,
    ):
        os.environ.pop("BEACON_NODE_AUTH_TOKEN", None)
        os.environ.pop("BEACON_NODE_URL", None)
        with _patch_client(make_handler()):
            cli.main(["info"])
    out = capsys.readouterr().out
    assert "Bearer (***)" in out
    # Real token must not leak into output
    assert "supersecret" not in out


def test_status_warns_once_when_liveness_unsupported(temp_config, capsys):
    cfg_path, state_path = temp_config
    with _patch_client(make_handler(support_liveness=False)):
        cli.main(["status"])
        first = capsys.readouterr()
        cli.main(["status"])
        second = capsys.readouterr()
    assert "does not implement /eth/v1/validator/liveness" in first.err
    # Second run reads the warned flag from state and stays quiet
    assert "does not implement /eth/v1/validator/liveness" not in second.err


def test_status_with_liveness_works_end_to_end(temp_config, capsys):
    with _patch_client(make_handler(support_liveness=True)):
        rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    # The table should render validator 1
    assert "1" in out
    assert "active_ongoing" in out
