import math
from src.solve import solve

def test_euler_convergence_exponential():
    """
    Verifica que o erro da integração por Euler explícito em y'=y diminui
    monotonicamente conforme o número de passos aumenta.
    """
    t0 = 0.0
    tf = 1.0
    y0 = 1.0
    n_values = [10, 100, 1000]
    errors = []
    for n in n_values:
        approx = solve(lambda t, y: y, t0, tf, y0, n)
        exact = y0 * math.exp(tf - t0)
        errors.append(abs(approx - exact))
    # O erro deve diminuir quando n cresce
    for i in range(len(errors) - 1):
        assert errors[i] > errors[i+1], (
            f"Erro com n={n_values[i]} ({errors[i]}) deve ser maior que "
            f"erro com n={n_values[i+1]} ({errors[i+1]})"
        )