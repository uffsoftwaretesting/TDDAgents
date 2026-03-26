import pytest
from sqlalchemy import Integer, String, Boolean, DateTime
from infra.repository.sqlalchemy_impl import models


def test_model_class_exists():
    # Ensure the CPFValidation model class is defined
    assert hasattr(models, 'CPFValidation'), "CPFValidation class not defined in models"


def test_tablename():
    # Ensure a __tablename__ is set
    cls = models.CPFValidation
    assert hasattr(cls, '__tablename__'), "__tablename__ not defined"
    assert isinstance(cls.__tablename__, str) and cls.__tablename__, "__tablename__ should be a non-empty string"


def test_columns_definition():
    # Inspect table metadata for correct columns and types
    cls = models.CPFValidation
    table = cls.__table__
    # id column
    assert 'id' in table.c, "id column missing"
    id_col = table.c.id
    assert id_col.primary_key, "id should be primary key"
    assert isinstance(id_col.type, Integer), "id should be Integer"

    # cpf column
    assert 'cpf' in table.c, "cpf column missing"
    cpf_col = table.c.cpf
    assert isinstance(cpf_col.type, String), "cpf should be String"
    assert cpf_col.type.length == 11, "cpf length should be 11"
    assert not cpf_col.nullable, "cpf should be non-nullable"

    # valid column
    assert 'valid' in table.c, "valid column missing"
    valid_col = table.c.valid
    assert isinstance(valid_col.type, Boolean), "valid should be Boolean"
    assert not valid_col.nullable, "valid should be non-nullable"

    # timestamp column
    assert 'timestamp' in table.c, "timestamp column missing"
    ts_col = table.c.timestamp
    assert isinstance(ts_col.type, DateTime), "timestamp should be DateTime"
    assert ts_col.type.timezone, "timestamp should have timezone set to True"
    assert not ts_col.nullable, "timestamp should be non-nullable"