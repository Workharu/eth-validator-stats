from __future__ import annotations

import dataclasses
import datetime
import logging
import os
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from . import __version__ as _PACKAGE_VERSION
from .config_io import AppConfig, load_config, resolve_config_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeySpec:
    """A single config leaf key derived from the dataclass schema."""

    dotted_path: str  # e.g. "alerts.heartbeat_url"
    default: object
    type_name: str  # e.g. "str", "int", "bool"


@dataclass(frozen=True)
class SyncResult:
    """Outcome of a sync_user_config() call. Returned for testing/logging."""

    # None on success or no-drift. Otherwise one of:
    #   "no_config"   load_config-resolved path doesn't exist
    #   "not_writable"  os.access W_OK returned False
    #   "disabled"  ETH_VALIDATOR_STATS_NO_CONFIG_SYNC=1
    #   "sentinel"  user placed SENTINEL_DISABLE marker in their config
    #   "io_error"  copy/open raised
    skipped_reason: (
        Literal["no_config", "not_writable", "disabled", "sentinel", "io_error"] | None
    )
    appended_keys: list[str]  # dotted paths actually written to disk


_SCALAR_TYPES = (str, int, float, bool)


def _resolve_type(field_type: Any) -> tuple[type | None, type | None]:
    """Return (scalar_type_or_None, nested_dataclass_or_None).

    Both None means skip this field. Handles Optional[T] by unwrapping to T
    so callers always receive the concrete dataclass when present, never a
    Union wrapper.
    """
    if dataclasses.is_dataclass(field_type):
        return None, field_type

    origin = typing.get_origin(field_type)
    if origin is typing.Union or isinstance(field_type, types.UnionType):
        args = [a for a in typing.get_args(field_type) if a is not type(None)]
        if len(args) == 1:
            return _resolve_type(args[0])
        return None, None

    if origin in (list, dict, tuple, set, frozenset):
        return None, None

    if isinstance(field_type, type) and issubclass(field_type, _SCALAR_TYPES):
        return field_type, None

    return None, None


def _has_default(f: dataclasses.Field) -> tuple[bool, Any]:
    if f.default is not dataclasses.MISSING:
        return True, f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return True, f.default_factory()
    return False, None


def schema_keys(cls: type, _prefix: str = "") -> list[KeySpec]:
    """Walk a dataclass tree depth-first, returning scalar leaf keys.

    Rules:
      - Required fields (no default) excluded.
      - list/dict/tuple/set fields excluded.
      - Nested @dataclass fields recursed into; path is dotted with parent name.
      - Optional[T] unwraps to T.
    """
    # Build a localns so that get_type_hints can resolve locally-defined
    # dataclasses (e.g. those defined inside test functions) whose names
    # are not present in any module's global namespace.
    localns: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            factory = f.default_factory  # type: ignore[misc]
            if dataclasses.is_dataclass(factory):
                localns[factory.__name__] = factory
        if isinstance(f.default, type) and dataclasses.is_dataclass(f.default):
            localns[f.default.__name__] = f.default
    hints = typing.get_type_hints(cls, localns=localns)
    out: list[KeySpec] = []
    for f in dataclasses.fields(cls):
        ftype = hints.get(f.name, f.type)
        scalar, nested_cls = _resolve_type(ftype)

        if nested_cls is not None:
            # Always recurse into nested dataclasses, even if the parent
            # field has no default — we're not auto-populating the
            # parent, just discovering its leaves.
            out.extend(schema_keys(nested_cls, _prefix=f"{_prefix}{f.name}."))
            continue

        has_default, default = _has_default(f)
        if not has_default or scalar is None:
            continue

        out.append(
            KeySpec(
                dotted_path=f"{_prefix}{f.name}",
                default=default,
                type_name=scalar.__name__,
            )
        )
    return out


_MAX_CONFIG_SIZE = 1 << 20  # 1 MiB — paranoia bound against /dev/zero or runaway files

# Marker the operator can place anywhere in their config to disable
# all future auto-appends. Substring-matched against the raw file text.
SENTINEL_DISABLE = "# config-sync: off"

# Per-key inline hints rendered as trailing comments in the appended
# block. Keep terse — the goal is "what unit/meaning" not "full docs".
# Missing keys render without a trailing comment.
_KEY_HINTS: dict[str, str] = {
    "beacon_auth_token": "optional bearer token (Infura / Alchemy / proxied node)",
    "alerts.request_timeout_s": "seconds",
    "alerts.daily_heartbeat_hour": "local time, 0-23",
    "alerts.withdrawal_max_gap_slots": "slots; >this between polls disables withdrawal detection",
    "alerts.proposal_lookahead_epochs": "epochs ahead to pre-notify; 0 disables",
}


def _format_key_line(
    key: str,
    value: object,
    *,
    indent: str,
    dotted_path: str | None = None,
) -> str:
    """Format a single commented key/value line, optionally with a trailing hint.

    `key` is the YAML key as it will appear (leaf for nested, dotted for root).
    `dotted_path` is the lookup key for _KEY_HINTS; falls back to `key`.
    """
    line = indent + _dump_value_line(key, value)
    hint = _KEY_HINTS.get(dotted_path or key)
    if hint:
        line = f"{line}  # {hint}"
    return line


def find_missing_keys(config_path: Path, keys: list[KeySpec]) -> list[KeySpec]:
    """Return the subset of `keys` whose leaf name is not present anywhere
    in the raw text of `config_path` (parsed values, comments, anywhere).

    Leaf-name match is intentional: it gives us free idempotency against
    blocks we previously appended, and treats commented-out values as
    "user already knows about this". Correctness depends on leaf-name
    uniqueness across the schema, which we assert.

    If the file is unreadable, too large, or non-UTF-8, return an empty list
    — sync becomes a no-op.
    """
    leaf_names = [k.dotted_path.rsplit(".", 1)[-1] for k in keys]
    assert len(leaf_names) == len(set(leaf_names)), (
        f"config_sync: duplicate leaf names would cause false-negatives: "
        f"{[n for n in leaf_names if leaf_names.count(n) > 1]}"
    )

    try:
        if config_path.stat().st_size > _MAX_CONFIG_SIZE:
            logger.debug(
                "config at %s exceeds %d bytes; skipping sync", config_path, _MAX_CONFIG_SIZE
            )
            return []
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    out: list[KeySpec] = []
    for k, leaf in zip(keys, leaf_names, strict=True):
        if leaf not in text:
            out.append(k)
    return out


def _dump_value_line(key: str, value: object) -> str:
    """Return a single-line YAML dump of {key: value}, no trailing newline.

    Raises ValueError if the dump spans multiple lines — a multi-line default
    would break the surrounding comment block (only the first line gets the
    `# ` prefix in the appender). Today no AppConfig default triggers this;
    the guard is for future contributors.
    """
    # default_flow_style=False keeps mapping style; width=1024 prevents
    # PyYAML from wrapping long string values. allow_unicode=True keeps
    # any URLs / non-ascii intact.
    out = yaml.safe_dump(
        {key: value},
        default_flow_style=False,
        width=1024,
        allow_unicode=True,
        sort_keys=False,
    )
    result = out.rstrip("\n")
    if "\n" in result:
        raise ValueError(
            f"config_sync: default for {key!r} dumps to multiple lines; "
            "would corrupt the appended YAML comment block"
        )
    return result


def render_upgrade_block(
    missing: list[KeySpec],
    *,
    version: str,
    today: datetime.date,
) -> str:
    """Render the YAML-comment block to append at the end of the user's config.

    Layout:
      <blank line>
      # === Added by eth-validator-stats v{version} on {today} ===
      # ...explanatory lines...
      #
      # <root key 1>: <default>
      # <root key 2>: <default>
      #
      # alerts:
      #   <leaf 1>: <default>
      #   <leaf 2>: <default>

    Groups are omitted entirely if they have no missing children.
    """
    # Split by parent dotted-prefix, preserving order.
    root: list[KeySpec] = []
    grouped: dict[str, list[KeySpec]] = {}
    group_order: list[str] = []
    for k in missing:
        if "." not in k.dotted_path:
            root.append(k)
            continue
        parent, _ = k.dotted_path.split(".", 1)
        if parent not in grouped:
            grouped[parent] = []
            group_order.append(parent)
        grouped[parent].append(k)

    lines: list[str] = [
        "",
        f"# === Added by eth-validator-stats v{version} on {today.isoformat()} ===",
        "# New config keys introduced in this version. Defaults shown.",
        "# Uncomment to enable; leave commented to silence permanently.",
        "# See config.yml.example for details on each key.",
        "# To disable all future auto-appends, add anywhere in this file:",
        f"#     {SENTINEL_DISABLE}",
        "#",
    ]

    if root:
        for k in root:
            lines.append(_format_key_line(k.dotted_path, k.default, indent="# "))
        if group_order:
            lines.append("#")

    for i, parent in enumerate(group_order):
        lines.append(f"# {parent}:")
        for k in grouped[parent]:
            leaf = k.dotted_path.split(".", 1)[1]
            lines.append(_format_key_line(leaf, k.default, indent="#   ", dotted_path=k.dotted_path))
        if i != len(group_order) - 1:
            lines.append("#")

    return "\n".join(lines) + "\n"


def _safe_copy_bak(src: Path, dst: Path) -> None:
    """Copy `src` bytes to `dst`, refusing to follow if `dst` is a symlink.

    Raises OSError (ELOOP on Linux/macOS) if `dst` exists and is a symlink —
    defends against an attacker pre-planting `config.yml.bak` as a symlink
    pointing at a file they want the CLI to clobber. Preserves the source's
    permission bits via fchmod on the open fd (no umask interference, no
    TOCTOU window). O_NOFOLLOW is unavailable on Windows; falls back to 0.
    """
    data = src.read_bytes()
    src_mode = src.stat().st_mode & 0o777
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | nofollow, 0o600)
    try:
        os.write(fd, data)
        os.fchmod(fd, src_mode)
    finally:
        os.close(fd)


def append_upgrade_block(path: Path, block: str) -> bool:
    """Atomically append `block` to `path` after copying `path` to `.bak`.

    Returns True on success, False on any failure. Never raises.

    Failure modes (all return False):
      - Path is not writable for the current process.
      - `_safe_copy_bak` raises (disk full, dst is a symlink, etc.).
      - `open(path, "a")` raises (race after the os.access check).
      - Write itself raises (disk full mid-write).
    """
    try:
        if not os.access(path, os.W_OK):
            logger.debug("config at %s not writable; skipping sync", path)
            return False
    except OSError as exc:
        logger.debug("config sync os.access on %s failed: %s", path, exc)
        return False

    bak = path.with_suffix(path.suffix + ".bak")
    try:
        _safe_copy_bak(path, bak)
    except OSError as exc:
        logger.debug("config sync bak copy %s -> %s failed: %s", path, bak, exc)
        return False

    try:
        needs_leading_newline = False
        if path.stat().st_size > 0:
            with path.open("rb") as f:
                f.seek(-1, os.SEEK_END)
                last_byte = f.read(1)
            if last_byte != b"\n":
                needs_leading_newline = True
        with path.open("a", encoding="utf-8") as f:
            if needs_leading_newline:
                f.write("\n")
            f.write(block)
    except OSError as exc:
        logger.debug("config sync append to %s failed: %s", path, exc)
        return False

    return True


def sync_user_config(path: Path) -> SyncResult:
    """Top-level orchestrator. Never raises.

    Detects schema-vs-file drift and appends missing keys as a commented
    YAML block. Idempotent: subsequent runs find no drift and do nothing.
    """
    if os.environ.get("ETH_VALIDATOR_STATS_NO_CONFIG_SYNC") == "1":
        return SyncResult(skipped_reason="disabled", appended_keys=[])

    try:
        if not path.exists():
            return SyncResult(skipped_reason="no_config", appended_keys=[])
        if not os.access(path, os.W_OK):
            # Check up-front so we report "not_writable" even when there's no drift.
            # append_upgrade_block also checks, but only after find_missing_keys runs.
            return SyncResult(skipped_reason="not_writable", appended_keys=[])
    except OSError as exc:
        # Path.exists() / os.access can raise PermissionError when a parent
        # directory denies traversal — must not propagate.
        logger.debug("config sync path check on %s failed: %s", path, exc)
        return SyncResult(skipped_reason="io_error", appended_keys=[])

    try:
        text = path.read_text(encoding="utf-8")
        if any(line.lstrip() == SENTINEL_DISABLE for line in text.splitlines()):
            return SyncResult(skipped_reason="sentinel", appended_keys=[])
        keys = schema_keys(AppConfig)
        missing = find_missing_keys(path, keys)
    except Exception as exc:  # noqa: BLE001
        logger.debug("config sync introspection on %s failed: %s", path, exc)
        return SyncResult(skipped_reason="io_error", appended_keys=[])

    if not missing:
        return SyncResult(skipped_reason=None, appended_keys=[])

    block = render_upgrade_block(
        missing,
        version=_PACKAGE_VERSION,
        today=datetime.date.today(),
    )
    if not append_upgrade_block(path, block):
        return SyncResult(skipped_reason="io_error", appended_keys=[])

    return SyncResult(
        skipped_reason=None,
        appended_keys=[k.dotted_path for k in missing],
    )


def load_config_with_sync() -> AppConfig:
    """load_config() + best-effort schema sync. Never raises from sync."""
    cfg = load_config()
    try:
        sync_user_config(resolve_config_path())
    except Exception:  # noqa: BLE001 — defense in depth; sync_user_config is already no-raise
        pass
    return cfg
