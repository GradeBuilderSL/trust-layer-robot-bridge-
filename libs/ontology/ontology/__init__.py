"""
Ontology module - структуры данных для описания действий, состояний и правил робота.

Основные классы:
- ActionGate: базовый класс для всех действий
- ActionSequence: составное действие как последовательность шагов
- RobotCommand: элементарная команда роботу
"""

from .action_gate import ActionGate
from .robot_command import RobotCommand

# Импортируем ActionSequence если доступен
try:
    from .action_composer import ActionSequence
    __all__ = ['ActionGate', 'ActionSequence', 'RobotCommand']
except ImportError:
    __all__ = ['ActionGate', 'RobotCommand']
    ActionSequence = None  # type: ignore
