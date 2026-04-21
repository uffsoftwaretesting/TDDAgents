import math
from interpolacao_lagrange import interpolacao_lagrange


def test_single_node_various_x_alvo_para_garantir_retorno():
    # Para um único nó, o retorno deve ser sempre f(node) independente de x_alvo
    node = 5.5

    def f(x):
        return x + 2.0

    expected = f(node)

    x_alvo_values = [
        node,
        node + 10.0,
        -100.0,
        0.0,
        math.inf,
        -math.inf,
    ]

    for x_alvo in x_alvo_values:
        result = interpolacao_lagrange([node], f, x_alvo)
        assert result == expected, (
            f"Para x_alvo={x_alvo}, esperava {expected} mas obteve {result}"
        )


def test_single_node_chama_f_uma_vez_com_argumento_correto():
    # Verifica que f seja chamada exatamente uma vez com o valor do nó
    node = 3.2
    calls = []

    def f(x):
        calls.append(x)
        return 7.7

    result = interpolacao_lagrange([node], f, 0.0)
    assert result == 7.7

    assert calls == [node], (
        f"f foi chamada com argumentos {calls}, esperado [{node}]"
    )
