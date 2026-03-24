"""Object detection and zone detection data structures."""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class BoundingBox:
    """Bounding box for object detection."""
    x: float
    y: float
    width: float
    height: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BoundingBox':
        return cls(
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"]
        )


@dataclass
class ObjectDetection:
    """Object detection result."""
    label: str
    confidence: float
    bbox: BoundingBox
    x: float = 0.0  # world coordinate
    y: float = 0.0  # world coordinate
    z: float = 0.0  # world coordinate
    id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "bbox": self.bbox.to_dict(),
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "id": self.id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ObjectDetection':
        return cls(
            label=data["label"],
            confidence=data["confidence"],
            bbox=BoundingBox.from_dict(data["bbox"]),
            x=data.get("x", 0.0),
            y=data.get("y", 0.0),
            z=data.get("z", 0.0),
            id=data.get("id")
        )


@dataclass
class ZoneDetection:
    """Zone detection result."""
    zone_type: str
    description: str
    boundary_points: List[List[float]]  # List of [x, y] points
    confidence: float = 1.0
    id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_type": self.zone_type,
            "description": self.description,
            "boundary_points": self.boundary_points,
            "confidence": self.confidence,
            "id": self.id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ZoneDetection':
        return cls(
            zone_type=data["zone_type"],
            description=data["description"],
            boundary_points=data["boundary_points"],
            confidence=data.get("confidence", 1.0),
            id=data.get("id")
        )

# Alias — some code imports DetectedObject instead of ObjectDetection
DetectedObject = ObjectDetection



class ObjectClass:
    """Object class constants for detection."""
    PERSON = "person"
    FORKLIFT = "forklift"
    PALLET = "pallet"
    RACK = "rack"
    OBSTACLE = "obstacle"
    ROBOT = "robot"
    UNKNOWN = "unknown"

