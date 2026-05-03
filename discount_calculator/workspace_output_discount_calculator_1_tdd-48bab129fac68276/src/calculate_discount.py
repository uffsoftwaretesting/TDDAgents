from decimal import Decimal, ROUND_HALF_UP

def calculate_discount(price, discount_percent):
    """
    Calcula o valor de desconto e o preço final de um produto dado preço original e porcentagem de desconto.
    Retorna um dicionário com chaves 'discount' e 'final_price', ambos arredondados a duas casas decimais usando ROUND_HALF_UP.
    """
    # Tipo: price deve ser int ou float
    if not isinstance(price, (int, float)):
        raise TypeError("price must be an int or float")
    # Tipo: discount_percent deve ser int ou float
    if not isinstance(discount_percent, (int, float)):
        raise TypeError("discount_percent must be an int or float")
    # Domínio: price não negativo
    if price < 0:
        raise ValueError("price must be non-negative")
    # Domínio: discount_percent entre 0 e 100
    if discount_percent < 0 or discount_percent > 100:
        raise ValueError("discount_percent must be between 0 and 100")
    # Conversão para Decimal para cálculo preciso
    price_dec = Decimal(str(price))
    percent_dec = Decimal(str(discount_percent))
    # Cálculo bruto do desconto e preço final
    raw_discount = price_dec * (percent_dec / Decimal('100'))
    raw_final_price = price_dec - raw_discount
    # Arredondamento usando ROUND_HALF_UP a duas casas decimais
    discount_dec = raw_discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    final_price_dec = raw_final_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    # Retorna valores como float
    return {
        'discount': float(discount_dec),
        'final_price': float(final_price_dec)
    }