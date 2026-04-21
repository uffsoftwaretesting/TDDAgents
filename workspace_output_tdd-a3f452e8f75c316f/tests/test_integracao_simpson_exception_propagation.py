import pytest
import sys
import builtins
import importlib

from src.integracao_simpson_1_3 import integracao_simpson_1_3

class CustomError(Exception):
    """Exceção customizada para teste de propagação"""
    pass


def test_exception_propagation_with_numpy():
    """
    Quando numpy está disponível, uma exceção em f deve ser propagada sem captura.
    """
    # f que sempre levanta
    def f(x):
        raise CustomError("boom with numpy")

    with pytest.raises(CustomError) as excinfo:
        integracao_simpson_1_3(f, 0, 1, 2)
    assert str(excinfo.value) == "boom with numpy"


def _reload_module():
    """
    Recarrega o módulo para reaplicar hooks de import.
    """
    sys.modules.pop('src.integracao_simpson_1_3', None)
    return importlib.import_module('src.integracao_simpson_1_3')


def test_exception_propagation_pure_python(monkeypatch):
    """
    Quando numpy não está instalado, f levanta erro em x intermediário e deve ser propagado.
    """
    # Simula falha ao importar numpy
    real_import = builtins.__import__
    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'numpy' or name.startswith('numpy.'):
            raise ImportError
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    sys.modules.pop('numpy', None)
    sys.modules.pop('numpy.core', None)

    module = _reload_module()

    # Dados a=0, b=1, N=2 => h = 0.5, iterador único em x=0.5
    def f(x):
        # lança apenas em x intermediário
        if abs(x - 0.5) < 1e-8:
            raise CustomError("boom in pure python")
        return 0.0

    with pytest.raises(CustomError) as excinfo:
        module.integracao_simpson_1_3(f, 0, 1, 2)
    assert str(excinfo.value) == "boom in pure python"