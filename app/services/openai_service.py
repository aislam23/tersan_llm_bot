"""
Сервис для работы с OpenAI Responses API и file_search векторным хранилищем.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
import contextlib
import queue
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
        use_web_search: Optional[bool] = None,
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

        # Опциональный веб-поиск: либо включается явно, либо берётся из настроек
        enable_web_search = settings.openai_enable_web_search if use_web_search is None else use_web_search
        if enable_web_search:
            ws_tool: Dict[str, Any] = {"type": "web_search_preview"}
            # контекст размера
            size = settings.openai_web_search_context_size
            if size:
                ws_tool["search_context_size"] = size
            # геолокация
            loc: Dict[str, Any] = {}
            if settings.openai_web_search_country or settings.openai_web_search_city or settings.openai_web_search_region or settings.openai_web_search_timezone:
                loc["type"] = "approximate"
                if settings.openai_web_search_country:
                    loc["country"] = settings.openai_web_search_country
                if settings.openai_web_search_city:
                    loc["city"] = settings.openai_web_search_city
                if settings.openai_web_search_region:
                    loc["region"] = settings.openai_web_search_region
                if settings.openai_web_search_timezone:
                    loc["timezone"] = settings.openai_web_search_timezone
            if loc:
                ws_tool["user_location"] = loc
            tools.append(ws_tool)

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

    async def analyze_image(
        self,
        image_path: str,
        *,
        question: Optional[str] = None,
        detail: Optional[str] = None,  # "low" | "high" | "auto"
        chat_id: Optional[int | str] = None,
    ) -> str:
        """Проанализировать изображение с помощью vision-способностей модели.

        Включает изображение как input_image (через Files API, purpose="vision").
        Текстовый запрос берётся из question или задаётся по умолчанию.
        Если указан chat_id, добавляется контекст памяти (summary + недавняя история).
        """
        if not self.client.api_key:  # type: ignore[attr-defined]
            return "OpenAI не сконфигурирован. Обратитесь к администратору."

        user_prompt = (question or "Опиши изображение и ответь на возможные вопросы по нему. Пиши по-русски.").strip()

        # Загружаем изображение в Files API с purpose="vision"
        try:
            file_obj = await asyncio.to_thread(
                self.client.files.create,
                file=open(image_path, "rb"),
                purpose="vision",
            )
            file_id = getattr(file_obj, "id", None)
            if not file_id:
                return "Не удалось подготовить изображение для анализа."
        except Exception as e:
            logger.error(f"Ошибка загрузки изображения в OpenAI Files: {e}")
            return "Не удалось загрузить изображение для анализа."

        # Собираем сообщения с учётом памяти
        messages: List[Dict[str, Any]] = []
        if chat_id is not None:
            try:
                summary = await memory.get_summary(chat_id)
                if summary:
                    messages.append({
                        "role": "developer",
                        "content": (
                            "Краткая сводка предыдущего диалога для контекста. "
                            "Используй как фоновые факты, не повторяй её дословно в ответах.\n" + summary
                        ),
                    })
                history = await memory.get_history(chat_id)
                for msg in history:
                    role = msg.get("role")
                    content = msg.get("content")
                    if role in ("user", "assistant") and isinstance(content, str):
                        messages.append({"role": role, "content": content})
            except Exception as e:
                logger.debug(f"Не удалось добавить контекст памяти к vision-запросу: {e}")

        # Финальное пользовательское сообщение с изображением
        image_item: Dict[str, Any] = {"type": "input_image", "file_id": file_id}
        if detail in {"low", "high", "auto"}:
            image_item["detail"] = detail

        messages.append({
            "role": "user",
            "content": [
                {"type": "input_text", "text": user_prompt},
                image_item,
            ],
        })

        try:
            response = await asyncio.to_thread(
                self.client.responses.create,
                model=self.model,
                input=messages,
                instructions=settings.openai_instructions,
                prompt_cache_key=settings.openai_prompt_cache_key,
            )
        except Exception as e:
            logger.error(f"Ошибка vision-запроса к OpenAI: {e}")
            return "Произошла ошибка при анализе изображения."

        # Извлекаем текст
        try:
            text = getattr(response, "output_text", None) or ""
            if not text:
                parts: List[str] = []
                for item in (response.output or []):
                    if getattr(item, "type", "") == "message":
                        for content in (item.content or []):
                            if getattr(content, "type", "") == "output_text":
                                parts.append(getattr(content, "text", ""))
                text = "\n".join([p for p in parts if p])
        except Exception:
            text = ""

        # Обновляем память
        if chat_id is not None and text:
            try:
                await memory.append_message(chat_id, "user", f"[изображение] {user_prompt}")
                await memory.append_message(chat_id, "assistant", text)
                await self._maybe_summarize(chat_id)
            except Exception as e:
                logger.warning(f"Не удалось обновить память диалога (vision): {e}")

        return text or ""

    async def stream_answer_iter(
        self,
        question: str,
        *,
        use_file_search: bool = True,
        use_web_search: Optional[bool] = None,
        chat_id: Optional[int | str] = None,
    ) -> AsyncIterator[str]:
        """Асинхронно стримим ответ модели порциями текста.

        Если стриминг выключен в настройках — выдаём единый полный ответ одной порцией.
        По окончании сохраняем сообщение пользователя и полный ответ в память (если задан chat_id).
        """
        # Если нет API-ключа — сразу завершаем пустым результатом
        if not self.client.api_key:  # type: ignore[attr-defined]
            return

        # Если стриминг выключен — отдадим одним куском
        if not settings.openai_streaming_enabled:
            full = await self.answer_question(
                question,
                use_file_search=use_file_search,
                use_web_search=use_web_search,
                chat_id=chat_id,
            )
            if full:
                yield full
            return

        # Собираем инструменты и сообщения (как в обычном вызове)
        tools: List[Dict[str, Any]] = []
        if use_file_search and self.vector_store_id:
            tools.append({
                "type": "file_search",
                "vector_store_ids": [self.vector_store_id],
            })

        enable_web_search = settings.openai_enable_web_search if use_web_search is None else use_web_search
        if enable_web_search:
            ws_tool: Dict[str, Any] = {"type": "web_search_preview"}
            size = settings.openai_web_search_context_size
            if size:
                ws_tool["search_context_size"] = size
            loc: Dict[str, Any] = {}
            if settings.openai_web_search_country or settings.openai_web_search_city or settings.openai_web_search_region or settings.openai_web_search_timezone:
                loc["type"] = "approximate"
                if settings.openai_web_search_country:
                    loc["country"] = settings.openai_web_search_country
                if settings.openai_web_search_city:
                    loc["city"] = settings.openai_web_search_city
                if settings.openai_web_search_region:
                    loc["region"] = settings.openai_web_search_region
                if settings.openai_web_search_timezone:
                    loc["timezone"] = settings.openai_web_search_timezone
            if loc:
                ws_tool["user_location"] = loc
            tools.append(ws_tool)

        if chat_id is not None:
            input_messages = await self._build_messages_with_memory(chat_id=chat_id, user_text=question)
        else:
            input_messages = [{"role": "user", "content": question}]

        # Мостик потоковых событий SDK → асинхронный итератор (через threadsafe очередь)
        q: "queue.Queue[Tuple[str, Optional[str]]]" = queue.Queue()
        SENTINEL_DONE: Tuple[str, Optional[str]] = ("done", None)
        SENTINEL_ERROR: Tuple[str, Optional[str]] = ("error", None)
        full_answer_parts: List[str] = []

        def _worker() -> None:
            try:
                stream = self.client.responses.create(
                    model=self.model,
                    input=input_messages,
                    tools=tools or None,
                    instructions=settings.openai_instructions,
                    prompt_cache_key=settings.openai_prompt_cache_key,
                    stream=True,
                )
                for event in stream:
                    try:
                        ev_type = getattr(event, "type", None) or getattr(event, "event", "") or ""
                        if isinstance(ev_type, str) and "response.output_text.delta" in ev_type:
                            # В разных версиях SDK свойство может называться по-разному
                            delta = getattr(event, "delta", None) or getattr(event, "text", None) or getattr(event, "output_text_delta", None)
                            if isinstance(delta, str) and delta:
                                full_answer_parts.append(delta)
                                q.put(("delta", delta))
                        elif isinstance(ev_type, str) and (ev_type.endswith("response.completed") or ev_type == "response.completed"):
                            q.put(SENTINEL_DONE)
                            break
                        elif isinstance(ev_type, str) and ("failed" in ev_type or ev_type == "error"):
                            q.put(SENTINEL_ERROR)
                            break
                    except Exception:
                        # Игнорируем частные ошибки обработки отдельных событий
                        continue
                else:
                    # Если цикл завершился без явного completed — всё равно завершим
                    q.put(SENTINEL_DONE)
            except Exception as e:
                logger.error(f"Ошибка стриминга OpenAI: {e}")
                with contextlib.suppress(Exception):
                    q.put(SENTINEL_ERROR)

        # Запускаем воркер в отдельном потоке
        bg = asyncio.create_task(asyncio.to_thread(_worker))

        try:
            while True:
                # Блокирующее ожидание элемента изочереди в отдельном потоке
                kind, payload = await asyncio.to_thread(q.get)
                if (kind, payload) == SENTINEL_DONE:
                    break
                if (kind, payload) == SENTINEL_ERROR:
                    # Прерываем без текста; память не обновляем
                    return
                if kind == "delta" and isinstance(payload, str):
                    yield payload
        finally:
            with contextlib.suppress(Exception):
                await bg

        # По завершении — обновляем память целым ответом
        if chat_id is not None and full_answer_parts:
            final_text = "".join(full_answer_parts)
            try:
                await memory.append_message(chat_id, "user", question)
                await memory.append_message(chat_id, "assistant", final_text)
                await self._maybe_summarize(chat_id)
            except Exception as e:
                logger.warning(f"Не удалось обновить память диалога (stream): {e}")

    async def transcribe_audio(
        self,
        file_path: str,
        *,
        response_format: Optional[str] = None,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """Транскрибировать аудио-файл в текст с помощью Audio Transcriptions API.

        По умолчанию использует настройки из конфигурации и возвращает распознанный текст.
        """
        if not self.client.api_key:  # type: ignore[attr-defined]
            return "OpenAI не сконфигурирован. Обратитесь к администратору."

        def _do_call(use_model: str):
            stt_resp_format = response_format or settings.openai_stt_response_format or "text"
            with open(file_path, "rb") as f:
                return self.client.audio.transcriptions.create(
                    model=use_model,
                    file=f,
                    response_format=stt_resp_format,
                    prompt=(prompt or settings.openai_stt_prompt or None),
                    language=(language or settings.openai_stt_language or None),
                )

        def _extract_text(res: Any) -> str:
            try:
                if isinstance(res, str):
                    return res.strip()
                text = getattr(res, "text", None)
                if isinstance(text, str):
                    return text.strip()
                if isinstance(res, dict):
                    maybe = res.get("text")
                    if isinstance(maybe, str):
                        return maybe.strip()
            except Exception:
                return ""
            return ""

        primary_model = model or settings.openai_stt_model
        try:
            logger.info(f"STT: start transcribe file={file_path} model={primary_model}")
            result = await asyncio.to_thread(_do_call, primary_model)
            text = _extract_text(result)
            if text:
                return text
            logger.warning(f"STT: empty result with model={primary_model}; will try whisper-1 fallback")
            # Фоллбек на whisper-1, если основной снапшот не дал текста
            fallback_model = "whisper-1"
            result2 = await asyncio.to_thread(_do_call, fallback_model)
            text2 = _extract_text(result2)
            if text2:
                logger.info("STT: fallback whisper-1 succeeded")
                return text2
            logger.warning("STT: fallback whisper-1 also returned empty text")
            return ""
        except Exception as e:
            logger.error(f"Ошибка транскрибации аудио '{file_path}': {e}")
            return ""

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


