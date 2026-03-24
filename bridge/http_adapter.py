"""HTTP adapter for Noetix N2 robot.

Connects to the real robot's HTTP API at http://<robot-ip>:8000/api/...
Falls back gracefully to error state if robot is unreachable.
"""
import json
import logging
import time
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

from bridge.adapter_base import RobotAdapter


class HttpAdapter(RobotAdapter):
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
        """Send velocity command. Tries /api/cmd/velocity, fallback to /chat/send."""
        # Try direct velocity endpoint (Noetix N2 API)
        result = self._post(
            f"/api/cmd/velocity?vx={vx:.3f}&vy={vy:.3f}&wz={wz:.3f}",
            {},
        )
        if result is not None:
            return {"status": "ok", "adapter": "http"}

        # Fallback: Isaac Sim h1_bridge uses /chat/send for commands
        import math
        speed = math.hypot(vx, vy)
        if speed < 0.01 and abs(wz) < 0.01:
            chat_cmd = "stop"
        elif abs(wz) > 0.1:
            chat_cmd = f"turn {'left' if wz > 0 else 'right'}"
        else:
            chat_cmd = f"walk forward {speed:.1f}"

        logger.info("http_adapter: /api/cmd/velocity failed, using /chat/send: %s", chat_cmd)
        result = self._post("/chat/send", {"message": chat_cmd})
        if result is not None:
            return {"status": "ok", "adapter": "http_chat", "chat_cmd": chat_cmd}
        return {"status": "error", "error": self._last_error}

    def navigate_to(
        self, x_m: float, y_m: float,
        heading_rad: float = 0.0, speed_mps: float = 0.3,
    ) -> dict:
        """Navigate to position. Tries API endpoints, fallback to chat commands."""
        # Try 1: direct navigate endpoint
        result = self._post(
            "/api/cmd/navigate",
            {"x": x_m, "y": y_m, "heading": heading_rad, "speed": speed_mps},
        )
        if result is not None:
            return {"status": "ok", "adapter": "http", "target": {"x": x_m, "y": y_m}}

        # Try 2: Isaac Sim chat command (h1_bridge parses "walk to X Y")
        chat_cmd = f"find_and_approach {x_m:.1f} {y_m:.1f}"
        result = self._post("/chat/send", {"message": chat_cmd})
        if result is not None:
            logger.info("http_adapter: navigate via /chat/send: %s", chat_cmd)
            return {"status": "moving_to_destination", "adapter": "http_chat",
                    "target": {"x": x_m, "y": y_m}, "chat_cmd": chat_cmd}

        # Try 3: compute velocity toward target
        import math
        state = self.get_telemetry()
        pos = state.get("position", {})
        rx, ry = float(pos.get("x", 0)), float(pos.get("y", 0))
        dx, dy = x_m - rx, y_m - ry
        dist = math.hypot(dx, dy)
        if dist < 0.1:
            return {"status": "ok", "adapter": "http", "note": "already_at_target"}
        vx = (dx / dist) * min(speed_mps, 0.8)
        vy = (dy / dist) * min(speed_mps, 0.8)
        self.send_velocity(vx, vy, 0.0)
        return {"status": "moving_to_destination", "adapter": "http",
                "target": {"x": x_m, "y": y_m}}

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

    def probe_capabilities(self) -> dict:
        """Active probe of real N2 hardware subsystems. Uses short timeouts."""
        import time as _time
        caps = {}

        # Probe robot API — get full status first
        state = self._get("/api/status")
        sensors = (state or {}).get("sensors", {})

        # Camera
        cam_data = sensors.get("camera", {})
        cam_ok = cam_data.get("health", 0) > 0.3 or cam_data.get("ok", False)
        cam_test = self._get("/api/camera/status") or {}
        caps["camera"] = {
            "available": cam_ok or cam_test.get("ok", False),
            "health": cam_data.get("health", 0),
            "fps": cam_data.get("fps") or cam_test.get("fps"),
            "resolution": cam_test.get("resolution"),
            "probe": "ok" if cam_ok else "degraded_or_unavailable",
            "has_preview": cam_ok,
            "note": cam_test.get("note", ""),
        }

        # Lidar
        lidar = sensors.get("lidar", {})
        lidar_ok = lidar.get("health", 0) > 0.3 or lidar.get("available", False)
        caps["lidar"] = {
            "available": lidar_ok,
            "health": lidar.get("health", 0),
            "probe": "ok" if lidar_ok else "not_installed",
            "note": "" if lidar_ok else "Lidar not detected on /api/status",
        }

        # IMU
        imu = sensors.get("imu", {})
        imu_ok = imu.get("health", 0) > 0.3
        caps["imu"] = {
            "available": imu_ok,
            "health": imu.get("health", 0),
            "probe": "ok" if imu_ok else "degraded",
            "note": "Tilt/pitch/roll from IMU",
        }

        # Voice (try /api/voice/status; degrade gracefully)
        voice = self._get("/api/voice/status") or {}
        mic_ok = voice.get("mic_available", False)
        spk_ok = voice.get("speaker_available", False)
        caps["microphone"] = {
            "available": mic_ok,
            "sample_rate": voice.get("sample_rate", 16000),
            "probe": "ok" if mic_ok else "client_stt_fallback",
            "method": "robot_stt" if mic_ok else "client_stt",
            "note": "Robot microphone" if mic_ok else "Web Speech API fallback (client-side)",
        }
        caps["speaker"] = {
            "available": spk_ok,
            "probe": "ok" if spk_ok else "client_tts_fallback",
            "method": "robot_tts" if spk_ok else "client_tts",
            "note": "Robot speaker" if spk_ok else "Web Speech API fallback (client-side)",
        }

        # Drive
        drive_ok = self.connected and state is not None
        caps["drive"] = {
            "available": drive_ok,
            "probe": "ok" if drive_ok else "disconnected",
            "type": "holonomic",
            "max_speed_mps": 0.8,
            "note": f"HTTP commands to {self.robot_url}/api/cmd/velocity",
        }

        # Battery
        bat = float((state or {}).get("battery_pct", 0))
        bat_ok = bat > 5
        caps["battery"] = {
            "available": True,
            "level_pct": round(bat, 1),
            "probe": "ok" if bat_ok else "critical",
            "estimated_runtime_min": int(bat * 2.4) if bat > 0 else 0,
            "note": "Critical — charge before demo" if not bat_ok else "",
        }

        # Network
        t0 = _time.time()
        net_ok = self._get("/health") is not None
        latency_ms = round((_time.time() - t0) * 1000, 1)
        caps["network"] = {
            "available": net_ok,
            "probe": "ok" if net_ok else "unreachable",
            "latency_ms": latency_ms if net_ok else None,
            "robot_url": self.robot_url,
            "note": f"Round-trip to {self.robot_url}/health",
        }

        return caps

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
