"""
Robot skills definitions for cognitive planning.
"""
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from enum import Enum


class SkillParameterType(str, Enum):
    """Types of skill parameters."""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    POSITION = "position"
    OBJECT_ID = "object_id"
    ZONE_ID = "zone_id"


class SkillParameter(BaseModel):
    """Parameter definition for a skill."""
    name: str
    type: SkillParameterType
    description: str
    required: bool = True
    default: Optional[Any] = None


class SkillMetadata(BaseModel):
    """Metadata for a robot skill."""
    id: str
    name: str
    description: str
    parameters: List[SkillParameter] = []
    preconditions: List[str] = []
    required_capabilities: List[str] = []
    requires_enterprise: bool = False
    max_speed_mps: Optional[float] = None
    max_payload_kg: Optional[float] = None
    estimated_duration: Optional[float] = None
    tags: List[str] = []


# Add planning types at the end of the file
class PlanningRequest(BaseModel):
    """Request for LLM planning."""
    task_description: str
    context: Optional[Dict[str, Any]] = None
    constraints: Optional[List[str]] = None
    max_steps: int = Field(default=10, ge=1, le=50)


class SkillStep(BaseModel):
    """A single step in a plan."""
    skill_id: str
    parameters: Dict[str, Any]
    preconditions: Optional[List[str]] = None
    expected_outcome: Optional[str] = None


class PlanningResponse(BaseModel):
    """Response from LLM planning."""
    plan_id: str
    steps: List[SkillStep]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: Optional[str] = None
    total_estimated_time: Optional[float] = None