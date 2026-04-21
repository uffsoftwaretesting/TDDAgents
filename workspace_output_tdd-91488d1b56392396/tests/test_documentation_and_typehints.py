import inspect
import pytest
from typing import Callable, List, get_type_hints

from src.euler_impl.euler_implicit import euler_implicito
from src.euler_impl.newton_solver import _newton_solver


def test_euler_implicito_docstring_structure():
    """
    Verify that euler_implicito has a docstring with Parameters, Returns, and Raises sections.
    """
    doc = inspect.getdoc(euler_implicito)
    assert doc is not None, "euler_implicito should have a docstring"
    # Check for standard sections
    assert 'Parameters' in doc, "Docstring must include a 'Parameters' section"
    assert 'Returns' in doc, "Docstring must include a 'Returns' section"
    assert 'Raises' in doc, "Docstring must include a 'Raises' section"


def test_euler_implicito_type_hints():
    """
    Ensure euler_implicito has the correct type hints for all parameters and return.
    """
    hints = get_type_hints(euler_implicito)
    expected_keys = {'func', 't0', 'y0', 't_final', 'h', 'tol', 'max_iter', 'return'}
    assert set(hints.keys()) == expected_keys, f"Expected type hints keys {expected_keys}, got {set(hints.keys())}"
    # Check individual annotations
    assert hints['func'] == Callable[[float, float], float]
    assert hints['t0'] == float
    assert hints['y0'] == float
    assert hints['t_final'] == float
    assert hints['h'] == float
    assert hints['tol'] == float
    assert hints['max_iter'] == int
    assert hints['return'] == List[float]


def test_newton_solver_docstring_structure():
    """
    Verify that _newton_solver has a docstring with Parameters, Returns, and Raises sections.
    """
    doc = inspect.getdoc(_newton_solver)
    assert doc is not None, "_newton_solver should have a docstring"
    assert 'Parameters' in doc, "Docstring must include a 'Parameters' section"
    assert 'Returns' in doc, "Docstring must include a 'Returns' section"
    assert 'Raises' in doc, "Docstring must include a 'Raises' section"


def test_newton_solver_type_hints():
    """
    Ensure _newton_solver has the correct type hints for all parameters and return.
    """
    hints = get_type_hints(_newton_solver)
    expected_keys = {'phi', 'phi_prime', 'y_init', 'tol', 'max_iter', 'return'}
    assert set(hints.keys()) == expected_keys, f"Expected type hints keys {expected_keys}, got {set(hints.keys())}"
    assert hints['phi'] == Callable[[float], float]
    assert hints['phi_prime'] == Callable[[float], float]
    assert hints['y_init'] == float
    assert hints['tol'] == float
    assert hints['max_iter'] == int
    assert hints['return'] == float
