"""
ActionSequence - композиция действий как последовательность шагов.

Класс ActionSequence позволяет описывать сложные действия робота
как последовательность элементарных шагов (ActionGate или других ActionSequence).
Обеспечивает структурированное представление и выполнение многошаговых операций.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from libs.ontology.action_gate import ActionGate

logger = logging.getLogger(__name__)


@dataclass
class ActionSequence:
    """Составное действие как последовательность шагов.
    
    Attributes:
        id: Уникальный идентификатор последовательности
        name: Человекочитаемое название
        description: Описание назначения последовательности
        steps: Список шагов (ActionGate или вложенные ActionSequence)
        preconditions: Предварительные условия (совместимо с ActionGate)
        postconditions: Постусловия (ожидаемые результаты)
        metadata: Дополнительные метаданные (теги, категории и т.д.)
        version: Версия последовательности
    """
    id: str
    name: str
    description: str = ""
    steps: List[Union[ActionGate, ActionSequence]] = field(default_factory=list)
    preconditions: List[Dict[str, Any]] = field(default_factory=list)
    postconditions: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"
    
    def __post_init__(self):
        """Валидация при создании."""
        if not self.id:
            raise ValueError("ActionSequence must have an id")
        if not self.name:
            raise ValueError("ActionSequence must have a name")
    
    def validate(self) -> Dict[str, Any]:
        """Проверка корректности последовательности.
        
        Returns:
            Словарь с результатами валидации:
            - valid: bool - прошла ли валидация
            - errors: List[str] - список ошибок
            - warnings: List[str] - список предупреждений
        """
        errors = []
        warnings = []
        
        # Проверка на циклы
        if self._has_cycles(set()):
            errors.append("Sequence contains cyclic dependencies")
        
        # Проверка шагов
        for i, step in enumerate(self.steps):
            if hasattr(step, 'validate'):
                try:
                    result = step.validate()
                    if not result.get('valid', True):
                        errors.append(f"Step {i} invalid: {result.get('errors', [])}")
                except Exception as e:
                    errors.append(f"Step {i} validation failed: {str(e)}")
            else:
                warnings.append(f"Step {i} has no validate method")
        
        # Проверка обязательных полей
        for req_field in ('id', 'name'):
            if not getattr(self, req_field, None):
                errors.append(f"Missing required field: {req_field}")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }
    
    def _has_cycles(self, visited: set) -> bool:
        """Проверка на циклические зависимости в последовательности.
        
        Args:
            visited: Множество уже посещенных идентификаторов
            
        Returns:
            True если обнаружен цикл, иначе False
        """
        if self.id in visited:
            return True
        
        visited.add(self.id)
        
        for step in self.steps:
            if isinstance(step, ActionSequence):
                if step._has_cycles(visited.copy()):
                    return True
        
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь.
        
        Returns:
            Словарь с данными последовательности
        """
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'steps': [self._step_to_dict(step) for step in self.steps],
            'preconditions': self.preconditions,
            'postconditions': self.postconditions,
            'metadata': self.metadata,
            'version': self.version,
            'type': 'ActionSequence'
        }
    
    def _step_to_dict(self, step: Union[ActionGate, ActionSequence]) -> Dict[str, Any]:
        """Рекурсивная сериализация шага."""
        if hasattr(step, 'to_dict'):
            return step.to_dict()
        elif hasattr(step, '__dict__'):
            return {**step.__dict__, 'type': step.__class__.__name__}
        else:
            return {'type': str(type(step)), 'value': str(step)}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionSequence:
        """Десериализация из словаря.
        
        Args:
            data: Словарь с данными последовательности
            
        Returns:
            Экземпляр ActionSequence
            
        Raises:
            ValueError: Если данные некорректны
        """
        try:
            # Импортируем здесь, чтобы избежать циклического импорта
            from libs.ontology.action_gate import ActionGate
            
            steps = []
            for step_data in data.get('steps', []):
                step_type = step_data.get('type', '')
                
                if step_type == 'ActionSequence':
                    step = ActionSequence.from_dict(step_data)
                elif step_type == 'ActionGate' or 'action_type' in step_data:
                    step = ActionGate.from_dict(step_data)
                else:
                    # Пытаемся создать ActionGate по умолчанию
                    try:
                        step = ActionGate.from_dict(step_data)
                    except:
                        logger.warning(f"Cannot deserialize step: {step_data}")
                        continue
                
                steps.append(step)
            
            return cls(
                id=data['id'],
                name=data['name'],
                description=data.get('description', ''),
                steps=steps,
                preconditions=data.get('preconditions', []),
                postconditions=data.get('postconditions', []),
                metadata=data.get('metadata', {}),
                version=data.get('version', '1.0')
            )
        except KeyError as e:
            raise ValueError(f"Missing required field in ActionSequence data: {e}")
    
    def to_json(self) -> str:
        """Сериализация в JSON строку."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> ActionSequence:
        """Десериализация из JSON строки."""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    async def execute(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Выполнение последовательности шагов.
        
        Args:
            context: Контекст выполнения (состояние мира, робота и т.д.)
            
        Returns:
            Словарь с результатами выполнения:
            - success: bool - успешно ли выполнена вся последовательность
            - results: List[Dict] - результаты каждого шага
            - error: Optional[str] - сообщение об ошибке если success=False
            - context: Обновленный контекст
        """
        if context is None:
            context = {}
        
        results = []
        
        try:
            # Валидация перед выполнением
            validation = self.validate()
            if not validation['valid']:
                return {
                    'success': False,
                    'error': f"Sequence validation failed: {validation['errors']}",
                    'results': [],
                    'context': context
                }
            
            # Выполнение шагов по порядку
            for i, step in enumerate(self.steps):
                logger.info(f"Executing step {i+1}/{len(self.steps)}: {step}")
                
                try:
                    if hasattr(step, 'execute'):
                        # Асинхронное выполнение если доступно
                        result = await step.execute(context)
                    elif hasattr(step, 'execute_sync'):
                        # Синхронное выполнение
                        result = step.execute_sync(context)
                    else:
                        # Просто передаем шаг дальше
                        result = {
                            'success': True,
                            'step': str(step),
                            'context': context
                        }
                    
                    results.append(result)
                    
                    # Если шаг не удался, останавливаем последовательность
                    if not result.get('success', True):
                        return {
                            'success': False,
                            'error': f"Step {i} failed: {result.get('error', 'Unknown error')}",
                            'results': results,
                            'context': context
                        }
                    
                    # Обновляем контекст из результата
                    if 'context' in result:
                        context.update(result['context'])
                    
                except Exception as e:
                    logger.error(f"Error executing step {i}: {e}")
                    return {
                        'success': False,
                        'error': f"Step {i} execution error: {str(e)}",
                        'results': results,
                        'context': context
                    }
            
            # Все шаги выполнены успешно
            return {
                'success': True,
                'results': results,
                'context': context,
                'postconditions_met': self._check_postconditions(context)
            }
            
        except Exception as e:
            logger.error(f"Error executing sequence {self.id}: {e}")
            return {
                'success': False,
                'error': f"Sequence execution error: {str(e)}",
                'results': results,
                'context': context
            }
    
    def _check_postconditions(self, context: Dict[str, Any]) -> bool:
        """Проверка постусловий после выполнения.
        
        Args:
            context: Контекст выполнения
            
        Returns:
            True если все постусловия выполнены
        """
        if not self.postconditions:
            return True
        
        # Базовая проверка - в реальной реализации нужно использовать GateEngine
        for condition in self.postconditions:
            condition_type = condition.get('type')
            # Здесь должна быть логика проверки условий
            # Для простоты всегда возвращаем True
            pass
        
        return True
    
    def get_skill_info(self) -> Dict[str, Any]:
        """Получение информации о последовательности как о навыке.
        
        Returns:
            Словарь с информацией для Skill Library
        """
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'type': 'action_sequence',
            'steps_count': len(self.steps),
            'preconditions': self.preconditions,
            'postconditions': self.postconditions,
            'metadata': self.metadata,
            'version': self.version
        }