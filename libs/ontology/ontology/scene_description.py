"""
Scene description structures for VLM analysis.

Provides dataclasses for representing VLM scene analysis results,
including natural language descriptions, activity levels, context types,
and key scene elements with spatial relations.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
from .objects import DetectedObject, ObjectClass


class SceneActivity(Enum):
    """Activity level detected in the scene."""
    NORMAL = "normal"
    SUSPICIOUS = "suspicious"
    HAZARDOUS = "hazardous"
    UNKNOWN = "unknown"


class SceneContext(Enum):
    """Context type of the scene."""
    INDUSTRIAL = "industrial"
    LABORATORY = "laboratory"
    WAREHOUSE = "warehouse"
    PUBLIC_SPACE = "public_space"
    OUTDOOR = "outdoor"
    UNKNOWN = "unknown"


@dataclass
class SceneElement:
    """Individual scene element with description."""
    object_ref: Optional[DetectedObject] = None
    description: str = ""
    confidence: float = 0.0
    spatial_relation: Optional[str] = None  # "left_of", "near", "on_top_of", etc.

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            "description": self.description,
            "confidence": self.confidence,
            "spatial_relation": self.spatial_relation
        }
        if self.object_ref:
            result["object_ref"] = self.object_ref.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneElement":
        """Create from dictionary representation."""
        obj_ref = None
        if "object_ref" in data:
            obj_ref = DetectedObject.from_dict(data["object_ref"])
        
        return cls(
            object_ref=obj_ref,
            description=data.get("description", ""),
            confidence=data.get("confidence", 0.0),
            spatial_relation=data.get("spatial_relation")
        )


@dataclass
class SceneDescription:
    """Complete scene description from VLM analysis."""
    natural_description: str  # Natural description in operator's language
    activity_level: SceneActivity
    context_type: SceneContext
    key_elements: List[SceneElement] = field(default_factory=list)
    potential_hazards: List[str] = field(default_factory=list)
    human_presence: bool = False
    human_count: int = 0
    robot_visibility: float = 0.0  # How well the robot is visible in scene (0-1)
    confidence: float = 0.0
    timestamp: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "natural_description": self.natural_description,
            "activity_level": self.activity_level.value,
            "context_type": self.context_type.value,
            "key_elements": [elem.to_dict() for elem in self.key_elements],
            "potential_hazards": self.potential_hazards,
            "human_presence": self.human_presence,
            "human_count": self.human_count,
            "robot_visibility": self.robot_visibility,
            "confidence": self.confidence,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneDescription":
        """Create from dictionary representation."""
        key_elements = []
        if "key_elements" in data:
            key_elements = [SceneElement.from_dict(elem) for elem in data["key_elements"]]
        
        return cls(
            natural_description=data.get("natural_description", ""),
            activity_level=SceneActivity(data.get("activity_level", "unknown")),
            context_type=SceneContext(data.get("context_type", "unknown")),
            key_elements=key_elements,
            potential_hazards=data.get("potential_hazards", []),
            human_presence=data.get("human_presence", False),
            human_count=data.get("human_count", 0),
            robot_visibility=data.get("robot_visibility", 0.0),
            confidence=data.get("confidence", 0.0),
            timestamp=data.get("timestamp", 0.0)
        )