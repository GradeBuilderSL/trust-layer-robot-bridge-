"""
VLM Objects — data structures for VLM-based object detection and description.
"""
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class VLMObjectDescription:
    """Textual description of an object to search for via VLM."""
    text_description: str
    attributes: Dict[str, Any]  # color, size, shape, etc.
    context_hints: List[str]  # e.g., "near the door", "on the table"
    min_confidence: float = 0.5


@dataclass
class VLMDetection:
    """A single object detection result from VLM."""
    bbox: List[float]  # [x1, y1, x2, y2] normalized coordinates (0-1)
    confidence: float
    description: str  # matched description or generated caption
    attributes: Dict[str, Any]  # extracted attributes
    class_name: str = ""  # optional, if VLM provides class

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bbox": self.bbox,
            "confidence": self.confidence,
            "description": self.description,
            "attributes": self.attributes,
            "class_name": self.class_name,
        }