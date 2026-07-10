"""
Placeholder test for Phase 0 setup.
"""
import pytest
from roman_converter.converter import roman_to_int

def test_placeholder():
    """A basic sanity check ensuring test suite runs."""
    assert True

def test_roman_to_int_import():
    """Test that roman_to_int function is importable and callable."""
    assert callable(roman_to_int)
