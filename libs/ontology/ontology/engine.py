"""OntologyEngine — OWL2 + SPARQL graph manager.

Uses rdflib when available; falls back to a lightweight dict-based store
so the module is importable without extra dependencies in all environments.

Usage:
    from ontology.engine import OntologyEngine

    eng = OntologyEngine()
    eng.load("libs/ontology/schemas/warehouse.owl")
    rows = eng.query("SELECT ?s WHERE { ?s a <.../Zone> }")
    eng.update_entity("wh:ZoneA1", {"rm:maxSpeedMps": 0.5})
    eng.export("/tmp/snapshot.ttl")
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Optional rdflib import ────────────────────────────────────────────────
try:
    import rdflib
    from rdflib import Graph, URIRef, Literal, Namespace, RDF, RDFS, OWL, XSD
    from rdflib.plugins.sparql import prepareQuery
    _RDFLIB = True
except ImportError:
    _RDFLIB = False
    logger.warning("rdflib not installed — OntologyEngine using dict fallback. "
                   "Install: pip install rdflib")

# Well-known namespaces (used as string constants in fallback mode too)
NS_RM  = "https://ontology.partenit.ai/robomind#"
NS_WH  = "https://ontology.partenit.ai/warehouse#"
NS_OD  = "https://ontology.partenit.ai/outdoor#"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


# ── Fallback triple store ─────────────────────────────────────────────────

class _DictStore:
    """Minimal in-memory triple store for environments without rdflib."""

    def __init__(self) -> None:
        # {subject: {predicate: [object, ...]}}
        self._triples: Dict[str, Dict[str, List[str]]] = {}
        self._lock = threading.Lock()

    def add(self, s: str, p: str, o: str) -> None:
        with self._lock:
            self._triples.setdefault(s, {}).setdefault(p, [])
            if o not in self._triples[s][p]:
                self._triples[s][p].append(o)

    def remove(self, s: str, p: str, o: Optional[str] = None) -> None:
        with self._lock:
            if s not in self._triples:
                return
            if o is None:
                self._triples[s].pop(p, None)
            elif p in self._triples[s]:
                self._triples[s][p] = [v for v in self._triples[s][p] if v != o]

    def subjects(self, predicate: Optional[str] = None,
                 obj: Optional[str] = None) -> Iterator[str]:
        with self._lock:
            for s, preds in self._triples.items():
                if predicate is None and obj is None:
                    yield s
                    continue
                if predicate and predicate not in preds:
                    continue
                vals = preds.get(predicate, [])
                if obj is None or obj in vals:
                    yield s

    def objects(self, subject: str, predicate: str) -> List[str]:
        with self._lock:
            return list(self._triples.get(subject, {}).get(predicate, []))

    def triples(self) -> List[Tuple[str, str, str]]:
        result = []
        with self._lock:
            for s, preds in self._triples.items():
                for p, objs in preds.items():
                    for o in objs:
                        result.append((s, p, o))
        return result

    def __len__(self) -> int:
        return sum(len(os) for ps in self._triples.values() for os in ps.values())

    def merge(self, other: "_DictStore") -> None:
        for s, p, o in other.triples():
            self.add(s, p, o)

    def export_turtle(self) -> str:
        lines = ["# Turtle serialization (fallback mode)"]
        for s, p, o in self.triples():
            lines.append(f'<{s}> <{p}> "{o}" .')
        return "\n".join(lines)

    def import_turtle(self, text: str) -> int:
        """Very basic Turtle parser — reads lines of form <s> <p> "o" ."""
        imported = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                parts = line.rstrip(".").split("> <")
                if len(parts) == 2:
                    s = parts[0].lstrip("<")
                    rest = parts[1]
                    p_end = rest.find(">")
                    p = rest[:p_end]
                    o = rest[p_end + 2:].strip().strip('"')
                    self.add(s, p, o)
                    imported += 1
            except Exception:
                pass
        return imported


# ── OntologyEngine ────────────────────────────────────────────────────────

class OntologyEngine:
    """OWL2 ontology engine with SPARQL support (rdflib) and dict fallback."""

    def __init__(self, base_uri: str = "https://ontology.partenit.ai/world") -> None:
        self._base_uri = base_uri
        self._lock = threading.RLock()
        self._subscribers: List[Tuple[str, Callable]] = []
        self._change_log: List[Dict[str, Any]] = []

        if _RDFLIB:
            self._graph = Graph()
            self._graph.bind("rm", Namespace(NS_RM))
            self._graph.bind("wh", Namespace(NS_WH))
            self._graph.bind("od", Namespace(NS_OD))
            self._fallback: Optional[_DictStore] = None
        else:
            self._graph = None
            self._fallback = _DictStore()

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, path: str, format: str = "turtle") -> int:
        """Load an OWL/Turtle file. Returns number of triples added."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Ontology file not found: {path}")

        with self._lock:
            if _RDFLIB:
                before = len(self._graph)
                self._graph.parse(str(p), format=format)
                added = len(self._graph) - before
            else:
                added = self._fallback.import_turtle(p.read_text())

        logger.info("Loaded %d triples from %s", added, path)
        return added

    def load_text(self, text: str, format: str = "turtle") -> int:
        """Load ontology from a string."""
        with self._lock:
            if _RDFLIB:
                before = len(self._graph)
                self._graph.parse(data=text, format=format)
                added = len(self._graph) - before
            else:
                added = self._fallback.import_turtle(text)
        return added

    # ── Querying ──────────────────────────────────────────────────────────

    def query(self, sparql: str) -> List[Dict[str, str]]:
        """Execute SPARQL SELECT query. Returns list of {var: value} dicts."""
        if not _RDFLIB:
            logger.debug("SPARQL requires rdflib — returning empty (fallback mode)")
            return []
        with self._lock:
            try:
                results = self._graph.query(sparql)
                rows = []
                for row in results:
                    rows.append({
                        str(var): str(row[var]) if row[var] is not None else ""
                        for var in results.vars
                    })
                return rows
            except Exception as exc:
                logger.warning("SPARQL query failed: %s", exc)
                return []

    def subjects_of_type(self, type_uri: str) -> List[str]:
        """Return all subject URIs that are instances of type_uri."""
        if _RDFLIB:
            rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
            t = URIRef(type_uri)
            with self._lock:
                return [str(s) for s in self._graph.subjects(rdf_type, t)]
        else:
            return list(self._fallback.subjects(NS_RDF + "type", type_uri))

    def get_properties(self, subject_uri: str) -> Dict[str, List[str]]:
        """Return all (predicate → [objects]) for given subject URI."""
        result: Dict[str, List[str]] = {}
        if _RDFLIB:
            s = URIRef(subject_uri)
            with self._lock:
                for p, o in self._graph.predicate_objects(s):
                    key = str(p)
                    result.setdefault(key, []).append(str(o))
        else:
            with self._lock:
                for p, objs in self._fallback._triples.get(subject_uri, {}).items():
                    result[p] = list(objs)
        return result

    # ── Updating ──────────────────────────────────────────────────────────

    def update_entity(self, uri: str, properties: Dict[str, Any]) -> None:
        """Set/overwrite datatype properties on an entity.

        uri: full URI string
        properties: {predicate_uri: value}
        """
        with self._lock:
            if _RDFLIB:
                s = URIRef(uri)
                for pred_uri, value in properties.items():
                    p = URIRef(pred_uri)
                    # Remove old values
                    self._graph.remove((s, p, None))
                    # Add new value
                    if isinstance(value, float):
                        self._graph.add((s, p, Literal(value, datatype=XSD.double)))
                    elif isinstance(value, int):
                        self._graph.add((s, p, Literal(value, datatype=XSD.integer)))
                    elif isinstance(value, bool):
                        self._graph.add((s, p, Literal(value, datatype=XSD.boolean)))
                    else:
                        self._graph.add((s, p, Literal(str(value))))
            else:
                for pred_uri, value in properties.items():
                    self._fallback.remove(uri, pred_uri)
                    self._fallback.add(uri, pred_uri, str(value))

        self._record_change("update", uri, properties)
        self._notify_subscribers(uri, "update", properties)

    def add_entity(self, uri: str, type_uri: str,
                   properties: Optional[Dict[str, Any]] = None) -> None:
        """Add a new entity with given type and optional properties."""
        with self._lock:
            rdf_type = NS_RDF + "type"
            if _RDFLIB:
                s = URIRef(uri)
                t = URIRef(type_uri)
                self._graph.add((s, RDF.type, t))
            else:
                self._fallback.add(uri, rdf_type, type_uri)

        if properties:
            self.update_entity(uri, properties)

        self._record_change("add", uri, {"type": type_uri, **(properties or {})})
        self._notify_subscribers(uri, "add", {"type": type_uri})

    def remove_entity(self, uri: str) -> None:
        """Remove all triples for the given subject URI."""
        with self._lock:
            if _RDFLIB:
                s = URIRef(uri)
                self._graph.remove((s, None, None))
            else:
                self._fallback._triples.pop(uri, None)
        self._record_change("remove", uri, {})

    # ── Merge ─────────────────────────────────────────────────────────────

    def merge(self, other: "OntologyEngine") -> int:
        """Merge another OntologyEngine into this one. Returns triples added."""
        before = len(self)
        with self._lock:
            if _RDFLIB and other._graph is not None:
                for triple in other._graph:
                    self._graph.add(triple)
            elif self._fallback is not None and other._fallback is not None:
                self._fallback.merge(other._fallback)
        added = len(self) - before
        logger.info("Merged %d new triples", added)
        return added

    # ── Export / Import ───────────────────────────────────────────────────

    def export(self, path: str, format: str = "turtle") -> None:
        """Serialize ontology to file."""
        with self._lock:
            if _RDFLIB:
                self._graph.serialize(destination=path, format=format)
            else:
                Path(path).write_text(self._fallback.export_turtle())
        logger.info("Exported ontology to %s", path)

    def export_bytes(self, format: str = "turtle") -> bytes:
        """Serialize ontology to bytes."""
        with self._lock:
            if _RDFLIB:
                return self._graph.serialize(format=format).encode()
            else:
                return self._fallback.export_turtle().encode()

    def snapshot_hash(self) -> str:
        """SHA-256 of the canonical serialization (for versioning)."""
        data = self.export_bytes()
        return hashlib.sha256(data).hexdigest()

    # ── Change subscriptions ──────────────────────────────────────────────

    def subscribe(self, pattern: str, callback: Callable) -> None:
        """Register callback for entity changes matching pattern (URI prefix)."""
        self._subscribers.append((pattern, callback))

    def _notify_subscribers(self, uri: str, action: str,
                            props: Dict[str, Any]) -> None:
        for pattern, cb in self._subscribers:
            if uri.startswith(pattern):
                try:
                    cb(uri=uri, action=action, properties=props)
                except Exception as exc:
                    logger.warning("Subscriber callback error: %s", exc)

    def _record_change(self, action: str, uri: str,
                       props: Dict[str, Any]) -> None:
        self._change_log.append({
            "ts": time.time(),
            "action": action,
            "uri": uri,
            "props": props,
        })
        if len(self._change_log) > 1000:
            self._change_log = self._change_log[-500:]

    # ── Helpers ───────────────────────────────────────────────────────────

    def __len__(self) -> int:
        if _RDFLIB:
            return len(self._graph)
        return len(self._fallback)

    def triple_count(self) -> int:
        return len(self)

    def is_rdflib(self) -> bool:
        return _RDFLIB
