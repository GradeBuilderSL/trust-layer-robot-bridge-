"""trust-layer ontology library.

Provides OWL2 / SPARQL ontology management for semantic world modelling.
Core components:
  OntologyEngine   — load / query / update / merge / export OWL2 graphs
  WorldModel       — high-level semantic map API (zones, objects, constraints)
  SpatialGraph     — waypoint graph with semantic edge weights
  ConstraintStore  — zone → STL rule mapping
  KnowledgeDiff    — diff / merge between ontology snapshots
"""
from .engine import OntologyEngine
from .world_model import WorldModel
from .constraint_store import ConstraintStore
from .knowledge_diff import KnowledgeDiff

__all__ = [
    "OntologyEngine",
    "WorldModel",
    "ConstraintStore",
    "KnowledgeDiff",
]
