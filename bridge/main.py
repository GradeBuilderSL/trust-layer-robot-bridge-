"""Trust-Layer Robot Bridge — FastAPI app that runs on robot or locally.

Connects to real robot (HTTP/H1 adapter) or simulates (mock adapter).
Exposes endpoints for the Test Dashboard to call.
Runs the local safety pipeline on every move command.

Adapter types (ADAPTER_TYPE env var):
  mock  — simulated Noetix N2 (default, no hardware needed)
  http  — real Noetix N2 via HTTP API
  h1    — Unitree H1 humanoid via h1_server.py on onboard PC

Quick-start for H1:
  1. On H1:     ssh unitree@192.168.123.1 "python -m bridge.h1_server"
  2. On laptop: ADAPTER_TYPE=h1 ROBOT_URL=http://192.168.123.1:8081 \\
                uvicorn bridge.main:app

License:
  Activate:  POST /license/activate  {"key": "abc123def456"}
  Status:    GET  /license/status

Autonomous mode:
  Bridge auto-switches to AUTONOMOUS when workstation is unreachable.
  Local brain handles safety, Q&A, exhibition FSM without Wi-Fi.
  Status: GET /brain/status
"""
import json
import logging
import os
import subprocess
import time
import threading
import urllib.request
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger("bridge")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bridge.adapter_base import RobotAdapter, normalize_capabilities
from bridge.mock_adapter import MockAdapter
from bridge.http_adapter import HttpAdapter
from bridge.h1_adapter import H1Adapter
from bridge.safety_pipeline import SafetyPipeline
from bridge.license_manager import LicenseManager
from bridge.local_brain import LocalBrain
from bridge.connectivity_monitor import ConnectivityMonitor
from bridge.event_buffer import EventBuffer
from bridge.watchdog import EdgeWatchdog
from bridge.local_behavior import LocalBehaviorManager
from bridge.local_navigator import LocalNavigator
from bridge.local_cache import LocalKnowledgeCache


# ── Configuration ────────────────────────────────────────────────────────

ADAPTER_TYPE = os.getenv("ADAPTER_TYPE", "mock")
ROBOT_URL = os.getenv("ROBOT_URL", "http://192.168.1.100:8000")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8080"))
POLL_HZ = int(os.getenv("POLL_HZ", "10"))
WORKSTATION_URL = os.getenv("WORKSTATION_URL", "http://localhost:8888")
DATA_DIR = os.getenv("DATA_DIR", "/data")
ACTIVATION_SERVER = os.getenv(
    "ACTIVATION_SERVER", "https://activate.partenit.ai"
)
DECISION_LOG_URL = os.getenv("DECISION_LOG_URL", "")  # e.g. http://decision_log:9114
# Watchdog: how long with no heartbeat before SAFE_FALLBACK.
# Live robot: 800ms. Sim/dev: increase to avoid false positives during idle gaps.
WATCHDOG_TIMEOUT_MS = int(os.getenv("WATCHDOG_TIMEOUT_MS", "800"))


# ── Decision log push ─────────────────────────────────────────────────────

def _push_decision(robot_id: str, command: str, gate) -> None:
    """Push GateResult to central decision_log asynchronously (fire-and-forget)."""
    if not DECISION_LOG_URL:
        return
    audit_ref = getattr(gate, "audit_ref", "") or "ISO 3691-4:2023"
    packet = {
        "robot_id": robot_id,
        "agent_id": robot_id,
        "command": command,
        "action_type": command,
        "verdict": gate.decision,
        "decision_type": gate.decision,
        "rule_id": gate.rule_id or "—",
        "rule_code": gate.rule_id or "—",
        "reason": gate.reason or "OK",
        "rationale": gate.reason or "OK",
        "standard": audit_ref,
        "audit_ref": audit_ref,
        "ts": time.time(),
        "timestamp_ms": int(time.time() * 1000),
        "source": "robot_bridge",
        "adapter": ADAPTER_TYPE,
    }

    def _post():
        try:
            body = json.dumps(packet).encode()
            req = urllib.request.Request(
                f"{DECISION_LOG_URL}/log",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=1.0)
        except Exception:
            pass  # never block the safety path

    threading.Thread(target=_post, daemon=True).start()


# ── State ────────────────────────────────────────────────────────────────

_adapter = None
_pipeline = SafetyPipeline()
_license_mgr = LicenseManager(data_dir=DATA_DIR, robot_api_url=None)
_brain = LocalBrain(data_dir=DATA_DIR)
_event_buf = EventBuffer(db_path=f"{DATA_DIR}/event_buffer.db")
_connectivity: ConnectivityMonitor | None = None
_cache = LocalKnowledgeCache(cache_dir=f"{DATA_DIR}/cache")
_local_behavior: LocalBehaviorManager | None = None
_local_navigator: LocalNavigator | None = None

_state_lock = threading.Lock()
_latest_state: dict = {}
_latest_entities: list[dict] = []
_poller_thread: threading.Thread | None = None
_running = False
_start_time = time.time()

_chat_history: list[dict] = []
_chat_history_lock = threading.Lock()
_CHAT_HISTORY_MAX = 100

# ── EdgeWatchdog ──────────────────────────────────────────────────────────────
# Fires SAFE_FALLBACK if upstream (operator_ui/sim_dashboard) stops heartbeating.
# Heartbeat is registered on every /robot/move, /robot/stop, /robot/heartbeat.

def _on_watchdog_fallback():
    """Called when watchdog fires: stop robot immediately."""
    logger.warning("SAFE_FALLBACK: watchdog timeout — stopping robot")
    if _local_behavior:
        _local_behavior.on_disconnect()
    else:
        # Fallback to original stop behavior
        try:
            if _adapter:
                _adapter.stop()
        except Exception as exc:
            logger.error("SAFE_FALLBACK stop failed: %s", exc)
    # Push SAFE_FALLBACK event to decision_log
    class _FallbackGate:
        decision = "DENY"
        reason = f"SAFE_FALLBACK: watchdog timeout ({WATCHDOG_TIMEOUT_MS}ms no heartbeat)"
        rule_id = "WATCHDOG-FALLBACK"
        audit_ref = "ISO 13482:2014 §5.4.2 (fail-safe on connectivity loss)"
        params = {}
    _push_decision("watchdog", "SAFE_FALLBACK", _FallbackGate())


def _on_watchdog_recover():
    logger.info("watchdog: upstream heartbeat restored")
    if _local_behavior:
        _local_behavior.on_reconnect()
    # Sync cache immediately on reconnect
    _cache.sync_now()


_watchdog = EdgeWatchdog(
    timeout_ms=WATCHDOG_TIMEOUT_MS,
    on_fallback=_on_watchdog_fallback,
    on_recover=_on_watchdog_recover,
)


# ── Adapter factory ─────────────────────────────────────────────────────

def _create_adapter():
    if ADAPTER_TYPE == "http":
        return HttpAdapter(robot_url=ROBOT_URL)
    if ADAPTER_TYPE == "h1":
        h1_url = os.getenv("ROBOT_URL", "http://192.168.123.1:8081")
        return H1Adapter(robot_url=h1_url)
    return MockAdapter()


# ── Background poller ────────────────────────────────────────────────────

def _poller():
    global _latest_state, _latest_entities
    dt = 1.0 / POLL_HZ
    while _running:
        try:
            state    = _adapter.get_state()
            entities = _adapter.get_entities()
            with _state_lock:
                _latest_state    = state
                _latest_entities = entities
        except Exception:
            pass
        time.sleep(dt)


def _on_mode_change(new_mode: str):
    """Called when connectivity monitor switches mode."""
    if new_mode == ConnectivityMonitor.MODE_AUTONOMOUS:
        if not _brain.is_loaded:
            _brain.load()
    _event_buf.write_event("mode_change", {
        "mode": new_mode, "ts": time.time(), "adapter": ADAPTER_TYPE
    })


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _adapter, _poller_thread, _running, _connectivity
    global _local_behavior, _local_navigator

    # 1. Verify license (offline, non-blocking)
    status = _license_mgr.verify()
    if status.profession_id:
        _brain._profession_id = status.profession_id

    # 2. Pre-load local brain (graceful — may not have files yet)
    _brain.load()

    # 3. Start adapter + poller
    _adapter = _create_adapter()
    _license_mgr._robot_api_url = ROBOT_URL  # use adapter URL for serial
    _running = True
    _poller_thread = threading.Thread(target=_poller, daemon=True)
    _poller_thread.start()

    # 3b. Initialize local behavior manager and navigator
    _local_behavior = LocalBehaviorManager(
        adapter=_adapter, brain=_brain, event_buffer=_event_buf
    )
    _local_navigator = LocalNavigator(
        adapter=_adapter, safety_gate=_brain._pipeline,
        event_buffer=_event_buf
    )
    _local_behavior.set_navigator(_local_navigator)

    # 3c. Configure cache and start sync
    _cache.configure(
        knowledge_url=os.getenv("KNOWLEDGE_SERVICE_URL", ""),
        nlgw_url=os.getenv("WORKSTATION_URL", ""),
    )
    _cache.start_sync()

    # 4. Start connectivity monitor
    _connectivity = ConnectivityMonitor(
        workstation_url=WORKSTATION_URL,
        on_mode_change=_on_mode_change,
    )
    _connectivity.start()

    # 5. Start EdgeWatchdog (200ms heartbeat, 800ms → SAFE_FALLBACK)
    _watchdog.start()

    # 5. Periodic event buffer maintenance (every hour)
    def _maintenance():
        while _running:
            time.sleep(3600)
            _event_buf.cleanup_old()
            _event_buf.enforce_size_limit()
    threading.Thread(target=_maintenance, daemon=True).start()

    yield

    _running = False
    _cache.stop_sync()
    if _connectivity:
        _connectivity.stop()
    if _poller_thread:
        _poller_thread.join(timeout=2)


# ── App ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Trust-Layer Robot Bridge",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ───────────────────────────────────────────────────────

class MoveRequest(BaseModel):
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0


class ActionRequest(BaseModel):
    action_id: str = ""
    action_type: str
    robot_id: str = ""
    target_position: Optional[dict] = None  # {x, y, z} in metres
    target_zone_id: str = ""
    target_object_id: str = ""
    target_speed_mps: float = 0.0
    constraints: dict = {}
    source: str = ""
    trace_id: str = ""


class ScenarioRequest(BaseModel):
    battery: float | None = None
    tilt_deg: float | None = None
    entities: list[dict] | None = None


# ── Endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Bridge health check."""
    with _state_lock:
        connected = _adapter.connected if _adapter else False
    wds = _watchdog.status()
    return {
        "status": "ok",
        "adapter": ADAPTER_TYPE,
        "connected": connected,
        "uptime_s": round(time.time() - _start_time, 1),
        "poll_hz": POLL_HZ,
        "watchdog": wds,
        "safe_fallback": wds["in_fallback"],
        "rules_loaded": _pipeline.get_stats().get("rules_loaded", 0),
        "rules_backend": _pipeline.get_stats().get("rules_backend", "unknown"),
    }


@app.get("/robot/state")
def robot_state():
    """Current robot telemetry."""
    with _state_lock:
        state = dict(_latest_state)
        entities = list(_latest_entities)
    state["entities"] = entities
    state["safety_stats"] = _pipeline.get_stats()
    return state


@app.post("/robot/move")
def robot_move(req: MoveRequest):
    """Send velocity command through safety pipeline."""
    if not _adapter:
        raise HTTPException(503, "Adapter not initialized")

    # Register heartbeat — upstream is alive
    _watchdog.heartbeat()

    # Refuse if in SAFE_FALLBACK (watchdog fired)
    if _watchdog.in_fallback:
        return {
            "requested": {"vx": req.vx, "vy": req.vy, "wz": req.wz},
            "applied": {"vx": 0.0, "vy": 0.0, "wz": 0.0},
            "gate": {
                "decision": "DENY",
                "reason": "SAFE_FALLBACK active — restore heartbeat first",
                "rule_id": "WATCHDOG-FALLBACK",
                "audit_ref": "claude.md §0.1",
                "params": {},
            },
            "send": {"status": "safe_fallback"},
        }

    with _state_lock:
        state = dict(_latest_state)
        entities = list(_latest_entities)

    # Run safety pipeline
    vx, vy, wz, gate = _pipeline.check(
        req.vx, req.vy, req.wz, state, entities,
    )

    # Push every decision to central decision_log (async, non-blocking)
    robot_id = state.get("robot_id", state.get("name", f"bridge-{ADAPTER_TYPE}"))
    cmd_str = f"move vx={req.vx:.2f} vy={req.vy:.2f} wz={req.wz:.2f}"
    _push_decision(robot_id, cmd_str, gate)

    result = {
        "requested": {"vx": req.vx, "vy": req.vy, "wz": req.wz},
        "applied": {"vx": round(vx, 3), "vy": round(vy, 3), "wz": round(wz, 3)},
        "gate": {
            "decision": gate.decision,
            "reason": gate.reason,
            "rule_id": gate.rule_id,
            "audit_ref": getattr(gate, "audit_ref", ""),
            "params": gate.params,
        },
    }

    # Forward to adapter only if not fully denied
    if gate.decision != "DENY":
        send_result = _adapter.send_velocity(vx, vy, wz)
        result["send"] = send_result
    else:
        _adapter.stop()
        result["send"] = {"status": "denied_stop"}

    return result


@app.post("/robot/stop")
def robot_stop():
    """Emergency stop."""
    if not _adapter:
        raise HTTPException(503, "Adapter not initialized")
    _watchdog.heartbeat()  # operator is present
    result = _adapter.stop()
    return result


@app.post("/robot/heartbeat")
def robot_heartbeat():
    """Explicit heartbeat from upstream — resets watchdog timer."""
    _watchdog.heartbeat()
    with _state_lock:
        state = dict(_latest_state)
    return {
        "ok": True,
        "in_fallback": _watchdog.in_fallback,
        "battery": state.get("battery", None),
        "position": state.get("position", None),
        "cache_age_s": round(_cache.last_sync_age, 1),
        "local_safety_active": (
            _local_behavior.is_disconnected
            if _local_behavior else False
        ),
    }


@app.get("/robot/local_behavior")
def get_local_behavior():
    """Local behavior manager, cache, and navigator status."""
    return {
        "behavior": (
            _local_behavior.status()
            if _local_behavior else {}
        ),
        "cache": _cache.stats(),
        "navigator": {
            "active": _local_navigator is not None
        },
        "brain": _brain.status_dict() if _brain else {},
    }


@app.get("/watchdog/status")
def watchdog_status():
    """EdgeWatchdog status: timeout threshold, fallback state, last heartbeat age."""
    return _watchdog.status()


@app.get("/robot/reasoning")
def robot_reasoning():
    """Recent safety reasoning messages."""
    msgs = _pipeline.get_reasoning(clear=True)
    return {"messages": msgs, "count": len(msgs)}


@app.post("/scenario/inject")
def scenario_inject(req: ScenarioRequest):
    """Inject test scenario conditions."""
    if not _adapter:
        raise HTTPException(503, "Adapter not initialized")

    overrides = {}
    if req.battery is not None:
        overrides["battery"] = req.battery
    if req.tilt_deg is not None:
        overrides["tilt_deg"] = req.tilt_deg
    if req.entities is not None:
        overrides["entities"] = req.entities

    _adapter.inject_scenario(overrides)
    return {"status": "injected", "overrides": list(overrides.keys())}


@app.post("/scenario/clear")
def scenario_clear():
    """Clear all scenario overrides, reset to nominal."""
    if not _adapter:
        raise HTTPException(503, "Adapter not initialized")
    _adapter.clear_scenario()
    return {"status": "cleared"}


@app.get("/pipeline/stats")
def pipeline_stats():
    """Safety pipeline statistics."""
    return _pipeline.get_stats()


# ── Action endpoint ──────────────────────────────────────────────────────


class _AllowGate:
    """Lightweight gate stub for actions that skip the safety pipeline."""
    decision = "ALLOW"
    reason = "safe action"
    rule_id = ""
    audit_ref = ""
    params: dict = {}


@app.post("/robot/action")
def robot_action(req: ActionRequest):
    """Execute a StandardAction on the robot.

    Dispatches by action_type:
      stop / e_stop   — emergency stop (always allowed)
      wait / idle     — no-op acknowledgement
      navigate_to     — drive to target_position {x, y, z}
      scan            — camera capture
    All movement actions run through the safety pipeline.
    """
    if not _adapter:
        raise HTTPException(503, "Adapter not initialized")

    _watchdog.heartbeat()

    if _watchdog.in_fallback:
        return {
            "status": "denied",
            "action_id": req.action_id,
            "action_type": req.action_type,
            "reason": "SAFE_FALLBACK active — restore heartbeat first",
            "rule_id": "WATCHDOG-FALLBACK",
        }

    atype = req.action_type

    # ── stop / e_stop ────────────────────────────────────────────────────
    if atype in ("stop", "e_stop"):
        result = _adapter.stop()
        _push_decision(
            req.robot_id or f"bridge-{ADAPTER_TYPE}",
            f"action:{atype}",
            _AllowGate(),
        )
        return {
            "status": "ok",
            "action_id": req.action_id,
            "action_type": atype,
            "result": result,
        }

    # ── wait / idle ───────────────────────────────────────────────────────
    if atype in ("wait", "idle"):
        return {
            "status": "ok",
            "action_id": req.action_id,
            "action_type": atype,
        }

    # ── navigate_to ───────────────────────────────────────────────────────
    if atype == "navigate_to":
        pos = req.target_position or {}
        x_m = float(pos.get("x", 0.0))
        y_m = float(pos.get("y", 0.0))
        speed = req.target_speed_mps or 0.3

        with _state_lock:
            state = dict(_latest_state)
            entities = list(_latest_entities)

        _, _, _, gate = _pipeline.check(speed, 0.0, 0.0, state, entities)
        robot_id = state.get(
            "robot_id", req.robot_id or f"bridge-{ADAPTER_TYPE}"
        )
        _push_decision(
            robot_id, f"action:navigate_to ({x_m:.1f},{y_m:.1f})", gate
        )

        if gate.decision == "DENY":
            return {
                "status": "denied",
                "action_id": req.action_id,
                "action_type": atype,
                "reason": gate.reason,
                "rule_id": gate.rule_id,
            }

        nav_result = _adapter.navigate_to(x_m, y_m, speed_mps=speed)
        return {
            "status": "ok",
            "action_id": req.action_id,
            "action_type": atype,
            "result": nav_result,
        }

    # ── scan ──────────────────────────────────────────────────────────────
    if atype == "scan":
        if hasattr(_adapter, "capture_photo"):
            photo = _adapter.capture_photo()
            return {
                "status": "ok",
                "action_id": req.action_id,
                "action_type": atype,
                "result": photo,
            }
        return {
            "status": "not_supported",
            "action_id": req.action_id,
            "action_type": atype,
            "note": "Camera not available on this adapter",
        }

    # ── find_and_approach ─────────────────────────────────────────────────
    if atype == "find_and_approach":
        return {
            "status": "not_supported",
            "action_id": req.action_id,
            "action_type": atype,
            "note": (
                "find_and_approach requires VLM pipeline "
                "— handled by nl_command_gateway"
            ),
        }

    return {
        "status": "unknown_action",
        "action_id": req.action_id,
        "action_type": atype,
        "note": f"Unknown action_type: {atype}",
    }


# ── LiDAR endpoint ───────────────────────────────────────────────────────


@app.get("/lidar/scan")
def lidar_scan():
    """Latest LiDAR scan data.

    Returns scan from:
      1. Adapter get_lidar_scan() if it provides real data
      2. ROS2 /scan topic presence (detected, not subscribed)
      3. Error response if no LiDAR found
    """
    scan = _adapter.get_lidar_scan() if _adapter else None
    if scan and scan.get("available"):
        return scan

    ros = _ros_discover()
    lidar_caps = ros.get("capabilities", {}).get("lidar", {})
    if ros.get("available") and lidar_caps.get("ros_available"):
        topics = lidar_caps.get("topics", [])
        return {
            "available": True,
            "source": "ros2_detected",
            "topics": topics,
            "note": (
                "LiDAR detected via ROS2 discovery. "
                "Subscribe to topic directly for scan data."
            ),
        }

    return {
        "available": False,
        "error": "no_lidar",
        "note": "LiDAR not detected on this robot",
    }


# ── Voice endpoints ──────────────────────────────────────────────────────

class SpeakRequest(BaseModel):
    text: str
    language: str = "ru"


@app.post("/voice/speak")
def voice_speak(req: SpeakRequest):
    """Text-to-speech: send text to robot's speaker.

    If robot has TTS hardware, forwards to it.
    Otherwise returns the text for client-side TTS.
    """
    if _adapter and hasattr(_adapter, "speak"):
        result = _adapter.speak(req.text, req.language)
        return {"ok": True, "method": "robot_tts", **result}
    # Fallback: return text for client-side Web Speech API
    return {
        "ok": True,
        "method": "client_tts",
        "text": req.text,
        "language": req.language,
    }


class SttResult(BaseModel):
    text: str = ""
    language: str = ""
    confidence: float = 0.0


@app.get("/voice/listen")
def voice_listen():
    """Speech-to-text: get transcription from robot's mic.

    If robot has STT hardware, returns transcription.
    Otherwise returns empty (client should use Web Speech API).
    """
    if _adapter and hasattr(_adapter, "listen"):
        result = _adapter.listen()
        return {"ok": True, "method": "robot_stt", **result}
    return {
        "ok": True,
        "method": "client_stt",
        "text": "",
        "message": "Use Web Speech API on client",
    }


# ── Camera endpoints ─────────────────────────────────────────────────────

@app.post("/camera/capture")
def camera_capture():
    """Capture a photo from robot's camera.

    Returns base64-encoded JPEG or URL to download.
    """
    if _adapter and hasattr(_adapter, "capture_photo"):
        result = _adapter.capture_photo()
        return {"ok": True, **result}
    return {
        "ok": False,
        "error": "Camera not available on this adapter",
    }


@app.get("/camera/status")
def camera_status():
    """Camera hardware status."""
    with _state_lock:
        state = dict(_latest_state)
    sensors = state.get("sensors", {})
    camera = sensors.get("camera", {})
    return {
        "available": camera.get("health", 0) > 0.5,
        "fps": camera.get("fps", 0),
        "health": camera.get("health", 0),
    }


# ── ROS discovery ────────────────────────────────────────────────────────

# Maps topic patterns → capability key
_ROS_TOPIC_MAP: list[tuple[list[str], str]] = [
    (["/camera/image_raw", "/camera/color/image_raw", "/image_raw",
      "/camera/rgb/image_raw", "/head_camera/image_raw"],        "camera"),
    (["/scan", "/lidar/scan", "/points", "/velodyne_points",
      "/lidar_points", "/laser_scan"],                           "lidar"),
    (["/imu/data", "/imu_data", "/imu", "/imu/raw"],             "imu"),
    (["/odom", "/wheel_odom", "/odometry/filtered",
      "/odometry/local"],                                         "odometry"),
    (["/cmd_vel", "/cmd_vel_safe", "/cmd_vel_mux/input/navi"],   "drive"),
    (["/battery_state", "/battery", "/power_supply_state"],      "battery"),
    (["/joint_states", "/joint_state"],                          "joints"),
    (["/depth/image_raw", "/camera/depth/image_raw",
      "/depth_registered/image_raw"],                            "depth_camera"),
    (["/diagnostics", "/diagnostics_agg"],                       "diagnostics"),
    (["/tf", "/tf_static"],                                       "tf"),
    (["/map", "/map_metadata"],                                   "mapping"),
    (["/amcl_pose", "/localization_pose"],                        "localization"),
    (["/path", "/plan", "/move_base/NavfnROS/plan"],              "navigation"),
    (["/audio", "/audio_capture", "/recognizer/output"],          "audio"),
]

_ROS_NODE_MAP: list[tuple[list[str], str]] = [
    (["nav2_", "move_base", "navfn"],                             "navigation"),
    (["slam_", "cartographer", "hector_slam"],                    "slam"),
    (["realsense", "camera_node", "image_proc"],                  "camera"),
    (["imu_", "microstrain", "phidgets_imu"],                     "imu"),
    (["lidar_", "velodyne_", "rplidar", "hokuyo"],                "lidar"),
    (["audio_capture", "sound_play", "tts_"],                     "audio"),
]


def _ros_discover() -> dict:
    """Run ros2 topic and node discovery via subprocess.

    Returns dict with:
      available (bool), topics (list), nodes (list), capabilities (dict of key→status)
    """
    result: dict = {"available": False, "topics": [], "nodes": [], "capabilities": {}}
    try:
        raw_topics = subprocess.check_output(
            ["ros2", "topic", "list", "-t"],
            timeout=5.0, text=True, stderr=subprocess.DEVNULL,
        ).strip().splitlines()
        raw_nodes = subprocess.check_output(
            ["ros2", "node", "list"],
            timeout=5.0, text=True, stderr=subprocess.DEVNULL,
        ).strip().splitlines()
    except FileNotFoundError:
        result["error"] = "ros2_not_installed"
        return result
    except subprocess.TimeoutExpired:
        result["error"] = "ros2_timeout"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result

    result["available"] = True

    # Parse topic names (format: "/name [pkg/msg/Type]")
    topic_names: set[str] = set()
    topic_types: dict[str, str] = {}
    for line in raw_topics:
        parts = line.strip().split()
        if parts:
            topic_names.add(parts[0])
            if len(parts) >= 2:
                topic_types[parts[0]] = parts[1].strip("[]")

    result["topics"] = sorted(topic_names)
    result["nodes"] = sorted(t.strip() for t in raw_nodes if t.strip())

    # Map topics → capabilities
    caps: dict[str, dict] = {}
    for topic_list, cap_key in _ROS_TOPIC_MAP:
        found = [t for t in topic_list if t in topic_names]
        if found:
            caps[cap_key] = {
                "ros_available": True,
                "topics": found,
                "type": topic_types.get(found[0], ""),
            }

    # Supplement with node hints
    node_str = " ".join(result["nodes"])
    for node_patterns, cap_key in _ROS_NODE_MAP:
        if any(p in node_str for p in node_patterns):
            caps.setdefault(cap_key, {})["ros_nodes_detected"] = True

    result["capabilities"] = caps
    return result


# ── Capabilities endpoint ────────────────────────────────────────────────

@app.get("/robot/capabilities")
def robot_capabilities():
    """Active hardware capability scan. Probes all subsystems and returns status report.

    Each capability: available (bool), probe (str), health (float 0-1), note (str).
    probe values: ok | degraded | not_installed | disconnected | client_*_fallback | state_only
    """
    scanned_at = time.time()

    # Use adapter's probe (guaranteed by RobotAdapter ABC)
    if _adapter and isinstance(_adapter, RobotAdapter):
        caps = _adapter.probe_capabilities()
    else:
        # Derive from latest polled state as fallback
        with _state_lock:
            state = dict(_latest_state)
        sensors = state.get("sensors", {})
        caps = {}
        for name in ("camera", "lidar", "imu"):
            s = sensors.get(name, {})
            ok = s.get("health", 0) > 0.3 or s.get("available", False)
            caps[name] = {
                "available": ok, "health": s.get("health", 0),
                "probe": "state_only", "note": "Derived from telemetry, not actively probed",
            }
        bat = state.get("battery", 0)
        caps["microphone"] = {"available": False, "probe": "unknown", "method": "client_stt"}
        caps["speaker"] = {"available": False, "probe": "unknown", "method": "client_tts"}
        caps["drive"] = {
            "available": (_adapter.connected if _adapter else False),
            "probe": "state_only", "type": "holonomic", "max_speed_mps": 0.8,
        }
        caps["battery"] = {
            "available": True, "level_pct": bat, "probe": "state_only",
            "estimated_runtime_min": int(bat * 2.4) if bat > 0 else 0,
        }
        caps["network"] = {
            "available": (_adapter.connected if _adapter else False),
            "probe": "state_only", "adapter": ADAPTER_TYPE,
        }

    # Annotate voice capabilities from bridge level (adapter-agnostic)
    caps.setdefault("microphone", {})["bridge_stt"] = (
        hasattr(_adapter, "listen") if _adapter else False
    )
    caps.setdefault("speaker", {})["bridge_tts"] = (
        hasattr(_adapter, "speak") if _adapter else False
    )

    # ROS discovery (best-effort, never blocks capabilities from being returned)
    ros = _ros_discover()
    if ros.get("available"):
        # Merge ROS-discovered capabilities into caps dict
        for ros_cap_key, ros_info in ros["capabilities"].items():
            entry = caps.setdefault(ros_cap_key, {})
            # Only upgrade available status if not already set by adapter
            if "available" not in entry:
                entry["available"] = ros_info.get("ros_available", False)
                entry["probe"] = "ros_discovery"
            entry["ros_topics"] = ros_info.get("topics", [])
            entry["ros_type"] = ros_info.get("type", "")
            if ros_info.get("ros_nodes_detected"):
                entry["ros_nodes_detected"] = True
            if not entry.get("note"):
                entry["note"] = f"ROS topic: {', '.join(ros_info.get('topics', []))}"
    else:
        # Mark capabilities that could only come from ROS as unknown
        for ros_cap_key, _ in _ROS_TOPIC_MAP:
            pass  # adapter probes are authoritative; no need to downgrade

    # Normalize: fill missing required fields so all adapters return the same schema
    caps = normalize_capabilities(caps)

    # Readiness score: fraction of core subsystems that are available
    available_count = sum(1 for c in caps.values() if c.get("available"))
    total = len(caps)

    return {
        "capabilities": caps,
        "adapter": ADAPTER_TYPE,
        "robot_url": ROBOT_URL if ADAPTER_TYPE != "mock" else None,
        "scanned_at": scanned_at,
        "scan_duration_ms": round((time.time() - scanned_at) * 1000, 1),
        "readiness": {
            "score": round(available_count / total, 2) if total else 0,
            "available": available_count,
            "total": total,
        },
        "ros_discovery": {
            "available": ros.get("available", False),
            "topics_found": len(ros.get("topics", [])),
            "nodes_found": len(ros.get("nodes", [])),
            "error": ros.get("error"),
            "topics": ros.get("topics", []),
            "nodes": ros.get("nodes", []),
        },
    }


@app.get("/camera/frame")
def camera_frame():
    """Single camera frame for preview. Returns base64 JPEG or SVG placeholder."""
    if _adapter and hasattr(_adapter, "capture_photo"):
        result = _adapter.capture_photo()
        return {"ok": True, **result}
    if ADAPTER_TYPE == "mock":
        # SVG test pattern as placeholder
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='320' height='240'>"
            "<rect width='100%' height='100%' fill='#0a0a14'/>"
            "<rect x='10' y='10' width='300' height='220' fill='none' stroke='#5B4CE033' stroke-width='1'/>"
            "<text x='160' y='110' fill='#5B4CE0' font-family='monospace' font-size='13' "
            "text-anchor='middle'>MOCK CAMERA</text>"
            "<text x='160' y='132' fill='#64748b' font-family='monospace' font-size='11' "
            "text-anchor='middle'>320 × 240 · 15 fps</text>"
            "<circle cx='160' cy='168' r='18' fill='none' stroke='#5B4CE044' stroke-width='1'/>"
            "<circle cx='160' cy='168' r='6' fill='#5B4CE066'/>"
            "</svg>"
        )
        return {"ok": True, "method": "mock_svg", "format": "svg", "data": svg}
    return {"ok": False, "error": "Camera capture not supported on this adapter"}


# ── License endpoints ────────────────────────────────────────────────────

class ActivateRequest(BaseModel):
    key: str
    activation_server_url: Optional[str] = None


class ApplyTokenRequest(BaseModel):
    token_jwt: str


@app.get("/license/status")
def license_status():
    """Current license state."""
    return _license_mgr.status_dict()


@app.post("/license/activate")
def license_activate(req: ActivateRequest):
    """Activate license online (or return offline request if server unreachable)."""
    server_url = req.activation_server_url or ACTIVATION_SERVER
    status = _license_mgr.activate_online(req.key, server_url)
    if status.state.value == "ACTIVE":
        _brain._profession_id = status.profession_id
    return {
        "result": status.state.value,
        **_license_mgr.status_dict(),
    }


@app.get("/license/activation-request")
def license_activation_request(key: str):
    """Offline flow step 1: generate activation request payload."""
    return _license_mgr.generate_activation_request(key)


@app.post("/license/apply-token")
def license_apply_token(req: ApplyTokenRequest):
    """Offline flow step 3: apply signed JWT received from activation server."""
    status = _license_mgr.apply_activation_response(req.token_jwt)
    if status.state.value == "ACTIVE":
        _brain._profession_id = status.profession_id
    return {
        "result": status.state.value,
        **_license_mgr.status_dict(),
    }


# ── Brain / Autonomous mode endpoints ────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    language: str = "ru"


@app.get("/brain/status")
def brain_status():
    """Local brain and connectivity status."""
    conn = _connectivity.status_dict() if _connectivity else {"mode": "CONNECTED"}
    return {
        "connectivity": conn,
        "brain":        _brain.status_dict(),
        "event_buffer": _event_buf.stats(),
    }


@app.post("/brain/sync")
def brain_sync():
    """Trigger manual sync: upload buffered events to workstation."""
    pending = _event_buf.get_pending(limit=200)
    if not pending:
        return {"status": "nothing_to_sync", "pending": 0}

    import urllib.request
    import json as _json
    synced_ids = []
    errors = []
    for evt in pending:
        try:
            body = _json.dumps(evt).encode()
            req = urllib.request.Request(
                f"{WORKSTATION_URL}/events/ingest",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            synced_ids.append(evt["id"])
        except Exception as exc:
            errors.append(str(exc))

    if synced_ids:
        _event_buf.mark_synced(synced_ids)

    return {
        "status":   "ok" if not errors else "partial",
        "synced":   len(synced_ids),
        "failed":   len(errors),
        "errors":   errors[:3],
    }


class ChatSendRequest(BaseModel):
    message: str = ""  # command word: stop, move_forward, move_backward, turn_left, turn_right, …


_CHAT_SEND_MAP = {
    "stop":           (0.0,  0.0, 0.0,  True),   # (vx, vy, wz, is_stop)
    "move_forward":   (0.3,  0.0, 0.0,  False),
    "move_backward":  (-0.3, 0.0, 0.0,  False),
    "turn_left":      (0.0,  0.0, 0.5,  False),
    "turn_right":     (0.0,  0.0, -0.5, False),
}


@app.post("/chat/send")
def chat_send(req: ChatSendRequest):
    """Accept command word from sim_dashboard / operator_ui and execute via safety pipeline.

    Drop-in replacement for Isaac Sim bridge /chat/send — allows operator_ui + sim_dashboard
    to work identically with real robot (ADAPTER_TYPE=http) and simulation (ADAPTER_TYPE=mock).
    """
    cmd = req.message.strip().lower()

    if not _adapter:
        return {"status": "error", "reason": "adapter_not_initialized"}

    if cmd == "stop" or cmd not in _CHAT_SEND_MAP:
        # Unknown commands → safe stop
        _adapter.stop()
        if cmd not in _CHAT_SEND_MAP and cmd not in ("look around", "wave", "bow", "sit", "stand"):
            return {"status": "received", "command": cmd, "executed": "stop_fallback"}
        return {"status": "received", "command": cmd, "executed": "stop"}

    vx, vy, wz, _ = _CHAT_SEND_MAP[cmd]
    with _state_lock:
        state = dict(_latest_state)
        entities = list(_latest_entities)

    vx_safe, vy_safe, wz_safe, gate = _pipeline.check(vx, vy, wz, state, entities)
    if gate.decision != "DENY":
        _adapter.send_velocity(vx_safe, vy_safe, wz_safe)
    else:
        _adapter.stop()

    return {
        "status": "received",
        "command": cmd,
        "gate": gate.decision,
        "rule_id": gate.rule_id,
    }


def _append_chat(role: str, text: str) -> None:
    with _chat_history_lock:
        _chat_history.insert(0, {"role": role, "message": text, "ts": time.time()})
        if len(_chat_history) > _CHAT_HISTORY_MAX:
            _chat_history.pop()


@app.get("/chat/history")
def chat_history():
    """Return recent chat messages (newest first)."""
    with _chat_history_lock:
        return list(_chat_history)


@app.post("/chat")
def chat(req: ChatRequest):
    """Q&A chat — routes to local brain when in AUTONOMOUS mode."""
    _append_chat("user", req.message)
    mode = _connectivity.mode if _connectivity else "CONNECTED"

    if mode != "CONNECTED":
        answer = _brain.answer_question(req.message, req.language)
        _event_buf.write_event("qa_log", {
            "question": req.message,
            "answer":   answer,
            "mode":     mode,
            "ts":       time.time(),
        })
        _append_chat("robot", answer)
        return {
            "reply":  answer,
            "source": "local_brain",
            "mode":   mode,
        }

    # Connected: forward to workstation LLM
    import urllib.request
    import json as _json
    try:
        body = _json.dumps({
            "message":  req.message,
            "language": req.language,
        }).encode()
        request = urllib.request.Request(
            f"{WORKSTATION_URL}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            data = _json.loads(resp.read())
        _append_chat("robot", data.get("reply", ""))
        return {**data, "source": "workstation_llm", "mode": mode}
    except Exception as exc:
        # LLM unavailable — fall back to local brain
        answer = _brain.answer_question(req.message, req.language)
        _append_chat("robot", answer)
        return {
            "reply":  answer,
            "source": "local_brain_fallback",
            "mode":   mode,
            "error":  str(exc),
        }


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "bridge.main:app",
        host="0.0.0.0",
        port=BRIDGE_PORT,
        reload=False,
    )
