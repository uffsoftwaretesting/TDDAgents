from core.domain.entities.cpf import CPF
from core.domain.errors import DomainError
from application.dto.validation_result import ValidationResult
from infrastructure.validators.validate_docbr import LibraryCPFValidator


class ValidateCPFUseCase:
    """
    Use case for validating a CPF.
    """
    def __init__(self):
        self._validator = LibraryCPFValidator()

    def execute(self, raw_cpf: str) -> ValidationResult:
        # Trim whitespace from input
        trimmed = raw_cpf.strip()
        try:
            # Instantiate domain entity (may raise DomainError)
            cpf_entity = CPF(trimmed)
        except DomainError:
            # On domain error, return invalid result with empty formatting
            return ValidationResult(cpf_original=trimmed, cpf_formatado="", valid=False)

        normalized = cpf_entity.normalized
        # Delegate digit validation to the infrastructure adapter
        is_valid = self._validator.is_valid(normalized)

        # Format only when we have exactly 11 digits
        formatted = ""
        if len(normalized) == 11:
            formatted = f"{normalized[0:3]}.{normalized[3:6]}.{normalized[6:9]}-{normalized[9:11]}"

        return ValidationResult(cpf_original=cpf_entity.raw, cpf_formatado=formatted, valid=is_valid)
