"""Progress reporting data structures for composite action tracking."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any
import time


class ProgressStatus(Enum):
    """Status of a progress step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ProgressReport:
    """Message reporting progress of a composite action step."""
    action_id: str           # ID of the composite action
    step_id: str             # ID of the current step
    step_name: str           # Human-readable step name
    status: ProgressStatus   # Current status
    progress: float          # 0.0 to 1.0
    message: Optional[str] = None      # Optional detail message
    metadata: Optional[Dict[str, Any]] = None  # Optional structured data
    timestamp: float = time.time()     # Creation timestamp
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "action_id": self.action_id,
            "step_id": self.step_id,
            "step_name": self.step_name,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "metadata": self.metadata or {},
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProgressReport":
        """Create from dictionary."""
        return cls(
            action_id=data["action_id"],
            step_id=data["step_id"],
            step_name=data["step_name"],
            status=ProgressStatus(data["status"]),
            progress=data["progress"],
            message=data.get("message"),
            metadata=data.get("metadata"),
            timestamp=data.get("timestamp", time.time())
        )