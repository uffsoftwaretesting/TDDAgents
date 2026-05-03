import inspect
import pytest
import collections.abc
from typing import Callable
import rk4_classico as module
from rk4_classico import rk4_classico


def test_type_hints_on_signature():
    """
    Verifica se rk4_classico possui annotations PEP484 corretas:
      - f: Callable[[float, float], float]
      - t0, y0, t_final, h: float
      - retorno: float
    """
    sig = inspect.signature(rk4_classico)
    params = sig.parameters

    # Verifica annotation de f
    assert 'f' in params, "Parâmetro 'f' não encontrado na assinatura"
    f_ann = params['f'].annotation
    origin = getattr(f_ann, '__origin__', None)
    assert origin in (Callable, collections.abc.Callable), \
        f"Annotation de 'f' deve ser Callable, encontrada {f_ann!r}"

    args = getattr(f_ann, '__args__', None)
    assert args is not None, "__args__ não encontrado em annotation de 'f'"
    # Suporta dois formatos de __args__: ([float, float], float) ou (float, float, float)
    if len(args) == 2:
        # estilo antigo: ([float, float], float)
        params_list, ret = args
        assert isinstance(params_list, list), \
            f"Esperado lista de parâmetros, encontrado {params_list!r}"
        assert params_list == [float, float], \
            f"Parametros devem ser [float, float], encontrados {params_list!r}"
        assert ret is float, \
            f"Tipo de retorno deve ser float, encontrado {ret!r}"
    elif len(args) == 3:
        # estilo Python 3.13+: (float, float, float)
        a, b, ret = args
        assert a is float and b is float and ret is float, \
            f"Esperado (float, float, float), encontrado {args!r}"
    else:
        pytest.fail(f"Formato inesperado de __args__ para 'f': {args!r}")

    # Verifica os floats
    for name in ('t0', 'y0', 't_final', 'h'):
        assert name in params, f"Parâmetro '{name}' não encontrado na assinatura"
        ann = params[name].annotation
        assert ann is float, \
            f"Annotation de '{name}' deve ser float, encontrada {ann!r}"

    # Verifica annotation de retorno
    ret_ann = sig.return_annotation
    assert ret_ann is float, f"Annotation de retorno deve ser float, encontrada {ret_ann!r}"


def test_docstring_contains_sections():
    """
    Verifica se existe docstring e se contém as seções:
      - Parameters
      - Returns
      - Raises
    """
    doc = rk4_classico.__doc__
    assert doc and isinstance(doc, str), "Docstring não encontrada ou vazia"
    for section in ('Parameters', 'Returns', 'Raises'):
        assert section in doc, f"Seção '{section}' não encontrada na docstring"


def test_no_prints_or_logging_in_source():
    """
    Assegura que não há chamadas a print() nem import de logging,
    pois não deve haver efeitos colaterais ou logs.
    """
    source = inspect.getsource(module)
    assert 'print(' not in source, "Encontrado 'print(' no código-fonte"
    assert 'logging' not in source, "Encontrado 'logging' no código-fonte"