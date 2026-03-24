"""Visual memory classes for storing and querying observations.

Types for L3 (Knowledge layer) to support visual memory queries like
"ты видел красный куб 2 минуты назад у стены".
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union


@dataclasses.dataclass
class VisualObservation:
    """Unit of visual memory storage."""
    object_type: str
    object_id: Optional[str]
    confidence: float
    timestamp: datetime
    robot_position: Dict[str, float]  # x, y, z, yaw
    camera_frame: Optional[str]
    bounding_box: Optional[Dict[str, float]]  # x1, y1, x2, y2
    attributes: Dict[str, Any]  # color, size, material, etc.
    source_service: str  # "perception_edge" or "skill_library"
    audit_ref: str  # reference to decision_log hash
    observation_id: str = ""  # unique identifier

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        result = dataclasses.asdict(self)
        # Convert datetime to ISO string
        result["timestamp"] = self.timestamp.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VisualObservation:
        """Create from dict."""
        # Parse ISO timestamp back to datetime
        if isinstance(data.get("timestamp"), str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


@dataclasses.dataclass
class VisualQuery:
    """Query to visual memory."""
    object_type: Optional[str] = None
    object_id: Optional[str] = None
    time_window_seconds: Optional[float] = None  # e.g., 120 for "2 minutes ago"
    spatial_region: Optional[Dict[str, float]] = None  # bounding box in world coords
    min_confidence: float = 0.3
    attributes_filter: Optional[Dict[str, Any]] = None
    limit: int = 100

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VisualQuery:
        return cls(**data)


class MemoryResponseStatus(Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INVALID_QUERY = "invalid_query"


@dataclasses.dataclass
class MemoryResponse:
    """Response from visual memory query."""
    status: MemoryResponseStatus
    observations: List[VisualObservation]
    query: VisualQuery
    count: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "status": self.status.value,
            "observations": [obs.to_dict() for obs in self.observations],
            "query": self.query.to_dict(),
            "count": self.count,
        }
        if self.error_message:
            result["error_message"] = self.error_message
        return result


@dataclasses.dataclass
class StoreRequest:
    """Request to store observations."""
    observations: List[VisualObservation]
    audit_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observations": [obs.to_dict() for obs in self.observations],
            "audit_ref": self.audit_ref,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StoreRequest:
        observations = [VisualObservation.from_dict(obs) for obs in data.get("observations", [])]
        return cls(observations=observations, audit_ref=data.get("audit_ref"))