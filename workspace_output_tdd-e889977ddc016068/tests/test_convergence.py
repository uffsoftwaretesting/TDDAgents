import math
import pytest
from integrador_trapezio import solve


def test_convergence_order_for_quadratic():
    """
    Verifica a ordem de convergência para f(x)=x^2 no intervalo [0,1].
    A ordem teórica do método composto do trapézio é 2, ou seja,
    ao dobrar n, o erro deve diminuir aproximadamente por um fator de 4.
    """
    # Definição da função e valores exatos
    def f(x):
        return x**2
    a, b = 0.0, 1.0
    analytic = 1.0 / 3.0

    # Valores de n dobrando a cada passo
    n_values = [10, 20, 40, 80]

    # Cálculo dos erros absolutos
    errors = [abs(solve(f, a, b, n) - analytic) for n in n_values]

    # Deve haver convergência: erro diminui quando n dobra
    for i in range(len(errors) - 1):
        assert errors[i+1] < errors[i], (
            f"Erro não diminuiu ao passar de n={n_values[i]} (err={errors[i]}) "
            f"para n={n_values[i+1]} (err={errors[i+1]})"
        )

    # Cálculo da ordem observada entre cada par sucessivo
    observed_orders = [
        math.log(errors[i] / errors[i+1], 2)
        for i in range(len(errors) - 1)
    ]

    # A ordem deve estar próxima de 2 (tol de 5%)
    for p in observed_orders:
        assert p == pytest.approx(2.0, rel=0.05), (
            f"Ordem observada {p:.3f} fora da tolerância para método de ordem 2"
        )


def test_determinism_in_convergence():
    """
    Garante que o cálculo das sucessivas aproximações de erro seja determinístico.
    Chamamos solve duas vezes para cada n e comparamos os erros.
    """
    def f(x):
        return math.sin(x)
    a, b = 0.0, math.pi
    analytic = 2.0
    n_values = [50, 100, 200]

    # Executa duas vezes e verifica que erros coincidem
    errors_run1 = [abs(solve(f, a, b, n) - analytic) for n in n_values]
    errors_run2 = [abs(solve(f, a, b, n) - analytic) for n in n_values]
    assert errors_run1 == errors_run2, (
        f"Cálculo não é determinístico: {errors_run1} != {errors_run2}"
    )
