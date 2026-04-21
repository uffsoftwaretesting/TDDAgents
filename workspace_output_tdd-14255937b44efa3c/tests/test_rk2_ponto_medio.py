import inspect
from typing import Callable

import pytest

from src.rk2_ponto_medio import rk2_ponto_medio

def test_import_rk2_ponto_medio():
    # Verifica se o módulo pode ser importado sem erros
    import src.rk2_ponto_medio  # noqa: F401

def test_rk2_ponto_medio_callable():
    # A função deve ser chamável
    assert callable(rk2_ponto_medio), "rk2_ponto_medio deve ser uma função chamável"

def test_rk2_ponto_medio_signature():
    # Inspeciona a assinatura da função
    sig = inspect.signature(rk2_ponto_medio)
    params = sig.parameters

    # Verifica nomes e ordem dos parâmetros
    expected_names = ['f', 't0', 'y0', 't_final', 'h']
    assert list(params.keys()) == expected_names, \
        f"Parâmetros esperados {expected_names}, encontrados {list(params.keys())}"

    # Verifica anotações de tipo de cada parâmetro
    expected_annotations = {
        'f': Callable[[float, float], float],
        't0': float,
        'y0': float,
        't_final': float,
        'h': float,
    }
    for name, expected_ann in expected_annotations.items():
        ann = params[name].annotation
        assert ann == expected_ann, \
            f"Anotação de '{name}' incorreta: esperada {expected_ann}, encontrada {ann}"

    # Verifica anotação de retorno
    assert sig.return_annotation == float, \
        f"Anotação de retorno incorreta: esperada float, encontrada {sig.return_annotation}"

def test_trivial_case_returns_y0_without_calling_f():
    # Caso trivial: t_final == t0
    t0 = 0.0
    y0 = 1.23
    t_final = t0
    h = 0.5

    # Define f que falha se chamada
    def f_should_not_be_called(t, y):
        raise RuntimeError("f foi chamado no caso trivial")

    result = rk2_ponto_medio(f_should_not_be_called, t0, y0, t_final, h)
    assert result == y0, f"Esperado {y0} mas obteve {result} quando t_final == t0"
