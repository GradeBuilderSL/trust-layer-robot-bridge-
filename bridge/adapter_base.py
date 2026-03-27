"""RobotAdapter — abstract base class for all Trust Layer robot adapters.

Every adapter (Mock, HTTP/N2, H1, future ROS2, CAN-bus, …) must implement this
interface. Trust Layer code depends ONLY on this contract; it never reaches into
adapter internals.

Analogy from PROMPT_ROBOT_ADAPTER_SYSTEM.md:
    "Like USB — the device says what it can do, the OS doesn't care how."
"""
from __future__ import annotations

import abc
from typing import Any


# ---------------------------------------------------------------------------
# Probe status literals (used in capability entries)
# ---------------------------------------------------------------------------

class ProbeStatus:
    OK = "ok"
    NOT_INSTALLED = "not_installed"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    CRITICAL = "critical"
    ERROR = "error"
    CLIENT_STT = "client_stt_fallback"
    CLIENT_TTS = "client_tts_fallback"


# Required top-level capability keys.
# Any adapter that lacks one of these returns a "not_installed" default.
CAPABILITY_KEYS = [
    "camera", "lidar", "imu", "microphone", "speaker",
    "drive", "battery", "network",
]

_CAP_DEFAULT: dict[str, Any] = {
    "available": False,
    "probe": ProbeStatus.NOT_INSTALLED,
    "note": "",
}


def normalize_capabilities(raw: dict) -> dict:
    """Fill in missing fields so every capability has a consistent schema.

    Guarantees:
      - All CAPABILITY_KEYS present
      - Each entry has at minimum: available (bool), probe (str), note (str)
    """
    out: dict[str, Any] = {}
    for key in CAPABILITY_KEYS:
        entry = dict(raw.get(key) or {})
        # Ensure mandatory fields
        entry.setdefault("available", False)
        default_probe = (
            ProbeStatus.NOT_INSTALLED if not entry["available"]
            else ProbeStatus.OK
        )
        entry.setdefault("probe", default_probe)
        entry.setdefault("note", "")
        # health: default to 1.0 when available, 0.0 when not
        if "health" not in entry:
            entry["health"] = 1.0 if entry["available"] else 0.0
        out[key] = entry
    # Pass through any extra keys the adapter adds (e.g. "joints", "depth_camera")
    for k, v in raw.items():
        if k not in out:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class RobotAdapter(abc.ABC):
    """Common interface every robot adapter must implement.

    Attributes:
        name      -- adapter identifier ("mock" | "http" | "h1" | …)
        connected -- True if last communication with the robot succeeded
    """

    name: str = "unknown"
    connected: bool = False

    # ── Telemetry ─────────────────────────────────────────────────────────

    @abc.abstractmethod
    def get_state(self) -> dict:
        """Return current robot telemetry.

        Required fields in returned dict:
            position        {x, y, z}           — metres
            velocity        {vx, vy, vz}        — m/s
            heading_rad     float               — world frame
            speed_mps       float
            battery         float               — percent (0–100)
            tilt_deg        float
            timestamp_s     float               — Unix time
            adapter         str                 — self.name
        """

    @abc.abstractmethod
    def get_entities(self) -> list[dict]:
        """Return detected scene entities (humans, obstacles, objects).

        Each entity: {entity_id, class_name, distance_m, is_human, position}
        Return [] if the adapter has no perception.
        """

    # ── Actuation ─────────────────────────────────────────────────────────

    @abc.abstractmethod
    def send_velocity(self, vx: float, vy: float, wz: float) -> dict:
        """Send a velocity command.

        Args:
            vx  -- forward/backward (m/s)
            vy  -- lateral (m/s); 0.0 for non-holonomic / legged robots
            wz  -- angular (rad/s); positive = CCW

        Returns:
            {"status": "ok" | "error", "adapter": self.name, ...}
        """

    @abc.abstractmethod
    def stop(self) -> dict:
        """Emergency stop — halt all motion immediately.

        Returns:
            {"status": "stopped", "adapter": self.name}
        """

    # ── Capability discovery ───────────────────────────────────────────────

    @abc.abstractmethod
    def probe_capabilities(self) -> dict:
        """Probe hardware and return raw capability dict.

        The returned dict is passed through normalize_capabilities() by the
        bridge /robot/capabilities endpoint before being sent to the client.
        Keys should match CAPABILITY_KEYS but extras are allowed.
        """

    # ── Scenario injection (optional — default no-op) ─────────────────────

    def inject_scenario(self, overrides: dict) -> None:
        """Inject test conditions (battery_pct, tilt_deg, entities, …).

        Default: no-op. Override in adapters that support scenario injection.
        """

    def clear_scenario(self) -> None:
        """Reset to nominal operating state.

        Default: no-op.
        """

    # ── High-level actions (optional — default not_supported) ─────────────

    def navigate_to(
        self,
        x_m: float,
        y_m: float,
        heading_rad: float = 0.0,
        speed_mps: float = 0.3,
    ) -> dict:
        """Navigate to absolute position (x_m, y_m) in the robot's world frame.

        Override in adapters that have a navigation stack (ROS2 Nav2, etc.).
        Default: not supported.
        """
        return {
            "status": "not_supported",
            "adapter": self.name,
            "note": "This adapter does not implement navigate_to",
        }

    def coordinate_transform(self, facility_x: float, facility_y: float, facility_theta: float = 0.0) -> tuple:
        """Transform facility coordinates to robot local frame.

        Override in subclass if robot uses different coordinate system.
        Default: identity transform (facility = robot frame).

        Returns: (robot_x, robot_y, robot_theta)
        """
        return (facility_x, facility_y, facility_theta)

    def mode_control(self, mode: str) -> dict:
        """Set robot operating mode.

        Modes: AUTOMATIC, SEMIAUTOMATIC, MANUAL, SERVICE, PAUSE
        Mapping to Trust Layer: AUTOMATIC->FULL, SEMIAUTOMATIC->ADVISORY, MANUAL->SHADOW

        Returns: {"ok": True, "mode": mode}
        """
        mode_map = {
            "AUTOMATIC": "FULL",
            "SEMIAUTOMATIC": "ADVISORY",
            "MANUAL": "SHADOW",
            "SERVICE": "DISABLED",
            "PAUSE": "SHADOW",
        }
        tl_mode = mode_map.get(mode, "ADVISORY")
        return {"ok": True, "mode": tl_mode, "vda5050_mode": mode}

    def get_lidar_scan(self) -> dict:
        """Return latest LiDAR scan data.

        Returns a dict with:
            available (bool), source (str), ranges (list[float]),
            angle_min_rad, angle_max_rad, angle_increment_rad,
            range_min_m, range_max_m, timestamp_s

        Override in adapters with a real LiDAR sensor.
        Default: not installed.
        """
        return {
            "available": False,
            "error": "not_installed",
            "adapter": self.name,
        }
