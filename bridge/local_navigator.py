"""Local Navigator — minimal navigation for disconnected mode.

NOT a replacement for Nav2 or SLAM. This is survival mode:
- Move toward a target point at low speed
- Stop if obstacle detected
- Respect zone restrictions from cache
- Give up after timeout

Used by LocalBehaviorManager for return_to_base, seek_wifi, continue_patrol.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class LocalNavigator:
    """Simple point-to-point navigation with obstacle avoidance.

    Does NOT do path planning. Drives straight toward target,
    stops for obstacles, retries after pause.
    """

    MAX_SPEED_MPS = 0.3       # very slow — safety first in offline mode
    OBSTACLE_STOP_M = 0.5     # stop if anything closer than 0.5m
    OBSTACLE_WAIT_S = 3.0     # wait 3s then retry
    ARRIVAL_THRESHOLD_M = 0.3 # close enough to target
    TIMEOUT_S = 120.0         # give up after 2 minutes
    TICK_HZ = 2.0             # control loop rate

    def __init__(self, adapter, safety_gate=None, event_buffer=None):
        self._adapter = adapter
        self._safety = safety_gate  # LocalBrain._pipeline or SafetyPipeline
        self._event_buffer = event_buffer

    def navigate_to(
        self,
        target_x: float,
        target_y: float,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        """Navigate to (target_x, target_y). Returns result string.

        Results: ARRIVED, TIMEOUT, OBSTACLE, SAFETY_DENY, CANCELLED
        """
        start_time = time.time()
        tick_interval = 1.0 / self.TICK_HZ

        logger.info("LocalNavigator: heading to (%.1f, %.1f) at max %.1f m/s",
                    target_x, target_y, self.MAX_SPEED_MPS)

        last_entities = []

        while True:
            # Check cancellation
            if stop_event and stop_event.is_set():
                self._adapter.stop()
                return "CANCELLED"

            # Check timeout
            if time.time() - start_time > self.TIMEOUT_S:
                self._adapter.stop()
                logger.warning("LocalNavigator: timeout after %.0fs", self.TIMEOUT_S)
                return "TIMEOUT"

            # Get current state
            try:
                state = self._adapter.get_state()
            except Exception:
                self._adapter.stop()
                time.sleep(1)
                continue

            pos = state.get("position", {})
            current_x = pos.get("x", 0.0)
            current_y = pos.get("y", 0.0)

            # Check arrival
            dx = target_x - current_x
            dy = target_y - current_y
            distance = math.sqrt(dx * dx + dy * dy)

            if distance < self.ARRIVAL_THRESHOLD_M:
                self._adapter.stop()
                logger.info("LocalNavigator: ARRIVED at (%.1f, %.1f)", target_x, target_y)
                return "ARRIVED"

            # Check obstacles
            try:
                last_entities = self._adapter.get_entities()
                nearest = 999.0
                for e in last_entities:
                    d = e.get("distance_m", 999.0)
                    if d < nearest:
                        nearest = d
                if nearest < self.OBSTACLE_STOP_M:
                    self._adapter.stop()
                    logger.info("LocalNavigator: obstacle at %.1fm, waiting %.0fs",
                               nearest, self.OBSTACLE_WAIT_S)
                    if self._event_buffer:
                        self._event_buffer.write_event("nav_obstacle", {
                            "distance_m": nearest,
                            "position": {"x": current_x, "y": current_y},
                        })
                    # Wait and hope obstacle moves
                    for _ in range(int(self.OBSTACLE_WAIT_S / 0.5)):
                        if stop_event and stop_event.is_set():
                            return "CANCELLED"
                        time.sleep(0.5)
                    continue
            except Exception:
                pass

            # Safety check
            if self._safety:
                try:
                    cmd = {"vx": self.MAX_SPEED_MPS, "vy": 0, "wz": 0}
                    vx, vy, wz, gate = self._safety.check(
                        cmd["vx"], cmd["vy"], cmd["wz"],
                        state, last_entities,
                    )
                    if gate.decision == "DENY":
                        self._adapter.stop()
                        logger.warning("LocalNavigator: safety DENY: %s", gate.reason)
                        return "SAFETY_DENY"
                except Exception:
                    pass

            # Compute velocity toward target
            angle = math.atan2(dy, dx)
            heading = state.get("heading_rad", 0.0)

            # Simple: if facing roughly right direction, go forward
            angle_diff = angle - heading
            # Normalize to [-pi, pi]
            while angle_diff > math.pi:
                angle_diff -= 2 * math.pi
            while angle_diff < -math.pi:
                angle_diff += 2 * math.pi

            if abs(angle_diff) > 0.3:  # ~17 degrees — need to turn first
                wz = 0.3 if angle_diff > 0 else -0.3
                self._adapter.send_velocity(0.0, 0.0, wz)
            else:
                # Go forward
                speed = min(self.MAX_SPEED_MPS, distance * 0.5)  # slow down near target
                self._adapter.send_velocity(speed, 0.0, 0.0)

            time.sleep(tick_interval)

        # Should not reach here
        self._adapter.stop()
        return "UNKNOWN"
