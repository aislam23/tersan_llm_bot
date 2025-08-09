"""
Сервис для работы с OpenAI Responses API и file_search векторным хранилищем.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import asyncio

from loguru import logger
from openai import OpenAI

from app.config import settings


class OpenAIService:
    """Инкапсулирует взаимодействие с OpenAI Responses API.

    Возможности:
    - Ответы на вопросы сотрудников на основе внутренних PDF (file_search).
    - Загрузка PDF и прикрепление к векторному хранилищу.
    - Создание векторного хранилища при необходимости.
    """

    def __init__(self) -> None:
        if not settings.openai_api_key:
            logger.warning("OPENAI_API_KEY не задан. OpenAIService будет неактивен.")
        self.client = OpenAI(api_key=settings.openai_api_key or None)
        self.model = settings.openai_model
        self.vector_store_id = settings.openai_vector_store_id or ""

    # -------------------- Public API --------------------
    async def answer_question(self, question: str, *, use_file_search: bool = True) -> str:
        """Задать вопрос модели. Если включён use_file_search и настроен vector_store,
        модель будет использовать поиск по документам компании.
        """
        tools: List[Dict[str, Any]] = []
        if use_file_search and self.vector_store_id:
            tools.append({
                "type": "file_search",
                "vector_store_ids": [self.vector_store_id],
            })

        try:
            # Выполняем синхронный SDK-вызов в отдельном потоке, чтобы не блокировать event loop
            response = await asyncio.to_thread(
                self.client.responses.create,
                model=self.model,
                input=question,
                tools=tools or None,
                instructions=settings.openai_instructions,
                prompt_cache_key=settings.openai_prompt_cache_key,
            )
        except Exception as e:
            logger.error(f"Ошибка запроса к OpenAI: {e}")
            raise

        # Безопасно агрегируем текст
        try:
            text = getattr(response, "output_text", None)
            if not text:
                # Фоллбек на разбор output
                text_parts: List[str] = []
                for item in (response.output or []):
                    if getattr(item, "type", "") == "message":
                        for content in (item.content or []):
                            if getattr(content, "type", "") == "output_text":
                                text_parts.append(getattr(content, "text", ""))
                text = "\n".join([p for p in text_parts if p]) or ""
        except Exception:
            text = ""

        return text or ""  # Пусть будет пустая строка, обработаем на уровне хендлера

    def create_vector_store(self, name: str) -> str:
        """Создать векторное хранилище, вернуть его id."""
        try:
            vs = self.client.vector_stores.create(name=name)
            logger.info(f"Создан vector store: {vs.id} ({vs.name})")
            return vs.id
        except Exception as e:
            logger.error(f"Не удалось создать vector store: {e}")
            raise

    def set_vector_store(self, vector_store_id: str) -> None:
        self.vector_store_id = vector_store_id

    def upload_pdf(self, file_path: str) -> Optional[str]:
        """Загрузить PDF в Files и прикрепить к текущему vector store. Возвращает file_id."""
        if not self.vector_store_id:
            raise RuntimeError("Vector store не настроен. Сначала укажите ID хранилища.")
        try:
            file = self.client.files.create(file=open(file_path, "rb"), purpose="assistants")
            self.client.vector_stores.files.create(
                vector_store_id=self.vector_store_id,
                file_id=file.id,
            )
            logger.info(f"Файл {file_path} загружен (file_id={file.id}) и привязан к {self.vector_store_id}")
            return file.id
        except Exception as e:
            logger.error(f"Ошибка загрузки PDF '{file_path}': {e}")
            return None


# Единый синглтон-сервис для всего приложения
openai_service = OpenAIService()


