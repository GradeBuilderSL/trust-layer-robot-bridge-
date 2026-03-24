"""Dialogue context manager for anaphora resolution."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from .world_model import Object3D


@dataclass
class DialogueTurn:
    """A single turn in a dialogue."""
    speaker: str  # "operator" or "system"
    text: str
    timestamp: float
    resolved_objects: List[Object3D] = field(default_factory=list)
    raw_objects: List[Object3D] = field(default_factory=list)


class DialogueContextManager:
    """Manages dialogue context for anaphora resolution."""
    
    def __init__(self, max_history: int = 10) -> None:
        self.history: List[DialogueTurn] = []
        self.max_history = max_history
        self.referenced_objects: Dict[str, Object3D] = {}
        self.last_action: Optional[str] = None

    def add_turn(
        self, 
        speaker: str, 
        text: str, 
        objects_from_perception: Optional[List[Object3D]] = None,
        resolved_objects: Optional[List[Object3D]] = None
    ) -> None:
        """Add a dialogue turn to the context."""
        turn = DialogueTurn(
            speaker=speaker,
            text=text,
            timestamp=time.time(),
            resolved_objects=resolved_objects or [],
            raw_objects=objects_from_perception or []
        )
        self.history.append(turn)
        
        # Update referenced objects cache
        if resolved_objects:
            for obj in resolved_objects:
                if obj.entity_id:
                    self.referenced_objects[obj.entity_id] = obj
        
        # Trim history if needed
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def get_last_n_turns(self, n: int = 3) -> List[DialogueTurn]:
        """Get the last n dialogue turns."""
        return self.history[-n:] if self.history else []

    def clear_context(self) -> None:
        """Clear the dialogue context."""
        self.history.clear()
        self.referenced_objects.clear()
        self.last_action = None

    def update_referenced_objects(self, objects: List[Object3D]) -> None:
        """Update the cache of referenced objects."""
        for obj in objects:
            if obj.entity_id:
                self.referenced_objects[obj.entity_id] = obj

    def get_recent_objects(self, seconds: float = 10.0) -> List[Object3D]:
        """Get objects mentioned in recent dialogue turns."""
        cutoff_time = time.time() - seconds
        recent_objects = []
        for turn in reversed(self.history):
            if turn.timestamp < cutoff_time:
                break
            recent_objects.extend(turn.resolved_objects)
            recent_objects.extend(turn.raw_objects)
        return recent_objects