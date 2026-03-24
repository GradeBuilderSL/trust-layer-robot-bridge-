"""
Robot profile definitions for command mapping.

Defines structures for mapping canonical intents to robot-specific ROS2
endpoints based on robot capabilities.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


class ROS2MessageType(Enum):
    """Type of ROS2 communication endpoint."""
    TOPIC = "topic"
    ACTION = "action"
    SERVICE = "service"


@dataclass
class ROS2Mapping:
    """Mapping of a single intent to ROS2 endpoint."""
    intent: str  # Canonical intent name from ActionGate
    ros2_type: ROS2MessageType
    endpoint: str  # Topic/action/service name
    message_type: str  # ROS2 message type
    parameters: Dict[str, Any] = field(default_factory=dict)  # Default parameters
    qos_profile: Optional[str] = None  # QoS profile name


@dataclass
class RobotProfile:
    """Profile of a specific robot model."""
    robot_id: str
    manufacturer: str
    model: str
    capabilities: List[str]  # Supported capabilities
    ros2_mappings: Dict[str, ROS2Mapping]  # intent -> mapping
    default_namespace: str = "/"
    version: str = "1.0"