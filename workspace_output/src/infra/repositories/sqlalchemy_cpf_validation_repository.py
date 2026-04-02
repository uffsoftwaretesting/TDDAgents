from typing import Optional, List
from uuid import UUID

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.models import CPFValidationModel
from src.domain.models import CPFValidation


class SQLAlchemyCPFValidationRepository:
    """
    Concrete SQLAlchemy repository for CPFValidation entities.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, validation: CPFValidation) -> CPFValidation:
        # Map domain entity to ORM model
        model = CPFValidationModel(
            id=str(validation.id),
            cpf=validation.cpf,
            is_valid=validation.is_valid,
            created_at=validation.created_at
        )
        self._session.add(model)
        await self._session.commit()
        return validation

    async def get_by_cpf(self, cpf: str) -> Optional[CPFValidation]:
        # Retrieve the most recent record by created_at desc
        stmt = (
            select(CPFValidationModel)
            .where(CPFValidationModel.cpf == cpf)
            .order_by(desc(CPFValidationModel.created_at))
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalars().first()
        if model is None:
            return None
        # Convert ORM model to domain entity
        return CPFValidation(
            id=UUID(model.id),
            cpf=model.cpf,
            is_valid=model.is_valid,
            created_at=model.created_at
        )

    async def list_all(self, page: int, size: int) -> List[CPFValidation]:
        # Paginate: page starts at 1
        offset = (page - 1) * size
        stmt = (
            select(CPFValidationModel)
            .order_by(desc(CPFValidationModel.created_at))
            .offset(offset)
            .limit(size)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        # Map all to domain entities
        return [
            CPFValidation(
                id=UUID(m.id),
                cpf=m.cpf,
                is_valid=m.is_valid,
                created_at=m.created_at
            )
            for m in models
        ]
