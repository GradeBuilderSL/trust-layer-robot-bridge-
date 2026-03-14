"""HTTP adapter for Noetix N2 robot.

Connects to the real robot's HTTP API at http://<robot-ip>:8000/api/...
Falls back gracefully to error state if robot is unreachable.
"""
import json
import time
import urllib.request
import urllib.error


class HttpAdapter:
    """Connects to Noetix N2 via HTTP REST API."""

    def __init__(self, robot_url: str = "http://192.168.1.100:8000"):
        self.robot_url = robot_url.rstrip("/")
        self.connected = False
        self.name = "http"
        self._last_error = ""
        self._timeout = 1.0

    def get_state(self) -> dict:
        """Read robot state from GET /api/status."""
        data = self._get("/api/status")
        if data is None:
            return self._error_state()
        self.connected = True

        # Map N2 API response to our standard format
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
            "battery": float(data.get("battery_pct", 100)),
            "tilt_deg": float(data.get("tilt_deg", 0)),
            "temperature_c": float(data.get("temperature_c", 25)),
            "mode": data.get("mode", "ADVISORY"),
            "timestamp_s": time.time(),
            "adapter": "http",
            "sensors": data.get("sensors", {
                "camera": {"health": 0.0, "available": False},
                "lidar": {"health": 0.0, "available": False},
                "imu": {"health": 0.0, "available": False},
            }),
        }

    def send_velocity(self, vx: float, vy: float, wz: float) -> dict:
        """Send velocity via POST /api/cmd/velocity."""
        result = self._post(
            f"/api/cmd/velocity?vx={vx:.3f}&vy={vy:.3f}&wz={wz:.3f}",
            {},
        )
        if result is None:
            return {"status": "error", "error": self._last_error}
        return {"status": "ok", "adapter": "http"}

    def stop(self) -> dict:
        """Emergency stop via POST /api/cmd/stop."""
        result = self._post("/api/cmd/stop", {})
        if result is None:
            return {"status": "error", "error": self._last_error}
        return {"status": "stopped", "adapter": "http"}

    def get_entities(self) -> list[dict]:
        """N2 doesn't expose entities — return empty."""
        return []

    def inject_scenario(self, overrides: dict) -> None:
        """Forward scenario injection to robot's sim endpoints."""
        self._post("/api/sim/set_context", overrides)

    def clear_scenario(self) -> None:
        """Clear scenario on robot."""
        self._post("/api/sim/set_context", {
            "crowd_density": 0,
            "tilt_angle": 0,
            "battery_level": 95,
        })

    # ── HTTP helpers ──────────────────────────────────────────────────

    def _get(self, path: str) -> dict | None:
        try:
            url = f"{self.robot_url}{path}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            self._last_error = str(e)
            self.connected = False
            return None

    def _post(self, path: str, data: dict) -> dict | None:
        try:
            url = f"{self.robot_url}{path}"
            body = json.dumps(data).encode()
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

    def _error_state(self) -> dict:
        return {
            "position": {"x": 0, "y": 0, "z": 0},
            "velocity": {"vx": 0, "vy": 0, "vz": 0},
            "heading_rad": 0, "speed_mps": 0,
            "battery": 0, "tilt_deg": 0, "temperature_c": 0,
            "mode": "UNKNOWN", "timestamp_s": time.time(),
            "adapter": "http", "error": self._last_error,
            "sensors": {},
        }
