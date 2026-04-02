import pytest
from uuid import UUID, uuid4
from datetime import datetime, timezone

from src.domain.models import CPFValidation


def test_cpf_validation_creation_and_types():
    # Prepare sample data
    id_value = uuid4()
    cpf_value = '12345678901'
    is_valid_value = True
    created_at_value = datetime.now(timezone.utc)

    # Create the domain entity
    validation = CPFValidation(
        id=id_value,
        cpf=cpf_value,
        is_valid=is_valid_value,
        created_at=created_at_value
    )

    # Assert instance and attribute types
    assert isinstance(validation, CPFValidation)
    assert isinstance(validation.id, UUID)
    assert validation.id == id_value

    assert isinstance(validation.cpf, str)
    assert validation.cpf == cpf_value

    assert isinstance(validation.is_valid, bool)
    assert validation.is_valid is is_valid_value

    assert isinstance(validation.created_at, datetime)
    assert validation.created_at == created_at_value
    # Ensure created_at is timezone-aware and UTC
    assert validation.created_at.tzinfo is not None
    assert validation.created_at.tzinfo == timezone.utc
