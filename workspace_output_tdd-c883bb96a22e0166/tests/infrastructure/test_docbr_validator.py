import pytest
from src.infrastructure.cpf_validator.docbr_validator import ValidateDocbrCpfValidator
from src.domain.ports.cpf_validator import CPFValidator


def test_validator_is_instance_of_interface():
    """
    ValidateDocbrCpfValidator should implement the CPFValidator interface.
    """
    validator = ValidateDocbrCpfValidator()
    assert isinstance(validator, CPFValidator)


def test_is_valid_delegates_to_external_validator(mocker):
    """
    The is_valid method must call validate_docbr.CPF.validate and return True when it returns True.
    """
    # Patch the external CPF class
    mock_cpf_class = mocker.patch('validate_docbr.CPF')
    mock_external = mock_cpf_class.return_value
    mock_external.validate.return_value = True

    validator = ValidateDocbrCpfValidator()
    result = validator.is_valid('123.456.789-09')

    # Ensure delegation and correct return value
    mock_external.validate.assert_called_once_with('123.456.789-09')
    assert result is True


def test_is_valid_returns_false_when_external_validator_returns_false(mocker):
    """
    The is_valid method must call validate_docbr.CPF.validate and return False when it returns False.
    """
    mock_cpf_class = mocker.patch('validate_docbr.CPF')
    mock_external = mock_cpf_class.return_value
    mock_external.validate.return_value = False

    validator = ValidateDocbrCpfValidator()
    result = validator.is_valid('111.111.111-11')

    mock_external.validate.assert_called_once_with('111.111.111-11')
    assert result is False
