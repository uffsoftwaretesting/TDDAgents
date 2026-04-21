import sys
import builtins
import importlib
import math
import pytest

def _reload_module():
    """
    Remove and re-import the simpson module to pick up current import hooks.
    """
    sys.modules.pop('src.integracao_simpson_1_3', None)
    return importlib.import_module('src.integracao_simpson_1_3')

@pytest.mark.parametrize("func, a, b, N, expected", [
    (lambda x: x**2, 0, 1, 2, 1/3),
    (math.sin, 0, math.pi, 100, 2.0),
])
def test_precision_pure_python(monkeypatch, func, a, b, N, expected):
    # Simula falha ao importar numpy para usar fallback pure Python
    real_import = builtins.__import__
    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'numpy' or name.startswith('numpy.'):
            raise ImportError
        return real_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', fake_import)
    sys.modules.pop('numpy', None)
    sys.modules.pop('numpy.core', None)

    module = _reload_module()
    result = module.integracao_simpson_1_3(func, a, b, N)
    assert isinstance(result, float), "Resultado deve ser float mesmo sem numpy"
    assert abs(result - expected) < 1e-6, f"Esperado ~{expected}, obteve {result}"
