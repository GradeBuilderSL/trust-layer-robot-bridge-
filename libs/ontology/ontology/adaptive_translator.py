"""AdaptiveTranslator — система адаптивного преобразования команд для разных типов роботов.

Детерминированные правила преобразования одной и той же команды для разных роботов.
Например: "посмотри налево" → поворот головы (humanoid) или поворот корпуса (AMR).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from ontology.robot_command import RobotType, RobotCapabilities, RobotCommand

logger = logging.getLogger(__name__)


@dataclass
class TranslationResult:
    """Результат адаптивного преобразования команды."""
    success: bool
    robot_command: Optional[RobotCommand] = None
    error_message: str = ""
    alternatives_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразовать в словарь."""
        result = {
            "success": self.success,
            "alternatives_count": self.alternatives_count,
            "error_message": self.error_message,
        }
        if self.robot_command:
            result["robot_command"] = self.robot_command.to_dict()
        return result


class CommandTranslator(ABC):
    """Абстрактный базовый класс для адаптивных переводчиков команд."""
    
    @abstractmethod
    def can_translate(self, command: str, action_type: str) -> bool:
        """Может ли этот переводчик обработать данную команду."""
        pass
    
    @abstractmethod
    def translate(
        self, 
        command: str, 
        action_type: str,
        parameters: Dict[str, Any],
        robot_type: RobotType,
        robot_capabilities: RobotCapabilities
    ) -> Optional[RobotCommand]:
        """Преобразовать команду для заданного типа робота."""
        pass
    
    @abstractmethod
    def get_supported_robot_types(self) -> List[RobotType]:
        """Получить список поддерживаемых типов роботов."""
        pass


class LookCommandTranslator(CommandTranslator):
    """Переводчик команд взгляда/поворота."""
    
    def can_translate(self, command: str, action_type: str) -> bool:
        return action_type in ["look", "gaze", "turn", "rotate"]
    
    def translate(
        self, 
        command: str, 
        action_type: str,
        parameters: Dict[str, Any],
        robot_type: RobotType,
        robot_capabilities: RobotCapabilities
    ) -> Optional[RobotCommand]:
        """Преобразовать команду взгляда для разных типов роботов."""
        
        # Базовые параметры
        direction = parameters.get("direction", "forward")
        angle = parameters.get("angle", 45.0)  # градусы
        speed = parameters.get("speed", 0.5)  # 0-1
        
        # Создаем базовую команду
        robot_command = RobotCommand(
            command_id=f"look_{direction}_{int(angle)}",
            action_type="look",
            parameters=parameters,
            original_request=command,
            source="adaptive_translator"
        )
        
        # Добавляем адаптивные реализации для разных типов роботов
        
        # 1. HUMANOID - поворот головы
        if robot_capabilities.can_turn_head:
            robot_command.add_alternative(
                implementation={
                    "action": "turn_head",
                    "params": {
                        "direction": direction,
                        "angle_deg": min(angle, robot_capabilities.max_head_rotation_deg),
                        "speed": speed * robot_capabilities.max_rotation_speed_deg,
                        "joint": "neck_yaw"
                    }
                },
                robot_type=RobotType.HUMANOID,
                description=f"Поворот головы {direction} на {angle}°",
                safety_score=0.9 if angle <= 90 else 0.7
            )
        
        # 2. AMR/MANIPULATOR/GENERIC - поворот корпуса
        if robot_capabilities.can_turn_body:
            robot_command.add_alternative(
                implementation={
                    "action": "turn_body",
                    "params": {
                        "direction": direction,
                        "angle_deg": min(angle, robot_capabilities.max_body_rotation_deg),
                        "speed": speed * robot_capabilities.max_rotation_speed_deg,
                        "coordinate_frame": "base_link"
                    }
                },
                robot_type=RobotType.AMR,
                description=f"Поворот корпуса {direction} на {angle}°",
                safety_score=0.8
            )
            
            # Также для других типов роботов с корпусом
            for rt in [RobotType.MANIPULATOR, RobotType.GENERIC]:
                robot_command.add_alternative(
                    implementation={
                        "action": "turn_body",
                        "params": {
                            "direction": direction,
                            "angle_deg": min(angle, robot_capabilities.max_body_rotation_deg),
                            "speed": speed * robot_capabilities.max_rotation_speed_deg,
                            "coordinate_frame": "base_link"
                        }
                    },
                    robot_type=rt,
                    description=f"Поворот {rt.value} {direction} на {angle}°",
                    safety_score=0.8
                )
        
        # 3. DRONE - изменение ориентации или позиции
        if robot_capabilities.can_fly:
            robot_command.add_alternative(
                implementation={
                    "action": "adjust_yaw",
                    "params": {
                        "direction": direction,
                        "angle_deg": angle,
                        "speed": speed,
                        "stabilize": True
                    }
                },
                robot_type=RobotType.DRONE,
                description=f"Изменение курса дрона {direction} на {angle}°",
                safety_score=0.6  # Ниже, т.к. полет более рискован
            )
        
        # Выбираем наилучшую реализацию для заданного типа робота
        if robot_command.select_best_implementation(robot_type, robot_capabilities):
            return robot_command
        
        return None
    
    def get_supported_robot_types(self) -> List[RobotType]:
        return [RobotType.HUMANOID, RobotType.AMR, RobotType.DRONE, 
                RobotType.MANIPULATOR, RobotType.GENERIC]


class MoveCommandTranslator(CommandTranslator):
    """Переводчик команд движения."""
    
    def can_translate(self, command: str, action_type: str) -> bool:
        return action_type in ["move", "go", "navigate", "drive"]
    
    def translate(
        self, 
        command: str, 
        action_type: str,
        parameters: Dict[str, Any],
        robot_type: RobotType,
        robot_capabilities: RobotCapabilities
    ) -> Optional[RobotCommand]:
        """Преобразовать команду движения для разных типов роботов."""
        
        distance = parameters.get("distance", 1.0)  # метры
        direction = parameters.get("direction", "forward")
        speed = parameters.get("speed", 0.3)  # m/s
        
        # Ограничиваем скорость возможностями робота
        speed = min(speed, robot_capabilities.max_speed_mps)
        
        robot_command = RobotCommand(
            command_id=f"move_{direction}_{int(distance)}",
            action_type="move",
            parameters=parameters,
            original_request=command,
            source="adaptive_translator"
        )
        
        # Общая реализация для наземных роботов
        if robot_capabilities.can_move_independently and not robot_capabilities.can_fly:
            for rt in [RobotType.HUMANOID, RobotType.AMR, RobotType.GENERIC]:
                robot_command.add_alternative(
                    implementation={
                        "action": "move_linear",
                        "params": {
                            "direction": direction,
                            "distance_m": distance,
                            "speed_mps": speed,
                            "obstacle_avoidance": True
                        }
                    },
                    robot_type=rt,
                    description=f"Движение {direction} на {distance}м",
                    safety_score=0.85 if speed <= 1.0 else 0.7
                )
        
        # Для дронов
        if robot_capabilities.can_fly:
            robot_command.add_alternative(
                implementation={
                    "action": "fly_to",
                    "params": {
                        "direction": direction,
                        "distance_m": distance,
                        "speed_mps": speed,
                        "altitude_m": parameters.get("altitude", 2.0),
                        "auto_land": False
                    }
                },
                robot_type=RobotType.DRONE,
                description=f"Полет {direction} на {distance}м",
                safety_score=0.7  # Ниже из-за рисков полета
            )
        
        if robot_command.select_best_implementation(robot_type, robot_capabilities):
            return robot_command
        
        return None
    
    def get_supported_robot_types(self) -> List[RobotType]:
        return [RobotType.HUMANOID, RobotType.AMR, RobotType.DRONE, 
                RobotType.GENERIC]


class GraspCommandTranslator(CommandTranslator):
    """Переводчик команд захвата/манипуляции."""
    
    def can_translate(self, command: str, action_type: str) -> bool:
        return action_type in ["grasp", "pick", "hold", "grip"]
    
    def translate(
        self, 
        command: str, 
        action_type: str,
        parameters: Dict[str, Any],
        robot_type: RobotType,
        robot_capabilities: RobotCapabilities
    ) -> Optional[RobotCommand]:
        """Преобразовать команду захвата для разных типов роботов."""
        
        if not robot_capabilities.has_gripper and not robot_capabilities.has_arms:
            return None
        
        object_name = parameters.get("object", "object")
        force = parameters.get("force", 0.5)
        
        robot_command = RobotCommand(
            command_id=f"grasp_{object_name}",
            action_type="grasp",
            parameters=parameters,
            original_request=command,
            source="adaptive_translator"
        )
        
        # Для роботов с захватом
        if robot_capabilities.has_gripper:
            for rt in [RobotType.MANIPULATOR, RobotType.AMR, RobotType.HUMANOID]:
                robot_command.add_alternative(
                    implementation={
                        "action": "close_gripper",
                        "params": {
                            "object": object_name,
                            "force": force,
                            "pre_grasp_pose": True,
                            "detect_slip": True
                        }
                    },
                    robot_type=rt,
                    description=f"Захват объекта '{object_name}'",
                    safety_score=0.8 if force <= 0.7 else 0.6
                )
        
        # Для роботов с руками (humanoid)
        if robot_capabilities.has_arms:
            robot_command.add_alternative(
                implementation={
                    "action": "grasp_with_hand",
                    "params": {
                        "object": object_name,
                        "hand": parameters.get("hand", "right"),
                        "grip_type": parameters.get("grip_type", "power"),
                        "force": force
                    }
                },
                robot_type=RobotType.HUMANOID,
                description=f"Захват объекта '{object_name}' рукой",
                safety_score=0.75  # Сложнее контролировать силу
            )
        
        if robot_command.select_best_implementation(robot_type, robot_capabilities):
            return robot_command
        
        return None
    
    def get_supported_robot_types(self) -> List[RobotType]:
        return [RobotType.HUMANOID, RobotType.MANIPULATOR, RobotType.AMR]


class AdaptiveTranslationEngine:
    """Движок адаптивного преобразования команд."""
    
    def __init__(self):
        self.translators: List[CommandTranslator] = [
            LookCommandTranslator(),
            MoveCommandTranslator(),
            GraspCommandTranslator(),
        ]
        self.logger = logging.getLogger(__name__)
    
    def translate_command(
        self,
        command_text: str,
        action_type: str,
        parameters: Dict[str, Any],
        robot_type: Union[RobotType, str],
        robot_capabilities: Union[RobotCapabilities, Dict[str, Any]]
    ) -> TranslationResult:
        """Адаптивно преобразовать команду для заданного робота."""
        
        # Нормализация входных данных
        if isinstance(robot_type, str):
            try:
                robot_type = RobotType(robot_type.lower())
            except ValueError:
                robot_type = RobotType.GENERIC
        
        if isinstance(robot_capabilities, dict):
            robot_capabilities = RobotCapabilities.from_profile(robot_capabilities)
        
        # Ищем подходящий переводчик
        translator = None
        for t in self.translators:
            if t.can_translate(command_text, action_type):
                translator = t
                break
        
        if not translator:
            return TranslationResult(
                success=False,
                error_message=f"No translator found for action type: {action_type}"
            )
        
        # Пытаемся преобразовать
        try:
            robot_command = translator.translate(
                command_text,
                action_type,
                parameters,
                robot_type,
                robot_capabilities
            )
            
            if robot_command:
                return TranslationResult(
                    success=True,
                    robot_command=robot_command,
                    alternatives_count=len(robot_command.alternative_implementations)
                )
            else:
                return TranslationResult(
                    success=False,
                    error_message=f"Translator failed to create command for {robot_type.value}"
                )
                
        except Exception as e:
            self.logger.error(f"Translation error: {e}", exc_info=True)
            return TranslationResult(
                success=False,
                error_message=f"Translation error: {str(e)}"
            )
    
    def get_supported_actions(self) -> List[str]:
        """Получить список поддерживаемых типов действий."""
        actions = set()
        for translator in self.translators:
            # Упрощенная логика определения типов действий
            if isinstance(translator, LookCommandTranslator):
                actions.update(["look", "gaze", "turn", "rotate"])
            elif isinstance(translator, MoveCommandTranslator):
                actions.update(["move", "go", "navigate", "drive"])
            elif isinstance(translator, GraspCommandTranslator):
                actions.update(["grasp", "pick", "hold", "grip"])
        return list(actions)


# Глобальный экземпляр для использования в других модулях
_translation_engine = None

def get_translation_engine() -> AdaptiveTranslationEngine:
    """Получить глобальный экземпляр AdaptiveTranslationEngine."""
    global _translation_engine
    if _translation_engine is None:
        _translation_engine = AdaptiveTranslationEngine()
    return _translation_engine