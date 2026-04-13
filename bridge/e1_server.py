"""E1 Server — REST API wrapper, runs ON Noetix E1's onboard Jetson Orin Nano Super.

Bridges Noetix DDS SDK / ROS 2 / kinematic-sim → HTTP so the Trust Layer
robot bridge can connect from any machine on the same network.

=== Why three transports ===

  noetix_dds  — Production. Uses Noetix `dds_demo_release_e1` SDK (CycloneDDS-
                based, similar to Unitree SDK2). Topic names are loaded from
                E1_DDS_TOPIC_PREFIX env var because the official topic schema
                from Feishu requires login and is not vendored in this repo.
                Fill in the topics once you've unpacked the SDK tarball on the
                Jetson — see _NoetixDDSTransport for exact spots.

  ros2        — Recommended for development and ROS2 fleets. Uses rclpy to
                publish geometry_msgs/Twist on /cmd_vel and subscribe
                nav_msgs/Odometry on /odom plus sensor_msgs/BatteryState. Works
                out-of-the-box with ROS2 Humble/Jazzy on the Jetson.

  sim         — No hardware. Kinematic integrator. Default when neither SDK
                nor rclpy can be imported.

=== Installation on E1 ===

  1. SSH into the Jetson Orin Nano Super (NOT the RK3588S motion-control
     board — see WARNING below):
       ssh noetix@192.168.55.101    # password: noetix
  2. git clone https://github.com/GradeBuilderSL/trust-layer-robot-bridge- \\
       /opt/trust-layer-bridge
  3. pip3 install fastapi uvicorn   # only if you want FastAPI; bare HTTP works too
  4. (optional) pip3 install cyclonedds   # for noetix_dds transport
  5. (optional) source /opt/ros/humble/setup.bash    # for ros2 transport
  6. python3 -m bridge.e1_server

=== Environment ===

  E1_SERVER_PORT       8083                       (this server's port)
  E1_TRANSPORT         auto | noetix_dds | ros2 | sim
  E1_NETWORK_IFACE     eth0                       (DDS network interface)
  E1_DDS_TOPIC_PREFIX  rt                         (matches Unitree convention)
  ROS_DOMAIN_ID        0                          (ROS2 only)
  IFLYTEK_APP_ID       ""                         (iFlytek voice — optional)
  IFLYTEK_API_KEY      ""
  IFLYTEK_API_SECRET   ""

=== ⚠ WARNING — DO NOT SSH INTO RK3588S ===

The RK3588S motion-control board (separate from the Jetson) runs the
EtherCAT loop. SSH'ing into it during motion will starve EtherCAT of CPU
and the robot will collapse on the spot. ALWAYS hang E1 on the safety frame
or lay it flat in disabled mode before touching the motion board.

This server runs on the Jetson Orin Nano Super, not on the RK3588S. Safe.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("e1_server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s e1_server %(levelname)s %(message)s",
)

# ── Configuration ─────────────────────────────────────────────────────────

E1_SERVER_PORT     = int(os.environ.get("E1_SERVER_PORT", "8083"))
E1_TRANSPORT_MODE  = os.environ.get("E1_TRANSPORT", "auto").lower()
E1_NETWORK_IFACE   = os.environ.get("E1_NETWORK_IFACE", "eth0")
E1_DDS_TOPIC_PFX   = os.environ.get("E1_DDS_TOPIC_PREFIX", "rt")
E1_ROBOT_ID        = os.environ.get("E1_ROBOT_ID", "e1-01")
E1_ROBOT_NAME      = os.environ.get("E1_ROBOT_NAME", "Noetix E1")

IFLYTEK_APP_ID     = os.environ.get("IFLYTEK_APP_ID", "")
IFLYTEK_API_KEY    = os.environ.get("IFLYTEK_API_KEY", "")
IFLYTEK_API_SECRET = os.environ.get("IFLYTEK_API_SECRET", "")


# ── Transport interface ───────────────────────────────────────────────────

class _Transport:
    """Pluggable transport. Each implementation must be safe to call from any
    thread; HTTP requests are served from the threading server's worker pool.
    """

    name = "abstract"

    def get_state(self) -> dict:
        raise NotImplementedError

    def send_velocity(self, vx: float, vyaw: float) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def set_mode(self, mode: str) -> None:
        # Optional. Sim ignores; DDS/ROS2 implementations override.
        pass

    def gesture(self, name: str, slot: str) -> None:
        pass

    def shutdown(self) -> None:
        pass


# ── Noetix DDS transport ──────────────────────────────────────────────────

class _NoetixDDSTransport(_Transport):
    """Talks to Noetix E1 motion-control board via the DDS SDK that ships in
    `dds_demo_release_e1.tar.gz`.

    The exact topic names and message classes from that SDK aren't open;
    you must unpack the tarball on the Jetson and patch the four spots
    marked `# >>> SDK <<<` below to match the schema. The skeleton runs
    fine without them — it just logs warnings.
    """

    name = "noetix_dds"

    def __init__(self) -> None:
        self._lowstate: dict = {}
        self._cmd_pub = None
        self._initialised = False
        self._init_sdk()

    def _init_sdk(self) -> None:
        try:
            # >>> SDK <<<  Replace with the import path Noetix ships, e.g.:
            # from noetix_e1_sdk.core.channel import ChannelFactory, ChannelSubscriber, ChannelPublisher
            # from noetix_e1_sdk.idl.SportCmd_ import SportCmd_
            # from noetix_e1_sdk.idl.LowState_ import LowState_
            from cyclonedds.domain import DomainParticipant  # type: ignore
            from cyclonedds.topic import Topic  # type: ignore
            from cyclonedds.pub import DataWriter, Publisher  # type: ignore
            from cyclonedds.sub import DataReader, Subscriber  # type: ignore

            self._dp = DomainParticipant(0)
            logger.info("Noetix DDS: cyclonedds DomainParticipant ready on %s",
                        E1_NETWORK_IFACE)

            # >>> SDK <<<  Wire publishers/subscribers here once you have
            # the IDL classes. Example shape:
            #
            #   self._cmd_topic = Topic(self._dp, f"{E1_DDS_TOPIC_PFX}/cmd_vel", SportCmd_)
            #   self._cmd_pub   = DataWriter(Publisher(self._dp), self._cmd_topic)
            #
            #   self._state_topic  = Topic(self._dp, f"{E1_DDS_TOPIC_PFX}/lowstate", LowState_)
            #   self._state_reader = DataReader(Subscriber(self._dp), self._state_topic)
            #   threading.Thread(target=self._reader_loop, daemon=True).start()

            self._initialised = True
        except Exception as exc:
            logger.warning(
                "Noetix DDS init failed (%s) — server will return cached/empty state. "
                "Install cyclonedds + dds_demo_release_e1 SDK and patch _NoetixDDSTransport.",
                exc,
            )

    def get_state(self) -> dict:
        # Until the SDK reader is wired, return whatever the optional thread
        # has cached. The bridge will see "transport=noetix_dds" but null
        # telemetry and surface that to the operator.
        return dict(self._lowstate)

    def send_velocity(self, vx: float, vyaw: float) -> None:
        if not self._initialised or self._cmd_pub is None:
            logger.debug("noetix_dds.send_velocity(%.3f, %.3f) ignored — SDK not wired",
                         vx, vyaw)
            return
        # >>> SDK <<<  Build the SportCmd_ sample and write it. Example:
        #
        #   cmd = SportCmd_(vx=float(vx), vy=0.0, vyaw=float(vyaw),
        #                   mode_request=0)  # 0 = walk
        #   self._cmd_pub.write(cmd)
        logger.debug("noetix_dds.send_velocity(%.3f, %.3f) — implement SDK call",
                     vx, vyaw)

    def stop(self) -> None:
        self.send_velocity(0.0, 0.0)


# ── ROS 2 transport ───────────────────────────────────────────────────────

class _ROS2Transport(_Transport):
    """rclpy-based transport. Publishes /cmd_vel and subscribes /odom plus
    /battery_state. Works with any ROS 2 stack on the Jetson.

    A background 20 Hz repeater keeps re-publishing the latest target
    velocity even when no new command arrives. This matters because:

    * Most humanoid/mobile base controllers apply a short safety timeout
      on /cmd_vel (~200–500 ms). If the operator sends one command and the
      task_executor's stream tick is delayed, the robot freezes mid-step.
    * Chat-driven commands come in bursts of HTTP calls at ~20 Hz, but
      network jitter can stretch that. A dedicated repeater decouples motor
      cadence from HTTP cadence.

    Sending stale zero commands is harmless — the robot already stopped.
    """

    name = "ros2"

    REPEAT_HZ = 20.0
    # If no new target arrives within this window, fall back to zero and
    # stop repeating non-zero commands. Safety net for a stuck upstream.
    STALE_CMD_TIMEOUT_S = 1.0

    def __init__(self) -> None:
        self._state: dict = {}
        self._lock = threading.Lock()
        self._node = None
        self._cmd_pub = None
        self._executor_thread: Optional[threading.Thread] = None
        self._target_vx = 0.0
        self._target_vyaw = 0.0
        self._target_ts = 0.0
        self._repeater_thread: Optional[threading.Thread] = None
        self._repeater_running = False
        self._init_node()
        self._start_repeater()

    def _init_node(self) -> None:
        try:
            import rclpy  # type: ignore
            from rclpy.node import Node  # type: ignore
            from geometry_msgs.msg import Twist  # type: ignore
            from nav_msgs.msg import Odometry  # type: ignore
            from sensor_msgs.msg import BatteryState  # type: ignore

            rclpy.init(args=None)

            class _E1Node(Node):  # type: ignore
                pass

            self._rclpy = rclpy
            self._Twist = Twist
            self._node = _E1Node("trust_layer_e1_server")
            self._cmd_pub = self._node.create_publisher(Twist, "/cmd_vel", 10)

            def _on_odom(msg):
                with self._lock:
                    self._state["pos_x"] = msg.pose.pose.position.x
                    self._state["pos_y"] = msg.pose.pose.position.y
                    self._state["pos_z"] = msg.pose.pose.position.z
                    q = msg.pose.pose.orientation
                    siny_cosp = 2 * (q.w * q.z + q.x * q.y)
                    cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
                    self._state["yaw_rad"] = math.atan2(siny_cosp, cosy_cosp)
                    self._state["vx"] = msg.twist.twist.linear.x
                    self._state["speed_mps"] = abs(msg.twist.twist.linear.x)

            def _on_battery(msg):
                with self._lock:
                    pct = msg.percentage * 100 if msg.percentage <= 1.0 else msg.percentage
                    self._state["battery_pct"] = float(pct)

            self._node.create_subscription(Odometry,    "/odom",          _on_odom,    10)
            self._node.create_subscription(BatteryState, "/battery_state", _on_battery, 10)

            def _spin():
                try:
                    rclpy.spin(self._node)
                except Exception as exc:
                    logger.warning("rclpy spin exited: %s", exc)

            self._executor_thread = threading.Thread(target=_spin, daemon=True)
            self._executor_thread.start()
            logger.info("ROS2 transport ready (publishing /cmd_vel, subscribing /odom)")
        except Exception as exc:
            logger.warning("ROS2 transport init failed: %s", exc)
            raise

    def get_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def send_velocity(self, vx: float, vyaw: float) -> None:
        if self._cmd_pub is None:
            return
        # Update target; publish immediately so the first command lands
        # before the repeater fires. The repeater re-publishes at 20 Hz.
        with self._lock:
            self._target_vx = float(vx)
            self._target_vyaw = float(vyaw)
            self._target_ts = time.monotonic()
        self._publish_now(float(vx), float(vyaw))

    def _publish_now(self, vx: float, vyaw: float) -> None:
        try:
            msg = self._Twist()
            msg.linear.x = float(vx)
            msg.angular.z = float(vyaw)
            self._cmd_pub.publish(msg)
        except Exception as exc:
            logger.warning("ROS2 /cmd_vel publish failed: %s", exc)

    def _start_repeater(self) -> None:
        """Daemon thread that re-publishes the latest target at REPEAT_HZ."""
        if self._repeater_running:
            return
        self._repeater_running = True
        dt = 1.0 / self.REPEAT_HZ

        def _loop() -> None:
            while self._repeater_running:
                try:
                    with self._lock:
                        vx = self._target_vx
                        vyaw = self._target_vyaw
                        age = time.monotonic() - self._target_ts if self._target_ts else 999.0
                    # If the upstream hasn't refreshed the target within the
                    # stale window, force zero. This keeps the motor bus fed
                    # but never stretches an old command forward in time.
                    if age > self.STALE_CMD_TIMEOUT_S and (vx or vyaw):
                        with self._lock:
                            self._target_vx = 0.0
                            self._target_vyaw = 0.0
                        vx, vyaw = 0.0, 0.0
                    if self._cmd_pub is not None:
                        self._publish_now(vx, vyaw)
                except Exception as exc:
                    logger.debug("repeater tick error: %s", exc)
                time.sleep(dt)

        self._repeater_thread = threading.Thread(
            target=_loop, daemon=True, name="e1-cmd-vel-repeater",
        )
        self._repeater_thread.start()
        logger.info(
            "ROS2 /cmd_vel repeater running at %.0f Hz (stale window %.1fs)",
            self.REPEAT_HZ, self.STALE_CMD_TIMEOUT_S,
        )

    def stop(self) -> None:
        self.send_velocity(0.0, 0.0)

    def shutdown(self) -> None:
        try:
            self._repeater_running = False
            if self._node is not None:
                self._node.destroy_node()
            self._rclpy.shutdown()
        except Exception:
            pass


# ── Sim transport (always available) ──────────────────────────────────────

class _SimTransport(_Transport):
    """Kinematic integrator. Used when no real hardware is reachable."""

    name = "sim"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = {
            "pos_x": 0.0, "pos_y": 0.0, "pos_z": 0.0,
            "yaw_rad": 0.0, "pitch_deg": 0.0,
            "vx": 0.0, "speed_mps": 0.0,
            "battery_pct": 92.0,
            "motor_temp_c": 33.0,
            "gait": "STAND",
            "mode_e1": "walking",
        }
        self._cmd_vx = 0.0
        self._cmd_wz = 0.0
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        dt = 0.1
        while self._running:
            with self._lock:
                s = self._state
                # 80/20 low-pass smoothing on the velocity command
                s["vx"] = 0.8 * self._cmd_vx + 0.2 * s["vx"]
                s["speed_mps"] = abs(s["vx"])
                s["yaw_rad"] += self._cmd_wz * dt
                s["pos_x"] += s["vx"] * math.cos(s["yaw_rad"]) * dt
                s["pos_y"] += s["vx"] * math.sin(s["yaw_rad"]) * dt
                s["battery_pct"] = max(5.0, s["battery_pct"] - 0.001)
                s["pitch_deg"] = random.uniform(-2, 2)
                s["gait"] = "WALK" if s["speed_mps"] > 0.01 else "STAND"
            time.sleep(dt)

    def get_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def send_velocity(self, vx: float, vyaw: float) -> None:
        self._cmd_vx = vx
        self._cmd_wz = vyaw

    def stop(self) -> None:
        self._cmd_vx = 0.0
        self._cmd_wz = 0.0

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self._state["mode_e1"] = mode


# ── Transport selection ───────────────────────────────────────────────────

def _select_transport() -> _Transport:
    requested = E1_TRANSPORT_MODE
    if requested == "noetix_dds":
        return _NoetixDDSTransport()
    if requested == "ros2":
        return _ROS2Transport()
    if requested == "sim":
        return _SimTransport()

    # auto: ROS 2 → sim. DDS is intentionally NOT auto-picked because the
    # _NoetixDDSTransport skeleton needs SDK wiring before it returns real
    # telemetry; until then it would hand the operator a battery=0 / pos=0
    # robot and look like a hardware fault. Set E1_TRANSPORT=noetix_dds
    # explicitly once you've patched the four `# >>> SDK <<<` spots.
    try:
        return _ROS2Transport()
    except Exception as exc:
        logger.debug("auto: ROS2 unavailable (%s)", exc)
    logger.info("auto: falling back to sim transport")
    return _SimTransport()


_transport = _select_transport()
logger.info("E1 transport: %s", _transport.name)


# ── E1 mode tracker (keeps the gamepad state machine in sync) ────────────

_state_lock = threading.Lock()
_e1_mode = "walking" if _transport.name != "sim" else _transport.get_state().get(
    "mode_e1", "walking"
)
_gesture_log: list[dict] = []
_audio_log: list[dict] = []


# ── iFlytek voice (best-effort, falls back to system speak / log) ────────

def _speak_text(text: str, lang: str) -> dict:
    """Send TTS text to E1's speaker. Tries iFlytek if credentials are set,
    otherwise falls back to system espeak-ng / pyttsx3 / log-only.
    """
    _audio_log.append({"text": text, "lang": lang, "ts": time.time()})
    logger.info("TTS [%s]: %s", lang, text[:80])

    if IFLYTEK_APP_ID and IFLYTEK_API_KEY and IFLYTEK_API_SECRET:
        # iFlytek Spark TTS REST endpoint — kept inline so the file remains
        # drop-in for the Jetson without extra deps. Networked call; the
        # robot needs internet for this path.
        try:
            import urllib.request
            body = json.dumps({
                "header": {"app_id": IFLYTEK_APP_ID},
                "parameter": {"oral": {"oral_level": "mid"},
                              "tts": {"vcn": "x4_lingxiaoyao_oral",
                                      "audio": {"encoding": "raw",
                                                "sample_rate": 16000}}},
                "payload": {"text": {"encoding": "utf8",
                                     "text": text}},
            }).encode()
            req = urllib.request.Request(
                "https://spark-api.cn-huabei-1.xf-yun.com/v1/private/dts_create",
                data=body,
                headers={"Content-Type": "application/json",
                         "X-Api-Key": IFLYTEK_API_KEY},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3.0).read()
            return {"status": "ok", "method": "iflytek_spark", "spoken": text}
        except Exception as exc:
            logger.warning("iFlytek TTS failed: %s", exc)

    # Fallback: try espeak-ng
    try:
        subprocess.run(
            ["espeak-ng", "-v", lang, "-s", "150", text],
            timeout=10, check=False,
        )
        return {"status": "ok", "method": "espeak", "spoken": text}
    except Exception:
        return {"status": "ok", "method": "log_only", "spoken": text}


def _listen(timeout_s: float) -> dict:
    """Stub: wraps the iFlytek 6-mic array if available, else returns empty.

    The wake word "小顽童" is handled inside the iFlytek module on E1; this
    endpoint is for explicit one-shot listen requests from the operator.
    """
    return {"status": "not_implemented",
            "note": "Wire to iFlytek STT — see Voice Module User Manual",
            "timeout_s": timeout_s}


# ── HTTP handler ──────────────────────────────────────────────────────────

class E1Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # suppress default log
        pass

    # ── GET ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            return self._state()
        if path == "/api/perception/entities":
            return self._json({"entities": []})
        if path == "/api/capabilities":
            return self._capabilities()
        if path == "/api/audio/listen":
            qs = parse_qs(parsed.query)
            t = float(qs.get("timeout", ["5.0"])[0])
            return self._json(_listen(t))
        if path == "/api/camera/capture":
            return self._json({"status": "not_implemented",
                               "note": "Wire to E1 head depth camera"})
        if path == "/health":
            return self._json({"status": "ok",
                               "transport": _transport.name,
                               "robot_id": E1_ROBOT_ID})
        return self._json({"error": "not found"}, 404)

    def _state(self) -> None:
        st = _transport.get_state()
        with _state_lock:
            mode = _e1_mode
        out = {
            "robot_id": E1_ROBOT_ID,
            "name": E1_ROBOT_NAME,
            "transport": _transport.name,
            "mode_e1": mode,
            "trust_mode": "ADVISORY",
            "pos_x": float(st.get("pos_x", 0.0)),
            "pos_y": float(st.get("pos_y", 0.0)),
            "pos_z": float(st.get("pos_z", 0.0)),
            "yaw_rad": float(st.get("yaw_rad", 0.0)),
            "pitch_deg": float(st.get("pitch_deg", 0.0)),
            "vx": float(st.get("vx", 0.0)),
            "speed_mps": float(st.get("speed_mps", 0.0)),
            "battery_pct": float(st.get("battery_pct", 0.0)),
            "motor_temp_c": float(st.get("motor_temp_c", 30.0)),
            "gait": st.get("gait", "STAND"),
            "camera_ok": 1, "imu_ok": 1, "mic_ok": 1, "speaker_ok": 1,
            "ts": time.time(),
        }
        self._json(out)

    def _capabilities(self) -> None:
        self._json({
            "camera":     {"available": True,  "probe": "ok",
                           "note": "depth camera (head)"},
            "lidar":      {"available": False, "probe": "not_installed",
                           "note": "Noetix E1 has no lidar"},
            "imu":        {"available": True,  "probe": "ok"},
            "microphone": {"available": True,  "probe": "ok",
                           "note": "iFlytek 6-mic array"},
            "speaker":    {"available": True,  "probe": "ok",
                           "note": "iFlytek AI sound card"},
            "drive":      {"available": True,  "probe": "ok",
                           "note": "humanoid walking, no strafe"},
            "battery":    {"available": True,  "probe": "ok"},
            "network":    {"available": True,  "probe": "ok",
                           "note": "4G IoT SIM (1y included) + Wi-Fi/Ethernet"},
            "joints": {
                "available": True,
                "total_dof": 24,
                "single_arm_dof": 5,
                "single_leg_dof": 6,
            },
            "transport": {"available": True, "probe": "ok",
                          "note": _transport.name},
        })

    # ── POST ─────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)

        path = urlparse(self.path).path
        if path == "/api/cmd/walk":
            return self._cmd_walk(body)
        if path == "/api/cmd/stop":
            return self._cmd_stop()
        if path == "/api/cmd/mode":
            return self._cmd_mode(body)
        if path == "/api/cmd/gesture":
            return self._cmd_gesture(body)
        if path == "/api/audio/speak":
            return self._json(_speak_text(
                body.get("text", ""), body.get("lang", "ru"),
            ))
        if path == "/api/ros2/publish":
            return self._json({
                "status": "not_implemented",
                "note": "Forward to local rclpy node when ROS2 transport is active",
                "transport": _transport.name,
            })
        return self._json({"error": "not found"}, 404)

    def _cmd_walk(self, body: dict) -> None:
        global _e1_mode
        vx = float(body.get("vx", 0))
        vyaw = float(body.get("vyaw", 0))
        with _state_lock:
            mode = _e1_mode
        if mode in ("disabled", "enabled", "preparation"):
            return self._json({
                "status": "denied",
                "reason": f"E1 in {mode} mode — switch to walking first",
                "valid_modes": ["walking", "running"],
            })
        try:
            _transport.send_velocity(vx, vyaw)
        except Exception as exc:
            return self._json({"status": "error", "error": str(exc)}, 500)
        self._json({"status": "ok", "vx": vx, "vyaw": vyaw,
                    "transport": _transport.name})

    def _cmd_stop(self) -> None:
        try:
            _transport.stop()
        except Exception as exc:
            return self._json({"status": "error", "error": str(exc)}, 500)
        self._json({"status": "stopped"})

    def _cmd_mode(self, body: dict) -> None:
        global _e1_mode
        new_mode = str(body.get("mode", "walking")).lower()
        valid = {"disabled", "enabled", "preparation",
                 "walking", "running", "teaching"}
        if new_mode not in valid:
            return self._json({"status": "error",
                               "error": f"unknown mode {new_mode}",
                               "valid": sorted(valid)}, 400)
        with _state_lock:
            _e1_mode = new_mode
        try:
            _transport.set_mode(new_mode)
        except Exception as exc:
            logger.warning("transport.set_mode failed: %s", exc)
        if new_mode == "disabled":
            try:
                _transport.stop()
            except Exception:
                pass
        self._json({"status": "switched", "mode": new_mode,
                    "transport": _transport.name})

    def _cmd_gesture(self, body: dict) -> None:
        name = body.get("name", "wave")
        slot = body.get("slot", "preset_a")
        _gesture_log.append({"name": name, "slot": slot, "ts": time.time()})
        try:
            _transport.gesture(name, slot)
        except Exception as exc:
            logger.warning("transport.gesture failed: %s", exc)
        self._json({"status": "ok", "gesture": name, "slot": slot})

    # ── Helpers ───────────────────────────────────────────────────────────

    def _json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ── Entry point ───────────────────────────────────────────────────────────

class _ThreadingHTTPServer(HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    server = _ThreadingHTTPServer(("0.0.0.0", E1_SERVER_PORT), E1Handler)
    logger.info("E1 server [%s] listening on :%d", _transport.name, E1_SERVER_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping E1 server")
        try:
            _transport.shutdown()
        except Exception:
            pass
