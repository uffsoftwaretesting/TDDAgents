import inspect
import pytest
from src.solve import solve
from typing import Callable, Union


def test_annotations_present():
    """
    Verifies that solve has proper PEP484 type hints for all parameters and return.
    """
    sig = inspect.signature(solve)
    params = sig.parameters
    # Check parameter annotations
    assert 'f' in params
    assert params['f'].annotation == Callable[[float], float]
    assert params['a'].annotation == Union[int, float]
    assert params['b'].annotation == Union[int, float]
    assert params['n'].annotation == int
    # Check return annotation
    assert sig.return_annotation == float


def test_docstring_google_style_sections():
    """
    Ensures the docstring uses Google-style sections: short description, Parameters, Returns, Raises.
    """
    doc = inspect.getdoc(solve)
    assert doc is not None, "Docstring must not be empty"
    # Check for basic Google-style headers
    assert 'Parameters:' in doc, "Docstring missing 'Parameters:' section"
    assert 'Returns:' in doc, "Docstring missing 'Returns:' section"
    assert 'Raises:' in doc, "Docstring missing 'Raises:' section"
    # Ensure each parameter is documented
    for param in ('f:', 'a:', 'b:', 'n:'):
        assert param in doc, f"Docstring missing documentation for parameter '{param}'"


def test_docstring_details_for_exceptions():
    """
    Checks that the Raises section documents TypeError and ValueError.
    """
    doc = inspect.getdoc(solve)
    start = doc.find('Raises:')
    assert start != -1, "Docstring must include a 'Raises:' section"
    raises_section = doc[start:]
    # The docstring should mention the exceptions that solve can raise
    assert 'TypeError' in raises_section, "Raises section must document TypeError"
    assert 'ValueError' in raises_section, "Raises section must document ValueError"