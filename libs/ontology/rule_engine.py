"""RuleEngine — 4-layer safety rule evaluation for trust-layer.

Implements EMERGENCY → HARD → POLICY → PREFERENCE priority hierarchy.
Rules are loaded from YAML files in libs/ontology/rules/.

Adapted from _ontorobotic/src/robot_brain/rule_engine.py +
_ontorobotic/src/robot_brain/rules_dsl.py

Usage:
    from ontology.rule_engine import RuleEngine, build_context

    engine = RuleEngine()
    engine.load_builtin_rules()  # ISO-10218-1, OSHA, HRI, hardware, nav

    ctx = build_context(
        robot={"battery_level": 4, "is_e_stopped": False, "sensor_ok": True,
               "tracking_status": "ok", "is_moving": True, "is_charging": False},
        action={"type": "navigate", "speed_mps": 1.2},
        zone={"zone_type": "HumanZone", "humans_present": True,
              "access_level": 0, "guarded": False},
    )
    result = engine.evaluate(ctx, action_type="navigate")
    print(result.is_valid, result.violations, result.forced_action)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from dataclasses import field as _dc_field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

try:
    import yaml
    _YAML = True
except ImportError:
    _YAML = False
    logger.warning("PyYAML not installed — RuleEngine cannot load YAML files. "
                   "Install: pip install pyyaml")

try:
    import json as _json
    import jsonschema as _jsonschema
    _SCHEMA_PATH = Path(__file__).parents[2] / "schemas" / "ontology_rule.schema.json"
    if _SCHEMA_PATH.exists():
        _RULE_SCHEMA = _json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    else:
        _RULE_SCHEMA = None
except ImportError:
    _jsonschema = None  # type: ignore[assignment]
    _RULE_SCHEMA = None

# Built-in rules directory (relative to this file)
_RULES_DIR = Path(__file__).parent / "rules"


# =============================================================================
# RULE LAYERS
# =============================================================================

class RuleLayer(str, Enum):
    """Rule priority layers (highest priority first)."""
    EMERGENCY  = "emergency"   # ALWAYS: force actions (e-stop, fire, crit battery)
    HARD       = "hard"        # NEVER: block forbidden actions (legal/safety limits)
    POLICY     = "policy"      # SHOULD: operational SOP — add penalty score
    PREFERENCE = "preference"  # PREFER: optimisation hints — small penalty

    @property
    def priority(self) -> int:
        return {
            RuleLayer.EMERGENCY:  1000,
            RuleLayer.HARD:        500,
            RuleLayer.POLICY:      100,
            RuleLayer.PREFERENCE:   10,
        }[self]


# =============================================================================
# CONDITION EVALUATION
# =============================================================================

class CondOp(str, Enum):
    EQ     = "eq"
    NE     = "ne"
    GT     = "gt"
    LT     = "lt"
    GE     = "ge"
    LE     = "le"
    IN     = "in"
    NOT_IN = "not_in"
    EXISTS     = "exists"
    NOT_EXISTS = "not_exists"
    AND = "and"
    OR  = "or"


@dataclass
class Condition:
    """A single leaf or compound (AND/OR) condition."""
    field: str = ""
    op: CondOp = CondOp.EQ
    value: Any = None
    sub: List["Condition"] = _dc_field(default_factory=list)

    def evaluate(self, ctx: Dict[str, Any]) -> bool:
        if self.op == CondOp.AND:
            return all(c.evaluate(ctx) for c in self.sub)
        if self.op == CondOp.OR:
            return any(c.evaluate(ctx) for c in self.sub)

        v = _get_nested(ctx, self.field)

        if self.op == CondOp.EXISTS:
            return v is not None
        if self.op == CondOp.NOT_EXISTS:
            return v is None
        if v is None:
            return False

        # Resolve cross-field references: if value is a string that looks
        # like a dotted path (e.g. "robot.max_force_for_body_region"),
        # try to resolve it from ctx for comparison operators.
        cmp_value = self.value
        if (isinstance(cmp_value, str) and "." in cmp_value
                and self.op in (CondOp.GT, CondOp.LT, CondOp.GE, CondOp.LE)):
            resolved = _get_nested(ctx, cmp_value)
            if resolved is not None:
                cmp_value = resolved
            else:
                return False  # referenced field missing → condition not met

        try:
            if self.op == CondOp.EQ:
                return v == cmp_value
            if self.op == CondOp.NE:
                return v != cmp_value
            if self.op == CondOp.GT:
                return v > cmp_value
            if self.op == CondOp.LT:
                return v < cmp_value
            if self.op == CondOp.GE:
                return v >= cmp_value
            if self.op == CondOp.LE:
                return v <= cmp_value
            if self.op == CondOp.IN:
                return v in cmp_value
            if self.op == CondOp.NOT_IN:
                return v not in cmp_value
        except TypeError:
            return False
        return False


def _get_nested(ctx: Dict[str, Any], path: str) -> Any:
    """Resolve dot-notation path: 'robot.battery_level' → ctx['robot']['battery_level']."""
    parts = path.split(".")
    v: Any = ctx
    for p in parts:
        if isinstance(v, dict):
            v = v.get(p)
        elif hasattr(v, p):
            v = getattr(v, p)
        else:
            return None
        if v is None:
            return None
    return v


def _parse_condition(data: Dict[str, Any]) -> Condition:
    """Parse a condition dict from YAML."""
    if "and" in data:
        return Condition(op=CondOp.AND, sub=[_parse_condition(c) for c in data["and"]])
    if "or" in data:
        return Condition(op=CondOp.OR, sub=[_parse_condition(c) for c in data["or"]])

    op_str = data.get("operator", "eq")
    try:
        op = CondOp(op_str)
    except ValueError:
        logger.warning("Unknown condition operator '%s', defaulting to 'eq'", op_str)
        op = CondOp.EQ

    return Condition(
        field=data.get("field", ""),
        op=op,
        value=data.get("value"),
    )


# =============================================================================
# RULE
# =============================================================================

@dataclass
class Rule:
    id: str
    name: str
    layer: RuleLayer
    condition: Condition
    # consequences
    forbidden_actions: List[str] = _dc_field(default_factory=list)  # HARD
    forced_action: Optional[str] = None                          # EMERGENCY
    forced_params: Dict[str, Any] = _dc_field(default_factory=dict)
    penalty: float = 0.0                                         # POLICY/PREF
    explanation: str = ""
    source: Dict[str, str] = _dc_field(default_factory=dict)  # {document, section, standard}
    tags: List[str] = _dc_field(default_factory=list)
    enabled: bool = True
    priority: int = 0  # secondary sort within layer
    # Regulatory metadata (optional, for audit / filtering)
    standard: str = ""                  # e.g. "ISO 3691-4:2023"
    section: str = ""                   # e.g. "4.5.2"
    audit_ref: str = ""                 # human-readable reference "ISO 3691-4:2023 §4.5.2"
    applicable_robots: List[str] = _dc_field(default_factory=list)   # ["amr", "agv", "humanoid"]
    jurisdiction: List[str] = _dc_field(default_factory=list)        # ["EU", "US", "INTL"]
    effective_date: str = ""            # ISO date string when regulation becomes effective

    @property
    def effective_priority(self) -> int:
        return self.layer.priority + self.priority

    def matches(self, ctx: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False

        # Optional filtering by robot_type and jurisdiction (per PROMPT_SAFETY_REGULATORY)
        robot_type = ctx.get("robot_type")
        if self.applicable_robots:
            # If robot_type is provided and not listed → skip; if absent, apply rule generically
            if robot_type is not None and str(robot_type) not in self.applicable_robots:
                return False

        ctx_jur = ctx.get("jurisdiction")
        if self.jurisdiction:
            if isinstance(ctx_jur, str):
                if ctx_jur not in self.jurisdiction:
                    return False
            elif isinstance(ctx_jur, (list, tuple, set)):
                if not any(j in self.jurisdiction for j in ctx_jur):
                    return False
            else:
                # No jurisdiction info in context — treat as generic (rule still applies)
                pass

        return self.condition.evaluate(ctx)


_LAYER_ALIASES: Dict[str, str] = {
    "advisory":    "policy",   # advisory mode name ≠ layer; maps to policy
    "soft":        "policy",
    "warn":        "policy",
    "critical":    "hard",
    "mandatory":   "hard",
    "fatal":       "emergency",
    "optional":    "preference",
    "hint":        "preference",
}


def _parse_rule(data: Dict[str, Any]) -> Rule:
    layer_str = data.get("layer", "policy")
    layer_str = _LAYER_ALIASES.get(layer_str, layer_str)
    try:
        layer = RuleLayer(layer_str)
    except ValueError:
        logger.warning("Unknown rule layer '%s' for rule '%s', defaulting to policy",
                       layer_str, data.get("id"))
        layer = RuleLayer.POLICY

    cond_data = data.get("condition", {})
    condition = _parse_condition(cond_data) if cond_data else Condition(op=CondOp.EQ, value=False)

    # EMERGENCY: action block
    forced_action: Optional[str] = None
    forced_params: Dict[str, Any] = {}
    action_block = data.get("action")
    if action_block and isinstance(action_block, dict):
        forced_action = action_block.get("type")
        forced_params = action_block.get("params", {})

    source_block = data.get("source", {})

    # Regulatory metadata with sensible defaults from `source` if not explicitly provided
    standard = data.get("standard", source_block.get("standard", ""))
    section = data.get("section", source_block.get("section", ""))
    audit_ref = data.get(
        "audit_ref",
        f"{standard} §{section}" if standard and section else "",
    )
    applicable_robots = data.get("applicable_robots", [])
    if isinstance(applicable_robots, str):
        applicable_robots = [applicable_robots]
    jurisdiction = data.get("jurisdiction", [])
    if isinstance(jurisdiction, str):
        jurisdiction = [jurisdiction]

    return Rule(
        id=data.get("id", "unknown"),
        name=data.get("name", data.get("id", "unknown")),
        layer=layer,
        condition=condition,
        forbidden_actions=data.get("forbidden_actions", []),
        forced_action=forced_action,
        forced_params=forced_params,
        penalty=float(data.get("penalty", 0.0)),
        explanation=data.get("explanation", ""),
        source=source_block,
        tags=data.get("tags", []),
        enabled=data.get("enabled", True),
        priority=int(data.get("priority", 0)),
        standard=standard,
        section=section,
        audit_ref=audit_ref,
        applicable_robots=applicable_robots,
        jurisdiction=jurisdiction,
        effective_date=data.get("effective_date", ""),
    )


# =============================================================================
# RULE LOADER
# =============================================================================

class RuleLoader:
    """Loads and indexes rules from YAML files."""

    def __init__(self) -> None:
        self._rules: List[Rule] = []
        self._by_id: Dict[str, Rule] = {}
        self._by_layer: Dict[RuleLayer, List[Rule]] = {layer: [] for layer in RuleLayer}
        self._by_tag: Dict[str, List[Rule]] = {}
        self._by_source: Dict[str, List[Rule]] = {}  # source_tag → rules

    def load_file(self, path: Union[str, Path]) -> int:
        if not _YAML:
            logger.error("PyYAML required to load rule files")
            return 0
        path = Path(path)
        if not path.exists():
            logger.warning("Rules file not found: %s", path)
            return 0
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not data:
            return 0
        rules_list = data.get("rules", data) if isinstance(data, dict) else data
        if not isinstance(rules_list, list):
            rules_list = [rules_list]

        # Optional schema validation (if jsonschema is installed)
        if _jsonschema is not None and _RULE_SCHEMA is not None:
            for r_data in rules_list:
                if isinstance(r_data, dict):
                    try:
                        _jsonschema.validate(r_data, _RULE_SCHEMA)
                    except _jsonschema.ValidationError as ve:
                        logger.warning(
                            "Rule '%s' in %s failed schema validation: %s",
                            r_data.get("id", "?"), path.name, ve.message,
                        )

        count = 0
        for r_data in rules_list:
            if not isinstance(r_data, dict):
                continue
            try:
                rule = _parse_rule(r_data)
                self._add(rule)
                count += 1
            except Exception as exc:
                logger.warning("Failed to parse rule '%s': %s",
                               r_data.get("id", "?"), exc)
        logger.info("Loaded %d rules from %s", count, path.name)
        return count

    def load_directory(self, directory: Union[str, Path]) -> int:
        directory = Path(directory)
        total = 0
        for yaml_file in sorted(directory.glob("*.yaml")):
            total += self.load_file(yaml_file)
        return total

    def _add(self, rule: Rule) -> None:
        self._rules.append(rule)
        self._by_id[rule.id] = rule
        self._by_layer[rule.layer].append(rule)
        for tag in rule.tags:
            self._by_tag.setdefault(tag, []).append(rule)

    def get(self, rule_id: str) -> Optional[Rule]:
        return self._by_id.get(rule_id)

    def by_layer(self, layer: RuleLayer) -> List[Rule]:
        return self._by_layer.get(layer, [])

    def sorted_rules(self) -> List[Rule]:
        return sorted(self._rules, key=lambda r: r.effective_priority, reverse=True)

    def stats(self) -> Dict[str, Any]:
        return {
            "total": len(self._rules),
            "by_layer": {lay.value: len(rules) for lay, rules in self._by_layer.items()},
            "enabled": sum(1 for r in self._rules if r.enabled),
        }

    # ── Profession pack support ─────────────────────────────────────

    def load_additional_rules(
        self,
        yaml_path: Union[str, Path],
        source: str = "profession",
    ) -> int:
        """Load extra rules tagged with *source* for later rollback.

        Profession rules may only be POLICY or PREFERENCE layer.
        EMERGENCY / HARD rules are rejected with a warning.
        Returns number of rules successfully loaded.
        """
        if not _YAML:
            logger.error("PyYAML required to load rule files")
            return 0
        path = Path(yaml_path)
        if not path.exists():
            logger.warning("Additional rules file not found: %s", path)
            return 0
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not data:
            return 0
        rules_list = data.get("rules", data) if isinstance(data, dict) else data
        if not isinstance(rules_list, list):
            rules_list = [rules_list]

        count = 0
        for r_data in rules_list:
            if not isinstance(r_data, dict):
                continue
            try:
                rule = _parse_rule(r_data)
                # Profession constraint: only POLICY / PREFERENCE
                if rule.layer in (RuleLayer.EMERGENCY, RuleLayer.HARD):
                    logger.warning(
                        "Rejecting rule '%s': profession rules cannot "
                        "be %s (only POLICY/PREFERENCE allowed)",
                        rule.id, rule.layer.value,
                    )
                    continue
                self._add(rule)
                self._by_source.setdefault(source, []).append(rule)
                count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to parse additional rule '%s': %s",
                    r_data.get("id", "?"), exc,
                )
        logger.info(
            "Loaded %d additional rules from %s (source=%s)",
            count, path.name, source,
        )
        return count

    def unload_rules_by_source(self, source: str) -> int:
        """Remove all rules tagged with *source*. Returns count removed."""
        to_remove = self._by_source.pop(source, [])
        if not to_remove:
            return 0
        remove_ids = {r.id for r in to_remove}
        self._rules = [r for r in self._rules if r.id not in remove_ids]
        for rid in remove_ids:
            self._by_id.pop(rid, None)
        for layer in RuleLayer:
            self._by_layer[layer] = [
                r for r in self._by_layer[layer]
                if r.id not in remove_ids
            ]
        for tag in list(self._by_tag):
            self._by_tag[tag] = [
                r for r in self._by_tag[tag]
                if r.id not in remove_ids
            ]
            if not self._by_tag[tag]:
                del self._by_tag[tag]
        logger.info(
            "Unloaded %d rules (source=%s)", len(remove_ids), source,
        )
        return len(remove_ids)

    @property
    def rules(self) -> List[Rule]:
        return list(self._rules)


# =============================================================================
# EVALUATION RESULT
# =============================================================================

@dataclass
class Violation:
    rule_id: str
    layer: str
    description: str
    severity: float = 1.0  # 0-1; 1 = blocking


@dataclass
class EvalResult:
    """Result of evaluating an action context against all rules."""
    is_valid: bool                  # False if any HARD rule triggered
    violations: List[Violation]     # all matched rules with consequences
    total_penalty: float            # sum of POLICY/PREFERENCE penalties
    forced_action: Optional[str]    # set by EMERGENCY override
    forced_params: Dict[str, Any]
    emergency_rule_id: Optional[str]
    explain: str
    trace: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "violations": [{"rule": v.rule_id, "layer": v.layer,
                             "description": v.description, "severity": v.severity}
                           for v in self.violations],
            "total_penalty": self.total_penalty,
            "forced_action": self.forced_action,
            "emergency_rule_id": self.emergency_rule_id,
            "explain": self.explain,
        }


# =============================================================================
# RULE ENGINE
# =============================================================================

class RuleEngine:
    """
    4-layer rule engine:

    1. EMERGENCY  — force immediate action (override all)
    2. HARD       — block forbidden action types
    3. POLICY     — add penalty (SHOULD-NOT)
    4. PREFERENCE — add small penalty (PREFER)
    """

    def __init__(self, loader: Optional[RuleLoader] = None) -> None:
        self._loader = loader or RuleLoader()

    def load_file(self, path: Union[str, Path]) -> int:
        return self._loader.load_file(path)

    def load_directory(self, directory: Union[str, Path]) -> int:
        return self._loader.load_directory(directory)

    def load_builtin_rules(self) -> int:
        """Load all YAML rule files from libs/ontology/rules/."""
        if not _RULES_DIR.exists():
            logger.warning("Built-in rules directory not found: %s", _RULES_DIR)
            return 0
        return self._loader.load_directory(_RULES_DIR)

    def load_additional_rules(
        self, yaml_path: Union[str, Path], source: str = "profession",
    ) -> int:
        """Delegate to loader — see RuleLoader.load_additional_rules."""
        return self._loader.load_additional_rules(yaml_path, source)

    def unload_rules_by_source(self, source: str) -> int:
        """Delegate to loader — see RuleLoader.unload_rules_by_source."""
        return self._loader.unload_rules_by_source(source)

    @property
    def loader(self) -> RuleLoader:
        return self._loader

    # ── Core evaluation ───────────────────────────────────────────────────

    def evaluate(self, ctx: Dict[str, Any],
                 action_type: str = "") -> EvalResult:
        """
        Evaluate the context against all rules.

        ctx:         flat or nested dict — e.g. {"robot": {"battery_level": 4}, ...}
        action_type: the type string of the proposed action (e.g. "navigate")
        """
        t0 = time.monotonic()
        violations: List[Violation] = []
        total_penalty = 0.0
        is_valid = True
        forced_action: Optional[str] = None
        forced_params: Dict[str, Any] = {}
        emergency_rule_id: Optional[str] = None

        for rule in self._loader.sorted_rules():
            if not rule.matches(ctx):
                continue

            if rule.layer == RuleLayer.EMERGENCY:
                if rule.forced_action and forced_action is None:
                    forced_action = rule.forced_action
                    forced_params = rule.forced_params
                    emergency_rule_id = rule.id
                violations.append(Violation(
                    rule_id=rule.id,
                    layer=rule.layer.value,
                    description=rule.explanation or f"Emergency rule {rule.id} triggered",
                    severity=1.0,
                ))

            elif rule.layer == RuleLayer.HARD:
                if action_type and action_type.lower() in [a.lower() for a in rule.forbidden_actions]:
                    is_valid = False
                    violations.append(Violation(
                        rule_id=rule.id,
                        layer=rule.layer.value,
                        description=rule.explanation or f"Action '{action_type}' forbidden by {rule.id}",
                        severity=1.0,
                    ))
                elif not rule.forbidden_actions:
                    # Hard rule with no specific forbidden_actions — still a violation
                    is_valid = False
                    violations.append(Violation(
                        rule_id=rule.id,
                        layer=rule.layer.value,
                        description=rule.explanation or f"Hard constraint {rule.id} triggered",
                        severity=0.9,
                    ))

            elif rule.layer in (RuleLayer.POLICY, RuleLayer.PREFERENCE):
                weight = 1.0 if rule.layer == RuleLayer.POLICY else 0.1
                total_penalty += rule.penalty * weight
                if rule.penalty > 0:
                    violations.append(Violation(
                        rule_id=rule.id,
                        layer=rule.layer.value,
                        description=rule.explanation or f"Policy {rule.id} penalty",
                        severity=rule.penalty / 100.0,
                    ))

        latency_ms = (time.monotonic() - t0) * 1000
        explain_lines = [f"Action: {action_type!r}",
                         f"Valid: {'YES' if is_valid else 'NO'}"]
        if forced_action:
            explain_lines.append(f"EMERGENCY OVERRIDE → {forced_action}")
        for v in violations:
            explain_lines.append(f"  [{v.layer}] {v.rule_id}: {v.description}")
        explain_lines.append(f"Penalty total: {total_penalty:.1f}")

        return EvalResult(
            is_valid=is_valid,
            violations=violations,
            total_penalty=total_penalty,
            forced_action=forced_action,
            forced_params=forced_params,
            emergency_rule_id=emergency_rule_id,
            explain="\n".join(explain_lines),
            trace={"latency_ms": round(latency_ms, 2),
                   "rules_total": len(self._loader.rules),
                   "rules_matched": len(violations)},
        )

    def stats(self) -> Dict[str, Any]:
        return self._loader.stats()


# =============================================================================
# CONTEXT BUILDER HELPER
# =============================================================================

def build_context(
    robot: Optional[Dict[str, Any]] = None,
    action: Optional[Dict[str, Any]] = None,
    zone: Optional[Dict[str, Any]] = None,
    target_zone: Optional[Dict[str, Any]] = None,
    events: Optional[List[str]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """
    Convenience helper to build the evaluation context dict.

    All sub-dicts accessible via dot notation in conditions:
        robot.battery_level, action.speed_mps, zone.humans_present, etc.
    """
    ctx: Dict[str, Any] = {
        "robot": robot or {},
        "action": action or {},
        "zone": zone or {},
        "target_zone": target_zone or zone or {},
        "events": events or [],
    }
    ctx.update(extra)
    return ctx
