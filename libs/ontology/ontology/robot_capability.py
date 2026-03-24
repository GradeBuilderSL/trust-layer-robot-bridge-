"""
Robot capability models for capability-aware command validation.

Provides deterministic checks whether a generated command is executable
on a specific robot before sending to bridge.
Part of L2a layer: deterministic only, no ML/LLM/network I/O.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class RobotType(Enum):
    """Supported robot types with known capabilities."""
    UR5 = "ur5"
    UR10 = "ur10"
    KUKA_KR6 = "kuka_kr6"
    H1 = "h1"
    N2 = "n2"
    CUSTOM = "custom"
    MOCK = "mock"  # For testing/simulation


@dataclass
class KinematicLimits:
    """Kinematic limitations of a robot."""
    max_reach_mm: float
    joint_limits_deg: Dict[str, Tuple[float, float]]  # joint_name: (min, max)
    max_payload_kg: float
    repeatability_mm: float
    workspace_limits_mm: Dict[str, Tuple[float, float]]  # axis: (min, max)


@dataclass
class DynamicLimits:
    """Dynamic limitations of a robot."""
    max_velocity_deg_per_s: Dict[str, float]
    max_acceleration_deg_per_s2: Dict[str, float]
    max_torque_nm: Dict[str, float]
    max_cartesian_velocity_mm_per_s: float
    max_cartesian_acceleration_mm_per_s2: float


@dataclass
class SkillCapability:
    """Capability requirements for a specific skill."""
    skill_id: str
    supported_parameters: List[str]
    execution_time_range_ms: Tuple[int, int]  # (min, max)
    precision_requirements: Dict[str, float]  # param_name: tolerance
    required_hardware: List[str]  # e.g., ["gripper", "force_sensor"]


@dataclass
class CapabilityValidationResult:
    """Result of capability validation."""
    is_valid: bool
    reason: str = ""
    required_capabilities: List[str] = None
    missing_capabilities: List[str] = None
    details: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.required_capabilities is None:
            self.required_capabilities = []
        if self.missing_capabilities is None:
            self.missing_capabilities = []
        if self.details is None:
            self.details = {}


class RobotCapability:
    """Main class representing robot capabilities and validation logic."""
    
    def __init__(self, robot_type: RobotType, robot_id: str, config_path: Optional[str] = None):
        self.robot_type = robot_type
        self.robot_id = robot_id
        self.kinematic = KinematicLimits(
            max_reach_mm=1000.0,
            joint_limits_deg={},
            max_payload_kg=5.0,
            repeatability_mm=0.1,
            workspace_limits_mm={"x": (-1000, 1000), "y": (-1000, 1000), "z": (0, 1500)}
        )
        self.dynamic = DynamicLimits(
            max_velocity_deg_per_s={},
            max_acceleration_deg_per_s2={},
            max_torque_nm={},
            max_cartesian_velocity_mm_per_s=500.0,
            max_cartesian_acceleration_mm_per_s2=2000.0
        )
        self.skills: Dict[str, SkillCapability] = {}
        self.available_hardware: List[str] = []
        
        if config_path:
            self._load_from_config(config_path)
        else:
            self._load_default_capabilities()
    
    def _load_default_capabilities(self) -> None:
        """Load default capabilities based on robot type."""
        if self.robot_type == RobotType.UR5:
            self.kinematic.max_reach_mm = 850.0
            self.kinematic.max_payload_kg = 5.0
            self.kinematic.joint_limits_deg = {
                "shoulder_pan": (-360, 360),
                "shoulder_lift": (-180, 180),
                "elbow": (-180, 180),
                "wrist1": (-360, 360),
                "wrist2": (-180, 180),
                "wrist3": (-360, 360)
            }
            self.available_hardware = ["gripper", "camera"]
        elif self.robot_type == RobotType.H1:
            self.kinematic.max_reach_mm = 1500.0
            self.kinematic.max_payload_kg = 15.0
            self.available_hardware = ["mobile_base", "arm", "gripper", "sensors"]
        elif self.robot_type == RobotType.N2:
            self.kinematic.max_reach_mm = 1200.0
            self.kinematic.max_payload_kg = 10.0
            self.available_hardware = ["mobile_base", "arm", "gripper"]
        elif self.robot_type == RobotType.MOCK:
            self.kinematic.max_reach_mm = 1000.0
            self.kinematic.max_payload_kg = 5.0
            self.available_hardware = ["mock_gripper"]
        
        # Load skills from default configuration
        self._load_default_skills()
    
    def _load_default_skills(self) -> None:
        """Load default skills based on robot type and hardware."""
        base_skills = [
            SkillCapability(
                skill_id="move_to_pose",
                supported_parameters=["x", "y", "z", "rx", "ry", "rz"],
                execution_time_range_ms=(1000, 5000),
                precision_requirements={"position_mm": 5.0, "orientation_deg": 2.0},
                required_hardware=[]
            ),
            SkillCapability(
                skill_id="pick_object",
                supported_parameters=["object_id", "gripper_force"],
                execution_time_range_ms=(2000, 8000),
                precision_requirements={"position_mm": 2.0},
                required_hardware=["gripper"]
            ),
            SkillCapability(
                skill_id="place_object",
                supported_parameters=["object_id", "target_x", "target_y", "target_z"],
                execution_time_range_ms=(2000, 8000),
                precision_requirements={"position_mm": 2.0},
                required_hardware=["gripper"]
            )
        ]
        
        for skill in base_skills:
            # Check if robot has required hardware
            if all(hw in self.available_hardware for hw in skill.required_hardware):
                self.skills[skill.skill_id] = skill
    
    def _load_from_config(self, config_path: str) -> None:
        """Load capabilities from configuration file."""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Load kinematic limits
            if "kinematic" in config:
                kin = config["kinematic"]
                self.kinematic.max_reach_mm = kin.get("max_reach_mm", 1000.0)
                self.kinematic.max_payload_kg = kin.get("max_payload_kg", 5.0)
                self.kinematic.repeatability_mm = kin.get("repeatability_mm", 0.1)
                self.kinematic.joint_limits_deg = kin.get("joint_limits_deg", {})
                self.kinematic.workspace_limits_mm = kin.get("workspace_limits_mm", {})
            
            # Load dynamic limits
            if "dynamic" in config:
                dyn = config["dynamic"]
                self.dynamic.max_velocity_deg_per_s = dyn.get("max_velocity_deg_per_s", {})
                self.dynamic.max_acceleration_deg_per_s2 = dyn.get("max_acceleration_deg_per_s2", {})
                self.dynamic.max_torque_nm = dyn.get("max_torque_nm", {})
                self.dynamic.max_cartesian_velocity_mm_per_s = dyn.get("max_cartesian_velocity_mm_per_s", 500.0)
                self.dynamic.max_cartesian_acceleration_mm_per_s2 = dyn.get("max_cartesian_acceleration_mm_per_s2", 2000.0)
            
            # Load hardware
            self.available_hardware = config.get("available_hardware", [])
            
            # Load skills
            if "skills" in config:
                for skill_config in config["skills"]:
                    skill = SkillCapability(
                        skill_id=skill_config["id"],
                        supported_parameters=skill_config.get("supported_parameters", []),
                        execution_time_range_ms=tuple(skill_config.get("execution_time_range_ms", [1000, 5000])),
                        precision_requirements=skill_config.get("precision_requirements", {}),
                        required_hardware=skill_config.get("required_hardware", [])
                    )
                    self.skills[skill.skill_id] = skill
            
        except Exception as e:
            logger.error(f"Failed to load capability config from {config_path}: {e}")
            self._load_default_capabilities()
    
    def can_reach_position(self, position_mm: List[float]) -> bool:
        """Check if position is within robot's reachable workspace."""
        if len(position_mm) < 3:
            return False
        
        x, y, z = position_mm[0], position_mm[1], position_mm[2]
        
        # Check distance from base (simplified sphere)
        distance = (x**2 + y**2 + z**2)**0.5
        if distance > self.kinematic.max_reach_mm:
            return False
        
        # Check workspace limits
        if self.kinematic.workspace_limits_mm:
            if "x" in self.kinematic.workspace_limits_mm:
                x_min, x_max = self.kinematic.workspace_limits_mm["x"]
                if not (x_min <= x <= x_max):
                    return False
            if "y" in self.kinematic.workspace_limits_mm:
                y_min, y_max = self.kinematic.workspace_limits_mm["y"]
                if not (y_min <= y <= y_max):
                    return False
            if "z" in self.kinematic.workspace_limits_mm:
                z_min, z_max = self.kinematic.workspace_limits_mm["z"]
                if not (z_min <= z <= z_max):
                    return False
        
        return True
    
    def validate_velocity(self, velocity_mm_per_s: float, is_cartesian: bool = True) -> bool:
        """Validate velocity against robot limits."""
        if is_cartesian:
            return velocity_mm_per_s <= self.dynamic.max_cartesian_velocity_mm_per_s
        return True  # Joint velocity validation would need joint-specific limits
    
    def validate_acceleration(self, acceleration_mm_per_s2: float, is_cartesian: bool = True) -> bool:
        """Validate acceleration against robot limits."""
        if is_cartesian:
            return acceleration_mm_per_s2 <= self.dynamic.max_cartesian_acceleration_mm_per_s2
        return True
    
    def has_skill(self, skill_id: str) -> bool:
        """Check if robot has a specific skill."""
        return skill_id in self.skills
    
    def validate_skill_execution(self, skill_id: str, parameters: Dict[str, Any]) -> CapabilityValidationResult:
        """Validate if a skill can be executed with given parameters."""
        result = CapabilityValidationResult(
            is_valid=False,
            details={"skill_id": skill_id, "parameters": parameters}
        )
        
        # Check if skill exists
        if skill_id not in self.skills:
            result.reason = f"Skill '{skill_id}' not available for robot {self.robot_id}"
            result.missing_capabilities = [skill_id]
            return result
        
        skill = self.skills[skill_id]
        result.required_capabilities = skill.required_hardware.copy()
        
        # Check hardware requirements
        missing_hardware = []
        for req_hw in skill.required_hardware:
            if req_hw not in self.available_hardware:
                missing_hardware.append(req_hw)
        
        if missing_hardware:
            result.reason = f"Missing hardware: {', '.join(missing_hardware)}"
            result.missing_capabilities = missing_hardware
            return result
        
        # Check parameter compatibility
        for param_name in parameters.keys():
            if param_name not in skill.supported_parameters:
                result.reason = f"Parameter '{param_name}' not supported for skill '{skill_id}'"
                return result
        
        # Check precision requirements if position is involved
        if "position_mm" in skill.precision_requirements:
            precision_req = skill.precision_requirements["position_mm"]
            if precision_req < self.kinematic.repeatability_mm:
                result.reason = f"Required precision {precision_req}mm exceeds robot repeatability {self.kinematic.repeatability_mm}mm"
                return result
        
        result.is_valid = True
        result.reason = "Skill executable"
        return result
    
    def validate_payload(self, payload_kg: float) -> bool:
        """Validate if payload is within robot capacity."""
        return payload_kg <= self.kinematic.max_payload_kg
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert capabilities to dictionary for serialization."""
        return {
            "robot_type": self.robot_type.value,
            "robot_id": self.robot_id,
            "kinematic": {
                "max_reach_mm": self.kinematic.max_reach_mm,
                "max_payload_kg": self.kinematic.max_payload_kg,
                "repeatability_mm": self.kinematic.repeatability_mm,
                "joint_limits_deg": self.kinematic.joint_limits_deg,
                "workspace_limits_mm": self.kinematic.workspace_limits_mm
            },
            "dynamic": {
                "max_cartesian_velocity_mm_per_s": self.dynamic.max_cartesian_velocity_mm_per_s,
                "max_cartesian_acceleration_mm_per_s2": self.dynamic.max_cartesian_acceleration_mm_per_s2
            },
            "available_hardware": self.available_hardware,
            "available_skills": list(self.skills.keys())
        }