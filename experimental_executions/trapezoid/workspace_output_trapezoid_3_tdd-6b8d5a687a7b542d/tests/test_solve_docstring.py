import inspect
import pytest
from src.solve import solve

def test_docstring_exists():
    """
    The solve function must have a non-empty docstring.
    """
    doc = solve.__doc__
    assert doc is not None, "solve must have a docstring"
    assert doc.strip(), "solve docstring must not be empty"


def test_docstring_summary_line():
    """
    The first line of the docstring should be a summary ending with a period and start with a capital letter.
    """
    doc = inspect.getdoc(solve)
    first_line = doc.strip().splitlines()[0]
    assert first_line[0].isupper(), "Docstring summary should start with a capital letter"
    assert first_line.endswith('.'), "Docstring summary should end with a period"


def test_docstring_contains_sections():
    """
    The docstring must include Parameters, Returns, and Raises sections.
    """
    doc = inspect.getdoc(solve)
    assert 'Parameters:' in doc, "Docstring must contain a 'Parameters:' section"
    assert 'Returns:' in doc, "Docstring must contain a 'Returns:' section"
    assert 'Raises:' in doc, "Docstring must contain a 'Raises:' section"


def test_docstring_parameters_listed():
    """
    Each parameter (f, a, b, n) must be documented under the Parameters section.
    """
    doc = inspect.getdoc(solve)
    # Extract the part after 'Parameters:'
    parts = doc.split('Parameters:')
    assert len(parts) == 2, "Parameters section format incorrect"
    params_block = parts[1]
    for param in ['f:', 'a:', 'b:', 'n:']:
        assert param in params_block, f"Parameter '{param}' must be documented in the docstring"