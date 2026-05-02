import pytest
from src.solver import adams_bashforth_3

class CustomError(Exception):
    pass

# 1. Teste de propagação de exceções de f no branch de passo único (dt_total < h)
def test_exception_propagates_in_single_step():
    """
    Se f lançar exceção no passo único (RK4), ela deve ser propagada sem captura.
    """
    def f_raise(t, y):
        raise CustomError("erro no f")

    with pytest.raises(CustomError) as excinfo:
        adams_bashforth_3(f_raise, 0.0, 1.0, 0.1, 0.5)
    assert str(excinfo.value) == "erro no f"

# 2. Teste de propagação de exceções de f no branch multistep (dt_total >= h)
def test_exception_propagates_in_multistep():
    """
    Se f lançar exceção em qualquer ponto do multistep (ab3), ela deve ser propagada.
    """
    # f que não falha no primeiro f0, mas falha em chamadas subsequentes
    calls = {'count': 0}
    def f_raise_later(t, y):
        calls['count'] += 1
        # falha na segunda chamada
        if calls['count'] == 2:
            raise ValueError("erro tardio")
        return 0.0

    # dt_total = 3*h (0.3 >= 0.1) -> multistep
    with pytest.raises(ValueError) as excinfo:
        adams_bashforth_3(f_raise_later, 0.0, 1.0, 0.3, 0.1)
    assert str(excinfo.value) == "erro tardio"

# 3. Teste de TypeError quando f retorna non-float no passo único
@pytest.mark.parametrize("ret_value", [1, "string", None, [0.0]])
def test_type_error_on_non_float_return_single_step(ret_value):
    """
    Se f retornar valor não-float no passo único, deve lançar TypeError.
    """
    def f_non_float(t, y):
        return ret_value

    with pytest.raises(TypeError):
        adams_bashforth_3(f_non_float, 0.0, 1.0, 0.1, 0.5)

# 4. Teste de TypeError quando f retorna non-float no multistep
@pytest.mark.parametrize("ret_value", [2, True, (1.0,), {}])
def test_type_error_on_non_float_return_multistep(ret_value):
    """
    Se f retornar valor não-float no multistep, deve lançar TypeError.
    """
    def f_non_float(t, y):
        return ret_value

    # dt_total = 0.3 >= h -> multistep
    with pytest.raises(TypeError):
        adams_bashforth_3(f_non_float, 0.0, 1.0, 0.3, 0.1)
