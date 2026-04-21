"""
Módulo interno: implementação do solver de Newton-Raphson.
"""
from typing import Callable
from .exceptions import ConvergenceError

def _newton_solver(
    phi: Callable[[float], float],
    phi_prime: Callable[[float], float],
    y_init: float,
    tol: float,
    max_iter: int
) -> float:
    """
    Newton-Raphson solver to find a root of phi(y) = 0.

    Parameters
    ----------
    phi : Callable[[float], float]
        Function whose root is sought.
    phi_prime : Callable[[float], float]
        Derivative of phi.
    y_init : float
        Initial guess for the root.
    tol : float
        Convergence tolerance.
    max_iter : int
        Maximum number of iterations.

    Returns
    -------
    float
        Approximated root of phi.

    Raises
    ------
    ConvergenceError
        If the method fails to converge or derivative is zero.
    """
    y_old = y_init
    for iteration in range(max_iter):
        phi_val = phi(y_old)
        deriv = phi_prime(y_old)
        # Avoid division by zero
        if deriv == 0:
            raise ConvergenceError(iteration + 1, None, None, tol)
        y_new = y_old - phi_val / deriv
        if abs(y_new - y_old) < tol:
            return y_new
        y_old = y_new
    # Did not converge within max_iter
    raise ConvergenceError(max_iter, None, None, tol)
