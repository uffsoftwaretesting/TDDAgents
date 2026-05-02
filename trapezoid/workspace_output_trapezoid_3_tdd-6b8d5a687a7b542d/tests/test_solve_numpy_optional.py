import time
import numpy as np
import pytest
from src.solve import solve

def test_uses_numpy_linspace(monkeypatch):
    """
    The solve function should use numpy.linspace for generating the mesh points
    when numpy is available. We monkeypatch numpy.linspace to detect its usage.
    """
    called = {'flag': False}

    original_linspace = np.linspace

    def fake_linspace(a, b, num):
        called['flag'] = True
        # Delegate to the real implementation so downstream logic can continue
        return original_linspace(a, b, num)

    monkeypatch.setattr(np, 'linspace', fake_linspace)
    # Use a simple function to trigger mesh generation
    result = solve(lambda x: x, 0.0, 1.0, 10)
    assert called['flag'], "solve should call numpy.linspace internally"
    # Also check correctness is maintained
    assert result == pytest.approx(0.5)
