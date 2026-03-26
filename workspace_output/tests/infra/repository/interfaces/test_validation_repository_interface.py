import inspect
from typing import get_origin, get_args

import pytest

from infra.repository.interfaces.validation_repository_interface import ValidationRepositoryInterface
from domain.entities.cpf_validation import CPFValidation


def test_methods_exist_and_are_async():
    # The interface should define async methods: save, get_by_cpf, list
    for method_name in ('save', 'get_by_cpf', 'list'):
        assert hasattr(ValidationRepositoryInterface, method_name), \
            f"Method '{method_name}' not found in ValidationRepositoryInterface"
        method = getattr(ValidationRepositoryInterface, method_name)
        assert inspect.iscoroutinefunction(method), \
            f"Method '{method_name}' should be defined as async"


def test_save_signature():
    sig = inspect.signature(ValidationRepositoryInterface.save)
    params = list(sig.parameters.values())
    # Expect: self, cpf: str, valid: bool
    assert len(params) == 3, f"Expected 3 parameters for save, got {len(params)}"
    _, cpf_param, valid_param = params
    assert cpf_param.name == 'cpf' and cpf_param.annotation is str, \
        f"Expected 'cpf' parameter annotated as str, got {cpf_param}"
    assert valid_param.name == 'valid' and valid_param.annotation is bool, \
        f"Expected 'valid' parameter annotated as bool, got {valid_param}"
    # Return type
    assert sig.return_annotation is CPFValidation, \
        f"Expected return annotation CPFValidation, got {sig.return_annotation}"


def test_get_by_cpf_signature():
    sig = inspect.signature(ValidationRepositoryInterface.get_by_cpf)
    params = list(sig.parameters.values())
    # Expect: self, cpf: str
    assert len(params) == 2, f"Expected 2 parameters for get_by_cpf, got {len(params)}"
    _, cpf_param = params
    assert cpf_param.name == 'cpf' and cpf_param.annotation is str, \
        f"Expected 'cpf' parameter annotated as str, got {cpf_param}"
    # Return type: list[CPFValidation]
    ret = sig.return_annotation
    assert get_origin(ret) is list, \
        f"Expected return type origin list, got {get_origin(ret)}"
    assert get_args(ret) == (CPFValidation,), \
        f"Expected return arguments (CPFValidation,), got {get_args(ret)}"


def test_list_signature():
    sig = inspect.signature(ValidationRepositoryInterface.list)
    params = list(sig.parameters.values())
    # Expect: self, offset: int, limit: int
    assert len(params) == 3, f"Expected 3 parameters for list, got {len(params)}"
    _, offset_param, limit_param = params
    assert offset_param.name == 'offset' and offset_param.annotation is int, \
        f"Expected 'offset' parameter annotated as int, got {offset_param}"
    assert limit_param.name == 'limit' and limit_param.annotation is int, \
        f"Expected 'limit' parameter annotated as int, got {limit_param}"
    # Return type: tuple[list[CPFValidation], int]
    ret = sig.return_annotation
    assert get_origin(ret) is tuple, \
        f"Expected return type origin tuple, got {get_origin(ret)}"
    args = get_args(ret)
    # First element should be list[CPFValidation]
    assert get_origin(args[0]) is list, \
        f"Expected first tuple return arg origin list, got {get_origin(args[0])}"
    assert get_args(args[0]) == (CPFValidation,), \
        f"Expected first tuple return arg (CPFValidation,), got {get_args(args[0])}"
    # Second element should be int
    assert args[1] is int, \
        f"Expected second tuple return arg int, got {args[1]}"