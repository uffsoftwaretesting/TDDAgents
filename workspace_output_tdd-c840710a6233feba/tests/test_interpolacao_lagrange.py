import pytest
from interpolacao_lagrange import interpolacao_lagrange


def test_single_node_returns_fx():
    # Com apenas um nó, o valor interpolado deve ser f(x_nos[0])
    # independentemente de x_alvo
    result = interpolacao_lagrange([1.0], lambda x: x ** 2, 2.0)
    assert result == 1.0


@pytest.mark.parametrize("x_nos", [[], 42])
def test_empty_or_non_sequence_x_nos_raises_value_error_with_message(x_nos):
    # x_nos vazio ou não-Sequence deve levantar ValueError
    with pytest.raises(ValueError) as excinfo:
        interpolacao_lagrange(x_nos, lambda x: float(x), 0.0)
    assert str(excinfo.value) == "É necessário pelo menos um nó de abscissa"


@pytest.mark.parametrize(
    "x_nos",
    [
        [1.0, "a"],  # elemento string
        [1.0, 2],      # elemento int
        [None],        # elemento None
    ],
)
def test_x_nos_with_non_float_element_raises_type_error(x_nos):
    # Qualquer elemento de x_nos que não seja float deve lançar TypeError
    with pytest.raises(TypeError) as excinfo:
        interpolacao_lagrange(
            x_nos,
            lambda x: float(x) if isinstance(x, (int, float)) else 0.0,
            0.0,
        )
    assert str(excinfo.value) == "Todos os nós de abscissa devem ser float"


def test_x_nos_with_duplicate_values_raises_value_error_with_message():
    # x_nos com valores duplicados deve lançar ValueError
    with pytest.raises(ValueError) as excinfo:
        interpolacao_lagrange([1.0, 1.0, 2.0], lambda x: float(x), 1.0)
    assert str(excinfo.value) == "Nós de abscissa duplicados"


@pytest.mark.parametrize(
    "x_alvo",
    ["1.5", 1, None, [1.5]],
)
def test_x_alvo_non_float_raises_type_error(x_alvo):
    # x_alvo não-flutuante deve lançar TypeError
    with pytest.raises(TypeError) as excinfo:
        interpolacao_lagrange([1.0], lambda x: float(x), x_alvo)
    assert str(excinfo.value) == "Ponto alvo deve ser float"


def test_f_return_non_float_single_node_raises_type_error():
    # f retorna não-float com um único nó deve lançar TypeError
    def f(x):
        return "y"

    with pytest.raises(TypeError) as excinfo:
        interpolacao_lagrange([1.0], f, 1.0)
    assert str(excinfo.value) == "Valor de f(x) deve ser float para todos os nós"


def test_f_return_non_float_multiple_nodes_raises_type_error():
    # f retorna não-float para algum nó em múltiplos nós
    def f(x):
        if x == 2.0:
            return "y"
        return x

    with pytest.raises(TypeError) as excinfo:
        interpolacao_lagrange([1.0, 2.0], f, 1.5)
    assert str(excinfo.value) == "Valor de f(x) deve ser float para todos os nós"
