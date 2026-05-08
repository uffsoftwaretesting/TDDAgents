import pytest
import math

from src.rk4_classico import rk4_classico


def test_rk4_classico_function_exists():
    """
    Verifica se a função rk4_classico está definida e é chamável.
    """
    assert callable(rk4_classico), "Função rk4_classico deve ser definida e chamável"


def test_rk4_classico_non_callable_f():
    """
    Se f não for chamável, deve lançar TypeError.
    """
    with pytest.raises(TypeError) as excinfo:
        rk4_classico(123, 0.0, 0.0, 1.0, 0.1)
    assert str(excinfo.value) == "f must be callable"


def test_rk4_classico_t_final_less_than_t0():
    """
    Se t_final < t0, deve lançar ValueError.
    """
    def f(t, y):
        return 0.0

    with pytest.raises(ValueError) as excinfo:
        rk4_classico(f, 1.0, 0.0, 0.5, 0.1)
    assert str(excinfo.value) == "t_final must be >= t0"


def test_rk4_classico_h_zero_or_negative():
    """
    Se h <= 0, deve lançar ValueError para cada caso.
    """
    def f(t, y):
        return 0.0

    for bad_h in [0, -0.1]:
        with pytest.raises(ValueError) as excinfo:
            rk4_classico(f, 0.0, 0.0, 1.0, bad_h)
        assert str(excinfo.value) == "h must be > 0"


def test_rk4_classico_t_final_equals_t0():
    """
    Se t_final == t0, deve retornar imediatamente y0 sem chamar f.
    """
    calls = {'count': 0}

    def f(t, y):
        calls['count'] += 1
        raise AssertionError("f should not ser called quando t_final == t0")

    t0 = 1.0
    y0 = 2.5
    result = rk4_classico(f, t0, y0, t0, 0.1)

    assert result == y0
    assert calls['count'] == 0


def test_rk4_classico_constant_zero_derivative_steps_and_result():
    """
    Para f(t,y)=0, verifica que são computados N passos,
    f é chamado 4 vezes por passo, e y permanece igual a y0.
    """
    calls = {'count': 0}

    def f(t, y):
        calls['count'] += 1
        return 0.0

    t0 = 0.0
    y0 = 5.0
    t_final = 2.0
    h = 0.5
    result = rk4_classico(f, t0, y0, t_final, h)

    assert result == y0

    expected_steps = math.floor((t_final - t0) / h)
    expected_calls = expected_steps * 4
    assert calls['count'] == expected_calls, (
        f"Esperado {expected_calls} chamadas a f, mas obteve {calls['count']}"
    )


@pytest.mark.parametrize(
    "t0,y0,t_final,h,C",
    [
        (0.0, 1.0, 5.0, 1.0, 2.0),
        (1.0, 0.5, 2.5, 0.5, 3.0),
    ],
)

def test_rk4_classico_constant_derivative_exact_interval(t0, y0, t_final, h, C):
    """
    Para f(t,y)=C constante e h dividindo exatamente o intervalo,
    y(t_final) deve ser y0 + C*(t_final - t0).
    """
    def f(t, y):
        return C

    result = rk4_classico(f, t0, y0, t_final, h)
    expected = y0 + C * (t_final - t0)
    assert result == pytest.approx(expected)


def test_rk4_classico_constant_derivative_with_truncated_step():
    """
    Para f(t,y)=1 e intervalo não múltiplo de h,
    deve executar passos completos e um passo truncado, resultando em y correto.
    Também verifica número de chamadas a f.
    """
    calls = {'count': 0}

    def f(t, y):
        calls['count'] += 1
        return 1.0

    t0, y0, t_final, h = 0.0, 0.0, 1.0, 0.3
    result = rk4_classico(f, t0, y0, t_final, h)

    assert result == pytest.approx(1.0)

    N = math.floor((t_final - t0) / h)
    expected_calls = N * 4 + 4
    assert calls['count'] == expected_calls, (
        f"Esperado {expected_calls} chamadas a f, mas obteve {calls['count']}"
    )


def test_rk4_classico_non_float_return_raises_type_error():
    """
    Se f retornar tipo não-float no primeiro cálculo de k1,
    deve lançar TypeError.
    """
    def f_bad(t, y):
        return "not a float"

    with pytest.raises(TypeError):
        rk4_classico(f_bad, 0.0, 1.0, 0.2, 0.1)


def test_rk4_classico_propagates_exception_from_f():
    """
    Se f lançar uma exceção qualquer, ela deve ser propagada sem captura interna.
    """
    class CustomError(Exception):
        pass

    def f_raise(t, y):
        raise CustomError("boom")

    with pytest.raises(CustomError) as excinfo:
        rk4_classico(f_raise, 0.0, 1.0, 0.2, 0.1)
    assert str(excinfo.value) == "boom"

# Novo teste para fase 7: precisão de ponto flutuante na acumulação de dt
def test_rk4_classico_floating_point_cumulative_dt_precision():
    """
    Para h levemente maior que fração exata devido à precisão de ponto flutuante,
    garante que a soma cumulativa de dt nunca ultrapasse t_final e retorna valor correto para f=0.
    """
    t0 = 0.0
    y0 = 7.0
    t_final = 1.0
    h = 0.1 + 1e-16
    ts = []

    def f(t, y):
        ts.append(t)
        return 0.0

    result = rk4_classico(f, t0, y0, t_final, h)
    # y não deve mudar
    assert result == pytest.approx(y0)
    # Nenhum t ultrapassa t_final
    assert max(ts) <= t_final
    # t_final foi alcançado exatamente
    assert pytest.approx(t_final) in ts
    # Número de chamadas correto: 4 por passo completo mais 4 no truncado
    N = math.floor((t_final - t0) / h)
    expected_calls = N * 4 + 4
    assert len(ts) == expected_calls