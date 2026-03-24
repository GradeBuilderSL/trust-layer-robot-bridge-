"""Observation classes with confidence decay functionality.

Contains the core Observation class that supports temporal decay of confidence
based on stability class, similar to the approach used in world memory.
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from ontology.stability import CONFIDENCE_DECAY_RATES, DEFAULT_STABILITY_CLASS


@dataclass
class Observation:
    """VLM observation with confidence decay support.
    
    Supports temporal decay of confidence based on stability class.
    Analogous to how world memory handles entity confidence decay.
    """
    
    entity_id: str
    entity_class: str
    distance_m: float
    bearing_rad: float
    initial_confidence: float
    captured_at_s: float
    stability_class: str = DEFAULT_STABILITY_CLASS
    
    # Decay-specific fields
    confidence_decay_rate: float = None  # Set from stability class if None
    last_confidence_update_s: float = None  # Set to captured_at_s if None
    
    def __post_init__(self):
        """Initialize derived values after construction."""
        if self.confidence_decay_rate is None:
            self.confidence_decay_rate = CONFIDENCE_DECAY_RATES.get(
                self.stability_class, CONFIDENCE_DECAY_RATES[DEFAULT_STABILITY_CLASS]
            )
        
        if self.last_confidence_update_s is None:
            self.last_confidence_update_s = self.captured_at_s
    
    def get_current_confidence(self, now_s: Optional[float] = None) -> float:
        """Calculate current confidence accounting for temporal decay.
        
        Args:
            now_s: Current timestamp in seconds. Defaults to time.monotonic()
            
        Returns:
            Current confidence value after applying exponential decay
        """
        if now_s is None:
            now_s = time.monotonic()
        
        time_elapsed = now_s - self.last_confidence_update_s
        
        # Exponential decay: confidence = initial * exp(-decay_rate * time)
        current_conf = self.initial_confidence * (
            2.718281828459045 ** (-self.confidence_decay_rate * time_elapsed)
        )
        
        # Ensure confidence doesn't go below 0
        return max(0.0, current_conf)
    
    def update_confidence_timestamp(self, now_s: float = None):
        """Update the last confidence update timestamp to current time."""
        if now_s is None:
            now_s = time.monotonic()
        self.last_confidence_update_s = now_s


# Convenience function to create observation from raw detection data
def create_observation_from_detection(
    entity_id: str,
    entity_class: str, 
    distance_m: float,
    bearing_rad: float,
    confidence: float,
    stability_class: str = DEFAULT_STABILITY_CLASS,
    now_s: float = None
) -> Observation:
    """Create an Observation instance from raw detection data."""
    if now_s is None:
        now_s = time.monotonic()
    
    return Observation(
        entity_id=entity_id,
        entity_class=entity_class,
        distance_m=distance_m,
        bearing_rad=bearing_rad,
        initial_confidence=confidence,
        captured_at_s=now_s,
        stability_class=stability_class
    )


# Function to apply decay to a list of observations
def apply_confidence_decay_to_observations(
    observations: List[Observation], 
    now_s: Optional[float] = None
) -> List[Observation]:
    """Apply confidence decay to a list of observations."""
    if now_s is None:
        now_s = time.monotonic()
    
    for obs in observations:
        obs.update_confidence_timestamp(now_s)
    
    return observations

# ── Additional observation types added for perception_edge ───────────────────

from dataclasses import dataclass as _dc, field as _field
from typing import List as _List, Optional as _Optional
from datetime import datetime as _dt


@_dc
class ObjectObservation:
    """Single object detection observation."""
    object_class: str
    confidence: float
    bbox: _List[float] = _field(default_factory=list)
    timestamp: _Optional[_dt] = None
    object_id: str = ""
    metadata: dict = _field(default_factory=dict)


@_dc
class ZoneObservation:
    """Zone detection observation."""
    zone_type: str
    confidence: float
    polygon: _List[_List[float]] = _field(default_factory=list)
    timestamp: _Optional[_dt] = None
    zone_id: str = ""


@_dc
class ObservationChain:
    """Chain of observations from a single frame."""
    timestamp: _Optional[_dt]
    object_observations: _List[ObjectObservation] = _field(default_factory=list)
    zone_observations: _List[ZoneObservation] = _field(default_factory=list)
    camera_pose: dict = _field(default_factory=dict)
    frame_id: str = ""

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ObservationChain":
        objs = [ObjectObservation(**o) for o in d.get("object_observations", [])]
        zones = [ZoneObservation(**z) for z in d.get("zone_observations", [])]
        ts = d.get("timestamp")
        return cls(
            timestamp=ts,
            object_observations=objs,
            zone_observations=zones,
            camera_pose=d.get("camera_pose", {}),
            frame_id=d.get("frame_id", ""),
        )

