from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table
from rich.text import Text

HIT = "●"
MISS = "·"
UNKNOWN = "?"

GWEI_PER_ETH = 1_000_000_000


@dataclass(frozen=True)
class DisplayRow:
    index: int
    label: str
    status: str
    balance_gwei: int
    liveness: list[tuple[int, int]]  # [(epoch, attested 0/1), ...] oldest -> newest


def format_balance(gwei: int) -> str:
    eth = gwei / GWEI_PER_ETH
    return f"{eth:.4f}"


def format_glyphs(liveness: list[tuple[int, int]], n: int = 5) -> str:
    tail = liveness[-n:]
    pad = n - len(tail)
    glyphs = [UNKNOWN] * pad + [HIT if attested else MISS for _, attested in tail]
    return "".join(glyphs)


def _status_text(status: str) -> Text:
    if status == "active_ongoing":
        return Text(status, style="green")
    if status.startswith("pending"):
        return Text(status, style="yellow")
    if status.startswith("exited") or "slashed" in status:
        return Text(status, style="red bold")
    return Text(status, style="red")


def build_table(rows: list[DisplayRow], *, n_atts: int = 5) -> Table:
    table = Table(title="Validators", header_style="bold")
    table.add_column("idx", justify="right")
    table.add_column("label")
    table.add_column("status")
    table.add_column("balance (ETH)", justify="right")
    table.add_column(f"last {n_atts} atts")
    for r in sorted(rows, key=lambda x: x.index):
        table.add_row(
            str(r.index),
            r.label or "",
            _status_text(r.status),
            format_balance(r.balance_gwei),
            format_glyphs(r.liveness, n_atts),
        )
    return table
