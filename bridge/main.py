"""Trust-Layer Robot Bridge — FastAPI app that runs on robot or locally.

Connects to real robot (HTTP/H1 adapter) or simulates (mock adapter).
Exposes endpoints for the Test Dashboard to call.
Runs the local safety pipeline on every move command.

Adapter types (ADAPTER_TYPE env var):
  mock  — simulated Noetix N2 (default, no hardware needed)
  http  — real Noetix N2 via HTTP API
  h1    — Unitree H1 humanoid via h1_server.py on onboard PC
  e1    — Noetix E1 humanoid via e1_server.py on onboard Jetson Orin Nano Super

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
import math
import os
import subprocess
import sys
import time
import threading
import urllib.request
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger("bridge")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bridge.adapter_base import RobotAdapter, normalize_capabilities
from bridge.mock_adapter import MockAdapter
from bridge.http_adapter import HttpAdapter
from bridge.h1_adapter import H1Adapter
from bridge.e1_adapter import E1Adapter
from bridge.mujoco_adapter import MujocoAdapter

# Tier taxonomy (T0/T1/T2/T3) — lives in libs/safety/tiers.py. Fail-open so
# bridges without libs/ mounted still work.
try:
    _LIBS_DIR = os.environ.get("TRUST_LAYER_LIBS", "/app/libs")
    if _LIBS_DIR not in sys.path:
        sys.path.insert(0, _LIBS_DIR)
    from safety.tiers import (  # type: ignore[import-not-found]
        assign_tier, tier_requires_approval, tier_requires_quorum,
        T0_OBSERVE, T1_PREPARE, T2_ACT, T3_COMMIT,
    )
    # Ed25519 capability tokens (SINT-style). Optional — bridges without
    # CAPABILITY_TOKENS_REQUIRED=1 still accept unsigned requests for
    # backwards compatibility.
    from safety.capability_tokens import (  # type: ignore[import-not-found]
        Ed25519TokenVerifier, CapabilityToken, RevocationStore,
    )
    # Agent trust tracker feeds Δ_trust into tier_ctx so the same tier
    # taxonomy escalates misbehaving agents automatically.
    from safety.agent_trust import AgentTrustTracker  # type: ignore[import-not-found]
    from safety.forbidden_combos import ForbiddenCombosDetector  # type: ignore[import-not-found]
    # HITL — synchronous operator-in-the-loop approvals. We reuse it
    # for the ISO 13482 §5.7.2 speed-near-human gate: when a move
    # command requests speed > 0.5 m/s with a person within 1.5 m,
    # the bridge blocks here until the operator approves / denies via
    # /hitl/respond. Timeout → safe deny.
    from safety.human_in_the_loop import HumanInTheLoop  # type: ignore[import-not-found]
    _TIERS_LOADED = True
except Exception as _tier_exc:
    _TIERS_LOADED = False
    T0_OBSERVE = "T0_OBSERVE"; T1_PREPARE = "T1_PREPARE"
    T2_ACT = "T2_ACT"; T3_COMMIT = "T3_COMMIT"

    def assign_tier(action_type, *, resource="", context=None):  # type: ignore[misc]
        class _Fallback:
            tier = T2_ACT
            base_tier = T2_ACT
            escalations: list = []
            reason = "tiers lib not loaded"
            def to_dict(self):
                return {"tier": self.tier, "base_tier": self.base_tier,
                        "escalations": [], "reason": self.reason}
        return _Fallback()

    def tier_requires_approval(tier):  # type: ignore[misc]
        return tier in (T2_ACT, T3_COMMIT)

    def tier_requires_quorum(tier):  # type: ignore[misc]
        return tier == T3_COMMIT

    Ed25519TokenVerifier = None  # type: ignore[misc,assignment]
    CapabilityToken = None  # type: ignore[misc,assignment]
    RevocationStore = None  # type: ignore[misc,assignment]
    AgentTrustTracker = None  # type: ignore[misc,assignment]
    ForbiddenCombosDetector = None  # type: ignore[misc,assignment]


# ── Shared trust tracker + forbidden-combo detector ───────────────────────
_trust_tracker = AgentTrustTracker() if AgentTrustTracker is not None else None
# HITL queue — used by the speed-near-human gate on /robot/action.
# None if libs/ isn't mounted (older deployments); the gate checks
# this and falls through to the standard DENY path when absent.
_hitl: Optional["HumanInTheLoop"] = (
    HumanInTheLoop() if _TIERS_LOADED else None
)
_forbidden_combos = ForbiddenCombosDetector() if ForbiddenCombosDetector is not None else None


# ── Capability token verifier state ───────────────────────────────────────
#
# Bridges start with CAPABILITY_TOKENS_REQUIRED=0 (backwards compatible). When
# set to 1, every /robot/move, /robot/action, /robot/stop must carry a valid
# X-Capability-Token header. Key material is fetched from license_service
# /v1/tokens/public_key at startup; if that fails and we're in required
# mode, bridge fails closed.
CAPABILITY_TOKENS_REQUIRED = os.getenv("CAPABILITY_TOKENS_REQUIRED", "0") == "1"
LICENSE_SERVICE_URL = os.getenv("LICENSE_SERVICE_URL", "http://license_service:9600")
_token_verifier = None
_token_revocations = None


def _init_token_verifier() -> None:
    """Fetch the issuer's public key from license_service and build a verifier."""
    global _token_verifier, _token_revocations
    if Ed25519TokenVerifier is None:
        return
    _token_revocations = RevocationStore()
    try:
        req = urllib.request.Request(
            f"{LICENSE_SERVICE_URL.rstrip('/')}/v1/tokens/public_key",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        if CAPABILITY_TOKENS_REQUIRED:
            logger.error(
                "capability tokens REQUIRED but license_service public key unreachable (%s) — fail-closed",
                exc,
            )
        else:
            logger.info(
                "capability tokens optional — license_service key unreachable (%s), running without verifier",
                exc,
            )
        return

    try:
        import base64
        pub_b64 = data.get("public_key_b64", "")
        padding = "=" * (-len(pub_b64) % 4)
        pub_bytes = base64.urlsafe_b64decode(pub_b64 + padding)
        _token_verifier = Ed25519TokenVerifier(
            trusted_issuers={"license_service": pub_bytes},
            revocations=_token_revocations,
        )
        logger.info(
            "capability tokens: verifier ready (issuer=license_service, required=%s)",
            CAPABILITY_TOKENS_REQUIRED,
        )
    except Exception as exc:
        logger.error("capability tokens: verifier init failed: %s", exc)


def _verify_capability_token(
    request: "Request | None",
    resource: str,
    action: str,
    physical: dict,
) -> tuple[bool, str, dict]:
    """Return (ok, reason, token_info). Fail-open when tokens are optional
    and no header is present; fail-closed otherwise.
    """
    if not CAPABILITY_TOKENS_REQUIRED and _token_verifier is None:
        return True, "tokens-disabled", {}
    if request is None:
        return (not CAPABILITY_TOKENS_REQUIRED), "no-request-object", {}

    header = request.headers.get("x-capability-token", "") if request else ""
    if not header:
        if CAPABILITY_TOKENS_REQUIRED:
            return False, "missing X-Capability-Token header", {}
        return True, "no-token-optional-mode", {}

    if _token_verifier is None:
        return (not CAPABILITY_TOKENS_REQUIRED), "verifier not initialised", {}

    try:
        tok = CapabilityToken.decode(header)
    except Exception as exc:
        return False, f"bad token encoding: {exc}", {}

    ok, reason = _token_verifier.verify(
        tok,
        request={"resource": resource, "action": action, "physical": physical},
    )
    info = {
        "token_id": tok.token_id,
        "subject": tok.subject,
        "expires_at": tok.expires_at,
        "resources": list(tok.resources),
        "actions": list(tok.actions),
    }
    return ok, reason, info
from bridge.safety_pipeline import SafetyPipeline, set_trace_callback as _pipeline_set_trace
from bridge.license_manager import LicenseManager
from bridge.local_brain import LocalBrain
from bridge.connectivity_monitor import ConnectivityMonitor
from bridge.event_buffer import EventBuffer
from bridge.watchdog import EdgeWatchdog
from bridge.local_behavior import LocalBehaviorManager
from bridge.local_navigator import LocalNavigator
from bridge.local_cache import LocalKnowledgeCache


# ── Trace logging (distributed tracing) ─────────────────────────────────

_trace_logger = logging.getLogger("trace")

# In-memory ring buffer of recent trace events, indexed by trace_id.
# The demo_ui /api/trace aggregator queries `/v1/traces/{tid}` and
# stitches these into the timeline alongside demo_ui + NLGW events,
# so every safety / pipeline / adapter step the bridge runs becomes
# visible in /debug. Bounded at TRACE_BUFFER_MAX events total — old
# trace_ids drop out FIFO once the cap is hit so we don't grow
# unbounded on a long-running deployment.
_TRACE_BUFFER_MAX = int(os.getenv("BRIDGE_TRACE_BUFFER_MAX", "5000"))
_trace_buffer: dict[str, list[dict]] = {}
_trace_order: list[str] = []  # trace_ids in insertion order, for FIFO eviction
_trace_lock = threading.Lock()


def _trace_log(operation: str, trace_id: str = "no-trace", **kwargs):
    """Emit structured trace log entry. Also stored in an in-memory
    ring buffer keyed by trace_id so the demo_ui /debug page can pull
    bridge-side events for a given chat message."""
    entry = {
        "trace_id": trace_id,
        "service": "bridge",
        "operation": operation,
        "timestamp": int(time.time() * 1000),
        **kwargs,
    }
    _trace_logger.info(json.dumps(entry, ensure_ascii=False))
    if trace_id and trace_id != "no-trace":
        with _trace_lock:
            bucket = _trace_buffer.get(trace_id)
            if bucket is None:
                bucket = []
                _trace_buffer[trace_id] = bucket
                _trace_order.append(trace_id)
                # Evict oldest trace_ids when over the budget. Keeps
                # memory flat under sustained chat traffic.
                while len(_trace_buffer) > _TRACE_BUFFER_MAX // 50 and _trace_order:
                    drop = _trace_order.pop(0)
                    _trace_buffer.pop(drop, None)
            bucket.append(entry)
            # Cap per-trace events too — a runaway loop shouldn't OOM
            # the bridge through one trace_id.
            if len(bucket) > 500:
                del bucket[: len(bucket) - 500]


# ── Configuration ────────────────────────────────────────────────────────

ADAPTER_TYPE = os.getenv("ADAPTER_TYPE", "mock")
ROBOT_URL = os.getenv("ROBOT_URL", "http://192.168.1.100:8000")
ROBOT_NAME = os.getenv("ROBOT_NAME", "")  # Display name for UI
ROBOT_ID = os.getenv("ROBOT_ID", "")      # Unique robot identifier
# robot_model surfaced to operator_ui /api/fleet. Falls back to a per-adapter
# default so the operator sees "Noetix E1" instead of "—" without extra config.
_DEFAULT_MODELS = {
    "mock":   "Mock (Noetix N2 sim)",
    "http":   "Noetix N2",
    "h1":     "Unitree H1",
    "e1":     "Noetix E1",
    "mujoco": "Unitree G1 (MuJoCo sim)",
}
ROBOT_MODEL = os.getenv("ROBOT_MODEL", _DEFAULT_MODELS.get(ADAPTER_TYPE, ADAPTER_TYPE))
def _port_from_env() -> int:
    # Railway injects PORT directly; BRIDGE_PORT is the local default.
    # Reject a literal "$PORT" placeholder some PaaS auto-config
    # tools paste verbatim into env vars.
    for key in ("PORT", "BRIDGE_PORT"):
        raw = (os.getenv(key) or "").strip()
        if raw and raw != "$PORT" and not raw.startswith("${"):
            return int(raw)
    return 8080
BRIDGE_PORT = _port_from_env()
POLL_HZ = int(os.getenv("POLL_HZ", "2"))
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

# ── Speed-near-human HITL helper ──────────────────────────────────────
# Called from BOTH /robot/action and /robot/move so every path that can
# drive the robot runs through the same ISO 13482 §5.7.2 check. Returns
# a dict describing the outcome:
#   None            — gate didn't apply (no humans nearby or speed < 0.5
#                      or HITL not wired)
#   {blocked:False} — operator approved; caller proceeds. Decision row
#                      already written to decision_log.
#   {blocked:True}  — operator denied / timed out; caller must return a
#                      denial response (with hitl meta).

_MOVEMENT_ACTION_TYPES = {
    "move", "navigate_to", "move_to", "move_relative",
    "move_forward", "escort", "escort_to_poi", "patrol",
}
_SPEED_NEAR_HUMAN_RADIUS_M = 2.0
_SPEED_NEAR_HUMAN_LIMIT_MPS = 0.5


def _speed_near_human_hitl_check(
    action_label: str,
    action_type_for_movement: str,
    speed_mps: float,
    entities: list,
    robot_id: str,
    tier: str,
    trace_id: str,
    override: bool = False,
) -> Optional[dict]:
    """Fire the ISO 13482 §5.7.2 HITL gate. See module comment above."""
    if _hitl is None or override:
        return None
    if action_type_for_movement not in _MOVEMENT_ACTION_TYPES:
        return None
    if speed_mps <= _SPEED_NEAR_HUMAN_LIMIT_MPS:
        return None
    near = [
        e for e in entities
        if (e.get("is_human") or e.get("class_name") == "person")
        and float(e.get("distance_m", 999)) < _SPEED_NEAR_HUMAN_RADIUS_M
    ]
    if not near:
        return None
    nearest = min(float(h.get("distance_m", 999)) for h in near)
    person = near[0].get("entity_id") or "person"
    situation = (
        f"Requested speed {speed_mps:.2f} m/s within {nearest:.2f} m "
        f"of {person}. ISO 13482:2014 §5.7.2 limits robot speed near "
        f"an assisted person to 0.5 m/s. Operator approval required."
    )
    logger.warning("HITL GATE: %s", situation)
    resp = _hitl.request_approval(
        robot_id=robot_id,
        situation=situation,
        risk_score=min(1.0, speed_mps / 1.5),
        rule_id="ISO13482-SPEED-NEAR-HUMAN-001",
        recommendation=(
            "Reduce to 0.4 m/s, or approve override only if operator has "
            "confirmed the path is clear and the person consents."
        ),
        options=[
            {"id": "approve", "description":
                "Approve this one action (operator takes responsibility)"},
            {"id": "deny", "description":
                "Deny — robot holds position (default safe)"},
        ],
        timeout_s=30.0,
    )
    meta = {
        "request_id": resp.request_id,
        "decision": resp.decision,
        "note": resp.operator_note,
        "rule_id": "ISO13482-SPEED-NEAR-HUMAN-001",
        "audit_ref": "ISO 13482:2014 §5.7.2",
        "nearest_human_m": round(nearest, 2),
        "requested_speed_mps": round(speed_mps, 2),
    }
    if resp.decision not in ("approve", "approve_remember"):
        class _HitlDeny:
            decision = "DENY"
            reason = f"HITL denied: {resp.operator_note or 'timeout_safe_fallback'}"
            rule_id = "ISO13482-SPEED-NEAR-HUMAN-001"
            audit_ref = "ISO 13482:2014 §5.7.2"
            params: dict = {}
        _push_decision(robot_id, f"{action_label} (HITL blocked)",
                       _HitlDeny(), trace_id=trace_id, tier=tier)
        if _trust_tracker is not None:
            _trust_tracker.record(
                robot_id, "hitl_deny",
                f"{action_type_for_movement} @ {speed_mps:.2f}mps near human",
            )
        meta["blocked"] = True
        return meta

    logger.info("HITL APPROVED: %s — proceeding with %s @ %.2f m/s",
                resp.request_id, action_type_for_movement, speed_mps)

    class _HitlApprove:
        decision = "ALLOW"
        reason = f"HITL override: {resp.operator_note or 'approved'}"
        rule_id = "ISO13482-SPEED-NEAR-HUMAN-001"
        audit_ref = "ISO 13482:2014 §5.7.2"
        params: dict = {}
    _push_decision(robot_id, f"{action_label} (HITL approved)",
                   _HitlApprove(), trace_id=trace_id, tier=tier)
    meta["blocked"] = False
    return meta


def _push_decision(robot_id: str, command: str, gate, trace_id: str = "", tier: str = "") -> None:
    """Push GateResult to central decision_log asynchronously (fire-and-forget)."""
    if not DECISION_LOG_URL:
        return
    if trace_id:
        logger.debug("push_decision trace_id=%s cmd=%s decision=%s", trace_id, command, gate.decision)
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
        "tier": tier or "",
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
        except Exception as _exc:
            logger.debug("Decision log push ignored: %s", _exc)

    threading.Thread(target=_post, daemon=True).start()


# ── State ────────────────────────────────────────────────────────────────

_adapter = None
_pipeline = SafetyPipeline()

# Bind the safety pipeline's per-rule trace events to the bridge's
# trace logger + ring buffer. Without this the pipeline runs in
# silence: the operator only sees the final verdict, not which of
# the 8 rules ran or why. We thread the trace_id via a threadlocal
# so the callback can stamp each rule event with the request's id.
_PIPELINE_TID = threading.local()


def _pipeline_trace_event(operation: str, **fields):
    tid = getattr(_PIPELINE_TID, "trace_id", "no-trace")
    _trace_log(operation, trace_id=tid, **fields)


_pipeline_set_trace(_pipeline_trace_event)
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


def _json_safe_deep(obj):
    """Replace NaN/Inf floats recursively so JSON responses never raise ValueError."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe_deep(v) for v in obj]
    return obj


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
    if ADAPTER_TYPE == "e1":
        e1_url = os.getenv("ROBOT_URL", "http://192.168.55.101:8083")
        return E1Adapter(robot_url=e1_url)
    if ADAPTER_TYPE == "mujoco":
        mj_url = os.getenv("ROBOT_URL", "http://mujoco_bridge:8000")
        return MujocoAdapter(robot_url=mj_url)
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
        except Exception as _exc:
            logger.debug("State poll ignored: %s", _exc)
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

    # 4b. Initialise Ed25519 capability-token verifier (SINT-style).
    # Fail-closed only when CAPABILITY_TOKENS_REQUIRED=1.
    _init_token_verifier()

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
    model_config = {"extra": "allow"}  # Accept unknown fields (vx, vy, wz from gateway)
    action_id: str = ""
    action_type: str
    robot_id: str = ""
    target_position: Optional[dict] = None  # {x, y, z} in metres
    target_zone_id: str = ""
    target_object_id: str = ""
    target_speed_mps: float = 0.0
    constraints: dict = {}
    params: Optional[dict] = None
    # ros2_publish fields (Skill Composer sends these at top level)
    topic: str = ""
    msg_type: str = ""
    data: Optional[dict] = None
    timing: str = "once"
    source: str = ""
    trace_id: str = ""
    # Velocity fields (from _bridge_velocity_via_action)
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0
    speed: float = 0.0
    speed_mps: float = 0.0


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
    # Add robot identity for UI display
    if ROBOT_NAME:
        state["name"] = ROBOT_NAME
    if ROBOT_ID:
        state["robot_id"] = ROBOT_ID
    state["adapter"] = ADAPTER_TYPE
    state.setdefault("robot_model", ROBOT_MODEL)
    return state


@app.post("/robot/move")
def robot_move(req: MoveRequest, request: Request = None):
    """Send velocity command through safety pipeline."""
    trace_id = (request.headers.get("x-trace-id", "no-trace") if request else "no-trace")
    _trace_log("robot_move", trace_id=trace_id, vx=req.vx, vy=req.vy, wz=req.wz)

    if not _adapter:
        raise HTTPException(503, "Adapter not initialized")

    # ── Ed25519 capability-token verification (SINT-style) ───────────────
    tok_ok, tok_reason, tok_info = _verify_capability_token(
        request,
        resource="ros2:///cmd_vel",
        action="publish",
        physical={
            "velocity_mps": math.hypot(req.vx, req.vy),
            "force_n": 0.0,
        },
    )
    if not tok_ok:
        _trace_log("cap_token_deny", trace_id=trace_id, reason=tok_reason)
        class _TokenDeny:
            decision = "DENY"
            reason = f"capability token: {tok_reason}"
            rule_id = "CAP-TOKEN-001"
            audit_ref = "SINT-inspired Ed25519 capability token"
            params: dict = {}
        _push_decision(
            f"bridge-{ADAPTER_TYPE}", "move (cap_token_deny)",
            _TokenDeny(), trace_id=trace_id, tier=T2_ACT,
        )
        return {
            "requested": {"vx": req.vx, "vy": req.vy, "wz": req.wz},
            "applied": {"vx": 0.0, "vy": 0.0, "wz": 0.0},
            "gate": {
                "decision": "DENY",
                "reason": f"capability token: {tok_reason}",
                "rule_id": "CAP-TOKEN-001",
                "audit_ref": "SINT-inspired Ed25519 capability token",
                "params": {},
            },
            "send": {"status": "denied_cap_token"},
            "tier": T2_ACT,
        }

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

    # ── Speed-near-human HITL (ISO 13482 §5.7.2) ────────────────────
    # task_executor's move / move_forward / navigate_to fall-through
    # dispatches here with speed packed into vx. The gate blocks if
    # a person is within 2 m and requested speed > 0.5 m/s, same
    # logic as /robot/action.
    _req_speed = math.hypot(req.vx, req.vy)
    _hitl_meta = _speed_near_human_hitl_check(
        action_label="move",
        action_type_for_movement="move",
        speed_mps=_req_speed,
        entities=entities,
        robot_id=state.get("robot_id", state.get("name", f"bridge-{ADAPTER_TYPE}")),
        tier=T2_ACT,
        trace_id=trace_id,
    )
    if _hitl_meta and _hitl_meta.get("blocked"):
        return {
            "requested": {"vx": req.vx, "vy": req.vy, "wz": req.wz},
            "applied": {"vx": 0.0, "vy": 0.0, "wz": 0.0},
            "gate": {
                "decision": "DENY",
                "reason": f"HITL denied: {_hitl_meta.get('note') or 'timeout'}",
                "rule_id": "ISO13482-SPEED-NEAR-HUMAN-001",
                "audit_ref": "ISO 13482:2014 §5.7.2",
                "params": {},
            },
            "send": {"status": "denied_hitl"},
            "hitl": _hitl_meta,
        }

    # Run safety pipeline
    vx, vy, wz, gate = _pipeline.check(
        req.vx, req.vy, req.wz, state, entities,
    )

    # Push every decision to central decision_log (async, non-blocking)
    robot_id = state.get("robot_id", state.get("name", f"bridge-{ADAPTER_TYPE}"))
    cmd_str = f"move vx={req.vx:.2f} vy={req.vy:.2f} wz={req.wz:.2f}"
    _push_decision(robot_id, cmd_str, gate, trace_id=trace_id)
    _trace_log("safety_check", trace_id=trace_id, action="move", decision=gate.decision, rule_id=gate.rule_id)

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
        if _trust_tracker is not None:
            _trust_tracker.record(robot_id, "clean_action", f"move vx={vx:.2f}")
    else:
        _adapter.stop()
        result["send"] = {"status": "denied_stop"}
        if _trust_tracker is not None:
            _trust_tracker.record(robot_id, "deny_action", gate.reason or "denied")

    return result


@app.post("/robot/stop")
def robot_stop(request: Request = None):
    """Emergency stop."""
    trace_id = (request.headers.get("x-trace-id", "no-trace") if request else "no-trace")
    _trace_log("robot_stop", trace_id=trace_id)

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
    cache_age = 0.0
    try:
        cache_age = round(_json_safe_deep(_cache.last_sync_age), 1)
    except Exception as _exc:
        logger.debug("Ignored: %s", _exc)
    bat = state.get("battery")
    if bat is not None:
        bat = _json_safe_deep(bat)
    pos = state.get("position")
    if pos is not None:
        pos = _json_safe_deep(pos)
    return _json_safe_deep({
        "ok": True,
        "in_fallback": _watchdog.in_fallback,
        "battery": bat,
        "position": pos,
        "cache_age_s": cache_age,
        "local_safety_active": (
            _local_behavior.is_disconnected
            if _local_behavior else False
        ),
    })


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


# ── HITL (Human-in-the-Loop) approvals ─────────────────────────────────
# The speed-near-human gate on /robot/action raises ApprovalRequests
# into the HITL queue when a move violates ISO 13482 §5.7.2. Operator
# UIs poll /hitl/pending and submit decisions via /hitl/respond.

@app.get("/hitl/pending")
def hitl_pending(robot_id: str = ""):
    """List approval requests awaiting operator decision."""
    if _hitl is None:
        return {"pending": [], "available": False}
    return {"pending": _hitl.get_pending(robot_id=robot_id), "available": True}


class HitlResponseRequest(BaseModel):
    request_id: str
    decision: str    # "approve" | "deny" | "approve_remember"
    operator: str = ""
    note: str = ""


@app.post("/hitl/respond")
def hitl_respond(req: HitlResponseRequest):
    """Submit the operator's approval decision. Unblocks the /robot/action
    call that raised the approval. Decision must be one of approve /
    deny / approve_remember."""
    if _hitl is None:
        raise HTTPException(503, "HITL not available (libs/ not mounted)")
    if req.decision not in ("approve", "deny", "approve_remember"):
        raise HTTPException(400, f"Unknown decision: {req.decision}")
    note = req.note or (f"operator={req.operator}" if req.operator else "")
    ok = _hitl.submit_response(req.request_id, req.decision, note=note)
    if not ok:
        raise HTTPException(404, f"No pending request with id {req.request_id}")
    return {"ok": True, "request_id": req.request_id,
            "decision": req.decision}


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


@app.get("/agent/trust")
def agent_trust_snapshot():
    """Per-agent trust scores from the behavioural tracker (SINT-inspired)."""
    if _trust_tracker is None:
        return {"available": False, "reason": "trust tracker not loaded"}
    return {"available": True, "agents": _trust_tracker.snapshot()}


@app.get("/agent/trust/{agent_id}")
def agent_trust_one(agent_id: str):
    if _trust_tracker is None:
        return {"available": False}
    st = _trust_tracker.state(agent_id)
    if st is None:
        return {"available": True, "agent_id": agent_id, "score": None}
    return {"available": True, **st.to_dict()}


@app.post("/agent/trust/{agent_id}/reset")
def agent_trust_reset(agent_id: str):
    if _trust_tracker is None:
        raise HTTPException(503, "trust tracker not loaded")
    _trust_tracker.record(agent_id, "operator_reset", "manual reset")
    return {"ok": True, "score": _trust_tracker.score(agent_id)}


# ── Action endpoint ──────────────────────────────────────────────────────


class _AllowGate:
    """Lightweight gate stub for actions that skip the safety pipeline."""
    decision = "ALLOW"
    reason = "safe action"
    rule_id = ""
    audit_ref = ""
    params: dict = {}


@app.post("/robot/action")
def robot_action(req: ActionRequest, request: Request = None):
    """Execute a StandardAction on the robot.

    Dispatches by action_type:
      stop / e_stop   — emergency stop (always allowed)
      wait / idle     — no-op acknowledgement
      navigate_to     — drive to target_position {x, y, z}
      scan            — camera capture
    All movement actions run through the safety pipeline.
    """
    trace_id = (request.headers.get("x-trace-id", "no-trace") if request else "no-trace")
    _trace_log("robot_action", trace_id=trace_id, action_type=req.action_type, robot_id=req.robot_id or "")

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
            "tier": T2_ACT,
        }

    atype = req.action_type

    # ── Tier assignment (SINT-inspired T0/T1/T2/T3) ──────────────────────
    # Pulls human proximity, running-mode flags, and per-agent trust score
    # out of latest state + AgentTrustTracker so tier escalation is fully
    # driven by live signals.
    tier_ctx: dict = {}
    with _state_lock:
        _st = dict(_latest_state)
        _ents = list(_latest_entities)
    _humans = [e for e in _ents if e.get("is_human") or e.get("class_name") == "person"]
    if _humans:
        try:
            tier_ctx["human_distance_m"] = min(
                float(e.get("distance_m", 999)) for e in _humans
            )
        except (TypeError, ValueError):
            pass
    if _st.get("mode_e1") == "running" or _st.get("gait") == "RUN":
        tier_ctx["running_mode"] = True

    # Δ_trust feed from AgentTrustTracker. The request's subject comes from
    # either the capability token (preferred) or the fallback robot_id.
    _agent_id = req.robot_id or f"bridge-{ADAPTER_TYPE}"
    if _trust_tracker is not None:
        tier_ctx["trust"] = _trust_tracker.score(_agent_id)

    _tier_info = assign_tier(atype, context=tier_ctx)

    # ── Forbidden-combo sliding-window check ─────────────────────────────
    if _forbidden_combos is not None and atype not in ("stop", "e_stop", "idle", "wait"):
        _combo_ok, _violated = _forbidden_combos.check(
            _agent_id, atype,
            params=dict(req.params or {}),
        )
        _trace_log("forbidden_combo_check", trace_id=trace_id,
                   layer="forbidden_combos", action=atype,
                   decision="PASS" if _combo_ok else "DENY",
                   violation=(_violated.name if not _combo_ok else None))
        if not _combo_ok:
            if _trust_tracker is not None:
                _trust_tracker.record(_agent_id, "forbidden_combo", _violated.name)
            class _ComboDeny:
                decision = "DENY"
                reason = f"forbidden combo: {_violated.name} ({_violated.description})"
                rule_id = f"COMBO-{_violated.name.upper()}"
                audit_ref = _violated.audit_ref or "forbidden-combo"
                params: dict = {}
            _push_decision(
                _agent_id, f"action:{atype}",
                _ComboDeny(), trace_id=trace_id, tier=_tier_info.tier,
            )
            return {
                "status": "denied",
                "action_id": req.action_id,
                "action_type": atype,
                "reason": _ComboDeny.reason,
                "rule_id": _ComboDeny.rule_id,
                "tier": _tier_info.tier,
                "tier_info": _tier_info.to_dict(),
            }
        # Record the action only if it passed the combo check.
        _forbidden_combos.record(_agent_id, atype, params=dict(req.params or {}))
    _trace_log("tier_assigned", trace_id=trace_id,
               action_type=atype, tier=_tier_info.tier,
               base_tier=_tier_info.base_tier,
               escalations=len(_tier_info.escalations))

    # ── Speed-near-human HITL gate (ISO 13482 §5.7.2) ────────────────────
    _req_speed = (req.target_speed_mps or req.speed_mps or req.speed
                  or float((req.params or {}).get("speed", 0.0))
                  or float((req.params or {}).get("speed_mps", 0.0)))
    _hitl_override = bool((req.params or {}).get("hitl_override", False))
    _trace_log("hitl_gate_eval", trace_id=trace_id,
               layer="hitl_gate", rule_id="ISO13482-SPEED-NEAR-HUMAN-001",
               audit_ref="ISO 13482:2014 §5.7.2",
               action=atype, requested_speed_mps=round(_req_speed, 2),
               override=_hitl_override,
               escalation_threshold_mps=0.5)
    _hitl_meta = _speed_near_human_hitl_check(
        action_label=f"action:{atype}",
        action_type_for_movement=atype,
        speed_mps=_req_speed,
        entities=_ents,
        robot_id=req.robot_id or f"bridge-{ADAPTER_TYPE}",
        tier=_tier_info.tier,
        trace_id=trace_id,
        override=_hitl_override,
    )
    if _hitl_meta:
        _trace_log("hitl_gate_decision", trace_id=trace_id,
                   layer="hitl_gate", rule_id="ISO13482-SPEED-NEAR-HUMAN-001",
                   blocked=_hitl_meta.get("blocked"),
                   decision=_hitl_meta.get("decision"),
                   nearest_human_m=_hitl_meta.get("nearest_human_m"))
    if _hitl_meta and _hitl_meta.get("blocked"):
        return {
            "status": "denied",
            "action_id": req.action_id,
            "action_type": atype,
            "reason": f"Operator denied (HITL): {_hitl_meta.get('note') or 'timeout'}",
            "rule_id": "ISO13482-SPEED-NEAR-HUMAN-001",
            "audit_ref": "ISO 13482:2014 §5.7.2",
            "tier": _tier_info.tier,
            "hitl": _hitl_meta,
        }

    # ── stop / e_stop ────────────────────────────────────────────────────
    if atype in ("stop", "e_stop"):
        result = _adapter.stop()
        _push_decision(
            req.robot_id or f"bridge-{ADAPTER_TYPE}",
            f"action:{atype}",
            _AllowGate(),
            trace_id=trace_id,
            tier=_tier_info.tier,
        )
        return {
            "status": "ok",
            "action_id": req.action_id,
            "action_type": atype,
            "result": result,
            "tier": _tier_info.tier,
            "tier_info": _tier_info.to_dict(),
        }

    # ── wait / idle ───────────────────────────────────────────────────────
    if atype in ("wait", "idle"):
        return {
            "status": "ok",
            "action_id": req.action_id,
            "action_type": atype,
        }

    # ── move (direct velocity) ───────────────────────────────────────────
    if atype == "move":
        # vx/vy/wz from top-level fields or params dict
        vx = req.vx or float((req.params or {}).get("vx", 0))
        vy = req.vy or float((req.params or {}).get("vy", 0))
        wz = req.wz or float((req.params or {}).get("wz", 0))
        speed = req.speed_mps or req.speed or req.target_speed_mps or float((req.params or {}).get("speed", 0.3))

        with _state_lock:
            state = dict(_latest_state)
            entities = list(_latest_entities)

        # Pass the FULL velocity triple (vx, vy, wz) to the pipeline.
        # The earlier code passed (speed, 0, 0), erasing the rotation
        # component — so the pipeline could never tell pure rotation
        # apart from translation, and HUMAN-001's `is_pure_rotation`
        # branch never fired. The pipeline may return tightened
        # velocities (LIMIT) — honour those instead of the original.
        _PIPELINE_TID.trace_id = trace_id
        nvx, nvy, nwz, gate = _pipeline.check(vx, vy, wz, state, entities)
        robot_id = state.get("robot_id", req.robot_id or f"bridge-{ADAPTER_TYPE}")
        _push_decision(robot_id, f"move vx={vx:.2f} wz={wz:.2f}", gate, trace_id=trace_id)
        _trace_log("safety_check", trace_id=trace_id, action="move", decision=gate.decision, rule_id=gate.rule_id)

        if gate.decision == "DENY":
            return {
                "status": "denied", "action_id": req.action_id,
                "action_type": atype, "reason": gate.reason, "rule_id": gate.rule_id,
                "audit_ref": gate.audit_ref,
            }

        send_result = _adapter.send_velocity(nvx, nvy, nwz)
        _trace_log("adapter_send", trace_id=trace_id, action="move", result=str(send_result)[:200])
        return {
            "status": "limited" if gate.decision == "LIMIT" else "ok",
            "action_id": req.action_id,
            "action_type": atype,
            "result": send_result,
            "rule_id": gate.rule_id if gate.decision == "LIMIT" else None,
            "reason": gate.reason if gate.decision == "LIMIT" else None,
            "audit_ref": gate.audit_ref if gate.decision == "LIMIT" else None,
            "params": gate.params if gate.decision == "LIMIT" else None,
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

        _PIPELINE_TID.trace_id = trace_id
        _, _, _, gate = _pipeline.check(speed, 0.0, 0.0, state, entities)
        robot_id = state.get(
            "robot_id", req.robot_id or f"bridge-{ADAPTER_TYPE}"
        )
        _push_decision(
            robot_id, f"action:navigate_to ({x_m:.1f},{y_m:.1f})", gate,
            trace_id=trace_id,
        )
        _trace_log("safety_check", trace_id=trace_id, action="navigate_to", decision=gate.decision, rule_id=gate.rule_id)

        if gate.decision == "DENY":
            return {
                "status": "denied",
                "action_id": req.action_id,
                "action_type": atype,
                "reason": gate.reason,
                "rule_id": gate.rule_id,
            }

        nav_result = _adapter.navigate_to(x_m, y_m, speed_mps=speed)
        # Fallback: if adapter doesn't support navigate_to, use LocalNavigator
        if nav_result.get("status") == "not_supported" and _local_navigator:
            logger.info("Adapter doesn't support navigate_to, using LocalNavigator")
            nav_result = _local_navigator.navigate_to(x_m, y_m, speed_mps=speed)
        return {
            "status": "ok" if nav_result.get("status") != "not_supported" else "fallback",
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

    # ── ros2_publish / gestures / gripper — delegate to adapter ────────
    _delegated_actions = (
        "ros2_publish", "wave", "nod", "crouch", "stand_up",
        "gesture", "agree", "sit_down", "rise",
        "handshake", "dance", "spin", "bow", "clap", "greet",
        "arms_up", "point_forward", "high_five", "head_shake",
        "dance_with_music", "point_at",
        "gripper", "gripper_control",
    )
    if atype in _delegated_actions:
        # Build params dict from request fields
        action_params = dict(req.params or {})
        # For ros2_publish, pull top-level fields the Skill Composer sends
        if atype == "ros2_publish":
            if req.topic:
                action_params["topic"] = req.topic
            if req.msg_type:
                action_params["msg_type"] = req.msg_type
            if req.data is not None:
                action_params["data"] = req.data
            if req.timing != "once":
                action_params["timing"] = req.timing

        # Delegate to adapter's handle_action if available
        if hasattr(_adapter, "handle_action"):
            try:
                result = _adapter.handle_action(atype, action_params)
            except Exception as exc:
                logger.warning("handle_action(%s) failed: %s", atype, exc)
                result = {"status": "error", "error": str(exc)}
        else:
            # Adapter doesn't support handle_action — accept silently
            result = {"status": "ok", "note": f"{atype} accepted (adapter has no handler)"}

        _push_decision(
            req.robot_id or f"bridge-{ADAPTER_TYPE}",
            f"action:{atype}",
            _AllowGate(),
            trace_id=trace_id,
        )
        return {
            "status": "ok",
            "action_id": req.action_id,
            "action_type": atype,
            "result": result,
        }

    # Last-resort: ask the adapter whether it has a native handler for
    # this action. Adapters like MujocoAdapter expose rich primitives
    # (find_visual, look_around, describe_scene, head_pan/tilt, rotate,
    # move_relative) that don't fit the whitelist above but are
    # legitimate robot capabilities. If the adapter returns a real
    # status, we pass it through; otherwise fall back to unknown_action.
    if hasattr(_adapter, "handle_action"):
        try:
            adapter_result = _adapter.handle_action(atype, dict(req.params or {}))
        except Exception as exc:
            logger.warning("handle_action(%s) fallback failed: %s", atype, exc)
            adapter_result = None
        if (adapter_result and
            adapter_result.get("status") not in (None, "unknown_action")):
            _push_decision(
                req.robot_id or f"bridge-{ADAPTER_TYPE}",
                f"action:{atype}",
                _AllowGate(),
                trace_id=trace_id,
            )
            return {
                "status": "ok",
                "action_id": req.action_id,
                "action_type": atype,
                "result": adapter_result,
                **{k: v for k, v in adapter_result.items()
                   if k in ("describe", "result", "observations", "target")},
            }

    return {
        "status": "unknown_action",
        "action_id": req.action_id,
        "action_type": atype,
        "note": f"Unknown action_type: {atype}",
    }


# ── LiDAR endpoint ───────────────────────────────────────────────────────


@app.get("/v1/traces/{trace_id}")
def get_trace(trace_id: str):
    """Return the bridge-side trace events for a given trace_id.
    The demo_ui /api/trace aggregator queries this so /debug shows
    safety_pipeline / HITL / forbidden_combo / adapter_send events
    interleaved with NLGW + demo_ui events on the same timeline."""
    with _trace_lock:
        events = list(_trace_buffer.get(trace_id, []))
    return {"trace_id": trace_id, "events": events, "count": len(events)}


@app.get("/scene/objects")
def scene_objects():
    """Ground-truth scene state from the underlying simulator.

    Real robots don't have a "ground truth" — perception has to find
    objects via the camera/VLM/SLAM stack. The MuJoCo simulator does,
    and we expose it so the language stack can resolve coloured-cube
    references ("зелёный куб" → coordinates) deterministically. For
    H1/E1/HTTP adapters this returns an empty list — callers must fall
    back to perception."""
    if _adapter is None:
        return {"objects": [], "zones": [], "humans": [], "available": False}
    # mujoco_adapter exposes a private _get; we know the schema and
    # don't want to add a get_scene_objects() to the abstract base
    # class only mujoco implements. Best-effort.
    fetcher = getattr(_adapter, "_get", None)
    if not callable(fetcher):
        return {"objects": [], "zones": [], "humans": [], "available": False}
    try:
        data = fetcher("/scene/objects") or {}
    except Exception as exc:
        logger.warning("scene/objects passthrough failed: %s", exc)
        return {"objects": [], "zones": [], "humans": [], "available": False}
    data["available"] = True
    return data


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


# ── Operator approval endpoint ───────────────────────────────────────────


class OperatorAskRequest(BaseModel):
    robot_id: str = ""
    action: str = ""
    reason: str = ""
    timeout_s: float = 30.0


@app.post("/operator/ask")
def operator_ask(req: OperatorAskRequest):
    """Forward approval request to operator UI. Returns {approved: bool}."""
    # For now, auto-approve with logging (operator UI integration later)
    logger.info("OPERATOR_ASK: robot=%s action=%s reason=%s",
                req.robot_id, req.action, req.reason)
    return {"approved": True, "method": "auto_approve",
            "note": "operator_ui integration pending"}


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "bridge.main:app",
        host="0.0.0.0",
        port=BRIDGE_PORT,
        reload=False,
    )
