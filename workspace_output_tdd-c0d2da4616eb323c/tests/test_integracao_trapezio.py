import pytest
import math
from src.integracao_trapezio import integracao_trapezio


def test_integracao_trapezio_callable():
    # Verifica se a função existe e é chamável
    assert callable(integracao_trapezio)


def test_integracao_trapezio_basico_retorna_zero_quando_a_igual_b():
    # Se a == b, deve retornar 0.0 sem chamar f
    f = lambda x: x**2
    result = integracao_trapezio(f, 5.0, 5.0)
    assert result == 0.0


def test_integracao_trapezio_composite_N1_retorna_meia():
    # Para N=1 em [0,1] e f(x)=x, a regra composta deve dar 0.5
    result = integracao_trapezio(lambda x: x, 0.0, 1.0, N=1)
    assert result == pytest.approx(0.5)


def test_integracao_trapezio_f_nao_callable_gera_TypeError():
    # f deve ser callable
    with pytest.raises(TypeError) as excinfo:
        integracao_trapezio(123, 0.0, 1.0, N=1)
    assert str(excinfo.value) == "f deve ser uma função Callable[[float], float]"


def test_integracao_trapezio_a_b_nao_float_gera_TypeError():
    # a e b devem ser floats
    with pytest.raises(TypeError) as excinfo:
        integracao_trapezio(lambda x: x, "0", 1.0, N=1)
    assert str(excinfo.value) == "a e b devem ser floats"
    with pytest.raises(TypeError) as excinfo2:
        integracao_trapezio(lambda x: x, 0.0, None, N=1)
    assert str(excinfo2.value) == "a e b devem ser floats"


def test_integracao_trapezio_a_maior_b_gera_ValueError():
    # a deve ser menor que b
    with pytest.raises(ValueError) as excinfo:
        integracao_trapezio(lambda x: x, 2.0, 1.0, N=1)
    assert str(excinfo.value) == "a deve ser menor que b"


def test_integracao_trapezio_ambos_N_e_tol_None_gera_ValueError():
    # Precisar informar N ou tol
    with pytest.raises(ValueError) as excinfo:
        integracao_trapezio(lambda x: x, 0.0, 1.0)
    assert str(excinfo.value) == "É preciso fornecer N ou tol"


def test_integracao_trapezio_tol_nao_float_ou_invalido_gera_ValueError():
    # tol deve ser float positivo
    with pytest.raises(ValueError) as excinfo1:
        integracao_trapezio(lambda x: x, 0.0, 1.0, tol="0.1")
    assert str(excinfo1.value) == "tol deve ser float positivo"

    with pytest.raises(ValueError) as excinfo2:
        integracao_trapezio(lambda x: x, 0.0, 1.0, tol=0.0)
    assert str(excinfo2.value) == "tol deve ser float positivo"

    with pytest.raises(ValueError) as excinfo3:
        integracao_trapezio(lambda x: x, 0.0, 1.0, tol=-1.0)
    assert str(excinfo3.value) == "tol deve ser float positivo"


def test_integracao_trapezio_N_nao_int_ou_invalido_gera_ValueError():
    # N deve ser int positivo quando tol não informado
    with pytest.raises(ValueError) as excinfo1:
        integracao_trapezio(lambda x: x, 0.0, 1.0, N=0)
    assert str(excinfo1.value) == "N deve ser int positivo"

    with pytest.raises(ValueError) as excinfo2:
        integracao_trapezio(lambda x: x, 0.0, 1.0, N=-1)
    assert str(excinfo2.value) == "N deve ser int positivo"

    with pytest.raises(ValueError) as excinfo3:
        integracao_trapezio(lambda x: x, 0.0, 1.0, N=1.5)
    assert str(excinfo3.value) == "N deve ser int positivo"


def test_integracao_trapezio_nao_invoke_f_quando_a_igual_b():
    # f que lançaria se chamada
    def f_erro(x):
        raise RuntimeError("f foi chamado")
    result = integracao_trapezio(f_erro, 1.0, 1.0)
    assert result == 0.0


def test_integracao_trapezio_nao_valida_modos_quando_a_igual_b():
    # Mesmo com N e tol inválidos, retorna 0.0 sem invocar f
    def f_erro(x):
        raise RuntimeError("f foi chamado")
    result = integracao_trapezio(f_erro, 2.5, 2.5, N=0, tol=-1.0)
    assert result == 0.0

# Novos testes para o Sub-requisito Fase 5

def test_integracao_trapezio_composite_only_sin_N2():
    """
    Quando apenas N é fornecido, deve usar modo composto e retornar _trapezio_composto(sin, 0, pi, 2) ~= pi/2
    """
    result = integracao_trapezio(math.sin, 0.0, math.pi, N=2)
    assert result == pytest.approx(math.pi/2)


def test_integracao_trapezio_tol_only_uses_adaptive():
    """
    Quando apenas tol é fornecido, deve usar integração adaptativa e convergir para 2.0 em [0, pi]
    """
    tol = 1e-6
    result = integracao_trapezio(math.sin, 0.0, math.pi, tol=tol)
    assert result == pytest.approx(2.0, rel=tol)


def test_integracao_trapezio_mixed_N_and_tol_prioritizes_tol():
    """
    Mesmo que N seja fornecido, se tol estiver presente, deve ignorar N e usar adaptativo.
    Para f=sin em [0,pi] e N pequeno, o modo composto daria ~pi/2, mas adaptativo deve dar ~2.
    """
    tol = 1e-6
    result = integracao_trapezio(math.sin, 0.0, math.pi, N=2, tol=tol)
    # Esperamos ~2 (não ~pi/2)
    assert result == pytest.approx(2.0, rel=tol)

# Novos testes parametrizados para casos gerais
@pytest.mark.parametrize("f,a,b,expected", [
    (math.cos, 0.0, math.pi/2, 1.0),
    (lambda x: x**3, 0.0, 1.0, 1.0/4.0),
    (math.exp, 0.0, 1.0, math.e - 1.0),
    (lambda x: 1/(1+x**2), 0.0, 1.0, math.pi/4),
])
def test_integracao_trapezio_adaptive_parametrized(f, a, b, expected):
    tol = 1e-6
    result = integracao_trapezio(f, a, b, tol=tol)
    assert result == pytest.approx(expected, rel=tol)


@pytest.mark.parametrize("f,a,b,expected,N", [
    (math.cos, 0.0, math.pi/2, 1.0, 100),
    (lambda x: x**3, 0.0, 1.0, 1.0/4.0, 100),
    (math.exp, 0.0, 1.0, math.e - 1.0, 200),
    (lambda x: 1/(1+x**2), 0.0, 1.0, math.pi/4, 200),
])
def test_integracao_trapezio_composite_parametrized(f, a, b, expected, N):
    result = integracao_trapezio(f, a, b, N=N)
    assert result == pytest.approx(expected, rel=1e-3)


def test_integracao_trapezio_adaptativa_nao_converge_max_iter():
    import sys
    f = lambda x: x**2
    tol = 1e-14  # > sys.float_info.epsilon
    with pytest.raises(ValueError) as excinfo:
        integracao_trapezio(f, 0.0, 1.0, tol=tol)
    assert str(excinfo.value) == "Não convergiu em até 20 iterações"
