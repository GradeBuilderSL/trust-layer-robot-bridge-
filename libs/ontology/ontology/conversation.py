"""Conversation Context Manager — управление историей диалогов со скользящим окном и суммаризацией.

Заменяет простой словарь _sessions в trust_edge для эффективного управления
длинными диалогами с автоматическим сохранением контекста через суммаризацию.

Основные возможности:
- Скользящее окно сообщений (последние N сообщений в памяти)
- Автоматическая суммаризация старых сообщений при превышении порога
- Сохранение контекста диалога при длинных сессиях
- Поддержка различных ролей (user, assistant, system)
- Интеграция с LLM для создания суммаризаций (опционально)

Использование:
    from ontology.conversation import ConversationContextManager
    
    manager = ConversationContextManager(max_window_size=10, summary_threshold=20)
    manager.add_message("session1", "user", "Привет, как дела?")
    context, summary = manager.get_context("session1")
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ConversationMessage:
    """Сообщение в контексте диалога."""
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь для сериализации."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConversationMessage:
        """Создание из словаря."""
        timestamp = datetime.fromisoformat(data["timestamp"]) if isinstance(data["timestamp"], str) else data["timestamp"]
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=timestamp,
            metadata=data.get("metadata", {})
        )


class ConversationContextManager:
    """
    Менеджер контекста диалога со скользящим окном и суммаризацией.
    
    Attributes:
        max_window_size: Максимальное количество сообщений в активном окне
        summary_threshold: Порог сообщений для запуска суммаризации
    """
    
    def __init__(self, max_window_size: int = 10, summary_threshold: int = 20):
        """
        Инициализация менеджера контекста.
        
        Args:
            max_window_size: Максимальное количество сообщений в скользящем окне
            summary_threshold: Количество сообщений, после которого запускается суммаризация
        """
        self._conversations: Dict[str, List[ConversationMessage]] = defaultdict(list)
        self._summaries: Dict[str, str] = {}
        self._all_messages: Dict[str, List[ConversationMessage]] = defaultdict(list)  # Все сообщения для суммаризации
        self.max_window_size = max_window_size
        self.summary_threshold = summary_threshold
        
        # Статистика для мониторинга
        self._stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {
            "messages_total": 0,
            "summaries_created": 0,
            "window_trims": 0
        })
    
    def add_message(
        self, 
        session_id: str, 
        role: str, 
        content: str, 
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Добавление сообщения в контекст диалога.
        
        Args:
            session_id: Идентификатор сессии
            role: Роль отправителя (user/assistant/system)
            content: Текст сообщения
            metadata: Дополнительные метаданные
        """
        if metadata is None:
            metadata = {}
            
        message = ConversationMessage(
            role=role,
            content=content,
            metadata=metadata
        )
        
        # Добавляем в активное окно
        self._conversations[session_id].append(message)
        
        # Добавляем в полную историю
        self._all_messages[session_id].append(message)
        
        # Обновляем статистику
        self._stats[session_id]["messages_total"] += 1
        
        # Проверяем необходимость обрезки окна
        if len(self._conversations[session_id]) > self.max_window_size:
            self._trim_window(session_id)
        
        # Проверяем необходимость суммаризации
        if self._should_summarize(session_id):
            self._create_summary(session_id)
    
    def get_context(self, session_id: str) -> Tuple[str, Optional[str]]:
        """
        Получение текущего контекста диалога.
        
        Args:
            session_id: Идентификатор сессии
            
        Returns:
            Tuple[строка контекста, опциональная суммаризация]
        """
        if session_id not in self._conversations:
            return "", None
        
        messages = self._conversations[session_id]
        summary = self._summaries.get(session_id)
        
        # Форматируем контекст
        context_lines = []
        for msg in messages:
            context_lines.append(f"{msg.role}: {msg.content}")
        
        context = "\n".join(context_lines)
        return context, summary
    
    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Получение списка сообщений в формате словарей.
        
        Args:
            session_id: Идентификатор сессии
            
        Returns:
            Список сообщений в формате словарей
        """
        if session_id not in self._conversations:
            return []
        
        return [msg.to_dict() for msg in self._conversations[session_id]]
    
    def get_full_history(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Получение полной истории сообщений (включая суммаризированные).
        
        Args:
            session_id: Идентификатор сессии
            
        Returns:
            Полная история сообщений
        """
        if session_id not in self._all_messages:
            return []
        
        return [msg.to_dict() for msg in self._all_messages[session_id]]
    
    def clear(self, session_id: str) -> None:
        """
        Очистка контекста диалога для указанной сессии.
        
        Args:
            session_id: Идентификатор сессии
        """
        if session_id in self._conversations:
            del self._conversations[session_id]
        
        if session_id in self._summaries:
            del self._summaries[session_id]
        
        if session_id in self._all_messages:
            del self._all_messages[session_id]
        
        if session_id in self._stats:
            del self._stats[session_id]
    
    def get_stats(self, session_id: str) -> Dict[str, int]:
        """
        Получение статистики по сессии.
        
        Args:
            session_id: Идентификатор сессии
            
        Returns:
            Статистика сессии
        """
        return self._stats.get(session_id, {
            "messages_total": 0,
            "summaries_created": 0,
            "window_trims": 0
        })
    
    def _trim_window(self, session_id: str) -> None:
        """Обрезка скользящего окна до максимального размера."""
        if session_id in self._conversations:
            messages = self._conversations[session_id]
            if len(messages) > self.max_window_size:
                # Сохраняем обрезанные сообщения в полной истории
                # (они уже там есть через add_message)
                self._conversations[session_id] = messages[-self.max_window_size:]
                self._stats[session_id]["window_trims"] += 1
                logger.debug(f"Trimmed window for session {session_id}, kept {self.max_window_size} messages")
    
    def _should_summarize(self, session_id: str) -> bool:
        """
        Проверка необходимости создания суммаризации.
        
        Args:
            session_id: Идентификатор сессии
            
        Returns:
            True если нужно создать суммаризацию
        """
        if session_id not in self._all_messages:
            return False
        
        total_messages = len(self._all_messages[session_id])
        window_messages = len(self._conversations.get(session_id, []))

        # Сообщения вне окна
        outside_window = total_messages - window_messages

        # Суммаризируем когда суммарное количество сообщений превышает порог
        # и есть сообщения вне окна
        return total_messages >= self.summary_threshold and outside_window > 0
    
    def _create_summary(self, session_id: str, llm_client: Any = None) -> None:
        """
        Создание суммаризации старых сообщений.
        
        Args:
            session_id: Идентификатор сессии
            llm_client: Клиент LLM для создания суммаризации (опционально)
        """
        if session_id not in self._all_messages:
            return
        
        all_messages = self._all_messages[session_id]
        window_messages = self._conversations.get(session_id, [])
        
        # Определяем сообщения для суммаризации (все кроме текущего окна)
        messages_to_summarize = []
        window_start_index = max(0, len(all_messages) - len(window_messages))
        
        for i, msg in enumerate(all_messages):
            if i < window_start_index:
                messages_to_summarize.append(msg)
        
        if not messages_to_summarize:
            return
        
        # Создаем суммаризацию
        if llm_client is not None:
            # Используем LLM для создания качественной суммаризации
            summary = self._create_llm_summary(messages_to_summarize, llm_client)
        else:
            # Простая суммаризация (первые N символов каждого сообщения)
            summary = self._create_simple_summary(messages_to_summarize)
        
        # Сохраняем суммаризацию
        self._summaries[session_id] = summary
        self._stats[session_id]["summaries_created"] += 1
        
        # Очищаем старые сообщения из полной истории (они теперь суммаризированы)
        self._all_messages[session_id] = window_messages.copy()
        
        logger.info(f"Created summary for session {session_id}, length: {len(summary)} chars")
    
    def _create_llm_summary(self, messages: List[ConversationMessage], llm_client: Any) -> str:
        """
        Создание суммаризации с использованием LLM.
        
        Args:
            messages: Сообщения для суммаризации
            llm_client: Клиент LLM
            
        Returns:
            Текст суммаризации
        """
        try:
            # Формируем промпт для суммаризации
            conversation_text = "\n".join([f"{msg.role}: {msg.content}" for msg in messages])
            
            prompt = f"""Суммаризируй следующий диалог, сохраняя ключевые моменты и контекст:

{conversation_text}

Суммаризация (на русском языке, кратко, сохраняя суть):"""
            
            # Вызываем LLM (зависит от конкретного клиента)
            # Здесь предполагается общий интерфейс с методом complete()
            response = llm_client.complete(prompt)
            return response.strip()
            
        except Exception as e:
            logger.error(f"Error creating LLM summary: {e}")
            # Fallback на простую суммаризацию
            return self._create_simple_summary(messages)
    
    def _create_simple_summary(self, messages: List[ConversationMessage]) -> str:
        """
        Простая суммаризация (для случаев без LLM).
        
        Args:
            messages: Сообщения для суммаризации
            
        Returns:
            Текст суммаризации
        """
        summary_parts = []
        
        for msg in messages:
            # Берем первые 100 символов каждого сообщения или все сообщение если оно короче
            content_preview = msg.content[:100] + ("..." if len(msg.content) > 100 else "")
            summary_parts.append(f"{msg.role}: {content_preview}")
        
        return f"Суммаризация предыдущих {len(messages)} сообщений:\n" + "\n".join(summary_parts)
    
    def session_exists(self, session_id: str) -> bool:
        """
        Проверка существования сессии.
        
        Args:
            session_id: Идентификатор сессии
            
        Returns:
            True если сессия существует
        """
        return session_id in self._conversations or session_id in self._all_messages
    
    def list_sessions(self) -> List[str]:
        """
        Получение списка всех активных сессий.
        
        Returns:
            Список идентификаторов сессий
        """
        return list(set(list(self._conversations.keys()) + list(self._all_messages.keys())))
    
    def cleanup_inactive_sessions(self, max_inactive_time: float = 3600) -> List[str]:
        """
        Очистка неактивных сессий.
        
        Args:
            max_inactive_time: Максимальное время неактивности в секундах
            
        Returns:
            Список удаленных сессий
        """
        removed_sessions = []
        current_time = datetime.now()
        
        for session_id in list(self._conversations.keys()):
            if session_id in self._conversations and self._conversations[session_id]:
                last_message = self._conversations[session_id][-1]
                time_diff = (current_time - last_message.timestamp).total_seconds()
                
                if time_diff > max_inactive_time:
                    self.clear(session_id)
                    removed_sessions.append(session_id)
        
        return removed_sessions
    
    def get_conversation_hash(self, session_id: str) -> str:
        """
        Получение хеша контекста диалога для аудита.
        
        Args:
            session_id: Идентификатор сессии
            
        Returns:
            SHA256 хеш контекста
        """
        if session_id not in self._conversations:
            return ""
        
        messages_data = [json.dumps(msg.to_dict(), sort_keys=True) for msg in self._conversations[session_id]]
        summary_data = self._summaries.get(session_id, "")
        
        data_to_hash = "\n".join(messages_data) + "\n" + summary_data
        return hashlib.sha256(data_to_hash.encode()).hexdigest()