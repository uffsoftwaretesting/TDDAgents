from application.ports.icpf_validator import ICpfValidator


class ValidateCpfUseCase:
    """
    Caso de uso para validação de CPF.
    Injeta uma implementação de ICpfValidator e expõe o método execute.
    """
    def __init__(self, validator: ICpfValidator) -> None:
        self.validator = validator

    def execute(self, cpf: str) -> bool:
        try:
            return self.validator.validate(cpf)
        except Exception:
            return False
