"""Local Knowledge Cache — offline-capable knowledge store.

Caches data from server (POIs, FAQ, zones, safety rules) and persists to disk.
Used by LocalBrain for Q&A and LocalNavigator for zone checks when server unavailable.

Sync strategy:
- On every successful server response: update relevant cache section
- Every 60s: background full sync attempt
- On startup: load from disk (survives restart)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class LocalKnowledgeCache:
    """Thread-safe knowledge cache with disk persistence."""

    SYNC_INTERVAL_S = 60.0
    CACHE_FILE = "knowledge_cache.json"

    def __init__(self, cache_dir: str = "/data/cache"):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = self._cache_dir / self.CACHE_FILE

        self._lock = threading.RLock()
        self._pois: Dict[str, dict] = {}
        self._faq: List[dict] = []
        self._zones: List[dict] = []
        self._safety_rules: List[dict] = []
        self._robot_profile: dict = {}
        self._last_sync: float = 0
        self._sync_thread: Optional[threading.Thread] = None
        self._running = False

        # Server URLs (set from main.py)
        self._knowledge_service_url = ""
        self._nlgw_url = ""
        self._safety_edge_url = ""

        self.load_from_disk()

    def configure(self, knowledge_url: str = "", nlgw_url: str = "", safety_url: str = ""):
        """Set server URLs for background sync."""
        self._knowledge_service_url = knowledge_url.rstrip("/")
        self._nlgw_url = nlgw_url.rstrip("/")
        self._safety_edge_url = safety_url.rstrip("/")

    # ── Data access ────────────────────────────────────────────────────

    def get_poi(self, poi_id: str) -> Optional[dict]:
        with self._lock:
            return self._pois.get(poi_id)

    def search_poi(self, query: str) -> List[dict]:
        """Simple substring search across POI names and aliases."""
        query_lower = query.lower()
        results = []
        with self._lock:
            for poi in self._pois.values():
                if query_lower in poi.get("name", "").lower():
                    results.append(poi)
                    continue
                for lang_aliases in poi.get("aliases", {}).values():
                    if isinstance(lang_aliases, list):
                        if any(query_lower in a.lower() for a in lang_aliases):
                            results.append(poi)
                            break
        return results

    def search_faq(self, question: str) -> List[dict]:
        """Simple word-overlap FAQ search."""
        q_words = set(question.lower().split())
        scored = []
        with self._lock:
            for entry in self._faq:
                entry_words = set(entry.get("question", "").lower().split())
                overlap = len(q_words & entry_words)
                if overlap > 0:
                    scored.append((overlap, entry))
        scored.sort(reverse=True, key=lambda x: x[0])
        return [s[1] for s in scored[:3]]

    def is_restricted_zone(self, x: float, y: float) -> bool:
        """Check if point is in a restricted zone."""
        with self._lock:
            for zone in self._zones:
                if zone.get("type") not in ("restricted", "no_entry"):
                    continue
                polygon = zone.get("polygon", [])
                if polygon and self._point_in_polygon(x, y, polygon):
                    return True
        return False

    def get_base_position(self) -> Optional[Dict[str, float]]:
        """Get base/home position from cached zones."""
        with self._lock:
            for zone in self._zones:
                if zone.get("type") in ("base", "home", "charging"):
                    center = zone.get("center", {})
                    if "x" in center and "y" in center:
                        return center
        return None

    @property
    def poi_count(self) -> int:
        with self._lock:
            return len(self._pois)

    @property
    def faq_count(self) -> int:
        with self._lock:
            return len(self._faq)

    @property
    def last_sync_age(self) -> float:
        return time.time() - self._last_sync if self._last_sync else float("inf")

    # ── Update from server ─────────────────────────────────────────────

    def update(self, data_type: str, data: Any):
        """Update cache section from server response."""
        with self._lock:
            if data_type == "pois" and isinstance(data, list):
                self._pois = {p.get("poi_id", p.get("id", str(i))): p
                             for i, p in enumerate(data)}
            elif data_type == "faq" and isinstance(data, list):
                self._faq = data
            elif data_type == "zones" and isinstance(data, list):
                self._zones = data
            elif data_type == "safety_rules" and isinstance(data, list):
                self._safety_rules = data
            elif data_type == "profile" and isinstance(data, dict):
                self._robot_profile = data
            self._last_sync = time.time()
        self._persist()

    # ── Background sync ────────────────────────────────────────────────

    def start_sync(self):
        """Start background sync thread."""
        if self._running:
            return
        self._running = True
        self._sync_thread = threading.Thread(
            target=self._sync_loop, daemon=True, name="cache-sync"
        )
        self._sync_thread.start()
        logger.info("Cache sync started (interval=%.0fs)", self.SYNC_INTERVAL_S)

    def stop_sync(self):
        self._running = False

    def sync_now(self):
        """Force immediate sync (called on reconnect)."""
        threading.Thread(target=self._do_sync, daemon=True).start()

    def _sync_loop(self):
        while self._running:
            self._do_sync()
            time.sleep(self.SYNC_INTERVAL_S)

    def _do_sync(self):
        """Attempt to fetch fresh data from all configured services."""
        synced = 0

        # POIs from knowledge_service
        if self._knowledge_service_url:
            data = self._fetch(f"{self._knowledge_service_url}/knowledge/poi")
            if data and "items" in data:
                self.update("pois", data["items"])
                synced += 1

        # FAQ — try knowledge packs
        if self._knowledge_service_url:
            data = self._fetch(f"{self._knowledge_service_url}/knowledge/faq")
            if data and isinstance(data, list):
                self.update("faq", data)
                synced += 1

        # Zones from onboarding wizard or profession
        if self._nlgw_url:
            data = self._fetch(f"{self._nlgw_url}/profession/zones")
            if data and isinstance(data, list):
                self.update("zones", data)
                synced += 1

        if synced:
            logger.debug("Cache sync: updated %d sections", synced)

    def _fetch(self, url: str, timeout: float = 3.0) -> Optional[dict]:
        """HTTP GET with timeout, returns parsed JSON or None."""
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    # ── Disk persistence ───────────────────────────────────────────────

    def _persist(self):
        """Save cache to disk."""
        try:
            with self._lock:
                data = {
                    "pois": self._pois,
                    "faq": self._faq,
                    "zones": self._zones,
                    "safety_rules": self._safety_rules,
                    "robot_profile": self._robot_profile,
                    "last_sync": self._last_sync,
                    "saved_at": time.time(),
                }
            with open(self._cache_path, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            logger.warning("Cache persist failed: %s", exc)

    def load_from_disk(self):
        """Load cache from disk (at startup)."""
        if not self._cache_path.exists():
            return
        try:
            with open(self._cache_path) as f:
                data = json.load(f)
            with self._lock:
                self._pois = data.get("pois", {})
                self._faq = data.get("faq", [])
                self._zones = data.get("zones", [])
                self._safety_rules = data.get("safety_rules", [])
                self._robot_profile = data.get("robot_profile", {})
                self._last_sync = data.get("last_sync", 0)
            age = time.time() - self._last_sync
            logger.info("Cache loaded from disk (age: %.0fs, %d POIs, %d FAQ, %d zones)",
                       age, len(self._pois), len(self._faq), len(self._zones))
        except Exception as exc:
            logger.warning("Cache load failed: %s", exc)

    def stats(self) -> dict:
        with self._lock:
            return {
                "pois": len(self._pois),
                "faq": len(self._faq),
                "zones": len(self._zones),
                "safety_rules": len(self._safety_rules),
                "last_sync_age_s": round(self.last_sync_age, 1),
                "cache_file": str(self._cache_path),
            }

    # ── Geometry helper ────────────────────────────────────────────────

    @staticmethod
    def _point_in_polygon(x: float, y: float, polygon: list) -> bool:
        """Ray casting point-in-polygon test."""
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i].get("x", 0), polygon[i].get("y", 0)
            xj, yj = polygon[j].get("x", 0), polygon[j].get("y", 0)
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-10) + xi):
                inside = not inside
            j = i
        return inside
