"""Unit tests for bridge.h1_adapter.

Mirrors the E1 adapter test pattern: inject fake `_get` / `_post`
helpers on the adapter instance so every call is recorded in-memory
and the test runs in <100ms without touching the network.

H1 has some behaviours E1 doesn't:
* No lateral movement (vy is hard-clamped to 0)
* Gait switching (WALK / TROT / STAND)
* Wider envelope than E1 (1.2 m/s vs 0.8)
* Stand / lie / gesture as *direct* endpoints (no /api/cmd/mode wrapper)

These tests pin that behaviour so future changes to the H1 adapter
don't silently drift.
"""
from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

from bridge.h1_adapter import H1Adapter


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def adapter():
    """H1Adapter with in-memory HTTP stubs."""
    a = H1Adapter(robot_url="http://h1.test:8081")
    a.call_log: list = []
    a.replies: dict = {}

    def fake_get(path: str):
        a.call_log.append(("GET", path, None))
        return a.replies.get(("GET", path))

    def fake_post(path: str, data: dict):
        a.call_log.append(("POST", path, dict(data or {})))
        return a.replies.get(("POST", path))

    a._get = fake_get
    a._post = fake_post
    return a


# ── State ──────────────────────────────────────────────────────────────────


def test_get_state_happy_path(adapter):
    adapter.replies[("GET", "/api/state")] = {
        "pos_x": 1.5, "pos_y": 0.0, "pos_z": 0.95,
        "vx": 0.3, "yaw_rad": 0.2, "speed_mps": 0.3,
        "battery_pct": 85, "pitch_deg": 2.0, "motor_temp_c": 42,
        "mode": "ADVISORY", "gait": "WALK",
        "camera_ok": 1, "imu_ok": 1,
    }
    state = adapter.get_state()
    assert adapter.connected is True
    assert state["position"]["x"] == pytest.approx(1.5)
    assert state["position"]["z"] == pytest.approx(0.95)
    # H1 can't strafe — vy must be zero regardless of input
    assert state["velocity"]["vy"] == 0.0
    assert state["battery"] == pytest.approx(85)
    assert state["gait"] == "WALK"
    assert state["adapter"] == "h1"
    assert state["sensors"]["lidar"]["available"] is False


def test_get_state_error_returns_skeleton(adapter):
    """Server unreachable → _error_state skeleton, connected=False."""
    adapter.replies.clear()
    adapter._last_error = "Connection refused"
    state = adapter.get_state()
    assert state["mode"] == "UNKNOWN"
    assert state["battery"] == 0
    assert state["adapter"] == "h1"
    assert "error" in state


# ── Velocity clamping ──────────────────────────────────────────────────────


def test_send_velocity_clamps_forward(adapter):
    """vx clamped at MAX_SPEED = 1.2 m/s."""
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    result = adapter.send_velocity(vx=5.0, vy=0.0, wz=0.0)
    assert result["status"] == "ok"
    _, _, body = adapter.call_log[-1]
    assert body["vx"] == pytest.approx(adapter.MAX_SPEED)


def test_send_velocity_clamps_reverse(adapter):
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    adapter.send_velocity(vx=-5.0, vy=0.0, wz=0.0)
    _, _, body = adapter.call_log[-1]
    assert body["vx"] == pytest.approx(-adapter.MAX_SPEED)


def test_send_velocity_clamps_angular(adapter):
    """wz clamped at MAX_ANGULAR = 2.0 rad/s."""
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    adapter.send_velocity(vx=0.0, vy=0.0, wz=10.0)
    _, _, body = adapter.call_log[-1]
    assert body["vyaw"] == pytest.approx(adapter.MAX_ANGULAR)


def test_send_velocity_ignores_lateral(adapter):
    """vy is dropped — H1 can't strafe. No `vy` key in payload."""
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    adapter.send_velocity(vx=0.3, vy=0.5, wz=0.0)
    _, _, body = adapter.call_log[-1]
    assert "vy" not in body
    assert body["vx"] == pytest.approx(0.3)


def test_send_velocity_error_surfaces(adapter):
    adapter.replies.clear()
    adapter._last_error = "timeout"
    result = adapter.send_velocity(vx=0.3, vy=0.0, wz=0.0)
    assert result["status"] == "error"
    assert result["error"] == "timeout"


def test_stop_posts_empty_body(adapter):
    adapter.replies[("POST", "/api/cmd/stop")] = {"status": "stopped"}
    result = adapter.stop()
    assert result["status"] == "stopped"
    assert ("POST", "/api/cmd/stop", {}) in adapter.call_log


# ── Entities ──────────────────────────────────────────────────────────────


def test_get_entities_returns_list(adapter):
    adapter.replies[("GET", "/api/perception/entities")] = {
        "entities": [
            {"id": "person_1", "class_name": "person", "distance_m": 2.0},
            {"id": "box_1", "class_name": "box", "distance_m": 3.5},
        ],
    }
    entities = adapter.get_entities()
    assert len(entities) == 2
    assert entities[0]["class_name"] == "person"


def test_get_entities_empty_when_server_down(adapter):
    adapter.replies.clear()
    assert adapter.get_entities() == []


# ── Stand / lie / gait / gesture ──────────────────────────────────────────


def test_stand_up_direct_endpoint(adapter):
    """Unlike E1, H1 hits /api/cmd/stand_up directly, not /api/cmd/mode."""
    adapter.replies[("POST", "/api/cmd/stand_up")] = {"status": "standing"}
    result = adapter.stand_up()
    assert result["status"] == "standing"
    methods = [m for m, _, _ in adapter.call_log]
    paths = [p for _, p, _ in adapter.call_log]
    assert "POST" in methods
    assert "/api/cmd/stand_up" in paths


def test_lie_down_direct_endpoint(adapter):
    adapter.replies[("POST", "/api/cmd/lie_down")] = {"status": "lying"}
    result = adapter.lie_down()
    assert result["status"] == "lying"


def test_stand_up_error_fallback(adapter):
    """When server is down, stand_up returns a structured error."""
    adapter.replies.clear()
    result = adapter.stand_up()
    assert result["status"] == "error"
    assert result["adapter"] == "h1"


def test_gesture_forwards_name(adapter):
    adapter.replies[("POST", "/api/cmd/gesture")] = {"status": "ok"}
    adapter.gesture("wave")
    _, _, body = adapter.call_log[-1]
    assert body["name"] == "wave"


def test_set_gait_forwards_mode(adapter):
    adapter.replies[("POST", "/api/cmd/gait")] = {"status": "ok"}
    adapter.set_gait("TROT")
    _, _, body = adapter.call_log[-1]
    assert body["gait"] == "TROT"


# ── Speak ──────────────────────────────────────────────────────────────────


def test_speak_forwards_text_lang(adapter):
    adapter.replies[("POST", "/api/audio/speak")] = {"status": "ok"}
    adapter.speak("Привет", lang="ru")
    _, _, body = adapter.call_log[-1]
    assert body["text"] == "Привет"
    assert body["lang"] == "ru"


def test_capture_photo_uses_get(adapter):
    adapter.replies[("GET", "/api/camera/capture")] = {
        "ok": True, "image_b64": "deadbeef",
    }
    result = adapter.capture_photo()
    assert result["ok"] is True
    assert any(
        m == "GET" and p == "/api/camera/capture"
        for m, p, _ in adapter.call_log
    )


# ── Scenario injection ────────────────────────────────────────────────────


def test_inject_scenario_posts_overrides(adapter):
    adapter.replies[("POST", "/api/sim/context")] = {"ok": True}
    adapter.inject_scenario({"crowd_density": 0.8, "tilt_angle": 5})
    _, _, body = adapter.call_log[-1]
    assert body["crowd_density"] == 0.8
    assert body["tilt_angle"] == 5


def test_clear_scenario_posts_empty(adapter):
    adapter.replies[("POST", "/api/sim/context")] = {"ok": True}
    adapter.clear_scenario()
    _, _, body = adapter.call_log[-1]
    assert body == {}
