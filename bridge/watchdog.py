"""
Edge-side 200 ms / 800 ms watchdog for robot_bridge.

Mirrors safety_edge/watchdog.py — same parameters, same semantics.
If the upstream system (operator_ui / sim_dashboard / Core) stops sending
heartbeats for 800 ms, the watchdog fires SAFE_FALLBACK: the bridge must
stop the robot and refuse new move commands until heartbeat is restored.

Heartbeat is registered on every incoming /robot/move or /robot/heartbeat call.
Watchdog runs in a daemon background thread that polls every 200 ms.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_MS = 200   # expected cadence from upstream
TIMEOUT_MS = 800              # 4 × 200 ms → SAFE_FALLBACK
GRACE_MS = 10_000             # 10 s startup grace period


class EdgeWatchdog:
    """Thread-safe watchdog — fires SAFE_FALLBACK on missed heartbeat."""

    def __init__(
        self,
        timeout_ms: int = TIMEOUT_MS,
        grace_ms: int = GRACE_MS,
        on_fallback=None,
        on_recover=None,
    ):
        self.timeout_ms = timeout_ms
        self.grace_ms = grace_ms
        self._on_fallback = on_fallback   # callable() — called on SAFE_FALLBACK
        self._on_recover = on_recover     # callable() — called on recovery
        self._last_beat_ms: int | None = None
        self._in_fallback: bool = False
        self._lock = threading.Lock()
        self._start_ms = int(time.monotonic() * 1000)
        self._thread: threading.Thread | None = None

    # ── public API ───────────────────────────────────────────────────────

    def heartbeat(self) -> None:
        """Register that upstream is alive. Call on every move/command."""
        now = int(time.monotonic() * 1000)
        with self._lock:
            was_fallback = self._in_fallback
            self._last_beat_ms = now
            if was_fallback:
                self._in_fallback = False
        if was_fallback:
            logger.info("watchdog: heartbeat restored — leaving SAFE_FALLBACK")
            if self._on_recover:
                try:
                    self._on_recover()
                except Exception:
                    pass

    @property
    def in_fallback(self) -> bool:
        with self._lock:
            return self._in_fallback

    def start(self) -> None:
        """Start background polling thread (daemon)."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="bridge-watchdog"
        )
        self._thread.start()
        logger.info(
            "watchdog: started — timeout=%dms grace=%dms", self.timeout_ms, self.grace_ms
        )

    # ── internals ────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        interval_s = HEARTBEAT_INTERVAL_MS / 1000
        while True:
            time.sleep(interval_s)
            self._check()

    def _check(self) -> None:
        now = int(time.monotonic() * 1000)
        elapsed_since_boot = now - self._start_ms
        with self._lock:
            if elapsed_since_boot < self.grace_ms:
                return  # startup grace period
            last = self._last_beat_ms
            already_fallback = self._in_fallback

        if last is None:
            age_ms = elapsed_since_boot
        else:
            age_ms = now - last

        if age_ms >= self.timeout_ms and not already_fallback:
            with self._lock:
                self._in_fallback = True
            logger.warning(
                "watchdog: SAFE_FALLBACK — no heartbeat for %d ms (threshold %d ms)",
                age_ms, self.timeout_ms,
            )
            if self._on_fallback:
                try:
                    self._on_fallback()
                except Exception:
                    pass

    def status(self) -> dict:
        with self._lock:
            last = self._last_beat_ms
            fb = self._in_fallback
        now = int(time.monotonic() * 1000)
        age = (now - last) if last is not None else None
        return {
            "in_fallback": fb,
            "last_beat_age_ms": age,
            "timeout_ms": self.timeout_ms,
        }
