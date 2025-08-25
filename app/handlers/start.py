"""
Обработчик команды /start
"""
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import CommandStart
from loguru import logger

from app.database import db
from app.config import settings

router = Router()


@router.message(CommandStart())
async def start_command(message: Message):
    """Обработчик команды /start"""
    user = message.from_user
    
    # Сохраняем пользователя в базу данных
    await db.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # Авто-выдаем админку и доступ, если пользователь в ENV-админах
    if settings.is_admin(user.id):
        try:
            await db.set_user_admin(user.id, True)
            await db.set_user_access(user.id, True)
        except Exception as e:
            logger.warning(f"Не удалось обновить права ENV-админу {user.id}: {e}")
    
    # Проверяем, есть ли deep-link токен: "/start <token>"
    token: str | None = None
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        token = parts[1].strip()
    
    # Если есть токен — пытаемся активировать приглашение
    if token:
        used = await db.use_invitation(token, user.id)
        if used:
            # Выдаем доступ
            await db.set_user_access(user.id, True)
            welcome_text = (
                f"👋 Привет, {user.first_name or 'пользователь'}!\n\n"
                f"✅ Ваш доступ активирован по приглашению.\n\n"
                f"Для помощи используйте /help"
            )
            await message.answer(welcome_text)
            return
        else:
            await message.answer(
                "❌ Ссылка-приглашение недействительна или уже использована. Обратитесь к администратору."
            )
            return
    
    # Если пользователь уже имеет доступ — показываем приветствие
    db_user = await db.get_user(user.id)
    is_admin = await db.is_user_admin(user.id)
    if db_user and (db_user.is_active or is_admin):
        # Приветственное сообщение
        welcome_text = f"""
👋 Привет, {user.first_name or 'пользователь'}!

Добро пожаловать в наш бот! 

Для получения помощи используйте команду /help
"""
        
        await message.answer(welcome_text)
        return
    
    # Иначе — доступ закрыт
    await message.answer(
        "🚫 Доступ к боту возможен только по приглашению администратора.\n"
        "Попросите у администратора одноразовую ссылку и перейдите по ней."
    )
