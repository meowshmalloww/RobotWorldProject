import pytest

from app.db import SQLITE_BUSY_TIMEOUT_SECONDS, engine, init_db


@pytest.mark.asyncio
async def test_sqlite_runtime_uses_wal_and_explicit_busy_timeout():
    await init_db()

    async with engine.connect() as connection:
        journal_mode = (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
        busy_timeout_ms = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
        foreign_keys = (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar_one()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout_ms == SQLITE_BUSY_TIMEOUT_SECONDS * 1000
    assert foreign_keys == 1
