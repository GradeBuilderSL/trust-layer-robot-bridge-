"""Connectivity Monitor — detects workstation Wi-Fi loss and manages mode transitions.

Modes:
  CONNECTED    — workstation reachable, full Trust Layer functionality
  AUTONOMOUS   — workstation unreachable, local_brain handles everything
  RECONNECTING — workstation just came back, syncing before returning to CONNECTED

Transition logic (asymmetric):
  CONNECTED → AUTONOMOUS:   3 consecutive failed pings (6 seconds)
  AUTONOMOUS → RECONNECTING: 1 successful ping
  RECONNECTING → CONNECTED:  sync completes successfully

Usage:
    monitor = ConnectivityMonitor(
        workstation_url="http://192.168.1.100:8888",
        on_mode_change=lambda mode: print(f"Mode: {mode}"),
    )
    monitor.start()
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ConnectivityMonitor:
    """Monitors workstation reachability in a background daemon thread."""

    MODE_CONNECTED    = "CONNECTED"
    MODE_AUTONOMOUS   = "AUTONOMOUS"
    MODE_RECONNECTING = "RECONNECTING"

    def __init__(
        self,
        workstation_url: str,
        ping_interval_s: float = 2.0,
        fail_threshold: int = 3,
        on_mode_change: Callable[[str], None] | None = None,
        on_sync_needed: Callable[[], None] | None = None,
    ):
        self._url           = workstation_url.rstrip("/")
        self._interval      = ping_interval_s
        self._fail_threshold = fail_threshold
        self._on_mode_change = on_mode_change
        self._on_sync_needed = on_sync_needed

        self._mode          = self.MODE_CONNECTED
        self._fail_count    = 0
        self._lock          = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running       = False
        self._last_ping_ok  = True
        self._last_check_ts = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        """Start the monitor daemon thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="connectivity-monitor", daemon=True
        )
        self._thread.start()
        logger.info("ConnectivityMonitor started → pinging %s every %.1fs",
                    self._url, self._interval)

    def stop(self):
        """Stop the monitor thread (graceful)."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval * 2)

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def is_connected(self) -> bool:
        return self.mode == self.MODE_CONNECTED

    @property
    def is_autonomous(self) -> bool:
        return self.mode == self.MODE_AUTONOMOUS

    def status_dict(self) -> dict:
        with self._lock:
            return {
                "mode":          self._mode,
                "last_ping_ok":  self._last_ping_ok,
                "fail_count":    self._fail_count,
                "last_check_ts": self._last_check_ts,
            }

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _loop(self):
        while self._running:
            ok = self._ping()
            with self._lock:
                self._last_ping_ok  = ok
                self._last_check_ts = time.time()
                old_mode            = self._mode

                if ok:
                    self._fail_count = 0
                    if self._mode == self.MODE_AUTONOMOUS:
                        self._mode = self.MODE_RECONNECTING
                    elif self._mode == self.MODE_RECONNECTING:
                        self._mode = self.MODE_CONNECTED
                    # CONNECTED → CONNECTED: nothing changes
                else:
                    self._fail_count += 1
                    if (self._mode == self.MODE_CONNECTED
                            and self._fail_count >= self._fail_threshold):
                        self._mode = self.MODE_AUTONOMOUS

                new_mode = self._mode

            if new_mode != old_mode:
                logger.warning("ConnectivityMonitor: %s → %s", old_mode, new_mode)
                if self._on_mode_change:
                    try:
                        self._on_mode_change(new_mode)
                    except Exception as exc:
                        logger.error("on_mode_change callback failed: %s", exc)

                if new_mode == self.MODE_RECONNECTING and self._on_sync_needed:
                    try:
                        self._on_sync_needed()
                    except Exception as exc:
                        logger.error("on_sync_needed callback failed: %s", exc)

            time.sleep(self._interval)

    def _ping(self) -> bool:
        """Return True if workstation /health responds within 2s."""
        try:
            req = urllib.request.urlopen(
                f"{self._url}/health", timeout=2.0
            )
            req.close()
            return True
        except Exception:
            return False
