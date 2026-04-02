import asyncio
import pytest
from alembic.config import Config
from alembic import command
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import inspect

# mark this test as asyncio-enabled
@pytest.mark.asyncio
async def test_alembic_creates_cpf_validations_table(tmp_path):
    # Create a temporary SQLite database file
    db_file = tmp_path / "test_migrations.db"
    test_db_url = f"sqlite+aiosqlite:///{db_file}"

    # Load the alembic configuration and override the URL
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", test_db_url)

    # Run migrations up to head
    command.upgrade(alembic_cfg, "head")

    # Create an async engine and verify the table exists
    engine = create_async_engine(test_db_url, future=True)
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("cpf_validations"))

    assert has_table, "Table 'cpf_validations' should exist after running migrations"