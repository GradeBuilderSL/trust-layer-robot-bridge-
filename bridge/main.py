"""Trust-Layer Robot Bridge — FastAPI app that runs on robot or locally.

Connects to the real Noetix N2 (via HTTP adapter) or simulates it (mock adapter).
Exposes endpoints for the Test Dashboard to call.
Runs the local safety pipeline on every move command.
"""
import os
import time
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bridge.mock_adapter import MockAdapter
from bridge.http_adapter import HttpAdapter
from bridge.safety_pipeline import SafetyPipeline


# ── Configuration ────────────────────────────────────────────────────────

ADAPTER_TYPE = os.getenv("ADAPTER_TYPE", "mock")       # mock | http
ROBOT_URL = os.getenv("ROBOT_URL", "http://192.168.1.100:8000")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8080"))
POLL_HZ = int(os.getenv("POLL_HZ", "10"))


# ── State ────────────────────────────────────────────────────────────────

_adapter = None
_pipeline = SafetyPipeline()
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
    return MockAdapter()


# ── Background poller ────────────────────────────────────────────────────

def _poller():
    global _latest_state, _latest_entities
    dt = 1.0 / POLL_HZ
    while _running:
        try:
            state = _adapter.get_state()
            entities = _adapter.get_entities()
            with _state_lock:
                _latest_state = state
                _latest_entities = entities
        except Exception:
            pass
        time.sleep(dt)


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _adapter, _poller_thread, _running
    _adapter = _create_adapter()
    _running = True
    _poller_thread = threading.Thread(target=_poller, daemon=True)
    _poller_thread.start()
    yield
    _running = False
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


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "bridge.main:app",
        host="0.0.0.0",
        port=BRIDGE_PORT,
        reload=False,
    )
