import re
import pytest
from taylor_2.taylor_2 import taylor_2

def test_docstring_exists_and_not_empty():
    doc = taylor_2.__doc__
    assert doc, "taylor_2 must have a docstring"

@pytest.mark.parametrize("section", [
    "Parameters",
    "Returns",
    "Raises",
    "Example",
    "Examples",
])
def test_docstring_has_sections(section):
    doc = taylor_2.__doc__
    # Look for section headings (NumPy or Google style)
    patterns = [
        rf"^{section}\s*:\s*$",      # Google style
        rf"^{section}\s*\n[-=]+",   # NumPy style
    ]
    assert any(re.search(p, doc, re.MULTILINE) for p in patterns), \
        f"Docstring must include a '{section}' section"
