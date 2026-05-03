import pytest

from src.application.ports.validate_cpf_use_case import IValidateCpfUseCase
from src.application.use_cases.validate_cpf_use_case import ValidateCpfUseCase


class DummyRepositorySuccess:
    """
    Repositório dummy que sempre retorna True na validação externa.
    """
    def validate(self, cpf_str: str) -> bool:
        return True


class DummyRepositoryFail:
    """
    Repositório dummy que sempre retorna False na validação externa.
    """
    def validate(self, cpf_str: str) -> bool:
        return False


def test_validate_cpf_use_case_implements_interface():
    use_case = ValidateCpfUseCase(repository=DummyRepositorySuccess())
    assert isinstance(use_case, IValidateCpfUseCase)


@ pytest.mark.parametrize("cpf_input", [
    "529.982.247-25",  # máscara
    "52998224725",     # sem máscara
])
def test_execute_returns_true_for_valid_cpf(cpf_input):
    use_case = ValidateCpfUseCase(repository=DummyRepositorySuccess())
    result = use_case.execute(cpf_input)
    assert result is True


@ pytest.mark.parametrize("invalid_cpf", [
    "111.111.111-11",  # todos dígitos iguais
    "123.456.789-00",  # dígitos verificadores incorretos
])
def test_execute_returns_false_for_invalid_cpf_due_to_domain(invalid_cpf):
    use_case = ValidateCpfUseCase(repository=DummyRepositorySuccess())
    result = use_case.execute(invalid_cpf)
    assert result is False


def test_execute_returns_false_when_repository_reports_invalid():
    # Mesmo CPF válido em domínio, repositório externo indica inválido
    cpf_input = "529.982.247-25"
    use_case = ValidateCpfUseCase(repository=DummyRepositoryFail())
    result = use_case.execute(cpf_input)
    assert result is False
