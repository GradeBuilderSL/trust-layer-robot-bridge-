"""Unit tests for SafetyPipeline fallback rules.

Tests the 6-rule deterministic fallback pipeline (no ActionGate / no libs).
"""
from __future__ import annotations

import math
import sys
import os

# Ensure bridge package is importable from repo root
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# Force fallback mode: prevent ActionGate from loading by ensuring TRUST_LAYER_LIBS
# points to a non-existent path so _try_load_action_gate returns None.
os.environ["TRUST_LAYER_LIBS"] = "/nonexistent_libs_path"

from bridge.safety_pipeline import SafetyPipeline, GateResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_state(battery: float = 80.0, tilt_deg: float = 0.0) -> dict:
    """Return a healthy robot state dict."""
    return {"battery": battery, "tilt_deg": tilt_deg}


def _human(distance_m: float) -> dict:
    return {"is_human": True, "distance_m": distance_m}


def _obstacle(distance_m: float) -> dict:
    return {"class_name": "box", "distance_m": distance_m}


# ---------------------------------------------------------------------------
# Rule 1 — Battery < 10% => DENY (BATT-001)
# ---------------------------------------------------------------------------

class TestBatteryRule:
    def test_deny_when_battery_below_threshold(self):
        p = SafetyPipeline()
        vx, vy, wz, res = p.check(0.5, 0.0, 0.0, _ok_state(battery=5.0), [])
        assert res.decision == "DENY"
        assert res.rule_id == "BATT-001"
        assert vx == 0 and vy == 0 and wz == 0

    def test_deny_at_zero_battery(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.3, 0.0, 0.0, _ok_state(battery=0.0), [])
        assert res.decision == "DENY"
        assert res.rule_id == "BATT-001"

    def test_allow_at_exactly_10_pct(self):
        """Threshold is < 10, so exactly 10 should NOT trigger BATT-001."""
        p = SafetyPipeline()
        _, _, _, res = p.check(0.3, 0.0, 0.0, _ok_state(battery=10.0), [])
        assert res.decision != "DENY" or res.rule_id != "BATT-001"

    def test_allow_above_threshold(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.3, 0.0, 0.0, _ok_state(battery=50.0), [])
        assert res.decision == "ALLOW"


# ---------------------------------------------------------------------------
# Rule 2 — Tilt > 20 deg => DENY (TILT-001)
# ---------------------------------------------------------------------------

class TestTiltRule:
    def test_deny_positive_tilt(self):
        p = SafetyPipeline()
        vx, vy, wz, res = p.check(0.3, 0.0, 0.0, _ok_state(tilt_deg=25.0), [])
        assert res.decision == "DENY"
        assert res.rule_id == "TILT-001"
        assert vx == 0 and vy == 0 and wz == 0

    def test_deny_negative_tilt(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.3, 0.0, 0.0, _ok_state(tilt_deg=-25.0), [])
        assert res.decision == "DENY"
        assert res.rule_id == "TILT-001"

    def test_allow_at_exactly_20(self):
        """Threshold is > 20, so exactly 20 should NOT trigger."""
        p = SafetyPipeline()
        _, _, _, res = p.check(0.3, 0.0, 0.0, _ok_state(tilt_deg=20.0), [])
        assert res.rule_id != "TILT-001"

    def test_allow_small_tilt(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.3, 0.0, 0.0, _ok_state(tilt_deg=5.0), [])
        assert res.decision == "ALLOW"


# ---------------------------------------------------------------------------
# Rule 3 — Human < 1.5m => DENY (HUMAN-001)
# ---------------------------------------------------------------------------

class TestHumanStopRule:
    def test_deny_when_human_very_close(self):
        p = SafetyPipeline()
        vx, vy, wz, res = p.check(0.5, 0.0, 0.0, _ok_state(), [_human(1.0)])
        assert res.decision == "DENY"
        assert res.rule_id == "HUMAN-001"
        assert vx == 0 and vy == 0 and wz == 0

    def test_deny_at_zero_distance(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.5, 0.0, 0.0, _ok_state(), [_human(0.0)])
        assert res.decision == "DENY"
        assert res.rule_id == "HUMAN-001"

    def test_allow_at_exactly_1_5m(self):
        """Threshold is < 1.5, so exactly 1.5 should NOT trigger HUMAN-001."""
        p = SafetyPipeline()
        _, _, _, res = p.check(0.3, 0.0, 0.0, _ok_state(), [_human(1.5)])
        assert res.rule_id != "HUMAN-001"

    def test_person_class_name_detected(self):
        """Entity with class_name='person' (no is_human) should also trigger."""
        p = SafetyPipeline()
        entity = {"class_name": "person", "distance_m": 0.5}
        _, _, _, res = p.check(0.5, 0.0, 0.0, _ok_state(), [entity])
        assert res.decision == "DENY"
        assert res.rule_id == "HUMAN-001"


# ---------------------------------------------------------------------------
# Rule 4 — Human < 2.5m & speed > 0.3 => LIMIT (HUMAN-002)
# ---------------------------------------------------------------------------

class TestHumanSlowRule:
    def test_limit_when_human_nearby_and_fast(self):
        p = SafetyPipeline()
        vx, vy, wz, res = p.check(0.6, 0.0, 0.0, _ok_state(), [_human(2.0)])
        assert res.decision == "LIMIT"
        assert res.rule_id == "HUMAN-002"
        # Speed should be scaled to 0.3 m/s
        result_speed = math.hypot(vx, vy)
        assert abs(result_speed - 0.3) < 0.01

    def test_no_limit_when_human_nearby_but_slow(self):
        """Speed <= 0.3 should not trigger HUMAN-002."""
        p = SafetyPipeline()
        _, _, _, res = p.check(0.2, 0.0, 0.0, _ok_state(), [_human(2.0)])
        assert res.rule_id != "HUMAN-002"

    def test_no_limit_when_human_far(self):
        """Human at 3.0m (>2.5) should not trigger HUMAN-002."""
        p = SafetyPipeline()
        _, _, _, res = p.check(0.6, 0.0, 0.0, _ok_state(), [_human(3.0)])
        assert res.rule_id != "HUMAN-002"

    def test_wz_preserved_during_limit(self):
        """Angular velocity should NOT be scaled by HUMAN-002 rule."""
        p = SafetyPipeline()
        _, _, wz, res = p.check(0.6, 0.0, 0.5, _ok_state(), [_human(2.0)])
        assert res.decision == "LIMIT"
        assert wz == 0.5


# ---------------------------------------------------------------------------
# Rule 5 — Obstacle < 0.5m => DENY (OBS-001)
# ---------------------------------------------------------------------------

class TestObstacleRule:
    def test_deny_when_obstacle_close_and_moving(self):
        p = SafetyPipeline()
        vx, vy, wz, res = p.check(0.3, 0.0, 0.0, _ok_state(), [_obstacle(0.3)])
        assert res.decision == "DENY"
        assert res.rule_id == "OBS-001"
        assert vx == 0 and vy == 0 and wz == 0

    def test_allow_when_obstacle_close_but_stationary(self):
        """Speed <= 0.1 should not trigger OBS-001 even with close obstacle."""
        p = SafetyPipeline()
        _, _, _, res = p.check(0.05, 0.0, 0.0, _ok_state(), [_obstacle(0.3)])
        assert res.rule_id != "OBS-001"

    def test_allow_when_obstacle_far(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.5, 0.0, 0.0, _ok_state(), [_obstacle(1.0)])
        # Either ALLOW outright, or LIMIT via the velocity-polygon envelope
        # that treats 1.0 m as "expanded zone" — both are acceptable here.
        # The test's intent is "no DENY": far obstacles must not block.
        assert res.decision in ("ALLOW", "LIMIT")
        assert res.decision != "DENY"

    def test_human_not_treated_as_obstacle(self):
        """Human entity should be handled by HUMAN rules, not OBS-001."""
        p = SafetyPipeline()
        _, _, _, res = p.check(0.3, 0.0, 0.0, _ok_state(), [_human(0.3)])
        # Should be HUMAN-001 (deny due to < 1.5m), not OBS-001
        assert res.rule_id == "HUMAN-001"


# ---------------------------------------------------------------------------
# Rule 6 — Speed clamp (SPEED-001) and angular clamp (ANGULAR-001)
# ---------------------------------------------------------------------------

class TestSpeedClampRule:
    def test_clamp_high_linear_speed(self):
        p = SafetyPipeline()
        vx, vy, wz, res = p.check(1.5, 0.0, 0.0, _ok_state(), [])
        assert res.decision == "LIMIT"
        assert res.rule_id == "SPEED-001"
        result_speed = math.hypot(vx, vy)
        assert abs(result_speed - 0.8) < 0.01

    def test_clamp_diagonal_speed(self):
        """hypot(1.0, 1.0) ~ 1.41, should be clamped to 0.8."""
        p = SafetyPipeline()
        vx, vy, _, res = p.check(1.0, 1.0, 0.0, _ok_state(), [])
        assert res.decision == "LIMIT"
        assert res.rule_id == "SPEED-001"
        result_speed = math.hypot(vx, vy)
        assert abs(result_speed - 0.8) < 0.01

    def test_allow_speed_within_limit(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.5, 0.0, 0.0, _ok_state(), [])
        assert res.decision == "ALLOW"

    def test_clamp_high_angular_velocity(self):
        p = SafetyPipeline()
        _, _, wz, res = p.check(0.3, 0.0, 2.0, _ok_state(), [])
        assert res.decision == "LIMIT"
        assert res.rule_id == "ANGULAR-001"
        assert abs(wz - 1.0) < 0.01

    def test_clamp_negative_angular_velocity(self):
        p = SafetyPipeline()
        _, _, wz, res = p.check(0.3, 0.0, -2.0, _ok_state(), [])
        assert res.decision == "LIMIT"
        assert res.rule_id == "ANGULAR-001"
        assert abs(wz - (-1.0)) < 0.01


# ---------------------------------------------------------------------------
# Rule priority (battery checked before tilt before human, etc.)
# ---------------------------------------------------------------------------

class TestRulePriority:
    def test_battery_takes_priority_over_tilt(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.5, 0.0, 0.0, {"battery": 5, "tilt_deg": 30}, [])
        assert res.rule_id == "BATT-001"

    def test_tilt_takes_priority_over_human(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.5, 0.0, 0.0, _ok_state(tilt_deg=25), [_human(1.0)])
        assert res.rule_id == "TILT-001"

    def test_human_stop_takes_priority_over_obstacle(self):
        p = SafetyPipeline()
        _, _, _, res = p.check(0.5, 0.0, 0.0, _ok_state(), [_human(1.0), _obstacle(0.3)])
        assert res.rule_id == "HUMAN-001"


# ---------------------------------------------------------------------------
# Fail-closed behavior
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_exception_in_robot_state_causes_deny(self):
        """If robot_state is broken (e.g. non-numeric), pipeline should DENY."""
        p = SafetyPipeline()
        # Pass a battery value that can't convert to float
        vx, vy, wz, res = p.check(0.3, 0.0, 0.0, {"battery": "not_a_number"}, [])
        assert res.decision == "DENY"
        assert vx == 0 and vy == 0 and wz == 0


# ---------------------------------------------------------------------------
# GateResult dataclass
# ---------------------------------------------------------------------------

class TestGateResult:
    def test_defaults(self):
        r = GateResult()
        assert r.decision == "ALLOW"
        assert r.reason == ""
        assert r.rule_id == ""
        assert r.violations == []
        assert r.params == {}

    def test_custom_values(self):
        r = GateResult(decision="DENY", rule_id="TEST-1", reason="test reason")
        assert r.decision == "DENY"
        assert r.rule_id == "TEST-1"


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_count_checks(self):
        p = SafetyPipeline()
        p.check(0.3, 0.0, 0.0, _ok_state(), [])
        p.check(0.3, 0.0, 0.0, _ok_state(battery=5), [])
        p.check(1.5, 0.0, 0.0, _ok_state(), [])
        stats = p.get_stats()
        assert stats["total_checks"] == 3
        assert stats["allowed"] >= 1
        assert stats["denied"] >= 1
        assert stats["limited"] >= 1

    def test_stats_backend_is_fallback(self):
        p = SafetyPipeline()
        stats = p.get_stats()
        assert stats["rules_backend"] == "fallback_6_rules"
        # Fallback count started at 6 and grew to 8 when VELOCITY-POLYGON
        # rules were added. Assert a minimum rather than an exact number
        # so this test doesn't need touching every time a rule is added.
        assert stats["rules_loaded"] >= 6
