"""Local safety pipeline — runs on robot, checks every command.

Implements GateEngine-compatible checks (speed, tilt, battery, proximity)
and generates reasoning messages explaining each decision.
"""
import math
import time
import threading
from dataclasses import dataclass, field


@dataclass
class GateResult:
    decision: str = "ALLOW"  # ALLOW | DENY | LIMIT
    reason: str = ""
    params: dict = field(default_factory=dict)
    rule_id: str = ""


@dataclass
class ReasoningMessage:
    ts: float = 0.0
    key: str = ""
    text: str = ""


class SafetyPipeline:
    """Checks velocity commands against safety rules before forwarding."""

    # Noetix N2 safety thresholds
    MAX_SPEED_MPS = 0.8
    MAX_ANGULAR_RPS = 1.0
    BATTERY_CRITICAL = 10.0
    TILT_LIMIT_DEG = 20.0
    HUMAN_STOP_M = 1.5
    HUMAN_SLOW_M = 2.5
    HUMAN_SLOW_SPEED = 0.3

    def __init__(self):
        self._lock = threading.Lock()
        self._reasoning: list[ReasoningMessage] = []
        self._cooldowns: dict[str, float] = {}
        self._stats = {
            "total_checks": 0,
            "denied": 0,
            "limited": 0,
            "allowed": 0,
        }

    def check(
        self,
        vx: float,
        vy: float,
        wz: float,
        robot_state: dict,
        entities: list[dict],
    ) -> tuple[float, float, float, GateResult]:
        """Run safety checks. Returns (clamped_vx, clamped_vy, clamped_wz, result)."""
        with self._lock:
            self._stats["total_checks"] += 1

        speed = math.hypot(vx, vy)
        battery = robot_state.get("battery", 100)
        tilt = robot_state.get("tilt_deg", 0)

        # 1. Battery critical
        if battery < self.BATTERY_CRITICAL:
            self._stats["denied"] += 1
            self._emit("battery_deny",
                        f"Батарея {battery:.0f}% — ниже критического порога "
                        f"{self.BATTERY_CRITICAL:.0f}%. "
                        "Движение запрещено (BATT-001).", 8.0)
            return 0, 0, 0, GateResult(
                decision="DENY", reason="Battery critical",
                rule_id="BATT-001",
            )

        # 2. Tilt E-STOP
        if abs(tilt) > self.TILT_LIMIT_DEG:
            self._stats["denied"] += 1
            self._emit("tilt_estop",
                        f"Наклон {tilt:.1f}° превышает порог "
                        f"{self.TILT_LIMIT_DEG}°. "
                        "Аварийная остановка — риск опрокидывания.", 5.0)
            return 0, 0, 0, GateResult(
                decision="DENY", reason="Tilt exceeds safe limit",
                rule_id="TILT-001",
            )

        # 3. Human proximity
        min_human_dist = float("inf")
        for e in entities:
            if e.get("is_human") or e.get("class_name") == "person":
                min_human_dist = min(min_human_dist, e.get("distance_m", 999))

        if min_human_dist < self.HUMAN_STOP_M:
            self._stats["denied"] += 1
            self._emit("human_stop",
                        f"Человек в {min_human_dist:.1f} м — ближе порога "
                        f"{self.HUMAN_STOP_M} м. "
                        "Полная остановка (HUMAN-001).")
            return 0, 0, 0, GateResult(
                decision="DENY",
                reason=f"Human too close ({min_human_dist:.1f}m)",
                rule_id="HUMAN-001",
            )

        if min_human_dist < self.HUMAN_SLOW_M and speed > self.HUMAN_SLOW_SPEED:
            scale = self.HUMAN_SLOW_SPEED / speed if speed > 0 else 1.0
            vx *= scale
            vy *= scale
            self._stats["limited"] += 1
            self._emit("human_slow",
                        f"Человек приближается — {min_human_dist:.1f} м. "
                        f"Снижаю скорость до {self.HUMAN_SLOW_SPEED} м/с.")
            return vx, vy, wz, GateResult(
                decision="LIMIT",
                reason=f"Human nearby ({min_human_dist:.1f}m)",
                params={"max_speed_mps": self.HUMAN_SLOW_SPEED},
                rule_id="HUMAN-002",
            )

        # 4. Speed limit
        if speed > self.MAX_SPEED_MPS:
            scale = self.MAX_SPEED_MPS / speed
            vx *= scale
            vy *= scale
            self._stats["limited"] += 1
            self._emit("speed_limit",
                        f"Запрошенная скорость {speed:.2f} м/с превышает "
                        f"лимит {self.MAX_SPEED_MPS} м/с. Ограничиваю.")
            return vx, vy, wz, GateResult(
                decision="LIMIT",
                reason=f"Speed capped from {speed:.2f} to {self.MAX_SPEED_MPS}",
                params={"max_speed_mps": self.MAX_SPEED_MPS},
                rule_id="SPEED-001",
            )

        # 5. Angular speed limit
        if abs(wz) > self.MAX_ANGULAR_RPS:
            wz = self.MAX_ANGULAR_RPS * (1 if wz > 0 else -1)
            self._stats["limited"] += 1

        # 6. Obstacle proximity
        min_obs_dist = float("inf")
        for e in entities:
            if not (e.get("is_human") or e.get("class_name") == "person"):
                d = e.get("distance_m", 999)
                if d < min_obs_dist:
                    min_obs_dist = d

        if min_obs_dist < 0.5 and speed > 0.1:
            self._stats["denied"] += 1
            self._emit("obstacle_stop",
                        f"Препятствие в {min_obs_dist:.1f} м — "
                        "аварийная остановка.")
            return 0, 0, 0, GateResult(
                decision="DENY", reason="Obstacle too close",
                rule_id="OBS-001",
            )

        # All clear
        self._stats["allowed"] += 1
        if speed > 0.1:
            self._emit("nominal",
                        f"Движение разрешено. Скорость {speed:.2f} м/с, "
                        f"батарея {battery:.0f}%, наклон {tilt:.1f}°.", 5.0)
        return vx, vy, wz, GateResult(decision="ALLOW")

    def get_reasoning(self, clear: bool = True) -> list[dict]:
        """Return and optionally clear reasoning messages."""
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

    # ── internal ──────────────────────────────────────────────────────

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
