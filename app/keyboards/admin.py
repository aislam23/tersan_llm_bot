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