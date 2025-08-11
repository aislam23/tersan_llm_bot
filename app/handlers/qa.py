"""
Хендлеры пользовательских вопросов к ИИ-ассистенту Терсан.
"""
import os
from contextlib import suppress
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


router = Router(name="qa")


@router.message(F.text & ~F.text.startswith("/"))
async def qa_handler(message: Message) -> None:
    """Отвечаем на свободные текстовые вопросы, используя file_search при наличии."""
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
            async with ChatActionSender(
                bot=message.bot,
                chat_id=message.chat.id,
                action=ChatAction.TYPING,
            ):
                answer = await _answer(user_input, chat_id=message.chat.id)
                if not answer:
                    answer = "К сожалению, не удалось получить ответ. Попробуйте переформулировать вопрос."
                await message.answer(answer)
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
    edit_interval = max(0.05, float(getattr(settings, "openai_stream_edit_interval_sec", 0.25) or 0.25))
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


