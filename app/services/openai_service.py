"""
Сервис для работы с OpenAI Responses API и file_search векторным хранилищем.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import asyncio

from loguru import logger
from openai import OpenAI

from app.config import settings
from app.services.memory import memory
from app.services.tokenizer import count_messages_tokens


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
    async def answer_question(
        self,
        question: str,
        *,
        use_file_search: bool = True,
        chat_id: Optional[int | str] = None,
    ) -> str:
        """Задать вопрос модели с учётом контекста диалога.

        Если включён use_file_search и настроен vector_store, модель использует поиск по документам.
        Если передан chat_id, будет добавлен недавний контекст и сводка, а ответ запишется в память.
        """
        tools: List[Dict[str, Any]] = []
        if use_file_search and self.vector_store_id:
            tools.append({
                "type": "file_search",
                "vector_store_ids": [self.vector_store_id],
            })

        # Строим ввод с памятью
        input_messages: List[Dict[str, Any]]
        if chat_id is not None:
            input_messages = await self._build_messages_with_memory(chat_id=chat_id, user_text=question)
        else:
            input_messages = [{"role": "user", "content": question}]

        try:
            # Выполняем синхронный SDK-вызов в отдельном потоке, чтобы не блокировать event loop
            response = await asyncio.to_thread(
                self.client.responses.create,
                model=self.model,
                input=input_messages,
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

        # Обновляем память
        if chat_id is not None and text:
            try:
                await memory.append_message(chat_id, "user", question)
                await memory.append_message(chat_id, "assistant", text)
                # При слишком длинных цепочках периодически делаем сводку
                await self._maybe_summarize(chat_id)
            except Exception as e:
                logger.warning(f"Не удалось обновить память диалога: {e}")

        return text or ""  # Пусть будет пустая строка, обработаем на уровне хендлера

    async def _build_messages_with_memory(self, chat_id: int | str, user_text: str) -> List[Dict[str, Any]]:
        """Формируем массив сообщений (developer+summary+history+current).

        Оптимизация для Prompt Caching: инструкции идут отдельно (instructions),
        а в input помещаем краткую сводку и недавнюю историю.
        """
        messages: List[Dict[str, Any]] = []
        # 1) Добавляем краткую сводку, если есть
        summary = await memory.get_summary(chat_id)
        if summary:
            messages.append({
                "role": "developer",
                "content": (
                    "Краткая сводка предыдущего диалога для контекста. "
                    "Используй как фоновые факты, не повторяй её дословно в ответах.\n" + summary
                ),
            })

        # 2) Добавляем несколько последних сообщений истории
        history = await memory.get_history(chat_id)
        for msg in history:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and isinstance(content, str):
                messages.append({"role": role, "content": content})

        # 3) Текущий запрос пользователя
        messages.append({"role": "user", "content": user_text})
        
        # Контроль бюджета токенов: если подсчёт слишком большой, удаляем самые старые элементы истории,
        # оставляя сводку и последние реплики.
        try:
            max_prompt_tokens = 6000  # мягкий бюджет для запроса (зависит от модели)
            # instructions идут отдельно, поэтому здесь считаем только messages
            while len(messages) > 2 and count_messages_tokens(messages, self.model) > max_prompt_tokens:
                # Удаляем первый после developer-сводки (если есть), иначе самый старый
                # Найдём индекс первого не-developer сообщения
                first_idx = 0
                if messages and messages[0].get("role") == "developer":
                    first_idx = 1
                # Если остаётся только пользовательский текущий ввод — остановимся
                if len(messages) - first_idx <= 1:
                    break
                del messages[first_idx]
        except Exception as e:
            logger.debug(f"Не удалось оценить/обрезать токены: {e}")

        return messages

    async def _maybe_summarize(self, chat_id: int | str) -> None:
        """Периодически преобразуем длинную историю в компактную сводку.

        Стратегия: если история превышает ~1.5x max_history_messages, попросим модель
        сделать краткую сводку и сохраним её. После этого обрежем историю до последних 8 сообщений.
        """
        try:
            history = await memory.get_history(chat_id, limit=settings.conversation_max_history_messages * 2)
            if len(history) < int(settings.conversation_max_history_messages * 1.5):
                return

            # Формируем простой запрос на суммаризацию
            convo_text = []
            for msg in history:
                role = msg.get("role")
                content = msg.get("content")
                if not isinstance(content, str):
                    continue
                prefix = "Пользователь:" if role == "user" else "Ассистент:"
                convo_text.append(f"{prefix} {content}")
            prompt = (
                "Суммируй диалог кратко в 8–12 строках: ключевые факты, намерения пользователя, принятые решения, "+
                "открытые вопросы. Пиши по-русски, без общих слов. Это будет использовано как контекст.\n\n" +
                "\n".join(convo_text)
            )

            response = await asyncio.to_thread(
                self.client.responses.create,
                model=self.model,
                reasoning={"effort": "low"},
                instructions="Ты помощник, делаешь краткие деловые сводки переписок.",
                input=[{"role": "user", "content": prompt}],
                prompt_cache_key=settings.openai_prompt_cache_key,
            )
            summary_text = getattr(response, "output_text", "") or ""
            if summary_text:
                await memory.set_summary(chat_id, summary_text)
                await memory.trim_to_last(chat_id, keep_last=8)
        except Exception as e:
            logger.debug(f"Суммаризация не выполнена: {e}")

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


