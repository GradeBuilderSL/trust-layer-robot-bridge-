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

import collections
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ontology.rule_engine import RuleEngine, EvalResult, build_context as _build_ctx
from ontology import regulatory_index
from ontology.robot_command import RobotCommand
from ontology.capability import (
    CapabilityPolicy, CapabilityProfile, CapabilityPolicyManager,
    CapabilityType, COMMAND_TO_CAPABILITY
)

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
    # Memory operations
    MEMORY_SCAN_FAILED       = "MEMORY_SCAN_FAILED"
    MEMORY_STORAGE_FAILED    = "MEMORY_STORAGE_FAILED"
    MEMORY_TRUST_THRESHOLD   = "MEMORY_TRUST_THRESHOLD"
    # Knowledge validation
    KNOWLEDGE_VALIDATION_ERROR = "KNOWLEDGE_VALIDATION_ERROR"
    KNOWLEDGE_FILE_CORRUPTED = "KNOWLEDGE_FILE_CORRUPTED"
    KNOWLEDGE_LANGUAGE_VIOLATION = "KNOWLEDGE_LANGUAGE_VIOLATION"
    KNOWLEDGE_RANGE_VIOLATION = "KNOWLEDGE_RANGE_VIOLATION"
    KNOWLEDGE_INTEGRITY_ERROR = "KNOWLEDGE_INTEGRITY_ERROR"


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
    "OSHA-LOAD_CAPACITY":          ReasonCode.HARD_LOAD_CAPACITY,
    "OSHA-AISLE-CLEARANCE":        ReasonCode.HARD_AISLE_CLEARANCE,
    "NAV-LOGIC-DEADLOCK":          ReasonCode.HARD_NAV_DEADLOCK,
    "NAV-COMM-LOST-STOP":          ReasonCode.HARD_COMM_LOST,
    "MEMORY-SCAN-FAILED":          ReasonCode.MEMORY_SCAN_FAILED,
    "MEMORY-STORAGE-FAILED":       ReasonCode.MEMORY_STORAGE_FAILED,
    "MEMORY-TRUST-THRESHOLD":      ReasonCode.MEMORY_TRUST_THRESHOLD,
    # Knowledge validation rules
    "KNOWLEDGE-VALIDATION-ERROR":  ReasonCode.KNOWLEDGE_VALIDATION_ERROR,
    "KNOWLEDGE-FILE-CORRUPTED":    ReasonCode.KNOWLEDGE_FILE_CORRUPTED,
    "KNOWLEDGE-LANGUAGE-VIOLATION": ReasonCode.KNOWLEDGE_LANGUAGE_VIOLATION,
    "KNOWLEDGE-RANGE-VIOLATION":   ReasonCode.KNOWLEDGE_RANGE_VIOLATION,
    "KNOWLEDGE-INTEGRITY-ERROR":   ReasonCode.KNOWLEDGE_INTEGRITY_ERROR,
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
    used_profile: Optional[str] = None  # профиль, использованный для проверки (основной или fallback)
    fallback_info: Optional[Dict[str, Any]] = None  # информация о fallback, если использовался

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "forced_action": self.forced_action,
            "violations": self.violations,
            "explain": self.explain,
            "trace": self.trace,
            "used_profile": self.used_profile,
            "fallback_info": self.fallback_info,
        }

    @property
    def has_emergency(self) -> bool:
        return any(v.get("layer") == "EMERGENCY" for v in self.violations)


# =============================================================================
# ACTION GATE
# =============================================================================

class ActionGate:
    """Deterministic gate for L2a constraint_solver.  Loads rules once at startup."""

    def __init__(
        self,
        rules_dir: Optional[Path] = None,
        extra_rule_files: Optional[List[Path]] = None,
        load_builtin: bool = True,
        action_id: Optional[str] = None,
        action_type: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[dict] = None,
    ) -> None:
        # Step descriptor fields (used when ActionGate is an action node in a sequence)
        self.action_id = action_id
        self.action_type = action_type
        self._step_description = description
        self.parameters = parameters or {}

        # Rule engine and state
        self.engine = RuleEngine()
        self.rule_engine = self.engine  # alias used by stage code paths
        self.extra_rule_files = extra_rule_files or []
        self._profession_sources: Dict[str, str] = {}  # rule_id → profession source tag
        self._profession_source = ""
        self._rules_loaded = False
        self._loaded = False  # alias for _rules_loaded
        self._custom_dir_failed = False  # True when explicit rules_dir doesn't exist

        # L3 rule contexts (regulatory_index) are loaded lazily when needed
        self._regulatory_context: Optional[Dict[str, Any]] = None

        if rules_dir is not None:
            self._rules_dir = Path(rules_dir)
            self.rules_dir = self._rules_dir
            if not self._rules_dir.exists():
                logger.warning("Rules directory %s does not exist — fail-closed mode", self._rules_dir)
                self._custom_dir_failed = True
        else:
            self._rules_dir = _DEFAULT_RULES_DIR
            self.rules_dir = self._rules_dir

        if load_builtin and not self._custom_dir_failed:
            self.load_rules()

    def to_dict(self) -> dict:
        """Serialize to dict (step descriptor form)."""
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "description": self._step_description,
            "parameters": self.parameters,
            "type": "ActionGate",
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActionGate":
        """Deserialize from dict (step descriptor form)."""
        return cls(
            load_builtin=False,
            action_id=data.get("action_id"),
            action_type=data.get("action_type"),
            description=data.get("description"),
            parameters=data.get("parameters", {}),
        )

    def execute_sync(self, context: Optional[dict] = None) -> dict:
        """Synchronous execution of this action step."""
        return {"success": True, "action_id": self.action_id, "context": context or {}}

    async def execute(self, context: Optional[dict] = None) -> dict:
        """Async execution of this action step."""
        return self.execute_sync(context)

    def load_rules(self) -> None:
        """Load YAML rules from the configured directory."""
        if self._rules_loaded:
            return

        logger.info(f"ActionGate loading rules from {self._rules_dir}")
        start = time.perf_counter()

        # Load all .yaml files from the rules directory
        rule_files = list(self._rules_dir.glob("*.yaml"))
        if not rule_files:
            logger.warning(f"No .yaml rule files found in {self._rules_dir}")

        # Add extra rule files
        for ef in self.extra_rule_files:
            if ef not in rule_files:
                rule_files.append(ef)

        for rf in rule_files:
            self.engine.load_file(rf)
            # Extract profession source tags from metadata
            with open(rf, "r", encoding="utf-8") as f:
                import yaml
                try:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict) and "rules" in data:
                        for rule in data["rules"]:
                            rule_id = rule.get("id", "")
                            if rule_id and "metadata" in rule:
                                source = rule["metadata"].get("source", "")
                                if source.startswith("profession:"):
                                    self._profession_sources[rule_id] = source
                except Exception as e:
                    logger.warning(f"Could not extract profession sources from {rf}: {e}")

        logger.info(f"ActionGate loaded {len(getattr(self.engine, 'rules', []))} rules, "
                    f"{len(self._profession_sources)} profession-tagged rules, "
                    f"took {(time.perf_counter() - start) * 1000:.1f} ms")

        self._rules_loaded = True
        self._loaded = True

    # Alias for stage compatibility
    def load(self) -> None:
        """Load rules (alias for load_rules)."""
        self.load_rules()

    def check_action(
        self,
        action: str,
        context: Dict[str, Any],
        profession: Optional[str] = None,
        robot_command: Optional[RobotCommand] = None,
        capability_policy: Optional[CapabilityPolicy] = None,
        **kwargs: Any,
    ) -> GateResult:
        """Evaluate regulatory rules for the given action and context.

        Args:
            action: action name (e.g., "navigate", "lift", "charge")
            context: dictionary of context variables (robot state, environment, etc.)
            profession: optional profession name to add to context
            robot_command: optional RobotCommand object for additional checks
            capability_policy: optional CapabilityPolicy for capability checks

        Returns:
            GateResult with allowed boolean and detailed violations.
        """
        start_time = datetime.now()

        # Fail-closed: custom rules_dir was specified but doesn't exist
        if self._custom_dir_failed:
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.HARD_GENERIC,
                forced_action="stop",
                forced_params={},
                violations=[{
                    "rule_id": "NO_RULES_DIR",
                    "layer": "L2a",
                    "description": "Rules directory not found — fail-closed",
                    "severity": 1.0,
                }],
                explain="Rules directory missing — fail-closed by policy",
                trace={"latency_ms": 0, "rules_total": 0, "rules_matched": 0, "action_type": action},
            )

        try:
            # Ensure rules are loaded
            if not self._rules_loaded:
                self.load_rules()

            # context is already a build_context result — use it directly
            if profession:
                ctx = dict(context)
                ctx["profession"] = profession
            else:
                ctx = context

            # If robot_command is provided, add its fields to context
            if robot_command is not None:
                ctx = dict(ctx)
                ctx["robot_command"] = robot_command.to_dict()

            # Evaluate all rules (deterministic) — returns a single EvalResult
            eval_result: EvalResult = self.engine.evaluate(ctx, action)

            # EMERGENCY override (forced_action set) → action is NOT allowed
            # HARD violation (is_valid=False) → action is NOT allowed
            has_emergency = bool(eval_result.forced_action)
            allowed = eval_result.is_valid and not has_emergency
            forced_action = eval_result.forced_action
            forced_params = eval_result.forced_params or {}

            # Convert Violation objects to dicts, enriching with audit metadata
            try:
                from ontology import regulatory_index as _reg_idx
                _reg_index = _reg_idx._INDEX
            except Exception:
                _reg_index = {}

            violations = []
            for v in eval_result.violations:
                vd: Dict[str, Any] = {
                    "rule_id": v.rule_id,
                    "layer": v.layer,
                    "description": v.description,
                    "severity": v.severity,
                }
                ref = _reg_index.get(v.rule_id)
                if ref is not None:
                    vd["audit"] = {
                        "standard": ref.standard,
                        "document": ref.document,
                        "section": ref.section,
                        "obligation": ref.obligation,
                        "jurisdiction": ref.jurisdiction,
                    }
                violations.append(vd)

            # Check capability policy if provided (from stage)
            if capability_policy is not None and robot_command is not None:
                try:
                    capability_type = COMMAND_TO_CAPABILITY.get(action)
                    if capability_type:
                        profile = CapabilityProfile.from_robot_command(robot_command, capability_type)
                        policy_violations = capability_policy.check_violations(profile)
                        if policy_violations:
                            capability_violation = {
                                "rule_id": "CAPABILITY_POLICY_VIOLATION",
                                "layer": "HARD_CONSTRAINT",
                                "description": f"Capability policy violation: {policy_violations[0]}",
                                "severity": "high",
                                "audit": {},
                            }
                            violations.append(capability_violation)
                            allowed = False
                except Exception as e:
                    logger.warning(f"Capability check failed: {e}")

            explain = eval_result.explain or ("All constraints satisfied" if allowed else "Constraint violated")

            # Determine reason code: scan violations for known mappings
            if allowed:
                reason_code = ""
            else:
                reason_code = ""
                for v in violations:
                    rc = _RULE_TO_REASON.get(v["rule_id"])
                    if rc:
                        reason_code = rc
                        break
                if not reason_code:
                    # Try emergency_rule_id
                    if eval_result.emergency_rule_id:
                        reason_code = _RULE_TO_REASON.get(
                            eval_result.emergency_rule_id,
                            ReasonCode.EMERGENCY_GENERIC if has_emergency else ReasonCode.HARD_GENERIC,
                        )
                    elif has_emergency:
                        reason_code = ReasonCode.EMERGENCY_GENERIC
                    else:
                        reason_code = ReasonCode.HARD_GENERIC

            # Check if any profession rules matched
            profession_source = ""
            for v in violations:
                source = self._profession_sources.get(v.get("rule_id", ""), "")
                if source:
                    profession_source = source
                    break

            # Calculate latency
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000

            trace = {
                "latency_ms": round(latency_ms, 3),
                "rules_total": len(getattr(self.engine, "rules", [])),
                "rules_matched": len(violations),
                "action_type": action,
            }

            return GateResult(
                allowed=allowed,
                reason_code=reason_code,
                forced_action=forced_action,
                forced_params=forced_params,
                violations=violations,
                explain=explain,
                trace=trace,
                profession_source=profession_source,
            )

        except Exception as exc:
            logger.exception("ActionGate.check_action failed: %s", exc)
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.HARD_GENERIC,
                forced_action="stop",
                forced_params={},
                violations=[{
                    "rule_id": "GATE_ERROR",
                    "layer": "L2a",
                    "description": str(exc),
                    "severity": 1.0,
                }],
                explain=f"ActionGate error — fail-closed: {exc}",
                trace={"latency_ms": 0, "rules_total": 0, "rules_matched": 0, "action_type": action},
            )

    @property
    def rule_count(self) -> int:
        """Total number of loaded rules."""
        if not self._rules_loaded:
            return 0
        return len(getattr(self.engine, "rules", []))

    def get_rule_stats(self) -> Dict[str, Any]:
        """Return statistics about loaded rules."""
        if not self._rules_loaded:
            return {"loaded": False, "total": 0, "count": 0, "by_layer": {}}
        rules = getattr(self.engine, "rules", [])
        by_layer: Dict[str, int] = {}
        for rule in rules:
            layer = getattr(rule, "layer", "unknown")
            by_layer[layer] = by_layer.get(layer, 0) + 1
        return {
            "loaded": True,
            "total": len(rules),
            "count": len(rules),
            "by_layer": by_layer,
        }

    def reload(self) -> None:
        """Reload rules from disk (useful for development)."""
        self._rules_loaded = False
        self._loaded = False
        self.engine = RuleEngine()
        self.rule_engine = self.engine
        self._profession_sources.clear()
        self.load_rules()

    def stats(self) -> Dict[str, Any]:
        """Alias for get_rule_stats() — used by constraint_solver health endpoint."""
        return self.get_rule_stats()

    def check_robot_state(self, robot_state: Dict[str, Any]) -> "GateResult":
        """Check robot state for emergency conditions.

        Returns REJECT if robot is e-stopped, sensor failure, or critical battery.
        Returns ALLOW otherwise.
        """
        _empty_trace: Dict[str, Any] = {"latency_ms": 0, "rules_total": 0, "rules_matched": 0}
        if robot_state.get("is_e_stopped"):
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.EMERGENCY_ESTOP,
                forced_action="stop",
                forced_params={},
                violations=[{"description": "Robot is e-stopped"}],
                explain="E-stop active — all actions rejected",
                trace=_empty_trace,
            )
        if not robot_state.get("sensor_ok", True):
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.EMERGENCY_SENSOR_FAIL,
                forced_action="stop",
                forced_params={},
                violations=[{"description": "Sensor failure"}],
                explain="Sensor failure — all actions rejected",
                trace=_empty_trace,
            )
        battery = robot_state.get("battery_level", 100)
        if battery < 5:
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.EMERGENCY_BATTERY_CRIT,
                forced_action="stop",
                forced_params={},
                violations=[{"description": "Critical battery level"}],
                explain="Critical battery — actions rejected",
                trace=_empty_trace,
            )
        return GateResult(
            allowed=True,
            reason_code="",
            forced_action=None,
            forced_params={},
            violations=[],
            explain="Robot state OK",
            trace=_empty_trace,
        )

    def check_entity_safety(
        self,
        entity: Any,
        robot_action: str = "",
    ) -> "GateResult":
        """Check whether a perceived entity is safe to interact with.

        Fail-safe: any error in entity attribute access → REJECT.
        """
        _NAVIGATE_ACTIONS = {"navigate", "navigate_to", "move", "move_small", ""}
        _empty_trace: Dict[str, Any] = {"latency_ms": 0, "rules_total": 0, "rules_matched": 0}
        try:
            semantic_type = entity.semantic_type
            status = getattr(entity, "status", "confirmed")
            class_name = getattr(entity, "class_name", "")
            safety_tags = list(getattr(entity, "safety_tags", []))
            anomaly_type = getattr(entity, "anomaly_type", "")
        except Exception:
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.EMERGENCY_HUMAN_PERCEPT,
                forced_action="stop",
                forced_params={},
                violations=[{"description": "Entity attribute error — fail-safe reject"}],
                explain="Entity attribute access failed — fail-safe REJECT",
                trace=_empty_trace,
            )

        # Rule 1: hypothesis human
        is_human = semantic_type == "human" or class_name in {"person", "human"}
        if is_human and status == "hypothesis":
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.EMERGENCY_HUMAN_PERCEPT,
                forced_action="stop",
                forced_params={},
                violations=[{"description": "Hypothesis human entity detected"}],
                explain="Hypothesis human percept — EMERGENCY stop",
                trace=_empty_trace,
            )

        # Rule 2: trip hazard blocks navigate-type actions
        if "trip_hazard" in safety_tags and robot_action in _NAVIGATE_ACTIONS:
            return GateResult(
                allowed=False,
                reason_code=ReasonCode.HARD_GENERIC,
                forced_action=None,
                forced_params={},
                violations=[{"description": "Trip hazard blocks navigation"}],
                explain="Trip hazard entity — navigation blocked",
                trace=_empty_trace,
            )

        # Rule 3: unexpected_new or hazard tag → allow with speed limit
        if anomaly_type == "unexpected_new" or "hazard" in safety_tags:
            return GateResult(
                allowed=True,
                reason_code="LIMIT_SPEED",
                forced_action=None,
                forced_params={},
                violations=[],
                explain="Potentially hazardous entity — reduce speed",
                trace=_empty_trace,
            )

        # Rule 4: normal entity
        return GateResult(
            allowed=True,
            reason_code="",
            forced_action=None,
            forced_params={},
            violations=[],
            explain="Entity is safe",
            trace=_empty_trace,
        )


# Module-level singleton
_gate_singleton: Optional[ActionGate] = None

def get_action_gate() -> ActionGate:
    """Return the singleton ActionGate instance (lazy-loaded)."""
    global _gate_singleton
    if _gate_singleton is None:
        _gate_singleton = ActionGate()
        _gate_singleton.load()
    return _gate_singleton


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def check_action(
    action_type: str,
    context: Dict[str, Any],
    profession: Optional[str] = None,
) -> GateResult:
    """Convenience function using the singleton gate."""
    return get_action_gate().check_action(action_type, context, profession)


def build_gate_context(
    candidate: Any = None,
    policy: Any = None,
    robot_state: Any = None,
    world_state: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Build evaluation context dict for check_action() from structured inputs.

    Args:
        candidate: action candidate from planner (e.g., waypoint, lift height)
        policy: policy bundle (e.g., speed limits, zone restrictions)
        robot_state: current robot state (battery, position, mode, etc.)
        world_state: optional world state (obstacles, humans, dynamic objects)

    Returns:
        Context dictionary with normalized keys.
    """
    candidate = candidate or {}
    policy = policy or {}
    robot_state = robot_state or {}

    # Build action dict from candidate
    action_type = candidate.get("action_type", candidate.get("type", ""))
    waypoints = candidate.get("waypoints") or []
    speed_mps = 0.0
    zone: Dict[str, Any] = {}
    if waypoints:
        first = waypoints[0]
        speed_mps = float(first.get("max_speed_mps", 0.0))
        zone = {
            "zone_type": first.get("zone_type", ""),
            "access_level": first.get("access_level", 0),
            "humans_present": first.get("humans_present", False),
            "guarded": first.get("guarded", False),
            "elevated": first.get("elevated", False),
        }
    action: Dict[str, Any] = {
        "type": action_type,
        "speed_mps": speed_mps,
    }
    action.update({k: v for k, v in candidate.items()
                   if k not in ("action_type", "waypoints", "zone_path")})

    target_zone: Dict[str, Any] = kwargs.pop("target_zone", {}) or zone

    ctx = _build_ctx(
        robot=robot_state,
        action=action,
        zone=zone,
        target_zone=target_zone,
        **kwargs,
    )

    # Add policy fields
    if policy:
        ctx["policy"] = policy

    # Add world state
    if world_state:
        ctx["world"] = world_state

    return ctx


def reset_gate() -> None:
    """Reset the module-level singleton gate (used in tests)."""
    global _gate_singleton
    _gate_singleton = None

# Alias for backward compatibility
get_gate = get_action_gate
