"""Async SQLAlchemy engine + session factory (SQLite via aiosqlite).

RobotWorld has several legitimate writers (commands, audit events, telemetry,
and durable background runs).  SQLite's rollback journal makes a concurrent
reader block a writer, while its default five-second busy timeout is too short
for long-running local robotics activity.  WAL keeps reads concurrent with the
single writer and the explicit timeout absorbs short write bursts without
turning them into failed agent activities.
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import DB_PATH

SQLITE_BUSY_TIMEOUT_SECONDS = 30

engine = create_async_engine(
    f"sqlite+aiosqlite:///{DB_PATH}",
    echo=False,
    future=True,
    connect_args={"timeout": SQLITE_BUSY_TIMEOUT_SECONDS},
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Apply connection-local safety and contention settings to every handle."""

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    from . import models  # noqa: F401 — register metadata

    # journal_mode is database-persistent. Set it before opening the schema
    # transaction so a fresh workspace and an upgraded workspace behave alike.
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        journal_mode = str(result.scalar_one()).lower()
        if journal_mode != "wal":
            raise RuntimeError(f"SQLite refused WAL journal mode: {journal_mode}")

    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
