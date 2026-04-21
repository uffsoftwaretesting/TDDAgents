import pytest
from src.solver_euler import euler_explicito


def test_docstring_exists_and_contains_sections():
    """
    The function should have a docstring with the key sections:
    Parameters, Returns, Raises, and Notes.
    """
    doc = euler_explicito.__doc__
    assert doc is not None, "Function euler_explicito should have a docstring"
    required_sections = ["Parameters", "Returns", "Raises", "Notes"]
    for section in required_sections:
        assert section in doc, f"Docstring missing '{section}' section"