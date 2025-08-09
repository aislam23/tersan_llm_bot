"""
Хранилище контекста диалога (кратковременная память) на Redis.

Поддерживает:
- добавление и получение последних сообщений диалога;
- хранение сводки (summary) длинной переписки для экономии токенов;
- мягкое ограничение размера истории;

Формат сообщения: {"role": "user|assistant", "content": str, "ts": int}
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from redis import asyncio as aioredis

from app.config import settings


class ConversationMemory:
    """Хранилище истории диалогов в Redis.

    Ключи:
    - История: f"chat:{chat_id}:history" (тип: LIST из JSON-строк)
    - Сводка:  f"chat:{chat_id}:summary" (тип: STRING)
    """

    def __init__(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        # TTL для ключей памяти (в секундах), продлевается при каждом добавлении сообщений
        self.ttl_seconds: int = 60 * 60 * 24 * 7  # 7 дней
        # Мягкие лимиты
        self.max_history_messages: int = settings.conversation_max_history_messages

    @staticmethod
    def _history_key(chat_id: int | str) -> str:
        return f"chat:{chat_id}:history"

    @staticmethod
    def _summary_key(chat_id: int | str) -> str:
        return f"chat:{chat_id}:summary"

    async def append_message(self, chat_id: int | str, role: str, content: str) -> None:
        """Добавить сообщение в конец истории и обрезать при необходимости."""
        key = self._history_key(chat_id)
        message = {"role": role, "content": content, "ts": int(time.time())}
        try:
            await self.redis.rpush(key, json.dumps(message, ensure_ascii=False))
            await self.redis.expire(key, self.ttl_seconds)
            # Мягкая обрезка по кол-ву сообщений
            await self.redis.ltrim(key, -self.max_history_messages, -1)
        except Exception as e:
            logger.error(f"Redis append_message error: {e}")

    async def get_history(self, chat_id: int | str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Вернуть последние N сообщений (по умолчанию весь лимит)."""
        key = self._history_key(chat_id)
        if limit is None:
            limit = self.max_history_messages
        try:
            # Берём хвост списка
            raw = await self.redis.lrange(key, -limit, -1)
            result: List[Dict[str, Any]] = []
            for item in raw:
                try:
                    result.append(json.loads(item))
                except Exception:
                    # Пропускаем битые элементы
                    continue
            return result
        except Exception as e:
            logger.error(f"Redis get_history error: {e}")
            return []

    async def clear_history(self, chat_id: int | str) -> None:
        try:
            await self.redis.delete(self._history_key(chat_id))
        except Exception as e:
            logger.error(f"Redis clear_history error: {e}")

    async def get_summary(self, chat_id: int | str) -> Optional[str]:
        try:
            key = self._summary_key(chat_id)
            summary = await self.redis.get(key)
            return summary if summary else None
        except Exception as e:
            logger.error(f"Redis get_summary error: {e}")
            return None

    async def set_summary(self, chat_id: int | str, summary: str) -> None:
        try:
            key = self._summary_key(chat_id)
            await self.redis.set(key, summary, ex=self.ttl_seconds)
        except Exception as e:
            logger.error(f"Redis set_summary error: {e}")

    async def trim_to_last(self, chat_id: int | str, keep_last: int) -> None:
        """Оставить только последние N сообщений в истории."""
        key = self._history_key(chat_id)
        try:
            await self.redis.ltrim(key, -keep_last, -1)
            await self.redis.expire(key, self.ttl_seconds)
        except Exception as e:
            logger.error(f"Redis trim_to_last error: {e}")

    async def clear_summary(self, chat_id: int | str) -> None:
        try:
            await self.redis.delete(self._summary_key(chat_id))
        except Exception as e:
            logger.error(f"Redis clear_summary error: {e}")


# Глобальный синглтон
memory = ConversationMemory()


