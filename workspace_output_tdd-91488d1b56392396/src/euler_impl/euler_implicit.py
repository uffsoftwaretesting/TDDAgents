"""
Módulo principal: implementação com validação de parâmetros e Euler Implícito via Newton solver.
"""
from typing import Callable, List
import math
from .newton_solver import _newton_solver

def euler_implicito(
    func: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float,
    tol: float = 1e-8,
    max_iter: int = 50
) -> List[float]:
    """
    Euler Implicit solver for ODE initial value problems.

    Parameters
    ----------
    func : Callable[[float, float], float]
        Function defining the ODE dy/dt = f(t, y).
    t0 : float
        Initial time.
    y0 : float
        Initial value y(t0).
    t_final : float
        Final time of integration.
    h : float
        Step size.
    tol : float, optional
        Tolerance for Newton-Raphson solver (default 1e-8).
    max_iter : int, optional
        Maximum iterations for Newton-Raphson solver (default 50).

    Returns
    -------
    List[float]
        Values of y at each time step, including initial and final.

    Raises
    ------
    ValueError
        If input parameters have invalid types or values.
    ConvergenceError
        If Newton-Raphson method fails to converge within max_iter.
    """
    # Type checks
    if not callable(func):
        raise ValueError("func must be a callable accepting (t, y) and returning float")
    if not isinstance(t0, float):
        raise ValueError("t0 must be a float")
    if not isinstance(y0, float):
        raise ValueError("y0 must be a float")
    if not isinstance(t_final, float):
        raise ValueError("t_final must be a float")
    if not isinstance(h, float):
        raise ValueError("h must be a float")
    if not isinstance(tol, float):
        raise ValueError("tol must be a float")
    if not isinstance(max_iter, int):
        raise ValueError("max_iter must be an int")
    # Domain checks
    if h <= 0:
        raise ValueError("h must be greater than 0")
    if tol <= 0:
        raise ValueError("tol must be greater than 0")
    if max_iter < 1:
        raise ValueError("max_iter must be at least 1")
    if t_final <= t0:
        raise ValueError("t_final must be greater than t0")

    n_steps = math.ceil((t_final - t0) / h)
    ys: List[float] = [y0]
    t_current = t0
    y_current = y0

    for step in range(n_steps):
        # Determine step size
        if step < n_steps - 1:
            h_i = h
        else:
            h_i = t_final - t_current
        # Next time point
        t_next = t_current + h_i
        # Define phi and phi_prime for Newton solver (implicit Euler)
        def phi(y: float) -> float:
            return y - y_current - h_i * func(t_next, y)

        def phi_prime(y: float) -> float:
            eps = 1e-6
            return (phi(y + eps) - phi(y - eps)) / (2 * eps)

        # Solve for next y
        y_next = _newton_solver(phi, phi_prime, y_current, tol, max_iter)
        # Update lists and current values
        ys.append(y_next)
        t_current = t_next
        y_current = y_next

    return ys
