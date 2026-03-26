from abc import ABC, abstractmethod
from domain.entities.cpf_validation import CPFValidation

class ValidationRepositoryInterface(ABC):
    @abstractmethod
    async def save(self, cpf: str, valid: bool) -> CPFValidation:
        """Persist a CPF validation and return the created CPFValidation entity"""
        pass

    @abstractmethod
    async def get_by_cpf(self, cpf: str) -> list[CPFValidation]:
        """Retrieve all CPFValidation records for a given CPF"""
        pass

    @abstractmethod
    async def list(self, offset: int, limit: int) -> tuple[list[CPFValidation], int]:
        """List CPFValidation records with pagination, returning items and total count"""
        pass