import pytest
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

import src.infra.database as database_module
from src.infra.models import CPFValidationModel
from src.infra.repositories.sqlalchemy_cpf_validation_repository import SQLAlchemyCPFValidationRepository
from src.domain.models import CPFValidation

@ pytest.fixture
def db_url(tmp_path):
    # Use a temporary SQLite file for isolation, function-scoped
    db_file = tmp_path / "test_repo.db"
    return f"sqlite+aiosqlite:///{db_file}"

@ pytest.fixture()
async def engine(db_url):
    # Create async engine and initialize metadata
    engine = create_async_engine(db_url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(database_module.Base.metadata.create_all)
    yield engine
    await engine.dispose()

@ pytest.fixture
async def session(engine):
    # Provide a transactional AsyncSession
    AsyncSessionLocal = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with AsyncSessionLocal() as sess:
        yield sess

@ pytest.fixture
def repository(session):
    # Instantiate repository with the test session
    return SQLAlchemyCPFValidationRepository(session=session)

@ pytest.mark.asyncio
async def test_save_and_persist(repository, session):
    # Create a domain entity
    now = datetime.now(timezone.utc)
    domain = CPFValidation(
        id=uuid4(),
        cpf="12345678901",
        is_valid=True,
        created_at=now
    )
    # Save via repository
    saved = await repository.save(domain)
    assert isinstance(saved, CPFValidation)
    # Verify persisted via ORM model
    result = await session.execute(
        select(CPFValidationModel).where(CPFValidationModel.cpf == "12345678901")
    )
    row = result.scalars().first()
    assert row is not None
    assert row.id == str(domain.id)
    assert row.cpf == domain.cpf
    assert row.is_valid == domain.is_valid
    assert row.created_at == domain.created_at

@pytest.mark.asyncio
async def test_get_by_cpf_returns_latest(repository):
    cpf = "11111111111"
    # Create two entries: older and newer
    base = datetime.now(timezone.utc)
    older = CPFValidation(
        id=uuid4(), cpf=cpf, is_valid=False, created_at=base
    )
    newer = CPFValidation(
        id=uuid4(), cpf=cpf, is_valid=True,
        created_at=base + timedelta(seconds=10)
    )
    await repository.save(older)
    await repository.save(newer)
    latest = await repository.get_by_cpf(cpf)
    assert latest is not None
    assert latest.id == newer.id
    assert latest.is_valid is True

@pytest.mark.asyncio
async def test_get_by_cpf_none(repository):
    # Querying unknown CPF should give None
    result = await repository.get_by_cpf("00000000000")
    assert result is None

@pytest.mark.asyncio
async def test_list_all_pagination(repository):
    # Insert multiple entries with increasing timestamps
    base = datetime.now(timezone.utc)
    entries = []
    for i in range(5):
        ent = CPFValidation(
            id=uuid4(),
            cpf=str(i).zfill(11),
            is_valid=(i % 2 == 0),
            created_at=base + timedelta(seconds=i)
        )
        entries.append(ent)
        await repository.save(ent)
    # Page 1, size 2 => two newest entries
    page1 = await repository.list_all(page=1, size=2)
    assert len(page1) == 2
    # The two entries with highest created_at: i=4 then i=3
    assert page1[0].id == entries[4].id
    assert page1[1].id == entries[3].id
    # Page 2, size 2 => next two: i=2 then i=1
    page2 = await repository.list_all(page=2, size=2)
    assert len(page2) == 2
    assert page2[0].id == entries[2].id
    assert page2[1].id == entries[1].id
    # Page 3, size 2 => last entry: i=0
    page3 = await repository.list_all(page=3, size=2)
    assert len(page3) == 1
    assert page3[0].id == entries[0].id
