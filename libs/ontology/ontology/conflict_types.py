"""
Conflict types for rule conflict mediation.
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum

class ConflictSeverity(Enum):
    LOW = "low"  # Незначительный конфликт
    MEDIUM = "medium"  # Требует внимания
    HIGH = "high"  # Критический конфликт
    SAFETY_CRITICAL = "safety_critical"  # Безопасность под угрозой

class ResolutionStrategy(Enum):
    PRIORITY_BASED = "priority_based"  # По приоритету правил
    CONTEXT_AWARE = "context_aware"  # Учитывая контекст
    SAFETY_FIRST = "safety_first"  # Приоритет безопасности
    EFFICIENCY_OPTIMIZED = "efficiency_optimized"  # Оптимизация эффективности
    HUMAN_INTERVENTION = "human_intervention"  # Требует вмешательства оператора

@dataclass
class RuleConflict:
    """Конфликт между двумя или более правилами"""
    conflict_id: str
    conflicting_rules: List[str]  # IDs правил
    conflict_type: str  # Тип конфликта (например, "speed_limit", "zone_access")
    severity: ConflictSeverity
    context: Dict[str, Any]  # Контекст конфликта
    detected_at: float  # timestamp
    
@dataclass
class MediationRequest:
    """Запрос на медиацию конфликта"""
    request_id: str
    conflict: RuleConflict
    available_strategies: List[ResolutionStrategy]
    additional_context: Optional[Dict[str, Any]] = None
    
@dataclass
class MediationResult:
    """Результат медиации конфликта"""
    request_id: str
    recommended_strategy: ResolutionStrategy
    confidence: float  # 0.0-1.0
    reasoning: str  # Объяснение от LLM
    suggested_actions: List[str]  # Предлагаемые действия
    fallback_to: Optional[str] = None  # Fallback стратегия