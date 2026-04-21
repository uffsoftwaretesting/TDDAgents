import sys
import importlib
import builtins
import pytest
import numpy


def _reload_module():
    """
    Remove and re-import the simpson module to pick up current import hooks.
    """
    sys.modules.pop('src.integracao_simpson_1_3', None)
    return importlib.import_module('src.integracao_simpson_1_3')


def test_integracao_with_numpy(monkeypatch):
    """
    Quando numpy está disponível, deve usar numpy e retornar float correto.
    """
    # Garanta que numpy real esteja em sys.modules
    monkeypatch.setitem(sys.modules, 'numpy', numpy)
    monkeypatch.setitem(sys.modules, 'numpy.core', numpy.core)
    module = _reload_module()
    result = module.integracao_simpson_1_3(lambda x: x**2, 0, 1, 2)
    assert isinstance(result, float), "Resultado deve ser float mesmo com numpy presente"
    # A integral de x^2 de 0 a 1 com N=2 é 1/3
    assert abs(result - 1/3) < 1e-6, f"Esperado ~0.333333, obteve {result}"


def test_integracao_without_numpy(monkeypatch):
    """
    Quando numpy não está instalado, deve cair no fallback pure Python e retornar float correto.
    """
    # Simula falha ao importar numpy
    real_import = builtins.__import__
    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'numpy' or name.startswith('numpy.'):
            raise ImportError
        return real_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', fake_import)
    # Remove quaisquer entradas de numpy no cache
    sys.modules.pop('numpy', None)
    sys.modules.pop('numpy.core', None)
    module = _reload_module()
    result = module.integracao_simpson_1_3(lambda x: x**3, 0, 1, 2)
    assert isinstance(result, float), "Resultado deve ser float mesmo sem numpy"
    # A integral de x^3 de 0 a 1 com N=2 é 1/4
    assert abs(result - 1/4) < 1e-6, f"Esperado ~0.25, obteve {result}"