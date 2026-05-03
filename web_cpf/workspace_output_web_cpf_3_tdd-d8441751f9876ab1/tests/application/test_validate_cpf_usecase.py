from unittest.mock import Mock

from src.application.validators import ICPFValidator
from src.application.validate_cpf_usecase import ValidateCPFUseCase


def test_validate_cpf_usecase_returns_true_when_validator_is_valid() -> None:
    # Arrange
    mock_validator = Mock(spec=ICPFValidator)
    mock_validator.is_valid.return_value = True
    use_case = ValidateCPFUseCase(mock_validator)
    test_cpf = "12345678909"

    # Act
    result = use_case.execute(test_cpf)

    # Assert
    assert result is True
    mock_validator.is_valid.assert_called_once_with(test_cpf)


def test_validate_cpf_usecase_returns_false_when_validator_is_not_valid() -> None:
    # Arrange
    mock_validator = Mock(spec=ICPFValidator)
    mock_validator.is_valid.return_value = False
    use_case = ValidateCPFUseCase(mock_validator)
    test_cpf = "98765432100"

    # Act
    result = use_case.execute(test_cpf)

    # Assert
    assert result is False
    mock_validator.is_valid.assert_called_once_with(test_cpf)
