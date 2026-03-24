"""
Session management for conversation memory.
Extended with short-term memory manager.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from .memory import ShortTermMemoryManager, EntityTracker, MemoryChunk, EntityType

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """A single message in a conversation."""
    id: str
    content: str
    sender: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "sender": self.sender,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Message:
        msg = cls(
            id=data["id"],
            content=data["content"],
            sender=data["sender"],
            metadata=data.get("metadata", {}),
        )
        msg.timestamp = datetime.fromisoformat(data["timestamp"])
        return msg


@dataclass
class History:
    """Conversation history."""
    messages: List[Message] = field(default_factory=list)
    max_messages: int = 100

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        # Maintain max messages limit
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def get_recent_messages(self, count: int = 10) -> List[Message]:
        return self.messages[-count:] if self.messages else []

    def get_messages_since(self, timestamp: datetime) -> List[Message]:
        return [msg for msg in self.messages if msg.timestamp > timestamp]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "messages": [msg.to_dict() for msg in self.messages],
            "max_messages": self.max_messages,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> History:
        history = cls(max_messages=data.get("max_messages", 100))
        history.messages = [Message.from_dict(msg) for msg in data.get("messages", [])]
        return history


class Session:
    """A conversation session with memory management."""
    
    def __init__(self, session_id: str, robot_id: str = "", max_history: int = 100, **kwargs):
        self.session_id = session_id
        self.robot_id = robot_id
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.history = History(max_messages=max_history)
        self.metadata: Dict[str, Any] = kwargs
        
        # Memory management
        self.memory_manager = ShortTermMemoryManager()
        self.entity_tracker = EntityTracker()
    
    def add_message(self, message: Union[Message, Dict[str, Any]]) -> Message:
        """Add a message to the session."""
        if isinstance(message, dict):
            msg = Message(
                id=message.get("id", str(uuid.uuid4())),
                content=message["content"],
                sender=message["sender"],
                metadata=message.get("metadata", {}),
            )
            if "timestamp" in message:
                msg.timestamp = datetime.fromisoformat(message["timestamp"])
        else:
            msg = message
        
        self.history.add_message(msg)
        self.last_activity = datetime.now()
        
        # Add to memory manager for processing
        self.memory_manager.add_message(msg.to_dict())
        
        return msg
    
    async def add_message_with_memory(self, message: Union[Message, Dict[str, Any]], 
                                     llm_client=None) -> Message:
        """Add a message with automatic memory summarization."""
        msg = self.add_message(message)
        
        # Check if we should summarize
        if self.memory_manager.should_summarize() and llm_client:
            chunk = await self.memory_manager.summarize_chunk(llm_client)
            if chunk:
                logger.info(f"Created memory chunk {chunk.id} with {len(chunk.entities)} entities")
        
        return msg
    
    def get_recent_messages(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent messages as dictionaries."""
        return [msg.to_dict() for msg in self.history.get_recent_messages(count)]
    
    def get_context_with_memory(self, lookback: int = 5) -> Dict[str, Any]:
        """Get context including recent messages and memory chunks."""
        context = {
            "session_id": self.session_id,
            "robot_id": self.robot_id,
            "recent_messages": self.get_recent_messages(lookback),
            "memory_chunks": [chunk.to_dict() for chunk in self.memory_manager.memory_chunks[-3:]],
            "active_entities": [e.to_dict() for e in self.entity_tracker.get_all_entities()][-10:],
            "last_activity": self.last_activity.isoformat(),
            "memory_summary_ready": self.memory_manager.should_summarize(),
        }
        return context
    
    def get_entity_context(self, entity_id: str) -> Dict[str, Any]:
        """Get context for a specific entity."""
        return self.entity_tracker.get_entity_context(entity_id)
    
    def get_entities_by_type(self, entity_type: Union[str, EntityType]) -> List[Dict[str, Any]]:
        """Get entities filtered by type."""
        if isinstance(entity_type, str):
            entity_type = EntityType(entity_type)
        entities = self.entity_tracker.get_entities_by_type(entity_type)
        return [e.to_dict() for e in entities]
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize session to dictionary."""
        return {
            "session_id": self.session_id,
            "robot_id": self.robot_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "history": self.history.to_dict(),
            "memory_manager": self.memory_manager.to_dict(),
            "entity_tracker": self.entity_tracker.to_dict(),
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Session:
        """Create session from dictionary."""
        session = cls(
            session_id=data["session_id"],
            robot_id=data.get("robot_id", ""),
            max_history=data.get("history", {}).get("max_messages", 100),
        )
        session.created_at = datetime.fromisoformat(data["created_at"])
        session.last_activity = datetime.fromisoformat(data["last_activity"])
        session.history = History.from_dict(data.get("history", {}))
        session.memory_manager = ShortTermMemoryManager.from_dict(data.get("memory_manager", {}))
        session.entity_tracker = EntityTracker.from_dict(data.get("entity_tracker", {}))
        session.metadata = data.get("metadata", {})
        return session


class SessionManager:
    """Manages multiple conversation sessions."""
    
    def __init__(self):
        self.sessions: Dict[str, Session] = {}
        self.session_timeout_seconds = 3600  # 1 hour
    
    def get_session(self, session_id: str, robot_id: str = "") -> Session:
        """Get or create a session."""
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(session_id, robot_id)
        return self.sessions[session_id]
    
    def remove_session(self, session_id: str) -> None:
        """Remove a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]
    
    def cleanup_inactive_sessions(self) -> List[str]:
        """Remove inactive sessions and return list of removed session IDs."""
        now = datetime.now()
        to_remove = []
        
        for session_id, session in self.sessions.items():
            inactive_seconds = (now - session.last_activity).total_seconds()
            if inactive_seconds > self.session_timeout_seconds:
                to_remove.append(session_id)
        
        for session_id in to_remove:
            del self.sessions[session_id]
        
        return to_remove
    
    def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Get all sessions as dictionaries."""
        return [session.to_dict() for session in self.sessions.values()]