"""
Skill ontology models for the cognitive layer.

Defines Skill, SkillCapability, SkillPrecondition, SkillEffect, etc.
Used by skill_library service and cognitive_planner.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class SkillCapability(BaseModel):
    """A capability required or provided by a skill."""
    
    id: str
    name: str
    description: Optional[str] = None
    version: str = "1.0"
    parameters: Dict[str, Any] = Field(default_factory=dict)


class SkillPrecondition(BaseModel):
    """A condition that must be true before the skill can execute."""
    
    type: str  # robot_has_arm, object_in_range, battery_level, etc.
    parameters: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class SkillEffect(BaseModel):
    """An effect that the skill has on the world when executed."""
    
    type: str  # object_moved, object_grasped, door_opened, etc.
    parameters: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    description: Optional[str] = None


class Skill(BaseModel):
    """A robot skill that can be matched and executed."""
    
    # Core identity
    id: str
    name: str
    description: str
    version: str = "1.0"
    
    # Semantic metadata
    tags: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    domain: Optional[str] = None  # navigation, manipulation, interaction, etc.
    
    # Capabilities
    required_capabilities: List[str] = Field(default_factory=list)
    provided_capabilities: List[str] = Field(default_factory=list)
    
    # Execution model
    preconditions: List[SkillPrecondition] = Field(default_factory=list)
    effects: List[SkillEffect] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    
    # Performance characteristics
    estimated_duration_seconds: float = 0.0
    energy_cost_joules: float = 0.0
    success_rate: float = 1.0
    max_speed_mps: float = 0.0  # maximum speed required/used by skill
    
    # Safety & compliance
    safety_class: str = "A"  # A (safe) to D (dangerous)
    requires_enterprise: bool = False
    license_required: List[str] = Field(default_factory=list)
    
    # Embedding for semantic matching
    embedding: Optional[List[float]] = None  # vector embedding for semantic search
    
    # References
    source_bundle: Optional[str] = None
    documentation_url: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "nav_to_point",
                "name": "Navigate to Point",
                "description": "Move robot to specified coordinates",
                "tags": ["navigation", "movement"],
                "required_capabilities": ["wheel_movement", "localization"],
                "preconditions": [
                    {"type": "battery_level", "parameters": {"min_percent": 20}}
                ],
                "effects": [
                    {"type": "position_changed", "parameters": {"x": 10, "y": 5}}
                ]
            }
        }