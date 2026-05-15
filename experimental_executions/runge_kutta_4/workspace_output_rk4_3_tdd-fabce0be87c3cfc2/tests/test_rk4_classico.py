import pytest
from rk4_classico import rk4_classico

def test_trivial_case_returns_y0_when_t0_equals_t_final():
    # Se t0 == t_final, retorna imediatamente y0
    result = rk4_classico(lambda t, y: y, 0.0, 2.5, 0.0, 0.1)
    assert result == 2.5

def test_f_not_callable_raises_typeerror():
    # f deve ser callable
    with pytest.raises(TypeError):
        rk4_classico(123, 0.0, 1.0, 1.0, 0.1)

def test_non_float_params_raise_typeerror():
    # t0, y0, t_final e h devem ser floats
    with pytest.raises(TypeError):
        rk4_classico(lambda t, y: y, "0.0", 1.0, 1.0, 0.1)
    with pytest.raises(TypeError):
        rk4_classico(lambda t, y: y, 0.0, "1.0", 1.0, 0.1)
    with pytest.raises(TypeError):
        rk4_classico(lambda t, y: y, 0.0, 1.0, "1.0", 0.1)
    with pytest.raises(TypeError):
        rk4_classico(lambda t, y: y, 0.0, 1.0, 1.0, "0.1")

def test_negative_step_raises_valueerror():
    # h deve ser positivo
    with pytest.raises(ValueError):
        rk4_classico(lambda t, y: y, 0.0, 1.0, 2.0, -0.1)

def test_t_final_less_than_t0_raises_valueerror():
    # t_final deve ser >= t0
    with pytest.raises(ValueError):
        rk4_classico(lambda t, y: y, 2.0, 1.0, 1.0, 0.1)

# Novos testes para validação de mensagens de TypeError

def test_f_not_callable_message():
    with pytest.raises(TypeError) as excinfo:
        rk4_classico(123, 0.0, 1.0, 1.0, 0.1)
    assert str(excinfo.value) == "f deve ser callable"

def test_t0_not_float_message():
    with pytest.raises(TypeError) as excinfo:
        rk4_classico(lambda t, y: y, "0.0", 1.0, 1.0, 0.1)
    assert str(excinfo.value) == "t0 deve ser float"

def test_y0_not_float_message():
    with pytest.raises(TypeError) as excinfo:
        rk4_classico(lambda t, y: y, 0.0, "1.0", 1.0, 0.1)
    assert str(excinfo.value) == "y0 deve ser float"

def test_t_final_not_float_message():
    with pytest.raises(TypeError) as excinfo:
        rk4_classico(lambda t, y: y, 0.0, 1.0, "1.0", 0.1)
    assert str(excinfo.value) == "t_final deve ser float"

def test_h_not_float_message():
    with pytest.raises(TypeError) as excinfo:
        rk4_classico(lambda t, y: y, 0.0, 1.0, 1.0, "0.1")
    assert str(excinfo.value) == "h deve ser float"

# Novos testes para validação de domínio

def test_zero_step_raises_valueerror():
    # h = 0 deve gerar ValueError
    with pytest.raises(ValueError):
        rk4_classico(lambda t, y: y, 0.0, 1.0, 2.0, 0.0)

def test_zero_step_valueerror_message():
    # Verifica mensagem de ValueError para h = 0
    with pytest.raises(ValueError) as excinfo:
        rk4_classico(lambda t, y: y, 0.0, 1.0, 2.0, 0.0)
    assert str(excinfo.value) == "Passo h deve ser positivo"

def test_t_final_less_than_t0_valueerror_message():
    # Verifica mensagem de ValueError para t_final < t0
    with pytest.raises(ValueError) as excinfo:
        rk4_classico(lambda t, y: y, 2.0, 1.0, 1.5, 0.1)
    assert str(excinfo.value) == "t_final deve ser maior ou igual a t0"

# Teste adicional para garantir early return sem chamar f

def test_trivial_case_does_not_evaluate_f():
    # Quando t0 == t_final, f não deve ser chamado e deve retornar y0
    def f_raising(t, y):
        raise AssertionError("f should not be called when t0 == t_final")
    result = rk4_classico(f_raising, 1.0, 5.0, 1.0, 0.2)
    assert result == 5.0
