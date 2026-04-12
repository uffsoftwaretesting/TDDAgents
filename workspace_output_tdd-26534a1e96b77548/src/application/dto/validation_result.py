class ValidationResult:
    """
    DTO representing the result of a CPF validation.

    Attributes:
        cpf_original (str): The original CPF input (trimmed).
        cpf_formatado (str): The formatted CPF (XXX.XXX.XXX-XX) or empty string if not applicable.
        valid (bool): Whether the CPF is valid or not.
    """
    def __init__(self, cpf_original: str, cpf_formatado: str, valid: bool):
        self.cpf_original = cpf_original
        self.cpf_formatado = cpf_formatado
        self.valid = valid
