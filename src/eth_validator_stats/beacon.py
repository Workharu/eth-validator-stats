from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ChainInfo:
    genesis_time: int
    seconds_per_slot: int
    slots_per_epoch: int


@dataclass(frozen=True)
class Head:
    slot: int


@dataclass(frozen=True)
class ValidatorInfo:
    index: int
    pubkey: str
    status: str
    balance_gwei: int


@dataclass(frozen=True)
class NodeVersion:
    version: str


def epoch_of(slot: int, info: ChainInfo) -> int:
    return slot // info.slots_per_epoch


class BeaconClient:
    def __init__(
        self,
        base_url: str,
        *,
        auth_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        headers = {"Accept": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BeaconClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_chain_info(self) -> ChainInfo:
        genesis = self._get_json("/eth/v1/beacon/genesis")["data"]
        spec = self._get_json("/eth/v1/config/spec")["data"]
        return ChainInfo(
            genesis_time=int(genesis["genesis_time"]),
            seconds_per_slot=int(_spec_field(spec, "SECONDS_PER_SLOT")),
            slots_per_epoch=int(_spec_field(spec, "SLOTS_PER_EPOCH")),
        )

    def get_head(self) -> Head:
        data = self._get_json("/eth/v1/beacon/headers/head")["data"]
        return Head(slot=int(data["header"]["message"]["slot"]))

    def get_node_version(self) -> NodeVersion:
        data = self._get_json("/eth/v1/node/version")["data"]
        return NodeVersion(version=str(data.get("version", "unknown")))

    def get_validators(self, ids: list[str]) -> list[ValidatorInfo]:
        if not ids:
            return []
        body = {"ids": ids, "statuses": []}
        data = self._post_json("/eth/v1/beacon/states/head/validators", json=body)["data"]
        return [
            ValidatorInfo(
                index=int(v["index"]),
                pubkey=v["validator"]["pubkey"],
                status=v["status"],
                balance_gwei=int(v["balance"]),
            )
            for v in data
        ]

    def get_proposer_duties(self, epoch: int) -> list[tuple[int, int]]:
        """Return [(slot, validator_index)] for proposer duties in `epoch`.

        Per spec, nodes are only required to know current and next epoch's duties.
        Older epochs typically 404 or return empty.
        """
        data = self._get_json(f"/eth/v1/validator/duties/proposer/{epoch}")["data"]
        return [(int(d["slot"]), int(d["validator_index"])) for d in data]

    def get_block_header_at_slot(self, slot: int) -> int | None:
        """Return the proposer_index for the canonical block at `slot`, or None
        if no block was proposed (slot was skipped — 404 response).
        """
        try:
            data = self._get_json(f"/eth/v1/beacon/headers/{slot}")["data"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return int(data["header"]["message"]["proposer_index"])

    def get_liveness(self, epoch: int, indices: list[int]) -> dict[int, bool] | None:
        """Return {index: is_live}.

        Returns None when the node does not implement the liveness endpoint
        (older clients return 404 or 405). Callers should treat this as a
        degraded mode and skip recording attestation history.
        """
        if not indices:
            return {}
        body = [str(i) for i in indices]
        try:
            data = self._post_json(f"/eth/v1/validator/liveness/{epoch}", json=body)["data"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 405, 501):
                return None
            raise
        return {int(item["index"]): bool(item.get("is_live", False)) for item in data}

    def _get_json(self, path: str) -> dict:
        resp = self._client.get(path)
        resp.raise_for_status()
        return resp.json()

    def _post_json(self, path: str, *, json: object) -> dict:
        resp = self._client.post(path, json=json)
        resp.raise_for_status()
        return resp.json()


def _spec_field(spec: dict, name: str) -> object:
    """Look up a spec field tolerantly. Beacon API historically returned UPPERCASE keys;
    some newer clients also accept/return lowercase. We try the canonical form first."""
    if name in spec:
        return spec[name]
    lower = name.lower()
    if lower in spec:
        return spec[lower]
    raise KeyError(f"spec response missing both {name} and {lower}")
