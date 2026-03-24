"""SpatialGraph — topological + metric navigation graph with semantic edge weights.

Nodes are Waypoints in the ontology. Edges are NavigationEdge instances.
Shortest-path planning respects zone speed limits and access levels from
the WorldModel.

Usage:
    from ontology.spatial_graph import SpatialGraph

    sg = SpatialGraph(world_model)
    sg.add_waypoint("wp_a1", x=1.0, y=2.0, zone_id="zone_a1")
    sg.add_edge("wp_a1", "wp_a2", width_m=1.2)
    path = sg.shortest_safe_path("wp_a1", "wp_exit", robot_max_speed=1.5)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import networkx as nx
    _NX = True
except ImportError:
    _NX = False
    logger.warning("networkx not installed — SpatialGraph using simple BFS fallback. "
                   "Install: pip install networkx")


@dataclass
class WaypointNode:
    wp_id: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    zone_id: str = ""
    label: str = ""
    max_speed_mps: float = 2.0   # inherited from zone
    access_level: int = 0


@dataclass
class NavEdge:
    from_id: str
    to_id: str
    width_m: float = 1.0
    bidirectional: bool = True
    max_speed_mps: float = 2.0    # min of both endpoint zones
    weight: float = 0.0           # computed: distance × speed_cost


class SpatialGraph:
    """Semantic navigation graph backed by NetworkX (or BFS fallback)."""

    def __init__(self, world_model: Optional[Any] = None) -> None:
        self._wm = world_model
        self._waypoints: Dict[str, WaypointNode] = {}
        self._edges: List[NavEdge] = {}  # type: ignore[assignment]
        self._edges = []

        if _NX:
            self._graph = nx.DiGraph()
        else:
            self._graph = None
            self._adj: Dict[str, List[str]] = {}

    # ── Waypoints ─────────────────────────────────────────────────────────

    def add_waypoint(
        self, wp_id: str,
        x: float = 0.0, y: float = 0.0, z: float = 0.0,
        zone_id: str = "", label: str = "",
    ) -> None:
        max_speed = 2.0
        access = 0
        if self._wm and zone_id:
            zone = self._wm.get_zone(zone_id)
            if zone:
                max_speed = zone.max_speed_mps
                access = zone.access_level

        node = WaypointNode(
            wp_id=wp_id, x=x, y=y, z=z,
            zone_id=zone_id, label=label or wp_id,
            max_speed_mps=max_speed, access_level=access,
        )
        self._waypoints[wp_id] = node

        if _NX:
            self._graph.add_node(
                wp_id, x=x, y=y, z=z,
                zone_id=zone_id, max_speed=max_speed, access=access,
            )
        else:
            self._adj.setdefault(wp_id, [])

    def get_waypoint(self, wp_id: str) -> Optional[WaypointNode]:
        return self._waypoints.get(wp_id)

    # ── Edges ─────────────────────────────────────────────────────────────

    def add_edge(
        self, from_id: str, to_id: str,
        width_m: float = 1.0, bidirectional: bool = True,
    ) -> None:
        for fid, tid in ([(from_id, to_id)] +
                         ([(to_id, from_id)] if bidirectional else [])):
            w_a = self._waypoints.get(fid)
            w_b = self._waypoints.get(tid)
            if w_a is None or w_b is None:
                logger.warning("Edge %s→%s skipped: unknown waypoint", fid, tid)
                continue

            dist = math.sqrt(
                (w_b.x - w_a.x) ** 2 +
                (w_b.y - w_a.y) ** 2 +
                (w_b.z - w_a.z) ** 2
            )
            effective_speed = min(w_a.max_speed_mps, w_b.max_speed_mps)
            # Weight: time = dist / speed; penalise slow zones
            weight = dist / max(effective_speed, 0.01)

            edge = NavEdge(
                from_id=fid, to_id=tid,
                width_m=width_m, bidirectional=bidirectional,
                max_speed_mps=effective_speed, weight=weight,
            )
            self._edges.append(edge)

            if _NX:
                self._graph.add_edge(
                    fid, tid,
                    weight=weight,
                    width_m=width_m,
                    max_speed=effective_speed,
                )
            else:
                self._adj.setdefault(fid, []).append(tid)

    # ── Path planning ─────────────────────────────────────────────────────

    def shortest_safe_path(
        self,
        from_id: str,
        to_id: str,
        robot_max_speed: float = 2.0,
        robot_width_m: float = 0.5,
        skip_access_levels: Optional[List[int]] = None,
    ) -> Optional[List[str]]:
        """Return ordered list of waypoint IDs for the best safe path.

        Excludes edges too narrow for the robot or zones with forbidden access.
        If no path found, returns None.
        """
        forbidden_access = set(skip_access_levels or [3])  # forbidden by default

        if _NX:
            return self._nx_path(from_id, to_id, robot_max_speed,
                                 robot_width_m, forbidden_access)
        else:
            return self._bfs_path(from_id, to_id, robot_width_m, forbidden_access)

    def _nx_path(
        self, from_id: str, to_id: str,
        robot_max_speed: float, robot_width_m: float,
        forbidden_access: set,
    ) -> Optional[List[str]]:
        # Build a filtered subgraph
        def edge_ok(u, v, data) -> bool:
            if data.get("width_m", 1.0) < robot_width_m:
                return False
            w_v = self._waypoints.get(v)
            if w_v and w_v.access_level in forbidden_access:
                return False
            return True

        try:
            # Filter edges
            sg = nx.subgraph_view(
                self._graph,
                filter_edge=edge_ok,
            )
            path = nx.shortest_path(sg, from_id, to_id, weight="weight")
            return list(path)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def _bfs_path(
        self, from_id: str, to_id: str,
        robot_width_m: float, forbidden_access: set,
    ) -> Optional[List[str]]:
        """Simple BFS fallback when networkx is absent."""
        from collections import deque
        visited = {from_id}
        queue = deque([[from_id]])
        while queue:
            path = queue.popleft()
            node = path[-1]
            if node == to_id:
                return path
            for nxt in self._adj.get(node, []):
                if nxt in visited:
                    continue
                wp = self._waypoints.get(nxt)
                if wp and wp.access_level in forbidden_access:
                    continue
                visited.add(nxt)
                queue.append(path + [nxt])
        return None

    # ── Dynamic updates ───────────────────────────────────────────────────

    def replan_on_change(
        self, from_id: str, to_id: str,
        blocked_nodes: Optional[List[str]] = None,
        **kwargs,
    ) -> Optional[List[str]]:
        """Replan path avoiding currently blocked nodes (e.g. human entered zone)."""
        if blocked_nodes:
            # Temporarily mark blocked
            orig_access = {}
            for nid in blocked_nodes:
                if nid in self._waypoints:
                    orig_access[nid] = self._waypoints[nid].access_level
                    self._waypoints[nid].access_level = 3  # forbidden
                    if _NX:
                        self._graph.nodes[nid]["access"] = 3

        result = self.shortest_safe_path(from_id, to_id, **kwargs)

        # Restore
        if blocked_nodes:
            for nid, acc in orig_access.items():
                self._waypoints[nid].access_level = acc
                if _NX:
                    self._graph.nodes[nid]["access"] = acc

        return result

    def __len__(self) -> int:
        return len(self._waypoints)
