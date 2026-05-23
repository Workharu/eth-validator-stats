from __future__ import annotations

import json

import httpx
import pytest

from eth_validator_stats.beacon import BeaconClient, ChainInfo, epoch_of


def make_transport(routes: dict[tuple[str, str], dict]) -> httpx.MockTransport:
    """routes maps (method, path) -> JSON response dict."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, text=f"no route for {key}")
        return httpx.Response(200, json=routes[key])

    return httpx.MockTransport(handler)


def test_chain_info_parsing():
    transport = make_transport(
        {
            ("GET", "/eth/v1/beacon/genesis"): {"data": {"genesis_time": "1606824023"}},
            ("GET", "/eth/v1/config/spec"): {"data": {"SECONDS_PER_SLOT": "12", "SLOTS_PER_EPOCH": "32"}},
        }
    )
    with BeaconClient("http://node", transport=transport) as c:
        info = c.get_chain_info()
    assert info == ChainInfo(genesis_time=1606824023, seconds_per_slot=12, slots_per_epoch=32)


def test_head_parsing():
    transport = make_transport(
        {
            ("GET", "/eth/v1/beacon/headers/head"): {
                "data": {"header": {"message": {"slot": "9876543"}}}
            }
        }
    )
    with BeaconClient("http://node", transport=transport) as c:
        head = c.get_head()
    assert head.slot == 9876543


def test_get_validators_uses_post_and_parses_response():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "index": "12345",
                        "balance": "32010000000",
                        "status": "active_ongoing",
                        "validator": {"pubkey": "0xabc"},
                    },
                    {
                        "index": "12346",
                        "balance": "31990000000",
                        "status": "exited_slashed",
                        "validator": {"pubkey": "0xdef"},
                    },
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        out = c.get_validators(["0xabc", "12346"])

    assert seen["method"] == "POST"
    assert seen["path"] == "/eth/v1/beacon/states/head/validators"
    assert seen["body"] == {"ids": ["0xabc", "12346"], "statuses": []}
    assert [v.index for v in out] == [12345, 12346]
    assert out[0].balance_gwei == 32010000000
    assert out[1].status == "exited_slashed"


def test_get_liveness_posts_bare_array_and_returns_dict():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": "12345", "is_live": True},
                    {"index": "12346", "is_live": False},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        out = c.get_liveness(412300, [12345, 12346])

    assert seen["method"] == "POST"
    assert seen["path"] == "/eth/v1/validator/liveness/412300"
    assert seen["body"] == ["12345", "12346"]
    assert out == {12345: True, 12346: False}


def test_empty_inputs_skip_network():
    """Empty id/index lists should not make a request."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        assert c.get_validators([]) == []
        assert c.get_liveness(1, []) == {}


def test_http_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        with pytest.raises(httpx.HTTPStatusError):
            c.get_head()


def test_epoch_of():
    info = ChainInfo(genesis_time=0, seconds_per_slot=12, slots_per_epoch=32)
    assert epoch_of(0, info) == 0
    assert epoch_of(31, info) == 0
    assert epoch_of(32, info) == 1
    assert epoch_of(1024, info) == 32


def test_auth_token_sets_bearer_header():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": {"version": "TestClient/v0"}})

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", auth_token="mysecret", transport=transport) as c:
        c.get_node_version()
    assert seen["auth"] == "Bearer mysecret"


def test_no_auth_token_means_no_auth_header():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": {"version": "x"}})

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        c.get_node_version()
    assert seen["auth"] is None


def test_get_node_version_parses():
    transport = make_transport(
        {("GET", "/eth/v1/node/version"): {"data": {"version": "Prysm/v4.2.1/linux-amd64"}}}
    )
    with BeaconClient("http://node", transport=transport) as c:
        v = c.get_node_version()
    assert v.version == "Prysm/v4.2.1/linux-amd64"


def test_get_node_version_missing_field_returns_unknown():
    transport = make_transport({("GET", "/eth/v1/node/version"): {"data": {}}})
    with BeaconClient("http://node", transport=transport) as c:
        v = c.get_node_version()
    assert v.version == "unknown"


def test_liveness_returns_none_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        out = c.get_liveness(123, [1, 2])
    assert out is None


def test_liveness_returns_none_on_405():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(405, text="method not allowed")

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        out = c.get_liveness(123, [1])
    assert out is None


def test_liveness_raises_on_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        with pytest.raises(httpx.HTTPStatusError):
            c.get_liveness(123, [1])


def test_spec_field_accepts_lowercase_fallback():
    """Some clients may return lowercase spec keys; we tolerate both."""
    transport = make_transport(
        {
            ("GET", "/eth/v1/beacon/genesis"): {"data": {"genesis_time": "1606824023"}},
            ("GET", "/eth/v1/config/spec"): {"data": {"seconds_per_slot": "12", "slots_per_epoch": "32"}},
        }
    )
    with BeaconClient("http://node", transport=transport) as c:
        info = c.get_chain_info()
    assert info.seconds_per_slot == 12
    assert info.slots_per_epoch == 32


def test_liveness_defaults_missing_is_live_to_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": "1"}]})  # no is_live field

    transport = httpx.MockTransport(handler)
    with BeaconClient("http://node", transport=transport) as c:
        out = c.get_liveness(1, [1])
    assert out == {1: False}
