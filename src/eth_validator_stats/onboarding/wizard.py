from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..alerts import DEFAULT_NTFY_ICON_URL, AlertsConfig, NtfyNotifier
from ..beacon import BeaconClient, ValidatorInfo
from ..config_io import AppConfig, ConfigEntry, write_config
from .portscan import Found
from .portscan import scan as default_scan
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
    # Include the default icon URL so the verification push during
    # `init` looks the same as every real alert that follows.
    # Without this the test push arrives icon-less and users wonder
    # why their first push doesn't match the documented behavior.
    return NtfyNotifier(topic_url, icon_url=DEFAULT_NTFY_ICON_URL)


def _render_qr_for_terminal(url: str) -> str:
    """Return an ASCII/block-character QR code for `url`, or empty string on failure."""
    try:
        import io as _io

        import segno
        buf = _io.StringIO()
        segno.make(url, micro=False).terminal(out=buf, border=1, compact=True)
        return buf.getvalue()
    except Exception:
        return ""


def run_wizard(
    args: WizardArgs,
    *,
    cfg_path: Path,
    io: IOLike | None = None,
    portscan_fn: PortscanFn | None = None,
    beacon_client_factory: BeaconFactory | None = None,
    notifier_factory: NotifierFactory | None = None,
    topic_generator: TopicGenerator | None = None,
    write_mode: int = 0o600,
    write_uid: int | None = None,
    write_gid: int | None = None,
) -> int:
    io = io or StdIO()
    beacon_client_factory = beacon_client_factory or _default_beacon_factory
    notifier_factory = notifier_factory or _default_notifier_factory
    topic_generator = topic_generator or _default_topic_generator

    # Step 1 — Beacon node URL (may skip portscan entirely if --beacon-url given)
    if args.beacon_url:
        _probe_node_version(args.beacon_url, beacon_client_factory, io)
        beacon_url = args.beacon_url
    else:
        portscan_fn = portscan_fn or default_scan
        beacon_url, _ = _step_beacon_url(args, io, portscan_fn, beacon_client_factory)

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
    # Apply mode/uid/gid atomically on the tmp file before rename — see
    # config_io.write_config's docstring for why this matters on system
    # installs where the service user needs to read /etc/<pkg>/config.yml
    # under mode 0644.
    write_config(cfg, cfg_path, mode=write_mode, uid=write_uid, gid=write_gid)

    io.write(f"\n✓ Wrote {cfg_path}\n")
    io.write(
        "\nAdd more validators (verified against the beacon node):\n"
        "  eth-validator-stats validators add <pubkey-or-index> --label home-2\n"
        "  eth-validator-stats validators list\n"
        "\nOr the 3-character alias:\n"
        "  evs validators add 12346 --label home-3\n"
        "  evs status\n"
        "\nNext, try:\n"
        "  eth-validator-stats status\n"
        "  eth-validator-stats check --missed 3\n"
    )
    return 0


def _probe_node_version(url: str, beacon_factory: BeaconFactory, io: IOLike) -> str:
    """Probe /eth/v1/node/version on a beacon URL. Print result. On failure, offer save-anyway."""
    io.write(f"  Probing {url}/eth/v1/node/version ...\n")
    try:
        with beacon_factory(url, auth_token=None) as client:
            version = client.get_node_version().version
        io.write(f"  ✓ Connected: {version}\n")
        return version
    except Exception as e:
        io.write(f"  ✗ Could not reach beacon node: {type(e).__name__}: {e}\n")
        if confirm(io, "Save this URL anyway and continue?", default=False):
            return ""
        raise SystemExit(f"beacon node not reachable at {url}") from e


def _step_beacon_url(args: WizardArgs, io: IOLike, portscan_fn: PortscanFn, beacon_factory: BeaconFactory) -> tuple[str, str]:
    if args.beacon_url:
        version = _probe_node_version(args.beacon_url, beacon_factory, io)
        return (args.beacon_url, version)
    host = args.host or prompt(io, "Where is your beacon node running?", default="localhost")
    io.write(f"Scanning {host}... (3500, 5052, 5051, 9596 in parallel)\n")
    found = asyncio.run(portscan_fn(host))
    if not found:
        io.write("  no beacon API responded on well-known ports\n\n")
        io.write("  Enter the full URL where your beacon node's HTTP API is reachable.\n")
        io.write("  Format: scheme + host + port (NO API path).\n\n")
        io.write("  Examples:\n")
        io.write("    http://localhost:24010\n")
        io.write("    http://192.0.2.10:5052\n")
        io.write("    https://eth-node.example.com\n\n")
        io.write("  To verify your URL before pasting it in, run this in another terminal:\n")
        io.write("    curl <URL>/eth/v1/node/version\n")
        io.write("  A correct URL returns JSON shaped like:\n")
        io.write('    {"data":{"version":"Prysm/v7.1.2/linux-amd64/go1.22"}}\n\n')
        manual = prompt(io, "Beacon node URL", default=None)
        if not manual:
            raise SystemExit("no beacon URL provided")
        version = _probe_node_version(manual, beacon_factory, io)
        return (manual, version)
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
    # User said yes — they want auth. Insist on a non-empty token, since
    # an empty token is the same as having said no and silently
    # accepting it would be confusing.
    while True:
        raw = prompt(io, "Bearer token (will be stored in config)", default="").strip()
        if raw:
            return raw
        io.write(
            "  empty token; if you don't actually need auth, answer 'n' to the previous question.\n"
        )
        if not confirm(io, "Save without an auth token (equivalent to answering 'no')?", default=False):
            continue
        return ""


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

    qr_text = _render_qr_for_terminal(topic_url)
    if qr_text:
        io.write("\n  Scan this QR with your phone camera (or the ntfy app's scanner)\n")
        io.write("  to subscribe — no typing required:\n\n")
        io.write(qr_text)
        io.write(f"\n  Topic URL (if scanning isn't an option): {topic_url}\n\n")
    else:
        io.write(f"\n  Topic URL: {topic_url}\n")
        io.write("  Open the ntfy app on your phone and subscribe to this topic.\n\n")

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
