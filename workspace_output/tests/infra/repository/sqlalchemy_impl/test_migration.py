import os
import shutil
import pytest
from alembic.config import Config
from alembic import command

@pytest.mark.skipif(
    'TEST_DATABASE_URL' not in os.environ,
    reason="TEST_DATABASE_URL environment variable not set"
)
def test_upgrade_and_downgrade_migrations(tmp_path):
    """
    This test will copy the alembic.ini to a temporary directory,
    override the sqlalchemy.url, and run upgrade to head and downgrade to base.
    """
    # Copy alembic.ini into temp directory
    shutil.copy('alembic.ini', tmp_path / 'alembic.ini')
    # Configure Alembic
    cfg = Config(str(tmp_path / 'alembic.ini'))
    # Point script_location to our alembic folder
    cfg.set_main_option('script_location', 'alembic')
    # Override database URL for migrations
    cfg.set_main_option('sqlalchemy.url', os.environ['TEST_DATABASE_URL'])

    # Apply migrations (should run without error)
    command.upgrade(cfg, 'head')
    # Revert migrations to base (should run without error)
    command.downgrade(cfg, 'base')