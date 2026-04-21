import inspect
import collections.abc
from typing import Callable

import pytest

from src.adams_bashforth_3 import adams_bashforth_3, _validate_args


def test_docstring_adams_bashforth_3():
    """
    Garante que `adams_bashforth_3` possui docstring não vazia.
    """
    doc = adams_bashforth_3.__doc__
    assert doc is not None and doc.strip() != "", "Função `adams_bashforth_3` deve ter uma docstring"


def test_docstring_validate_args():
    """
    Garante que `_validate_args` possui docstring não vazia.
    """
    doc = _validate_args.__doc__
    assert doc is not None and doc.strip() != "", "Função `_validate_args` deve ter uma docstring"


def test_adams_bashforth_3_type_annotations():
    """
    Verifica se `adams_bashforth_3` está completamente anotada com tipos PEP484.
    """
    sig = inspect.signature(adams_bashforth_3)
    expected_params = ['f', 't0', 'y0', 't_final', 'h']
    # Checa ordem e nomes dos parâmetros
    assert list(sig.parameters.keys()) == expected_params, (
        f"Parâmetros esperados {expected_params}, mas encontrados {list(sig.parameters.keys())}"
    )
    # Verifica anotações dos parâmetros
    for name in expected_params:
        param = sig.parameters[name]
        assert param.annotation is not inspect._empty, (
            f"Parâmetro '{name}' deve ter anotação de tipo"
        )
        if name != 'f':
            assert param.annotation is float, (
                f"Parâmetro '{name}' deve ser anotado como float"
            )
    # Verifica anotação de `f` como Callable[[float, float], float]
    f_anno = sig.parameters['f'].annotation
    origin = getattr(f_anno, '__origin__', None)
    assert origin in (Callable, collections.abc.Callable), "Parâmetro 'f' deve ser typing.Callable"
    args = getattr(f_anno, '__args__', None)
    assert isinstance(args, tuple) and len(args) in (2, 3), (
        "Annotation de Callable deve ter dois argumentos (lista de tipos e tipo de retorno) "
        "ou três (params individuais + retorno)"
    )
    # Normaliza params_list e ret_type para ambas as formas de __args__
    if len(args) == 2:
        params_list, ret_type = args
    else:
        params_list, ret_type = list(args[:-1]), args[-1]
    assert params_list == [float, float], (
        "Tipos de parâmetro de `f` devem ser [float, float]"
    )
    assert ret_type is float, "Tipo de retorno de `f` deve ser float"
    # Verifica retorno da função
    assert sig.return_annotation is float, "Retorno de `adams_bashforth_3` deve ser float"


def test_validate_args_type_annotations():
    """
    Verifica se `_validate_args` está completamente anotada com tipos PEP484.
    """
    sig = inspect.signature(_validate_args)
    expected_params = ['f', 't0', 'y0', 't_final', 'h']
    # Checa nomes dos parâmetros
    assert list(sig.parameters.keys()) == expected_params, (
        f"Parâmetros esperados {expected_params}, mas encontrados {list(sig.parameters.keys())}"
    )
    # Verifica anotações dos parâmetros
    for name in expected_params:
        param = sig.parameters[name]
        assert param.annotation is not inspect._empty, (
            f"Parâmetro '{name}' deve ter anotação de tipo"
        )
    # Verifica anotação de retorno None
    assert sig.return_annotation is None, "Retorno de `_validate_args` deve ser None"