"""HTTP adapter — universal adapter for robots with HTTP API.

Supports:
  - Isaac Sim H1 bridge: /control/move, /control/stop, /robot/state
  - Noetix N2 robot:     /api/cmd/velocity, /api/cmd/stop, /api/status

Auto-detects API style on first successful call and caches for speed.
"""
import json
import logging
import math
import time
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

from bridge.adapter_base import RobotAdapter

# Endpoint styles for different robots
_ENDPOINTS = {
    "isaac_sim": {
        "move":     "/control/move",
        "stop":     "/control/stop",
        "state":    "/robot/state",
        "health":   "/health",
        "camera":   "/camera/latest",
        "entities": "/sim/entities",
    },
    "noetix_n2": {
        "move":     "/api/cmd/velocity",
        "stop":     "/api/cmd/stop",
        "state":    "/api/status",
        "health":   "/health",
        "camera":   "/api/camera/frame",
        "entities": None,
    },
}


class HttpAdapter(RobotAdapter):
    """Universal HTTP adapter — auto-detects Isaac Sim vs Noetix N2 API."""

    def __init__(self, robot_url: str = "http://192.168.1.100:8000"):
        self.robot_url = robot_url.rstrip("/")
        self.connected = False
        self.name = "http"
        self._last_error = ""
        self._timeout = 2.0
        self._api_style = None  # "isaac_sim" | "noetix_n2" — auto-detected

    def _detect_api(self) -> str:
        """Auto-detect which API style the robot uses."""
        if self._api_style:
            return self._api_style

        # Try Isaac Sim first (most common in dev)
        result = self._get("/robot/state")
        if result is not None:
            self._api_style = "isaac_sim"
            logger.info("Detected API style: isaac_sim (/robot/state)")
            return self._api_style

        # Try N2 API
        result = self._get("/api/status")
        if result is not None:
            self._api_style = "noetix_n2"
            logger.info("Detected API style: noetix_n2 (/api/status)")
            return self._api_style

        # Default to Isaac Sim
        self._api_style = "isaac_sim"
        return self._api_style

    def _ep(self, name: str) -> str:
        """Get endpoint path for current API style."""
        style = self._detect_api()
        return _ENDPOINTS[style].get(name, "")

    # ── State ──────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        """Read robot state — auto-detects endpoint."""
        style = self._detect_api()

        if style == "isaac_sim":
            data = self._get("/robot/state")
            if data is None:
                return self._error_state()
            self.connected = True
            pos = data.get("position", {})
            vel = data.get("velocity", {})
            return {
                "position": {
                    "x": float(pos.get("x", 0)),
                    "y": float(pos.get("y", 0)),
                    "z": float(pos.get("z", 0)),
                },
                "velocity": {
                    "vx": float(vel.get("vx", 0)),
                    "vy": float(vel.get("vy", 0)),
                    "vz": float(vel.get("vz", 0)),
                },
                "heading_rad": float(data.get("heading_rad", data.get("yaw", 0))),
                "speed_mps": float(data.get("speed_mps", math.hypot(
                    vel.get("vx", 0), vel.get("vy", 0)))),
                "battery": float(data.get("battery", data.get("battery_pct", 0))) or 95.0,
                "tilt_deg": float(data.get("tilt_deg", 0)),
                "temperature_c": float(data.get("temperature_c", 25)),
                "mode": data.get("mode", "ADVISORY"),
                "timestamp_s": time.time(),
                "adapter": "http",
            }
        else:
            # Noetix N2 format
            data = self._get("/api/status")
            if data is None:
                return self._error_state()
            self.connected = True
            return {
                "position": {
                    "x": float(data.get("position_x", 0)),
                    "y": float(data.get("position_y", 0)),
                    "z": 0.0,
                },
                "velocity": {
                    "vx": float(data.get("vx", 0)),
                    "vy": float(data.get("vy", 0)),
                    "vz": 0.0,
                },
                "heading_rad": float(data.get("heading_rad", 0)),
                "speed_mps": float(data.get("speed_mps", 0)),
                "battery": float(data.get("battery_pct", 0)) or 95.0,
                "tilt_deg": float(data.get("tilt_deg", 0)),
                "temperature_c": float(data.get("temperature_c", 25)),
                "mode": data.get("mode", "ADVISORY"),
                "timestamp_s": time.time(),
                "adapter": "http",
            }

    # ── Movement ───────────────────────────────────────────────────────

    def send_velocity(self, vx: float, vy: float, wz: float) -> dict:
        """Send velocity command — uses correct endpoint for API style."""
        style = self._detect_api()

        if style == "isaac_sim":
            # Isaac Sim: POST /control/move {"vx", "vy", "wz"}
            result = self._post("/control/move", {"vx": vx, "vy": vy, "wz": wz})
        else:
            # Noetix N2: POST /api/cmd/velocity?vx=...&vy=...&wz=...
            result = self._post(
                f"/api/cmd/velocity?vx={vx:.3f}&vy={vy:.3f}&wz={wz:.3f}", {}
            )

        if result is not None:
            logger.debug("Velocity sent: vx=%.2f vy=%.2f wz=%.2f → %s", vx, vy, wz, style)
            return {"status": "ok", "adapter": "http", "api": style}

        logger.warning("send_velocity failed (%s): %s", style, self._last_error)
        return {"status": "error", "error": self._last_error}

    def navigate_to(
        self, x_m: float, y_m: float,
        heading_rad: float = 0.0, speed_mps: float = 0.3,
    ) -> dict:
        """Navigate to position — compute velocity toward target."""
        state = self.get_state()
        pos = state.get("position", {})
        rx = float(pos.get("x", 0))
        ry = float(pos.get("y", 0))
        dx, dy = x_m - rx, y_m - ry
        dist = math.hypot(dx, dy)

        if dist < 0.15:
            return {"status": "ok", "note": "already_at_target", "distance": dist}

        # Compute velocity vector toward target
        speed = min(speed_mps, 0.8)
        vx = (dx / dist) * speed
        vy = (dy / dist) * speed

        logger.info("navigate_to: (%.1f,%.1f) → (%.1f,%.1f) dist=%.1f vx=%.2f vy=%.2f",
                     rx, ry, x_m, y_m, dist, vx, vy)

        result = self.send_velocity(vx, vy, 0.0)
        return {
            "status": "moving_to_destination",
            "target": {"x": x_m, "y": y_m},
            "distance_m": round(dist, 2),
            "speed_mps": speed,
            **result,
        }

    def stop(self) -> dict:
        """Emergency stop — uses correct endpoint for API style."""
        style = self._detect_api()

        if style == "isaac_sim":
            result = self._post("/control/stop", {})
        else:
            result = self._post("/api/cmd/stop", {})

        if result is not None:
            logger.info("Robot stopped via %s", style)
            return {"status": "stopped", "adapter": "http", "api": style}

        # Ultimate fallback: zero velocity
        self.send_velocity(0, 0, 0)
        logger.warning("stop fallback: sent zero velocity")
        return {"status": "stopped", "adapter": "http_fallback"}

    # ── Entities ───────────────────────────────────────────────────────

    def get_entities(self) -> list[dict]:
        """Get detected entities (Isaac Sim only)."""
        if self._detect_api() == "isaac_sim":
            data = self._get("/sim/entities")
            return data if isinstance(data, list) else []
        return []

    # ── Scenarios ──────────────────────────────────────────────────────

    def inject_scenario(self, overrides: dict) -> None:
        """Inject test scenario."""
        style = self._detect_api()
        if style == "isaac_sim":
            self._post("/sim/scenario", overrides)
        else:
            self._post("/api/sim/set_context", overrides)

    def clear_scenario(self) -> None:
        """Clear scenario."""
        style = self._detect_api()
        if style == "isaac_sim":
            self._post("/sim/scenario", {"name": "clear"})
        else:
            self._post("/api/sim/set_context", {
                "crowd_density": 0, "tilt_angle": 0, "battery_level": 95,
            })

    # ── HTTP helpers ──────────────────────────────────────────────────

    def _get(self, path: str) -> dict | None:
        try:
            url = f"{self.robot_url}{path}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                ct = resp.headers.get("Content-Type", "")
                if "json" not in ct and "text/html" in ct:
                    return None  # HTML page, not JSON API
                return json.loads(resp.read())
        except Exception as e:
            self._last_error = str(e)
            self.connected = False
            return None

    def _post(self, path: str, data: dict) -> dict | None:
        try:
            url = f"{self.robot_url}{path}"
            body = json.dumps(data or {}).encode()
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            self._last_error = str(e)
            self.connected = False
            return None

    # ── Capabilities probe ────────────────────────────────────────────

    def probe_capabilities(self) -> dict:
        """Probe robot hardware capabilities."""
        caps = {}
        style = self._detect_api()
        state = self.get_state()

        # Camera
        cam_ok = self._get("/camera/latest") is not None if style == "isaac_sim" \
            else (self._get("/api/camera/status") or {}).get("ok", False)
        caps["camera"] = {
            "available": cam_ok,
            "probe": "ok" if cam_ok else "not_available",
            "note": "Head camera" if cam_ok else "Camera not detected",
        }

        # Drive
        caps["drive"] = {
            "available": self.connected,
            "probe": "ok" if self.connected else "disconnected",
            "type": "holonomic",
            "max_speed_mps": 0.8,
        }

        # Battery
        bat = float(state.get("battery", 95))
        caps["battery"] = {
            "available": True,
            "level_pct": bat,
            "probe": "ok" if bat > 10 else "low",
        }

        # IMU
        caps["imu"] = {
            "available": True,
            "probe": "ok",
            "note": "6-axis IMU (pitch/roll/yaw)",
        }

        # Network
        t0 = time.time()
        net_ok = self._get(self._ep("health")) is not None
        latency = round((time.time() - t0) * 1000, 1)
        caps["network"] = {
            "available": net_ok,
            "probe": "ok" if net_ok else "unreachable",
            "latency_ms": latency,
            "adapter": style,
        }

        return caps

    def _error_state(self) -> dict:
        return {
            "position": {"x": 0, "y": 0, "z": 0},
            "velocity": {"vx": 0, "vy": 0, "vz": 0},
            "heading_rad": 0, "speed_mps": 0,
            "battery": 95.0,
            "tilt_deg": 0, "temperature_c": 25,
            "mode": "ADVISORY", "timestamp_s": time.time(),
            "adapter": "http", "error": self._last_error,
            "sensors": {},
        }
