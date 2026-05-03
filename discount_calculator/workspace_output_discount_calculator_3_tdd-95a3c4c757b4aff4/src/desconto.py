from decimal import Decimal, ROUND_HALF_UP

def calcular_desconto(preco, percentual):
    """
    Calcula o valor de desconto e o preço final de um produto.

    Parâmetros:
        preco (int|float): preço original do produto, valor deve ser >= 0.
        percentual (int|float): percentual de desconto a ser aplicado, entre 0 e 100.

    Retorna:
        dict: dicionário com as chaves 'desconto' e 'preco_final', ambos float arredondados a duas casas decimais.

    Examples:
        >>> calcular_desconto(100, 10)
        {'desconto': 10.0, 'preco_final': 90.0}
        >>> calcular_desconto(99.99, 33.3333)
        {'desconto': 33.33, 'preco_final': 66.66}
    """
    # Validação de tipo
    if not isinstance(preco, (int, float)) or not isinstance(percentual, (int, float)):
        raise TypeError("preco e percentual devem ser números (int ou float)")
    # Validação de domínio do preco
    if preco < 0:
        raise ValueError("preco não pode ser negativo")
    # Validação de domínio do percentual
    if percentual < 0 or percentual > 100:
        raise ValueError("percentual deve estar entre 0 e 100")
    # Cálculo e arredondamento com Decimal e HALF_UP
    preco_dec = Decimal(str(preco))
    perc_dec = Decimal(str(percentual))
    desconto_dec = (preco_dec * perc_dec / Decimal('100')).quantize(
        Decimal('0.00'), rounding=ROUND_HALF_UP
    )
    preco_final_dec = (preco_dec - desconto_dec).quantize(
        Decimal('0.00'), rounding=ROUND_HALF_UP
    )
    return {"desconto": float(desconto_dec), "preco_final": float(preco_final_dec)}