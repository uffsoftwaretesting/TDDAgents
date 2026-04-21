import copy
import pytest
from src.solver_euler import euler_explicito

# Variáveis globais para testar pureza
GLOBAL_INT = 100
GLOBAL_LIST = [1, 2, 3]
GLOBAL_DICT = {'a': 1, 'b': [4, 5]}


def test_euler_explicito_does_not_modify_global_int():
    before_int = GLOBAL_INT
    # zero steps (t_final == t0) garante retorno imediato
    result = euler_explicito(lambda t, y: 0.0, 0.0, 1.0, 0.0, 0.1)
    assert result == 1.0
    assert GLOBAL_INT == before_int, (
        f"Expected GLOBAL_INT to remain {before_int}, but got {GLOBAL_INT}"
    )


def test_euler_explicito_does_not_modify_global_list_and_dict():
    before_list = GLOBAL_LIST.copy()
    before_dict = copy.deepcopy(GLOBAL_DICT)
    # passo simples para alterar y, mas não deve tocar variáveis globais
    result = euler_explicito(lambda t, y: y * 0.0, 1.0, 2.0, 2.0, 0.5)
    # resultado não importa para a pureza, apenas que globals não mudaram
    assert GLOBAL_LIST == before_list, (
        f"Expected GLOBAL_LIST to remain {before_list}, but got {GLOBAL_LIST}"
    )
    assert GLOBAL_DICT == before_dict, (
        f"Expected GLOBAL_DICT to remain {before_dict}, but got {GLOBAL_DICT}"
    )