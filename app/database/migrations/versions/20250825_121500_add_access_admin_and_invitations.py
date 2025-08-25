"""
Добавление users.is_admin, изменение дефолта users.is_active на FALSE,
создание таблицы одноразовых приглашений invitations
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from loguru import logger

from app.database.migrations.base import Migration


class AddAccessAdminAndInvitationsMigration(Migration):
    """Добавляет поля доступа/админки и таблицу приглашений"""
    
    def get_version(self) -> str:
        # Уникальная версия в формате YYYYMMDD_HHMMSS
        return "20250825_121500"
    
    def get_description(self) -> str:
        return "Add users.is_admin, set users.is_active default FALSE, create invitations"
    
    async def check_can_apply(self, connection: AsyncConnection) -> bool:
        """Применяем миграцию, если чего-то из нужного нет"""
        # is_admin column exists?
        result = await connection.execute(text(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_schema = 'public' 
                  AND table_name = 'users' 
                  AND column_name = 'is_admin'
            );
            """
        ))
        has_is_admin = bool(result.scalar())

        # invitations table exists?
        result = await connection.execute(text(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                  AND table_name = 'invitations'
            );
            """
        ))
        has_invitations = bool(result.scalar())

        # Применяем, если отсутствует колонка или таблица
        return not (has_is_admin and has_invitations)

    async def upgrade(self, connection: AsyncConnection) -> None:
        # Добавим колонку is_admin, если её нет
        logger.info("Ensuring users.is_admin exists...")
        await connection.execute(text(
            """
            ALTER TABLE users 
            ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
            """
        ))

        # Установим дефолт для is_active = FALSE (безопасно выполнить повторно)
        logger.info("Setting users.is_active default to FALSE...")
        await connection.execute(text(
            """
            ALTER TABLE users 
            ALTER COLUMN is_active SET DEFAULT FALSE;
            """
        ))

        # Создадим таблицу invitations, если её нет
        logger.info("Ensuring invitations table exists...")
        await connection.execute(text(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id SERIAL PRIMARY KEY,
                token VARCHAR(255) UNIQUE NOT NULL,
                created_by BIGINT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                is_used BOOLEAN DEFAULT FALSE,
                used_by BIGINT,
                used_at TIMESTAMP WITH TIME ZONE
            );
            """
        ))

        await connection.execute(text(
            """
            CREATE INDEX IF NOT EXISTS idx_invitations_is_used 
            ON invitations(is_used);
            """
        ))

        logger.info("✅ users.is_admin ensured, users.is_active default set to FALSE, invitations ready")


