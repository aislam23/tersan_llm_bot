"""
Хендлеры пользовательских вопросов к ИИ-ассистенту Терсан.
"""
from aiogram import Router, F
from aiogram.enums import ChatAction
from aiogram.types import Message
from loguru import logger

from app.services.openai_service import openai_service


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
        answer = await _answer(user_input)
        if not answer:
            answer = "К сожалению, не удалось получить ответ. Попробуйте переформулировать вопрос."
        await message.answer(answer)
    except Exception as e:
        logger.error(f"QA error: {e}")
        await message.answer("Произошла ошибка при обращении к ИИ. Сообщите администратору.")


async def _answer(question: str) -> str:
    # Если нет API-ключа — сразу выходим
    if not openai_service.client.api_key:  # type: ignore[attr-defined]
        return "OpenAI не сконфигурирован. Обратитесь к администратору."

    text = await openai_service.answer_question(question, use_file_search=True)
    return text or ""


