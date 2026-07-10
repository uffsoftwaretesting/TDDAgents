import importlib
import pytest

class FakeCpfLibrary:
    """
    Fake wrapper for the external CPF library.
    Allows injecting return_value or exception and captures calls.
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

@pytest.fixture
def fake_cpf_lib(monkeypatch):
    """
    Injects FakeCpfLibrary into the adapter as the CPF class.
    Returns the fake instance so tests can configure it.
    """
    # Import adapter module to patch
    adapter_module = importlib.import_module('infrastructure.cpf_validator_adapter')
    # Create a single fake instance for the test
    fake = FakeCpfLibrary()
    # Monkeypatch the CPF constructor in adapter to always return our fake
    monkeypatch.setattr(adapter_module, 'CPF', lambda: fake)
    return fake

@ pytest.mark.parametrize("input_cpf, lib_return, expected", [
    ("52998224725", True, True),
    ("529.982.247-25", True, True),
    ("529.982.247-25", False, False),
    ("52998224725", False, False),
])
def test_adapter_returns_bool_based_on_library(input_cpf, lib_return, expected, fake_cpf_lib):
    # Configure the fake library's return_value
    fake_cpf_lib.return_value = lib_return
    # Instantiate the adapter and call validate
    adapter = importlib.import_module('infrastructure.cpf_validator_adapter').CpfValidatorAdapter()
    result = adapter.validate(input_cpf)
    assert result is expected
    # Ensure normalization: only digits passed to external lib
    assert fake_cpf_lib.calls[-1] == "52998224725"


def test_adapter_catches_exceptions_and_returns_false(fake_cpf_lib):
    # Simulate library throwing an exception
    fake_cpf_lib.exception = Exception("internal lib error")
    adapter = importlib.import_module('infrastructure.cpf_validator_adapter').CpfValidatorAdapter()
    result = adapter.validate("123.456.789-09")
    # Adapter should catch and return False
    assert result is False
    # Confirm the normalized input was still passed
    assert fake_cpf_lib.calls[-1] == "12345678909"