"""
Visitor Questions module - структуры данных для обработки вопросов посетителей.

Определяет типы вопросов и ответов для когнитивного слоя.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List


class QuestionType(str, Enum):
    """Типы вопросов посетителей."""
    GENERAL = "general"          # Общие вопросы
    SAFETY = "safety"            # Вопросы безопасности
    LOCATION = "location"        # Вопросы о местоположении
    OPERATIONAL = "operational"  # Вопросы о работе/операциях
    EXHIBITION = "exhibition"   # Выставочные вопросы
    DIRECTIONS = "directions"   # Указания направления


@dataclass
class QuestionContext:
    """Контекст вопроса посетителя."""
    location: Optional[str] = None           # Местоположение посетителя
    robot_id: Optional[str] = None           # ID робота
    language: str = "ru"                     # Язык вопроса
    timestamp: Optional[float] = None        # Временная метка
    session_id: Optional[str] = None         # ID сессии
    visitor_type: Optional[str] = None       # Тип посетителя (ребенок, взрослый и т.д.)


@dataclass
class VisitorQuestion:
    """Вопрос посетителя."""
    question: str                           # Текст вопроса
    question_type: QuestionType = QuestionType.GENERAL  # Тип вопроса
    context: Optional[QuestionContext] = None  # Контекст вопроса
    metadata: Dict[str, Any] = None         # Дополнительные метаданные
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.context is None:
            self.context = QuestionContext()


@dataclass
class VisitorAnswer:
    """Ответ на вопрос посетителя."""
    answer: str                            # Текст ответа
    confidence_score: float = 1.0          # Уверенность в ответе (0.0-1.0)
    source: str = "knowledge_base"         # Источник ответа
    source_id: Optional[str] = None        # ID источника (если есть)
    references: List[str] = None           # Ссылки на источники
    suggested_actions: List[str] = None    # Предлагаемые действия
    reason_code: str = "SUCCESS"           # Код результата
    
    def __post_init__(self):
        if self.references is None:
            self.references = []
        if self.suggested_actions is None:
            self.suggested_actions = []
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразовать в словарь."""
        return {
            "answer": self.answer,
            "confidence_score": self.confidence_score,
            "source": self.source,
            "source_id": self.source_id,
            "references": self.references,
            "suggested_actions": self.suggested_actions,
            "reason_code": self.reason_code
        }