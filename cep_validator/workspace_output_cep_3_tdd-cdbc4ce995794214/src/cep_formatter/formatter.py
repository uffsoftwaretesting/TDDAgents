def format_cep(cep):
    """
    Formata CEP brasileiro no padrão XXXXX-XXX.

    :param cep: CEP brasileiro a ser formatado, como str ou int contendo exatamente 8 dígitos.
    :return: string formatada no padrão "XXXXX-XXX".
    :raises TypeError: CEP deve ser string ou inteiro
    :raises ValueError: CEP deve conter apenas dígitos
    :raises ValueError: CEP deve ter exatamente 8 dígitos
    """
    # Validação de tipo: deve ser str ou int
    if not isinstance(cep, (str, int)):
        raise TypeError("CEP deve ser string ou inteiro")

    # Conversão para string
    cep_str = str(cep)

    # Validar que contenha apenas dígitos
    if not cep_str.isdigit():
        raise ValueError("CEP deve conter apenas dígitos")

    # Validar comprimento exato de 8 caracteres
    if len(cep_str) != 8:
        raise ValueError("CEP deve ter exatamente 8 dígitos")

    # Retornar formatado
    return cep_str[:5] + "-" + cep_str[5:]
