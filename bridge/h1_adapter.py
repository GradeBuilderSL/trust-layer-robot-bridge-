"""H1 adapter — Unitree H1 humanoid robot via HTTP REST API.

Connects to the h1_server.py running on H1's onboard PC (or a nearby laptop
with SDK2 installed). Maps H1-specific API to Trust Layer's standard adapter
interface.

Key differences from N2 (wheeled AMR):
  - No true lateral movement (vy clamped to 0)
  - Tighter tilt limits (humanoid falls at ~25°)
  - Extra commands: stand_up, lie_down, wave, gesture
  - IMU-based position estimation (no wheel odometry)
  - Default API port: 8081 (configurable via ROBOT_URL env var)

Usage:
    ADAPTER_TYPE=h1 ROBOT_URL=http://192.168.123.1:8081 python -m bridge.main
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from bridge.adapter_base import RobotAdapter

logger = logging.getLogger(__name__)


class H1Adapter(RobotAdapter):
    """Adapter for Unitree H1 humanoid robot.

    Speaks to h1_server.py REST API, which bridges Unitree SDK2 → HTTP.
    Falls back gracefully when robot is unreachable (returns error state).
    """

    MAX_SPEED    = 1.2   # m/s  — H1 walking speed
    MAX_ANGULAR  = 2.0   # rad/s
    MAX_TILT_DEG = 25.0  # degrees — humanoid falls at ~30°

    def __init__(self, robot_url: str = "http://192.168.123.1:8081") -> None:
        self.robot_url = robot_url.rstrip("/")
        self.connected = False
        self.name = "h1"
        self._last_error = ""
        self._timeout = 1.5

    # ── Standard adapter interface ────────────────────────────────────────

    def get_state(self) -> dict:
        """Read H1 state → standard Trust Layer format."""
        data = self._get("/api/state")
        if data is None:
            return self._error_state()
        self.connected = True
        return {
            "position": {
                "x": float(data.get("pos_x", 0)),
                "y": float(data.get("pos_y", 0)),
                "z": float(data.get("pos_z", 0)),   # height above ground
            },
            "velocity": {
                "vx": float(data.get("vx", 0)),
                "vy": 0.0,   # H1 doesn't strafe
                "vz": 0.0,
            },
            "heading_rad": float(data.get("yaw_rad", 0)),
            "speed_mps":   float(data.get("speed_mps", 0)),
            "battery":     float(data.get("battery_pct", 100)),
            "tilt_deg":    float(data.get("pitch_deg", 0)),   # forward lean
            "temperature_c": float(data.get("motor_temp_c", 30)),
            "mode":  data.get("mode", "ADVISORY"),
            "gait":  data.get("gait", "WALK"),        # WALK | STAND | SIT
            "timestamp_s": time.time(),
            "adapter": "h1",
            "sensors": {
                "camera": {
                    "health": float(data.get("camera_ok", 1)),
                    "available": bool(data.get("camera_ok", True)),
                },
                "imu": {
                    "health": float(data.get("imu_ok", 1)),
                    "available": bool(data.get("imu_ok", True)),
                },
                "lidar": {"health": 0.0, "available": False},
            },
        }

    def send_velocity(self, vx: float, vy: float, wz: float) -> dict:
        """Send walking command. vy is ignored (H1 doesn't strafe)."""
        vx = max(-self.MAX_SPEED, min(self.MAX_SPEED, vx))
        wz = max(-self.MAX_ANGULAR, min(self.MAX_ANGULAR, wz))
        result = self._post("/api/cmd/walk", {"vx": round(vx, 3), "vyaw": round(wz, 3)})
        if result is None:
            return {"status": "error", "error": self._last_error}
        return {"status": "ok", "adapter": "h1"}

    def stop(self) -> dict:
        result = self._post("/api/cmd/stop", {})
        if result is None:
            return {"status": "error", "error": self._last_error}
        return {"status": "stopped", "adapter": "h1"}

    def get_entities(self) -> list[dict]:
        data = self._get("/api/perception/entities")
        if data is None:
            return []
        return data.get("entities", [])

    def inject_scenario(self, overrides: dict) -> None:
        self._post("/api/sim/context", overrides)

    def clear_scenario(self) -> None:
        self._post("/api/sim/context", {})

    # ── H1-specific commands ──────────────────────────────────────────────

    def stand_up(self) -> dict:
        """Transition from sit/lie to standing."""
        result = self._post("/api/cmd/stand_up", {})
        return result or {"status": "error", "adapter": "h1"}

    def lie_down(self) -> dict:
        """Lie down (safe shutdown posture)."""
        result = self._post("/api/cmd/lie_down", {})
        return result or {"status": "error", "adapter": "h1"}

    def gesture(self, name: str) -> dict:
        """Trigger a named gesture animation.

        Supported gestures (depends on firmware):
            wave, nod, shake_head, bow, point_left, point_right, hello
        """
        result = self._post("/api/cmd/gesture", {"name": name})
        return result or {"status": "error", "adapter": "h1"}

    def set_gait(self, gait: str) -> dict:
        """Switch gait mode: WALK | TROT | STAND."""
        result = self._post("/api/cmd/gait", {"gait": gait})
        return result or {"status": "error", "adapter": "h1"}

    def speak(self, text: str, lang: str = "ru") -> dict:
        """Send TTS text to H1's onboard speaker."""
        result = self._post("/api/audio/speak", {"text": text, "lang": lang})
        return result or {"status": "error", "adapter": "h1"}

    def capture_photo(self) -> dict:
        """Capture image from H1 head camera. Returns base64 JPEG."""
        result = self._get("/api/camera/capture")
        return result or {"status": "error", "adapter": "h1"}

    def probe_capabilities(self) -> dict:
        """Hardware capability probe.

        Asks h1_server /api/capabilities and normalises the answer into
        the shape the Trust Layer expects (same keys as HttpAdapter /
        E1Adapter). Falls back to a disconnected skeleton when the
        server is unreachable, so the adapter never lies about what's
        available.

        Key facts about H1 hardware:
          * No lidar — pinned to ``not_installed`` regardless of what
            the server reports, same policy as E1.
          * Camera + IMU are standard on every H1 build.
          * Speaker depends on firmware — surfaced from the server.
        """
        data = self._get("/api/capabilities") or {}
        default = {
            "camera":     {"available": True,  "probe": "ok"},
            "lidar":      {"available": False, "probe": "not_installed",
                           "note": "Unitree H1 ships with stereo cameras only, no lidar"},
            "imu":        {"available": True,  "probe": "ok"},
            "microphone": {"available": False, "probe": "not_available"},
            "speaker":    {"available": False, "probe": "not_available"},
            "drive":      {"available": True,  "probe": "ok",
                           "note": "bipedal locomotion"},
            "battery":    {"available": True,  "probe": "ok"},
            "network":    {"available": self.connected, "probe": "ok" if self.connected else "disconnected"},
        }
        # Overlay whatever the server actually reported.
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    default[key] = value
        # H1 physically has no lidar — override any server claim.
        default["lidar"] = {
            "available": False,
            "probe": "not_installed",
            "note": "Unitree H1 has stereo cameras only, no lidar",
        }
        return default

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _get(self, path: str) -> dict | None:
        try:
            req = urllib.request.Request(
                f"{self.robot_url}{path}", method="GET"
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            self._last_error = str(exc)
            self.connected = False
            logger.debug("H1 GET %s failed: %s", path, exc)
            return None

    def _post(self, path: str, data: dict) -> dict | None:
        try:
            body = json.dumps(data).encode()
            req = urllib.request.Request(
                f"{self.robot_url}{path}",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            self._last_error = str(exc)
            self.connected = False
            logger.debug("H1 POST %s failed: %s", path, exc)
            return None

    def _error_state(self) -> dict:
        return {
            "position": {"x": 0, "y": 0, "z": 0},
            "velocity": {"vx": 0, "vy": 0, "vz": 0},
            "heading_rad": 0, "speed_mps": 0,
            "battery": 0, "tilt_deg": 0, "temperature_c": 0,
            "mode": "UNKNOWN", "timestamp_s": time.time(),
            "adapter": "h1", "error": self._last_error,
            "sensors": {},
        }
