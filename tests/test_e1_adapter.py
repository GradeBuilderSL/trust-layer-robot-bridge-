"""Unit tests for bridge.e1_adapter.

Exercises the E1Adapter class without any network activity — we stub
the HTTP helpers (`_get` / `_post`) directly on the instance so the
test suite runs in milliseconds and doesn't need an actual Jetson on
the other end. Covers:

* Envelope clamping (walking vs running mode)
* Mode switching via set_mode / stand_up / lie_down
* Gesture dispatch
* handle_action generic dispatcher
* spin / velocity forwarding
* Graceful error handling when e1_server is unreachable
"""
from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

from bridge.e1_adapter import E1Adapter, E1Limits


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def adapter():
    """E1Adapter instance with in-memory fake HTTP helpers.

    Every call to self._get / self._post appends to `call_log` so tests
    can assert the exact calls the adapter made, and the return value
    is taken from `replies` keyed by path. Tests override either dict
    before exercising the adapter.
    """
    a = E1Adapter(robot_url="http://e1.test:8083")
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


# ── Velocity clamping ──────────────────────────────────────────────────────


def test_send_velocity_walking_clamp_forward(adapter):
    """Walking envelope caps vx at MAX_SPEED_WALKING_MPS."""
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    result = adapter.send_velocity(
        vx=E1Limits.MAX_SPEED_WALKING_MPS + 2.0, vy=0.0, wz=0.0,
    )
    assert result["status"] == "ok"
    assert result["adapter"] == "e1"
    # The clamped value — not the requested one — is what got sent.
    assert result["applied"]["vx"] == pytest.approx(E1Limits.MAX_SPEED_WALKING_MPS)
    _, _, body = adapter.call_log[-1]
    assert body["vx"] == pytest.approx(E1Limits.MAX_SPEED_WALKING_MPS)


def test_send_velocity_walking_clamp_reverse(adapter):
    """Backward velocity is symmetrically clamped at -MAX_SPEED_WALKING_MPS."""
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    result = adapter.send_velocity(
        vx=-(E1Limits.MAX_SPEED_WALKING_MPS + 5.0), vy=0.0, wz=0.0,
    )
    assert result["applied"]["vx"] == pytest.approx(-E1Limits.MAX_SPEED_WALKING_MPS)


def test_send_velocity_running_mode_higher_cap(adapter):
    """Running mode allows higher linear velocity than walking."""
    adapter._current_mode = "running"
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    # Something between the two caps — allowed in running, clamped in walking.
    target = (E1Limits.MAX_SPEED_WALKING_MPS + E1Limits.MAX_SPEED_RUNNING_MPS) / 2
    result = adapter.send_velocity(vx=target, vy=0.0, wz=0.0)
    assert result["applied"]["vx"] == pytest.approx(target)


def test_send_velocity_angular_clamp(adapter):
    """wz is always capped at MAX_ANGULAR_RAD_S regardless of mode."""
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    result = adapter.send_velocity(
        vx=0.0, vy=0.0, wz=E1Limits.MAX_ANGULAR_RAD_S + 10.0,
    )
    assert result["applied"]["wz"] == pytest.approx(E1Limits.MAX_ANGULAR_RAD_S)


def test_send_velocity_http_failure_returns_error(adapter):
    """When e1_server is unreachable, adapter returns structured error."""
    adapter.replies.clear()  # _post returns None for every call
    adapter._last_error = "http://e1.test:8083: Connection refused"
    result = adapter.send_velocity(vx=0.3, vy=0.0, wz=0.0)
    assert result["status"] == "error"
    assert result["adapter"] == "e1"
    assert "error" in result


# ── Stop / mode / gestures ─────────────────────────────────────────────────


def test_stop_sends_cmd_stop(adapter):
    adapter.replies[("POST", "/api/cmd/stop")] = {"status": "stopped"}
    result = adapter.stop()
    assert result["status"] == "stopped"
    assert ("POST", "/api/cmd/stop", {}) in adapter.call_log


def test_set_mode_updates_internal_mode(adapter):
    adapter.replies[("POST", "/api/cmd/mode")] = {"status": "switched"}
    result = adapter.set_mode("running")
    assert result["status"] == "switched"
    assert adapter._current_mode == "running"
    _, _, body = adapter.call_log[-1]
    assert body["mode"] == "running"


def test_set_mode_rejects_unknown(adapter):
    result = adapter.set_mode("teleport")
    assert result["status"] == "error"
    assert "unknown mode" in result["error"]
    # valid modes list is surfaced so the caller can recover
    assert "valid" in result


def test_set_mode_case_insensitive(adapter):
    adapter.replies[("POST", "/api/cmd/mode")] = {"status": "switched"}
    adapter.set_mode("WALKING")
    _, _, body = adapter.call_log[-1]
    assert body["mode"] == "walking"


def test_stand_up_calls_preparation_mode(adapter):
    adapter.replies[("POST", "/api/cmd/mode")] = {"status": "ok"}
    adapter.stand_up()
    _, _, body = adapter.call_log[-1]
    assert body["mode"] == "preparation"


def test_lie_down_calls_disabled_mode(adapter):
    adapter.replies[("POST", "/api/cmd/mode")] = {"status": "ok"}
    adapter.lie_down()
    _, _, body = adapter.call_log[-1]
    assert body["mode"] == "disabled"


def test_gesture_dispatch(adapter):
    adapter.replies[("POST", "/api/cmd/gesture")] = {"status": "ok"}
    result = adapter.gesture("greet")
    assert result["status"] == "ok"
    _, _, body = adapter.call_log[-1]
    assert body["name"] == "greet"
    assert body["slot"]  # non-empty slot resolved


# ── handle_action (generic dispatcher) ─────────────────────────────────────


def test_handle_action_wave_maps_to_greet(adapter):
    adapter.replies[("POST", "/api/cmd/gesture")] = {"status": "ok"}
    result = adapter.handle_action("wave", {})
    assert result["status"] == "ok"
    _, _, body = adapter.call_log[-1]
    assert body["name"] == "greet"


def test_handle_action_spin_forwards_angular(adapter):
    adapter.replies[("POST", "/api/cmd/walk")] = {"status": "ok"}
    result = adapter.handle_action("spin", {})
    assert result["status"] == "ok"
    _, _, body = adapter.call_log[-1]
    assert body["vyaw"] > 0.0  # non-zero rotation


def test_handle_action_mode_forwards_params(adapter):
    adapter.replies[("POST", "/api/cmd/mode")] = {"status": "switched"}
    adapter.handle_action("mode", {"mode": "running"})
    _, _, body = adapter.call_log[-1]
    assert body["mode"] == "running"
    assert adapter._current_mode == "running"


def test_handle_action_gesture_forwards_name(adapter):
    adapter.replies[("POST", "/api/cmd/gesture")] = {"status": "ok"}
    adapter.handle_action("gesture", {"name": "handshake"})
    _, _, body = adapter.call_log[-1]
    assert body["name"] == "handshake"


def test_handle_action_unknown_returns_not_supported(adapter):
    result = adapter.handle_action("teleport_to_mars", {})
    assert result["status"] == "not_supported"
    assert result["adapter"] == "e1"
    assert "teleport_to_mars" in result["note"]


# ── Capabilities ───────────────────────────────────────────────────────────


def test_probe_capabilities_server_unreachable(adapter):
    """When e1_server doesn't answer, adapter returns a disconnected
    skeleton instead of crashing — and lidar is still pinned to
    not_installed because E1 physically has no lidar."""
    adapter.replies.clear()
    caps = adapter.probe_capabilities()
    assert caps["lidar"]["available"] is False
    assert caps["lidar"]["probe"] == "not_installed"
    # Every capability key is present in the disconnected fallback
    for key in ("camera", "imu", "microphone", "speaker", "drive", "battery", "network"):
        assert key in caps


def test_probe_capabilities_forces_lidar_not_installed(adapter):
    """Even when the server reports lidar as available, we override:
    E1 has a depth camera, not a lidar, and we don't want that
    advertisement to mislead the safety pipeline."""
    adapter.replies[("GET", "/api/capabilities")] = {
        "camera": {"available": True, "probe": "ok"},
        "lidar": {"available": True, "probe": "ok"},
    }
    caps = adapter.probe_capabilities()
    assert caps["lidar"]["available"] is False
    assert caps["lidar"]["probe"] == "not_installed"
    assert "no lidar" in caps["lidar"]["note"]


# ── Speak / listen (iFlytek proxy) ─────────────────────────────────────────


def test_speak_forwards_text_and_lang(adapter):
    adapter.replies[("POST", "/api/audio/speak")] = {"status": "ok"}
    result = adapter.speak("Привет мир", lang="ru")
    assert result["status"] == "ok"
    _, _, body = adapter.call_log[-1]
    assert body["text"] == "Привет мир"
    assert body["lang"] == "ru"


def test_speak_error_when_server_down(adapter):
    adapter.replies.clear()
    result = adapter.speak("anything")
    assert result["status"] == "error"
    assert result["adapter"] == "e1"
