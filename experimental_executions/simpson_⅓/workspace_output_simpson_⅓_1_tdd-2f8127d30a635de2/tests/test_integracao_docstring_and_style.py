import inspect
import pathlib
import pytest
from src.integracao import integracao_simpson_1_3


def test_docstring_sections():
    """
    Garante que a docstring define objetivo, parâmetros, retorno e exceções.
    """
    doc = integracao_simpson_1_3.__doc__
    # Docstring deve existir
    assert doc is not None and doc.strip() != ""
    # Deve conter seções obrigatórias
    assert "Parâmetros:" in doc, "Falta seção 'Parâmetros:' na docstring"
    assert "Retorna:" in doc, "Falta seção 'Retorna:' na docstring"
    assert "Exceções:" in doc, "Falta seção 'Exceções:' na docstring"
    # Primeira linha deve descrever brevemente o objetivo
    first_line = doc.strip().splitlines()[0]
    assert first_line.lower().startswith("aproxima"), (
        f"Linha de objetivo inesperada: {first_line!r}"
    )


def test_function_name_snake_case():
    """
    Garante que o nome da função segue snake_case.
    """
    name = integracao_simpson_1_3.__name__
    assert name == "integracao_simpson_1_3", (
        f"Nome da função deve ser 'integracao_simpson_1_3', mas é {name!r}"
    )


def test_source_line_length():
    """
    Verifica que nenhuma linha em src/integracao.py excede 79 caracteres.
    """
    file_path = pathlib.Path(__file__).parents[1] / "src" / "integracao.py"
    with open(file_path, encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            # Desconsidera newline ao contar
            length = len(line.rstrip("\n"))
            assert length <= 79, (
                f"Linha {idx} com {length} caracteres (máximo 79): {line!r}"
            )


def test_comments_for_numeric_steps():
    """
    Garante que existem comentários mínimos nos passos numéricos críticos.
    """
    file_path = pathlib.Path(__file__).parents[1] / "src" / "integracao.py"
    source = file_path.read_text(encoding="utf-8")
    # Comentário previsto antes do cálculo de h e total
    assert "# Regra de Simpson 1/3 composta" in source, (
        "Comentário '# Regra de Simpson 1/3 composta' não encontrado"
    )
    # Comentário previsto antes da soma dos termos internos
    assert "# Soma dos termos dentro do intervalo" in source, (
        "Comentário '# Soma dos termos dentro do intervalo' não encontrado"
    )
