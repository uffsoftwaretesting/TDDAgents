import inspect
import pytest
from src.palindrome_checker import normalize_string, is_palindrome

def test_normalize_string_docstring_exists():
    """
    normalize_string should have a non-empty docstring.
    """
    doc = normalize_string.__doc__
    assert isinstance(doc, str) and doc.strip(), "normalize_string should have a docstring"


def test_is_palindrome_docstring_exists():
    """
    is_palindrome should have a non-empty docstring.
    """
    doc = is_palindrome.__doc__
    assert isinstance(doc, str) and doc.strip(), "is_palindrome should have a docstring"


def test_docstring_summary_style():
    """
    Both functions should have a summary line starting with uppercase and ending with a period.
    """
    for func in (normalize_string, is_palindrome):
        doc = inspect.getdoc(func)
        summary = doc.splitlines()[0]
        assert summary[0].isupper(), f"Summary line of {func.__name__} should start with a capital letter"
        assert summary.endswith('.'), f"Summary line of {func.__name__} should end with a period"