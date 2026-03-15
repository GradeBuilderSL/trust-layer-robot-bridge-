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
import os
import time
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bridge.mock_adapter import MockAdapter
from bridge.http_adapter import HttpAdapter
from bridge.h1_adapter import H1Adapter
from bridge.safety_pipeline import SafetyPipeline
from bridge.license_manager import LicenseManager
from bridge.local_brain import LocalBrain
from bridge.connectivity_monitor import ConnectivityMonitor
from bridge.event_buffer import EventBuffer


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


# ── State ────────────────────────────────────────────────────────────────

_adapter = None
_pipeline = SafetyPipeline()
_license_mgr = LicenseManager(data_dir=DATA_DIR, robot_api_url=None)
_brain = LocalBrain(data_dir=DATA_DIR)
_event_buf = EventBuffer(db_path=f"{DATA_DIR}/event_buffer.db")
_connectivity: ConnectivityMonitor | None = None

_state_lock = threading.Lock()
_latest_state: dict = {}
_latest_entities: list[dict] = []
_poller_thread: threading.Thread | None = None
_running = False
_start_time = time.time()


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

    # 4. Start connectivity monitor
    _connectivity = ConnectivityMonitor(
        workstation_url=WORKSTATION_URL,
        on_mode_change=_on_mode_change,
    )
    _connectivity.start()

    # 5. Periodic event buffer maintenance (every hour)
    def _maintenance():
        while _running:
            time.sleep(3600)
            _event_buf.cleanup_old()
            _event_buf.enforce_size_limit()
    threading.Thread(target=_maintenance, daemon=True).start()

    yield

    _running = False
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
    return {
        "status": "ok",
        "adapter": ADAPTER_TYPE,
        "connected": connected,
        "uptime_s": round(time.time() - _start_time, 1),
        "poll_hz": POLL_HZ,
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

    with _state_lock:
        state = dict(_latest_state)
        entities = list(_latest_entities)

    # Run safety pipeline
    vx, vy, wz, gate = _pipeline.check(
        req.vx, req.vy, req.wz, state, entities,
    )

    result = {
        "requested": {"vx": req.vx, "vy": req.vy, "wz": req.wz},
        "applied": {"vx": round(vx, 3), "vy": round(vy, 3), "wz": round(wz, 3)},
        "gate": {
            "decision": gate.decision,
            "reason": gate.reason,
            "rule_id": gate.rule_id,
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
    result = _adapter.stop()
    return result


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


@app.post("/chat")
def chat(req: ChatRequest):
    """Q&A chat — routes to local brain when in AUTONOMOUS mode."""
    mode = _connectivity.mode if _connectivity else "CONNECTED"

    if mode != "CONNECTED":
        answer = _brain.answer_question(req.message, req.language)
        _event_buf.write_event("qa_log", {
            "question": req.message,
            "answer":   answer,
            "mode":     mode,
            "ts":       time.time(),
        })
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
        return {**data, "source": "workstation_llm", "mode": mode}
    except Exception as exc:
        # LLM unavailable — fall back to local brain
        answer = _brain.answer_question(req.message, req.language)
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
