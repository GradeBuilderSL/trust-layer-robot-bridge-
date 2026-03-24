"""
MemoryEntry - структура для записей долгосрочной памяти робота.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum
import uuid
import json


class MemoryType(Enum):
    """Типы записей памяти."""
    EVENT = "event"
    OBSERVATION = "observation"
    LEARNED_SKILL = "learned_skill"
    HUMAN_INTERACTION = "human_interaction"
    SYSTEM_EVENT = "system_event"
    DECISION = "decision"
    PERCEPTION = "perception"


class MemoryPriority(Enum):
    """Приоритеты записей памяти."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class MemoryEntry:
    """Запись долгосрочной памяти робота."""
    id: str
    timestamp: datetime
    memory_type: MemoryType
    content: Dict[str, Any]
    priority: MemoryPriority = MemoryPriority.MEDIUM
    tags: List[str] = None
    robot_id: Optional[str] = None
    location: Optional[str] = None
    expires_at: Optional[datetime] = None
    is_archived: bool = False
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь для сериализации."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "memory_type": self.memory_type.value,
            "content": self.content,
            "priority": self.priority.value,
            "tags": self.tags,
            "robot_id": self.robot_id,
            "location": self.location,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_archived": self.is_archived
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemoryEntry':
        """Создание из словаря."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            memory_type=MemoryType(data["memory_type"]),
            content=data["content"],
            priority=MemoryPriority(data.get("priority", 2)),
            tags=data.get("tags", []),
            robot_id=data.get("robot_id"),
            location=data.get("location"),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            is_archived=data.get("is_archived", False)
        )
    
    @classmethod
    def create(
        cls,
        memory_type: MemoryType,
        content: Dict[str, Any],
        robot_id: Optional[str] = None,
        **kwargs
    ) -> 'MemoryEntry':
        """Создание новой записи памяти."""
        return cls(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            memory_type=memory_type,
            content=content,
            robot_id=robot_id,
            **kwargs
        )