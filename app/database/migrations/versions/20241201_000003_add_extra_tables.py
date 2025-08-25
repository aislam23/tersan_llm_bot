"""
Добавление полей доступа/админки пользователям и таблицы одноразовых приглашений
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from loguru import logger

from app.database.migrations.base import Migration


class AddExtraTablesMigration(Migration):
    """Добавление invitation и полей is_admin, default для is_active"""
    
    def get_version(self) -> str:
        return "20241201_000003"
    
    def get_description(self) -> str:
        return "Add invitations table, add is_admin to users, set default is_active false"
    
    async def upgrade(self, connection: AsyncConnection) -> None:
        # Добавим колонку is_admin, если её нет
        res = await connection.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = 'users' 
                AND column_name = 'is_admin'
            );
        """))
        has_is_admin = res.scalar()
        if not has_is_admin:
            logger.info("Adding is_admin column to users table...")
            await connection.execute(text("""
                ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;
            """))

        # Обновим default для is_active на FALSE
        logger.info("Setting users.is_active default to FALSE...")
        await connection.execute(text("""
            ALTER TABLE users ALTER COLUMN is_active SET DEFAULT FALSE;
        """))

        # Создадим таблицу invitations, если её нет
        res = await connection.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'invitations'
            );
        """))
        has_inv = res.scalar()
        if not has_inv:
            logger.info("Creating invitations table...")
            await connection.execute(text("""
                CREATE TABLE invitations (
                    id SERIAL PRIMARY KEY,
                    token VARCHAR(255) UNIQUE NOT NULL,
                    created_by BIGINT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    is_used BOOLEAN DEFAULT FALSE,
                    used_by BIGINT,
                    used_at TIMESTAMP WITH TIME ZONE
                );
            """))
            await connection.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_invitations_is_used ON invitations(is_used);
            """))