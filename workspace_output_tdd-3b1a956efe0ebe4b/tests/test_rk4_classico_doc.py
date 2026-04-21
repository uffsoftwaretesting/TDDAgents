import inspect
import pytest
from typing import Callable
from src.rk4_classico import rk4_classico

def test_docstring_pep257_format():
    # Check that docstring exists and has a proper summary and blank line
    doc = rk4_classico.__doc__
    assert doc is not None, "Docstring is missing"
    lines = doc.splitlines()
    assert lines, "Docstring is empty"
    # First line summary
    assert lines[0].strip() == \
        "Método clássico de Runge-Kutta de 4ª ordem para equações diferenciais ordinárias escalares."
    # Second line must be blank according to PEP 257
    assert lines[1].strip() == ""


def test_docstring_sections():
    # Check presence of required sections in docstring
    doc = rk4_classico.__doc__
    for section in ("Parameters", "Returns", "Raises", "Notes"):
        assert section in doc, f"Section '{section}' not found in docstring"


def test_type_hints_of_signature():
    # Inspect signature to ensure correct type annotations
    sig = inspect.signature(rk4_classico)
    # Check annotation of f
    assert sig.parameters['f'].annotation == Callable[[float, float], float]
    # Check annotations of scalar parameters
    for param in ('t0', 'y0', 't_final', 'h'):
        assert sig.parameters[param].annotation == float, \
            f"Parameter '{param}' should be annotated as float"
    # Check return annotation
    assert sig.return_annotation == float, "Return type should be annotated as float"