"""VLM Observation data structures for world memory integration."""
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime
from enum import Enum


class ObservationConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class DetectedObject:
    """Обнаруженный объект в сцене"""
    label: str
    confidence: float
    bbox: List[float]  # [x1, y1, x2, y2] normalized
    position_3d: Optional[List[float]] = None  # [x, y, z] in meters
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class SceneObservation:
    """Наблюдение всей сцены"""
    timestamp: datetime
    source: str  # "perception_edge"
    camera_id: str
    detected_objects: List[DetectedObject]
    scene_description: str  # текстовое описание от VLM
    confidence: ObservationConfidence


@dataclass
class WorldMemoryEntry:
    """Запись в мировой памяти"""
    id: str
    observation: SceneObservation
    created_at: datetime
    expires_at: Optional[datetime] = None  # TTL для наблюдений
    metadata: Dict = field(default_factory=dict)