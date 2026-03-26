import os
import pytest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

import infra.repository.sqlalchemy_impl.models as models
from infra.repository.sqlalchemy_impl.validation_repository import ValidationRepository
from domain.entities.cpf_validation import CPFValidation


def pytest_configure():
    # ensure pytest-asyncio is recognized
    try:
        import pytest_asyncio  # noqa: F401
    except ImportError:
        pass


@ pytest.fixture(scope="module")
def db_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL environment variable not set, skipping integration tests")
    return url


@ pytest.fixture(scope="module")
def engine(db_url):
    # create async engine
    engine = create_async_engine(db_url, future=True)
    yield engine
    # dispose engine after tests
    engine.sync_engine.dispose()  # ensure connections are closed


@ pytest.fixture
async def session(engine):
    # reset schema for each test
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.drop_all)
        await conn.run_sync(models.Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session


@ pytest.fixture
async def repo(session):
    # repository takes AsyncSession in constructor
    return ValidationRepository(session)


@ pytest.mark.asyncio
async def test_save_and_get_by_cpf(repo):
    # Test saving a record and retrieving by CPF
    cpf_value = "12345678901"
    valid_flag = True
    saved = await repo.save(cpf_value, valid_flag)
    # Should return a domain CPFValidation entity
    assert isinstance(saved, CPFValidation)
    assert saved.id is not None
    assert saved.cpf.value == cpf_value
    assert saved.valid is True
    assert isinstance(saved.timestamp, datetime)
    assert saved.timestamp.tzinfo == timezone.utc

    # get_by_cpf should return our saved record
    results = await repo.get_by_cpf(cpf_value)
    assert isinstance(results, list)
    assert len(results) == 1
    rec = results[0]
    assert isinstance(rec, CPFValidation)
    assert rec.id == saved.id
    assert rec.cpf.value == cpf_value
    assert rec.valid is True
    assert rec.timestamp == saved.timestamp


@ pytest.mark.asyncio
async def test_list_pagination(repo):
    # Setup multiple records
    # First record
    first = await repo.save("11111111111", True)
    # Second record
    second = await repo.save("22222222222", False)

    # List with offset=0, limit=1
    items0, total0 = await repo.list(offset=0, limit=1)
    assert total0 == 2
    assert len(items0) == 1
    assert items0[0].id == first.id

    # List with offset=1, limit=1
    items1, total1 = await repo.list(offset=1, limit=1)
    assert total1 == 2
    assert len(items1) == 1
    assert items1[0].id == second.id

    # Offset beyond total should return empty list
    items2, total2 = await repo.list(offset=2, limit=1)
    assert total2 == 2
    assert items2 == []
