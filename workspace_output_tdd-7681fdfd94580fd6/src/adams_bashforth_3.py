from typing import Callable


def _validate_args(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> None:
    """
    Valida os argumentos de entrada para o método Adams-Bashforth de 3 passos.

    Parâmetros:
    f: Callable[[float, float], float] - função integranda f(t, y)
    t0: float - tempo inicial
    y0: float - valor inicial y(t0)
    t_final: float - tempo final para integração
    h: float - tamanho do passo (deve ser positivo)

    Levanta:
    TypeError - se tipos estiverem incorretos
    ValueError - se h não for positivo ou t_final < t0
    """
    # f must be callable
    if not callable(f):
        raise TypeError("f must be callable")
    # t0, y0, t_final, h must be floats
    if not isinstance(t0, float):
        raise TypeError("t0 must be a float")
    if not isinstance(y0, float):
        raise TypeError("y0 must be a float")
    if not isinstance(t_final, float):
        raise TypeError("t_final must be a float")
    if not isinstance(h, float):
        raise TypeError("h must be a float")
    # h must be positive
    if h <= 0:
        raise ValueError("Passo h deve ser positivo")
    # t_final must be >= t0
    if t_final < t0:
        raise ValueError("t_final deve ser ≥ t0")


def adams_bashforth_3(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    """
    Integra a EDO y' = f(t, y) de t0 até t_final usando o método explícito de Adams-Bashforth de 3 passos.

    Parâmetros:
    f: Callable[[float, float], float] - função integranda f(t, y)
    t0: float - tempo inicial
    y0: float - valor inicial y(t0)
    t_final: float - tempo final para integração
    h: float - tamanho do passo (positivo)

    Retorna:
    float - valor aproximado de y em t_final

    Levanta exceções de validação e propaga erros de f sem modificação.
    """
    # Validate arguments
    _validate_args(f, t0, y0, t_final, h)
    # No integration needed
    if t_final == t0:
        return y0
    # Compute number of steps and ensure it's integer within tolerance
    delta = t_final - t0
    n_steps_exact = delta / h
    EPS = 1e-12
    n_steps_round = int(round(n_steps_exact))
    if abs(delta - n_steps_round * h) > EPS * h:
        raise ValueError("Número de passos não inteiro")
    n_steps = n_steps_round
    # Handle simple cases
    if n_steps == 0:
        return y0
    if n_steps == 1:
        return y0 + h * f(t0, y0)
    if n_steps == 2:
        t1 = t0 + h
        y1 = y0 + h * f(t0, y0)
        t2 = t1 + h
        y2 = y1 + h * f(t1, y1)
        return y2
    # n_steps >= 3: first two steps via Runge-Kutta 3rd order (Heun 3-stage)
    t_values = [t0]
    y_values = [y0]
    t_prev = t0
    y_prev = y0
    for _ in range(2):
        k1 = f(t_prev, y_prev)
        k2 = f(t_prev + 0.5 * h, y_prev + 0.5 * h * k1)
        k3 = f(t_prev + h, y_prev - h * k1 + 2.0 * h * k2)
        y_next = y_prev + (h / 6.0) * (k1 + 4.0 * k2 + k3)
        t_next = t_prev + h
        t_values.append(t_next)
        y_values.append(y_next)
        t_prev = t_next
        y_prev = y_next
    # Adams-Bashforth 3-step for subsequent steps
    for i in range(2, n_steps):
        t_i2 = t_values[i - 2]
        t_i1 = t_values[i - 1]
        t_i = t_values[i]
        y_i2 = y_values[i - 2]
        y_i1 = y_values[i - 1]
        y_i = y_values[i]
        f_i2 = f(t_i2, y_i2)
        f_i1 = f(t_i1, y_i1)
        f_i = f(t_i, y_i)
        y_new = y_i + (h / 12.0) * (5.0 * f_i2 - 16.0 * f_i1 + 23.0 * f_i)
        t_new = t_i + h
        t_values.append(t_new)
        y_values.append(y_new)
    # Return final approximation
    return y_values[n_steps]