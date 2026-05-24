from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertsConfig:
    ntfy_topic: str = ""
    cooldown_minutes: int = 30
    storm_threshold: int = 10
    request_timeout_s: float = 5.0
    missed_attestations_threshold: int = 2
    withdrawal_threshold_gwei: int = 1_000_000  # 0.001 ETH — skim noise below this
    # Skip withdrawal detection if the two compared balance snapshots are this many
    # slots apart or more (default 64 ≈ ~12.8 min on mainnet). Wider gaps make it
    # impossible to distinguish a real withdrawal from cumulative attestation losses.
    withdrawal_max_gap_slots: int = 64
    proposal_lookahead_epochs: int = 1


class Notifier(Protocol):
    def send(self, title: str, body: str) -> None: ...


class NullNotifier:
    def send(self, title: str, body: str) -> None:  # noqa: ARG002
        return


class NtfyNotifier:
    def __init__(
        self,
        topic_url: str,
        *,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        raise_on_error: bool = False,
    ) -> None:
        self.topic_url = topic_url
        self.timeout = timeout
        self._transport = transport
        self._raise_on_error = raise_on_error

    def send(self, title: str, body: str) -> None:
        if not self.topic_url:
            return
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as c:
                r = c.post(
                    self.topic_url,
                    content=body.encode("utf-8"),
                    headers={"Title": title},
                )
                r.raise_for_status()
        except Exception as e:
            logger.warning("ntfy notify failed: %s", e)
            if self._raise_on_error:
                raise


def make_notifier(cfg: AlertsConfig) -> Notifier:
    if cfg.ntfy_topic:
        return NtfyNotifier(cfg.ntfy_topic, timeout=cfg.request_timeout_s)
    return NullNotifier()


BLIND_KEY = "blind_alerted_until_ts"


def process_blind(state: dict, error_message: str, notifier: Notifier, cfg: AlertsConfig, now: int) -> None:
    """Beacon node unreachable: send MONITOR BLIND, deduped by cooldown."""
    until = int(state.get(BLIND_KEY, 0))
    if now < until:
        return
    notifier.send("MONITOR BLIND", f"beacon node unreachable: {error_message}")
    state[BLIND_KEY] = now + cfg.cooldown_minutes * 60


def clear_blind_if_recovered(state: dict, notifier: Notifier) -> None:
    """Beacon node reachable again after a previous BLIND: send recovery message."""
    if int(state.get(BLIND_KEY, 0)) > 0:
        notifier.send("MONITOR RECOVERED", "beacon node reachable again")
        state[BLIND_KEY] = 0


def process_validator_alerts(
    state: dict,
    configured_indices: set[int],
    current_alerts: list[tuple[int, str, str]],
    notifier: Notifier,
    cfg: AlertsConfig,
    now: int,
) -> tuple[list[tuple[int, str, str]], list[tuple[int, str]]]:
    """Apply dedup, storm grouping, recovery messaging. Mutates state. Returns (notified_alerts, recoveries)."""
    cooldown_s = cfg.cooldown_minutes * 60
    vstate = state.setdefault("validators", {})

    new_alerts: list[tuple[int, str, str]] = []
    alerting_now: set[int] = set()
    for idx, label, rule in current_alerts:
        alerting_now.add(idx)
        record = vstate.setdefault(str(idx), {})
        prev_rule = record.get("alerted_rule")
        until = int(record.get("alerted_until_ts", 0))
        if prev_rule != rule or now >= until:
            new_alerts.append((idx, label, rule))
            record["alerted_rule"] = rule
            record["alerted_until_ts"] = now + cooldown_s

    recoveries: list[tuple[int, str]] = []
    for idx_str, record in vstate.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if idx not in configured_indices:
            continue
        prev_rule = record.get("alerted_rule")
        if prev_rule and idx not in alerting_now:
            recoveries.append((idx, record.get("label", "")))
            record["alerted_rule"] = None
            record["alerted_until_ts"] = 0

    if len(new_alerts) > cfg.storm_threshold:
        sample = ", ".join(
            f"{idx}({label})" if label else str(idx)
            for idx, label, _ in new_alerts[:5]
        )
        notifier.send(
            "VALIDATOR STORM",
            f"{len(new_alerts)} new validator alerts. Sample: {sample}",
        )
    else:
        for idx, label, rule in new_alerts:
            label_part = f" {label}" if label else ""
            notifier.send(f"validator {idx}{label_part}", rule)

    if len(recoveries) > cfg.storm_threshold:
        sample = ", ".join(
            f"{idx}({label})" if label else str(idx)
            for idx, label in recoveries[:5]
        )
        notifier.send(
            "VALIDATOR STORM RECOVERED",
            f"{len(recoveries)} validators recovered. Sample: {sample}",
        )
    else:
        for idx, label in recoveries:
            label_part = f" {label}" if label else ""
            notifier.send(f"validator {idx}{label_part} RECOVERED", "back to active_ongoing")

    return new_alerts, recoveries


def record_scheduled_proposals(
    state: dict,
    duties: list[tuple[int, int]],
    configured_indices: set[int],
) -> None:
    """Persist upcoming proposer duties into per-validator state for our validators.
    Idempotent — re-recording the same (validator, slot) is a no-op.
    """
    vstate = state.setdefault("validators", {})
    for slot, validator_index in duties:
        if validator_index not in configured_indices:
            continue
        key = str(validator_index)
        record = vstate.setdefault(key, {})
        proposals = record.setdefault("scheduled_proposals", [])
        if any(int(p.get("slot", -1)) == slot for p in proposals):
            continue
        proposals.append({"slot": int(slot), "alerted": False, "verified": False})


def process_upcoming_proposals(
    state: dict,
    current_slot: int,
    seconds_per_slot: int,
    slots_per_epoch: int,
    lookahead_epochs: int,
    notifier: Notifier,
) -> list[tuple[int, str, int]]:
    """Notify exactly once per scheduled proposal within the lookahead window.
    Returns [(idx, label, slot)] for the alerts that fired.
    """
    fired: list[tuple[int, str, int]] = []
    window_slots = lookahead_epochs * slots_per_epoch
    vstate = state.setdefault("validators", {})
    for idx_str, record in vstate.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        label = record.get("label", "")
        for prop in record.get("scheduled_proposals", []):
            slot = int(prop.get("slot", -1))
            if slot < current_slot:
                continue
            if slot > current_slot + window_slots:
                continue
            if prop.get("alerted"):
                continue
            slots_away = slot - current_slot
            total_seconds = slots_away * seconds_per_slot
            if total_seconds < 60:
                delay_str = f"~{total_seconds}s"
            else:
                delay_str = f"~{total_seconds // 60} min"
            label_part = f" {label}" if label else ""
            notifier.send(
                f"validator {idx}{label_part} proposing soon",
                f"slot {slot} ({delay_str} away)",
            )
            prop["alerted"] = True
            fired.append((idx, label, slot))
    return fired


HeaderFetcher = Callable[[int], int | None]


def process_proposal_outcomes(
    state: dict,
    current_slot: int,
    fetch_header_fn: HeaderFetcher,
    notifier: Notifier,
) -> list[tuple[int, str, int, bool]]:
    """For each scheduled proposal whose slot has passed and is not yet verified,
    fetch the canonical block header at that slot. If the proposer_index matches our
    validator, the block landed; otherwise it was missed (or reorged out).
    Returns [(idx, label, slot, produced)].
    """
    results: list[tuple[int, str, int, bool]] = []
    vstate = state.setdefault("validators", {})
    for idx_str, record in vstate.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        label = record.get("label", "")
        for prop in record.get("scheduled_proposals", []):
            if prop.get("verified"):
                continue
            slot = int(prop.get("slot", -1))
            if slot >= current_slot:
                continue
            try:
                proposer_index = fetch_header_fn(slot)
            except Exception:
                # Leave unverified; we'll retry on the next check run.
                continue
            produced = proposer_index == idx
            label_part = f" {label}" if label else ""
            # Keep titles ASCII — ntfy delivers the Title via an HTTP header
            # which httpx ASCII-encodes; non-ASCII in the title silently fails.
            if produced:
                notifier.send(
                    f"validator {idx}{label_part} proposed slot {slot}",
                    f"✓ block landed at slot {slot}",
                )
            else:
                notifier.send(
                    f"validator {idx}{label_part} missed proposal at slot {slot}",
                    f"✗ no block produced at slot {slot}",
                )
            prop["verified"] = True
            prop["produced"] = produced
            results.append((idx, label, slot, produced))
    return results


def prune_scheduled_proposals(
    state: dict,
    current_slot: int,
    keep_slots: int = 1000,
) -> int:
    """Drop verified proposals whose slot is older than `current_slot - keep_slots`.
    Unverified proposals are always kept (they need outcome verification).
    Returns the total number of entries removed.
    """
    if current_slot <= keep_slots:
        return 0
    threshold = current_slot - keep_slots
    removed = 0
    for record in state.get("validators", {}).values():
        proposals = record.get("scheduled_proposals", [])
        if not proposals:
            continue
        kept = [
            p for p in proposals
            if not (p.get("verified") and int(p.get("slot", 0)) < threshold)
        ]
        removed += len(proposals) - len(kept)
        record["scheduled_proposals"] = kept
    return removed


def process_withdrawals(
    state: dict,
    configured_indices: set[int],
    notifier: Notifier,
    cfg: AlertsConfig,
    current_slot: int = 0,
) -> list[tuple[int, str, int]]:
    """Detect balance drops on active validators and notify.

    Compares `previous_balance_gwei` (set by poll() before overwriting last_balance_gwei)
    to the current `last_balance_gwei`. A drop >= cfg.withdrawal_threshold_gwei on an
    active_ongoing validator is treated as a withdrawal.

    If the two balance snapshots are >cfg.withdrawal_max_gap_slots apart (recorded via
    previous_balance_at_slot / last_balance_at_slot), the drop is ambiguous — it could
    be cumulative attestation losses over many epochs of downtime — and we skip alerting.
    Passing current_slot=0 (or omitting it) disables the gap check entirely; that path
    is intended only for tests.

    Returns [(idx, label, drop_gwei)] for alerts that fired.
    """
    detected: list[tuple[int, str, int]] = []
    vstate = state.get("validators", {})
    for idx_str, record in vstate.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if idx not in configured_indices:
            continue
        prev = record.get("previous_balance_gwei")
        curr = record.get("last_balance_gwei")
        status = record.get("last_status", "")
        if prev is None or curr is None:
            continue
        if status != "active_ongoing":
            continue
        prev_i = int(prev)
        curr_i = int(curr)
        if prev_i <= curr_i:
            continue
        drop = prev_i - curr_i
        if drop < cfg.withdrawal_threshold_gwei:
            continue
        # Gap check: refuse to attribute drops to a "withdrawal" when too many
        # slots passed between the two snapshots (could be attestation losses).
        if current_slot > 0 and cfg.withdrawal_max_gap_slots > 0:
            prev_slot = record.get("previous_balance_at_slot")
            if prev_slot is not None and current_slot - int(prev_slot) > cfg.withdrawal_max_gap_slots:
                continue
        label = record.get("label", "")
        detected.append((idx, label, drop))
        eth = drop / 1_000_000_000
        label_part = f" {label}" if label else ""
        notifier.send(
            f"validator {idx}{label_part} withdrawal",
            f"{eth:.4f} ETH withdrawn",
        )
    return detected
