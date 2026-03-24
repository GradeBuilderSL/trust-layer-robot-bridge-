"""
CharacterProfile - структура для хранения характеристик персонажа робота.
Используется для генерации персонализированных system prompt.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import json


@dataclass
class CharacterProfile:
    """Профиль характера робота."""
    
    name: str
    """Имя характера (например, 'Добродушный помощник', 'Строгий инспектор')"""
    
    personality_traits: Dict[str, float]
    """
    Словарь черт характера с их интенсивностью (0.0 - 1.0).
    Пример: {'дружелюбие': 0.9, 'профессионализм': 0.8, 'юмор': 0.3}
    """
    
    communication_style: str
    """
    Стиль общения.
    Пример: 'формальный', 'дружелюбный', 'лаконичный', 'подробный'
    """
    
    expertise_level: str
    """
    Уровень экспертизы.
    Пример: 'новичок', 'опытный', 'эксперт'
    """
    
    safety_priority: float
    """
    Приоритет безопасности (0.0 - 1.0).
    Высокое значение означает более консервативное поведение.
    """
    
    additional_notes: str = ""
    """Дополнительные заметки о характере."""
    
    metadata: Dict[str, Any] = field(default_factory=dict)
    """Метаданные профиля."""
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать профиль в словарь."""
        return {
            'name': self.name,
            'personality_traits': self.personality_traits,
            'communication_style': self.communication_style,
            'expertise_level': self.expertise_level,
            'safety_priority': self.safety_priority,
            'additional_notes': self.additional_notes,
            'metadata': self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CharacterProfile':
        """Создать профиль из словаря."""
        return cls(
            name=data['name'],
            personality_traits=data['personality_traits'],
            communication_style=data['communication_style'],
            expertise_level=data['expertise_level'],
            safety_priority=data['safety_priority'],
            additional_notes=data.get('additional_notes', ''),
            metadata=data.get('metadata', {})
        )
    
    def to_json(self) -> str:
        """Конвертировать профиль в JSON строку."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'CharacterProfile':
        """Создать профиль из JSON строки."""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    def get_top_traits(self, n: int = 3) -> List[tuple]:
        """Получить топ-N черт характера по интенсивности."""
        sorted_traits = sorted(
            self.personality_traits.items(), 
            key=lambda x: x[1], 
            reverse=True
        )
        return sorted_traits[:n]
    
    def get_trait_value(self, trait: str) -> float:
        """Получить значение черты характера (0.0 если не существует)."""
        return self.personality_traits.get(trait, 0.0)
    
    def validate(self) -> bool:
        """Валидировать профиль."""
        if not self.name:
            return False
        if not isinstance(self.personality_traits, dict):
            return False
        if not all(0.0 <= v <= 1.0 for v in self.personality_traits.values()):
            return False
        if not self.communication_style:
            return False
        if not self.expertise_level:
            return False
        if not 0.0 <= self.safety_priority <= 1.0:
            return False
        return True

# Constants used for character profile validation
class ToneSpectrumConstants:
    FORMAL = "formal"
    CASUAL = "casual"
    TECHNICAL = "technical"
    FRIENDLY = "friendly"
    AUTHORITATIVE = "authoritative"
    ALL = [FORMAL, CASUAL, TECHNICAL, FRIENDLY, AUTHORITATIVE]

class SpeechPatternConstants:
    TECHNICAL_TERMS = "uses_technical_terms"
    EXPLANATIONS = "provides_explanations"
    DIRECT = "direct_speech"
    ALL = [TECHNICAL_TERMS, EXPLANATIONS, DIRECT]

class EmotionalColoringConstants:
    NEUTRAL = "neutral"
    WARM = "warm"
    PROFESSIONAL = "professional"
    ALL = [NEUTRAL, WARM, PROFESSIONAL]

