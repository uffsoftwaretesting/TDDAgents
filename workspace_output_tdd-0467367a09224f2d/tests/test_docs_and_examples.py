import pytest
import pathlib
import re
from diferencas_finitas_bvp.core import diferencas_finitas_bvp

def test_readme_exists():
    """
    Verifica que o arquivo README.md exista na raiz do projeto.
    """
    project_root = pathlib.Path(__file__).parent.parent
    readme = project_root / "README.md"
    assert readme.exists(), "README.md should exist at project root"


def test_readme_contains_installation_and_usage():
    """
    Verifica que o README.md contenha instruções de instalação e de uso.
    """
    project_root = pathlib.Path(__file__).parent.parent
    readme = project_root / "README.md"
    text = readme.read_text(encoding="utf-8").lower()
    assert "pip install" in text, "README.md should contain installation instructions using 'pip install'"
    assert "usage" in text, "README.md should contain a 'Usage' section"
    assert "```" in readme.read_text(encoding="utf-8"), "README.md should contain code block examples"


def test_core_docstring_sphinx_style():
    """
    Verifica que a docstring de diferencas_finitas_bvp siga o estilo Sphinx:
    - Possua :param <name>: para cada parâmetro
    - :return: ou :returns:
    - :raises ValueError: e :raises RuntimeError:
    - Seção Examples
    """
    doc = diferencas_finitas_bvp.__doc__
    assert doc, "Docstring for diferencas_finitas_bvp should be present"
    # Parâmetros obrigatórios
    params = ["f", "a", "b", "bc", "N", "x_alvo"]
    for p in params:
        pattern = rf":param {p}:"
        assert re.search(pattern, doc), f"Docstring should contain '{pattern}'"
    # Retorno
    assert re.search(r":returns?:", doc), "Docstring should contain ':return:' or ':returns:'"
    # Exceções
    assert re.search(r":raises? ValueError:", doc), "Docstring should document ValueError"
    assert re.search(r":raises? RuntimeError:", doc), "Docstring should document RuntimeError"
    # Seção de exemplos
    assert re.search(r"Examples", doc) or re.search(r"Example", doc), "Docstring should contain an 'Examples' section"