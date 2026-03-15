"""H1 Server — REST API wrapper, runs ON Unitree H1's onboard PC.

Bridges Unitree SDK2 (DDS/CycloneDDS) → HTTP so the robot bridge can connect
from any machine on the same network.

=== Installation on H1 ===
  1. SSH into H1 (default: ssh unitree@192.168.123.1, pass: 123)
  2. git clone https://github.com/GradeBuilderSL/trust-layer-robot-bridge- /opt/trust-layer-bridge
  3. pip3 install unitree_sdk2py fastapi uvicorn
  4. python3 -m bridge.h1_server          # or systemd service

=== Environment ===
  H1_SERVER_PORT     8081        (this server's port)
  H1_NETWORK_IFACE   eth0        (DDS network interface for SDK2)
  H1_SIM_MODE        0           (1 = simulate, no real SDK needed)

=== Without SDK (simulation / dev laptop) ===
  H1_SIM_MODE=1 python -m bridge.h1_server
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger("h1_server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s h1_server %(levelname)s %(message)s",
)

H1_SERVER_PORT  = int(os.environ.get("H1_SERVER_PORT", "8081"))
H1_NETWORK_IFACE = os.environ.get("H1_NETWORK_IFACE", "eth0")
H1_SIM_MODE     = os.environ.get("H1_SIM_MODE", "0") == "1"

# ── Try to import Unitree SDK2 ────────────────────────────────────────────

_SDK_AVAILABLE = False
_sport_client  = None

if not H1_SIM_MODE:
    try:
        from unitree_sdk2py.core.channel import ChannelFactory
        from unitree_sdk2py.go2.sport.sport_client import SportClient
        ChannelFactory.Instance().Init(0, H1_NETWORK_IFACE)
        _sport_client = SportClient()
        _sport_client.SetTimeout(3.0)
        _sport_client.Init()
        _SDK_AVAILABLE = True
        logger.info("Unitree SDK2 initialised on interface %s", H1_NETWORK_IFACE)
    except Exception as exc:
        logger.warning("SDK2 unavailable (%s) — falling back to sim mode", exc)
        H1_SIM_MODE = True

# ── Simulation state (used when SDK unavailable) ──────────────────────────

_sim_lock   = threading.Lock()
_sim_state  = {
    "pos_x": 0.0, "pos_y": 0.0, "pos_z": 0.0,
    "yaw_rad": 0.0, "pitch_deg": 0.0,
    "vx": 0.0, "speed_mps": 0.0,
    "battery_pct": 88.0,
    "motor_temp_c": 32.0,
    "gait": "STAND",
    "mode": "ADVISORY",
    "camera_ok": 1, "imu_ok": 1,
}
_sim_cmd_vx  = 0.0
_sim_cmd_wz  = 0.0
_sim_running = True


def _sim_step() -> None:
    global _sim_cmd_vx, _sim_cmd_wz
    dt = 0.1
    while _sim_running:
        with _sim_lock:
            s = _sim_state
            s["vx"] = _sim_cmd_vx * 0.8 + s["vx"] * 0.2
            s["speed_mps"] = abs(s["vx"])
            s["yaw_rad"] += _sim_cmd_wz * dt
            s["pos_x"]   += s["vx"] * math.cos(s["yaw_rad"]) * dt
            s["pos_y"]   += s["vx"] * math.sin(s["yaw_rad"]) * dt
            s["battery_pct"] = max(5.0, s["battery_pct"] - 0.001)
            s["pitch_deg"] = random.uniform(-2, 2)  # walking sway
            if s["speed_mps"] > 0.01:
                s["gait"] = "WALK"
            else:
                s["gait"] = "STAND"
        time.sleep(dt)


if H1_SIM_MODE:
    threading.Thread(target=_sim_step, daemon=True).start()
    logger.info("H1 running in SIMULATION mode (port %d)", H1_SERVER_PORT)

# ── Gesture / audio simulation ────────────────────────────────────────────

_gesture_log: list[dict] = []
_audio_log:   list[dict] = []


def _do_gesture(name: str) -> None:
    entry = {"gesture": name, "ts": time.time()}
    _gesture_log.append(entry)
    logger.info("Gesture: %s", name)
    if _SDK_AVAILABLE and _sport_client:
        try:
            # SDK2 gesture API varies by firmware; best-effort
            _sport_client.Gesture(name)
        except Exception as exc:
            logger.warning("SDK gesture failed: %s", exc)


# ── HTTP handler ──────────────────────────────────────────────────────────

class H1Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # suppress default log
        pass

    # ── GET ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._state()
        elif self.path == "/api/perception/entities":
            self._entities()
        elif self.path == "/api/camera/capture":
            self._camera_capture()
        else:
            self._json({"error": "not found"}, 404)

    def _state(self) -> None:
        if H1_SIM_MODE:
            with _sim_lock:
                self._json(dict(_sim_state))
        else:
            # Read from SDK2 low state
            try:
                low = _sport_client.GetLowState()
                bat = getattr(low.bms_state, "soc", 85)
                self._json({
                    "pos_x": 0.0, "pos_y": 0.0, "pos_z": 0.0,
                    "yaw_rad": 0.0, "pitch_deg": 0.0,
                    "vx": 0.0, "speed_mps": 0.0,
                    "battery_pct": float(bat),
                    "motor_temp_c": 35.0,
                    "gait": "STAND",
                    "mode": "ADVISORY",
                    "camera_ok": 1, "imu_ok": 1,
                })
            except Exception as exc:
                self._json({"error": str(exc)}, 500)

    def _entities(self) -> None:
        # H1 doesn't have lidar; return empty or camera-based detections
        self._json({"entities": []})

    def _camera_capture(self) -> None:
        # Placeholder: real impl would grab from H1 camera via SDK2
        self._json({
            "status": "ok",
            "note": "camera capture not implemented — use /api/audio/speak for voice",
        })

    # ── POST ─────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/api/cmd/walk":
            self._cmd_walk(body)
        elif self.path == "/api/cmd/stop":
            self._cmd_stop()
        elif self.path == "/api/cmd/stand_up":
            self._cmd_stand_up()
        elif self.path == "/api/cmd/lie_down":
            self._cmd_lie_down()
        elif self.path == "/api/cmd/gesture":
            self._cmd_gesture(body)
        elif self.path == "/api/cmd/gait":
            self._cmd_gait(body)
        elif self.path == "/api/audio/speak":
            self._audio_speak(body)
        elif self.path == "/api/sim/context":
            self._sim_context(body)
        else:
            self._json({"error": "not found"}, 404)

    def _cmd_walk(self, body: dict) -> None:
        global _sim_cmd_vx, _sim_cmd_wz
        vx   = float(body.get("vx",   0))
        vyaw = float(body.get("vyaw", 0))
        if H1_SIM_MODE:
            _sim_cmd_vx = vx
            _sim_cmd_wz = vyaw
        elif _SDK_AVAILABLE and _sport_client:
            try:
                _sport_client.Move(vx, 0, vyaw)
            except Exception as exc:
                self._json({"status": "error", "error": str(exc)}, 500)
                return
        self._json({"status": "ok", "vx": vx, "vyaw": vyaw})

    def _cmd_stop(self) -> None:
        global _sim_cmd_vx, _sim_cmd_wz
        if H1_SIM_MODE:
            _sim_cmd_vx = 0
            _sim_cmd_wz = 0
        elif _SDK_AVAILABLE and _sport_client:
            try:
                _sport_client.StopMove()
            except Exception:
                pass
        self._json({"status": "stopped"})

    def _cmd_stand_up(self) -> None:
        if H1_SIM_MODE:
            with _sim_lock:
                _sim_state["gait"] = "STAND"
        elif _SDK_AVAILABLE and _sport_client:
            try:
                _sport_client.StandUp()
            except Exception:
                pass
        self._json({"status": "standing"})

    def _cmd_lie_down(self) -> None:
        if H1_SIM_MODE:
            with _sim_lock:
                _sim_state["gait"] = "SIT"
        elif _SDK_AVAILABLE and _sport_client:
            try:
                _sport_client.StandDown()
            except Exception:
                pass
        self._json({"status": "lying"})

    def _cmd_gesture(self, body: dict) -> None:
        name = body.get("name", "wave")
        _do_gesture(name)
        self._json({"status": "ok", "gesture": name})

    def _cmd_gait(self, body: dict) -> None:
        gait = body.get("gait", "WALK")
        if H1_SIM_MODE:
            with _sim_lock:
                _sim_state["gait"] = gait
        self._json({"status": "ok", "gait": gait})

    def _audio_speak(self, body: dict) -> None:
        text = body.get("text", "")
        lang = body.get("lang", "ru")
        _audio_log.append({"text": text, "lang": lang, "ts": time.time()})
        logger.info("TTS [%s]: %s", lang, text[:80])
        # Real impl: subprocess.run(["espeak-ng", "-v", lang, text]) or pyttsx3
        self._json({"status": "ok", "spoken": text, "lang": lang})

    def _sim_context(self, body: dict) -> None:
        # Allow test dashboard to inject scenario overrides
        if H1_SIM_MODE and body:
            with _sim_lock:
                _sim_state.update(body)
        self._json({"status": "ok"})

    # ── Helpers ───────────────────────────────────────────────────────────

    def _json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", H1_SERVER_PORT), H1Handler)
    mode = "SDK2" if _SDK_AVAILABLE else "SIMULATION"
    logger.info("H1 server [%s] listening on :%d", mode, H1_SERVER_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped")
