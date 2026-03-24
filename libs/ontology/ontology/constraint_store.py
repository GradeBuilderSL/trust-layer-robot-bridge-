"""ConstraintStore — maps zones to STL safety rules + runtime lookup.

Bridges between ontology (zone definitions) and the formal verification
layer (STL checker). Also integrates with libs/rlm for deterministic rules.

Usage:
    from ontology.constraint_store import ConstraintStore

    cs = ConstraintStore(world_model)
    cs.add("zone_a1", "G(speed < 0.3)", source="regulation")
    constraints = cs.get_constraints(position=(1.0, 2.0), zone_id="zone_a1")
    # → [Constraint(formula="G(speed < 0.3)", max_speed=0.3, ...)]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Constraint:
    zone_uri: str
    stl_formula: str
    source: str = "operator"     # operator | learned | regulation
    max_speed_mps: Optional[float] = None  # parsed shortcut
    min_clearance_m: Optional[float] = None
    access_level: Optional[int] = None
    label: str = ""


class ConstraintStore:
    """Runtime store of zone constraints for fast lookup by position or zone ID."""

    def __init__(self, world_model: Optional[Any] = None) -> None:
        self._wm = world_model
        # zone_uri → [Constraint]
        self._rules: Dict[str, List[Constraint]] = {}

    # ── Adding rules ──────────────────────────────────────────────────────

    def add(
        self, zone_id_or_uri: str, stl_formula: str,
        source: str = "operator", label: str = "",
        max_speed_mps: Optional[float] = None,
        min_clearance_m: Optional[float] = None,
    ) -> Constraint:
        zone_uri = zone_id_or_uri
        c = Constraint(
            zone_uri=zone_uri,
            stl_formula=stl_formula,
            source=source,
            label=label or stl_formula,
            max_speed_mps=max_speed_mps,
            min_clearance_m=min_clearance_m,
        )
        self._rules.setdefault(zone_uri, []).append(c)
        logger.debug("Added constraint for %s: %s", zone_uri, stl_formula)
        return c

    def add_from_zone(self, zone_id_or_uri: str) -> List[Constraint]:
        """Pull constraints from WorldModel for a zone and cache them."""
        if not self._wm:
            return []
        zone = self._wm.get_zone(zone_id_or_uri)
        if not zone:
            return []

        added = []
        # Speed limit
        if zone.max_speed_mps < 2.0:
            c = self.add(
                zone.uri,
                f"G(speed < {zone.max_speed_mps})",
                source="zone_property",
                max_speed_mps=zone.max_speed_mps,
            )
            added.append(c)
        # Clearance
        if zone.min_clearance_m > 0:
            c = self.add(
                zone.uri,
                f"G(clearance > {zone.min_clearance_m})",
                source="zone_property",
                min_clearance_m=zone.min_clearance_m,
            )
            added.append(c)
        # Access
        if zone.access_level >= 3:
            c = self.add(
                zone.uri,
                "G(NOT inForbiddenZone)",
                source="zone_property",
                access_level=zone.access_level,
            )
            added.append(c)

        # Also load STL constraints from ontology
        try:
            ontology_rules = self._wm.get_constraints_for_zone(zone_id_or_uri)
            for rule in ontology_rules:
                c = self.add(
                    zone.uri,
                    rule["formula"],
                    source=rule.get("source", "ontology"),
                )
                added.append(c)
        except Exception:
            pass

        return added

    # ── Lookup ────────────────────────────────────────────────────────────

    def get_constraints(
        self,
        zone_id_or_uri: Optional[str] = None,
        position: Optional[Tuple[float, float]] = None,
    ) -> List[Constraint]:
        """Return all constraints for a zone (by ID) or position.

        Position lookup requires WorldModel with polygon data.
        """
        if zone_id_or_uri:
            zone_uri = zone_id_or_uri
            rules = self._rules.get(zone_uri, [])
            if not rules:
                # Lazy-load from world model
                rules = self.add_from_zone(zone_id_or_uri)
            return rules

        if position and self._wm:
            return self._lookup_by_position(position)

        return []

    def get_speed_limit(self, zone_id_or_uri: str) -> float:
        """Return the strictest speed limit for a zone."""
        constraints = self.get_constraints(zone_id_or_uri)
        speeds = [c.max_speed_mps for c in constraints
                  if c.max_speed_mps is not None]
        if speeds:
            return min(speeds)
        if self._wm:
            return self._wm.get_speed_limit(zone_id_or_uri)
        return 2.0

    def all_stl_formulas(self) -> List[str]:
        """Return all distinct STL formulas in the store."""
        formulas = set()
        for rules in self._rules.values():
            for c in rules:
                formulas.add(c.stl_formula)
        return sorted(formulas)

    def all_constraints(self) -> List[Constraint]:
        result = []
        for rules in self._rules.values():
            result.extend(rules)
        return result

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zones": {
                uri: [{"formula": c.stl_formula, "source": c.source}
                      for c in rules]
                for uri, rules in self._rules.items()
            }
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        for uri, rules in data.get("zones", {}).items():
            for rule in rules:
                self.add(uri, rule["formula"], rule.get("source", "operator"))

    # ── Internal ──────────────────────────────────────────────────────────

    def _lookup_by_position(
        self, position: Tuple[float, float]
    ) -> List[Constraint]:
        """Find all zones containing position and return their constraints."""
        result = []
        if not self._wm:
            return result
        for zone in self._wm.all_zones():
            if zone.polygon and self._point_in_polygon(position, zone.polygon):
                result.extend(self.get_constraints(zone.uri))
        return result

    @staticmethod
    def _point_in_polygon(
        point: Tuple[float, float],
        polygon: List[Tuple[float, float]],
    ) -> bool:
        """Ray-casting algorithm for point-in-polygon test."""
        x, y = point
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
                inside = not inside
            j = i
        return inside
