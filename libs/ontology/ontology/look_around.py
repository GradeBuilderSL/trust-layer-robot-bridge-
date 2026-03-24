"""
Look around data structures for AIDEV-491.

Structures for look_around behavior: sequence of rotations + VLM capture +
observation aggregation.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class LookAroundStatus(Enum):
    """Status of look_around operation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ObservationPoint:
    """Observation result at a single point."""
    angle_degrees: float
    timestamp: str
    image_hash: Optional[str] = None
    detected_objects: List[Dict[str, Any]] = field(default_factory=list)
    zones_detected: List[Dict[str, Any]] = field(default_factory=list)
    vlm_analysis: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class LookAroundReport:
    """Final look_around report."""
    skill_id: str
    robot_id: str
    status: LookAroundStatus
    start_time: str
    end_time: Optional[str] = None
    observation_points: List[ObservationPoint] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    total_objects_detected: int = 0
    unique_zones: List[str] = field(default_factory=list)