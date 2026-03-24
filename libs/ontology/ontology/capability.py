"""
Capability-based safety policies for robot capabilities restriction.

Defines capability profiles for robots and policies that restrict capabilities
based on jurisdiction, profession, or other constraints.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from ontology.robot_command import RobotCommand, CommandType

logger = logging.getLogger(__name__)


class CapabilityType(enum.Enum):
    """Types of robot capabilities."""
    MOVEMENT = "movement"
    MANIPULATION = "manipulation"
    SENSING = "sensing"
    COMMUNICATION = "communication"
    TOOL_USAGE = "tool_usage"
    NAVIGATION = "navigation"
    GRASPING = "grasping"
    LIFTING = "lifting"
    CUTTING = "cutting"
    WELDING = "welding"


# Mapping from command types to capability types
COMMAND_TO_CAPABILITY: Dict[CommandType, Set[CapabilityType]] = {
    CommandType.MOVE: {CapabilityType.MOVEMENT, CapabilityType.NAVIGATION},
    CommandType.TURN: {CapabilityType.MOVEMENT},
    CommandType.STOP: {CapabilityType.MOVEMENT},
    CommandType.GRASP: {CapabilityType.GRASPING, CapabilityType.MANIPULATION},
    CommandType.RELEASE: {CapabilityType.GRASPING, CapabilityType.MANIPULATION},
    CommandType.LIFT: {CapabilityType.LIFTING, CapabilityType.MANIPULATION},
    CommandType.LOWER: {CapabilityType.LIFTING, CapabilityType.MANIPULATION},
    CommandType.CUT: {CapabilityType.CUTTING, CapabilityType.TOOL_USAGE},
    CommandType.WELD: {CapabilityType.WELDING, CapabilityType.TOOL_USAGE},
    CommandType.SENSE: {CapabilityType.SENSING},
    CommandType.COMMUNICATE: {CapabilityType.COMMUNICATION},
}


@dataclass
class CapabilityConstraint:
    """Constraint for a specific capability type."""
    capability_type: CapabilityType
    max_speed: Optional[float] = None
    max_payload: Optional[float] = None
    allowed_zones: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    prohibited_actions: List[str] = field(default_factory=list)
    requires_supervision: bool = False


@dataclass
class CapabilityProfile:
    """Robot's capability profile - what the robot can physically do."""
    robot_id: str
    capabilities: Dict[CapabilityType, Dict[str, Any]]  # capability type -> capability params
    max_speed_mps: float = 0.0
    max_payload_kg: float = 0.0
    available_tools: List[str] = field(default_factory=list)
    
    def get_capability(self, cap_type: CapabilityType) -> Optional[Dict[str, Any]]:
        """Get capability parameters for a specific type."""
        return self.capabilities.get(cap_type)
    
    def has_capability(self, cap_type: CapabilityType) -> bool:
        """Check if robot has a specific capability."""
        return cap_type in self.capabilities


@dataclass
class CapabilityPolicy:
    """Policy that restricts capabilities based on jurisdiction/profession."""
    policy_id: str
    jurisdiction: str
    profession: str
    restrictions: Dict[CapabilityType, CapabilityConstraint]  # capability type -> constraints
    description: str = ""
    
    def check_command(self, command: RobotCommand, 
                     profile: CapabilityProfile) -> Tuple[bool, str]:
        """
        Check if command is allowed under this policy.
        
        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # Get capability types for this command
        capability_types = COMMAND_TO_CAPABILITY.get(command.type, set())
        
        for cap_type in capability_types:
            if cap_type in self.restrictions:
                constraint = self.restrictions[cap_type]
                
                # Check max speed for movement capabilities
                if cap_type == CapabilityType.MOVEMENT and constraint.max_speed is not None:
                    if command.params.get("speed", 0.0) > constraint.max_speed:
                        return False, f"Speed {command.params.get('speed')} exceeds maximum {constraint.max_speed} for {cap_type.value}"
                
                # Check if action is prohibited
                if command.type.value.lower() in constraint.prohibited_actions:
                    return False, f"Command {command.type.value} is prohibited for {cap_type.value}"
                
                # Check tool restrictions
                if cap_type in {CapabilityType.TOOL_USAGE, CapabilityType.MANIPULATION}:
                    tool = command.params.get("tool")
                    if tool and constraint.allowed_tools and tool not in constraint.allowed_tools:
                        return False, f"Tool '{tool}' not allowed for {cap_type.value}"
        
        return True, ""


class CapabilityPolicyManager:
    """Manager for capability policies."""
    
    def __init__(self):
        self.policies: Dict[str, CapabilityPolicy] = {}  # policy_id -> policy
        self.robot_policies: Dict[str, str] = {}  # robot_id -> policy_id
        self.robot_profiles: Dict[str, CapabilityProfile] = {}  # robot_id -> profile
    
    def add_policy(self, policy: CapabilityPolicy) -> None:
        """Add or update a capability policy."""
        self.policies[policy.policy_id] = policy
    
    def remove_policy(self, policy_id: str) -> bool:
        """Remove a capability policy."""
        if policy_id in self.policies:
            del self.policies[policy_id]
            # Remove from robots using this policy
            robots_to_remove = [rid for rid, pid in self.robot_policies.items() if pid == policy_id]
            for rid in robots_to_remove:
                del self.robot_policies[rid]
            return True
        return False
    
    def assign_policy_to_robot(self, robot_id: str, policy_id: str) -> bool:
        """Assign a capability policy to a robot."""
        if policy_id in self.policies:
            self.robot_policies[robot_id] = policy_id
            return True
        return False
    
    def set_robot_profile(self, profile: CapabilityProfile) -> None:
        """Set or update a robot's capability profile."""
        self.robot_profiles[profile.robot_id] = profile
    
    def check_robot_command(self, robot_id: str, command: RobotCommand) -> Tuple[bool, str, Optional[str]]:
        """
        Check if a command is allowed for a robot based on its assigned policy.
        
        Returns:
            Tuple of (allowed: bool, reason: str, policy_id: Optional[str])
        """
        # Get robot's policy
        policy_id = self.robot_policies.get(robot_id)
        if not policy_id:
            return True, "No capability policy assigned", None
        
        # Get policy
        policy = self.policies.get(policy_id)
        if not policy:
            return True, f"Assigned policy {policy_id} not found", None
        
        # Get robot's capability profile
        profile = self.robot_profiles.get(robot_id)
        if not profile:
            return True, f"No capability profile for robot {robot_id}", policy_id
        
        # Check against policy
        allowed, reason = policy.check_command(command, profile)
        return allowed, reason, policy_id