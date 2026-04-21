import inspect
from interpolacao_lagrange import interpolacao_lagrange


def test_docstring_exists():
    """
    Garante que a função possui docstring.
    """
    doc = inspect.getdoc(interpolacao_lagrange)
    assert doc, "Docstring is missing for interpolacao_lagrange"


def test_docstring_summary_line():
    """
    Garante que o primeiro parágrafo contém a descrição resumida da função.
    """
    doc = inspect.getdoc(interpolacao_lagrange)
    first_line = doc.splitlines()[0]
    expected_start = "Interpolação via polinômio de Lagrange"
    assert first_line.startswith(expected_start), (
        f"Docstring summary should start with '{expected_start}', "
        f"got '{first_line}'"
    )


def test_docstring_sections():
    """
    Garante que as seções Parâmetros, Retorna, Exceções e
    Complexidade estão documentadas.
    """
    doc = inspect.getdoc(interpolacao_lagrange)
    sections = [
        "Parâmetros:",
        "Retorna:",
        "Exceções:",
        "Complexidade:",
    ]
    for section in sections:
        assert section in doc, f"Docstring section '{section}' not found"


def test_docstring_complexity_notation():
    """
    Garante que a complexidade O(n²) está especificada.
    """
    doc = inspect.getdoc(interpolacao_lagrange)
    assert "O(n²)" in doc or "O(n^2)" in doc, (
        "Complexidade Big-O O(n²) not documented"
    )
