from __future__ import annotations

import dataclasses
import datetime
import logging
import os
import shutil
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import __version__ as _PACKAGE_VERSION
from .config_io import AppConfig

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
    #   "io_error"  shutil/open raised
    skipped_reason: str | None
    appended_keys: list[str]  # dotted paths actually written to disk


_SCALAR_TYPES = (str, int, float, bool)


def _resolve_type(field_type: Any) -> tuple[type | None, bool]:
    """Return (concrete_scalar_type_or_None, is_nested_dataclass).

    Handles Optional[T] by unwrapping to T. Anything that's neither a
    scalar nor a dataclass returns (None, False) and gets skipped.
    """
    if dataclasses.is_dataclass(field_type):
        return None, True

    import types as _types

    origin = typing.get_origin(field_type)
    if origin is typing.Union or isinstance(field_type, _types.UnionType):
        args = [a for a in typing.get_args(field_type) if a is not type(None)]
        if len(args) == 1:
            return _resolve_type(args[0])
        return None, False

    if origin in (list, dict, tuple, set, frozenset):
        return None, False

    if isinstance(field_type, type) and issubclass(field_type, _SCALAR_TYPES):
        return field_type, False

    return None, False


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
        scalar, is_nested = _resolve_type(ftype)
        has_default, default = _has_default(f)

        if is_nested:
            # Always recurse into nested dataclasses, even if the parent
            # field has no default — we're not auto-populating the
            # parent, just discovering its leaves.
            sub_cls = ftype
            out.extend(schema_keys(sub_cls, _prefix=f"{_prefix}{f.name}."))
            continue

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


def find_missing_keys(config_path: Path, keys: list[KeySpec]) -> list[KeySpec]:
    """Return the subset of `keys` whose leaf name is not present anywhere
    in the raw text of `config_path` (parsed values, comments, anywhere).

    Leaf-name match is intentional: it gives us free idempotency against
    blocks we previously appended, and treats commented-out values as
    "user already knows about this".

    If the file is unreadable, return an empty list — sync becomes a no-op.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []

    out: list[KeySpec] = []
    for k in keys:
        leaf = k.dotted_path.rsplit(".", 1)[-1]
        if leaf not in text:
            out.append(k)
    return out


def _dump_value_line(key: str, value: object) -> str:
    """Return a single-line YAML dump of {key: value}, no trailing newline."""
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
    return out.rstrip("\n")


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
        "# See config.yml.example or README.md for what each does.",
        "# (Delete a line if you want it re-added next upgrade; keep the bare",
        "#  key name in a comment if you want to permanently suppress it.)",
        "#",
    ]

    if root:
        for k in root:
            lines.append("# " + _dump_value_line(k.dotted_path, k.default))
        if group_order:
            lines.append("#")

    for i, parent in enumerate(group_order):
        lines.append(f"# {parent}:")
        for k in grouped[parent]:
            leaf = k.dotted_path.split(".", 1)[1]
            lines.append("#   " + _dump_value_line(leaf, k.default))
        if i != len(group_order) - 1:
            lines.append("#")

    return "\n".join(lines) + "\n"


def append_upgrade_block(path: Path, block: str) -> bool:
    """Atomically append `block` to `path` after copying `path` to `.bak`.

    Returns True on success, False on any failure. Never raises.

    Failure modes (all return False):
      - Path is not writable for the current process.
      - `shutil.copy2` raises (disk full, source vanished, etc.).
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
        shutil.copy2(path, bak)
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

    if not path.exists():
        return SyncResult(skipped_reason="no_config", appended_keys=[])

    if not os.access(path, os.W_OK):
        # Check up-front so we report "not_writable" even when there's no drift.
        # append_upgrade_block also checks, but only after find_missing_keys runs.
        return SyncResult(skipped_reason="not_writable", appended_keys=[])

    try:
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
