"""ActionGate — deterministic regulatory rule gate for L2a (GateFast).

Wraps RuleEngine for use inside constraint_solver (L2a).
Loaded once at startup from YAML files — no network, no DB, no retry.
Identical input → identical output (deterministic).

Per STEERING.md §3:
  L2a GateFast: hard constraints only, fail-closed, deterministic.
  L3 Knowledge: rule_engine + regulatory_index (enrichment, audit).

ActionGate is the bridge: it pre-loads L3 rules at startup and exposes
a single deterministic check_action() call suitable for L2a.

Usage (from constraint_solver):

    from ontology.action_gate import ActionGate, GateResult

    _gate = ActionGate()            # module-level singleton, loaded once

    def solve(candidate, policy, emergency_flag, ...):
        ctx = build_gate_context(candidate, policy, robot_state)
        gate_result = _gate.check_action("navigate", ctx)
        if not gate_result.allowed:
            return REJECT(gate_result.reason_code, gate_result.explain)
        ...
        # continue with waypoint-level constraint checks
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ontology.rule_engine import RuleEngine, EvalResult, build_context as _build_ctx
from ontology import regulatory_index

logger = logging.getLogger(__name__)

# Default rules directory (relative to this file)
_DEFAULT_RULES_DIR = Path(__file__).parent / "rules"


# =============================================================================
# REASON CODES  (per STEERING.md §10 — each reject has a ReasonCode)
# =============================================================================

class ReasonCode:
    # Emergency layer
    EMERGENCY_ESTOP          = "EMERGENCY_ESTOP"
    EMERGENCY_SENSOR_FAIL    = "EMERGENCY_SENSOR_FAIL"
    EMERGENCY_BATTERY_CRIT   = "EMERGENCY_BATTERY_CRIT"
    EMERGENCY_HW_MODE        = "EMERGENCY_HW_MODE"
    EMERGENCY_SAFETY_FAULT   = "EMERGENCY_SAFETY_FAULT"
    EMERGENCY_CALIBRATION    = "EMERGENCY_CALIBRATION"
    EMERGENCY_HUMAN_PERCEPT  = "EMERGENCY_HUMAN_PERCEPT"
    EMERGENCY_DYN_DISTANCE   = "EMERGENCY_DYN_DISTANCE"
    EMERGENCY_NAV_SERVER     = "EMERGENCY_NAV_SERVER"
    EMERGENCY_TORQUE_CONTACT = "EMERGENCY_TORQUE_CONTACT"
    EMERGENCY_SYSTEM_ABNORM  = "EMERGENCY_SYSTEM_ABNORM"
    EMERGENCY_SINGLE_FAULT   = "EMERGENCY_SINGLE_FAULT"
    EMERGENCY_GENERIC        = "EMERGENCY_GENERIC"
    # Hard constraint layer
    HARD_SPEED_LIMIT         = "HARD_SPEED_LIMIT"
    HARD_CHARGING_LOCK       = "HARD_CHARGING_LOCK"
    HARD_RESTRICTED_ZONE     = "HARD_RESTRICTED_ZONE"
    HARD_ISO_GUARD_ZONE      = "HARD_ISO_GUARD_ZONE"
    HARD_ISO_POWER_RESTORE   = "HARD_ISO_POWER_RESTORE"
    HARD_ISO_MIN_SEPARATION  = "HARD_ISO_MIN_SEPARATION"
    HARD_ISO_LOCAL_CTRL      = "HARD_ISO_LOCAL_CTRL"
    HARD_HRI_SHARED_OBJ      = "HARD_HRI_SHARED_OBJ"
    HARD_HW_TEMPERATURE      = "HARD_HW_TEMPERATURE"
    HARD_HW_ADVISORY_SPEED   = "HARD_HW_ADVISORY_SPEED"
    HARD_LOAD_CAPACITY       = "HARD_LOAD_CAPACITY"
    HARD_AISLE_CLEARANCE     = "HARD_AISLE_CLEARANCE"
    HARD_NAV_DEADLOCK        = "HARD_NAV_DEADLOCK"
    HARD_COMM_LOST           = "HARD_COMM_LOST"
    HARD_GENERIC             = "HARD_CONSTRAINT_VIOLATED"


# Map rule_id prefix → ReasonCode
_RULE_TO_REASON: Dict[str, str] = {
    "EMRG-ESTOP":                  ReasonCode.EMERGENCY_ESTOP,
    "ISO3691-4-ESTOP-001":         ReasonCode.EMERGENCY_ESTOP,
    "ISO3691-4-SAFETY-FAULT-001":  ReasonCode.EMERGENCY_SAFETY_FAULT,
    "ISO3691-4-SPEED-OPERATING-001": ReasonCode.HARD_SPEED_LIMIT,
    "ISO3691-4-OVERSPEED-001":     ReasonCode.HARD_SPEED_LIMIT,
    "ISO3691-4-STABILITY-TILT-001": ReasonCode.HARD_GENERIC,
    "ISO3691-4-ZONE-RESTRICTED-001": ReasonCode.HARD_RESTRICTED_ZONE,
    "ISO3691-4-CHARGE-SAFETY-001": ReasonCode.HARD_CHARGING_LOCK,
    "EMRG-SENSOR-FAILURE":         ReasonCode.EMERGENCY_SENSOR_FAIL,
    "EMRG-CRITICAL-BATTERY":       ReasonCode.EMERGENCY_BATTERY_CRIT,
    "HW-LOW-BATTERY-AUTO-CROUCH":  ReasonCode.EMERGENCY_BATTERY_CRIT,
    "HW-DAMPING-MODE-OVERRIDE":    ReasonCode.EMERGENCY_HW_MODE,
    "HW-CALIBRATION-BLOCK":        ReasonCode.EMERGENCY_CALIBRATION,
    "HW-SYSTEM-ABNORMALITY":       ReasonCode.EMERGENCY_SYSTEM_ABNORM,
    "ISO-SAFETY-CTRL-FAILURE":     ReasonCode.EMERGENCY_SAFETY_FAULT,
    "ISO-SINGLE-FAULT-SAFE-STATE": ReasonCode.EMERGENCY_SINGLE_FAULT,
    "ISO-CONTACT-TORQUE-STOP":     ReasonCode.EMERGENCY_TORQUE_CONTACT,
    "HRI-PERCEPTION-MISMATCH-STOP": ReasonCode.EMERGENCY_HUMAN_PERCEPT,
    "HRI-DYNAMIC-SAFETY-STOP":     ReasonCode.EMERGENCY_DYN_DISTANCE,
    "NAV-CTRL-SERVER-DOWN":        ReasonCode.EMERGENCY_NAV_SERVER,
    "OSHA-SPEED-LIMIT":            ReasonCode.HARD_SPEED_LIMIT,
    "HW-MAX-SPEED-ADVISORY":       ReasonCode.HARD_HW_ADVISORY_SPEED,
    "OSHA-CHARGING-LOCK":          ReasonCode.HARD_CHARGING_LOCK,
    "OSHA-RESTRICTED-ZONE":        ReasonCode.HARD_RESTRICTED_ZONE,
    "HRI-NO-APPROACH-HUMAN-ZONE":  ReasonCode.HARD_RESTRICTED_ZONE,
    "ISO-GUARD-ZONE-STOP":         ReasonCode.HARD_ISO_GUARD_ZONE,
    "ISO-POWER-RESTORE-STATIC":    ReasonCode.HARD_ISO_POWER_RESTORE,
    "ISO-MIN-SEPARATION":          ReasonCode.HARD_ISO_MIN_SEPARATION,
    "ISO-HRI-RESTART-INHIBIT":     ReasonCode.HARD_ISO_MIN_SEPARATION,
    "ISO-LOCAL-CONTROL-INHIBIT":   ReasonCode.HARD_ISO_LOCAL_CTRL,
    "ISO-SAFE-ZONE-BOUNDS":        ReasonCode.HARD_ISO_GUARD_ZONE,
    "HRI-SHARED-OBJECT-WAIT":      ReasonCode.HARD_HRI_SHARED_OBJ,
    "HW-OPERATING-TEMPERATURE":    ReasonCode.HARD_HW_TEMPERATURE,
    "OSHA-LOAD-CAPACITY":          ReasonCode.HARD_LOAD_CAPACITY,
    "OSHA-AISLE-CLEARANCE":        ReasonCode.HARD_AISLE_CLEARANCE,
    "NAV-LOGIC-DEADLOCK":          ReasonCode.HARD_NAV_DEADLOCK,
    "NAV-COMM-LOST-STOP":          ReasonCode.HARD_COMM_LOST,
}


# =============================================================================
# GATE RESULT
# =============================================================================

@dataclass
class GateResult:
    """Result of an ActionGate check.  Per STEERING.md: safety is binary."""
    allowed: bool               # True = ALLOW; False = REJECT
    reason_code: str            # ReasonCode constant; "" when allowed
    forced_action: Optional[str]  # set by EMERGENCY rules; None otherwise
    forced_params: Dict[str, Any]
    violations: List[Dict[str, Any]]  # [{rule_id, layer, description, severity, audit}]
    explain: str                # human-readable trace
    trace: Dict[str, Any]       # latency_ms, rules_total, rules_matched
    profession_source: str = ""  # profession source tag if any profession rules matched

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "forced_action": self.forced_action,
            "violations": self.violations,
            "explain": self.explain,
            "trace": self.trace,
        }

    @property
    def is_emergency(self) -> bool:
        return self.forced_action is not None

    @property
    def decision(self) -> str:
        """Bridge-compatible: ALLOW | REJECT | EMERGENCY_STOP."""
        if self.allowed:
            return "ALLOW"
        if self.is_emergency or (self.reason_code and "EMERGENCY" in self.reason_code):
            return "EMERGENCY_STOP"
        return "REJECT"

    @property
    def reason(self) -> str:
        """Bridge-compatible: human-readable reason string."""
        return self.explain or self.reason_code or ""


# =============================================================================
# ACTION GATE
# =============================================================================

class ActionGate:
    """
    Deterministic regulatory gate.  Loaded once at startup from YAML files.

    Thread-safe after init (read-only after load_rules).
    """

    def __init__(
        self,
        rules_dir: Optional[str] = None,
        extra_rules_file: Optional[str] = None,
    ) -> None:
        self._engine = RuleEngine()
        self._loaded = False

        # Allow env override for custom rule sets
        dir_path = rules_dir or os.environ.get("TRUSTLAYER_RULES_DIR")
        if dir_path:
            n = self._engine.load_directory(dir_path)
        else:
            n = self._engine.load_builtin_rules()

        if extra_rules_file:
            n += self._engine.load_file(extra_rules_file)

        self._loaded = n > 0
        logger.info("ActionGate: loaded %d regulatory rules", n)

    # ── Public API ────────────────────────────────────────────────────────

    def check_action(
        self,
        action_type: str,
        context: Dict[str, Any],
        robot_type: Optional[str] = None,
        jurisdiction: Optional[str] = None,
    ) -> GateResult:
        """
        Check whether action_type is allowed in the given context.

        Parameters
        ----------
        action_type : e.g. "navigate", "pick", "place", "restart"
        context     : built with build_gate_context() or ontology.rule_engine.build_context()

        Returns
        -------
        GateResult with allowed=True/False and reason_code per ReasonCode constants.
        """
        if not self._loaded:
            # No rules loaded → fail-closed (per STEERING.md §5)
            logger.warning("ActionGate: no rules loaded — rejecting (fail-closed)")
            return GateResult(
                allowed=False,
                reason_code="NO_RULES_LOADED",
                forced_action="stop",
                forced_params={"reason": "ActionGate: no regulatory rules loaded — fail-closed"},
                violations=[],
                explain="REJECT: ActionGate has no rules loaded (fail-closed per policy)",
                trace={"rules_total": 0},
            )

        # Affordance pre-check (L2a fast reject before full rule eval)
        semantic_type = context.get("semantic_type", "")
        if semantic_type and action_type not in ("idle", "stop", "e_stop"):
            try:
                from world_knowledge.affordance_graph import (
                    get_affordance_graph,
                )
                ag = get_affordance_graph()
                ok, reason = ag.can_perform(
                    action_type, semantic_type, context
                )
                if not ok:
                    return GateResult(
                        allowed=False,
                        reason_code=ReasonCode.HARD_GENERIC,
                        forced_action="stop",
                        forced_params={"reason": reason},
                        violations=[],
                        explain=(
                            f"REJECT (affordance): {reason}"
                        ),
                        trace={
                            "source": "affordance_graph",
                            "action_type": action_type,
                            "semantic_type": semantic_type,
                        },
                    )
            except Exception:
                pass  # graceful: world_knowledge not loaded

        # Enrich context with optional robot_type / jurisdiction
        enriched_ctx = dict(context)
        if robot_type is not None:
            enriched_ctx["robot_type"] = robot_type
        if jurisdiction is not None:
            enriched_ctx["jurisdiction"] = jurisdiction

        eval_result: EvalResult = self._engine.evaluate(
            enriched_ctx, action_type
        )

        allowed = eval_result.is_valid and eval_result.forced_action is None
        reason_code = ""

        if not allowed:
            # Determine primary reason code
            blocking = [v for v in eval_result.violations
                        if v.layer in ("emergency", "hard")]
            if blocking:
                first = blocking[0]
                reason_code = _RULE_TO_REASON.get(first.rule_id, "")
                if not reason_code:
                    reason_code = (
                        ReasonCode.EMERGENCY_ESTOP
                        if first.layer == "emergency"
                        else ReasonCode.HARD_GENERIC
                    )

        # Enrich violations with regulatory audit info where available
        reg_index = regulatory_index._default_index  # module-level singleton
        violations_payload: List[Dict[str, Any]] = []
        profession_source = ""
        by_source = getattr(self._engine.loader, '_by_source', {})
        # Build reverse map: rule_id → source
        rule_source_map: Dict[str, str] = {}
        for src, rules in by_source.items():
            for r in rules:
                rule_source_map[r.id] = src

        for v in eval_result.violations:
            ref = reg_index.lookup(v.rule_id)
            audit = None
            if ref is not None:
                audit = {
                    "standard": ref.standard,
                    "document": ref.document,
                    "section": ref.section,
                    "obligation": ref.obligation,
                    "citation": ref.citation,
                }
            v_source = rule_source_map.get(v.rule_id, "platform")
            if v_source.startswith("profession:") and not profession_source:
                profession_source = v_source
            violations_payload.append(
                {
                    "rule_id": v.rule_id,
                    "layer": v.layer,
                    "description": v.description,
                    "severity": v.severity,
                    "audit": audit,
                    "source": v_source,
                }
            )

        return GateResult(
            allowed=allowed,
            reason_code=reason_code,
            forced_action=eval_result.forced_action,
            forced_params=eval_result.forced_params,
            violations=violations_payload,
            explain=eval_result.explain,
            trace=eval_result.trace,
            profession_source=profession_source,
        )

    def check_entity_safety(
        self,
        entity: Any,
        robot_action: str = "navigate",
    ) -> GateResult:
        """Check safety constraints based on entity semantic_type and tags.

        DOES NOT replace the existing 47 rules — only ADDS constraints.
        L3 advisory: can LIMIT_SPEED but cannot unlock what L2a blocked.

        Safety is always worst-case:
          - status="hypothesis" + semantic_type="human" → treat as real human
          - anomaly_type="unexpected_new" + no safety_tags → LIMIT_SPEED
          - "trip_hazard" in safety_tags + navigate action → REJECT

        Returns GateResult with allowed=True/False and reason_code.
        """
        try:
            sem_type = getattr(entity, "semantic_type", "") or ""
            status = getattr(entity, "status", "confirmed")
            anomaly = getattr(entity, "anomaly_type", "")
            tags: list = list(getattr(entity, "safety_tags", []) or [])
            class_name = getattr(entity, "class_name", "") or ""
            min_cl = float(getattr(entity, "min_clearance_m", 0.0) or 0.0)

            # Worst-case: hypothesis human → treat as real human
            if status == "hypothesis" and (
                sem_type == "human" or class_name in ("human", "person", "pedestrian")
            ):
                return GateResult(
                    allowed=False,
                    reason_code=ReasonCode.EMERGENCY_HUMAN_PERCEPT,
                    forced_action="stop",
                    forced_params={"reason": "hypothesis_human_worst_case"},
                    violations=[{
                        "rule_id": "SEMANTIC-HYPOTHESIS-HUMAN",
                        "layer": "hard",
                        "description": (
                            "Hypothesis human — worst-case assumption"
                        ),
                        "severity": "high",
                    }],
                    explain=(
                        "REJECT: entity status=hypothesis, "
                        "semantic_type=human — "
                        "worst-case safety invariant"
                    ),
                    trace={"rules_total": 0, "semantic_check": True},
                )

            # Trip hazard in path
            if "trip_hazard" in tags and robot_action in (
                "navigate", "move", "navigate_through"
            ):
                return GateResult(
                    allowed=False,
                    reason_code=ReasonCode.HARD_AISLE_CLEARANCE,
                    forced_action=None,
                    forced_params={},
                    violations=[{
                        "rule_id": "SEMANTIC-TRIP-HAZARD",
                        "layer": "hard",
                        "description": f"Trip hazard in path: {sem_type or class_name}",
                        "severity": "high",
                    }],
                    explain=(
                        f"REJECT: {entity.entity_id!r} has "
                        f"safety_tag='trip_hazard' — "
                        f"'{robot_action}' blocked"
                    ),
                    trace={"rules_total": 0, "semantic_check": True},
                )

            # Unknown object in path → LIMIT_SPEED (not full reject)
            if anomaly == "unexpected_new" and not tags:
                return GateResult(
                    allowed=True,
                    reason_code="LIMIT_SPEED",
                    forced_action="limit_speed",
                    forced_params={"max_speed_mps": 0.3,
                                   "reason": "unknown_object_in_path"},
                    violations=[{
                        "rule_id": "SEMANTIC-UNKNOWN-OBJECT",
                        "layer": "policy",
                        "description": "Unexpected unknown object — speed limited",
                        "severity": "low",
                    }],
                    explain=(
                        f"ALLOW(speed limit): {entity.entity_id!r} "
                        f"unexpected + unclassified — max 0.3 m/s"
                    ),
                    trace={"rules_total": 0, "semantic_check": True},
                )

            # Hazard tag → advisory speed limit
            if "hazard" in tags:
                cap = max(0.3, min_cl * 2)  # speed cap proportional to clearance
                return GateResult(
                    allowed=True,
                    reason_code="LIMIT_SPEED",
                    forced_action="limit_speed",
                    forced_params={
                        "max_speed_mps": cap,
                        "reason": "hazard_proximity",
                    },
                    violations=[{
                        "rule_id": "SEMANTIC-HAZARD-TAG",
                        "layer": "policy",
                        "description": (
                            f"Hazard nearby — speed limited to {cap} m/s"
                        ),
                        "severity": "medium",
                    }],
                    explain=(
                        f"ALLOW with speed limit {cap} m/s: "
                        f"entity {entity.entity_id!r} has safety_tag='hazard'"
                    ),
                    trace={"rules_total": 0, "semantic_check": True},
                )

        except Exception as exc:
            logger.warning(
                "check_entity_safety error for %s: %s",
                getattr(entity, "entity_id", "?"),
                exc,
            )
            # Fail-safe: reject on error to stay conservative
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.HARD_GENERIC,
                forced_action=None,
                forced_params={},
                violations=[{
                    "rule_id": "SEMANTIC-CHECK-ERROR",
                    "layer": "hard",
                    "description": f"check_entity_safety raised: {exc}",
                    "severity": "high",
                }],
                explain=(
                    f"REJECT: check_entity_safety error (fail-safe): {exc}"
                ),
                trace={
                    "rules_total": 0,
                    "semantic_check": True,
                    "error": str(exc),
                },
            )

        # Default: allowed
        return GateResult(
            allowed=True,
            reason_code="",
            forced_action=None,
            forced_params={},
            violations=[],
            explain="ALLOW: no semantic safety constraints triggered",
            trace={"rules_total": 0, "semantic_check": True},
        )

    def check_robot_state(self, robot: Dict[str, Any]) -> GateResult:
        """
        Check robot state alone (no specific action) — detects emergency conditions.
        Useful as a pre-flight safety pulse every N ms.
        """
        ctx = _build_ctx(robot=robot, action={"type": "idle", "speed_mps": 0.0})
        return self.check_action("idle", ctx)

    # ── Profession pack support ─────────────────────────────────────

    def load_profession_rules(
        self, yaml_path: str, source: str = "profession",
    ) -> int:
        """Load additional POLICY/PREFERENCE rules from a profession pack.

        Returns count of rules loaded.  EMERGENCY/HARD rules are rejected.
        """
        n = self._engine.load_additional_rules(yaml_path, source)
        if n > 0:
            self._loaded = True
        logger.info(
            "ActionGate: loaded %d profession rules (source=%s)", n, source,
        )
        return n

    def unload_profession_rules(self, source: str) -> int:
        """Remove all rules tagged with *source*."""
        n = self._engine.unload_rules_by_source(source)
        logger.info(
            "ActionGate: unloaded %d rules (source=%s)", n, source,
        )
        return n

    @property
    def active_profession_source(self) -> Optional[str]:
        """Return first profession source tag, or None."""
        by_src = getattr(self._engine.loader, '_by_source', {})
        for src in by_src:
            if src.startswith("profession:"):
                return src
        return None

    @property
    def rule_count(self) -> int:
        return self._engine.stats()["total"]

    def stats(self) -> Dict[str, Any]:
        s = self._engine.stats()
        s["active_profession"] = self.active_profession_source
        return s


# =============================================================================
# CONTEXT BUILDER  (converts constraint_solver inputs to gate context)
# =============================================================================

def build_gate_context(
    candidate: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, Any]] = None,
    robot_state: Optional[Dict[str, Any]] = None,
    target_zone: Optional[Dict[str, Any]] = None,
    current_zone: Optional[Dict[str, Any]] = None,
    *,
    action_type: Optional[str] = None,
    world_entities: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Convert constraint_solver inputs into an ActionGate context dict.

    Bridge-style (Isaac Sim): action_type=, robot_state=, world_entities=
    Legacy: candidate=, policy=, robot_state= (plan constraint_solver)

    candidate   : PlanCandidate dict (from planner_rsm / constraint_solver)
    policy      : PolicyBundle dict
    robot_state : current robot state (from perception / world_model)
                  expected keys: battery_level, battery_soc, is_e_stopped, sensor_ok,
                  tracking_status, is_moving, is_charging, mode, comm_ok, tilt_deg
    target_zone : zone metadata dict (zone_type, access_level, humans_present, ...)
    current_zone: current zone metadata dict
    """
    # Bridge-style: action_type + robot_state + world_entities (Isaac Sim)
    if action_type is not None and robot_state is not None:
        return _build_gate_context_bridge(action_type, robot_state, world_entities or [])

    if candidate is None or policy is None or robot_state is None:
        raise TypeError(
            "build_gate_context requires (candidate, policy, robot_state) or "
            "(action_type=..., robot_state=..., world_entities=...)"
        )

    waypoints = candidate.get("waypoints", [])
    first_wp = waypoints[0] if waypoints else {}
    last_wp = waypoints[-1] if waypoints else {}

    # Infer action speed from fastest waypoint
    speeds = [wp.get("max_speed_mps", 0.0) for wp in waypoints if "max_speed_mps" in wp]
    action_speed = max(speeds) if speeds else candidate.get("max_speed_mps", 0.0)

    # Merge zone info from policy + explicit override
    _target_zone = target_zone or {
        "zone_type": last_wp.get("zone_type", "unknown"),
        "access_level": last_wp.get("access_level", 0),
        "humans_present": last_wp.get("humans_present", False),
        "zone_id": last_wp.get("zone_id", ""),
        "guarded": last_wp.get("guarded", False),
        "elevated": last_wp.get("elevated", False),
    }
    _current_zone = current_zone or {
        "zone_type": first_wp.get("zone_type", "unknown"),
        "access_level": first_wp.get("access_level", 0),
        "humans_present": first_wp.get("humans_present", False),
        "zone_id": first_wp.get("zone_id", ""),
        "guarded": first_wp.get("guarded", False),
        "elevated": first_wp.get("elevated", False),
    }

    # Build action dict
    action = {
        "type": candidate.get("action_type", "navigate"),
        "speed_mps": action_speed,
        "direction": candidate.get("direction", "forward"),
        "load_exceeds_capacity": candidate.get("load_exceeds_capacity", False),
        "load_elevated": candidate.get("load_elevated", False),
        "path_clearance_insufficient": candidate.get("path_clearance_insufficient", False),
        "target_object_human_accessing": candidate.get("target_object_human_accessing", False),
        "out_of_safe_zone": candidate.get("out_of_safe_zone", False),
        "route_edge_repeatedly_blocked": candidate.get("route_edge_repeatedly_blocked", False),
        "incline_deg": candidate.get("incline_deg", 0.0),
        "step_height_cm": candidate.get("step_height_cm", 0.0),
    }

    return _build_ctx(
        robot=robot_state,
        action=action,
        zone=_current_zone,
        target_zone=_target_zone,
    )


def _build_gate_context_bridge(
    action_type: str,
    robot_state: Dict[str, Any],
    world_entities: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build gate context from bridge-style args (Isaac Sim h1_bridge_trustlayer)."""
    humans_present = any(
        e.get("is_human") and float(e.get("distance_m", float("inf"))) < 2.5
        for e in world_entities
    )
    speed = float(robot_state.get("speed_mps", 0.0))
    battery_soc = float(robot_state.get("battery_soc", 100))
    robot = {
        "battery_level": battery_soc,  # rules may expect 0–100 SOC
        "battery_soc": battery_soc,
        "mode": robot_state.get("mode", "ADVISORY"),
        "tilt_deg": robot_state.get("tilt_deg", 0.0),
        "is_e_stopped": False,
        "sensor_ok": True,
        "tracking_status": "ok",
        "is_moving": speed > 0,
        "is_charging": False,
        "comm_ok": True,
    }
    action = {
        "type": (action_type or "navigate").lower(),
        "speed_mps": speed,
    }
    zone = {
        "zone_type": "unknown",
        "access_level": 0,
        "humans_present": humans_present,
        "guarded": False,
        "elevated": False,
    }
    return _build_ctx(robot=robot, action=action, zone=zone, target_zone=zone)


# =============================================================================
# MODULE-LEVEL check_action  (bridge expects function, not method)
# =============================================================================

def check_action(context: Dict[str, Any]) -> GateResult:
    """
    Check action against regulatory rules. Bridge-compatible: returns result
    with .decision ("ALLOW"|"REJECT"|"EMERGENCY_STOP") and .reason.

    context : from build_gate_context() — has robot, action, zone
    """
    g = get_gate()
    action_type = (
        context.get("action", {}).get("type")
        or context.get("action_type", "navigate")
    )
    if isinstance(action_type, str):
        action_type = action_type.lower()
    res = g.check_action(str(action_type), context)
    return res


# =============================================================================
# MODULE-LEVEL SINGLETON  (loaded once at import time when rules dir exists)
# =============================================================================

_gate: Optional[ActionGate] = None


def get_gate() -> ActionGate:
    """Return the module-level ActionGate singleton (lazy-init)."""
    global _gate
    if _gate is None:
        _gate = ActionGate()
    return _gate


def reset_gate() -> None:
    """Reset the singleton (for testing)."""
    global _gate
    _gate = None
