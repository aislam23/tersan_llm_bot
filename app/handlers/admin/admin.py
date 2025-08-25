"""
Админские хендлеры
"""
import re
from datetime import datetime
from typing import Optional
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from loguru import logger

from app.config import settings
from app.database import db
from app.states import AdminStates
from app.keyboards import AdminKeyboards
from app.services import BroadcastService
from app.services.openai_service import openai_service

router = Router()


async def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом (ENV или в БД)"""
    return await db.is_user_admin(user_id)


@router.message(Command("admin"))
async def admin_command(message: Message, bot: Bot):
    """Обработчик команды /admin"""
    if not await is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора")
        return
    
    # Получаем статистику бота
    stats = await db.get_bot_stats()
    if not stats:
        # Если статистики нет, создаем её
        stats = await db.update_bot_stats()
    
    # Получаем актуальные данные
    total_users = await db.get_users_count()
    active_users = await db.get_active_users_count()
    
    # Форматируем время последнего запуска
    last_restart = stats.last_restart.strftime("%d.%m.%Y %H:%M:%S")
    
    # Формируем сообщение со статистикой
    text = f"""
🔧 <b>Админская панель</b>

📊 <b>Статистика бота:</b>
👥 Всего пользователей: <b>{total_users}</b>
✅ Активных пользователей: <b>{active_users}</b>
🟢 Статус: <b>{stats.status}</b>
🕐 Последний запуск: <b>{last_restart}</b>

Выберите действие:
"""
    
    await message.answer(
        text=text,
        reply_markup=AdminKeyboards.main_admin_menu()
    )


@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    """Начало создания рассылки"""
    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    await state.set_state(AdminStates.broadcast_message)
    
    await callback.message.edit_text(
        "📤 <b>Создание рассылки</b>\n\n"
        "Отправьте сообщение любого типа (текст, фото, видео, документ и т.д.), "
        "которое хотите разослать всем пользователям бота.\n\n"
        "Для отмены введите /cancel"
    )
    
    await callback.answer()


@router.callback_query(F.data == "admin_invite")
async def admin_generate_invite(callback: CallbackQuery):
    """Сгенерировать одноразовое приглашение"""
    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав")
        return
    inv = await db.create_invitation(created_by=callback.from_user.id)
    bot = callback.message.bot
    me = await bot.get_me()
    start_link = f"https://t.me/{me.username}?start={inv.token}"
    await callback.message.answer(
        f"🔗 Одноразовая ссылка-приглашение:\n<code>{start_link}</code>\n\n"
        f"Ссылка станет недействительной после первого использования."
    )
    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def admin_users_list(callback: CallbackQuery):
    """Показать список пользователей"""
    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав")
        return
    users = await db.get_all_users()
    # Формируем заголовок и кнопки
    buttons_data: list[tuple[int, str]] = []
    for u in users:
        title = f"{u.first_name or ''} {u.last_name or ''} (@{u.username})".strip()
        title = title or str(u.id)
        marker = "✅" if u.is_active else "🚫"
        admin_mark = " ⭐" if getattr(u, "is_admin", False) or settings.is_admin(u.id) else ""
        buttons_data.append((u.id, f"{marker} {title}{admin_mark}"))
    text = "👥 Список пользователей. Нажмите, чтобы открыть карточку пользователя."
    await callback.message.edit_text(text, reply_markup=AdminKeyboards.users_list(buttons_data))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_"))
async def admin_user_card(callback: CallbackQuery):
    """Карточка пользователя"""
    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав")
        return
    data = callback.data or ""
    # варианты: admin_user_<id>, admin_user_grant_<id>, admin_user_revoke_<id>, admin_user_make_admin_<id>
    parts = data.split("_")
    action = parts[2]
    user_id_str = parts[-1]
    try:
        target_user_id = int(user_id_str)
    except ValueError:
        await callback.answer("Некорректный ID")
        return
    # Выполним действие если нужно
    if action == "grant":
        await db.set_user_access(target_user_id, True)
    elif action == "revoke":
        await db.set_user_access(target_user_id, False)
    elif action == "make":
        # next part is 'admin'
        await db.set_user_admin(target_user_id, True)
    # Загружаем карточку
    u = await db.get_user(target_user_id)
    if not u:
        await callback.answer("Пользователь не найден")
        return
    is_admin_flag = await db.is_user_admin(u.id)
    text = (
        f"🪪 <b>Пользователь</b> <code>{u.id}</code>\n"
        f"Имя: {u.first_name or ''} {u.last_name or ''}\n"
        f"Username: @{u.username or '-'}\n"
        f"Зарегистрирован: {u.created_at.strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"Доступ: {'✅ есть' if u.is_active or is_admin_flag else '🚫 нет'}\n"
        f"Админ: {'⭐ да' if is_admin_flag else '—'}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=AdminKeyboards.user_card_actions(u.id, is_active=bool(u.is_active), is_admin=is_admin_flag)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_back_main")
async def back_to_main(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    # Получаем актуальные цифры
    stats = await db.get_bot_stats()
    if not stats:
        stats = await db.update_bot_stats()
    total_users = await db.get_users_count()
    active_users = await db.get_active_users_count()
    last_restart = stats.last_restart.strftime("%d.%m.%Y %H:%M:%S")
    text = (
        f"🔧 <b>Админская панель</b>\n\n"
        f"📊 <b>Статистика бота:</b>\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"✅ Активных пользователей: <b>{active_users}</b>\n"
        f"🟢 Статус: <b>{stats.status}</b>\n"
        f"🕐 Последний запуск: <b>{last_restart}</b>\n\n"
        f"Выберите действие:"
    )
    await callback.message.edit_text(text, reply_markup=AdminKeyboards.main_admin_menu())
    await callback.answer()


@router.message(Command("docs_store"))
async def set_docs_store(message: Message):
    """Установить/создать vector store для корпоративных документов.
    Использование:
    /docs_store create НазваниеХранилища
    /docs_store set vs_XXXXXXXXXXXXXXXX
    """
    if not await is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора")
        return

    args = (message.text or "").split(maxsplit=2)
    if len(args) < 2:
        await message.answer("Укажите действие: create &lt;name&gt; или set &lt;id&gt;")
        return

    action = args[1].lower()
    if action == "create":
        name = args[2] if len(args) >= 3 else "tersan_docs"
        try:
            vs_id = openai_service.create_vector_store(name)
            openai_service.set_vector_store(vs_id)
            await message.answer(f"✅ Vector store создан и активирован: <code>{vs_id}</code>")
        except Exception as e:
            await message.answer(f"❌ Ошибка создания: <code>{e}</code>")
    elif action == "set":
        if len(args) < 3:
            await message.answer("Укажите ID: /docs_store set vs_xxx")
            return
        openai_service.set_vector_store(args[2])
        await message.answer(f"✅ Текущий vector store: <code>{args[2]}</code>")
    else:
        await message.answer("Неизвестное действие. Используйте create или set.")


@router.message(Command("docs_upload"))
async def docs_upload(message: Message):
    """Загрузка PDF в активное векторное хранилище.
    Команда должна сопровождаться документом (PDF).
    """
    if not await is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора")
        return

    if not message.document:
        await message.answer("Прикрепите PDF-файл к сообщению с командой /docs_upload")
        return

    if not message.document.mime_type or "pdf" not in message.document.mime_type.lower():
        await message.answer("Поддерживаются только PDF-файлы")
        return

    # Скачиваем файл во временную директорию
    file = await message.bot.get_file(message.document.file_id)
    file_path = file.file_path
    local_path = f"/tmp/{message.document.file_unique_id}.pdf"
    await message.bot.download_file(file_path, destination=local_path)

    file_id = openai_service.upload_pdf(local_path)
    if file_id:
        await message.answer("✅ Документ загружен в базу знаний")
    else:
        await message.answer("❌ Не удалось загрузить документ")


@router.message(StateFilter(AdminStates.broadcast_message))
async def receive_broadcast_message(message: Message, state: FSMContext):
    """Получение сообщения для рассылки"""
    if not await is_admin(message.from_user.id):
        await state.clear()
        return
    
    # Сохраняем сообщение в состояние
    await state.update_data(broadcast_message=message)
    
    # Получаем количество пользователей для рассылки
    users_count = await db.get_active_users_count()
    
    await message.answer(
        f"✅ <b>Сообщение получено!</b>\n\n"
        f"👥 Количество получателей: <b>{users_count}</b>\n\n"
        f"Хотите добавить кнопку к сообщению?",
        reply_markup=AdminKeyboards.broadcast_add_button()
    )


@router.callback_query(F.data == "broadcast_add_button", StateFilter(AdminStates.broadcast_message))
async def add_button_to_broadcast(callback: CallbackQuery, state: FSMContext):
    """Добавление кнопки к рассылке"""
    await state.set_state(AdminStates.broadcast_button)
    
    await callback.message.edit_text(
        "🔗 <b>Добавление кнопки</b>\n\n"
        "Отправьте кнопку в формате:\n"
        "<code>Текст кнопки | https://example.com</code>\n\n"
        "Пример:\n"
        "<code>Наш сайт | https://example.com</code>\n\n"
        "Для отмены введите /cancel"
    )
    
    await callback.answer()


@router.message(StateFilter(AdminStates.broadcast_button))
async def receive_broadcast_button(message: Message, state: FSMContext):
    """Получение кнопки для рассылки"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    # Парсим кнопку
    button_pattern = r"^(.+?)\s*\|\s*(https?://.+)$"
    match = re.match(button_pattern, message.text.strip())
    
    if not match:
        await message.answer(
            "❌ <b>Неверный формат кнопки!</b>\n\n"
            "Используйте формат:\n"
            "<code>Текст кнопки | https://example.com</code>\n\n"
            "Попробуйте еще раз или введите /cancel для отмены"
        )
        return
    
    button_text = match.group(1).strip()
    button_url = match.group(2).strip()
    
    # Сохраняем данные кнопки
    await state.update_data(
        button_text=button_text,
        button_url=button_url
    )
    
    # Создаем превью кнопки
    preview_keyboard = AdminKeyboards.create_custom_button(button_text, button_url)
    
    await message.answer(
        f"✅ <b>Кнопка создана!</b>\n\n"
        f"📝 Текст: <b>{button_text}</b>\n"
        f"🔗 Ссылка: <code>{button_url}</code>\n\n"
        f"Превью кнопки:",
        reply_markup=preview_keyboard
    )
    
    # Переходим к подтверждению
    data = await state.get_data()
    users_count = await db.get_active_users_count()
    
    await message.answer(
        f"📤 <b>Подтверждение рассылки</b>\n\n"
        f"👥 Получателей: <b>{users_count}</b>\n"
        f"🔗 С кнопкой: <b>Да</b>\n\n"
        f"Отправить рассылку?",
        reply_markup=AdminKeyboards.broadcast_confirm(users_count)
    )


@router.callback_query(F.data == "broadcast_no_button", StateFilter(AdminStates.broadcast_message))
async def broadcast_without_button(callback: CallbackQuery, state: FSMContext):
    """Рассылка без кнопки"""
    users_count = await db.get_active_users_count()
    
    await callback.message.edit_text(
        f"📤 <b>Подтверждение рассылки</b>\n\n"
        f"👥 Получателей: <b>{users_count}</b>\n"
        f"🔗 С кнопкой: <b>Нет</b>\n\n"
        f"Отправить рассылку?",
        reply_markup=AdminKeyboards.broadcast_confirm(users_count)
    )
    
    await callback.answer()


@router.callback_query(F.data == "broadcast_confirm_yes")
async def confirm_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и запуск рассылки"""
    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    data = await state.get_data()
    broadcast_message = data.get("broadcast_message")
    
    if not broadcast_message:
        await callback.message.edit_text("❌ Ошибка: сообщение для рассылки не найдено")
        await state.clear()
        return
    
    # Создаем кнопку если есть
    custom_keyboard = None
    if data.get("button_text") and data.get("button_url"):
        custom_keyboard = AdminKeyboards.create_custom_button(
            data["button_text"],
            data["button_url"]
        )
    
    # Начинаем рассылку
    broadcast_service = BroadcastService(bot)
    
    # Сообщение о начале рассылки
    progress_message = await callback.message.edit_text(
        "📤 <b>Рассылка запущена...</b>\n\n"
        "📊 Прогресс: <b>0%</b>\n"
        "✅ Отправлено: <b>0</b>\n"
        "❌ Ошибок: <b>0</b>\n"
        "🚫 Заблокировано: <b>0</b>"
    )
    
    # Функция для обновления прогресса
    async def update_progress(stats: dict):
        progress_percent = int((stats["sent"] + stats["failed"] + stats["blocked"]) / stats["total"] * 100)
        
        try:
            await progress_message.edit_text(
                f"📤 <b>Рассылка в процессе...</b>\n\n"
                f"📊 Прогресс: <b>{progress_percent}%</b>\n"
                f"✅ Отправлено: <b>{stats['sent']}</b>\n"
                f"❌ Ошибок: <b>{stats['failed']}</b>\n"
                f"🚫 Заблокировано: <b>{stats['blocked']}</b>"
            )
        except Exception:
            # Игнорируем ошибки обновления прогресса
            pass
    
    # Запускаем рассылку
    try:
        final_stats = await broadcast_service.send_broadcast(
            message=broadcast_message,
            custom_keyboard=custom_keyboard,
            progress_callback=update_progress
        )
        
        # Финальная статистика
        success_rate = int(final_stats["sent"] / final_stats["total"] * 100) if final_stats["total"] > 0 else 0
        
        await progress_message.edit_text(
            f"✅ <b>Рассылка завершена!</b>\n\n"
            f"📊 <b>Итоговая статистика:</b>\n"
            f"👥 Всего получателей: <b>{final_stats['total']}</b>\n"
            f"✅ Успешно доставлено: <b>{final_stats['sent']}</b>\n"
            f"❌ Ошибок доставки: <b>{final_stats['failed']}</b>\n"
            f"🚫 Заблокировали бота: <b>{final_stats['blocked']}</b>\n"
            f"📈 Успешность: <b>{success_rate}%</b>"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при рассылке: {e}")
        await progress_message.edit_text(
            f"❌ <b>Ошибка при рассылке!</b>\n\n"
            f"Описание: <code>{str(e)}</code>"
        )
    
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "broadcast_confirm_no")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Отмена рассылки"""
    await state.clear()
    await callback.message.edit_text("❌ Рассылка отменена")
    await callback.answer()


@router.callback_query(F.data == "broadcast_cancel")
async def cancel_broadcast_creation(callback: CallbackQuery, state: FSMContext):
    """Отмена создания рассылки"""
    await state.clear()
    await callback.message.edit_text("❌ Создание рассылки отменено")
    await callback.answer()


@router.message(Command("cancel"))
async def cancel_any_state(message: Message, state: FSMContext):
    """Отмена любого состояния"""
    if not await is_admin(message.from_user.id):
        return
    
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Операция отменена")
    else:
        await message.answer("ℹ️ Нет активных операций для отмены") 