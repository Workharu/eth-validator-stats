from __future__ import annotations

import asyncio

import httpx

from eth_validator_stats.onboarding.portscan import scan


def _make_handler(port_to_response: dict[int, httpx.Response]):
    """Routes by port. Missing ports = ConnectError simulation via 599."""
    def handler(request: httpx.Request) -> httpx.Response:
        port = request.url.port or 80
        if port in port_to_response:
            return port_to_response[port]
        raise httpx.ConnectError("no listener", request=request)
    return handler


def _ok_version(name: str) -> httpx.Response:
    return httpx.Response(200, json={"data": {"version": name}})


def _run(coro):
    return asyncio.run(coro)


def test_scan_all_four_well_known_ports_respond():
    handler = _make_handler({
        3500: _ok_version("Prysm/v7"),
        5052: _ok_version("Lighthouse/v5"),
        5051: _ok_version("Teku/v24"),
        9596: _ok_version("Lodestar/v1"),
    })
    transport = httpx.MockTransport(handler)
    results = _run(scan("localhost", transport=transport))
    assert len(results) == 4
    versions = {r.client_version for r in results}
    assert versions == {"Prysm/v7", "Lighthouse/v5", "Teku/v24", "Lodestar/v1"}


def test_scan_only_one_port_responds():
    handler = _make_handler({3500: _ok_version("Prysm/v7")})
    transport = httpx.MockTransport(handler)
    results = _run(scan("localhost", transport=transport))
    assert len(results) == 1
    assert results[0].port == 3500
    assert results[0].url == "http://localhost:3500"


def test_scan_malformed_body_treated_as_no_response():
    handler = _make_handler({3500: httpx.Response(200, json={"unexpected": "shape"})})
    transport = httpx.MockTransport(handler)
    results = _run(scan("localhost", transport=transport))
    assert results == []


def test_scan_404_treated_as_no_response():
    handler = _make_handler({3500: httpx.Response(404)})
    transport = httpx.MockTransport(handler)
    results = _run(scan("localhost", transport=transport))
    assert results == []


def test_scan_returns_empty_when_nothing_responds():
    handler = _make_handler({})
    transport = httpx.MockTransport(handler)
    results = _run(scan("localhost", transport=transport))
    assert results == []


def test_scan_isolation_one_probe_error_does_not_abort_others():
    def handler(request: httpx.Request) -> httpx.Response:
        port = request.url.port
        if port == 3500:
            raise httpx.ReadError("connection reset", request=request)
        if port == 5052:
            return _ok_version("Lighthouse/ok")
        raise httpx.ConnectError("no listener", request=request)
    transport = httpx.MockTransport(handler)
    results = _run(scan("localhost", transport=transport))
    assert len(results) == 1
    assert results[0].port == 5052
