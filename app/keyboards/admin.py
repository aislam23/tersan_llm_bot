"""
Клавиатуры для админской части
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


class AdminKeyboards:
    """Клавиатуры для админской панели"""
    
    @staticmethod
    def main_admin_menu() -> InlineKeyboardMarkup:
        """Главное меню админа"""
        builder = InlineKeyboardBuilder()
        
        builder.add(InlineKeyboardButton(
            text="📊 Рассылка",
            callback_data="admin_broadcast"
        ))
        builder.add(InlineKeyboardButton(
            text="🔗 Сгенерировать приглашение",
            callback_data="admin_invite"
        ))
        builder.add(InlineKeyboardButton(
            text="👥 Пользователи",
            callback_data="admin_users"
        ))
        builder.add(InlineKeyboardButton(
            text="📚 Хранилище документов (/docs_store)",
            callback_data="noop_docs_store"
        ))
        builder.add(InlineKeyboardButton(
            text="⬆️ Загрузить PDF (/docs_upload)",
            callback_data="noop_docs_upload"
        ))
        
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def broadcast_confirm(message_count: int) -> InlineKeyboardMarkup:
        """Подтверждение рассылки"""
        builder = InlineKeyboardBuilder()
        
        builder.add(InlineKeyboardButton(
            text=f"✅ Отправить ({message_count} польз.)",
            callback_data="broadcast_confirm_yes"
        ))
        
        builder.add(InlineKeyboardButton(
            text="❌ Отменить",
            callback_data="broadcast_confirm_no"
        ))
        
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def broadcast_add_button() -> InlineKeyboardMarkup:
        """Меню добавления кнопки к рассылке"""
        builder = InlineKeyboardBuilder()
        
        builder.add(InlineKeyboardButton(
            text="➕ Добавить кнопку",
            callback_data="broadcast_add_button"
        ))
        
        builder.add(InlineKeyboardButton(
            text="📤 Отправить без кнопки",
            callback_data="broadcast_no_button"
        ))
        
        builder.add(InlineKeyboardButton(
            text="❌ Отменить",
            callback_data="broadcast_cancel"
        ))
        
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def broadcast_button_confirm() -> InlineKeyboardMarkup:
        """Подтверждение кнопки для рассылки"""
        builder = InlineKeyboardBuilder()
        
        builder.add(InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data="broadcast_button_confirm"
        ))
        
        builder.add(InlineKeyboardButton(
            text="❌ Отменить",
            callback_data="broadcast_cancel"
        ))
        
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def create_custom_button(text: str, url: str) -> InlineKeyboardMarkup:
        """Создание кастомной кнопки для рассылки"""
        builder = InlineKeyboardBuilder()
        
        builder.add(InlineKeyboardButton(
            text=text,
            url=url
        ))
        
        return builder.as_markup() 

    @staticmethod
    def users_list(users: list[tuple[int, str]]) -> InlineKeyboardMarkup:
        """Список пользователей (кнопки)"""
        builder = InlineKeyboardBuilder()
        for user_id, title in users:
            builder.add(InlineKeyboardButton(
                text=title,
                callback_data=f"admin_user_{user_id}"
            ))
        builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back_main"))
        builder.adjust(1)
        return builder.as_markup()

    @staticmethod
    def user_card_actions(user_id: int, *, is_active: bool, is_admin: bool) -> InlineKeyboardMarkup:
        """Кнопки действий в карточке пользователя"""
        builder = InlineKeyboardBuilder()
        if is_active:
            builder.add(InlineKeyboardButton(text="🚫 Забрать доступ", callback_data=f"admin_user_revoke_{user_id}"))
        else:
            builder.add(InlineKeyboardButton(text="✅ Выдать доступ", callback_data=f"admin_user_grant_{user_id}"))
        if not is_admin:
            builder.add(InlineKeyboardButton(text="⭐ Сделать админом", callback_data=f"admin_user_make_admin_{user_id}"))
        builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users"))
        builder.adjust(1)
        return builder.as_markup()