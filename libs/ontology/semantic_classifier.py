"""SemanticClassifier — rule-based classification of SpatialEntity.

Enriches a SpatialEntity with:
  - semantic_type : "wall" | "shelf" | "box" | "human" | "door" | ...
  - stability_class: "structural" | "semi_static" | "dynamic" | "ephemeral"
  - safety_tags    : ["fragile", "hazard", "trip_hazard", "narrow_passage", ...]
  - min_clearance_m: minimum safe clearance in metres

Architecture constraints (STEERING.md §7, §2):
  - Rule-based only — no ML/neural nets in L2
  - May be called from L3 advisory path (NOT from L2a safety_edge hot path)
  - class_name from perception_edge (YOLO) is used as a strong hint
  - Falls back to geometry-based classification if class_name is absent
  - Graceful: returns entity unchanged if classification raises
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from world_memory.world_state import SpatialEntity

# ── Classification tables ─────────────────────────────────────────────────────

# class_name (from YOLO / perception_edge) → (semantic_type, stability_class,
#                                             safety_tags, clearance_m)
_CLASS_RULES: Dict[str, tuple] = {
    # People & dynamic agents
    "human":      ("human",    "ephemeral",   ["human_proximity"],   0.8),
    "person":     ("human",    "ephemeral",   ["human_proximity"],   0.8),
    "pedestrian": ("human",    "ephemeral",   ["human_proximity"],   0.8),
    # Wheeled vehicles
    "forklift":   ("vehicle",  "dynamic",     ["hazard", "heavy"],   1.5),
    "cart":       ("vehicle",  "dynamic",     ["hazard"],            0.5),
    "robot":      ("robot",    "dynamic",     ["hazard"],            0.5),
    # Furniture / semi-fixed objects
    "shelf":      ("shelf",    "semi_static", ["fragile"],           0.3),
    "rack":       ("shelf",    "semi_static", ["fragile"],           0.3),
    "table":      ("furniture", "semi_static", [],                   0.3),
    "chair":      ("furniture", "semi_static", [],                   0.2),
    "pallet":     ("box",      "dynamic",     [],                    0.2),
    # Boxes / loose objects on floor
    "box":        ("box",      "dynamic",     ["trip_hazard"],       0.2),
    "crate":      ("box",      "dynamic",     ["trip_hazard"],       0.2),
    "barrel":     ("box",      "dynamic",     ["hazard"],            0.3),
    # Hazardous / special
    "cable":      ("cable",    "dynamic",     ["trip_hazard"],       0.3),
    "wire":       ("cable",    "dynamic",     ["trip_hazard"],       0.3),
    "door":       ("door",     "semi_static", ["narrow_passage"],    0.15),
    # Structural
    "wall":       ("wall",     "structural",  [],                    0.15),
    "pillar":     ("wall",     "structural",  [],                    0.2),
    "column":     ("wall",     "structural",  [],                    0.2),
    # Signs / markers
    "sign":       ("sign",     "structural",  [],                    0.0),
    "cone":       ("cone",     "dynamic",     ["hazard"],            0.3),
}

# Geometry-based rules (when class_name gives no hint).
# Geometry hint keys:
#   height_m, width_m, depth_m, is_moving (bool), is_vertical_plane (bool)
_GEOMETRY_RULES = [
    # Moving → ephemeral human or vehicle
    {
        "is_moving": True,
        "semantic_type": "human",
        "stability_class": "ephemeral",
        "safety_tags": ["human_proximity"],
        "min_clearance_m": 0.8,
    },
    # Tall vertical plane → structural wall
    {
        "is_vertical_plane": True,
        "semantic_type": "wall",
        "stability_class": "structural",
        "safety_tags": [],
        "min_clearance_m": 0.15,
    },
    # Large object on floor (> 0.5 m in any dimension) → semi_static furniture
    {
        "floor_object": True,
        "large": True,
        "semantic_type": "furniture",
        "stability_class": "semi_static",
        "safety_tags": [],
        "min_clearance_m": 0.3,
    },
    # Small object on floor (≤ 0.5 m) → dynamic box/obstacle
    {
        "floor_object": True,
        "large": False,
        "semantic_type": "box",
        "stability_class": "dynamic",
        "safety_tags": ["trip_hazard"],
        "min_clearance_m": 0.2,
    },
]

_LARGE_THRESHOLD_M: float = 0.5   # objects larger than this are "large"


def _classify_by_geometry(geometry_hint: dict) -> Optional[dict]:
    """Return classification dict from geometry hint, or None if undetermined."""
    if not geometry_hint:
        return None

    is_moving = bool(geometry_hint.get("is_moving", False))
    is_vertical_plane = bool(geometry_hint.get("is_vertical_plane", False))
    height = float(geometry_hint.get("height_m", 0.0))
    width = float(geometry_hint.get("width_m", 0.0))
    depth = float(geometry_hint.get("depth_m", 0.0))
    on_floor = bool(geometry_hint.get("on_floor", True))

    max_dim = max(height, width, depth)
    large = max_dim > _LARGE_THRESHOLD_M

    if is_moving:
        return _GEOMETRY_RULES[0]
    if is_vertical_plane or (height > 1.5 and width > 0.5):
        return _GEOMETRY_RULES[1]
    if on_floor and large:
        return _GEOMETRY_RULES[2]
    if on_floor and not large:
        return _GEOMETRY_RULES[3]
    return None


class SemanticClassifier:
    """Classify SpatialEntity by semantic_type, stability_class, safety_tags.

    Uses class_name (YOLO hint) first; falls back to geometry_hint if absent.

    Usage (L3 advisory path only):
        classifier = SemanticClassifier()
        enriched = classifier.classify(entity, geometry_hint={"height_m": 0.3, "on_floor": True})
    """

    def __init__(self, rule_engine=None) -> None:
        """
        rule_engine: optional ontology.rule_engine.RuleEngine instance.

        When provided, it is used to *augment* safety_tags / min_clearance_m
        based on ontology rules. It never relaxes or removes any tags produced
        by the built-in tables above.
        """
        self._rule_engine = rule_engine

    def classify(
        self,
        entity: "SpatialEntity",
        geometry_hint: Optional[Dict] = None,
    ) -> "SpatialEntity":
        """Return entity with semantic fields populated.

        Tries class_name lookup first, then geometry_hint.
        If neither yields a classification, returns entity unchanged
        (fail-safe: unknown is better than wrong).

        Never mutates the input entity — returns a new instance via dataclasses.replace.
        """
        try:
            return self._do_classify(entity, geometry_hint or {})
        except Exception as exc:
            logger.warning(
                "SemanticClassifier.classify failed for %s: %s",
                entity.entity_id,
                exc,
            )
            return entity

    def _do_classify(
        self,
        entity: "SpatialEntity",
        geometry_hint: Dict,
    ) -> "SpatialEntity":
        import dataclasses

        # 1. Try class_name lookup (YOLO-provided label takes priority)
        key = (entity.class_name or "").lower().strip()
        rule = _CLASS_RULES.get(key)

        if rule is None and entity.semantic_type:
            # Maybe semantic_type was already set by a previous pass
            rule = _CLASS_RULES.get(entity.semantic_type.lower().strip())

        if rule is not None:
            sem_type, stab_class, tags, clearance = rule
            base = dataclasses.replace(
                entity,
                semantic_type=sem_type,
                stability_class=stab_class,
                safety_tags=list(tags),
                min_clearance_m=clearance,
            )
            return self._augment_with_rules(base)

        # 2. Fallback: geometry-based classification
        geo_result = _classify_by_geometry(geometry_hint)
        if geo_result is not None:
            base = dataclasses.replace(
                entity,
                semantic_type=geo_result["semantic_type"],
                stability_class=geo_result["stability_class"],
                safety_tags=list(geo_result["safety_tags"]),
                min_clearance_m=geo_result["min_clearance_m"],
            )
            return self._augment_with_rules(base)

        # 3. No classification possible — return unchanged
        return entity

    def _augment_with_rules(self, entity: "SpatialEntity") -> "SpatialEntity":
        """Apply ontology.rule_engine to add safety_tags / min_clearance_m.

        Advisory (L3): only adds tags / increases min_clearance_m.
        Never removes tags or decreases clearance.
        """
        # Infer physics_tags from WorldKnowledgeBase (optional, L3 advisory)
        try:
            import dataclasses as _dc
            from world_knowledge.knowledge_base import (
                get_knowledge_base,
            )
            kb = get_knowledge_base()
            geometry = {}
            bb = getattr(entity, "attributes", {}).get("bounding_box")
            if bb and len(bb) >= 3:
                geometry = {
                    "length": bb[0],
                    "width": bb[1],
                    "height": bb[2],
                }
            tags = kb.infer_physics_tags(
                entity.semantic_type or entity.class_name or "",
                geometry,
            )
            if tags and hasattr(entity, "physics_tags"):
                existing = list(getattr(entity, "physics_tags", []))
                from effect_model.physics_types import PhysicsTag
                new_tags = []
                for t in tags:
                    try:
                        new_tags.append(PhysicsTag(t))
                    except ValueError:
                        pass
                merged = list(
                    {pt.value: pt for pt in existing + new_tags}.values()
                )
                entity = _dc.replace(entity, physics_tags=merged)
        except Exception:
            pass  # graceful: world_knowledge not available

        if self._rule_engine is None:
            return entity

        try:
            import dataclasses

            ctx = {
                "entity": {
                    "class_name": entity.class_name,
                    "semantic_type": entity.semantic_type,
                    "stability_class": entity.stability_class,
                    "safety_tags": list(entity.safety_tags),
                    "min_clearance_m": entity.min_clearance_m,
                }
            }
            result = self._rule_engine.evaluate(ctx, action_type="classify")
            extra_tags: List[str] = []
            extra_clearance = 0.0
            for v in result.violations:
                t = v.trace.get("safety_tag")
                if t:
                    extra_tags.append(str(t))
                mc = v.trace.get("min_clearance_m")
                if mc:
                    try:
                        extra_clearance = max(extra_clearance, float(mc))
                    except (TypeError, ValueError):
                        continue

            merged_tags = sorted(set(entity.safety_tags) | set(extra_tags))
            clearance = max(entity.min_clearance_m, extra_clearance)
            return dataclasses.replace(
                entity,
                safety_tags=merged_tags,
                min_clearance_m=clearance,
            )
        except Exception:
            # Fail-safe: if ontology evaluation fails, keep base entity as-is.
            return entity

    def batch_classify(
        self,
        entities: List["SpatialEntity"],
        geometry_hints: Optional[Dict[str, Dict]] = None,
    ) -> List["SpatialEntity"]:
        """Classify a list of entities. geometry_hints keyed by entity_id."""
        hints = geometry_hints or {}
        return [
            self.classify(e, geometry_hint=hints.get(e.entity_id))
            for e in entities
        ]
