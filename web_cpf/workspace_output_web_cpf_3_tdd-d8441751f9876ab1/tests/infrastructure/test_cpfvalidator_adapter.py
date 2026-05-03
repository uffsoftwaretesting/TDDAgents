import pytest
from src.infrastructure.cpfvalidator_adapter import CPFValidatorAdapter


class DummyCPF:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.return_value: bool | None = None

    def validate(self, cpf_str: str) -> bool:
        # record the received argument for normalization check
        self.calls.append(cpf_str)
        return self.return_value  # type: ignore[return-value]


def test_normalization_and_valid_true(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: dummy external CPF returns True and records call
    dummy = DummyCPF()
    dummy.return_value = True
    monkeypatch.setattr(
        "src.infrastructure.cpfvalidator_adapter.CPF",
        lambda: dummy,
    )
    adapter = CPFValidatorAdapter()

    result = adapter.is_valid("529.982.247-25")
    assert result is True
    assert dummy.calls == ["52998224725"]


def test_returns_false_when_external_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: dummy external CPF returns False
    dummy = DummyCPF()
    dummy.return_value = False
    monkeypatch.setattr(
        "src.infrastructure.cpfvalidator_adapter.CPF",
        lambda: dummy,
    )
    adapter = CPFValidatorAdapter()

    result = adapter.is_valid("12345678909")
    assert result is False
    assert dummy.calls == ["12345678909"]


def test_returns_false_on_external_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: external CPF.validate raises an exception
    class ErrorCPF:
        def validate(self, cpf_str: str) -> bool:
            raise ValueError("external error")

    monkeypatch.setattr(
        "src.infrastructure.cpfvalidator_adapter.CPF",
        lambda: ErrorCPF(),
    )
    adapter = CPFValidatorAdapter()

    assert adapter.is_valid("any-format") is False
