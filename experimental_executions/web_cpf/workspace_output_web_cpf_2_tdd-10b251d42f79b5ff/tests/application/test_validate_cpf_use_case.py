import pytest

from application.validate_cpf_use_case import ValidateCpfUseCase


class FakeValidator:
    """
    Simula a interface ICpfValidator com método validate(cpf) -> bool
    Pode retornar um valor fixo ou lançar uma exceção.
    """
    def __init__(self, return_value=None, exception=None):
        self.return_value = return_value
        self.exception = exception
        self.calls = []

    def validate(self, cpf: str) -> bool:
        self.calls.append(cpf)
        if self.exception:
            raise self.exception
        return self.return_value


@pytest.mark.parametrize("validator_return, expected", [
    (True, True),
    (False, False),
])
def test_execute_returns_expected_when_validator_returns(validator_return, expected):
    validator = FakeValidator(return_value=validator_return)
    use_case = ValidateCpfUseCase(validator)

    result = use_case.execute("12345678901")

    assert result is expected
    # Confirma que o validator recebeu o mesmo CPF
    assert validator.calls == ["12345678901"]


def test_execute_returns_false_when_validator_raises():
    # Simula falha interna no validator
    validator = FakeValidator(exception=Exception("validation error"))
    use_case = ValidateCpfUseCase(validator)

    # Deve capturar a exceção e retornar False
    result = use_case.execute("00000000000")

    assert result is False
    assert validator.calls == ["00000000000"]