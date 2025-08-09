"""
Handlers package
"""
from aiogram import Dispatcher

from .start import router as start_router
from .help import router as help_router
from .admin import admin_router
from .qa import router as qa_router


def setup_routers(dp: Dispatcher) -> None:
    """Настройка всех роутеров"""
    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(help_router)
    dp.include_router(qa_router)
