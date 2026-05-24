from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Protocol

import httpx


@dataclass(frozen=True)
class AlertsConfig:
    ntfy_topic: str = ""
    cooldown_minutes: int = 30
    storm_threshold: int = 10
    request_timeout_s: float = 5.0
    missed_attestations_threshold: int = 2
    withdrawal_threshold_gwei: int = 1_000_000  # 0.001 ETH — skim noise below this
    proposal_lookahead_epochs: int = 1


class Notifier(Protocol):
    def send(self, title: str, body: str) -> None: ...


class NullNotifier:
    def send(self, title: str, body: str) -> None:  # noqa: ARG002
        return


class NtfyNotifier:
    def __init__(self, topic_url: str, *, timeout: float = 5.0, transport: httpx.BaseTransport | None = None) -> None:
        self.topic_url = topic_url
        self.timeout = timeout
        self._transport = transport

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
            sys.stderr.write(f"warning: ntfy notify failed: {e}\n")


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

    for idx, label in recoveries:
        label_part = f" {label}" if label else ""
        notifier.send(f"validator {idx}{label_part} RECOVERED", "back to active_ongoing")

    return new_alerts, recoveries


HeaderFetcher = Callable[[int], "int | None"]


def record_scheduled_proposals(
    state: dict,
    duties: list[tuple[int, int]],
    configured_indices: set[int],
) -> None:
    """Persist upcoming proposer duties into per-validator state. Idempotent."""
    vstate = state.setdefault("validators", {})
    for slot, validator_index in duties:
        if validator_index not in configured_indices:
            continue
        record = vstate.setdefault(str(validator_index), {})
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
    """Notify once per scheduled proposal that falls within the lookahead window."""
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
            if slot < current_slot or slot > current_slot + window_slots:
                continue
            if prop.get("alerted"):
                continue
            slots_away = slot - current_slot
            mins_away = (slots_away * seconds_per_slot) // 60
            label_part = f" {label}" if label else ""
            notifier.send(
                f"validator {idx}{label_part} proposing soon",
                f"slot {slot} (~{mins_away} min away)",
            )
            prop["alerted"] = True
            fired.append((idx, label, slot))
    return fired


def process_proposal_outcomes(
    state: dict,
    current_slot: int,
    fetch_header_fn: HeaderFetcher,
    notifier: Notifier,
) -> list[tuple[int, str, int, bool]]:
    """For each past, unverified scheduled proposal, query the canonical header
    and notify success or miss. Mark verified."""
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
                continue
            produced = proposer_index == idx
            label_part = f" {label}" if label else ""
            if produced:
                notifier.send(
                    f"validator {idx}{label_part} ✓ proposed slot {slot}",
                    "block landed",
                )
            else:
                notifier.send(
                    f"validator {idx}{label_part} ✗ missed proposal at slot {slot}",
                    "no block produced at this slot",
                )
            prop["verified"] = True
            prop["produced"] = produced
            results.append((idx, label, slot, produced))
    return results


def process_withdrawals(
    state: dict,
    configured_indices: set[int],
    notifier: Notifier,
    cfg: AlertsConfig,
) -> list[tuple[int, str, int]]:
    """Detect balance drops on active validators and notify.

    Compares `previous_balance_gwei` (set by poll() before overwriting last_balance_gwei)
    to the current `last_balance_gwei`. A drop >= cfg.withdrawal_threshold_gwei on an
    active_ongoing validator is treated as a withdrawal. Returns [(idx, label, drop_gwei)].
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
        label = record.get("label", "")
        detected.append((idx, label, drop))
        eth = drop / 1_000_000_000
        label_part = f" {label}" if label else ""
        notifier.send(
            f"validator {idx}{label_part} withdrawal",
            f"{eth:.4f} ETH withdrawn",
        )
    return detected
