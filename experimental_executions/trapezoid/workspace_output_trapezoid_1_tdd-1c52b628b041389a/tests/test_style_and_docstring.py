import pytest
import inspect
import pycodestyle
import os
from src.solve import solve


def test_solve_has_docstring():
    """
    Ensure that the solve function has a non-empty docstring.
    """
    doc = inspect.getdoc(solve)
    assert isinstance(doc, str) and doc.strip(), \
        "Function 'solve' must have a non-empty docstring"


def test_docstring_contains_sections():
    """
    Ensure the docstring follows Google-style with 'Args:' and 'Returns:' sections.
    """
    doc = inspect.getdoc(solve)
    assert 'Args:' in doc, "Docstring must include an 'Args:' section"
    assert 'Returns:' in doc, "Docstring must include a 'Returns:' section"


def test_docstring_contains_raises_section():
    """
    Ensure the docstring follows Google-style with 'Raises:' section.
    """
    doc = inspect.getdoc(solve)
    assert 'Raises:' in doc, "Docstring must include a 'Raises:' section"


def test_pep8_conformance():
    """
    Ensure that src/solve.py has no PEP8 violations.
    """
    style = pycodestyle.StyleGuide(quiet=True)
    result = style.check_files(['src/solve.py'])
    assert result.total_errors == 0, \
        f"Found PEP8 style errors in src/solve.py: {result.total_errors}"


def test_no_print_or_logging_in_module():
    """
    Ensure that src/solve.py does not contain print statements or logging usage.
    """
    # Localize o caminho do arquivo src/solve.py
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    src_path = os.path.join(root, 'src', 'solve.py')
    with open(src_path, 'r') as f:
        content = f.read()
    assert 'print(' not in content, "Module 'src/solve' must not contain print statements"
    assert 'import logging' not in content and 'logging.' not in content, \
        "Module 'src/solve' must not contain logging usage"