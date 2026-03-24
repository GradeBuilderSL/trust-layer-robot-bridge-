"""Affordance Model — disposition-based safety constraints.

Based on SOMA (Socio-physical Model of Activities, University of Bremen).
Maps object dispositions to safety constraints automatically.

When robot interacts with an object, the affordance model determines
which safety constraints apply based on the object's dispositions.

Usage:
    model = AffordanceModel()
    constraints = model.safety_constraints("GlassPanel", "pick")
    # → {"max_force_N": 5.0, "max_speed_mps": 0.1, "requires_caution": True,
    #    "risk_factors": ["BREAK_RISK", "CUT_RISK"]}
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

logger = logging.getLogger(__name__)

_TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "object_taxonomy.yaml")


# ── Safety constraints per disposition ───────────────────────────────

@dataclass(frozen=True)
class SafetyConstraint:
    """A safety constraint triggered by a disposition."""
    max_force_N: Optional[float] = None
    max_speed_mps: Optional[float] = None
    min_distance_m: Optional[float] = None
    requires_caution: bool = False
    requires_ppe: bool = False
    no_approach: bool = False
    risk_factors: Tuple[str, ...] = ()


# Disposition → SafetyConstraint mapping
# When an object has a disposition, these constraints apply to actions on it
DISPOSITION_CONSTRAINTS: Dict[str, SafetyConstraint] = {
    "breakable": SafetyConstraint(
        max_force_N=5.0, max_speed_mps=0.1, requires_caution=True,
        risk_factors=("BREAK_RISK",)),
    "sharp_when_broken": SafetyConstraint(
        requires_caution=True, risk_factors=("CUT_RISK",)),
    "crushable": SafetyConstraint(
        max_force_N=10.0, requires_caution=True,
        risk_factors=("CRUSH_RISK",)),
    "heavy": SafetyConstraint(
        max_speed_mps=0.3, requires_caution=True,
        risk_factors=("CRUSH_RISK",)),
    "heavy_possible": SafetyConstraint(
        requires_caution=True, risk_factors=("CRUSH_RISK",)),
    "hazardous": SafetyConstraint(
        no_approach=True, min_distance_m=2.0,
        risk_factors=("CONTAMINATION_RISK",)),
    "high_voltage": SafetyConstraint(
        no_approach=True, min_distance_m=1.5,
        risk_factors=("ELECTRICAL_HAZARD",)),
    "high_temperature": SafetyConstraint(
        no_approach=True, min_distance_m=1.0,
        risk_factors=("BURN_RISK",)),
    "pressurized": SafetyConstraint(
        max_force_N=20.0, risk_factors=("PRESSURIZED",)),
    "entanglement_hazard": SafetyConstraint(
        min_distance_m=0.5, risk_factors=("ENTANGLEMENT_RISK",)),
    "moving_surface": SafetyConstraint(
        min_distance_m=0.5, risk_factors=("ENTANGLEMENT_RISK",)),
    "unpredictable": SafetyConstraint(
        min_distance_m=1.5, max_speed_mps=0.5,
        risk_factors=("COLLISION_RISK",)),
    "low_height": SafetyConstraint(
        min_distance_m=2.0, max_speed_mps=0.3,
        risk_factors=("COLLISION_RISK", "CRITICAL_PROXIMITY")),
    "autonomous_movement": SafetyConstraint(
        min_distance_m=1.0, risk_factors=("COLLISION_RISK",)),
    "immovable": SafetyConstraint(
        risk_factors=("COLLISION_RISK",)),
    "climbable_hazard": SafetyConstraint(
        risk_factors=("FALLING_OBJECT_RISK",)),
    "tall_structure": SafetyConstraint(
        risk_factors=("FALLING_OBJECT_RISK",)),
    "liquid_containable": SafetyConstraint(
        requires_caution=True, risk_factors=("SPILL_RISK",)),
    "sensitive_to_moisture": SafetyConstraint(
        requires_caution=True, risk_factors=("BREAK_RISK",)),
}


# ── Taxonomy Loader ──────────────────────────────────────────────────

class ObjectTaxonomy:
    """Loads and queries the object type hierarchy."""

    def __init__(self, path: str = _TAXONOMY_PATH):
        self._nodes: Dict[str, dict] = {}
        self._alias_map: Dict[str, str] = {}  # lowercase alias → class name
        self._load(path)

    def _load(self, path: str) -> None:
        if not os.path.exists(path):
            logger.warning("Taxonomy file not found: %s", path)
            return
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return
        for class_name, props in data.items():
            if not isinstance(props, dict):
                continue
            self._nodes[class_name] = props
            for alias in props.get("aliases", []):
                self._alias_map[alias.lower()] = class_name

    def resolve(self, label: str) -> Optional[str]:
        """Resolve a label/alias to a taxonomy class name."""
        if label in self._nodes:
            return label
        return self._alias_map.get(label.lower())

    def parent_of(self, class_name: str) -> Optional[str]:
        node = self._nodes.get(class_name)
        return node["parent"] if node else None

    def ancestors(self, class_name: str) -> List[str]:
        """Return ancestor chain from immediate parent to root."""
        result = []
        current = class_name
        visited: Set[str] = set()
        while current:
            node = self._nodes.get(current)
            if not node or current in visited:
                break
            visited.add(current)
            parent = node.get("parent")
            if parent:
                result.append(parent)
            current = parent
        return result

    def children_of(self, class_name: str) -> List[str]:
        """Return direct children of a class."""
        return [name for name, props in self._nodes.items()
                if props.get("parent") == class_name]

    def descendants(self, class_name: str) -> List[str]:
        """Return all descendants (recursive)."""
        result = []
        queue = self.children_of(class_name)
        while queue:
            child = queue.pop(0)
            result.append(child)
            queue.extend(self.children_of(child))
        return result

    def is_a(self, class_name: str, ancestor: str) -> bool:
        """Check if class_name is a subtype of ancestor."""
        if class_name == ancestor:
            return True
        return ancestor in self.ancestors(class_name)

    def stability(self, class_name: str) -> str:
        """Get stability class for an object type."""
        node = self._nodes.get(class_name)
        return node.get("stability", "unknown") if node else "unknown"

    def dispositions(self, class_name: str) -> List[str]:
        """Get all dispositions (including inherited from ancestors)."""
        result: List[str] = []
        seen: Set[str] = set()
        current = class_name
        while current:
            node = self._nodes.get(current)
            if not node or current in seen:
                break
            seen.add(current)
            for d in node.get("dispositions", []):
                if d not in result:
                    result.append(d)
            current = node.get("parent")
        return result

    def safety_tags(self, class_name: str) -> List[str]:
        """Get all safety tags (including inherited)."""
        result: List[str] = []
        seen: Set[str] = set()
        current = class_name
        while current:
            node = self._nodes.get(current)
            if not node or current in seen:
                break
            seen.add(current)
            for t in node.get("safety_tags", []):
                if t not in result:
                    result.append(t)
            current = node.get("parent")
        return result

    @property
    def all_classes(self) -> List[str]:
        return list(self._nodes.keys())


# ── Affordance Model ─────────────────────────────────────────────────

class AffordanceModel:
    """Maps object types + actions to safety constraints via dispositions."""

    def __init__(self, taxonomy: Optional[ObjectTaxonomy] = None):
        self._taxonomy = taxonomy or ObjectTaxonomy()

    def safety_constraints(self, class_name_or_label: str,
                           action: str = "") -> Dict[str, Any]:
        """Compute aggregated safety constraints for an object.

        Args:
            class_name_or_label: taxonomy class name or alias
            action: optional action type (pick, place, navigate, push)

        Returns:
            dict with max_force_N, max_speed_mps, min_distance_m,
                 requires_caution, no_approach, risk_factors
        """
        resolved = self._taxonomy.resolve(class_name_or_label)
        if not resolved:
            return {"risk_factors": [], "requires_caution": False}

        dispositions = self._taxonomy.dispositions(resolved)
        tags = self._taxonomy.safety_tags(resolved)

        # Aggregate constraints from all dispositions
        max_force: Optional[float] = None
        max_speed: Optional[float] = None
        min_dist: Optional[float] = None
        caution = False
        no_approach = False
        ppe = False
        risks: List[str] = list(tags)

        for disp in dispositions:
            constraint = DISPOSITION_CONSTRAINTS.get(disp)
            if not constraint:
                continue
            if constraint.max_force_N is not None:
                max_force = min(max_force, constraint.max_force_N) \
                    if max_force is not None else constraint.max_force_N
            if constraint.max_speed_mps is not None:
                max_speed = min(max_speed, constraint.max_speed_mps) \
                    if max_speed is not None else constraint.max_speed_mps
            if constraint.min_distance_m is not None:
                min_dist = max(min_dist, constraint.min_distance_m) \
                    if min_dist is not None else constraint.min_distance_m
            caution = caution or constraint.requires_caution
            no_approach = no_approach or constraint.no_approach
            ppe = ppe or constraint.requires_ppe
            for rf in constraint.risk_factors:
                if rf not in risks:
                    risks.append(rf)

        return {
            "class_name": resolved,
            "dispositions": dispositions,
            "max_force_N": max_force,
            "max_speed_mps": max_speed,
            "min_distance_m": min_dist,
            "requires_caution": caution,
            "no_approach": no_approach,
            "requires_ppe": ppe,
            "risk_factors": risks,
        }

    def is_hazardous(self, class_name_or_label: str) -> bool:
        """Quick check if object is hazardous (no_approach)."""
        c = self.safety_constraints(class_name_or_label)
        return c.get("no_approach", False)

    def risk_level(self, class_name_or_label: str) -> str:
        """Compute risk level: LOW / MEDIUM / HIGH / CRITICAL."""
        c = self.safety_constraints(class_name_or_label)
        risks = c.get("risk_factors", [])
        if c.get("no_approach"):
            return "CRITICAL"
        critical_tags = {"ELECTRICAL_HAZARD", "CONTAMINATION_RISK", "CRITICAL_PROXIMITY"}
        if any(r in critical_tags for r in risks):
            return "HIGH"
        if c.get("requires_caution") or len(risks) >= 2:
            return "MEDIUM"
        if risks:
            return "LOW"
        return "LOW"
