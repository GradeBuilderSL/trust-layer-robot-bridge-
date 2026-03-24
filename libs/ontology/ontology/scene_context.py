"""Scene context for passing current scene observations to LLM prompts."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from libs.robot_interface.telemetry import RobotPose


@dataclass
class SceneContext:
    """Контекст сцены для передачи в LLM"""
    objects: List['ObjectDetection'] = field(default_factory=list)
    zones: List['ZoneDetection'] = field(default_factory=list)
    robot_pose: Optional[RobotPose] = None
    timestamp: datetime = field(default_factory=datetime.now)
    scene_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь для JSON сериализации"""
        # Local import to avoid circular dependency
        from .objects import ObjectDetection, ZoneDetection
        
        return {
            "objects": [obj.to_dict() for obj in self.objects],
            "zones": [zone.to_dict() for zone in self.zones],
            "robot_pose": self.robot_pose.to_dict() if self.robot_pose else None,
            "timestamp": self.timestamp.isoformat(),
            "scene_id": self.scene_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SceneContext':
        """Создать из словаря"""
        from .objects import ObjectDetection, ZoneDetection
        from libs.robot_interface.telemetry import RobotPose
        
        return cls(
            objects=[ObjectDetection.from_dict(obj) for obj in data.get("objects", [])],
            zones=[ZoneDetection.from_dict(zone) for zone in data.get("zones", [])],
            robot_pose=RobotPose.from_dict(data["robot_pose"]) if data.get("robot_pose") else None,
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now(),
            scene_id=data.get("scene_id", "")
        )