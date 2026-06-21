import os
import inspect
import pytest
from converter import fahrenheit_to_celsius


def test_type_hints():
    """
    The function must have type hints on its parameter and return a float.
    """
    sig = inspect.signature(fahrenheit_to_celsius)
    # parameter annotation present
    param = sig.parameters['temperature']
    assert param.annotation is not inspect._empty, "Missing type hint for parameter 'temperature'"
    # return annotation is float
    assert sig.return_annotation == float, "Return type hint should be 'float'"


def test_docstring_contains_sections():
    """
    The docstring must describe parameters, return value, and exceptions.
    """
    doc = fahrenheit_to_celsius.__doc__
    # Docstring must exist and be non-empty
    assert doc is not None and doc.strip(), "Missing docstring on fahrenheit_to_celsius"
    # It must document parameters
    assert 'Parâmetros' in doc or 'Parameters' in doc, "Docstring should describe parameters"
    # It must document return value
    assert 'Retorna' in doc or 'Returns' in doc, "Docstring should describe return value"
    # It must document exceptions
    assert 'Exceções' in doc or 'Raises' in doc, "Docstring should document exceptions"


def test_readme_exists_and_has_overview_and_usage():
    """
    There should be a README.md with overview and a usage example in a code block.
    """
    readme_path = os.path.join(os.getcwd(), 'README.md')
    # Check file exists
    assert os.path.isfile(readme_path), "README.md file is missing"
    content = open(readme_path, encoding='utf-8').read()
    lower = content.lower()
    # Overview mentions Fahrenheit and Celsius conversion
    assert 'fahrenheit' in lower and 'celsius' in lower and 'convert' in lower, \
        "README.md should contain project overview mentioning Fahrenheit and Celsius"
    # Should include a Python code block for usage
    assert '```python' in content, "README.md should include a code block for usage example"
    # Should mention the function name in usage
    assert 'fahrenheit_to_celsius' in content, "README.md should include usage of fahrenheit_to_celsius"
    # Should have a Usage or Exemplo section header
    assert 'usage' in lower or 'exemplo' in lower, "README.md should have a Usage or Exemplo section"