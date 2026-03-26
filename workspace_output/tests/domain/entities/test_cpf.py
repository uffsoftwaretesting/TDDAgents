import pytest

from domain.entities.cpf import CPF


def test_init_accepts_valid_cpf_string():
    value = "12345678901"
    cpf = CPF(value)
    assert cpf.value == value


@pytest.mark.parametrize(
    "invalid",
    [
        "1234567890",      # too short
        "123456789012",    # too long
        "abcdefghijk",     # non-numeric
        "12345!78901",     # special character
        ""                 # empty
    ],
)
def test_init_raises_valueerror_for_invalid_format(invalid):
    with pytest.raises(ValueError):
        CPF(invalid)


def test_is_valid_true_for_valid_cpf(monkeypatch):
    # Simulate validate-docbr returning True for valid CPFs
    import validate_docbr

    class DummyCPF:
        def validate(self, value):
            return True

    monkeypatch.setattr(validate_docbr, 'CPF', DummyCPF)

    cpf_value = "12345678901"
    cpf = CPF(cpf_value)
    assert cpf.is_valid() is True


def test_is_valid_false_for_invalid_cpf(monkeypatch):
    # Simulate validate-docbr returning False for invalid CPFs
    import validate_docbr

    class DummyCPF:
        def validate(self, value):
            return False

    monkeypatch.setattr(validate_docbr, 'CPF', DummyCPF)

    cpf_value = "12345678901"
    cpf = CPF(cpf_value)
    assert cpf.is_valid() is False


def test_repeated_digits_are_invalid(monkeypatch):
    # Simulate that CPFs with all repeated digits are invalid
    import validate_docbr

    class DummyCPF:
        def validate(self, value):
            if len(set(value)) == 1:
                return False
            return True

    monkeypatch.setattr(validate_docbr, 'CPF', DummyCPF)

    repeated = "11111111111"
    cpf = CPF(repeated)
    assert cpf.is_valid() is False
