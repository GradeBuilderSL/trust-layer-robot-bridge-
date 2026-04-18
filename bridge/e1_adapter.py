"""E1 adapter — Noetix E1 humanoid robot via HTTP REST API.

Connects to e1_server.py running on E1's onboard Jetson Orin Nano Super
(default IP 192.168.55.101). Maps E1-specific API to Trust Layer's standard
adapter interface.

E1 hardware (from Noetix delivery doc):
  - 19–24 DOF humanoid, 1.4 m, 44.5 kg
  - Single arm DOF: 5  (gestures, teaching mode)
  - Single leg DOF: 6
  - Walking speed: ~0.7 m/s safe, up to 1.2 m/s in running mode
  - Cooling: local air cooling (motors heat up under load)
  - Voice: iFlytek Spark4.0Ultra, 6-mic array, wake word "小顽童"
  - Compute: Jetson Orin Nano Super (67 TOPS, EDU edition)
  - Motion control: RK3588S over CAN (1Mbps), EtherCAT — DO NOT SSH while moving

Mode flow (matches gamepad state machine):
  Disabled → Enabled → Preparation → Walking ↔ Running
                                       ↓
                                    Teaching

Usage:
    ADAPTER_TYPE=e1 ROBOT_URL=http://192.168.55.101:8083 python -m bridge.main
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from bridge.adapter_base import RobotAdapter, ProbeStatus

logger = logging.getLogger(__name__)


# E1-specific safety envelope. Tighter than H1 because E1 is lighter (44.5kg
# vs H1 47kg) but less stable in turns due to smaller foot polygon.
class E1Limits:
    MAX_SPEED_WALKING_MPS = 0.7
    MAX_SPEED_RUNNING_MPS = 1.2
    MAX_ANGULAR_RAD_S = 1.5
    MAX_TILT_DEG = 22.0          # falls past ~25°
    FALL_THRESHOLD_DEG = 30.0
    MIN_BATTERY_PCT = 15
    MAX_OBSTACLE_HEIGHT_M = 0.05  # delivery doc: cannot step above 5cm
    MAX_SLOPE_DEG = 25.0


# Gestures supported by E1 (from delivery-doc gamepad table). The strings
# below are passed to e1_server which maps them to the gamepad-equivalent
# preset action or to a teaching slot.
E1_GESTURES = {
    "handshake": "preset_a",
    "greet":     "preset_b",
    "wave":      "preset_b",
    "cheer":     "preset_y",
    "play_a":    "preset_a",
    "play_b":    "preset_b",
    "play_x":    "preset_x",
    "play_y":    "preset_y",
}

# Mode tokens accepted by /api/cmd/mode on the onboard server.
E1_MODES = {"disabled", "enabled", "preparation", "walking", "running", "teaching"}


class E1Adapter(RobotAdapter):
    """Adapter for Noetix E1 humanoid robot.

    Speaks to e1_server.py REST API on the onboard Jetson Orin Nano Super.
    Falls back gracefully when robot is unreachable (returns error state).
    """

    def __init__(self, robot_url: str = "http://192.168.55.101:8083") -> None:
        self.robot_url = robot_url.rstrip("/")
        self.connected = False
        self.name = "e1"
        self._last_error = ""
        self._timeout = 1.5
        self._current_mode = "unknown"

    # ── Standard adapter interface ────────────────────────────────────────

    def get_state(self) -> dict:
        data = self._get("/api/state")
        if data is None:
            return self._error_state()
        self.connected = True
        self._current_mode = data.get("mode_e1", self._current_mode)

        return {
            "position": {
                "x": float(data.get("pos_x", 0)),
                "y": float(data.get("pos_y", 0)),
                "z": float(data.get("pos_z", 0)),
            },
            "velocity": {
                "vx": float(data.get("vx", 0)),
                "vy": 0.0,            # E1 doesn't strafe
                "vz": 0.0,
            },
            "heading_rad":   float(data.get("yaw_rad", 0)),
            "speed_mps":     float(data.get("speed_mps", 0)),
            "battery":       float(data.get("battery_pct", 0)),
            "tilt_deg":      float(data.get("pitch_deg", 0)),
            "temperature_c": float(data.get("motor_temp_c", 30)),
            "mode":          data.get("trust_mode", "ADVISORY"),
            "mode_e1":       data.get("mode_e1", "unknown"),
            "gait":          data.get("gait", "STAND"),
            "transport":     data.get("transport", "unknown"),  # dds | ros2 | sim
            "robot_model":   "Noetix E1",
            "robot_id":      data.get("robot_id", "e1-01"),
            "name":          data.get("name", "Noetix E1"),
            "manufacturer":  "Noetix Robotics",
            "timestamp_s":   time.time(),
            "adapter":       "e1",
            "sensors": {
                "camera":     {"available": bool(data.get("camera_ok", True)),
                               "health": float(data.get("camera_ok", 1))},
                "imu":        {"available": bool(data.get("imu_ok", True)),
                               "health": float(data.get("imu_ok", 1))},
                "microphone": {"available": bool(data.get("mic_ok", True)),
                               "health": float(data.get("mic_ok", 1))},
                "speaker":    {"available": bool(data.get("speaker_ok", True)),
                               "health": float(data.get("speaker_ok", 1))},
                "lidar":      {"available": False, "health": 0.0},
            },
        }

    def get_entities(self) -> list[dict]:
        data = self._get("/api/perception/entities")
        if data is None:
            return []
        return data.get("entities", [])

    def send_velocity(self, vx: float, vy: float, wz: float) -> dict:
        # Clamp to walking-mode envelope by default. Running mode is opt-in
        # via /robot/action with action_type="mode" params={mode: running}.
        max_v = (
            E1Limits.MAX_SPEED_RUNNING_MPS
            if self._current_mode == "running"
            else E1Limits.MAX_SPEED_WALKING_MPS
        )
        vx = max(-max_v, min(max_v, vx))
        wz = max(-E1Limits.MAX_ANGULAR_RAD_S, min(E1Limits.MAX_ANGULAR_RAD_S, wz))

        result = self._post("/api/cmd/walk", {
            "vx": round(vx, 3),
            "vyaw": round(wz, 3),
        })
        if result is None:
            return {"status": "error", "adapter": "e1", "error": self._last_error}
        return {"status": "ok", "adapter": "e1", "applied": {"vx": vx, "wz": wz}}

    def stop(self) -> dict:
        result = self._post("/api/cmd/stop", {})
        if result is None:
            return {"status": "error", "adapter": "e1", "error": self._last_error}
        return {"status": "stopped", "adapter": "e1"}

    def probe_capabilities(self) -> dict:
        data = self._get("/api/capabilities")
        if data is None:
            return {
                "camera":     {"available": False, "probe": ProbeStatus.DISCONNECTED},
                "lidar":      {"available": False, "probe": ProbeStatus.NOT_INSTALLED,
                               "note": "E1 has no lidar"},
                "imu":        {"available": False, "probe": ProbeStatus.DISCONNECTED},
                "microphone": {"available": False, "probe": ProbeStatus.DISCONNECTED},
                "speaker":    {"available": False, "probe": ProbeStatus.DISCONNECTED},
                "drive":      {"available": False, "probe": ProbeStatus.DISCONNECTED},
                "battery":    {"available": False, "probe": ProbeStatus.DISCONNECTED},
                "network":    {"available": False, "probe": ProbeStatus.DISCONNECTED},
            }
        # E1 lacks lidar by hardware spec — pin to not_installed.
        data["lidar"] = {"available": False, "probe": ProbeStatus.NOT_INSTALLED,
                         "note": "Noetix E1 has depth camera only, no lidar"}
        return data

    # ── E1-specific commands (exposed via /robot/action delegated_actions) ─

    def speak(self, text: str, lang: str = "ru") -> dict:
        result = self._post("/api/audio/speak", {"text": text, "lang": lang})
        return result or {"status": "error", "adapter": "e1"}

    def listen(self, timeout_s: float = 5.0) -> dict:
        result = self._get(f"/api/audio/listen?timeout={timeout_s:.1f}")
        return result or {"status": "error", "adapter": "e1"}

    def stand_up(self) -> dict:
        return self._post("/api/cmd/mode", {"mode": "preparation"}) or \
            {"status": "error", "adapter": "e1"}

    def lie_down(self) -> dict:
        return self._post("/api/cmd/mode", {"mode": "disabled"}) or \
            {"status": "error", "adapter": "e1"}

    def gesture(self, name: str) -> dict:
        slot = E1_GESTURES.get(name, "preset_a")
        result = self._post("/api/cmd/gesture", {"name": name, "slot": slot})
        return result or {"status": "error", "adapter": "e1"}

    def set_mode(self, mode: str) -> dict:
        m = mode.lower()
        if m not in E1_MODES:
            return {"status": "error", "error": f"unknown mode {mode}",
                    "valid": sorted(E1_MODES)}
        result = self._post("/api/cmd/mode", {"mode": m})
        if result and result.get("status") in ("ok", "switched"):
            self._current_mode = m
        return result or {"status": "error", "adapter": "e1"}

    def capture_photo(self) -> dict:
        result = self._get("/api/camera/capture")
        return result or {"status": "error", "adapter": "e1"}

    def handle_action(self, action_type: str, params: dict) -> dict:
        """Generic dispatcher used by /robot/action delegated_actions list.

        Trust Layer's bridge passes high-level action names ("wave", "stand_up",
        "ros2_publish", etc.) here when the adapter advertises handle_action.
        """
        params = params or {}
        action_type = action_type.lower()

        if action_type in ("wave", "greet", "hello"):
            return self.gesture("greet")
        if action_type in ("handshake",):
            return self.gesture("handshake")
        if action_type in ("cheer", "agree", "clap"):
            return self.gesture("cheer")
        if action_type in ("stand_up", "rise", "ready"):
            return self.stand_up()
        if action_type in ("sit_down", "crouch", "lie_down"):
            return self.lie_down()
        if action_type == "mode":
            return self.set_mode(str(params.get("mode", "walking")))
        if action_type == "gesture":
            return self.gesture(str(params.get("name", "wave")))
        if action_type == "ros2_publish":
            # E1's onboard Jetson runs ROS2; e1_server proxies the publish.
            return self._post("/api/ros2/publish", params) or \
                {"status": "error", "adapter": "e1"}
        if action_type == "spin":
            return self.send_velocity(0.0, 0.0, 0.6)
        return {"status": "not_supported", "adapter": "e1",
                "note": f"E1 adapter has no handler for {action_type}"}

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
            logger.debug("E1 GET %s failed: %s", path, exc)
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
            logger.debug("E1 POST %s failed: %s", path, exc)
            return None

    def _error_state(self) -> dict:
        return {
            "position": {"x": 0, "y": 0, "z": 0},
            "velocity": {"vx": 0, "vy": 0, "vz": 0},
            "heading_rad": 0, "speed_mps": 0,
            "battery": 0, "tilt_deg": 0, "temperature_c": 0,
            "mode": "UNKNOWN", "mode_e1": "unknown",
            "robot_model": "Noetix E1",
            "robot_id": "e1-01",
            "name": "Noetix E1",
            "manufacturer": "Noetix Robotics",
            "timestamp_s": time.time(),
            "adapter": "e1",
            "error": self._last_error,
            "sensors": {},
        }
