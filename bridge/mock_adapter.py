"""Mock robot adapter — simulates Noetix N2 telemetry without real hardware.

Used for development, demo, and testing when no physical robot is available.
Generates realistic telemetry data that responds to injected scenarios and
velocity commands.
"""
import math
import random
import time
import threading

from bridge.adapter_base import RobotAdapter


class MockAdapter(RobotAdapter):
    """Simulates a Noetix N2 robot with realistic physics."""

    MAX_SPEED = 0.8      # m/s
    MAX_ANGULAR = 1.0    # rad/s
    DECEL = 1.5          # m/s^2
    POLL_HZ = 10
    DT = 1.0 / POLL_HZ

    def __init__(self):
        self._lock = threading.Lock()
        # Robot state
        self._x = 0.0
        self._y = 0.0
        self._heading = 0.0  # radians
        self._vx = 0.0
        self._vy = 0.0
        self._wz = 0.0
        self._battery = 95.0
        self._tilt_deg = 0.0
        self._temperature = 28.0
        # Injected scenario overrides
        self._overrides: dict = {}
        # Simulated entities (humans, obstacles)
        self._entities: list[dict] = []
        # Timestamps
        self._start_time = time.time()
        self._last_cmd_time = 0.0
        # Command to execute
        self._cmd_vx = 0.0
        self._cmd_vy = 0.0
        self._cmd_wz = 0.0
        # Navigation target (set by navigate_to)
        self._nav_target_x: float | None = None
        self._nav_target_y: float | None = None
        self._nav_target_heading: float = 0.0
        self._nav_speed: float = 0.3
        # Status
        self.connected = True
        self.name = "mock"

    def get_state(self) -> dict:
        """Return current robot state (called at POLL_HZ)."""
        with self._lock:
            self._simulate_step()
            return {
                "position": {
                    "x": round(self._x, 3),
                    "y": round(self._y, 3),
                    "z": 0.0,
                },
                "velocity": {
                    "vx": round(self._vx, 3),
                    "vy": round(self._vy, 3),
                    "vz": 0.0,
                },
                "heading_rad": round(self._heading, 4),
                "speed_mps": round(math.hypot(self._vx, self._vy), 3),
                "battery": round(self._battery, 1),
                "tilt_deg": round(self._tilt_deg, 1),
                "temperature_c": round(self._temperature, 1),
                "mode": "ADVISORY",
                "timestamp_s": time.time(),
                "uptime_s": round(time.time() - self._start_time, 1),
                "adapter": "mock",
                "sensors": {
                    "camera": {"health": 0.95, "fps": 15},
                    "lidar": {"health": 0.0, "available": False},
                    "imu": {"health": 0.92, "tilt_deg": self._tilt_deg},
                },
            }

    def send_velocity(self, vx: float, vy: float, wz: float) -> dict:
        """Accept velocity command."""
        with self._lock:
            self._cmd_vx = max(-self.MAX_SPEED, min(self.MAX_SPEED, vx))
            self._cmd_vy = max(-self.MAX_SPEED, min(self.MAX_SPEED, vy))
            self._cmd_wz = max(-self.MAX_ANGULAR, min(self.MAX_ANGULAR, wz))
            self._last_cmd_time = time.time()
        return {"status": "ok", "adapter": "mock"}

    def stop(self) -> dict:
        """Emergency stop."""
        with self._lock:
            self._cmd_vx = 0.0
            self._cmd_vy = 0.0
            self._cmd_wz = 0.0
            self._vx = 0.0
            self._vy = 0.0
            self._wz = 0.0
        return {"status": "stopped", "adapter": "mock"}

    def inject_scenario(self, overrides: dict) -> None:
        """Inject scenario conditions (battery, tilt, entities, etc.)."""
        with self._lock:
            self._overrides.update(overrides)
            # Handle entity injection
            if "entities" in overrides:
                self._entities = overrides.pop("entities")
            if "battery" in overrides:
                self._battery = float(overrides["battery"])
            if "tilt_deg" in overrides:
                self._tilt_deg = float(overrides["tilt_deg"])

    def clear_scenario(self) -> None:
        """Reset to nominal state."""
        with self._lock:
            self._overrides.clear()
            self._entities.clear()
            self._battery = 95.0
            self._tilt_deg = 0.0
            self._cmd_vx = 0.0
            self._cmd_vy = 0.0
            self._cmd_wz = 0.0
            self._vx = 0.0
            self._vy = 0.0
            self._wz = 0.0

    def probe_capabilities(self) -> dict:
        """Return simulated hardware capability report (no real probing needed)."""
        with self._lock:
            bat = round(self._battery, 1)
        return {
            "camera": {
                "available": True, "health": 0.95, "fps": 15,
                "resolution": "1280x720", "probe": "ok", "latency_ms": 42,
                "has_preview": True, "note": "RGB head camera (simulated)",
            },
            "lidar": {
                "available": False, "health": 0.0,
                "probe": "not_installed", "note": "Lidar not installed on this unit",
            },
            "imu": {
                "available": True, "health": 0.92,
                "probe": "ok", "latency_ms": 2, "note": "6-axis IMU (pitch/roll/yaw)",
            },
            "microphone": {
                "available": True, "probe": "ok",
                "sample_rate": 16000, "channels": 1,
                "method": "client_stt", "note": "Web Speech API (client-side STT)",
            },
            "speaker": {
                "available": True, "probe": "ok",
                "method": "client_tts", "note": "Web Speech API (client-side TTS)",
            },
            "drive": {
                "available": True, "probe": "ok",
                "type": "holonomic", "max_speed_mps": 0.8,
                "note": "Omni-wheel drive — vx/vy/wz commands",
            },
            "battery": {
                "available": True, "probe": "ok",
                "level_pct": bat,
                "estimated_runtime_min": int(bat * 2.4),
                "note": "Li-Ion battery pack",
            },
            "network": {
                "available": True, "probe": "ok",
                "latency_ms": 1, "adapter": "mock",
                "note": "Local mock — no real network",
            },
        }

    def get_entities(self) -> list[dict]:
        """Return simulated scene entities."""
        with self._lock:
            result = []
            for e in self._entities:
                dx = e.get("x", 0) - self._x
                dy = e.get("y", 0) - self._y
                dist = math.hypot(dx, dy)
                result.append({
                    "entity_id": e.get("entity_id", "unknown"),
                    "class_name": e.get("class_name", "obstacle"),
                    "label": e.get("label", "Object"),
                    "position": [e.get("x", 0), e.get("y", 0), 0.0],
                    "distance_m": round(dist, 2),
                    "confidence": round(max(0.1, 1.0 - dist / 8.0), 2),
                    "is_human": e.get("class_name") == "person",
                    "safety_tags": e.get("safety_tags", []),
                })
            return result

    def navigate_to(
        self,
        x_m: float,
        y_m: float,
        heading_rad: float = 0.0,
        speed_mps: float = 0.3,
    ) -> dict:
        """Simulate navigate_to: drive toward (x_m, y_m) at given speed."""
        with self._lock:
            self._nav_target_x = float(x_m)
            self._nav_target_y = float(y_m)
            self._nav_target_heading = float(heading_rad)
            self._nav_speed = max(0.1, min(self.MAX_SPEED, float(speed_mps)))
            self._last_cmd_time = time.time()
        return {
            "status": "ok",
            "adapter": self.name,
            "target": {"x": x_m, "y": y_m},
            "speed_mps": speed_mps,
        }

    def get_lidar_scan(self) -> dict:
        """Return synthetic 2D LiDAR scan (36 rays, 10° resolution)."""
        ranges = [
            2.0 + 0.5 * math.sin(i * math.pi / 9) for i in range(36)
        ]
        return {
            "available": True,
            "source": "mock",
            "angle_min_rad": -math.pi,
            "angle_max_rad": math.pi,
            "angle_increment_rad": round(2 * math.pi / 36, 6),
            "ranges": [round(r, 3) for r in ranges],
            "range_min_m": 0.1,
            "range_max_m": 10.0,
            "timestamp_s": time.time(),
        }

    # ── internal ──────────────────────────────────────────────────────

    def _simulate_step(self):
        """Advance physics by one DT step."""
        # Navigate-to: compute velocity toward target
        if self._nav_target_x is not None:
            dx = self._nav_target_x - self._x
            dy = self._nav_target_y - self._y
            dist = math.hypot(dx, dy)
            if dist < 0.05:  # arrived
                self._nav_target_x = None
                self._nav_target_y = None
                self._cmd_vx = 0.0
                self._cmd_vy = 0.0
            else:
                # Drive in world frame, decompose to robot frame
                desired_angle = math.atan2(dy, dx)
                cos_h = math.cos(self._heading)
                sin_h = math.sin(self._heading)
                world_vx = (dx / dist) * self._nav_speed
                world_vy = (dy / dist) * self._nav_speed
                # Rotate to robot frame
                self._cmd_vx = world_vx * cos_h + world_vy * sin_h
                self._cmd_vy = -world_vx * sin_h + world_vy * cos_h
                # Steer toward target heading
                angle_err = math.atan2(
                    math.sin(desired_angle - self._heading),
                    math.cos(desired_angle - self._heading),
                )
                self._cmd_wz = max(-self.MAX_ANGULAR,
                                   min(self.MAX_ANGULAR, angle_err * 2.0))
            self._last_cmd_time = time.time()

        # Smooth velocity toward command (simple ramp)
        ramp = 0.3  # acceleration factor per tick
        self._vx += (self._cmd_vx - self._vx) * ramp
        self._vy += (self._cmd_vy - self._vy) * ramp
        self._wz += (self._cmd_wz - self._wz) * ramp

        # Auto-stop if no command for 2 seconds
        if time.time() - self._last_cmd_time > 2.0:
            self._cmd_vx = 0.0
            self._cmd_vy = 0.0
            self._cmd_wz = 0.0

        # Integrate position (world frame)
        cos_h = math.cos(self._heading)
        sin_h = math.sin(self._heading)
        dx = self._vx * cos_h - self._vy * sin_h
        dy = self._vx * sin_h + self._vy * cos_h
        self._x += dx * self.DT
        self._y += dy * self.DT
        self._heading += self._wz * self.DT
        # Normalize heading
        self._heading = math.atan2(
            math.sin(self._heading), math.cos(self._heading)
        )

        # Battery drain (slow)
        speed = math.hypot(self._vx, self._vy)
        self._battery -= (0.001 + speed * 0.003) * self.DT
        self._battery = max(0.0, self._battery)

        # Small sensor noise
        self._tilt_deg = self._overrides.get(
            "tilt_deg",
            random.gauss(0.5, 0.3),
        )
        self._temperature += random.gauss(0, 0.02)
