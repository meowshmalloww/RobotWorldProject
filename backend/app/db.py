"""Async SQLAlchemy engine + session factory (SQLite via aiosqlite)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import DB_PATH

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    from . import models  # noqa: F401 — register metadata

    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
