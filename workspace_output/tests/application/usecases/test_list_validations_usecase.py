import pytest
from datetime import datetime, timezone

from domain.entities.cpf import CPF
from domain.entities.cpf_validation import CPFValidation
from application.usecases.list_validations_usecase import ListValidationsUseCase, PaginatedValidationsDTO


class DummyRepo:
    def __init__(self, items=None, total=0):
        self.items = items or []
        self.total = total
        self.called_with = None

    async def list(self, offset: int, limit: int):
        self.called_with = (offset, limit)
        return self.items, self.total


@pytest.mark.asyncio
async def test_execute_raises_value_error_for_invalid_page():
    repo = DummyRepo()
    usecase = ListValidationsUseCase(repo)
    with pytest.raises(ValueError):
        await usecase.execute(page=0, size=10)


@pytest.mark.asyncio
async def test_execute_raises_value_error_for_invalid_size():
    repo = DummyRepo()
    usecase = ListValidationsUseCase(repo)
    with pytest.raises(ValueError):
        await usecase.execute(page=1, size=0)


@pytest.mark.asyncio
async def test_execute_returns_empty_items_when_page_too_high():
    # Supondo total=2, page=2, size=2 → offset=2, não há itens
    repo = DummyRepo(items=[], total=2)
    usecase = ListValidationsUseCase(repo)
    result = await usecase.execute(page=2, size=2)
    assert isinstance(result, PaginatedValidationsDTO)
    assert result.page == 2
    assert result.size == 2
    assert result.total == 2
    assert result.items == []


@pytest.mark.asyncio
async def test_execute_calculates_offset_and_maps_items_and_returns_dto():
    # Cria um registro de domínio
    ts = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)
    cpf_value = "12345678901"
    rec = CPFValidation(id=5, cpf=CPF(cpf_value), valid=True, timestamp=ts)
    repo = DummyRepo(items=[rec], total=1)
    usecase = ListValidationsUseCase(repo)
    page, size = 2, 3

    result = await usecase.execute(page=page, size=size)

    # Verifica offset e limit passados ao repositório
    assert repo.called_with == ((page - 1) * size, size)
    # Verifica campos do DTO
    assert result.page == page
    assert result.size == size
    assert result.total == 1
    # Verifica o mapeamento do item
    assert len(result.items) == 1
    item = result.items[0]
    assert item.id == rec.id
    assert item.cpf == rec.cpf.value
    assert item.valid == rec.valid
    assert item.timestamp == rec.timestamp
