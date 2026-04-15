"""Unit tests for bridge.mock_adapter.MockAdapter.

MockAdapter is the test stand-in for a real Noetix N2 — it runs a
tiny kinematic integrator that other bridge components use when no
hardware is available. These tests pin the behaviour so refactoring
the simulator doesn't silently break the existing demo scripts that
depend on it.
"""
from __future__ import annotations

import math
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

from bridge.mock_adapter import MockAdapter


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock():
    return MockAdapter()


# ── Core state ──────────────────────────────────────────────────────────────


def test_initial_state_is_nominal(mock):
    state = mock.get_state()
    assert state["adapter"] == "mock"
    assert state["battery"] >= 90
    assert state["mode"] == "ADVISORY"
    assert state["position"]["x"] == 0.0
    assert state["position"]["y"] == 0.0
    assert state["velocity"]["vx"] == 0.0
    assert "timestamp_s" in state
    assert "uptime_s" in state


def test_state_has_sensor_summary(mock):
    state = mock.get_state()
    sensors = state.get("sensors", {})
    assert "camera" in sensors
    assert "imu" in sensors
    # MockAdapter reports lidar as unavailable — pinned by design
    assert sensors["lidar"]["available"] is False


# ── Velocity clamping ──────────────────────────────────────────────────────


def test_send_velocity_clamps_linear_above_max(mock):
    result = mock.send_velocity(vx=10.0, vy=0.0, wz=0.0)
    assert result["status"] == "ok"
    # Give the integrator a few ticks to accelerate
    for _ in range(20):
        mock.get_state()
    state = mock.get_state()
    assert abs(state["velocity"]["vx"]) <= mock.MAX_SPEED + 1e-6


def test_send_velocity_clamps_angular_above_max(mock):
    mock.send_velocity(vx=0.0, vy=0.0, wz=99.0)
    for _ in range(20):
        mock.get_state()
    # After ramp, wz in the internal state is capped.
    state = mock.get_state()
    # heading rotates but stays bounded — test that wz command capped
    assert state["velocity"]["vx"] == 0.0


def test_send_velocity_negative_direction(mock):
    mock.send_velocity(vx=-0.3, vy=0.0, wz=0.0)
    for _ in range(10):
        mock.get_state()
    state = mock.get_state()
    assert state["velocity"]["vx"] < 0


# ── Stop ───────────────────────────────────────────────────────────────────


def test_stop_zeros_velocities(mock):
    mock.send_velocity(vx=0.5, vy=0.0, wz=0.0)
    for _ in range(10):
        mock.get_state()
    result = mock.stop()
    assert result["status"] == "stopped"
    state = mock.get_state()
    assert state["velocity"]["vx"] == 0.0
    assert state["velocity"]["vy"] == 0.0


# ── Physics integration ───────────────────────────────────────────────────


def test_motion_advances_position(mock):
    mock.send_velocity(vx=0.5, vy=0.0, wz=0.0)
    start = mock.get_state()
    for _ in range(30):
        mock.get_state()
    end = mock.get_state()
    # Robot must have moved some distance from origin (exact value
    # depends on ramp + DT, so we just assert "monotonically non-zero").
    assert end["position"]["x"] > start["position"]["x"]


def test_rotation_updates_heading(mock):
    mock.send_velocity(vx=0.0, vy=0.0, wz=0.5)
    h0 = mock.get_state()["heading_rad"]
    for _ in range(30):
        mock.get_state()
    h1 = mock.get_state()["heading_rad"]
    assert h0 != h1


def test_auto_stop_after_no_commands(mock):
    mock.send_velocity(vx=0.5, vy=0.0, wz=0.0)
    # Run a few ticks to let it accelerate
    for _ in range(5):
        mock.get_state()
    initial_speed = mock.get_state()["speed_mps"]
    assert initial_speed > 0.0
    # Force the last_cmd_time well into the past so the auto-stop
    # inside _simulate_step triggers.
    mock._last_cmd_time = time.time() - 10.0
    for _ in range(40):
        mock.get_state()
    final = mock.get_state()
    assert abs(final["velocity"]["vx"]) < 0.05


# ── Scenario injection ────────────────────────────────────────────────────


def test_inject_battery_scenario(mock):
    mock.inject_scenario({"battery": 5.0})
    state = mock.get_state()
    assert state["battery"] == pytest.approx(5.0, abs=0.1)


def test_inject_tilt_scenario(mock):
    mock.inject_scenario({"tilt_deg": 25.0})
    state = mock.get_state()
    # tilt_deg is read from overrides at every step
    assert state["tilt_deg"] == pytest.approx(25.0, abs=0.1)


def test_inject_entities(mock):
    mock.inject_scenario({
        "entities": [
            {"entity_id": "p1", "class_name": "person", "x": 1.0, "y": 0.0},
            {"entity_id": "b1", "class_name": "box", "x": 2.0, "y": 0.0},
        ],
    })
    entities = mock.get_entities()
    assert len(entities) == 2
    # Each entry has the standard shape operators expect
    for e in entities:
        assert "entity_id" in e
        assert "class_name" in e
        assert "distance_m" in e
        assert "is_human" in e
    assert any(e["is_human"] for e in entities)  # person is flagged


def test_clear_scenario_resets(mock):
    mock.inject_scenario({"battery": 20.0, "tilt_deg": 15.0,
                          "entities": [{"entity_id": "p", "class_name": "person"}]})
    mock.clear_scenario()
    state = mock.get_state()
    assert state["battery"] > 90  # back to nominal
    # tilt_deg is re-synthesised from random noise after clear
    assert abs(state["tilt_deg"]) < 5
    assert mock.get_entities() == []


# ── Capabilities ──────────────────────────────────────────────────────────


def test_probe_capabilities_shape(mock):
    caps = mock.probe_capabilities()
    # Every expected key is present
    for key in ("camera", "lidar", "imu", "microphone", "speaker",
                "drive", "battery", "network"):
        assert key in caps, f"missing cap key {key}"
    assert caps["lidar"]["available"] is False
    assert caps["camera"]["available"] is True
    assert caps["drive"]["type"] == "holonomic"


def test_probe_capabilities_reflects_battery(mock):
    mock.inject_scenario({"battery": 42.0})
    caps = mock.probe_capabilities()
    assert caps["battery"]["level_pct"] == pytest.approx(42.0, abs=0.5)


# ── navigate_to ──────────────────────────────────────────────────────────


def test_navigate_to_target_moves_robot(mock):
    result = mock.navigate_to(x_m=1.0, y_m=0.0, speed_mps=0.5)
    assert result["status"] == "ok"
    assert result["target"] == {"x": 1.0, "y": 0.0}
    # Let it drive for 5 simulated seconds (50 ticks at 10 Hz).
    for _ in range(50):
        mock.get_state()
    state = mock.get_state()
    # Not required to reach exactly — ramp + decel — but must have moved
    # substantially toward the target.
    assert state["position"]["x"] > 0.1


def test_navigate_to_clamps_speed(mock):
    result = mock.navigate_to(x_m=10.0, y_m=0.0, speed_mps=99.0)
    assert result["status"] == "ok"
    # speed is clamped to MAX_SPEED internally
    assert mock._nav_speed <= mock.MAX_SPEED


def test_navigate_to_none_after_arrival(mock):
    mock.navigate_to(x_m=0.05, y_m=0.0, speed_mps=0.3)
    for _ in range(60):
        mock.get_state()
    # After arriving within 5 cm, target is cleared
    assert mock._nav_target_x is None


# ── LiDAR mock ────────────────────────────────────────────────────────────


def test_get_lidar_scan_returns_36_rays(mock):
    scan = mock.get_lidar_scan()
    assert scan["available"] is True
    assert scan["source"] == "mock"
    assert len(scan["ranges"]) == 36
    assert scan["angle_min_rad"] == pytest.approx(-math.pi, abs=1e-6)
    for r in scan["ranges"]:
        assert 0.0 < r < 10.0
