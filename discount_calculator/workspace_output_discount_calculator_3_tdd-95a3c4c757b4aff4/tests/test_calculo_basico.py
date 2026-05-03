import pytest

from src.desconto import calcular_desconto

@ pytest.mark.parametrize("preco, percentual, desconto_esperado, preco_final_esperado", [
    (200,    12.5, 25.00, 175.00),   # int preco, float percentual
    (99.99,  50,   50.00, 49.99),     # float preco, int percentual (arredondamento de 49.995)
])
def test_calculo_basico_adicional(preco, percentual, desconto_esperado, preco_final_esperado):
    """
    Testes adicionais de casos básicos válidos para garantir a implementação correta
    da lógica de cálculo e arredondamento a duas casas decimais.
    """
    result = calcular_desconto(preco, percentual)
    # Verifica estrutura do retorno
    assert isinstance(result, dict)
    assert set(result.keys()) == {"desconto", "preco_final"}
    # Verifica valores arredondados conforme especificação
    assert result["desconto"] == desconto_esperado
    assert result["preco_final"] == preco_final_esperado
