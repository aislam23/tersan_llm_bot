"""
Хендлеры пользовательских вопросов к ИИ-ассистенту Терсан.
"""
import os
from contextlib import suppress
from aiogram import Router, F
from aiogram.enums import ChatAction
from aiogram.types import Message
from loguru import logger

from app.services.openai_service import openai_service
from app.services.audio import convert_to_wav
from app.services.memory import memory


router = Router(name="qa")


@router.message(F.text & ~F.text.startswith("/"))
async def qa_handler(message: Message) -> None:
    """Отвечаем на свободные текстовые вопросы, используя file_search при наличии."""
    user_input = (message.text or "").strip()
    if not user_input:
        return

    # Маркер, чтобы отличать обычные чаты от служебных
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except Exception:
        pass
    try:
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


@router.message(F.voice)
async def qa_voice_handler(message: Message) -> None:
    """Принимаем голосовое сообщение: скачиваем, транскрибируем, отвечаем текстом."""
    try:
        voice = message.voice
        if not voice:
            return

        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

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
        answer = await _answer(transcript, chat_id=message.chat.id)
        if not answer:
            answer = "К сожалению, не удалось получить ответ. Попробуйте переформулировать вопрос."
        await message.answer(answer)
    except Exception as e:
        logger.error(f"QA voice error: {e}")
        await message.answer("Произошла ошибка при обработке голосового сообщения.")
    finally:
        # Чистим временные файлы
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

        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

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

        answer = await _answer(transcript, chat_id=message.chat.id)
        if not answer:
            answer = "К сожалению, не удалось получить ответ. Попробуйте переформулировать вопрос."
        await message.answer(answer)
    except Exception as e:
        logger.error(f"QA audio error: {e}")
        await message.answer("Произошла ошибка при обработке аудиофайла.")
    finally:
        with suppress(Exception):
            if 'src_path' in locals() and os.path.exists(src_path):
                os.remove(src_path)
        with suppress(Exception):
            if 'wav_path' in locals() and os.path.exists(wav_path) and wav_path != src_path:
                os.remove(wav_path)


