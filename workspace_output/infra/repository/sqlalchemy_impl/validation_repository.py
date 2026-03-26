from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from infra.repository.interfaces.validation_repository_interface import ValidationRepositoryInterface
from infra.repository.sqlalchemy_impl import models
from domain.entities.cpf import CPF
from domain.entities.cpf_validation import CPFValidation


class ValidationRepository(ValidationRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, cpf: str, valid: bool) -> CPFValidation:
        # Create model instance
        now = datetime.now(timezone.utc)
        model = models.CPFValidation(cpf=cpf, valid=valid, timestamp=now)
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        # Build domain entity
        domain_cpf = CPF(model.cpf)
        domain_validation = CPFValidation(
            id=model.id,
            cpf=domain_cpf,
            valid=model.valid,
            timestamp=model.timestamp
        )
        return domain_validation

    async def get_by_cpf(self, cpf: str) -> list[CPFValidation]:
        stmt = select(models.CPFValidation).where(models.CPFValidation.cpf == cpf)
        result = await self.session.execute(stmt)
        records = result.scalars().all()
        domain_list: list[CPFValidation] = []
        for rec in records:
            domain_list.append(
                CPFValidation(
                    id=rec.id,
                    cpf=CPF(rec.cpf),
                    valid=rec.valid,
                    timestamp=rec.timestamp
                )
            )
        return domain_list

    async def list(self, offset: int, limit: int) -> tuple[list[CPFValidation], int]:
        # Paginated query
        stmt = (
            select(models.CPFValidation)
            .order_by(models.CPFValidation.id)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        records = result.scalars().all()
        # Total count
        count_stmt = select(func.count()).select_from(models.CPFValidation)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()
        # Map to domain entities
        domain_items: list[CPFValidation] = []
        for rec in records:
            domain_items.append(
                CPFValidation(
                    id=rec.id,
                    cpf=CPF(rec.cpf),
                    valid=rec.valid,
                    timestamp=rec.timestamp
                )
            )
        return domain_items, total
