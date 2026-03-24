"""
FactExtractor — извлечение фактов (субъект-предикат-объект) из диалога с помощью LLM.

Слой: L2b (разрешён LLM, асинхронные запросы).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class FactTriple:
    """Структура для хранения факта с метаданными."""
    subject: str
    predicate: str
    object: str
    confidence: float  # 0.0 - 1.0
    source_text: str  # исходный фрагмент диалога
    extracted_at: datetime  # время извлечения
    ttl_seconds: int  # время жизни в секундах, 0 = бесконечно

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "confidence": self.confidence,
            "source_text": self.source_text,
            "extracted_at": self.extracted_at.isoformat(),
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FactTriple:
        return cls(
            subject=data["subject"],
            predicate=data["predicate"],
            object=data["object"],
            confidence=data["confidence"],
            source_text=data["source_text"],
            extracted_at=datetime.fromisoformat(data["extracted_at"]),
            ttl_seconds=data["ttl_seconds"],
        )


class FactExtractor:
    """
    Извлекает факты из текста диалога с помощью LLM.

    Использует существующий LLM клиент из trust_edge (через внедрение зависимости).
    """

    def __init__(self, llm_client, default_ttl: int = 86400):
        """
        :param llm_client: клиент для запросов к LLM (должен иметь метод async_query)
        :param default_ttl: время жизни по умолчанию в секундах (24 часа)
        """
        self.llm = llm_client
        self.default_ttl = default_ttl

    async def extract_from_dialog(self, dialog_text: str) -> List[FactTriple]:
        """
        Извлекает факты из текста диалога.

        :param dialog_text: текст диалога (одно или несколько сообщений)
        :return: список извлечённых фактов
        """
        if not dialog_text.strip():
            return []

        prompt = self._build_prompt(dialog_text)
        try:
            response = await self.llm.async_query(prompt, max_tokens=2000)
            facts = self._parse_llm_response(response)
            # Добавляем метаданные
            now = datetime.now(timezone.utc)
            for fact in facts:
                fact.extracted_at = now
                if fact.ttl_seconds == 0:
                    fact.ttl_seconds = self.default_ttl
            return facts
        except Exception as e:
            logger.error("Failed to extract facts from dialog: %s", e)
            return []

    def _build_prompt(self, dialog_text: str) -> str:
        return f"""Извлеки факты из диалога оператора с роботом.
Факты должны быть в формате "субъект — предикат — объект".
Пример:
  Оператор: "Робот, принеси коробку из зоны А"
  Факты:
    субъект: оператор, предикат: хочет, объект: принести коробку
    субъект: коробка, предикат: находится в, объект: зона А

Диалог:
{dialog_text}

Выведи только JSON массив с фактами. Каждый факт — объект с полями:
  "subject": строка (субъект),
  "predicate": строка (предикат),
  "object": строка (объект),
  "confidence": число от 0 до 1 (уверенность),
  "source_text": строка (фрагмент диалога, откуда извлечён факт),
  "ttl_seconds": число (время жизни в секундах, 0 если не указано).

Убедись, что субъект, предикат и объект — краткие, без лишних слов.
"""

    def _parse_llm_response(self, response: str) -> List[FactTriple]:
        """Парсит JSON ответ LLM в список FactTriple."""
        try:
            # Очистка ответа: удалить markdown коды, лишние пробелы
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.endswith("```"):
                response = response[:-3]
            data = json.loads(response)
            if not isinstance(data, list):
                logger.warning("LLM response is not a list: %s", data)
                return []

            facts = []
            for item in data:
                try:
                    fact = FactTriple(
                        subject=str(item.get("subject", "")),
                        predicate=str(item.get("predicate", "")),
                        object=str(item.get("object", "")),
                        confidence=float(item.get("confidence", 0.5)),
                        source_text=str(item.get("source_text", "")),
                        extracted_at=datetime.now(timezone.utc),  # временная метка
                        ttl_seconds=int(item.get("ttl_seconds", 0)),
                    )
                    if fact.subject and fact.predicate and fact.object:
                        facts.append(fact)
                except (ValueError, KeyError) as e:
                    logger.warning("Failed to parse fact item %s: %s", item, e)
            return facts
        except json.JSONDecodeError as e:
            logger.error("Failed to decode LLM response as JSON: %s", e)
            return []