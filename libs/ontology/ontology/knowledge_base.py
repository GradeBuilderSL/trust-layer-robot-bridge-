"""
Knowledge Base для вопросов посетителей.

Простой keyword matching по базе знаний для начальной реализации.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from ontology.visitor_questions import (
    VisitorQuestion, VisitorAnswer, QuestionType
)

logger = logging.getLogger(__name__)


class ReasonCode:
    """Коды результатов для ответов на вопросы."""
    SUCCESS = "SUCCESS"
    KNOWLEDGE_GAP = "KNOWLEDGE_GAP"
    INVALID_QUESTION = "INVALID_QUESTION"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"


class KnowledgeBase:
    """База знаний для вопросов посетителей."""
    
    def __init__(self, knowledge_path: Optional[str] = None):
        """
        Инициализировать базу знаний.
        
        Args:
            knowledge_path: Путь к файлу знаний YAML
        """
        self.knowledge_path = knowledge_path
        self.knowledge: List[Dict] = []
        self.keyword_index: Dict[str, List[int]] = {}
        self.loaded = False
        
    def load_knowledge(self) -> bool:
        """Загрузить знания из файла."""
        if not self.knowledge_path:
            logger.warning("No knowledge path specified")
            return False
            
        try:
            import yaml
            path = Path(self.knowledge_path)
            if not path.exists():
                logger.warning(f"Knowledge file not found: {self.knowledge_path}")
                return False
                
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                
            self.knowledge = data.get('questions', [])
            
            # Построить индекс ключевых слов
            self._build_keyword_index()
            
            self.loaded = True
            logger.info(f"Loaded {len(self.knowledge)} knowledge entries from {self.knowledge_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load knowledge: {e}")
            return False
    
    def _build_keyword_index(self):
        """Построить индекс ключевых слов для быстрого поиска."""
        self.keyword_index = {}
        
        for idx, entry in enumerate(self.knowledge):
            # Извлечь ключевые слова из вопроса
            question = entry.get('question', '').lower()
            keywords = entry.get('keywords', [])
            
            # Добавить все ключевые слова в индекс
            all_keywords = set()
            
            # Добавить ключевые слова из конфига
            for kw in keywords:
                all_keywords.add(kw.lower())
            
            # Добавить слова из вопроса
            words = re.findall(r'\b\w+\b', question)
            for word in words:
                if len(word) > 3:  # Игнорировать короткие слова
                    all_keywords.add(word.lower())
            
            # Добавить в индекс
            for keyword in all_keywords:
                if keyword not in self.keyword_index:
                    self.keyword_index[keyword] = []
                self.keyword_index[keyword].append(idx)
    
    def _calculate_match_score(self, question: str, entry: Dict) -> float:
        """
        Рассчитать score совпадения вопроса с записью в базе знаний.
        
        Args:
            question: Вопрос пользователя
            entry: Запись из базы знаний
            
        Returns:
            Score от 0.0 до 1.0
        """
        question_lower = question.lower()
        entry_question = entry.get('question', '').lower()
        entry_keywords = entry.get('keywords', [])
        
        # Проверка точного совпадения
        if question_lower == entry_question:
            return 1.0
        
        # Проверка совпадения по ключевым словам
        matched_keywords = 0
        for keyword in entry_keywords:
            if keyword.lower() in question_lower:
                matched_keywords += 1
        
        if matched_keywords > 0:
            keyword_score = matched_keywords / len(entry_keywords)
            
            # Дополнительный бонус за совпадение слов из вопроса
            entry_words = set(re.findall(r'\b\w+\b', entry_question))
            question_words = set(re.findall(r'\b\w+\b', question_lower))
            word_overlap = len(entry_words & question_words)
            
            if len(question_words) > 0:
                overlap_score = word_overlap / len(question_words)
            else:
                overlap_score = 0
            
            return 0.7 * keyword_score + 0.3 * overlap_score
        
        return 0.0
    
    def search_visitor_question(self, question: VisitorQuestion) -> VisitorAnswer:
        """
        Найти ответ на вопрос посетителя.
        
        Args:
            question: Вопрос посетителя
            
        Returns:
            Ответ на вопрос
        """
        if not self.loaded:
            success = self.load_knowledge()
            if not success:
                return VisitorAnswer(
                    answer="Система знаний временно недоступна.",
                    confidence_score=0.0,
                    source="system",
                    reason_code=ReasonCode.SERVICE_UNAVAILABLE
                )
        
        if not question.question.strip():
            return VisitorAnswer(
                answer="Пожалуйста, задайте вопрос.",
                confidence_score=0.0,
                source="system",
                reason_code=ReasonCode.INVALID_QUESTION
            )
        
        # Поиск в базе знаний
        best_match = None
        best_score = 0.0
        
        for entry in self.knowledge:
            score = self._calculate_match_score(question.question, entry)
            
            # Учесть тип вопроса
            entry_type = entry.get('type', 'general')
            if (question.question_type.value == entry_type or 
                question.question_type == QuestionType.GENERAL):
                score = score * 1.0  # Полный score
            else:
                score = score * 0.7  # Штраф за несоответствие типа
            
            if score > best_score:
                best_score = score
                best_match = entry
        
        # Порог уверенности
        confidence_threshold = 0.3
        
        if best_match and best_score >= confidence_threshold:
            answer_text = best_match.get('answer', '')
            
            # Подставить контекстные значения если есть
            if question.context and question.context.location:
                answer_text = answer_text.replace('{location}', question.context.location)
            
            return VisitorAnswer(
                answer=answer_text,
                confidence_score=best_score,
                source="knowledge_base",
                source_id=best_match.get('id'),
                references=best_match.get('references', []),
                suggested_actions=best_match.get('suggested_actions', []),
                reason_code=ReasonCode.SUCCESS
            )
        else:
            # Ответ не найден
            return VisitorAnswer(
                answer="Извините, у меня нет информации по этому вопросу. Пожалуйста, обратитесь к сотруднику.",
                confidence_score=0.0,
                source="system",
                reason_code=ReasonCode.KNOWLEDGE_GAP
            )
    
    def get_health_status(self) -> Dict[str, Any]:
        """Получить статус health базы знаний."""
        return {
            "loaded": self.loaded,
            "entry_count": len(self.knowledge),
            "index_size": len(self.keyword_index),
            "has_knowledge_path": bool(self.knowledge_path)
        }