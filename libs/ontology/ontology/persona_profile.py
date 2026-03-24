"""
Устаревший модуль persona_profile с тремя пресетами.

Deprecated: используйте character_profile.CharacterProfile.

Этот модуль оставлен для обратной совместимости и будет удален в будущих версиях.
"""
import warnings
from typing import Dict, Any, Optional
import sys
import os

# Добавляем путь для импорта из libs
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from ontology.character_profile import CharacterProfile
except ImportError:
    CharacterProfile = None

warnings.warn(
    "persona_profile is deprecated. Use character_profile.CharacterProfile instead.",
    DeprecationWarning,
    stacklevel=2
)


class PersonaProfile:
    """
    Устаревший класс для профиля персонажа с тремя пресетами.
    
    Deprecated: используйте CharacterProfile.
    """
    
    VALID_TYPES = ["formal", "casual", "technical"]
    
    def __init__(self, persona_type: str = "formal"):
        """
        Инициализирует persona profile.
        
        Args:
            persona_type: Тип персонажа (formal, casual, technical)
            
        Raises:
            ValueError: если тип невалидный
        """
        if persona_type not in self.VALID_TYPES:
            raise ValueError(
                f"Invalid persona_type: {persona_type}. "
                f"Must be one of {self.VALID_TYPES}"
            )
        
        warnings.warn(
            f"PersonaProfile is deprecated. Use CharacterProfile instead. "
            f"(persona_type={persona_type})",
            DeprecationWarning,
            stacklevel=2
        )
        
        self.persona_type = persona_type
        self._character_profile: Optional[CharacterProfile] = None
    
    @property
    def character_profile(self) -> CharacterProfile:
        """
        Конвертирует в CharacterProfile.
        
        Returns:
            CharacterProfile: эквивалентный CharacterProfile
            
        Raises:
            RuntimeError: если CharacterProfile недоступен
        """
        if CharacterProfile is None:
            raise RuntimeError(
                "CharacterProfile is not available. "
                "Cannot convert deprecated PersonaProfile."
            )
        
        if self._character_profile is None:
            pass