from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from api.core.config import settings


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos SQLAlchemy."""


DATABASE_URL = settings.database_url or "sqlite+aiosqlite:///./rnd.db"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Crea las tablas si aún no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
