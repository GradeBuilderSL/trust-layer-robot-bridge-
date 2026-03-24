"""
Short-term memory manager for conversation memory.
Provides automatic summarization of old messages via LLM and entity tracking.
L2b layer: Uses LLM via trust_edge for summarization.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class EntityType(Enum):
    """Types of entities that can be tracked in conversations."""
    PERSON = "person"
    OBJECT = "object"
    LOCATION = "location"
    ACTION = "action"
    EVENT = "event"
    GOAL = "goal"
    ROBOT = "robot"
    TASK = "task"


@dataclass
class Entity:
    """An entity tracked in the conversation."""
    id: str
    type: EntityType
    name: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    first_seen: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    mention_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert entity to dictionary for serialization."""
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "attributes": self.attributes,
            "first_seen": self.first_seen.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "confidence": self.confidence,
            "metadata": self.metadata,
            "mention_count": self.mention_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Entity:
        """Create entity from dictionary."""
        entity = cls(
            id=data["id"],
            type=EntityType(data["type"]),
            name=data["name"],
            attributes=data.get("attributes", {}),
            confidence=data.get("confidence", 1.0),
            metadata=data.get("metadata", {}),
            mention_count=data.get("mention_count", 1),
        )
        entity.first_seen = datetime.fromisoformat(data["first_seen"])
        entity.last_updated = datetime.fromisoformat(data["last_updated"])
        return entity


@dataclass
class MemoryChunk:
    """A summarized chunk of conversation history."""
    id: str
    summary: str
    entities: List[Entity]
    start_time: datetime
    end_time: datetime
    message_ids: List[str] = field(default_factory=list)
    importance_score: float = 0.5
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert memory chunk to dictionary."""
        return {
            "id": self.id,
            "summary": self.summary,
            "entities": [e.to_dict() for e in self.entities],
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "message_ids": self.message_ids,
            "importance_score": self.importance_score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MemoryChunk:
        """Create memory chunk from dictionary."""
        chunk = cls(
            id=data["id"],
            summary=data["summary"],
            entities=[Entity.from_dict(e) for e in data["entities"]],
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=datetime.fromisoformat(data["end_time"]),
            message_ids=data.get("message_ids", []),
            importance_score=data.get("importance_score", 0.5),
            metadata=data.get("metadata", {}),
        )
        return chunk


class EntityTracker:
    """Tracks entities mentioned in conversations."""
    
    def __init__(self):
        self.entities: Dict[str, Entity] = {}
        self.entity_mentions: Dict[str, List[datetime]] = {}
        self.entity_aliases: Dict[str, Set[str]] = {}
    
    def update_entity(self, entity_id: str, entity_type: EntityType, 
                     name: str, attributes: Dict[str, Any], confidence: float = 1.0) -> Entity:
        """Update or create an entity."""
        now = datetime.now()
        
        if entity_id in self.entities:
            # Update existing entity
            entity = self.entities[entity_id]
            entity.name = name or entity.name
            entity.type = entity_type or entity.type
            entity.attributes.update(attributes)
            entity.last_updated = now
            entity.confidence = max(entity.confidence, confidence)
            entity.mention_count += 1
        else:
            # Create new entity
            entity = Entity(
                id=entity_id,
                type=entity_type,
                name=name,
                attributes=attributes,
                confidence=confidence,
                mention_count=1,
            )
            self.entities[entity_id] = entity
            self.entity_mentions[entity_id] = [now]
        
        # Track mention
        if entity_id in self.entity_mentions:
            self.entity_mentions[entity_id].append(now)
        else:
            self.entity_mentions[entity_id] = [now]
        
        return entity
    
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get entity by ID."""
        return self.entities.get(entity_id)
    
    def get_entities_by_type(self, entity_type: EntityType) -> List[Entity]:
        """Get all entities of a specific type."""
        return [e for e in self.entities.values() if e.type == entity_type]
    
    def get_entity_context(self, entity_id: str) -> Dict[str, Any]:
        """Get context information for an entity."""
        entity = self.get_entity(entity_id)
        if not entity:
            return {}
        
        mentions = self.entity_mentions.get(entity_id, [])
        return {
            "entity": entity.to_dict(),
            "mention_count": len(mentions),
            "first_mentioned": min(mentions).isoformat() if mentions else None,
            "last_mentioned": max(mentions).isoformat() if mentions else None,
            "mentions": [m.isoformat() for m in mentions[-10:]],  # Last 10 mentions
        }
    
    def get_all_entities(self) -> List[Entity]:
        """Get all tracked entities."""
        return list(self.entities.values())
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize entity tracker to dictionary."""
        return {
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "entity_mentions": {k: [m.isoformat() for m in v] 
                              for k, v in self.entity_mentions.items()},
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EntityTracker:
        """Create entity tracker from dictionary."""
        tracker = cls()
        tracker.entities = {k: Entity.from_dict(v) for k, v in data.get("entities", {}).items()}
        
        # Restore mentions
        for entity_id, mentions in data.get("entity_mentions", {}).items():
            tracker.entity_mentions[entity_id] = [datetime.fromisoformat(m) for m in mentions]
        
        return tracker


class ShortTermMemoryManager:
    """Manages short-term conversation memory with automatic summarization."""
    
    def __init__(self, max_chunks: int = 10, chunk_size: int = 20):
        self.memory_chunks: List[MemoryChunk] = []
        self.entity_tracker = EntityTracker()
        self.max_chunks = max_chunks
        self.chunk_size = chunk_size
        self.current_chunk_messages: List[Dict[str, Any]] = []
        self.last_summary_time = datetime.now()
    
    def add_message(self, message: Dict[str, Any]) -> None:
        """Add a message to the current chunk."""
        self.current_chunk_messages.append(message)
        
        # Extract entities from message
        self._extract_entities_from_message(message)
    
    def _extract_entities_from_message(self, message: Dict[str, Any]) -> None:
        """Extract entities from a message content."""
        content = message.get("content", "")
        sender = message.get("sender", "")
        
        # Simple entity extraction (can be enhanced with NLP)
        if sender and sender != "system":
            entity_id = f"person_{sender.lower()}"
            self.entity_tracker.update_entity(
                entity_id=entity_id,
                entity_type=EntityType.PERSON,
                name=sender,
                attributes={"role": "participant"},
                confidence=0.9,
            )
        
        # Check for common entity patterns (simplified)
        import re
        patterns = [
            (r'\b(robot|bot)\b', EntityType.ROBOT, {"type": "robot"}),
            (r'\b(move|go|travel)\b', EntityType.ACTION, {"action_type": "movement"}),
            (r'\b(box|package|object)\b', EntityType.OBJECT, {"movable": True}),
            (r'\b(room|area|location)\b', EntityType.LOCATION, {}),
            (r'\b(task|job|mission)\b', EntityType.TASK, {}),
        ]
        
        for pattern, entity_type, attributes in patterns:
            matches = re.findall(pattern, content.lower())
            for match in matches:
                entity_id = f"{entity_type.value}_{match}_{uuid.uuid4().hex[:8]}"
                self.entity_tracker.update_entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    name=match,
                    attributes=attributes,
                    confidence=0.7,
                )
    
    def should_summarize(self) -> bool:
        """Check if we should summarize the current chunk."""
        if len(self.current_chunk_messages) >= self.chunk_size:
            return True
        
        # Check time-based summarization (if last summary was more than 5 minutes ago)
        time_since_last = (datetime.now() - self.last_summary_time).total_seconds()
        if time_since_last > 300 and len(self.current_chunk_messages) > 0:  # 5 minutes
            return True
        
        return False
    
    async def summarize_chunk(self, llm_client=None) -> Optional[MemoryChunk]:
        """Summarize the current chunk of messages."""
        if not self.current_chunk_messages:
            return None
        
        # Prepare messages for summarization
        messages = []
        message_ids = []
        
        for msg in self.current_chunk_messages:
            messages.append({
                "id": msg.get("id", ""),
                "sender": msg.get("sender", ""),
                "content": msg.get("content", ""),
                "timestamp": msg.get("timestamp", datetime.now().isoformat()),
            })
            message_ids.append(msg.get("id", ""))
        
        # Create summary
        summary = await self._create_summary(messages, llm_client)
        
        # Create memory chunk
        start_time = datetime.fromisoformat(self.current_chunk_messages[0].get("timestamp", datetime.now().isoformat()))
        end_time = datetime.fromisoformat(self.current_chunk_messages[-1].get("timestamp", datetime.now().isoformat()))
        
        chunk = MemoryChunk(
            id=f"chunk_{uuid.uuid4().hex[:16]}",
            summary=summary,
            entities=self.entity_tracker.get_all_entities(),
            start_time=start_time,
            end_time=end_time,
            message_ids=message_ids,
            importance_score=self._calculate_importance(messages),
        )
        
        # Add to memory chunks
        self.memory_chunks.append(chunk)
        
        # Maintain max chunks limit
        if len(self.memory_chunks) > self.max_chunks:
            self.memory_chunks = self.memory_chunks[-self.max_chunks:]
        
        # Clear current chunk
        self.current_chunk_messages = []
        self.last_summary_time = datetime.now()
        
        return chunk
    
    async def _create_summary(self, messages: List[Dict[str, Any]], llm_client=None) -> str:
        """Create a summary of messages using LLM or fallback method."""
        if llm_client:
            try:
                # Try to use LLM for better summarization
                return await self._summarize_with_llm(messages, llm_client)
            except Exception as e:
                logger.warning(f"LLM summarization failed: {e}. Using fallback.")
        
        # Fallback: simple concatenation of key points
        return self._summarize_fallback(messages)
    
    async def _summarize_with_llm(self, messages: List[Dict[str, Any]], llm_client) -> str:
        """Summarize messages using LLM via trust_edge."""
        # This will be called from decision_log which will make HTTP request to trust_edge
        # For now, return placeholder
        return "LLM-based summary (would be generated via trust_edge)"
    
    def _summarize_fallback(self, messages: List[Dict[str, Any]]) -> str:
        """Fallback summarization method."""
        if not messages:
            return "No messages to summarize."
        
        participants = set(msg.get("sender", "") for msg in messages if msg.get("sender"))
        participant_str = ", ".join(sorted(participants)) if participants else "Unknown"
        
        topics = []
        for msg in messages[-5:]:  # Last 5 messages
            content = msg.get("content", "")
            if len(content) > 50:
                topics.append(content[:50] + "...")
            else:
                topics.append(content)
        
        return f"Conversation between {participant_str}. Recent topics: {' | '.join(topics)}"
    
    def _calculate_importance(self, messages: List[Dict[str, Any]]) -> float:
        """Calculate importance score for the chunk."""
        if not messages:
            return 0.0
        
        # Simple importance calculation based on message count and entity mentions
        base_score = min(len(messages) / self.chunk_size, 1.0)
        
        # Boost score if many entities mentioned
        entity_count = len(self.entity_tracker.get_all_entities())
        entity_score = min(entity_count / 10.0, 0.3)  # Max 0.3 boost
        
        return min(base_score + entity_score, 1.0)
    
    def get_context(self, lookback_messages: int = 5) -> Dict[str, Any]:
        """Get current context including recent messages and memory."""
        recent_messages = self.current_chunk_messages[-lookback_messages:] if self.current_chunk_messages else []
        
        return {
            "recent_messages": recent_messages,
            "memory_chunks": [chunk.to_dict() for chunk in self.memory_chunks[-3:]],  # Last 3 chunks
            "active_entities": [e.to_dict() for e in self.entity_tracker.get_all_entities()][-10:],  # Last 10 entities
            "current_chunk_size": len(self.current_chunk_messages),
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize memory manager to dictionary."""
        return {
            "memory_chunks": [chunk.to_dict() for chunk in self.memory_chunks],
            "entity_tracker": self.entity_tracker.to_dict(),
            "current_chunk_messages": self.current_chunk_messages,
            "last_summary_time": self.last_summary_time.isoformat(),
            "config": {
                "max_chunks": self.max_chunks,
                "chunk_size": self.chunk_size,
            },
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ShortTermMemoryManager:
        """Create memory manager from dictionary."""
        config = data.get("config", {})
        manager = cls(
            max_chunks=config.get("max_chunks", 10),
            chunk_size=config.get("chunk_size", 20),
        )
        
        manager.memory_chunks = [MemoryChunk.from_dict(c) for c in data.get("memory_chunks", [])]
        manager.entity_tracker = EntityTracker.from_dict(data.get("entity_tracker", {}))
        manager.current_chunk_messages = data.get("current_chunk_messages", [])
        
        last_summary = data.get("last_summary_time")
        if last_summary:
            manager.last_summary_time = datetime.fromisoformat(last_summary)
        
        return manager