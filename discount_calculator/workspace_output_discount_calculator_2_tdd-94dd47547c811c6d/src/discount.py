def calculate_discount(price, discount_percentage):
    """
    Calcula o valor do desconto e o preço final após aplicação do desconto.

    Args:
        price (int | float): preço original do produto
        discount_percentage (int | float): percentual de desconto a ser aplicado

    Returns:
        dict: com chaves "discount_amount" e "final_price", ambos floats arredondados em duas casas decimais
    """
    if not isinstance(price, (int, float)):
        raise TypeError("price must be an int or float")
    if not isinstance(discount_percentage, (int, float)):
        raise TypeError("discount_percentage must be an int or float")
    if price < 0:
        raise ValueError("price must be non-negative")
    if discount_percentage < 0 or discount_percentage > 100:
        raise ValueError("discount_percentage must be between 0 and 100")

    discount_amount = round(price * (discount_percentage / 100), 2)
    final_price = round(price - discount_amount, 2)
    return {"discount_amount": discount_amount, "final_price": final_price}