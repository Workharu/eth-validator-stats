from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


def watch_loop(
    iteration_fn: Callable[[], int],
    interval_seconds: float,
    stop_event: threading.Event,
) -> int:
    """Run iteration_fn repeatedly until stop_event is set.

    Each iteration is wrapped in a try/except: failures are logged at ERROR
    with traceback and the loop continues to the next iteration. This is
    deliberate — transient beacon-node outages already surface through the
    MONITOR_BLIND alert path; we don't want the OS supervisor to restart the
    process for a single failed scan.

    Returns 0 on clean shutdown.
    """
    while not stop_event.is_set():
        try:
            iteration_fn()
        except Exception:
            logger.exception("watch iteration failed")
        stop_event.wait(interval_seconds)
    return 0


def _install_signal_handlers(stop_event: threading.Event) -> None:
    """Map SIGTERM (systemd stop), SIGINT (Ctrl-C), and SIGBREAK (Windows
    Ctrl-Break, sent by WinSW) onto stop_event.set().

    The handler must be async-signal-safe. The Python logging module
    isn't (it can deadlock if a signal fires mid-flush inside a
    handler's lock), so we only set the event in the handler and let
    the main thread log the "received signal N" message when
    watch_loop notices the event is set.
    """
    def handler(signum: int, frame) -> None:
        # Stash the signal number for the main thread to log on next wake.
        stop_event.signum = signum  # type: ignore[attr-defined]
        stop_event.set()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, handler)


def cmd_watch(args: argparse.Namespace) -> int:
    """`eth-validator-stats watch` entry point. Loops run_check_once until signalled."""
    # Local import to avoid a circular import (cli imports _watch).
    from .cli import run_check_once

    stop = threading.Event()
    _install_signal_handlers(stop)

    interval = float(args.interval)
    logger.info("watch starting (interval=%.0fs)", interval)
    rc = watch_loop(lambda: run_check_once(args), interval_seconds=interval, stop_event=stop)
    # Log the signal number now (out of signal-handler context) — see
    # _install_signal_handlers docstring for why this is deferred.
    signum = getattr(stop, "signum", None)
    if signum is not None:
        logger.info("received signal %d, shutdown complete", signum)
    logger.info("watch stopped")
    return rc
