"""Local Behavior Manager — configurable autonomous behavior when server disconnected.

Replaces the simple adapter.stop() in watchdog with intelligent behavior:
- STOP_AND_WAIT: current behavior (just stop)
- RETURN_TO_BASE: navigate to known safe position
- HOLD_INTERACTIVE: stop but continue answering questions from cache
- SEEK_CONNECTIVITY: slowly move toward known WiFi zones
- CONTINUE_PATROL: finish current patrol route from cache

Configured via profession pack or environment variables.
"""
from __future__ import annotations

import enum
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DisconnectedBehavior(enum.Enum):
    STOP_AND_WAIT = "stop"
    RETURN_TO_BASE = "return_base"
    HOLD_INTERACTIVE = "hold_interactive"
    SEEK_CONNECTIVITY = "seek_wifi"
    CONTINUE_PATROL = "patrol"


class LocalBehaviorManager:
    """Manages robot behavior when server connection is lost.

    Integrates with:
    - EdgeWatchdog (on_fallback callback)
    - LocalBrain (Q&A, FSM)
    - LocalNavigator (return to base)
    - EventBuffer (audit logging)
    """

    def __init__(self, adapter, brain=None, event_buffer=None):
        self._adapter = adapter
        self._brain = brain
        self._event_buffer = event_buffer
        self._navigator = None  # set later via set_navigator()

        # Configuration (from env or profession pack)
        behavior_str = os.environ.get("DISCONNECTED_BEHAVIOR", "stop")
        try:
            self._behavior = DisconnectedBehavior(behavior_str)
        except ValueError:
            self._behavior = DisconnectedBehavior.STOP_AND_WAIT

        self._base_position = self._parse_position(
            os.environ.get("BASE_POSITION", "")
        )
        self._wifi_zones = self._parse_wifi_zones(
            os.environ.get("WIFI_ZONES", "")
        )
        self._patrol_route: List[Dict[str, float]] = []

        # State
        self._disconnected = False
        self._disconnect_time: float = 0
        self._behavior_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def set_navigator(self, navigator):
        """Set local navigator (injected after creation to break circular deps)."""
        self._navigator = navigator

    def configure_from_profession(self, config: dict):
        """Update behavior config from profession pack YAML."""
        if "disconnected_behavior" in config:
            try:
                self._behavior = DisconnectedBehavior(config["disconnected_behavior"])
            except ValueError:
                pass
        if "base_position" in config:
            pos = config["base_position"]
            self._base_position = {"x": float(pos.get("x", 0)), "y": float(pos.get("y", 0))}
        if "wifi_zones" in config:
            self._wifi_zones = [
                {"x": float(z.get("x", 0)), "y": float(z.get("y", 0)),
                 "radius": float(z.get("radius", 2.0))}
                for z in config["wifi_zones"]
            ]
        logger.info("LocalBehavior configured: behavior=%s base=%s wifi_zones=%d",
                    self._behavior.value, self._base_position, len(self._wifi_zones))

    def cache_patrol_route(self, route: List[Dict[str, float]]):
        """Cache current patrol route from bt_executor for offline continuation."""
        with self._lock:
            self._patrol_route = list(route)
        logger.debug("Cached patrol route: %d waypoints", len(route))

    # ── Main callbacks (called by watchdog) ────────────────────────────

    def on_disconnect(self):
        """Called by watchdog when heartbeat lost. Starts local behavior."""
        with self._lock:
            if self._disconnected:
                return
            self._disconnected = True
            self._disconnect_time = time.time()
            self._stop_event.clear()

        logger.warning("CONNECTION LOST → switching to local behavior: %s",
                      self._behavior.value)

        if self._event_buffer:
            self._event_buffer.write_event("disconnect", {
                "behavior": self._behavior.value,
                "base_position": self._base_position,
                "timestamp": time.time(),
            })

        # Stop robot first (always safe)
        try:
            self._adapter.stop()
        except Exception as exc:
            logger.error("Failed to stop on disconnect: %s", exc)

        # Then start the configured behavior
        if self._behavior == DisconnectedBehavior.STOP_AND_WAIT:
            pass  # already stopped

        elif self._behavior == DisconnectedBehavior.RETURN_TO_BASE:
            if self._base_position and self._navigator:
                self._start_behavior_thread(self._do_return_to_base)
            else:
                logger.warning("RETURN_TO_BASE: no base_position or navigator configured")

        elif self._behavior == DisconnectedBehavior.HOLD_INTERACTIVE:
            # Robot stays put, but local brain answers questions
            logger.info("HOLD_INTERACTIVE: stopped, answering from cache")

        elif self._behavior == DisconnectedBehavior.SEEK_CONNECTIVITY:
            if self._wifi_zones and self._navigator:
                self._start_behavior_thread(self._do_seek_wifi)
            else:
                logger.warning("SEEK_WIFI: no wifi_zones or navigator configured")

        elif self._behavior == DisconnectedBehavior.CONTINUE_PATROL:
            if self._patrol_route and self._navigator:
                self._start_behavior_thread(self._do_continue_patrol)
            else:
                logger.warning("CONTINUE_PATROL: no patrol route cached")

    def on_reconnect(self):
        """Called when connectivity restored."""
        with self._lock:
            was_disconnected = self._disconnected
            self._disconnected = False
            duration = time.time() - self._disconnect_time if self._disconnect_time else 0

        self._stop_event.set()  # stop any running behavior thread

        if was_disconnected:
            logger.info("CONNECTION RESTORED after %.1f sec → returning to server control",
                       duration)
            if self._event_buffer:
                self._event_buffer.write_event("reconnect", {
                    "duration_sec": round(duration, 1),
                    "behavior": self._behavior.value,
                    "timestamp": time.time(),
                })

    @property
    def is_disconnected(self) -> bool:
        with self._lock:
            return self._disconnected

    def status(self) -> dict:
        with self._lock:
            return {
                "disconnected": self._disconnected,
                "behavior": self._behavior.value,
                "base_position": self._base_position,
                "wifi_zones_count": len(self._wifi_zones),
                "patrol_route_cached": len(self._patrol_route),
                "disconnect_duration": round(time.time() - self._disconnect_time, 1)
                    if self._disconnected else 0,
            }

    # ── Behavior implementations ───────────────────────────────────────

    def _start_behavior_thread(self, target):
        self._behavior_thread = threading.Thread(
            target=target, daemon=True, name="local-behavior"
        )
        self._behavior_thread.start()

    def _do_return_to_base(self):
        """Navigate to base position at low speed."""
        logger.info("Returning to base: %s", self._base_position)
        result = self._navigator.navigate_to(
            self._base_position["x"], self._base_position["y"],
            stop_event=self._stop_event,
        )
        logger.info("Return to base result: %s", result)
        if self._event_buffer:
            self._event_buffer.write_event("local_navigation", {
                "target": "base", "result": result
            })

    def _do_seek_wifi(self):
        """Move toward nearest known WiFi zone."""
        if not self._wifi_zones:
            return
        # Pick nearest zone
        state = self._adapter.get_state()
        rx = state.get("position", {}).get("x", 0)
        ry = state.get("position", {}).get("y", 0)

        nearest = min(self._wifi_zones,
                     key=lambda z: (z["x"] - rx) ** 2 + (z["y"] - ry) ** 2)
        logger.info("Seeking WiFi zone at (%.1f, %.1f)", nearest["x"], nearest["y"])
        result = self._navigator.navigate_to(
            nearest["x"], nearest["y"],
            stop_event=self._stop_event,
        )
        if self._event_buffer:
            self._event_buffer.write_event("local_navigation", {
                "target": "wifi_zone", "result": result
            })

    def _do_continue_patrol(self):
        """Continue cached patrol route."""
        with self._lock:
            route = list(self._patrol_route)
        waypoints_completed = 0
        for i, waypoint in enumerate(route):
            if self._stop_event.is_set():
                break
            logger.info("Patrol waypoint %d/%d: (%.1f, %.1f)",
                       i + 1, len(route), waypoint["x"], waypoint["y"])
            result = self._navigator.navigate_to(
                waypoint["x"], waypoint["y"],
                stop_event=self._stop_event,
            )
            waypoints_completed = i + 1
            if result != "ARRIVED":
                break
        if self._event_buffer:
            self._event_buffer.write_event("local_patrol", {
                "waypoints_completed": waypoints_completed, "total": len(route)
            })

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_position(s: str) -> Optional[Dict[str, float]]:
        """Parse 'x,y' string into dict."""
        if not s:
            return None
        parts = s.split(",")
        if len(parts) != 2:
            return None
        try:
            return {"x": float(parts[0]), "y": float(parts[1])}
        except ValueError:
            return None

    @staticmethod
    def _parse_wifi_zones(s: str) -> List[Dict[str, float]]:
        """Parse 'x1,y1,r1;x2,y2,r2' string into zone list."""
        if not s:
            return []
        zones = []
        for zone_str in s.split(";"):
            parts = zone_str.strip().split(",")
            if len(parts) == 3:
                try:
                    zones.append({
                        "x": float(parts[0]), "y": float(parts[1]),
                        "radius": float(parts[2])
                    })
                except ValueError:
                    pass
        return zones
