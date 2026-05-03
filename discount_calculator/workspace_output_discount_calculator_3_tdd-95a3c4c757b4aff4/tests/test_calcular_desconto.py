import pytest

from src.desconto import calcular_desconto


def test_desconto_basico():
    result = calcular_desconto(100, 10)
    assert isinstance(result, dict)
    assert result["desconto"] == 10.00
    assert result["preco_final"] == 90.00


def test_desconto_basico_float():
    result = calcular_desconto(250.5, 20)
    assert result["desconto"] == 50.10
    assert result["preco_final"] == 200.40


def test_desconto_percentual_zero():
    preco = 123.45
    result = calcular_desconto(preco, 0)
    assert result["desconto"] == 0.00
    assert result["preco_final"] == round(preco, 2)


def test_desconto_percentual_cem():
    preco = 75
    result = calcular_desconto(preco, 100)
    assert result["desconto"] == round(preco, 2)
    assert result["preco_final"] == 0.00


def test_desconto_precisao_numerica():
    result = calcular_desconto(99.99, 33.3333)
    expected_desconto = round(99.99 * (33.3333 / 100), 2)
    expected_preco_final = round(99.99 - expected_desconto, 2)
    assert result["desconto"] == expected_desconto
    assert result["preco_final"] == expected_preco_final

@pytest.mark.parametrize("preco", ["100", None, [10]])
def test_preco_tipo_invalido(preco):
    with pytest.raises(TypeError) as excinfo:
        calcular_desconto(preco, 10)
    assert "preco e percentual devem ser números" in str(excinfo.value)

@pytest.mark.parametrize("percentual", [[10], "10", None])
def test_percentual_tipo_invalido(percentual):
    with pytest.raises(TypeError) as excinfo:
        calcular_desconto(100, percentual)
    assert "preco e percentual devem ser números" in str(excinfo.value)

@pytest.mark.parametrize("preco", [-1, -0.01])
def test_preco_valor_negativo(preco):
    with pytest.raises(ValueError) as excinfo:
        calcular_desconto(preco, 10)
    assert "preco não pode ser negativo" in str(excinfo.value)

@pytest.mark.parametrize("percentual", [-5, 150])
def test_percentual_valor_fora_intervalo(percentual):
    with pytest.raises(ValueError) as excinfo:
        calcular_desconto(100, percentual)
    assert "percentual deve estar entre 0 e 100" in str(excinfo.value)

# Teste de contrato: verifica apenas a estrutura do retorno (esqueleto e contrato)
def test_contrato_retorno_dict_com_chaves():
    """
    Verifica que calcular_desconto retorna um dict contendo as chaves 'desconto' e 'preco_final'.
    Não valida valores específicos, apenas o formato.
    """
    result = calcular_desconto(50, 5)
    assert isinstance(result, dict)
    assert set(result.keys()) == {"desconto", "preco_final"}

# Novos testes para validar a mensagem exata do TypeError conforme especificação técnica
@pytest.mark.parametrize("preco, percentual", [
    ("100", 10),
    (100, "10")
])
def test_mensagem_tipo_padronizada_exata(preco, percentual):
    """
    Garante que, para tipos inválidos de preco ou percentual, a mensagem do TypeError
    seja exatamente 'preco e percentual devem ser números (int ou float)'.
    """
    with pytest.raises(TypeError) as excinfo:
        calcular_desconto(preco, percentual)
    assert str(excinfo.value) == "preco e percentual devem ser números (int ou float)"

# Novos testes para validar a mensagem exata do ValueError nas validações de domínio
def test_mensagem_preco_valor_negativo_exata():
    """
    Garante que, para preco negativo, a mensagem do ValueError
    seja exatamente 'preco não pode ser negativo'.
    """
    with pytest.raises(ValueError) as excinfo:
        calcular_desconto(-1, 10)
    assert str(excinfo.value) == "preco não pode ser negativo"

@pytest.mark.parametrize("percentual", [-5, 150])
def test_mensagem_percentual_fora_intervalo_exata(percentual):
    """
    Garante que, para percentual fora de [0,100], a mensagem do ValueError
    seja exatamente 'percentual deve estar entre 0 e 100'.
    """
    with pytest.raises(ValueError) as excinfo:
        calcular_desconto(100, percentual)
    assert str(excinfo.value) == "percentual deve estar entre 0 e 100"

# Novo teste de domínio: preco = 0 deve resultar em desconto e preço final zero
def test_preco_zero_retorna_zero_desconto_e_preco_final():
    """
    Verifica que, para preco = 0 e percentual qualquer dentro do intervalo,
    o desconto e o preço final sejam 0.00.
    """
    result = calcular_desconto(0, 50)
    assert result["desconto"] == 0.00
    assert result["preco_final"] == 0.00

# Testes básicos parametrizados conforme sub-requisito atual
@pytest.mark.parametrize("preco, percentual, desconto_esperado, preco_final_esperado", [
    (100, 10, 10.00, 90.00),
    (250.5, 20, 50.10, 200.40),
])
def test_casos_basicos_parametrizados(preco, percentual, desconto_esperado, preco_final_esperado):
    """
    Testa casos básicos válidos parametrizados, verificando
    o tipo do retorno, chaves e valores arredondados.
    """
    result = calcular_desconto(preco, percentual)
    assert isinstance(result, dict)
    assert set(result.keys()) == {"desconto", "preco_final"}
    assert result["desconto"] == desconto_esperado
    assert result["preco_final"] == preco_final_esperado
