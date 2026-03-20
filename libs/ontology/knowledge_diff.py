"""KnowledgeDiff — diff and merge between two OntologyEngine snapshots.

Used during knowledge transfer between robots: identify what the receiving
robot already knows vs what's new, and merge without duplicates.

Usage:
    from ontology.knowledge_diff import KnowledgeDiff

    diff = KnowledgeDiff(base_engine, incoming_engine)
    report = diff.compute()
    print(report.added_count, report.modified_count, report.conflict_count)
    diff.apply(base_engine)   # merge non-conflicting additions
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from ontology.engine import OntologyEngine

logger = logging.getLogger(__name__)


@dataclass
class DiffReport:
    added_triples: List[Tuple[str, str, str]] = field(default_factory=list)
    removed_triples: List[Tuple[str, str, str]] = field(default_factory=list)
    modified_entities: List[str] = field(default_factory=list)
    conflict_entities: List[str] = field(default_factory=list)

    @property
    def added_count(self) -> int:
        return len(self.added_triples)

    @property
    def removed_count(self) -> int:
        return len(self.removed_triples)

    @property
    def modified_count(self) -> int:
        return len(self.modified_entities)

    @property
    def conflict_count(self) -> int:
        return len(self.conflict_entities)

    def summary(self) -> str:
        return (f"DiffReport: +{self.added_count} -{self.removed_count} "
                f"~{self.modified_count} conflicts={self.conflict_count}")


class KnowledgeDiff:
    """Compares two OntologyEngine instances and prepares a merge plan."""

    def __init__(self, base: OntologyEngine, incoming: OntologyEngine) -> None:
        self._base = base
        self._incoming = incoming
        self._report: DiffReport | None = None

    def compute(self) -> DiffReport:
        """Compute diff between base and incoming ontologies."""
        report = DiffReport()

        if self._base.is_rdflib() and self._incoming.is_rdflib():
            report = self._rdflib_diff()
        else:
            report = self._fallback_diff()

        self._report = report
        logger.info("Diff computed: %s", report.summary())
        return report

    def apply(self, target: OntologyEngine, resolve_conflicts: str = "incoming") -> int:
        """Apply computed diff to target engine.

        resolve_conflicts: "incoming" (take new value) | "base" (keep original) | "skip"
        Returns number of triples applied.
        """
        if self._report is None:
            self.compute()

        applied = 0
        for triple in self._report.added_triples:
            try:
                target.add_entity(triple[0], triple[1], {triple[1]: triple[2]})
                applied += 1
            except Exception:
                pass

        if resolve_conflicts in ("incoming", "both"):
            for entity_uri in self._report.conflict_entities:
                incoming_props = self._incoming.get_properties(entity_uri)
                if incoming_props:
                    target.update_entity(entity_uri, {
                        k: v[0] for k, v in incoming_props.items()
                    })
                    applied += 1

        return applied

    def merge_into_base(self, resolve_conflicts: str = "incoming") -> int:
        """Convenience: compute diff and apply to base engine."""
        self.compute()
        return self.apply(self._base, resolve_conflicts)

    # ── Internal ──────────────────────────────────────────────────────────

    def _rdflib_diff(self) -> DiffReport:
        report = DiffReport()

        # Get triple sets as (s, p, o) string tuples
        base_triples = self._get_triple_set(self._base)
        incoming_triples = self._get_triple_set(self._incoming)

        added = incoming_triples - base_triples
        removed = base_triples - incoming_triples

        report.added_triples = list(added)
        report.removed_triples = list(removed)

        # Find entities that changed (appear in both add and remove for same subject+predicate)
        base_sp = {(s, p): o for s, p, o in base_triples}
        for s, p, o in added:
            if (s, p) in base_sp and base_sp[(s, p)] != o:
                report.modified_entities.append(s)
                # Check for conflict: both changed
                if s not in report.conflict_entities:
                    report.conflict_entities.append(s)

        return report

    def _fallback_diff(self) -> DiffReport:
        """Dict-store based diff."""
        report = DiffReport()

        if self._base._fallback is None or self._incoming._fallback is None:
            return report

        base_set = set(self._base._fallback.triples())
        incoming_set = set(self._incoming._fallback.triples())

        report.added_triples = list(incoming_set - base_set)
        report.removed_triples = list(base_set - incoming_set)

        return report

    def _get_triple_set(self, eng: OntologyEngine) -> Set[Tuple[str, str, str]]:
        """Get all triples as a set of (s, p, o) string tuples."""
        if eng._graph is None:
            if eng._fallback:
                return {(s, p, o) for s, p, o in eng._fallback.triples()}
            return set()
        return {(str(s), str(p), str(o)) for s, p, o in eng._graph}
