"""Camera data structures for perception pipeline."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time


@dataclass
class CameraIntrinsics:
    """Camera intrinsic parameters."""
    fx: float = 800.0
    fy: float = 800.0
    cx: float = 320.0
    cy: float = 240.0
    width: int = 640
    height: int = 480
    distortion: Dict[str, float] = field(default_factory=dict)


@dataclass
class CameraExtrinsics:
    """Camera extrinsic parameters (pose in world frame)."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0


@dataclass
class CameraFrame:
    """A single captured camera frame."""
    image: Any = None
    timestamp: float = field(default_factory=time.time)
    intrinsics: Optional[CameraIntrinsics] = None
    extrinsics: Optional[CameraExtrinsics] = None
    frame_id: str = ""
    encoding: str = "bgr8"

    def to_dict(self) -> dict:
        return {
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "encoding": self.encoding,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CameraFrame":
        return cls(
            frame_id=d.get("frame_id", ""),
            timestamp=d.get("timestamp", time.time()),
            encoding=d.get("encoding", "bgr8"),
        )
