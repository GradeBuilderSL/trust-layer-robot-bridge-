"""WorldModel — high-level semantic map API built on OntologyEngine.

Provides human-friendly methods to manage zones, objects, and waypoints
without writing raw SPARQL or RDF triples.

Usage:
    from ontology.world_model import WorldModel

    wm = WorldModel()
    wm.load_schema("libs/ontology/schemas/warehouse.owl")
    wm.add_zone("zone_a1", "HumanZone", max_speed_mps=0.3, label="Aisle A1")
    wm.add_object("shelf_a1_01", "Shelf", zone_uri="zone_a1")
    speed = wm.get_speed_limit("zone_a1")   # → 0.3
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ontology.engine import OntologyEngine, NS_RM, NS_WH

logger = logging.getLogger(__name__)

# Namespace shortcuts
_RM = NS_RM
_WH = NS_WH


@dataclass
class ZoneInfo:
    uri: str
    zone_type: str          # e.g. "HumanZone", "StorageZone"
    label: str = ""
    max_speed_mps: float = 2.0
    min_clearance_m: float = 0.3
    access_level: int = 0   # 0=open, 1=auth, 2=restricted, 3=forbidden
    constraint_uris: List[str] = field(default_factory=list)
    polygon: List[Tuple[float, float]] = field(default_factory=list)  # [(x,y)]


@dataclass
class ObjectInfo:
    uri: str
    obj_type: str
    label: str = ""
    zone_uri: str = ""
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: Dict[str, str] = field(default_factory=dict)


class WorldModel:
    """Semantic world model backed by OntologyEngine."""

    # RDF type predicate
    _RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

    def __init__(self, engine: Optional[OntologyEngine] = None,
                 environment_id: str = "default") -> None:
        self._engine = engine or OntologyEngine()
        self._env_id = environment_id
        self._ns = f"https://world.partenit.ai/{environment_id}#"
        self._zone_cache: Dict[str, ZoneInfo] = {}
        self._object_cache: Dict[str, ObjectInfo] = {}

    # ── Schema loading ────────────────────────────────────────────────────

    def load_schema(self, path: str) -> int:
        """Load an OWL schema (warehouse.owl, outdoor.owl, etc.)."""
        return self._engine.load(path)

    def load_schema_from_package(self, schema_name: str) -> int:
        """Load a built-in schema by name (warehouse | outdoor | robomind)."""
        here = Path(__file__).parent / "schemas" / f"{schema_name}.owl"
        return self._engine.load(str(here))

    # ── Zone management ───────────────────────────────────────────────────

    def add_zone(
        self,
        zone_id: str,
        zone_type: str,
        label: str = "",
        max_speed_mps: float = 2.0,
        min_clearance_m: float = 0.3,
        access_level: int = 0,
        polygon: Optional[List[Tuple[float, float]]] = None,
    ) -> str:
        """Add a zone and return its URI."""
        uri = self._uri(zone_id)
        type_uri = self._type_uri(zone_type)

        props: Dict[str, Any] = {
            _RM + "maxSpeedMps": max_speed_mps,
            _RM + "minClearanceM": min_clearance_m,
            _RM + "accessLevel": access_level,
        }
        if label:
            props[_RM + "label"] = label

        self._engine.add_entity(uri, type_uri, props)

        info = ZoneInfo(
            uri=uri,
            zone_type=zone_type,
            label=label or zone_id,
            max_speed_mps=max_speed_mps,
            min_clearance_m=min_clearance_m,
            access_level=access_level,
            polygon=polygon or [],
        )
        self._zone_cache[uri] = info
        logger.info("Added zone %s (type=%s, max_speed=%.1f)", uri, zone_type, max_speed_mps)
        return uri

    def get_zone(self, zone_id_or_uri: str) -> Optional[ZoneInfo]:
        """Retrieve zone info by id or full URI."""
        uri = zone_id_or_uri if zone_id_or_uri.startswith("http") else self._uri(zone_id_or_uri)
        if uri in self._zone_cache:
            return self._zone_cache[uri]
        # Fallback: load from graph
        props = self._engine.get_properties(uri)
        if not props:
            return None
        info = ZoneInfo(
            uri=uri,
            zone_type=self._extract_local(props.get(self._RDF_TYPE, ["Zone"])[0]),
            label=props.get(_RM + "label", [uri])[0],
            max_speed_mps=float(props.get(_RM + "maxSpeedMps", [2.0])[0]),
            min_clearance_m=float(props.get(_RM + "minClearanceM", [0.3])[0]),
            access_level=int(props.get(_RM + "accessLevel", [0])[0]),
        )
        self._zone_cache[uri] = info
        return info

    def get_speed_limit(self, zone_id_or_uri: str) -> float:
        """Return max_speed_mps for zone, or 2.0 if unknown."""
        info = self.get_zone(zone_id_or_uri)
        return info.max_speed_mps if info else 2.0

    def get_clearance_limit(self, zone_id_or_uri: str) -> float:
        info = self.get_zone(zone_id_or_uri)
        return info.min_clearance_m if info else 0.3

    def all_zones(self) -> List[ZoneInfo]:
        """Return all zones from ontology."""
        zone_type = _RM + "Zone"
        uris = self._engine.subjects_of_type(zone_type)
        # Also look for subclasses by query
        if self._engine.is_rdflib():
            sparql = """
            PREFIX rm: <https://ontology.partenit.ai/robomind#>
            SELECT DISTINCT ?z WHERE {
                ?z a ?t .
                ?t rdfs:subClassOf* rm:Zone .
            }"""
            rows = self._engine.query(sparql)
            uris = list({r["z"] for r in rows} | set(uris))
        result = []
        for uri in uris:
            info = self.get_zone(uri)
            if info:
                result.append(info)
        return result

    def human_zones(self) -> List[ZoneInfo]:
        uri_type = _WH + "HumanZone"
        return [
            self.get_zone(u)
            for u in self._engine.subjects_of_type(uri_type)
            if self.get_zone(u)
        ]

    # ── Object management ─────────────────────────────────────────────────

    def add_object(
        self,
        obj_id: str,
        obj_type: str,
        label: str = "",
        zone_id: str = "",
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        properties: Optional[Dict[str, Any]] = None,
    ) -> str:
        uri = self._uri(obj_id)
        type_uri = self._type_uri(obj_type)
        props: Dict[str, Any] = {}
        if label:
            props[_RM + "label"] = label
        props[_RM + "posX"] = position[0]
        props[_RM + "posY"] = position[1]
        props[_RM + "posZ"] = position[2]
        if properties:
            props.update({k if k.startswith("http") else (_RM + k): v
                          for k, v in properties.items()})

        self._engine.add_entity(uri, type_uri, props)

        if zone_id:
            zone_uri = zone_id if zone_id.startswith("http") else self._uri(zone_id)
            self._engine.update_entity(uri, {_RM + "locatedIn": zone_uri})

        info = ObjectInfo(
            uri=uri, obj_type=obj_type, label=label or obj_id,
            zone_uri=zone_id, position=position,
            properties=properties or {},
        )
        self._object_cache[uri] = info
        return uri

    def update_object_position(
        self, obj_id_or_uri: str, position: Tuple[float, float, float]
    ) -> None:
        uri = obj_id_or_uri if obj_id_or_uri.startswith("http") else self._uri(obj_id_or_uri)
        self._engine.update_entity(uri, {
            _RM + "posX": position[0],
            _RM + "posY": position[1],
            _RM + "posZ": position[2],
        })
        if uri in self._object_cache:
            self._object_cache[uri] = ObjectInfo(
                **{**self._object_cache[uri].__dict__, "position": position}
            )

    def all_objects(self) -> List[ObjectInfo]:
        objs = []
        for uri, info in self._object_cache.items():
            objs.append(info)
        return objs

    # ── Constraints ───────────────────────────────────────────────────────

    def add_constraint(
        self, zone_id_or_uri: str, stl_formula: str,
        source: str = "operator", label: str = ""
    ) -> str:
        """Add an STL constraint to a zone."""
        zone_uri = (zone_id_or_uri if zone_id_or_uri.startswith("http")
                    else self._uri(zone_id_or_uri))
        rule_id = f"rule_{hash(stl_formula) & 0xFFFFFF:06x}"
        rule_uri = self._uri(rule_id)
        self._engine.add_entity(rule_uri, _RM + "ConstraintRule", {
            _RM + "stlFormula": stl_formula,
            _RM + "constraintSource": source,
            _RM + "label": label or stl_formula,
        })
        # Link zone → rule
        self._engine.update_entity(zone_uri, {_RM + "hasConstraint": rule_uri})
        if zone_uri in self._zone_cache:
            self._zone_cache[zone_uri].constraint_uris.append(rule_uri)
        return rule_uri

    def get_constraints_for_zone(self, zone_id_or_uri: str) -> List[Dict[str, str]]:
        zone_uri = (zone_id_or_uri if zone_id_or_uri.startswith("http")
                    else self._uri(zone_id_or_uri))
        if not self._engine.is_rdflib():
            return []
        sparql = f"""
        PREFIX rm: <{_RM}>
        SELECT ?rule ?formula ?source WHERE {{
            <{zone_uri}> rm:hasConstraint ?rule .
            ?rule rm:stlFormula ?formula .
            OPTIONAL {{ ?rule rm:constraintSource ?source }}
        }}"""
        rows = self._engine.query(sparql)
        return [{"uri": r["rule"], "formula": r["formula"],
                 "source": r.get("source", "")} for r in rows]

    # ── Snapshot / Transfer ───────────────────────────────────────────────

    def snapshot(self) -> bytes:
        """Export entire world model as OWL Turtle bytes."""
        return self._engine.export_bytes("turtle")

    def snapshot_hash(self) -> str:
        return self._engine.snapshot_hash()

    def merge_snapshot(self, owl_bytes: bytes) -> int:
        """Import and merge a snapshot from another robot."""
        other = OntologyEngine()
        other.load_text(owl_bytes.decode(errors="ignore"))
        return self._engine.merge(other)

    # ── From SLAM map ─────────────────────────────────────────────────────

    def from_slam_map(self, nav2_waypoints: List[Dict], annotations: List[Dict]) -> None:
        """Bootstrap world model from Nav2 waypoints + manual zone annotations.

        nav2_waypoints: [{"id": str, "x": float, "y": float, "z": float}]
        annotations: [{"id": str, "type": str, "waypoints": [str], ...}]
        """
        # Add zones from annotations
        for ann in annotations:
            zone_id = ann["id"]
            zone_type = ann.get("type", "Zone")
            self.add_zone(
                zone_id, zone_type,
                label=ann.get("label", zone_id),
                max_speed_mps=ann.get("max_speed_mps", 2.0),
                min_clearance_m=ann.get("min_clearance_m", 0.3),
                access_level=ann.get("access_level", 0),
            )

        # Add waypoints from Nav2
        for wp in nav2_waypoints:
            wp_uri = self._uri(wp["id"])
            self._engine.add_entity(wp_uri, _RM + "Waypoint", {
                _RM + "posX": float(wp.get("x", 0.0)),
                _RM + "posY": float(wp.get("y", 0.0)),
                _RM + "posZ": float(wp.get("z", 0.0)),
                _RM + "label": wp["id"],
            })

        logger.info("Bootstrapped world model: %d zones, %d waypoints",
                    len(annotations), len(nav2_waypoints))

    # ── Internal helpers ──────────────────────────────────────────────────

    def _uri(self, local_id: str) -> str:
        if local_id.startswith("http"):
            return local_id
        return f"{self._ns}{local_id}"

    def _type_uri(self, type_name: str) -> str:
        if type_name.startswith("http"):
            return type_name
        # Check known namespaces
        wh_types = {"StorageZone", "HumanZone", "TransitAisle", "LoadingDock",
                    "ChargingStation", "PackingStation", "Shelf", "ConveyorBelt",
                    "Door", "Pallet", "Worker", "Forklift"}
        if type_name in wh_types:
            return _WH + type_name
        return _RM + type_name

    @staticmethod
    def _extract_local(uri: str) -> str:
        for sep in ("#", "/"):
            if sep in uri:
                return uri.rsplit(sep, 1)[-1]
        return uri
