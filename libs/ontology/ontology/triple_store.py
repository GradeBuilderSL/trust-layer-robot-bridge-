"""
Triple store for ontology facts (subject-predicate-object) and fact retrieval.
"""
from __future__ import annotations

import logging
from typing import List, Tuple, Optional, Dict, Any
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class Triple:
    """A fact in triple store with metadata."""
    subject: str
    predicate: str
    obj: str
    timestamp: float
    confidence: float = 1.0
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class TripleStore:
    """In-memory triple store for testing. In production, replace with a real graph DB."""
    def __init__(self):
        self.triples: List[Triple] = []

    def add(self, subject: str, predicate: str, obj: str, 
            confidence: float = 1.0, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Add a triple with optional metadata."""
        triple = Triple(
            subject=subject,
            predicate=predicate,
            obj=obj,
            timestamp=time.time(),
            confidence=confidence,
            metadata=metadata or {}
        )
        self.triples.append(triple)

    def query(self, 
              subject: Optional[str] = None, 
              predicate: Optional[str] = None, 
              obj: Optional[str] = None,
              min_confidence: float = 0.0) -> List[Triple]:
        """Query triples matching the given pattern."""
        results = []
        for triple in self.triples:
            if triple.confidence < min_confidence:
                continue
            if subject is not None and triple.subject != subject:
                continue
            if predicate is not None and triple.predicate != predicate:
                continue
            if obj is not None and triple.obj != obj:
                continue
            results.append(triple)
        return results

    def get_entities_connected_to(self, entity: str, min_confidence: float = 0.5) -> List[str]:
        """Get all entities directly connected to the given entity."""
        connected = set()
        for triple in self.triples:
            if triple.confidence < min_confidence:
                continue
            if triple.subject == entity:
                connected.add(triple.obj)
            if triple.obj == entity:
                connected.add(triple.subject)
        return list(connected)


class FactRetriever:
    """Retrieves relevant facts from a triple store for given context entities."""
    
    def __init__(self, triple_store: TripleStore):
        self.store = triple_store
    
    def get_relevant_facts(self, context_entities: List[str], max_facts: int = 10) -> List[Triple]:
        """
        Retrieve facts relevant to the context entities.
        
        Strategy:
        1. Find all triples where context_entities are subject or object
        2. Rank by:
           - Direct connection to multiple context entities
           - Higher confidence
           - Recency (newer facts first)
        3. Return top max_facts
        """
        if not context_entities:
            return []
        
        # Collect all candidate triples
        candidates = []
        for entity in context_entities:
            # As subject
            candidates.extend(self.store.query(subject=entity, min_confidence=0.3))
            # As object
            candidates.extend(self.store.query(obj=entity, min_confidence=0.3))
        
        # Remove duplicates (by object identity)
        unique_candidates = []
        seen = set()
        for triple in candidates:
            triple_id = (triple.subject, triple.predicate, triple.obj)
            if triple_id not in seen:
                seen.add(triple_id)
                unique_candidates.append(triple)
        
        # Rank candidates
        ranked = sorted(
            unique_candidates,
            key=lambda t: self._compute_relevance_score(t, context_entities),
            reverse=True
        )
        
        # Return top facts
        return ranked[:max_facts]
    
    def _compute_relevance_score(self, triple: Triple, context_entities: List[str]) -> float:
        """Compute relevance score for a triple given context entities."""
        score = 0.0
        
        # Direct connection to context entities
        if triple.subject in context_entities:
            score += 2.0
        if triple.obj in context_entities:
            score += 2.0
        
        # Connection count bonus
        entity_count = sum(1 for e in context_entities if e in [triple.subject, triple.obj])
        if entity_count > 1:
            score += 3.0  # Bonus for connecting multiple entities
        
        # Confidence
        score += triple.confidence * 1.5
        
        # Recency (prefer newer facts)
        age_hours = (time.time() - triple.timestamp) / 3600
        recency_factor = max(0, 1.0 - (age_hours / 24.0))  # Decay over 24 hours
        score += recency_factor
        
        # Specific predicates bonus
        important_predicates = {
            'hasCapability', 'locatedIn', 'interactedWith', 'prefers',
            'avoids', 'requires', 'causes', 'prevents'
        }
        if triple.predicate in important_predicates:
            score += 1.0
        
        return score
    
    def get_formatted_facts(self, context_entities: List[str], max_facts: int = 10) -> str:
        """Get relevant facts formatted as a readable string for prompts."""
        facts = self.get_relevant_facts(context_entities, max_facts)
        if not facts:
            return ""
        
        lines = ["Relevant facts from memory:"]
        for i, triple in enumerate(facts, 1):
            confidence_str = f" (confidence: {triple.confidence:.2f})" if triple.confidence < 0.95 else ""
            lines.append(f"{i}. {triple.subject} {triple.predicate} {triple.obj}{confidence_str}")
        
        return "\n".join(lines)