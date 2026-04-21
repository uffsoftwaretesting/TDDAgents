import sys

def test_python_version():
    assert sys.version_info >= (3, 8), f"Python 3.8+ required, found {sys.version_info}"

def test_typing_import():
    import typing  # noqa: F401

def test_math_import():
    import math  # noqa: F401

def test_pytest_import():
    import pytest  # noqa: F401
