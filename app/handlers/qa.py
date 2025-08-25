"""
Хендлеры пользовательских вопросов к ИИ-ассистенту Терсан.
"""
import os
from contextlib import suppress
import asyncio
from aiogram import Router, F
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from app.services.openai_service import openai_service
from app.services.audio import convert_to_wav
from app.services.memory import memory
from app.config import settings
import time
import mimetypes
from app.database import db


router = Router(name="qa")


async def _typing_heartbeat(bot, chat_id, period: float = 4.0):
    """Периодически шлём ChatAction.TYPING, пока задача не отменена."""
    try:
        while True:
            with suppress(Exception):
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(max(1.0, float(period)))
    except asyncio.CancelledError:
        return


@router.message(F.text & ~F.text.startswith("/"))
async def qa_handler(message: Message) -> None:
    """Отвечаем на свободные текстовые вопросы, используя file_search при наличии."""
    # Проверка доступа: разрешаем только активным пользователям или админам
    db_user = await db.get_user(message.from_user.id)
    if not (db_user and (db_user.is_active or await db.is_user_admin(message.from_user.id))):
        await message.answer("🚫 Доступ к функциям бота закрыт. Получите приглашение у администратора.")
        return
    user_input = (message.text or "").strip()
    if not user_input:
        return

    # Поддерживаем индикацию «печатает…» всё время, пока готовим ответ
    try:
        if settings.openai_streaming_enabled:
            async with ChatActionSender(
                bot=message.bot,
                chat_id=message.chat.id,
                action=ChatAction.TYPING,
            ):
                await _answer_streaming(message, user_input)
        else:
            # Heartbeat «печатает…» до отправки финального ответа
            typing_task = asyncio.create_task(_typing_heartbeat(message.bot, message.chat.id, 4.0))
            try:
                answer = await _answer(user_input, chat_id=message.chat.id)
                if not answer:
                    answer = "К сожалению, не удалось получить ответ. Попробуйте переформулировать вопрос."
                await message.answer(answer)
            finally:
                typing_task.cancel()
                with suppress(Exception):
                    await typing_task
    except Exception as e:
        logger.error(f"QA error: {e}")
        await message.answer("Произошла ошибка при обращении к ИИ. Сообщите администратору.")


async def _answer(question: str, *, chat_id: int | str | None = None) -> str:
    # Если нет API-ключа — сразу выходим
    if not openai_service.client.api_key:  # type: ignore[attr-defined]
        return "OpenAI не сконфигурирован. Обратитесь к администратору."

    text = await openai_service.answer_question(
        question,
        use_file_search=True,
        use_web_search=None,  # берём из настроек, можно будет переключать командами
        chat_id=chat_id,
    )
    return text or ""


async def _answer_streaming(message: Message, question: str) -> None:
    """Стриминговый ответ: по мере генерации редактируем сообщение."""
    if not openai_service.client.api_key:  # type: ignore[attr-defined]
        await message.answer("OpenAI не сконфигурирован. Обратитесь к администратору.")
        return

    reply = await message.answer("…")
    last_edit_ts = 0.0
    edit_interval = max(0.05, float(getattr(settings, "openai_stream_edit_interval_sec", 1.0) or 1.0))
    accumulated_text: str = ""

    try:
        async for delta in openai_service.stream_answer_iter(
            question,
            use_file_search=True,
            use_web_search=None,
            chat_id=message.chat.id,
        ):
            if not delta:
                continue
            accumulated_text += delta
            now = time.time()
            if now - last_edit_ts >= edit_interval:
                text_to_show = accumulated_text
                if len(text_to_show) > 4096:
                    text_to_show = text_to_show[:4093] + "…"
                with suppress(Exception):
                    await reply.edit_text(text_to_show)
                last_edit_ts = now

        final_text = accumulated_text.strip() or "К сожалению, не удалось получить ответ. Попробуйте переформулировать вопрос."
        if len(final_text) > 4096:
            final_text = final_text[:4093] + "…"
        with suppress(Exception):
            await reply.edit_text(final_text)
    except Exception as e:
        logger.error(f"Streaming QA error: {e}")
        with suppress(Exception):
            await reply.edit_text("Произошла ошибка при обращении к ИИ. Сообщите администратору.")


@router.message(F.voice)
async def qa_voice_handler(message: Message) -> None:
    # Проверка доступа
    db_user = await db.get_user(message.from_user.id)
    if not (db_user and (db_user.is_active or await db.is_user_admin(message.from_user.id))):
        await message.answer("🚫 Доступ к функциям бота закрыт. Получите приглашение у администратора.")
        return
    """Принимаем голосовое сообщение: скачиваем, транскрибируем, отвечаем текстом."""
    try:
        voice = message.voice
        if not voice:
            return

        # Поддерживаем индикацию «печатает…» пока идёт распознавание и подготовка ответа
        sender_cm = ChatActionSender(bot=message.bot, chat_id=message.chat.id, action=ChatAction.TYPING)
        await sender_cm.__aenter__()

        # Скачиваем файл во временную директорию
        file = await message.bot.get_file(voice.file_id)
        src_path = f"/tmp/{voice.file_unique_id}.oga"
        await message.bot.download_file(file.file_path, destination=src_path)

        # Конвертация: OGG/Opus → WAV; поддерживаемые форматы отдаём как есть
        wav_path = convert_to_wav(src_path)
        if not wav_path:
            logger.error("Конвертация голосового сообщения не удалась (возможно, нет opus-tools)")
            await message.answer("Не удалось обработать аудио на сервере. Сообщите администратору (нужны opus-tools).")
            return

        # Транскрибуем аудио
        transcript = await openai_service.transcribe_audio(wav_path)
        if not transcript:
            logger.warning("STT вернул пустой текст для voice")
            await message.answer("Не удалось распознать голос. Попробуйте ещё раз.")
            return

        # Отвечаем как на обычный текст
        if settings.openai_streaming_enabled:
            await _answer_streaming(message, transcript)
        else:
            async with ChatActionSender(
                bot=message.bot,
                chat_id=message.chat.id,
                action=ChatAction.TYPING,
            ):
                answer = await _answer(transcript, chat_id=message.chat.id)
                if not answer:
                    answer = "К сожалению, не удалось получить ответ. Попробуйте переформулировать вопрос."
                await message.answer(answer)
    except Exception as e:
        logger.error(f"QA voice error: {e}")
        await message.answer("Произошла ошибка при обработке голосового сообщения.")
    finally:
        # Завершаем индикацию и чистим временные файлы
        with suppress(Exception):
            if 'sender_cm' in locals():
                await sender_cm.__aexit__(None, None, None)
        with suppress(Exception):
            if 'src_path' in locals() and os.path.exists(src_path):
                os.remove(src_path)
        with suppress(Exception):
            if 'wav_path' in locals() and os.path.exists(wav_path) and wav_path != src_path:
                os.remove(wav_path)


@router.message(F.audio)
async def qa_audio_handler(message: Message) -> None:
    # Проверка доступа
    db_user = await db.get_user(message.from_user.id)
    if not (db_user and (db_user.is_active or await db.is_user_admin(message.from_user.id))):
        await message.answer("🚫 Доступ к функциям бота закрыт. Получите приглашение у администратора.")
        return
    """Принимаем аудиофайл: скачиваем, транскрибируем, отвечаем текстом."""
    try:
        audio = message.audio
        if not audio:
            return

        # Поддерживаем индикацию «печатает…» пока идёт распознавание и подготовка ответа
        sender_cm = ChatActionSender(bot=message.bot, chat_id=message.chat.id, action=ChatAction.TYPING)
        await sender_cm.__aenter__()

        file = await message.bot.get_file(audio.file_id)
        # Стараемся угадать расширение из пути на стороне Telegram
        ext = os.path.splitext(file.file_path or "")[1] or ".bin"
        src_path = f"/tmp/{audio.file_unique_id}{ext}"
        await message.bot.download_file(file.file_path, destination=src_path)

        wav_path = convert_to_wav(src_path)
        if not wav_path:
            logger.error("Конвертация аудиофайла не удалась (возможно, нет opus-tools)")
            await message.answer("Не удалось обработать аудиофайл на сервере. Сообщите администратору (нужны opus-tools).")
            return

        transcript = await openai_service.transcribe_audio(wav_path)
        if not transcript:
            logger.warning("STT вернул пустой текст для audio")
            await message.answer("Не удалось распознать аудио. Попробуйте другой файл.")
            return

        if settings.openai_streaming_enabled:
            await _answer_streaming(message, transcript)
        else:
            async with ChatActionSender(
                bot=message.bot,
                chat_id=message.chat.id,
                action=ChatAction.TYPING,
            ):
                answer = await _answer(transcript, chat_id=message.chat.id)
                if not answer:
                    answer = "К сожалению, не удалось получить ответ. Попробуйте переформулировать вопрос."
                await message.answer(answer)
    except Exception as e:
        logger.error(f"QA audio error: {e}")
        await message.answer("Произошла ошибка при обработке аудиофайла.")
    finally:
        with suppress(Exception):
            if 'sender_cm' in locals():
                await sender_cm.__aexit__(None, None, None)
        with suppress(Exception):
            if 'src_path' in locals() and os.path.exists(src_path):
                os.remove(src_path)
        with suppress(Exception):
            if 'wav_path' in locals() and os.path.exists(wav_path) and wav_path != src_path:
                os.remove(wav_path)


@router.message(F.photo)
async def qa_photo_handler(message: Message) -> None:
    # Проверка доступа
    db_user = await db.get_user(message.from_user.id)
    if not (db_user and (db_user.is_active or await db.is_user_admin(message.from_user.id))):
        await message.answer("🚫 Доступ к функциям бота закрыт. Получите приглашение у администратора.")
        return
    """Принимаем фото/изображение: скачиваем, отправляем в vision, отвечаем текстом."""
    try:
        photos = message.photo or []
        if not photos:
            return
        # Берём максимальное по размеру
        photo = photos[-1]

        sender_cm = ChatActionSender(bot=message.bot, chat_id=message.chat.id, action=ChatAction.TYPING)
        await sender_cm.__aenter__()

        file = await message.bot.get_file(photo.file_id)
        src_path = f"/tmp/{photo.file_unique_id}.jpg"
        await message.bot.download_file(file.file_path, destination=src_path)

        # Вопрос пользователя может быть в подписи к фото (caption)
        user_q = (message.caption or "Что на этом изображении? Дай полезный разбор для нашей работы.").strip()

        answer = await openai_service.analyze_image(
            src_path,
            question=user_q,
            detail="auto",
            chat_id=message.chat.id,
        )
        if not answer:
            answer = "К сожалению, не удалось проанализировать изображение. Попробуйте другое или добавьте пояснение."
        await message.answer(answer)
    except Exception as e:
        logger.error(f"QA photo error: {e}")
        await message.answer("Произошла ошибка при обработке изображения.")
    finally:
        with suppress(Exception):
            if 'sender_cm' in locals():
                await sender_cm.__aexit__(None, None, None)
        with suppress(Exception):
            if 'src_path' in locals() and os.path.exists(src_path):
                os.remove(src_path)


@router.message(F.document)
async def qa_document_handler(message: Message) -> None:
    # Проверка доступа
    db_user = await db.get_user(message.from_user.id)
    if not (db_user and (db_user.is_active or await db.is_user_admin(message.from_user.id))):
        await message.answer("🚫 Доступ к функциям бота закрыт. Получите приглашение у администратора.")
        return
    """Принимаем документ. Если PDF: добавляем во vector store и отвечаем на подпись. Если это изображение по MIME — обрабатываем как vision.

    Иначе — просто добавляем файл в files (assistants) и просим модель ответить по подписи без file_search.
    """
    try:
        doc = message.document
        if not doc:
            return

        sender_cm = ChatActionSender(bot=message.bot, chat_id=message.chat.id, action=ChatAction.TYPING)
        await sender_cm.__aenter__()

        file = await message.bot.get_file(doc.file_id)
        # Определяем расширение
        guessed_ext = os.path.splitext(doc.file_name or "")[1] or os.path.splitext(file.file_path or "")[1] or ""
        if not guessed_ext:
            # попробуем по MIME
            mime = doc.mime_type or ""
            guessed_ext = mimetypes.guess_extension(mime) or ""
        ext = guessed_ext or ".bin"
        src_path = f"/tmp/{doc.file_unique_id}{ext}"
        await message.bot.download_file(file.file_path, destination=src_path)

        caption = (message.caption or "").strip()
        mime_type = (doc.mime_type or "").lower()

        # Если это PDF — загрузим в vector store и ответим на подпись с использованием file_search
        if ext.lower() == ".pdf" or "pdf" in mime_type:
            try:
                fid = openai_service.upload_pdf(src_path)
                if not fid:
                    await message.answer("PDF получен, но не удалось добавить в базу знаний. Администратору стоит проверить логи.")
                # После загрузки — короткий ответ на подпись (если есть). Далее текстовые вопросы будут работать с file_search автоматически.
                if caption:
                    answer = await openai_service.answer_question(caption, chat_id=message.chat.id, use_file_search=True)
                    if not answer:
                        answer = "Файл добавлен. Задайте вопрос по содержимому PDF."
                    await message.answer(answer)
                else:
                    await message.answer("PDF добавлен в базу знаний. Теперь вы можете задавать вопросы по его содержанию.")
            except Exception as e:
                logger.error(f"QA document (pdf) error: {e}")
                await message.answer("Не удалось обработать PDF-файл.")
            return

        # Если это изображение, присланное как документ (например, PNG/JPEG/WEBP)
        if any(mt in mime_type for mt in ["image/", "jpeg", "png", "webp", "gif"]):
            q = caption or "Что изображено на этом файле?"
            answer = await openai_service.analyze_image(src_path, question=q, detail="auto", chat_id=message.chat.id)
            if not answer:
                answer = "Не удалось проанализировать изображение. Попробуйте другое или добавьте пояснение."
            await message.answer(answer)
            return

        # Прочие документы: просто подтверждаем загрузку и советуем задавать вопросы текстом
        await message.answer("Файл получен. Для PDF мы можем добавить в базу знаний, для других форматов задайте текстовый вопрос, приложив нужные фрагменты.")
    except Exception as e:
        logger.error(f"QA document error: {e}")
        await message.answer("Произошла ошибка при обработке документа.")
    finally:
        with suppress(Exception):
            if 'sender_cm' in locals():
                await sender_cm.__aexit__(None, None, None)
        with suppress(Exception):
            if 'src_path' in locals() and os.path.exists(src_path):
                os.remove(src_path)


