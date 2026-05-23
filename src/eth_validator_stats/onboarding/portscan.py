from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

WELL_KNOWN_PORTS: tuple[int, ...] = (3500, 5052, 5051, 9596)


@dataclass(frozen=True)
class Found:
    url: str
    port: int
    client_version: str
    latency_ms: int


async def _probe_one(
    host: str, port: int, scheme: str, timeout_s: float, transport: httpx.BaseTransport | None
) -> Found | None:
    url = f"{scheme}://{host}:{port}"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(base_url=url, timeout=timeout_s, transport=transport) as client:
            r = await client.get("/eth/v1/node/version")
            if r.status_code != 200:
                return None
            try:
                version = r.json()["data"]["version"]
            except (KeyError, TypeError, ValueError):
                return None
            if not isinstance(version, str) or not version:
                return None
            latency_ms = int((time.monotonic() - started) * 1000)
            return Found(url=url, port=port, client_version=version, latency_ms=latency_ms)
    except Exception:
        return None


async def scan(
    host: str,
    *,
    timeout_s: float = 0.8,
    transport: httpx.BaseTransport | None = None,
) -> list[Found]:
    """Probe well-known beacon API ports on host in parallel.
    Tries http first; if nothing responds, tries https once.
    Returns a list of Found tuples (possibly empty).
    """
    for scheme in ("http", "https"):
        coros = [_probe_one(host, p, scheme, timeout_s, transport) for p in WELL_KNOWN_PORTS]
        results = await asyncio.gather(*coros)
        found = [r for r in results if r is not None]
        if found:
            return found
    return []
