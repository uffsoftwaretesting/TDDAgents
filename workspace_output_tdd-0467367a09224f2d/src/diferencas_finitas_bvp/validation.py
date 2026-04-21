"""
Módulo de validações de parâmetros e domínio.
"""
import numpy as np
from typing import Callable, Dict

def _validate_inputs(*args, **kwargs) -> None:
    """
    Valida os parâmetros de entrada.
    """
    # Only accept exactly 6 positional arguments and no keyword arguments
    if kwargs or len(args) != 6:
        raise NotImplementedError
    f, a, b, bc, N, x_alvo = args
    # f must be callable
    if not callable(f):
        raise ValueError("f must be callable")
    # a and b must be floats
    if not isinstance(a, float) or not isinstance(b, float):
        raise ValueError("a and b must be float")
    # a < b
    if not a < b:
        raise ValueError("a must be less than b")
    # bc must be dict with 'u_a' and 'u_b'
    if not isinstance(bc, dict):
        raise ValueError("bc must be dict")
    if 'u_a' not in bc or 'u_b' not in bc:
        raise ValueError("bc must contain 'u_a' and 'u_b'")
    u_a = bc['u_a']
    u_b = bc['u_b']
    if not isinstance(u_a, float) or not isinstance(u_b, float):
        raise ValueError("bc values must be float")
    # N must be int >= 1
    if not isinstance(N, int) or N < 1:
        raise ValueError("N must be int >= 1")
    # x_alvo must be float within [a, b]
    if not isinstance(x_alvo, float):
        raise ValueError("x_alvo must be float")
    if x_alvo < a or x_alvo > b:
        raise ValueError("x_alvo must be within [a, b]")
    # Test that f returns numpy.ndarray of correct shape
    x_test = np.array([a, b], dtype=float)
    try:
        y = f(x_test)
    except Exception:
        raise ValueError("f must accept numpy.ndarray input and return numpy.ndarray")
    if not isinstance(y, np.ndarray):
        raise ValueError("f must return numpy.ndarray")
    if y.shape != x_test.shape:
        raise ValueError("f must return array of same shape as input")
