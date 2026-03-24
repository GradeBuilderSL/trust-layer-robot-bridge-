"""Hypothesis classes for representing unobserved zones and hypotheses about their state.

Used by HypothesisEngine to generate and manage hypotheses about unobserved areas.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Any


class HypothesisType(Enum):
    """Types of hypotheses that can be generated."""
    OBJECT_PRESENCE = "object_presence"
    OBJECT_STATE = "object_state"
    ZONE_OCCUPANCY = "zone_occupancy"
    HUMAN_INTENT = "human_intent"
    ROBOT_CAPABILITY = "robot_capability"


class ConfidenceLevel(Enum):
    """Confidence levels for hypotheses."""
    LOW = "low"        # 0.0-0.3
    MEDIUM = "medium"  # 0.3-0.7
    HIGH = "high"      # 0.7-1.0


@dataclass
class UnobservedZone:
    """Ненаблюдаемая зона (область с ограниченной видимостью)"""
    zone_id: str
    polygon: List[Dict[str, float]]  # координаты в системе робота
    reason: str  # "occlusion", "sensor_limit", "blind_spot"
    last_observed: Optional[datetime] = None
    observation_duration: float = 0.0  # секунды с последнего наблюдения
    
    def __post_init__(self):
        """Validate zone data."""
        if not self.zone_id:
            self.zone_id = f"zone_{uuid.uuid4().hex[:8]}"
        
        # Ensure polygon has correct format
        if self.polygon and isinstance(self.polygon, list):
            for point in self.polygon:
                if not isinstance(point, dict):
                    raise ValueError("Polygon points must be dictionaries")
                if 'x' not in point or 'y' not in point:
                    raise ValueError("Polygon points must contain 'x' and 'y' keys")


@dataclass
class Hypothesis:
    """Гипотеза о состоянии ненаблюдаемой зоны"""
    hypothesis_id: str
    type: HypothesisType
    unobserved_zone_id: str
    description: str
    confidence: float  # 0.0-1.0
    confidence_level: ConfidenceLevel = field(init=False)
    evidence: List[Dict[str, Any]]  # источники доказательств
    timestamp: datetime
    ttl: float = 30.0  # время жизни в секундах
    
    def __post_init__(self):
        """Calculate confidence level based on confidence value."""
        if self.confidence <= 0.3:
            self.confidence_level = ConfidenceLevel.LOW
        elif self.confidence <= 0.7:
            self.confidence_level = ConfidenceLevel.MEDIUM
        else:
            self.confidence_level = ConfidenceLevel.HIGH
        
        # Validate confidence range
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")
        
        if not self.hypothesis_id:
            self.hypothesis_id = f"hyp_{uuid.uuid4().hex[:8]}"


@dataclass
class HypothesisSet:
    """Набор гипотез для сцены"""
    scene_id: str
    timestamp: datetime
    unobserved_zones: List[UnobservedZone]
    hypotheses: List[Hypothesis]
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate hypothesis set."""
        if not self.scene_id:
            self.scene_id = f"scene_{uuid.uuid4().hex[:8]}"
        
        # Ensure all hypotheses reference valid zones
        zone_ids = {zone.zone_id for zone in self.unobserved_zones}
        for hypothesis in self.hypotheses:
            if hypothesis.unobserved_zone_id not in zone_ids:
                raise ValueError(
                    f"Hypothesis references unknown zone: {hypothesis.unobserved_zone_id}"
                )
        
        # Set default metadata if empty
        if not self.metadata:
            self.metadata = {
                'engine_version': '1.0',
                'generation_method': 'deterministic_analysis'
            }