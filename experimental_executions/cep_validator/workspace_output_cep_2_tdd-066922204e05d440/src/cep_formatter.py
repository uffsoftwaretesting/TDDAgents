"""
Módulo para formatação de CEP de acordo com padrão brasileiro "XXXXX-XXX".
"""

def format_cep(cep_input) -> str:
    """
    Recebe um CEP como str ou int e retorna no formato "XXXXX-XXX".

    Exceções:
    - TypeError: quando o tipo não for str nem int.
    - ValueError: quando contiver caracteres não numéricos ou tamanho diferente de 8.
    """
    # Validação de tipo
    if not isinstance(cep_input, (str, int)):
        raise TypeError("Tipo inválido: esperado str ou int.")

    # Conversão para string e padding para int
    if isinstance(cep_input, int):
        cep_str = str(cep_input).zfill(8)
    else:
        cep_str = cep_input

    # Validação de conteúdo numérico
    if not cep_str.isdigit():
        raise ValueError("CEP deve conter apenas dígitos.")

    # Validação de tamanho
    if len(cep_str) != 8:
        raise ValueError("CEP deve ter exatamente 8 dígitos.")

    # Formatação
    return cep_str[:5] + "-" + cep_str[5:]