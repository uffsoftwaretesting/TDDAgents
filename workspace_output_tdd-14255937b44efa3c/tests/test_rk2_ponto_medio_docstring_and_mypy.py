import inspect
import subprocess
import sys

import pytest

from src.rk2_ponto_medio import rk2_ponto_medio

def test_docstring_present_and_style():
    """
    Garante que a função rk2_ponto_medio tenha uma docstring
    e que siga o estilo NumPy ou Google.
    """
    doc = inspect.getdoc(rk2_ponto_medio)
    assert doc is not None, "Docstring ausente em rk2_ponto_medio"

    # Verifica se segue estilo NumPy (Parameters / Returns) ou Google (Args: / Returns:)
    has_numpy = "Parameters" in doc and "Returns" in doc
    has_google = "Args:" in doc and "Returns:" in doc
    assert has_numpy or has_google, (
        "Docstring deve seguir o estilo NumPy (Parameters/Returns) ou Google (Args:/Returns:), "
        "mas seções não encontradas"
    )

def test_mypy_no_errors():
    """
    Executa mypy no módulo e assegura que não haja erros de tipagem.
    """
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "src/rk2_ponto_medio.py"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, (
        "Mypy encontrou problemas de tipagem:\n"
        f"{result.stdout}\n{result.stderr}"
    )
