from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum

class CapabilityType(Enum):
    MANIPULATION = "manipulation"
    LOCOMOTION = "locomotion"
    PERCEPTION = "perception"
    COMMUNICATION = "communication"
    POWER = "power"
    SAFETY = "safety"
    NAVIGATION = "navigation"
    SENSING = "sensing"

@dataclass
class RobotCapability:
    """Описание одной capability робота"""
    name: str
    capability_type: CapabilityType
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    is_available: bool = True
    constraints: List[str] = field(default_factory=list)
    
@dataclass
class RobotCapabilities:
    """Контейнер всех capabilities робота"""
    robot_id: str
    robot_model: str
    manufacturer: str = "Unknown"
    capabilities: List[RobotCapability] = field(default_factory=list)
    
    def get_capabilities_by_type(self, capability_type: CapabilityType) -> List[RobotCapability]:
        """Получить capabilities по типу"""
        return [cap for cap in self.capabilities if cap.capability_type == capability_type]
    
    def get_available_capabilities(self) -> List[RobotCapability]:
        """Получить только доступные capabilities"""
        return [cap for cap in self.capabilities if cap.is_available]
    
    def to_prompt_format(self) -> str:
        """Форматирование для включения в промпт"""
        lines = [f"Robot: {self.robot_model} (ID: {self.robot_id})"]
        lines.append(f"Manufacturer: {self.manufacturer}")
        lines.append("\nCapabilities:")
        
        for cap_type in CapabilityType:
            type_caps = self.get_capabilities_by_type(cap_type)
            if type_caps:
                lines.append(f"\n{cap_type.value.upper()}:")
                for cap in type_caps:
                    status = "✓" if cap.is_available else "✗"
                    lines.append(f"  {status} {cap.name}: {cap.description}")
                    if cap.parameters:
                        params_str = ", ".join([f"{k}={v}" for k, v in cap.parameters.items()])
                        lines.append(f"    Parameters: {params_str}")
                    if cap.constraints:
                        lines.append(f"    Constraints: {', '.join(cap.constraints)}")
        
        return "\n".join(lines)