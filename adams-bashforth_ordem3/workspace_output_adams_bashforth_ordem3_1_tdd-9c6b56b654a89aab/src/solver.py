"""Solver de EDO via Adams–Bashforth de 3ª ordem (implementação mínima para validações)."""
from typing import Callable, List, Tuple

# Tipo para função EDO: f(t, y) -> dy/dt
ODEFunction = Callable[[float, float], float]

def adams_bashforth_3(f: ODEFunction, t0: float, y0: float, t_final: float, h: float) -> float:
    """
    def adams_bashforth_3(f: ODEFunction, t0: float, y0: float, t_final: float, h: float) -> float

    Resolve um problema de valor inicial para EDO de primeira ordem usando Adams–Bashforth de 3ª ordem.

    O método inicializa as duas primeiras etapas com Runge–Kutta de 4ª ordem (RK4) e, em seguida,
    aplica a fórmula de Adams–Bashforth de 3ª ordem:
        y_next = y_n + dt/12 * (23 * f_n - 16 * f_n1 + 5 * f_n2)

    Parâmetros:
        f: função que recebe (t, y) e retorna dy/dt
        t0: instante inicial
        y0: valor inicial em t0
        t_final: instante final
        h: passo de integração

    Retorna:
        Estimativa de y(t_final)
    """
    # Validações iniciais
    if not callable(f):
        raise TypeError("f deve ser chamável")
    if not isinstance(t0, float):
        raise TypeError("t0 deve ser float")
    if not isinstance(y0, float):
        raise TypeError("y0 deve ser float")
    if not isinstance(t_final, float):
        raise TypeError("t_final deve ser float")
    if not isinstance(h, float):
        raise TypeError("h deve ser float")
    if h <= 0:
        raise ValueError("Passo h deve ser > 0")
    if t_final < t0:
        raise ValueError("t_final deve ser ≥ t0")

    # Função segura para avaliar f e verificar tipo de retorno
    def safe_f(t: float, y: float) -> float:
        val = f(t, y)
        if not isinstance(val, float):
            raise TypeError("Retorno de f deve ser float")
        return val

    dt_total = t_final - t0
    # Se o passo total for menor que h, usar um único passo RK4
    if dt_total < h:
        dt = dt_total
        k1 = safe_f(t0, y0)
        k2 = safe_f(t0 + dt/2, y0 + dt/2 * k1)
        k3 = safe_f(t0 + dt/2, y0 + dt/2 * k2)
        k4 = safe_f(t0 + dt, y0 + dt * k3)
        return y0 + dt/6 * (k1 + 2*k2 + 2*k3 + k4)

    # Para multistep, inicializar história com dois passos RK4 de tamanho h
    # Avaliar derivadas iniciais
    f0 = safe_f(t0, y0)
    # Primeiro passo RK4
    t1 = t0 + h
    y1 = _rk4_step(f, t0, y0, h)
    f1 = safe_f(t1, y1)
    # Segundo passo RK4
    t2 = t1 + h
    y2 = _rk4_step(f, t1, y1, h)
    f2 = safe_f(t2, y2)

    # Montar janela deslizante
    t_n2, y_n2, f_n2 = t0, y0, f0
    t_n1, y_n1, f_n1 = t1, y1, f1
    t_n,  y_n,  f_n  = t2, y2, f2
    t_current, y_current = t2, y2

    # Executar passos restantes com AB3
    while t_current < t_final:
        if t_current + h < t_final:
            dt = h
        else:
            dt = t_final - t_current
        # Fórmula de Adams–Bashforth de 3ª ordem
        y_next = y_current + dt/12 * (23 * f_n - 16 * f_n1 + 5 * f_n2)
        t_next = t_current + dt
        f_next = safe_f(t_next, y_next)
        # Atualizar janela
        t_n2, y_n2, f_n2 = t_n1, y_n1, f_n1
        t_n1, y_n1, f_n1 = t_n,  y_n,  f_n
        t_n,  y_n,  f_n   = t_next, y_next, f_next
        t_current, y_current = t_next, y_next

    return y_current


def _rk4_step(f: ODEFunction, t: float, y: float, dt: float) -> float:
    """
    Executa um passo de Runge-Kutta de quarta ordem (RK4).

    Parâmetros:
        f: função que recebe (t, y) e retorna dy/dt
        t: instante atual
        y: valor atual em t
        dt: passo de integração

    Retorna:
        Estimativa de y(t + dt)
    """
    if dt == 0:
        return y
    k1 = f(t, y)
    k2 = f(t + dt/2, y + dt/2 * k1)
    k3 = f(t + dt/2, y + dt/2 * k2)
    k4 = f(t + dt, y + dt * k3)
    return y + dt/6 * (k1 + 2*k2 + 2*k3 + k4)


def _ab3_step(hist: List[Tuple[float, float, float]], dt: float) -> float:
    """
    Executa um passo de Adams–Bashforth de terceira ordem.

    Parâmetros:
        hist: lista de três tuplas (t_{n-2}, y_{n-2}, f_{n-2}),
              (t_{n-1}, y_{n-1}, f_{n-1}),
              (t_n,       y_n,       f_n)
        dt: tamanho do passo

    Retorna:
        Estimativa de y(t_n + dt)
    """
    # Se dt zero, retorna y_n sem alteração
    if dt == 0:
        return hist[2][1]
    _, _, f_n2 = hist[0]
    _, _, f_n1 = hist[1]
    _, y_n, f_n = hist[2]
    return y_n + dt/12 * (23 * f_n - 16 * f_n1 + 5 * f_n2)
