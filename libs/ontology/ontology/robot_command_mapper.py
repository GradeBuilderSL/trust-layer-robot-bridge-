"""
RobotCommandMapper - maps canonical intents to robot-specific ROS2 endpoints.

Provides deterministic mapping from ontology intents to concrete ROS2
topics/actions/services based on robot profile.
"""

import yaml
from typing import Dict, Optional, Tuple, Any, List
from dataclasses import asdict

from .robot_profile import RobotProfile, ROS2Mapping, ROS2MessageType


class RobotCommandMapper:
    """
    Mapper for converting canonical commands to ROS2 endpoints.
    
    Thread-safe after initialization (read-only profile).
    """
    
    def __init__(self, profile_path: str):
        """
        Initialize mapper with robot profile.
        
        Args:
            profile_path: Path to YAML robot profile file
            
        Raises:
            ValueError: If profile validation fails
            FileNotFoundError: If profile file doesn't exist
        """
        self.profile = self._load_profile(profile_path)
        self._validate_profile()
        
    def _load_profile(self, path: str) -> RobotProfile:
        """Load and parse robot profile from YAML file."""
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        
        mappings = {}
        for intent_str, mapping_data in data.get("ros2_mappings", {}).items():
            mapping = ROS2Mapping(
                intent=intent_str,
                ros2_type=ROS2MessageType(mapping_data["ros2_type"]),
                endpoint=mapping_data["endpoint"],
                message_type=mapping_data["message_type"],
                parameters=mapping_data.get("parameters", {}),
                qos_profile=mapping_data.get("qos_profile")
            )
            mappings[intent_str] = mapping
        
        return RobotProfile(
            robot_id=data["robot_id"],
            manufacturer=data["manufacturer"],
            model=data["model"],
            capabilities=data["capabilities"],
            ros2_mappings=mappings,
            default_namespace=data.get("default_namespace", "/"),
            version=data.get("version", "1.0")
        )
    
    def _validate_profile(self):
        """Validate robot profile structure and required intents."""
        if not self.profile.ros2_mappings:
            raise ValueError("Profile must contain ROS2 mappings")
        
        # Check for required intents (MOVE_TO_POSE and STOP are mandatory)
        required_intents = {"MOVE_TO_POSE", "STOP"}
        profile_intents = set(self.profile.ros2_mappings.keys())
        missing = required_intents - profile_intents
        
        if missing:
            raise ValueError(f"Missing required intents in profile: {missing}")
    
    def map_to_ros2(self, intent: str, parameters: Dict[str, Any]) -> Tuple[ROS2Mapping, Dict[str, Any]]:
        """
        Map intent and parameters to ROS2 endpoint.
        
        Args:
            intent: Canonical intent name
            parameters: Intent parameters
            
        Returns:
            Tuple of (ROS2Mapping, merged_parameters)
            
        Raises:
            KeyError: If intent is not mapped in profile
        """
        if intent not in self.profile.ros2_mappings:
            raise KeyError(
                f"Intent '{intent}' not mapped in profile for robot {self.profile.robot_id}"
            )
        
        mapping = self.profile.ros2_mappings[intent]
        
        # Merge default parameters with provided ones (provided take precedence)
        merged_params = mapping.parameters.copy()
        merged_params.update(parameters)
        
        return mapping, merged_params
    
    def get_supported_intents(self) -> List[str]:
        """Get list of intents supported by this robot."""
        return list(self.profile.ros2_mappings.keys())
    
    def has_capability(self, capability: str) -> bool:
        """Check if robot has specific capability."""
        return capability in self.profile.capabilities
    
    def to_dict(self) -> Dict[str, Any]:
        """Export profile to dictionary."""
        return asdict(self.profile)