import pytest

from src.application.ports.validate_cpf_usecase import ValidateCpfUseCase
from src.application.use_cases.validate_cpf import ValidateCpfInteractor


class StubCPFValidator:
    """
    Stub para CPFValidator. Armazena o valor de retorno e o último CPF recebido.
    """
    def __init__(self, result: bool):
        self.result = result
        self.called_with = None

    def is_valid(self, cpf: str) -> bool:
        # Armazena o CPF recebido para verificação posterior
        self.called_with = cpf
        return self.result


def test_execute_returns_true_when_validator_returns_true():
    # Arrange: stub que retorna True
    stub_validator = StubCPFValidator(True)
    interactor = ValidateCpfInteractor(stub_validator)

    # Sanity check: deve implementar a interface
    assert isinstance(interactor, ValidateCpfUseCase)

    # Act: executa com um CPF qualquer
    cpf_input = "123.456.789-09"
    result = interactor.execute(cpf_input)

    # Assert: delegou corretamente e retornou True
    assert stub_validator.called_with == cpf_input
    assert result is True


def test_execute_returns_false_when_validator_returns_false():
    # Arrange: stub que retorna False
    stub_validator = StubCPFValidator(False)
    interactor = ValidateCpfInteractor(stub_validator)

    # Sanity check: deve implementar a interface
    assert isinstance(interactor, ValidateCpfUseCase)

    # Act: executa com um CPF inválido
    cpf_input = "111.111.111-11"
    result = interactor.execute(cpf_input)

    # Assert: delegou corretamente e retornou False
    assert stub_validator.called_with == cpf_input
    assert result is False