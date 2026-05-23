from __future__ import annotations

import asyncio
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from ..alerts import AlertsConfig, NtfyNotifier
from ..beacon import BeaconClient, ValidatorInfo
from ..config_io import AppConfig, ConfigEntry, write_config
from .portscan import Found, scan as default_scan
from .prompts import IOLike, StdIO, confirm, parse_validator_input, prompt


@dataclass(frozen=True)
class WizardArgs:
    host: str | None
    beacon_url: str | None
    auth_token: str | None
    validator: str | None
    label: str | None
    ntfy_topic: str | None
    no_ntfy: bool
    yes: bool
    force: bool


# Type aliases for injection
PortscanFn = Callable[[str], Awaitable[list[Found]]]
BeaconFactory = Callable[..., object]  # returns context manager with .get_validators()
NotifierFactory = Callable[[str], object]  # returns object with .send(title, body)
TopicGenerator = Callable[[], str]


def _default_topic_generator() -> str:
    return "eth-vstats-" + secrets.token_hex(4)


def _default_beacon_factory(url: str, *, auth_token: str | None = None):
    return BeaconClient(url, auth_token=auth_token)


def _default_notifier_factory(topic_url: str):
    return NtfyNotifier(topic_url)


def run_wizard(
    args: WizardArgs,
    *,
    cfg_path: Path,
    io: IOLike | None = None,
    portscan_fn: PortscanFn | None = None,
    beacon_client_factory: BeaconFactory | None = None,
    notifier_factory: NotifierFactory | None = None,
    topic_generator: TopicGenerator | None = None,
) -> int:
    io = io or StdIO()
    beacon_client_factory = beacon_client_factory or _default_beacon_factory
    notifier_factory = notifier_factory or _default_notifier_factory
    topic_generator = topic_generator or _default_topic_generator

    # Step 1 — Beacon node URL (may skip portscan entirely if --beacon-url given)
    if args.beacon_url:
        beacon_url = args.beacon_url
    else:
        portscan_fn = portscan_fn or default_scan
        beacon_url, _ = _step_beacon_url(args, io, portscan_fn)

    # Step 2 — Optional auth
    auth_token = _step_auth(args, io)

    # Step 3 — Starter validator
    entry = _step_validator(args, io, beacon_client_factory, beacon_url, auth_token)

    # Step 4 — ntfy
    ntfy_topic = _step_ntfy(args, io, notifier_factory, topic_generator)

    # Step 5 — Write
    cfg = AppConfig(
        beacon_node_url=beacon_url,
        validators=[entry],
        beacon_auth_token=auth_token,
        alerts=AlertsConfig(ntfy_topic=ntfy_topic),
    )
    write_config(cfg, cfg_path)

    io.write(f"\n✓ Wrote {cfg_path}\n")
    io.write(
        "\nTo add more validators, edit the file and append entries under `validators:`.\n"
        "  validators:\n"
        '    - pubkey: "0x..."\n'
        '      label: "home-2"\n'
        "    - index: 12346\n"
        '      label: "home-3"\n\n'
        "Next:\n"
        "  eth-validator-stats status\n"
        "  eth-validator-stats check --missed 3\n"
    )
    return 0


def _step_beacon_url(args: WizardArgs, io: IOLike, portscan_fn: PortscanFn) -> tuple[str, str]:
    if args.beacon_url:
        return (args.beacon_url, "")
    host = args.host or prompt(io, "Where is your beacon node running?", default="localhost")
    io.write(f"Scanning {host}... (3500, 5052, 5051, 9596 in parallel)\n")
    found = asyncio.run(portscan_fn(host))
    if not found:
        io.write("  no beacon API responded on well-known ports\n\n")
        io.write("  Enter the full URL where your beacon node's HTTP API is reachable.\n")
        io.write("  Format: scheme + host + port (NO API path).\n\n")
        io.write("  Examples:\n")
        io.write("    http://localhost:24010\n")
        io.write("    http://192.168.10.15:5052\n")
        io.write("    https://eth-node.example.com\n\n")
        io.write("  To verify your URL before pasting it in, run this in another terminal:\n")
        io.write("    curl <URL>/eth/v1/node/version\n")
        io.write("  A correct URL returns JSON shaped like:\n")
        io.write('    {"data":{"version":"Prysm/v7.1.2/linux-amd64/go1.22"}}\n\n')
        manual = prompt(io, "Beacon node URL", default=None)
        if not manual:
            raise SystemExit("no beacon URL provided")
        return (manual, "")
    for f in found:
        io.write(f"  ✓ Found {f.client_version} at {f.url}\n")
    if len(found) == 1:
        chosen = found[0]
    else:
        for i, f in enumerate(found, start=1):
            io.write(f"  [{i}] {f.url} ({f.client_version})\n")
        idx_raw = prompt(io, "Select", default="1")
        chosen = found[int(idx_raw) - 1]
    accepted = confirm(io, f"Use {chosen.url}?", default=True)
    if not accepted:
        raise SystemExit("user declined detected beacon URL")
    return (chosen.url, chosen.client_version)


def _step_auth(args: WizardArgs, io: IOLike) -> str:
    if args.auth_token is not None:
        return args.auth_token
    needs = confirm(io, "Does this node need a Bearer auth token?", default=False)
    if not needs:
        return ""
    raw = prompt(io, "Bearer token (will be stored in config)", default="")
    return raw


def _step_validator(
    args: WizardArgs,
    io: IOLike,
    beacon_factory: BeaconFactory,
    beacon_url: str,
    auth_token: str,
) -> ConfigEntry:
    if args.validator:
        kind, value = parse_validator_input(args.validator)
    else:
        from .prompts import prompt_validator
        kind, value = prompt_validator(io)
    identifier = str(value) if kind == "index" else str(value)
    label = args.label if args.label is not None else prompt(io, "Label", default="validator-1")

    # Verify against the beacon node
    with beacon_factory(beacon_url, auth_token=auth_token or None) as client:
        results = client.get_validators([identifier])
    if not results:
        raise SystemExit(f"validator {identifier!r} not found on this beacon node")
    info: ValidatorInfo = results[0]
    io.write(
        f"  ✓ index={info.index} status={info.status} "
        f"balance={info.balance_gwei / 1_000_000_000:.4f} ETH\n"
    )
    return ConfigEntry(
        identifier=identifier,
        label=label,
        pubkey=info.pubkey if kind == "pubkey" else None,
        index=info.index if kind == "index" else None,
    )


def _step_ntfy(
    args: WizardArgs,
    io: IOLike,
    notifier_factory: NotifierFactory,
    topic_generator: TopicGenerator,
) -> str:
    if args.no_ntfy:
        return ""
    if args.ntfy_topic:
        topic = args.ntfy_topic
    else:
        if not confirm(io, "Enable push notifications via ntfy?", default=True):
            return ""
        suggested = topic_generator()
        chosen = prompt(io, "Topic name", default=suggested)
        topic = chosen
    topic_url = topic if topic.startswith("http") else f"https://ntfy.sh/{topic}"
    io.write(f"Sending test message to {topic_url}\n")
    notifier = notifier_factory(topic_url)
    notifier.send(
        "eth-validator-stats setup test",
        "Welcome — if you see this on your phone, your ntfy wire-up is working.",
    )
    io.write(
        "  ✓ Test push sent. Install the ntfy app and subscribe to the topic, "
        "then confirm the message arrived.\n"
    )
    if args.yes:
        return topic_url
    prompt(io, "Press Enter once you've confirmed (or skip)", default="")
    return topic_url
