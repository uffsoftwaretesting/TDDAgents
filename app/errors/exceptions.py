"""
Exceções customizadas para controle de fluxo do TDD Agents.
"""

class TDDWorkflowError(Exception):
    """Classe base para erros do pipeline TDD."""
    pass

class TransientInfraError(TDDWorkflowError):
    """
    Erros temporários (Rede, Rate Limit, Timeout).
    Devem engatilhar retentativas (retries) até o limite configurado.
    """
    def __init__(self, message: str, original_exc: Exception = None):
        super().__init__(message)
        self.original_exc = original_exc

class FatalInfraError(TDDWorkflowError):
    """
    Erros destrutivos (Falta de permissão, Sandbox expirada, Disco cheio, Context Window).
    Devem abortar o grafo imediatamente (END).
    """
    def __init__(self, message: str, original_exc: Exception = None):
        super().__init__(message)
        self.original_exc = original_exc
    