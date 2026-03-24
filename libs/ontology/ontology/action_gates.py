"""Action gates for validating robot commands."""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import logging

logger = logging.getLogger("ontology.action_gates")


@dataclass
class ActionGate:
    """Base class for action gates."""
    gate_id: str
    description: str


class DirectedLookGate(ActionGate):
    """Gate for validating directed look commands."""
    
    def __init__(self):
        super().__init__(
            gate_id="directed_look",
            description="Validates directed look commands (head/body rotation)"
        )
        self.allowed_directions = {"left", "right", "up", "down", "forward"}
        self.max_angle = 90.0  # degrees
        self.min_angle = 0.0   # degrees
        self.min_duration = 0.1  # seconds
        self.max_duration = 10.0  # seconds
        self.safety_margins = {
            "neck": 45.0,  # max neck rotation
            "body": 30.0   # max body rotation
        }
    
    def validate(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a directed look command.
        
        Args:
            command: Dictionary with keys 'direction', 'angle', 'duration'.
        
        Returns:
            Dict with 'ok' (bool), 'reason' (str if not ok), and safety_checks.
        """
        direction = command.get('direction', '').lower()
        angle = command.get('angle', 0.0)
        duration = command.get('duration', 0.0)
        
        # Check direction
        if direction not in self.allowed_directions:
            return {
                'ok': False, 
                'reason': f'Invalid direction: {direction}. Allowed: {self.allowed_directions}'
            }
        
        # Check angle range
        if not isinstance(angle, (int, float)):
            return {'ok': False, 'reason': f'Angle must be a number, got {type(angle)}'}
        
        if angle < self.min_angle or angle > self.max_angle:
            return {
                'ok': False, 
                'reason': f'Angle must be between {self.min_angle} and {self.max_angle} degrees'
            }
        
        # Check duration
        if not isinstance(duration, (int, float)):
            return {'ok': False, 'reason': f'Duration must be a number, got {type(duration)}'}
        
        if duration < self.min_duration or duration > self.max_duration:
            return {
                'ok': False, 
                'reason': f'Duration must be between {self.min_duration} and {self.max_duration} seconds'
            }
        
        # Safety checks based on direction
        safety_violations = []
        if direction in ["left", "right"] and angle > self.safety_margins["neck"]:
            safety_violations.append(f"Neck rotation angle {angle} exceeds safety limit {self.safety_margins['neck']}")
        
        if direction in ["up", "down"] and angle > self.safety_margins["body"]:
            safety_violations.append(f"Body tilt angle {angle} exceeds safety limit {self.safety_margins['body']}")
        
        if safety_violations:
            return {
                'ok': False,
                'reason': 'Safety violation: ' + ', '.join(safety_violations)
            }
        
        return {
            'ok': True,
            'safety_checks': {
                'direction_valid': True,
                'angle_within_limits': True,
                'duration_within_limits': True,
                'safety_margins_respected': True
            }
        }
    
    def get_joint_mapping_suggestion(self, direction: str, angle: float) -> Dict[str, float]:
        """Suggest joint mapping for a direction and angle.
        
        Args:
            direction: Look direction
            angle: Angle in degrees
            
        Returns:
            Dictionary mapping joint names to target angles.
        """
        mapping = {}
        
        if direction == "left":
            mapping = {"neck_yaw": angle, "neck_roll": 0.0}
        elif direction == "right":
            mapping = {"neck_yaw": -angle, "neck_roll": 0.0}
        elif direction == "up":
            mapping = {"neck_pitch": -angle, "neck_roll": 0.0}
        elif direction == "down":
            mapping = {"neck_pitch": angle, "neck_roll": 0.0}
        elif direction == "forward":
            mapping = {"neck_yaw": 0.0, "neck_pitch": 0.0, "neck_roll": 0.0}
        
        return mapping