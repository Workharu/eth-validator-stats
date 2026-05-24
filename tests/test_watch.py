from __future__ import annotations

import logging
import threading

from eth_validator_stats._watch import watch_loop


def test_watch_loop_runs_until_stop_is_set():
    stop = threading.Event()
    calls: list[int] = []

    def iteration() -> int:
        calls.append(1)
        if len(calls) >= 3:
            stop.set()
        return 0

    watch_loop(iteration, interval_seconds=0, stop_event=stop)

    assert len(calls) == 3


def test_watch_loop_exits_immediately_if_stop_already_set():
    stop = threading.Event()
    stop.set()
    calls: list[int] = []

    def iteration() -> int:
        calls.append(1)
        return 0

    watch_loop(iteration, interval_seconds=0, stop_event=stop)

    assert calls == []


def test_watch_loop_swallows_iteration_exception_and_continues(caplog):
    stop = threading.Event()
    calls: list[int] = []

    def iteration() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        if len(calls) >= 2:
            stop.set()
        return 0

    with caplog.at_level(logging.ERROR, logger="eth_validator_stats._watch"):
        watch_loop(iteration, interval_seconds=0, stop_event=stop)

    assert len(calls) == 2
    assert any(
        "watch iteration failed" in rec.message and rec.levelno == logging.ERROR
        for rec in caplog.records
    )


def test_watch_loop_respects_interval(monkeypatch):
    """stop_event.wait(interval) is the sleep mechanism; verify it is called."""
    stop = threading.Event()
    waits: list[float] = []

    real_wait = stop.wait

    def spy_wait(timeout=None):
        waits.append(timeout if timeout is not None else -1.0)
        # First call: don't actually wait — set stop so we exit on next check.
        stop.set()
        return real_wait(0)

    monkeypatch.setattr(stop, "wait", spy_wait)

    def iteration() -> int:
        return 0

    watch_loop(iteration, interval_seconds=42.0, stop_event=stop)

    assert waits == [42.0]
