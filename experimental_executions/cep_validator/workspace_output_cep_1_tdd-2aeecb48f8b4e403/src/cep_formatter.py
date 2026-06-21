"""
Módulo para formatação de CEP.
"""

def format_cep(cep):
    """Normaliza e formata CEPs brasileiros no padrão XXXXX-XXX."""
    # Validação de tipo
    if not isinstance(cep, (str, int)):
        raise TypeError("CEP deve ser uma string ou inteiro")
    # Conversão para string
    cep_str = str(cep)
    # Verificar se contém apenas dígitos
    if not cep_str.isdigit():
        raise ValueError("CEP deve conter apenas dígitos")
    # Verificar tamanho exato de 8 dígitos
    if len(cep_str) != 8:
        raise ValueError("CEP deve ter exatamente 8 dígitos")
    # Formatar inserindo hífen após o quinto dígito
    return cep_str[:5] + "-" + cep_str[5:]