"""Local safety pipeline — runs on robot, checks every command.

Phase 1: Try to load ActionGate from libs/ontology (full 131-rule set from YAML).
         If libs not available → fall back to 6-rule built-in pipeline.
         Either way: fail-closed (error → DENY), binary ALLOW/DENY/LIMIT.

Per STEERING.md §3: L2a deterministic, no ML, no network I/O for rules themselves.
Per STEERING.md §4.C: fail-closed — error loading rules → DENY ALL.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Try to load ActionGate from mounted libs ──────────────────────────────────

_LIBS_DIR = os.environ.get("TRUST_LAYER_LIBS", "/app/libs")
_gate = None          # ActionGate singleton or None
_rules_loaded = 0     # count of rules loaded from YAML

def _try_load_action_gate():
    """Attempt to import ActionGate from libs/ontology. Returns gate or None."""
    global _gate, _rules_loaded
    if _LIBS_DIR and _LIBS_DIR not in sys.path:
        sys.path.insert(0, _LIBS_DIR)
    try:
        from ontology.action_gate import ActionGate  # noqa: PLC0415
        gate = ActionGate()
        _rules_loaded = len(gate._engine._loader._rules)  # type: ignore[attr-defined]
        logger.info(
            "safety_pipeline: ActionGate loaded — %d rules from YAML (libs=%s)",
            _rules_loaded, _LIBS_DIR,
        )
        return gate
    except Exception as exc:
        logger.warning(
            "safety_pipeline: ActionGate unavailable (%s) — using 6-rule fallback", exc
        )
        return None

_gate = _try_load_action_gate()


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class GateResult:
    decision: str = "ALLOW"   # ALLOW | DENY | LIMIT
    reason: str = ""
    params: dict = field(default_factory=dict)
    rule_id: str = ""
    audit_ref: str = ""       # e.g. "ISO 3691-4:2023 §6.2.3"
    violations: list = field(default_factory=list)


@dataclass
class ReasoningMessage:
    ts: float = 0.0
    key: str = ""
    text: str = ""


# ── Audit references for built-in fallback rules ─────────────────────────────

_FALLBACK_AUDIT: dict[str, str] = {
    "BATT-001":   "ISO 3691-4:2023 §4.4.4 (energy source safety)",
    "TILT-001":   "ISO 3691-4:2023 §4.4.2 (stability, tipping hazard)",
    "HUMAN-001":  "ISO 3691-4:2023 §4.3.4 (minimum separation distance 1.5 m)",
    "HUMAN-002":  "ISO 3691-4:2023 §4.3.4 (speed reduction near person at 2.5 m)",
    "SPEED-001":  "ISO 3691-4:2023 §6.2.3 (max operating speed 0.8 m/s advisory mode)",
    "ANGULAR-001":"ISO 3691-4:2023 §6.2.3 (angular velocity limit)",
    "OBS-001":    "ISO 3691-4:2023 §4.3.2 (obstacle detection and stop)",
}


# ── Safety pipeline ───────────────────────────────────────────────────────────

class SafetyPipeline:
    """Checks velocity commands against safety rules before forwarding.

    If ActionGate is available (libs mounted), uses full 131-rule YAML-based check.
    Otherwise falls back to 6 built-in rules with proper audit_ref.
    Always fail-closed: any exception → DENY.
    """

    # Noetix N2 thresholds (used in fallback + ActionGate context)
    MAX_SPEED_MPS     = 0.8
    MAX_ANGULAR_RPS   = 1.0
    BATTERY_CRITICAL  = 10.0
    TILT_LIMIT_DEG    = 20.0
    HUMAN_STOP_M      = 1.5
    HUMAN_SLOW_M      = 2.5
    HUMAN_SLOW_SPEED  = 0.3

    def __init__(self):
        self._lock = threading.Lock()
        self._reasoning: list[ReasoningMessage] = []
        self._cooldowns: dict[str, float] = {}
        self._stats = {
            "total_checks": 0,
            "denied": 0,
            "limited": 0,
            "allowed": 0,
            "rules_backend": "action_gate" if _gate else "fallback_6_rules",
            "rules_loaded": _rules_loaded if _gate else 6,
        }

    def check(
        self,
        vx: float,
        vy: float,
        wz: float,
        robot_state: dict,
        entities: list[dict],
    ) -> tuple[float, float, float, GateResult]:
        """Run safety checks. Returns (clamped_vx, clamped_vy, clamped_wz, result).

        Fail-closed: any exception → DENY with reason SAFETY_CHECK_ERROR.
        """
        try:
            return self._check_inner(vx, vy, wz, robot_state, entities)
        except Exception as exc:
            logger.error("safety_pipeline: unexpected error — DENY (fail-closed): %s", exc)
            with self._lock:
                self._stats["denied"] += 1
            return 0.0, 0.0, 0.0, GateResult(
                decision="DENY",
                reason=f"Safety check error (fail-closed): {exc}",
                rule_id="SAFETY-CHECK-ERROR",
                audit_ref="STEERING.md §4.C (fail-closed invariant)",
            )

    def _check_inner(
        self,
        vx: float,
        vy: float,
        wz: float,
        robot_state: dict,
        entities: list[dict],
    ) -> tuple[float, float, float, GateResult]:
        with self._lock:
            self._stats["total_checks"] += 1

        if _gate is not None:
            return self._check_via_action_gate(vx, vy, wz, robot_state, entities)
        return self._check_fallback(vx, vy, wz, robot_state, entities)

    # ── ActionGate path (131 YAML rules) ─────────────────────────────────

    def _check_via_action_gate(
        self,
        vx: float,
        vy: float,
        wz: float,
        robot_state: dict,
        entities: list[dict],
    ) -> tuple[float, float, float, GateResult]:
        """Delegate to ActionGate for full regulatory rule check."""
        speed = math.hypot(vx, vy)
        battery = float(robot_state.get("battery", 100))
        tilt = float(robot_state.get("tilt_deg", 0))
        min_human = float("inf")
        for e in entities:
            if e.get("is_human") or e.get("class_name") == "person":
                min_human = min(min_human, float(e.get("distance_m", 999)))

        try:
            from ontology.rule_engine import build_context  # noqa: PLC0415
            ctx = build_context(
                robot={
                    "battery_level": battery,
                    "is_e_stopped": False,
                    "sensor_ok": True,
                    "tracking_status": "ok",
                    "is_moving": speed > 0.05,
                    "is_charging": False,
                    "tilt_deg": abs(tilt),
                    "speed_mps": speed,
                },
                action={
                    "type": "navigate",
                    "speed_mps": speed,
                    "angular_velocity_rps": abs(wz),
                },
                zone={
                    "zone_type": "OperationalZone",
                    "humans_present": min_human < 5.0,
                    "access_level": 0,
                    "guarded": False,
                    "min_human_distance_m": min_human if min_human < 999 else None,
                },
            )
            result = _gate.check_action("navigate", ctx)
        except Exception as exc:
            logger.error("ActionGate.check_action failed — falling back to 6-rule: %s", exc)
            return self._check_fallback(vx, vy, wz, robot_state, entities)

        if not result.allowed:
            decision = "DENY"
            rule_id = result.reason_code or "GATE-REJECT"
            # Gather audit_ref from first violation
            audit = ""
            for v in (result.violations or []):
                a = v.get("audit", {})
                if a:
                    audit = f"{a.get('standard', '')} §{a.get('section', '')}".strip()
                    break
            audit = audit or f"audit_ref: {rule_id}"
            self._stats["denied"] += 1
            self._emit(
                rule_id,
                result.explain or f"Отказ: {rule_id} — {audit}",
            )
            return 0.0, 0.0, 0.0, GateResult(
                decision=decision,
                reason=result.explain or rule_id,
                rule_id=rule_id,
                audit_ref=audit,
                violations=result.violations or [],
            )

        # Allowed — still apply CommandClamp for speed/angular
        vx, vy, wz, clamp_result = self._apply_command_clamp(vx, vy, wz, speed)
        if clamp_result.decision == "LIMIT":
            self._stats["limited"] += 1
            return vx, vy, wz, clamp_result

        self._stats["allowed"] += 1
        if speed > 0.1:
            self._emit(
                "nominal",
                f"Движение разрешено (ActionGate: {_rules_loaded} правил). "
                f"Скорость {speed:.2f} м/с, батарея {battery:.0f}%.",
                5.0,
            )
        return vx, vy, wz, GateResult(decision="ALLOW")

    # ── CommandClamp ──────────────────────────────────────────────────────

    def _apply_command_clamp(
        self, vx: float, vy: float, wz: float, speed: float
    ) -> tuple[float, float, float, GateResult]:
        """Apply speed + angular velocity caps. Returns (vx, vy, wz, result)."""
        if speed > self.MAX_SPEED_MPS:
            scale = self.MAX_SPEED_MPS / speed
            vx *= scale
            vy *= scale
            self._emit(
                "speed_clamp",
                f"CommandClamp: скорость {speed:.2f}→{self.MAX_SPEED_MPS} м/с",
            )
            return vx, vy, wz, GateResult(
                decision="LIMIT",
                reason=f"Speed clamped from {speed:.2f} to {self.MAX_SPEED_MPS}",
                params={"original_speed": speed, "clamped_speed": self.MAX_SPEED_MPS},
                rule_id="SPEED-001",
                audit_ref=_FALLBACK_AUDIT["SPEED-001"],
            )
        if abs(wz) > self.MAX_ANGULAR_RPS:
            orig_wz = wz
            wz = self.MAX_ANGULAR_RPS * (1 if wz > 0 else -1)
            return vx, vy, wz, GateResult(
                decision="LIMIT",
                reason=f"Angular velocity clamped from {orig_wz:.2f} to {wz:.2f}",
                params={"original_wz": orig_wz, "clamped_wz": wz},
                rule_id="ANGULAR-001",
                audit_ref=_FALLBACK_AUDIT["ANGULAR-001"],
            )
        return vx, vy, wz, GateResult(decision="ALLOW")

    # ── Fallback: 6-rule pipeline ─────────────────────────────────────────

    def _check_fallback(
        self,
        vx: float,
        vy: float,
        wz: float,
        robot_state: dict,
        entities: list[dict],
    ) -> tuple[float, float, float, GateResult]:
        """6-rule deterministic fallback. Includes audit_ref for each rule."""
        speed = math.hypot(vx, vy)
        battery = float(robot_state.get("battery", 100))
        tilt = float(robot_state.get("tilt_deg", 0))

        # 1. Battery critical
        if battery < self.BATTERY_CRITICAL:
            self._stats["denied"] += 1
            self._emit(
                "battery_deny",
                f"Батарея {battery:.0f}% — ниже критического порога "
                f"{self.BATTERY_CRITICAL:.0f}%. Движение запрещено (BATT-001). "
                f"[{_FALLBACK_AUDIT['BATT-001']}]",
                8.0,
            )
            return 0, 0, 0, GateResult(
                decision="DENY",
                reason=f"Battery critical ({battery:.0f}%)",
                rule_id="BATT-001",
                audit_ref=_FALLBACK_AUDIT["BATT-001"],
            )

        # 2. Tilt E-STOP
        if abs(tilt) > self.TILT_LIMIT_DEG:
            self._stats["denied"] += 1
            self._emit(
                "tilt_estop",
                f"Наклон {tilt:.1f}° превышает порог {self.TILT_LIMIT_DEG}°. "
                "Аварийная остановка — риск опрокидывания.",
                5.0,
            )
            return 0, 0, 0, GateResult(
                decision="DENY",
                reason=f"Tilt {tilt:.1f}° exceeds {self.TILT_LIMIT_DEG}°",
                rule_id="TILT-001",
                audit_ref=_FALLBACK_AUDIT["TILT-001"],
            )

        # 3. Human proximity
        min_human_dist = float("inf")
        for e in entities:
            if e.get("is_human") or e.get("class_name") == "person":
                min_human_dist = min(min_human_dist, float(e.get("distance_m", 999)))

        if min_human_dist < self.HUMAN_STOP_M:
            self._stats["denied"] += 1
            self._emit(
                "human_stop",
                f"Человек в {min_human_dist:.1f} м — ближе порога "
                f"{self.HUMAN_STOP_M} м. Полная остановка (HUMAN-001).",
            )
            return 0, 0, 0, GateResult(
                decision="DENY",
                reason=f"Human too close ({min_human_dist:.1f}m < {self.HUMAN_STOP_M}m)",
                rule_id="HUMAN-001",
                audit_ref=_FALLBACK_AUDIT["HUMAN-001"],
            )

        if min_human_dist < self.HUMAN_SLOW_M and speed > self.HUMAN_SLOW_SPEED:
            scale = self.HUMAN_SLOW_SPEED / speed if speed > 0 else 1.0
            vx *= scale
            vy *= scale
            self._stats["limited"] += 1
            self._emit(
                "human_slow",
                f"Человек приближается — {min_human_dist:.1f} м. "
                f"Снижаю скорость до {self.HUMAN_SLOW_SPEED} м/с.",
            )
            return vx, vy, wz, GateResult(
                decision="LIMIT",
                reason=f"Human nearby ({min_human_dist:.1f}m), speed limited",
                params={"max_speed_mps": self.HUMAN_SLOW_SPEED},
                rule_id="HUMAN-002",
                audit_ref=_FALLBACK_AUDIT["HUMAN-002"],
            )

        # 4. Obstacle proximity
        min_obs_dist = float("inf")
        for e in entities:
            if not (e.get("is_human") or e.get("class_name") == "person"):
                d = float(e.get("distance_m", 999))
                if d < min_obs_dist:
                    min_obs_dist = d

        if min_obs_dist < 0.5 and speed > 0.1:
            self._stats["denied"] += 1
            self._emit("obstacle_stop", f"Препятствие в {min_obs_dist:.1f} м — остановка.")
            return 0, 0, 0, GateResult(
                decision="DENY",
                reason=f"Obstacle too close ({min_obs_dist:.1f}m)",
                rule_id="OBS-001",
                audit_ref=_FALLBACK_AUDIT["OBS-001"],
            )

        # 5+6. CommandClamp (speed + angular)
        vx, vy, wz, clamp_result = self._apply_command_clamp(vx, vy, wz, speed)
        if clamp_result.decision == "LIMIT":
            self._stats["limited"] += 1
            return vx, vy, wz, clamp_result

        # All clear
        self._stats["allowed"] += 1
        if speed > 0.1:
            self._emit(
                "nominal",
                f"Движение разрешено. Скорость {speed:.2f} м/с, "
                f"батарея {battery:.0f}%, наклон {tilt:.1f}°.",
                5.0,
            )
        return vx, vy, wz, GateResult(decision="ALLOW")

    # ── Reasoning + stats ─────────────────────────────────────────────────

    def get_reasoning(self, clear: bool = True) -> list[dict]:
        with self._lock:
            msgs = [
                {"ts": m.ts, "key": m.key, "text": m.text}
                for m in self._reasoning
            ]
            if clear:
                self._reasoning.clear()
            return msgs

    def get_stats(self) -> dict:
        return dict(self._stats)

    # ── internal helpers ──────────────────────────────────────────────────

    def _emit(self, key: str, text: str, interval: float = 3.0):
        now = time.time()
        last = self._cooldowns.get(key, 0)
        if now - last < interval:
            return
        self._cooldowns[key] = now
        with self._lock:
            self._reasoning.append(ReasoningMessage(ts=now, key=key, text=text))
            if len(self._reasoning) > 100:
                self._reasoning[:] = self._reasoning[-50:]
