from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import json

# Добавляем импорт нового модуля robot_capabilities
from libs.ontology.robot_capabilities import RobotCapabilities, CapabilityType

@dataclass
class PromptContext:
    """Контекст для построения промпта"""
    robot_id: str
    robot_model: str
    telemetry: Optional[Dict[str, Any]] = None
    robot_capabilities: Optional[RobotCapabilities] = None
    ros2_discovery_summary: Optional[str] = None
    action_constraints: List[str] = field(default_factory=list)
    environment_context: Dict[str, Any] = field(default_factory=dict)
    action_description: Optional[str] = None

class PromptBuilder:
    """Построитель промптов с инъекцией capabilities и ROS2 данных"""
    
    def __init__(self, ros2_node=None):
        if ros2_node:
            from libs.robot_interface.ros2_discovery import ROS2Discovery
            self.ros2_discovery = ROS2Discovery(ros2_node)
        else:
            self.ros2_discovery = None
        
    def build_trust_assessment_prompt(self, context: PromptContext) -> str:
        """Построить промпт для оценки доверия с capabilities"""
        
        # Базовый системный промпт
        system_prompt = """You are a robotic trust assessment system. Your task is to evaluate whether a proposed robot action should be trusted based on:
1. Robot's current capabilities and state
2. Safety constraints and regulations
3. Environmental context
4. Historical performance

You must respond with a structured assessment including trust score (0-100), confidence level, and specific reasons."""
        
        # Контекст capabilities
        capabilities_section = ""
        if context.robot_capabilities:
            capabilities_section = f"""
ROBOT CAPABILITIES:
{context.robot_capabilities.to_prompt_format()}
"""
        
        # ROS2 discovery данные
        discovery_section = ""
        if context.ros2_discovery_summary:
            discovery_section = f"""
ROS2 SYSTEM STATE:
{context.ros2_discovery_summary}
"""
        elif self.ros2_discovery and context.telemetry:
            # Динамически получить discovery данные если не предоставлены
            discovery_section = f"""
ROS2 SYSTEM STATE:
{self.ros2_discovery.get_discovery_summary(context.telemetry)}
"""
        
        # Текущая телеметрия
        telemetry_section = ""
        if context.telemetry:
            telemetry_section = f"""
CURRENT TELEMETRY:
- Robot: {context.telemetry.get('robot_model', context.robot_model)} ({context.telemetry.get('robot_id', context.robot_id)})
- Position: {context.telemetry.get('position', 'Unknown')}
- Battery: {context.telemetry.get('battery_level', 'Unknown')}%
- Safety stop: {context.telemetry.get('safety_stop_active', False)}
- Timestamp: {context.telemetry.get('timestamp', 'Unknown')}
"""
        
        # Ограничения действий
        constraints_section = ""
        if context.action_constraints:
            constraints_section = f"""
ACTION CONSTRAINTS:
{chr(10).join(f'- {constraint}' for constraint in context.action_constraints)}
"""
        
        # Контекст окружения
        environment_section = ""
        if context.environment_context:
            env_items = [f"{k}: {v}" for k, v in context.environment_context.items()]
            environment_section = f"""
ENVIRONMENT CONTEXT:
{chr(10).join(env_items)}
"""
        
        # Сборка полного промпта
        full_prompt = f"""{system_prompt}

{capabilities_section}
{discovery_section}
{telemetry_section}
{constraints_section}
{environment_section}

ASSESSMENT REQUEST:
Based on the above information, assess the trustworthiness of the proposed robot action. Consider:
1. Does the robot have the physical capability to perform this action?
2. Are all necessary ROS2 components available and functioning?
3. Does the action violate any safety constraints?
4. Is the robot in a suitable state (battery, safety, etc.)?

Provide your assessment in JSON format with: trust_score, confidence, reasons, and capability_check.
"""
        
        return full_prompt
    
    def build_capability_aware_prompt(self, context: PromptContext) -> str:
        """Построить промпт для конкретного действия с учетом capabilities"""
        
        if not context.action_description:
            context.action_description = "unspecified action"
        
        prompt = f"""Action to assess: {context.action_description}

Robot capabilities:
{context.robot_capabilities.to_prompt_format() if context.robot_capabilities else "No capability data available"}

Current system state:
{context.ros2_discovery_summary or "No ROS2 discovery data"}

Question: Can the robot safely and effectively perform this action given its current capabilities and state?
Consider:
1. Physical capability match
2. System readiness
3. Safety implications
4. Performance constraints

Answer with detailed reasoning."""
        
        return prompt
    
    def build_command_generation_prompt(self, context: PromptContext, user_query: str) -> str:
        """Построить промпт для генерации команд с учетом capabilities"""
        
        capabilities_text = context.robot_capabilities.to_prompt_format() if context.robot_capabilities else "No capability data available"
        discovery_text = context.ros2_discovery_summary or "No ROS2 discovery data"
        
        prompt = f"""You are a robot command generator. Generate appropriate commands for the robot based on the user query and the robot's capabilities.

USER QUERY: {user_query}

ROBOT CAPABILITIES:
{capabilities_text}

ROS2 SYSTEM STATE:
{discovery_text}

INSTRUCTIONS:
1. Only generate commands that the robot can physically perform based on its capabilities
2. Consider the current ROS2 system state when selecting appropriate actions
3. Include necessary safety checks
4. Format the response as a JSON array of commands with parameters

Generate commands that match the robot's capabilities and current system state."""
        
        return prompt