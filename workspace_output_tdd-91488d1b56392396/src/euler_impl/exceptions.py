"""
Módulo de exceções customizadas para o solver de Euler Implícito.
"""

class ConvergenceError(Exception):
    """Exceção lançada quando o solver de Newton-Raphson não converge."""
    def __init__(self, step, t, h_i, criterion):
        # Store provided values
        self.step = step
        self.t = t
        self.h_i = h_i
        self.criterion = criterion
        # Build descriptive message
        message = (
            f"Newton-Raphson did not converge at step {step}, "
            f"t={t}, h_i={h_i}, criterion={criterion}"
        )
        super().__init__(message)
